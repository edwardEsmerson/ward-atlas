#!/usr/bin/env python3
"""Exercise the frozen eight-group source-branch contract on actual FalkorDB.

This verifier owns one lane-named graph, loopback app/database processes,
project-local state directory, and browser runs. Expectations are read only
from the frozen source-observation-branch packet; API output never amends the
oracle. No external service, host database, or real hospital data is used.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Any
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from verify_falkordb_live import (
    LiveVerifier,
    RespConnection,
    VerificationFailure,
    explicit_bind_preflight,
    query_result,
    result_rows,
)
from wardatlas_app import Handler, WardAtlasHTTPServer, WardAtlasService
from wardatlas_decisions import decision_seed_queries
from wardatlas_fixtures import seed_queries
from wardatlas_shift_board import seed_shift_board_queries
from wardatlas_source_branch import (
    ACTUAL_SOURCE_OBSERVED_AT,
    BRANCH_EDGES,
    BRANCH_ID,
    OVERRIDE_ID,
    SOURCE_ID,
    disconnected_decoy_branch_queries,
    restricted_source_branch_queries,
    source_branch_seed_queries,
)


ROOT = Path(__file__).resolve().parents[1]
EXPECTED_PATH = ROOT / "contracts/decision-source-branch/expected-results.json"
MATRIX_PATH = ROOT / "contracts/decision-source-branch/adversarial-matrix.json"
REQUIRED_GROUPS = [
    "cold-empty-branch-lookup",
    "mainline-at-16-05",
    "branch-at-16-05-suppression",
    "branch-at-16-06-restoration",
    "mainline-isolation",
    "missing-edge-no-fallback-and-reseed",
    "decoy-and-restricted-invariance",
    "repeat-reconnect-read-only-desktop-mobile-cleanup",
]
UNKNOWN_1605 = "2026-10-14T16:05:00Z"
KNOWN_1604 = "2026-10-14T16:04:00Z"
KNOWN_1606 = "2026-10-14T16:06:00Z"
RESTRICTED_TRIPWIRES = [
    "BR-RESTRICTED-OPS-77",
    "OBS-RESTRICTED-OPS-77",
    "SRC-RESTRICTED-OPS-77",
    "Restricted synthetic source observation canary 77",
    "ROLE-RESTRICTED-OPS-77",
]


def canonical(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: canonical(value[key]) for key in sorted(value)}
    if isinstance(value, list):
        return [canonical(item) for item in value]
    return value


def same(left: Any, right: Any) -> bool:
    return canonical(left) == canonical(right)


def request(base_url: str, path: str, method: str = "GET", payload: dict | None = None) -> tuple[int, Any]:
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    req = Request(base_url + path, data=data, method=method, headers={"Content-Type": "application/json", "Accept": "application/json"})
    try:
        with urlopen(req, timeout=20) as response:
            status, raw = response.status, response.read()
    except HTTPError as exc:
        status, raw = exc.code, exc.read()
    try:
        return status, json.loads(raw.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return status, raw.decode("utf-8", "replace")


class SourceBranchLive:
    def __init__(self, args: argparse.Namespace, expected: dict[str, Any], matrix: dict[str, Any]):
        self.args = args
        self.expected = expected
        self.matrix = matrix
        self.groups = {item["group_id"]: item for item in matrix["groups"]}
        self.cases = {item["id"]: item["expected"] for item in expected["groups"]}
        self.run_id = f"lane{args.lane}_{uuid.uuid4().hex}"
        self.db = LiveVerifier(args.db_port, expected, matrix)
        self.db.run_id = self.run_id
        self.db.graph = f"wardatlas_test_source_branch_{self.run_id}"
        self.base_url = f"http://127.0.0.1:{args.app_port}"
        self.state_dir = ROOT / ".local" / "source-observation-branch-runs" / self.run_id
        self.state_dir_owned = False
        self.service: WardAtlasService | None = None
        self.httpd: WardAtlasHTTPServer | None = None
        self.http_thread: threading.Thread | None = None
        self.app_stopped = False
        self.coverage: dict[str, set[str]] = {group: set() for group in REQUIRED_GROUPS}
        self.assertions: list[dict[str, Any]] = []
        self.scenarios: list[dict[str, Any]] = []
        self.observed: dict[str, Any] = {}
        self.cleanup = {
            "owned_graph_deleted": False,
            "owned_database_process_stopped": False,
            "owned_database_runtime_dir_removed": False,
            "owned_app_stopped": False,
            "owned_local_state_removed": False,
        }

    def check(self, group_id: str, assertion_id: str, passed: bool, detail: Any = None) -> None:
        allowed = {item["id"] for item in self.groups[group_id]["pinned_assertions"]}
        if assertion_id not in allowed:
            raise VerificationFailure(f"unmapped assertion {group_id}/{assertion_id}")
        if assertion_id in self.coverage[group_id]:
            raise VerificationFailure(f"duplicate assertion {group_id}/{assertion_id}")
        self.coverage[group_id].add(assertion_id)
        record = {"group_id": group_id, "assertion_id": assertion_id, "passed": bool(passed)}
        if detail is not None:
            record["detail"] = detail
        self.assertions.append(record)
        if not passed:
            raise VerificationFailure(f"{group_id}/{assertion_id} failed: {detail!r}")

    def scenario(self, group_id: str, callback, *, defer_owned_cleanup: bool = False) -> None:
        record = {"id": group_id, "status": "running"}
        self.scenarios.append(record)
        started = time.monotonic()
        try:
            callback()
            expected = {item["id"] for item in self.groups[group_id]["pinned_assertions"]}
            missing = expected - self.coverage[group_id]
            if defer_owned_cleanup:
                missing.discard("repeat.owned-cleanup")
            if missing:
                raise VerificationFailure(f"unexecuted mapped assertions: {sorted(missing)}")
            record["status"] = "pass-pending-owned-cleanup" if defer_owned_cleanup else "pass"
        except Exception as exc:
            record["status"] = "fail"
            record["error"] = f"{type(exc).__name__}: {exc}"
            raise
        finally:
            record["assertion_count"] = len(self.coverage[group_id])
            record["elapsed_ms"] = round((time.monotonic() - started) * 1000, 3)

    def get(self, path: str) -> tuple[int, Any]:
        return request(self.base_url, path)

    def post(self, path: str, payload: dict | None = None) -> tuple[int, Any]:
        return request(self.base_url, path, "POST", payload or {})

    def branch(self, known_at: str | None) -> tuple[int, Any]:
        query = {"scope": "operations"}
        if known_at is not None:
            query["known_at"] = known_at
        return self.get("/api/decision-source-branch?" + urlencode(query))

    def timeline(self, known_at: str | None) -> tuple[int, Any]:
        query = {"scope": "operations"}
        if known_at is not None:
            query["known_at"] = known_at
        return self.get("/api/decision-timeline?" + urlencode(query))

    def reset(self) -> None:
        status, body = self.post("/api/scenario/reset")
        if status != 200 or body.get("status") != "reset":
            raise VerificationFailure(f"test-owned graph reset failed: {status} {body!r}")

    def graph_rows(self, label: str, cypher: str) -> list[dict[str, Any]]:
        return self.db.graph_rows(label, cypher)

    def graph_snapshot(self, label: str) -> dict[str, Any]:
        nodes = self.graph_rows(label + "-nodes", "MATCH (n) RETURN n.id AS id, labels(n) AS labels, properties(n) AS props ORDER BY id")
        edges = self.graph_rows(label + "-edges", "MATCH (a)-[r]->(b) RETURN a.id AS from_id, type(r) AS kind, r.id AS id, properties(r) AS props, b.id AS to_id ORDER BY id")
        return {"nodes": nodes, "edges": edges}

    def graph_counts(self, label: str) -> dict[str, int]:
        nodes = self.graph_rows(label + "-node-count", "MATCH (n) RETURN count(n) AS count")
        edges = self.graph_rows(label + "-edge-count", "MATCH ()-[r]->() RETURN count(r) AS count")
        return {"nodes": int(nodes[0]["count"]), "relationships": int(edges[0]["count"])}

    def seed(self, label: str, queries: list[str]) -> None:
        for index, query in enumerate(queries):
            self.db.graph_query(f"{label}-{index}", query)

    def fixture_queries(self) -> list[str]:
        return [*seed_queries(), *seed_shift_board_queries(), *decision_seed_queries(), *source_branch_seed_queries()]

    def run_browser(self, mode: str, label: str) -> dict[str, Any]:
        report_path = self.args.evidence_dir / f"browser-{label}.json"
        screenshots = self.args.evidence_dir / f"screenshots-{label}"
        command = [
            "node", str(ROOT / "scripts/decision_source_branch_browser_assertions.cjs"),
            self.base_url, mode, str(report_path), str(screenshots),
        ]
        result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, timeout=180)
        if result.returncode != 0 or not report_path.is_file():
            raise VerificationFailure(f"source-branch browser {label} failed: {(result.stdout + result.stderr)[-5000:]}")
        report = json.loads(report_path.read_text(encoding="utf-8"))
        if report.get("status") != "PASS" or report.get("assertion_count", 0) == 0:
            raise VerificationFailure(f"source-branch browser {label} did not pass: {report}")
        self.observed[f"browser-{label}"] = report
        return report

    @staticmethod
    def _branch_part(value: dict[str, Any]) -> dict[str, Any]:
        return value.get("hypothetical_branch", {})

    @staticmethod
    def _projection(value: dict[str, Any]) -> dict[str, Any]:
        return SourceBranchLive._branch_part(value).get("projection", {})

    def start(self) -> None:
        explicit_bind_preflight(self.args.app_port)
        self.db.launch()
        # Create only this run's graph and confirm it is empty before reset.
        self.graph_rows("cold-owned-graph", "MATCH (n) RETURN n.id AS id LIMIT 1")
        self.state_dir.parent.mkdir(parents=True, exist_ok=True)
        self.state_dir.mkdir(mode=0o700)
        self.state_dir_owned = True
        self.service = WardAtlasService(self.args.db_port, self.db.graph, self.state_dir)
        self.httpd = WardAtlasHTTPServer(("127.0.0.1", self.args.app_port), Handler, self.service)
        self.http_thread = threading.Thread(target=self.httpd.serve_forever, kwargs={"poll_interval": 0.1}, daemon=True)
        self.http_thread.start()
        deadline = time.monotonic() + 12
        while time.monotonic() < deadline:
            try:
                status, body = self.get("/api/health")
                if status == 200 and body.get("database") == "FalkorDB" and body.get("module_version") == 60002:
                    return
            except Exception:
                time.sleep(0.05)
        raise VerificationFailure("owned loopback WardAtlas/FalkorDB API did not become ready")

    def run(self) -> None:
        self.start()

        def cold_group() -> None:
            expected = self.cases["cold-empty-branch-lookup"]
            status, value = self.branch(None)
            projection = self._projection(value)
            serialized = json.dumps(value, sort_keys=True)
            self.check("cold-empty-branch-lookup", "cold.state", status == 200 and not self._branch_part(value).get("scenario") and projection.get("claim_context_state") == expected["branch_state"], {"status": status, "branch": self._branch_part(value)})
            self.check("cold-empty-branch-lookup", "cold.full-projection", same(projection, expected["projection"]), projection)
            self.check("cold-empty-branch-lookup", "cold.no-identifiers", not any(token in serialized for token in expected["exposed_ids"] + [BRANCH_ID, OVERRIDE_ID, SOURCE_ID, "DEC-OPS-9", "SOP-OPS-9", "ROLE-"]), serialized)
            self.check("cold-empty-branch-lookup", "cold.no-titles", not any(token in serialized for token in expected["exposed_titles"] + ["Synthetic", "Current Text", "Reconciliation"]), serialized)
            self.check("cold-empty-branch-lookup", "cold.no-count-fields", not any("count" in key.lower() for key in value) and not expected["exposed_count_fields"], value)
            self.check("cold-empty-branch-lookup", "cold.no-existence", expected["existence_hint_exposed"] is False and "existence_hint" not in serialized, value)
            self.check("cold-empty-branch-lookup", "cold.no-absence-conclusion", projection.get("negative_conclusion_asserted") is False and projection.get("decision_record") is None and projection.get("conflict") is None, projection)
            self.check("cold-empty-branch-lookup", "cold.no-mainline-fallback", expected["mainline_fallback_used"] is False and same(projection, expected["projection"]), projection)
            self.observed["cold"] = value

        self.scenario("cold-empty-branch-lookup", cold_group)
        cold_browser = self.run_browser("cold-seed", "cold-seed")
        self.observed["browser-cold-seed"] = cold_browser

        def mainline_group() -> None:
            expected = self.cases["mainline-at-16-05"]
            status, value = self.branch(UNKNOWN_1605)
            mainline = value.get("mainline", {})
            projection = expected["projection"]
            self.check("mainline-at-16-05", "mainline.full-16-05-snapshot", status == 200 and same(mainline, projection), mainline)
            v3 = next((row for row in mainline.get("claims", []) if row.get("version_id") == "SOP-OPS-9-V3"), {})
            self.check("mainline-at-16-05", "mainline.v3-source-time", v3.get("source", {}).get("observed_at") == expected["v3_source_observed_at"], v3)
            self.check("mainline-at-16-05", "mainline.conflict", mainline.get("conflict", {}).get("edge") == expected["conflict_edge"], mainline.get("conflict"))
            self.check("mainline-at-16-05", "mainline.record-state", mainline.get("decision_record", {}).get("recorded_state") == expected["decision_record_state"], mainline.get("decision_record"))
            self.check("mainline-at-16-05", "mainline.considered-links", mainline.get("decision_considers_edges") == expected["considered_edges"], mainline.get("decision_considers_edges"))
            self.check("mainline-at-16-05", "mainline.unverified-claims", [row.get("claim_label") for row in mainline.get("claims", [])] == expected["claim_labels"], [row.get("claim_label") for row in mainline.get("claims", [])])
            self.check("mainline-at-16-05", "mainline.no-selected-version", expected["selected_version"] is None and mainline.get("current_owner_or_procedure_selected") is False, mainline)
            base_status, base = self.timeline(UNKNOWN_1605)
            self.check("mainline-at-16-05", "mainline.no-override", base_status == 200 and same(mainline, base) and value["hypothetical_branch"].get("scenario", {}).get("source_fact_modified") is False, {"timeline": base, "mainline": mainline})
            self.observed["mainline-1605"] = value

        self.scenario("mainline-at-16-05", mainline_group)

        def branch_1605_group() -> None:
            expected = self.cases["branch-at-16-05-suppression"]
            status, value = self.branch(UNKNOWN_1605)
            branch = self._branch_part(value)
            projection = branch.get("projection", {})
            serialized = json.dumps(branch, sort_keys=True)
            self.check("branch-at-16-05-suppression", "branch-1605.full-projection", status == 200 and same(projection, expected["projection"]), projection)
            self.check("branch-at-16-05-suppression", "branch-1605.explicit-assumption", branch.get("scenario") == expected["branch_assumption"], branch.get("scenario"))
            self.check("branch-at-16-05-suppression", "branch-1605.exact-v2-claim", projection.get("claims") == [expected["v2_claim"]], projection.get("claims"))
            self.check("branch-at-16-05-suppression", "branch-1605.record-retained", projection.get("decision_record", {}).get("recorded_state") == expected["decision_record_state"], projection.get("decision_record"))
            self.check("branch-at-16-05-suppression", "branch-1605.only-v2-considered", projection.get("decision_considers_edges") == expected["considered_edges"], projection.get("decision_considers_edges"))
            self.check("branch-at-16-05-suppression", "branch-1605.incomplete-context", projection.get("claim_context_state") == expected["claim_context_state"], projection.get("claim_context_state"))
            self.check("branch-at-16-05-suppression", "branch-1605.no-conflict", projection.get("conflict") == expected["conflict"], projection.get("conflict"))
            self.check("branch-at-16-05-suppression", "branch-1605.forbidden-v3-literals", not any(item in serialized for item in expected["forbidden_v3_literals"]), serialized)
            forbidden_paths = expected["forbidden_v3_paths"]
            self.check("branch-at-16-05-suppression", "branch-1605.forbidden-v3-paths", not any(all(part in serialized for part in path) for path in forbidden_paths), serialized)
            self.check("branch-at-16-05-suppression", "branch-1605.forbidden-v3-relationships", not any(all(edge.get(key) in serialized for key in ("from", "relationship", "to")) for edge in expected["forbidden_v3_relationships"]), serialized)
            self.check("branch-at-16-05-suppression", "branch-1605.no-v3-count-or-path", expected["v3_count_or_path_exposed"] is False and not any("v3_count" in key.lower() or "path_count" in key.lower() for key in projection), projection)
            self.check("branch-at-16-05-suppression", "branch-1605.no-selection-or-resolution", expected["v2_selected_or_conflict_resolved"] is False and projection.get("conflict") is None and projection.get("current_owner_or_procedure_selected") is False and projection.get("decision_record", {}).get("recorded_state") == "open-unresolved", projection)
            self.observed["branch-1605"] = value

        self.scenario("branch-at-16-05-suppression", branch_1605_group)

        def branch_1606_group() -> None:
            expected = self.cases["branch-at-16-06-restoration"]
            status, value = self.branch(KNOWN_1606)
            branch = self._branch_part(value)
            projection = branch.get("projection", {})
            self.check("branch-at-16-06-restoration", "branch-1606.full-projection", status == 200 and same(projection, expected["projection"]), projection)
            self.check("branch-at-16-06-restoration", "branch-1606.explicit-assumption", branch.get("scenario") == expected["branch_assumption"], branch.get("scenario"))
            self.check("branch-at-16-06-restoration", "branch-1606.evidence-backed", projection.get("claim_context_state") == expected["claim_context_state"], projection.get("claim_context_state"))
            v3 = next((row for row in projection.get("claims", []) if row.get("version_id") == "SOP-OPS-9-V3"), {})
            self.check("branch-at-16-06-restoration", "branch-1606.v3-hypothetical-time", v3.get("source", {}).get("observed_at") == expected["v3_hypothetical_observed_at"], v3)
            self.check("branch-at-16-06-restoration", "branch-1606.unresolved-conflict-edge", projection.get("conflict", {}).get("edge") == expected["conflict_edge"] and projection.get("conflict", {}).get("edge", {}).get("status") == "unresolved", projection.get("conflict"))
            self.check("branch-at-16-06-restoration", "branch-1606.both-considered-links", projection.get("decision_considers_edges") == expected["considered_edges"], projection.get("decision_considers_edges"))
            self.check("branch-at-16-06-restoration", "branch-1606.actual-source-time", branch.get("scenario", {}).get("actual_source_observed_at") == expected["actual_source_observed_at"], branch.get("scenario"))
            self.check("branch-at-16-06-restoration", "branch-1606.not-source-truth", branch.get("scenario", {}).get("scenario_not_source_truth") is expected["scenario_not_source_truth"] and branch.get("scenario", {}).get("source_fact_modified") is False, branch.get("scenario"))
            self.observed["branch-1606"] = value

        self.scenario("branch-at-16-06-restoration", branch_1606_group)

        def isolation_group() -> None:
            expected = self.cases["mainline-isolation"]
            before = self.graph_snapshot("isolation-before")
            count_before = self.graph_counts("isolation-count-before")
            _, at_1604 = self.branch(KNOWN_1604)
            _, at_1605 = self.branch(UNKNOWN_1605)
            _, at_1606 = self.branch(KNOWN_1606)
            _, after_1605 = self.branch(UNKNOWN_1605)
            _, after_1606 = self.branch(KNOWN_1606)
            _, base_1605 = self.timeline(UNKNOWN_1605)
            _, base_1606 = self.timeline(KNOWN_1606)
            after = self.graph_snapshot("isolation-after")
            count_after = self.graph_counts("isolation-count-after")
            v3 = next((row for row in at_1604["mainline"]["claims"] if row["version_id"] == "SOP-OPS-9-V3"), {})
            self.check("mainline-isolation", "isolation.full-mainline-1604", same(at_1604.get("mainline"), expected["mainline_at_16_04"]), at_1604.get("mainline"))
            self.check("mainline-isolation", "isolation.full-mainline-1605", same(at_1605.get("mainline"), expected["mainline_at_16_05"]), at_1605.get("mainline"))
            self.check("mainline-isolation", "isolation.full-mainline-1606", same(at_1606.get("mainline"), expected["mainline_at_16_06"]), at_1606.get("mainline"))
            self.check("mainline-isolation", "isolation.actual-v3-time", v3.get("source", {}).get("observed_at") == expected["v3_source_observed_at"], v3)
            self.check("mainline-isolation", "isolation.mainline-1605-unchanged", same(at_1605.get("mainline"), after_1605.get("mainline")) == expected["mainline_16_05_unchanged_after_branch_query"], {"before": at_1605.get("mainline"), "after": after_1605.get("mainline")})
            self.check("mainline-isolation", "isolation.mainline-1606-unchanged", same(at_1606.get("mainline"), after_1606.get("mainline")) == expected["mainline_16_06_unchanged_after_branch_query"], {"before": at_1606.get("mainline"), "after": after_1606.get("mainline")})
            self.check("mainline-isolation", "isolation.base-endpoint-unchanged", expected["base_timeline_16_05_and_16_06_unchanged"] is True and same(base_1605, expected["mainline_at_16_05"]) and same(base_1606, expected["mainline_at_16_06"]), {"16:05": base_1605, "16:06": base_1606})
            source_rows = self.graph_rows("isolation-source-fact", f"MATCH (source:Evidence {{id: '{SOURCE_ID}'}}) RETURN source.observed_at AS observed_at")
            self.check("mainline-isolation", "isolation.source-fact-unchanged", source_rows == [{"observed_at": ACTUAL_SOURCE_OBSERVED_AT}] and expected["stored_source_fact_unchanged"] is True, source_rows)
            self.check("mainline-isolation", "isolation.read-only-branch-query", expected["branch_query_graph_writes"] == 0 and same(before, after) and count_before == count_after, {"counts": [count_before, count_after], "snapshot_equal": same(before, after)})
            self.observed["isolation"] = {"before": before, "after": after, "mainline_1604": at_1604, "mainline_1605": at_1605, "mainline_1606": at_1606, "base_1605": base_1605, "base_1606": base_1606}

        self.scenario("mainline-isolation", isolation_group)

        def missing_edge_group() -> None:
            expected = self.cases["missing-edge-no-fallback-and-reseed"]
            self.reset()
            required = expected["required_branch_edges"]
            labels = self.graph_rows("branch-explicit-node-labels", f"MATCH (a {{id: '{BRANCH_ID}'}}), (b {{id: '{OVERRIDE_ID}'}}), (c:Evidence {{id: '{SOURCE_ID}'}}) RETURN labels(a) AS branch, labels(b) AS override, labels(c) AS evidence")
            root_edge = self.graph_rows("branch-explicit-root-edge", f"MATCH (a:ScenarioBranch {{id: '{BRANCH_ID}'}})-[r:HAS_HYPOTHETICAL_OBSERVATION]->(b:ObservationOverride {{id: '{OVERRIDE_ID}'}}) RETURN a.id AS from, type(r) AS relationship, b.id AS to, r.id AS id")
            source_edge = self.graph_rows("branch-explicit-source-edge", f"MATCH (b:ObservationOverride {{id: '{OVERRIDE_ID}'}})-[r:FOR_SOURCE]->(c:Evidence {{id: '{SOURCE_ID}'}}) RETURN b.id AS from, type(r) AS relationship, c.id AS to, r.id AS id")
            branch_edges = [*root_edge, *source_edge]
            self.check("missing-edge-no-fallback-and-reseed", "edges.required-explicit-links", [{key: row[key] for key in ("from", "relationship", "to")} for row in branch_edges] == required, branch_edges)
            self.check("missing-edge-no-fallback-and-reseed", "edges.explicit-graph-node-labels", labels == [{"branch": "[ScenarioBranch]", "override": "[ObservationOverride]", "evidence": "[Evidence]"}] and all(label in expected["branch_node_labels"] for label in ("ScenarioBranch", "ObservationOverride", "Evidence")), labels)

            baseline_mainline = self.branch(UNKNOWN_1605)[1]["mainline"]
            before_root = self.graph_snapshot("missing-root-before")
            root_delete = f"MATCH (:ScenarioBranch {{id: '{BRANCH_ID}'}})-[r:HAS_HYPOTHETICAL_OBSERVATION {{id: '{BRANCH_EDGES[0]['id']}'}}]->(:ObservationOverride {{id: '{OVERRIDE_ID}'}}) DELETE r"
            root_present = self.graph_rows("missing-root-edge-present", root_delete.replace("DELETE r", "RETURN r.id AS id"))
            self.db.graph_query("remove-only-owned-branch-root-edge", root_delete)
            after_root = self.graph_snapshot("missing-root-after")
            status_root, missing_root = self.branch(UNKNOWN_1605)
            root_branch = self._branch_part(missing_root)
            root_projection = root_branch.get("projection", {})
            root_browser = self.run_browser("missing-branch", "missing-root-edge")
            root_browser_projection = root_browser.get("desktop", {}).get("api", {}).get("hypothetical_branch", {}).get("projection")
            self.check("missing-edge-no-fallback-and-reseed", "edges.missing-root-link-unknown", status_root == 200 and same(root_projection, expected["missing_branch_to_override_edge_result"]["projection"]) and not root_branch.get("scenario") and same(root_browser_projection, expected["missing_branch_to_override_edge_result"]["projection"]), {"status": status_root, "branch": root_branch, "browser_projection": root_browser_projection})
            self.reset()
            before_source = self.graph_snapshot("missing-source-before")
            source_delete = f"MATCH (:ObservationOverride {{id: '{OVERRIDE_ID}'}})-[r:FOR_SOURCE {{id: '{BRANCH_EDGES[1]['id']}'}}]->(:Evidence {{id: '{SOURCE_ID}'}}) DELETE r"
            source_present = self.graph_rows("missing-source-edge-present", source_delete.replace("DELETE r", "RETURN r.id AS id"))
            self.db.graph_query("remove-only-owned-override-source-edge", source_delete)
            after_source = self.graph_snapshot("missing-source-after")
            status_source, missing_source = self.branch(UNKNOWN_1605)
            source_branch = self._branch_part(missing_source)
            source_projection = source_branch.get("projection", {})
            source_browser = self.run_browser("missing-branch", "missing-source-edge")
            source_browser_projection = source_browser.get("desktop", {}).get("api", {}).get("hypothetical_branch", {}).get("projection")
            self.check("missing-edge-no-fallback-and-reseed", "edges.missing-source-link-unknown", status_source == 200 and same(source_projection, expected["missing_override_to_source_edge_result"]["projection"]) and not source_branch.get("scenario") and same(source_browser_projection, expected["missing_override_to_source_edge_result"]["projection"]), {"status": status_source, "branch": source_branch, "browser_projection": source_browser_projection})
            mainline_after = missing_source.get("mainline")
            self.check("missing-edge-no-fallback-and-reseed", "edges.mainline-preserved", same(mainline_after, expected["mainline_at_16_05_after_edge_removal"]) and same(mainline_after, baseline_mainline), mainline_after)

            self.reset()
            status_1605, reseeded_1605 = self.branch(UNKNOWN_1605)
            status_1606, reseeded_1606 = self.branch(KNOWN_1606)
            self.check("missing-edge-no-fallback-and-reseed", "edges.reseed-1605-exact", status_1605 == 200 and same(self._branch_part(reseeded_1605), expected["reseeded_branch_at_16_05"]), self._branch_part(reseeded_1605))
            self.check("missing-edge-no-fallback-and-reseed", "edges.reseed-1606-exact", status_1606 == 200 and same(self._branch_part(reseeded_1606), expected["reseeded_branch_at_16_06"]), self._branch_part(reseeded_1606))
            only_one = (
                root_present == [{"id": BRANCH_EDGES[0]["id"]}]
                and source_present == [{"id": BRANCH_EDGES[1]["id"]}]
                and len(before_root["nodes"]) == len(after_root["nodes"])
                and len(before_root["edges"]) - len(after_root["edges"]) == 1
                and len(before_source["nodes"]) == len(after_source["nodes"])
                and len(before_source["edges"]) - len(after_source["edges"]) == 1
            )
            self.check("missing-edge-no-fallback-and-reseed", "edges.remove-owned-only", only_one, {"root_removed": root_present, "source_removed": source_present, "edge_deltas": [len(before_root["edges"]) - len(after_root["edges"]), len(before_source["edges"]) - len(after_source["edges"])]})
            no_fallback = (
                root_projection == expected["missing_branch_to_override_edge_result"]["projection"]
                and source_projection == expected["missing_override_to_source_edge_result"]["projection"]
                and root_projection != missing_root.get("mainline")
                and source_projection != mainline_after
                and expected["mainline_fallback_used"] is False
            )
            self.check("missing-edge-no-fallback-and-reseed", "edges.no-fallback", no_fallback, {"root_branch": root_projection, "source_branch": source_projection})
            self.check("missing-edge-no-fallback-and-reseed", "edges.reseed-exact-views", same(self._branch_part(reseeded_1605), expected["reseeded_branch_at_16_05"]) and same(self._branch_part(reseeded_1606), expected["reseeded_branch_at_16_06"]), {"16:05": self._branch_part(reseeded_1605), "16:06": self._branch_part(reseeded_1606)})
            self.observed["missing_edges"] = {"root": root_projection, "source": source_projection, "reseeded_1605": reseeded_1605, "reseeded_1606": reseeded_1606, "root_browser": root_browser, "source_browser": source_browser}

        self.scenario("missing-edge-no-fallback-and-reseed", missing_edge_group)

        def acl_group() -> None:
            expected = self.cases["decoy-and-restricted-invariance"]
            self.reset()
            status, clean = self.branch(UNKNOWN_1605)
            self.check("decoy-and-restricted-invariance", "acl.full-clean-operations-view", status == 200 and same(clean, expected["clean_full_operations_projection"]), clean)
            before_browser = self.run_browser("existing", "acl-before")
            self.seed("disconnected-branch-decoy", disconnected_decoy_branch_queries())
            _, decoy = self.branch(UNKNOWN_1605)
            self.check("decoy-and-restricted-invariance", "acl.full-view-after-decoy", same(decoy, expected["after_disconnected_decoy_projection"]), decoy)
            self.check("decoy-and-restricted-invariance", "acl.decoy-invariant", same(decoy, clean) == expected["decoy_projection_semantically_identical"], {"before": clean, "after": decoy})
            graph_before = self.graph_counts("acl-count-before-restricted")
            self.seed("restricted-branch-injection", restricted_source_branch_queries())
            graph_after = self.graph_counts("acl-count-after-restricted")
            status_after, restricted = self.branch(UNKNOWN_1605)
            self.check("decoy-and-restricted-invariance", "acl.full-view-after-restricted", status_after == 200 and same(restricted, expected["after_connected_restricted_injection_projection"]), restricted)
            self.check("decoy-and-restricted-invariance", "acl.full-api-invariant", same(restricted, clean) == expected["full_operations_projection_invariant"], {"before": clean, "after": restricted})
            after_browser = self.run_browser("existing", "acl-after")
            before_dom = [(before_browser["desktop"]["text"], before_browser["desktop"]["accessibility_tree"]), (before_browser["mobile"]["text"], before_browser["mobile"]["accessibility_tree"])]
            after_dom = [(after_browser["desktop"]["text"], after_browser["desktop"]["accessibility_tree"]), (after_browser["mobile"]["text"], after_browser["mobile"]["accessibility_tree"])]
            self.check("decoy-and-restricted-invariance", "acl.browser-accessibility-invariant", before_dom == after_dom and expected["browser_text_and_accessibility_tree_invariant"] is True, {"before": before_dom, "after": after_dom})
            serialized = json.dumps({"api": restricted, "browser": after_browser}, sort_keys=True).lower()
            self.check("decoy-and-restricted-invariance", "acl.no-restricted-tripwires", expected["restricted_literals_disclosed"] == [] and not any(token.lower() in serialized for token in RESTRICTED_TRIPWIRES), {"hidden_tripwires_absent": not any(token.lower() in serialized for token in RESTRICTED_TRIPWIRES)})
            self.check("decoy-and-restricted-invariance", "acl.no-count-or-existence-leak", expected["hidden_count_or_existence_disclosed"] is False and graph_after["nodes"] > graph_before["nodes"] and not any("count" in key.lower() or "existence" in key.lower() for key in restricted) and not any(token.lower() in serialized for token in RESTRICTED_TRIPWIRES), {"graph_counts": [graph_before, graph_after], "top_level_keys": list(restricted)})
            self.observed["acl"] = {"clean": clean, "decoy": decoy, "restricted": restricted, "graph_counts": [graph_before, graph_after], "browser_before": before_browser, "browser_after": after_browser}

        self.scenario("decoy-and-restricted-invariance", acl_group)

        def repeat_group() -> None:
            expected = self.cases["repeat-reconnect-read-only-desktop-mobile-cleanup"]
            self.reset()
            first_counts = self.graph_counts("repeat-first-seed-counts")
            first_1605 = self.branch(UNKNOWN_1605)[1]
            self.reset()
            second_counts = self.graph_counts("repeat-second-seed-counts")
            baseline = self.branch(UNKNOWN_1605)[1]
            self.check("repeat-reconnect-read-only-desktop-mobile-cleanup", "repeat.two-seeds", expected["seed_runs"] == 2, {"runs": 2})
            self.check("repeat-reconnect-read-only-desktop-mobile-cleanup", "repeat.zero-node-delta", second_counts["nodes"] - first_counts["nodes"] == expected["duplicate_node_delta_after_second_seed"], {"first": first_counts, "second": second_counts})
            self.check("repeat-reconnect-read-only-desktop-mobile-cleanup", "repeat.zero-edge-delta", second_counts["relationships"] - first_counts["relationships"] == expected["duplicate_relationship_delta_after_second_seed"], {"first": first_counts, "second": second_counts})

            old_client = RespConnection("127.0.0.1", self.args.db_port, timeout=4.0)
            old_rows = result_rows(query_result(old_client.command("GRAPH.QUERY", self.db.graph, f"MATCH (branch:ScenarioBranch {{id:'{BRANCH_ID}'}})-[:HAS_HYPOTHETICAL_OBSERVATION]->(override:ObservationOverride {{id:'{OVERRIDE_ID}'}}) RETURN branch.id AS branch_id, override.id AS override_id")))
            old_client.close()
            fresh_client = RespConnection("127.0.0.1", self.args.db_port, timeout=4.0)
            new_rows = result_rows(query_result(fresh_client.command("GRAPH.QUERY", self.db.graph, f"MATCH (branch:ScenarioBranch {{id:'{BRANCH_ID}'}})-[:HAS_HYPOTHETICAL_OBSERVATION]->(override:ObservationOverride {{id:'{OVERRIDE_ID}'}}) RETURN branch.id AS branch_id, override.id AS override_id")))
            fresh_client.close()
            _, reconnected_mainline = self.timeline(UNKNOWN_1605)
            _, branch_1605 = self.branch(UNKNOWN_1605)
            _, branch_1606 = self.branch(KNOWN_1606)
            self.check("repeat-reconnect-read-only-desktop-mobile-cleanup", "repeat.fresh-client", expected["fresh_falkordb_client_required"] is True and old_rows == new_rows == [{"branch_id": BRANCH_ID, "override_id": OVERRIDE_ID}], {"old": old_rows, "new": new_rows})
            self.check("repeat-reconnect-read-only-desktop-mobile-cleanup", "repeat.reconnected-mainline", same(reconnected_mainline, expected["reconnected_mainline_at_16_05"]), reconnected_mainline)
            self.check("repeat-reconnect-read-only-desktop-mobile-cleanup", "repeat.reconnected-branch-1605", same(branch_1605, {"scope": "operations", "mainline": expected["reconnected_mainline_at_16_05"], "hypothetical_branch": expected["reconnected_branch_at_16_05"]}), branch_1605)
            expected_mainline_1606 = dict(expected["reconnected_mainline_at_16_05"])
            expected_mainline_1606["known_at"] = KNOWN_1606
            self.check("repeat-reconnect-read-only-desktop-mobile-cleanup", "repeat.reconnected-branch-1606", same(branch_1606, {"scope": "operations", "mainline": expected_mainline_1606, "hypothetical_branch": expected["reconnected_branch_at_16_06"]}), branch_1606)

            before_counts = self.graph_counts("read-only-before")
            state_before = {path.name: path.read_bytes() for path in self.state_dir.iterdir() if path.is_file()}
            browser = self.run_browser("existing", "repeat-keyboard-mobile")
            after_counts = self.graph_counts("read-only-after")
            state_after = {path.name: path.read_bytes() for path in self.state_dir.iterdir() if path.is_file()}
            files_or_actions = self.state_dir.exists() and state_before == state_after and "confirmed-actions.json" not in state_after and "evidence-watch.json" not in state_after
            no_external = all(host == f"127.0.0.1:{self.args.app_port}" for host in browser.get("hosts", []))
            if self.service is None:
                raise VerificationFailure("owned API service is unavailable")
            no_external = no_external and not self.service.pending and not self.service.pending_watches
            self.check("repeat-reconnect-read-only-desktop-mobile-cleanup", "repeat.zero-write-counts", before_counts == after_counts and files_or_actions and no_external and expected["query_and_browser_write_counts"] == {"graph_nodes": 0, "graph_relationships": 0, "local_actions": 0, "files": 0, "external_writes": 0}, {"counts": [before_counts, after_counts], "state_files": list(state_after), "external_hosts": browser.get("hosts", [])})
            desktop_at_1606 = browser.get("keyboard", {})
            mobile = browser.get("mobile", {})
            self.check("repeat-reconnect-read-only-desktop-mobile-cleanup", "repeat.desktop-mobile-api-equality", same(desktop_at_1606.get("api"), mobile.get("api")) and desktop_at_1606.get("text") == mobile.get("text") and desktop_at_1606.get("accessibility_tree") == mobile.get("accessibility_tree") and expected["desktop_mobile_api_semantically_equal"] is True, {"api_equal": same(desktop_at_1606.get("api"), mobile.get("api")), "text_equal": desktop_at_1606.get("text") == mobile.get("text"), "accessibility_equal": desktop_at_1606.get("accessibility_tree") == mobile.get("accessibility_tree")})
            keyboard_assertions = [item for item in browser.get("assertions", []) if "keyboard" in item.get("name", "").lower()]
            expected_mainline_1606_for_ui = dict(expected["reconnected_mainline_at_16_05"])
            expected_mainline_1606_for_ui["known_at"] = KNOWN_1606
            expected_keyboard_projection = {
                "scope": "operations",
                "mainline": expected_mainline_1606_for_ui,
                "hypothetical_branch": expected["reconnected_branch_at_16_06"],
            }
            self.check("repeat-reconnect-read-only-desktop-mobile-cleanup", "repeat.keyboard-selection-api-equality", expected["keyboard_selection_api_semantically_equal"] is True and bool(keyboard_assertions) and all(item.get("passed") for item in keyboard_assertions) and same(desktop_at_1606.get("api"), expected_keyboard_projection) and desktop_at_1606.get("cutoff") == KNOWN_1606, {"keyboard_assertions": keyboard_assertions, "cutoff": desktop_at_1606.get("cutoff"), "api": desktop_at_1606.get("api")})
            self.observed["repeat"] = {"first": first_1605, "baseline": baseline, "reconnected_mainline": reconnected_mainline, "branch_1605": branch_1605, "branch_1606": branch_1606, "counts": [first_counts, second_counts], "browser": browser}

        self.scenario("repeat-reconnect-read-only-desktop-mobile-cleanup", repeat_group, defer_owned_cleanup=True)

        missing = {group: sorted({item["id"] for item in self.groups[group]["pinned_assertions"]} - self.coverage[group]) for group in REQUIRED_GROUPS}
        if any(missing[group] and not (group == REQUIRED_GROUPS[-1] and missing[group] == ["repeat.owned-cleanup"]) for group in missing):
            raise VerificationFailure(f"frozen assertion coverage incomplete: {missing}")
        if len(self.assertions) != sum(group["nonzero_assertion_count"] for group in self.matrix["groups"]) - 1:
            raise VerificationFailure("actual mapped assertion count before cleanup differs from the frozen contract")

    def stop(self) -> None:
        if self.httpd is not None:
            try:
                self.httpd.shutdown()
                self.httpd.server_close()
                if self.http_thread is not None:
                    self.http_thread.join(timeout=6)
                self.app_stopped = self.http_thread is None or not self.http_thread.is_alive()
            except Exception:
                self.app_stopped = False
        if self.state_dir_owned and self.state_dir.exists():
            shutil.rmtree(self.state_dir)
            self.cleanup["owned_local_state_removed"] = not self.state_dir.exists()
        self.db.stop()
        self.cleanup["owned_graph_deleted"] = bool(self.db.cleanup.get("owned_graph_deleted"))
        self.cleanup["owned_database_process_stopped"] = bool(self.db.cleanup.get("owned_process_stopped"))
        expected_runtime_dir = (ROOT / ".local" / "falkordb-runs" / self.run_id).resolve()
        if self.db.server_log_path is not None and self.db.server_log_path.parent.resolve() == expected_runtime_dir and expected_runtime_dir.exists():
            shutil.rmtree(expected_runtime_dir)
            self.cleanup["owned_database_runtime_dir_removed"] = not expected_runtime_dir.exists()
        self.cleanup["owned_app_stopped"] = self.app_stopped

    def report(self, status: str, error: str | None) -> dict[str, Any]:
        missing = {group: sorted({item["id"] for item in self.groups[group]["pinned_assertions"]} - self.coverage[group]) for group in REQUIRED_GROUPS}
        return {
            "schema_version": 1,
            "status": status,
            "error": error,
            "run_id": self.run_id,
            "graph": self.db.graph,
            "binds": {"database": f"127.0.0.1:{self.args.db_port}", "app": f"127.0.0.1:{self.args.app_port}"},
            "falkordb": self.db.database,
            "groups": self.scenarios,
            "mapped_assertion_count": len(self.assertions),
            "mapped_assertions": self.assertions,
            "missing_matrix_assertions": missing,
            "observed": self.observed,
            "cleanup": self.cleanup,
            "owned_resources_cleaned": all(self.cleanup.values()),
            "query_count": self.db.query_count,
            "timings": self.db.timings,
            "resource_samples": self.db.resource_samples,
        }


def select_ports() -> tuple[int, int, int]:
    try:
        base = int(os.environ["WORKSHOP_PORT_BASE"])
        span = int(os.environ["WORKSHOP_PORT_SPAN"])
        lane = int(os.environ["WORKSHOP_LANE"])
    except (KeyError, ValueError) as exc:
        raise VerificationFailure("WORKSHOP_PORT_BASE, WORKSHOP_PORT_SPAN, and WORKSHOP_LANE are required") from exc
    if base < 1024 or span < 92 or lane < 0:
        raise VerificationFailure("allocated private port range must include offsets +90 and +91")
    db_port, app_port = base + 90, base + 91
    if app_port >= base + span:
        raise VerificationFailure("allocated private port range does not contain both selected offsets")
    return lane, db_port, app_port


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    for flag in (
        "require-live-falkordb", "all-eight-groups", "assert-api", "desktop-mobile",
        "assert-keyboard", "assert-mainline-immutable", "assert-no-fallback",
        "assert-reconnect", "assert-owned-cleanup",
    ):
        parser.add_argument("--" + flag, action="store_true")
    parser.add_argument("--report", type=Path)
    parser.add_argument("--evidence-dir", type=Path)
    args = parser.parse_args(argv)
    required = (
        args.require_live_falkordb, args.all_eight_groups, args.assert_api,
        args.desktop_mobile, args.assert_keyboard, args.assert_mainline_immutable,
        args.assert_no_fallback, args.assert_reconnect, args.assert_owned_cleanup,
    )
    if not all(required):
        parser.error("all actual-FalkorDB, eight-group, API, browser, keyboard, immutability, no-fallback, reconnect and cleanup flags are mandatory")

    verifier: SourceBranchLive | None = None
    status = "PASS"
    error = None
    try:
        lane, db_port, app_port = select_ports()
        expected = json.loads(EXPECTED_PATH.read_text(encoding="utf-8"))
        matrix = json.loads(MATRIX_PATH.read_text(encoding="utf-8"))
        if [row["id"] for row in expected.get("groups", [])] != REQUIRED_GROUPS or [row["group_id"] for row in matrix.get("groups", [])] != REQUIRED_GROUPS:
            raise VerificationFailure("frozen oracle and matrix do not contain the exact eight required groups in order")
        if any(type(group.get("nonzero_assertion_count")) is not int or group["nonzero_assertion_count"] <= 0 for group in matrix["groups"]):
            raise VerificationFailure("every frozen source-branch group must have nonzero literal assertions")
        if matrix.get("candidate_output_is_oracle") is not False:
            raise VerificationFailure("source-branch matrix must reject candidate-derived expectations")
        evidence_dir = args.evidence_dir or Path("/workspace/evidence/decision-source-branch") / f"lane-{lane}"
        evidence_dir.mkdir(parents=True, exist_ok=True)
        args.evidence_dir = evidence_dir
        args.lane, args.db_port, args.app_port = lane, db_port, app_port
        args.report = args.report or evidence_dir / f"live-{uuid.uuid4().hex}.json"
        verifier = SourceBranchLive(args, expected, matrix)
        verifier.run()
    except Exception as exc:
        status = "FAIL"
        error = f"{type(exc).__name__}: {exc}"
    finally:
        if verifier is not None:
            verifier.stop()
            if status == "PASS":
                try:
                    verifier.check(
                        "repeat-reconnect-read-only-desktop-mobile-cleanup",
                        "repeat.owned-cleanup",
                        all(verifier.cleanup.values()),
                        dict(verifier.cleanup),
                    )
                except Exception as exc:
                    status = "FAIL"
                    error = f"owned cleanup assertion failed: {type(exc).__name__}: {exc}"
                else:
                    repeat_status = next((item for item in verifier.scenarios if item["id"] == REQUIRED_GROUPS[-1]), None)
                    if repeat_status is not None:
                        repeat_status["status"] = "pass"
                        repeat_status["assertion_count"] = len(verifier.coverage[REQUIRED_GROUPS[-1]])
            if status == "PASS" and len(verifier.assertions) != sum(group["nonzero_assertion_count"] for group in verifier.matrix["groups"]):
                status = "FAIL"
                error = f"final mapped assertion count mismatch: {len(verifier.assertions)}"
            if status == "PASS" and any(verifier.coverage[group] != {item["id"] for item in verifier.groups[group]["pinned_assertions"]} for group in REQUIRED_GROUPS):
                status = "FAIL"
                error = "one or more frozen mapped assertions were not executed"
            report = verifier.report(status, error)
            report["oracle_sha256"] = __import__("hashlib").sha256(EXPECTED_PATH.read_bytes()).hexdigest()
            report["matrix_sha256"] = __import__("hashlib").sha256(MATRIX_PATH.read_bytes()).hexdigest()
            args.report.parent.mkdir(parents=True, exist_ok=True)
            args.report.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        else:
            report = {"status": status, "error": error}
            print(f"decision source branch live: FAIL: {error}", file=sys.stderr)
    if verifier is None:
        return 1
    if status != "PASS":
        print(f"decision source branch live: FAIL: {error}; report={args.report}", file=sys.stderr)
        return 1
    print(f"decision source branch live: PASS (8 groups, {len(verifier.assertions)} pinned assertions, actual FalkorDB/API, desktop+mobile keyboard replay, no-fallback/reconnect and owned cleanup; report={args.report})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

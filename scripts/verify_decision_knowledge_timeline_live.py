#!/usr/bin/env python3
"""Exercise the pinned twelve-outcome source-time replay on actual FalkorDB.

This owns one lane-named graph, loopback API, local state directory, and browser
run. All values come from the fixed timeline contract, never candidate output.
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
from wardatlas_decisions import disconnected_decoy_queries, restricted_decision_queries
from wardatlas_fixtures import restricted_fixture_queries


ROOT = Path(__file__).resolve().parents[1]
EXPECTED_PATH = ROOT / "contracts/decision-knowledge-timeline/expected-results.json"
MATRIX_PATH = ROOT / "contracts/decision-knowledge-timeline/adversarial-matrix.json"
REQUIRED_GROUPS = [
    "cold-empty-unknown", "before-first-observation", "v2-observation-boundary",
    "v2-unchanged-before-v3", "v3-and-graph-derived-conflict",
    "pre-record-evidence-no-existence-leak", "record-source-boundary",
    "v3-observation-time-shift", "separate-evidence-edge-removals",
    "decoy-and-restricted-invariance", "timestamp-normalization-and-rejection",
    "reseed-reconnect-read-only-browser-cleanup",
]


def canonical(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: canonical(value[key]) for key in sorted(value)}
    if isinstance(value, list):
        return [canonical(item) for item in value]
    return value


def same(left: Any, right: Any) -> bool:
    return canonical(left) == canonical(right)


def get_pointer(value: Any, pointer: str) -> Any:
    if not pointer.startswith("/"):
        raise VerificationFailure(f"invalid expected JSON pointer: {pointer}")
    for part in pointer[1:].split("/"):
        key = part.replace("~1", "/").replace("~0", "~")
        value = value[int(key)] if isinstance(value, list) else value[key]
    return value


def request(base_url: str, path: str, method: str = "GET", payload: dict | None = None) -> tuple[int, Any]:
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    req = Request(base_url + path, data=body, method=method, headers={"Content-Type": "application/json", "Accept": "application/json"})
    try:
        with urlopen(req, timeout=20) as response:
            status, raw = response.status, response.read()
    except HTTPError as exc:
        status, raw = exc.code, exc.read()
    try:
        return status, json.loads(raw.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return status, raw.decode("utf-8", "replace")


class TimelineLive:
    def __init__(self, args: argparse.Namespace, expected: dict[str, Any], matrix: dict[str, Any]):
        self.args = args
        self.expected = expected
        self.matrix = matrix
        self.cases = {row["id"]: row["expected"] for row in expected["cases"]}
        self.groups = {row["group_id"]: row for row in matrix["groups"]}
        self.run_id = f"lane{args.lane}_{uuid.uuid4().hex}"
        self.db = LiveVerifier(args.db_port, expected, matrix)
        self.db.run_id = self.run_id
        self.db.graph = f"wardatlas_test_timeline_{self.run_id}"
        self.base_url = f"http://127.0.0.1:{args.app_port}"
        self.state_dir = ROOT / ".local" / "decision-knowledge-timeline-runs" / self.run_id
        self.state_dir_owned = False
        self.service: WardAtlasService | None = None
        self.httpd: WardAtlasHTTPServer | None = None
        self.http_thread: threading.Thread | None = None
        self.app_stopped = False
        self.coverage: dict[str, set[str]] = {gid: set() for gid in REQUIRED_GROUPS}
        self.assertions: list[dict[str, Any]] = []
        self.observed: dict[str, Any] = {}
        self.cleanup = {
            "owned_graph_deleted": False,
            "owned_database_process_stopped": False,
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
        item = {"group_id": group_id, "assertion_id": assertion_id, "passed": bool(passed)}
        if detail is not None:
            item["detail"] = detail
        self.assertions.append(item)
        if not passed:
            raise VerificationFailure(f"{group_id}/{assertion_id} failed: {detail!r}")

    def scenario(self, group_id: str, callback) -> None:
        row = {"id": group_id, "status": "running"}
        self.db.scenarios.append(row)
        started = time.monotonic()
        try:
            callback()
            missing = {item["id"] for item in self.groups[group_id]["pinned_assertions"]} - self.coverage[group_id]
            if missing:
                raise VerificationFailure(f"unexecuted mapped assertions: {sorted(missing)}")
            row["status"] = "pass"
        except Exception as exc:
            row["status"] = "fail"
            row["error"] = f"{type(exc).__name__}: {exc}"
            raise
        finally:
            row["assertion_count"] = len(self.coverage[group_id])
            row["elapsed_ms"] = round((time.monotonic() - started) * 1000, 3)

    def get(self, path: str) -> tuple[int, Any]:
        return request(self.base_url, path)

    def post(self, path: str, payload: dict | None = None) -> tuple[int, Any]:
        return request(self.base_url, path, "POST", payload or {})

    def timeline(self, cutoff: str | None) -> tuple[int, Any]:
        values = {"scope": "operations"}
        if cutoff is not None:
            values["known_at"] = cutoff
        return self.get("/api/decision-timeline?" + urlencode(values))

    def graph_rows(self, label: str, query: str) -> list[dict[str, Any]]:
        return self.db.graph_rows(label, query)

    def graph_snapshot(self, label: str) -> dict[str, Any]:
        nodes = self.graph_rows(label + "-nodes", "MATCH (n) RETURN n.id AS id, labels(n) AS labels, properties(n) AS props ORDER BY id")
        edges = self.graph_rows(label + "-edges", "MATCH (a)-[r]->(b) RETURN a.id AS from_id, type(r) AS kind, r.id AS id, properties(r) AS props, b.id AS to_id ORDER BY id")
        return {"nodes": nodes, "edges": edges}

    def graph_counts(self, label: str) -> dict[str, int]:
        nodes = self.graph_rows(label + "-node-count", "MATCH (n) RETURN count(n) AS count")
        edges = self.graph_rows(label + "-edge-count", "MATCH ()-[r]->() RETURN count(r) AS count")
        return {"nodes": int(nodes[0]["count"]), "relationships": int(edges[0]["count"])}

    def reset(self) -> None:
        status, result = self.post("/api/scenario/reset")
        if status != 200 or result.get("status") != "reset":
            raise VerificationFailure(f"owned scenario reset failed: {result!r}")

    def sop_impact(self) -> Any:
        status, body = self.get("/api/sop-impact?scope=operations&as_of=2026-10-14T18%3A30%3A00Z")
        if status != 200:
            raise VerificationFailure(f"existing SOP-impact API failed: {status} {body!r}")
        return body

    def run_browser(self, mode: str, label: str) -> dict[str, Any]:
        report_path = self.args.evidence_dir / f"timeline-browser-{label}.json"
        screenshot_dir = self.args.evidence_dir / f"screenshots-{label}"
        command = ["node", str(ROOT / "scripts/decision_knowledge_timeline_browser_assertions.cjs"), self.base_url, mode, str(report_path), str(screenshot_dir)]
        result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, timeout=200)
        if result.returncode != 0 or not report_path.is_file():
            raise VerificationFailure(f"browser {label} failed: {(result.stdout + result.stderr)[-5000:]}")
        report = json.loads(report_path.read_text(encoding="utf-8"))
        if report.get("status") != "PASS" or not report.get("assertion_count"):
            raise VerificationFailure(f"browser {label} did not pass: {report}")
        self.observed[f"browser-{label}"] = report
        return report

    def start(self) -> None:
        explicit_bind_preflight(self.args.app_port)
        self.db.launch()
        # Create and inspect only the uniquely named graph owned by LiveVerifier.
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
                if status == 200 and body.get("database") == "FalkorDB":
                    return
            except Exception:
                time.sleep(0.05)
        raise VerificationFailure("owned loopback API did not become ready")

    def _known(self, actual: dict[str, Any], cutoff: str) -> bool:
        return actual.get("known_at") == cutoff

    def run(self) -> None:
        self.start()
        path_oracle = self.expected["baseline"]["existing_incident_impact_paths"]
        empty_docket_status, empty_docket = self.get("/api/decision-docket?scope=operations")

        def cold_group() -> None:
            status, actual = self.timeline(None)
            expected = self.cases["cold-empty-unknown"]["projection"]
            serialized = json.dumps(actual, sort_keys=True)
            self.check("cold-empty-unknown", "cold.generic-unknown", status == 200 and actual.get("generic_unknown") is expected["generic_unknown"], actual)
            self.check("cold-empty-unknown", "cold.no-claim-rows", actual.get("claims") == expected["claims"], actual)
            self.check("cold-empty-unknown", "cold.no-record", actual.get("decision_record") == expected["decision_record"], actual)
            self.check("cold-empty-unknown", "cold.no-visible-ids", not any(token in serialized for token in ("SOP-OPS-9", "DEC-OPS-9", "REGISTER-OPS", "SRC-SOP", "ROLE-")), serialized)
            self.check("cold-empty-unknown", "cold.no-visible-titles", not any(token in serialized for token in ("Synthetic", "Reconciliation", "Current Text")), serialized)
            self.check("cold-empty-unknown", "cold.no-count-fields", not any("count" in key.lower() for key in actual), actual)
            self.check("cold-empty-unknown", "cold.no-count-or-existence", actual.get("generic_unknown") is True and "existence_hint" not in serialized and not any(token in serialized for token in ("DEC-OPS", "SOP-OPS", "SRC-")), actual)
            self.check("cold-empty-unknown", "cold.no-negative-conclusion", actual.get("negative_conclusion_asserted") is expected["negative_conclusion_asserted"], actual)
            self.observed["cold"] = actual
            self.observed["cold_docket"] = empty_docket
        # The expected group has eight checks; cold docket state is retained as
        # additional evidence without adding an unmapped matrix assertion.

        # Run the real browser against the cold graph; its explicit reset seeds
        # only this app's owned graph, then exercises the four arrival cutoffs.
        self.scenario("cold-empty-unknown", cold_group)
        browser = self.run_browser("cold-seed-replay", "baseline")
        self.observed["browser-baseline"] = browser
        impact_before = self.sop_impact()

        def before_group() -> None:
            expected = self.cases["before-first-observation"]
            status, actual = self.timeline(expected["known_at"])
            projection = expected["projection"]
            serialized = json.dumps(actual, sort_keys=True)
            self.check("before-first-observation", "before.time", status == 200 and actual.get("known_at") == expected["known_at"], actual)
            self.check("before-first-observation", "before.generic-unknown", actual.get("generic_unknown") == projection["generic_unknown"] and actual.get("claims") == [], actual)
            self.check("before-first-observation", "before.no-sources", actual.get("claims") == expected["source_or_version_rows"], actual)
            self.check("before-first-observation", "before.no-decision-ids", not any(token in serialized for token in ("DEC-OPS-9", "REGISTER-OPS", "SRC-DEC-OPS")), serialized)
            self.check("before-first-observation", "before.no-count-fields", not any("count" in key.lower() for key in actual), actual)
            self.check("before-first-observation", "before.no-count-or-existence", "existence_hint" not in serialized and not any(token in serialized for token in ("DEC-OPS-9", "REGISTER-OPS")), actual)
            self.check("before-first-observation", "before.not-negative", actual.get("negative_conclusion_asserted") is projection["negative_conclusion_asserted"], actual)
            self.observed["before"] = actual
        self.scenario("before-first-observation", before_group)

        def v2_group() -> None:
            expected = self.cases["v2-observation-boundary"]
            status, actual = self.timeline(expected["projection"]["known_at"])
            claim = actual.get("claims", [{}])[0]
            self.check("v2-observation-boundary", "v2.exact-cutoff", status == 200 and actual.get("known_at") == expected["projection"]["known_at"], actual)
            self.check("v2-observation-boundary", "v2.only-source-id", [row["source"]["id"] for row in actual.get("claims", [])] == expected["visible_source_order"], actual)
            self.check("v2-observation-boundary", "v2.source-path", bool(actual.get("claims")) and all(row["discovery_path"] == expected["claim_discovery_source"] for row in actual["claims"]), actual)
            self.check("v2-observation-boundary", "v2.claim-source-time", claim.get("source", {}).get("observed_at") == expected["projection"]["claims"][0]["source"]["observed_at"], claim)
            self.check("v2-observation-boundary", "v2.claim-edge", claim.get("claim_edge") == expected["projection"]["claims"][0]["claim_edge"], claim)
            self.check("v2-observation-boundary", "v2.claim-role", claim.get("role_id") == expected["projection"]["claims"][0]["role_id"], claim)
            self.check("v2-observation-boundary", "v2.applicability-edge", claim.get("applicability_edge") == expected["projection"]["claims"][0]["applicability_edge"], claim)
            self.check("v2-observation-boundary", "v2.source-id-title", claim.get("source") == expected["projection"]["claims"][0]["source"], claim)
            self.check("v2-observation-boundary", "v2.evidence-edge", claim.get("source", {}).get("evidence_link") == expected["projection"]["claims"][0]["source"]["evidence_link"], claim)
            disclosed = actual.get("decision_record") is not None or bool(actual.get("decision_considers_edges"))
            self.check("v2-observation-boundary", "v2.record-hidden", disclosed == expected["record_or_considers_disclosed"], actual)
            self.check("v2-observation-boundary", "v2.no-conflict-conclusion", (actual.get("conflict") is not None) == expected["conflict_concluded"], actual)
            self.observed["v2"] = actual
        self.scenario("v2-observation-boundary", v2_group)

        def v2_later_group() -> None:
            expected = self.cases["v2-unchanged-before-v3"]
            status, actual = self.timeline(expected["projection"]["known_at"])
            comparable = dict(actual); comparable["known_at"] = "2026-10-14T16:00:00Z"
            earlier = dict(self.observed["v2"])
            serialized = json.dumps(actual, sort_keys=True)
            self.check("v2-unchanged-before-v3", "v2-later.time", status == 200 and actual.get("known_at") == expected["projection"]["known_at"], actual)
            self.check("v2-unchanged-before-v3", "v2-later.full-projection", same(actual, expected["projection"]), actual)
            self.check("v2-unchanged-before-v3", "v2-later.same-projection", same(comparable, earlier), {"actual": actual, "earlier": earlier})
            self.check("v2-unchanged-before-v3", "v2-later.exact-source", [row["source"]["id"] for row in actual["claims"]] == expected["visible_source_order"], actual)
            self.check("v2-unchanged-before-v3", "v2-later.no-future-leak", not any(token in serialized for token in ("SOP-OPS-9-V3", "SRC-SOP-CURRENT-V3", "DEC-OPS-9-REVIEW-01", "CONSIDERS")), serialized)
            self.observed["v2-later"] = actual
        self.scenario("v2-unchanged-before-v3", v2_later_group)

        def v3_group() -> None:
            expected = self.cases["v3-and-graph-derived-conflict"]
            status, actual = self.timeline(expected["projection"]["known_at"])
            claims = actual.get("claims", [])
            conflict = actual.get("conflict")
            serialized = json.dumps(actual, sort_keys=True)
            self.check("v3-and-graph-derived-conflict", "v3.exact-cutoff", status == 200 and actual.get("known_at") == expected["projection"]["known_at"], actual)
            self.check("v3-and-graph-derived-conflict", "v3.v2-and-v3-sources", [row["source"]["id"] for row in claims] == expected["visible_source_order"], claims)
            self.check("v3-and-graph-derived-conflict", "v3.v3-evidence-time", len(claims) == 2 and claims[1]["source"]["observed_at"] == expected["projection"]["claims"][1]["source"]["observed_at"], claims)
            self.check("v3-and-graph-derived-conflict", "v3.v3-claim-edge", len(claims) == 2 and claims[1]["claim_edge"] == expected["projection"]["claims"][1]["claim_edge"], claims)
            self.check("v3-and-graph-derived-conflict", "v3.v3-applicability-edge", len(claims) == 2 and claims[1]["applicability_edge"] == expected["projection"]["claims"][1]["applicability_edge"], claims)
            self.check("v3-and-graph-derived-conflict", "v3.v3-source-id-title", len(claims) == 2 and claims[1]["source"]["id"] == expected["projection"]["claims"][1]["source"]["id"] and claims[1]["source"]["title"] == expected["projection"]["claims"][1]["source"]["title"], claims)
            self.check("v3-and-graph-derived-conflict", "v3.conflict-kind", conflict is not None and conflict["epistemic_type"] == expected["conflict_kind"], conflict)
            self.check("v3-and-graph-derived-conflict", "v3.conflict-edge", conflict is not None and conflict["edge"] == expected["projection"]["conflict"]["edge"], conflict)
            self.check("v3-and-graph-derived-conflict", "v3.record-hidden", actual.get("decision_record") is None and actual.get("decision_considers_edges") == [] and same(actual, expected["projection"]), actual)
            self.check("v3-and-graph-derived-conflict", "v3.not-authoritative", conflict is not None and (conflict["source_quote"] or conflict["authoritative_rule"]) == expected["conflict_is_authoritative_rule_or_source_quote"], conflict)
            self.observed["v3"] = actual
        self.scenario("v3-and-graph-derived-conflict", v3_group)

        def pre_record_group() -> None:
            expected = self.cases["pre-record-evidence-no-existence-leak"]
            status, actual = self.timeline(expected["projection"]["known_at"])
            comparable = dict(actual); comparable["known_at"] = "2026-10-14T16:04:00Z"
            v3 = dict(self.observed["v3"])
            serialized = json.dumps(actual, sort_keys=True)
            self.check("pre-record-evidence-no-existence-leak", "pre-record.time", status == 200 and actual.get("known_at") == expected["projection"]["known_at"], actual)
            self.check("pre-record-evidence-no-existence-leak", "pre-record.full-projection", same(actual, expected["projection"]), actual)
            self.check("pre-record-evidence-no-existence-leak", "pre-record.same-projection", same(comparable, v3), {"actual": actual, "v3": v3})
            self.check("pre-record-evidence-no-existence-leak", "pre-record.no-identity", not any(token in serialized for token in expected["exposed_record_literals"]), serialized)
            self.check("pre-record-evidence-no-existence-leak", "pre-record.no-count", not any("count" in key.lower() for key in actual) and actual.get("claims") == expected["projection"]["claims"], actual)
            self.check("pre-record-evidence-no-existence-leak", "pre-record.no-existence-hint", expected["existence_hint_exposed"] is False and "existence_hint" not in serialized, actual)
            self.check("pre-record-evidence-no-existence-leak", "pre-record.not-absence-claim", actual.get("negative_conclusion_asserted") is expected["no-record-exists_conclusion"], actual)
            self.check("pre-record-evidence-no-existence-leak", "pre-record.record-suppressed", actual.get("decision_record") is None and actual.get("decision_considers_edges") == [] and expected["record_id_title_state_source_and_considers_suppressed"], actual)
            self.observed["pre-record"] = actual
        self.scenario("pre-record-evidence-no-existence-leak", pre_record_group)

        def record_group() -> None:
            expected = self.cases["record-source-boundary"]
            status, actual = self.timeline(expected["projection"]["known_at"])
            record = actual.get("decision_record") or {}
            self.check("record-source-boundary", "record.cutoff", status == 200 and actual.get("known_at") == expected["projection"]["known_at"], actual)
            self.check("record-source-boundary", "record.exact-id", record.get("id") == expected["projection"]["decision_record"]["id"], record)
            self.check("record-source-boundary", "record.own-source-edge", record.get("source", {}).get("evidence_link") == expected["projection"]["decision_record"]["source"]["evidence_link"], record)
            self.check("record-source-boundary", "record.source-time", record.get("source", {}).get("observed_at") == expected["projection"]["decision_record"]["source"]["observed_at"], record)
            self.check("record-source-boundary", "record.state", record.get("recorded_state") == expected["projection"]["decision_record"]["recorded_state"], record)
            self.check("record-source-boundary", "record.register-tracks-edge", record.get("register_edge") == expected["projection"]["decision_record"]["register_edge"], record)
            self.check("record-source-boundary", "record.register-source-path", record.get("provenance_path") == expected["projection"]["decision_record"]["provenance_path"], record)
            self.check("record-source-boundary", "record.considers-v2-v3", actual.get("decision_considers_edges") == expected["projection"]["decision_considers_edges"], actual)
            self.check("record-source-boundary", "record.both-claims-known", len(actual.get("claims", [])) == 2 and all(edge["to"] in {item["version_id"] for item in actual["claims"]} for edge in actual["decision_considers_edges"]), actual)
            self.check("record-source-boundary", "record.no-selection", expected["selected_version"] is None and actual["current_owner_or_procedure_selected"] is False, actual)
            self.check("record-source-boundary", "record.no-action", expected["action_proposed"] is False and actual["contact_or_action_supplied"] is False, actual)
            self.observed["record"] = actual
        self.scenario("record-source-boundary", record_group)

        def shift_group() -> None:
            expected = self.cases["v3-observation-time-shift"]
            mutation = self.db.graph_query(
                "shift-v3-source-time",
                "MATCH (source:Evidence {id:'SRC-SOP-CURRENT-V3', access:'operations'}) SET source.observed_at='2026-10-14T16:06:00Z' RETURN source.observed_at AS observed_at",
            )
            observed_time = self.graph_rows("read-shifted-v3-time", "MATCH (source:Evidence {id:'SRC-SOP-CURRENT-V3'}) RETURN source.observed_at AS observed_at")
            _, at_1605 = self.timeline(expected["at_16_05"]["known_at"])
            _, at_1606 = self.timeline(expected["at_16_06"]["known_at"])
            visible_1605 = json.dumps(at_1605, sort_keys=True)
            forbidden_literals = expected["at_16_05_forbidden_v3_literals"]
            self.check("v3-observation-time-shift", "shift.cutoff", observed_time == [{"observed_at": expected["v3_observed_at_mutated_to"]}], observed_time)
            self.check("v3-observation-time-shift", "shift.16-05-full-projection", same(at_1605, expected["at_16_05"]), at_1605)
            self.check("v3-observation-time-shift", "shift.16-06-full-projection", same(at_1606, expected["at_16_06"]), at_1606)
            self.check("v3-observation-time-shift", "shift.16-05-full-claims", at_1605.get("claims") == expected["at_16_05"]["claims"], at_1605.get("claims"))
            self.check("v3-observation-time-shift", "shift.16-05-v2-only", [item["version_id"] for item in at_1605.get("claims", [])] == ["SOP-OPS-9-V2"], at_1605.get("claims"))
            self.check("v3-observation-time-shift", "shift.16-05.record-retained", at_1605.get("decision_record", {}).get("recorded_state") == expected["at_16_05"]["decision_record"]["recorded_state"], at_1605.get("decision_record"))
            self.check("v3-observation-time-shift", "shift.16-05.considers-v2-only", at_1605.get("decision_considers_edges") == expected["at_16_05"]["decision_considers_edges"], at_1605.get("decision_considers_edges"))
            self.check("v3-observation-time-shift", "shift.16-05.incomplete", at_1605.get("claim_context_state") == expected["at_16_05"]["claim_context_state"], at_1605.get("claim_context_state"))
            self.check("v3-observation-time-shift", "shift.16-05.forbidden-v3-literals", not any(item in visible_1605 for item in forbidden_literals), visible_1605)
            self.check("v3-observation-time-shift", "shift.16-05.forbidden-v3-edges", all(not any(value == item for value in at_1605.values()) for item in expected["at_16_05_forbidden_v3_relationships"]), at_1605)
            self.check("v3-observation-time-shift", "shift.16-05.forbidden-v3-paths", all(not any(value == item for value in at_1605.values()) for item in expected["at_16_05_forbidden_v3_paths"]), at_1605)
            self.check("v3-observation-time-shift", "shift.16-05.no-v3-count-path", expected["at_16_05_v3_count_or_path_exposed"] is False and not any("v3_count" in key.lower() or "path_count" in key.lower() for key in at_1605), at_1605)
            self.check("v3-observation-time-shift", "shift.16-05.no-conflict", (at_1605.get("conflict") is not None) == expected["at_16_05_conflict_edge_exposed"], at_1605.get("conflict"))
            self.check("v3-observation-time-shift", "shift.16-06-v3-source-time", at_1606.get("claims", [{}])[1].get("source", {}).get("observed_at") == expected["at_16_06"]["claims"][1]["source"]["observed_at"], at_1606.get("claims"))
            self.check("v3-observation-time-shift", "shift.16-06-considers-return", at_1606.get("decision_considers_edges") == expected["at_16_06"]["decision_considers_edges"], at_1606.get("decision_considers_edges"))
            _, impact = self.get("/api/sop-impact?scope=operations&as_of=2026-10-14T18%3A30%3A00Z")
            self.check("v3-observation-time-shift", "shift.existing-paths-unchanged", impact.get("impact_paths") == path_oracle, impact.get("impact_paths"))
            self.observed["shift"] = {"mutation": mutation, "at_16_05": at_1605, "at_16_06": at_1606}
            self.observed["shift_browser"] = self.run_browser("shifted-cutoff", "v3-shift")
            self.db.graph_query("restore-v3-source-time", "MATCH (source:Evidence {id:'SRC-SOP-CURRENT-V3', access:'operations'}) SET source.observed_at='2026-10-14T16:04:00Z'")
            if not same(self.timeline("2026-10-14T16:05:00Z")[1], self.observed["record"]):
                raise VerificationFailure("restoring the V3 source timestamp did not restore the exact 16:05 baseline")
        self.scenario("v3-observation-time-shift", shift_group)

        def remove_group() -> None:
            expected = self.cases["separate-evidence-edge-removals"]
            baseline_impact = self.sop_impact()
            self.reset()
            before = self.graph_snapshot("remove-v3-before")
            before_counts = self.graph_counts("remove-v3-count-before")
            delete_v3 = "MATCH (:SOPVersion {id:'SOP-OPS-9-V3'})-[r:EVIDENCED_BY {id:'REL-SOP-V3-EVIDENCE'}]->(:Evidence {id:'SRC-SOP-CURRENT-V3'}) DELETE r"
            removed = self.graph_rows("delete-v3-evidence-edge", delete_v3.replace("DELETE r", "RETURN r.id AS id"))
            self.db.graph_query("remove-v3-evidence-edge", delete_v3)
            after = self.graph_snapshot("remove-v3-after")
            after_counts = self.graph_counts("remove-v3-count-after")
            _, v3_removed_projection = self.timeline("2026-10-14T16:05:00Z")
            v3_json = json.dumps(v3_removed_projection, sort_keys=True)
            self.check("separate-evidence-edge-removals", "remove-v3.only-edge", removed == [{"id": "REL-SOP-V3-EVIDENCE"}] and len(before["nodes"]) == len(after["nodes"]) and len(before["edges"]) - len(after["edges"]) == 1 and before_counts["relationships"] - after_counts["relationships"] == 1, {"removed": removed, "counts": [before_counts, after_counts]})
            self.check("separate-evidence-edge-removals", "remove-v3-v2-remains", v3_removed_projection.get("claims", [{}])[0].get("version_id") == "SOP-OPS-9-V2", v3_removed_projection.get("claims"))
            self.check("separate-evidence-edge-removals", "remove-v3.considers-v2-only", v3_removed_projection.get("decision_considers_edges") == expected["v3_source_evidence_edge_removal"]["projection"]["decision_considers_edges"], v3_removed_projection)
            self.check("separate-evidence-edge-removals", "remove-v3.no-v3-or-conflict", expected["v3_source_evidence_edge_removal"]["v3_or_conflict_exposed"] is False and not any(token in v3_json for token in ("SOP-OPS-9-V3", "SRC-SOP-CURRENT-V3", "SOP_CONFLICT")), v3_removed_projection)
            self.check("separate-evidence-edge-removals", "remove-v3.record-state-retained", v3_removed_projection.get("decision_record", {}).get("recorded_state") == expected["v3_source_evidence_edge_removal"]["record_state_retained"], v3_removed_projection)
            self.check("separate-evidence-edge-removals", "remove-v3.incomplete", v3_removed_projection.get("claim_context_state") == expected["v3_source_evidence_edge_removal"]["claim_context"], v3_removed_projection)
            self.reset()

            before = self.graph_snapshot("remove-record-before")
            before_counts = self.graph_counts("remove-record-count-before")
            delete_record = "MATCH (:DecisionRecord {id:'DEC-OPS-9-REVIEW-01'})-[r:EVIDENCED_BY {id:'REL-DEC-OPS-9-REVIEW-01-EVIDENCE'}]->(:Evidence {id:'SRC-DEC-OPS-9-REVIEW-01'}) DELETE r"
            removed_record = self.graph_rows("delete-record-evidence-edge", delete_record.replace("DELETE r", "RETURN r.id AS id"))
            self.db.graph_query("remove-record-evidence-edge", delete_record)
            after = self.graph_snapshot("remove-record-after")
            after_counts = self.graph_counts("remove-record-count-after")
            _, record_removed_projection = self.timeline("2026-10-14T16:05:00Z")
            docket_status, docket = self.get("/api/decision-docket?scope=operations")
            record_removed = expected["decision_record_source_edge_removal"]
            after_impact = self.sop_impact()
            self.check("separate-evidence-edge-removals", "remove-record.own-edge", removed_record == [{"id": "REL-DEC-OPS-9-REVIEW-01-EVIDENCE"}] and len(before["nodes"]) == len(after["nodes"]) and len(before["edges"]) - len(after["edges"]) == 1 and before_counts["relationships"] - after_counts["relationships"] == 1, {"removed": removed_record, "counts": [before_counts, after_counts]})
            self.check("separate-evidence-edge-removals", "remove-record.facts-suppressed", record_removed_projection.get("decision_record") is None and record_removed["record_facts_suppressed"] is True, record_removed_projection)
            self.check("separate-evidence-edge-removals", "remove-record.preserved-claims", record_removed_projection.get("claims") == record_removed["projection"]["claims"], record_removed_projection)
            self.check("separate-evidence-edge-removals", "remove-record.preserved-conflict", record_removed_projection.get("conflict") == record_removed["projection"]["conflict"], record_removed_projection)
            self.check("separate-evidence-edge-removals", "remove-record.timeline-not-unknown", record_removed_projection.get("generic_unknown") is record_removed["projection"]["generic_unknown"], record_removed_projection)
            self.check("separate-evidence-edge-removals", "remove-record.no-property-citation", record_removed["property_only_provenance_accepted"] is False and record_removed_projection.get("decision_record") is None, record_removed_projection)
            self.check("separate-evidence-edge-removals", "remove-record.generic-unknown", docket_status == 200 and docket.get("state") == "unknown" and docket.get("evidence_state") == "no-evidence" and docket.get("docket_rows") == [], docket)
            lookup = record_removed["record_lookup_result"]
            self.check(
                "separate-evidence-edge-removals", "remove-record.exact-unknown-outcome",
                docket.get("state") == lookup["state"] and docket.get("evidence_state") == lookup["evidence_state"]
                and docket.get("docket_rows") == lookup["docket_rows"]
                and docket.get("no_decision_concluded") == lookup["no_decision_concluded"]
                and docket.get("current_owner_selected") == lookup["current_owner_claimed"]
                and docket.get("conflict_resolution_concluded") == lookup["conflict_resolution_concluded"]
                and "docket_row_count" not in docket and not any(key in docket for key in ("visible_count_exposed", "existence_hint_exposed"))
                and not any(token in json.dumps(docket) for token in ("DEC-OPS-9-REVIEW-01", "SRC-DEC-OPS-9-REVIEW-01", "open-unresolved")),
                docket,
            )
            self.check("separate-evidence-edge-removals", "remove.edges.preserve-impact", record_removed_projection.get("claims") == record_removed["projection"]["claims"] and after_impact.get("impact_paths") == path_oracle and same(after_impact, baseline_impact), {"paths": after_impact.get("impact_paths"), "impact_same": same(after_impact, baseline_impact)})
            self.observed["edge_removals"] = {"v3": v3_removed_projection, "record": record_removed_projection, "record_docket": docket}
            self.reset()
        self.scenario("separate-evidence-edge-removals", remove_group)

        def acl_group() -> None:
            expected = self.cases["decoy-and-restricted-invariance"]
            _, baseline = self.timeline("2026-10-14T16:05:00Z")
            self.check("decoy-and-restricted-invariance", "acl.baseline-full-projection", same(baseline, expected["clean_operations_projection"]), baseline)
            for index, query in enumerate(disconnected_decoy_queries()):
                self.db.graph_query(f"seed-disconnected-decoy-{index}", query)
            _, after_decoy = self.timeline("2026-10-14T16:05:00Z")
            self.check("decoy-and-restricted-invariance", "acl.decoy-full-projection", same(after_decoy, expected["after_disconnected_decoy_projection"]), after_decoy)
            self.check("decoy-and-restricted-invariance", "acl.decoy-invariant", same(after_decoy, baseline) == expected["decoy_projection_semantically_identical"], after_decoy)
            baseline_browser = self.run_browser("baseline-cutoff", "acl-before")
            graph_before = self.graph_counts("acl-count-before")
            for index, query in enumerate([*restricted_fixture_queries(), *restricted_decision_queries()]):
                self.db.graph_query(f"seed-restricted-source-{index}", query)
            graph_after = self.graph_counts("acl-count-after")
            _, after_restricted = self.timeline("2026-10-14T16:05:00Z")
            self.check("decoy-and-restricted-invariance", "acl.restricted-full-projection", same(after_restricted, expected["after_connected_restricted_injection_projection"]), after_restricted)
            self.check("decoy-and-restricted-invariance", "acl.full-view-invariant", same(after_restricted, baseline) == expected["operations_projection_invariant"], after_restricted)
            browser_after = self.run_browser("baseline-cutoff", "acl-after")
            bdesktop = baseline_browser["desktop"]; ad = browser_after["desktop"]
            bmobile = baseline_browser["mobile"]; am = browser_after["mobile"]
            self.check("decoy-and-restricted-invariance", "acl.browser-accessibility-invariant", ad["text"] == bdesktop["text"] and ad["accessibility_tree"] == bdesktop["accessibility_tree"] and am["text"] == bmobile["text"] and am["accessibility_tree"] == bmobile["accessibility_tree"], {"before": [bdesktop, bmobile], "after": [ad, am]})
            combined = json.dumps({"api": after_restricted, "browser": browser_after}, sort_keys=True)
            self.check("decoy-and-restricted-invariance", "acl.no-leak", expected["restricted_ids_titles_facts_counts_existence_edges_paths_or_paraphrases_exposed"] is False and not any(token.lower() in combined.lower() for token in ("DEC-RESTRICTED", "SRC-RESTRICTED", "ROLE-RESTRICTED", "north annex diversion", "canary 77")), combined[:1800])
            self.check("decoy-and-restricted-invariance", "acl.no-tripwires", expected["restricted_literals_disclosed"] == [] and not any(token in combined for token in ("Restricted Decision Docket Canary 77", "Restricted Synthetic Decision Source 77")), combined[:1800])
            self.check("decoy-and-restricted-invariance", "acl.no-hidden-count", expected["hidden_count_or_existence_disclosed"] is False and graph_after["nodes"] > graph_before["nodes"] and not any("restricted" in key.lower() or "existence" in key.lower() for key in after_restricted), {"counts": [graph_before, graph_after], "projection": after_restricted})
            self.observed["acl"] = {"baseline": baseline, "decoy": after_decoy, "restricted": after_restricted, "graph_counts": [graph_before, graph_after], "browser_before": baseline_browser, "browser_after": browser_after}
            self.reset()
        self.scenario("decoy-and-restricted-invariance", acl_group)

        def time_group() -> None:
            expected = self.cases["timestamp-normalization-and-rejection"]
            before_counts = self.graph_counts("timestamp-before-counts")
            action_path = self.state_dir / "confirmed-actions.json"
            before_action = action_path.read_bytes() if action_path.exists() else None
            status, offset = self.timeline(expected["valid_offset_input"])
            self.check("timestamp-normalization-and-rejection", "time.offset-input", status == 200, offset)
            self.check("timestamp-normalization-and-rejection", "time.utc-normalization", offset.get("known_at") == expected["normalized_known_at"], offset)
            self.check("timestamp-normalization-and-rejection", "time.full-result", same(offset, expected["normalized_projection"]), offset)
            _, utc = self.timeline(expected["normalized_known_at"])
            self.check("timestamp-normalization-and-rejection", "time.same-result", same(offset, utc) and same(offset, self.observed["record"]), {"offset": offset, "utc": utc})
            malformed_status, malformed = self.timeline(expected["malformed_timestamp"])
            self.check("timestamp-normalization-and-rejection", "time.malformed-rejected", malformed_status == 400 and expected["malformed_rejected"] is True, {"status": malformed_status, "body": malformed})
            naive_status, naive = self.timeline("2026-10-14T16:05:00")
            self.check("timestamp-normalization-and-rejection", "time.naive-rejected", naive_status == 400 and expected["naive_rejected"] is True, {"status": naive_status, "body": naive})
            after_counts = self.graph_counts("timestamp-after-counts")
            after_action = action_path.read_bytes() if action_path.exists() else None
            self.check("timestamp-normalization-and-rejection", "time.reject-before-graph-write", before_counts == after_counts and expected["rejection_precedes_graph_mutation"] is True, {"before": before_counts, "after": after_counts})
            self.check("timestamp-normalization-and-rejection", "time.reject-before-action-write", before_action == after_action and expected["rejection_precedes_local_action_mutation"] is True, {"before": before_action, "after": after_action})
            self.check("timestamp-normalization-and-rejection", "time.no-local-clock-fallback", expected["falls_back_to_local_or_current_time"] is False and malformed_status == naive_status == 400, {"malformed": malformed_status, "naive": naive_status})
            self.observed["time"] = {"offset": offset, "malformed": malformed, "naive": naive}
        self.scenario("timestamp-normalization-and-rejection", time_group)

        def repeat_group() -> None:
            expected = self.cases["reseed-reconnect-read-only-browser-cleanup"]
            self.reset()
            after_first = self.graph_counts("repeat-after-first-seed")
            _, projection_first = self.timeline("2026-10-14T16:05:00Z")
            self.reset()
            after_second = self.graph_counts("repeat-after-second-seed")
            _, projection = self.timeline("2026-10-14T16:05:00Z")
            self.check("reseed-reconnect-read-only-browser-cleanup", "repeat.two-seeds", expected["fixture_seed_runs"] == 2, {"runs": 2})
            self.check("reseed-reconnect-read-only-browser-cleanup", "repeat.zero-node-delta", after_second["nodes"] - after_first["nodes"] == expected["second_seed_node_delta"], {"after_first": after_first, "after_second": after_second})
            self.check("reseed-reconnect-read-only-browser-cleanup", "repeat.zero-edge-delta", after_second["relationships"] - after_first["relationships"] == expected["second_seed_relationship_delta"], {"after_first": after_first, "after_second": after_second})
            self.check("reseed-reconnect-read-only-browser-cleanup", "repeat.baseline-full-projection", same(projection, expected["exact_projection_after_reseed"]), projection)

            old_client = RespConnection("127.0.0.1", self.args.db_port, timeout=4.0)
            old_reply = old_client.command("GRAPH.QUERY", self.db.graph, "MATCH (record:DecisionRecord {id:'DEC-OPS-9-REVIEW-01'}) RETURN record.id AS id")
            old_rows = result_rows(query_result(old_reply))
            old_client.close()
            new_client = RespConnection("127.0.0.1", self.args.db_port, timeout=4.0)
            new_reply = new_client.command("GRAPH.QUERY", self.db.graph, "MATCH (record:DecisionRecord {id:'DEC-OPS-9-REVIEW-01'}) RETURN record.id AS id")
            new_rows = result_rows(query_result(new_reply))
            new_client.close()
            _, reconnected = self.timeline("2026-10-14T16:05:00Z")
            self.check("reseed-reconnect-read-only-browser-cleanup", "repeat.fresh-client", old_rows == new_rows == [{"id": "DEC-OPS-9-REVIEW-01"}] and expected["fresh_client_required"] is True, {"old": old_rows, "new": new_rows})
            self.check("reseed-reconnect-read-only-browser-cleanup", "repeat.reconnected-full-projection", same(reconnected, expected["fresh_client_projection"]), reconnected)
            pre_read_counts = self.graph_counts("read-only-before")
            action_path = self.state_dir / "confirmed-actions.json"
            action_before = action_path.read_bytes() if action_path.exists() else None
            # API replay and responsive browser retrieval are read-only; this
            # browser run leaves only screenshots/reports in this task's evidence.
            baseline_browser = self.run_browser("baseline-cutoff", "reconnect")
            post_read_counts = self.graph_counts("read-only-after")
            action_after = action_path.read_bytes() if action_path.exists() else None
            self.check("reseed-reconnect-read-only-browser-cleanup", "repeat.zero-graph-node-writes", pre_read_counts["nodes"] == post_read_counts["nodes"] and expected["query_and_browser_graph_node_writes"] == 0, {"before": pre_read_counts, "after": post_read_counts})
            self.check("reseed-reconnect-read-only-browser-cleanup", "repeat.zero-edge-writes", pre_read_counts["relationships"] == post_read_counts["relationships"] and expected["query_and_browser_graph_relationship_writes"] == 0, {"before": pre_read_counts, "after": post_read_counts})
            self.check("reseed-reconnect-read-only-browser-cleanup", "repeat.zero-action-writes", action_before == action_after and expected["query_and_browser_local_action_writes"] == 0 and not self.service.pending, {"before": action_before, "after": action_after})
            self.check("reseed-reconnect-read-only-browser-cleanup", "repeat.api-desktop-mobile", same(baseline_browser["desktop"]["api"], projection) and same(baseline_browser["mobile"]["api"], projection) and baseline_browser["desktop"]["text"] == baseline_browser["mobile"]["text"], baseline_browser)
            graph = self.db.graph
            owned = graph.startswith(f"wardatlas_test_timeline_lane{self.args.lane}_") and self.state_dir_owned and self.state_dir.is_dir() and self.state_dir.resolve().is_relative_to(ROOT)
            self.check("reseed-reconnect-read-only-browser-cleanup", "repeat.owned-cleanup", owned and expected["cleanup_deletes_only_task_owned_unique_graph_and_state"] is True, {"graph": graph, "state_dir": str(self.state_dir), "owned": self.state_dir_owned})
            self.observed["repeat"] = {"first": projection_first, "reseeded": projection, "reconnected": reconnected, "counts": [after_first, after_second], "browser": baseline_browser}

        self.scenario("reseed-reconnect-read-only-browser-cleanup", repeat_group)

        missing = {gid: sorted({a["id"] for a in self.groups[gid]["pinned_assertions"]} - self.coverage[gid]) for gid in REQUIRED_GROUPS}
        if any(missing.values()):
            raise VerificationFailure(f"matrix assertion coverage incomplete: {missing}")
        if len(self.assertions) != sum(group["nonzero_assertion_count"] for group in self.matrix["groups"]):
            raise VerificationFailure("actual assertion count differs from the frozen matrix mapping")

    def stop(self) -> None:
        if self.httpd is not None:
            try:
                self.httpd.shutdown()
                self.httpd.server_close()
                if self.http_thread:
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
        self.cleanup["owned_app_stopped"] = self.app_stopped

    def report(self, status: str, error: str | None) -> dict[str, Any]:
        missing = {gid: sorted({a["id"] for a in self.groups[gid]["pinned_assertions"]} - self.coverage[gid]) for gid in REQUIRED_GROUPS}
        return {
            "schema_version": 1,
            "status": status,
            "error": error,
            "run_id": self.run_id,
            "graph": self.db.graph,
            "binds": {"database": f"127.0.0.1:{self.args.db_port}", "app": f"127.0.0.1:{self.args.app_port}"},
            "falkordb": self.db.database,
            "groups": self.db.scenarios,
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
    if base < 1024 or span < 8 or lane < 0:
        raise VerificationFailure("workshop range must contain at least eight high ports and a decimal lane")
    db_port, app_port = base + 80, base + 81
    if app_port >= base + span:
        raise VerificationFailure("allocated private port range is too small for the selected offsets")
    return lane, db_port, app_port


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--require-live-falkordb", action="store_true")
    parser.add_argument("--all-twelve-groups", action="store_true")
    parser.add_argument("--assert-api", action="store_true")
    parser.add_argument("--desktop-mobile", action="store_true")
    parser.add_argument("--assert-no-future-leak", action="store_true")
    parser.add_argument("--assert-reconnect", action="store_true")
    parser.add_argument("--assert-owned-cleanup", action="store_true")
    parser.add_argument("--report", type=Path)
    parser.add_argument("--evidence-dir", type=Path)
    args = parser.parse_args(argv)
    if not all((args.require_live_falkordb, args.all_twelve_groups, args.assert_api, args.desktop_mobile, args.assert_no_future_leak, args.assert_reconnect, args.assert_owned_cleanup)):
        parser.error("all live FalkorDB, twelve-group, API, desktop/mobile, no-future-leak, reconnect, and cleanup flags are mandatory")
    verifier: TimelineLive | None = None
    status = "PASS"
    error = None
    try:
        lane, db_port, app_port = select_ports()
        expected = json.loads(EXPECTED_PATH.read_text(encoding="utf-8"))
        matrix = json.loads(MATRIX_PATH.read_text(encoding="utf-8"))
        if [row["id"] for row in expected["cases"]] != REQUIRED_GROUPS or [row["group_id"] for row in matrix["groups"]] != REQUIRED_GROUPS:
            raise VerificationFailure("expected-results and matrix do not contain the twelve frozen groups in order")
        if any(type(group.get("nonzero_assertion_count")) is not int or group["nonzero_assertion_count"] <= 0 for group in matrix["groups"]):
            raise VerificationFailure("each frozen group must have nonzero assertion mappings")
        evidence_dir = args.evidence_dir or Path("/workspace/evidence/decision-knowledge-timeline") / f"lane-{lane}"
        evidence_dir.mkdir(parents=True, exist_ok=True)
        args.evidence_dir = evidence_dir
        args.lane, args.db_port, args.app_port = lane, db_port, app_port
        args.report = args.report or evidence_dir / f"live-{uuid.uuid4().hex}.json"
        verifier = TimelineLive(args, expected, matrix)
        verifier.run()
    except Exception as exc:
        status = "FAIL"
        error = f"{type(exc).__name__}: {exc}"
    finally:
        if verifier is not None:
            verifier.stop()
            if status == "PASS" and not all(verifier.cleanup.values()):
                status = "FAIL"
                error = f"owned cleanup incomplete: {verifier.cleanup}"
            report = verifier.report(status, error)
            args.report.parent.mkdir(parents=True, exist_ok=True)
            args.report.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        else:
            report = {"status": status, "error": error}
            print(f"decision knowledge timeline live: FAIL: {error}", file=sys.stderr)
    if verifier is None:
        return 1
    if status != "PASS":
        print(f"decision knowledge timeline live: FAIL: {error}; report={args.report}", file=sys.stderr)
        return 1
    print(f"decision knowledge timeline live: PASS (12 groups, {len(verifier.assertions)} mapped assertions, actual FalkorDB/API, desktop+mobile, mutation/reconnect and owned cleanup; report={args.report})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

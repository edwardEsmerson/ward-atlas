#!/usr/bin/env python3
"""Exercise all nine frozen decision-docket groups on actual FalkorDB/API/UI.

All graphs, server processes, state, fixtures, and screenshots belong to this
one run. The expected values are read directly from the frozen candidate-free
decision packet; API output never supplies expected values.
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
    result_rows,
)
from wardatlas_app import Handler, WardAtlasHTTPServer, WardAtlasService
from wardatlas_decisions import (
    DECISION_ID,
    RECORD_SOURCE_ID,
    REGISTER_ID,
    decision_seed_queries,
    disconnected_decoy_queries,
    restricted_decision_queries,
)
from wardatlas_fixtures import seed_queries
from wardatlas_shift_board import seed_shift_board_queries


ROOT = Path(__file__).resolve().parents[1]
EXPECTED_PATH = ROOT / "tests/decision-provenance-expected-results.json"
MATRIX_PATH = ROOT / "tests/decision-provenance-adversarial-matrix.json"
REQUIRED_GROUPS = [
    "empty-unknown",
    "exact-one-record-baseline",
    "considers-v2-edge-removed",
    "decision-evidence-link-removed",
    "v2-claim-evidence-link-removed",
    "disconnected-decoy-excluded",
    "restricted-source-operations-invariance",
    "idempotent-reseed-restores-baseline",
    "fresh-client-read-only-owned-cleanup",
]


def canonical(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: canonical(value[key]) for key in sorted(value)}
    if isinstance(value, list):
        return [canonical(item) for item in value]
    return value


def same(left: Any, right: Any) -> bool:
    return canonical(left) == canonical(right)


def api_request(base_url: str, path: str, method: str = "GET", payload: dict | None = None) -> tuple[int, Any]:
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    request = Request(base_url + path, data=body, method=method, headers={"Content-Type": "application/json", "Accept": "application/json"})
    try:
        with urlopen(request, timeout=15) as response:
            status, raw = response.status, response.read()
    except HTTPError as exc:
        status, raw = exc.code, exc.read()
    try:
        return status, json.loads(raw.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return status, raw.decode("utf-8", "replace")


class DecisionDocketLive:
    def __init__(self, args: argparse.Namespace, expected: dict[str, Any], matrix: dict[str, Any]):
        self.args = args
        self.expected = expected
        self.matrix = matrix
        self.cases = {row["id"]: row["expected"] for row in expected["cases"]}
        self.groups = {row["group_id"]: row for row in matrix["groups"]}
        self.run_id = f"lane{args.lane}_{uuid.uuid4().hex}"
        self.db = LiveVerifier(args.db_port, expected, matrix)
        self.db.run_id = self.run_id
        self.db.graph = f"wardatlas_test_decision_{self.run_id}"
        self.base_url = f"http://127.0.0.1:{args.app_port}"
        self.state_dir = ROOT / ".local" / "decision-docket-runs" / self.run_id
        self.state_dir_owned = False
        self.service: WardAtlasService | None = None
        self.httpd: WardAtlasHTTPServer | None = None
        self.http_thread: threading.Thread | None = None
        self.app_stopped = False
        self.check_log: list[dict[str, Any]] = []
        self.coverage: dict[str, set[str]] = {key: set() for key in REQUIRED_GROUPS}
        self.observed: dict[str, Any] = {}
        self.cleanup: dict[str, bool] = {
            "owned_graph_deleted": False,
            "owned_database_process_stopped": False,
            "owned_app_stopped": False,
            "owned_local_state_removed": False,
        }

    def check(self, group_id: str, assertion_id: str, passed: bool, detail: Any = None) -> None:
        allowed = {item["id"] for item in self.groups[group_id]["pinned_assertions"]}
        if assertion_id not in allowed:
            raise VerificationFailure(f"unmapped assertion used: {group_id}/{assertion_id}")
        if assertion_id in self.coverage[group_id]:
            raise VerificationFailure(f"mapped assertion executed more than once: {group_id}/{assertion_id}")
        self.coverage[group_id].add(assertion_id)
        self.check_log.append({"group_id": group_id, "assertion_id": assertion_id, "passed": bool(passed), "detail": detail})
        if not passed:
            raise VerificationFailure(f"{group_id}/{assertion_id} failed: {detail!r}")

    def scenario(self, group_id: str, callback) -> None:
        started = time.monotonic()
        case = {"id": group_id, "status": "running"}
        self.db.scenarios.append(case)
        try:
            callback()
            missing = {item["id"] for item in self.groups[group_id]["pinned_assertions"]} - self.coverage[group_id]
            if missing:
                raise VerificationFailure(f"matrix assertions were not executed: {sorted(missing)}")
            case["status"] = "pass"
        except Exception as exc:
            case["status"] = "fail"
            case["error"] = f"{type(exc).__name__}: {exc}"
            raise
        finally:
            case["assertions"] = len(self.coverage[group_id])
            case["elapsed_ms"] = round((time.monotonic() - started) * 1000, 3)

    def get(self, path: str) -> tuple[int, Any]:
        return api_request(self.base_url, path)

    def post(self, path: str, payload: dict | None = None) -> tuple[int, Any]:
        return api_request(self.base_url, path, "POST", payload or {})

    def query_rows(self, label: str, query: str) -> list[dict[str, Any]]:
        return self.db.graph_rows(label, query)

    def graph_snapshot(self, label: str) -> dict[str, Any]:
        nodes = self.query_rows(label + "-nodes", "MATCH (n) RETURN n.id AS id, labels(n) AS labels, properties(n) AS props ORDER BY id")
        edges = self.query_rows(label + "-edges", "MATCH (a)-[r]->(b) RETURN a.id AS from_id, type(r) AS kind, r.id AS id, properties(r) AS props, b.id AS to_id ORDER BY id")
        return {"nodes": nodes, "edges": edges}

    def graph_counts(self, label: str) -> dict[str, int]:
        nodes = self.query_rows(label + "-node-count", "MATCH (n) RETURN count(n) AS count")
        edges = self.query_rows(label + "-edge-count", "MATCH ()-[r]->() RETURN count(r) AS count")
        return {"nodes": int(nodes[0]["count"]), "relationships": int(edges[0]["count"])}

    def docket(self) -> tuple[int, Any]:
        return self.get("/api/decision-docket?scope=operations")

    def impact(self) -> tuple[int, Any]:
        return self.get("/api/sop-impact?scope=operations&as_of=2026-10-14T18%3A30%3A00Z")

    def fixture_queries(self) -> list[str]:
        return [*seed_queries(), *seed_shift_board_queries(), *decision_seed_queries()]

    def start(self) -> None:
        explicit_bind_preflight(self.args.app_port)
        self.db.launch()
        self.db.graph_rows("cold-owned-graph", "MATCH (n) RETURN n.id AS id LIMIT 1")
        self.state_dir.parent.mkdir(parents=True, exist_ok=True)
        self.state_dir.mkdir()
        self.state_dir_owned = True
        self.service = WardAtlasService(self.args.db_port, self.db.graph, self.state_dir)
        self.httpd = WardAtlasHTTPServer(("127.0.0.1", self.args.app_port), Handler, self.service)
        self.http_thread = threading.Thread(target=self.httpd.serve_forever, kwargs={"poll_interval": 0.1}, daemon=True)
        self.http_thread.start()
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            try:
                status, body = self.get("/api/health")
                if status == 200 and body.get("database") == "FalkorDB":
                    return
            except Exception:
                time.sleep(0.05)
        raise VerificationFailure("owned loopback WardAtlas API did not become ready")

    def run_browser(self, mode: str, label: str) -> dict[str, Any]:
        report_path = self.args.evidence_dir / f"decision-docket-browser-{label}.json"
        screenshot_path = self.args.evidence_dir / f"screenshots-{label}"
        command = [
            "node", str(ROOT / "scripts/decision_provenance_browser_assertions.cjs"),
            self.base_url, mode, str(report_path), str(screenshot_path),
        ]
        result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, timeout=150)
        if result.returncode != 0 or not report_path.is_file():
            raise VerificationFailure(f"decision docket browser {label} failed: {(result.stdout + result.stderr)[-5000:]}")
        report = json.loads(report_path.read_text(encoding="utf-8"))
        if report.get("status") != "PASS" or report.get("assertion_count", 0) == 0:
            raise VerificationFailure(f"decision docket browser {label} did not pass: {report}")
        self.observed[f"browser-{label}"] = report
        return report

    def assert_baseline(self, group: str, response: Any) -> None:
        expected = self.expected["baseline"]
        row = response["docket_rows"][0]
        record = row["decision_record"]
        baseline_record = expected["decision_record"]
        self.check(group, "baseline.root-track-edge", response["root_id"] == expected["root_id"] and response["root_tracking_edge"] == expected["root_tracking_edge"], response.get("root_tracking_edge"))
        self.check(group, "baseline.one-record-only", response.get("docket_row_count") == expected["docket_row_count"] == len(response["docket_rows"]), response.get("docket_row_count"))
        self.check(group, "baseline.record-state-source", record["id"] == baseline_record["id"] and record["title"] == baseline_record["title"] and record["recorded_state"] == baseline_record["recorded_state"] and record["recorded_statement"] == baseline_record["recorded_statement"] and record["record_source"]["id"] == baseline_record["record_source"]["id"] and record["record_source"]["title"] == baseline_record["record_source"]["title"] and record["record_source"]["observed_at"] == baseline_record["record_source"]["observed_at"], record)
        self.check(group, "baseline.record-evidence-edge", record["record_source"]["evidence_relationship"] == "EVIDENCED_BY" and record["record_source"]["evidence_link"] == baseline_record["record_source"]["evidence_link"], record["record_source"])
        self.check(group, "baseline.considered-versions", row["considered_edges"] == expected["considered_edges"], row["considered_edges"])
        for assertion, expected_claim in zip(("baseline.v2-claim-provenance", "baseline.v3-claim-provenance"), expected["claims"]):
            actual = next((claim for claim in row["claims"] if claim["version_id"] == expected_claim["version_id"]), None)
            source = expected_claim["source"]
            self.check(group, assertion, actual is not None and actual["claim_edge"] == expected_claim["claim_edge"] and actual["role_id"] == expected_claim["role_id"] and actual["claim_label"] == expected_claim["claim_label"] and actual["source"]["id"] == source["id"] and actual["source"]["title"] == source["title"] and actual["source"]["observed_at"] == source["observed_at"] and actual["source"]["evidence_link"] == source["evidence_link"] and actual["is_current_owner_fact"] is False, actual)
        self.check(group, "baseline.unresolved-conflict", row["conflict_edge"] == expected["conflict_edge"] and row["conflict_edge"]["status"] == "unresolved", row["conflict_edge"])
        self.check(group, "baseline.no-authority-or-action", row["selected_version"] is None and response["current_owner_selected"] is False and response["procedure_selected"] is False and response["contact_supplied"] is False and response["action_proposed"] is False and row["conflict_resolution_concluded"] is False and row["state"] == "open-unresolved" and row["source_order"] == expected["source_order"], row)

    def run(self) -> None:
        self.start()

        def empty_group() -> None:
            status, value = self.docket()
            serialized = json.dumps(value, sort_keys=True)
            self.check("empty-unknown", "empty.no-docket-row", status == 200 and value.get("docket_rows") == [], value)
            self.check("empty-unknown", "empty.generic-unknown", value.get("state") == "unknown" and value.get("evidence_state") == "no-evidence", value)
            self.check("empty-unknown", "empty.no-decision-identifiers", all(token not in serialized for token in ("DEC-OPS-9-REVIEW-01", "SOP-OPS-9 claim reconciliation review", "SRC-DEC-OPS-9-REVIEW-01")), serialized)
            self.check("empty-unknown", "empty.no-count-or-existence", "docket_row_count" not in value and not any("visible_count" in key or "existence_hint" in key for key in value), sorted(value))
            self.check("empty-unknown", "empty.no-negative-conclusion", value.get("no_decision_concluded") is False and value.get("conflict_resolution_concluded") is False, value)
            self.check("empty-unknown", "empty.no-owner-conclusion", value.get("current_owner_selected") is False and value.get("procedure_selected") is False and value.get("contact_supplied") is False, value)
            self.observed["empty"] = value

        self.scenario("empty-unknown", empty_group)

        # The browser itself captures cold UI, activates the existing explicit
        # synthetic reset, and saves desktop/mobile baseline evidence.
        browser_baseline = self.run_browser("seed-baseline", "baseline")
        status, baseline = self.docket()
        if status != 200:
            raise VerificationFailure(f"decision docket API returned {status}: {baseline!r}")
        self.observed["baseline"] = baseline
        self.scenario("exact-one-record-baseline", lambda: self.assert_baseline("exact-one-record-baseline", baseline))

        impact_status, impact_baseline = self.impact()
        if impact_status != 200:
            raise VerificationFailure(f"existing SOP-impact endpoint returned {impact_status}: {impact_baseline!r}")
        paths = self.expected["baseline"]["existing_incident_impact_paths"]

        def counterfactual(group_id: str, edge_id: str, query: str, callback) -> None:
            reset_status, reset = self.post("/api/scenario/reset")
            if reset_status != 200 or reset.get("status") != "reset":
                raise VerificationFailure(f"cannot restore owned scenario before {group_id}: {reset!r}")
            before = self.graph_snapshot(group_id + "-before")
            counts_before = self.graph_counts(group_id + "-count-before")
            row_count = self.query_rows(group_id + "-edge-present", query.replace("DELETE r", "RETURN count(r) AS count"))
            if len(row_count) != 1 or int(row_count[0]["count"]) != 1:
                raise VerificationFailure(f"expected exactly one owned edge before mutation {edge_id}: {row_count!r}")
            deletion = self.db.graph_query(group_id + "-remove-only-edge", query)
            after = self.graph_snapshot(group_id + "-after")
            counts_after = self.graph_counts(group_id + "-count-after")
            edge_ids_before = {row["id"] for row in before["edges"]}
            edge_ids_after = {row["id"] for row in after["edges"]}
            callback(before, after, counts_before, counts_after, edge_ids_before - edge_ids_after, edge_ids_after - edge_ids_before)
            if edge_id not in edge_ids_before:
                raise VerificationFailure(f"owned mutation edge not found in snapshot: {edge_id}")
            self.observed[group_id] = {"before": before, "after": after, "delete_reply": deletion}

        def considered_removed(before, after, c_before, c_after, removed, added):
            status, value = self.docket()
            row = value["docket_rows"][0]
            impact_status, impact = self.impact()
            self.check("considers-v2-edge-removed", "considered.only-v2-edge-removed", removed == {"REL-DEC-OPS-9-CONSIDERS-V2"} and not added and before["nodes"] == after["nodes"] and len(before["edges"]) - len(after["edges"]) == 1 and c_before["relationships"] - c_after["relationships"] == 1, {"removed": sorted(removed), "added": sorted(added), "counts": [c_before, c_after]})
            self.check("considers-v2-edge-removed", "considered.incomplete-unknown", status == 200 and row["state"] == "incomplete/unknown" and value["state"] == "incomplete/unknown", value)
            self.check("considers-v2-edge-removed", "considered.no-complete-pair", row["complete_two_claim_witness_present"] is False and {claim["version_id"] for claim in row["claims"]} != {"SOP-OPS-9-V2", "SOP-OPS-9-V3"}, row)
            self.check("considers-v2-edge-removed", "considered.no-v3-promotion", row["selected_version"] is None and row["current_owner_selected"] is False and row["claims"][0]["version_id"] == "SOP-OPS-9-V3", row)
            self.check("considers-v2-edge-removed", "considered.no-resolution-conclusion", row["conflict_edge"] == self.expected["baseline"]["conflict_edge"] and row["conflict_resolution_concluded"] is False and row["conflict_edge"]["status"] == "unresolved", row)
            self.check("considers-v2-edge-removed", "considered.impact-paths-preserved", impact_status == 200 and impact["impact_paths"] == paths, impact.get("impact_paths"))

        self.scenario("considers-v2-edge-removed", lambda: counterfactual(
            "considers-v2-edge-removed",
            "REL-DEC-OPS-9-CONSIDERS-V2",
            "MATCH (:DecisionRecord {id:'DEC-OPS-9-REVIEW-01'})-[r:CONSIDERS {id:'REL-DEC-OPS-9-CONSIDERS-V2'}]->(:SOPVersion {id:'SOP-OPS-9-V2'}) DELETE r",
            considered_removed,
        ))

        def decision_evidence_removed(before, after, c_before, c_after, removed, added):
            status, value = self.docket()
            prop = self.query_rows("decision-source-properties-remain", "MATCH (d:DecisionRecord {id:'DEC-OPS-9-REVIEW-01'}), (s:Evidence {id:'SRC-DEC-OPS-9-REVIEW-01'}) RETURN d.title AS title, d.recorded_state AS state, s.id AS source_id, s.title AS source_title, s.observed_at AS observed_at")
            impact_status, impact = self.impact()
            self.check("decision-evidence-link-removed", "record-evidence.only-own-edge-removed", removed == {"REL-DEC-OPS-9-REVIEW-01-EVIDENCE"} and not added and before["nodes"] == after["nodes"] and len(before["edges"]) - len(after["edges"]) == 1 and c_before["relationships"] - c_after["relationships"] == 1, {"removed": sorted(removed), "counts": [c_before, c_after]})
            self.check("decision-evidence-link-removed", "record-evidence.source-properties-retained", prop == [{"title": "SOP-OPS-9 claim reconciliation review", "state": "open-unresolved", "source_id": "SRC-DEC-OPS-9-REVIEW-01", "source_title": "Synthetic Decision Register: SOP-OPS-9 Reconciliation", "observed_at": "2026-10-14T16:05:00Z"}], prop)
            self.check("decision-evidence-link-removed", "record-evidence.generic-unknown", status == 200 and value == self.observed["empty"], value)
            self.check("decision-evidence-link-removed", "record-evidence.suppress-record-facts", all(token not in json.dumps(value) for token in ("DEC-OPS-9-REVIEW-01", "SOP-OPS-9 claim reconciliation review", "open-unresolved", "SRC-DEC-OPS-9-REVIEW-01")), value)
            self.check("decision-evidence-link-removed", "record-evidence.reject-property-citation", value.get("evidence_state") == "no-evidence" and value.get("docket_rows") == [] and not prop == [], {"projection": value, "retained_properties": prop})
            self.check("decision-evidence-link-removed", "record-evidence.sop-impact-unchanged", impact_status == 200 and same(impact, impact_baseline), {"baseline": impact_baseline, "after": impact})

        self.scenario("decision-evidence-link-removed", lambda: counterfactual(
            "decision-evidence-link-removed",
            "REL-DEC-OPS-9-REVIEW-01-EVIDENCE",
            "MATCH (:DecisionRecord {id:'DEC-OPS-9-REVIEW-01'})-[r:EVIDENCED_BY {id:'REL-DEC-OPS-9-REVIEW-01-EVIDENCE'}]->(:Evidence {id:'SRC-DEC-OPS-9-REVIEW-01'}) DELETE r",
            decision_evidence_removed,
        ))

        def v2_evidence_removed(before, after, c_before, c_after, removed, added):
            status, value = self.docket()
            row = value["docket_rows"][0]
            impact_status, impact = self.impact()
            v2_link = self.query_rows("v2-source-properties-remain", "MATCH (v:SOPVersion {id:'SOP-OPS-9-V2'}), (s:Evidence {id:'SRC-SOP-CURRENT-V2'}) RETURN v.escalation_role_id AS role_id, s.id AS source_id, s.title AS title, s.observed_at AS observed_at")
            self.check("v2-claim-evidence-link-removed", "claim-evidence.only-v2-edge-removed", removed == {"REL-SOP-V2-EVIDENCE"} and not added and before["nodes"] == after["nodes"] and len(before["edges"]) - len(after["edges"]) == 1 and c_before["relationships"] - c_after["relationships"] == 1, {"removed": sorted(removed), "counts": [c_before, c_after]})
            self.check("v2-claim-evidence-link-removed", "claim-evidence.no-v2-property-reconstruction", all(claim["version_id"] != "SOP-OPS-9-V2" and claim["source"]["id"] != "SRC-SOP-CURRENT-V2" for claim in row["claims"]) and len(v2_link) == 1, {"claims": row["claims"], "retained_properties": v2_link})
            v3 = [claim for claim in row["claims"] if claim["version_id"] == "SOP-OPS-9-V3"]
            expected_v3 = self.expected["baseline"]["claims"][1]
            self.check("v2-claim-evidence-link-removed", "claim-evidence.v3-source-backed-claim-remains", len(v3) == 1 and v3[0]["role_id"] == expected_v3["role_id"] and v3[0]["claim_label"] == expected_v3["claim_label"] and v3[0]["source"]["id"] == expected_v3["source"]["id"] and v3[0]["source"]["title"] == expected_v3["source"]["title"] and v3[0]["source"]["observed_at"] == expected_v3["source"]["observed_at"] and v3[0]["source"]["evidence_link"] == expected_v3["source"]["evidence_link"], v3)
            self.check("v2-claim-evidence-link-removed", "claim-evidence.incomplete-unknown", status == 200 and row["state"] == "incomplete/unknown" and value["state"] == "incomplete/unknown", value)
            self.check("v2-claim-evidence-link-removed", "claim-evidence.no-v3-winner", row["selected_version"] is None and row["current_owner_selected"] is False and row["conflict_edge"]["status"] == "unresolved" and row["conflict_resolution_concluded"] is False, row)
            self.check("v2-claim-evidence-link-removed", "claim-evidence.incident-paths-unchanged", impact_status == 200 and impact["impact_paths"] == paths, impact.get("impact_paths"))
            impact_claim_ids = {item["sop_version_id"] for item in impact["role_claims"]}
            self.check("v2-claim-evidence-link-removed", "claim-evidence.only-unsupported-v2-omitted", "SOP-OPS-9-V2" not in impact_claim_ids and "SOP-OPS-9-V3" in impact_claim_ids and impact["impact_paths"] == paths and impact["winner_selected"] is False, {"claim_ids": sorted(impact_claim_ids), "impact_paths": impact["impact_paths"], "conflict_status": impact["conflict_status"]})

        self.scenario("v2-claim-evidence-link-removed", lambda: counterfactual(
            "v2-claim-evidence-link-removed",
            "REL-SOP-V2-EVIDENCE",
            "MATCH (:SOPVersion {id:'SOP-OPS-9-V2'})-[r:EVIDENCED_BY {id:'REL-SOP-V2-EVIDENCE'}]->(:Evidence {id:'SRC-SOP-CURRENT-V2'}) DELETE r",
            v2_evidence_removed,
        ))

        def decoy_group() -> None:
            reset_status, _ = self.post("/api/scenario/reset")
            _, baseline = self.docket()
            before = self.graph_snapshot("decoy-before")
            for index, query in enumerate(disconnected_decoy_queries()):
                self.db.graph_query(f"disconnected-decoy-seed-{index}", query)
            after = self.graph_snapshot("decoy-after")
            _, current = self.docket()
            root_path = self.query_rows("decoy-root-track-path", "MATCH (:DecisionRegister {id:'REGISTER-OPS-DECISIONS-01'})-[:TRACKS]->(:DecisionRecord {id:'DEC-OPS-9-DECOY-91'}) RETURN count(*) AS count")
            serialized = json.dumps(current, sort_keys=True)
            tripwires = ["DEC-OPS-9-DECOY-91", "SOP-OPS-9-DECOY-91", "ROLE-OPS-9-DECOY-91", "SRC-OPS-9-DECOY-91"]
            self.check("disconnected-decoy-excluded", "decoy.no-root-tracks-path", reset_status == 200 and root_path == [{"count": 0}], root_path)
            self.check("disconnected-decoy-excluded", "decoy.not-returned", all(value not in serialized for value in tripwires), current)
            self.check("disconnected-decoy-excluded", "decoy.baseline-identical", same(current, baseline) and len(after["nodes"]) - len(before["nodes"]) == 4 and len(after["edges"]) - len(before["edges"]) == 4, {"baseline": baseline, "after": current})
            self.observed["decoy"] = current

        self.scenario("disconnected-decoy-excluded", decoy_group)

        browser_before = self.run_browser("existing", "acl-before")

        def restricted_group() -> None:
            _, api_before = self.docket()
            full_view_before = self.get("/api/decision-docket?scope=operations")[1]
            before_counts = self.graph_counts("acl-count-before")
            browser_before_report = browser_before
            for index, query in enumerate(restricted_decision_queries()):
                self.db.graph_query(f"restricted-decision-seed-{index}", query)
            after_counts = self.graph_counts("acl-count-after-injection")
            status, api_after = self.docket()
            browser_after_report = self.run_browser("existing", "acl-after")
            after_browser_counts = self.graph_counts("acl-count-after-browser")
            hidden = self.expected["test_setup_only"]["restricted_branch"]
            serialized = json.dumps({"api": api_after, "browser": browser_after_report}, sort_keys=True)
            forbidden = [hidden[key] for key in ("decision_id", "decision_title", "version_id", "role_id", "source_id", "source_title", "source_body")]
            self.check("restricted-source-operations-invariance", "acl.operations-projection-identical", status == 200 and same(api_before, api_after) and same(full_view_before, api_after), {"before": api_before, "after": api_after})
            self.check("restricted-source-operations-invariance", "acl.counts-identical", api_before.get("docket_row_count") == api_after.get("docket_row_count") == 1 and after_counts["nodes"] - before_counts["nodes"] == 4 and after_counts["relationships"] - before_counts["relationships"] == 5 and after_browser_counts == after_counts, {"visible_before": api_before.get("docket_row_count"), "visible_after": api_after.get("docket_row_count"), "graph_counts": [before_counts, after_counts, after_browser_counts]})
            self.check("restricted-source-operations-invariance", "acl.browser-accessibility-identical", browser_before_report["desktop"]["body_text"] == browser_after_report["desktop"]["body_text"] and browser_before_report["desktop"]["accessibility_tree"] == browser_after_report["desktop"]["accessibility_tree"] and browser_before_report["mobile"]["body_text"] == browser_after_report["mobile"]["body_text"] and browser_before_report["mobile"]["accessibility_tree"] == browser_after_report["mobile"]["accessibility_tree"], {"before_desktop": browser_before_report["desktop"]["accessibility_tree"], "after_desktop": browser_after_report["desktop"]["accessibility_tree"]})
            self.check("restricted-source-operations-invariance", "acl.no-identifiers-or-source-text", all(item not in serialized for item in forbidden), {"forbidden": forbidden, "visible": serialized[:1500]})
            self.check("restricted-source-operations-invariance", "acl.no-count-existence-or-relationship", not any(item in serialized for item in ("DEC-RESTRICTED", "SRC-RESTRICTED", "ROLE-RESTRICTED", "visible_restricted", "hidden_count", "restricted_count", "REL-REGISTER-RESTRICTED-77")), serialized[:2000])
            self.check("restricted-source-operations-invariance", "acl.no-derived-paraphrase", "north annex diversion remains unverified" not in serialized.lower() and "canary 77" not in serialized.lower(), serialized[:2000])
            self.observed["acl"] = {"before": api_before, "after": api_after, "graph_counts": [before_counts, after_counts, after_browser_counts], "browser_before": browser_before_report, "browser_after": browser_after_report}

        self.scenario("restricted-source-operations-invariance", restricted_group)

        def reseed_group() -> None:
            reset_status, _ = self.post("/api/scenario/reset")
            _, exact_baseline = self.docket()
            ids = [
                ("REL-DEC-OPS-9-CONSIDERS-V2", "MATCH (:DecisionRecord {id:'DEC-OPS-9-REVIEW-01'})-[r:CONSIDERS {id:'REL-DEC-OPS-9-CONSIDERS-V2'}]->(:SOPVersion {id:'SOP-OPS-9-V2'}) DELETE r"),
                ("REL-DEC-OPS-9-REVIEW-01-EVIDENCE", "MATCH (:DecisionRecord {id:'DEC-OPS-9-REVIEW-01'})-[r:EVIDENCED_BY {id:'REL-DEC-OPS-9-REVIEW-01-EVIDENCE'}]->(:Evidence {id:'SRC-DEC-OPS-9-REVIEW-01'}) DELETE r"),
                ("REL-SOP-V2-EVIDENCE", "MATCH (:SOPVersion {id:'SOP-OPS-9-V2'})-[r:EVIDENCED_BY {id:'REL-SOP-V2-EVIDENCE'}]->(:Evidence {id:'SRC-SOP-CURRENT-V2'}) DELETE r"),
            ]
            all_restored = True
            second_load_zero = True
            source_order_restored = True
            mutation_results = []
            for edge_id, deletion in ids:
                before = self.graph_counts("reseed-pre-" + edge_id)
                self.db.graph_query("reseed-delete-" + edge_id, deletion)
                for index, statement in enumerate(self.fixture_queries()):
                    self.db.graph_query(f"reseed-{edge_id}-load1-{index}", statement)
                after_first = self.graph_counts("reseed-first-" + edge_id)
                for index, statement in enumerate(self.fixture_queries()):
                    self.db.graph_query(f"reseed-{edge_id}-load2-{index}", statement)
                after_second = self.graph_counts("reseed-second-" + edge_id)
                status, current = self.docket()
                all_restored = all_restored and status == 200 and same(current, exact_baseline)
                second_load_zero = second_load_zero and after_second == after_first
                row = current["docket_rows"][0]
                expected_sources = self.expected["baseline"]["source_order"]
                source_order_restored = source_order_restored and row["source_order"] == expected_sources and [claim["source"]["observed_at"] for claim in row["claims"]] == [claim["source"]["observed_at"] for claim in self.expected["baseline"]["claims"]]
                mutation_results.append({"edge": edge_id, "before": before, "after_first": after_first, "after_second": after_second, "restored": same(current, exact_baseline)})
            duplicate_nodes = self.query_rows("reseed-duplicate-node-ids", "MATCH (n) WHERE n.id IS NOT NULL WITH n.id AS id, count(n) AS copies WHERE copies > 1 RETURN id ORDER BY id")
            duplicate_edges = self.query_rows("reseed-duplicate-edge-ids", "MATCH ()-[r]->() WHERE r.id IS NOT NULL WITH r.id AS id, count(r) AS copies WHERE copies > 1 RETURN id ORDER BY id")
            self.check("idempotent-reseed-restores-baseline", "reseed.two-loads", reset_status == 200 and len(mutation_results) == 3 and all(item["after_second"] == item["after_first"] for item in mutation_results), mutation_results)
            self.check("idempotent-reseed-restores-baseline", "reseed.no-duplicate-identifiers", duplicate_nodes == [], duplicate_nodes)
            self.check("idempotent-reseed-restores-baseline", "reseed.no-duplicate-relationships", duplicate_edges == [], duplicate_edges)
            self.check("idempotent-reseed-restores-baseline", "reseed.exact-baseline-restored", all_restored, mutation_results)
            self.check("idempotent-reseed-restores-baseline", "reseed.source-order-and-times", source_order_restored, mutation_results)
            self.observed["reseed"] = mutation_results

        self.scenario("idempotent-reseed-restores-baseline", reseed_group)

        def fresh_group() -> None:
            _, before = self.docket()
            counts_before = self.graph_counts("fresh-before")
            action_path = self.state_dir / "confirmed-actions.json"
            action_before = action_path.read_bytes() if action_path.exists() else None
            prior_client = self.db.client
            if prior_client:
                prior_client.close()
            self.db.client = RespConnection("127.0.0.1", self.args.db_port, timeout=3.0)
            self.db.graph_rows("fresh-client-new-connection", "MATCH (n:DecisionRecord {id:'DEC-OPS-9-REVIEW-01'}) RETURN n.id AS id")
            status, after = self.docket()
            browser_read = self.run_browser("existing", "fresh-client")
            counts_after = self.graph_counts("fresh-after")
            action_after = action_path.read_bytes() if action_path.exists() else None
            self.check("fresh-client-read-only-owned-cleanup", "fresh-client.new-connection", prior_client is not None and prior_client is not self.db.client and self.db.client is not None and status == 200, {"old_connection_closed": prior_client is not None, "new_connection_created": prior_client is not self.db.client, "status": status})
            self.check("fresh-client-read-only-owned-cleanup", "fresh-client.exact-snapshot", same(after, self.observed["baseline"]), after)
            self.check("fresh-client-read-only-owned-cleanup", "fresh-client.zero-graph-writes", counts_before == counts_after, {"before": counts_before, "after": counts_after})
            self.check("fresh-client-read-only-owned-cleanup", "fresh-client.zero-local-action-writes", action_before is None and action_after is None and not self.service.pending, {"before": action_before, "after": action_after, "pending": len(self.service.pending)})
            self.check("fresh-client-read-only-owned-cleanup", "fresh-client.owned-graph-state-cleanup", self.db.graph.startswith(f"wardatlas_test_decision_lane{self.args.lane}_") and self.state_dir_owned and self.state_dir.is_dir() and self.state_dir.resolve().is_relative_to(ROOT), {"graph": self.db.graph, "state_dir": str(self.state_dir), "owned": self.state_dir_owned})
            self.check("fresh-client-read-only-owned-cleanup", "fresh-client.owned-process-cleanup", self.db.process is not None and self.db.process.poll() is None and self.service is not None and self.httpd is not None, {"db_pid": self.db.process.pid if self.db.process else None, "app_thread_alive": self.http_thread.is_alive() if self.http_thread else False})
            self.observed["fresh"] = {"before": before, "after": after, "counts": [counts_before, counts_after], "browser": browser_read}

        self.scenario("fresh-client-read-only-owned-cleanup", fresh_group)

    def stop(self) -> None:
        if self.httpd is not None:
            try:
                self.httpd.shutdown()
                self.httpd.server_close()
                if self.http_thread:
                    self.http_thread.join(timeout=5)
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
        missing = {
            group: sorted({item["id"] for item in self.groups[group]["pinned_assertions"]} - self.coverage[group])
            for group in REQUIRED_GROUPS
        }
        return {
            "schema_version": 1,
            "status": status,
            "error": error,
            "run_id": self.run_id,
            "graph": self.db.graph,
            "binds": {"database": f"127.0.0.1:{self.args.db_port}", "app": f"127.0.0.1:{self.args.app_port}"},
            "falkordb": self.db.database,
            "group_order": REQUIRED_GROUPS,
            "mapped_assertion_count": len(self.check_log),
            "mapped_assertions": self.check_log,
            "missing_matrix_assertions": missing,
            "scenarios": self.db.scenarios,
            "observed": self.observed,
            "cleanup": self.cleanup,
            "owned_resources_cleaned": all(self.cleanup.values()),
            "timings": self.db.timings,
            "resource_samples": self.db.resource_samples,
        }


def choose_ports(args: argparse.Namespace) -> int:
    base = int(os.environ.get("WORKSHOP_PORT_BASE", "0"))
    span = int(os.environ.get("WORKSHOP_PORT_SPAN", "0"))
    lane = os.environ.get("WORKSHOP_LANE", "")
    if not lane.isdigit() or base < 1024 or span < 4:
        raise VerificationFailure("WORKSHOP_LANE, WORKSHOP_PORT_BASE, and WORKSHOP_PORT_SPAN must define a private high-port range")
    db_port = args.db_port if args.db_port is not None else base + span - 2
    app_port = args.app_port if args.app_port is not None else base + span - 1
    if db_port == app_port or not (base <= db_port < base + span and base <= app_port < base + span):
        raise VerificationFailure("database and app ports must be distinct members of this worker's private range")
    args.lane = lane
    args.db_port = db_port
    args.app_port = app_port
    return int(lane)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expected", type=Path, default=EXPECTED_PATH)
    parser.add_argument("--matrix", type=Path, default=MATRIX_PATH)
    parser.add_argument("--db-port", type=int)
    parser.add_argument("--app-port", type=int)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--evidence-dir", type=Path)
    parser.add_argument("--require-live-falkordb", action="store_true")
    parser.add_argument("--require-nine-groups", action="store_true")
    parser.add_argument("--assert-api", action="store_true")
    parser.add_argument("--assert-browser", action="store_true")
    parser.add_argument("--desktop-mobile", action="store_true")
    parser.add_argument("--assert-owned-cleanup", action="store_true")
    args = parser.parse_args()
    required = (args.require_live_falkordb, args.require_nine_groups, args.assert_api, args.assert_browser, args.desktop_mobile, args.assert_owned_cleanup)
    if not all(required):
        parser.error("all of --require-live-falkordb --require-nine-groups --assert-api --assert-browser --desktop-mobile --assert-owned-cleanup are mandatory")
    try:
        lane = choose_ports(args)
        expected = json.loads(args.expected.read_text(encoding="utf-8"))
        matrix = json.loads(args.matrix.read_text(encoding="utf-8"))
        group_order = [row["id"] for row in expected["cases"]]
        matrix_order = [row["group_id"] for row in matrix["groups"]]
        if group_order != REQUIRED_GROUPS or matrix_order != REQUIRED_GROUPS:
            raise VerificationFailure("candidate-independent decision packet/matrix do not contain the exact nine groups")
        if any(type(group.get("nonzero_assertion_count")) is not int or group["nonzero_assertion_count"] <= 0 for group in matrix["groups"]):
            raise VerificationFailure("every pinned decision group must map nonzero assertions")
        evidence_dir = args.evidence_dir or Path("/workspace/evidence/decision-provenance-docket") / f"lane-{lane}"
        evidence_dir.mkdir(parents=True, exist_ok=True)
        args.evidence_dir = evidence_dir
        report_path = args.report or evidence_dir / f"live-{uuid.uuid4().hex}.json"
        verifier = DecisionDocketLive(args, expected, matrix)
        status = "PASS"
        error = None
        try:
            verifier.run()
        except Exception as exc:
            status = "FAIL"
            error = f"{type(exc).__name__}: {exc}"
        finally:
            verifier.stop()
            if status == "PASS" and not all(verifier.cleanup.values()):
                status = "FAIL"
                error = f"owned cleanup incomplete: {verifier.cleanup}"
            report = verifier.report(status, error)
            report_path.parent.mkdir(parents=True, exist_ok=True)
            report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        if status != "PASS":
            print(f"decision-provenance live: FAIL: {error}; report={report_path}", file=sys.stderr)
            return 1
        if not report["owned_resources_cleaned"]:
            print(f"decision-provenance live: FAIL: owned cleanup incomplete; {report['cleanup']}", file=sys.stderr)
            return 1
        print(f"decision-provenance live: PASS (9 groups, {report['mapped_assertion_count']} mapped assertions, actual FalkorDB/API/browser desktop+mobile, owned cleanup; report={report_path})")
        return 0
    except Exception as exc:
        print(f"decision-provenance live: FAIL: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

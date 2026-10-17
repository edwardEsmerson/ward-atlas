#!/usr/bin/env python3
"""Verify saved-handover vs live-graph comparison on actual FalkorDB and browsers.

Every expected value is loaded from the frozen candidate-independent drift
oracle. The verifier owns one unique loopback FalkorDB process, one graph, one
local action-state directory and the app processes it starts.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from verify_falkordb_live import LiveVerifier, RespConnection, VerificationFailure, explicit_bind_preflight, query_result, result_rows
from wardatlas_fixtures import seed_queries
from wardatlas_shift_board import seed_shift_board_queries


ROOT = Path(__file__).resolve().parents[1]
EXPECTED_PATH = ROOT / "tests/handover-drift-expected-results.json"
MATRIX_PATH = ROOT / "tests/handover-drift-adversarial-matrix.json"
HANDOVER_PLAN = Path("/workspace/worktrees/planner-d0017f7f160844fe9cf5a6036aa5068f/plans/planner-d0017f7f160844fe9cf5a6036aa5068f.md")
DRIFT_PLAN = Path("/workspace/worktrees/planner-828ef7b6a76e4128b9e1b2ecad33c4a7/plans/planner-828ef7b6a76e4128b9e1b2ecad33c4a7.md")
SOP_PLAN = Path("/workspace/worktrees/planner-3bfcb0d8abbc45bc82e0fab8d715c2da/plans/planner-3bfcb0d8abbc45bc82e0fab8d715c2da.md")
ESCALATION_PLAN = Path("/workspace/worktrees/planner-51d0c80bef174057bc10db9a64ec3d74/plans/planner-51d0c80bef174057bc10db9a64ec3d74.md")
SHIFT_PLANS = (
    Path("/workspace/worktrees/planner-5f75fb17810c463fb9d84bd5821b254f/plans/planner-5f75fb17810c463fb9d84bd5821b254f.md"),
    Path("/workspace/worktrees/planner-9b1a51761889446c8b5525bde02bc5d6/plans/planner-9b1a51761889446c8b5525bde02bc5d6.md"),
    Path("/workspace/worktrees/planner-13609527bde94e87ae5bcacaa2c86a70/plans/planner-13609527bde94e87ae5bcacaa2c86a70.md"),
    Path("/workspace/worktrees/planner-13609527bde94e87ae5bcacaa2c86a70/plans/planner-ed8ab68cd001411d97ee43310862ae22.md"),
)


def request_json(base_url: str, path: str, method: str = "GET", payload: dict[str, Any] | None = None) -> tuple[int, dict[str, Any]]:
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        base_url + path,
        data=body,
        method=method,
        headers={"Content-Type": "application/json", "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read())


def selected_ports(args: argparse.Namespace) -> tuple[int, int, int, int]:
    try:
        base = int(os.environ["WORKSHOP_PORT_BASE"])
        span = int(os.environ["WORKSHOP_PORT_SPAN"])
    except (KeyError, ValueError) as exc:
        raise VerificationFailure("WORKSHOP_PORT_BASE and WORKSHOP_PORT_SPAN must be set") from exc
    if base < 1024 or span < 2:
        raise VerificationFailure("the allocated private port range must contain at least two high ports")

    def choose(value: int | None, offset: int, label: str) -> int:
        port = base + offset if value is None else value
        if not base <= port < base + span:
            raise VerificationFailure(f"{label} must be inside this worker's allocated private port range")
        return port

    db_port = choose(args.db_port, 180, "drift database port")
    app_port = choose(args.app_port, 181, "drift app port")
    suite_ports = (choose(None, 182, "original database port"), choose(None, 184, "integrated UI app port"),
                   choose(None, 185, "integrated UI database port"), choose(None, 186, "handover app port"),
                   choose(None, 187, "handover database port"), choose(None, 188, "SOP app port"),
                   choose(None, 189, "SOP database port"), choose(None, 190, "source-change database port"))
    ports = (db_port, app_port, *suite_ports)
    if len(set(ports)) != len(ports):
        raise VerificationFailure("each sequential acceptance suite must have a distinct allocated port")
    if db_port == app_port:
        raise VerificationFailure("app and database ports must be distinct")
    return base, span, db_port, app_port


class DriftWorkflow:
    def __init__(self, args: argparse.Namespace, expected: dict[str, Any], matrix: dict[str, Any]):
        self.args = args
        self.expected = expected
        self.matrix = matrix
        self.expected_by_id = {case["id"]: case["expected"] for case in expected["cases"]}
        self.lane = os.environ.get("WORKSHOP_LANE", "")
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,32}", self.lane):
            raise VerificationFailure("WORKSHOP_LANE must be a nonempty safe lane identifier")
        self.live = LiveVerifier(args.db_port, expected, matrix)
        self.live.graph = f"wardatlas_test_{self.lane}_{self.live.run_id}"
        self.base_url = f"http://127.0.0.1:{args.app_port}"
        self.state_dir = ROOT / ".local" / "handover-drift-runs" / self.lane / self.live.run_id
        self.state_owned = False
        self.state_removed = False
        self.app_process: subprocess.Popen | None = None
        self.app_log_handle = None
        self.app_start_count = 0
        self.app_process_history: list[dict[str, Any]] = []
        self.browser_report = args.evidence_dir / "handover-drift-browser.json"
        self.screenshot_dir = args.evidence_dir / "screenshots"
        self.browser_data: dict[str, Any] = {}
        self.status = "running"
        self.error: str | None = None
        self.action: dict[str, Any] | None = None
        self.saved_action_bytes: bytes | None = None
        self.case_by_id = {case["case_id"]: case for case in matrix["cases"]}

    def check(self, case: dict[str, Any], name: str, passed: bool, detail: Any = None) -> None:
        self.live.check(case, name, bool(passed), detail)

    def query_rows(self, label: str, query: str) -> list[dict[str, Any]]:
        return self.live.graph_rows(label, query)

    def graph_counts(self, label: str) -> dict[str, int]:
        nodes = self.query_rows(f"{label}-nodes", "MATCH (n) RETURN count(n) AS count")
        relationships = self.query_rows(f"{label}-relationships", "MATCH ()-[r]->() RETURN count(r) AS count")
        return {"nodes": int(nodes[0]["count"]), "relationships": int(relationships[0]["count"])}

    @property
    def action_file(self) -> Path:
        return self.state_dir / "confirmed-actions.json"

    def read_action_bytes(self) -> bytes:
        if not self.action_file.is_file():
            return b""
        return self.action_file.read_bytes()

    def api_get(self, path: str) -> tuple[int, dict[str, Any]]:
        return request_json(self.base_url, path)

    def api_post(self, path: str, payload: dict[str, Any] | None = None) -> tuple[int, dict[str, Any]]:
        return request_json(self.base_url, path, "POST", payload or {})

    def compare_path(self, action_id: str | None = None, observed_at: str | None = None, branch_at: str | None = None, scope: str = "operations") -> str:
        params = {"scope": scope}
        if action_id is not None:
            params["action_id"] = action_id
        if observed_at is not None:
            params["observed_at"] = observed_at
        if branch_at is not None:
            params["branch_at"] = branch_at
        return "/api/handover/compare?" + urllib.parse.urlencode(params)

    def start_app(self) -> None:
        explicit_bind_preflight(self.args.app_port)
        self.app_start_count += 1
        log_path = self.args.evidence_dir / f"wardatlas-app-{self.app_start_count}.log"
        self.app_log_handle = log_path.open("wb")
        self.app_process = subprocess.Popen(
            [
                sys.executable, str(ROOT / "scripts/wardatlas_app.py"),
                "--app-port", str(self.args.app_port), "--db-port", str(self.args.db_port),
                "--graph-name", self.live.graph, "--state-dir", str(self.state_dir),
            ],
            cwd=ROOT,
            stdin=subprocess.DEVNULL,
            stdout=self.app_log_handle,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            if self.app_process.poll() is not None:
                self.app_log_handle.flush()
                raise VerificationFailure(f"owned app process exited with {self.app_process.returncode}; see {log_path}")
            try:
                status, _ = self.api_get("/api/health")
                if status == 200:
                    return
            except Exception:
                time.sleep(0.05)
        raise VerificationFailure(f"owned loopback app did not become ready; see {log_path}")

    def stop_app(self) -> None:
        if self.app_process is None:
            return
        process = self.app_process
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        stopped = process.poll() is not None
        self.app_process_history.append({"pid": process.pid, "exit_code": process.returncode, "stopped": stopped})
        if self.app_log_handle is not None:
            self.app_log_handle.flush()
            self.app_log_handle.close()
            self.app_log_handle = None
        self.app_process = None
        if not stopped:
            raise VerificationFailure("an app process owned by this verifier is still running")

    def start(self) -> None:
        self.args.evidence_dir.mkdir(parents=True, exist_ok=True)
        self.args.report.parent.mkdir(parents=True, exist_ok=True)
        self.live.launch()
        self.state_dir.parent.mkdir(parents=True, exist_ok=True)
        try:
            self.state_dir.mkdir(mode=0o700)
            self.state_owned = True
        except FileExistsError as exc:
            raise VerificationFailure(f"refusing to reuse a pre-existing state directory: {self.state_dir}") from exc
        # A read-only query proves this task's unique graph starts empty before app startup.
        cold = self.query_rows("cold-owned-graph", "MATCH (n) RETURN n.id AS id LIMIT 1")
        self.live.graph_created = True
        if cold:
            raise VerificationFailure(f"new lane/run graph is unexpectedly nonempty: {cold!r}")
        self.start_app()

    def reset_scenario(self) -> dict[str, Any]:
        status, body = self.api_post("/api/scenario/reset")
        if status != 200:
            raise VerificationFailure(f"owned synthetic reseed failed: HTTP {status}: {body!r}")
        return body

    def confirm_canonical_action(self) -> dict[str, Any]:
        status, draft = self.api_post("/api/handover/draft", {"incident_id": "INC-204", "as_of": "2026-10-14T18:30:00Z"})
        if status != 201:
            raise VerificationFailure(f"cannot prepare pinned synthetic handover action: HTTP {status}: {draft!r}")
        status, confirmation = self.api_post("/api/handover/confirm", {"token": draft.get("confirmation_token"), "confirm": True})
        if status != 201 or confirmation.get("persisted") is not True:
            raise VerificationFailure(f"explicit local confirmation failed: HTTP {status}: {confirmation!r}")
        action = confirmation.get("action")
        if not isinstance(action, dict) or action.get("snapshot") != self.expected["canonical_snapshot"]:
            raise VerificationFailure("confirmed action did not persist the exact pinned graph witness")
        self.action = action
        self.saved_action_bytes = self.read_action_bytes()
        return action

    def run_cold_case(self, case: dict[str, Any]) -> None:
        expected = self.expected_by_id[case["id"]]
        cold_counts_before = self.graph_counts("cold-no-action-before")
        status, no_action = self.api_get(self.compare_path())
        action_status, actions = self.api_get("/api/actions")
        cold_counts_after = self.graph_counts("cold-no-action-after")
        no_snapshot = expected["cold_without_snapshot"]
        self.check(case, "cold graph with no saved action is explicitly empty/unknown", status == 200 and no_action["historical_snapshot"] is None and no_action["observed"]["current_state"] == no_snapshot["current_state"] and no_action["comparison_status"] == no_snapshot["comparison_status"], no_action)
        self.check(case, "cold graph does not invent current paths or witness", no_action["observed"]["current_impact_paths"] == no_snapshot["current_impact_paths"] and no_action["observed"]["sop_witness"] is no_snapshot["current_sop_witness"], no_action["observed"])
        self.check(case, "cold graph returns no saved action or absence-of-risk conclusion", action_status == 200 and actions["actions"] == no_snapshot["saved_actions"] and no_action["observed"]["no_operational_risk_conclusion_asserted"] is False, {"actions": actions, "observed": no_action["observed"]})
        self.check(case, "read-only cold comparison leaves empty owned graph unchanged", cold_counts_before == cold_counts_after == {"nodes": 0, "relationships": 0}, {"before": cold_counts_before, "after": cold_counts_after})

        self.reset_scenario()
        action = self.confirm_canonical_action()
        self.live.graph_query("empty-only-owned-fixture-for-cold-history", "MATCH (n) DETACH DELETE n")
        empty_counts = self.graph_counts("empty-owned-graph-with-history")
        empty_status, with_history = self.api_get(self.compare_path(action["id"]))
        unchanged_bytes = self.read_action_bytes() == self.saved_action_bytes
        saved_expected = expected["saved_snapshot_with_empty_or_unavailable_graph"]
        self.check(case, "cold graph with saved action labels history and preserves the canonical snapshot", empty_status == 200 and with_history["historical_snapshot_label"] == saved_expected["historical_snapshot_label"] and with_history["historical_snapshot"] == self.expected["canonical_snapshot"], with_history)
        self.check(case, "saved history plus empty graph yields unknown current paths and comparison", with_history["observed"]["current_state"] == saved_expected["current_state"] and with_history["observed"]["current_impact_paths"] == saved_expected["current_impact_paths"] and with_history["comparison_status"] == saved_expected["comparison_status"], with_history["observed"])
        self.check(case, "empty graph has no fabricated current SOP witness and saved action is unchanged", empty_counts == {"nodes": 0, "relationships": 0} and with_history["observed"]["sop_witness"] is None and with_history["saved_action_unchanged"] is True and unchanged_bytes, {"comparison": with_history, "empty_counts": empty_counts})
        self.check(case, "unknown cold comparison does not infer risk or conflict absence", with_history["observed"]["safe_conclusion_asserted"] is False and with_history["observed"]["no_operational_risk_conclusion_asserted"] == saved_expected["risk_or_conflict_absence_concluded"], with_history["observed"])
        self.reset_scenario()

    def run_snapshot_case(self, case: dict[str, Any]) -> None:
        expected = self.expected_by_id[case["id"]]
        status, actions = self.api_get("/api/actions")
        action = actions["actions"][0] if status == 200 and actions.get("count") == 1 else {}
        self.action = action
        self.saved_action_bytes = self.read_action_bytes()
        self.check(case, "confirmed action retains the exact timestamped canonical graph witness", bool(action) and action.get("snapshot") == self.expected["canonical_snapshot"] and expected["snapshot"] == "#/canonical_snapshot", action)
        self.check(case, "action confirmation timestamp remains separate from graph snapshot time", bool(action) and action.get("confirmed_at") and action["confirmed_at"] != expected["as_of"] and action["snapshot"]["as_of"] == expected["as_of"], action)
        path_evidence = action.get("snapshot", {}).get("impact", {}).get("path_evidence", [])
        self.check(case, "saved witness carries exact source IDs titles and per-hop observation times", path_evidence == expected["path_evidence"], path_evidence)
        witness = (action.get("snapshot") or {}).get("sop_witness")
        self.check(case, "SOP witness is separate, unresolved, unverified and does not select a winner", witness == self.expected["canonical_snapshot"]["sop_witness"] and witness.get("conflict_status") == "unresolved" and witness.get("winner_selected") is False, witness)

    def compare(self, action_id: str | None = None, observed_at: str | None = None, branch_at: str | None = None) -> tuple[int, dict[str, Any]]:
        return self.api_get(self.compare_path(action_id, observed_at, branch_at))

    def run_restoration_case(self, case: dict[str, Any]) -> None:
        expected = self.expected_by_id[case["id"]]
        status, result = self.compare(self.action["id"], expected["as_of"], "2026-10-14T18:50:00Z")
        observed = result.get("observed", {})
        self.check(case, "19:30Z live query identifies observed restoration with zero active impact paths", status == 200 and observed.get("as_of") == expected["as_of"] and observed.get("current_state") == expected["current_status"] and observed.get("current_impact_paths") == expected["current_active_paths"], observed)
        self.check(case, "observed delta removes exactly the two reviewed paths and adds none", [row["path"] for row in observed.get("removed_paths", [])] == expected["removed_paths"] and observed.get("added_paths") == expected["added_paths"] and observed.get("path_delta", {}).get("status") == "changed", observed.get("path_delta"))
        self.check(case, "removed-path provenance is the exact saved per-path witness", [row["provenance"] for row in observed.get("removed_paths", [])] == self.expected["canonical_snapshot"]["impact"]["path_evidence"], observed.get("removed_paths"))
        self.check(case, "restoration is source-backed by the pinned event, source and event time", all(observed.get("restoration_source_fact", {}).get(key) == value for key, value in expected["restoration_source_fact"].items()), observed.get("restoration_source_fact"))
        self.check(case, "separate SOP witness remains unresolved and byte-equivalent to reviewed witness", observed.get("sop_conflict_status") == expected["sop_conflict_status"] and observed.get("sop_witness_unchanged") is expected["sop_witness_unchanged"] and observed.get("sop_witness") == self.expected["canonical_snapshot"]["sop_witness"], observed)
        self.check(case, "restoration does not assert safety, no risk, or mutate saved action", observed.get("safe_conclusion_asserted") == expected["safe_conclusion_asserted"] and observed.get("no_operational_risk_conclusion_asserted") == expected["no_operational_risk_conclusion_asserted"] and result.get("saved_action_unchanged") is expected["saved_action_unchanged"] and self.read_action_bytes() == self.saved_action_bytes, {"observed": observed, "action_unchanged": self.read_action_bytes() == self.saved_action_bytes})

    def run_branch_case(self, case: dict[str, Any]) -> None:
        expected = self.expected_by_id[case["id"]]
        status, result = self.compare(self.action["id"], "2026-10-14T18:30:00Z", expected["as_of"])
        branch = result.get("hypothetical_branch", {})
        self.check(case, "18:50Z branch has the pinned identity and hypothetical-restored status", status == 200 and branch.get("as_of") == expected["as_of"] and branch.get("branch_id") == expected["branch_id"] and branch.get("status") == expected["branch_status"] and branch.get("delta_label") == expected["branch_delta_label"], branch)
        self.check(case, "hypothetical branch has no active paths and an explicitly hypothetical path delta", branch.get("paths") == expected["branch_paths"] and branch.get("path_delta", {}).get("label") == "hypothetical" and branch.get("path_delta", {}).get("status") == "hypothetical", branch)
        self.check(case, "observed mainline remains impacted with both canonical paths", branch.get("observed_mainline_status") == expected["observed_mainline_status"] and [row["path"] for row in branch.get("observed_mainline_paths", [])] == expected["observed_mainline_paths"], branch)
        self.check(case, "branch does not mutate observed state or historical action and leaves SOP witness separate", branch.get("observed_state_mutated") is expected["observed_state_mutated"] and result.get("historical_snapshot") == self.expected["canonical_snapshot"] and result.get("saved_action_unchanged") is True and branch.get("sop_witness_unchanged") is True, {"branch": branch, "snapshot": result.get("historical_snapshot")})

    def edge_count(self, edge_id: str) -> int:
        rows = self.query_rows(f"edge-count-{edge_id}", f"MATCH ()-[r {{id:'{edge_id}'}}]->() RETURN count(r) AS count")
        return int(rows[0]["count"])

    def seed_owned_edge(self, edge_id: str) -> None:
        candidates = [query for query in [*seed_queries(), *seed_shift_board_queries()] if edge_id in query]
        if len(candidates) != 1:
            raise VerificationFailure(f"expected one deterministic owned fixture query for {edge_id}, found {len(candidates)}")
        self.live.graph_query(f"reseed-owned-edge-{edge_id}", candidates[0])

    def remove_owned_edge(self, edge: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        edge_id = "REL-SOP-V2-APPLIES-C4" if edge["relationship"] == "APPLIES_TO" else "REL-SOP-9-CONFLICT"
        before = self.edge_count(edge_id)
        source, target = edge["from"], edge["to"]
        relation = edge["relationship"]
        self.live.graph_query(f"remove-only-owned-{edge_id}", f"MATCH (a {{id:'{source}'}})-[r:{relation} {{id:'{edge_id}'}}]->(b {{id:'{target}'}}) DELETE r")
        after = self.edge_count(edge_id)
        status, comparison = self.compare(self.action["id"], "2026-10-14T18:30:00Z", "2026-10-14T18:50:00Z")
        if before != 1 or after != 0:
            raise VerificationFailure(f"counterfactual did not remove exactly owned relation {edge_id}: {before}->{after}")
        return status, comparison

    def run_edge_removal_case(self, case: dict[str, Any]) -> None:
        expected = self.expected_by_id[case["id"]]
        baseline_status, baseline = self.compare(self.action["id"], "2026-10-14T18:30:00Z", "2026-10-14T18:50:00Z")
        base_paths = baseline["observed"]["current_impact_paths"]
        self.check(case, "counterfactual baseline contains exactly the pinned impact paths", baseline_status == 200 and base_paths == expected["baseline_current_impact_paths"], base_paths)
        for label, edge_name in (("applicability_edge_removal", "applicability"), ("conflict_edge_removal", "conflict")):
            spec = expected[label]
            status, comparison = self.remove_owned_edge(spec["removed_edge"])
            observed = comparison["observed"]
            self.check(case, f"{edge_name} edge removal preserves the historical snapshot and action", status == 200 and comparison["historical_snapshot"] == self.expected["canonical_snapshot"] and comparison["saved_action_unchanged"] is True and self.read_action_bytes() == self.saved_action_bytes, comparison)
            self.check(case, f"{edge_name} witness edge loss reports only pinned unknown linkage", observed["sop_witness"]["linkage_status"] == spec["current_linkage_status"] and observed["sop_witness"]["conflict_status"] in {"unknown", "unresolved"} and observed["sop_witness"].get("conflict_edge") is None, observed["sop_witness"])
            expected_claims = self.expected["canonical_snapshot"]["sop_witness"]["claim_evidence"]
            if edge_name == "applicability":
                expected_claims = [claim for claim in expected_claims if claim["sop_version_id"] == "SOP-OPS-9-V3"]
            self.check(case, f"{edge_name} edge loss returns only source claims supported by surviving witness links", observed["sop_witness"]["claim_evidence"] == expected_claims, observed["sop_witness"]["claim_evidence"])
            self.check(case, f"{edge_name} edge loss does not change the two graph impact paths or their provenance", observed["current_impact_paths"] == spec["current_impact_paths"] and observed["current_path_evidence"] == spec["current_path_evidence"] and observed["path_delta"]["status"] == "unchanged", observed)
            self.check(case, f"{edge_name} missing witness never concludes resolution, absence, or creates a proposal", observed.get("sop_conflict_status") == "unknown" and observed["sop_witness"].get("conflict_edge") is None and "proposed_actions" not in comparison and observed.get("safe_conclusion_asserted") is False, comparison)
            self.seed_owned_edge(spec["removed_edge"]["relationship"] == "APPLIES_TO" and "REL-SOP-V2-APPLIES-C4" or "REL-SOP-9-CONFLICT")
            _, reseeded = self.compare(self.action["id"], "2026-10-14T18:30:00Z", "2026-10-14T18:50:00Z")
            self.check(case, f"{edge_name} owned reseed restores exact current witness", reseeded["observed"]["sop_witness"] == self.expected["canonical_snapshot"]["sop_witness"], reseeded["observed"]["sop_witness"])

    @staticmethod
    def escape(value: str) -> str:
        return json.dumps(value, ensure_ascii=True)

    def inject_restricted_probe(self, probe: dict[str, Any]) -> None:
        version, department, role, source = probe["version_id"], probe["department_id"], probe["claimed_role_id"], probe["source_id"]
        rel = probe["relationship_id"]
        statements = [
            f"MERGE (n:SOPVersion {{id:{self.escape(version)}}}) SET n.access='restricted', n.sop_id={self.escape(version)}, n.referenced_equipment_id='CONVEYOR-C4'",
            f"MERGE (n:Department {{id:{self.escape(department)}}}) SET n.access='restricted'",
            f"MERGE (n:Role {{id:{self.escape(role)}}}) SET n.access='restricted'",
            f"MERGE (n:Evidence {{id:{self.escape(source)}}}) SET n.access='restricted', n.title={self.escape(probe['source_title'])}, n.text={self.escape(probe['source_text'])}, n.observed_at='2026-10-14T16:10:00Z'",
            f"MATCH (a:Service {{id:'SVC-MATERIAL-TRANSFER'}}), (b:Department {{id:{self.escape(department)}}}) MERGE (a)-[r:SERVES {{id:{self.escape(rel)}}}]->(b) SET r.access='restricted'",
            f"MATCH (a:Department {{id:{self.escape(department)}}}), (b:Role {{id:{self.escape(role)}}}) MERGE (a)-[r:OWNED_BY {{id:{self.escape(rel + '-OWNER')}}}]->(b) SET r.access='restricted', r.current=true",
            f"MATCH (a:SOPVersion {{id:{self.escape(version)}}}), (b:Equipment {{id:'CONVEYOR-C4'}}) MERGE (a)-[r:APPLIES_TO {{id:{self.escape(rel + '-APPLIES')}}}]->(b) SET r.access='restricted'",
            f"MATCH (a:SOPVersion {{id:{self.escape(version)}}}), (b:Role {{id:{self.escape(role)}}}) MERGE (a)-[r:CLAIMS_ROLE {{id:{self.escape(rel + '-CLAIMS')}}}]->(b) SET r.access='restricted'",
            f"MATCH (a:SOPVersion {{id:{self.escape(version)}}}), (b:Evidence {{id:{self.escape(source)}}}) MERGE (a)-[r:EVIDENCED_BY {{id:{self.escape(rel + '-EVIDENCE')}}}]->(b) SET r.access='restricted'",
            f"MATCH (a:SOPVersion {{id:'SOP-OPS-9-V2'}}), (b:SOPVersion {{id:{self.escape(version)}}}) MERGE (a)-[r:SOP_CONFLICT {{id:{self.escape(rel + '-CONFLICT')}}}]->(b) SET r.access='restricted', r.status='unresolved'",
        ]
        for index, statement in enumerate(statements):
            self.live.graph_query(f"inject-owned-denied-probe-{index}", statement)

    def run_acl_case(self, case: dict[str, Any]) -> None:
        expected = self.expected_by_id[case["id"]]
        action_id = self.action["id"]
        before_status, before = self.compare(action_id, "2026-10-14T19:30:00Z", "2026-10-14T18:50:00Z")
        self.inject_restricted_probe(expected["restricted_probe"])
        after_status, after = self.compare(action_id, "2026-10-14T19:30:00Z", "2026-10-14T18:50:00Z")
        denied_queries_before = self.live.query_count
        denied_status, denied = self.api_get(self.compare_path(action_id, scope="restricted"))
        serialized = json.dumps(after, sort_keys=True)
        probe = expected["restricted_probe"]
        markers = [probe["version_id"], probe["claimed_role_id"], probe["source_id"], probe["source_title"], probe["source_text"], probe["department_id"], probe["relationship_id"]]
        self.check(case, "operations comparison is semantically invariant before/after restricted evidence injection", before_status == after_status == 200 and before == after, {"before": before, "after": after})
        self.check(case, "restricted identifiers titles facts and relationship paths do not leak", not any(marker in serialized for marker in markers) and all(probe["department_id"] not in json.dumps(row) and probe["claimed_role_id"] not in json.dumps(row) for row in after["observed"]["current_path_evidence"]), {"hidden_markers_absent": not any(marker in serialized for marker in markers)})
        self.check(case, "comparison output reveals no restricted count or existence signal", not any(key.lower().endswith("count") or "restricted" in key.lower() for key in after), sorted(after))
        self.check(case, "saved historical snapshot and visible current delta remain unchanged", after["historical_snapshot"] == before["historical_snapshot"] and after["observed"]["path_delta"] == before["observed"]["path_delta"], after["observed"]["path_delta"])
        self.check(case, "non-operations scope is denied before graph traversal without revealing source existence", denied_status == 403 and denied == {"error": "This view is not available."} and self.live.query_count == denied_queries_before, {"status": denied_status, "body": denied, "queries_before": denied_queries_before, "queries_after": self.live.query_count})

    def run_read_only_case(self, case: dict[str, Any]) -> None:
        expected = self.expected_by_id[case["id"]]
        action_before = self.read_action_bytes()
        graph_before = self.graph_counts("readonly-before")
        status, result = self.compare(self.action["id"], "2026-10-14T19:30:00Z", "2026-10-14T18:50:00Z")
        graph_after = self.graph_counts("readonly-after")
        action_after = self.read_action_bytes()
        actions_status, actions = self.api_get("/api/actions")
        flags = result.get("action_boundary", {})
        self.check(case, "read-only comparison performs zero graph and confirmed-action writes", status == 200 and graph_before == graph_after and action_before == action_after and flags.get("graph_mutations") == expected["graph_mutations"] and flags.get("confirmed_action_file_mutations") == expected["confirmed_action_file_mutations"], {"counts": [graph_before, graph_after], "same_action_bytes": action_before == action_after, "flags": flags})
        self.check(case, "comparison neither automatically confirms nor changes saved action", flags.get("explicit_confirmation_performed") is expected["explicit_confirmation_performed"] and flags.get("automatic_confirmation") is expected["automatic_confirmation"] and actions_status == 200 and actions["count"] == 1 and actions["actions"][0]["snapshot"] == self.expected["canonical_snapshot"], actions)
        self.check(case, "comparison causes no external, clinical, truth-resolution or work-completed side effect", flags.get("external_writes") == expected["external_writes"] and flags.get("messages_sent") is expected["messages_sent"] and flags.get("calls_made") is expected["calls_made"] and flags.get("tickets_created") is expected["tickets_created"] and flags.get("clinical_action_proposed") is expected["clinical_action_proposed"] and flags.get("source_truth_resolved") is expected["source_truth_resolved"] and flags.get("work_claimed_performed") is expected["work_claimed_performed"], flags)

    def run_time_case(self, case: dict[str, Any]) -> None:
        expected = self.expected_by_id[case["id"]]
        action_before = self.read_action_bytes()
        counts_before = self.graph_counts("time-validation-before")
        status, offset = self.compare(self.action["id"], expected["offset_input"], "2026-10-14T20:50:00+02:00")
        self.check(case, "offset input normalizes to the exact canonical UTC snapshot", status == 200 and offset["observed"]["as_of"] == expected["normalized_as_of"] and offset["historical_snapshot"] == self.expected["canonical_snapshot"], offset)
        queries_before_invalid = self.live.query_count
        bad_status, bad = self.compare(self.action["id"], expected["malformed_timestamp"], "2026-10-14T18:50:00Z")
        counts_after = self.graph_counts("time-validation-after")
        action_after = self.read_action_bytes()
        self.check(case, "malformed timestamp is rejected with HTTP 400", bad_status == 400 and "Invalid timestamp" in bad.get("error", ""), bad)
        self.check(case, "timestamp rejection precedes graph query and graph/action mutation", self.live.query_count == queries_before_invalid + 2 and counts_before == counts_after and action_before == action_after, {"queries_before": queries_before_invalid, "queries_after_invalid_and_count_reads": self.live.query_count, "graph_counts": [counts_before, counts_after], "action_unchanged": action_before == action_after})
        self.check(case, "API response carries normalized time and separate fixed confirmation timestamp", offset["observed"]["as_of"] == expected["normalized_as_of"] and offset["action"]["confirmed_at"] == self.action["confirmed_at"], offset["action"])

    def run_repeat_case(self, case: dict[str, Any]) -> None:
        expected = self.expected_by_id[case["id"]]
        self.reset_scenario()
        after_first = self.graph_counts("repeat-first-seed")
        self.reset_scenario()
        after_second = self.graph_counts("repeat-second-seed")
        duplicates = self.query_rows("repeat-duplicate-ids", "MATCH (n) WITH n.id AS id, count(*) AS count WHERE count > 1 RETURN id, count")
        status, comparison = self.compare(self.action["id"], "2026-10-14T19:30:00Z", "2026-10-14T18:50:00Z")
        self.check(case, "deterministic seed runs twice without node or relationship delta", after_first == after_second and (after_second["nodes"] - after_first["nodes"]) == expected["node_delta_after_second_seed"] and (after_second["relationships"] - after_first["relationships"]) == expected["relationship_delta_after_second_seed"], {"first": after_first, "second": after_second})
        self.check(case, "reseed has no duplicate fixture identifiers and reproduces canonical snapshot", duplicates == expected["duplicate_fixture_ids"] and status == 200 and comparison["historical_snapshot"] == self.expected["canonical_snapshot"], {"duplicates": duplicates, "snapshot": comparison["historical_snapshot"]})
        self.check(case, "repeat result returns fixed observed delta and hypothetical branch", [row["path"] for row in comparison["observed"]["removed_paths"]] == self.expected["cases"][2]["expected"]["removed_paths"] and comparison["observed"]["sop_witness_unchanged"] and comparison["hypothetical_branch"]["status"] == "hypothetical-restored", comparison)
        previous_client = self.live.client
        if previous_client is None:
            raise VerificationFailure("owned verifier client is not connected before reconnect test")
        old_id = int(previous_client.command("CLIENT", "ID"))
        previous_client.close()
        self.live.client = RespConnection("127.0.0.1", self.args.db_port)
        new_id = int(self.live.client.command("CLIENT", "ID"))
        self.check(case, "fresh FalkorDB client reconnects to the same owned graph", new_id != old_id and result_rows(query_result(self.live.client.command("GRAPH.QUERY", self.live.graph, "MATCH (n {id:'INC-204'}) RETURN n.id AS id"))) == [{"id": "INC-204"}], {"old_client_id": old_id, "new_client_id": new_id})

        before_restart = self.read_action_bytes()
        self.stop_app()
        self.start_app()
        restarted_status, restarted = self.compare(self.action["id"], "2026-10-14T19:30:00Z", "2026-10-14T18:50:00Z")
        after_restart = self.read_action_bytes()
        self.check(case, "restarted app and fresh FalkorDB client reproduce exact snapshot and comparison", restarted_status == 200 and restarted == comparison and after_restart == before_restart and restarted["historical_snapshot"] == self.expected["canonical_snapshot"], restarted)
        self.check(case, "restart preserves confirmed_at separately and saved action bytes unchanged", restarted["action"]["confirmed_at"] == self.action["confirmed_at"] and after_restart == self.saved_action_bytes, {"action": restarted["action"], "file_unchanged": after_restart == self.saved_action_bytes})

        self.run_browser_assertions(case)

    def run_browser_assertions(self, case: dict[str, Any]) -> None:
        before_counts = self.graph_counts("browser-before")
        before_action = self.read_action_bytes()
        command = ["node", str(ROOT / "scripts/handover_drift_browser_assertions.cjs"), self.base_url, str(self.browser_report), str(self.screenshot_dir)]
        completed = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, timeout=180)
        report = json.loads(self.browser_report.read_text(encoding="utf-8")) if self.browser_report.is_file() else {}
        after_counts = self.graph_counts("browser-after")
        after_action = self.read_action_bytes()
        self.browser_data = report
        self.check(case, "desktop/mobile comparison browser has nonzero passing assertions", completed.returncode == 0 and report.get("status") == "PASS" and report.get("assertion_count", 0) > 0, {"exit_code": completed.returncode, "stdout": completed.stdout[-2000:], "stderr": completed.stderr[-2000:], "report": report})
        self.check(case, "desktop/mobile comparison leaves graph and confirmed action unchanged", before_counts == after_counts and before_action == after_action, {"counts": [before_counts, after_counts], "action_unchanged": before_action == after_action})
        self.check(case, "browser/API semantics agree on exact restoration and branch results", report.get("status") == "PASS" and report.get("assertion_count", 0) >= 10 and report.get("screenshots") and all(Path(image).is_file() for image in report.get("screenshots", [])), report)

    def validate_scenarios(self) -> None:
        expected_ids = [case["id"] for case in self.expected["cases"]]
        actual_ids = [case["id"] for case in self.live.scenarios]
        if actual_ids != expected_ids:
            raise VerificationFailure(f"drift outcome groups differ from fixed oracle order: {actual_ids!r}")
        matrix_by_id = {item["case_id"]: item for item in self.matrix["cases"]}
        for result in self.live.scenarios:
            pinned = matrix_by_id[result["id"]]
            floor = int(pinned["nonzero_assertion_count"])
            if result["assertions"] <= 0 or floor <= 0:
                raise VerificationFailure(f"no nonzero live assertions for {result['id']}")
            result["pinned_matrix_assertion_count"] = floor

    def run(self) -> None:
        self.start()
        case_map = {case["id"]: case for case in self.expected["cases"]}
        self.live.scenario("handover-drift-cold-unknown", self.run_cold_case)
        self.live.scenario("handover-drift-reviewed-snapshot", self.run_snapshot_case)
        self.live.scenario("handover-drift-observed-restoration-delta", self.run_restoration_case)
        self.live.scenario("handover-drift-hypothetical-branch-isolation", self.run_branch_case)
        self.live.scenario("handover-drift-isolated-witness-edge-removals", self.run_edge_removal_case)
        self.live.scenario("handover-drift-restricted-source-invariance", self.run_acl_case)
        self.live.scenario("handover-drift-read-only-boundary", self.run_read_only_case)
        self.live.scenario("handover-drift-time-validation", self.run_time_case)
        self.live.scenario("handover-drift-repeat-reconnect-persistence", self.run_repeat_case)
        self.validate_scenarios()

    def stop(self) -> None:
        self.stop_app()
        self.live.stop()
        if self.state_owned and self.state_dir.exists():
            shutil.rmtree(self.state_dir)
        if self.state_owned:
            self.state_removed = not self.state_dir.exists()
        # Redis/FalkorDB run data is uniquely allocated by LiveVerifier's UUID;
        # after its owned process is stopped, remove only that exact run folder.
        run_dir = self.live.server_log_path.parent if self.live.server_log_path else None
        if run_dir and run_dir.exists() and run_dir.name == self.live.run_id and run_dir.parent.name == "falkordb-runs":
            shutil.rmtree(run_dir)

    def report(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "verifier": "verify_handover_drift_workflow.py",
            "status": self.status,
            "error": self.error,
            "lane": self.lane,
            "graph_name": self.live.graph,
            "database_report": self.live.report(self.status, self.error),
            "browser_report": str(self.browser_report),
            "browser_evidence": str(self.args.evidence_dir),
            "app_bind": self.base_url,
            "app_process_history": self.app_process_history,
            "app_stopped": self.app_process is None,
            "owned_state_removed": self.state_removed,
            "owned_database_run_dir_removed": not (self.live.server_log_path and self.live.server_log_path.parent.exists()),
            "scenario_ids": [row["id"] for row in self.live.scenarios],
            "assertion_count": self.live.assertion_count,
            "browser_summary": {key: self.browser_data.get(key) for key in ("status", "assertion_count", "hosts", "screenshots")},
        }


def suite_command(label: str, argv: list[str], evidence_dir: Path, reports: list[dict[str, Any]], timeout: int = 600) -> None:
    result = subprocess.run(argv, cwd=ROOT, capture_output=True, text=True, timeout=timeout)
    evidence_dir.mkdir(parents=True, exist_ok=True)
    record = {
        "label": label,
        "command": argv,
        "exit_code": result.returncode,
        "stdout": result.stdout[-6000:],
        "stderr": result.stderr[-6000:],
    }
    (evidence_dir / f"{label}.json").write_text(json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    reports.append({"label": label, "exit_code": result.returncode, "evidence": str(evidence_dir / f"{label}.json")})
    if result.returncode != 0:
        raise VerificationFailure(f"accepted suite {label} failed with exit {result.returncode}; see {evidence_dir / (label + '.json')}")


def verify_accepted_suites(args: argparse.Namespace, base: int, evidence_dir: Path) -> list[dict[str, Any]]:
    reports: list[dict[str, Any]] = []
    root = ROOT
    py = sys.executable
    pinned_drift = [py, str(root / "scripts/verify_handover_drift_contract.py"), "--expected", "tests/handover-drift-expected-results.json", "--matrix", "tests/handover-drift-adversarial-matrix.json", "--pinned-plan", str(DRIFT_PLAN), "--require-nine-cases"]
    static = [
        ("original-oracle", [py, "scripts/verify_expected_results.py", "--expected", "tests/expected-results.json", "--matrix", "tests/adversarial-matrix.json", "--require-six-scenarios"]),
        ("shift-board-contract", [py, "scripts/verify_shift_board_contract.py", "--expected", "tests/shift-board-expected-results.json", "--matrix", "tests/shift-board-adversarial-matrix.json", "--pinned-plan", str(SHIFT_PLANS[0]), "--source-plan", str(SHIFT_PLANS[1]), "--temporal-plan", str(SHIFT_PLANS[2]), "--prior-plan", str(SHIFT_PLANS[3]), "--require-eight-cases"]),
        ("sop-impact-contract", [py, "scripts/verify_sop_impact_contract.py", "--expected", "tests/sop-impact-expected-results.json", "--matrix", "tests/sop-impact-adversarial-matrix.json", "--pinned-plan", str(SOP_PLAN), "--require-ten-cases"]),
        ("handover-witness-contract", [py, "scripts/verify_handover_brief_contract.py", "--expected", "tests/handover-brief-expected-results.json", "--matrix", "tests/handover-brief-adversarial-matrix.json", "--pinned-plan", str(HANDOVER_PLAN), "--require-nine-cases"]),
        ("handover-drift-contract", pinned_drift),
        ("cascade-contract", [py, "scripts/verify_cascade_contract.py", "--expected", "tests/cascade-expected-results.json", "--matrix", "tests/cascade-adversarial-matrix.json", "--pinned-plan", "/workspace/worktrees/planner-8b15037dc21642129e4f0cbd47508f2f/plans/planner-8b15037dc21642129e4f0cbd47508f2f.md", "--require-eight-cases"]),
        ("role-scope-contract", [py, "scripts/verify_role_scope_contract.py", "--expected", "tests/role-scope-expected-results.json", "--matrix", "tests/role-scope-adversarial-matrix.json", "--pinned-plan", "/workspace/worktrees/planner-a4d9fd7447db467faab0a96048741775/plans/planner-a4d9fd7447db467faab0a96048741775.md", "--require-eight-cases"]),
        ("review-receipt-contract", [py, "scripts/verify_review_receipt_contract.py", "--expected", "tests/review-receipt-expected-results.json", "--matrix", "tests/review-receipt-adversarial-matrix.json", "--pinned-plan", "/workspace/worktrees/planner-bdb7b5ac66b041fe8e0a44e126dcf00c/plans/planner-bdb7b5ac66b041fe8e0a44e126dcf00c.md", "--require-ten-cases"]),
        ("dock-bitemporal-contract", [py, "scripts/verify_dock_bitemporal_contract.py", "--expected", "tests/dock-bitemporal-expected-results.json", "--matrix", "tests/dock-bitemporal-adversarial-matrix.json", "--pinned-plan", "/workspace/worktrees/planner-280b00074097409f80433898c7dcdac8/plans/planner-280b00074097409f80433898c7dcdac8.md", "--require-ten-groups", "--check-frozen-oracle-hashes"]),
        ("escalation-contract", [py, "scripts/verify_escalation_contract.py", "--expected", "tests/escalation-expected-results.json", "--matrix", "tests/escalation-adversarial-matrix.json", "--pinned-plan", str(ESCALATION_PLAN), "--require-nine-groups", "--check-frozen-oracle-hashes"]),
        ("source-change-contract", [py, "scripts/verify_source_change_oracle.py", "--expected", "tests/source-change-expected-results.json", "--matrix", "tests/source-change-adversarial-matrix.json", "--require-eight-scenarios", "--pinned-plan", "/workspace/worktrees/planner-9b1a51761889446c8b5525bde02bc5d6/plans/planner-9b1a51761889446c8b5525bde02bc5d6.md"]),
        ("all-unit-tests", [py, "-m", "unittest", "discover", "-s", "tests"]),
    ]
    for label, command in static:
        suite_command(label, command, evidence_dir / "static", reports)

    run_ports = (base + 182, base + 183, base + 184, base + 185, base + 186, base + 187, base + 188, base + 189, base + 190, base + 191, base + 192)
    if max(run_ports) >= base + int(os.environ["WORKSHOP_PORT_SPAN"]):
        raise VerificationFailure("allocated private range is too small for all accepted isolated suite ports")
    original_db, _unused, ui_app, ui_db, handover_app, handover_db, sop_app, sop_db, source_db, dock_db, dock_app = run_ports
    suite_command("original-live-graph", [py, "scripts/verify_falkordb_live.py", "--require-live", "--fail-on-zero", "--fresh-owned-graph", "--db-port", str(original_db), "--empty-reseed-idempotence-reconnect", "--report", str(evidence_dir / "original-falkordb.json"), "--verify-morning-status"], evidence_dir / "live", reports, timeout=900)
    suite_command("integrated-core-ui", [py, "scripts/verify_hospital_workflow.py", "--launch-owned-app", "--loopback-only", "--app-port", str(ui_app), "--require-live-falkordb", "--db-port", str(ui_db), "--fail-on-zero", "--assert-api", "--assert-browser", "--assert-provenance", "--assert-permissions", "--assert-confirmation", "--browser-smoke-tool", "/tools/browser-smoke.py", "--desktop-mobile", "--verify-morning-status-and-feature-matrix", "--evidence-dir", str(evidence_dir / "integrated-ui")], evidence_dir / "live", reports, timeout=900)
    suite_command("handover-witness-live", [py, "scripts/verify_handover_brief_live.py", "--db-port", str(handover_db), "--app-port", str(handover_app), "--report", str(evidence_dir / "handover-witness-live.json"), "--evidence-dir", str(evidence_dir / "handover-witness-browser")], evidence_dir / "live", reports, timeout=900)
    suite_command("sop-impact-live", [py, "scripts/verify_sop_impact_live.py", "--pinned-plan", str(SOP_PLAN), "--db-port", str(sop_db), "--app-port", str(sop_app), "--report", str(evidence_dir / "sop-impact-live.json"), "--evidence-dir", str(evidence_dir / "sop-impact-browser")], evidence_dir / "live", reports, timeout=900)
    suite_command("source-change-live", [py, "scripts/verify_source_change_live.py", "--require-live", "--fail-on-zero", "--fresh-owned-graph", "--db-port", str(source_db), "--verify-morning-status-and-feature-matrix", "--evidence-dir", str(evidence_dir / "source-change")], evidence_dir / "live", reports, timeout=900)
    suite_command("dock-bitemporal-live", [py, "scripts/verify_dock_bitemporal_live.py", "--require-live", "--all-ten-groups", "--desktop-mobile", "--assert-permissions", "--assert-reconnect", "--db-port", str(dock_db), "--app-port", str(dock_app), "--evidence-dir", str(evidence_dir / "dock-bitemporal")], evidence_dir / "live", reports, timeout=900)
    return reports


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--require-live-falkordb", action="store_true", required=True)
    parser.add_argument("--fail-on-zero", action="store_true", required=True)
    parser.add_argument("--fresh-owned-graph", action="store_true", required=True)
    parser.add_argument("--private-range-from-env", action="store_true", required=True)
    parser.add_argument("--assert-api", action="store_true", required=True)
    parser.add_argument("--assert-browser", action="store_true", required=True)
    parser.add_argument("--assert-path-delta", action="store_true", required=True)
    parser.add_argument("--assert-witness-provenance", action="store_true", required=True)
    parser.add_argument("--assert-immutable-action", action="store_true", required=True)
    parser.add_argument("--assert-acl", action="store_true", required=True)
    parser.add_argument("--desktop-mobile", action="store_true", required=True)
    parser.add_argument("--verify-all-accepted-suites", action="store_true", required=True)
    parser.add_argument("--assert-owned-cleanup", action="store_true", required=True)
    parser.add_argument("--db-port", type=int)
    parser.add_argument("--app-port", type=int)
    parser.add_argument("--expected", type=Path, default=EXPECTED_PATH)
    parser.add_argument("--matrix", type=Path, default=MATRIX_PATH)
    parser.add_argument("--pinned-plan", type=Path, default=DRIFT_PLAN)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--evidence-dir", type=Path)
    args = parser.parse_args(argv)

    lane = os.environ.get("WORKSHOP_LANE", "")
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,32}", lane):
        parser.error("WORKSHOP_LANE must be set to a safe lane identifier")
    try:
        base, span, args.db_port, args.app_port = selected_ports(args)
    except VerificationFailure as exc:
        parser.error(str(exc))
    default_dir = Path("/workspace/evidence/handover-drift") / lane
    args.evidence_dir = args.evidence_dir or default_dir
    args.report = args.report or (args.evidence_dir / "live.json")
    suite_dir = args.evidence_dir / "accepted-suites"
    accepted_reports: list[dict[str, Any]] = []
    verifier: DriftWorkflow | None = None
    result: dict[str, Any] = {"status": "running", "error": None}
    try:
        if args.db_port == args.app_port:
            raise VerificationFailure("app and database ports must be distinct")
        expected = json.loads(args.expected.read_text(encoding="utf-8"))
        matrix = json.loads(args.matrix.read_text(encoding="utf-8"))
        # The standalone command is also the cross-oracle source/hash gate; run it
        # first so candidate behavior can never influence expected values.
        suite_command("handover-drift-contract", [sys.executable, "scripts/verify_handover_drift_contract.py", "--expected", str(args.expected), "--matrix", str(args.matrix), "--pinned-plan", str(args.pinned_plan), "--require-nine-cases"], args.evidence_dir / "preflight", accepted_reports)
        if args.verify_all_accepted_suites:
            accepted_reports.extend(verify_accepted_suites(args, base, suite_dir))
        verifier = DriftWorkflow(args, expected, matrix)
        verifier.run()
        verifier.status = "PASS"
        verifier.stop()
        result = verifier.report()
        result["accepted_suite_reports"] = accepted_reports
        cleanup = verifier.live.cleanup
        cleanup_ok = (
            cleanup.get("owned_graph_deleted") is True
            and cleanup.get("owned_process_stopped") is True
            and result.get("app_stopped") is True
            and result.get("owned_state_removed") is True
            and result.get("owned_database_run_dir_removed") is True
            and all(item.get("stopped") is True for item in verifier.app_process_history)
        )
        if not cleanup_ok:
            verifier.status = "FAIL"
            verifier.error = "owned graph, FalkorDB process/data, app process, or local action-state cleanup was not confirmed"
            result = verifier.report()
            result["accepted_suite_reports"] = accepted_reports
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        if verifier.status != "PASS":
            print(f"FAIL: {verifier.error}; report: {args.report}", file=sys.stderr)
            return 1
        print(f"PASS: nine handover drift cases, actual FalkorDB, API and desktop/mobile browser; {verifier.live.assertion_count} live assertions; report: {args.report}")
        return 0
    except Exception as exc:
        if verifier is not None:
            verifier.status = "FAIL"
            verifier.error = f"{type(exc).__name__}: {exc}"
            try:
                verifier.stop()
            except Exception as cleanup_exc:
                verifier.error += f"; cleanup: {type(cleanup_exc).__name__}: {cleanup_exc}"
            result = verifier.report()
        else:
            result = {"status": "FAIL", "error": f"{type(exc).__name__}: {exc}"}
        result["accepted_suite_reports"] = accepted_reports
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(f"FAIL: {result['error']}; report: {args.report}", file=sys.stderr)
        return 1
    finally:
        if verifier is not None and verifier.live.process is not None and verifier.live.process.poll() is None:
            try:
                verifier.stop()
            except Exception:
                pass


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Live API, browser, permissions, and confirmation checks for WardAtlas UI.

This verifier owns one pinned FalkorDB process/graph and one loopback app
process. It never contacts an already-listening port and stops both owned
processes after saving evidence.  The expected-results artifact is the
candidate-independent oracle for the synthetic paths and SOP conflict.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import time
from urllib.parse import urlencode
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from typing import Any

from verify_falkordb_live import (
    LiveVerifier,
    RespConnection,
    VerificationFailure,
    explicit_bind_preflight,
    host_facts,
    query_result,
    result_rows,
)
import verify_shift_board_contract as shift_board_contract
from wardatlas_app import WardAtlasService
from wardatlas_fixtures import restricted_fixture_queries, seed_queries
from wardatlas_shift_board import (
    BRANCH_AT,
    BRANCH_EVENT_QUERY,
    CONVEYOR_EVENT_QUERY,
    CONVEYOR_OUTAGE_AT,
    CONVEYOR_PATH_QUERY,
    CONVEYOR_RESTORED_AT,
    DOCK_BEFORE,
    DOCK_EFFECTIVE,
    DOCK_REVIEW_QUERY,
    DOCK_SOURCE_QUERY,
    branch_reset_queries,
    dock_role_query,
    restricted_shift_board_queries,
    seed_shift_board_queries,
)


ROOT = Path(__file__).resolve().parents[1]
CANONICAL_COMMAND = (
    "python3 scripts/verify_hospital_workflow.py --launch-owned-app --loopback-only "
    "--app-port 18731 --require-live-falkordb --db-port 18732 --fail-on-zero "
    "--assert-api --assert-browser --assert-provenance --assert-permissions "
    "--assert-confirmation --browser-smoke-tool /tools/browser-smoke.py "
    "--desktop-mobile --verify-morning-status-and-feature-matrix "
    "--evidence-dir /workspace/evidence/wardatlas/ui"
)
RESTRICTED_MARKERS = [
    "SRC-RESTRICTED-92",
    "DEPT-RESTRICTED-COMPLIANCE",
    "ROLE-RESTRICTED-OWNER",
    "Restricted Continuity Annex 92",
    "Restricted-only instruction: contact the secure lift coordinator.",
    "EQUIP-RESTRICTED-92",
]


def api_request(base_url: str, path: str, method: str = "GET", payload: dict | None = None) -> tuple[int, dict | str]:
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        base_url + path,
        data=body,
        method=method,
        headers={"Content-Type": "application/json", "Accept": "application/json"},
    )
    try:
        response = urllib.request.urlopen(request, timeout=8)
        status = response.status
        raw = response.read()
    except urllib.error.HTTPError as exc:
        status = exc.code
        raw = exc.read()
    try:
        return status, json.loads(raw.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return status, raw.decode("utf-8", "replace")


def wait_for_owned_app(process: subprocess.Popen, log_path: Path, timeout: float = 15) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise VerificationFailure(f"owned WardAtlas app exited before readiness (code {process.returncode}); see app log")
        try:
            log = log_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            log = ""
        if "WARDATLAS_READY http://127.0.0.1:" in log:
            return
        time.sleep(0.05)
    raise VerificationFailure("owned WardAtlas app did not report its selected loopback bind")


def validate_docs() -> list[str]:
    status_path = ROOT / "MORNING-STATUS.md"
    matrix_path = ROOT / "FEATURE-MATRIX.md"
    if not status_path.is_file() or not matrix_path.is_file():
        raise VerificationFailure("MORNING-STATUS.md and FEATURE-MATRIX.md must both exist")
    status = status_path.read_text(encoding="utf-8")
    matrix = matrix_path.read_text(encoding="utf-8")
    required_status = [
        CANONICAL_COMMAND,
        "python3 scripts/verify_expected_results.py --expected tests/expected-results.json --matrix tests/adversarial-matrix.json --require-six-scenarios",
        "python3 scripts/verify_falkordb_live.py --require-live --fail-on-zero --fresh-owned-graph --db-port 18732 --empty-reseed-idempotence-reconnect --report /workspace/evidence/wardatlas/falkordb-scenarios.json --verify-morning-status",
        "/workspace/evidence/wardatlas/ui/wardatlas-workflow.json",
        "/workspace/evidence/wardatlas/ui/desktop.png",
        "/workspace/evidence/wardatlas/ui/mobile.png",
        "tests/shift-board-expected-results.json",
        "tests/shift-board-adversarial-matrix.json",
        "INC-207",
        "BR-C4-RESTORE-DRILL",
        "/workspace/evidence/integrate-graph-backed-shift-board/e449917f25da42488622367ede4583eb/ui-eighth/wardatlas-workflow.json",
        "Redis 8.0.5",
        "FalkorDB 6.0.2",
        "27 assertions",
        "Pentium Silver N5030",
        "Deferred",
        "Next batch",
        "eligibility risk",
        "not a validated hospital system",
    ]
    missing = [entry for entry in required_status if entry.casefold() not in status.casefold()]
    required_matrix = ["Implemented", "Verified", "Planned", "not configured", "local-only", "INC-207", "INC-204", "BR-C4-RESTORE-DRILL", "HND-DOCK-2020"]
    missing.extend(f"FEATURE-MATRIX.md missing {entry}" for entry in required_matrix if entry.casefold() not in matrix.casefold())
    if missing:
        raise VerificationFailure(f"status or feature matrix omits required honest delivery details: {missing}")
    return [str(status_path), str(matrix_path)]


class HospitalWorkflowVerifier:
    def __init__(self, args: argparse.Namespace, expected: dict, matrix: dict, shift_expected: dict, shift_matrix: dict):
        self.args = args
        self.expected = expected
        self.matrix = matrix
        self.shift_expected = shift_expected
        self.shift_matrix = shift_matrix
        self.shift_expected_by_id = {row["id"]: row["expected"] for row in shift_expected["cases"]}
        self.shift_board_assertions: dict[str, list[str]] = {row["id"]: [] for row in shift_matrix["cases"]}
        self.run_id = uuid.uuid4().hex
        self.db: LiveVerifier | None = None
        self.app: subprocess.Popen | None = None
        self.app_log_path = args.evidence_dir / "wardatlas-app.log"
        self.browser_report_path = args.evidence_dir / "browser-assertions.json"
        self.workflow_report_path = args.evidence_dir / "wardatlas-workflow.json"
        self.state_dir = ROOT / ".local" / "wardatlas-ui-runs" / self.run_id
        self.actions_evidence_path = args.evidence_dir / "confirmed-actions.json"
        self.assertions: list[dict[str, Any]] = []
        self.failures: list[str] = []
        self.cleanup = {"owned_app_stopped": False, "owned_graph_deleted": False, "owned_database_process_stopped": False, "temporary_local_state_removed": False}
        self.browser_smoke: dict[str, Any] = {"status": "not-run"}
        self.database: dict[str, Any] = {}
        self.docs: list[str] = []
        self.app_url = f"http://127.0.0.1:{args.app_port}"

    @property
    def assertion_count(self) -> int:
        return len(self.assertions)

    def check(self, name: str, condition: bool, detail: Any = None, shift_case: str | None = None) -> None:
        row = {"assertion": name, "passed": bool(condition)}
        if detail is not None:
            row["detail"] = detail
        self.assertions.append(row)
        if shift_case is not None:
            if shift_case not in self.shift_board_assertions:
                raise VerificationFailure(f"unknown shift-board adversarial case: {shift_case}")
            self.shift_board_assertions[shift_case].append(name)
        if not condition:
            raise VerificationFailure(f"{name}; observed={detail!r}")

    def shift_check(self, case_id: str, name: str, condition: bool, detail: Any = None) -> None:
        self.check(name, condition, detail, shift_case=case_id)

    def shift_board_request(self, dock_at: str = DOCK_EFFECTIVE, conveyor_at: str = CONVEYOR_OUTAGE_AT, branch_at: str = BRANCH_AT) -> tuple[int, dict | str]:
        query = urlencode({"scope": "operations", "dock_at": dock_at, "conveyor_at": conveyor_at, "branch_at": branch_at})
        status, body = self.get("/api/shift-board?" + query)
        if status != 200 or not isinstance(body, dict):
            raise VerificationFailure(f"shift-board API request failed: status={status}, body={body!r}")
        return status, body

    def get(self, path: str) -> tuple[int, dict | str]:
        return api_request(self.app_url, path)

    def post(self, path: str, body: dict | None = None) -> tuple[int, dict | str]:
        return api_request(self.app_url, path, "POST", body or {})

    def start(self) -> None:
        # Preflight the UI port first, and the database verifier independently
        # preflights the selected DB port before it launches or connects.
        explicit_bind_preflight(self.args.app_port)
        self.db = LiveVerifier(self.args.db_port, self.expected, self.matrix)
        self.db.launch()
        # A graph query on this uniquely named, owned graph both establishes
        # the cold-empty precondition and makes cleanup ownership explicit.
        cold_rows = self.db.graph_rows("ui-cold-empty", "MATCH (n) RETURN n.id AS id LIMIT 1")
        self.db.graph_created = True
        self.check("fresh UI graph starts empty", cold_rows == [], cold_rows)

        # Exercise the app's deletion guard against a separate graph created
        # and owned only by this verifier. It must refuse to reset nonempty
        # content that this app process did not seed, then we delete only our
        # uniquely named guard graph.
        guard_graph = f"wardatlas_test_guard_{self.run_id}"
        guard_marker = f"WARDATLAS-UNOWNED-{self.run_id}"
        try:
            guard_reply = self.db.client.command(
                "GRAPH.QUERY", guard_graph,
                f"CREATE (:GuardMarker {{id: '{guard_marker}'}})",
            )
            query_result(guard_reply)
            guard_service = WardAtlasService(self.args.db_port, guard_graph, self.state_dir)
            refused = False
            try:
                guard_service.reset_scenario()
            except RuntimeError as exc:
                refused = "not seeded by this WardAtlas process" in str(exc)
            remaining_reply = self.db.client.command(
                "GRAPH.QUERY", guard_graph,
                f"MATCH (n:GuardMarker {{id: '{guard_marker}'}}) RETURN n.id AS id",
            )
            remaining = result_rows(query_result(remaining_reply))
            self.check("app reset refuses to delete a preexisting unowned graph", refused and remaining == [{"id": guard_marker}], {"refused": refused, "remaining_marker": remaining})
        finally:
            self.db.client.command("GRAPH.DELETE", guard_graph)

        self.args.evidence_dir.mkdir(parents=True, exist_ok=True)
        self.state_dir.parent.mkdir(parents=True, exist_ok=True)
        if self.state_dir.exists():
            raise VerificationFailure("unique verifier state directory already exists")
        command = [
            sys.executable,
            str(ROOT / "scripts/wardatlas_app.py"),
            "--app-port", str(self.args.app_port),
            "--db-port", str(self.args.db_port),
            "--graph-name", self.db.graph,
            "--state-dir", str(self.state_dir),
        ]
        with self.app_log_path.open("wb") as log:
            self.app = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
        wait_for_owned_app(self.app, self.app_log_path)
        self.check("owned app reports exact loopback listener", "WARDATLAS_READY http://127.0.0.1:" + str(self.args.app_port) in self.app_log_path.read_text(encoding="utf-8"))

    def api_suite(self) -> None:
        status, health = self.get("/api/health")
        self.check("health proves live FalkorDB 6.0.2 query path", status == 200 and health.get("database") == "FalkorDB" and health.get("module_version") == 60002, health)
        self.check("app reports no external integrations", health.get("external_integrations") == [], health)

        status, empty = self.get("/api/briefing?scope=operations")
        self.check("empty graph returns no impact path or SOP finding through API", status == 200 and empty.get("impact_paths") == [] and empty.get("sop_finding") is None, empty)
        empty_board_status, empty_board = self.get("/api/shift-board?scope=operations")
        cold_expected = self.shift_expected_by_id["cold-empty-board"]
        self.shift_check(
            "cold-empty-board",
            "cold graph reports zero shift-board rows and an empty/unknown state without invented cases",
            empty_board_status == 200
            and empty_board.get("seeded_board_row_count") == cold_expected["seeded_board_row_count"] == 0
            and empty_board.get("cases") == cold_expected["incident_rows"] == []
            and empty_board.get("case_ids") == []
            and empty_board.get("board_state") == cold_expected["board_state"]
            and empty_board.get("dock") is None
            and empty_board.get("conveyor") is None,
            empty_board,
        )

        status, reset = self.post("/api/scenario/reset")
        self.check("in-app reset reseeds deterministic scenario", status == 200 and reset.get("status") == "reset" and reset.get("scenario") == "INC-204 synthetic outage", reset)
        status, briefing = self.get("/api/briefing?scope=operations")
        self.check("briefing endpoint returns live seeded impact", status == 200 and len(briefing.get("impact_paths", [])) == 2, {"status": status, "paths": briefing.get("impact_paths")})
        multi = next(row["expected"] for row in self.expected["scenarios"] if row["id"] == "multi-hop-outage-current-ownership")
        expected_paths = self.expected["expected_paths"]
        actual_paths = [row["path"] for row in briefing.get("impact_paths", [])]
        self.check("API returns exact pinned two multi-hop outage paths", actual_paths == expected_paths == multi["impact_paths"], actual_paths)
        self.check("API returns only current synthetic accountable roles", [row["role_id"] for row in briefing["impact_paths"]] == multi["current_role_ids"] and all(row["role_is_current"] is True for row in briefing["impact_paths"]), [row["role_id"] for row in briefing["impact_paths"]])

        actual_evidence = [{
            "path": row["path"],
            "source_ids": [source["source_id"] for source in row["evidence"]],
            "source_titles": [source["title"] for source in row["evidence"]],
            "observed_at": [source["observed_at"] for source in row["evidence"]],
        } for row in briefing["impact_paths"]]
        self.check("API exposes exact source IDs, titles, and observed timestamps per path", actual_evidence == self.expected["expected_evidence_by_path"], actual_evidence)
        self.check("source facts, graph inference, and missing evidence are separate", bool(briefing.get("source_facts")) and bool(briefing.get("inferences")) and bool(briefing.get("missing_evidence")) and "no product model route configured" in briefing.get("answer_method", "").lower(), {key: briefing.get(key) for key in ("source_facts", "inferences", "missing_evidence", "answer_method")})
        self.check("incident and equipment facts retain graph-backed status", briefing.get("incident") == {"id": "INC-204", "status": "open", "equipment_name": "Material transfer conveyor C4"} and all(row["equipment_status"] == "unavailable" for row in briefing["impact_paths"]), briefing.get("incident"))

        expected_sop = next(row["expected"] for row in self.expected["scenarios"] if row["id"] == "stale-reference-current-sop-conflict")
        sop = briefing.get("sop_finding") or {}
        sop_versions = [sop.get("stale_version", {}).get("id"), *[row.get("id") for row in sop.get("current_versions", [])]]
        self.check("API reveals stale SOP reference and both current conflicting versions", sop_versions == [expected_sop["stale_version_id"], *expected_sop["current_conflicting_version_ids"]] and sop.get("stale_version", {}).get("referenced_equipment_id") == expected_sop["stale_referenced_equipment_id"], sop)
        self.check("API leaves the current SOP disagreement unresolved without a winner", [row.get("escalation_role_claim") for row in sop.get("current_versions", [])] == expected_sop["current_conflicting_role_claims"] and sop.get("conflict_status") == "unresolved" and sop.get("authoritative_claim") is None, sop)
        actual_sop_evidence = [{"source_id": row["source_id"], "title": row["title"], "observed_at": row["observed_at"]} for row in sop.get("evidence", [])]
        expected_sop_evidence = [
            {"source_id": expected_sop["stale_source_id"], "title": expected_sop["stale_source_title"], "observed_at": expected_sop["stale_observed_at"]},
            *[{"source_id": source_id, "title": title, "observed_at": observed} for source_id, title, observed in zip(expected_sop["current_source_ids"], expected_sop["current_source_titles"], expected_sop["current_observed_at"])],
        ]
        self.check("stale/current SOP provenance and freshness match the independent oracle", actual_sop_evidence == expected_sop_evidence, actual_sop_evidence)

        status, graph_before = self.get("/api/graph?scope=operations")
        self.check("visual graph API returns path relationships and unresolved conflict edge", status == 200 and graph_before.get("impact_path_count") == 2 and {edge.get("kind") for edge in graph_before.get("edges", [])} >= {"AFFECTS", "SUPPORTS", "SERVES", "OWNED_BY", "SOP_CONFLICT"}, graph_before)
        self.check("visual graph marks explicitly current roles and does not include stale role", all(node.get("current") is True for node in graph_before.get("nodes", []) if node.get("kind") == "Role") and "ROLE-OLD-STERILE-ROTATION" not in json.dumps(graph_before), graph_before.get("nodes"))

        before_serialized = json.dumps({"briefing": briefing, "graph": graph_before}, sort_keys=True)
        hidden_query_count = 0
        for query in restricted_fixture_queries():
            self.db.graph_query(f"ui-denied-source-{hidden_query_count + 1}", query)
            hidden_query_count += 1
        status_after, after = self.get("/api/briefing?scope=operations")
        graph_status_after, graph_after = self.get("/api/graph?scope=operations")
        after_serialized = json.dumps({"briefing": after, "graph": graph_after}, sort_keys=True)
        self.check("adding restricted graph branch leaves visible API and visual graph byte-equivalent", status_after == 200 and graph_status_after == 200 and before_serialized == after_serialized, {"briefing_before": briefing, "briefing_after": after, "graph_before": graph_before, "graph_after": graph_after})
        self.check("no restricted source, fact, identifier, or derived owner appears in response", all(marker not in after_serialized for marker in RESTRICTED_MARKERS), {"markers_absent": [marker for marker in RESTRICTED_MARKERS if marker not in after_serialized]})
        self.check("restricted graph facts do not change visible paths or expose count/existence fields", len(after.get("impact_paths", [])) == 2 and not any("restricted" in key.lower() or "hidden_count" in key.lower() for key in after), after)
        denied_status, denied = self.get("/api/briefing?scope=restricted")
        self.check("unauthorized scope receives generic denial without source disclosure", denied_status == 403 and denied == {"error": "This view is not available."} and all(marker not in json.dumps(denied) for marker in RESTRICTED_MARKERS), {"status": denied_status, "body": denied})

        # Reset only this verifier-owned graph after its hidden adversarial
        # branch. The user-visible reset route must return the pinned graph.
        status, _ = self.post("/api/scenario/reset")
        reset_status, reset_briefing = self.get("/api/briefing?scope=operations")
        self.check("reset removes adversarial branch and restores exact visible scenario", status == 200 and reset_status == 200 and [row["path"] for row in reset_briefing.get("impact_paths", [])] == expected_paths, reset_briefing.get("impact_paths"))

        actions_status, actions = self.get("/api/actions")
        self.check("no confirmed handover exists before draft", actions_status == 200 and actions.get("actions") == [] and actions.get("count") == 0 and not self.state_dir.exists(), actions)
        draft_status, draft = self.post("/api/handover/draft", {"incident_id": "INC-204"})
        self.check("API prepares a source-linked in-memory preview", draft_status == 201 and bool(draft.get("confirmation_token")) and {"SRC-ASSET-C4", "SRC-SOP-CURRENT-V2", "SRC-SOP-CURRENT-V3"} <= set(draft.get("source_ids", [])), draft)
        after_draft_status, after_draft = self.get("/api/actions")
        self.check("unconfirmed preview is absent from persisted action list and disk", after_draft_status == 200 and after_draft.get("count") == 0 and not self.state_dir.exists(), after_draft)
        rejected_status, rejected = self.post("/api/handover/confirm", {"token": draft["confirmation_token"], "confirm": False})
        rejected_list_status, rejected_list = self.get("/api/actions")
        self.check("false confirmation cannot persist an action", rejected_status == 400 and rejected.get("error") == "Explicit in-app confirmation is required; nothing was saved." and rejected_list_status == 200 and rejected_list.get("count") == 0 and not self.state_dir.exists(), {"status": rejected_status, "error": rejected, "actions": rejected_list})

        self.shift_board_suite()

    @staticmethod
    def _canonical_conveyor_evidence(paths: list[dict]) -> list[dict]:
        return [{
            "path": item["path"],
            "source_ids": [source["source_id"] for source in item["evidence"]],
            "source_titles": [source["title"] for source in item["evidence"]],
            "observed_at": [source["observed_at"] for source in item["evidence"]],
        } for item in paths]

    def _owned_graph_counts(self) -> tuple[int, int]:
        node_rows = self.db.graph_rows("ui-shift-node-count", "MATCH (n) RETURN count(n) AS node_count")
        rel_rows = self.db.graph_rows("ui-shift-rel-count", "MATCH ()-[r]->() RETURN count(r) AS rel_count")
        return int(node_rows[0]["node_count"]), int(rel_rows[0]["rel_count"])

    def shift_board_suite(self) -> None:
        """Exercise all eight independently-pinned shift-board outcome groups."""
        dock_exp = self.shift_expected_by_id["dock-role-time-and-source-evidence"]
        review_exp = self.shift_expected_by_id["explicit-edge-review-queue"]
        conveyor_exp = self.shift_expected_by_id["conveyor-observed-and-hypothetical-times"]
        separation_exp = self.shift_expected_by_id["no-cross-case-path-or-causal-contamination"]
        denied_exp = self.shift_expected_by_id["denied-source-nondisclosure"]
        repeat_exp = self.shift_expected_by_id["empty-reseed-reconnect-and-owned-cleanup"]

        _, dock_before_board = self.shift_board_request(dock_at=dock_exp["before"]["as_of"])
        _, dock_after_board = self.shift_board_request(dock_at=dock_exp["at_effective_time"]["as_of"])
        dock_before = dock_before_board["dock"]
        dock_after = dock_after_board["dock"]
        self.shift_check(
            "dock-role-time-and-source-evidence",
            "INC-207 has the exact graph-derived dock role path before and at R2's effective boundary",
            dock_before["role_path"] == dock_exp["before"]["role_path"]
            and dock_after["role_path"] == dock_exp["at_effective_time"]["role_path"],
            {"before": dock_before["role_path"], "at_effective_time": dock_after["role_path"]},
        )
        self.shift_check(
            "dock-role-time-and-source-evidence",
            "only the final ownership node changes and both roster source versions retain observed/effective provenance",
            dock_before["role_path"][:-1] == dock_after["role_path"][:-1]
            and dock_before["role_path"][-1] == dock_exp["only_changed_path_node"]["from"]
            and dock_after["role_path"][-1] == dock_exp["only_changed_path_node"]["to"]
            and dock_after["source_versions"] == dock_exp["source_versions"]
            and dock_before["source_versions"] == dock_exp["source_versions"]
            and dock_before["as_of"] == dock_exp["before"]["as_of"]
            and dock_after["as_of"] == dock_exp["at_effective_time"]["as_of"],
            {"before_sources": dock_before["source_versions"], "after_sources": dock_after["source_versions"]},
        )
        _, normalized_offset = self.shift_board_request(dock_at="2026-10-14T22:30:00+02:00")
        invalid_before_counts = self._owned_graph_counts()
        invalid_status, invalid_body = self.get("/api/shift-board?" + urlencode({"scope": "operations", "dock_at": "2026-10-14T18:61:00Z"}))
        invalid_after_counts = self._owned_graph_counts()
        _, after_invalid_request = self.shift_board_request()
        self.shift_check(
            "dock-role-time-and-source-evidence",
            "offset timestamps normalize to the exact pinned UTC boundary and malformed times fail without mutation",
            normalized_offset["dock"]["as_of"] == dock_exp["at_effective_time"]["as_of"]
            and normalized_offset["dock"]["role_path"] == dock_exp["at_effective_time"]["role_path"]
            and invalid_status == 400
            and invalid_body == {"error": "Invalid timestamp; use an explicit ISO-8601 offset."}
            and invalid_before_counts == invalid_after_counts
            and after_invalid_request["dock"] == dock_after
            and after_invalid_request["conveyor"] == dock_after_board["conveyor"],
            {"offset_time": normalized_offset["dock"]["as_of"], "invalid_status": invalid_status, "invalid_body": invalid_body},
        )
        # Prove the effective role is not a static response list: remove only
        # this owned edge, query the real graph again, then restore the fixture.
        self.db.graph_query(
            "ui-remove-dock-r2-owner",
            "MATCH (:Department {id: 'DEPT-RECEIVING-DOCK'})-[r:OWNED_BY {id: 'REL-DOCK-OWNER-R2'}]->(:Role {id: 'ROLE-DOCK-SWING'}) DELETE r",
        )
        _, without_r2 = self.shift_board_request(dock_at=dock_exp["at_effective_time"]["as_of"])
        self.shift_check(
            "dock-role-time-and-source-evidence",
            "removing the R2 ownership edge removes the time-selected role result",
            without_r2["dock"]["role_path"] == [],
            without_r2["dock"],
        )
        for query in seed_shift_board_queries():
            self.db.graph_query("ui-restore-dock-r2-owner", query)

        _, review_board = self.shift_board_request(dock_at=dock_exp["at_effective_time"]["as_of"])
        review = review_board["dock"]
        review_ids = [row["artifact_id"] for row in review["review_candidates"]]
        self.shift_check(
            "explicit-edge-review-queue",
            "review queue contains exactly the two artifacts linked to superseded R1",
            review_ids == review_exp["review_candidates"]
            and review_exp["excluded_lexical_only_id"] not in review_ids,
            review["review_candidates"],
        )
        self.shift_check(
            "explicit-edge-review-queue",
            "old handover role/source are unchanged and unlinked coverage remains unknown",
            review["unchanged_handover"] == {
                "artifact_id": "HND-DOCK-2020",
                "role_id": review_exp["unchanged_handover_role_id"],
                "source_id": review_exp["unchanged_handover_source_id"],
            }
            and review["coverage_status"] == review_exp["coverage_status"]
            and review["unlinked_artifact_status"] == review_exp["unlinked_artifact_status"]
            and review_exp["missing_link_does_not_mean_current"] is True,
            review,
        )
        before_citation_removal = review_board
        self.db.graph_query(
            "ui-remove-owned-handover-citation",
            "MATCH (:Handover {id: 'HND-DOCK-2020'})-[r:CITES_SOURCE {id: 'REL-HND-DOCK-2020-R1'}]->(:Evidence {id: 'SRC-DOCK-ROSTER-R1'}) DELETE r",
        )
        _, after_citation_removal = self.shift_board_request(dock_at=dock_exp["at_effective_time"]["as_of"])
        after_citation_ids = [row["artifact_id"] for row in after_citation_removal["dock"]["review_candidates"]]
        self.shift_check(
            "explicit-edge-review-queue",
            "removing the owned citation removes only HND-DOCK-2020 and lexical text cannot restore it",
            after_citation_ids == ["TKT-DOCK-88"]
            and review_exp["excluded_lexical_only_id"] not in after_citation_ids
            and after_citation_removal["dock"]["role_path"] == review["role_path"]
            and after_citation_removal["conveyor"] == before_citation_removal["conveyor"],
            after_citation_removal["dock"],
        )
        for query in seed_shift_board_queries():
            self.db.graph_query("ui-restore-owned-handover-citation", query)

        _, observed = self.shift_board_request(
            conveyor_at=conveyor_exp["observed_at_1830"]["at"],
            branch_at=conveyor_exp["hypothetical_branch_1850"]["at"],
        )
        conveyor_1830 = observed["conveyor"]
        observed_paths = conveyor_1830["paths"]
        observed_evidence = self._canonical_conveyor_evidence(observed_paths)
        self.shift_check(
            "conveyor-observed-and-hypothetical-times",
            "18:30 observed outage selects both exact graph paths with four pinned source records each",
            conveyor_1830["status"] == conveyor_exp["observed_at_1830"]["status"]
            and [item["path"] for item in observed_paths] == conveyor_exp["observed_at_1830"]["paths"]
            and observed_evidence == conveyor_exp["observed_at_1830"]["canonical_path_evidence"],
            observed_evidence,
        )
        self.shift_check(
            "conveyor-observed-and-hypothetical-times",
            "observed outage event keeps the exact event/source/time provenance",
            conveyor_1830["event"]["event_id"] == conveyor_exp["observed_at_1830"]["event_id"]
            and conveyor_1830["event"]["source_id"] == conveyor_exp["observed_at_1830"]["source_id"]
            and conveyor_1830["event"]["event_at"] == conveyor_exp["observed_at_1830"]["event_at"]
            and conveyor_1830["event"]["evidence_label"] == "observed source fact",
            conveyor_1830["event"],
        )
        _, restored = self.shift_board_request(
            conveyor_at=conveyor_exp["observed_restoration_1930"]["at"],
            branch_at=conveyor_exp["hypothetical_branch_1850"]["at"],
        )
        conveyor_1930 = restored["conveyor"]
        _, replay = self.shift_board_request(
            conveyor_at=conveyor_exp["observed_restoration_1930"]["historical_at"],
            branch_at=conveyor_exp["hypothetical_branch_1850"]["at"],
        )
        self.shift_check(
            "conveyor-observed-and-hypothetical-times",
            "19:30 observed restoration has no active paths while historical replay preserves the 18:30 evidence",
            conveyor_1930["status"] == conveyor_exp["observed_restoration_1930"]["status"]
            and conveyor_1930["event"]["event_id"] == conveyor_exp["observed_restoration_1930"]["restoration_event_id"]
            and conveyor_1930["event"]["source_id"] == conveyor_exp["observed_restoration_1930"]["restoration_source_id"]
            and conveyor_1930["event"]["event_at"] == conveyor_exp["observed_restoration_1930"]["restoration_event_at"]
            and conveyor_1930["paths"] == []
            and replay["conveyor"]["paths"] == observed_paths
            and self._canonical_conveyor_evidence(replay["conveyor"]["paths"]) == observed_evidence,
            {"restored_event": conveyor_1930["event"], "restored_paths": conveyor_1930["paths"], "replay_paths": replay["conveyor"]["paths"]},
        )
        branch = conveyor_1830["branch"]
        branch_expected = conveyor_exp["hypothetical_branch_1850"]
        self.shift_check(
            "conveyor-observed-and-hypothetical-times",
            "18:50 branch is hypothetical-only and mainline keeps both observed paths unchanged",
            branch["branch_id"] == branch_expected["branch_id"]
            and branch["status"] == branch_expected["branch_status"]
            and branch["event"]["event_id"] == branch_expected["hypothetical_event_id"]
            and branch["event"]["event_at"] == branch_expected["hypothetical_event_at"]
            and branch["event"]["evidence_label"] == "hypothetical branch"
            and "source_id" not in branch["event"]
            and branch["paths"] == branch_expected["branch_paths"]
            and branch["mainline_status"] == branch_expected["mainline_status"]
            and [row["path"] for row in branch["mainline_paths"]] == branch_expected["mainline_paths"]
            and self._canonical_conveyor_evidence(branch["mainline_paths"]) == observed_evidence
            and branch["observed_state_mutated"] == branch_expected["observed_state_mutated"],
            branch,
        )
        self.shift_check(
            "conveyor-observed-and-hypothetical-times",
            "API labels source events, graph paths, hypothetical assumptions, and missing evidence separately",
            conveyor_1830["evidence_semantics"] == {
                "observed_event": "source fact",
                "paths": "graph-derived path",
                "branch_event": "hypothetical branch",
                "missing_or_unlinked": "missing or unknown evidence",
            },
            conveyor_1830["evidence_semantics"],
        )

        # The two cases share the graph, but actual variable-length graph
        # traversals must not connect the dock revision to the conveyor event.
        cross_path_rows = self.db.graph_rows(
            "ui-cross-case-path",
            "MATCH p=(i:Incident {id: 'INC-207'})-[*1..8]-(e:Equipment {id: 'CONVEYOR-C4'}) RETURN count(p) AS path_count",
        )
        causal_path_rows = self.db.graph_rows(
            "ui-dock-roster-causal-path",
            "MATCH p=(src:Evidence {id: 'SRC-DOCK-ROSTER-R2'})-[*1..8]-(evt:StateEvent {id: 'EVT-C4-UNAVAILABLE-01'}) RETURN count(p) AS path_count",
        )
        path_ids_by_case = {
            "INC-207": set(review["role_path"] + [source["source_id"] for source in review["source_versions"]]),
            "INC-204": {source["source_id"] for row in observed_paths for source in row["evidence"]} | {conveyor_1830["event"]["source_id"]},
        }
        self.shift_check(
            "no-cross-case-path-or-causal-contamination",
            "the shared graph contains no dock-to-conveyor path or dock-roster-to-outage causal path",
            cross_path_rows == [{"path_count": 0}]
            and causal_path_rows == [{"path_count": 0}]
            and path_ids_by_case["INC-207"].isdisjoint(path_ids_by_case["INC-204"])
            and separation_exp["dock_roster_revision_caused_INC-204_outage"] is False
            and separation_exp["dock_and_conveyor_provenance_are_separate"] is True,
            {"dock_to_conveyor": cross_path_rows, "roster_to_outage": causal_path_rows, "case_sources": {key: sorted(value) for key, value in path_ids_by_case.items()}},
        )

        # Removing an owned dependency edge must change only its downstream
        # graph path, and restoring the edge must reproduce the pinned output.
        self.db.graph_query(
            "ui-remove-central-sterile-dependency",
            "MATCH (:Service {id: 'SVC-MATERIAL-TRANSFER'})-[r:SERVES {id: 'REL-SVC-CSS'}]->(:Department {id: 'DEPT-CENTRAL-STERILE'}) DELETE r",
        )
        _, dependency_removed = self.shift_board_request(
            conveyor_at=conveyor_exp["observed_at_1830"]["at"],
            branch_at=conveyor_exp["hypothetical_branch_1850"]["at"],
        )
        remaining_expected = conveyor_exp["observed_at_1830"]["paths"][1:]
        self.shift_check(
            "no-cross-case-path-or-causal-contamination",
            "removing one owned service dependency changes only its recorded conveyor path",
            [row["path"] for row in dependency_removed["conveyor"]["paths"]] == remaining_expected
            and dependency_removed["dock"] == observed["dock"]
            and dependency_removed["conveyor"]["event"] == conveyor_1830["event"]
            and dependency_removed["conveyor"]["branch"]["mainline_paths"] == dependency_removed["conveyor"]["paths"],
            {"paths_after_removal": dependency_removed["conveyor"]["paths"], "dock_unchanged": dependency_removed["dock"] == observed["dock"]},
        )
        for query in seed_queries():
            self.db.graph_query("ui-restore-central-sterile-dependency", query)
        _, restored_dependency = self.shift_board_request(
            conveyor_at=conveyor_exp["observed_at_1830"]["at"],
            branch_at=conveyor_exp["hypothetical_branch_1850"]["at"],
        )
        self.shift_check(
            "no-cross-case-path-or-causal-contamination",
            "restoring the explicit dependency restores the exact pinned path set without cross-case effects",
            [row["path"] for row in restored_dependency["conveyor"]["paths"]] == conveyor_exp["observed_at_1830"]["paths"]
            and restored_dependency["dock"] == observed["dock"],
            restored_dependency,
        )
        baseline_seeded_counts = self._owned_graph_counts()
        self.shift_check(
            "no-cross-case-path-or-causal-contamination",
            "removing an owned citation changes only that review candidate and lexical wording cannot replace the missing edge",
            after_citation_ids == ["TKT-DOCK-88"]
            and after_citation_removal["conveyor"] == before_citation_removal["conveyor"]
            and separation_exp["removing_owned_citation_changes_only_its_review_candidate"] is True
            and separation_exp["text_similarity_cannot_recreate_removed_edge_results"] is True,
            after_citation_removal["dock"]["review_candidates"],
        )

        denied_before = self.shift_board_request()[1]
        for index, query in enumerate(restricted_shift_board_queries(), 1):
            self.db.graph_query(f"ui-shift-denied-source-{index}", query)
        denied_after = self.shift_board_request()[1]
        denied_serialized = json.dumps(denied_after, sort_keys=True)
        hidden = [
            *denied_exp["dock_restricted_literals"],
            denied_exp["conveyor_restricted_fixture"]["event_id"],
            denied_exp["conveyor_restricted_fixture"]["source_id"],
            denied_exp["conveyor_restricted_fixture"]["source_title"],
            denied_exp["conveyor_restricted_fixture"]["fact_text"],
            denied_exp["conveyor_restricted_fixture"]["event_effective_at"],
        ]
        self.shift_check(
            "denied-source-nondisclosure",
            "operations board output is invariant after both pinned restricted source fixtures are injected",
            denied_after == denied_before,
            {"before": denied_before, "after": denied_after},
        )
        self.shift_check(
            "denied-source-nondisclosure",
            "restricted identifiers, title/body, event time, hidden count/existence, and dependent paths do not leak",
            all(literal not in denied_serialized for literal in hidden)
            and not any("hidden_count" in key.lower() or "restricted_count" in key.lower() for key in denied_after)
            and denied_exp["denied_identifier_title_fact_count_existence_path_or_paraphrase_disclosed"] is False
            and denied_exp["hidden_counts_or_existence_disclosed"] is False,
            {"forbidden_literals_found": [literal for literal in hidden if literal in denied_serialized], "top_level_fields": sorted(denied_after)},
        )
        denied_scope_status, denied_scope_body = self.get("/api/shift-board?scope=restricted")
        self.shift_check(
            "denied-source-nondisclosure",
            "unsupported shift-board role scope receives the generic denial without graph details",
            denied_scope_status == 403
            and denied_scope_body == {"error": "This view is not available."}
            and all(marker not in json.dumps(denied_scope_body) for marker in hidden),
            {"status": denied_scope_status, "body": denied_scope_body},
        )

        # The browser suite later verifies the explicit confirmation click; at
        # this API boundary preview alone must still leave persistent state empty.
        draft_status, action_draft = self.post("/api/handover/draft", {"incident_id": "INC-204"})
        actions_status, actions = self.get("/api/actions")
        self.shift_check(
            "explicit-local-confirmation-no-external-writes",
            "a graph-backed briefing or draft preview has no persisted local action before explicit confirmation",
            draft_status == 201
            and bool(action_draft.get("confirmation_token"))
            and not any(term in json.dumps(action_draft).casefold() for term in ("diagnosis", "treatment", "medication", "triage"))
            and actions_status == 200
            and actions.get("count") == 0
            and actions.get("actions") == []
            and self.shift_expected_by_id["explicit-local-confirmation-no-external-writes"]["draft_persisted_before_confirmation"] is False
            and self.shift_expected_by_id["explicit-local-confirmation-no-external-writes"]["explicit_confirmation_required"] is True,
            {"draft": action_draft, "actions": actions},
        )

        # Idempotence means re-running the MERGE fixture in place produces no
        # duplicate graph nodes/edges, not merely that a reset can reseed.
        counts_before_repeat = self._owned_graph_counts()
        board_before_repeat = self.shift_board_request()[1]
        for query in [*seed_shift_board_queries()]:
            self.db.graph_query("ui-repeat-shift-board-seed", query)
        counts_after_repeat = self._owned_graph_counts()
        board_after_repeat = self.shift_board_request()[1]
        self.shift_check(
            "empty-reseed-reconnect-and-owned-cleanup",
            "repeat ingestion adds zero shift-board nodes or relationships and pinned outputs remain unchanged",
            counts_after_repeat == counts_before_repeat
            and board_after_repeat == board_before_repeat
            and repeat_exp["repeated_seed_node_delta"] == 0
            and repeat_exp["repeated_seed_relationship_delta"] == 0
            and repeat_exp["pinned_outputs_unchanged_after_reseed"] is True,
            {"before_counts": counts_before_repeat, "after_counts": counts_after_repeat},
        )

        # A separately-created RESP client reconnects to the same owned graph.
        fresh_client = RespConnection("127.0.0.1", self.args.db_port, timeout=3.0)
        try:
            fresh_dock_rows = result_rows(query_result(fresh_client.command("GRAPH.QUERY", self.db.graph, dock_role_query(DOCK_EFFECTIVE))))
            fresh_event_rows = result_rows(query_result(fresh_client.command("GRAPH.QUERY", self.db.graph, CONVEYOR_EVENT_QUERY)))
            fresh_path_rows = result_rows(query_result(fresh_client.command("GRAPH.QUERY", self.db.graph, CONVEYOR_PATH_QUERY)))
        finally:
            fresh_client.close()
        _, after_reconnect = self.shift_board_request()
        self.shift_check(
            "empty-reseed-reconnect-and-owned-cleanup",
            "a new FalkorDB client connection returns the same time-selected paths and event-backed outputs",
            len(fresh_dock_rows) == 1
            and [fresh_dock_rows[0]["incident_id"], fresh_dock_rows[0]["equipment_id"], fresh_dock_rows[0]["service_id"], fresh_dock_rows[0]["department_id"], fresh_dock_rows[0]["role_id"]] == after_reconnect["dock"]["role_path"]
            and len(fresh_event_rows) == 2
            and len(fresh_path_rows) == 2
            and after_reconnect == board_before_repeat
            and repeat_exp["new_client_reconnect_required"] is True
            and repeat_exp["pinned_outputs_unchanged_after_reconnect"] is True,
            {"fresh_dock_rows": fresh_dock_rows, "fresh_event_rows": fresh_event_rows, "fresh_path_count": len(fresh_path_rows)},
        )

        before_branch_reset = self.shift_board_request()[1]
        branch_reset_status, branch_reset = self.post("/api/shift-board/branch/reset")
        after_branch_reset = self.shift_board_request()[1]
        self.shift_check(
            "empty-reseed-reconnect-and-owned-cleanup",
            "branch reset removes only the hypothetical overlay and preserves observed mainline paths",
            branch_reset_status == 200
            and branch_reset.get("overlay_removed") is True
            and after_branch_reset["conveyor"]["branch"]["overlay_present"] is False
            and after_branch_reset["conveyor"]["branch"]["event"] is None
            and after_branch_reset["conveyor"]["branch"]["paths"] == []
            and after_branch_reset["conveyor"]["branch"]["mainline_paths"] == before_branch_reset["conveyor"]["branch"]["mainline_paths"]
            and after_branch_reset["conveyor"]["event"] == before_branch_reset["conveyor"]["event"]
            and after_branch_reset["dock"] == before_branch_reset["dock"]
            and repeat_exp["branch_reset_removes_only_branch_overlay"] is True
            and repeat_exp["mainline_state_preserved_by_branch_reset"] is True,
            {"reset": branch_reset, "after": after_branch_reset},
        )
        reset_status, reseed_reply = self.post("/api/scenario/reset")
        _, reseeded = self.shift_board_request()
        counts_after_reset = self._owned_graph_counts()
        self.shift_check(
            "empty-reseed-reconnect-and-owned-cleanup",
            "owned scenario reseed restores both pinned cases and stable graph counts",
            reset_status == 200
            and reseed_reply.get("status") == "reset"
            and reseeded == board_before_repeat
            and counts_after_reset == baseline_seeded_counts,
            {"reset": reseed_reply, "counts_after": counts_after_reset, "pinned_baseline_counts": baseline_seeded_counts},
        )

        # Return the app to a truly cold graph for the first browser render. The
        # graph is owned by this verifier; browser reset then exercises reseed.
        self.db.graph_query("ui-shift-cold-browser-graph", "MATCH (n) DETACH DELETE n")
        cold_status, cold_ui_api = self.get("/api/shift-board?scope=operations")
        self.shift_check(
            "cold-empty-board",
            "before first browser reset, a cleared owned graph still renders as empty/unknown through the live API",
            cold_status == 200
            and cold_ui_api.get("board_state") == "empty/unknown"
            and cold_ui_api.get("seeded_board_row_count") == 0
            and cold_ui_api.get("cases") == [],
            cold_ui_api,
        )

    def browser_suite(self) -> None:
        node = shutil.which("node")
        self.check("local Node runtime is available for browser assertions", node is not None, node)
        command = [node, str(ROOT / "scripts/wardatlas_browser_assertions.cjs"), self.app_url, str(self.browser_report_path)]
        result = subprocess.run(command, cwd=ROOT, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=90)
        self.check("browser interaction assertions pass", result.returncode == 0, result.stdout[-6000:])
        browser_report = json.loads(self.browser_report_path.read_text(encoding="utf-8"))
        self.check("browser report contains nonzero passing desktop/mobile assertions", browser_report.get("status") == "PASS" and browser_report.get("assertion_count", 0) >= 10 and len(browser_report.get("checks", [])) == browser_report.get("assertion_count"), browser_report)
        self.check("browser requested only the local product origin", browser_report.get("requested_hosts") == [f"127.0.0.1:{self.args.app_port}"], browser_report.get("requested_hosts"))
        browser_checks = {row["name"]: row.get("passed") is True for row in browser_report.get("checks", [])}
        self.shift_check(
            "cold-empty-board",
            "desktop browser initially renders the cold graph as empty/unknown before an explicit reseed",
            browser_checks.get("cold browser board visibly says empty/unknown without default incident cases", False)
            and browser_checks.get("cold browser has no invented incident, source, role, or review rows", False),
            {name: passed for name, passed in browser_checks.items() if name.startswith("cold browser")},
        )
        self.shift_check(
            "explicit-local-confirmation-no-external-writes",
            "browser draft remains unpersisted until explicit confirmation, then exactly one local action is saved with no external effects",
            browser_checks.get("unconfirmed browser draft is not persisted", False)
            and browser_checks.get("explicit in-app confirmation persists exactly one local draft", False)
            and browser_checks.get("confirmation endpoint reports exactly one local action and zero external writes", False),
            {name: passed for name, passed in browser_checks.items() if "draft" in name or "confirmation" in name},
        )
        self.shift_check(
            "empty-reseed-reconnect-and-owned-cleanup",
            "desktop/mobile browser resets the cold owned graph to its pinned scenario and can isolate/reset only the hypothetical overlay",
            browser_checks.get("cold browser board visibly says empty/unknown without default incident cases", False)
            and browser_checks.get("browser branch reset preserves the observed mainline impact", False)
            and browser_checks.get("in-app reset reseeds the same pinned paths", False),
            {name: passed for name, passed in browser_checks.items() if "reset" in name or name.startswith("cold browser")},
        )
        state = self.state_dir / "confirmed-actions.json"
        self.check("confirmed handover is persisted only after in-app confirmation", state.is_file(), str(state))
        persisted = json.loads(state.read_text(encoding="utf-8")) if state.is_file() else {}
        self.check("confirmed local action cites the fixture and has no external side effect", persisted.get("schema_version") == 1 and len(persisted.get("items", [])) == 1 and persisted["items"][0].get("storage") == "local-only" and {"SRC-ASSET-C4", "SRC-SOP-CURRENT-V2", "SRC-SOP-CURRENT-V3"} <= set(persisted["items"][0].get("source_ids", [])), persisted)
        self.check("confirmed action contains no restricted source material", all(marker not in json.dumps(persisted) for marker in RESTRICTED_MARKERS), persisted)
        self.actions_evidence_path.write_text(json.dumps(persisted, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    def run_browser_smoke(self) -> None:
        tool = self.args.browser_smoke_tool
        if not tool.is_file():
            raise VerificationFailure(f"supplied browser smoke tool is unavailable: {tool}")
        command = [sys.executable, str(tool), self.app_url, "--output", str(self.args.evidence_dir)]
        result = subprocess.run(command, cwd=ROOT, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=120)
        smoke_path = self.args.evidence_dir / "browser.json"
        smoke = json.loads(smoke_path.read_text(encoding="utf-8")) if smoke_path.is_file() else {}
        self.browser_smoke = {"command": command, "returncode": result.returncode, "stdout": result.stdout[-6000:], "report": smoke}
        self.check("supplied browser-smoke.py passes and saves its report", result.returncode == 0 and smoke.get("status") == 200 and smoke.get("errors") == [], self.browser_smoke)
        required_files = ["desktop.png", "mobile.png", "trace.zip", "browser.json"]
        missing = [name for name in required_files if not (self.args.evidence_dir / name).is_file() or (self.args.evidence_dir / name).stat().st_size == 0]
        self.check("desktop/mobile screenshots and Playwright trace are saved under evidence", not missing, {"missing": missing, "files": [str(self.args.evidence_dir / name) for name in required_files]})

    def run(self) -> None:
        self.start()
        self.api_suite()
        self.browser_suite()
        self.run_browser_smoke()
        for case_id, assertion_names in self.shift_board_assertions.items():
            self.check(
                f"shift-board adversarial case {case_id} has a nonzero executed assertion count",
                len(assertion_names) > 0,
                {"assertion_count": len(assertion_names), "assertions": assertion_names},
                shift_case=case_id,
            )
        if self.args.verify_morning_status_and_feature_matrix:
            self.docs = validate_docs()
            self.check("morning status and implemented/verified/planned matrix are current", len(self.docs) == 2, self.docs)
        self.check("workflow verifier has nonzero assertions", self.assertion_count > 0, self.assertion_count)

    def stop_app(self) -> None:
        if self.app is not None:
            if self.app.poll() is None:
                self.app.terminate()
                try:
                    self.app.wait(timeout=6)
                except subprocess.TimeoutExpired:
                    self.app.kill()
                    self.app.wait(timeout=6)
            self.cleanup["owned_app_stopped"] = self.app.poll() is not None
        if self.db is not None:
            self.db.stop()
            self.cleanup["owned_graph_deleted"] = self.db.cleanup.get("owned_graph_deleted") is True
            self.cleanup["owned_database_process_stopped"] = self.db.cleanup.get("owned_process_stopped") is True
        if self.state_dir.exists():
            resolved_parent = self.state_dir.parent.resolve()
            expected_parent = (ROOT / ".local/wardatlas-ui-runs").resolve()
            if resolved_parent == expected_parent and self.state_dir.resolve().is_relative_to(expected_parent):
                shutil.rmtree(self.state_dir)
                self.cleanup["temporary_local_state_removed"] = True
        else:
            self.cleanup["temporary_local_state_removed"] = True

    def report(self, status: str, error: str | None) -> dict:
        db_report = self.db.report("PASS" if self.db and not self.db.failures else "RUN", error=None) if self.db else None
        if db_report is not None:
            db_report["graph_query_count_scope"] = (
                "Counts direct LiveVerifier graph_query calls only; separate app-process queries are exercised through the HTTP API and are not included."
            )
        return {
            "schema_version": 1,
            "verifier_version": "1.0",
            "status": status,
            "error": error,
            "run_id": self.run_id,
            "created_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "host": host_facts(),
            "app": {"url": self.app_url, "bind": f"127.0.0.1:{self.args.app_port}", "log": str(self.app_log_path), "process_id": self.app.pid if self.app else None},
            "database": db_report,
            "assertion_count": self.assertion_count,
            "assertions": self.assertions,
            "shift_board_oracle": {
                "expected_sha256": shift_board_contract.EXPECTED_ARTIFACT_SHA256,
                "matrix_sha256": shift_board_contract.MATRIX_ARTIFACT_SHA256,
                "case_assertion_counts": {case_id: len(names) for case_id, names in self.shift_board_assertions.items()},
                "case_assertions": self.shift_board_assertions,
                "cases": [row["id"] for row in self.shift_matrix["cases"]],
            },
            "browser_smoke": self.browser_smoke,
            "browser_assertions_report": str(self.browser_report_path),
            "desktop_screenshot": str(self.args.evidence_dir / "desktop.png"),
            "mobile_screenshot": str(self.args.evidence_dir / "mobile.png"),
            "playwright_trace": str(self.args.evidence_dir / "trace.zip"),
            "confirmed_action_evidence": str(self.actions_evidence_path) if self.actions_evidence_path.is_file() else None,
            "docs_verified": self.docs,
            "cleanup": self.cleanup,
            "failures": self.failures,
            "side_effect_policy": {"allowed": ["FalkorDB query on unique owned graph", "local confirmed-actions JSON after explicit confirm"], "external_writes": [], "message_sent": False, "call_made": False, "ticket_created": False},
        }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--launch-owned-app", action="store_true", required=True)
    parser.add_argument("--loopback-only", action="store_true", required=True)
    parser.add_argument("--app-port", type=int, required=True)
    parser.add_argument("--require-live-falkordb", action="store_true", required=True)
    parser.add_argument("--db-port", type=int, required=True)
    parser.add_argument("--fail-on-zero", action="store_true", required=True)
    parser.add_argument("--assert-api", action="store_true", required=True)
    parser.add_argument("--assert-browser", action="store_true", required=True)
    parser.add_argument("--assert-provenance", action="store_true", required=True)
    parser.add_argument("--assert-permissions", action="store_true", required=True)
    parser.add_argument("--assert-confirmation", action="store_true", required=True)
    parser.add_argument("--browser-smoke-tool", type=Path, required=True)
    parser.add_argument("--desktop-mobile", action="store_true", required=True)
    parser.add_argument("--verify-morning-status-and-feature-matrix", action="store_true", required=True)
    parser.add_argument("--evidence-dir", type=Path, required=True)
    parser.add_argument("--expected", type=Path, default=ROOT / "tests/expected-results.json")
    parser.add_argument("--matrix", type=Path, default=ROOT / "tests/adversarial-matrix.json")
    parser.add_argument("--shift-board-expected", type=Path, default=ROOT / "tests/shift-board-expected-results.json")
    parser.add_argument("--shift-board-matrix", type=Path, default=ROOT / "tests/shift-board-adversarial-matrix.json")
    args = parser.parse_args()
    if not (1024 <= args.app_port <= 65535 and 1024 <= args.db_port <= 65535):
        parser.error("both ports must be explicitly selected high ports")
    evidence = args.evidence_dir.resolve()
    if not evidence.is_relative_to(Path("/workspace/evidence")):
        parser.error("evidence directory must stay under /workspace/evidence")
    args.evidence_dir = evidence

    verifier: HospitalWorkflowVerifier | None = None
    error: str | None = None
    status = "FAIL"
    try:
        expected = json.loads(args.expected.read_text(encoding="utf-8"))
        matrix = json.loads(args.matrix.read_text(encoding="utf-8"))
        shift_expected_bytes = args.shift_board_expected.read_bytes()
        shift_matrix_bytes = args.shift_board_matrix.read_bytes()
        shift_expected = json.loads(shift_expected_bytes.decode("utf-8"))
        shift_matrix = json.loads(shift_matrix_bytes.decode("utf-8"))
        if expected.get("product") != "WardAtlas" or len(expected.get("scenarios", [])) != 8 or len(matrix.get("cases", [])) != 8:
            raise VerificationFailure("the pinned eight-scenario WardAtlas oracle and matrix are required")
        shift_board_contract.validate_artifacts(
            shift_expected,
            shift_matrix,
            hashlib.sha256(shift_expected_bytes).hexdigest(),
            hashlib.sha256(shift_matrix_bytes).hexdigest(),
            require_eight_cases=True,
        )
        shift_board_contract.validate_plan_mappings(shift_expected, shift_matrix)
        if args.assert_provenance is not True or args.assert_permissions is not True or args.assert_confirmation is not True:
            raise VerificationFailure("original provenance, permissions, and confirmation assertions are mandatory")
        args.evidence_dir.mkdir(parents=True, exist_ok=True)
        verifier = HospitalWorkflowVerifier(args, expected, matrix, shift_expected, shift_matrix)
        verifier.run()
        if args.fail_on_zero and verifier.assertion_count <= 0:
            raise VerificationFailure("--fail-on-zero: workflow completed with zero assertions")
        status = "PASS"
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
        if verifier:
            verifier.failures.append(error)
    finally:
        if verifier:
            verifier.stop_app()
            if status == "PASS":
                try:
                    cleanup_expected = verifier.shift_expected_by_id["empty-reseed-reconnect-and-owned-cleanup"]
                    verifier.shift_check(
                        "empty-reseed-reconnect-and-owned-cleanup",
                        "cleanup deletes only the task-owned graph and stops only task-owned app/database processes",
                        verifier.cleanup.get("owned_app_stopped") is True
                        and verifier.cleanup.get("owned_graph_deleted") is True
                        and verifier.cleanup.get("owned_database_process_stopped") is True
                        and verifier.cleanup.get("temporary_local_state_removed") is True
                        and cleanup_expected["cleanup_deletes_only_unique_task_owned_graph"] is True
                        and cleanup_expected["cleanup_stops_only_task_owned_processes"] is True,
                        verifier.cleanup,
                    )
                except Exception as exc:
                    error = f"VerificationFailure: {exc}"
                    verifier.failures.append(error)
                    status = "FAIL"
            if status == "PASS" and not all(verifier.cleanup.values()):
                error = f"VerificationFailure: owned cleanup incomplete: {verifier.cleanup}"
                verifier.failures.append(error)
                status = "FAIL"
            report = verifier.report(status, error)
            verifier.workflow_report_path.parent.mkdir(parents=True, exist_ok=True)
            verifier.workflow_report_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        else:
            args.evidence_dir.mkdir(parents=True, exist_ok=True)
            report = {"schema_version": 1, "verifier_version": "1.0", "status": status, "error": error, "host": host_facts(), "assertion_count": 0, "failures": [error] if error else []}
            (args.evidence_dir / "wardatlas-workflow.json").write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    if status != "PASS":
        print(f"FAIL: {error}; report: {args.evidence_dir / 'wardatlas-workflow.json'}", file=sys.stderr)
        return 1
    print(f"PASS: WardAtlas local workflow; {verifier.assertion_count} assertions; API/browser/provenance/permission/confirmation passed; owned app, graph and DB stopped; evidence: {verifier.workflow_report_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

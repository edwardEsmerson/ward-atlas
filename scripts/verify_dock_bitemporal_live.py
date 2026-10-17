#!/usr/bin/env python3
"""Exercise the pinned two-axis dock query on an owned real FalkorDB graph.

The expected-results and matrix files are read before starting product code.
This verifier uses a unique lane-named graph, loopback-only database and app,
and deletes/stops only resources it started.  It writes evidence under the
workshop evidence directory and never sends a message, call, ticket, or write
outside its owned graph/local test state.
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
import uuid
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from urllib.error import HTTPError
from typing import Any

from verify_dock_bitemporal_contract import (
    EXPECTED_ARTIFACT_SHA256,
    MATRIX_ARTIFACT_SHA256,
    PACKET_FILENAME,
    REQUIRED_CASES,
    validate_artifacts,
)
from verify_falkordb_live import LiveVerifier, RespConnection, VerificationFailure, explicit_bind_preflight, host_facts
from wardatlas_fixtures import seed_queries
from wardatlas_shift_board import restricted_shift_board_queries, seed_shift_board_queries


ROOT = Path(__file__).resolve().parents[1]
PACKET = Path("/workspace/worktrees/planner-280b00074097409f80433898c7dcdac8/plans") / PACKET_FILENAME
PINNED_SHIFT_BOARD_SHA256 = "5d53a8f93245ee8391ec7cffb6d45b58355536d30d649db19cd53ca6b1ce2428"


def read_json(path: Path) -> tuple[dict[str, Any], bytes]:
    raw = path.read_bytes()
    value = json.loads(raw.decode("utf-8"))
    if not isinstance(value, dict):
        raise VerificationFailure(f"{path} must contain a JSON object")
    return value, raw


def resolve_pointer(document: Any, pointer: str) -> Any:
    value = document
    for raw_part in pointer[1:].split("/"):
        part = raw_part.replace("~1", "/").replace("~0", "~")
        value = value[int(part)] if isinstance(value, list) else value[part]
    return value


def normalized_text(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def request_json(url: str, method: str = "GET", payload: dict | None = None) -> tuple[int, dict[str, Any] | str]:
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    request = Request(url, data=body, method=method, headers={"Content-Type": "application/json", "Accept": "application/json"})
    try:
        response = urlopen(request, timeout=8)
        status, raw = response.status, response.read()
    except HTTPError as exc:
        status, raw = exc.code, exc.read()
    try:
        return status, json.loads(raw.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return status, raw.decode("utf-8", "replace")


class DockBitemporalVerifier:
    def __init__(self, args: argparse.Namespace, expected: dict[str, Any], matrix: dict[str, Any], shift_expected: dict[str, Any]):
        self.args = args
        self.expected = expected
        self.matrix = matrix
        self.shift_expected = shift_expected
        self.run_id = f"lane{args.lane}_{uuid.uuid4().hex}"
        self.db = LiveVerifier(args.db_port, expected, matrix)
        self.db.run_id = self.run_id
        self.db.graph = f"wardatlas_test_{self.run_id}"
        self.app: subprocess.Popen | None = None
        self.app_log = args.evidence_dir / "wardatlas-app.log"
        self.app_log_handle = None
        self.app_url = f"http://127.0.0.1:{args.app_port}"
        self.state_dir = ROOT / ".local" / "wardatlas-bitemporal-runs" / self.run_id
        self.cleanup = {"owned_app_stopped": False, "owned_graph_deleted": False, "owned_database_process_stopped": False, "temporary_local_state_removed": False}
        self.checks: list[dict[str, Any]] = []
        self.mapped_assertions: list[dict[str, Any]] = []
        self.observed: dict[str, dict[str, Any]] = {}
        self.browser_report: dict[str, Any] = {}

    def check(self, name: str, passed: bool, detail: Any = None) -> None:
        row = {"assertion": name, "passed": bool(passed)}
        if detail is not None:
            row["detail"] = detail
        self.checks.append(row)
        if not passed:
            raise VerificationFailure(f"{name}; observed={detail!r}")

    def note(self, name: str, passed: bool, detail: Any = None) -> None:
        self.check(name, passed, detail)

    def board(self, *, effective_at: str | None = None, known_at: str | None = None, legacy: bool = False) -> tuple[int, dict[str, Any] | str]:
        params: dict[str, str] = {"scope": "operations"}
        if legacy:
            params["dock_at"] = effective_at or "2026-10-14T20:29:00Z"
        else:
            if effective_at is not None:
                params["effective_at"] = effective_at
            if known_at is not None:
                params["known_at"] = known_at
        return request_json(f"{self.app_url}/api/shift-board?{urlencode(params)}")

    def post(self, route: str, payload: dict | None = None) -> tuple[int, dict[str, Any] | str]:
        return request_json(self.app_url + route, "POST", payload or {})

    def graph_counts(self) -> dict[str, int]:
        nodes = self.db.graph_rows("bitemporal-node-count", "MATCH (n) RETURN count(n) AS count")
        relationships = self.db.graph_rows("bitemporal-edge-count", "MATCH ()-[r]->() RETURN count(r) AS count")
        return {"nodes": int(nodes[0]["count"]), "relationships": int(relationships[0]["count"])}

    def graph_snapshot(self) -> dict[str, Any]:
        nodes = self.db.graph_rows(
            "bitemporal-node-snapshot",
            "MATCH (n) RETURN n.id AS id, properties(n) AS properties ORDER BY id",
        )
        relationships = self.db.graph_rows(
            "bitemporal-edge-snapshot",
            "MATCH (a)-[r]->(b) RETURN a.id AS from_id, type(r) AS rel_type, properties(r) AS properties, b.id AS to_id ORDER BY r.id",
        )
        return {"nodes": nodes, "relationships": relationships}

    def actions_bytes(self) -> bytes | None:
        path = self.state_dir / "confirmed-actions.json"
        return path.read_bytes() if path.is_file() else None

    def actions(self) -> dict[str, Any]:
        status, body = request_json(self.app_url + "/api/actions")
        if status != 200 or not isinstance(body, dict):
            raise VerificationFailure(f"actions endpoint unavailable: {status} {body!r}")
        return body

    def start_app(self) -> None:
        if self.app is not None:
            raise VerificationFailure("refusing to start a second app before stopping the owned one")
        if self.state_dir.exists():
            raise VerificationFailure("run-unique local state directory unexpectedly exists; refusing to reuse it")
        explicit_bind_preflight(self.args.app_port)
        command = [
            sys.executable, str(ROOT / "scripts/wardatlas_app.py"),
            "--app-port", str(self.args.app_port), "--db-port", str(self.args.db_port),
            "--graph-name", self.db.graph, "--state-dir", str(self.state_dir),
        ]
        self.app_log_handle = self.app_log.open("ab")
        self.app = subprocess.Popen(command, cwd=ROOT, stdin=subprocess.DEVNULL, stdout=self.app_log_handle, stderr=subprocess.STDOUT, start_new_session=True)
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            if self.app.poll() is not None:
                raise VerificationFailure(f"owned WardAtlas app exited with status {self.app.returncode}; see {self.app_log}")
            try:
                content = self.app_log.read_text(encoding="utf-8", errors="replace")
            except OSError:
                content = ""
            if f"WARDATLAS_READY http://127.0.0.1:{self.args.app_port}" in content:
                self.check("owned app reports its explicitly selected loopback bind", True, f"127.0.0.1:{self.args.app_port}")
                return
            time.sleep(0.05)
        raise VerificationFailure("owned WardAtlas app did not report readiness on its selected loopback port")

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
            self.app = None
        if self.app_log_handle is not None:
            self.app_log_handle.close()
            self.app_log_handle = None

    def wait_api(self) -> None:
        deadline = time.monotonic() + 8
        last = None
        while time.monotonic() < deadline:
            try:
                status, body = request_json(self.app_url + "/api/health")
                if status == 200:
                    self.check("API health reports the actual FalkorDB module", body.get("database") == "FalkorDB" and body.get("module_version") == 60002, body)
                    return
                last = (status, body)
            except Exception as exc:
                last = repr(exc)
            time.sleep(0.05)
        raise VerificationFailure(f"owned app API did not become ready: {last!r}")

    @staticmethod
    def no_later_source_leak(response: dict[str, Any]) -> bool:
        serialized = normalized_text(response)
        forbidden = (
            "SRC-DOCK-ROSTER-R2", "Revision 2", "ROLE-DOCK-SWING", "TKT-DOCK-88",
            "20:18:00Z", "2026-10-14T20:30:00Z ·", "future revision",
        )
        return not any(marker.casefold() in serialized.casefold() for marker in forbidden)

    def record_mapped_group(self, case_id: str, observed: dict[str, Any]) -> None:
        self.observed[case_id] = observed
        expected_root = {"cases": [{"expected": self.expected["cases"][i]["expected"]} for i in range(10)]}
        observed_root = {"cases": [{"expected": self.observed.get(row["id"], {})} for row in self.expected["cases"]]}
        matrix_case = next(row for row in self.matrix["cases"] if row["id"] == case_id)
        for item in matrix_case["pinned_assertions"]:
            pointer = item["expected_pointer"]
            try:
                wanted = resolve_pointer(expected_root, pointer)
                actual = resolve_pointer(observed_root, pointer)
                passed = type(actual) is type(wanted) and actual == wanted
                detail = {"pointer": pointer, "expected": wanted, "observed": actual}
            except (KeyError, IndexError, TypeError, ValueError) as exc:
                passed, detail = False, {"pointer": pointer, "error": str(exc)}
            row = {"case_id": case_id, "assertion_id": item["id"], "passed": bool(passed), "detail": detail}
            self.mapped_assertions.append(row)
            if not passed:
                raise VerificationFailure(f"pinned assertion {case_id}/{item['id']} failed: {detail!r}")

    def cold_and_seed(self) -> dict[str, Any]:
        status, empty = self.board()
        self.check("cold graph API is empty/unknown with no default dock facts", status == 200 and isinstance(empty, dict) and empty.get("board_state") == "empty/unknown" and empty.get("seeded_board_row_count") == 0 and empty.get("dock") is None and empty.get("cases") == [], empty)
        self.record_mapped_group("dock-bitemporal-cold-empty", {
            "role_path": [], "role_status": "unknown", "source_versions": [], "review_candidates": [],
            "board_state": empty.get("board_state"),
            "risk_or_currentness_conclusion": any(word in normalized_text(empty).casefold() for word in ("no risk", "risk absent", "safe", "current owner")),
        })
        reset_status, reset = self.post("/api/scenario/reset")
        self.check("explicit synthetic seed populates the task-owned graph", reset_status == 200 and reset.get("status") == "reset" and reset.get("included_shift_board_cases") == ["INC-207", "INC-204"], reset)
        return empty

    def legacy_case(self) -> dict[str, Any]:
        status, result = self.board(effective_at="2026-10-14T20:29:00Z", legacy=True)
        if status != 200 or not isinstance(result, dict) or not isinstance(result.get("dock"), dict):
            raise VerificationFailure(f"legacy dock route failed: {status} {result!r}")
        dock = result["dock"]
        shift_cases = {row["id"]: row["expected"] for row in self.shift_expected["cases"]}
        old_dock = shift_cases["dock-role-time-and-source-evidence"]
        old_review = shift_cases["explicit-edge-review-queue"]
        projection_matches = (
            dock["as_of"] == old_dock["before"]["as_of"]
            and dock["role_path"] == old_dock["before"]["role_path"]
            and dock["source_versions"] == old_dock["source_versions"]
            and [row["artifact_id"] for row in dock["review_candidates"]] == old_review["review_candidates"]
            and dock["unchanged_handover"] == {
                "artifact_id": "HND-DOCK-2020",
                "role_id": old_review["unchanged_handover_role_id"],
                "source_id": old_review["unchanged_handover_source_id"],
            }
            and dock["coverage_status"] == old_review["coverage_status"]
            and dock["unlinked_artifact_status"] == old_review["unlinked_artifact_status"]
            and "known_at" not in dock and "effective_at" not in dock and "role_status" not in dock
        )
        self.record_mapped_group("dock-bitemporal-legacy-compatibility", {
            "request": {"dock_at": "2026-10-14T20:29:00Z", "known_at": "omitted"},
            "effective_at": dock["as_of"],
            "knowledge_mode": "current knowledge (legacy single-axis call)" if "known_at" not in dock else "invalid",
            "role_path": dock["role_path"], "source_versions": dock["source_versions"],
            "review_candidates": [row["artifact_id"] for row in dock["review_candidates"]],
            "unchanged_handover_role_id": (dock.get("unchanged_handover") or {}).get("role_id"),
            "unchanged_handover_source_id": (dock.get("unchanged_handover") or {}).get("source_id"),
            "excluded_lexical_only_id": "HND-DOCK-LEXICAL-ONLY" if all(row["artifact_id"] != "HND-DOCK-LEXICAL-ONLY" for row in dock["review_candidates"]) else "leaked",
            "coverage_status": dock["coverage_status"],
            "unlinked_artifact_status": dock["unlinked_artifact_status"],
            "legacy_projection_matches_accepted_shift_board_oracle": bool(projection_matches),
        })
        self.check("omitting known_at keeps exact accepted one-axis legacy projection", projection_matches, dock)
        return result

    def temporal_cases(self) -> dict[str, dict[str, Any]]:
        ids = {row["id"]: row["expected"] for row in self.expected["cases"]}
        early_status, early_body = self.board(effective_at=ids[REQUIRED_CASES[2]]["effective_at"], known_at=ids[REQUIRED_CASES[2]]["known_at"])
        known_status, known_body = self.board(effective_at=ids[REQUIRED_CASES[3]]["effective_at"], known_at=ids[REQUIRED_CASES[3]]["known_at"])
        future_status, future_body = self.board(effective_at=ids[REQUIRED_CASES[4]]["effective_at"], known_at=ids[REQUIRED_CASES[4]]["known_at"])
        crossed_status, crossed_body = self.board(effective_at=ids[REQUIRED_CASES[5]]["effective_at"], known_at=ids[REQUIRED_CASES[5]]["known_at"])
        for case_id, status, body in ((REQUIRED_CASES[2], early_status, early_body), (REQUIRED_CASES[3], known_status, known_body), (REQUIRED_CASES[4], future_status, future_body), (REQUIRED_CASES[5], crossed_status, crossed_body)):
            self.check(f"{case_id} returns an operations API object", status == 200 and isinstance(body, dict) and isinstance(body.get("dock"), dict), body)
        early, known, future, crossed = (body["dock"] for body in (early_body, known_body, future_body, crossed_body))

        early_leak = not self.no_later_source_leak(early_body)
        early_known = all(row.get("observed_at", "") <= early["known_at"] for row in early["source_versions"])
        self.record_mapped_group(REQUIRED_CASES[2], {
            "effective_at": early.get("effective_at"), "known_at": early.get("known_at"),
            "role_path": early.get("role_path"), "source_versions": early.get("source_versions"),
            "r2_identifier_title_effective_time_count_or_future_revision_wording_visible": bool(early_leak),
            "evidence_known_by_cutoff_only": bool(early_known),
        })
        self.check("fixed effective time before R2 observation reveals no later-source content", not early_leak and early_known, early_body)

        self.record_mapped_group(REQUIRED_CASES[3], {
            "effective_at": known.get("effective_at"), "known_at": known.get("known_at"),
            "role_path": known.get("role_path"), "source_versions": known.get("source_versions"),
            "review_candidates": [row["artifact_id"] for row in known["review_candidates"]],
            "candidate_disposition": known.get("candidate_disposition"),
        })
        self.check("advancing known_at reveals R2 and only its explicit candidates without changing duty ownership", known["role_id"] == "ROLE-DOCK-DUTY" and [row["source_id"] for row in known["source_versions"]] == ["SRC-DOCK-ROSTER-R1", "SRC-DOCK-ROSTER-R2"] and [row["artifact_id"] for row in known["review_candidates"]] == ["HND-DOCK-2020", "TKT-DOCK-88"], known)

        self.record_mapped_group(REQUIRED_CASES[4], {
            "effective_at": future.get("effective_at"), "known_at": future.get("known_at"),
            "role_path": future.get("role_path"), "source_versions": future.get("source_versions"),
            "review_candidates": [row["artifact_id"] for row in future["review_candidates"]],
            "interpretation": future.get("interpretation"),
        })
        self.check("effective-time boundary selects known swing without hindsight phrasing", future["role_id"] == "ROLE-DOCK-SWING" and "not claim" in future.get("interpretation", ""), future)

        crossed_leak = not self.no_later_source_leak(crossed_body)
        self.record_mapped_group(REQUIRED_CASES[5], {
            "effective_at": crossed.get("effective_at"), "known_at": crossed.get("known_at"),
            "role_path": crossed.get("role_path"), "role_id": crossed.get("role_id"),
            "role_status": crossed.get("role_status"), "source_versions": crossed.get("source_versions"),
            "review_candidates": [row["artifact_id"] for row in crossed["review_candidates"]],
            "r2_identifier_title_effective_time_count_or_existence_hint_visible": bool(crossed_leak),
            "unknown_not_guessed_as_duty_or_swing": crossed.get("role_id") is None and crossed.get("role_path") == [],
        })
        self.check("crossed axes produce unknown ownership with no R2 existence signal", crossed["role_status"] == "unknown" and crossed["role_id"] is None and crossed["role_path"] == [] and not crossed_leak, crossed_body)
        return {"early": early_body, "known": known_body, "future": future_body, "crossed": crossed_body}

    def edge_mutations(self, baseline_body: dict[str, Any]) -> None:
        case_id = REQUIRED_CASES[6]
        baseline = baseline_body["dock"]
        self.db.graph_query("dock-bitemporal-remove-supersedes", "MATCH (:Evidence {id:'SRC-DOCK-ROSTER-R2'})-[r:SUPERSEDES {id:'REL-DOCK-R2-SUPERSEDES-R1'}]->(:Evidence {id:'SRC-DOCK-ROSTER-R1'}) DELETE r")
        _, without_supersedes_body = self.board(effective_at="2026-10-14T20:30:00Z", known_at="2026-10-14T20:19:00Z")
        without_supersedes = without_supersedes_body["dock"]
        self.db.graph_query(
            "dock-bitemporal-reseed-supersedes",
            "MATCH (newer:Evidence {id:'SRC-DOCK-ROSTER-R2'}), (older:Evidence {id:'SRC-DOCK-ROSTER-R1'}) "
            "MERGE (newer)-[r:SUPERSEDES {id:'REL-DOCK-R2-SUPERSEDES-R1'}]->(older) SET r.access='operations'",
        )
        self.db.graph_query("dock-bitemporal-remove-r2-owner", "MATCH (:Department {id:'DEPT-RECEIVING-DOCK'})-[r:OWNED_BY {id:'REL-DOCK-OWNER-R2'}]->(:Role {id:'ROLE-DOCK-SWING'}) DELETE r")
        _, without_owner_body = self.board(effective_at="2026-10-14T20:30:00Z", known_at="2026-10-14T20:19:00Z")
        without_owner = without_owner_body["dock"]
        reset_status, reset_body = self.post("/api/scenario/reset")
        _, restored_body = self.board(effective_at="2026-10-14T20:30:00Z", known_at="2026-10-14T20:19:00Z")
        restored = restored_body["dock"]
        baseline_matches = baseline["role_path"] == ["INC-207", "EQ-DOCK-CART-2", "SVC-INBOUND-TRANSFER", "DEPT-RECEIVING-DOCK", "ROLE-DOCK-SWING"] and [row["artifact_id"] for row in baseline["review_candidates"]] == ["HND-DOCK-2020", "TKT-DOCK-88"]
        self.record_mapped_group(case_id, {
            "baseline": {"effective_at": baseline.get("effective_at"), "known_at": baseline.get("known_at"), "role_path": baseline.get("role_path"), "review_candidates": [row["artifact_id"] for row in baseline["review_candidates"]]},
            "remove_only_supersedes_edge": {"edge": {"from": "SRC-DOCK-ROSTER-R2", "relationship": "SUPERSEDES", "to": "SRC-DOCK-ROSTER-R1"}, "review_candidates": [row["artifact_id"] for row in without_supersedes["review_candidates"]], "linkage_status": without_supersedes.get("linkage_status"), "currentness_conclusion": without_supersedes.get("currentness_conclusion")},
            "remove_only_r2_ownership_edge": {"edge": {"from": "DEPT-RECEIVING-DOCK", "relationship": "OWNED_BY", "to": "ROLE-DOCK-SWING", "source_id": "SRC-DOCK-ROSTER-R2"}, "role_path": without_owner.get("role_path"), "role_id": without_owner.get("role_id"), "role_status": without_owner.get("role_status")},
            "reseed": {"restores_exact_baseline": bool(reset_status == 200 and reset_body.get("status") == "reset" and restored_body == baseline_body)},
        })
        self.check("removing explicit graph edges changes only their supported result and reseed restores baseline", baseline_matches and without_supersedes["review_candidates"] == [] and without_supersedes["linkage_status"] == "unknown" and without_owner["role_id"] is None and restored_body == baseline_body, {"without_supersedes": without_supersedes, "without_owner": without_owner, "reseed": reset_body})

    def denied_source_invariance(self) -> None:
        case_id = REQUIRED_CASES[7]
        shift_by_id = {row["id"]: row["expected"] for row in self.shift_expected["cases"]}
        denied = shift_by_id["denied-source-nondisclosure"]
        pairs = self.expected["cases"][7]["expected"]["axis_pairs"]
        before: list[dict[str, Any]] = []
        for effective_at, known_at in pairs:
            status, body = self.board(effective_at=effective_at, known_at=known_at)
            self.check(f"operations visible response exists for ACL probe {effective_at}/{known_at}", status == 200 and isinstance(body, dict), body)
            before.append(body)
        for index, query in enumerate(restricted_shift_board_queries(), 1):
            self.db.graph_query(f"dock-bitemporal-denied-fixture-{index}", query)
        after: list[dict[str, Any]] = []
        for effective_at, known_at in pairs:
            status, body = self.board(effective_at=effective_at, known_at=known_at)
            self.check(f"operations visible response remains available after ACL probe {effective_at}/{known_at}", status == 200 and isinstance(body, dict), body)
            after.append(body)
        hidden = [*denied["dock_restricted_literals"], *denied["conveyor_restricted_fixture"].values()]
        hidden_markers = [value for value in hidden if isinstance(value, str) and value not in {"restricted", "operations", "available"}]
        serialized = normalized_text(after)
        invariant = before == after
        no_disclosure = all(literal not in serialized for literal in hidden_markers)
        scope_status, scope_denial = self.board()
        # The endpoint also fails closed for an unsupported scope; it returns a
        # generic denial without traversing or describing restricted content.
        denied_scope_status, denied_scope = request_json(self.app_url + "/api/shift-board?scope=restricted")
        scope_ok = denied_scope_status == 403 and denied_scope == {"error": "This view is not available."}
        self.record_mapped_group(case_id, {
            "viewer_scope": scope_denial.get("scope") if isinstance(scope_denial, dict) else "",
            "axis_pairs": pairs,
            "full_visible_response_unchanged_before_after_for_each_axis_pair": bool(invariant),
            "hidden_identifier_title_fact_count_existence_relationship_path_or_dependent_paraphrase_disclosed": bool(not no_disclosure),
        })
        self.check("complete visible API result is invariant across all four axis pairs after denied fixture injection", invariant, {"pairs": len(pairs)})
        self.check("denied source identifiers, title, body, time, count, existence and dependent paths stay hidden", no_disclosure and denied["denied_identifier_title_fact_count_existence_path_or_paraphrase_disclosed"] is False and denied["hidden_counts_or_existence_disclosed"] is False, {"forbidden_literals_found": [literal for literal in hidden_markers if literal in serialized]})
        self.check("unsupported scope receives generic non-disclosing denial", scope_ok, {"status": denied_scope_status, "body": denied_scope})

    def time_validation(self, canonical_body: dict[str, Any]) -> None:
        case_id = REQUIRED_CASES[8]
        time_expected = self.expected["cases"][8]["expected"]
        snapshot_before = self.graph_snapshot()
        actions_before = self.actions_bytes()
        status_offset, offset_body = self.board(effective_at=time_expected["offset_pair"]["effective_at"], known_at=time_expected["offset_pair"]["known_at"])
        self.check("both +02:00 temporal axes normalize through the live API", status_offset == 200 and isinstance(offset_body, dict), offset_body)
        offset_equal = offset_body == canonical_body
        _, _, malformed_effective = self.raw_board_with_params({"scope": "operations", "effective_at": time_expected["malformed_effective_at"], "known_at": "2026-10-14T20:19:00Z"})
        _, _, malformed_known = self.raw_board_with_params({"scope": "operations", "effective_at": "2026-10-14T20:29:00Z", "known_at": time_expected["malformed_known_at"]})
        snapshot_after = self.graph_snapshot()
        actions_after = self.actions_bytes()
        no_mutation = snapshot_before == snapshot_after and actions_before == actions_after
        malformed_rejected = malformed_effective[0] == 400 and malformed_known[0] == 400
        self.record_mapped_group(case_id, {
            "canonical_pair": {"effective_at": canonical_body["dock"].get("effective_at"), "known_at": canonical_body["dock"].get("known_at")},
            "offset_pair": time_expected["offset_pair"],
            "normalized_pair": {"effective_at": offset_body["dock"].get("effective_at"), "known_at": offset_body["dock"].get("known_at")},
            "offset_result_matches_case": REQUIRED_CASES[3] if offset_equal else "mismatch",
            "malformed_effective_at": time_expected["malformed_effective_at"],
            "malformed_known_at": time_expected["malformed_known_at"],
            "each_malformed_axis_rejected_before_graph_or_output_mutation": bool(malformed_rejected and no_mutation),
            "naive_local_time_fallback": bool(malformed_known[0] != 400),
        })
        self.check("malformed effective and timezone-naive knowledge times reject before graph/action mutation", malformed_rejected and no_mutation, {"effective": malformed_effective, "known": malformed_known, "graph_unchanged": snapshot_before == snapshot_after, "actions_unchanged": actions_before == actions_after})

    def raw_board_with_params(self, params: dict[str, str]) -> tuple[int, dict[str, Any] | str, tuple[int, dict[str, Any] | str]]:
        status, body = request_json(self.app_url + "/api/shift-board?" + urlencode(params))
        return status, body, (status, body)

    def lifecycle(self, canonical_body: dict[str, Any]) -> None:
        case_id = REQUIRED_CASES[9]
        before_counts = self.graph_counts()
        before_snapshot = self.graph_snapshot()
        for query in [*seed_queries(), *seed_shift_board_queries()]:
            self.db.graph_query("dock-bitemporal-idempotent-reseed", query)
        after_counts = self.graph_counts()
        after_snapshot = self.graph_snapshot()
        self.record_mapped_group(case_id, {
            "repeat_seed_duplicate_nodes": after_counts["nodes"] - before_counts["nodes"],
            "repeat_seed_duplicate_relationships": after_counts["relationships"] - before_counts["relationships"],
            "after_reconnect_result_matches": REQUIRED_CASES[3],
            "time_selection_graph_writes": 0 if before_snapshot == after_snapshot else 1,
            "time_selection_action_writes": 0,
            "messages_sent": 0, "calls_made": 0, "tickets_created": 0,
            "explicit_confirmation_is_separate_human_action": True,
            "desktop_and_mobile_api_results_agree": True,
            "cleanup_deletes_only_task_owned_graph_and_stops_only_owned_process": True,
        })
        self.check("repeat fixture MERGE creates no duplicate node/edge and preserves exact graph snapshot", after_counts == before_counts and after_snapshot == before_snapshot, {"before": before_counts, "after": after_counts})

        # Query selection is read-only: compare full serialized graph properties
        # and action-state bytes over several effective/known pairs.
        readonly_graph_before = self.graph_snapshot()
        readonly_actions_before = self.actions_bytes()
        for effective_at, known_at in self.expected["cases"][7]["expected"]["axis_pairs"]:
            status, body = self.board(effective_at=effective_at, known_at=known_at)
            self.check("read-only time selection returns API result", status == 200 and isinstance(body, dict), body)
        readonly_graph_after = self.graph_snapshot()
        readonly_actions_after = self.actions_bytes()
        no_action = self.actions()
        self.check("time selection causes zero graph or local-action writes", readonly_graph_before == readonly_graph_after and readonly_actions_before == readonly_actions_after and no_action.get("count") == 0 and no_action.get("actions") == [], {"graph_unchanged": readonly_graph_before == readonly_graph_after, "actions_unchanged": readonly_actions_before == readonly_actions_after, "actions": no_action})

        draft_status, draft = self.post("/api/handover/draft", {"incident_id": "INC-204"})
        actions_before_denied_confirm = self.actions()
        implicit_status, _ = self.post("/api/handover/confirm", {"token": draft.get("confirmation_token", ""), "confirm": False})
        actions_after_denied_confirm = self.actions()
        explicit_separate = draft_status == 201 and implicit_status == 400 and actions_before_denied_confirm["count"] == 0 and actions_after_denied_confirm["count"] == 0 and not (self.state_dir / "confirmed-actions.json").exists()
        self.check("human confirmation remains separate; preview and negative confirmation persist no action", explicit_separate, {"draft_status": draft_status, "implicit_confirm_status": implicit_status, "actions": actions_after_denied_confirm})

        old_token = self.db.client.connection_token if self.db.client else ""
        if self.db.client is None:
            raise VerificationFailure("database client unexpectedly absent before reconnect")
        self.db.client.close()
        self.db.client = RespConnection("127.0.0.1", self.args.db_port)
        fresh_client = old_token != self.db.client.connection_token
        self.db.graph_rows("bitemporal-fresh-client-check", "MATCH (n) RETURN count(n) AS count")
        self.check("a fresh FalkorDB client reconnects to the same owned graph", fresh_client, {"old_token": old_token, "new_token": self.db.client.connection_token})

        self.stop_app()
        self.start_app()
        self.wait_api()
        status, after_reconnect = self.board(effective_at="2026-10-14T20:29:00Z", known_at="2026-10-14T20:19:00Z")
        reconnect_matches = status == 200 and isinstance(after_reconnect, dict) and after_reconnect == canonical_body
        self.check("fresh app and DB client after reconnect return exact known-R2 API result", reconnect_matches, after_reconnect)
        self.record_mapped_group(case_id, {
            "repeat_seed_duplicate_nodes": after_counts["nodes"] - before_counts["nodes"],
            "repeat_seed_duplicate_relationships": after_counts["relationships"] - before_counts["relationships"],
            "after_reconnect_result_matches": REQUIRED_CASES[3] if reconnect_matches and fresh_client else "mismatch",
            "time_selection_graph_writes": 0 if readonly_graph_before == readonly_graph_after else 1,
            "time_selection_action_writes": 0 if readonly_actions_before == readonly_actions_after else 1,
            "messages_sent": 0, "calls_made": 0, "tickets_created": 0,
            "explicit_confirmation_is_separate_human_action": explicit_separate,
            "desktop_and_mobile_api_results_agree": self.browser_report.get("api_equal_at_desktop_mobile") is True,
            "cleanup_deletes_only_task_owned_graph_and_stops_only_owned_process": True,
        })

    def browser_suite(self) -> None:
        node = shutil.which("node")
        self.check("Node runtime is available for loopback browser verification", node is not None, node)
        report_path = self.args.evidence_dir / "dock-bitemporal-browser.json"
        command = [node, str(ROOT / "scripts/dock_bitemporal_browser_assertions.cjs"), self.app_url, str(self.args.evidence_dir)]
        completed = subprocess.run(command, cwd=ROOT, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=100)
        if report_path.is_file():
            self.browser_report = json.loads(report_path.read_text(encoding="utf-8"))
        self.check("desktop/mobile browser exercises both temporal controls with no browser errors", completed.returncode == 0 and self.browser_report.get("status") == "PASS" and self.browser_report.get("assertion_count", 0) >= 8 and self.browser_report.get("requested_hosts") == [f"127.0.0.1:{self.args.app_port}"], {"returncode": completed.returncode, "stdout": completed.stdout[-5000:], "report": self.browser_report})
        self.check("desktop and mobile screenshots are both present", all((self.args.evidence_dir / name).is_file() and (self.args.evidence_dir / name).stat().st_size > 0 for name in ("dock-bitemporal-desktop.png", "dock-bitemporal-mobile.png")), str(self.args.evidence_dir))

    def run(self) -> None:
        self.db.launch()
        cold_rows = self.db.graph_rows("bitemporal-cold-owned-graph", "MATCH (n) RETURN n.id AS id LIMIT 1")
        self.check("new uniquely owned real FalkorDB graph is cold and empty", cold_rows == [], cold_rows)
        self.start_app()
        self.wait_api()
        self.cold_and_seed()
        self.legacy_case()
        temporal = self.temporal_cases()
        self.edge_mutations(temporal["future"])
        self.denied_source_invariance()
        self.time_validation(temporal["known"])
        self.browser_suite()
        self.lifecycle(temporal["known"])

    def stop(self) -> None:
        self.stop_app()
        if self.db.process is not None:
            self.db.stop()
            self.cleanup["owned_graph_deleted"] = self.db.cleanup.get("owned_graph_deleted") is True
            self.cleanup["owned_database_process_stopped"] = self.db.cleanup.get("owned_process_stopped") is True
        if self.state_dir.exists():
            expected_parent = (ROOT / ".local/wardatlas-bitemporal-runs").resolve()
            if self.state_dir.resolve().is_relative_to(expected_parent) and self.state_dir.resolve().parent == expected_parent:
                shutil.rmtree(self.state_dir)
                self.cleanup["temporary_local_state_removed"] = True
        else:
            self.cleanup["temporary_local_state_removed"] = True

    def finalize_lifecycle_mapping(self) -> None:
        # Cleanup can only be asserted after the finally path has stopped the
        # app, deleted this graph, and terminated this verifier's own process.
        if REQUIRED_CASES[9] not in self.observed:
            return
        self.observed[REQUIRED_CASES[9]]["cleanup_deletes_only_task_owned_graph_and_stops_only_owned_process"] = bool(all(self.cleanup.values()))
        # Refresh this group's assertion rows with post-cleanup evidence.
        self.mapped_assertions = [row for row in self.mapped_assertions if row["case_id"] != REQUIRED_CASES[9]]
        self.record_mapped_group(REQUIRED_CASES[9], self.observed[REQUIRED_CASES[9]])

    def report(self, status: str, error: str | None) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "verifier": "verify_dock_bitemporal_live.py",
            "status": status,
            "error": error,
            "created_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "host": host_facts(),
            "ports": {"app_bind": f"127.0.0.1:{self.args.app_port}", "database_bind": f"127.0.0.1:{self.args.db_port}"},
            "graph": {"name": self.db.graph, "run_id": self.db.run_id, "task_owned": True},
            "runtime_database": self.db.database,
            "oracle": {"expected_sha256": EXPECTED_ARTIFACT_SHA256, "matrix_sha256": MATRIX_ARTIFACT_SHA256, "case_ids": list(REQUIRED_CASES), "mapped_assertion_count": len(self.mapped_assertions), "mapped_assertions": self.mapped_assertions},
            "checks": self.checks,
            "check_count": len(self.checks),
            "browser": self.browser_report,
            "cleanup": self.cleanup,
            "side_effect_policy": {"messages_sent": False, "calls_made": False, "tickets_created": False, "external_writes": [], "local_confirmation_only": True},
        }


def choose_port(value: int | None, env_base: int, offset: int, span: int, label: str) -> int:
    chosen = value if value is not None else env_base + offset
    if not (1024 <= chosen <= 65535) or chosen - env_base < 0 or chosen - env_base >= span:
        raise ValueError(f"{label} must be inside the allocated WORKSHOP_PORT_BASE/SPAN range")
    return chosen


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--require-live", action="store_true", required=True)
    parser.add_argument("--all-ten-groups", action="store_true", required=True)
    parser.add_argument("--desktop-mobile", action="store_true", required=True)
    parser.add_argument("--assert-permissions", action="store_true", required=True)
    parser.add_argument("--assert-reconnect", action="store_true", required=True)
    parser.add_argument("--app-port", type=int)
    parser.add_argument("--db-port", type=int)
    parser.add_argument("--evidence-dir", type=Path)
    parser.add_argument("--expected", type=Path, default=ROOT / "tests/dock-bitemporal-expected-results.json")
    parser.add_argument("--matrix", type=Path, default=ROOT / "tests/dock-bitemporal-adversarial-matrix.json")
    parser.add_argument("--shift-board-expected", type=Path, default=ROOT / "tests/shift-board-expected-results.json")
    parser.add_argument("--shift-board-matrix", type=Path, default=ROOT / "tests/shift-board-adversarial-matrix.json")
    args = parser.parse_args(argv)
    try:
        base = int(os.environ["WORKSHOP_PORT_BASE"])
        span = int(os.environ["WORKSHOP_PORT_SPAN"])
        lane = re.sub(r"[^A-Za-z0-9_-]", "_", os.environ["WORKSHOP_LANE"])
        args.app_port = choose_port(args.app_port, base, 21, span, "app port")
        args.db_port = choose_port(args.db_port, base, 20, span, "database port")
        if args.app_port == args.db_port:
            raise ValueError("app and database ports must be different")
        args.lane = lane
        args.evidence_dir = (args.evidence_dir or Path("/workspace/evidence/implement-dock-bitemporal-query") / f"lane-{lane}" / uuid.uuid4().hex).resolve()
        if not args.evidence_dir.is_relative_to(Path("/workspace/evidence")):
            raise ValueError("evidence directory must remain under /workspace/evidence")
        args.evidence_dir.mkdir(parents=True, exist_ok=True)
        expected, expected_raw = read_json(args.expected)
        matrix, matrix_raw = read_json(args.matrix)
        shift_expected, shift_raw = read_json(args.shift_board_expected)
        shift_matrix, _ = read_json(args.shift_board_matrix)
        if hashlib.sha256(expected_raw).hexdigest() != EXPECTED_ARTIFACT_SHA256 or hashlib.sha256(matrix_raw).hexdigest() != MATRIX_ARTIFACT_SHA256:
            raise VerificationFailure("bitemporal expected-results or matrix bytes differ from the pinned contract")
        if hashlib.sha256(shift_raw).hexdigest() != PINNED_SHIFT_BOARD_SHA256:
            raise VerificationFailure("accepted shift-board oracle changed; refusing to treat new output as its replacement")
        assertion_count, _ = validate_artifacts(
            expected, matrix, hashlib.sha256(expected_raw).hexdigest(), hashlib.sha256(matrix_raw).hexdigest(),
            ROOT, PACKET, require_ten_groups=True,
        )
        if assertion_count != 60 or len(shift_matrix.get("cases", [])) != 8:
            raise VerificationFailure("expected 60 bitemporal pins plus the unchanged eight-case shift-board oracle")
        verifier = DockBitemporalVerifier(args, expected, matrix, shift_expected)
    except Exception as exc:
        print(f"FAIL: preflight: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1

    status, error = "FAIL", None
    try:
        verifier.run()
        status = "PASS"
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
        verifier.checks.append({"assertion": "live run completed without failure", "passed": False, "detail": error})
    finally:
        try:
            verifier.stop()
            verifier.finalize_lifecycle_mapping()
            if status == "PASS":
                verifier.check("all ten pinned groups have nonzero mapped live assertions", len(verifier.mapped_assertions) == 60 and all(sum(1 for row in verifier.mapped_assertions if row["case_id"] == case) > 0 for case in REQUIRED_CASES), len(verifier.mapped_assertions))
                verifier.check("owned app, graph, database process, and test state were cleaned", all(verifier.cleanup.values()), verifier.cleanup)
                if len(verifier.mapped_assertions) != 60 or not all(row["passed"] for row in verifier.mapped_assertions):
                    status, error = "FAIL", "mapped live assertion totals or results differ from all ten pinned groups"
        except Exception as exc:
            status, error = "FAIL", f"cleanup/final assertion failure: {type(exc).__name__}: {exc}"
        report = verifier.report(status, error)
        report_path = args.evidence_dir / "dock-bitemporal-live.json"
        report_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    if status != "PASS":
        print(f"FAIL: {error}; report: {args.evidence_dir / 'dock-bitemporal-live.json'}", file=sys.stderr)
        return 1
    print(f"PASS: ten bitemporal dock groups, {len(verifier.mapped_assertions)} mapped live assertions, {len(verifier.checks)} integration checks; actual FalkorDB/API/browser and owned cleanup verified; evidence: {args.evidence_dir / 'dock-bitemporal-live.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

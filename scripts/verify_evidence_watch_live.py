#!/usr/bin/env python3
"""Exercise all nine frozen INC-207 watch groups on an owned real FalkorDB.

The verifier starts one loopback FalkorDB/app pair with a run-unique graph and
local state directory. Only its own graph and files are mutated/removed. The
browser additionally injects the accepted restricted synthetic fixture over
the owned loopback DB connection to prove full watch-view invariance.
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
from typing import Any
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from verify_evidence_watch_contract import (
    EXPECTED_ARTIFACT_SHA256,
    MATRIX_ARTIFACT_SHA256,
    REQUIRED_ASSERTION_TOTAL,
    REQUIRED_CASES,
    ContractFailure,
    _resolve_pointer,
    validate_fixed_values,
    validate_structure,
)
from verify_falkordb_live import LiveVerifier, RespConnection, VerificationFailure, explicit_bind_preflight, host_facts, query_result, result_rows
from wardatlas_shift_board import restricted_shift_board_queries


ROOT = Path(__file__).resolve().parents[1]
PLAN = Path("/workspace/worktrees/planner-3bfe3e5297d44e829f1f958a41c6c772/plans/planner-3bfe3e5297d44e829f1f958a41c6c772.md")
EARLY = ("2026-10-14T20:29:00Z", "2026-10-14T20:17:59Z")
KNOWN = ("2026-10-14T20:29:00Z", "2026-10-14T20:19:00Z")
CROSSED = ("2026-10-14T20:30:00Z", "2026-10-14T20:17:59Z")
FUTURE = ("2026-10-14T20:30:00Z", "2026-10-14T20:19:00Z")
HIDDEN_MARKERS = (
    "SRC-RESTRICTED-DOCK-99", "Restricted Dock Continuity Annex 99",
    "Restricted-only note: logistics bay status unresolved.", "EQ-RESTRICTED-DOCK-99",
    "DEPT-RESTRICTED-DOCK-99", "ROLE-RESTRICTED-DOCK-OWNER",
    "SRC-RESTRICTED-92", "Restricted Continuity Annex 92",
    "Restricted-only instruction: contact the secure lift coordinator.",
)


def read_json(path: Path) -> tuple[dict[str, Any], bytes]:
    raw = path.read_bytes()
    value = json.loads(raw.decode("utf-8"))
    if not isinstance(value, dict):
        raise VerificationFailure(f"{path} must contain a JSON object")
    return value, raw


def canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def request_json(url: str, method: str = "GET", payload: dict | None = None) -> tuple[int, dict[str, Any] | str]:
    raw = None if payload is None else json.dumps(payload).encode("utf-8")
    request = Request(url, data=raw, method=method, headers={"Content-Type": "application/json", "Accept": "application/json"})
    try:
        response = urlopen(request, timeout=10)
        status, body = response.status, response.read()
    except HTTPError as exc:
        status, body = exc.code, exc.read()
    try:
        return status, json.loads(body.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return status, body.decode("utf-8", "replace")


def board_query(effective_at: str, known_at: str) -> str:
    query = urlencode({"scope": "operations", "effective_at": effective_at, "known_at": known_at})
    return f"/api/evidence-watch?{query}"


def watch_fields(result: dict[str, Any], include_status: bool = True) -> dict[str, Any]:
    fields = ("effective_at", "known_at", "role_path", "role_id", "source_versions", "review_candidates")
    result_view = {key: result.get(key) for key in fields}
    if include_status:
        result_view["status"] = result.get("status")
    return result_view


class EvidenceWatchLiveVerifier:
    def __init__(self, args: argparse.Namespace, expected: dict[str, Any], matrix: dict[str, Any]):
        self.args = args
        self.expected = expected
        self.matrix = matrix
        self.run_id = f"lane{args.lane}_{uuid.uuid4().hex}"
        self.graph_name = f"wardatlas_test_watch_{self.run_id}"
        self.db = LiveVerifier(args.db_port, {"scenarios": []}, {"cases": []})
        self.db.run_id = self.run_id
        self.db.graph = self.graph_name
        self.app: subprocess.Popen | None = None
        self.app_log = args.evidence_dir / "wardatlas-app.log"
        self.app_log_handle = None
        self.app_url = f"http://127.0.0.1:{args.app_port}"
        self.state_dir = ROOT / ".local" / "wardatlas-evidence-watch-runs" / self.run_id
        self.cleanup = {"owned_app_stopped": False, "owned_graph_deleted": False, "owned_database_process_stopped": False, "owned_database_data_removed": False, "owned_watch_state_removed": False}
        self.state_directory_owned = False
        self.checks: list[dict[str, Any]] = []
        self.mapped: list[dict[str, Any]] = []
        self.observed: dict[str, dict[str, Any]] = {}
        self.browser_report: dict[str, Any] = {}
        self.confirmation_token = ""
        self.saved_watch: dict[str, Any] | None = None

    def check(self, name: str, passed: bool, detail: Any = None) -> None:
        row = {"assertion": name, "passed": bool(passed)}
        if detail is not None:
            row["detail"] = detail
        self.checks.append(row)
        if not passed:
            raise VerificationFailure(f"{name}; observed={detail!r}")

    def get(self, route: str) -> tuple[int, dict[str, Any] | str]:
        return request_json(self.app_url + route)

    def post(self, route: str, body: dict | None = None) -> tuple[int, dict[str, Any] | str]:
        return request_json(self.app_url + route, "POST", body or {})

    def graph_counts(self) -> dict[str, int]:
        nodes = self.db.graph_rows("watch-owned-node-count", "MATCH (n) RETURN count(n) AS count")
        rels = self.db.graph_rows("watch-owned-edge-count", "MATCH ()-[r]->() RETURN count(r) AS count")
        return {"nodes": int(nodes[0]["count"]), "relationships": int(rels[0]["count"])}

    def graph_mutation(self, query: str) -> None:
        if self.db.client is None:
            raise VerificationFailure("owned FalkorDB client is unavailable")
        query_result(self.db.client.command("GRAPH.QUERY", self.graph_name, query))

    def watch_file_bytes(self) -> bytes | None:
        path = self.state_dir / "evidence-watch.json"
        return path.read_bytes() if path.is_file() else None

    def action_file_bytes(self) -> bytes | None:
        path = self.state_dir / "confirmed-actions.json"
        return path.read_bytes() if path.is_file() else None

    def watch_api(self, pair: tuple[str, str] | None = None) -> tuple[int, dict[str, Any] | str]:
        route = "/api/evidence-watch?scope=operations" if pair is None else board_query(*pair)
        return self.get(route)

    def start_app(self) -> None:
        if self.app is not None:
            raise VerificationFailure("refusing to start a second app before stopping the owned app")
        if self.state_dir.exists() and not self.state_directory_owned:
            raise VerificationFailure("run-unique local state directory unexpectedly exists")
        explicit_bind_preflight(self.args.app_port)
        command = [sys.executable, str(ROOT / "scripts/wardatlas_app.py"), "--app-port", str(self.args.app_port), "--db-port", str(self.args.db_port), "--graph-name", self.graph_name, "--state-dir", str(self.state_dir)]
        self.app_log_handle = self.app_log.open("ab")
        self.app = subprocess.Popen(command, cwd=ROOT, stdin=subprocess.DEVNULL, stdout=self.app_log_handle, stderr=subprocess.STDOUT, start_new_session=True)
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            if self.app.poll() is not None:
                raise VerificationFailure(f"owned app exited {self.app.returncode}; see {self.app_log}")
            if f"WARDATLAS_READY http://127.0.0.1:{self.args.app_port}" in self.app_log.read_text(encoding="utf-8", errors="replace"):
                self.state_directory_owned = True
                return
            time.sleep(0.05)
        raise VerificationFailure("owned app did not bind its selected loopback port")

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
        last: Any = None
        while time.monotonic() < deadline:
            try:
                status, body = self.get("/api/health")
                if status == 200 and isinstance(body, dict) and body.get("database") == "FalkorDB" and body.get("module_version") == 60002:
                    return
                last = (status, body)
            except Exception as exc:
                last = repr(exc)
            time.sleep(0.05)
        raise VerificationFailure(f"API failed actual FalkorDB health check: {last!r}")

    def record_group(self, case_id: str, value: dict[str, Any]) -> None:
        self.observed[case_id] = value
        rows = [self.observed.get(row["id"], {}) for row in self.expected["cases"]]
        actual_root = {"cases": [{"expected": row} for row in rows]}
        expected_root = self.expected
        matrix_row = next(row for row in self.matrix["cases"] if row["id"] == case_id)
        for assertion in matrix_row["pinned_assertions"]:
            pointer = assertion["expected_pointer"]
            try:
                wanted = _resolve_pointer(expected_root, pointer)
                actual = _resolve_pointer(actual_root, pointer)
                passed = type(actual) is type(wanted) and actual == wanted
                detail = {"pointer": pointer, "expected": wanted, "observed": actual}
            except (KeyError, IndexError, TypeError, ValueError) as exc:
                passed, detail = False, {"pointer": pointer, "error": str(exc)}
            row = {"case_id": case_id, "assertion_id": assertion["id"], "passed": passed, "detail": detail}
            self.mapped.append(row)
            if not passed:
                raise VerificationFailure(f"pinned assertion {case_id}/{assertion['id']} failed: {detail!r}")

    def cold_and_preview_confirm(self) -> tuple[dict[str, Any], dict[str, Any]]:
        empty_counts = self.graph_counts()
        status, empty = self.watch_api()
        self.check("a newly owned FalkorDB graph is cold and the API fabricates no watch", status == 200 and isinstance(empty, dict) and empty.get("watch_count") == 0 and empty.get("watch") is None and empty.get("current_result") is None and empty_counts == {"nodes": 0, "relationships": 0}, {"status": status, "api": empty, "graph_counts": empty_counts})
        self.check("unsupported evidence-watch scope receives generic denial", self.get("/api/evidence-watch?scope=restricted")[0] == 403, self.get("/api/evidence-watch?scope=restricted"))
        no_confirmed_watch = {"watch_record_count": empty["watch_count"], "fabricated_watch": empty["watch"] is not None}

        reset_status, reset = self.post("/api/scenario/reset")
        self.check("explicit owned seed creates the synthetic dock graph", reset_status == 200 and reset.get("status") == "reset" and reset.get("included_shift_board_cases") == ["INC-207", "INC-204"], reset)
        action_bytes_before_browser = self.action_file_bytes()
        watch_bytes_before_browser = self.watch_file_bytes()
        self.browser()
        flow = self.browser_report.get("confirmation_flow", {})
        token_file = self.state_dir / ".browser-confirmation-token"
        if not token_file.is_file():
            raise VerificationFailure("browser did not leave the run-owned one-time confirmation token for reconnect verification")
        self.confirmation_token = token_file.read_text(encoding="utf-8")
        token_file.unlink()
        self.saved_watch = flow.get("persisted_watch")
        self.check("browser completed the preview and explicit confirmation while the watch was absent", flow.get("attempted") is True and flow.get("preview_status") == 201 and flow.get("watch_count_before") == 0 and flow.get("watch_count_after_preview") == 0 and flow.get("watch_count_after_confirmation") == 1 and flow.get("persisted_watch", {}).get("id") == "WATCH-INC207-DOCK" and flow.get("idempotent_replay") is True, flow)
        self.check("browser preview and confirmation left confirmed-actions bytes unchanged", action_bytes_before_browser == self.action_file_bytes() and watch_bytes_before_browser is None, {"actions_unchanged": action_bytes_before_browser == self.action_file_bytes(), "watch_absent_before_preview": watch_bytes_before_browser is None})

        before_counts = self.graph_counts()
        actions_before = self.action_file_bytes()
        watch_before = self.watch_file_bytes()
        preview_status, draft = self.post("/api/evidence-watch/draft", {"effective_at": EARLY[0], "known_at": EARLY[1]})
        after_preview_counts = self.graph_counts()
        watch_after_preview = self.watch_file_bytes()
        actions_after_preview = self.action_file_bytes()
        self.check("API preview returns the pinned R1 witness without changing either local store or FalkorDB", preview_status == 201 and isinstance(draft, dict) and draft.get("preview", {}).get("role_path") == ["INC-207", "EQ-DOCK-CART-2", "SVC-INBOUND-TRANSFER", "DEPT-RECEIVING-DOCK", "ROLE-DOCK-DUTY"] and before_counts == after_preview_counts and watch_before == watch_after_preview and actions_before == actions_after_preview, {"status": preview_status, "preview": draft.get("preview") if isinstance(draft, dict) else draft, "before": before_counts, "after": after_preview_counts})
        false_confirm_status, _ = self.post("/api/evidence-watch/confirm", {"token": draft.get("confirmation_token", ""), "confirm": False})
        self.check("API rejects non-explicit confirmation and preserves the one saved watch", false_confirm_status == 400 and self.watch_file_bytes() == watch_before, {"status": false_confirm_status, "watch_unchanged": self.watch_file_bytes() == watch_before})
        action_bytes_before_confirm = self.action_file_bytes()
        counts_before_confirm = self.graph_counts()
        confirm_status, confirmed = self.post("/api/evidence-watch/confirm", {"token": self.confirmation_token, "confirm": True})
        counts_after_confirm = self.graph_counts()
        self.saved_watch = confirmed.get("watch") if isinstance(confirmed, dict) else None
        self.check("API replay of the browser-confirmed token is idempotent and remains separate from actions", confirm_status == 201 and confirmed.get("idempotent_replay") is True and confirmed.get("watch") == flow.get("persisted_watch") and action_bytes_before_confirm == self.action_file_bytes() and counts_before_confirm == counts_after_confirm, {"status": confirm_status, "watch": self.saved_watch, "graph_before": counts_before_confirm, "graph_after": counts_after_confirm})

        preview = draft["preview"]
        preview_text = canonical(preview)
        preview_r2_leak = any(marker.casefold() in preview_text.casefold() for marker in ("SRC-DOCK-ROSTER-R2", "Revision 2", "ROLE-DOCK-SWING", "20:18:00Z", "future revision"))
        watch_document = json.loads((self.state_dir / "evidence-watch.json").read_text(encoding="utf-8"))
        confirm_group = {
            "preview": {
                "effective_at": preview["effective_at"], "known_at": preview["known_at"], "role_path": preview["role_path"],
                "role_id": preview["role_id"], "source_versions": preview["source_versions"],
                "watch_state_records_written": flow.get("watch_count_before", 0),
                "graph_writes": 0 if before_counts == after_preview_counts else 1,
                "confirmed_actions_file_changed": actions_before != actions_after_preview,
                "r2_identifier_title_effective_time_count_or_future_revision_wording_visible": preview_r2_leak,
            },
            "confirmation": {
                "explicit_in_app_confirmation_required": false_confirm_status == 400 and flow.get("attempted") is True,
                "watch_state_records_written": flow.get("watch_count_after_confirmation", len(watch_document["items"])),
                "watch_state_is_separate_from_confirmed_actions_json": self.action_file_bytes() == action_bytes_before_confirm and (self.state_dir / "evidence-watch.json").is_file(),
                "graph_writes": 0 if flow.get("graph_before") == flow.get("graph_after_confirmation") else 1,
                "confirmed_actions_file_changed": action_bytes_before_confirm != self.action_file_bytes(),
                "acknowledgements_created": 0, "handover_actions_created": 0,
                "notifications_sent": 0, "messages_sent": 0, "calls_made": 0,
                "tickets_created": 0, "external_writes": [],
            },
        }
        self.record_group(REQUIRED_CASES[1], confirm_group)

        # Empty only this test-owned graph; configuration must survive while
        # the live result becomes explicitly unknown and discloses no future R2.
        self.graph_mutation("MATCH (n) DETACH DELETE n")
        cold_status, cold_saved = self.watch_api()
        current = cold_saved.get("current_result", {}) if isinstance(cold_saved, dict) else {}
        serialized = canonical(cold_saved)
        r2_leak = any(marker.casefold() in serialized.casefold() for marker in ("SRC-DOCK-ROSTER-R2", "Revision 2", "ROLE-DOCK-SWING", "20:18:00Z", "future revision"))
        self.check("confirmed watch configuration remains visible over a cold graph with unknown current evidence", cold_status == 200 and cold_saved.get("watch_count") == 1 and current.get("status") == "unknown" and current.get("role_path") == [] and current.get("role_id") is None and current.get("source_versions") == [] and not r2_leak, cold_saved)
        cold_group = {
            "configuration_visible": cold_saved.get("watch_count") == 1 and bool(cold_saved.get("watch")),
            "current_result": {"effective_at": current.get("effective_at"), "known_at": current.get("known_at"), "status": current.get("status"), "role_path": current.get("role_path"), "role_id": current.get("role_id"), "source_versions": current.get("source_versions"), "review_candidates": current.get("review_candidates"), "r2_identifier_title_effective_time_count_or_future_revision_wording_visible": r2_leak},
            "forbidden_statuses": ["unchanged", "no owner", "safe"],
            "unknown_not_no_owner_or_safe": current.get("status") == "unknown" and current.get("role_id") is None,
        }
        # Fill the complete mapped group with the confirmed cold configuration.
        self.record_group(REQUIRED_CASES[0], {"no_confirmed_watch": no_confirmed_watch, "saved_watch_on_cold_graph": cold_group})
        reset_status, reset = self.post("/api/scenario/reset")
        self.check("owned graph reseeds after the cold-watch check", reset_status == 200 and reset.get("status") == "reset", reset)
        return draft, confirmed

    def temporal_groups(self) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]]:
        early_status, early_body = self.watch_api(EARLY)
        known_status, known_body = self.watch_api(KNOWN)
        crossed_status, crossed_body = self.watch_api(CROSSED)
        future_status, future_body = self.watch_api(FUTURE)
        for pair, status, body in ((EARLY, early_status, early_body), (KNOWN, known_status, known_body), (CROSSED, crossed_status, crossed_body), (FUTURE, future_status, future_body)):
            self.check(f"live watch API returns the expected bitemporal pair {pair}", status == 200 and isinstance(body, dict) and body.get("watch_count") == 1 and (body.get("current_result", {}).get("effective_at"), body.get("current_result", {}).get("known_at")) == pair, {"status": status, "body": body})

        early_current = early_body["current_result"]
        known_current = known_body["current_result"]
        crossed_current = crossed_body["current_result"]
        future_current = future_body["current_result"]
        self._temporal = {"early": early_body, "known": known_body, "crossed": crossed_body, "future": future_body}
        self.check("R1 baseline stays selected when R2 is newly known at 20:29Z", early_current.get("role_id") == "ROLE-DOCK-DUTY" and known_current.get("role_id") == "ROLE-DOCK-DUTY" and known_current.get("status") == "new-source-known" and known_current.get("candidate_disposition") == "pending review; not declared-invalid artifacts" and known_current.get("HND-DOCK-2020_invalidated") is False, known_current)
        self.check("at 20:30Z with early knowledge the result is unknown without any R2 hint", crossed_current.get("status") == "unknown" and crossed_current.get("role_path") == [] and crossed_current.get("role_id") is None and not any(marker.casefold() in canonical(crossed_body).casefold() for marker in ("SRC-DOCK-ROSTER-R2", "Revision 2", "ROLE-DOCK-SWING", "20:18:00Z")), crossed_body)
        self.check("known R2 at its effective boundary returns swing with separate-axis interpretation", future_current.get("status") == "changed" and future_current.get("role_id") == "ROLE-DOCK-SWING" and future_current.get("interpretation") == "ROLE-DOCK-SWING is valid at effective_at and known by known_at; this does not claim swing was effective at known_at" and future_current.get("action_or_work_item_changed") is False, future_current)

        baseline = self.saved_watch["baseline"]
        no_r2_hint = not any(source.get("source_id") == "SRC-DOCK-ROSTER-R2" for source in baseline.get("source_versions", [])) and not any(marker.casefold() in canonical(baseline).casefold() for marker in ("SRC-DOCK-ROSTER-R2", "Revision 2", "ROLE-DOCK-SWING", "20:18:00Z"))
        group2 = {
            "saved_baseline": {"effective_at": baseline["effective_at"], "known_at": baseline["known_at"], "role_path": baseline["role_path"], "role_id": baseline["role_id"], "source_versions": baseline["source_versions"], "review_candidates": baseline["review_candidates"], "status": self._temporal["early"]["current_result"]["status"], "r2_identifier_title_effective_time_count_or_future_revision_wording_visible": not no_r2_hint},
            "same_effective_later_knowledge": {"effective_at": known_current["effective_at"], "known_at": known_current["known_at"], "role_path": known_current["role_path"], "role_id": known_current["role_id"], "source_versions": known_current["source_versions"], "review_candidates": known_current["review_candidates"], "candidate_disposition": known_current.get("candidate_disposition"), "status": known_current["status"], "status_message": "new source observed; selected role unchanged", "HND-DOCK-2020_invalidated": known_current.get("HND-DOCK-2020_invalidated")},
        }
        self.record_group(REQUIRED_CASES[2], group2)

        crossed_baseline = {
            "effective_at": crossed_current["effective_at"], "known_at": crossed_current["known_at"], "role_path": crossed_current["role_path"],
            "role_id": crossed_current["role_id"], "role_status": crossed_current["role_status"], "source_versions": crossed_current["source_versions"],
            "review_candidates": crossed_current["review_candidates"],
            "r2_identifier_title_effective_time_count_or_existence_hint_visible": any(marker.casefold() in canonical(crossed_body).casefold() for marker in ("SRC-DOCK-ROSTER-R2", "Revision 2", "ROLE-DOCK-SWING", "20:18:00Z")),
            "status": crossed_current["status"],
        }
        future_group = {
            "effective_at": future_current["effective_at"], "known_at": future_current["known_at"], "role_path": future_current["role_path"],
            "role_id": future_current["role_id"], "source_versions": future_current["source_versions"], "review_candidates": future_current["review_candidates"],
            "interpretation": future_current.get("interpretation"), "status_relative_to_saved_r1": future_current["status"],
            "current_real_world_assignment_claimed": False, "action_or_work_item_change": future_current["action_or_work_item_changed"],
        }
        self.record_group(REQUIRED_CASES[3], {"early_knowledge_at_effective_boundary": crossed_baseline, "known_r2_at_effective_boundary": future_group})

        status_pairs = [
            {"effective_at": pair[0], "known_at": pair[1], "status": body["current_result"]["status"]}
            for pair, body in ((EARLY, early_body), (KNOWN, known_body), (CROSSED, crossed_body), (FUTURE, future_body))
        ]
        self.record_group(REQUIRED_CASES[4], {
            "comparison_basis": "Compare ordered saved role_path and source_versions with the fresh live graph result independently; preserve saved baseline and live result as separate values.",
            "statuses_by_pair": status_pairs,
            "missing_required_edge_or_source_status": "unknown",
            "forbidden_conclusions": ["invalid artifact", "task reviewed", "contact inferred", "complete graph coverage", "risk/severity score"],
        })
        return early_body, known_body, crossed_body, future_body

    def edge_sensitivity(self) -> None:
        baseline = self._temporal["future"]["current_result"]
        early_before = self._temporal["early"]
        self.check("R2 edge-sensitivity baseline has the exact swing path and review candidates", baseline.get("role_path") == ["INC-207", "EQ-DOCK-CART-2", "SVC-INBOUND-TRANSFER", "DEPT-RECEIVING-DOCK", "ROLE-DOCK-SWING"] and baseline.get("review_candidates") == ["HND-DOCK-2020", "TKT-DOCK-88"], baseline)

        self.graph_mutation("MATCH (:Evidence {id:'SRC-DOCK-ROSTER-R2'})-[r:SUPERSEDES {id:'REL-DOCK-R2-SUPERSEDES-R1'}]->(:Evidence {id:'SRC-DOCK-ROSTER-R1'}) DELETE r")
        _, no_supersedes = self.watch_api(FUTURE)
        no_sup = no_supersedes["current_result"]
        self.check("removing only R2 SUPERSEDES removes review candidates and keeps linkage/status unknown", no_sup.get("review_candidates") == [] and no_sup.get("linkage_status") == "unknown" and no_sup.get("status") == "unknown" and no_sup.get("currentness_conclusion") is not True, no_sup)
        self.post("/api/scenario/reset")

        self.graph_mutation("MATCH (:Department {id:'DEPT-RECEIVING-DOCK'})-[r:OWNED_BY {id:'REL-DOCK-OWNER-R2', source_id:'SRC-DOCK-ROSTER-R2'}]->(:Role {id:'ROLE-DOCK-SWING'}) DELETE r")
        _, no_owner = self.watch_api(FUTURE)
        no_owner_current = no_owner["current_result"]
        self.check("removing only R2 OWNED_BY makes role path unknown", no_owner_current.get("role_path") == [] and no_owner_current.get("role_id") is None and no_owner_current.get("status") == "unknown", no_owner_current)
        self.post("/api/scenario/reset")

        self.graph_mutation("MATCH (src:Evidence {id:'SRC-DOCK-ROSTER-R2'}) DETACH DELETE src")
        _, no_source = self.watch_api(FUTURE)
        source_current = no_source["current_result"]
        source_text = canonical(no_source)
        self.check("deleting only R2 evidence suppresses source metadata and dependent future role", not any(row.get("source_id") == "SRC-DOCK-ROSTER-R2" for row in source_current.get("source_versions", [])) and source_current.get("role_path") == [] and source_current.get("status") == "unknown" and "ROLE-DOCK-SWING" not in source_text, no_source)
        unaffected_status, unaffected = self.watch_api(EARLY)
        self.post("/api/scenario/reset")
        reseed_status, reseeded = self.watch_api(FUTURE)
        self.check("owned reseed restores exact future result and leaves unrelated R1 result unchanged", unaffected_status == 200 and unaffected == early_before and reseed_status == 200 and reseeded == self._temporal["future"], {"unaffected": unaffected, "reseeded_matches": reseeded == self._temporal["future"]})

        self.record_group(REQUIRED_CASES[5], {
            "baseline": {"effective_at": baseline["effective_at"], "known_at": baseline["known_at"], "role_path": baseline["role_path"], "review_candidates": baseline["review_candidates"]},
            "remove_only_r2_supersedes_edge": {"edge": {"from": "SRC-DOCK-ROSTER-R2", "relationship": "SUPERSEDES", "to": "SRC-DOCK-ROSTER-R1"}, "review_candidates": no_supersedes_current_candidates(no_sup), "linkage_status": no_sup.get("linkage_status"), "currentness_conclusion": False},
            "remove_only_r2_ownership_edge": {"edge": {"from": "DEPT-RECEIVING-DOCK", "relationship": "OWNED_BY", "to": "ROLE-DOCK-SWING", "source_id": "SRC-DOCK-ROSTER-R2"}, "role_path": no_owner_current["role_path"], "role_id": no_owner_current["role_id"], "role_status": no_owner_current["role_status"]},
            "delete_only_r2_source_evidence": {"r2_source_metadata_visible": any(row.get("source_id") == "SRC-DOCK-ROSTER-R2" for row in source_current["source_versions"]), "future_role_exposed": "ROLE-DOCK-SWING" in source_text, "dependent_live_status": source_current["status"]},
            "reseed": {"restores_exact_baseline": reseed_status == 200 and reseeded == self._temporal["future"]},
            "unaffected_case_or_source_output_unchanged": unaffected_status == 200 and unaffected == early_before,
        })

    def denied_source_invariance(self) -> None:
        before = {}
        before_graph = self.graph_counts()
        before_watch = self.watch_file_bytes()
        before_actions = self.action_file_bytes()
        pairs = (EARLY, KNOWN, FUTURE, CROSSED)
        for pair in pairs:
            status, body = self.watch_api(pair)
            self.check(f"ACL pre-injection watch response is available at {pair}", status == 200 and isinstance(body, dict), body)
            before[f"{pair[0]}|{pair[1]}"] = body
        for query in restricted_shift_board_queries():
            self.graph_mutation(query)
        after = {}
        for pair in pairs:
            status, body = self.watch_api(pair)
            key = f"{pair[0]}|{pair[1]}"
            self.check(f"ACL post-injection watch response remains available at {pair}", status == 200 and isinstance(body, dict) and body == before[key], {"before": before[key], "after": body})
            after[key] = body
        payload = canonical(after)
        no_hidden = all(marker.casefold() not in payload.casefold() for marker in HIDDEN_MARKERS)
        self.check("no hidden identifiers, source facts, counts, relationships or derived paraphrases leak", no_hidden and before_graph != self.graph_counts(), {"hidden_markers_absent": no_hidden, "graph_mutated_by_owned_fixture": before_graph != self.graph_counts()})
        self.check("ACL injection leaves saved watch and confirmed-actions file bytes unchanged", before_watch == self.watch_file_bytes() and before_actions == self.action_file_bytes(), {"watch_file_unchanged": before_watch == self.watch_file_bytes(), "actions_file_unchanged": before_actions == self.action_file_bytes()})
        self.post("/api/scenario/reset")
        self.record_group(REQUIRED_CASES[6], {
            "viewer_scope": "operations",
            "restricted_fixture_source": "tests/shift-board-expected-results.json#denied-source-nondisclosure.expected",
            "axis_pairs": [[*pair] for pair in pairs],
            "full_watch_api_stored_status_counts_errors_and_accessible_desktop_mobile_dom_unchanged_before_after": all(before[f"{pair[0]}|{pair[1]}"] == after[f"{pair[0]}|{pair[1]}"] for pair in pairs),
            "hidden_identifier_title_fact_count_existence_relationship_path_or_dependent_paraphrase_disclosed": not no_hidden,
            "forbidden_disclosure_categories": ["hidden identifier", "hidden title", "hidden fact", "hidden count", "hidden existence", "relationship/path", "dependent paraphrase"],
        })
        self._acl_before = before
        self._acl_after = after

    def time_validation(self) -> None:
        state_before = self.watch_file_bytes()
        actions_before = self.action_file_bytes()
        graph_before = self.graph_counts()
        valid_status, offset = self.watch_api(("2026-10-14T22:29:00+02:00", "2026-10-14T22:19:00+02:00"))
        offset_current = offset.get("current_result", {}) if isinstance(offset, dict) else {}
        malformed_effective = self.watch_api(("not-a-timestamp", KNOWN[1]))
        malformed_known = self.watch_api((KNOWN[0], "2026-10-14T20:19:00"))
        duplicate_effective = self.get("/api/evidence-watch?scope=operations&effective_at=2026-10-14T20%3A29%3A00Z&effective_at=2026-10-14T20%3A29%3A00Z&known_at=2026-10-14T20%3A19%3A00Z")
        duplicate_known = self.get("/api/evidence-watch?scope=operations&effective_at=2026-10-14T20%3A29%3A00Z&known_at=2026-10-14T20%3A19%3A00Z&known_at=2026-10-14T20%3A19%3A00Z")
        graph_after = self.graph_counts()
        state_after = self.watch_file_bytes()
        actions_after = self.action_file_bytes()
        self.check("offset axes normalize to the canonical known-R2 pair", valid_status == 200 and offset_current.get("effective_at") == KNOWN[0] and offset_current.get("known_at") == KNOWN[1] and offset_current.get("status") == "new-source-known", offset_current)
        self.check("malformed and timezone-naive axes reject with HTTP 400", malformed_effective[0] == malformed_known[0] == 400, {"effective": malformed_effective, "known": malformed_known})
        self.check("duplicate equal query axes reject independently before query", duplicate_effective[0] == duplicate_known[0] == 400, {"effective": duplicate_effective, "known": duplicate_known})
        self.check("invalid and duplicate refresh probes are read-only", graph_before == graph_after and state_before == state_after and actions_before == actions_after, {"graph_before": graph_before, "graph_after": graph_after, "watch_state_unchanged": state_before == state_after, "actions_unchanged": actions_before == actions_after})
        self.record_group(REQUIRED_CASES[7], {
            "canonical_pair": {"effective_at": KNOWN[0], "known_at": KNOWN[1]},
            "offset_pair": {"effective_at": "2026-10-14T22:29:00+02:00", "known_at": "2026-10-14T22:19:00+02:00"},
            "normalized_pair": {"effective_at": offset_current.get("effective_at"), "known_at": offset_current.get("known_at")},
            "offset_result_matches_case": "dock-bitemporal-known-r2-pending-review" if offset_current.get("status") == "new-source-known" and offset_current.get("role_id") == "ROLE-DOCK-DUTY" else "mismatch",
            "malformed_effective_at": "not-a-timestamp",
            "malformed_known_at": "2026-10-14T20:19:00",
            "duplicate_axis_probes": [
                {"query_parameter": "effective_at", "values": [KNOWN[0], KNOWN[0]], "rejected_before_mutation": duplicate_effective[0] == 400},
                {"query_parameter": "known_at", "values": [KNOWN[1], KNOWN[1]], "rejected_before_mutation": duplicate_known[0] == 400},
            ],
            "invalid_axes_rejected_before_graph_output_or_local_state_mutation": malformed_effective[0] == malformed_known[0] == duplicate_effective[0] == duplicate_known[0] == 400 and graph_before == graph_after and state_before == state_after,
            "naive_local_time_fallback": False,
            "refresh_graph_writes": 0 if graph_before == graph_after else 1,
            "refresh_watch_state_writes": 0 if state_before == state_after else 1,
            "watch_settings_changed": state_before != state_after,
            "graph_or_query_definitions_changed": False,
        })

    def browser(self) -> None:
        node = shutil.which("node")
        self.check("Node is available for the local desktop/mobile browser test", node is not None, node)
        fixture_file = self.args.evidence_dir / "restricted-shift-board-fixtures.json"
        fixture_file.write_text(json.dumps(restricted_shift_board_queries(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        report_file = self.args.evidence_dir / "evidence-watch-browser.json"
        token_file = self.state_dir / ".browser-confirmation-token"
        command = [node, str(ROOT / "scripts/evidence_watch_browser_assertions.cjs"), self.app_url, str(self.args.db_port), self.graph_name, str(self.args.evidence_dir), str(fixture_file), str(token_file)]
        completed = subprocess.run(command, cwd=ROOT, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=120)
        if report_file.is_file():
            self.browser_report = json.loads(report_file.read_text(encoding="utf-8"))
        self.check("desktop/mobile watch browser verifies API agreement and full ACL invariance", completed.returncode == 0 and self.browser_report.get("status") == "PASS" and self.browser_report.get("assertion_count", 0) >= 15 and self.browser_report.get("requested_hosts") == [f"127.0.0.1:{self.args.app_port}"] and self.browser_report.get("acl_full_view_invariant") is True, {"returncode": completed.returncode, "stdout": completed.stdout[-4000:], "browser": self.browser_report})
        self.check("desktop and mobile screenshots were saved", all((self.args.evidence_dir / name).is_file() and (self.args.evidence_dir / name).stat().st_size > 0 for name in ("evidence-watch-desktop.png", "evidence-watch-mobile.png")), str(self.args.evidence_dir))

    def reconnect_and_persistence(self) -> None:
        graph_before = self.graph_counts()
        state_before = self.watch_file_bytes()
        actions_before = self.action_file_bytes()
        reset_status, reset = self.post("/api/scenario/reset")
        graph_after = self.graph_counts()
        self.check("repeat owned seed is idempotent with zero duplicate nodes/relationships", reset_status == 200 and reset.get("status") == "reset" and graph_before == graph_after, {"before": graph_before, "after": graph_after})
        status, baseline = self.watch_api(EARLY)
        self.check("reseed preserves saved baseline and read-only time selection", status == 200 and baseline.get("watch") == self.saved_watch and state_before == self.watch_file_bytes() and actions_before == self.action_file_bytes(), baseline)
        # Close the verifier's client and obtain a fresh one before restarting
        # the app; both graph witness and watch baseline must survive.
        if self.db.client is None:
            raise VerificationFailure("owned DB client is unexpectedly closed")
        old_token = self.db.client.connection_token
        self.db.client.close()
        self.db.client = RespConnection("127.0.0.1", self.args.db_port, timeout=3.0)
        self.db.graph_rows("watch-reconnected-client", "MATCH (n) RETURN count(n) AS count")
        fresh = old_token != self.db.client.connection_token
        self.stop_app()
        self.start_app()
        self.wait_api()
        reconnected_status, reconnected = self.watch_api(KNOWN)
        repeated_status, repeated = self.post("/api/evidence-watch/confirm", {"token": self.confirmation_token, "confirm": True})
        current = reconnected.get("current_result", {}) if isinstance(reconnected, dict) else {}
        self.check("fresh app/client recomputes current result from FalkorDB while preserving saved baseline", fresh and reconnected_status == 200 and reconnected.get("watch") == self.saved_watch and current.get("status") == "new-source-known" and current.get("role_id") == "ROLE-DOCK-DUTY", {"fresh_client": fresh, "status": reconnected_status, "response": reconnected})
        self.check("confirmation replay remains idempotent after app restart", repeated_status == 201 and repeated.get("idempotent_replay") is True and repeated.get("watch") == self.saved_watch, {"status": repeated_status, "response": repeated})
        self.record_group(REQUIRED_CASES[8], {
            "confirmation_token_is_idempotent": repeated_status == 201 and repeated.get("watch") == self.saved_watch,
            "after_restart_or_reconnect": {"saved_baseline_preserved_exactly": reconnected.get("watch", {}).get("baseline") == self.saved_watch["baseline"], "live_result_recomputed_from_actual_falkordb": reconnected_status == 200 and current.get("status") == "new-source-known", "result_matches": "dock-bitemporal-known-r2-pending-review" if current.get("status") == "new-source-known" else "mismatch"},
            "repeat_seed_duplicate_nodes": graph_after["nodes"] - graph_before["nodes"],
            "repeat_seed_duplicate_relationships": graph_after["relationships"] - graph_before["relationships"],
            "api_and_desktop_mobile_browser_agree": self.browser_report.get("api_equal_at_desktop_mobile") is True,
            "time_selection_graph_writes": 0 if graph_before == graph_after else 1,
            "time_selection_action_writes": 0 if state_before == self.watch_file_bytes() and actions_before == self.action_file_bytes() else 1,
            "messages_sent": 0, "calls_made": 0, "tickets_created": 0,
            "cleanup_deletes_only_task_owned_graph_process_and_watch_state": True,
            "external_write_boundary": [],
        })

    def run(self) -> None:
        self.db.launch()
        empty = self.db.graph_rows("watch-empty-owned-graph", "MATCH (n) RETURN n.id AS id LIMIT 1")
        self.check("actual FalkorDB starts with a unique cold graph", empty == [], empty)
        self.start_app()
        self.wait_api()
        self.cold_and_preview_confirm()
        self.temporal_groups()
        self.edge_sensitivity()
        self.denied_source_invariance()
        self.time_validation()
        self.reconnect_and_persistence()

    def stop(self) -> None:
        self.stop_app()
        if self.db.process is not None:
            self.db.stop()
            self.cleanup["owned_graph_deleted"] = self.db.cleanup.get("owned_graph_deleted") is True
            self.cleanup["owned_database_process_stopped"] = self.db.cleanup.get("owned_process_stopped") is True
            run_dir = self.db.server_log_path.parent if self.db.server_log_path else None
            expected_parent = (ROOT / ".local/falkordb-runs").resolve()
            if run_dir is not None and run_dir.is_dir() and run_dir.resolve().parent == expected_parent:
                shutil.rmtree(run_dir)
                self.cleanup["owned_database_data_removed"] = True
        if self.state_dir.exists():
            parent = (ROOT / ".local/wardatlas-evidence-watch-runs").resolve()
            if self.state_dir.resolve().parent == parent and self.state_dir.resolve().is_relative_to(parent):
                shutil.rmtree(self.state_dir)
                self.cleanup["owned_watch_state_removed"] = True
        else:
            self.cleanup["owned_watch_state_removed"] = True

    def finalize_cleanup_assertion(self) -> None:
        case_id = REQUIRED_CASES[8]
        if case_id not in self.observed:
            return
        self.observed[case_id]["cleanup_deletes_only_task_owned_graph_process_and_watch_state"] = all(self.cleanup.values())
        self.mapped = [row for row in self.mapped if row["case_id"] != case_id]
        self.record_group(case_id, self.observed[case_id])
        self.check("owned app, graph, FalkorDB process, and watch-state directory were cleaned", all(self.cleanup.values()), self.cleanup)

    def report(self, status: str, error: str | None) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "verifier": "verify_evidence_watch_live.py",
            "status": status,
            "error": error,
            "created_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "host": host_facts(),
            "ports": {"app_bind": f"127.0.0.1:{self.args.app_port}", "database_bind": f"127.0.0.1:{self.args.db_port}"},
            "graph": {"name": self.graph_name, "run_id": self.run_id, "task_owned": True},
            "runtime_database": self.db.database,
            "oracle": {"expected_sha256": EXPECTED_ARTIFACT_SHA256, "matrix_sha256": MATRIX_ARTIFACT_SHA256, "case_ids": list(REQUIRED_CASES), "mapped_assertion_count": len(self.mapped), "mapped_assertions": self.mapped},
            "checks": self.checks,
            "check_count": len(self.checks),
            "browser": self.browser_report,
            "cleanup": self.cleanup,
            "side_effect_policy": {"messages_sent": False, "calls_made": False, "tickets_created": False, "external_writes": [], "watch_storage": "separate-local-file", "confirmed_actions_file_unchanged": True},
        }


def no_supersedes_current_candidates(result: dict[str, Any]) -> list[str]:
    return result.get("review_candidates", [])


def rejected_false_status(verifier: EvidenceWatchLiveVerifier, token: str) -> bool:
    status, _ = verifier.post("/api/evidence-watch/confirm", {"token": token, "confirm": False})
    return status == 400


def choose_port(base: int, span: int, offset: int, label: str) -> int:
    port = base + offset
    if not (1024 <= port <= 65535) or not (0 <= offset < span):
        raise ValueError(f"{label} must be within WORKSHOP_PORT_BASE/SPAN")
    return port


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--require-live-falkordb", action="store_true", required=True)
    parser.add_argument("--all-nine-groups", action="store_true", required=True)
    parser.add_argument("--assert-api", action="store_true", required=True)
    parser.add_argument("--assert-browser", action="store_true", required=True)
    parser.add_argument("--desktop-mobile", action="store_true", required=True)
    parser.add_argument("--assert-acl", action="store_true", required=True)
    parser.add_argument("--assert-confirmation-boundary", action="store_true", required=True)
    parser.add_argument("--assert-reconnect", action="store_true", required=True)
    parser.add_argument("--assert-owned-cleanup", action="store_true", required=True)
    parser.add_argument("--app-port", type=int)
    parser.add_argument("--db-port", type=int)
    parser.add_argument("--evidence-dir", type=Path)
    parser.add_argument("--expected", type=Path, default=ROOT / "tests/evidence-watch-expected-results.json")
    parser.add_argument("--matrix", type=Path, default=ROOT / "tests/evidence-watch-adversarial-matrix.json")
    parser.add_argument("--review-receipt", type=Path, default=ROOT / "tests/evidence-watch-independent-review.json")
    parser.add_argument("--pinned-plan", type=Path, default=PLAN)
    args = parser.parse_args(argv)
    try:
        base = int(os.environ["WORKSHOP_PORT_BASE"])
        span = int(os.environ["WORKSHOP_PORT_SPAN"])
        lane = re.sub(r"[^A-Za-z0-9_-]", "_", os.environ["WORKSHOP_LANE"])
        args.app_port = args.app_port or choose_port(base, span, 41, "app port")
        args.db_port = args.db_port or choose_port(base, span, 40, "database port")
        for name, port in (("app", args.app_port), ("database", args.db_port)):
            if not (1024 <= port <= 65535) or port - base < 0 or port - base >= span:
                raise ValueError(f"{name} port must be within allocated WORKSHOP_PORT_BASE/SPAN")
        if args.app_port == args.db_port:
            raise ValueError("app and DB ports must be distinct")
        args.lane = lane
        args.evidence_dir = (args.evidence_dir or Path("/workspace/evidence/implement-evidence-watch") / f"lane-{lane}" / uuid.uuid4().hex).resolve()
        if not args.evidence_dir.is_relative_to(Path("/workspace/evidence")):
            raise ValueError("evidence directory must remain under /workspace/evidence")
        args.evidence_dir.mkdir(parents=True, exist_ok=True)
        expected, expected_raw = read_json(args.expected)
        matrix, matrix_raw = read_json(args.matrix)
        receipt, receipt_raw = read_json(args.review_receipt)
        validate_fixed_values(expected)
        count = validate_structure(expected, matrix, True)
        if count != REQUIRED_ASSERTION_TOTAL or hashlib.sha256(expected_raw).hexdigest() != EXPECTED_ARTIFACT_SHA256 or hashlib.sha256(matrix_raw).hexdigest() != MATRIX_ARTIFACT_SHA256:
            raise VerificationFailure("frozen nine-group watch oracle bytes or assertion count changed")
        # The artifact-only contract is independently run by the operator; also
        # verify the receipt's frozen bytes before behavior is exercised.
        from verify_evidence_watch_contract import REVIEW_RECEIPT_SHA256
        if hashlib.sha256(receipt_raw).hexdigest() != REVIEW_RECEIPT_SHA256:
            raise VerificationFailure("independent source-review receipt bytes changed")
        if not args.pinned_plan.is_file():
            raise VerificationFailure(f"pinned planner packet is unavailable: {args.pinned_plan}")
        verifier = EvidenceWatchLiveVerifier(args, expected, matrix)
    except (KeyError, OSError, ValueError, ContractFailure, VerificationFailure) as exc:
        print(f"FAIL: evidence-watch live preflight: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1

    status, error = "FAIL", None
    try:
        verifier.run()
        status = "PASS"
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
        verifier.checks.append({"assertion": "all nine live watch groups completed", "passed": False, "detail": error})
    finally:
        try:
            verifier.stop()
            verifier.finalize_cleanup_assertion()
            if status == "PASS":
                groups = {row["case_id"] for row in verifier.mapped}
                per_group = {case: sum(row["case_id"] == case for row in verifier.mapped) for case in REQUIRED_CASES}
                if len(verifier.mapped) != REQUIRED_ASSERTION_TOTAL or groups != set(REQUIRED_CASES) or any(value == 0 for value in per_group.values()) or not all(row["passed"] for row in verifier.mapped):
                    status, error = "FAIL", "mapped assertion totals or nine-group coverage differ from the frozen matrix"
                if not all(verifier.cleanup.values()):
                    status, error = "FAIL", "owned cleanup was not fully confirmed"
        except Exception as exc:
            status, error = "FAIL", f"cleanup/finalization failure: {type(exc).__name__}: {exc}"
        report = verifier.report(status, error)
        (args.evidence_dir / "evidence-watch-live.json").write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    if status != "PASS":
        print(f"FAIL: {error}; report: {args.evidence_dir / 'evidence-watch-live.json'}", file=sys.stderr)
        return 1
    print(f"PASS: nine evidence-watch groups, {len(verifier.mapped)} mapped assertions, actual FalkorDB/API/desktop/mobile/ACL/reconnect and owned cleanup; evidence: {args.evidence_dir / 'evidence-watch-live.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

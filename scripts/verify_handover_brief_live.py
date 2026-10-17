#!/usr/bin/env python3
"""Exercise the pinned handover witness through real FalkorDB, API and browser.

The graph, loopback app, state directory and browser evidence are run-owned.
Expected values come only from the frozen handover artifact and its validators.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from verify_falkordb_live import (
    LiveVerifier,
    RespConnection,
    VerificationFailure,
    explicit_bind_preflight,
    query_result,
    result_rows,
)
from wardatlas_app import Handler, WardAtlasHTTPServer, WardAtlasService
from wardatlas_fixtures import seed_queries
from wardatlas_handover import source_ids_from_snapshot
from wardatlas_shift_board import seed_shift_board_queries


ROOT = Path(__file__).resolve().parents[1]
EXPECTED_PATH = ROOT / "tests/handover-brief-expected-results.json"
MATRIX_PATH = ROOT / "tests/handover-brief-adversarial-matrix.json"
APPLICABILITY_EDGE_ID = "REL-SOP-V2-APPLIES-C4"
CONFLICT_EDGE_ID = "REL-SOP-9-CONFLICT"


def api_request(base_url: str, path: str, method: str = "GET", payload: dict | None = None) -> tuple[int, dict[str, Any]]:
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        base_url + path,
        data=body,
        method=method,
        headers={"Content-Type": "application/json", "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read())


def canonical_source_ids(snapshot: dict[str, Any]) -> list[str]:
    """Calculate expected source IDs from the independent canonical packet."""
    return source_ids_from_snapshot(snapshot)


def resolve_expected_reference(document: dict[str, Any], reference: Any) -> Any:
    """Resolve JSON Pointer references from the candidate-independent oracle."""
    if not isinstance(reference, str) or not reference.startswith("#/"):
        return reference
    value: Any = document
    for raw_part in reference[2:].split("/"):
        part = raw_part.replace("~1", "/").replace("~0", "~")
        value = value[int(part)] if isinstance(value, list) else value[part]
    return value


class HandoverLiveVerifier:
    def __init__(self, args: argparse.Namespace, expected: dict[str, Any], matrix: dict[str, Any]):
        self.args = args
        self.expected = expected
        self.matrix = matrix
        self.expected_cases = {row["id"]: row["expected"] for row in expected["cases"]}
        self.canonical = expected["canonical_snapshot"]
        self.lane = os.environ.get("WORKSHOP_LANE", "")
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,32}", self.lane):
            raise VerificationFailure("WORKSHOP_LANE must be a nonempty safe lane identifier")
        self.live = LiveVerifier(args.db_port, expected, matrix)
        self.live.graph = f"wardatlas_test_{self.lane}_{self.live.run_id}"
        self.state_dir = ROOT / ".local" / "handover-brief-runs" / self.lane / self.live.run_id
        self.state_dir_owned = False
        self.owned_state_removed = False
        self.base_url = f"http://127.0.0.1:{args.app_port}"
        self.app_service: WardAtlasService | None = None
        self.httpd: WardAtlasHTTPServer | None = None
        self.http_thread: threading.Thread | None = None
        self.app_stopped = False
        self.browser_report = args.evidence_dir / "handover-browser.json"
        self.screenshot_dir = args.evidence_dir / "screenshots"
        self.cold_graph_rows: list[dict[str, Any]] = []
        self.status = "running"
        self.error: str | None = None
        self.case_data: dict[str, Any] = {}

    def request(self, path: str, method: str = "GET", payload: dict | None = None) -> tuple[int, dict[str, Any]]:
        return api_request(self.base_url, path, method, payload)

    def get(self, path: str) -> tuple[int, dict[str, Any]]:
        return self.request(path)

    def post(self, path: str, body: dict | None = None) -> tuple[int, dict[str, Any]]:
        return self.request(path, "POST", body or {})

    def check(self, case: dict[str, Any], name: str, condition: bool, detail: Any = None) -> None:
        self.live.check(case, name, bool(condition), detail)

    def scenario(self, identifier: str, callback) -> None:
        self.live.scenario(identifier, callback)

    def query_rows(self, label: str, query: str) -> list[dict[str, Any]]:
        return self.live.graph_rows(label, query)

    def counts(self, label: str) -> dict[str, int]:
        nodes = self.query_rows(f"{label}-nodes", "MATCH (n) RETURN count(n) AS count")
        edges = self.query_rows(f"{label}-relationships", "MATCH ()-[r]->() RETURN count(r) AS count")
        return {"nodes": int(nodes[0]["count"]), "relationships": int(edges[0]["count"])}

    def draft(self, as_of: str = "2026-10-14T18:30:00Z") -> tuple[int, dict[str, Any]]:
        return self.post("/api/handover/draft", {"incident_id": "INC-204", "as_of": as_of})

    def seed_owned_edge(self, edge_id: str) -> None:
        selected = [query for query in [*seed_queries(), *seed_shift_board_queries()] if edge_id in query]
        if len(selected) != 1:
            raise VerificationFailure(f"expected exactly one deterministic fixture query for owned relation {edge_id}; found {len(selected)}")
        self.live.graph_query(f"reseed-owned-{edge_id}", selected[0])

    def start(self) -> None:
        explicit_bind_preflight(self.args.app_port)
        self.live.launch()
        # A read-only query gives this run a known cold graph and makes cleanup
        # ownership explicit before the app has an opportunity to touch it.
        cold = self.live.graph_rows("handover-cold-owned-graph", "MATCH (n) RETURN n.id AS id LIMIT 1")
        self.cold_graph_rows = cold
        self.live.graph_created = True

        self.state_dir.parent.mkdir(parents=True, exist_ok=True)
        try:
            # Atomic exclusive creation establishes ownership. A collision is
            # never reused, and the exception path must not remove its content.
            self.state_dir.mkdir()
            self.state_dir_owned = True
        except FileExistsError as exc:
            raise VerificationFailure(f"unique local action state already exists: {self.state_dir}") from exc
        self.app_service = WardAtlasService(self.args.db_port, self.live.graph, self.state_dir)
        self.httpd = WardAtlasHTTPServer(("127.0.0.1", self.args.app_port), Handler, self.app_service)
        self.http_thread = threading.Thread(target=self.httpd.serve_forever, kwargs={"poll_interval": 0.1}, daemon=True)
        self.http_thread.start()
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            try:
                status, _ = self.get("/api/health")
                if status == 200:
                    return
            except Exception:
                time.sleep(0.05)
        raise VerificationFailure("owned loopback WardAtlas API did not become ready")

    def browser_confirmation(self, case: dict[str, Any], expected: dict[str, Any]) -> None:
        before = self.counts("browser-before")
        self.args.evidence_dir.mkdir(parents=True, exist_ok=True)
        command = [
            "node", str(ROOT / "scripts/handover_brief_browser_assertions.cjs"), self.base_url,
            str(self.browser_report), str(self.screenshot_dir),
        ]
        completed = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, timeout=120)
        self.check(case, "desktop/mobile browser assertions pass with nonzero coverage", completed.returncode == 0 and self.browser_report.is_file(), {"stdout": completed.stdout[-3000:], "stderr": completed.stderr[-3000:]})
        if completed.returncode != 0:
            raise VerificationFailure("handover desktop/mobile browser assertions failed")
        browser = json.loads(self.browser_report.read_text(encoding="utf-8"))
        after = self.counts("browser-after")
        actions_status, actions = self.get("/api/actions")
        self.check(case, "browser preview and explicit confirm create exactly one local saved action with the same snapshot", browser["status"] == "PASS" and browser["assertion_count"] > 0 and actions_status == 200 and actions["count"] == expected["local_synthetic_actions_after_one_confirmation"] == 1 and actions["actions"][0]["snapshot"] == self.canonical, {"browser": browser, "actions": actions})
        self.check(case, "browser workflow performs no graph mutation and no external write", before == after and actions["storage"] == "local-only" and actions["actions"][0]["storage"] == "local-only", {"before": before, "after": after, "actions": actions})
        action_file = self.state_dir / "confirmed-actions.json"
        file_value = json.loads(action_file.read_text(encoding="utf-8"))
        self.check(case, "one human-confirmed snapshot is persisted unchanged as one synthetic local action", len(file_value["items"]) == 1 and file_value["items"][0]["snapshot"] == self.canonical and file_value["items"][0]["source_ids"] == canonical_source_ids(self.canonical), file_value)
        # A later change to the live witness must not rewrite the reviewed record.
        self.live.graph_query("post-confirm-remove-owned-conflict", f"MATCH (:SOPVersion {{id:'SOP-OPS-9-V2'}})-[r:SOP_CONFLICT {{id:'{CONFLICT_EDGE_ID}'}}]->(:SOPVersion {{id:'SOP-OPS-9-V3'}}) DELETE r")
        _, after_graph_change = self.get("/api/actions")
        self.check(case, "later graph edge removal does not rewrite the saved reviewed snapshot", after_graph_change["actions"][0]["snapshot"] == self.canonical, after_graph_change["actions"][0]["snapshot"])
        self.seed_owned_edge(CONFLICT_EDGE_ID)
        _, restored = self.draft()
        self.check(case, "fixture reseed restores the exact witness after graph-change immutability check", restored["snapshot"] == self.canonical, restored.get("snapshot"))
        self.case_data[case["id"]] = {"browser": browser, "action": actions["actions"][0]}

    def run(self) -> None:
        self.start()
        cases = self.expected_cases
        cold_case = {"id": "cold-empty-unknown", "assertions": 0}

        def cold_empty(case: dict[str, Any]) -> None:
            expected = cases[case["id"]]
            status, impact = self.get("/api/sop-impact?scope=operations&as_of=2026-10-14T18%3A30%3A00Z")
            draft_status, draft = self.draft()
            actions_status, actions = self.get("/api/actions")
            count_before = self.counts("cold-before-draft")
            self.check(case, "unique task-owned graph starts cold and empty", self.cold_graph_rows == [], self.cold_graph_rows)
            self.check(case, "cold SOP API is empty/unknown with no path, claim or source", status == 200 and impact["board_state"] == expected["board_state"] and impact["impact_paths"] == expected["impact_paths"] and impact["role_claims"] == [] and impact["source_rows"] == [], impact)
            self.check(case, "cold preview does not create a draft or witness", draft_status == 409 and "confirmation_token" not in draft and "snapshot" not in draft, {"status": draft_status, "body": draft})
            self.check(case, "cold graph exposes no saved actions or risk-absence conclusion", actions_status == 200 and actions["actions"] == expected["saved_actions"] and actions["count"] == 0 and impact.get("sop_safe_conclusion_asserted") is False and impact.get("no_operational_risk_conclusion_asserted") is False, {"actions": actions, "conclusions": [impact.get("sop_safe_conclusion_asserted"), impact.get("no_operational_risk_conclusion_asserted")]})
            self.check(case, "preview on cold graph has no graph mutation", count_before == {"nodes": 0, "relationships": 0}, count_before)
            self.check(case, "cold graph returns no explicit SOP witness edge or conclusion", impact["complete_conflict_witness_returned"] is False and impact["applicability_edges"] == [] and impact["conflict_edge"] is None and impact["conflict_conclusion"] is None, {key: impact[key] for key in ("complete_conflict_witness_returned", "applicability_edges", "conflict_edge", "conflict_conclusion")})
            self.check(case, "cold graph has exactly zero visible source rows and no incident-linked draft token", impact["source_rows"] == [] and impact["role_claims"] == [] and "confirmation_token" not in draft and "snapshot" not in draft, {"sources": impact["source_rows"], "claims": impact["role_claims"], "draft_keys": sorted(draft)})
            self.check(case, "cold empty/unknown does not assert conflict absence or operational safety", impact["conflict_status"] == "unknown" and impact["conflict_linkage_status"] == "incomplete/unknown" and impact["sop_safe_conclusion_asserted"] is False and impact["no_operational_risk_conclusion_asserted"] is False, {key: impact[key] for key in ("conflict_status", "conflict_linkage_status", "sop_safe_conclusion_asserted", "no_operational_risk_conclusion_asserted")})
            self.case_data[case["id"]] = {"impact": impact, "draft": draft}

        self.scenario("cold-empty-unknown", cold_empty)

        def observed_paths(case: dict[str, Any]) -> None:
            expected = cases[case["id"]]
            reset_status, reset = self.post("/api/scenario/reset")
            status, impact = self.get("/api/sop-impact?scope=operations&as_of=2026-10-14T18%3A30%3A00Z")
            self.check(case, "owned deterministic reseed succeeds on real graph", reset_status == 200 and reset.get("status") == "reset", reset)
            self.check(case, "actual FalkorDB returns exactly the two pinned paths", status == 200 and impact["impact_paths"] == expected["impact_paths"], impact.get("impact_paths"))
            self.check(case, "all four provenance sources, titles and timestamps per path match the pin", impact["path_evidence"] == expected["path_evidence"], impact["path_evidence"])
            self.check(case, "requested instant is normalized and ownership source is retained", impact["as_of"] == expected["as_of"] and impact["current_ownership_source_ids"] == expected["current_ownership_source_ids"], {"as_of": impact["as_of"], "ownership": impact["current_ownership_source_ids"]})
            self.check(case, "viewer scope is operations and result is from live graph", impact["scope"] == "operations" and impact["board_state"] == "ready", {"scope": impact["scope"], "state": impact["board_state"]})
            self.check(case, "both path endpoints are INC-204 and the affected C4 conveyor", all(path[0] == "INC-204" and path[1] == "CONVEYOR-C4" for path in impact["impact_paths"]) and len(impact["impact_paths"]) == expected["row_count"], impact["impact_paths"])
            self.case_data[case["id"]] = impact

        self.scenario("inc204-observed-paths-and-provenance", observed_paths)

        def typed_witness(case: dict[str, Any]) -> None:
            expected = cases[case["id"]]["sop_witness"]
            status, impact = self.get("/api/sop-impact?scope=operations&as_of=2026-10-14T18%3A30%3A00Z")
            draft_status, draft = self.draft()
            snapshot = draft.get("snapshot")
            actual_witness = ({
                "shared_equipment_id": impact["applicability_edges"][0]["to"] if impact["applicability_edges"] else None,
                "linkage_status": "complete" if impact["complete_conflict_witness_returned"] else impact["conflict_linkage_status"],
                "conflict_status": impact["conflict_status"],
                "applicability_edges": impact["applicability_edges"],
                "conflict_edge": impact["conflict_edge"],
                "claim_evidence": impact["role_claims"],
                "returned_claim_count": impact.get("returned_claim_count", 0),
                "claims_are_unverified": impact["claims_are_unverified"],
                "claims_are_current_owner_facts": impact["claims_are_current_owner_facts"],
                "winner_selected": impact["winner_selected"],
                "contact_supplied": False,
                "stale_version_excluded_from_c4_witness": None,
                "procedure_or_clinical_conclusion_supplied": False,
                "direct_incident_to_sop_causal_edge_asserted": False,
            })
            actual_witness["stale_version_excluded_from_c4_witness"] = {
                "version_id": impact["stale_version"]["id"],
                "source_id": impact["stale_version"]["source_id"],
                "source_title": impact["stale_version"]["source_title"],
                "observed_at": impact["stale_version"]["observed_at"],
                "referenced_equipment_id": impact["stale_version"]["referenced_equipment_id"],
                "is_applicable_to_c4": impact["stale_version"]["stale_is_applicable_to_c4"],
                "is_joined_to_inc204_conflict_witness": impact["stale_version"]["stale_is_joined_to_inc204_conflict_witness"],
            }
            self.check(case, "exact typed unresolved V2/V3 graph witness and source-backed claims", status == 200 and actual_witness == expected, actual_witness)
            self.check(case, "draft snapshot is the entire fixed timestamped graph witness", draft_status == 201 and snapshot == self.canonical, snapshot)
            proposal = [item for item in draft.get("proposed_actions", []) if "SOP-OPS-9" in item.get("text", "")]
            self.check(case, "complete witness permits only a source-linked non-authoritative SOP review proposal", len(proposal) == 1 and proposal[0]["source_ids"] == ["SRC-SOP-CURRENT-V2", "SRC-SOP-CURRENT-V3"] and "authoritative" in proposal[0]["text"].lower(), proposal)
            self.check(case, "archived V1 remains excluded from the C4 conflict witness", impact["stale_version"]["id"] == expected["stale_version_excluded_from_c4_witness"]["version_id"] and impact["stale_version"]["stale_is_applicable_to_c4"] is False and impact["stale_version"]["stale_is_joined_to_inc204_conflict_witness"] is False, impact["stale_version"])
            self.check(case, "both explicit APPLIES_TO edges have the pinned direction and C4 endpoint", impact["applicability_edges"] == expected["applicability_edges"], impact["applicability_edges"])
            self.check(case, "one explicit V2-to-V3 SOP_CONFLICT edge remains unresolved", impact["conflict_edge"] == expected["conflict_edge"] and impact["conflict_status"] == "unresolved", impact["conflict_edge"])
            self.check(case, "exactly the two pinned source-backed role claims are returned", len(impact["role_claims"]) == expected["returned_claim_count"] == 2 and [row["role_id"] for row in impact["role_claims"]] == [row["role_id"] for row in expected["claim_evidence"]], impact["role_claims"])
            self.check(case, "each claim is explicitly labeled unverified and evidence-linked", impact["claims_are_unverified"] is True and all(row["claim_label"] == "unverified SOP claim" and row["claim_relationship"] == "CLAIMS_ROLE" and row["source_evidence_linked"] is True for row in impact["role_claims"]), impact["role_claims"])
            self.check(case, "V2 claim source ID, title and observed timestamp match the pin", impact["role_claims"][0] == expected["claim_evidence"][0], impact["role_claims"][0])
            self.check(case, "V3 claim source ID, title and observed timestamp match the pin", impact["role_claims"][1] == expected["claim_evidence"][1], impact["role_claims"][1])
            self.check(case, "SOP claims are not current-owner facts and no winner is selected", impact["claims_are_current_owner_facts"] is expected["claims_are_current_owner_facts"] and impact["winner_selected"] is expected["winner_selected"] and snapshot["sop_witness"]["claims_are_current_owner_facts"] is False and snapshot["sop_witness"]["winner_selected"] is False, snapshot["sop_witness"])
            self.check(case, "snapshot supplies no contact, clinical conclusion or procedure", snapshot["sop_witness"]["contact_supplied"] is False and snapshot["sop_witness"]["procedure_or_clinical_conclusion_supplied"] is False, snapshot["sop_witness"])
            self.check(case, "archived V1 source ID/title/time and C3 reference match the stale pin", snapshot["sop_witness"]["stale_version_excluded_from_c4_witness"] == expected["stale_version_excluded_from_c4_witness"], snapshot["sop_witness"]["stale_version_excluded_from_c4_witness"])
            self.check(case, "stale V1 is absent from the C4 conflict version set", impact["stale_version"]["id"] not in impact["c4_conflict_version_ids"] and impact["stale_version"]["stale_is_joined_to_inc204_conflict_witness"] is False, {"stale": impact["stale_version"], "c4_versions": impact["c4_conflict_version_ids"]})
            self.check(case, "no direct incident-to-SOP causal edge is asserted", snapshot["sop_witness"]["direct_incident_to_sop_causal_edge_asserted"] is False and not any(edge.get("from") == "INC-204" and edge.get("to", "").startswith("SOP-") for edge in snapshot["sop_witness"]["applicability_edges"]), snapshot["sop_witness"])
            self.case_data[case["id"]] = {"impact": impact, "draft": draft}

        self.scenario("typed-current-sop-witness-and-stale-exclusion", typed_witness)

        def edge_counterfactual(case: dict[str, Any]) -> None:
            expected = cases[case["id"]]
            baseline = self.canonical
            for index, edge in enumerate(expected["edge_removals"]):
                relation = edge["removed_edge"]["relationship"]
                edge_id = APPLICABILITY_EDGE_ID if relation == "APPLIES_TO" else CONFLICT_EDGE_ID
                if index:
                    # Restore the first relation before independently removing
                    # the conflict edge; each counterfactual starts canonical.
                    self.seed_owned_edge(APPLICABILITY_EDGE_ID)
                before_count = self.query_rows(f"edge-{index}-owned-before", f"MATCH ()-[r:{relation} {{id:'{edge_id}'}}]->() RETURN count(r) AS count")
                before_graph = self.counts(f"edge-{index}-graph-before")
                if relation == "APPLIES_TO":
                    delete = f"MATCH (:SOPVersion {{id:'SOP-OPS-9-V2'}})-[r:APPLIES_TO {{id:'{edge_id}'}}]->(:Equipment {{id:'CONVEYOR-C4'}}) DELETE r"
                else:
                    delete = f"MATCH (:SOPVersion {{id:'SOP-OPS-9-V2'}})-[r:SOP_CONFLICT {{id:'{edge_id}'}}]->(:SOPVersion {{id:'SOP-OPS-9-V3'}}) DELETE r"
                self.live.graph_query(f"remove-owned-{edge_id}", delete)
                after_count = self.query_rows(f"edge-{index}-owned-after", f"MATCH ()-[r:{relation} {{id:'{edge_id}'}}]->() RETURN count(r) AS count")
                after_graph = self.counts(f"edge-{index}-graph-after")
                status, draft = self.draft()
                expected_link = edge["linkage_status"]
                expected_claims = baseline["sop_witness"]["claim_evidence"] if relation == "SOP_CONFLICT" else [row for row in baseline["sop_witness"]["claim_evidence"] if row["sop_version_id"] == "SOP-OPS-9-V3"]
                expected_sources = sorted({source for row in baseline["impact"]["path_evidence"] for source in row["source_ids"]} | {row["source_id"] for row in expected_claims} | {baseline["sop_witness"]["stale_version_excluded_from_c4_witness"]["source_id"]})
                self.check(case, f"counterfactual {relation} removal changes only its test-owned relationship", int(before_count[0]["count"]) == 1 and int(after_count[0]["count"]) == 0, {"before": before_count, "after": after_count, "edge": edge_id})
                self.check(case, f"counterfactual {relation} removal deletes exactly one owned edge and no node", before_graph["nodes"] == after_graph["nodes"] and before_graph["relationships"] - after_graph["relationships"] == 1, {"before": before_graph, "after": after_graph})
                if relation == "APPLIES_TO":
                    remaining = self.query_rows("counterfactual-v3-applicability-remains", "MATCH (:SOPVersion {id:'SOP-OPS-9-V3'})-[r:APPLIES_TO {id:'REL-SOP-V3-APPLIES-C4'}]->(:Equipment {id:'CONVEYOR-C4'}) RETURN count(r) AS count")
                else:
                    remaining = self.query_rows("counterfactual-applicability-edges-remain", "MATCH (:SOPVersion)-[r:APPLIES_TO]->(:Equipment {id:'CONVEYOR-C4'}) RETURN count(r) AS count")
                self.check(case, f"counterfactual {relation} removal leaves all other required witness links intact", int(remaining[0]["count"]) == (1 if relation == "APPLIES_TO" else 2), remaining)
                self.check(case, f"counterfactual {relation} yields pinned incomplete/unknown status, never resolved", status == 201 and not draft["snapshot"]["sop_witness"].get("conflict_edge") and draft["snapshot"]["sop_witness"]["linkage_status"] == expected_link and draft["snapshot"]["sop_witness"]["conflict_status"] == "unknown", draft["snapshot"].get("sop_witness"))
                self.check(case, f"counterfactual {relation} preserves exact pinned outage paths and all path provenance", draft["snapshot"]["impact"] == baseline["impact"] == {"status": "observed-impact", "paths": expected["baseline_impact_paths"], "path_evidence": self.expected_cases["inc204-observed-paths-and-provenance"]["path_evidence"]}, draft["snapshot"]["impact"])
                self.check(case, f"counterfactual {relation} preserves both ordered path arrays", draft["snapshot"]["impact"]["paths"] == expected["baseline_impact_paths"], draft["snapshot"]["impact"]["paths"])
                self.check(case, f"counterfactual {relation} preserves every path source ID/title/time row", draft["snapshot"]["impact"]["path_evidence"] == self.expected_cases["inc204-observed-paths-and-provenance"]["path_evidence"], draft["snapshot"]["impact"]["path_evidence"])
                self.check(case, f"counterfactual {relation} suppresses incident-linked SOP review proposal", not any("SOP-OPS-9" in action.get("text", "") for action in draft["proposed_actions"]), draft["proposed_actions"])
                self.check(case, f"counterfactual {relation} retains only source claims supported by surviving graph edges", draft["snapshot"]["sop_witness"]["claim_evidence"] == expected_claims and all(claim["source_evidence_linked"] for claim in expected_claims), draft["snapshot"]["sop_witness"]["claim_evidence"])
                self.check(case, f"counterfactual {relation} retains exactly the independently pinned visible source IDs", draft["source_ids"] == expected_sources, draft["source_ids"])
                self.check(case, f"counterfactual {relation} keeps archived V1 history outside the current C4 witness", draft["snapshot"]["sop_witness"]["stale_version_excluded_from_c4_witness"] == baseline["sop_witness"]["stale_version_excluded_from_c4_witness"], draft["snapshot"]["sop_witness"]["stale_version_excluded_from_c4_witness"])
                if edge["all_non_witness_snapshot_fields_unchanged"]:
                    self.check(case, f"counterfactual {relation} changes no non-witness snapshot fields", draft["snapshot"]["as_of"] == baseline["as_of"] and draft["snapshot"]["impact"] == baseline["impact"] and draft["snapshot"]["sop_witness"]["shared_equipment_id"] == baseline["sop_witness"]["shared_equipment_id"] and draft["snapshot"]["sop_witness"]["stale_version_excluded_from_c4_witness"] == baseline["sop_witness"]["stale_version_excluded_from_c4_witness"], draft["snapshot"])
                self.seed_owned_edge(edge_id)
                _, restored = self.draft()
                self.check(case, f"counterfactual {relation} reseed restores exact canonical witness", restored["snapshot"] == self.canonical, restored.get("snapshot"))

        self.scenario("owned-edge-removal-is-unknown-only", edge_counterfactual)

        def restoration(case: dict[str, Any]) -> None:
            expected = cases[case["id"]]
            status, impact = self.get("/api/sop-impact?scope=operations&as_of=2026-10-14T19%3A30%3A00Z")
            draft_status, draft = self.draft("2026-10-14T19:30:00Z")
            self.check(case, "restoration query normalizes exact 19:30 UTC time", status == 200 and impact["as_of"] == expected["as_of"] and draft["snapshot"]["as_of"] == expected["as_of"], {"impact": impact["as_of"], "snapshot": draft["snapshot"]["as_of"]})
            self.check(case, "observed restoration removes active paths and path evidence only", impact["impact_paths"] == expected["active_impact_paths"] and draft["snapshot"]["impact"]["status"] == "restored" and draft["snapshot"]["impact"]["paths"] == [], draft["snapshot"]["impact"])
            self.check(case, "source-backed unresolved SOP witness and both claims survive restoration", impact["conflict_status"] == expected["sop_conflict_status"] and impact["role_claims"] == expected["sop_claims"] and draft["snapshot"]["sop_witness"] == self.canonical["sop_witness"], draft["snapshot"]["sop_witness"])
            self.check(case, "restoration asserts neither SOP safety nor absence of operational risk", impact["sop_safe_conclusion_asserted"] is expected["sop_safe_conclusion_asserted"] and impact["no_operational_risk_conclusion_asserted"] is expected["no_operational_risk_conclusion_asserted"], {"safe": impact["sop_safe_conclusion_asserted"], "no_risk": impact["no_operational_risk_conclusion_asserted"]})
            self.check(case, "restored snapshot has no active path evidence rows", impact["impact_paths"] == [] and draft["snapshot"]["impact"]["paths"] == [] and draft["snapshot"]["impact"]["path_evidence"] == [], {"api_paths": impact["impact_paths"], "snapshot": draft["snapshot"]["impact"]})
            self.check(case, "both explicit applicability edges and unresolved conflict edge remain exact after restoration", draft["snapshot"]["sop_witness"]["applicability_edges"] == self.canonical["sop_witness"]["applicability_edges"] and draft["snapshot"]["sop_witness"]["conflict_edge"] == self.canonical["sop_witness"]["conflict_edge"], draft["snapshot"]["sop_witness"])
            self.check(case, "both restoration-time SOP claim IDs, titles and observation times stay source-backed", impact["role_claims"] == expected["sop_claims"] and all(row["source_evidence_linked"] is True and row["claim_label"] == "unverified SOP claim" for row in impact["role_claims"]), impact["role_claims"])
            self.check(case, "restoration handover makes no clinical, contact, external-write or performed-work claim", draft["action_boundary"]["clinical_action_proposed"] is False and draft["action_boundary"]["work_claimed_performed"] is False and draft["action_boundary"]["messages_sent"] is False and draft["action_boundary"]["calls_made"] is False and draft["action_boundary"]["external_writes"] == [], draft["action_boundary"])

        self.scenario("restoration-keeps-sop-claims-unresolved", restoration)

        def denied_invariance(case: dict[str, Any]) -> None:
            expected = cases[case["id"]]
            before_impact_status, before_impact = self.get("/api/sop-impact?scope=operations&as_of=2026-10-14T18%3A30%3A00Z")
            _, before = self.draft()
            before_semantic = {key: before[key] for key in ("incident_id", "summary", "proposed_actions", "source_ids", "snapshot")}
            probe = expected["restricted_probe"]
            self.inject_restricted_probe(probe)
            after_impact_status, after_impact = self.get("/api/sop-impact?scope=operations&as_of=2026-10-14T18%3A30%3A00Z")
            _, after = self.draft()
            after_semantic = {key: after[key] for key in ("incident_id", "summary", "proposed_actions", "source_ids", "snapshot")}
            serialized = json.dumps(after, sort_keys=True)
            hidden = [probe[key] for key in ("version_id", "claimed_role_id", "source_id", "source_title", "source_text", "department_id", "relationship_id")]
            self.check(case, "operations draft and graph snapshot are semantically identical before/after denied source", before_semantic == after_semantic == {"incident_id": "INC-204", "summary": before["summary"], "proposed_actions": before["proposed_actions"], "source_ids": canonical_source_ids(self.canonical), "snapshot": self.canonical}, {"before": before_semantic, "after": after_semantic})
            self.check(case, "pinned restricted source, role, title, text and relationship literals never leak", not any(value in serialized for value in hidden), {value: value in serialized for value in hidden})
            self.check(case, "no hidden count, existence, path, or dependent paraphrase leaks", not any(key in after for key in ("restricted_count", "hidden_count", "denied_count", "restricted_exists", "hidden_exists")) and all(value not in serialized for value in (probe["department_id"], probe["claimed_role_id"], "role probe 91", "restricted synthetic SOP-impact probe 91")), after)
            self.check(case, "snapshot remains the pinned visible witness with operations scope", expected["canonical_snapshot_unchanged"] is True and after["snapshot"] == self.canonical and probe["acl"] == "restricted", {"expected_unchanged": expected["canonical_snapshot_unchanged"], "snapshot": after["snapshot"]})
            for key in ("version_id", "claimed_role_id", "source_id", "source_title", "source_text", "department_id", "relationship_id"):
                self.check(case, f"restricted {key} literal is absent from the operations draft", probe[key] not in serialized, {key: probe[key], "disclosed": probe[key] in serialized})
            self.check(case, "entire read-only SOP-impact API response is invariant under restricted-source injection", before_impact_status == after_impact_status == 200 and before_impact == after_impact, {"before": before_impact, "after": after_impact})
            self.check(case, "restricted-source injection does not alter either exact visible outage path", after["snapshot"]["impact"]["paths"] == self.canonical["impact"]["paths"] and after_impact["impact_paths"] == self.canonical["impact"]["paths"], after_impact["impact_paths"])
            self.check(case, "all visible evidence IDs remain pinned and contain no restricted probe source", after["source_ids"] == canonical_source_ids(self.canonical) and probe["source_id"] not in after["source_ids"], after["source_ids"])
            self.check(case, "visible V2/V3 claims remain exactly source-backed and unchanged", after_impact["role_claims"] == self.canonical["sop_witness"]["claim_evidence"] and after_impact["returned_claim_count"] == 2, after_impact["role_claims"])
            self.check(case, "no hidden derived path or dependent paraphrase leaks", all(value not in serialized for value in (probe["department_id"], probe["claimed_role_id"], "role probe 91", "restricted synthetic SOP-impact probe 91")), serialized)
            self.check(case, "operations viewer remains operations-scoped after denied fixture injection", after_impact["scope"] == expected["viewer_scope"] == "operations" and probe["acl"] == "restricted", {"viewer_scope": after_impact["scope"], "fixture_acl": probe["acl"]})

        self.scenario("denied-source-response-invariance", denied_invariance)

        def confirmation(case: dict[str, Any]) -> None:
            before_counts = self.counts("preview-readonly-before")
            action_file = self.state_dir / "confirmed-actions.json"
            self.check(case, "no local action file exists before preview", not action_file.exists(), str(action_file))
            status, draft = self.draft()
            after_counts = self.counts("preview-readonly-after")
            actions_status, actions = self.get("/api/actions")
            self.check(case, "preview writes no nodes or relationships to FalkorDB", status == 201 and before_counts == after_counts, {"before": before_counts, "after": after_counts})
            expected_preview = resolve_expected_reference(self.expected, cases[case["id"]]["preview_snapshot"])
            self.check(case, "preview carries the exact candidate-independent structured snapshot", draft.get("snapshot") == self.canonical and draft.get("snapshot") == expected_preview, draft.get("snapshot"))
            self.check(case, "preview creates no local action and leaves action list empty", actions_status == 200 and actions["actions"] == [] and not action_file.exists(), actions)
            false_status, denied = self.post("/api/handover/confirm", {"token": draft["confirmation_token"], "confirm": False})
            after_denial_status, after_denial = self.get("/api/actions")
            self.check(case, "server rejects confirmation unless explicit true is supplied", false_status == 400 and "Explicit in-app confirmation" in denied.get("error", ""), {"status": false_status, "body": denied})
            self.check(case, "denied confirmation persists no draft/action", after_denial_status == 200 and after_denial["actions"] == [] and not action_file.exists(), after_denial)
            self.check(case, "default handover summary is unchanged from the prior local draft", draft["summary"] == "Open material-transfer outage affects two operations departments. Confirmed role paths and source timestamps are included; SOP-OPS-9 remains unresolved and no escalation winner is asserted.", draft["summary"])
            self.check(case, "original outage follow-up text and its two source IDs are preserved", draft["proposed_actions"][0] == {"text": "Carry forward the equipment status for next-shift verification; do not infer a restoration time.", "source_ids": ["SRC-INC-204", "SRC-ASSET-C4"]}, draft["proposed_actions"][0])
            self.check(case, "original SOP follow-up text and both current source IDs are preserved", draft["proposed_actions"][1] == {"text": "Resolve the SOP-OPS-9 escalation disagreement before treating either current claim as authoritative.", "source_ids": ["SRC-SOP-CURRENT-V2", "SRC-SOP-CURRENT-V3"]}, draft["proposed_actions"][1])
            self.check(case, "generic source list is exactly the canonical snapshot provenance", draft["source_ids"] == canonical_source_ids(self.canonical), draft["source_ids"])
            self.check(case, "snapshot timestamp is the pinned UTC review instant", draft["snapshot"]["as_of"] == self.canonical["as_of"], draft["snapshot"]["as_of"])
            self.check(case, "snapshot path arrays and complete evidence rows match the independent pin", draft["snapshot"]["impact"]["paths"] == self.canonical["impact"]["paths"] and draft["snapshot"]["impact"]["path_evidence"] == self.canonical["impact"]["path_evidence"], draft["snapshot"]["impact"])
            self.check(case, "snapshot SOP subgraph is exactly the unresolved typed C4 witness", draft["snapshot"]["sop_witness"] == self.canonical["sop_witness"], draft["snapshot"]["sop_witness"])
            self.check(case, "preview action boundary explicitly denies truth resolution, performed work, clinical actions and external writes", draft["action_boundary"] == {"source_truth_resolved": False, "work_claimed_performed": False, "clinical_action_proposed": False, "messages_sent": False, "calls_made": False, "external_tickets_created": False, "external_writes": [], "automatically_confirmed": False}, draft["action_boundary"])
            self.case_data[case["id"]] = {"draft": draft, "pre_confirm_counts": before_counts}
            self.browser_confirmation(case, cases[case["id"]])

        self.scenario("read-only-preview-and-explicit-local-confirmation", confirmation)

        def timestamp_validation(case: dict[str, Any]) -> None:
            expected = cases[case["id"]]
            counts_before = self.counts("timestamp-before")
            status, normalized = self.draft(expected["offset_input"])
            self.check(case, "pinned +02:00 input normalizes to UTC and returns the entire canonical snapshot", status == 201 and normalized["snapshot"] == self.canonical and normalized["snapshot"]["as_of"] == expected["normalized_as_of"], normalized.get("snapshot"))
            action_file = self.state_dir / "confirmed-actions.json"
            persisted_before = action_file.read_bytes() if action_file.exists() else None
            invalid_status, invalid = self.draft(expected["malformed_timestamp"])
            counts_after = self.counts("timestamp-after-invalid")
            persisted_after = action_file.read_bytes() if action_file.exists() else None
            self.check(case, "exact malformed timestamp is rejected with HTTP 400 before creating pending response", invalid_status == 400 and "confirmation_token" not in invalid and "snapshot" not in invalid, {"status": invalid_status, "body": invalid})
            self.check(case, "invalid timestamp performs no graph or local-action mutation", counts_before == counts_after and persisted_before == persisted_after, {"counts_before": counts_before, "counts_after": counts_after, "action_file_unchanged": persisted_before == persisted_after})
            actions_status, actions = self.get("/api/actions")
            self.check(case, "timestamp probes neither auto-confirm nor create external effects", actions_status == 200 and actions["count"] == 1 and len(actions["actions"]) == 1 and actions["actions"][0]["snapshot"] == self.canonical and not invalid.get("external_effects"), {"actions": actions, "invalid": invalid})

        self.scenario("timestamp-normalization-and-pre-mutation-rejection", timestamp_validation)

        def repeat_seed_reconnect(case: dict[str, Any]) -> None:
            expected = cases[case["id"]]
            before = self.counts("repeat-before")
            for index, query in enumerate([*seed_queries(), *seed_shift_board_queries()]):
                self.live.graph_query(f"repeat-seed-{index}", query)
            after = self.counts("repeat-after")
            duplicate_nodes = self.query_rows("duplicate-node-fixture-ids", "MATCH (n) WHERE n.id IS NOT NULL WITH n.id AS id, count(n) AS copies WHERE copies > 1 RETURN id ORDER BY id")
            duplicate_edges = self.query_rows("duplicate-edge-fixture-ids", "MATCH ()-[r]->() WHERE r.id IS NOT NULL WITH r.id AS id, count(r) AS copies WHERE copies > 1 RETURN id ORDER BY id")
            self.check(case, "second deterministic fixture seed adds zero duplicate nodes or relationships", after["nodes"] - before["nodes"] == expected["node_delta_after_second_seed"] and after["relationships"] - before["relationships"] == expected["relationship_delta_after_second_seed"], {"before": before, "after": after})
            self.check(case, "fixture graph contains no duplicate IDs", duplicate_nodes == expected["duplicate_fixture_ids"] and duplicate_edges == expected["duplicate_fixture_ids"], {"nodes": duplicate_nodes, "edges": duplicate_edges})
            self.live.graph_query("reconnect-remove-owned-applicability", f"MATCH (:SOPVersion {{id:'SOP-OPS-9-V2'}})-[r:APPLIES_TO {{id:'{APPLICABILITY_EDGE_ID}'}}]->(:Equipment {{id:'CONVEYOR-C4'}}) DELETE r")
            self.seed_owned_edge(APPLICABILITY_EDGE_ID)
            assert self.live.client is not None
            old_client = self.live.client
            old_client_id = int(old_client.command("CLIENT", "ID"))
            old_client.close()
            self.live.client = RespConnection("127.0.0.1", self.args.db_port)
            new_client_id = int(self.live.client.command("CLIENT", "ID"))
            direct = result_rows(query_result(self.live.client.command(
                "GRAPH.QUERY", self.live.graph,
                "MATCH (v2:SOPVersion {id:'SOP-OPS-9-V2'})-[r:SOP_CONFLICT {id:'REL-SOP-9-CONFLICT'}]->(v3:SOPVersion {id:'SOP-OPS-9-V3'}) RETURN v2.id AS v2, type(r) AS rel, v3.id AS v3, r.status AS status",
            )))
            self.check(case, "fresh FalkorDB client connection is observed after prior client closes", new_client_id != old_client_id, {"old_client_id": old_client_id, "new_client_id": new_client_id})
            self.check(case, "fresh client reconstructs explicit unresolved conflict edge", direct == [{"v2": "SOP-OPS-9-V2", "rel": "SOP_CONFLICT", "v3": "SOP-OPS-9-V3", "status": "unresolved"}], direct)
            status, draft = self.draft()
            expected_reconnected = resolve_expected_reference(self.expected, expected["reconnected_snapshot"])
            self.check(case, "application reconnect returns exact ordered path, claims and source provenance snapshot", status == 201 and draft["snapshot"] == expected_reconnected == self.canonical, draft.get("snapshot"))
            actions_status, actions = self.get("/api/actions")
            self.check(case, "saved action remains byte-equivalent through reseed and reconnect", actions_status == 200 and len(actions["actions"]) == 1 and actions["actions"][0]["snapshot"] == self.canonical, actions)
            self.check(case, "run graph name includes the worker lane and unique run identifier", self.live.graph.startswith(f"wardatlas_test_{self.lane}_") and self.live.graph.endswith(self.live.run_id), self.live.graph)

        # No other scenario is permitted to persist local action state.
        self.scenario("idempotent-reseed-and-fresh-client-equality", repeat_seed_reconnect)

        self.validate_scenarios()

    def inject_restricted_probe(self, probe: dict[str, Any]) -> None:
        # The exact values are synthetic test setup literals pinned independently;
        # only this unique owned graph is mutated, with every injected item denied.
        escape = lambda value: json.dumps(value, ensure_ascii=True)
        version, department, role, source = (probe["version_id"], probe["department_id"], probe["claimed_role_id"], probe["source_id"])
        relation = probe["relationship_id"]
        statements = [
            f"MERGE (n:SOPVersion {{id:{escape(version)}}}) SET n.access='restricted', n.sop_id={escape(version)}, n.referenced_equipment_id='CONVEYOR-C4'",
            f"MERGE (n:Department {{id:{escape(department)}}}) SET n.access='restricted'",
            f"MERGE (n:Role {{id:{escape(role)}}}) SET n.access='restricted'",
            f"MERGE (n:Evidence {{id:{escape(source)}}}) SET n.access='restricted', n.title={escape(probe['source_title'])}, n.text={escape(probe['source_text'])}, n.observed_at='2026-10-14T16:10:00Z'",
            f"MATCH (a:Service {{id:'SVC-MATERIAL-TRANSFER'}}), (b:Department {{id:{escape(department)}}}) MERGE (a)-[r:SERVES {{id:{escape(relation)}}}]->(b) SET r.access='restricted'",
            f"MATCH (a:Department {{id:{escape(department)}}}), (b:Role {{id:{escape(role)}}}) MERGE (a)-[r:OWNED_BY {{id:{escape(relation + '-OWNER')}}}]->(b) SET r.access='restricted', r.current=true",
            f"MATCH (a:SOPVersion {{id:{escape(version)}}}), (b:Equipment {{id:'CONVEYOR-C4'}}) MERGE (a)-[r:APPLIES_TO {{id:{escape(relation + '-APPLIES')}}}]->(b) SET r.access='restricted'",
            f"MATCH (a:SOPVersion {{id:{escape(version)}}}), (b:Role {{id:{escape(role)}}}) MERGE (a)-[r:CLAIMS_ROLE {{id:{escape(relation + '-CLAIMS')}}}]->(b) SET r.access='restricted'",
            f"MATCH (a:SOPVersion {{id:{escape(version)}}}), (b:Evidence {{id:{escape(source)}}}) MERGE (a)-[r:EVIDENCED_BY {{id:{escape(relation + '-EVIDENCE')}}}]->(b) SET r.access='restricted'",
            f"MATCH (a:SOPVersion {{id:'SOP-OPS-9-V2'}}), (b:SOPVersion {{id:{escape(version)}}}) MERGE (a)-[r:SOP_CONFLICT {{id:{escape(relation + '-CONFLICT')}}}]->(b) SET r.access='restricted', r.status='unresolved'",
        ]
        for index, statement in enumerate(statements):
            self.live.graph_query(f"inject-restricted-probe-{index}", statement)

    def validate_scenarios(self) -> None:
        expected_ids = [row["id"] for row in self.expected["cases"]]
        actual_ids = [row["id"] for row in self.live.scenarios]
        # The read-only confirmation and repeatability assertions are separated
        # in the report under one matrix row to preserve its nine fixed groups.
        if actual_ids != expected_ids:
            raise VerificationFailure(f"nine fixed groups did not run in pinned order: {actual_ids!r}")
        matrix_groups = {group["case_id"]: group for group in self.matrix["groups"]}
        for row in self.live.scenarios:
            minimum = len(matrix_groups[row["id"]]["assertions"])
            # Group-level assertions intentionally compare structured output to
            # complete pinned objects; the minimum remains nonzero by matrix.
            if row["assertions"] <= 0 or minimum <= 0:
                raise VerificationFailure(f"pinned case has no actual assertion coverage: {row['id']}")
            row["pinned_matrix_assertion_floor"] = minimum

    def stop(self) -> None:
        if self.httpd is not None:
            if self.http_thread is not None and self.http_thread.is_alive():
                self.httpd.shutdown()
            self.httpd.server_close()
        if self.http_thread is not None:
            self.http_thread.join(timeout=5)
        self.app_stopped = self.http_thread is None or not self.http_thread.is_alive()
        self.live.stop()
        if self.state_dir_owned and self.state_dir.exists():
            shutil.rmtree(self.state_dir)
        if self.state_dir_owned:
            self.owned_state_removed = not self.state_dir.exists()

    def report(self) -> dict[str, Any]:
        live_report = self.live.report(self.status, self.error)
        return {
            "schema_version": 1,
            "verifier": "verify_handover_brief_live.py",
            "status": self.status,
            "error": self.error,
            "database_report": live_report,
            "browser_report": str(self.browser_report),
            "browser_evidence": str(self.args.evidence_dir),
            "app_bind": f"127.0.0.1:{self.args.app_port}",
            "app_stopped": self.app_stopped,
            "owned_state_removed": self.owned_state_removed,
            "scenario_ids": [row["id"] for row in self.live.scenarios],
            "assertion_count": self.live.assertion_count,
        }


def selected_port(value: int, label: str) -> None:
    try:
        base = int(os.environ["WORKSHOP_PORT_BASE"])
        span = int(os.environ["WORKSHOP_PORT_SPAN"])
    except (KeyError, ValueError) as exc:
        raise VerificationFailure("WORKSHOP_PORT_BASE and WORKSHOP_PORT_SPAN must be set") from exc
    if not (25000 <= value <= 25999 and base <= value < base + span):
        raise VerificationFailure(f"{label} must be selected inside this worker's allocated private port range")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db-port", type=int, required=True)
    parser.add_argument("--app-port", type=int, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--evidence-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    verifier: HandoverLiveVerifier | None = None
    result: dict[str, Any] = {"status": "running", "error": None}
    try:
        selected_port(args.db_port, "database port")
        selected_port(args.app_port, "app port")
        if args.db_port == args.app_port:
            raise VerificationFailure("app and database must use distinct selected private ports")
        expected = json.loads(EXPECTED_PATH.read_text(encoding="utf-8"))
        matrix = json.loads(MATRIX_PATH.read_text(encoding="utf-8"))
        verifier = HandoverLiveVerifier(args, expected, matrix)
        verifier.run()
        verifier.status = "PASS"
        verifier.stop()
        result = verifier.report()
        cleanup_ok = verifier.live.cleanup.get("owned_graph_deleted") is True and verifier.live.cleanup.get("owned_process_stopped") is True and result["app_stopped"] and result["owned_state_removed"]
        if not cleanup_ok:
            verifier.status = "FAIL"
            verifier.error = "owned app, graph, database, or local state cleanup was not confirmed"
            result = verifier.report()
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        if verifier.status != "PASS":
            print(f"FAIL: {verifier.error}; report: {args.report}", file=sys.stderr)
            return 1
        print(f"PASS: all nine handover witness cases against actual FalkorDB/API and desktop/mobile browser; {verifier.live.assertion_count} live assertions; report: {args.report}")
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
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(f"FAIL: {result['error']}; report: {args.report}", file=sys.stderr)
        return 1
    finally:
        if verifier is not None and verifier.live.process is not None and verifier.live.process.poll() is None:
            verifier.stop()


if __name__ == "__main__":
    raise SystemExit(main())

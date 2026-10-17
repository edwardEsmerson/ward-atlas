#!/usr/bin/env python3
"""Exercise the ten pinned SOP-impact outcomes on real FalkorDB and browser UI."""

from __future__ import annotations

import argparse
import hashlib
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

from verify_falkordb_live import LiveVerifier, RespConnection, VerificationFailure, query_result, result_rows
from wardatlas_app import Handler, WardAtlasHTTPServer, WardAtlasService
from wardatlas_fixtures import seed_queries
from wardatlas_shift_board import CONVEYOR_OUTAGE_AT, CONVEYOR_RESTORED_AT, seed_shift_board_queries
from verify_sop_impact_contract import (
    EXPECTED_ARTIFACT_SHA256,
    MATRIX_ARTIFACT_SHA256,
    validate_artifacts,
)


ROOT = Path(__file__).resolve().parents[1]
EXPECTED = ROOT / "tests/sop-impact-expected-results.json"
MATRIX = ROOT / "tests/sop-impact-adversarial-matrix.json"
PACKET = Path("/workspace/worktrees/planner-3bfcb0d8abbc45bc82e0fab8d715c2da/plans/planner-3bfcb0d8abbc45bc82e0fab8d715c2da.md")
CONFLICT_EDGE_ID = "REL-SOP-9-CONFLICT"
APPLICABILITY_EDGE_ID = "REL-SOP-V2-APPLIES-C4"


def selected_port(name: str, fallback_offset: int) -> int:
    base_text = os.environ.get("WORKSHOP_PORT_BASE")
    span_text = os.environ.get("WORKSHOP_PORT_SPAN")
    if base_text is None or span_text is None:
        raise VerificationFailure("WORKSHOP_PORT_BASE and WORKSHOP_PORT_SPAN are required")
    base, span = int(base_text), int(span_text)
    value = int(os.environ.get(name, str(base + fallback_offset)))
    if span <= 0 or not (base <= value < base + span) or not (1024 <= value <= 65535):
        raise VerificationFailure(f"{name} must be in the assigned private port range")
    return value


def load_pins(packet: Path) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    expected_raw = EXPECTED.read_bytes()
    matrix_raw = MATRIX.read_bytes()
    expected = json.loads(expected_raw)
    matrix = json.loads(matrix_raw)
    assertions, digests = validate_artifacts(
        expected,
        matrix,
        hashlib.sha256(expected_raw).hexdigest(),
        hashlib.sha256(matrix_raw).hexdigest(),
        ROOT,
        packet,
        require_ten_cases=True,
    )
    if assertions != 60 or hashlib.sha256(expected_raw).hexdigest() != EXPECTED_ARTIFACT_SHA256 or hashlib.sha256(matrix_raw).hexdigest() != MATRIX_ARTIFACT_SHA256:
        raise VerificationFailure("SOP-impact oracle bytes or independent case floors changed")
    return expected, matrix, digests


class SopImpactLiveRun:
    def __init__(self, db_port: int, app_port: int, expected: dict[str, Any], matrix: dict[str, Any], evidence_dir: Path):
        self.live = LiveVerifier(db_port, expected, matrix)
        lane = os.environ.get("WORKSHOP_LANE", "")
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,32}", lane):
            raise VerificationFailure("WORKSHOP_LANE must be a nonempty safe lane identifier")
        self.live.graph = f"wardatlas_test_{lane}_{self.live.run_id}"
        self.app_port = app_port
        self.expected = expected
        self.matrix = matrix
        self.evidence_dir = evidence_dir
        self.app: WardAtlasService | None = None
        self.httpd: WardAtlasHTTPServer | None = None
        self.http_thread: threading.Thread | None = None
        self.state_dir = ROOT / ".local" / "sop-impact-state" / self.live.run_id
        self.api_observations: list[dict[str, Any]] = []

    def api(self, as_of: str | None = None, scope: str = "operations", extra: list[tuple[str, str]] | None = None) -> tuple[int, dict[str, Any]]:
        params: list[tuple[str, str]] = [("scope", scope)]
        if as_of is not None:
            params.append(("as_of", as_of))
        params.extend(extra or [])
        query = urllib.parse.urlencode(params)
        request = urllib.request.Request(f"http://127.0.0.1:{self.app_port}/api/sop-impact?{query}", headers={"Accept": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                return response.status, json.loads(response.read())
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read())

    def require_ok(self, at: str | None = None) -> dict[str, Any]:
        status, body = self.api(at)
        if status != 200:
            raise VerificationFailure(f"live /api/sop-impact returned {status}: {body}")
        return body

    def counts(self) -> dict[str, int]:
        nodes = self.live.graph_rows("sop-impact-node-count", "MATCH (n) RETURN count(n) AS count")
        rels = self.live.graph_rows("sop-impact-edge-count", "MATCH ()-[r]->() RETURN count(r) AS count")
        return {"nodes": int(nodes[0]["count"]), "relationships": int(rels[0]["count"])}

    def check(self, case: dict[str, Any], name: str, condition: bool, detail: Any = None) -> None:
        self.live.check(case, name, condition, detail)

    def scenario(self, identifier: str, callback) -> None:
        self.live.scenario(identifier, callback)

    def graph_query(self, label: str, statement: str) -> dict[str, Any]:
        return self.live.graph_query(label, statement)

    def seed_one_edge(self, relationship_id: str) -> None:
        selected = [query for query in seed_queries() if relationship_id in query]
        if len(selected) != 1:
            raise VerificationFailure(f"expected exactly one owned fixture seed for {relationship_id}; got {len(selected)}")
        self.graph_query(f"reseed-{relationship_id}", selected[0])

    def inject_restricted_probe(self) -> None:
        statements = [
            "MERGE (n:SOPVersion {id:'SOP-RESTRICTED-PROBE-91'}) SET n.access='restricted', n.sop_id='SOP-RESTRICTED-PROBE-91', n.referenced_equipment_id='CONVEYOR-C4'",
            "MERGE (n:Department {id:'DEPT-RESTRICTED-PROBE-91'}) SET n.access='restricted'",
            "MERGE (n:Role {id:'ROLE-RESTRICTED-PROBE-91'}) SET n.access='restricted'",
            "MERGE (n:Evidence {id:'SRC-RESTRICTED-SOP-PROBE-91'}) SET n.access='restricted', n.title='Restricted synthetic SOP-impact probe 91', n.text='Restricted synthetic SOP claim: role probe 91.', n.observed_at='2026-10-14T16:10:00Z'",
            "MATCH (a:Service {id:'SVC-MATERIAL-TRANSFER'}), (b:Department {id:'DEPT-RESTRICTED-PROBE-91'}) MERGE (a)-[r:SERVES {id:'REL-RESTRICTED-SOP-PROBE-91'}]->(b) SET r.access='restricted'",
            "MATCH (a:Department {id:'DEPT-RESTRICTED-PROBE-91'}), (b:Role {id:'ROLE-RESTRICTED-PROBE-91'}) MERGE (a)-[r:OWNED_BY {id:'REL-RESTRICTED-SOP-PROBE-91-OWNER'}]->(b) SET r.access='restricted', r.current=true",
            "MATCH (a:SOPVersion {id:'SOP-RESTRICTED-PROBE-91'}), (b:Equipment {id:'CONVEYOR-C4'}) MERGE (a)-[r:APPLIES_TO {id:'REL-RESTRICTED-SOP-PROBE-91-APPLIES'}]->(b) SET r.access='restricted'",
            "MATCH (a:SOPVersion {id:'SOP-RESTRICTED-PROBE-91'}), (b:Role {id:'ROLE-RESTRICTED-PROBE-91'}) MERGE (a)-[r:CLAIMS_ROLE {id:'REL-RESTRICTED-SOP-PROBE-91-CLAIMS'}]->(b) SET r.access='restricted'",
            "MATCH (a:SOPVersion {id:'SOP-RESTRICTED-PROBE-91'}), (b:Evidence {id:'SRC-RESTRICTED-SOP-PROBE-91'}) MERGE (a)-[r:EVIDENCED_BY {id:'REL-RESTRICTED-SOP-PROBE-91-EVIDENCE'}]->(b) SET r.access='restricted'",
            "MATCH (a:SOPVersion {id:'SOP-OPS-9-V2'}), (b:SOPVersion {id:'SOP-RESTRICTED-PROBE-91'}) MERGE (a)-[r:SOP_CONFLICT {id:'REL-RESTRICTED-SOP-PROBE-91-CONFLICT'}]->(b) SET r.access='restricted', r.status='unresolved'",
        ]
        for index, statement in enumerate(statements):
            self.graph_query(f"inject-restricted-probe-{index}", statement)

    def run(self) -> None:
        self.live.launch()
        self.app = WardAtlasService(self.live.port, self.live.graph, self.state_dir)
        self.httpd = WardAtlasHTTPServer(("127.0.0.1", self.app_port), Handler, self.app)
        self.http_thread = threading.Thread(target=self.httpd.serve_forever, kwargs={"poll_interval": 0.1}, daemon=True)
        self.http_thread.start()
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{self.app_port}/", timeout=1).close()
                break
            except Exception:
                time.sleep(0.05)
        else:
            raise VerificationFailure("owned loopback WardAtlas HTTP server did not become ready")

        cases = {row["id"]: row["expected"] for row in self.expected["cases"]}
        before_baseline: dict[str, Any] | None = None

        def cold_empty(case: dict[str, Any]) -> None:
            assert self.app is not None and self.live.client is not None
            data = self.require_ok(CONVEYOR_OUTAGE_AT)
            self.api_observations.append({"case": case["id"], "body": data})
            empty = cases[case["id"]]
            for key in ("impact_paths", "sop_versions", "role_claims", "source_rows", "conflict_conclusion", "board_state"):
                self.check(case, f"cold graph pins {key}", data.get(key) == empty[key], {"actual": data.get(key), "expected": empty[key]})
            count_fields = sorted(key for key in data if "count" in key.casefold())
            self.check(case, "cold graph exposes no fact/count fields", count_fields == empty["exposed_count_fields"], count_fields)
            self.check(case, "no claims or hidden source IDs appear in cold response", data.get("role_claims") == [] and data.get("source_rows") == [], data)

            # Supplemental partial-graph adversary: a source-backed event is
            # not a cold empty graph when its dependency/ownership path is
            # missing. The fixed expected literal is test-owned and distinct
            # from the ten packet outcomes above.
            partial_fixture = [
                "MERGE (n:Incident {id:'INC-204'}) SET n.access='operations', n.status='open'",
                "MERGE (n:StateEvent {id:'EVT-SOP-PARTIAL-91'}) SET n.access='operations', n.value='unavailable', n.event_kind='observed', n.event_at='2026-10-14T18:00:00Z'",
                "MERGE (n:Evidence {id:'SRC-SOP-PARTIAL-91'}) SET n.access='operations', n.title='Synthetic partial impact event source', n.observed_at='2026-10-14T18:00:00Z'",
                "MATCH (a:Incident {id:'INC-204'}), (b:StateEvent {id:'EVT-SOP-PARTIAL-91'}) MERGE (a)-[r:HAS_STATE_EVENT {id:'REL-SOP-PARTIAL-91-EVENT'}]->(b) SET r.access='operations'",
                "MATCH (a:StateEvent {id:'EVT-SOP-PARTIAL-91'}), (b:Evidence {id:'SRC-SOP-PARTIAL-91'}) MERGE (a)-[r:EVIDENCED_BY {id:'REL-SOP-PARTIAL-91-EVIDENCE'}]->(b) SET r.access='operations'",
            ]
            for index, statement in enumerate(partial_fixture):
                self.graph_query(f"partial-observed-event-{index}", statement)
            partial = self.require_ok(CONVEYOR_OUTAGE_AT)
            self.api_observations.append({"case": case["id"], "supplemental_partial_graph": partial})
            partial_expected_event = {
                "event_id": "EVT-SOP-PARTIAL-91",
                "value": "unavailable",
                "event_at": "2026-10-14T18:00:00Z",
                "evidence_label": "observed source fact",
                "source_id": "SRC-SOP-PARTIAL-91",
                "source_title": "Synthetic partial impact event source",
                "source_observed_at": "2026-10-14T18:00:00Z",
            }
            self.check(case, "partial source-backed outage event is not mislabeled as cold empty", partial["board_state"] == "ready" and partial["impact_state"] == "observed-impact" and partial["observed_event"] == partial_expected_event, {"board_state": partial["board_state"], "impact_state": partial["impact_state"], "event": partial["observed_event"]})
            self.check(case, "missing dependency/owner path remains incomplete/unknown, not safe or absent", partial["impact_paths"] == [] and partial["impact_linkage_status"] == "incomplete/unknown" and partial["conflict_conclusion"] is None, {"paths": partial["impact_paths"], "linkage": partial["impact_linkage_status"], "conclusion": partial["conflict_conclusion"]})
            self.check(case, "partial event source remains inspectable without an invented SOP version", partial["source_rows"] == [{"source_id": "SRC-SOP-PARTIAL-91", "title": "Synthetic partial impact event source", "observed_at": "2026-10-14T18:00:00Z"}] and partial["sop_versions"] == [], {"sources": partial["source_rows"], "versions": partial["sop_versions"]})
            self.live.client.command("GRAPH.DELETE", self.live.graph)
            self.app.scenario_seeded_by_this_process = False

        self.scenario("sop-impact-cold-empty", cold_empty)

        def observed_outage(case: dict[str, Any]) -> None:
            nonlocal before_baseline
            assert self.app is not None
            self.app.reset_scenario()
            data = self.require_ok(CONVEYOR_OUTAGE_AT)
            before_baseline = data
            self.api_observations.append({"case": case["id"], "body": data})
            expected = cases[case["id"]]
            self.check(case, "exact two ordered outage paths", data["impact_paths"] == expected["impact_paths"], data["impact_paths"])
            self.check(case, "path source IDs/titles/timestamps equal pinned evidence", data["path_evidence"] == expected["path_evidence"], data["path_evidence"])
            self.check(case, "as-of is the pinned UTC instant", data["as_of"] == expected["as_of"], data["as_of"])
            self.check(case, "current ownership provenance is the unchanged roster source on both paths", data["current_ownership_source_ids"] == expected["current_ownership_source_ids"], data["current_ownership_source_ids"])
            self.check(case, "result comes from operations-visible graph-backed paths", data["scope"] == "operations" and data["board_state"] == "ready", {"scope": data["scope"], "board_state": data["board_state"]})

        self.scenario("sop-impact-observed-outage-paths", observed_outage)

        def conflict_provenance(case: dict[str, Any]) -> None:
            assert before_baseline is not None
            data = self.require_ok(CONVEYOR_OUTAGE_AT)
            self.api_observations.append({"case": case["id"], "body": data})
            expected = cases[case["id"]]
            for key in ("applicability_edges", "conflict_edge", "claim_evidence", "returned_claim_count", "conflict_status", "claims_are_unverified", "claims_are_current_owner_facts", "winner_selected"):
                actual = data.get(key)
                if key == "claim_evidence":
                    actual = data.get("role_claims")
                self.check(case, f"exact pinned conflict provenance {key}", actual == expected[key], {"actual": actual, "expected": expected[key]})
            self.check(case, "conflict witness requires actual relationship IDs on the graph", bool(data["conflict_edge"]) and len(data["applicability_edges"]) == 2 and len(data["role_claims"]) == 2, {"edge": data["conflict_edge"], "applications": data["applicability_edges"], "claims": data["role_claims"]})

        self.scenario("sop-impact-conflict-provenance", conflict_provenance)

        def stale_excluded(case: dict[str, Any]) -> None:
            data = self.require_ok(CONVEYOR_OUTAGE_AT)
            self.api_observations.append({"case": case["id"], "body": data})
            expected = cases[case["id"]]
            stale = data.get("stale_version") or {}
            projection = {
                "stale_version_id": stale.get("id"),
                "stale_source_id": stale.get("source_id"),
                "stale_source_title": stale.get("source_title"),
                "stale_observed_at": stale.get("observed_at"),
                "stale_referenced_equipment_id": stale.get("referenced_equipment_id"),
                "stale_artifact_hidden": stale.get("stale_artifact_hidden"),
                "stale_is_applicable_to_c4": stale.get("stale_is_applicable_to_c4"),
                "stale_is_joined_to_inc204_conflict_witness": stale.get("stale_is_joined_to_inc204_conflict_witness"),
                "c4_conflict_version_ids": data["c4_conflict_version_ids"],
            }
            for key, value in expected.items():
                self.check(case, f"stale C3 exclusion {key}", projection.get(key) == value, {"actual": projection.get(key), "expected": value})
            self.check(case, "stale V1 remains visible as historical source but not C4 conflict witness", stale.get("id") in data["sop_versions"] and stale.get("id") not in data["c4_conflict_version_ids"], stale)

        self.scenario("sop-impact-stale-v1-excluded", stale_excluded)

        def applicability_counterfactual(case: dict[str, Any]) -> None:
            assert before_baseline is not None
            pre = self.counts()
            owned_before = self.live.graph_rows("count-owned-applicability-before", f"MATCH ()-[r:APPLIES_TO {{id:'{APPLICABILITY_EDGE_ID}'}}]->() RETURN count(r) AS count")
            self.graph_query("remove-owned-applicability-edge", f"MATCH (v:SOPVersion {{id:'SOP-OPS-9-V2'}})-[r:APPLIES_TO {{id:'{APPLICABILITY_EDGE_ID}'}}]->(e:Equipment {{id:'CONVEYOR-C4'}}) DELETE r")
            after_counts = self.counts()
            owned_after = self.live.graph_rows("count-owned-applicability-after", f"MATCH ()-[r:APPLIES_TO {{id:'{APPLICABILITY_EDGE_ID}'}}]->() RETURN count(r) AS count")
            data = self.require_ok(CONVEYOR_OUTAGE_AT)
            self.api_observations.append({"case": case["id"], "body": data})
            expected = cases[case["id"]]
            self.check(case, "only the test-owned V2 APPLIES_TO C4 edge was removed", pre["relationships"] - after_counts["relationships"] == 1 and int(owned_before[0]["count"]) == 1 and int(owned_after[0]["count"]) == 0, {"before": pre, "after": after_counts, "edge_before": owned_before, "edge_after": owned_after})
            self.check(case, "complete conflict witness disappears after applicability-edge removal", data["complete_conflict_witness_returned"] is expected["complete_conflict_witness_returned"], data["conflict_edge"])
            self.check(case, "impact linkage reports incomplete/unknown, not resolved or absent", data["impact_linkage_status"] == expected["impact_linkage_status"] and data["conflict_conclusion"] is None, {"linkage": data["impact_linkage_status"], "conclusion": data["conflict_conclusion"]})
            self.check(case, "both exact outage paths survive edge removal", data["impact_paths"] == expected["outage_paths_after_removal"], data["impact_paths"])
            self.check(case, "outage path evidence stays byte-for-byte semantic-equivalent", data["path_evidence"] == before_baseline["path_evidence"] and data["impact_paths"] == before_baseline["impact_paths"], data["path_evidence"])
            self.seed_one_edge(APPLICABILITY_EDGE_ID)
            restored = self.require_ok(CONVEYOR_OUTAGE_AT)
            self.check(case, "reseed restores the exact complete V2/V3 conflict witness", restored["conflict_edge"] == before_baseline["conflict_edge"] and restored["claim_evidence"] == before_baseline["role_claims"], {"edge": restored["conflict_edge"], "claims": restored["role_claims"]})

        self.scenario("sop-impact-applicability-edge-counterfactual", applicability_counterfactual)

        def conflict_counterfactual(case: dict[str, Any]) -> None:
            assert before_baseline is not None
            pre = self.counts()
            owned_before = self.live.graph_rows("count-owned-conflict-before", f"MATCH ()-[r:SOP_CONFLICT {{id:'{CONFLICT_EDGE_ID}'}}]->() RETURN count(r) AS count")
            self.graph_query("remove-owned-conflict-edge", f"MATCH (v2:SOPVersion {{id:'SOP-OPS-9-V2'}})-[r:SOP_CONFLICT {{id:'{CONFLICT_EDGE_ID}'}}]->(v3:SOPVersion {{id:'SOP-OPS-9-V3'}}) DELETE r")
            after_counts = self.counts()
            owned_after = self.live.graph_rows("count-owned-conflict-after", f"MATCH ()-[r:SOP_CONFLICT {{id:'{CONFLICT_EDGE_ID}'}}]->() RETURN count(r) AS count")
            data = self.require_ok(CONVEYOR_OUTAGE_AT)
            self.api_observations.append({"case": case["id"], "body": data})
            expected = cases[case["id"]]
            self.check(case, "only the V2-to-V3 test-owned SOP_CONFLICT edge was removed", pre["relationships"] - after_counts["relationships"] == 1 and int(owned_before[0]["count"]) == 1 and int(owned_after[0]["count"]) == 0, {"before": pre, "after": after_counts, "edge_before": owned_before, "edge_after": owned_after})
            self.check(case, "missing conflict linkage is unknown, never no-conflict or resolved", not data["complete_conflict_witness_returned"] and data["conflict_linkage_status"] == expected["conflict_linkage_status"] and data["conflict_conclusion"] is None, {"status": data["conflict_linkage_status"], "conclusion": data["conflict_conclusion"]})
            self.check(case, "source-backed role claims remain unchanged", data["role_claims"] == before_baseline["role_claims"] and expected["source_claims_preserved"] is True, data["role_claims"])
            self.check(case, "both exact outage paths and provenance remain unchanged", data["impact_paths"] == expected["outage_paths_after_removal"] and data["path_evidence"] == before_baseline["path_evidence"], data["impact_paths"])
            self.check(case, "source evidence remains separate from missing conflict edge", all(claim["source_evidence_linked"] for claim in data["role_claims"]), data["role_claims"])
            self.seed_one_edge(CONFLICT_EDGE_ID)
            restored = self.require_ok(CONVEYOR_OUTAGE_AT)
            self.check(case, "reseed restores exact pinned unresolved conflict edge and claims", expected["reseed_restores_exact_conflict_witness"] is True and restored["conflict_edge"] == before_baseline["conflict_edge"] and restored["role_claims"] == before_baseline["role_claims"], {"edge": restored["conflict_edge"], "claims": restored["role_claims"]})
            self.check(case, "restored conflict witness uses the original applicability edges", restored["applicability_edges"] == before_baseline["applicability_edges"], restored["applicability_edges"])

        self.scenario("sop-impact-conflict-edge-counterfactual", conflict_counterfactual)

        def restoration(case: dict[str, Any]) -> None:
            assert before_baseline is not None
            data = self.require_ok(CONVEYOR_RESTORED_AT)
            self.api_observations.append({"case": case["id"], "body": data})
            expected = cases[case["id"]]
            self.check(case, "19:30Z is normalized and exact", data["as_of"] == expected["as_of"], data["as_of"])
            self.check(case, "observed restoration leaves no active impact paths", data["impact_paths"] == expected["active_impact_paths"] and data["impact_state"] == "restored", {"paths": data["impact_paths"], "state": data["impact_state"]})
            self.check(case, "separate SOP conflict remains unresolved and source-backed", data["conflict_status"] == expected["sop_conflict_status"] and data["role_claims"] == before_baseline["role_claims"], {"status": data["conflict_status"], "claims": data["role_claims"]})
            self.check(case, "conflict witness matches observed-outage witness", data["conflict_edge"] == before_baseline["conflict_edge"] and data["applicability_edges"] == before_baseline["applicability_edges"], data["conflict_edge"])
            self.check(case, "event state and SOP conflict stay distinct", data["event_state_and_sop_conflict_are_distinct"] is expected["event_state_and_sop_conflict_are_distinct"], {"event": data["impact_state"], "conflict": data["conflict_status"]})
            self.check(case, "restoration asserts neither SOP safety nor no operational risk", data["sop_safe_conclusion_asserted"] is expected["sop_safe_conclusion_asserted"] and data["no_operational_risk_conclusion_asserted"] is expected["no_operational_risk_conclusion_asserted"], {"sop_safe": data["sop_safe_conclusion_asserted"], "no_risk": data["no_operational_risk_conclusion_asserted"]})

        self.scenario("sop-impact-restoration-snapshot", restoration)

        def denied_invariance(case: dict[str, Any]) -> None:
            before = self.require_ok(CONVEYOR_OUTAGE_AT)
            before_semantic = json.dumps(before, sort_keys=True, separators=(",", ":"))
            self.inject_restricted_probe()
            after = self.require_ok(CONVEYOR_OUTAGE_AT)
            self.api_observations.append({"case": case["id"], "before": before, "after": after})
            after_semantic = json.dumps(after, sort_keys=True, separators=(",", ":"))
            expected = cases[case["id"]]
            probe = self.expected["fixture"]["restricted_probe"]
            distinct_literals = [probe[key] for key in ("version_id", "claimed_role_id", "source_id", "source_title", "source_text", "department_id", "relationship_id")]
            self.check(case, "operations response is semantically invariant after restricted graph injection", before_semantic == after_semantic and expected["operations_response_semantically_identical_before_after"] is True, {"before_sha256": hashlib.sha256(before_semantic.encode()).hexdigest(), "after_sha256": hashlib.sha256(after_semantic.encode()).hexdigest()})
            self.check(case, "viewer and injected ACL labels match the pinned boundary", after["scope"] == expected["viewer_scope"] and probe["acl"] == expected["injected_probe_acl"], {"scope": after["scope"], "acl": probe["acl"]})
            self.check(case, "no distinctive restricted IDs/title/text/relation literals are disclosed", all(value not in after_semantic for value in distinct_literals), {value: value in after_semantic for value in distinct_literals})
            self.check(case, "no hidden count or existence field is disclosed", not any(key in after for key in ("restricted_count", "hidden_count", "denied_count", "restricted_exists", "hidden_exists")), list(after))
            self.check(case, "no restricted path or dependent paraphrase is disclosed", all(value not in after_semantic for value in (probe["department_id"], probe["claimed_role_id"], "role probe 91", "restricted synthetic SOP-impact probe 91")), after)
            self.check(case, "visible claim count and witness remain exact", after["role_claims"] == before["role_claims"] and after.get("returned_claim_count") == before.get("returned_claim_count"), after.get("returned_claim_count"))

        self.scenario("sop-impact-denied-source-invariance", denied_invariance)

        def reseed_reconnect(case: dict[str, Any]) -> None:
            assert self.app is not None and before_baseline is not None
            twice_before = self.counts()
            for query in [*seed_queries(), *seed_shift_board_queries()]:
                self.graph_query("repeat-idempotent-seed", query)
            twice_after = self.counts()
            self.check(case, "second deterministic seed creates zero nodes", twice_after["nodes"] - twice_before["nodes"] == cases[case["id"]]["node_delta_on_second_seed"], {"before": twice_before, "after": twice_after})
            self.check(case, "second deterministic seed creates zero relationships", twice_after["relationships"] - twice_before["relationships"] == cases[case["id"]]["relationship_delta_on_second_seed"], {"before": twice_before, "after": twice_after})
            # Counterfactually remove and restore one named, owned applicability relation.
            self.graph_query("reseed-case-remove-owned-edge", f"MATCH (v:SOPVersion {{id:'SOP-OPS-9-V2'}})-[r:APPLIES_TO {{id:'{APPLICABILITY_EDGE_ID}'}}]->(:Equipment {{id:'CONVEYOR-C4'}}) DELETE r")
            self.seed_one_edge(APPLICABILITY_EDGE_ID)
            reloaded = self.require_ok(CONVEYOR_OUTAGE_AT)
            self.check(case, "owned-edge reseed restores exact outage paths", cases[case["id"]]["exact_outage_paths_restored"] is True and reloaded["impact_paths"] == before_baseline["impact_paths"], reloaded["impact_paths"])
            self.check(case, "owned-edge reseed restores exact claims, sources, and timestamps", reloaded["role_claims"] == before_baseline["role_claims"] and cases[case["id"]]["exact_conflict_claims_sources_and_timestamps_restored"] is True and reloaded["conflict_edge"] == before_baseline["conflict_edge"], {"claims": reloaded["role_claims"], "edge": reloaded["conflict_edge"]})
            assert self.live.client is not None
            old_client = self.live.client
            old_id = int(old_client.command("CLIENT", "ID"))
            fresh = RespConnection("127.0.0.1", self.live.port)
            try:
                new_id = int(fresh.command("CLIENT", "ID"))
                raw = query_result(fresh.command("GRAPH.QUERY", self.live.graph, "MATCH (a:SOPVersion {id:'SOP-OPS-9-V2'})-[r:SOP_CONFLICT {id:'REL-SOP-9-CONFLICT'}]->(b:SOPVersion {id:'SOP-OPS-9-V3'}) RETURN a.id AS from_id, type(r) AS relationship, b.id AS to_id, r.status AS status"))
                rows = result_rows(raw)
            finally:
                fresh.close()
            self.check(case, "new FalkorDB client is required and observed", new_id != old_id, {"old_client_id": old_id, "new_client_id": new_id})
            self.check(case, "fresh client reads the exact unresolved conflict relation", rows == [{"from_id": "SOP-OPS-9-V2", "relationship": "SOP_CONFLICT", "to_id": "SOP-OPS-9-V3", "status": "unresolved"}], rows)
            after_reconnect = self.require_ok(CONVEYOR_OUTAGE_AT)
            semantic_keys = ("impact_paths", "path_evidence", "role_claims", "source_rows", "applicability_edges", "conflict_edge", "stale_version")
            self.check(case, "fresh API reconnect preserves complete ordered path/claim/source/time snapshot", all(after_reconnect[key] == before_baseline[key] for key in semantic_keys), {key: after_reconnect[key] == before_baseline[key] for key in semantic_keys})
            lane = os.environ["WORKSHOP_LANE"]
            self.check(case, "graph name is unique and lane-owned", self.live.graph.startswith(f"wardatlas_test_{lane}_") and self.live.graph.endswith(self.live.run_id), self.live.graph)
            self.check(case, "cleanup ownership is restricted to this graph and process", self.live.process is not None and self.live.database.get("bind", "").startswith("127.0.0.1:"), {"graph": self.live.graph, "bind": self.live.database.get("bind")})

        self.scenario("sop-impact-reseed-reconnect", reseed_reconnect)

        def time_browser_boundary(case: dict[str, Any]) -> None:
            before_counts = self.counts()
            boundary = self.expected["fixture"]
            encoded = self.require_ok(boundary["offset_input"])
            normalized = self.require_ok(boundary["offset_normalized_as_of"])
            self.check(case, "pinned offset input normalizes to UTC", encoded["as_of"] == cases[case["id"]]["normalized_as_of"] == normalized["as_of"], {"offset": encoded["as_of"], "utc": normalized["as_of"]})
            self.check(case, "normalized offset result matches exact outage paths and evidence", encoded["impact_paths"] == normalized["impact_paths"] == cases["sop-impact-observed-outage-paths"]["impact_paths"] and encoded["path_evidence"] == normalized["path_evidence"], encoded["impact_paths"])
            invalid_status, invalid = self.api(boundary["malformed_time_probe"])
            after_counts = self.counts()
            self.check(case, "malformed timestamp is rejected with HTTP 400", invalid_status == 400, {"status": invalid_status, "body": invalid})
            self.check(case, "invalid timestamp is rejected before graph mutation", before_counts == after_counts, {"before": before_counts, "after": after_counts})
            restricted_status, _ = self.api(CONVEYOR_OUTAGE_AT, scope="restricted")
            self.check(case, "API remains scoped to operations-only viewer", restricted_status == 403, restricted_status)
            browser_report = self.evidence_dir / "sop-impact-browser.json"
            screenshot_dir = self.evidence_dir / "screenshots"
            self.evidence_dir.mkdir(parents=True, exist_ok=True)
            assert self.live.client is not None and self.app is not None
            self.live.client.command("GRAPH.DELETE", self.live.graph)
            self.app.scenario_seeded_by_this_process = False
            command = ["node", str(ROOT / "scripts/sop_impact_browser_assertions.cjs"), f"http://127.0.0.1:{self.app_port}", str(browser_report), str(screenshot_dir)]
            completed = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, timeout=120)
            self.check(case, "desktop/mobile browser runner exits successfully", completed.returncode == 0, {"stdout": completed.stdout[-3000:], "stderr": completed.stderr[-3000:]})
            if completed.returncode != 0:
                raise VerificationFailure("SOP-impact desktop/mobile browser assertions failed")
            browser = json.loads(browser_report.read_text(encoding="utf-8"))
            self.check(case, "browser has nonzero desktop/mobile and local-only assertions", browser.get("status") == "PASS" and browser.get("assertion_count", 0) > 0, {"status": browser.get("status"), "assertions": browser.get("assertion_count")})
            boundary_expected = cases[case["id"]]
            action = encoded["action_boundary"]
            expected_flags = ("clinical_inference", "procedure_selected", "person_assigned", "messages_sent", "calls_made", "tickets_created", "automatically_confirmed")
            self.check(case, "no clinical inference, procedure or person is selected", all(action[key] is boundary_expected[key] for key in expected_flags[:3]), action)
            self.check(case, "no message/call/ticket/external write or automatic confirmation", all(action[key] is boundary_expected[key] for key in expected_flags[3:]) and action["external_writes"] == boundary_expected["external_writes"], action)

        self.scenario("sop-impact-time-browser-local-boundary", time_browser_boundary)

        actual_ids = [row["id"] for row in self.live.scenarios]
        expected_ids = [row["id"] for row in self.expected["cases"]]
        if actual_ids != expected_ids or any(row["status"] != "pass" or row["assertions"] <= 0 for row in self.live.scenarios):
            raise VerificationFailure("ten pinned SOP-impact groups were not each exercised with nonzero passing assertions")
        floors = {row["scenario_id"]: row["nonzero_assertion_count"] for row in self.matrix["cases"]}
        for scenario in self.live.scenarios:
            if scenario["assertions"] < floors[scenario["id"]]:
                raise VerificationFailure(f"pinned assertion floor failed for {scenario['id']}: {scenario['assertions']} < {floors[scenario['id']]}")

    def stop(self) -> None:
        if self.httpd is not None:
            self.httpd.shutdown()
            self.httpd.server_close()
        if self.http_thread is not None:
            self.http_thread.join(timeout=5)
        self.live.stop()
        if self.state_dir.exists():
            shutil.rmtree(self.state_dir)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db-port", type=int, default=None)
    parser.add_argument("--app-port", type=int, default=None)
    parser.add_argument("--pinned-plan", type=Path, default=PACKET)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--evidence-dir", type=Path, required=True)
    args = parser.parse_args(argv)

    verifier: SopImpactLiveRun | None = None
    error: str | None = None
    status = "FAIL"
    digests: dict[str, str] = {}
    expected: dict[str, Any] = {}
    matrix: dict[str, Any] = {}
    try:
        expected, matrix, digests = load_pins(args.pinned_plan)
        db_port = args.db_port if args.db_port is not None else selected_port("WORKSHOP_DB_PORT", 20)
        app_port = args.app_port if args.app_port is not None else selected_port("WORKSHOP_APP_PORT", 21)
        base, span = int(os.environ["WORKSHOP_PORT_BASE"]), int(os.environ["WORKSHOP_PORT_SPAN"])
        if db_port == app_port or any(port < base or port >= base + span for port in (db_port, app_port)):
            raise VerificationFailure("database and app ports must be distinct and allocated in this worker's private range")
        verifier = SopImpactLiveRun(db_port, app_port, expected, matrix, args.evidence_dir)
        verifier.run()
        status = "PASS"
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
        if verifier:
            verifier.live.failures.append(error)
    finally:
        if verifier:
            verifier.stop()
            if status == "PASS" and not (verifier.live.cleanup.get("owned_graph_deleted") is True and verifier.live.cleanup.get("owned_process_stopped") is True):
                error = "VerificationFailure: owned graph/process cleanup was not confirmed"
                verifier.live.failures.append(error)
                status = "FAIL"
            report = verifier.live.report(status, error)
            report["api_observations"] = verifier.api_observations
            report["oracle_digests"] = digests
            report["browser_report"] = str(args.evidence_dir / "sop-impact-browser.json")
        else:
            report = {"status": status, "error": error, "oracle_digests": digests, "scenario_results": [], "assertion_count": 0}
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    if status != "PASS":
        print(f"FAIL: {error}; report: {args.report}", file=sys.stderr)
        return 1
    print(f"PASS: actual FalkorDB SOP-impact API and desktop/mobile browser; 10 outcome groups, {verifier.live.assertion_count} assertions, graph/client cleanup confirmed; report: {args.report}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

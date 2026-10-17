#!/usr/bin/env python3
"""Verify the fixed handover-drift oracle and matrix without reading product output.

This checks the candidate-independent artifact contract only. It does not load
WardAtlas query/API/UI code and does not claim live FalkorDB behavior.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any


PACKET_SHA256 = "21a0b4cd4ae3b5a2fc7a1783a1964bc0bd47d68675a38b61b1a6bd14d606e6b5"
HANDOVER_WITNESS_PACKET_SHA256 = "82ee7daadb65238676478e1bea7e1229f3a7dee003d7d25620b7ebbf4c70d36b"
EXPECTED_ARTIFACT_SHA256 = "76121f6061fe6bec13d007b2dc3f9dadd737809b1be52e895674c7c39e156c1d"
MATRIX_ARTIFACT_SHA256 = "439adc3ed2f32c4e310d397ddf671bfb835dbaf8086b4b940f7d7335d22582aa"
PACKET_FILENAME = "planner-828ef7b6a76e4128b9e1b2ecad33c4a7.md"
HANDOVER_WITNESS_PACKET_FILENAME = "planner-d0017f7f160844fe9cf5a6036aa5068f.md"
HANDOVER_WITNESS_EXPECTED_SHA256 = "932ada7d43688b8bd1e4f147ac06dd585982d784ef8577f05cc00b07a97199f1"
HANDOVER_WITNESS_MATRIX_SHA256 = "d084f31a1b542670ab99818852cba8e1141bfb56688845c439b01f5ac1d89003"
HANDOVER_WITNESS_MAIN_COMMIT = "9a16defb295c32d95a73406bc3faefcae793716e"

PINNED_ORACLES = {
    "tests/handover-brief-expected-results.json": HANDOVER_WITNESS_EXPECTED_SHA256,
    "tests/handover-brief-adversarial-matrix.json": HANDOVER_WITNESS_MATRIX_SHA256,
    "tests/expected-results.json": "c77e00e47c4f1e26d565c3d9583653838d5cc88695c4e6aa19ac583205d92b87",
    "tests/shift-board-expected-results.json": "5d53a8f93245ee8391ec7cffb6d45b58355536d30d649db19cd53ca6b1ce2428",
    "tests/sop-impact-expected-results.json": "f94e65850384e36e7200b321d14628f074fee37fb159adbcd73bef00c6e1acca",
}
ORACLE_IDS = {
    "tests/handover-brief-expected-results.json": "handover-witness-expected-results",
    "tests/handover-brief-adversarial-matrix.json": "handover-witness-adversarial-matrix",
    "tests/expected-results.json": "original-expected-results",
    "tests/shift-board-expected-results.json": "shift-board-expected-results",
    "tests/sop-impact-expected-results.json": "sop-impact-expected-results",
}

REQUIRED_CASES = (
    "handover-drift-cold-unknown",
    "handover-drift-reviewed-snapshot",
    "handover-drift-observed-restoration-delta",
    "handover-drift-hypothetical-branch-isolation",
    "handover-drift-isolated-witness-edge-removals",
    "handover-drift-restricted-source-invariance",
    "handover-drift-read-only-boundary",
    "handover-drift-time-validation",
    "handover-drift-repeat-reconnect-persistence",
)
REQUIRED_MATRIX_POINTERS = {
    "handover-drift-cold-unknown": {
        "/cases/0/expected/cold_without_snapshot/historical_snapshot",
        "/cases/0/expected/cold_without_snapshot/risk_or_conflict_absence_concluded",
        "/cases/0/expected/saved_snapshot_with_empty_or_unavailable_graph/current_impact_paths",
        "/cases/0/expected/saved_snapshot_with_empty_or_unavailable_graph/current_sop_witness",
    },
    "handover-drift-reviewed-snapshot": {
        "/cases/1/expected/incident_id",
        "/cases/1/expected/snapshot",
        "/cases/1/expected/action_timestamp_field",
        "/cases/1/expected/direct_incident_to_sop_causal_edge_asserted",
    },
    "handover-drift-observed-restoration-delta": {
        "/cases/2/expected/current_status",
        "/cases/2/expected/sop_witness",
        "/cases/2/expected/historical_snapshot",
        "/cases/2/expected/new_role_paths",
        "/cases/2/expected/sop_witness_unchanged",
        "/cases/2/expected/no_operational_risk_conclusion_asserted",
    },
    "handover-drift-hypothetical-branch-isolation": {
        "/cases/3/expected/branch_status",
        "/cases/3/expected/historical_snapshot",
        "/cases/3/expected/branch_is_not_observed_fact",
    },
    "handover-drift-isolated-witness-edge-removals": {
        "/cases/4/expected/applicability_edge_removal/complete_current_witness_returned",
        "/cases/4/expected/applicability_edge_removal/current_impact_paths_unchanged",
        "/cases/4/expected/applicability_edge_removal/historical_snapshot",
        "/cases/4/expected/applicability_edge_removal/saved_action_unchanged",
        "/cases/4/expected/applicability_edge_removal/current_path_evidence_unchanged",
        "/cases/4/expected/applicability_edge_removal/no_resolved_or_absent_conclusion",
        "/cases/4/expected/conflict_edge_removal/conflict_conclusion",
        "/cases/4/expected/conflict_edge_removal/complete_current_witness_returned",
        "/cases/4/expected/conflict_edge_removal/current_impact_paths_unchanged",
        "/cases/4/expected/conflict_edge_removal/historical_snapshot",
        "/cases/4/expected/conflict_edge_removal/saved_action_unchanged",
        "/cases/4/expected/conflict_edge_removal/current_path_evidence_unchanged",
        "/cases/4/expected/historical_snapshot_remains_canonical",
    },
    "handover-drift-read-only-boundary": {
        "/cases/6/expected/messages_sent",
        "/cases/6/expected/calls_made",
        "/cases/6/expected/tickets_created",
        "/cases/6/expected/source_truth_resolved",
    },
    "handover-drift-repeat-reconnect-persistence": {
        "/cases/8/expected/repeated_query_result",
        "/cases/8/expected/cleanup_deletes_only_owned_graph",
        "/cases/8/expected/cleanup_stops_only_task_owned_processes",
        "/cases/8/expected/saved_action_unchanged_after_restart",
    },
}
PACKET_OUTCOME_LINES = tuple(range(64, 73))
PACKET_OUTCOME_PREFIXES = (
    "1. **No reviewed snapshot / cold graph:**",
    "2. **Reviewed snapshot at 18:30Z:**",
    "3. **Observed restoration comparison:**",
    "4. **Hypothetical comparison:**",
    "5. **Witness-edge counterfactuals:**",
    "6. **Denied-source invariance:**",
    "7. **Read-only review boundary:**",
    "8. **Time validation:**",
    "9. **Repeat/reconnect/persistence:**",
)
HANDOVER_WITNESS_OUTCOME_LINES = tuple(range(34, 43))
HANDOVER_WITNESS_OUTCOME_PREFIXES = (
    "1. **Cold empty:**",
    "2. **Observed outage paths:**",
    "3. **Typed SOP evidence:**",
    "4. **Edge counterfactual:**",
    "5. **Restoration separation:**",
    "6. **Denied-source invariance:**",
    "7. **Evidence snapshot and confirmation:**",
    "8. **Time validation:**",
    "9. **Repeatability and compatibility:**",
)

EXPECTED_PATHS = [
    ["INC-204", "CONVEYOR-C4", "SVC-MATERIAL-TRANSFER", "DEPT-CENTRAL-STERILE", "ROLE-STERILE-OPS-COORD"],
    ["INC-204", "CONVEYOR-C4", "SVC-MATERIAL-TRANSFER", "DEPT-MATERIALS-LOGISTICS", "ROLE-MATERIALS-DUTY"],
]
EXPECTED_APPLICABILITY_EDGES = [
    {"from": "SOP-OPS-9-V2", "relationship": "APPLIES_TO", "to": "CONVEYOR-C4"},
    {"from": "SOP-OPS-9-V3", "relationship": "APPLIES_TO", "to": "CONVEYOR-C4"},
]
EXPECTED_CONFLICT_EDGE = {
    "from": "SOP-OPS-9-V2",
    "relationship": "SOP_CONFLICT",
    "to": "SOP-OPS-9-V3",
    "status": "unresolved",
}
EXPECTED_CLAIMS = [
    {
        "sop_version_id": "SOP-OPS-9-V2",
        "claim_relationship": "CLAIMS_ROLE",
        "role_id": "ROLE-FACILITIES-DUTY",
        "claim_label": "unverified SOP claim",
        "source_evidence_linked": True,
        "source_id": "SRC-SOP-CURRENT-V2",
        "source_title": "Synthetic SOP-OPS-9 Current Text v2",
        "observed_at": "2026-10-14T16:00:00Z",
    },
    {
        "sop_version_id": "SOP-OPS-9-V3",
        "claim_relationship": "CLAIMS_ROLE",
        "role_id": "ROLE-MATERIALS-LEAD",
        "claim_label": "unverified SOP claim",
        "source_evidence_linked": True,
        "source_id": "SRC-SOP-CURRENT-V3",
        "source_title": "Synthetic SOP-OPS-9 Current Text v3",
        "observed_at": "2026-10-14T16:04:00Z",
    },
]


class ContractFailure(ValueError):
    """Raised when fixed oracle bytes, source pins, or case mappings drift."""


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ContractFailure(message)


def _load_json(path: Path) -> tuple[dict[str, Any], str]:
    try:
        raw = path.read_bytes()
        value = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ContractFailure(f"cannot load JSON artifact {path}: {exc}") from exc
    require(isinstance(value, dict), f"JSON artifact {path} must be an object")
    return value, hashlib.sha256(raw).hexdigest()


def _unique_ids(rows: list[dict[str, Any]], label: str) -> list[str]:
    ids = [row.get("id") for row in rows]
    require(all(isinstance(item, str) and item for item in ids), f"{label} IDs must be nonempty strings")
    require(len(ids) == len(set(ids)), f"{label} IDs must be unique")
    return ids


def _resolve_pointer(document: Any, pointer: str) -> Any:
    require(isinstance(pointer, str) and pointer.startswith("/"), f"invalid JSON pointer {pointer!r}")
    value = document
    for raw_part in pointer[1:].split("/"):
        part = raw_part.replace("~1", "/").replace("~0", "~")
        try:
            if isinstance(value, list):
                value = value[int(part)]
            elif isinstance(value, dict):
                value = value[part]
            else:
                raise KeyError(part)
        except (IndexError, KeyError, ValueError) as exc:
            raise ContractFailure(f"matrix JSON pointer does not resolve: {pointer}") from exc
    return value


def _leaf_pointers(value: Any, pointer: str):
    """Yield every terminal JSON pointer so no fixed case field escapes the matrix."""
    if isinstance(value, dict) and value:
        for key, child in value.items():
            escaped = str(key).replace("~", "~0").replace("/", "~1")
            yield from _leaf_pointers(child, f"{pointer}/{escaped}")
    elif isinstance(value, list) and value:
        for index, child in enumerate(value):
            yield from _leaf_pointers(child, f"{pointer}/{index}")
    else:
        yield pointer


def _pointer_covers(assertion_pointer: str, leaf_pointer: str) -> bool:
    """Whether an assertion directly pins a leaf or a structured value containing it."""
    return leaf_pointer == assertion_pointer or leaf_pointer.startswith(assertion_pointer.rstrip("/") + "/")


def verify_handover_witness_matrix(expected_oracle: dict[str, Any], matrix: dict[str, Any]) -> int:
    """Resolve every accepted handover-witness assertion against its pinned oracle."""
    require(matrix.get("required_case_count") == 9, "accepted handover-witness matrix must require all nine oracle groups")
    expected_cases = expected_oracle.get("cases")
    groups = matrix.get("groups")
    require(isinstance(expected_cases, list) and isinstance(groups, list), "accepted handover-witness expected cases and matrix groups must be arrays")
    case_ids = [row.get("id") for row in expected_cases]
    group_ids = [row.get("case_id") for row in groups]
    require(case_ids == group_ids and len(case_ids) == len(set(case_ids)), "accepted handover-witness matrix case groups differ from expected-results order")
    expected_by_id = {row["id"]: row for row in expected_cases}
    assertion_ids: set[str] = set()
    count = 0
    for group in groups:
        assertions = group.get("assertions")
        require(isinstance(assertions, list) and assertions, f"accepted handover-witness case {group.get('case_id')} has no assertions")
        for assertion in assertions:
            require(isinstance(assertion, dict), "accepted handover-witness matrix assertions must be objects")
            assertion_id = assertion.get("id")
            require(isinstance(assertion_id, str) and assertion_id.strip() and assertion_id not in assertion_ids, "accepted handover-witness matrix has a missing/duplicate assertion ID")
            assertion_ids.add(assertion_id)
            pointer = assertion.get("pointer")
            require(isinstance(pointer, str) and pointer.startswith("/"), f"accepted handover-witness assertion {assertion_id} has an invalid pointer")
            if pointer.startswith("/expected"):
                fixed_value = _resolve_pointer(expected_by_id[group["case_id"]], pointer)
            else:
                fixed_value = _resolve_pointer(expected_oracle, pointer)
            if assertion.get("equals_case_value") is True:
                require("equals" not in assertion, f"accepted handover-witness assertion {assertion_id} has two expected-value modes")
            else:
                require("equals_case_value" not in assertion and "equals" in assertion, f"accepted handover-witness assertion {assertion_id} must pin a literal or expected-case reference")
                require(fixed_value == assertion["equals"], f"accepted handover-witness assertion {assertion_id} literal differs from its expected-results field")
            count += 1
    require(count > 0, "accepted handover-witness matrix has no assertions")
    return count


def validate_structure(expected: dict[str, Any], matrix: dict[str, Any], require_nine_cases: bool) -> int:
    """Require exact outcome groups, safety scope, and nonempty traceable assertions."""
    require(expected.get("schema_version") == 1 and matrix.get("schema_version") == 1, "schema_version must be 1")
    require(expected.get("product") == "WardAtlas", "unexpected product")
    require("candidate" in expected.get("oracle_policy", "").lower() and "never" in expected.get("oracle_policy", "").lower(), "candidate output must not supply expectations")
    require("handover-witness packet/oracle" in expected.get("oracle_policy", "").lower(), "oracle policy must include the pinned handover-witness oracle")
    scope = expected.get("scope")
    require(isinstance(scope, dict) and scope.get("synthetic_only") is True, "oracle must remain synthetic-only")
    require(scope.get("clinical_decisions") is False, "oracle must forbid clinical decisions")
    require("actual falkordb" in str(scope.get("database_requirement", "")).lower(), "later behavior verification must require actual FalkorDB")
    require(scope.get("external_writes") is False and scope.get("candidate_output_is_oracle") is False, "scope must forbid external writes and candidate-derived expectations")
    require(matrix.get("oracle_file") == "tests/handover-drift-expected-results.json", "matrix points to a different oracle")
    require("candidate output never" in matrix.get("oracle_policy", "").lower(), "matrix must prohibit candidate-derived expectations")

    cases = expected.get("cases")
    matrix_cases = matrix.get("cases")
    require(isinstance(cases, list) and isinstance(matrix_cases, list), "expected and matrix cases must be arrays")
    case_ids = _unique_ids(cases, "expected case")
    matrix_ids = [case.get("case_id") for case in matrix_cases]
    require(all(isinstance(item, str) and item for item in matrix_ids), "matrix case IDs must be nonempty strings")
    require(len(matrix_ids) == len(set(matrix_ids)), "matrix case IDs must be unique")
    if require_nine_cases:
        require(len(cases) == 9 and len(matrix_cases) == 9, "--require-nine-cases requires exactly nine groups")
    require(tuple(case_ids) == REQUIRED_CASES, "the nine outcome groups or fixed order changed")
    require(matrix_ids == list(case_ids), "matrix cases must map one-to-one in expected-results order")

    sources = expected.get("pinned_sources")
    expected_sources = [
        {"id": "handover-drift-packet", "file": f"planner-828ef7b6a76e4128b9e1b2ecad33c4a7/plans/{PACKET_FILENAME}", "sha256": PACKET_SHA256, "outcome_lines": list(PACKET_OUTCOME_LINES)},
        {"id": "handover-witness-packet", "file": f"planner-d0017f7f160844fe9cf5a6036aa5068f/plans/{HANDOVER_WITNESS_PACKET_FILENAME}", "sha256": HANDOVER_WITNESS_PACKET_SHA256, "outcome_lines": list(HANDOVER_WITNESS_OUTCOME_LINES)},
        *[{"id": ORACLE_IDS[path], "file": path, "sha256": digest} for path, digest in PINNED_ORACLES.items()],
    ]
    require(sources == expected_sources, "pinned packet/oracle source identities or hashes changed")

    require(expected.get("handover_witness_oracle_review") == {
        "status": "accepted-source-and-matrix-pinned",
        "expected_results_file": "tests/handover-brief-expected-results.json",
        "expected_results_sha256": HANDOVER_WITNESS_EXPECTED_SHA256,
        "adversarial_matrix_file": "tests/handover-brief-adversarial-matrix.json",
        "adversarial_matrix_sha256": HANDOVER_WITNESS_MATRIX_SHA256,
        "tracked_main_commit_checked": HANDOVER_WITNESS_MAIN_COMMIT,
        "independent_fresh_review_required": True,
        "reason": "The accepted handover-witness expected-results oracle and its matrix are now tracked at the pinned main commit and are compared literally by this validator.",
    }, "accepted handover-witness expected-results/matrix source pins changed")

    fixture = expected.get("fixture")
    require(isinstance(fixture, dict), "fixture must be an object")
    require(fixture.get("incident_id") == "INC-204" and fixture.get("equipment_id") == "CONVEYOR-C4", "fixed incident/equipment IDs changed")
    require(fixture.get("service_id") == "SVC-MATERIAL-TRANSFER", "fixed service ID changed")
    require(fixture.get("snapshot_as_of") == "2026-10-14T18:30:00Z", "review snapshot time changed")
    require(fixture.get("observed_restoration_as_of") == "2026-10-14T19:30:00Z", "observed restoration time changed")
    require(fixture.get("branch_as_of") == "2026-10-14T18:50:00Z" and fixture.get("branch_id") == "BR-C4-RESTORE-DRILL", "hypothetical branch identity/time changed")
    require(fixture.get("restoration_event_id") == "EVT-C4-RESTORED-01", "observed restoration event changed")
    require(fixture.get("restoration_source_id") == "SRC-ASSET-C4-RESTORE", "observed restoration source changed")
    require(fixture.get("restoration_event_at") == "2026-10-14T19:15:00Z", "observed restoration event time changed")
    require(fixture.get("offset_input") == "2026-10-14T20:30:00+02:00" and fixture.get("offset_normalized_as_of") == "2026-10-14T18:30:00Z", "offset normalization fixture changed")
    require(fixture.get("malformed_timestamp_probe") == "not-a-timestamp", "malformed-time tripwire changed")
    require(fixture.get("viewer_scope") == "operations", "viewer scope changed")
    require(fixture.get("confirmation_timestamp_field") == "confirmed_at", "saved action confirmation timestamp field changed")
    require("verbatim" in fixture.get("confirmation_timestamp_semantics", "").lower(), "confirmation timestamp must remain a separate verbatim value")

    canonical = expected.get("canonical_snapshot")
    require(isinstance(canonical, dict), "canonical_snapshot must be an object")
    require(canonical.get("as_of") == "2026-10-14T18:30:00Z", "canonical graph snapshot time changed")
    impact = canonical.get("impact")
    require(isinstance(impact, dict) and impact.get("status") == "observed-impact", "canonical impact must be observed")
    require(impact.get("paths") == EXPECTED_PATHS, "canonical ordered outage paths changed")
    witness = canonical.get("sop_witness")
    require(isinstance(witness, dict), "canonical typed SOP witness is required")
    require(witness.get("shared_equipment_id") == "CONVEYOR-C4" and witness.get("linkage_status") == "complete", "canonical shared-equipment linkage changed")
    require(witness.get("conflict_status") == "unresolved", "canonical SOP conflict must remain unresolved")
    require(witness.get("applicability_edges") == EXPECTED_APPLICABILITY_EDGES, "canonical applicability edges changed")
    require(witness.get("conflict_edge") == EXPECTED_CONFLICT_EDGE, "canonical conflict edge changed")
    require(witness.get("claim_evidence") == EXPECTED_CLAIMS, "canonical typed role claims or provenance changed")
    require(witness.get("claims_are_current_owner_facts") is False and witness.get("winner_selected") is False, "SOP claims must not be promoted to current owners or winners")
    require(witness.get("direct_incident_to_sop_causal_edge_asserted") is False, "a direct incident-to-SOP causal edge must not be asserted")
    require(witness.get("procedure_or_clinical_conclusion_supplied") is False, "no procedure or clinical conclusion may be supplied")

    expected_by_id = {row.get("id"): row.get("expected") for row in expected.get("cases", [])}
    cold = expected_by_id["handover-drift-cold-unknown"]
    require(cold["cold_without_snapshot"]["current_state"] == "empty/unknown", "cold graph state must remain empty/unknown")
    require(cold["cold_without_snapshot"]["current_impact_paths"] == [] and cold["cold_without_snapshot"]["current_sop_witness"] is None, "cold graph must not fabricate paths or a witness")
    require(cold["cold_without_snapshot"]["comparison_status"] == "unknown" and cold["cold_without_snapshot"]["risk_or_conflict_absence_concluded"] is False, "cold unknown must not imply safety or conflict absence")
    require(cold["saved_snapshot_with_empty_or_unavailable_graph"]["historical_snapshot"] == "#/canonical_snapshot", "cold current graph must retain the historical snapshot")
    require(cold["saved_snapshot_with_empty_or_unavailable_graph"]["saved_action_unchanged"] is True, "cold graph may not rewrite the confirmed action")
    require(cold["saved_snapshot_with_empty_or_unavailable_graph"]["current_state"] == "empty/unknown", "cold current state with historical evidence must remain unknown")
    require(cold["saved_snapshot_with_empty_or_unavailable_graph"]["comparison_status"] == "unknown", "cold current comparison with historical evidence must remain unknown")
    require(cold["saved_snapshot_with_empty_or_unavailable_graph"]["historical_snapshot_label"] == "saved historical evidence", "historical evidence must be explicitly labeled as historical")

    reviewed = expected_by_id["handover-drift-reviewed-snapshot"]
    require(reviewed["as_of"] == "2026-10-14T18:30:00Z" and reviewed["impact_paths"] == EXPECTED_PATHS, "reviewed snapshot time or exact impact paths changed")
    require(reviewed["path_evidence"] == impact["path_evidence"], "reviewed snapshot path evidence differs from canonical provenance")
    require(reviewed["snapshot"] == "#/canonical_snapshot" and reviewed["sop_witness"] == "#/canonical_snapshot/sop_witness", "reviewed snapshot no longer points to complete canonical evidence")
    require(reviewed["action_timestamp_field"] == "confirmed_at" and reviewed["action_confirmation_timestamp_preserved_separately"] is True, "action confirmation timestamp must be preserved separately")
    require(reviewed["action_timestamp_not_replaced_by_snapshot_as_of"] is True and reviewed["action_snapshot_immutable_after_confirmation"] is True, "action timestamp/snapshot immutability changed")
    require(reviewed["direct_incident_to_sop_causal_edge_asserted"] is False and reviewed["winner_selected"] is False, "reviewed snapshot cannot assert unsupported causality or pick an SOP winner")

    restoration = expected_by_id["handover-drift-observed-restoration-delta"]
    require(restoration["as_of"] == "2026-10-14T19:30:00Z" and restoration["current_active_paths"] == [], "observed restoration time or current paths changed")
    require(restoration["removed_paths"] == EXPECTED_PATHS and restoration["added_paths"] == [] and restoration["new_role_paths"] == [], "restoration delta must remove exactly the historical paths and add none")
    require(restoration["restoration_source_fact"] == {
        "event_id": "EVT-C4-RESTORED-01", "source_id": "SRC-ASSET-C4-RESTORE", "event_at": "2026-10-14T19:15:00Z", "status": "observed"
    }, "observed restoration event/source/time changed")
    require(restoration["sop_witness"] == "#/canonical_snapshot/sop_witness" and restoration["sop_conflict_status"] == "unresolved" and restoration["sop_witness_unchanged"] is True, "restoration must retain separate unresolved SOP witness")
    require(restoration["safe_conclusion_asserted"] is False and restoration["no_operational_risk_conclusion_asserted"] is False and restoration["saved_action_unchanged"] is True, "restoration cannot imply safety or mutate reviewed history")

    branch = expected_by_id["handover-drift-hypothetical-branch-isolation"]
    require(branch["as_of"] == "2026-10-14T18:50:00Z" and branch["branch_id"] == "BR-C4-RESTORE-DRILL", "hypothetical branch time or identity changed")
    require(branch["branch_paths"] == [] and branch["branch_status"] == "hypothetical-restored" and branch["branch_delta_label"] == "hypothetical", "hypothetical branch output/label changed")
    require(branch["observed_mainline_status"] == "observed-impact" and branch["observed_mainline_paths"] == EXPECTED_PATHS, "branch must leave both observed mainline paths intact")
    require(branch["observed_state_mutated"] is False and branch["historical_snapshot"] == "#/canonical_snapshot" and branch["saved_action_unchanged"] is True, "hypothetical branch cannot mutate observed or historical state")

    edges = expected_by_id["handover-drift-isolated-witness-edge-removals"]
    require(edges["baseline_current_impact_paths"] == EXPECTED_PATHS, "edge counterfactual baseline paths changed")
    applies = edges["applicability_edge_removal"]
    require(applies["removed_edge"] == {"from": "SOP-OPS-9-V2", "relationship": "APPLIES_TO", "to": "CONVEYOR-C4", "ownership": "test-owned relation only"}, "applicability counterfactual must remove only its test-owned edge")
    require(applies["current_linkage_status"] == "incomplete/unknown" and applies["conflict_conclusion"] is None and applies["complete_current_witness_returned"] is False, "applicability edge loss must remain unknown, not resolve the conflict")
    require(applies["current_impact_paths"] == EXPECTED_PATHS and applies["current_impact_paths_unchanged"] is True and applies["saved_snapshot_unchanged"] is True and applies["saved_action_unchanged"] is True, "applicability edge loss must preserve paths and history")
    require(applies["current_path_evidence"] == impact["path_evidence"] and applies["current_path_evidence_unchanged"] is True, "applicability edge loss must preserve current ownership provenance")
    require(applies["incident_linked_sop_review_proposal_returned"] is False and applies["no_resolved_or_absent_conclusion"] is True, "missing applicability witness must suppress proposal without resolving/erasing conflict")
    conflict_removal = edges["conflict_edge_removal"]
    require(conflict_removal["removed_edge"] == {"from": "SOP-OPS-9-V2", "relationship": "SOP_CONFLICT", "to": "SOP-OPS-9-V3", "status": "unresolved", "ownership": "test-owned relation only"}, "conflict counterfactual must remove only its test-owned edge")
    require(conflict_removal["current_linkage_status"] == "unknown" and conflict_removal["conflict_conclusion"] is None and conflict_removal["complete_current_witness_returned"] is False, "conflict edge loss must remain unknown, not resolved/absent")
    require(conflict_removal["source_claims_preserved"] is True and conflict_removal["current_impact_paths"] == EXPECTED_PATHS and conflict_removal["current_impact_paths_unchanged"] is True, "conflict edge loss must preserve claims and outage paths")
    require(conflict_removal["current_path_evidence"] == impact["path_evidence"] and conflict_removal["current_path_evidence_unchanged"] is True, "conflict edge loss must preserve current ownership provenance")
    require(conflict_removal["incident_linked_sop_review_proposal_returned"] is False and conflict_removal["no_resolved_or_absent_conclusion"] is True, "missing conflict edge must suppress proposal without a no-conflict conclusion")
    require(conflict_removal["saved_snapshot_unchanged"] is True and conflict_removal["saved_action_unchanged"] is True and edges["reseed_restores_exact_current_witness"] is True, "edge counterfactual must preserve history and permit exact reseed")

    acl = expected_by_id["handover-drift-restricted-source-invariance"]
    require(acl["viewer_scope"] == "operations" and acl["restricted_probe"] == fixture["restricted_probe"], "restricted probe or operations scope changed")
    require(acl["operations_snapshot_delta_response_semantically_identical_before_after"] is True and acl["historical_snapshot_unchanged"] is True and acl["current_delta_unchanged"] is True, "restricted injection must not change visible output")
    require(acl["restricted_probe_literals_disclosed"] == [] and acl["hidden_count_or_existence_disclosed"] is False and acl["hidden_relationship_path_or_dependent_paraphrase_disclosed"] is False and acl["hidden_path_disclosed"] is False, "restricted IDs, facts, counts, existence, path or paraphrase must remain hidden")

    readonly = expected_by_id["handover-drift-read-only-boundary"]
    require(readonly["graph_mutations"] == 0 and readonly["confirmed_action_file_mutations"] == 0 and readonly["saved_action_before_after_identical"] is True, "review must not mutate graph or local action state")
    require(readonly["explicit_confirmation_performed"] is False and readonly["automatic_confirmation"] is False, "review must not confirm an action")
    require(readonly["external_writes"] == [] and readonly["messages_sent"] is False and readonly["calls_made"] is False and readonly["tickets_created"] is False, "review must not cause an external side effect")
    require(readonly["clinical_action_proposed"] is False and readonly["source_truth_resolved"] is False and readonly["work_claimed_performed"] is False, "review must not claim clinical action, source resolution, or completed work")

    time_case = expected_by_id["handover-drift-time-validation"]
    require(time_case["offset_input"] == fixture["offset_input"] and time_case["normalized_as_of"] == fixture["offset_normalized_as_of"], "time normalization values changed")
    require(time_case["normalized_result"] == "#/canonical_snapshot" and time_case["malformed_timestamp"] == fixture["malformed_timestamp_probe"], "normalized or malformed time reference changed")
    require(time_case["malformed_rejected"] is True and time_case["rejection_precedes_graph_mutation"] is True and time_case["rejection_precedes_action_mutation"] is True, "malformed time must be rejected before mutation")
    require(time_case["desktop_mobile_api_and_browser_semantics_agree"] is True, "desktop/mobile/API/browser semantics must agree")

    repeat = expected_by_id["handover-drift-repeat-reconnect-persistence"]
    require(repeat["seed_runs"] == 2 and repeat["node_delta_after_second_seed"] == 0 and repeat["relationship_delta_after_second_seed"] == 0 and repeat["duplicate_fixture_ids"] == [], "seed/reseed must be idempotent")
    require(repeat["fresh_falkordb_client_required"] is True and repeat["restarted_app_required"] is True, "persistence verification must use a fresh database client and restarted app")
    require(repeat["repeated_query_result"] == repeat["reconnected_snapshot"] == "#/canonical_snapshot" and repeat["reconnected_delta_matches_fixed_observed_delta"] is True, "repeat/reconnect must reconstruct fixed ordered snapshot and delta")
    require(repeat["historical_snapshot_and_action_timestamp_preserved"] is True and repeat["saved_action_unchanged_after_restart"] is True, "restart must preserve distinct action timestamp and historical snapshot")
    require(repeat["unique_task_owned_graph_required"] is True and repeat["cleanup_deletes_only_owned_graph"] is True and repeat["cleanup_stops_only_task_owned_processes"] is True and repeat["no_owned_process_left_running"] is True, "repeatability cleanup must remain task-owned and leave no process running")

    matrix_assertions = 0
    assertion_ids: set[str] = set()
    for index, (scenario, case) in enumerate(zip(cases, matrix_cases)):
        case_id = REQUIRED_CASES[index]
        require(scenario.get("id") == case_id, f"unexpected expected-results case at index {index}")
        require(isinstance(scenario.get("expected"), dict) and scenario["expected"], f"{case_id} must pin nonempty expected values")
        require(case.get("expected_case_id") == case_id, f"{case_id} matrix expected-case reference differs")
        require(bool(case.get("attack_or_edge_case")) and bool(case.get("setup")), f"{case_id} needs adversarial setup and purpose")
        assertions = case.get("pinned_assertions")
        count = case.get("nonzero_assertion_count")
        require(isinstance(assertions, list) and assertions, f"{case_id} has no pinned assertions")
        require(type(count) is int and count > 0, f"{case_id} must have a nonzero assertion count")
        require(count == len(assertions), f"{case_id} assertion count differs from its pinned assertions")
        assertion_pointers: set[str] = set()
        for assertion in assertions:
            require(isinstance(assertion, dict), f"{case_id} assertions must be structured objects")
            assertion_id = assertion.get("id")
            require(isinstance(assertion_id, str) and assertion_id.strip(), f"{case_id} has an empty assertion ID")
            require(assertion_id not in assertion_ids, f"duplicate assertion ID {assertion_id}")
            assertion_ids.add(assertion_id)
            require(isinstance(assertion.get("assertion"), str) and assertion["assertion"].strip(), f"{assertion_id} has no assertion description")
            pointer = assertion.get("expected_pointer")
            require(
                isinstance(pointer, str)
                and (pointer.startswith(f"/cases/{index}/expected/") or pointer == "/canonical_snapshot" or pointer.startswith("/canonical_snapshot/")),
                f"{assertion_id} must point into its case's fixed expected object or shared canonical snapshot",
            )
            _resolve_pointer(expected, pointer)
            assertion_pointers.add(pointer)
        missing_required = REQUIRED_MATRIX_POINTERS.get(case_id, set()) - assertion_pointers
        require(not missing_required, f"{case_id} required outcome field coverage missing: {sorted(missing_required)}")
        uncovered = [
            leaf
            for leaf in _leaf_pointers(scenario["expected"], f"/cases/{index}/expected")
            if not any(_pointer_covers(pointer, leaf) for pointer in assertion_pointers)
        ]
        require(not uncovered, f"{case_id} expected fields lack matrix assertion coverage: {uncovered}")
        require(case.get("planner_refs") == [f"{PACKET_FILENAME}:{PACKET_OUTCOME_LINES[index]}"], f"{case_id} does not map to its exact pinned packet line")
        matrix_assertions += count
    require(matrix_assertions > 0, "matrix must contain a nonzero number of assertions")
    return matrix_assertions


def verify_pinned_packet(path: Path) -> str:
    return _verify_packet(path, PACKET_SHA256, PACKET_OUTCOME_LINES, PACKET_OUTCOME_PREFIXES, "handover-drift")


def verify_handover_witness_packet(path: Path) -> str:
    return _verify_packet(path, HANDOVER_WITNESS_PACKET_SHA256, HANDOVER_WITNESS_OUTCOME_LINES, HANDOVER_WITNESS_OUTCOME_PREFIXES, "handover-witness")


def _verify_packet(path: Path, expected_digest: str, line_numbers: tuple[int, ...], prefixes: tuple[str, ...], label: str) -> str:
    try:
        raw = path.read_bytes()
        text = raw.decode("utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise ContractFailure(f"cannot read pinned {label} packet {path}: {exc}") from exc
    digest = hashlib.sha256(raw).hexdigest()
    require(digest == expected_digest, f"pinned {label} packet bytes differ from the independently pinned source")
    lines = text.splitlines()
    for line_number, prefix in zip(line_numbers, prefixes):
        require(len(lines) >= line_number and lines[line_number - 1].startswith(prefix), f"{label} packet line {line_number} outcome heading changed")
    return digest


def verify_existing_oracles(root: Path, expected: dict[str, Any]) -> dict[str, str]:
    """Require unchanged baseline bytes and direct overlap with fixed literals."""
    loaded: dict[str, dict[str, Any]] = {}
    digests: dict[str, str] = {}
    for relative, pinned in PINNED_ORACLES.items():
        try:
            raw = (root / relative).read_bytes()
        except OSError as exc:
            raise ContractFailure(f"cannot read pinned unchanged oracle {relative}: {exc}") from exc
        digest = hashlib.sha256(raw).hexdigest()
        require(digest == pinned, f"unchanged oracle {relative} differs from its accepted byte hash")
        try:
            loaded[relative] = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ContractFailure(f"cannot parse unchanged oracle {relative}: {exc}") from exc
        digests[relative] = digest

    original = loaded["tests/expected-results.json"]
    shift = loaded["tests/shift-board-expected-results.json"]
    sop = loaded["tests/sop-impact-expected-results.json"]
    handover_witness = loaded["tests/handover-brief-expected-results.json"]
    handover_witness_matrix = loaded["tests/handover-brief-adversarial-matrix.json"]
    original_cases = {row["id"]: row["expected"] for row in original["scenarios"]}
    shift_cases = {row["id"]: row["expected"] for row in shift["cases"]}
    sop_cases = {row["id"]: row["expected"] for row in sop["cases"]}
    handover_cases = {row["id"]: row["expected"] for row in handover_witness["cases"]}

    canonical = expected["canonical_snapshot"]
    impact = canonical["impact"]
    witness = canonical["sop_witness"]
    require(handover_witness["canonical_snapshot"] == canonical, "drift historical snapshot differs from accepted handover-witness oracle")
    verify_handover_witness_matrix(handover_witness, handover_witness_matrix)

    witness_cold = handover_cases["cold-empty-unknown"]
    require(witness_cold["impact_paths"] == [] and witness_cold["sop_witness"] is None and witness_cold["source_rows"] == 0 and witness_cold["saved_actions"] == [], "cold no-snapshot case differs from accepted handover-witness oracle")
    witness_observed = handover_cases["inc204-observed-paths-and-provenance"]
    require(witness_observed["as_of"] == canonical["as_of"] and witness_observed["impact_paths"] == impact["paths"] and witness_observed["path_evidence"] == impact["path_evidence"], "reviewed snapshot paths/provenance differ from accepted handover-witness oracle")
    witness_claims = handover_cases["typed-current-sop-witness-and-stale-exclusion"]["sop_witness"]
    witness_claim_snapshot = {key: value for key, value in witness_claims.items() if key not in {"returned_claim_count", "claims_are_unverified"}}
    require(witness_claim_snapshot == witness, "typed SOP witness differs from accepted handover-witness oracle")
    require(witness_claims["returned_claim_count"] == len(witness["claim_evidence"]) == 2 and witness_claims["claims_are_unverified"] is True, "accepted handover-witness claim count/typed status changed")

    witness_edges = handover_cases["owned-edge-removal-is-unknown-only"]
    drift_edges = expected["cases"][4]["expected"]
    expected_edge_rows = (drift_edges["applicability_edge_removal"], drift_edges["conflict_edge_removal"])
    for index, (witness_edge, drift_edge) in enumerate(zip(witness_edges["edge_removals"], expected_edge_rows)):
        require(witness_edge["removed_edge"] == drift_edge["removed_edge"], f"edge removal {index + 1} differs from accepted handover-witness oracle")
        require(witness_edge["complete_conflict_witness_returned"] == drift_edge["complete_current_witness_returned"] is False, f"edge removal {index + 1} witness-completeness expectation changed")
        require(witness_edge["linkage_status"] == drift_edge["current_linkage_status"], f"edge removal {index + 1} linkage status differs from accepted handover-witness oracle")
        require(witness_edge["impact_paths_after_removal"] == drift_edge["current_impact_paths"] == impact["paths"], f"edge removal {index + 1} outage paths differ from accepted handover-witness oracle")
        witness_path_evidence = _resolve_pointer(handover_witness, witness_edge["path_evidence_after_removal"][1:])
        require(witness_path_evidence == drift_edge["current_path_evidence"] == impact["path_evidence"], f"edge removal {index + 1} path provenance differs from accepted handover-witness oracle")
        require(witness_edge["path_evidence_unchanged"] is True and drift_edge["current_path_evidence_unchanged"] is True, f"edge removal {index + 1} must preserve current path provenance")
        require(witness_edge["incident_linked_sop_review_proposal_returned"] is False and drift_edge["incident_linked_sop_review_proposal_returned"] is False, f"edge removal {index + 1} proposal must remain suppressed without the witness")
        require(witness_edge["all_non_witness_snapshot_fields_unchanged"] is True and drift_edge["saved_snapshot_unchanged"] is True, f"edge removal {index + 1} must preserve the historical snapshot")
    require(witness_edges["reseeded_witness"] == "#/canonical_snapshot/sop_witness" and drift_edges["reseed_restores_exact_current_witness"] is True, "reseeded witness differs from accepted handover-witness oracle")

    witness_restoration = handover_cases["restoration-keeps-sop-claims-unresolved"]
    drift_restoration = expected["cases"][2]["expected"]
    require(witness_restoration["as_of"] == drift_restoration["as_of"] == "2026-10-14T19:30:00Z", "restoration time differs from accepted handover-witness oracle")
    require(witness_restoration["active_impact_paths"] == drift_restoration["current_active_paths"] == [], "restoration paths differ from accepted handover-witness oracle")
    require(witness_restoration["sop_conflict_status"] == drift_restoration["sop_conflict_status"] == "unresolved", "restoration must preserve unresolved SOP conflict")
    require(witness_restoration["sop_claims"] == witness["claim_evidence"] and witness_restoration["source_backed_witness_matches_canonical"] is True, "restoration SOP claims differ from accepted handover-witness oracle")
    require(witness_restoration["sop_safe_conclusion_asserted"] is False and witness_restoration["no_operational_risk_conclusion_asserted"] is False, "restoration must not infer safety/no risk")

    witness_acl = handover_cases["denied-source-response-invariance"]
    drift_acl = expected["cases"][5]["expected"]
    require(witness_acl["viewer_scope"] == drift_acl["viewer_scope"] == "operations" and witness_acl["restricted_probe"] == drift_acl["restricted_probe"], "restricted-source probe differs from accepted handover-witness oracle")
    require(witness_acl["operations_response_semantically_identical_before_after"] is True and drift_acl["operations_snapshot_delta_response_semantically_identical_before_after"] is True, "restricted-source response must be invariant")
    require(witness_acl["canonical_snapshot_unchanged"] is True and drift_acl["historical_snapshot_unchanged"] is True, "restricted-source injection must not alter historical snapshot")
    require(witness_acl["restricted_literals_disclosed"] == drift_acl["restricted_probe_literals_disclosed"] == [], "restricted probe literals must not leak")
    require(witness_acl["hidden_count_or_existence_disclosed"] is False and witness_acl["hidden_relation_or_dependent_paraphrase_disclosed"] is False and witness_acl["hidden_path_disclosed"] is False, "restricted count, relationship, paraphrase, or path must remain hidden")

    witness_handover = handover_cases["read-only-preview-and-explicit-local-confirmation"]
    drift_reviewed = expected["cases"][1]["expected"]
    require(witness_handover["preview_snapshot"] == witness_handover["saved_action_snapshot"] == drift_reviewed["snapshot"] == "#/canonical_snapshot", "reviewed and confirmed action snapshots differ from accepted handover-witness oracle")
    require(witness_handover["saved_snapshot_equals_preview"] is True and witness_handover["later_graph_change_rewrites_saved_snapshot"] is False and drift_reviewed["action_snapshot_immutable_after_confirmation"] is True, "saved handover snapshot must remain immutable")
    require(witness_handover["source_truth_resolved"] is False and witness_handover["work_claimed_performed"] is False and witness_handover["external_writes"] == [], "saved handover behavior differs from accepted local-only boundary")

    witness_time = handover_cases["timestamp-normalization-and-pre-mutation-rejection"]
    drift_time = expected["cases"][7]["expected"]
    require(witness_time["offset_input"] == drift_time["offset_input"] and witness_time["normalized_as_of"] == drift_time["normalized_as_of"], "offset input/normalization differs from accepted handover-witness oracle")
    require(witness_time["malformed_timestamp"] == drift_time["malformed_timestamp"] and witness_time["malformed_rejected"] is True and drift_time["malformed_rejected"] is True, "malformed time expectation differs from accepted handover-witness oracle")
    require(witness_time["rejection_precedes_graph_mutation"] is True and witness_time["rejection_precedes_action_mutation"] is True, "malformed time must be rejected before mutation")

    witness_repeat = handover_cases["idempotent-reseed-and-fresh-client-equality"]
    drift_repeat = expected["cases"][8]["expected"]
    require(witness_repeat["seed_runs"] == drift_repeat["seed_runs"] == 2 and witness_repeat["node_delta_after_second_seed"] == drift_repeat["node_delta_after_second_seed"] == 0 and witness_repeat["relationship_delta_after_second_seed"] == drift_repeat["relationship_delta_after_second_seed"] == 0, "repeat/reseed idempotence differs from accepted handover-witness oracle")
    require(drift_repeat["repeated_query_result"] == "#/canonical_snapshot", "repeat query must reproduce the exact pinned snapshot")
    require(witness_repeat["fresh_falkordb_client_required"] is True and drift_repeat["fresh_falkordb_client_required"] is True and witness_repeat["reconnected_snapshot"] == drift_repeat["reconnected_snapshot"] == "#/canonical_snapshot", "fresh-client snapshot differs from accepted handover-witness oracle")
    require(witness_repeat["cleanup_only_owned_graph_and_process"] is True and drift_repeat["cleanup_deletes_only_owned_graph"] is True and drift_repeat["cleanup_stops_only_task_owned_processes"] is True, "repeat cleanup differs from accepted handover-witness oracle")
    require(impact["paths"] == original["expected_paths"], "saved impact paths differ from original accepted oracle")
    require(impact["path_evidence"] == original["expected_evidence_by_path"], "saved path provenance differs from original accepted oracle")
    original_current = original_cases["multi-hop-outage-current-ownership"]
    shift_conveyor = shift_cases["conveyor-observed-and-hypothetical-times"]
    observed = shift_conveyor["observed_at_1830"]
    require(impact["paths"] == original_current["impact_paths"] == observed["paths"], "saved ordered paths disagree with original/shift-board oracles")
    require(impact["path_evidence"] == observed["canonical_path_evidence"], "saved path evidence disagrees with shift-board provenance")
    require(canonical["as_of"] == observed["at"] == "2026-10-14T18:30:00Z", "saved graph-state timestamp differs from shift-board oracle")

    sop_conflict = sop_cases["sop-impact-conflict-provenance"]
    sop_stale = sop_cases["sop-impact-stale-v1-excluded"]
    require(witness["applicability_edges"] == sop_conflict["applicability_edges"], "saved applicability edges differ from SOP-impact oracle")
    require(witness["conflict_edge"] == sop_conflict["conflict_edge"], "saved conflict edge differs from SOP-impact oracle")
    require(witness["claim_evidence"] == sop_conflict["claim_evidence"], "saved typed claims or their source provenance differ from SOP-impact oracle")
    require(witness["conflict_status"] == sop_conflict["conflict_status"] == "unresolved", "saved SOP witness must remain unresolved")
    require(witness["stale_version_excluded_from_c4_witness"] == {
        "version_id": sop_stale["stale_version_id"],
        "source_id": sop_stale["stale_source_id"],
        "source_title": sop_stale["stale_source_title"],
        "observed_at": sop_stale["stale_observed_at"],
        "referenced_equipment_id": sop_stale["stale_referenced_equipment_id"],
        "is_applicable_to_c4": sop_stale["stale_is_applicable_to_c4"],
        "is_joined_to_inc204_conflict_witness": sop_stale["stale_is_joined_to_inc204_conflict_witness"],
    }, "saved historical V1 exclusion differs from SOP-impact oracle")
    require(witness["claims_are_current_owner_facts"] == sop_conflict["claims_are_current_owner_facts"], "SOP claims must not be current owner facts")
    require(witness["winner_selected"] == sop_conflict["winner_selected"], "SOP conflict must not choose a winner")

    scenarios = {row["id"]: row["expected"] for row in expected["cases"]}
    cold = scenarios["handover-drift-cold-unknown"]["cold_without_snapshot"]
    original_empty = original_cases["cold-empty-graph"]
    board_empty = shift_cases["cold-empty-board"]
    sop_empty = sop_cases["sop-impact-cold-empty"]
    require(cold["current_impact_paths"] == original_empty["impact_paths"] == board_empty["role_paths"] == sop_empty["impact_paths"] == [], "cold current paths differ from pinned empty-graph oracles")
    require(cold["current_state"] == board_empty["board_state"] == sop_empty["board_state"] == "empty/unknown", "cold state must be empty/unknown")
    require(cold["current_sop_witness"] is None and cold["risk_or_conflict_absence_concluded"] is False, "cold graph must not invent a witness or no-conflict conclusion")
    cold_with_history = scenarios["handover-drift-cold-unknown"]["saved_snapshot_with_empty_or_unavailable_graph"]
    require(cold_with_history["historical_snapshot"] == "#/canonical_snapshot" and cold_with_history["saved_action_unchanged"] is True, "empty current graph must preserve an existing historical action snapshot")
    require(cold_with_history["current_state"] == "empty/unknown" and cold_with_history["comparison_status"] == "unknown" and cold_with_history["historical_snapshot_label"] == "saved historical evidence", "historical action with cold graph must label history and current unknown state")

    reviewed = scenarios["handover-drift-reviewed-snapshot"]
    require(reviewed["snapshot"] == "#/canonical_snapshot" and reviewed["sop_witness"] == "#/canonical_snapshot/sop_witness", "reviewed snapshot must resolve to the complete pinned witness")
    require(reviewed["action_timestamp_field"] == "confirmed_at" and reviewed["action_confirmation_timestamp_preserved_separately"] is True, "action confirmation timestamp must be preserved as a separate field")
    require(reviewed["action_timestamp_not_replaced_by_snapshot_as_of"] is True and reviewed["action_snapshot_immutable_after_confirmation"] is True, "action timestamp/snapshot immutability changed")

    restoration = shift_conveyor["observed_restoration_1930"]
    restore_case = scenarios["handover-drift-observed-restoration-delta"]
    require(restore_case["as_of"] == restoration["at"] == "2026-10-14T19:30:00Z", "restoration timestamp differs from shift-board oracle")
    require(restore_case["current_active_paths"] == restoration["active_paths"] == [], "restoration must remove all active paths")
    require(restore_case["removed_paths"] == restoration["historical_paths"] == impact["paths"], "restoration delta must remove exactly the historical paths")
    require(restore_case["added_paths"] == [] and restore_case["new_role_paths"] == [], "restoration delta must add no paths or roles")
    require(restore_case["restoration_source_fact"]["event_id"] == restoration["restoration_event_id"], "restoration event ID differs from shift-board oracle")
    require(restore_case["restoration_source_fact"]["source_id"] == restoration["restoration_source_id"], "restoration source ID differs from shift-board oracle")
    require(restore_case["restoration_source_fact"]["event_at"] == restoration["restoration_event_at"], "restoration event time differs from shift-board oracle")
    require(restore_case["sop_witness"] == "#/canonical_snapshot/sop_witness" and restore_case["sop_conflict_status"] == sop_conflict["conflict_status"], "restoration must preserve separate unresolved SOP witness")
    require(restore_case["safe_conclusion_asserted"] is False and restore_case["no_operational_risk_conclusion_asserted"] is False, "restoration must not imply safety or no risk")

    branch = shift_conveyor["hypothetical_branch_1850"]
    branch_case = scenarios["handover-drift-hypothetical-branch-isolation"]
    require(branch_case["as_of"] == branch["at"] == "2026-10-14T18:50:00Z", "branch time differs from shift-board oracle")
    require(branch_case["branch_id"] == branch["branch_id"] == "BR-C4-RESTORE-DRILL", "branch identity differs from shift-board oracle")
    require(branch_case["branch_paths"] == branch["branch_paths"] == [], "hypothetical branch path list differs from shift-board oracle")
    require(branch_case["observed_mainline_paths"] == branch["mainline_paths"] == impact["paths"], "hypothetical branch must preserve both observed mainline paths")
    require(branch_case["branch_delta_label"] == "hypothetical" and branch_case["observed_state_mutated"] == branch["observed_state_mutated"] is False, "branch labeling/isolation changed")

    edge_case = scenarios["handover-drift-isolated-witness-edge-removals"]
    sop_applies = sop_cases["sop-impact-applicability-edge-counterfactual"]
    sop_conflict_removed = sop_cases["sop-impact-conflict-edge-counterfactual"]
    applies = edge_case["applicability_edge_removal"]
    conflict = edge_case["conflict_edge_removal"]
    require({key: applies["removed_edge"][key] for key in ("from", "relationship", "to")} == {key: sop_applies["removed_edge"][key] for key in ("from", "relationship", "to")}, "applicability counterfactual edge differs from SOP-impact oracle")
    require(applies["current_linkage_status"] == sop_applies["impact_linkage_status"] == "incomplete/unknown", "applicability-edge loss must remain incomplete/unknown")
    require(applies["current_impact_paths"] == sop_applies["outage_paths_after_removal"] == impact["paths"], "applicability-edge removal must leave outage paths intact")
    require(applies["current_path_evidence"] == original["expected_evidence_by_path"], "applicability-edge removal must leave current ownership provenance intact")
    require({key: conflict["removed_edge"][key] for key in ("from", "relationship", "to", "status")} == {key: sop_conflict_removed["removed_edge"][key] for key in ("from", "relationship", "to", "status")}, "conflict counterfactual edge differs from SOP-impact oracle")
    require(conflict["current_linkage_status"] == sop_conflict_removed["conflict_linkage_status"] == "unknown", "conflict-edge loss must leave current linkage unknown")
    require(conflict["source_claims_preserved"] == sop_conflict_removed["source_claims_preserved"] is True, "conflict-edge removal must preserve source claims")
    require(conflict["current_impact_paths"] == sop_conflict_removed["outage_paths_after_removal"] == impact["paths"], "conflict-edge removal must leave outage paths intact")
    require(conflict["current_path_evidence"] == original["expected_evidence_by_path"], "conflict-edge removal must leave current ownership provenance intact")
    for counterfactual in (applies, conflict):
        require(counterfactual["conflict_conclusion"] is None, "missing witness edge must not assert no conflict or resolution")
        require(counterfactual["saved_snapshot_unchanged"] is True and counterfactual["saved_action_unchanged"] is True, "edge counterfactual must preserve the historical snapshot/action")
    require(edge_case["reseed_restores_exact_current_witness"] == sop_conflict_removed["reseed_restores_exact_conflict_witness"] is True, "owned reseed must restore the exact SOP witness")

    acl_case = scenarios["handover-drift-restricted-source-invariance"]
    require(acl_case["viewer_scope"] == sop["scope"]["viewer_scope"] == "operations", "restricted-source comparison must use the accepted operations scope")
    require(acl_case["restricted_probe"] == sop["fixture"]["restricted_probe"], "restricted probe differs from the unchanged SOP-impact oracle")
    require(acl_case["operations_snapshot_delta_response_semantically_identical_before_after"] is True, "operations response must remain invariant to restricted data")
    require(acl_case["restricted_probe_literals_disclosed"] == [] and acl_case["hidden_count_or_existence_disclosed"] is False and acl_case["hidden_relationship_path_or_dependent_paraphrase_disclosed"] is False, "restricted identifiers, counts, paths, or paraphrases must not leak")

    readonly = scenarios["handover-drift-read-only-boundary"]
    require(readonly["graph_mutations"] == 0 and readonly["confirmed_action_file_mutations"] == 0, "comparison must be read-only for graph and action file")
    require(readonly["external_writes"] == [] and readonly["messages_sent"] is False and readonly["calls_made"] is False and readonly["tickets_created"] is False, "comparison must have no external side effects")
    require(readonly["explicit_confirmation_performed"] is False and readonly["automatic_confirmation"] is False, "comparison must never automatically confirm an action")

    time_case = scenarios["handover-drift-time-validation"]
    require(time_case["offset_input"] == sop["fixture"]["offset_input"] and time_case["normalized_as_of"] == sop["fixture"]["offset_normalized_as_of"], "offset/time normalization differs from SOP-impact oracle")
    require(time_case["malformed_timestamp"] == sop["fixture"]["malformed_time_probe"] and time_case["malformed_rejected"] is True, "malformed timestamp tripwire differs or is accepted")
    require(time_case["rejection_precedes_graph_mutation"] is True and time_case["rejection_precedes_action_mutation"] is True, "malformed time must be rejected before mutation")

    repeat = scenarios["handover-drift-repeat-reconnect-persistence"]
    seed = original_cases["seed-reseed-idempotence"]
    reconnect = original_cases["reconnect-preserves-results"]
    require(repeat["seed_runs"] == seed["seed_runs"] == 2, "repeat seed count differs from original oracle")
    require(repeat["node_delta_after_second_seed"] == seed["node_count_delta_after_second_seed"] == 0, "repeat seed must not duplicate nodes")
    require(repeat["relationship_delta_after_second_seed"] == seed["relationship_count_delta_after_second_seed"] == 0, "repeat seed must not duplicate relationships")
    require(repeat["reconnected_snapshot"] == "#/canonical_snapshot" and repeat["reconnected_delta_matches_fixed_observed_delta"] is True, "reconnect must reconstruct fixed ordered snapshot and delta")
    require(repeat["fresh_falkordb_client_required"] == reconnect["new_client_connection_required"] is True, "reconnect case must use a fresh FalkorDB client")
    require(repeat["unique_task_owned_graph_required"] is True and repeat["cleanup_deletes_only_owned_graph"] is True and repeat["cleanup_stops_only_task_owned_processes"] is True, "cleanup must be constrained to task-owned graph/processes")
    return digests


def validate_artifacts(
    expected: dict[str, Any],
    matrix: dict[str, Any],
    expected_sha: str,
    matrix_sha: str,
    *,
    root: Path,
    pinned_plan: Path,
    handover_witness_plan: Path,
    require_nine_cases: bool,
) -> tuple[int, dict[str, str]]:
    require(expected_sha == EXPECTED_ARTIFACT_SHA256, "expected-results bytes differ from fixed handover-drift oracle")
    require(matrix_sha == MATRIX_ARTIFACT_SHA256, "adversarial-matrix bytes differ from fixed handover-drift matrix")
    assertions = validate_structure(expected, matrix, require_nine_cases)
    packet_digest = verify_pinned_packet(pinned_plan)
    witness_packet_digest = verify_handover_witness_packet(handover_witness_plan)
    oracle_digests = verify_existing_oracles(root, expected)
    require(expected["pinned_sources"][0]["sha256"] == packet_digest, "drift packet digest differs from the oracle source pin")
    require(expected["pinned_sources"][1]["sha256"] == witness_packet_digest, "handover-witness packet digest differs from the oracle source pin")
    return assertions, oracle_digests


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expected", type=Path, default=Path("tests/handover-drift-expected-results.json"))
    parser.add_argument("--matrix", type=Path, default=Path("tests/handover-drift-adversarial-matrix.json"))
    parser.add_argument("--pinned-plan", type=Path, required=True, help="path to the independently pinned handover-drift planner packet")
    parser.add_argument(
        "--handover-witness-plan",
        type=Path,
        default=Path("/workspace/worktrees/planner-d0017f7f160844fe9cf5a6036aa5068f/plans/planner-d0017f7f160844fe9cf5a6036aa5068f.md"),
        help="path to the independently pinned handover-witness packet",
    )
    parser.add_argument("--require-nine-cases", action="store_true")
    args = parser.parse_args(argv)
    try:
        expected, expected_sha = _load_json(args.expected)
        matrix, matrix_sha = _load_json(args.matrix)
        assertions, oracle_digests = validate_artifacts(
            expected,
            matrix,
            expected_sha,
            matrix_sha,
            root=Path.cwd(),
            pinned_plan=args.pinned_plan,
            handover_witness_plan=args.handover_witness_plan,
            require_nine_cases=args.require_nine_cases,
        )
    except ContractFailure as exc:
        print(f"handover-drift contract check failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps({
        "status": "pass",
        "check_type": "candidate-independent artifact contract only; no product behavior or live graph claim",
        "handover_witness_oracle_cross_comparison": "pass",
        "fresh_independent_review_required": True,
        "expected_sha256": expected_sha,
        "matrix_sha256": matrix_sha,
        "pinned_drift_packet_sha256": PACKET_SHA256,
        "pinned_handover_witness_packet_sha256": HANDOVER_WITNESS_PACKET_SHA256,
        "unchanged_oracle_sha256": oracle_digests,
        "case_count": len(REQUIRED_CASES),
        "nonzero_assertion_count": assertions,
    }, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

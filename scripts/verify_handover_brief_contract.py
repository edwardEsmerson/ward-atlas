#!/usr/bin/env python3
"""Validate the candidate-independent handover witness oracle and matrix.

This checks pinned source packets, oracle hashes, literal result values, and
nonzero case-to-assertion mappings only. It does not run or trust candidate
query, API, browser, graph, draft, or action output.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import sys
from pathlib import Path
from typing import Any


PACKET_PATH = "/workspace/worktrees/planner-d0017f7f160844fe9cf5a6036aa5068f/plans/planner-d0017f7f160844fe9cf5a6036aa5068f.md"
PACKET_SHA256 = "82ee7daadb65238676478e1bea7e1229f3a7dee003d7d25620b7ebbf4c70d36b"
PACKET_OUTCOME_LINES = list(range(34, 43))
PACKET_OUTCOME_HEADINGS = [
    "1. **Cold empty:**",
    "2. **Observed outage paths:**",
    "3. **Typed SOP evidence:**",
    "4. **Edge counterfactual:**",
    "5. **Restoration separation:**",
    "6. **Denied-source invariance:**",
    "7. **Evidence snapshot and confirmation:**",
    "8. **Time validation:**",
    "9. **Repeatability and compatibility:**",
]

PINNED_ORACLE_HASHES = {
    "tests/cascade-expected-results.json": "52fd4abe069e850e4ee070b8e6d0f9ed26fff5ad1e5e0c47efb28ad78227581b",
    "tests/expected-results.json": "c77e00e47c4f1e26d565c3d9583653838d5cc88695c4e6aa19ac583205d92b87",
    "tests/review-receipt-expected-results.json": "382383b65ae8a9984550227c8ad9000d893bcd9e0171298ec3bf6adb7e170972",
    "tests/role-scope-expected-results.json": "fc83b36dd9cfea900a1885816c6b84ffe7a1ab3d5c62d45be87f97a7368a9867",
    "tests/shift-board-expected-results.json": "5d53a8f93245ee8391ec7cffb6d45b58355536d30d649db19cd53ca6b1ce2428",
    "tests/sop-impact-expected-results.json": "f94e65850384e36e7200b321d14628f074fee37fb159adbcd73bef00c6e1acca",
    "tests/source-change-expected-results.json": "d26438dac655a9842d7bc60a2435ae1e3cab3a38a0f932d63005f044b0ba6097",
}

CASE_IDS = [
    "cold-empty-unknown",
    "inc204-observed-paths-and-provenance",
    "typed-current-sop-witness-and-stale-exclusion",
    "owned-edge-removal-is-unknown-only",
    "restoration-keeps-sop-claims-unresolved",
    "denied-source-response-invariance",
    "read-only-preview-and-explicit-local-confirmation",
    "timestamp-normalization-and-pre-mutation-rejection",
    "idempotent-reseed-and-fresh-client-equality",
]

PINNED_SOURCE_RECORDS = [
    {
        "id": "handover-brief-packet",
        "path": PACKET_PATH,
        "sha256": PACKET_SHA256,
        "outcome_lines": PACKET_OUTCOME_LINES,
    },
    {
        "id": "original-impact-oracle",
        "path": "tests/expected-results.json",
        "sha256": PINNED_ORACLE_HASHES["tests/expected-results.json"],
    },
    {
        "id": "shift-board-oracle",
        "path": "tests/shift-board-expected-results.json",
        "sha256": PINNED_ORACLE_HASHES["tests/shift-board-expected-results.json"],
    },
    {
        "id": "sop-impact-oracle",
        "path": "tests/sop-impact-expected-results.json",
        "sha256": PINNED_ORACLE_HASHES["tests/sop-impact-expected-results.json"],
    },
]

CASE_FULL_OBJECT_ASSERTIONS = {
    "cold-empty-unknown": "cold-empty.full-expected-object",
    "inc204-observed-paths-and-provenance": "inc204.full-expected-object",
    "typed-current-sop-witness-and-stale-exclusion": "sop.full-expected-object",
    "owned-edge-removal-is-unknown-only": "edge-counterfactual.full-expected-object",
    "restoration-keeps-sop-claims-unresolved": "restoration.full-expected-object",
    "denied-source-response-invariance": "acl.full-expected-object",
    "read-only-preview-and-explicit-local-confirmation": "handover.full-expected-object",
    "timestamp-normalization-and-pre-mutation-rejection": "time.full-expected-object",
    "idempotent-reseed-and-fresh-client-equality": "repeat.full-expected-object",
}

CASE_ORACLE_REFS = {
    "cold-empty-unknown": [
        "handover-brief-packet#outcome-1",
        "shift-board-expected-results.json#cold-empty-board",
        "sop-impact-expected-results.json#sop-impact-cold-empty",
    ],
    "inc204-observed-paths-and-provenance": [
        "handover-brief-packet#outcome-2",
        "expected-results.json#multi-hop-outage-current-ownership",
        "shift-board-expected-results.json#conveyor-observed-and-hypothetical-times",
        "sop-impact-expected-results.json#sop-impact-observed-outage-paths",
    ],
    "typed-current-sop-witness-and-stale-exclusion": [
        "handover-brief-packet#outcome-3",
        "expected-results.json#stale-reference-current-sop-conflict",
        "sop-impact-expected-results.json#sop-impact-conflict-provenance",
        "sop-impact-expected-results.json#sop-impact-stale-v1-excluded",
    ],
    "owned-edge-removal-is-unknown-only": [
        "handover-brief-packet#outcome-4",
        "sop-impact-expected-results.json#sop-impact-applicability-edge-counterfactual",
        "sop-impact-expected-results.json#sop-impact-conflict-edge-counterfactual",
    ],
    "restoration-keeps-sop-claims-unresolved": [
        "handover-brief-packet#outcome-5",
        "sop-impact-expected-results.json#sop-impact-restoration-snapshot",
    ],
    "denied-source-response-invariance": [
        "handover-brief-packet#outcome-6",
        "expected-results.json#denied-source-nondisclosure",
        "shift-board-expected-results.json#denied-source-nondisclosure",
        "sop-impact-expected-results.json#sop-impact-denied-source-invariance",
    ],
    "read-only-preview-and-explicit-local-confirmation": [
        "handover-brief-packet#outcome-7",
        "shift-board-expected-results.json#explicit-local-confirmation-no-external-writes",
    ],
    "timestamp-normalization-and-pre-mutation-rejection": [
        "handover-brief-packet#outcome-8",
        "sop-impact-expected-results.json#sop-impact-time-browser-local-boundary",
    ],
    "idempotent-reseed-and-fresh-client-equality": [
        "handover-brief-packet#outcome-9",
        "expected-results.json#seed-reseed-idempotence",
        "expected-results.json#reconnect-preserves-results",
        "sop-impact-expected-results.json#sop-impact-reseed-reconnect",
    ],
}

SCOPE = {
    "synthetic_only": True,
    "clinical_decisions": False,
    "database_requirement": "A later behavior check must use actual FalkorDB; this artifact oracle does not claim live execution.",
    "external_writes": False,
    "candidate_output_is_oracle": False,
}

FIXTURE = {
    "incident_id": "INC-204",
    "equipment_id": "CONVEYOR-C4",
    "service_id": "SVC-MATERIAL-TRANSFER",
    "observed_as_of": "2026-10-14T18:30:00Z",
    "restored_as_of": "2026-10-14T19:30:00Z",
    "offset_input": "2026-10-14T20:30:00+02:00",
    "offset_normalized_as_of": "2026-10-14T18:30:00Z",
    "malformed_timestamp_probe": "not-a-timestamp",
    "viewer_scope": "operations",
    "denied_source_id": "SRC-RESTRICTED-SOP-PROBE-91",
}


class ContractError(ValueError):
    """Raised when any oracle, artifact, or matrix pin is invalid."""


def _fail(message: str) -> None:
    raise ContractError(message)


def _read_json(path: Path, label: str) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        _fail(f"cannot read {label} {path}: {exc}")
    except json.JSONDecodeError as exc:
        _fail(f"invalid JSON in {label} {path}: {exc}")
    if not isinstance(data, dict):
        _fail(f"{label} must be a JSON object")
    return data


def _sha256(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError as exc:
        _fail(f"cannot hash pinned file {path}: {exc}")


def _need(condition: bool, message: str) -> None:
    if not condition:
        _fail(message)


def _json_equal(actual: Any, expected: Any) -> bool:
    """JSON value equality with strict types (not Python bool/int coercion)."""
    if type(actual) is not type(expected):
        return False
    if isinstance(actual, dict):
        return actual.keys() == expected.keys() and all(_json_equal(actual[key], expected[key]) for key in actual)
    if isinstance(actual, list):
        return len(actual) == len(expected) and all(_json_equal(left, right) for left, right in zip(actual, expected))
    return actual == expected


def _same(actual: Any, expected: Any, label: str) -> None:
    if not _json_equal(actual, expected):
        _fail(f"{label} differs from its pinned oracle")


def _find_case(document: dict[str, Any], collection: str, key: str, value: str, label: str) -> dict[str, Any]:
    rows = document.get(collection)
    _need(isinstance(rows, list), f"{label}.{collection} must be a list")
    matches = [row for row in rows if isinstance(row, dict) and row.get(key) == value]
    _need(len(matches) == 1, f"expected exactly one {label} {key}={value!r}")
    return matches[0]


def _json_pointer(document: Any, pointer: str, label: str) -> Any:
    _need(isinstance(pointer, str) and pointer.startswith("/"), f"{label}: JSON pointer must start with '/'")
    current = document
    for encoded in pointer[1:].split("/"):
        token = encoded.replace("~1", "/").replace("~0", "~")
        if isinstance(current, list):
            _need(token.isdigit(), f"{label}: nonnumeric array index in {pointer}")
            index = int(token)
            _need(index < len(current), f"{label}: array index out of range in {pointer}")
            current = current[index]
        elif isinstance(current, dict):
            _need(token in current, f"{label}: missing pointer target {pointer}")
            current = current[token]
        else:
            _fail(f"{label}: pointer traverses a scalar in {pointer}")
    return current


def _check_references(value: Any, document: dict[str, Any], label: str) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            _check_references(item, document, f"{label}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _check_references(item, document, f"{label}[{index}]")
    elif isinstance(value, str) and value.startswith("#/"):
        _json_pointer(document, value[1:], label)


def _source_oracles(repository_root: Path) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    for relative, expected_hash in PINNED_ORACLE_HASHES.items():
        source_path = repository_root / relative
        actual_hash = _sha256(source_path)
        _same(actual_hash, expected_hash, f"immutable source oracle hash {relative}")

    original = _read_json(repository_root / "tests/expected-results.json", "original outage oracle")
    shift = _read_json(repository_root / "tests/shift-board-expected-results.json", "shift-board oracle")
    sop = _read_json(repository_root / "tests/sop-impact-expected-results.json", "SOP-impact oracle")
    return original, shift, sop


def _pinned_case_results(original: dict[str, Any], shift: dict[str, Any], sop: dict[str, Any], packet_text: str) -> tuple[dict[str, Any], dict[str, Any]]:
    packet_lines = packet_text.splitlines()
    _need(len(packet_lines) >= max(PACKET_OUTCOME_LINES), "pinned packet is missing outcome lines")
    for line_number, heading in zip(PACKET_OUTCOME_LINES, PACKET_OUTCOME_HEADINGS):
        _need(packet_lines[line_number - 1].startswith(heading), f"packet outcome heading mismatch on line {line_number}")

    base_cold = _find_case(original, "scenarios", "id", "cold-empty-graph", "original oracle")["expected"]
    base_paths = _find_case(original, "scenarios", "id", "multi-hop-outage-current-ownership", "original oracle")["expected"]
    base_conflict = _find_case(original, "scenarios", "id", "stale-reference-current-sop-conflict", "original oracle")["expected"]
    base_seed = _find_case(original, "scenarios", "id", "seed-reseed-idempotence", "original oracle")["expected"]
    base_reconnect = _find_case(original, "scenarios", "id", "reconnect-preserves-results", "original oracle")["expected"]

    shift_cold = _find_case(shift, "cases", "id", "cold-empty-board", "shift-board oracle")["expected"]
    shift_conveyor = _find_case(shift, "cases", "id", "conveyor-observed-and-hypothetical-times", "shift-board oracle")["expected"]
    shift_confirmation = _find_case(shift, "cases", "id", "explicit-local-confirmation-no-external-writes", "shift-board oracle")["expected"]

    sop_fixture = sop.get("fixture")
    _need(isinstance(sop_fixture, dict), "SOP-impact fixture is missing")
    sop_cold = _find_case(sop, "cases", "id", "sop-impact-cold-empty", "SOP-impact oracle")["expected"]
    sop_observed = _find_case(sop, "cases", "id", "sop-impact-observed-outage-paths", "SOP-impact oracle")["expected"]
    sop_conflict = _find_case(sop, "cases", "id", "sop-impact-conflict-provenance", "SOP-impact oracle")["expected"]
    sop_stale = _find_case(sop, "cases", "id", "sop-impact-stale-v1-excluded", "SOP-impact oracle")["expected"]
    sop_applies_removed = _find_case(sop, "cases", "id", "sop-impact-applicability-edge-counterfactual", "SOP-impact oracle")["expected"]
    sop_conflict_removed = _find_case(sop, "cases", "id", "sop-impact-conflict-edge-counterfactual", "SOP-impact oracle")["expected"]
    sop_restored = _find_case(sop, "cases", "id", "sop-impact-restoration-snapshot", "SOP-impact oracle")["expected"]
    sop_acl = _find_case(sop, "cases", "id", "sop-impact-denied-source-invariance", "SOP-impact oracle")["expected"]
    sop_repeat = _find_case(sop, "cases", "id", "sop-impact-reseed-reconnect", "SOP-impact oracle")["expected"]
    sop_time = _find_case(sop, "cases", "id", "sop-impact-time-browser-local-boundary", "SOP-impact oracle")["expected"]

    paths = copy.deepcopy(sop_observed["impact_paths"])
    path_evidence = copy.deepcopy(sop_observed["path_evidence"])
    _same(paths, base_paths["impact_paths"], "observed INC-204 paths versus original oracle")
    _same(paths, shift_conveyor["observed_at_1830"]["paths"], "observed INC-204 paths versus shift-board oracle")
    _same(path_evidence, original["expected_evidence_by_path"], "complete path provenance versus original oracle")
    _same(path_evidence, shift_conveyor["observed_at_1830"]["canonical_path_evidence"], "complete path provenance versus shift-board oracle")
    _same(sop_observed["as_of"], FIXTURE["observed_as_of"], "18:30 snapshot timestamp")
    _same(sop_restored["as_of"], FIXTURE["restored_as_of"], "19:30 restoration timestamp")
    _same(shift_cold["board_state"], "empty/unknown", "cold empty-board semantics")
    _same(sop_cold["board_state"], "empty/unknown", "cold empty SOP semantics")

    stale_witness = {
        "version_id": sop_stale["stale_version_id"],
        "source_id": sop_stale["stale_source_id"],
        "source_title": sop_stale["stale_source_title"],
        "observed_at": sop_stale["stale_observed_at"],
        "referenced_equipment_id": sop_stale["stale_referenced_equipment_id"],
        "is_applicable_to_c4": sop_stale["stale_is_applicable_to_c4"],
        "is_joined_to_inc204_conflict_witness": sop_stale["stale_is_joined_to_inc204_conflict_witness"],
    }
    claims = copy.deepcopy(sop_conflict["claim_evidence"])
    conflict_witness = {
        "shared_equipment_id": FIXTURE["equipment_id"],
        "linkage_status": "complete",
        "conflict_status": sop_conflict["conflict_status"],
        "applicability_edges": copy.deepcopy(sop_conflict["applicability_edges"]),
        "conflict_edge": copy.deepcopy(sop_conflict["conflict_edge"]),
        "claim_evidence": claims,
        "stale_version_excluded_from_c4_witness": stale_witness,
        "claims_are_current_owner_facts": sop_conflict["claims_are_current_owner_facts"],
        "winner_selected": sop_conflict["winner_selected"],
        "contact_supplied": False,
        "direct_incident_to_sop_causal_edge_asserted": False,
        "procedure_or_clinical_conclusion_supplied": False,
    }
    conflict_case_witness = {
        **copy.deepcopy(conflict_witness),
        "returned_claim_count": sop_conflict["returned_claim_count"],
        "claims_are_unverified": sop_conflict["claims_are_unverified"],
    }
    canonical_snapshot = {
        "as_of": FIXTURE["observed_as_of"],
        "impact": {
            "status": "observed-impact",
            "paths": copy.deepcopy(paths),
            "path_evidence": copy.deepcopy(path_evidence),
        },
        "sop_witness": copy.deepcopy(conflict_witness),
    }

    source_claims = [
        {
            "sop_version_id": claim["sop_version_id"],
            "claim_relationship": claim["claim_relationship"],
            "role_id": claim["role_id"],
            "claim_label": claim["claim_label"],
            "source_evidence_linked": claim["source_evidence_linked"],
            "source_id": claim["source_id"],
            "source_title": claim["source_title"],
            "observed_at": claim["observed_at"],
        }
        for claim in claims
    ]
    referenced_claims = [
        {
            "sop_version_id": claim["sop_version_id"],
            "claim_relationship": claim["claim_relationship"],
            "role_id": claim["role_id"],
            "claim_label": claim["claim_label"],
            "source_id": claim["source_id"],
            "source_title": claim["source_title"],
            "observed_at": claim["observed_at"],
        }
        for claim in claims
    ]
    expected_paths_after_removal = copy.deepcopy(paths)
    path_evidence_reference = "#/cases/1/expected/path_evidence"

    removed_applies = {
        "removed_edge": {**copy.deepcopy(sop_applies_removed["removed_edge"]), "ownership": "test-owned relation only"},
        "complete_conflict_witness_returned": sop_applies_removed["complete_conflict_witness_returned"],
        "linkage_status": sop_applies_removed["impact_linkage_status"],
        "no_resolved_or_absent_conclusion": sop_applies_removed["conflict_not_reported_as_resolved_or_absent"],
        "incident_linked_sop_review_proposal_returned": False,
        "impact_paths_after_removal": copy.deepcopy(sop_applies_removed["outage_paths_after_removal"]),
        "impact_paths_unchanged": sop_applies_removed["outage_paths_unchanged"],
        "path_evidence_after_removal": path_evidence_reference,
        "path_evidence_unchanged": True,
        "all_non_witness_snapshot_fields_unchanged": True,
    }
    removed_conflict = {
        "removed_edge": {**copy.deepcopy(sop_conflict_removed["removed_edge"]), "ownership": "test-owned relation only"},
        "complete_conflict_witness_returned": sop_conflict_removed["complete_conflict_witness_returned"],
        "linkage_status": sop_conflict_removed["conflict_linkage_status"],
        "no_no_conflict_or_resolved_conclusion": sop_conflict_removed["no_no-conflict_or_resolved_conclusion"],
        "incident_linked_sop_review_proposal_returned": False,
        "source_claims_preserved": sop_conflict_removed["source_claims_preserved"],
        "impact_paths_after_removal": copy.deepcopy(sop_conflict_removed["outage_paths_after_removal"]),
        "impact_paths_unchanged": sop_conflict_removed["outage_paths_unchanged"],
        "path_evidence_after_removal": path_evidence_reference,
        "path_evidence_unchanged": True,
        "all_non_witness_snapshot_fields_unchanged": True,
        "reseed_restores_exact_conflict_witness": sop_conflict_removed["reseed_restores_exact_conflict_witness"],
    }

    expected_by_id = {
        "cold-empty-unknown": {
            "impact_paths": copy.deepcopy(base_cold["impact_paths"]),
            "sop_witness": None,
            "source_rows": shift_cold["source_rows"],
            "draft": None,
            "saved_actions": [],
            "board_state": shift_cold["board_state"],
            "risk_or_conflict_absence_concluded": False,
        },
        "inc204-observed-paths-and-provenance": {
            "as_of": sop_observed["as_of"],
            "incident_id": sop["fixture"]["incident_id"],
            "impact_paths": paths,
            "row_count": base_paths["row_count"],
            "path_evidence": path_evidence,
            "current_ownership_source_ids": copy.deepcopy(sop_observed["current_ownership_source_ids"]),
        },
        "typed-current-sop-witness-and-stale-exclusion": {"sop_witness": conflict_case_witness},
        "owned-edge-removal-is-unknown-only": {
            "baseline_impact_paths": copy.deepcopy(paths),
            "edge_removals": [removed_applies, removed_conflict],
            "reseeded_witness": "#/canonical_snapshot/sop_witness",
        },
        "restoration-keeps-sop-claims-unresolved": {
            "as_of": sop_restored["as_of"],
            "active_impact_paths": copy.deepcopy(sop_restored["active_impact_paths"]),
            "sop_conflict_status": sop_restored["sop_conflict_status"],
            "sop_claims": source_claims,
            "source_backed_witness_matches_canonical": True,
            "sop_safe_conclusion_asserted": sop_restored["sop_safe_conclusion_asserted"],
            "no_operational_risk_conclusion_asserted": sop_restored["no_operational_risk_conclusion_asserted"],
        },
        "denied-source-response-invariance": {
            "viewer_scope": sop["scope"]["viewer_scope"],
            "restricted_probe": copy.deepcopy(sop_fixture["restricted_probe"]),
            "operations_response_semantically_identical_before_after": sop_acl["operations_response_semantically_identical_before_after"],
            "canonical_snapshot_unchanged": True,
            "restricted_literals_disclosed": copy.deepcopy(sop_acl["restricted_probe_literals_disclosed"]),
            "hidden_count_or_existence_disclosed": sop_acl["hidden_count_or_existence_disclosed"],
            "hidden_relation_or_dependent_paraphrase_disclosed": sop_acl["hidden_relation_or_dependent_paraphrase_disclosed"],
            "hidden_path_disclosed": sop_acl["hidden_path_disclosed"],
        },
        "read-only-preview-and-explicit-local-confirmation": {
            "draft_persisted_before_confirmation": shift_confirmation["draft_persisted_before_confirmation"],
            "preview_graph_writes": 0,
            "preview_local_action_writes": 0,
            "preview_snapshot": "#/canonical_snapshot",
            "explicit_confirmation_required": shift_confirmation["explicit_confirmation_required"],
            "server_side_confirmation_required": True,
            "local_synthetic_actions_after_one_confirmation": shift_confirmation["local_synthetic_actions_written_after_one_confirmation"],
            "saved_action_snapshot": "#/canonical_snapshot",
            "saved_snapshot_equals_preview": True,
            "later_graph_change_rewrites_saved_snapshot": False,
            "source_truth_resolved": False,
            "work_claimed_performed": False,
            "clinical_action_proposed": shift_confirmation["clinical_action_proposed"],
            "messages_sent": shift_confirmation["messages_sent"],
            "calls_made": shift_confirmation["calls_made"],
            "external_tickets_created": shift_confirmation["external_tickets_created"],
            "external_writes": copy.deepcopy(shift_confirmation["external_writes"]),
        },
        "timestamp-normalization-and-pre-mutation-rejection": {
            "offset_input": sop_fixture["offset_input"],
            "normalized_as_of": sop_fixture["offset_normalized_as_of"],
            "normalized_result_matches": "#/canonical_snapshot",
            "malformed_timestamp": sop_fixture["malformed_time_probe"],
            "malformed_rejected": sop_time["malformed_timestamp_rejected_before_graph_mutation"],
            "rejection_precedes_graph_mutation": sop_time["malformed_timestamp_rejected_before_graph_mutation"],
            "rejection_precedes_action_mutation": True,
            "desktop_mobile_api_and_browser_semantics_agree": sop_time["desktop_mobile_api_and_browser_semantics_agree"],
            "clinical_inference": sop_time["clinical_inference"],
            "procedure_selected": sop_time["procedure_selected"],
            "person_assigned": sop_time["person_assigned"],
            "messages_sent": sop_time["messages_sent"],
            "calls_made": sop_time["calls_made"],
            "tickets_created": sop_time["tickets_created"],
            "external_writes": copy.deepcopy(sop_time["external_writes"]),
            "automatically_confirmed": sop_time["automatically_confirmed"],
        },
        "idempotent-reseed-and-fresh-client-equality": {
            "seed_runs": base_seed["seed_runs"],
            "node_delta_after_second_seed": sop_repeat["node_delta_on_second_seed"],
            "relationship_delta_after_second_seed": sop_repeat["relationship_delta_on_second_seed"],
            "duplicate_fixture_ids": copy.deepcopy(base_seed["duplicate_fixture_ids"]),
            "fresh_falkordb_client_required": sop_repeat["new_falkordb_client_required"],
            "reconnected_snapshot": "#/canonical_snapshot",
            "ordered_paths_claims_and_provenance_preserved": sop_repeat["complete_ordered_path_claim_source_timestamp_snapshot_preserved"],
            "owned_edge_removal_reseed_restores_exact_witness": sop_repeat["removed_test_owned_relation_reseeded"] and sop_repeat["exact_conflict_claims_sources_and_timestamps_restored"],
            "unique_task_owned_graph_required": sop_repeat["unique_lane_owned_graph_required"],
            "cleanup_only_owned_graph_and_process": sop_repeat["cleanup_only_owned_graph_and_process"],
        },
    }

    # Assert consistency of the source projections that become the canonical
    # snapshot. These checks keep the independently pinned oracles joined by
    # exact value rather than allowing a changed relationship to be inferred.
    _same(conflict_witness["applicability_edges"], sop_conflict["applicability_edges"], "SOP applicability witness")
    _same(conflict_witness["conflict_edge"], sop_conflict["conflict_edge"], "SOP conflict witness")
    _same(conflict_witness["claim_evidence"], sop_conflict["claim_evidence"], "SOP claim evidence")
    _same(stale_witness["version_id"], base_conflict["stale_version_id"], "stale SOP version")
    _same(stale_witness["source_id"], base_conflict["stale_source_id"], "stale SOP source")
    _same(sop_fixture["offset_input"], FIXTURE["offset_input"], "offset input")
    _same(sop_fixture["offset_normalized_as_of"], FIXTURE["offset_normalized_as_of"], "normalized UTC time")
    _same(sop_fixture["malformed_time_probe"], FIXTURE["malformed_timestamp_probe"], "malformed timestamp probe")
    for phrase in (
        "must not infer a direct causal edge from the incident to a SOP",
        "a missing witness edge must suppress the incident-linked SOP review proposal",
        "One server-side explicit confirmation",
        "a later graph change does not rewrite that reviewed snapshot",
    ):
        _need(phrase.lower() in packet_text.lower(), f"pinned packet no longer contains required rule: {phrase}")

    return expected_by_id, canonical_snapshot


def _validate_matrix(expected: dict[str, Any], matrix: dict[str, Any]) -> int:
    _same(matrix.get("schema_version"), 1, "matrix schema_version")
    _same(matrix.get("required_case_count"), 9, "matrix required_case_count")
    groups = matrix.get("groups")
    _need(isinstance(groups, list), "matrix.groups must be a list")
    group_ids = [group.get("case_id") for group in groups if isinstance(group, dict)]
    _same(group_ids, CASE_IDS, "matrix case mapping order/coverage")

    cases_by_id = {case["id"]: case for case in expected["cases"]}
    seen_assertions: set[str] = set()
    all_assertions: list[tuple[str, dict[str, Any]]] = []
    for group in groups:
        case_id = group["case_id"]
        assertions = group.get("assertions")
        _need(isinstance(assertions, list) and assertions, f"matrix group {case_id} has zero mapped assertions")
        _need(case_id in cases_by_id, f"matrix group maps unknown case {case_id}")
        ids = [assertion.get("id") for assertion in assertions if isinstance(assertion, dict)]
        _need(len(ids) == len(assertions), f"matrix group {case_id} contains a non-object assertion")
        _need(all(isinstance(assertion_id, str) and assertion_id for assertion_id in ids), f"matrix group {case_id} has an invalid assertion ID")
        _need(len(ids) == len(set(ids)), f"matrix group {case_id} has duplicate assertion IDs")
        for assertion in assertions:
            assertion_id = assertion.get("id")
            _need(isinstance(assertion_id, str) and assertion_id, f"matrix group {case_id} has an unnamed assertion")
            _need(assertion_id not in seen_assertions, f"duplicate matrix assertion ID {assertion_id}")
            seen_assertions.add(assertion_id)
            pointer = assertion.get("pointer")
            _need(isinstance(pointer, str), f"matrix assertion {assertion_id} is missing its JSON pointer")
            target_document = cases_by_id[case_id] if pointer == "/expected" or pointer.startswith("/expected/") else expected
            value = _json_pointer(target_document, pointer, f"matrix assertion {assertion_id}")
            if "equals" in assertion:
                _same(value, assertion["equals"], f"matrix assertion {assertion_id}")
            elif assertion.get("equals_case_value") is True:
                # The complete case object is itself matched to fixed values
                # before matrix validation, so this is an explicit mapping of
                # the named test assertion to that independently pinned value.
                pass
            else:
                _fail(f"matrix assertion {assertion_id} has no fixed equals value")
            all_assertions.append((case_id, assertion))

        full_id = CASE_FULL_OBJECT_ASSERTIONS[case_id]
        _need(
            any(assertion.get("id") == full_id and assertion.get("pointer") == "/expected" and assertion.get("equals_case_value") is True for assertion in assertions),
            f"matrix group {case_id} does not map its full fixed expected object",
        )

    _need(len(groups) == 9, "matrix must contain exactly nine groups")

    # The saved/previewed object is pinned as a complete deep value in both
    # JSON artifacts, not merely through a reference string.
    snapshot_assertions = [
        assertion for _, assertion in all_assertions
        if assertion.get("id") == "handover.entire-timestamped-snapshot-is-pinned"
    ]
    _need(len(snapshot_assertions) == 1, "matrix must contain one whole-snapshot assertion")
    _same(snapshot_assertions[0].get("pointer"), "/canonical_snapshot", "whole-snapshot assertion pointer")
    _same(snapshot_assertions[0].get("equals"), expected.get("canonical_snapshot"), "whole canonical snapshot matrix value")

    # Each pinned source is independently named in the matrix and its checksum
    # is an explicit assertion rather than decorative metadata.
    required_hash_assertions: dict[str, str] = {}
    for index, source in enumerate(expected["pinned_sources"]):
        required_hash_assertions[f"/pinned_sources/{index}/sha256"] = source["sha256"]
    for index, source in enumerate(expected["frozen_preexisting_oracle_files"]):
        required_hash_assertions[f"/frozen_preexisting_oracle_files/{index}/sha256"] = source["sha256"]
    matrix_hash_assertions = {
        assertion.get("pointer"): assertion.get("equals")
        for _, assertion in all_assertions
        if assertion.get("pointer") in required_hash_assertions and "equals" in assertion
    }
    for pointer, digest in required_hash_assertions.items():
        _same(matrix_hash_assertions.get(pointer), digest, f"matrix missing fixed source hash assertion {pointer}")

    for case in expected["cases"]:
        _check_references(case.get("expected"), expected, f"case {case['id']}")
    return len(all_assertions)


def validate_documents(
    expected: dict[str, Any],
    matrix: dict[str, Any],
    *,
    pinned_plan_path: Path,
    repository_root: Path,
) -> dict[str, int]:
    """Validate in-memory documents; candidate behavior is never consulted."""
    _same(expected.get("schema_version"), 1, "expected schema_version")
    _same(expected.get("product"), "WardAtlas", "expected product")
    _same(
        expected.get("purpose"),
        "Candidate-independent oracle for carrying the explicit INC-204 graph witness into a locally confirmed handover snapshot.",
        "expected purpose",
    )
    _same(
        expected.get("oracle_policy"),
        "Fixed from the pinned handover decision packet and the unchanged original, shift-board, and SOP-impact oracles. No candidate query, API, UI, draft, or saved action supplies an expected value.",
        "expected oracle policy",
    )
    _same(expected.get("scope"), SCOPE, "scope")
    _same(expected.get("fixture"), FIXTURE, "fixture")
    _same(expected.get("pinned_sources"), PINNED_SOURCE_RECORDS, "pinned source metadata")
    _same(
        expected.get("frozen_preexisting_oracle_files"),
        [{"path": path, "sha256": digest} for path, digest in PINNED_ORACLE_HASHES.items()],
        "frozen pre-existing oracle file list",
    )

    _same(str(pinned_plan_path), PACKET_PATH, "pinned packet path")
    _same(_sha256(pinned_plan_path), PACKET_SHA256, "pinned packet SHA-256")
    packet_text = pinned_plan_path.read_text(encoding="utf-8")
    original, shift, sop = _source_oracles(repository_root)
    expected_cases, canonical_snapshot = _pinned_case_results(original, shift, sop, packet_text)
    _same(expected.get("canonical_snapshot"), canonical_snapshot, "canonical snapshot")

    cases = expected.get("cases")
    _need(isinstance(cases, list), "expected.cases must be a list")
    case_ids = [case.get("id") for case in cases if isinstance(case, dict)]
    _same(case_ids, CASE_IDS, "expected nine fixed cases")
    for case in cases:
        case_id = case["id"]
        _same(case.get("expected"), expected_cases[case_id], f"fixed expected values for {case_id}")
        _same(case.get("oracle_refs"), CASE_ORACLE_REFS[case_id], f"packet/oracle references for {case_id}")

    assertion_count = _validate_matrix(expected, matrix)
    return {"case_count": len(cases), "assertion_count": assertion_count, "pinned_oracle_count": len(PINNED_ORACLE_HASHES)}


def validate_files(
    expected_path: Path,
    matrix_path: Path,
    pinned_plan_path: Path,
    repository_root: Path,
) -> dict[str, int]:
    expected = _read_json(expected_path, "handover expected results")
    matrix = _read_json(matrix_path, "handover adversarial matrix")
    return validate_documents(expected, matrix, pinned_plan_path=pinned_plan_path, repository_root=repository_root)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expected", type=Path, required=True, help="candidate-independent handover expected-results JSON")
    parser.add_argument("--matrix", type=Path, required=True, help="handover adversarial matrix JSON")
    parser.add_argument("--pinned-plan", type=Path, required=True, help="read-only pinned decision packet")
    parser.add_argument("--require-nine-cases", action="store_true", required=True, help="require the complete nine-case contract")
    parser.add_argument("--repo-root", type=Path, default=Path(__file__).resolve().parents[1], help="repository containing the immutable source oracles")
    args = parser.parse_args(argv)
    try:
        result = validate_files(
            args.expected.resolve(),
            args.matrix.resolve(),
            args.pinned_plan.resolve(),
            args.repo_root.resolve(),
        )
    except ContractError as exc:
        print(f"handover brief contract: FAIL: {exc}", file=sys.stderr)
        return 1
    print(
        "handover brief contract: PASS "
        f"({result['case_count']} fixed cases, {result['assertion_count']} mapped assertions, "
        f"{result['pinned_oracle_count']} unchanged source oracle hashes; no candidate behavior consulted)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

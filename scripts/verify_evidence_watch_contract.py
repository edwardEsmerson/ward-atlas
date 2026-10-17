#!/usr/bin/env python3
"""Validate the fixed evidence-watch oracle, source mappings, and review receipt.

This is an artifact-only contract check. It does not import product modules,
connect to FalkorDB, or claim that a watch behavior has been implemented.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any


EXPECTED_ARTIFACT_SHA256 = "1ee83f8ad7f9ad52b08b3759b20b590343c44eb09a7aeb2b18f1cd9d79027e1c"
MATRIX_ARTIFACT_SHA256 = "e7bdee4b56611d13fd7ad00195b4ec5ffb50d265503f04083d48854e96c13279"
# Set after the independently prepared receipt has been frozen.
REVIEW_RECEIPT_SHA256 = "fcaf08f5ffa759140c5a14a39d8b36f643ef8b3c7a607aac2d905bf9526922ef"

PLAN_FILENAME = "planner-3bfe3e5297d44e829f1f958a41c6c772.md"
PLAN_SHA256 = "0d5f5851e24fda62120f904ce539fe450da354a9b805e8e7589f17275642fa64"
PLAN_OUTCOME_LINES = tuple(range(70, 79))
PLAN_OUTCOME_PREFIXES = (
    "1. **No saved watch/cold graph:**",
    "2. **Preview vs explicit confirmation:**",
    "3. **Same effective time, new source knowledge:**",
    "4. **Crossed valid/known time:**",
    "5. **Watch comparison semantics:**",
    "6. **Owned edge/source sensitivity:**",
    "7. **Denied-source full-view invariance:**",
    "8. **Time/input and read-only refresh:**",
    "9. **Persistence/repeat/reconnect/cleanup:**",
)

PINNED_SOURCE_HASHES = {
    "tests/shift-board-expected-results.json": "5d53a8f93245ee8391ec7cffb6d45b58355536d30d649db19cd53ca6b1ce2428",
    "tests/shift-board-adversarial-matrix.json": "9cf1a4b144df3ff7357e389acec7c5e6b0e6ddcdab18577fa193c09541c0bbd7",
    "tests/dock-bitemporal-expected-results.json": "243c92775d7c1366962dcd7c5c9acd9ea753f2924af84c2c5dc99b0b5c872205",
    "tests/dock-bitemporal-adversarial-matrix.json": "84127f273043a03299823905bbecc06a755f79cb9a3b34c44793815eda7f5318",
}
SOURCE_IDS = {
    "shift-expected": "tests/shift-board-expected-results.json",
    "shift-matrix": "tests/shift-board-adversarial-matrix.json",
    "dock-expected": "tests/dock-bitemporal-expected-results.json",
    "dock-matrix": "tests/dock-bitemporal-adversarial-matrix.json",
}

REQUIRED_CASES = (
    "watch-cold-unknown-without-confirmation",
    "watch-preview-explicit-local-confirmation",
    "watch-new-source-known-still-r1",
    "watch-crossed-axes-unknown-and-effective-r2-changed",
    "watch-four-status-comparison-semantics",
    "watch-owned-edge-source-sensitivity",
    "watch-denied-source-full-view-invariance",
    "watch-time-validation-read-only-refresh",
    "watch-persistence-reconnect-idempotence-cleanup",
)
REQUIRED_ASSERTION_TOTAL = 86
EXPECTED_STATUS_PAIRS = [
    {
        "effective_at": "2026-10-14T20:29:00Z",
        "known_at": "2026-10-14T20:17:59Z",
        "status": "unchanged",
    },
    {
        "effective_at": "2026-10-14T20:29:00Z",
        "known_at": "2026-10-14T20:19:00Z",
        "status": "new-source-known",
    },
    {
        "effective_at": "2026-10-14T20:30:00Z",
        "known_at": "2026-10-14T20:17:59Z",
        "status": "unknown",
    },
    {
        "effective_at": "2026-10-14T20:30:00Z",
        "known_at": "2026-10-14T20:19:00Z",
        "status": "changed",
    },
]

EXPECTED_SOURCE_MANIFEST = [
    {
        "id": "evidence-watch-planner-packet",
        "file": f"planner-3bfe3e5297d44e829f1f958a41c6c772/plans/{PLAN_FILENAME}",
        "sha256": PLAN_SHA256,
        "outcome_lines": list(PLAN_OUTCOME_LINES),
    },
    {
        "id": "shift-expected",
        "file": "tests/shift-board-expected-results.json",
        "sha256": PINNED_SOURCE_HASHES["tests/shift-board-expected-results.json"],
    },
    {
        "id": "shift-matrix",
        "file": "tests/shift-board-adversarial-matrix.json",
        "sha256": PINNED_SOURCE_HASHES["tests/shift-board-adversarial-matrix.json"],
    },
    {
        "id": "dock-expected",
        "file": "tests/dock-bitemporal-expected-results.json",
        "sha256": PINNED_SOURCE_HASHES["tests/dock-bitemporal-expected-results.json"],
    },
    {
        "id": "dock-matrix",
        "file": "tests/dock-bitemporal-adversarial-matrix.json",
        "sha256": PINNED_SOURCE_HASHES["tests/dock-bitemporal-adversarial-matrix.json"],
    },
]


class ContractFailure(ValueError):
    """Raised when an oracle, source mapping, or reviewer receipt has drifted."""


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ContractFailure(message)


def sha256_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def canonical_sha256(value: Any) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return sha256_bytes(raw)


def _load_json(path: Path) -> tuple[dict[str, Any], bytes, str]:
    try:
        raw = path.read_bytes()
        value = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ContractFailure(f"cannot load JSON artifact {path}: {exc}") from exc
    require(isinstance(value, dict), f"JSON artifact {path} must contain an object")
    return value, raw, sha256_bytes(raw)


def _resolve_pointer(document: Any, pointer: str) -> Any:
    require(isinstance(pointer, str) and pointer.startswith("/"), f"invalid JSON pointer {pointer!r}")
    value = document
    for raw_part in pointer[1:].split("/"):
        part = raw_part.replace("~1", "/").replace("~0", "~")
        if isinstance(value, list):
            require(part.isdigit() and int(part) < len(value), f"JSON pointer does not resolve: {pointer}")
            value = value[int(part)]
        elif isinstance(value, dict) and part in value:
            value = value[part]
        else:
            raise ContractFailure(f"JSON pointer does not resolve: {pointer}")
    return value


def _find_case(rows: list[dict[str, Any]], case_id: str, label: str) -> dict[str, Any]:
    matches = [row for row in rows if row.get("id") == case_id or row.get("scenario_id") == case_id]
    require(len(matches) == 1, f"{label} must contain exactly one case {case_id}")
    return matches[0]


def _source_manifest() -> dict[str, str]:
    return {"planner": PLAN_SHA256, **PINNED_SOURCE_HASHES}


def verify_pinned_plan(path: Path) -> tuple[str, list[str]]:
    require(path.name == PLAN_FILENAME, f"pinned plan must be named {PLAN_FILENAME}")
    try:
        raw = path.read_bytes()
        text = raw.decode("utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise ContractFailure(f"cannot read pinned planner packet {path}: {exc}") from exc
    digest = sha256_bytes(raw)
    require(digest == PLAN_SHA256, "planner packet bytes differ from the fixed nine-outcome packet")
    lines = text.splitlines()
    for number, prefix in zip(PLAN_OUTCOME_LINES, PLAN_OUTCOME_PREFIXES):
        require(len(lines) >= number and lines[number - 1].startswith(prefix), f"planner outcome changed at line {number}")
    return digest, [sha256_bytes(lines[n - 1].encode("utf-8")) for n in PLAN_OUTCOME_LINES]


def verify_pinned_sources(root: Path) -> tuple[dict[str, dict[str, Any]], dict[str, str]]:
    docs: dict[str, dict[str, Any]] = {}
    hashes: dict[str, str] = {}
    for source_id, relative in SOURCE_IDS.items():
        doc, _, digest = _load_json(root / relative)
        require(digest == PINNED_SOURCE_HASHES[relative], f"frozen source hash changed: {relative}")
        docs[source_id] = doc
        hashes[relative] = digest
    return docs, hashes


def _validate_structure_impl(expected: dict[str, Any], matrix: dict[str, Any], require_nine_cases: bool) -> int:
    """Validate schema, exact fixed cases, nonzero assertions, and all pointers."""
    require(expected.get("schema_version") == 1 and matrix.get("schema_version") == 1, "schema_version must be 1")
    require(expected.get("product") == "WardAtlas", "unexpected product")
    require("candidate output is never" in expected.get("oracle_policy", "").lower(), "candidate output cannot be the oracle")
    require(expected.get("pinned_sources") == EXPECTED_SOURCE_MANIFEST, "planner/source hash manifest changed")
    require(matrix.get("oracle_file") == "tests/evidence-watch-expected-results.json", "matrix points at another oracle")
    require("candidate output never" in matrix.get("oracle_policy", "").lower(), "matrix must be candidate-independent")

    scope = expected.get("scope")
    require(isinstance(scope, dict), "oracle scope must be an object")
    for key, value in {
        "synthetic_only": True,
        "clinical_decisions": False,
        "candidate_output_is_oracle": False,
        "later_behavior_must_use_actual_falkordb": True,
        "watch_is_local_only": True,
        "external_writes": False,
        "watch_state_is_separate_from_confirmed_actions": True,
    }.items():
        require(scope.get(key) is value, f"scope {key} changed")

    assumptions = expected.get("contract_assumptions")
    require(isinstance(assumptions, dict), "contract assumptions are required")
    require(
        assumptions.get("duplicate_axis_input")
        == "A repeated query parameter for either effective_at or known_at is duplicate input and rejects even if values agree; probe each axis independently.",
        "duplicate-axis representation/decision changed",
    )
    require(bool(assumptions.get("watch_source_cutoff")), "watch source/evidence boundary is missing")

    cases, matrix_cases = expected.get("cases"), matrix.get("cases")
    require(isinstance(cases, list) and isinstance(matrix_cases, list), "expected and matrix cases must be arrays")
    ids = [row.get("id") for row in cases]
    matrix_ids = [row.get("id") for row in matrix_cases]
    require(all(isinstance(item, str) and item for item in ids + matrix_ids), "case IDs must be nonempty strings")
    require(len(ids) == len(set(ids)) and len(matrix_ids) == len(set(matrix_ids)), "case IDs must be unique")
    if require_nine_cases:
        require(len(cases) == 9 and len(matrix_cases) == 9, "--require-nine-cases requires exactly nine groups")
    require(tuple(ids) == REQUIRED_CASES, "the nine fixed evidence-watch outcomes or order changed")
    require(matrix_ids == ids, "matrix groups must map one-to-one in the same order")

    total = 0
    for index, (scenario, attack) in enumerate(zip(cases, matrix_cases)):
        line = PLAN_OUTCOME_LINES[index]
        case_id = REQUIRED_CASES[index]
        require(scenario.get("planner_ref") == f"{PLAN_FILENAME}:{line}", f"{case_id} planner reference changed")
        require(attack.get("scenario_id") == case_id, f"{case_id} maps to another scenario")
        require(attack.get("planner_line") == line and attack.get("planner_ref") == f"{PLAN_FILENAME}:{line}", f"{case_id} planner line mapping changed")
        require(isinstance(scenario.get("expected"), dict) and scenario["expected"], f"{case_id} has no fixed expected values")
        assertions = attack.get("pinned_assertions")
        require(isinstance(assertions, list) and assertions, f"{case_id} has no pinned assertions")
        assertion_ids = [row.get("id") for row in assertions if isinstance(row, dict)]
        require(len(assertion_ids) == len(assertions), f"{case_id} has malformed assertions")
        require(all(isinstance(item, str) and item for item in assertion_ids), f"{case_id} assertion IDs must be nonempty")
        require(len(assertion_ids) == len(set(assertion_ids)), f"{case_id} assertion IDs must be unique")
        count = attack.get("nonzero_assertion_count")
        require(type(count) is int and count > 0, f"{case_id} must have nonzero assertions")
        require(count == len(assertions), f"{case_id} assertion count differs from explicit mappings")
        require(bool(attack.get("attack_or_edge_case")) and bool(attack.get("setup")) and bool(attack.get("failure_detected")), f"{case_id} lacks adversarial semantics")
        for assertion in assertions:
            pointer = assertion.get("expected_pointer")
            _resolve_pointer(expected, pointer)
            mappings = assertion.get("source_mappings")
            require(isinstance(mappings, list), f"{assertion['id']} source_mappings must be an array")
            planner_only = assertion.get("planner_only")
            require(type(planner_only) is bool, f"{assertion['id']} planner_only must be boolean")
            require(bool(mappings) or planner_only, f"{assertion['id']} has neither source nor planner mapping")
            require(
                not any(mapping.get("relation") == "supports" for mapping in mappings) or planner_only,
                f"{assertion['id']} overstates a source support as complete verification",
            )
            for mapping in mappings:
                require(mapping.get("source_id") in SOURCE_IDS, f"{assertion['id']} cites an unknown source")
                require(mapping.get("source_matrix_id") in SOURCE_IDS, f"{assertion['id']} cites an unknown source matrix")
                require(mapping.get("relation") in {"exact", "supports"}, f"{assertion['id']} has an unsupported mapping relation")
                require(isinstance(mapping.get("source_case_id"), str) and isinstance(mapping.get("source_matrix_case_id"), str), f"{assertion['id']} source case mapping is incomplete")
        total += count
    require(total == REQUIRED_ASSERTION_TOTAL, f"fixed assertion total changed (expected {REQUIRED_ASSERTION_TOTAL}, got {total})")
    validate_fixed_values(expected)
    return total


def validate_structure(expected: dict[str, Any], matrix: dict[str, Any], require_nine_cases: bool) -> int:
    """Convert malformed/missing nested fields into a clear contract rejection."""
    try:
        return _validate_structure_impl(expected, matrix, require_nine_cases)
    except ContractFailure:
        raise
    except (KeyError, TypeError, IndexError) as exc:
        raise ContractFailure(f"malformed evidence-watch artifact: {exc}") from exc


def validate_fixed_values(expected: dict[str, Any]) -> None:
    """Reject status promotion, altered boundaries, and non-pinned temporal pairs."""
    cases = {row["id"]: row["expected"] for row in expected["cases"]}
    cold = cases[REQUIRED_CASES[0]]
    current = cold["saved_watch_on_cold_graph"]["current_result"]
    require(current.get("status") == "unknown" and current.get("role_path") == [] and current.get("role_id") is None, "cold graph must remain unknown with no guessed role")
    require(current.get("source_versions") == [] and current.get("review_candidates") == [], "cold graph must not invent sources or candidates")
    require(cold["no_confirmed_watch"] == {"watch_record_count": 0, "fabricated_watch": False}, "unconfirmed watch behavior changed")
    require(cold["saved_watch_on_cold_graph"]["forbidden_statuses"] == ["unchanged", "no owner", "safe"], "cold unknown promotion guards changed")

    preview = cases[REQUIRED_CASES[1]]
    require((preview["preview"].get("effective_at"), preview["preview"].get("known_at")) == ("2026-10-14T20:29:00Z", "2026-10-14T20:17:59Z"), "preview baseline pair changed")
    confirm = preview["confirmation"]
    require(confirm.get("explicit_in_app_confirmation_required") is True and confirm.get("watch_state_records_written") == 1, "watch confirmation must remain explicit and singular")
    require(confirm.get("watch_state_is_separate_from_confirmed_actions_json") is True, "watch state must remain separate from confirmed actions")
    require(confirm.get("graph_writes") == 0 and confirm.get("confirmed_actions_file_changed") is False and confirm.get("external_writes") == [], "watch confirmation crossed its local side-effect boundary")
    require(all(confirm.get(key) == 0 for key in ("acknowledgements_created", "handover_actions_created", "notifications_sent", "messages_sent", "calls_made", "tickets_created")), "watch confirmation must not perform operational actions")

    new_known = cases[REQUIRED_CASES[2]]
    saved = new_known["saved_baseline"]
    later = new_known["same_effective_later_knowledge"]
    require((saved["effective_at"], saved["known_at"]) == ("2026-10-14T20:29:00Z", "2026-10-14T20:17:59Z"), "saved R1 baseline pair changed")
    require(saved.get("role_id") == "ROLE-DOCK-DUTY" and saved.get("status") == "unchanged" and saved.get("review_candidates") == [], "saved baseline must be R1 and unchanged")
    require(saved.get("r2_identifier_title_effective_time_count_or_future_revision_wording_visible") is False, "saved baseline must not leak R2")
    require((later["effective_at"], later["known_at"]) == ("2026-10-14T20:29:00Z", "2026-10-14T20:19:00Z"), "new-knowledge pair changed")
    require(later.get("role_id") == "ROLE-DOCK-DUTY" and later.get("status") == "new-source-known", "known R2 must not change R1 before effective time")
    require(later.get("candidate_disposition") == "pending review; not declared-invalid artifacts" and later.get("HND-DOCK-2020_invalidated") is False, "pending candidates must not be promoted to invalid")
    require(later.get("review_candidates") == ["HND-DOCK-2020", "TKT-DOCK-88"], "known R2 review candidates changed")

    crossed = cases[REQUIRED_CASES[3]]
    early = crossed["early_knowledge_at_effective_boundary"]
    known = crossed["known_r2_at_effective_boundary"]
    require((early["effective_at"], early["known_at"]) == ("2026-10-14T20:30:00Z", "2026-10-14T20:17:59Z"), "crossed pair changed")
    require(early.get("status") == "unknown" and early.get("role_status") == "unknown" and early.get("role_path") == [], "expired R1 and unknown R2 must remain unknown")
    require(early.get("r2_identifier_title_effective_time_count_or_existence_hint_visible") is False, "crossed pair must not reveal R2")
    require((known["effective_at"], known["known_at"]) == ("2026-10-14T20:30:00Z", "2026-10-14T20:19:00Z"), "known R2 effective pair changed")
    require(known.get("role_id") == "ROLE-DOCK-SWING" and known.get("status_relative_to_saved_r1") == "changed", "effective, known R2 must be changed from saved R1")
    require(known.get("current_real_world_assignment_claimed") is False and known.get("action_or_work_item_change") is False, "watch comparison must not imply assignment or work-item change")
    require("valid at effective_at" in known.get("interpretation", "") and "not claim" in known.get("interpretation", ""), "valid/knowledge-time distinction was weakened")

    comparison = cases[REQUIRED_CASES[4]]
    require(comparison.get("statuses_by_pair") == EXPECTED_STATUS_PAIRS, "four exact watch statuses or time pairs changed")
    require(comparison.get("missing_required_edge_or_source_status") == "unknown", "missing source/edge cannot be promoted")
    require(comparison.get("forbidden_conclusions") == ["invalid artifact", "task reviewed", "contact inferred", "complete graph coverage", "risk/severity score"], "unsupported watch conclusions were changed")

    sensitivity = cases[REQUIRED_CASES[5]]
    require(sensitivity["remove_only_r2_supersedes_edge"].get("linkage_status") == "unknown" and sensitivity["remove_only_r2_ownership_edge"].get("role_status") == "unknown", "missing edges must remain unknown")
    require(sensitivity["delete_only_r2_source_evidence"] == {"r2_source_metadata_visible": False, "future_role_exposed": False, "dependent_live_status": "unknown"}, "source deletion semantics changed")
    require(sensitivity.get("reseed", {}).get("restores_exact_baseline") is True, "reseed must restore the exact baseline")

    acl = cases[REQUIRED_CASES[6]]
    require(acl.get("viewer_scope") == "operations" and len(acl.get("axis_pairs", [])) == 4, "ACL scope or axis coverage changed")
    require(acl.get("full_watch_api_stored_status_counts_errors_and_accessible_desktop_mobile_dom_unchanged_before_after") is True, "watch ACL view must remain invariant")
    require(acl.get("hidden_identifier_title_fact_count_existence_relationship_path_or_dependent_paraphrase_disclosed") is False, "hidden-source disclosure expectation weakened")

    time = cases[REQUIRED_CASES[7]]
    require(time.get("canonical_pair") == {"effective_at": "2026-10-14T20:29:00Z", "known_at": "2026-10-14T20:19:00Z"}, "canonical bitemporal pair changed")
    require(time.get("offset_pair") == {"effective_at": "2026-10-14T22:29:00+02:00", "known_at": "2026-10-14T22:19:00+02:00"}, "offset probe changed")
    require(time.get("normalized_pair") == time["canonical_pair"], "offset pair must normalize to canonical UTC")
    duplicate = time.get("duplicate_axis_probes")
    require(isinstance(duplicate, list) and [row.get("query_parameter") for row in duplicate] == ["effective_at", "known_at"], "both duplicate axes must be probed")
    require(all(len(row.get("values", [])) == 2 and row.get("rejected_before_mutation") is True for row in duplicate), "duplicate-axis probes must reject before mutation")
    require(time.get("invalid_axes_rejected_before_graph_output_or_local_state_mutation") is True and time.get("naive_local_time_fallback") is False, "invalid/naive axis behavior changed")
    require(time.get("refresh_graph_writes") == 0 and time.get("refresh_watch_state_writes") == 0, "refresh must be read-only")
    require(time.get("watch_settings_changed") is False and time.get("graph_or_query_definitions_changed") is False, "refresh scope was expanded")

    lifecycle = cases[REQUIRED_CASES[8]]
    require(lifecycle.get("confirmation_token_is_idempotent") is True, "watch confirmation token must be idempotent")
    reconnect = lifecycle["after_restart_or_reconnect"]
    require(reconnect.get("saved_baseline_preserved_exactly") is True and reconnect.get("live_result_recomputed_from_actual_falkordb") is True, "reconnect must preserve baseline and recompute from actual FalkorDB")
    require(reconnect.get("result_matches") == "dock-bitemporal-known-r2-pending-review", "reconnect result must match fixed known-R2 source result")
    require(lifecycle.get("repeat_seed_duplicate_nodes") == 0 and lifecycle.get("repeat_seed_duplicate_relationships") == 0, "repeat seed must be idempotent")
    require(lifecycle.get("api_and_desktop_mobile_browser_agree") is True, "API and browser must agree")
    require(lifecycle.get("cleanup_deletes_only_task_owned_graph_process_and_watch_state") is True, "cleanup must remain task-owned")


def validate_source_mappings(
    expected: dict[str, Any], matrix: dict[str, Any], sources: dict[str, dict[str, Any]]
) -> tuple[int, int, int]:
    exact_count = 0
    support_count = 0
    mapping_count = 0
    source_expected_id = {"dock-expected": "dock-matrix", "shift-expected": "shift-matrix"}
    for case in matrix["cases"]:
        for assertion in case["pinned_assertions"]:
            expected_value = _resolve_pointer(expected, assertion["expected_pointer"])
            for mapping in assertion["source_mappings"]:
                mapping_count += 1
                source_id = mapping["source_id"]
                matrix_id = mapping["source_matrix_id"]
                require(matrix_id == source_expected_id[source_id], f"{assertion['id']} pairs an oracle with the wrong matrix")
                source_doc, source_matrix = sources[source_id], sources[matrix_id]
                source_case = _find_case(source_doc.get("cases", []), mapping["source_case_id"], source_id)
                source_matrix_case = _find_case(source_matrix.get("cases", []), mapping["source_matrix_case_id"], matrix_id)
                source_value = _resolve_pointer(source_doc, mapping["source_pointer"])
                require(source_case.get("expected") is not None, f"{assertion['id']} cites a source case without expected values")
                require(_resolve_pointer(source_doc, mapping["source_pointer"]) is not None or mapping["relation"] == "exact", f"{assertion['id']} source value is unresolved")
                if matrix_id == "dock-matrix":
                    pinned = source_matrix_case.get("pinned_assertions")
                    matches = [row for row in pinned if isinstance(row, dict) and row.get("id") == mapping.get("source_assertion_id")]
                    require(len(matches) == 1, f"{assertion['id']} cites a missing dock matrix assertion")
                    require(matches[0].get("expected_pointer") == mapping["source_pointer"], f"{assertion['id']} source matrix pointer does not match its source field")
                    matrix_value: Any = matches[0]
                else:
                    text = mapping.get("source_assertion_text")
                    require(isinstance(text, str) and text in source_matrix_case.get("pinned_assertions", []), f"{assertion['id']} cites missing shift-board matrix assertion text")
                    matrix_value = text
                if mapping["relation"] == "exact":
                    require(expected_value == source_value, f"{assertion['id']} exact expected value differs from its accepted source literal")
                    exact_count += 1
                else:
                    require(mapping.get("relation") == "supports", f"{assertion['id']} has an unsupported source relation")
                    require(assertion.get("planner_only") is True, f"{assertion['id']} overstates supporting evidence")
                    support_count += 1
                # The value is returned for receipt creation/validation, not exposed to product behavior.
                _ = matrix_value
    return mapping_count, exact_count, support_count


def _mapping_without_hashes(mapping: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in mapping.items() if key not in {"source_value_sha256", "matrix_value_sha256"}}


def validate_no_hidden_source_leakage(
    documents: list[dict[str, Any]], sources: dict[str, dict[str, Any]]
) -> None:
    shift = sources["shift-expected"]
    cases = {row["id"]: row["expected"] for row in shift["cases"]}
    denied = cases["denied-source-nondisclosure"]
    literals = list(denied.get("dock_restricted_literals", []))
    conveyor = denied.get("conveyor_restricted_fixture", {})
    literals.extend(conveyor.get(key) for key in ("event_id", "source_id", "source_title", "fact_text"))
    haystack = "\n".join(json.dumps(doc, ensure_ascii=False, sort_keys=True) for doc in documents)
    for literal in literals:
        if isinstance(literal, str) and literal:
            require(literal not in haystack, "hidden-source literal leaked into a candidate-independent watch artifact")


def validate_review_receipt_structure(
    receipt: dict[str, Any],
    expected: dict[str, Any],
    matrix: dict[str, Any],
    sources: dict[str, dict[str, Any]],
    planner_line_hashes: list[str],
    frozen_hashes: dict[str, str],
) -> None:
    require(receipt.get("schema_version") == 1, "review receipt schema_version must be 1")
    reviewer = receipt.get("reviewer")
    require(isinstance(reviewer, dict), "reviewer identity is required")
    require(reviewer.get("role") == "independent source reviewer", "receipt reviewer role changed")
    require(reviewer.get("reviewer_id") == "opencode-subagent-ses_eb462a828ffeunty1oOo4sBy1o", "reviewer identity changed")
    require(reviewer.get("different_from_oracle_author") is True, "reviewer must not be the oracle author")
    require(reviewer.get("same_model_as_builder") is True, "model disclosure is required")
    require(reviewer.get("independence_basis") == "A separate reviewer inspected every cited immutable source value and matrix assertion, compared the fixed packet literals before behavior work, and recorded per-assertion mappings/hashes; session novelty alone was not treated as independence.", "reviewer independence basis changed")
    require(receipt.get("reviewed_before_candidate_behavior") is True and receipt.get("candidate_behavior_inspected") is False, "review must precede and exclude candidate behavior")
    require(receipt.get("review_result") == "PASS", "independent source review did not pass")
    require(receipt.get("frozen_hashes") == frozen_hashes, "review receipt frozen hashes differ from artifacts")
    require(receipt.get("comparison_counts") == {"groups": 9, "assertions": REQUIRED_ASSERTION_TOTAL, "source_mappings": 69, "exact_value_matches": 47, "support_mappings": 22}, "review receipt comparison counts changed")

    limitations = receipt.get("source_limitations")
    require(isinstance(limitations, list) and len(limitations) == 6 and all(isinstance(item, str) and item for item in limitations), "receipt must disclose unsupported watch-only source areas")
    reviews = receipt.get("case_reviews")
    require(isinstance(reviews, list) and [row.get("case_id") for row in reviews] == list(REQUIRED_CASES), "receipt must review all nine cases in fixed order")
    expected_by_id = {row["id"]: row for row in expected["cases"]}
    matrix_by_id = {row["id"]: row for row in matrix["cases"]}
    for index, review in enumerate(reviews):
        case_id = REQUIRED_CASES[index]
        line = PLAN_OUTCOME_LINES[index]
        require(review.get("plan_line") == line and review.get("result") == "PASS", f"{case_id} review result/line changed")
        require(review.get("plan_line_sha256") == planner_line_hashes[index], f"{case_id} planner-line hash changed")
        assertions = review.get("assertion_mappings")
        source_assertions = matrix_by_id[case_id]["pinned_assertions"]
        require(isinstance(assertions, list), f"{case_id} receipt assertion mappings must be an array")
        require([row.get("assertion_id") for row in assertions] == [row["id"] for row in source_assertions], f"{case_id} receipt misses or reorders assertion mappings")
        require(bool(review.get("comparison_summary")), f"{case_id} has no case-by-case comparison record")
        for assertion_review, assertion in zip(assertions, source_assertions):
            require(assertion_review.get("expected_pointer") == assertion["expected_pointer"], f"{case_id}/{assertion['id']} expected pointer differs from matrix")
            expected_value = _resolve_pointer(expected, assertion["expected_pointer"])
            require(assertion_review.get("expected_value_sha256") == canonical_sha256(expected_value), f"{case_id}/{assertion['id']} expected-value review hash changed")
            require(assertion_review.get("planner_line_sha256") == planner_line_hashes[index], f"{case_id}/{assertion['id']} planner-line review hash changed")
            reviewed_mappings = assertion_review.get("reviewed_source_mappings")
            mappings = assertion.get("source_mappings", [])
            require(isinstance(reviewed_mappings, list) and len(reviewed_mappings) == len(mappings), f"{case_id}/{assertion['id']} source comparison count changed")
            for reviewed, mapping in zip(reviewed_mappings, mappings):
                require(_mapping_without_hashes(reviewed) == mapping, f"{case_id}/{assertion['id']} source/assertion mapping differs from the reviewed matrix")
                source_doc = sources[mapping["source_id"]]
                source_value = _resolve_pointer(source_doc, mapping["source_pointer"])
                require(reviewed.get("source_value_sha256") == canonical_sha256(source_value), f"{case_id}/{assertion['id']} reviewed source literal hash changed")
                matrix_doc = sources[mapping["source_matrix_id"]]
                source_matrix_case = _find_case(matrix_doc["cases"], mapping["source_matrix_case_id"], mapping["source_matrix_id"])
                if mapping["source_matrix_id"] == "dock-matrix":
                    matrix_value = next(row for row in source_matrix_case["pinned_assertions"] if row["id"] == mapping["source_assertion_id"])
                else:
                    matrix_value = mapping["source_assertion_text"]
                require(reviewed.get("matrix_value_sha256") == canonical_sha256(matrix_value), f"{case_id}/{assertion['id']} reviewed matrix assertion hash changed")


def validate_artifacts(
    expected: dict[str, Any],
    matrix: dict[str, Any],
    receipt: dict[str, Any],
    expected_sha256: str,
    matrix_sha256: str,
    receipt_sha256: str,
    root: Path,
    pinned_plan: Path,
    require_nine_cases: bool,
) -> tuple[int, dict[str, str]]:
    assertion_count = validate_structure(expected, matrix, require_nine_cases)
    require(expected_sha256 == EXPECTED_ARTIFACT_SHA256, "evidence-watch expected-results bytes differ from their fixed hash")
    require(matrix_sha256 == MATRIX_ARTIFACT_SHA256, "evidence-watch adversarial-matrix bytes differ from their fixed hash")
    require(REVIEW_RECEIPT_SHA256 != "__REVIEW_RECEIPT_SHA256__", "review receipt hash has not been frozen in the validator")
    require(receipt_sha256 == REVIEW_RECEIPT_SHA256, "independent-review receipt bytes differ from their fixed hash")
    plan_digest, planner_line_hashes = verify_pinned_plan(pinned_plan)
    sources, source_hashes = verify_pinned_sources(root)
    require(expected.get("pinned_sources") == EXPECTED_SOURCE_MANIFEST, "new oracle source manifest changed")
    mappings, exact_count, support_count = validate_source_mappings(expected, matrix, sources)
    require((mappings, exact_count, support_count) == (69, 47, 22), "fixed source-mapping census changed")
    frozen_hashes = {"planner": plan_digest, **source_hashes, "tests/evidence-watch-expected-results.json": expected_sha256, "tests/evidence-watch-adversarial-matrix.json": matrix_sha256}
    validate_review_receipt_structure(receipt, expected, matrix, sources, planner_line_hashes, frozen_hashes)
    validate_no_hidden_source_leakage([expected, matrix, receipt], sources)
    return assertion_count, frozen_hashes


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expected", required=True, type=Path)
    parser.add_argument("--matrix", required=True, type=Path)
    parser.add_argument("--pinned-plan", required=True, type=Path)
    parser.add_argument("--require-nine-cases", action="store_true")
    parser.add_argument("--check-frozen-oracle-hashes", action="store_true")
    parser.add_argument("--review-receipt", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        expected, _, expected_hash = _load_json(args.expected)
        matrix, _, matrix_hash = _load_json(args.matrix)
        receipt, _, receipt_hash = _load_json(args.review_receipt)
        count, hashes = validate_artifacts(
            expected,
            matrix,
            receipt,
            expected_hash,
            matrix_hash,
            receipt_hash,
            root=args.expected.resolve().parents[1],
            pinned_plan=args.pinned_plan,
            require_nine_cases=args.require_nine_cases,
        )
    except ContractFailure as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1
    print(f"PASS: 9 evidence-watch groups; {count} nonzero assertions; independent source review receipt accepted.")
    print(f"Frozen hashes checked: {len(hashes)} planner/source/oracle artifacts.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

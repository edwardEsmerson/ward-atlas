#!/usr/bin/env python3
"""Validate the candidate-independent source-observation branch oracle."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
EXPECTED_GROUP_IDS = (
    "cold-empty-branch-lookup",
    "mainline-at-16-05",
    "branch-at-16-05-suppression",
    "branch-at-16-06-restoration",
    "mainline-isolation",
    "missing-edge-no-fallback-and-reseed",
    "decoy-and-restricted-invariance",
    "repeat-reconnect-read-only-desktop-mobile-cleanup",
)
FROZEN_ORACLE_PATHS = {
    "contracts/decision-knowledge-timeline/expected-results.json",
    "contracts/decision-knowledge-timeline/adversarial-matrix.json",
    "tests/sop-impact-expected-results.json",
    "tests/sop-impact-adversarial-matrix.json",
    "tests/decision-provenance-expected-results.json",
    "tests/decision-provenance-adversarial-matrix.json",
}
BRANCH_ASSUMPTION = {
    "label": "Hypothetical observation-time branch",
    "assumed_observed_at": "2026-10-14T16:06:00Z",
    "actual_source_observed_at": "2026-10-14T16:04:00Z",
    "source_fact_modified": False,
    "scenario_not_source_truth": True,
}
REQUIRED_REVIEW_CRITERIA = frozenset({
    "full_mainline_at_16_04_utc_and_source_fact",
    "branch_16_05_suppression_and_open_unresolved_record",
    "branch_16_06_restoration",
    "mainline_immutability",
    "either_missing_branch_edge_unknown_without_fallback",
    "decoy_and_restricted_full_view_invariance",
    "repeat_reconnect_read_only_and_owned_cleanup",
    "desktop_mobile_api_and_keyboard_agreement",
    "scenario_branch_override_evidence_labels",
})
GENERIC_MISSING_BRANCH_RESULT = {
    "branch_state": "unknown",
    "claim_context_state": "incomplete/unknown",
    "projection": {
        "known_at": "2026-10-14T16:05:00Z",
        "claim_context_state": "incomplete/unknown",
        "claims": [],
        "conflict": None,
        "decision_record": None,
        "decision_considers_edges": [],
        "generic_unknown": True,
        "negative_conclusion_asserted": False,
        "current_owner_or_procedure_selected": False,
        "contact_or_action_supplied": False,
    },
    "mainline_fallback_used": False,
    "source_or_branch_identity_exposed": False,
}


class ContractError(ValueError):
    """Raised when a frozen artifact or its source bindings do not validate."""


def _fail(message: str) -> None:
    raise ContractError(message)


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        _fail(f"cannot read JSON {path}: {exc}")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _source_path(root: Path, path: str) -> Path:
    candidate = Path(path)
    return candidate if candidate.is_absolute() else root / candidate


def _json_pointer(document: Any, pointer: str) -> Any:
    if pointer == "":
        return document
    if not isinstance(pointer, str) or not pointer.startswith("/"):
        _fail(f"expected a valid absolute JSON Pointer, got {pointer!r}")
    current = document
    for raw_token in pointer[1:].split("/"):
        token = raw_token.replace("~1", "/").replace("~0", "~")
        if isinstance(current, dict):
            if token not in current:
                _fail(f"JSON Pointer {pointer!r} does not resolve (missing {token!r})")
            current = current[token]
        elif isinstance(current, list):
            if not token.isdecimal() or (len(token) > 1 and token.startswith("0")):
                _fail(f"JSON Pointer {pointer!r} has invalid array index {token!r}")
            index = int(token)
            if index >= len(current):
                _fail(f"JSON Pointer {pointer!r} has out-of-range array index {index}")
            current = current[index]
        else:
            _fail(f"JSON Pointer {pointer!r} walks through a scalar")
    return current


def _group_map(expected: dict[str, Any]) -> dict[str, dict[str, Any]]:
    groups = expected.get("groups")
    if not isinstance(groups, list):
        _fail("expected-results groups must be an array")
    result: dict[str, dict[str, Any]] = {}
    for group in groups:
        if not isinstance(group, dict) or not isinstance(group.get("id"), str):
            _fail("every expected-result group needs a string id")
        if group["id"] in result:
            _fail(f"duplicate expected-result group id {group['id']!r}")
        result[group["id"]] = group
    return result


def _candidate_derived_path(value: Any, path: str = "$") -> str | None:
    """Reject candidate-output markers in data, while allowing the policy text."""
    if isinstance(value, dict):
        for key, nested in value.items():
            normalized = re.sub(r"[^a-z0-9]+", "_", str(key).lower()).strip("_")
            if normalized in {
                "candidate_output",
                "candidate_response",
                "candidate_derived",
                "derived_from_candidate",
                "candidate_output_source",
                "candidate_output_value",
            }:
                return f"{path}.{key}"
            found = _candidate_derived_path(nested, f"{path}.{key}")
            if found:
                return found
    elif isinstance(value, list):
        for index, nested in enumerate(value):
            found = _candidate_derived_path(nested, f"{path}[{index}]")
            if found:
                return found
    elif isinstance(value, str):
        lowered = value.lower()
        if any(
            marker in lowered
            for marker in (
                "candidate-derived expectation",
                "derived from candidate output",
                "candidate response supplied",
                "candidate output as oracle",
            )
        ):
            return path
    return None


def _source_case(oracle: dict[str, Any], case_id: str) -> dict[str, Any]:
    for case in oracle.get("cases", []):
        if case.get("id") == case_id:
            return case
    _fail(f"accepted source oracle lacks case {case_id!r}")


def _timeline_sources(root: Path) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    timeline = _read_json(root / "contracts/decision-knowledge-timeline/expected-results.json")
    sop = _read_json(root / "tests/sop-impact-expected-results.json")
    provenance = _read_json(root / "tests/decision-provenance-expected-results.json")
    return timeline, sop, provenance


def validate_frozen_hash_entries(
    entries: list[dict[str, Any]], root: Path, *, exact_paths: set[str] | None = None
) -> None:
    seen: set[str] = set()
    for entry in entries:
        if not isinstance(entry, dict):
            _fail("frozen source entries must be objects")
        path = entry.get("path")
        expected_hash = entry.get("sha256")
        if not isinstance(path, str) or not re.fullmatch(r"[0-9a-f]{64}", str(expected_hash)):
            _fail(f"invalid frozen-source path or SHA-256: {entry!r}")
        normalized = path if Path(path).is_absolute() else path.replace("\\", "/")
        if normalized in seen:
            _fail(f"duplicate frozen source path {path!r}")
        seen.add(normalized)
        source_path = _source_path(root, path)
        if not source_path.is_file():
            _fail(f"frozen source is missing: {source_path}")
        actual_hash = sha256_file(source_path)
        if actual_hash != expected_hash:
            _fail(f"frozen source hash changed: {path} expected {expected_hash}, got {actual_hash}")
    if exact_paths is not None and seen != exact_paths:
        _fail(f"frozen source inventory mismatch: expected {sorted(exact_paths)}, got {sorted(seen)}")


def validate_required_outcomes(expected: dict[str, Any], root: Path) -> None:
    groups = _group_map(expected)
    cold = groups["cold-empty-branch-lookup"]["expected"]
    if cold.get("branch_state") != "unknown" or cold.get("mainline_fallback_used") is not False:
        _fail("cold branch lookup must be generic unknown and must not fall back to mainline")
    if cold.get("exposed_ids") != [] or cold.get("exposed_titles") != [] or cold.get("exposed_count_fields") != []:
        _fail("cold branch lookup must expose no identifiers, titles, or counts")
    if cold.get("existence_hint_exposed") is not False or cold.get("no_decision_or_conflict_absence_concluded") is not False:
        _fail("cold branch lookup must not leak existence or infer absence")

    timeline, sop, provenance = _timeline_sources(root)
    source_mainline_1604 = _source_case(timeline, "v3-and-graph-derived-conflict")["expected"]["projection"]
    source_mainline_1605 = _source_case(timeline, "record-source-boundary")["expected"]["projection"]
    source_branch = _source_case(timeline, "v3-observation-time-shift")["expected"]
    if groups["mainline-at-16-05"]["expected"].get("projection") != source_mainline_1605:
        _fail("mainline 16:05 full projection differs from the accepted timeline oracle")
    mainline_1604 = groups["mainline-isolation"]["expected"].get("mainline_at_16_04")
    if mainline_1604 != source_mainline_1604:
        _fail("mainline 16:04 full projection differs from the accepted timeline oracle")
    isolation = groups["mainline-isolation"]["expected"]
    if isolation.get("mainline_at_16_05") != source_mainline_1605:
        _fail("isolated mainline 16:05 snapshot differs from accepted timeline oracle")
    source_mainline_1606 = dict(source_mainline_1605)
    source_mainline_1606["known_at"] = "2026-10-14T16:06:00Z"
    if isolation.get("mainline_at_16_06") != source_mainline_1606:
        _fail("isolated mainline 16:06 snapshot must preserve all 16:05 facts and change only cutoff")

    mainline_1604_v3 = next((claim for claim in mainline_1604["claims"] if claim.get("version_id") == "SOP-OPS-9-V3"), None)
    mainline_1605_v3 = next((claim for claim in source_mainline_1605["claims"] if claim.get("version_id") == "SOP-OPS-9-V3"), None)
    mainline_1606_v3 = next((claim for claim in source_mainline_1606["claims"] if claim.get("version_id") == "SOP-OPS-9-V3"), None)
    if not all(
        claim and claim.get("source", {}).get("observed_at") == "2026-10-14T16:04:00Z"
        for claim in (mainline_1604_v3, mainline_1605_v3, mainline_1606_v3)
    ):
        _fail("mainline V3 source fact must remain observed at 16:04Z at all pinned cutoffs")
    if mainline_1604.get("known_at") != "2026-10-14T16:04:00Z" or mainline_1604.get("decision_record") is not None:
        _fail("the 16:04Z mainline must precede the separately evidenced decision record")

    branch_1605 = groups["branch-at-16-05-suppression"]["expected"]
    if branch_1605.get("projection") != source_branch.get("at_16_05"):
        _fail("branch 16:05 full projection differs from the accepted source-time mutation oracle")
    if branch_1605.get("branch_assumption") != BRANCH_ASSUMPTION:
        _fail("branch 16:05 must explicitly label the hypothetical override without changing source truth")
    branch_projection_1605 = branch_1605["projection"]
    if [claim.get("version_id") for claim in branch_projection_1605.get("claims", [])] != ["SOP-OPS-9-V2"]:
        _fail("branch 16:05 must retain only the independently evidenced V2 claim")
    if branch_projection_1605.get("claim_context_state") != "incomplete/unknown" or branch_projection_1605.get("conflict") is not None:
        _fail("branch 16:05 must be incomplete/unknown without a V2→V3 conflict")
    decision = branch_projection_1605.get("decision_record")
    if not isinstance(decision, dict) or decision.get("recorded_state") != "open-unresolved":
        _fail("branch 16:05 must retain the independently evidenced open-unresolved record")
    expected_v2_considers = [{"from": "DEC-OPS-9-REVIEW-01", "relationship": "CONSIDERS", "to": "SOP-OPS-9-V2"}]
    if branch_projection_1605.get("decision_considers_edges") != expected_v2_considers:
        _fail("branch 16:05 must retain only the valid V2 CONSIDERS edge")
    if not branch_1605.get("forbidden_v3_literals") or branch_1605.get("v3_count_or_path_exposed") is not False:
        _fail("branch 16:05 must pin V3 non-disclosure and no count/path signal")
    if branch_1605.get("v2_selected_or_conflict_resolved") is not False:
        _fail("branch 16:05 must not select V2 or resolve the conflict")

    branch_1606 = groups["branch-at-16-06-restoration"]["expected"]
    if branch_1606.get("projection") != source_branch.get("at_16_06"):
        _fail("branch 16:06 full projection differs from the accepted source-time mutation oracle")
    if branch_1606.get("branch_assumption") != BRANCH_ASSUMPTION:
        _fail("branch 16:06 must preserve the explicit hypothetical scenario metadata")
    branch_projection_1606 = branch_1606["projection"]
    branch_v3 = next((claim for claim in branch_projection_1606.get("claims", []) if claim.get("version_id") == "SOP-OPS-9-V3"), None)
    if not branch_v3 or branch_v3.get("source", {}).get("observed_at") != "2026-10-14T16:06:00Z":
        _fail("branch 16:06 must restore V3 only under its hypothetical 16:06Z observation time")
    if branch_projection_1606.get("conflict", {}).get("edge", {}).get("status") != "unresolved":
        _fail("branch 16:06 must restore the unresolved graph-derived conflict")
    if branch_1606.get("actual_source_observed_at") != "2026-10-14T16:04:00Z" or branch_1606.get("scenario_not_source_truth") is not True:
        _fail("branch 16:06 must not overwrite actual source observation time")

    if isolation.get("v3_source_observed_at") != "2026-10-14T16:04:00Z":
        _fail("mainline isolation must preserve the actual V3 16:04Z observation")
    if any(isolation.get(key) is not True for key in (
        "mainline_16_05_unchanged_after_branch_query",
        "mainline_16_06_unchanged_after_branch_query",
        "base_timeline_16_05_and_16_06_unchanged",
        "stored_source_fact_unchanged",
    )):
        _fail("branch reads must leave both mainline snapshots, base endpoint, and stored source fact unchanged")

    edge_case = groups["missing-edge-no-fallback-and-reseed"]["expected"]
    if edge_case.get("branch_node_labels") != ["ScenarioBranch", "ObservationOverride", "Evidence"]:
        _fail("branch graph must pin the ScenarioBranch, ObservationOverride, and Evidence labels")
    expected_edges = [
        {"from": "BR-SOP-V3-LATE-OBS", "relationship": "HAS_HYPOTHETICAL_OBSERVATION", "to": "OBS-SOP-V3-LATE-OBS"},
        {"from": "OBS-SOP-V3-LATE-OBS", "relationship": "FOR_SOURCE", "to": "SRC-SOP-CURRENT-V3"},
    ]
    if edge_case.get("required_branch_edges") != expected_edges:
        _fail("branch fixture must use the exact two explicit pinned relationships")
    for key in ("missing_branch_to_override_edge_result", "missing_override_to_source_edge_result"):
        outcome = edge_case.get(key, {})
        if outcome != GENERIC_MISSING_BRANCH_RESULT:
            _fail(f"{key} must be the exact generic unknown projection with no mainline facts or fallback")
    if edge_case.get("mainline_fallback_used") is not False or edge_case.get("only_owned_edge_removed") is not True:
        _fail("missing branch-edge checks must never fall back and may remove only the owned edge")

    acl = groups["decoy-and-restricted-invariance"]["expected"]
    clean_view = acl.get("clean_full_operations_projection")
    if acl.get("after_disconnected_decoy_projection") != clean_view:
        _fail("disconnected decoy must preserve the complete operations projection")
    if acl.get("after_connected_restricted_injection_projection") != clean_view:
        _fail("restricted source injection must preserve the complete operations projection")
    if any(acl.get(key) is not True for key in (
        "decoy_projection_semantically_identical",
        "full_operations_projection_invariant",
        "browser_text_and_accessibility_tree_invariant",
    )) or acl.get("restricted_literals_disclosed") != [] or acl.get("hidden_count_or_existence_disclosed") is not False:
        _fail("decoy/restricted full-view, browser, and non-disclosure outcomes are incomplete")

    repeat = groups["repeat-reconnect-read-only-desktop-mobile-cleanup"]["expected"]
    if repeat.get("seed_runs") != 2 or repeat.get("duplicate_node_delta_after_second_seed") != 0 or repeat.get("duplicate_relationship_delta_after_second_seed") != 0:
        _fail("branch seeding must be idempotent with two runs and zero duplicate node/edge delta")
    if repeat.get("fresh_falkordb_client_required") is not True:
        _fail("branch reconnect expectations must require a fresh FalkorDB client")
    expected_writes = {"graph_nodes": 0, "graph_relationships": 0, "local_actions": 0, "files": 0, "external_writes": 0}
    if repeat.get("query_and_browser_write_counts") != expected_writes:
        _fail("branch and browser read paths must pin zero writes")
    if repeat.get("desktop_mobile_api_semantically_equal") is not True or repeat.get("keyboard_selection_api_semantically_equal") is not True:
        _fail("desktop, mobile, keyboard selection, and API agreement must all be pinned")
    if repeat.get("cleanup_only_owned_graph_state_and_processes") is not True:
        _fail("cleanup must be restricted to task-owned graph/state/processes")

    # Cross-check the separate accepted SOP and decision outcomes used to anchor this packet.
    sop_conflict = _source_case(sop, "sop-impact-conflict-provenance")["expected"]["conflict_edge"]
    provenance_baseline = _source_case(provenance, "exact-one-record-baseline")["expected"]
    if groups["mainline-at-16-05"]["expected"].get("conflict_edge") != sop_conflict:
        _fail("mainline conflict edge differs from the accepted SOP-impact oracle")
    if provenance_baseline["decision_record"]["recorded_state"] != "open-unresolved":
        _fail("accepted decision-provenance source no longer pins open-unresolved")


def validate_documents(expected: dict[str, Any], matrix: dict[str, Any], root: Path = ROOT) -> int:
    if expected.get("schema_version") != 1 or matrix.get("schema_version") != 1:
        _fail("expected-results and matrix schema_version must be 1")
    if expected.get("candidate_output_is_oracle") is not False or matrix.get("candidate_output_is_oracle") is not False:
        _fail("candidate output must be explicitly disallowed as an oracle")
    for document, label in ((expected, "expected-results"), (matrix, "matrix")):
        marker = _candidate_derived_path(document)
        if marker:
            _fail(f"candidate-derived expectation marker found in {label} at {marker}")
    expected_groups = expected.get("groups")
    matrix_groups = matrix.get("groups")
    if not isinstance(expected_groups, list) or not isinstance(matrix_groups, list):
        _fail("expected-results and matrix groups must be arrays")
    expected_ids = [group.get("id") if isinstance(group, dict) else None for group in expected_groups]
    matrix_ids = [group.get("group_id") if isinstance(group, dict) else None for group in matrix_groups]
    if tuple(expected_ids) != EXPECTED_GROUP_IDS or tuple(matrix_ids) != EXPECTED_GROUP_IDS:
        _fail(f"exactly the eight pinned groups are required, got expected={expected_ids}, matrix={matrix_ids}")
    if matrix.get("oracle_file") != "contracts/decision-source-branch/expected-results.json":
        _fail("matrix oracle_file must point to the frozen branch expected-results artifact")

    total = 0
    assertion_ids: set[str] = set()
    for index, (expected_group, matrix_group) in enumerate(zip(expected_groups, matrix_groups)):
        if expected_group.get("planner_refs") != matrix_group.get("planner_refs"):
            _fail(f"planner references differ for group {EXPECTED_GROUP_IDS[index]}")
        if expected_group.get("source_oracle_refs") != matrix_group.get("source_oracle_refs"):
            _fail(f"source-oracle references differ for group {EXPECTED_GROUP_IDS[index]}")
        assertions = matrix_group.get("pinned_assertions")
        if not isinstance(assertions, list):
            _fail(f"group {EXPECTED_GROUP_IDS[index]} pinned_assertions must be an array")
        declared = matrix_group.get("nonzero_assertion_count")
        if declared != len(assertions) or len(assertions) == 0:
            _fail(f"group {EXPECTED_GROUP_IDS[index]} assertion count is zero or incorrect")
        total += len(assertions)
        for assertion in assertions:
            if not isinstance(assertion, dict):
                _fail(f"group {EXPECTED_GROUP_IDS[index]} contains a malformed assertion")
            assertion_id = assertion.get("id")
            if not isinstance(assertion_id, str) or not assertion_id or assertion_id in assertion_ids:
                _fail(f"assertion id is absent or duplicated: {assertion_id!r}")
            assertion_ids.add(assertion_id)
            if not isinstance(assertion.get("assertion"), str) or not assertion["assertion"].strip():
                _fail(f"assertion {assertion_id!r} needs a descriptive label")
            pointer = assertion.get("expected_pointer")
            value = _json_pointer(expected, pointer)
            # False, null, empty arrays and empty objects are meaningful literal negative outcomes.
            if isinstance(value, float) and (value != value or value in (float("inf"), float("-inf"))):
                _fail(f"assertion {assertion_id!r} does not resolve to a JSON literal")
            prefix = f"/groups/{index}/expected/"
            if not isinstance(pointer, str) or not pointer.startswith(prefix):
                _fail(f"assertion {assertion_id!r} must map into its own group's expected outcomes")
    if total < 48:
        _fail(f"expected at least 48 nonzero literal assertions, found {total}")
    validate_required_outcomes(expected, root)
    return total


def validate_hash_manifest(
    expected: dict[str, Any], manifest: dict[str, Any], root: Path, pinned_plan: Path
) -> None:
    if manifest.get("schema_version") != 1 or manifest.get("frozen_before_candidate_behavior") is not True:
        _fail("frozen-source manifest must be schema 1 and explicitly frozen before candidate behavior")
    if manifest.get("candidate_output_used") is not False:
        _fail("frozen-source manifest may not use candidate output")
    entries = manifest.get("sources")
    if not isinstance(entries, list):
        _fail("frozen-source manifest sources must be an array")
    packet = [entry for entry in entries if entry.get("kind") == "approved-planner-packet"]
    if len(packet) != 1:
        _fail("frozen-source manifest must contain exactly one approved planner packet")
    if Path(packet[0]["path"]).resolve() != pinned_plan.resolve():
        _fail("--pinned-plan path does not match the frozen planner path")
    source_paths = {entry.get("path") for entry in entries if entry.get("kind") != "approved-planner-packet"}
    if source_paths != FROZEN_ORACLE_PATHS:
        _fail(f"frozen accepted oracle/matrix inventory changed: {sorted(source_paths)}")
    validate_frozen_hash_entries(entries, root)
    pinned = expected.get("pinned_sources")
    if not isinstance(pinned, list):
        _fail("expected-results must bind the frozen planner and accepted source hashes")
    projection = [{"id": x.get("id"), "path": x.get("path"), "sha256": x.get("sha256")} for x in entries]
    if pinned != projection:
        _fail("expected-results pinned_sources do not exactly match frozen-sources.json")
    manifest_path = root / expected.get("frozen_source_manifest", "")
    if not manifest_path.is_file():
        _fail(f"frozen-source manifest path is missing: {manifest_path}")


def validate_review_receipt(
    receipt: dict[str, Any],
    expected_path: Path,
    matrix_path: Path,
    manifest_path: Path,
    expected: dict[str, Any],
    matrix: dict[str, Any],
    manifest: dict[str, Any],
    root: Path,
) -> None:
    if receipt.get("reviewed_before_candidate_behavior") is not True or receipt.get("candidate_behavior_read") is not False:
        _fail("independent review receipt must affirm source review before candidate behavior")
    if receipt.get("candidate_output_used_to_derive_expectations") is not False or receipt.get("result") != "accepted":
        _fail("independent review receipt does not accept candidate-independent pinned expectations")
    reviewer = receipt.get("reviewer", {})
    if reviewer.get("reviewer_type") != "fresh independent source reviewer" or not reviewer.get("session_id"):
        _fail("review receipt lacks a fresh independent reviewer identity")
    expected_ids = [group["id"] for group in expected["groups"]]
    expected_counts = [group["nonzero_assertion_count"] for group in matrix["groups"]]
    if receipt.get("group_ids") != expected_ids or receipt.get("mapping_counts") != expected_counts:
        _fail("review receipt group ids/counts do not bind the frozen matrix")
    total = sum(expected_counts)
    if receipt.get("literal_mapping_total") != total:
        _fail("review receipt total mapping count does not bind the frozen matrix")
    criteria = receipt.get("reviewed_criteria", {})
    if not isinstance(criteria, dict) or set(criteria) != REQUIRED_REVIEW_CRITERIA:
        _fail("review receipt must contain exactly the named required review criteria")
    if any(criteria[key] is not True for key in REQUIRED_REVIEW_CRITERIA):
        _fail("review receipt must affirm every named required behavior group")

    contract_paths = [
        "contracts/decision-source-branch/expected-results.json",
        "contracts/decision-source-branch/adversarial-matrix.json",
        "contracts/decision-source-branch/frozen-sources.json",
    ]
    hashes = receipt.get("contract_hashes", {})
    paths = {expected_path.resolve(): contract_paths[0], matrix_path.resolve(): contract_paths[1], manifest_path.resolve(): contract_paths[2]}
    for absolute, relative in paths.items():
        if hashes.get(relative) != sha256_file(absolute):
            _fail(f"review receipt does not content-bind {relative}")

    source_entries = receipt.get("source_hashes")
    if not isinstance(source_entries, list):
        _fail("review receipt source_hashes must be an array")
    validate_frozen_hash_entries(source_entries, root)
    reviewed_paths = {entry["path"] for entry in source_entries}
    frozen_paths = {entry["path"] for entry in manifest["sources"]}
    if not frozen_paths.issubset(reviewed_paths):
        _fail("review receipt omits a source pinned by the frozen manifest")


def verify_contract(
    expected_path: Path,
    matrix_path: Path,
    pinned_plan: Path,
    review_receipt_path: Path | None = None,
    *,
    require_review_receipt: bool = False,
    root: Path = ROOT,
) -> tuple[int, str]:
    expected_path = expected_path.resolve()
    matrix_path = matrix_path.resolve()
    expected = _read_json(expected_path)
    matrix = _read_json(matrix_path)
    total = validate_documents(expected, matrix, root=root)
    manifest_path = root / expected.get("frozen_source_manifest", "")
    manifest = _read_json(manifest_path)
    validate_hash_manifest(expected, manifest, root, pinned_plan)
    if require_review_receipt:
        if review_receipt_path is None:
            review_receipt_path = root / "contracts/decision-source-branch/independent-review.json"
        receipt = _read_json(review_receipt_path)
        validate_review_receipt(
            receipt, expected_path, matrix_path, manifest_path, expected, matrix, manifest, root
        )
    return total, sha256_file(expected_path)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expected", required=True, type=Path)
    parser.add_argument("--matrix", required=True, type=Path)
    parser.add_argument("--pinned-plan", required=True, type=Path)
    parser.add_argument("--require-eight-groups", action="store_true")
    parser.add_argument("--check-frozen-oracle-hashes", action="store_true")
    parser.add_argument("--require-review-receipt", action="store_true")
    parser.add_argument("--review-receipt", type=Path)
    args = parser.parse_args(argv)
    try:
        total, oracle_hash = verify_contract(
            args.expected,
            args.matrix,
            args.pinned_plan,
            args.review_receipt,
            require_review_receipt=args.require_review_receipt,
            root=ROOT,
        )
        if args.require_eight_groups and len(_read_json(args.expected).get("groups", [])) != 8:
            _fail("--require-eight-groups requires exactly eight groups")
        if args.check_frozen_oracle_hashes:
            # validate_hash_manifest above always checks every frozen source byte.
            pass
    except (ContractError, OSError, KeyError, TypeError) as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1
    print(f"PASS: 8 groups, {total} literal mappings, frozen source hashes verified, oracle sha256={oracle_hash}")
    if args.require_review_receipt:
        print("PASS: content-bound independent source-review receipt verified before candidate behavior")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

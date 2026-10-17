#!/usr/bin/env python3
"""Verify the pinned WardAtlas review-receipt oracle and adversarial matrix.

This is an artifact-contract check only, not a product behavior verifier. It
accepts no candidate output, pins the review packet, cross-checks the fixed
context against the existing shift-board/source-change/original oracles, and
requires a nonzero mapped assertion set for each of ten outcomes.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any


PACKET_SHA256 = "4db10d9c0df59f2b400d9bd89307e0874e3b51395b38307e23bfc2f0dd7c0d60"
EXPECTED_ARTIFACT_SHA256 = "382383b65ae8a9984550227c8ad9000d893bcd9e0171298ec3bf6adb7e170972"
MATRIX_ARTIFACT_SHA256 = "0d5f0832699daa2ba69a9a4d3cde2225e4d672830fc613070f015c0788302b9a"

PINNED_ORACLES = {
    "tests/shift-board-expected-results.json": "5d53a8f93245ee8391ec7cffb6d45b58355536d30d649db19cd53ca6b1ce2428",
    "tests/source-change-expected-results.json": "d26438dac655a9842d7bc60a2435ae1e3cab3a38a0f932d63005f044b0ba6097",
    "tests/expected-results.json": "c77e00e47c4f1e26d565c3d9583653838d5cc88695c4e6aa19ac583205d92b87",
}
PINNED_ORACLE_IDS = {
    "tests/shift-board-expected-results.json": "shift-board-expected-results",
    "tests/source-change-expected-results.json": "source-change-expected-results",
    "tests/expected-results.json": "original-expected-results",
}

REQUIRED_CASES = (
    "cold-empty-and-unacknowledged-draft",
    "fixed-review-context",
    "explicit-review-only-acknowledgment",
    "graph-reconstructed-evidence-and-incomplete-edge",
    "idempotent-confirmation",
    "reconnect-and-historical-snapshot-stability",
    "unchanged-source-handover-and-original-oracles",
    "server-side-branch-and-pre-effective-denial",
    "denied-source-nondisclosure",
    "local-only-no-external-write",
)

PACKET_OUTCOME_LINES = dict(zip(range(1, 11), range(30, 40)))
PACKET_GROUP_PREFIXES = (
    "1. **Cold empty and unconfirmed draft:**",
    "2. **Fixed review context:**",
    "3. **Explicit review-only acknowledgment:**",
    "4. **Graph-reconstructed evidence:**",
    "5. **Idempotent confirmation:**",
    "6. **Reconnect / historical stability:**",
    "7. **Non-mutation:**",
    "8. **Hypothetical branch denial:**",
    "9. **Access non-disclosure:**",
    "10. **Local-only boundary:**",
)


class ContractFailure(ValueError):
    """Raised when fixed oracle content or its matrix mapping has drifted."""


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ContractFailure(message)


def _unique_ids(rows: list[dict[str, Any]], label: str) -> list[str]:
    ids = [row.get("id") for row in rows]
    require(
        all(isinstance(value, str) and value for value in ids),
        f"{label} entries must have nonempty string IDs",
    )
    require(len(ids) == len(set(ids)), f"{label} IDs must be unique")
    return ids


def validate_structure(
    expected: dict[str, Any], matrix: dict[str, Any], require_ten_cases: bool
) -> int:
    """Check schemas, complete case coverage, and nonzero exact mapping shape."""
    require(expected.get("schema_version") == 1, "expected-results must use schema_version 1")
    require(matrix.get("schema_version") == 1, "matrix must use schema_version 1")
    require(expected.get("product") == "WardAtlas", "unexpected product name")
    require(
        "candidate responses never" in expected.get("oracle_policy", "").lower(),
        "expected-results must forbid candidate-derived expectations",
    )
    scope = expected.get("scope")
    require(isinstance(scope, dict) and scope.get("synthetic_only") is True, "oracle must be synthetic-only")
    require(scope.get("clinical_decisions") is False, "oracle must forbid clinical decisions")
    require(
        "actual falkordb" in str(scope.get("database_requirement", "")).lower(),
        "oracle must retain the actual FalkorDB requirement",
    )
    require(scope.get("external_writes") is False, "oracle must forbid external writes")
    require(matrix.get("oracle_file") == "tests/review-receipt-expected-results.json", "matrix points to a different oracle")

    scenarios = expected.get("cases")
    cases = matrix.get("cases")
    require(isinstance(scenarios, list) and isinstance(cases, list), "expected and matrix cases must be arrays")
    scenario_ids = _unique_ids(scenarios, "expected case")
    case_ids = _unique_ids(cases, "matrix case")
    if require_ten_cases:
        require(len(scenarios) == 10 and len(cases) == 10, "--require-ten-cases requires exactly ten cases")
    require(tuple(scenario_ids) == REQUIRED_CASES, "the ten outcome groups or their fixed order changed")
    require(case_ids == scenario_ids, "matrix cases must map one-to-one in the same order as expected outcomes")

    total_assertions = 0
    for scenario, case in zip(scenarios, cases):
        case_id = scenario["id"]
        require(case.get("id") == case_id and case.get("scenario_id") == case_id, f"matrix mapping differs for {case_id}")
        outcome = scenario.get("expected")
        require(isinstance(outcome, dict) and outcome, f"{case_id} must pin a nonempty outcome")
        refs = scenario.get("planner_refs")
        require(refs == [f"review-receipt-packet#outcome-{REQUIRED_CASES.index(case_id) + 1}"], f"{case_id} must map to its exact packet outcome")
        assertions = case.get("pinned_assertions")
        count = case.get("nonzero_assertion_count")
        require(isinstance(assertions, list) and assertions, f"{case_id} has no assertions")
        require(all(isinstance(item, str) and item.strip() for item in assertions), f"{case_id} has an empty/non-string assertion")
        require(type(count) is int and count > 0, f"{case_id} must have a nonzero assertion count")
        require(count == len(assertions), f"{case_id} assertion count does not match its assertions")
        require(bool(case.get("attack_or_edge_case")), f"{case_id} must describe its adversarial edge case")
        require(bool(case.get("setup")), f"{case_id} must specify a reproducible setup")
        require(bool(case.get("failure_detected")), f"{case_id} must describe the failure it detects")
        require(
            case.get("planner_refs") == [f"planner-bdb7b5ac66b041fe8e0a44e126dcf00c.md:{PACKET_OUTCOME_LINES[REQUIRED_CASES.index(case_id) + 1]}"],
            f"{case_id} matrix does not reference its exact packet line",
        )
        total_assertions += count
    require(total_assertions > 0, "matrix must contain nonzero assertions")

    pinned_sources = expected.get("pinned_sources")
    require(isinstance(pinned_sources, list) and len(pinned_sources) == 4, "expected-results must pin packet and three unchanged oracle sources")
    require(
        pinned_sources[0]
        == {
            "id": "review-receipt-packet",
            "file": "planner-bdb7b5ac66b041fe8e0a44e126dcf00c.md",
            "sha256": PACKET_SHA256,
            "outcome_lines": list(range(30, 40)),
        },
        "review packet identity, digest, or outcome-line mapping changed",
    )
    expected_pins = [
        {"id": PINNED_ORACLE_IDS[path], "file": path, "sha256": digest}
        for path, digest in PINNED_ORACLES.items()
    ]
    require(pinned_sources[1:] == expected_pins, "existing shift-board/source-change/original oracle pins changed")
    return total_assertions


def _load_json(path: Path) -> tuple[dict[str, Any], str]:
    try:
        raw = path.read_bytes()
        value = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ContractFailure(f"cannot load JSON artifact {path}: {exc}") from exc
    require(isinstance(value, dict), f"JSON artifact {path} must contain an object")
    return value, hashlib.sha256(raw).hexdigest()


def verify_pinned_packet(path: Path) -> str:
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise ContractFailure(f"cannot read pinned review packet {path}: {exc}") from exc
    digest = hashlib.sha256(raw).hexdigest()
    require(digest == PACKET_SHA256, "planner packet bytes differ from the independently pinned review-receipt packet")
    lines = raw.decode("utf-8").splitlines()
    for line_number, prefix in zip(range(30, 40), PACKET_GROUP_PREFIXES):
        require(
            len(lines) >= line_number and lines[line_number - 1].startswith(prefix),
            f"planner packet line {line_number} no longer names its fixed outcome group",
        )
    return digest


def verify_existing_oracles(root: Path, expected: dict[str, Any]) -> dict[str, str]:
    """Fail closed if any previously committed oracle changes, then cross-check overlaps."""
    loaded: dict[str, dict[str, Any]] = {}
    observed: dict[str, str] = {}
    for relative, pinned_sha in PINNED_ORACLES.items():
        path = root / relative
        try:
            raw = path.read_bytes()
        except OSError as exc:
            raise ContractFailure(f"cannot read unchanged oracle source {path}: {exc}") from exc
        digest = hashlib.sha256(raw).hexdigest()
        require(digest == pinned_sha, f"unchanged oracle source {relative} differs from its accepted byte pin")
        try:
            loaded[relative] = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ContractFailure(f"cannot parse unchanged oracle source {relative}: {exc}") from exc
        observed[relative] = digest

    cases = {row["id"]: row["expected"] for row in expected["cases"]}
    shift = loaded["tests/shift-board-expected-results.json"]
    source = loaded["tests/source-change-expected-results.json"]
    original = loaded["tests/expected-results.json"]
    shift_cases = {row["id"]: row["expected"] for row in shift["cases"]}
    source_cases = {row["id"]: row["expected"] for row in source["scenarios"]}

    require(
        cases["cold-empty-and-unacknowledged-draft"]["cold_graph_review_receipt_count"] == 0
        and shift_cases["cold-empty-board"]["seeded_board_row_count"] == 0
        and source_cases["source-empty-graph"]["source_rows"] == 0,
        "cold receipt expectation conflicts with the unchanged empty-board/source oracles",
    )
    context = cases["fixed-review-context"]
    source_fixture = source["fixture"]
    shift_context = shift_cases["dock-role-time-and-source-evidence"]
    source_expected_paths = source["expected_paths"]
    require(context["current_role_path"] == shift_context["at_effective_time"]["role_path"] == source_expected_paths["after"], "fixed current role path conflicts with unchanged shift-board/source-change oracles")
    require(context["context_as_of"] == shift_context["at_effective_time"]["as_of"] == source_cases["dock-role-path-after-effective-time"]["as_of"], "fixed context time conflicts with unchanged source/shift-board oracles")
    require(context["current_source"] == {
        "source_id": source_fixture["source_after_id"],
        "title": source_fixture["source_after_title"],
        "observed_at": source_fixture["observed_at"],
        "effective_at": source_fixture["effective_at"],
        "supersedes": source_fixture["supersedes"],
    }, "fixed R2 identity or timestamps conflict with unchanged source-change oracle")
    queue = shift_cases["explicit-edge-review-queue"]
    source_queue = source_cases["explicit-linked-review-queue"]
    require(context["handover_id"] == source_fixture["handover_id"] == "HND-DOCK-2020", "fixed handover ID conflicts with unchanged source oracle")
    require(context["unchanged_handover_role_id"] == queue["unchanged_handover_role_id"] == source_queue["unchanged_handover_role_id"], "old handover role conflicts with unchanged source/handover oracles")
    require(context["unchanged_handover_source_id"] == queue["unchanged_handover_source_id"] == source_queue["unchanged_handover_source_id"], "old handover source conflicts with unchanged source/handover oracles")
    require(context["excluded_lexical_only_handover_id"] == queue["excluded_lexical_only_id"] == source_fixture["lexical_only_handover_id"], "lexical-only decoy differs from unchanged source/handover oracles")
    require(context["unlinked_artifact_coverage"] == queue["unlinked_artifact_status"] == source_queue["unlinked_artifact_status"] == "unknown", "unlinked coverage differs from unchanged source/handover oracles")
    reconnect = cases["reconnect-and-historical-snapshot-stability"]
    require(reconnect["ordered_path_snapshot"] == context["current_role_path"], "reconnect snapshot path differs from the fixed current path")
    require(reconnect["unchanged_handover_context"] == {
        "handover_id": context["handover_id"],
        "role_id": context["unchanged_handover_role_id"],
        "source_id": context["unchanged_handover_source_id"],
    }, "reconnect snapshot's old handover context differs from unchanged source/handover oracles")

    expected_branch = shift_cases["conveyor-observed-and-hypothetical-times"]["hypothetical_branch_1850"]
    branch_case = cases["server-side-branch-and-pre-effective-denial"]
    require(branch_case["unrelated_hypothetical_branch_id"] == expected_branch["branch_id"] == "BR-C4-RESTORE-DRILL", "fixed hypothetical branch conflicts with unchanged temporal/shift-board oracle")
    require(branch_case["hypothetical_branch_incident_id"] == shift_cases["conveyor-observed-and-hypothetical-times"]["incident_id"] == "INC-204", "branch incident differs from unchanged shift-board oracle")
    require(branch_case["pre_effective_as_of"] == source_cases["dock-role-path-before-effective-time"]["as_of"] == "2026-10-14T20:29:00Z", "pre-effective rejection time differs from unchanged source oracle")

    restricted = shift_cases["denied-source-nondisclosure"]["dock_restricted_literals"]
    source_restricted = source_fixture
    hidden_fields = [
        "restricted_source_id",
        "restricted_source_title",
        "restricted_fact",
        "restricted_equipment_id",
        "restricted_department_id",
        "restricted_role_id",
    ]
    require(cases["denied-source-nondisclosure"]["restricted_source_literals"] == restricted, "restricted-source tripwires differ from unchanged shift-board oracle")
    require([source_restricted[key] for key in hidden_fields] == restricted, "restricted-source tripwires differ from unchanged source-change oracle")
    conflict = source_cases["unresolved-sop-conflict-preserved"]
    require(conflict["conflicting_versions"] == ["SOP-OPS-9-V2", "SOP-OPS-9-V3"] and conflict["conflict_status"] == "unresolved", "unchanged source-change SOP conflict oracle drifted")
    require(original.get("scope", {}).get("clinical_decisions") is False, "original oracle no longer preserves the non-clinical boundary")
    return observed


def _expected_ref_lines(refs: list[str]) -> dict[str, set[int]]:
    result: dict[str, set[int]] = {}
    pattern = re.compile(r"^review-receipt-packet#outcome-(\d+)$")
    for ref in refs:
        match = pattern.fullmatch(ref)
        require(match is not None, f"invalid expected planner reference: {ref}")
        outcome = int(match.group(1))
        require(outcome in PACKET_OUTCOME_LINES, f"expected planner reference names unknown packet outcome: {ref}")
        result.setdefault("review-receipt-packet", set()).add(PACKET_OUTCOME_LINES[outcome])
    return result


def _matrix_ref_lines(refs: list[str]) -> dict[str, set[int]]:
    result: dict[str, set[int]] = {}
    filename = "planner-bdb7b5ac66b041fe8e0a44e126dcf00c.md"
    pattern = re.compile(r"^([^:]+\.md):(\d+)$")
    for ref in refs:
        match = pattern.fullmatch(ref)
        require(match is not None, f"invalid matrix planner line reference: {ref}")
        name, line = match.groups()
        require(name == filename, f"matrix planner reference names an unpinned file: {name}")
        result.setdefault("review-receipt-packet", set()).add(int(line))
    return result


def validate_plan_mappings(expected: dict[str, Any], matrix: dict[str, Any]) -> None:
    for scenario, case in zip(expected["cases"], matrix["cases"]):
        expected_lines = _expected_ref_lines(scenario["planner_refs"])
        matrix_lines = _matrix_ref_lines(case["planner_refs"])
        require(matrix_lines == expected_lines, f"matrix case {case['id']} does not map to the same exact packet outcome line")


def validate_artifacts(
    expected: dict[str, Any],
    matrix: dict[str, Any],
    expected_sha256: str,
    matrix_sha256: str,
    root: Path,
    pinned_plan: Path,
    require_ten_cases: bool,
) -> tuple[int, dict[str, str]]:
    assertion_count = validate_structure(expected, matrix, require_ten_cases)
    require(expected_sha256 == EXPECTED_ARTIFACT_SHA256, "review-receipt expected-results bytes differ from the independent fixed outcome packet")
    require(matrix_sha256 == MATRIX_ARTIFACT_SHA256, "review-receipt adversarial matrix bytes differ from the independent matrix pin")
    packet_digest = verify_pinned_packet(pinned_plan)
    oracle_digests = verify_existing_oracles(root, expected)
    validate_plan_mappings(expected, matrix)
    return assertion_count, {"review-receipt-packet": packet_digest, **oracle_digests}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expected", required=True, type=Path)
    parser.add_argument("--matrix", required=True, type=Path)
    parser.add_argument("--pinned-plan", required=True, type=Path)
    parser.add_argument("--require-ten-cases", action="store_true")
    args = parser.parse_args(argv)
    if not args.require_ten_cases:
        print("FAIL: --require-ten-cases is mandatory for this contract", file=sys.stderr)
        return 1

    try:
        expected, expected_digest = _load_json(args.expected)
        matrix, matrix_digest = _load_json(args.matrix)
        assertion_count, source_digests = validate_artifacts(
            expected,
            matrix,
            expected_digest,
            matrix_digest,
            root=Path(__file__).resolve().parents[1],
            pinned_plan=args.pinned_plan,
            require_ten_cases=args.require_ten_cases,
        )
    except (OSError, TypeError, ValueError, KeyError) as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1

    print(
        "PASS: exact review-receipt outcome packet and matrix; 10 outcome groups, "
        f"10 mapped adversarial cases, {assertion_count} nonzero assertions; "
        f"pinned packet and {len(source_digests) - 1} unchanged oracle sources verified; "
        "no candidate output used as oracle."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

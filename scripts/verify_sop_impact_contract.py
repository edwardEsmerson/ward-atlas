#!/usr/bin/env python3
"""Verify the fixed SOP-impact oracle and case mapping, without candidate output.

This is an artifact-contract check only. It does not run product code or claim
live FalkorDB behavior; the later feature verifier must exercise all ten cases.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PACKET_SHA256 = "4eed6fb7cb2b709a67278bff5b291f57647aaa7470ad968bae92b1620280a9c7"
EXPECTED_ARTIFACT_SHA256 = "f94e65850384e36e7200b321d14628f074fee37fb159adbcd73bef00c6e1acca"
MATRIX_ARTIFACT_SHA256 = "f5aa441a636b45b4222716d4a003afc91cf7598439833e36f390523b55f161c6"
PACKET_FILENAME = "planner-3bfcb0d8abbc45bc82e0fab8d715c2da.md"

PINNED_ORACLES = {
    "tests/expected-results.json": "c77e00e47c4f1e26d565c3d9583653838d5cc88695c4e6aa19ac583205d92b87",
    "tests/shift-board-expected-results.json": "5d53a8f93245ee8391ec7cffb6d45b58355536d30d649db19cd53ca6b1ce2428",
    "tests/source-change-expected-results.json": "d26438dac655a9842d7bc60a2435ae1e3cab3a38a0f932d63005f044b0ba6097",
    "tests/review-receipt-expected-results.json": "382383b65ae8a9984550227c8ad9000d893bcd9e0171298ec3bf6adb7e170972",
    "tests/role-scope-expected-results.json": "fc83b36dd9cfea900a1885816c6b84ffe7a1ab3d5c62d45be87f97a7368a9867",
}
ORACLE_IDS = {
    "tests/expected-results.json": "original-expected-results",
    "tests/shift-board-expected-results.json": "shift-board-expected-results",
    "tests/source-change-expected-results.json": "source-change-expected-results",
    "tests/review-receipt-expected-results.json": "review-receipt-expected-results",
    "tests/role-scope-expected-results.json": "role-scope-expected-results",
}

REQUIRED_CASES = (
    "sop-impact-cold-empty",
    "sop-impact-observed-outage-paths",
    "sop-impact-conflict-provenance",
    "sop-impact-stale-v1-excluded",
    "sop-impact-applicability-edge-counterfactual",
    "sop-impact-conflict-edge-counterfactual",
    "sop-impact-restoration-snapshot",
    "sop-impact-denied-source-invariance",
    "sop-impact-reseed-reconnect",
    "sop-impact-time-browser-local-boundary",
)
PACKET_OUTCOME_LINES = {number: 42 + number for number in range(1, 11)}
PACKET_OUTCOME_PREFIXES = (
    "1. **Cold empty:**",
    "2. **Observed outage paths:**",
    "3. **Conflict witness and provenance:**",
    "4. **Stale-version exclusion:**",
    "5. **Applicability-edge counterfactual:**",
    "6. **Conflict-edge counterfactual:**",
    "7. **Restoration snapshot:**",
    "8. **Denied-source non-disclosure:**",
    "9. **Repeat/reseed/reconnect:**",
    "10. **Time/browser/local boundary:**",
)


class ContractFailure(ValueError):
    """Raised when fixed oracle bytes or mapping have drifted."""


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ContractFailure(message)


def _unique_ids(rows: list[dict[str, Any]], label: str) -> list[str]:
    ids = [row.get("id") for row in rows]
    require(all(isinstance(item, str) and item for item in ids), f"{label} IDs must be nonempty strings")
    require(len(ids) == len(set(ids)), f"{label} IDs must be unique")
    return ids


def _load_json(path: Path) -> tuple[dict[str, Any], str]:
    try:
        raw = path.read_bytes()
        value = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ContractFailure(f"cannot load JSON artifact {path}: {exc}") from exc
    require(isinstance(value, dict), f"JSON artifact {path} must be an object")
    return value, hashlib.sha256(raw).hexdigest()


def validate_structure(expected: dict[str, Any], matrix: dict[str, Any], require_ten_cases: bool) -> int:
    """Require complete fixed case order, safety scope, and exact nonzero mapping shape."""
    require(expected.get("schema_version") == 1 and matrix.get("schema_version") == 1, "schema_version must be 1")
    require(expected.get("product") == "WardAtlas", "unexpected product")
    require("candidate responses never" in expected.get("oracle_policy", "").lower(), "candidate output must not be the oracle")
    scope = expected.get("scope")
    require(isinstance(scope, dict) and scope.get("synthetic_only") is True, "oracle must remain synthetic-only")
    require(scope.get("clinical_decisions") is False, "oracle must forbid clinical decisions")
    require("actual falkordb" in str(scope.get("database_requirement", "")).lower(), "actual FalkorDB is required")
    require(scope.get("external_writes") is False, "external writes must be forbidden")
    require(scope.get("candidate_output_is_oracle") is False, "candidate output must not supply expectations")
    require(matrix.get("oracle_file") == "tests/sop-impact-expected-results.json", "matrix points at a different oracle")

    cases = expected.get("cases")
    matrix_cases = matrix.get("cases")
    require(isinstance(cases, list) and isinstance(matrix_cases, list), "expected and matrix cases must be arrays")
    case_ids = _unique_ids(cases, "expected case")
    matrix_ids = _unique_ids(matrix_cases, "matrix case")
    if require_ten_cases:
        require(len(cases) == 10 and len(matrix_cases) == 10, "--require-ten-cases requires exactly ten cases")
    require(tuple(case_ids) == REQUIRED_CASES, "the ten outcome groups or fixed order changed")
    require(matrix_ids == case_ids, "matrix cases must map one-to-one in the same order as expected outcomes")

    total_assertions = 0
    for index, (scenario, case) in enumerate(zip(cases, matrix_cases), start=1):
        case_id = REQUIRED_CASES[index - 1]
        require(case.get("id") == case_id and case.get("scenario_id") == case_id, f"case mapping differs for {case_id}")
        require(isinstance(scenario.get("expected"), dict) and scenario["expected"], f"{case_id} must pin expected values")
        require(scenario.get("planner_refs") == [f"sop-impact-packet#outcome-{index}"], f"{case_id} must map to packet outcome {index}")
        assertions = case.get("pinned_assertions")
        count = case.get("nonzero_assertion_count")
        require(isinstance(assertions, list) and assertions, f"{case_id} has no pinned assertions")
        require(all(isinstance(item, str) and item.strip() for item in assertions), f"{case_id} has an empty assertion")
        require(type(count) is int and count > 0, f"{case_id} must have a nonzero assertion floor")
        require(count == len(assertions), f"{case_id} assertion count differs from its pinned assertions")
        require(bool(case.get("attack_or_edge_case")), f"{case_id} must describe its adversarial case")
        require(bool(case.get("setup")), f"{case_id} must describe reproducible setup")
        require(bool(case.get("failure_detected")), f"{case_id} must name the failure detected")
        require(
            case.get("planner_refs") == [f"{PACKET_FILENAME}:{PACKET_OUTCOME_LINES[index]}"],
            f"{case_id} matrix does not map to its exact packet line",
        )
        total_assertions += count
    require(total_assertions > 0, "matrix must contain nonzero assertions")

    pins = expected.get("pinned_sources")
    require(isinstance(pins, list) and len(pins) == 6, "oracle must pin packet and all five unchanged baseline oracles")
    require(
        pins[0] == {
            "id": "sop-impact-packet",
            "file": f"planner-3bfcb0d8abbc45bc82e0fab8d715c2da/plans/{PACKET_FILENAME}",
            "sha256": PACKET_SHA256,
            "outcome_lines": list(range(43, 53)),
        },
        "SOP-impact packet identity, digest, or line mapping changed",
    )
    expected_pins = [{"id": ORACLE_IDS[path], "file": path, "sha256": digest} for path, digest in PINNED_ORACLES.items()]
    require(pins[1:] == expected_pins, "unchanged baseline expected-oracle pins changed")

    fixture = expected.get("fixture", {})
    require(fixture.get("incident_id") == "INC-204" and fixture.get("equipment_id") == "CONVEYOR-C4", "fixed incident/equipment IDs changed")
    require(fixture.get("offset_input") == "2026-10-14T20:30:00+02:00", "fixed offset input changed")
    require(fixture.get("offset_normalized_as_of") == "2026-10-14T18:30:00Z", "fixed normalized timestamp changed")
    return total_assertions


def verify_pinned_packet(path: Path) -> str:
    try:
        raw = path.read_bytes()
        text = raw.decode("utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise ContractFailure(f"cannot read pinned SOP-impact packet {path}: {exc}") from exc
    digest = hashlib.sha256(raw).hexdigest()
    require(digest == PACKET_SHA256, "planner packet bytes differ from independently pinned SOP-impact packet")
    lines = text.splitlines()
    for line_number, prefix in zip(range(43, 53), PACKET_OUTCOME_PREFIXES):
        require(len(lines) >= line_number and lines[line_number - 1].startswith(prefix), f"planner packet line {line_number} outcome heading changed")
    return digest


def verify_existing_oracles(root: Path, expected: dict[str, Any]) -> dict[str, str]:
    """Fail closed on any old oracle edit and verify the new packet copies its overlaps exactly."""
    loaded: dict[str, dict[str, Any]] = {}
    digests: dict[str, str] = {}
    for relative, pinned in PINNED_ORACLES.items():
        try:
            raw = (root / relative).read_bytes()
        except OSError as exc:
            raise ContractFailure(f"cannot read unchanged oracle {relative}: {exc}") from exc
        digest = hashlib.sha256(raw).hexdigest()
        require(digest == pinned, f"unchanged oracle {relative} differs from its accepted byte hash")
        try:
            loaded[relative] = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ContractFailure(f"cannot parse unchanged oracle {relative}: {exc}") from exc
        digests[relative] = digest

    cases = {row["id"]: row["expected"] for row in expected["cases"]}
    original = loaded["tests/expected-results.json"]
    shift = loaded["tests/shift-board-expected-results.json"]
    source = loaded["tests/source-change-expected-results.json"]
    role_scope = loaded["tests/role-scope-expected-results.json"]
    original_cases = {row["id"]: row["expected"] for row in original["scenarios"]}
    shift_cases = {row["id"]: row["expected"] for row in shift["cases"]}
    role_scope_cases = {row["id"]: row["expected"] for row in role_scope["cases"]}

    cold = cases["sop-impact-cold-empty"]
    require(
        cold["impact_paths"] == original_cases["cold-empty-graph"]["impact_paths"] == [],
        "cold SOP-impact paths differ from the original empty-graph oracle",
    )
    require(
        cold["board_state"] == role_scope_cases["cold-empty-and-unknown-scope"]["cold_graph_state"] == "empty/unknown",
        "cold empty state differs from the unchanged role-scope oracle",
    )
    require(
        role_scope_cases["cold-empty-and-unknown-scope"]["cold_graph_paths"] == []
        and role_scope_cases["cold-empty-and-unknown-scope"]["cold_graph_source_ids"] == [],
        "new empty-state oracle conflicts with unchanged role-scope cold graph",
    )

    outage = cases["sop-impact-observed-outage-paths"]
    original_outage = original_cases["multi-hop-outage-current-ownership"]
    shift_outage = shift_cases["conveyor-observed-and-hypothetical-times"]["observed_at_1830"]
    require(outage["impact_paths"] == original_outage["impact_paths"] == shift_outage["paths"], "new impact paths differ from unchanged outage oracles")
    require(outage["as_of"] == shift_outage["at"] == "2026-10-14T18:30:00Z", "new outage snapshot time differs from shift-board oracle")
    require(outage["path_evidence"] == original["expected_evidence_by_path"], "new path provenance differs from original fixed evidence")
    require(outage["path_evidence"] == shift_outage["canonical_path_evidence"], "new path provenance differs from shift-board canonical evidence")
    require(outage["current_ownership_source_ids"] == [item["source_ids"][-1] for item in original["expected_evidence_by_path"]], "current ownership source IDs differ from original oracle")

    conflict = cases["sop-impact-conflict-provenance"]
    old_conflict = original_cases["stale-reference-current-sop-conflict"]
    require(conflict["conflict_status"] == old_conflict["conflict_status"] == "unresolved", "unresolved source conflict status drifted")
    claims = conflict["claim_evidence"]
    require([claim["sop_version_id"] for claim in claims] == old_conflict["current_conflicting_version_ids"], "current SOP version IDs differ from original oracle")
    require([claim["role_id"] for claim in claims] == old_conflict["current_conflicting_role_claims"], "current role-claim labels differ from original oracle")
    require([claim["source_id"] for claim in claims] == old_conflict["current_source_ids"], "claim source IDs differ from original oracle")
    require([claim["source_title"] for claim in claims] == old_conflict["current_source_titles"], "claim source titles differ from original oracle")
    require([claim["observed_at"] for claim in claims] == old_conflict["current_observed_at"], "claim source timestamps differ from original oracle")
    source_conflict_cases = {row["id"]: row["expected"] for row in source["scenarios"]}
    require(
        source_conflict_cases["unresolved-sop-conflict-preserved"]["conflicting_versions"]
        == [claim["sop_version_id"] for claim in claims]
        and source_conflict_cases["unresolved-sop-conflict-preserved"]["conflict_status"] == conflict["conflict_status"],
        "new conflict witness differs from unchanged source-change oracle",
    )
    stale = cases["sop-impact-stale-v1-excluded"]
    for new_key, old_key in (
        ("stale_version_id", "stale_version_id"),
        ("stale_source_id", "stale_source_id"),
        ("stale_source_title", "stale_source_title"),
        ("stale_observed_at", "stale_observed_at"),
        ("stale_referenced_equipment_id", "stale_referenced_equipment_id"),
    ):
        require(stale[new_key] == old_conflict[old_key], f"stale SOP value {new_key} differs from original oracle")

    restored = cases["sop-impact-restoration-snapshot"]
    restoration = shift_cases["conveyor-observed-and-hypothetical-times"]["observed_restoration_1930"]
    require(restored["as_of"] == restoration["at"] == "2026-10-14T19:30:00Z", "restoration time differs from shift-board oracle")
    require(restored["active_impact_paths"] == restoration["active_paths"] == [], "restoration active paths differ from shift-board oracle")
    require(restored["sop_conflict_status"] == old_conflict["conflict_status"], "restoration must preserve unresolved source-backed conflict")

    denied = cases["sop-impact-denied-source-invariance"]
    shift_denied = shift_cases["denied-source-nondisclosure"]
    require(denied["viewer_scope"] == shift_denied["viewer_scope"] == "operations", "viewer scope differs from accepted ACL oracle")
    require(denied["injected_probe_acl"] == shift_denied["conveyor_restricted_fixture"]["source_acl_label"] == "restricted", "denied ACL label differs from accepted ACL oracle")
    require(expected["scope"]["denied_acl"] == "restricted", "denied ACL scope drifted")

    boundary = cases["sop-impact-time-browser-local-boundary"]
    fixture = expected["fixture"]
    parsed = datetime.fromisoformat(fixture["offset_input"])
    normalized = parsed.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    require(normalized == fixture["offset_normalized_as_of"] == boundary["normalized_as_of"] == outage["as_of"], "offset timestamp does not normalize to pinned outage result")
    require(boundary["offset_input"] == fixture["offset_input"], "time boundary input differs from fixture")
    require(boundary["normalized_result_matches"] == "sop-impact-observed-outage-paths", "normalized time must map to exact outage outcome")
    require(boundary["malformed_timestamp_probe"] == fixture["malformed_time_probe"], "malformed timestamp test input differs from fixture")
    require(original["scope"]["clinical_decisions"] is False, "original oracle no longer preserves non-clinical boundary")
    return digests


def _expected_packet_lines(refs: list[str]) -> set[int]:
    result: set[int] = set()
    pattern = re.compile(r"^sop-impact-packet#outcome-(\d+)$")
    for ref in refs:
        match = pattern.fullmatch(ref)
        require(match is not None, f"invalid expected packet reference {ref}")
        outcome = int(match.group(1))
        require(outcome in PACKET_OUTCOME_LINES, f"unknown expected packet outcome {ref}")
        result.add(PACKET_OUTCOME_LINES[outcome])
    return result


def _matrix_packet_lines(refs: list[str]) -> set[int]:
    result: set[int] = set()
    pattern = re.compile(r"^([^:]+\.md):(\d+)$")
    for ref in refs:
        match = pattern.fullmatch(ref)
        require(match is not None, f"invalid matrix packet line reference {ref}")
        filename, line = match.groups()
        require(filename == PACKET_FILENAME, f"matrix points to an unpinned packet {filename}")
        result.add(int(line))
    return result


def validate_plan_mappings(expected: dict[str, Any], matrix: dict[str, Any]) -> None:
    for scenario, case in zip(expected["cases"], matrix["cases"]):
        require(
            _matrix_packet_lines(case["planner_refs"]) == _expected_packet_lines(scenario["planner_refs"]),
            f"matrix case {case['id']} does not map to the same exact packet outcome line",
        )


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
    require(expected_sha256 == EXPECTED_ARTIFACT_SHA256, "SOP-impact expected-results bytes differ from fixed packet outcomes")
    require(matrix_sha256 == MATRIX_ARTIFACT_SHA256, "SOP-impact matrix bytes differ from its fixed adversarial assertions")
    packet_digest = verify_pinned_packet(pinned_plan)
    oracle_digests = verify_existing_oracles(root, expected)
    validate_plan_mappings(expected, matrix)
    return assertion_count, {"sop-impact-packet": packet_digest, **oracle_digests}


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
        assertions, source_digests = validate_artifacts(
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
        "PASS: exact SOP-impact packet and matrix; 10 outcome groups, "
        f"10 mapped adversarial cases, {assertions} nonzero assertions; "
        f"pinned packet and {len(source_digests) - 1} unchanged oracle sources verified; "
        "no candidate output used as oracle."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

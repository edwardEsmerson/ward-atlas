#!/usr/bin/env python3
"""Validate WardAtlas's independently pinned cascading-simulation contract.

This validates oracle artifacts and their planning/source mappings only. It does
not run graph queries or claim that the proposed cascade is implemented.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any


EXPECTED_ARTIFACT_SHA256 = "12221f236058569bf1f4a4b0cd3b75b901f56b60fa34ba4ee5f47cb8ac5353c2"
MATRIX_ARTIFACT_SHA256 = "c74431b0c2bebbdc25419bf58d39129d150779e1ef7418b64c80dee6979398db"

# Fixed companion planning packets live beside the supplied cascade packet.
# Their digests keep this oracle disjoint from (and accountable to) existing work.
PINNED_PLANS = {
    "cascade-packet": (
        "planner-8b15037dc21642129e4f0cbd47508f2f.md",
        "a0ab793c8fe21383decf987f3582f3d9f94a89ce68d5afe48efada7fb60cbe11",
    ),
    "original-planner": (
        "planner-ed8ab68cd001411d97ee43310862ae22.md",
        "01c10540400a08541da696b075d69183bfd62a290caca3f75f8aabdd8dfdd957",
    ),
    "source-change-planner": (
        "planner-9b1a51761889446c8b5525bde02bc5d6.md",
        "3879d8eb6197f631311c659528b939d96f069702406d3e257b1ab1d84c40e3b7",
    ),
    "temporal-planner": (
        "planner-13609527bde94e87ae5bcacaa2c86a70.md",
        "cb62e05f8b0307a27f6050aef455ccf7b0df42d8f83a1479124c127187b46464",
    ),
    "shift-board-planner": (
        "planner-5f75fb17810c463fb9d84bd5821b254f.md",
        "5dd4c50cd2044e016e3593c337a5ccae966cfba8ef74cbcb8d0ffd95eaed55de",
    ),
}
PINNED_PLAN_LITERALS = {
    "cascade-packet": (
        "2026-10-15T06:00:00Z", "2026-10-15T06:15:00Z", "2026-10-15T06:30:00Z",
        "2026-10-15T07:30:00+01:00", "BR-FPU-OUTAGE-DRILL", "BR-FPU-ISOLATE-DOCK-FEED",
        "EVT-FPU-OUTAGE-HYP-01", "SRC-TABLETOP-FPU-7", "SRC-RESTRICTED-FPU-77",
        "INC-312 → EQ-FPU-7 → EQ-DOCK-CHARGER-2 → SVC-DOCK-TRANSFER → DEPT-RECEIVING-DOCK → ROLE-DOCK-FACILITIES",
        "INC-312 → EQ-FPU-7 → EQ-LINEN-CONVEYOR-7 → SVC-LINEN-TRANSFER → DEPT-LINEN-LOGISTICS → ROLE-LINEN-DUTY",
        "EQ-FPU-7 → EQ-DOCK-CHARGER-2",
    ),
    "original-planner": (
        "INC-LIFT-01", "EQ-GOODS-LIFT-01", "SVC-LINEN-SUPPLY", "DEPT-LINEN",
        "DEPT-SUPPLY", "ROLE-FACILITIES-DUTY", "ROLE-SUPPLY-COORD", "SOP-RESTRICTED-01",
    ),
    "source-change-planner": (
        "2026-10-14T20:29:00Z", "2026-10-14T20:30:00Z", "SRC-DOCK-ROSTER-R2",
        "INC-207 → EQ-DOCK-CART-2 → SVC-INBOUND-TRANSFER → DEPT-RECEIVING-DOCK → ROLE-DOCK-DUTY",
        "ROLE-DOCK-SWING", "SRC-RESTRICTED-DOCK-99",
    ),
    "temporal-planner": (
        "2026-10-14T18:30:00Z", "BR-C4-RESTORE-DRILL", "EVT-C4-UNAVAILABLE-01",
        "INC-204 → CONVEYOR-C4 → SVC-MATERIAL-TRANSFER → DEPT-CENTRAL-STERILE → ROLE-STERILE-OPS-COORD",
        "INC-204 → CONVEYOR-C4 → SVC-MATERIAL-TRANSFER → DEPT-MATERIALS-LOGISTICS → ROLE-MATERIALS-DUTY",
    ),
    "shift-board-planner": (
        "INC-207", "INC-204", "HND-DOCK-2020", "TKT-DOCK-88",
        "ROLE-DOCK-SWING", "no cross-case contamination", "new-client reconnect",
    ),
}

REQUIRED_CASES = (
    "cold-empty-graph",
    "baseline-mainline",
    "shared-dependency-cascade",
    "branch-only-edge-counterfactual",
    "no-cross-case-contamination",
    "denied-source-nondisclosure",
    "time-repeat-and-reconnect",
    "local-only-scope-boundary",
)

CASCADE_PATHS = [
    [
        "INC-312", "EQ-FPU-7", "EQ-DOCK-CHARGER-2", "SVC-DOCK-TRANSFER",
        "DEPT-RECEIVING-DOCK", "ROLE-DOCK-FACILITIES",
    ],
    [
        "INC-312", "EQ-FPU-7", "EQ-LINEN-CONVEYOR-7", "SVC-LINEN-TRANSFER",
        "DEPT-LINEN-LOGISTICS", "ROLE-LINEN-DUTY",
    ],
]
INC204_PATHS = [
    ["INC-204", "CONVEYOR-C4", "SVC-MATERIAL-TRANSFER", "DEPT-CENTRAL-STERILE", "ROLE-STERILE-OPS-COORD"],
    ["INC-204", "CONVEYOR-C4", "SVC-MATERIAL-TRANSFER", "DEPT-MATERIALS-LOGISTICS", "ROLE-MATERIALS-DUTY"],
]
INC207_PATH = ["INC-207", "EQ-DOCK-CART-2", "SVC-INBOUND-TRANSFER", "DEPT-RECEIVING-DOCK", "ROLE-DOCK-SWING"]
LEGACY_PATHS = [
    ["INC-LIFT-01", "EQ-GOODS-LIFT-01", "SVC-LINEN-SUPPLY", "DEPT-LINEN", "ROLE-FACILITIES-DUTY"],
    ["INC-LIFT-01", "EQ-GOODS-LIFT-01", "SVC-LINEN-SUPPLY", "DEPT-SUPPLY", "ROLE-FACILITIES-DUTY"],
]


class ContractFailure(ValueError):
    """An oracle artifact or source mapping violates its independent contract."""


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ContractFailure(message)


def _ids(rows: Any, label: str) -> list[str]:
    require(isinstance(rows, list), f"{label} must be an array")
    values = [row.get("id") if isinstance(row, dict) else None for row in rows]
    require(all(isinstance(value, str) and value for value in values), f"{label} IDs must be nonempty strings")
    require(len(values) == len(set(values)), f"{label} IDs must be unique")
    return values


def _plan_outcome_lines(spec: str) -> set[int]:
    result: set[int] = set()
    for part in spec.split(","):
        if not part:
            raise ContractFailure("empty numbered outcome in planner reference")
        if "-" in part:
            bounds = part.split("-")
            require(len(bounds) == 2, f"invalid planner outcome range: {part}")
            start, end = map(int, bounds)
            require(start > 0 and end >= start, f"invalid planner outcome range: {part}")
            result.update(range(start, end + 1))
        else:
            number = int(part)
            require(number > 0, f"invalid planner outcome number: {part}")
            result.add(number)
    return result


def _line_range(spec: str) -> set[int]:
    result: set[int] = set()
    for part in spec.split(","):
        if "-" in part:
            bounds = part.split("-")
            require(len(bounds) == 2, f"invalid planner line range: {part}")
            start, end = map(int, bounds)
            require(start > 0 and end >= start, f"invalid planner line range: {part}")
            result.update(range(start, end + 1))
        else:
            line = int(part)
            require(line > 0, f"invalid planner line: {part}")
            result.add(line)
    return result


def validate_plan_mappings(expected: dict[str, Any], matrix: dict[str, Any]) -> None:
    """Require expected and adversarial refs to resolve to identical packet lines."""
    sources = expected.get("pinned_sources")
    require(isinstance(sources, list), "pinned_sources must be an array")
    by_id = {row.get("id"): row for row in sources if isinstance(row, dict)}
    by_file = {Path(row.get("path", "")).name: row for row in sources if isinstance(row, dict)}
    require(len(by_id) == len(sources), "pinned source IDs must be unique")
    require(len(by_file) == len(sources), "pinned planner file names must be unique")
    for outcome, case in zip(expected["cases"], matrix["cases"]):
        expected_lines: dict[str, set[int]] = {}
        for ref in outcome.get("planner_refs", []):
            match = re.fullmatch(r"([^#]+)#outcomes?-(.+)", ref)
            require(match is not None, f"invalid expected planner ref: {ref}")
            source_id, spec = match.groups()
            require(source_id in by_id, f"unknown expected planner source: {source_id}")
            source = by_id[source_id]
            line_list = source.get("outcome_lines")
            indexes = _plan_outcome_lines(spec)
            require(isinstance(line_list, list), f"{source_id} has no outcome-line map")
            require(indexes and max(indexes) <= len(line_list), f"{ref} selects a missing numbered outcome")
            expected_lines.setdefault(source_id, set()).update(line_list[index - 1] for index in indexes)

        matrix_lines: dict[str, set[int]] = {}
        for ref in case.get("planner_refs", []):
            match = re.fullmatch(r"([^:]+\.md):([0-9,-]+)", ref)
            require(match is not None, f"invalid matrix planner ref: {ref}")
            filename, spec = match.groups()
            require(filename in by_file, f"matrix references unpinned planner file: {filename}")
            source = by_file[filename]
            matrix_lines.setdefault(source["id"], set()).update(_line_range(spec))
        require(
            matrix_lines == expected_lines,
            f"matrix refs for {case.get('id')} do not map to the exact expected packet outcome lines",
        )
        for source_id, lines in expected_lines.items():
            require(bool(lines) and lines <= set(by_id[source_id]["outcome_lines"]), f"{case.get('id')} maps outside {source_id}'s pinned outcomes")


def _case_map(document: dict[str, Any], label: str) -> dict[str, dict[str, Any]]:
    rows = document.get("cases", document.get("scenarios"))
    require(isinstance(rows, list), f"{label} cases must be an array")
    _ids(rows, label)
    return {row["id"]: row for row in rows}


def _read_json(path: Path) -> dict[str, Any]:
    try:
        parsed = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ContractFailure(f"cannot read oracle source {path}: {exc}") from exc
    require(isinstance(parsed, dict), f"oracle source {path} must be a JSON object")
    return parsed


def verify_existing_oracles(expected: dict[str, Any], tests_dir: Path) -> int:
    """Cross-check compatibility pins against untouched product oracle artifacts.

    Temporal and legacy planner artifacts belong to separately admitted work and
    are not copied into this task's checkout. Their immutable planner packets are
    verified separately; if their oracle JSON is present in a later integration,
    this function also checks it instead of silently skipping a mismatch.
    """
    expected_records = expected.get("preserved_existing_outputs")
    require(isinstance(expected_records, list), "preserved existing outputs must be explicit")
    records = {item.get("oracle"): item for item in expected_records if isinstance(item, dict)}
    require(len(records) == len(expected_records), "existing-output oracle names must be unique")

    original = _read_json(tests_dir / "expected-results.json")
    original_matrix = _read_json(tests_dir / "adversarial-matrix.json")
    orig_case = _case_map(original, "original expected-results").get("multi-hop-outage-current-ownership")
    require(orig_case is not None, "original oracle lacks the pinned INC-204 multi-hop case")
    orig_paths = orig_case.get("expected", {}).get("impact_paths")
    require(orig_paths == records.get("original", {}).get("paths") == INC204_PATHS, "INC-204 paths differ from original oracle pin")
    require("multi-hop-outage-current-ownership" in _ids(original_matrix.get("cases"), "original matrix"), "original matrix lost its INC-204 mapping")

    source = _read_json(tests_dir / "source-change-expected-results.json")
    source_matrix = _read_json(tests_dir / "source-change-adversarial-matrix.json")
    source_case = _case_map(source, "source-change expected-results").get("dock-role-path-after-effective-time")
    require(source_case is not None, "source-change oracle lacks the pinned INC-207 ownership case")
    source_path = source_case.get("expected", {}).get("role_path")
    require(source_path == records.get("source-change", {}).get("paths", [None])[0] == INC207_PATH, "INC-207 source-change path differs from its pinned oracle")
    require("dock-role-path-after-effective-time" in _ids(source_matrix.get("cases"), "source-change matrix"), "source-change matrix lost its INC-207 mapping")

    shift = _read_json(tests_dir / "shift-board-expected-results.json")
    shift_matrix = _read_json(tests_dir / "shift-board-adversarial-matrix.json")
    shift_cases = _case_map(shift, "shift-board expected-results")
    conveyor = shift_cases.get("conveyor-observed-and-hypothetical-times", {}).get("expected", {}).get("observed_at_1830", {}).get("paths")
    dock = shift_cases.get("dock-role-time-and-source-evidence", {}).get("expected", {}).get("at_effective_time", {}).get("role_path")
    require(conveyor == records.get("shift-board-conveyor", {}).get("paths") == INC204_PATHS, "INC-204 shift-board paths differ from their pin")
    require(dock == records.get("shift-board-dock", {}).get("paths", [None])[0] == INC207_PATH, "INC-207 shift-board path differs from its pin")
    shift_ids = _ids(shift_matrix.get("cases"), "shift-board matrix")
    require("conveyor-observed-and-hypothetical-times" in shift_ids and "dock-role-time-and-source-evidence" in shift_ids, "shift-board matrix lost an isolation source case")

    # A temporal or legacy oracle may be merged by another lane. If present, it
    # must agree with this exact compatibility snapshot; absence is not a reason
    # to touch or reconstruct that in-flight task's files.
    optional = (
        ("temporal", "temporal-expected-results.json", "temporal-outage-active", "active_paths", INC204_PATHS),
        ("prior-planner-legacy", "prior-planner-expected-results.json", "legacy-multi-hop-outage-ownership", "paths", LEGACY_PATHS),
    )
    for record_id, filename, scenario_id, field, paths in optional:
        candidate = tests_dir / filename
        matrix_candidate = tests_dir / filename.replace("expected-results", "adversarial-matrix")
        if candidate.exists() != matrix_candidate.exists():
            raise ContractFailure(f"incomplete optional oracle pair: {filename}")
        if candidate.exists():
            doc = _read_json(candidate)
            case = _case_map(doc, filename).get(scenario_id)
            require(case is not None, f"{filename} lost {scenario_id}")
            actual = case.get("expected", {}).get(field)
            if record_id == "temporal":
                actual = case.get("expected", {}).get("active_paths")
            require(actual == records.get(record_id, {}).get("paths") == paths, f"{filename} conflicts with the preserved cascade compatibility pin")
            _case_map(_read_json(matrix_candidate), matrix_candidate.name)

    require(
        expected.get("existing_product_oracles") == [
            "tests/expected-results.json",
            "tests/source-change-expected-results.json",
            "tests/shift-board-expected-results.json",
        ],
        "existing product oracle source list changed",
    )
    return 3 + sum(1 for path in (tests_dir / "temporal-expected-results.json", tests_dir / "prior-planner-expected-results.json") if path.exists())


def validate_artifacts(
    expected: dict[str, Any],
    matrix: dict[str, Any],
    expected_sha256: str,
    matrix_sha256: str,
    require_eight_cases: bool,
) -> int:
    """Check exact pinned outcomes, branch isolation and nonzero matrix coverage."""
    require(require_eight_cases, "--require-eight-cases is mandatory")
    require(expected.get("schema_version") == matrix.get("schema_version") == 1, "both artifacts must use schema_version 1")
    require(expected.get("product") == "WardAtlas", "expected-results must identify WardAtlas")
    scope = expected.get("scope")
    require(isinstance(scope, dict) and scope.get("synthetic_only") is True, "cascade contract must be synthetic-only")
    require(scope.get("clinical_decisions") is False, "cascade contract must forbid clinical decisions")
    require("actual falkordb" in str(scope.get("database_requirement", "")).lower(), "later behavior check must require actual FalkorDB")
    require(scope.get("existing_incident_isolation") == ["INC-204", "INC-207"], "existing incident isolation boundary changed")

    outcomes = expected.get("cases")
    matrix_cases = matrix.get("cases")
    outcome_ids = _ids(outcomes, "expected cases")
    case_ids = _ids(matrix_cases, "matrix cases")
    require(outcome_ids == list(REQUIRED_CASES), "eight fixed outcome IDs or order differ from the packet")
    require(case_ids == outcome_ids, "matrix must map one-to-one to all eight expected cases in order")
    require(matrix.get("oracle_file") == "tests/cascade-expected-results.json", "matrix points to a different oracle")

    for outcome, case in zip(outcomes, matrix_cases):
        case_id = outcome["id"]
        require(case.get("scenario_id") == case_id, f"matrix scenario mapping differs for {case_id}")
        assertions = case.get("pinned_assertions")
        count = case.get("nonzero_assertion_count")
        require(isinstance(assertions, list) and assertions, f"{case_id} requires nonzero explicit assertions")
        require(all(isinstance(item, str) and item.strip() for item in assertions), f"{case_id} has an empty assertion")
        require(type(count) is int and count > 0, f"{case_id} assertion count must be a positive integer")
        require(count == len(assertions), f"{case_id} assertion count differs from its explicit assertions")
        require(isinstance(outcome.get("expected"), dict) and outcome["expected"], f"{case_id} has no pinned expected values")
        require(isinstance(outcome.get("planner_refs"), list) and outcome["planner_refs"], f"{case_id} lacks packet mapping")
        require(isinstance(case.get("planner_refs"), list) and case["planner_refs"], f"{case_id} matrix lacks packet mapping")
        require(isinstance(case.get("failure_detected"), str) and case["failure_detected"].strip(), f"{case_id} lacks a failure condition")

    fixture = expected.get("fixture", {})
    fixed_fixture = {
        "incident_id": "INC-312",
        "shared_equipment_id": "EQ-FPU-7",
        "source_id": "SRC-TABLETOP-FPU-7",
        "outage_branch_id": "BR-FPU-OUTAGE-DRILL",
        "outage_event_id": "EVT-FPU-OUTAGE-HYP-01",
        "outage_event_at": "2026-10-15T06:15:00Z",
        "baseline_available_at": "2026-10-15T06:00:00Z",
        "evaluation_at": "2026-10-15T06:30:00Z",
        "isolation_branch_id": "BR-FPU-ISOLATE-DOCK-FEED",
        "removed_dependency": ["EQ-FPU-7", "EQ-DOCK-CHARGER-2"],
        "restricted_source_id": "SRC-RESTRICTED-FPU-77",
    }
    for key, value in fixed_fixture.items():
        require(fixture.get(key) == value, f"fixture.{key} differs from packet")
    require(expected.get("canonical_paths") == {"dock": CASCADE_PATHS[0], "linen": CASCADE_PATHS[1]}, "exact six-hop cascade paths differ from packet")

    cases = {row["id"]: row["expected"] for row in outcomes}
    require(cases["shared-dependency-cascade"].get("paths") == CASCADE_PATHS, "cascade path order/content differs from pinned paths")
    require(cases["branch-only-edge-counterfactual"].get("counterfactual_paths") == [CASCADE_PATHS[1]], "edge-isolation branch must return exactly the linen path")
    require(cases["branch-only-edge-counterfactual"].get("outage_branch_paths_unchanged") == CASCADE_PATHS, "outage branch pin must remain unchanged")
    require(cases["branch-only-edge-counterfactual"].get("mainline_paths") == [], "mainline pin must remain zero impact")
    require(cases["time-repeat-and-reconnect"].get("offset_input") == "2026-10-15T07:30:00+01:00" and cases["time-repeat-and-reconnect"].get("normalized_as_of") == "2026-10-15T06:30:00Z", "offset timestamp pin changed")
    require(cases["time-repeat-and-reconnect"].get("malformed_timestamp") == "2026-10-15T06:61:00Z" and cases["time-repeat-and-reconnect"].get("graph_and_branch_mutated_by_rejected_timestamp") is False, "malformed-time rejection/isolation pin changed")
    require(cases["denied-source-nondisclosure"].get("restricted_source_id") == "SRC-RESTRICTED-FPU-77" and cases["denied-source-nondisclosure"].get("denied_identifier_title_fact_count_existence_path_or_dependent_paraphrase_disclosed") is False, "denied-source non-disclosure pin changed")
    require(cases["cold-empty-graph"].get("state") == "empty/unknown" and cases["cold-empty-graph"].get("impact_paths") == [] and cases["cold-empty-graph"].get("invented_available_state_service_role_or_safe_outcome") is False, "cold empty must remain empty/unknown without invented state/service/role/safety claims")
    require(cases["baseline-mainline"].get("outage_impact_paths") == [] and cases["baseline-mainline"].get("scenario_branch_applied") is False, "baseline mainline must remain unchanged")
    require(cases["local-only-scope-boundary"].get("external_writes") == [] and cases["local-only-scope-boundary"].get("real_world_action_performed") is False and cases["local-only-scope-boundary"].get("diagnosis_treatment_patient_impact_or_clinical_triage_inferred") is False and cases["local-only-scope-boundary"].get("hospital_safety_claim_made") is False, "local/non-clinical scope pin changed")
    require(cases["no-cross-case-contamination"].get("unrelated_incidents") == ["INC-204", "INC-207"] and cases["no-cross-case-contamination"].get("path_connects_unrelated_incident_to_cascade_equipment") is False, "cross-case branch isolation pin changed")
    require(cases["no-cross-case-contamination"].get("preserved_existing_outputs") == expected.get("preserved_existing_outputs"), "case isolation paths differ from the separately listed prior oracle pins")
    require(expected.get("preserved_existing_outputs", [])[-1].get("paths") == LEGACY_PATHS, "separate prior-planner paths changed")

    if expected_sha256 != EXPECTED_ARTIFACT_SHA256:
        raise ContractFailure("cascade expected-results bytes differ from the independently pinned packet")
    if matrix_sha256 != MATRIX_ARTIFACT_SHA256:
        raise ContractFailure("cascade adversarial matrix bytes differ from its independent pin")
    validate_plan_mappings(expected, matrix)
    assertions_total = sum(case["nonzero_assertion_count"] for case in matrix_cases)
    require(assertions_total > 0, "matrix must contain a nonzero total assertion count")
    return assertions_total


def verify_plan_file(label: str, path: Path) -> str:
    if label not in PINNED_PLANS:
        raise ContractFailure(f"unknown pinned planner source: {label}")
    expected_name, expected_digest = PINNED_PLANS[label]
    require(path.name == expected_name, f"{label} path must end in {expected_name}")
    try:
        content = path.read_bytes()
        text = content.decode("utf-8")
    except OSError as exc:
        raise ContractFailure(f"cannot read pinned planner packet {path}: {exc}") from exc
    except UnicodeDecodeError as exc:
        raise ContractFailure(f"pinned planner packet is not UTF-8: {path}") from exc
    actual = hashlib.sha256(content).hexdigest()
    if actual != expected_digest:
        raise ContractFailure(f"{label} content does not match pinned SHA-256")
    missing = [literal for literal in PINNED_PLAN_LITERALS[label] if literal not in text]
    require(not missing, f"{label} is missing fixed outcome literals: {missing}")
    return actual


def _load_artifact(path: Path) -> tuple[dict[str, Any], str]:
    try:
        raw = path.read_bytes()
        value = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ContractFailure(f"cannot load JSON artifact {path}: {exc}") from exc
    require(isinstance(value, dict), f"{path} must contain a JSON object")
    return value, hashlib.sha256(raw).hexdigest()


def verify(expected_path: Path, matrix_path: Path, pinned_plan: Path) -> dict[str, Any]:
    expected, expected_digest = _load_artifact(expected_path)
    matrix, matrix_digest = _load_artifact(matrix_path)
    count = validate_artifacts(expected, matrix, expected_digest, matrix_digest, require_eight_cases=True)
    tests_dir = expected_path.resolve().parent
    checked_oracles = verify_existing_oracles(expected, tests_dir)
    plan_dir = pinned_plan.resolve().parent
    for label, (filename, _digest) in PINNED_PLANS.items():
        path = pinned_plan if label == "cascade-packet" else plan_dir / filename
        verify_plan_file(label, path)
    source_rows = []
    outcome_lines = (
        [41, 42, 43, 44, 45, 46, 47, 48],
        [29, 30, 31, 32, 33, 34],
        [39, 40, 41, 42, 43, 44, 45, 46],
        [42, 43, 44, 45, 46, 47, 48, 49, 50],
        [31, 32, 33, 34, 35, 36, 37, 38],
    )
    for (label, (filename, digest)), lines in zip(PINNED_PLANS.items(), outcome_lines):
        source_rows.append({"id": label, "path": f"plans/{filename}", "sha256": digest, "outcome_lines": lines})
    require(expected.get("pinned_sources") == source_rows, "expected-results pinned source list/digests/outcome lines differ")
    return {
        "status": "PASS",
        "outcome_groups": len(expected["cases"]),
        "matrix_cases": len(matrix["cases"]),
        "nonzero_assertions": count,
        "existing_product_oracle_sources_checked": checked_oracles,
        "pinned_planner_packets_checked": len(PINNED_PLANS),
        "candidate_output_used": False,
        "live_database_behavior_claimed": False,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expected", required=True, type=Path)
    parser.add_argument("--matrix", required=True, type=Path)
    parser.add_argument("--pinned-plan", required=True, type=Path)
    parser.add_argument("--require-eight-cases", action="store_true")
    args = parser.parse_args(argv)
    try:
        require(args.require_eight_cases, "--require-eight-cases is mandatory for this contract")
        print(json.dumps(verify(args.expected, args.matrix, args.pinned_plan), sort_keys=True))
        return 0
    except (OSError, TypeError, ValueError) as exc:
        print(json.dumps({"status": "FAIL", "error": f"{type(exc).__name__}: {exc}"}, sort_keys=True), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

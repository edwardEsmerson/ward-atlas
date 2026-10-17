#!/usr/bin/env python3
"""Independently pin the integrated WardAtlas shift-board oracle and matrix.

This is an artifact-contract validator, not an app/query/API verifier. It reads
only the two fixed JSON artifacts and the four supplied planning packets. No
candidate response is accepted as input or used to produce an expected value.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any


EXPECTED_ARTIFACT_SHA256 = "5d53a8f93245ee8391ec7cffb6d45b58355536d30d649db19cd53ca6b1ce2428"
MATRIX_ARTIFACT_SHA256 = "9cf1a4b144df3ff7357e389acec7c5e6b0e6ddcdab18577fa193c09541c0bbd7"

PINNED_PLANS = {
    "pinned-plan": {
        "id": "shift-board-packet",
        "sha256": "5dd4c50cd2044e016e3593c337a5ccae966cfba8ef74cbcb8d0ffd95eaed55de",
    },
    "source-plan": {
        "id": "source-change-plan",
        "sha256": "3879d8eb6197f631311c659528b939d96f069702406d3e257b1ab1d84c40e3b7",
    },
    "temporal-plan": {
        "id": "temporal-plan",
        "sha256": "cb62e05f8b0307a27f6050aef455ccf7b0df42d8f83a1479124c127187b46464",
    },
    "prior-plan": {
        "id": "separate-legacy-plan",
        "sha256": "01c10540400a08541da696b075d69183bfd62a290caca3f75f8aabdd8dfdd957",
    },
}

REQUIRED_CASES = (
    "cold-empty-board",
    "dock-role-time-and-source-evidence",
    "explicit-edge-review-queue",
    "conveyor-observed-and-hypothetical-times",
    "no-cross-case-path-or-causal-contamination",
    "denied-source-nondisclosure",
    "explicit-local-confirmation-no-external-writes",
    "empty-reseed-reconnect-and-owned-cleanup",
)


def fail(message: str) -> None:
    raise ValueError(message)


def _unique_ids(rows: list[dict[str, Any]], label: str) -> list[str]:
    ids = [row.get("id") for row in rows]
    if any(not isinstance(value, str) or not value for value in ids):
        fail(f"{label} entries must have nonempty string IDs")
    if len(ids) != len(set(ids)):
        fail(f"{label} IDs must be unique")
    return ids


def validate_artifacts(
    expected: dict[str, Any],
    matrix: dict[str, Any],
    expected_sha256: str,
    matrix_sha256: str,
    require_eight_cases: bool,
) -> int:
    """Validate case structure, matrix coverage, and immutable artifact pins."""
    if expected.get("schema_version") != 1 or matrix.get("schema_version") != 1:
        fail("both artifacts must use schema_version 1")
    if expected.get("product") != "WardAtlas":
        fail("unexpected product name")
    scope = expected.get("scope")
    if not isinstance(scope, dict) or scope.get("synthetic_only") is not True:
        fail("expected-results must be synthetic-only")
    if scope.get("clinical_decisions") is not False:
        fail("expected-results must forbid clinical decisions")
    if "actual falkordb" not in str(scope.get("database_requirement", "")).lower():
        fail("oracle must retain the actual FalkorDB requirement")
    if not isinstance(expected.get("pinned_sources"), list):
        fail("expected-results must retain the four pinned source plans")

    scenarios = expected.get("cases")
    cases = matrix.get("cases")
    if not isinstance(scenarios, list) or not isinstance(cases, list):
        fail("expected cases and matrix cases must be arrays")
    scenario_ids = _unique_ids(scenarios, "expected case")
    case_ids = _unique_ids(cases, "matrix case")
    if require_eight_cases and len(scenarios) != 8:
        fail(f"expected exactly eight outcome groups; found {len(scenarios)}")
    if tuple(scenario_ids) != REQUIRED_CASES:
        fail("the eight outcome groups or their pinned order differ from the contract")
    if case_ids != scenario_ids:
        fail("matrix must map one-to-one to the eight expected cases in the same order")
    if matrix.get("oracle_file") != "tests/shift-board-expected-results.json":
        fail("matrix oracle_file must identify the shift-board expected-results artifact")

    for scenario, case in zip(scenarios, cases):
        if scenario.get("id") != case.get("id") or case.get("scenario_id") != scenario.get("id"):
            fail(f"matrix case {case.get('id')} does not map to its exact expected case")
        assertions = case.get("pinned_assertions")
        count = case.get("nonzero_assertion_count")
        if not isinstance(assertions, list) or not assertions:
            fail(f"matrix case {case.get('id')} must have explicit assertions")
        if any(not isinstance(assertion, str) or not assertion.strip() for assertion in assertions):
            fail(f"matrix case {case.get('id')} has an empty/non-string assertion")
        if type(count) is not int or count <= 0:
            fail(f"matrix case {case.get('id')} must have a nonzero integer assertion count")
        if count != len(assertions):
            fail(f"matrix case {case.get('id')} count does not equal its explicit assertions")
        if not isinstance(scenario.get("expected"), dict) or not scenario["expected"]:
            fail(f"case {scenario.get('id')} must pin a nonempty expected outcome")
        if not isinstance(scenario.get("planner_refs"), list) or not scenario["planner_refs"]:
            fail(f"case {scenario.get('id')} must trace to pinned planning outcomes")
        if not isinstance(case.get("planner_refs"), list) or not case["planner_refs"]:
            fail(f"matrix case {case.get('id')} must trace to pinned planning outcomes")
        if not isinstance(case.get("failure_detected"), str) or not case["failure_detected"].strip():
            fail(f"matrix case {case.get('id')} must state the adversarial failure detected")

    if [source.get("id") for source in expected["pinned_sources"]] != [
        PINNED_PLANS[key]["id"] for key in PINNED_PLANS
    ]:
        fail("expected-results does not identify the exact four pinned planning sources")
    for key, pinned in PINNED_PLANS.items():
        source_row = expected["pinned_sources"][list(PINNED_PLANS).index(key)]
        if source_row.get("sha256") != pinned["sha256"]:
            fail(f"expected-results has a changed digest for {pinned['id']}")

    # These independent digests pin the complete JSON, including every exact
    # synthetic ID/time/path and each matrix-to-outcome mapping. They are not
    # computed from runtime candidate behavior.
    if expected_sha256 != EXPECTED_ARTIFACT_SHA256:
        fail("shift-board expected-results bytes differ from the independently pinned packet")
    if matrix_sha256 != MATRIX_ARTIFACT_SHA256:
        fail("shift-board adversarial matrix bytes differ from its independent pin")

    assertions_total = sum(case["nonzero_assertion_count"] for case in cases)
    if assertions_total <= 0:
        fail("the matrix must contain a nonzero total assertion count")
    return assertions_total


def _line_spec(spec: str) -> set[int]:
    """Expand `4,6-8` into a one-based set of pinned Markdown line numbers."""
    result: set[int] = set()
    for part in spec.split(","):
        if not part:
            fail("empty line in planner reference")
        if "-" in part:
            bounds = part.split("-")
            if len(bounds) != 2:
                fail(f"invalid planner line range: {part}")
            start, end = (int(bound) for bound in bounds)
            if start <= 0 or end < start:
                fail(f"invalid planner line range: {part}")
            result.update(range(start, end + 1))
        else:
            line = int(part)
            if line <= 0:
                fail(f"invalid planner line: {part}")
            result.add(line)
    return result


def _expected_ref_lines(refs: list[str], sources_by_id: dict[str, dict[str, Any]]) -> dict[str, set[int]]:
    result: dict[str, set[int]] = {}
    pattern = re.compile(r"^([^#]+)#outcomes?-(.+)$")
    for ref in refs:
        match = pattern.fullmatch(ref)
        if not match:
            fail(f"invalid expected planner reference: {ref}")
        source_id, spec = match.groups()
        if source_id not in sources_by_id:
            fail(f"expected planner reference names unknown source: {source_id}")
        meta = sources_by_id[source_id]
        selected: set[int] = set()
        for part in spec.split(","):
            indexes = _line_spec(part)
            for outcome_index in indexes:
                if outcome_index > len(meta["outcome_lines"]):
                    fail(f"{ref} refers to an outcome absent from {source_id}")
                selected.add(meta["outcome_lines"][outcome_index - 1])
        result.setdefault(source_id, set()).update(selected)
    return result


def _matrix_ref_lines(refs: list[str], sources_by_file: dict[str, str]) -> dict[str, set[int]]:
    result: dict[str, set[int]] = {}
    pattern = re.compile(r"^([^:]+\.md):([0-9,-]+)$")
    for ref in refs:
        match = pattern.fullmatch(ref)
        if not match:
            fail(f"invalid matrix planner line reference: {ref}")
        filename, spec = match.groups()
        source_id = sources_by_file.get(filename)
        if source_id is None:
            fail(f"matrix reference names unknown planner file: {filename}")
        result.setdefault(source_id, set()).update(_line_spec(spec))
    return result


def validate_plan_mappings(expected: dict[str, Any], matrix: dict[str, Any]) -> None:
    """Cross-check every matrix row against the exact numbered plan outcomes.

    The plan digests pin the source documents; this check independently proves
    the expected-case outcome references and matrix's filename/line references
    select the same numbered packet outcomes, including the separate legacy
    oracle boundary.
    """
    sources_by_id = {row["id"]: row for row in expected["pinned_sources"]}
    sources_by_file = {
        Path(row["plan"]).name: row["id"] for row in expected["pinned_sources"]
    }
    for scenario, case in zip(expected["cases"], matrix["cases"]):
        expected_lines = _expected_ref_lines(scenario["planner_refs"], sources_by_id)
        matrix_lines = _matrix_ref_lines(case["planner_refs"], sources_by_file)
        if matrix_lines != expected_lines:
            fail(
                f"matrix planner references for {case['id']} do not map to the "
                "same exact pinned outcome lines as expected-results"
            )
        for source_id, line_numbers in expected_lines.items():
            allowed_lines = set(sources_by_id[source_id]["outcome_lines"])
            if not line_numbers or not line_numbers <= allowed_lines:
                fail(f"{case['id']} maps outside {source_id}'s pinned outcome lines")


def verify_plan_file(label: str, path: Path) -> str:
    """Require the exact read-only planning packet named by the task contract."""
    if label not in PINNED_PLANS:
        fail(f"unknown pinned-plan label: {label}")
    try:
        data = path.read_bytes()
    except OSError as exc:
        fail(f"cannot read {label} {path}: {exc}")
    digest = hashlib.sha256(data).hexdigest()
    expected_digest = PINNED_PLANS[label]["sha256"]
    if digest != expected_digest:
        fail(f"{label} content does not match pinned {PINNED_PLANS[label]['id']} SHA-256")
    return digest


def _load_json(path: Path) -> tuple[dict[str, Any], str]:
    try:
        raw = path.read_bytes()
        value = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        fail(f"cannot load JSON artifact {path}: {exc}")
    if not isinstance(value, dict):
        fail(f"JSON artifact {path} must contain an object")
    return value, hashlib.sha256(raw).hexdigest()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expected", required=True, type=Path)
    parser.add_argument("--matrix", required=True, type=Path)
    parser.add_argument("--pinned-plan", required=True, type=Path)
    parser.add_argument("--source-plan", required=True, type=Path)
    parser.add_argument("--temporal-plan", required=True, type=Path)
    parser.add_argument("--prior-plan", required=True, type=Path)
    parser.add_argument("--require-eight-cases", action="store_true")
    args = parser.parse_args(argv)

    try:
        if not args.require_eight_cases:
            fail("--require-eight-cases is mandatory for this contract")
        expected, expected_digest = _load_json(args.expected)
        matrix, matrix_digest = _load_json(args.matrix)
        assertion_count = validate_artifacts(
            expected,
            matrix,
            expected_digest,
            matrix_digest,
            require_eight_cases=args.require_eight_cases,
        )
        validate_plan_mappings(expected, matrix)
        plan_paths = {
            "pinned-plan": args.pinned_plan,
            "source-plan": args.source_plan,
            "temporal-plan": args.temporal_plan,
            "prior-plan": args.prior_plan,
        }
        for label, path in plan_paths.items():
            verify_plan_file(label, path)
    except (OSError, TypeError, ValueError) as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1

    print(
        "PASS: exact shift-board packet and matrix; 8 outcome groups, "
        f"8 mapped adversarial cases, {assertion_count} nonzero assertions; "
        "all four pinned plan files match their recorded SHA-256; "
        "no candidate output used as oracle."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

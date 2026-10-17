#!/usr/bin/env python3
"""Validate the immutable WardAtlas role-scope oracle and adversarial matrix.

This checks only pinned planning artifacts. It does not inspect or run product
behavior, and it has no candidate-output input from which to derive expected
values.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any


EXPECTED_ARTIFACT_SHA256 = "fc83b36dd9cfea900a1885816c6b84ffe7a1ab3d5c62d45be87f97a7368a9867"
MATRIX_ARTIFACT_SHA256 = "2cc162e4f4a01daa81bbc0e8c57d9f7f2bc0a6167c603e8613afa3fbb8b02ee3"
ROLE_SCOPE_PLAN_SHA256 = "01073e7e1bc4294a8bb14948aa3db24bb25f48911a9435269571f1ab167b6fec"

REQUIRED_CASES = (
    "cold-empty-and-unknown-scope",
    "exact-role-scope-paths-and-grant-traces",
    "grant-edge-removal-is-scope-specific",
    "mainline-versus-hypothetical-branch",
    "selected-scope-trace-and-outside-unknown",
    "distinctive-ungranted-source-nondisclosure",
    "idempotent-reseed-and-reconnect",
    "desktop-mobile-api-and-local-boundary",
)

DOCK_PATH = [
    "INC-312",
    "EQ-FPU-7",
    "EQ-DOCK-CHARGER-2",
    "SVC-DOCK-TRANSFER",
    "DEPT-RECEIVING-DOCK",
    "ROLE-DOCK-FACILITIES",
]
LINEN_PATH = [
    "INC-312",
    "EQ-FPU-7",
    "EQ-LINEN-CONVEYOR-7",
    "SVC-LINEN-TRANSFER",
    "DEPT-LINEN-LOGISTICS",
    "ROLE-LINEN-DUTY",
]
AS_OF = "2026-10-15T06:30:00Z"
EVENT_AT = "2026-10-15T06:15:00Z"
BRANCH_ID = "BR-FPU-OUTAGE-DRILL"
EVENT_ID = "EVT-FPU-OUTAGE-HYP-01"
SOURCE_ID = "SRC-TABLETOP-FPU-7"

PINNED_SCOPE_RESULTS = [
    {
        "scope_id": "SCOPE-DOCK-FACILITIES",
        "grant_ids": ["GRANT-DOCK-01"],
        "paths": [DOCK_PATH],
        "hypothetical": True,
        "source_id": SOURCE_ID,
        "event_id": EVENT_ID,
        "event_at": EVENT_AT,
        "branch_id": BRANCH_ID,
        "as_of": AS_OF,
        "coverage": "selected_scope_only",
        "outside_scope": "unknown",
    },
    {
        "scope_id": "SCOPE-LINEN-LOGISTICS",
        "grant_ids": ["GRANT-LINEN-01"],
        "paths": [LINEN_PATH],
        "hypothetical": True,
        "source_id": SOURCE_ID,
        "event_id": EVENT_ID,
        "event_at": EVENT_AT,
        "branch_id": BRANCH_ID,
        "as_of": AS_OF,
        "coverage": "selected_scope_only",
        "outside_scope": "unknown",
    },
    {
        "scope_id": "SCOPE-OPS-COORDINATOR",
        "grant_ids": ["GRANT-OPS-DOCK-01", "GRANT-OPS-LINEN-01"],
        "paths": [DOCK_PATH, LINEN_PATH],
        "hypothetical": True,
        "source_id": SOURCE_ID,
        "event_id": EVENT_ID,
        "event_at": EVENT_AT,
        "branch_id": BRANCH_ID,
        "as_of": AS_OF,
        "coverage": "selected_scope_only",
        "outside_scope": "unknown",
    },
]

PINNED_SCOPE = {
    "synthetic_only": True,
    "clinical_decisions": False,
    "database_requirement": "Actual FalkorDB; a fake or Redis-only substitute is not an acceptable integrated behavior check.",
    "graph_ownership": "Use a unique task-owned graph, include the allocated WORKSHOP_LANE in its identity, and delete only that graph.",
    "scope_selector_semantics": "A synthetic demo persona selector, not authentication, real identity, or proof of authorization.",
    "coverage": "selected_scope_only",
    "outside_scope": "unknown",
    "external_effects": False,
}

PINNED_SOURCES = [
    {
        "id": "role-scope-packet",
        "plan": "planner-a4d9fd7447db467faab0a96048741775.md",
        "sha256": ROLE_SCOPE_PLAN_SHA256,
        "pinned_lines": [13, 14, 15, 16, 17, 18, 19, 20, 21, 23, 24, 25, 26, 27, 28, 29, 30, 31, 32, 33, 34],
    },
    {
        "id": "cascade-outcome-predecessor",
        "plan": "planner-8b15037dc21642129e4f0cbd47508f2f.md",
        "sha256": "a0ab793c8fe21383decf987f3582f3d9f94a89ce68d5afe48efada7fb60cbe11",
        "pinned_lines": [11, 43, 46],
        "verification_note": "The operator-supplied --pinned-plan path is the role-scope packet. Cascade values below are cross-pinned here from the named predecessor; the verifier does not discover expected values from candidate output.",
    },
]

PINNED_FIXED_FIXTURE = {
    "incident_id": "INC-312",
    "as_of": AS_OF,
    "branch_id": BRANCH_ID,
    "branch_status": "hypothetical",
    "event_id": EVENT_ID,
    "event_at": EVENT_AT,
    "source_id": SOURCE_ID,
    "source_observed_at": None,
    "source_observed_at_status": "not pinned by either packet; do not fabricate a separate source-observation timestamp",
    "scope_results": PINNED_SCOPE_RESULTS,
}

DENIED_FIXTURE = {
    "source_id": "SRC-RESTRICTED-FPU-77",
    "title": "Restricted FPU side-channel memo / Violet-Quartz-77",
    "body": "Restricted-only: violet-quartz-77 routes through the mothballed loading spur.",
    "path": [
        "INC-312",
        "EQ-RESTRICTED-FPU-77",
        "SVC-RESTRICTED-LOADING-77",
        "DEPT-RESTRICTED-LOGISTICS-77",
        "ROLE-RESTRICTED-OWNER-77",
    ],
    "distinctive_path_markers": [
        "EQ-RESTRICTED-FPU-77",
        "SVC-RESTRICTED-LOADING-77",
        "DEPT-RESTRICTED-LOGISTICS-77",
        "ROLE-RESTRICTED-OWNER-77",
    ],
}

PINNED_EXPECTED = {
    "cold-empty-and-unknown-scope": {
        "cold_graph_paths": [],
        "cold_graph_role_ids": [],
        "cold_graph_source_ids": [],
        "cold_graph_state": "empty/unknown",
        "availability_claim": False,
        "unknown_scope_response": "generic denial",
        "unknown_scope_valid_scope_list": False,
        "unknown_scope_path_count": False,
        "unknown_scope_path_or_source_details": False,
    },
    "exact-role-scope-paths-and-grant-traces": {
        "results_in_order": PINNED_SCOPE_RESULTS,
        "extra_paths_per_scope": False,
        "scope_is_authentication": False,
    },
    "grant-edge-removal-is-scope-specific": {
        "removed_grant_id": "GRANT-DOCK-01",
        "dock_after_removal": {
            "paths": [],
            "result": "no matching path in the selected scope",
            "coverage": "selected_scope_only",
            "outside_scope": "unknown",
        },
        "linen_after_removal": [LINEN_PATH],
        "coordinator_after_removal": [DOCK_PATH, LINEN_PATH],
        "reseed_restores_dock_path": True,
        "disconnected_lexical_decoy_id": "DOC-FPU-LEXICAL-DECOY-01",
        "lexical_decoy_restores_path": False,
    },
    "mainline-versus-hypothetical-branch": {
        "mainline_as_of": AS_OF,
        "mainline_paths": [],
        "mainline_unchanged": True,
        "hypothetical_branch_id": BRANCH_ID,
        "hypothetical_branch_status": "hypothetical",
        "hypothetical_branch_paths": [DOCK_PATH, LINEN_PATH],
        "other_branch_id": "BR-OTHER-UNKNOWN",
        "other_branch_paths": [],
        "evaluating_other_branch_mutates_mainline_or_named_branch": False,
    },
    "selected-scope-trace-and-outside-unknown": {
        "required_trace_fields": [
            "selected_scope_id",
            "grant_edge_ids",
            "returned_paths",
            "source_id",
            "event_id",
            "event_at",
            "branch_id",
            "as_of",
            "coverage",
            "outside_scope",
        ],
        "grant_edges_by_scope": {
            "SCOPE-DOCK-FACILITIES": ["GRANT-DOCK-01"],
            "SCOPE-LINEN-LOGISTICS": ["GRANT-LINEN-01"],
            "SCOPE-OPS-COORDINATOR": ["GRANT-OPS-DOCK-01", "GRANT-OPS-LINEN-01"],
        },
        "source_id": SOURCE_ID,
        "event_id": EVENT_ID,
        "event_at": EVENT_AT,
        "branch_id": BRANCH_ID,
        "as_of": AS_OF,
        "coverage": "selected_scope_only",
        "outside_scope": "unknown",
        "fact_inference_and_missing_evidence_distinct": True,
        "trace_claims_global_completeness": False,
    },
    "distinctive-ungranted-source-nondisclosure": {
        "viewer_scope": "selected synthetic scope",
        "denied_fixture": DENIED_FIXTURE,
        "fixture_marker_selection": "Synthetic test-only tripwires fixed here because the packet requires distinctive markers but does not prescribe their literal values; never include them in an expected response.",
        "visible_semantic_response_unchanged_before_after": True,
        "forbidden_response_literals": [
            "SRC-RESTRICTED-FPU-77",
            "Restricted FPU side-channel memo / Violet-Quartz-77",
            "Restricted-only: violet-quartz-77 routes through the mothballed loading spur.",
            "violet-quartz-77",
            "EQ-RESTRICTED-FPU-77",
            "SVC-RESTRICTED-LOADING-77",
            "DEPT-RESTRICTED-LOGISTICS-77",
            "ROLE-RESTRICTED-OWNER-77",
        ],
        "forbidden_disclosure_classes": [
            "hidden ID",
            "title",
            "fact",
            "path",
            "path count",
            "source count",
            "existence",
            "dependent paraphrase",
        ],
        "hidden_count_or_existence_disclosed": False,
        "dependent_paraphrase_disclosed": False,
    },
    "idempotent-reseed-and-reconnect": {
        "second_seed_node_delta": 0,
        "second_seed_relationship_delta": 0,
        "reconnect_uses_fresh_client": True,
        "reconnect_ordered_scope_results_equal": True,
        "reconnect_complete_trace_equal": True,
        "grant_edge_removal_changes_only_case_3_pinned_scope_results": True,
        "graph_name_is_unique_and_lane_scoped": True,
    },
    "desktop-mobile-api-and-local-boundary": {
        "surfaces": ["desktop browser", "mobile browser", "API"],
        "all_surfaces_match_exact_scope_paths_and_traces": True,
        "unknown_scope_is_generic_without_scope_enumeration": True,
        "scope_selector_is_real_identity_or_authentication": False,
        "external_effects": [],
        "handover_action": False,
        "source_mutation": False,
        "message_or_call": False,
        "clinical_inference_or_action": False,
    },
}

CASE_PLAN_LINES = {
    "cold-empty-and-unknown-scope": "23-25",
    "exact-role-scope-paths-and-grant-traces": "13-21,26",
    "grant-edge-removal-is-scope-specific": "27",
    "mainline-versus-hypothetical-branch": "13-19,28",
    "selected-scope-trace-and-outside-unknown": "21,29",
    "distinctive-ungranted-source-nondisclosure": "30",
    "idempotent-reseed-and-reconnect": "31",
    "desktop-mobile-api-and-local-boundary": "32",
}

PINNED_ASSERTIONS = {
    "cold-empty-and-unknown-scope": [
        "cold graph has zero role-scoped paths",
        "cold graph says empty/unknown and makes no availability claim",
        "unknown scope receives a generic denial",
        "denial reveals no valid-scope list, path count, path, or source details",
        "no role or source identifier is invented",
    ],
    "exact-role-scope-paths-and-grant-traces": [
        "dock scope returns exactly INC-312 → EQ-FPU-7 → EQ-DOCK-CHARGER-2 → SVC-DOCK-TRANSFER → DEPT-RECEIVING-DOCK → ROLE-DOCK-FACILITIES",
        "dock path trace contains only GRANT-DOCK-01",
        "linen scope returns exactly INC-312 → EQ-FPU-7 → EQ-LINEN-CONVEYOR-7 → SVC-LINEN-TRANSFER → DEPT-LINEN-LOGISTICS → ROLE-LINEN-DUTY",
        "linen path trace contains only GRANT-LINEN-01",
        "coordinator returns both exact paths in dock-then-linen order",
        "coordinator trace contains GRANT-OPS-DOCK-01 then GRANT-OPS-LINEN-01",
        "all three outcomes are labeled hypothetical",
        "each trace pins EVT-FPU-OUTAGE-HYP-01, SRC-TABLETOP-FPU-7, 06:15Z event, BR-FPU-OUTAGE-DRILL, and 06:30Z as-of",
        "no scope returns an additional path",
    ],
    "grant-edge-removal-is-scope-specific": [
        "removing GRANT-DOCK-01 leaves dock scope with no matching path and outside scope unknown",
        "linen scope retains its exact single path",
        "coordinator retains both exact paths through its separate grants",
        "reseed restores the exact dock path",
        "the disconnected lexical decoy cannot restore a graph path",
        "the grant removal does not mutate the event, source, branch, or other scope grants",
    ],
    "mainline-versus-hypothetical-branch": [
        "mainline has zero impact paths at 2026-10-15T06:30:00Z",
        "BR-FPU-OUTAGE-DRILL returns only the two pinned scoped paths and is labeled hypothetical",
        "BR-OTHER-UNKNOWN returns no paths",
        "querying another branch does not change mainline or BR-FPU-OUTAGE-DRILL",
    ],
    "selected-scope-trace-and-outside-unknown": [
        "each answer trace names the selected scope and only its explicit grant edge IDs",
        "trace contains exactly that scope's returned path or paths",
        "trace contains SRC-TABLETOP-FPU-7 and EVT-FPU-OUTAGE-HYP-01",
        "trace distinguishes event time 06:15Z from as-of time 06:30Z and identifies the branch",
        "coverage is selected_scope_only and outside_scope is unknown",
        "source facts, graph inference, and missing/out-of-scope evidence remain distinct without a completeness claim",
    ],
    "distinctive-ungranted-source-nondisclosure": [
        "all visible selected-scope results are semantically unchanged before and after injection",
        "no distinctive denied ID, title, body, or path marker is disclosed",
        "no hidden source/path count or source existence is disclosed",
        "no dependent paraphrase derived from the denied body or path is disclosed",
        "denied fixture markers are test-only tripwires and never expected response content",
    ],
    "idempotent-reseed-and-reconnect": [
        "second seed creates zero duplicate nodes",
        "second seed creates zero duplicate relationships",
        "fresh-client reconnect preserves ordered scope paths",
        "fresh-client reconnect preserves complete grant/source/event/branch/time traces",
        "grant-removal counterfactual changes only the exact pinned scope outcomes",
    ],
    "desktop-mobile-api-and-local-boundary": [
        "desktop displays the exact selected-scope API paths and traces",
        "mobile displays the same exact selected-scope API paths and traces",
        "unknown scope denial is generic and does not enumerate available personas",
        "selector is visibly a synthetic persona, not real identity or authentication",
        "no handover action, source mutation, ticket, message, call, or other external write is performed",
        "no clinical inference or clinical action is produced",
        "hypothetical branch and outside-scope unknown semantics remain visible",
        "browser and API never disclose the fixed denied-source tripwires",
    ],
}


def fail(message: str) -> None:
    raise ValueError(message)


def _unique_ids(rows: list[dict[str, Any]], label: str) -> list[str]:
    ids = [row.get("id") for row in rows]
    if any(not isinstance(value, str) or not value for value in ids):
        fail(f"{label} entries must have nonempty string IDs")
    if len(ids) != len(set(ids)):
        fail(f"{label} IDs must be unique")
    return ids


def _line_spec(spec: str) -> set[int]:
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


def _planner_ref_lines(refs: list[str], pattern: str, source_name: str) -> set[int]:
    result: set[int] = set()
    prefix = f"{source_name}{pattern}"
    for ref in refs:
        if not ref.startswith(prefix):
            fail(f"planner reference must name {source_name}: {ref}")
        result.update(_line_spec(ref[len(prefix) :]))
    return result


def validate_plan_mappings(expected: dict[str, Any], matrix: dict[str, Any]) -> None:
    """Check each expected group and matrix case point at the same packet lines."""
    expected_cases = expected["cases"]
    matrix_cases = matrix["cases"]
    source_name = "role-scope-packet"
    allowed_lines = set(PINNED_SOURCES[0]["pinned_lines"])
    for scenario, case in zip(expected_cases, matrix_cases):
        case_id = scenario["id"]
        expected_lines = _planner_ref_lines(scenario["planner_refs"], "#lines-", source_name)
        # Matrix refs use a filename prefix rather than the expected-results ID.
        matrix_lines: set[int] = set()
        matrix_prefix = f"{PINNED_SOURCES[0]['plan']}:"
        for ref in case["planner_refs"]:
            if not ref.startswith(matrix_prefix):
                fail(f"matrix planner reference must name the role-scope packet: {ref}")
            matrix_lines.update(_line_spec(ref[len(matrix_prefix) :]))
        if not expected_lines or matrix_lines != expected_lines:
            fail(
                f"matrix planner references for {case_id} do not map to the same exact pinned packet lines"
            )
        if not expected_lines <= allowed_lines:
            fail(f"{case_id} maps outside the role-scope packet's pinned lines")


def validate_artifacts(
    expected: dict[str, Any],
    matrix: dict[str, Any],
    expected_sha256: str,
    matrix_sha256: str,
    require_eight_cases: bool,
) -> int:
    """Validate all fixed values, eight nonzero mappings, and immutable bytes."""
    if not require_eight_cases:
        fail("--require-eight-cases is mandatory for this contract")
    if expected.get("schema_version") != 1 or matrix.get("schema_version") != 1:
        fail("both artifacts must use schema_version 1")
    if expected.get("product") != "WardAtlas":
        fail("unexpected product name")
    if expected.get("scope") != PINNED_SCOPE:
        fail("scope must retain the pinned synthetic, non-clinical, actual-FalkorDB boundary")
    if expected.get("pinned_sources") != PINNED_SOURCES:
        fail("expected-results must retain the exact role-scope and cascade predecessor provenance pins")
    if expected.get("fixed_fixture") != PINNED_FIXED_FIXTURE:
        fail("fixed fixture path/grant/source/time/branch values differ from the independent packet pins")

    scenarios = expected.get("cases")
    cases = matrix.get("cases")
    if not isinstance(scenarios, list) or not isinstance(cases, list):
        fail("expected cases and matrix cases must be arrays")
    scenario_ids = _unique_ids(scenarios, "expected case")
    case_ids = _unique_ids(cases, "matrix case")
    if require_eight_cases and len(scenarios) != 8:
        fail(f"expected exactly eight outcome groups; found {len(scenarios)}")
    if tuple(scenario_ids) != REQUIRED_CASES:
        fail("the eight fixed outcome groups or their pinned order differ from the contract")
    if case_ids != list(REQUIRED_CASES):
        fail("matrix must map one-to-one to all eight expected cases in the same order")
    if matrix.get("oracle_file") != "tests/role-scope-expected-results.json":
        fail("matrix oracle_file must identify the role-scope expected-results artifact")

    for scenario, case in zip(scenarios, cases):
        case_id = scenario["id"]
        if scenario.get("expected") != PINNED_EXPECTED[case_id]:
            fail(f"{case_id} expected path/grant/source/time/branch/access values differ from the independent pins")
        expected_refs = [f"role-scope-packet#lines-{CASE_PLAN_LINES[case_id]}"]
        if case_id == "exact-role-scope-paths-and-grant-traces":
            expected_refs = [
                "role-scope-packet#lines-13-21",
                "role-scope-packet#lines-26",
            ]
        elif case_id == "mainline-versus-hypothetical-branch":
            expected_refs = [
                "role-scope-packet#lines-13-19",
                "role-scope-packet#lines-28",
            ]
        elif case_id == "selected-scope-trace-and-outside-unknown":
            expected_refs = [
                "role-scope-packet#lines-21",
                "role-scope-packet#lines-29",
            ]
        if scenario.get("planner_refs") != expected_refs:
            fail(f"{case_id} expected-results packet mapping changed")
        if case.get("scenario_id") != case_id:
            fail(f"matrix case {case_id} does not map to its exact expected case")
        if case.get("pinned_assertions") != PINNED_ASSERTIONS[case_id]:
            fail(f"matrix assertions for {case_id} do not match the pinned adversarial outcomes")
        assertions = case.get("pinned_assertions")
        count = case.get("nonzero_assertion_count")
        if not isinstance(assertions, list) or not assertions:
            fail(f"matrix case {case_id} must have explicit nonzero assertions")
        if type(count) is not int or count <= 0:
            fail(f"matrix case {case_id} must have a positive integer assertion count")
        if count != len(assertions):
            fail(f"matrix case {case_id} count does not equal its explicit assertions")
        matrix_refs = case.get("planner_refs")
        if case_id == "exact-role-scope-paths-and-grant-traces":
            fixed_matrix_refs = [
                "planner-a4d9fd7447db467faab0a96048741775.md:13-21",
                "planner-a4d9fd7447db467faab0a96048741775.md:26",
            ]
        elif case_id == "mainline-versus-hypothetical-branch":
            fixed_matrix_refs = [
                "planner-a4d9fd7447db467faab0a96048741775.md:13-19",
                "planner-a4d9fd7447db467faab0a96048741775.md:28",
            ]
        elif case_id == "selected-scope-trace-and-outside-unknown":
            fixed_matrix_refs = [
                "planner-a4d9fd7447db467faab0a96048741775.md:21",
                "planner-a4d9fd7447db467faab0a96048741775.md:29",
            ]
        else:
            fixed_matrix_refs = [
                f"planner-a4d9fd7447db467faab0a96048741775.md:{CASE_PLAN_LINES[case_id]}"
            ]
        if matrix_refs != fixed_matrix_refs:
            fail(f"matrix planner mapping for {case_id} changed from the fixed packet lines")
        for field in ("attack_or_edge_case", "setup", "failure_detected"):
            value = case.get(field)
            if not isinstance(value, str) or not value.strip():
                fail(f"matrix case {case_id} must provide nonempty {field}")

    validate_plan_mappings(expected, matrix)
    total_assertions = sum(case["nonzero_assertion_count"] for case in cases)
    if total_assertions <= 0:
        fail("matrix must contain nonzero assertions")

    # Hashes pin all descriptions and metadata too, not just the selected fields.
    # The explicit checks above give actionable failures for the critical values.
    if expected_sha256 != EXPECTED_ARTIFACT_SHA256:
        fail("role-scope expected-results bytes differ from the independently pinned artifact")
    if matrix_sha256 != MATRIX_ARTIFACT_SHA256:
        fail("role-scope adversarial-matrix bytes differ from the independently pinned artifact")
    return total_assertions


def verify_plan_file(path: Path) -> str:
    """Require the exact planning packet named by the task contract."""
    try:
        data = path.read_bytes()
    except OSError as exc:
        fail(f"cannot read pinned role-scope plan {path}: {exc}")
    digest = hashlib.sha256(data).hexdigest()
    if digest != ROLE_SCOPE_PLAN_SHA256:
        fail("pinned plan content does not match the role-scope packet SHA-256")
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
    parser.add_argument("--require-eight-cases", action="store_true")
    args = parser.parse_args(argv)

    try:
        expected, expected_digest = _load_json(args.expected)
        matrix, matrix_digest = _load_json(args.matrix)
        assertion_count = validate_artifacts(
            expected,
            matrix,
            expected_digest,
            matrix_digest,
            require_eight_cases=args.require_eight_cases,
        )
        verify_plan_file(args.pinned_plan)
    except (OSError, TypeError, ValueError) as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1

    print(
        "PASS: exact role-scope planner pins and matrix; 8 outcome groups, "
        f"8 mapped adversarial cases, {assertion_count} nonzero assertions; "
        "all fixed paths/grants/provenance/branch and non-disclosure markers checked; "
        "no candidate output used as oracle."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

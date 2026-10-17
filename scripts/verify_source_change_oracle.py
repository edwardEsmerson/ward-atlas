#!/usr/bin/env python3
"""Check WardAtlas source-change oracle files against independent fixed values."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


EXPECTED_PATHS = {
    "before": ["INC-207", "EQ-DOCK-CART-2", "SVC-INBOUND-TRANSFER", "DEPT-RECEIVING-DOCK", "ROLE-DOCK-DUTY"],
    "after": ["INC-207", "EQ-DOCK-CART-2", "SVC-INBOUND-TRANSFER", "DEPT-RECEIVING-DOCK", "ROLE-DOCK-SWING"],
}
EXPECTED_IDS = [
    "source-empty-graph",
    "dock-role-path-before-effective-time",
    "dock-role-path-after-effective-time",
    "explicit-linked-review-queue",
    "unresolved-sop-conflict-preserved",
    "restricted-source-nondisclosure",
    "append-only-idempotent-edge-counterfactual",
    "reconnect-time-normalization-and-rejection",
]
EXPECTED_CANDIDATES = ["HND-DOCK-2020", "TKT-DOCK-88"]
EXPECTED_SOURCE_VERSIONS = [
    {"source_id": "SRC-DOCK-ROSTER-R1", "title": "Synthetic Dock Operations Roster Revision 1", "observed_at": "2026-10-14T19:00:00Z"},
    {"source_id": "SRC-DOCK-ROSTER-R2", "title": "Synthetic Dock Operations Roster Revision 2", "observed_at": "2026-10-14T20:18:00Z", "effective_at": "2026-10-14T20:30:00Z", "supersedes": "SRC-DOCK-ROSTER-R1"},
]
RESTRICTED_LITERALS = [
    "SRC-RESTRICTED-DOCK-99",
    "Restricted Dock Continuity Annex 99",
    "Restricted-only note: logistics bay status unresolved.",
    "EQ-RESTRICTED-DOCK-99",
    "DEPT-RESTRICTED-DOCK-99",
    "ROLE-RESTRICTED-DOCK-OWNER",
]
EXPECTED_SCENARIOS = {
    "source-empty-graph": {
        "source_rows": 0, "role_paths": [], "review_candidates": [],
        "coverage_status": "explicit_edges_only", "unlinked_artifact_status": "unknown",
    },
    "dock-role-path-before-effective-time": {
        "as_of": "2026-10-14T20:29:00Z", "role_path": EXPECTED_PATHS["before"], "review_candidates": [],
    },
    "dock-role-path-after-effective-time": {
        "as_of": "2026-10-14T20:30:00Z", "role_path": EXPECTED_PATHS["after"], "source_versions": EXPECTED_SOURCE_VERSIONS,
    },
    "explicit-linked-review-queue": {
        "review_candidates": EXPECTED_CANDIDATES,
        "unchanged_handover_role_id": "ROLE-DOCK-DUTY",
        "unchanged_handover_source_id": "SRC-DOCK-ROSTER-R1",
        "excluded_lexical_only_id": "HND-DOCK-LEXICAL-ONLY",
        "coverage_status": "explicit_edges_only",
        "unlinked_artifact_status": "unknown",
    },
    "unresolved-sop-conflict-preserved": {
        "conflicting_versions": ["SOP-OPS-9-V2", "SOP-OPS-9-V3"],
        "conflict_status": "unresolved", "winner_selected": False, "actions_proposed": [],
    },
    "restricted-source-nondisclosure": {
        "viewer_scope": "operations", "hidden_literals": RESTRICTED_LITERALS,
        "visible_output_unchanged": True, "hidden_counts_or_existence_disclosed": False,
    },
    "append-only-idempotent-edge-counterfactual": {
        "second_ingest_node_delta": 0, "second_ingest_relationship_delta": 0,
        "removed_citation_candidate_absent": True, "old_handover_unchanged": True,
        "both_source_versions_preserved": True,
    },
    "reconnect-time-normalization-and-rejection": {
        "reconnect_same_result": True,
        "offset_input": "2026-10-14T22:30:00+02:00",
        "normalized_as_of": "2026-10-14T20:30:00Z",
        "offset_role_path": EXPECTED_PATHS["after"],
        "malformed_time_rejected_before_mutation": True,
        "missing_effective_time_rejected_before_mutation": True,
        "no_external_effects": True,
    },
}
EXPECTED_ASSERTION_FLOORS = [5, 3, 5, 5, 4, 5, 5, 6]


class OracleFailure(ValueError):
    pass


CHECK_COUNT = 0


def require(condition: bool, message: str) -> None:
    global CHECK_COUNT
    CHECK_COUNT += 1
    if not condition:
        raise OracleFailure(message)


def check(expected_path: Path, matrix_path: Path, pinned_plan: Path, require_eight: bool) -> dict[str, Any]:
    global CHECK_COUNT
    CHECK_COUNT = 0
    expected = json.loads(expected_path.read_text(encoding="utf-8"))
    matrix = json.loads(matrix_path.read_text(encoding="utf-8"))
    plan = pinned_plan.read_text(encoding="utf-8")
    require(expected.get("schema_version") == 1 and expected.get("product") == "WardAtlas", "expected oracle is not WardAtlas schema 1")
    require(matrix.get("schema_version") == 1 and matrix.get("oracle_file") == "tests/source-change-expected-results.json", "adversarial matrix points to a different oracle")
    require(expected.get("scope", {}).get("source_history") == "Append-only; revisions and assignments are never overwritten.", "append-only source-history requirement is missing")
    require(expected.get("scope", {}).get("coverage_status") == "explicit_edges_only", "coverage must say explicit_edges_only")
    require(expected.get("scope", {}).get("unlinked_artifact_status") == "unknown", "unlinked artifacts must remain unknown")
    require(expected.get("research_provenance") == {
        "reference": "/workspace/inbox/company-brain-prior-13-reference.json",
        "sha256": "c771e8613e12c0853da7eb318d68a23b7da9ecc7fa0ccc6390fbec416b1a2a4c",
        "status": "prior public ideation context; product choice remains the already-approved Hospital Brain",
    }, "prior public research provenance/hash is missing or changed")
    require(expected.get("expected_paths") == EXPECTED_PATHS, "exact before/after role paths differ from the independent pin")
    require(expected.get("expected_review_candidates") == EXPECTED_CANDIDATES, "exact candidate queue differs from the independent pin")
    fixture = expected.get("fixture", {})
    fixed = {
        "observed_at": "2026-10-14T20:18:00Z",
        "effective_at": "2026-10-14T20:30:00Z",
        "supersedes": "SRC-DOCK-ROSTER-R1",
        "source_after_id": "SRC-DOCK-ROSTER-R2",
        "handover_id": "HND-DOCK-2020",
        "ticket_id": "TKT-DOCK-88",
        "lexical_only_handover_id": "HND-DOCK-LEXICAL-ONLY",
    }
    for key, value in fixed.items():
        require(fixture.get(key) == value, f"fixture.{key} differs from the independent pin")
    require(fixture.get("source_before_title") == "Synthetic Dock Operations Roster Revision 1", "R1 title differs from the independent pin")
    require(fixture.get("source_after_title") == "Synthetic Dock Operations Roster Revision 2", "R2 title differs from the independent pin")
    require(
        {key: fixture.get(key) for key in ("restricted_source_id", "restricted_source_title", "restricted_fact", "restricted_equipment_id", "restricted_department_id", "restricted_role_id")}
        == {
            "restricted_source_id": RESTRICTED_LITERALS[0],
            "restricted_source_title": RESTRICTED_LITERALS[1],
            "restricted_fact": RESTRICTED_LITERALS[2],
            "restricted_equipment_id": RESTRICTED_LITERALS[3],
            "restricted_department_id": RESTRICTED_LITERALS[4],
            "restricted_role_id": RESTRICTED_LITERALS[5],
        },
        "fixed restricted-source IDs/title/fact differ from the independent pin",
    )
    scenarios = expected.get("scenarios", [])
    cases = matrix.get("cases", [])
    ids = [item.get("id") for item in scenarios]
    case_ids = [item.get("id") for item in cases]
    require(ids == EXPECTED_IDS and case_ids == EXPECTED_IDS, "the eight independently pinned scenario IDs or order changed")
    require(not require_eight or len(scenarios) == len(cases) == 8, "--require-eight-scenarios requires all eight cases")
    for index, (scenario, case) in enumerate(zip(scenarios, cases)):
        require(scenario.get("expected") == EXPECTED_SCENARIOS[scenario["id"]], f"{scenario.get('id')} expected outcome differs from the independent fixed values")
        require(case.get("scenario_id") == scenario["id"], f"matrix scenario ID mismatch for {scenario.get('id')}")
        require(isinstance(case.get("pinned_assertions"), list) and len(case["pinned_assertions"]) > 0, f"{case.get('id')} has no pinned assertions")
        require(case.get("nonzero_assertion_count") == EXPECTED_ASSERTION_FLOORS[index], f"{case.get('id')} assertion floor differs from the pinned matrix")
    by_id = {item["id"]: item["expected"] for item in scenarios}
    # A separate planner artifact is mandatory. The hard-coded values above
    # make this checker independent of candidate output; these literal checks
    # also catch a mismatched/missing packet in a clean source-only clone.
    required_packet_literals = [
        "2026-10-14T20:29:00Z", "2026-10-14T20:30:00Z", "2026-10-14T20:18:00Z",
        "INC-207", "EQ-DOCK-CART-2", "SVC-INBOUND-TRANSFER", "DEPT-RECEIVING-DOCK",
        "ROLE-DOCK-DUTY", "ROLE-DOCK-SWING", "SRC-DOCK-ROSTER-R2",
        "HND-DOCK-2020", "TKT-DOCK-88", "HND-DOCK-LEXICAL-ONLY",
        "/workspace/inbox/company-brain-prior-13-reference.json",
        "c771e8613e12c0853da7eb318d68a23b7da9ecc7fa0ccc6390fbec416b1a2a4c",
        *RESTRICTED_LITERALS, "SOP-OPS-9-V2", "SOP-OPS-9-V3",
    ]
    missing = [literal for literal in required_packet_literals if literal not in plan]
    require(not missing, f"pinned planner packet is missing fixed source-change outcome literals: {missing}")
    return {
        "status": "PASS",
        "scenario_count": len(scenarios),
        "matrix_case_count": len(cases),
        "nonzero_assertion_floor": sum(case["nonzero_assertion_count"] for case in cases),
        "pinned_plan": str(pinned_plan),
        "independent_checks": CHECK_COUNT,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expected", type=Path, required=True)
    parser.add_argument("--matrix", type=Path, required=True)
    parser.add_argument("--require-eight-scenarios", action="store_true", required=True)
    parser.add_argument("--pinned-plan", type=Path, required=True)
    args = parser.parse_args()
    try:
        print(json.dumps(check(args.expected, args.matrix, args.pinned_plan, args.require_eight_scenarios), sort_keys=True))
        return 0
    except Exception as exc:
        print(json.dumps({"status": "FAIL", "error": f"{type(exc).__name__}: {exc}"}, sort_keys=True), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

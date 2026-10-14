#!/usr/bin/env python3
"""Check the pinned scenario oracle and adversarial matrix, without querying a candidate."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


REQUIRED_SCENARIOS = {
    "cold-empty-graph",
    "multi-hop-outage-current-ownership",
    "disconnected-decoy-exclusion",
    "equipment-outage-counterfactual",
    "stale-reference-current-sop-conflict",
    "denied-source-nondisclosure",
    "seed-reseed-idempotence",
    "reconnect-preserves-results",
}


def fail(message: str) -> None:
    raise ValueError(message)


def validate(expected: dict, matrix: dict, require_six: bool) -> int:
    if expected.get("schema_version") != 1 or matrix.get("schema_version") != 1:
        fail("both artifacts must use schema_version 1")
    if expected.get("product") != "WardAtlas":
        fail("unexpected product name")
    scope = expected.get("scope", {})
    if scope.get("synthetic_only") is not True or scope.get("clinical_decisions") is not False:
        fail("fixture scope must be synthetic and non-clinical")
    if "actual falkordb" not in scope.get("database_requirement", "").lower():
        fail("oracle must require actual FalkorDB")

    scenario_rows = expected.get("scenarios")
    cases = matrix.get("cases")
    if not isinstance(scenario_rows, list) or not isinstance(cases, list):
        fail("scenarios and cases must be arrays")
    scenario_ids = [s.get("id") for s in scenario_rows]
    case_ids = [c.get("id") for c in cases]
    if len(scenario_ids) != len(set(scenario_ids)):
        fail("scenario IDs are not unique")
    if len(case_ids) != len(set(case_ids)):
        fail("adversarial case IDs are not unique")
    if require_six and len(scenario_rows) < 6:
        fail(f"expected at least six scenarios; found {len(scenario_rows)}")
    missing = REQUIRED_SCENARIOS - set(scenario_ids)
    if missing:
        fail(f"missing required scenarios: {', '.join(sorted(missing))}")
    if set(case_ids) != set(scenario_ids):
        fail("each pinned scenario must have exactly one adversarial case")
    by_scenario = {s["id"]: s for s in scenario_rows}
    for case in cases:
        sid = case.get("scenario_id")
        if sid not in by_scenario:
            fail(f"matrix case {case.get('id')} references an unknown scenario")
        if case.get("id") != sid:
            fail(f"matrix case {case.get('id')} must map one-to-one to scenario {sid}")
        if not isinstance(case.get("nonzero_assertion_count"), int) or case["nonzero_assertion_count"] <= 0:
            fail(f"matrix case {case.get('id')} must pin at least one nonzero assertion")
        if len(case.get("pinned_assertions", [])) != case["nonzero_assertion_count"]:
            fail(f"matrix case {case.get('id')} assertion count does not match its explicit assertions")
        if by_scenario[sid].get("oracle") is None:
            fail(f"scenario {sid} has no independent oracle description")

    paths = expected.get("expected_paths")
    expected_outage = by_scenario["multi-hop-outage-current-ownership"]["expected"]
    if paths != expected_outage.get("impact_paths"):
        fail("top-level canonical paths disagree with the scenario's pinned paths")
    if len(paths) != 2 or any(len(path) != 5 for path in paths):
        fail("canonical outage expectations must contain two five-node paths")
    if len({tuple(path) for path in paths}) != 2:
        fail("canonical paths must be distinct")
    evidence = expected.get("expected_evidence_by_path")
    if not isinstance(evidence, list) or len(evidence) != len(paths):
        fail("each canonical path must pin its provenance payload")
    for index, row in enumerate(evidence):
        if row.get("path") != paths[index]:
            fail("provenance path order must match the canonical path oracle")
        for key in ("source_ids", "source_titles", "observed_at"):
            values = row.get(key)
            if not isinstance(values, list) or len(values) != 4 or any(not v for v in values):
                fail(f"canonical path {index} must pin four nonempty {key}")
    if evidence[0]["source_ids"] != expected_outage.get("source_ids_by_path", [None])[0]:
        fail("first canonical provenance IDs disagree with the multi-hop scenario pin")
    if evidence[1]["source_ids"] != expected_outage.get("source_ids_by_path", [None, None])[1]:
        fail("second canonical provenance IDs disagree with the multi-hop scenario pin")
    if evidence[0]["observed_at"] != expected_outage.get("observed_at_by_path", [None])[0]:
        fail("first canonical evidence timestamps disagree with the multi-hop scenario pin")
    if evidence[1]["observed_at"] != expected_outage.get("observed_at_by_path", [None, None])[1]:
        fail("second canonical evidence timestamps disagree with the multi-hop scenario pin")
    sop = by_scenario["stale-reference-current-sop-conflict"]["expected"]
    for key in (
        "stale_source_id", "stale_source_title", "stale_observed_at",
        "current_source_ids", "current_source_titles", "current_observed_at",
    ):
        value = sop.get(key)
        if not value or (isinstance(value, list) and any(not item for item in value)):
            fail(f"SOP conflict oracle must pin nonempty provenance field {key}")
    if len(sop["current_source_ids"]) != 2 or len(sop["current_source_titles"]) != 2 or len(sop["current_observed_at"]) != 2:
        fail("SOP conflict oracle must pin exactly two current evidence records")
    denial = by_scenario["denied-source-nondisclosure"]["expected"]
    if denial.get("visible_paths_before_denied_fixture") != denial.get("visible_paths_after_denied_fixture"):
        fail("denied-source oracle must pin identical visible paths before and after")
    if denial.get("visible_paths_before_denied_fixture") != paths:
        fail("denied-source visible path pin must match the canonical outage paths")
    if denial.get("visible_evidence_before_denied_fixture") != denial.get("visible_evidence_after_denied_fixture"):
        fail("denied-source oracle must pin identical evidence before and after")
    if denial.get("denied_identifiers_or_derived_paths_disclosed") is not False:
        fail("denied-source oracle must forbid identifier and derived-path disclosure")
    if denial.get("denied_count_or_existence_disclosed") is not False:
        fail("denied-source oracle must forbid hidden-count and existence leakage")
    for key in (
        "denied_source_id", "denied_department_id", "denied_role_id",
        "denied_source_title", "denied_fact_text", "denied_equipment_id",
    ):
        if not isinstance(denial.get(key), str) or not denial[key]:
            fail(f"denied-source oracle must pin a nonempty disclosure tripwire: {key}")

    assertion_count = sum(c["nonzero_assertion_count"] for c in cases)
    print(
        f"PASS: {len(scenario_rows)} pinned scenarios, {len(cases)} adversarial cases, "
        f"{assertion_count} nonzero assertions; no candidate output was used as oracle."
    )
    return assertion_count


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expected", required=True, type=Path)
    parser.add_argument("--matrix", required=True, type=Path)
    parser.add_argument("--require-six-scenarios", action="store_true")
    args = parser.parse_args()
    try:
        expected = json.loads(args.expected.read_text(encoding="utf-8"))
        matrix = json.loads(args.matrix.read_text(encoding="utf-8"))
        validate(expected, matrix, args.require_six_scenarios)
    except (OSError, json.JSONDecodeError, ValueError, TypeError) as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

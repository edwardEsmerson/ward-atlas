#!/usr/bin/env python3
"""Check the pinned service-coverage oracle without consulting product output.

This is an artifact-only contract validator. It does not import application
code, connect to FalkorDB, or claim that the service ledger is implemented.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any


EXPECTED_ARTIFACT_SHA256 = "2f5da05f710c3fc5514ceb4d4a89415e40db1b340b91160b8062db0404b02bd4"
MATRIX_ARTIFACT_SHA256 = "4e5b5d58218a280876654a53434f11acc89c30c3ed25bc44862c244bdfb17c81"
PACKET_SHA256 = "d3e21b76131c124ca9c5d4cd82773adc876da39752b773ae24c104fc691a6e04"
PACKET_FILENAME = "planner-f3a45b2e499f44f6b202f14a16889105.md"
PACKET_OUTCOME_LINES = tuple(range(41, 50))
PACKET_OUTCOME_PREFIXES = tuple(
    f"{number}. `{case_id}`"
    for number, case_id in enumerate(
        (
            "cold-empty-unknown",
            "three-exact-tracked-services",
            "dependency-edge-removal",
            "role-edge-removal",
            "evidence-edge-removal",
            "untracked-decoy-exclusion",
            "restricted-source-full-view-invariance",
            "reseed-idempotence",
            "fresh-client-read-only-cleanup",
        ),
        start=1,
    )
)

# Byte hashes of every expected-results/adversarial-matrix JSON in the task
# baseline, plus the two immutable oracle files merged from origin/main before
# final verification. Any change, deletion, or replacement fails closed.
PINNED_EXISTING = {
    "tests/adversarial-matrix.json": "6d0edbc69620da85b494185879565c8260c9d2d3fe6afdd5bf56c5acd92205ba",
    "tests/cascade-adversarial-matrix.json": "c74431b0c2bebbdc25419bf58d39129d150779e1ef7418b64c80dee6979398db",
    "tests/cascade-expected-results.json": "52fd4abe069e850e4ee070b8e6d0f9ed26fff5ad1e5e0c47efb28ad78227581b",
    "tests/conveyor-bitemporal-adversarial-matrix.json": "e036570a3db09c8b544cb339b2665beeee3d469416c4b5fe69e5be2b3597eb67",
    "tests/conveyor-bitemporal-expected-results.json": "8ffa7ee25a52219808656ead871fac4779625300f9eea0eaa9543e8b0c7740e6",
    "tests/dock-bitemporal-adversarial-matrix.json": "84127f273043a03299823905bbecc06a755f79cb9a3b34c44793815eda7f5318",
    "tests/dock-bitemporal-expected-results.json": "243c92775d7c1366962dcd7c5c9acd9ea753f2924af84c2c5dc99b0b5c872205",
    "tests/escalation-adversarial-matrix.json": "7e8e6c9813e88544dd61b78e208a48d3edfd48d7b4f3a3b6dbbb1a119c51af17",
    "tests/escalation-expected-results.json": "b9db49c9398d76c063ac126b5234024ba32dbac1fb7806b443e4f6508868e929",
    "tests/expected-results.json": "c77e00e47c4f1e26d565c3d9583653838d5cc88695c4e6aa19ac583205d92b87",
    "tests/graph-explorer-adversarial-matrix.json": "e6559d172b671375be25cb7c6d39d47bb286e9fedd77eab9a1f213803ed2f500",
    "tests/graph-explorer-expected-results.json": "d3ecfd1d498a19d7355b09c424e4b0b00c8bb83975421b8ae235cc07ef92d1de",
    "tests/handover-brief-adversarial-matrix.json": "d084f31a1b542670ab99818852cba8e1141bfb56688845c439b01f5ac1d89003",
    "tests/handover-brief-expected-results.json": "932ada7d43688b8bd1e4f147ac06dd585982d784ef8577f05cc00b07a97199f1",
    "tests/handover-drift-adversarial-matrix.json": "439adc3ed2f32c4e310d397ddf671bfb835dbaf8086b4b940f7d7335d22582aa",
    "tests/handover-drift-expected-results.json": "76121f6061fe6bec13d007b2dc3f9dadd737809b1be52e895674c7c39e156c1d",
    "tests/handover-known-time-adversarial-matrix.json": "7b8450947696e55af0dc48aaa6d27fd0231f458b0f527b3fc00f643ad1f64f67",
    "tests/handover-known-time-expected-results.json": "fd72df03c8e865cfe347839074ee8251bf57216deac33fcc9d5a4d1152513f93",
    "tests/review-receipt-adversarial-matrix.json": "0d5f0832699daa2ba69a9a4d3cde2225e4d672830fc613070f015c0788302b9a",
    "tests/review-receipt-expected-results.json": "382383b65ae8a9984550227c8ad9000d893bcd9e0171298ec3bf6adb7e170972",
    "tests/role-scope-adversarial-matrix.json": "2cc162e4f4a01daa81bbc0e8c57d9f7f2bc0a6167c603e8613afa3fbb8b02ee3",
    "tests/role-scope-expected-results.json": "fc83b36dd9cfea900a1885816c6b84ffe7a1ab3d5c62d45be87f97a7368a9867",
    "tests/shift-board-adversarial-matrix.json": "9cf1a4b144df3ff7357e389acec7c5e6b0e6ddcdab18577fa193c09541c0bbd7",
    "tests/shift-board-expected-results.json": "5d53a8f93245ee8391ec7cffb6d45b58355536d30d649db19cd53ca6b1ce2428",
    "tests/sop-impact-adversarial-matrix.json": "f5aa441a636b45b4222716d4a003afc91cf7598439833e36f390523b55f161c6",
    "tests/sop-impact-expected-results.json": "f94e65850384e36e7200b321d14628f074fee37fb159adbcd73bef00c6e1acca",
    "tests/source-change-adversarial-matrix.json": "2afff10adb99964a821291d491169487600f37aa41d7b2d153dc98b463a0da84",
    "tests/source-change-expected-results.json": "d26438dac655a9842d7bc60a2435ae1e3cab3a38a0f932d63005f044b0ba6097",
}

REQUIRED_CASES = tuple(prefix.split("`", 2)[1] for prefix in PACKET_OUTCOME_PREFIXES)
PORTFOLIO_ID = "PORTFOLIO-OPS-01"
RELATIONSHIP_ORDER = ("TRACKS", "DEPENDS_ON", "SERVES", "OWNED_BY")
SERVICES = (
    ("SVC-STERILE-01", "EQ-AUTOCLAVE-17", "DEPT-STERILE-SERVICES", "ROLE-STERILE-OPS-DUTY"),
    ("SVC-MATERIALS-01", "EQ-TUG-05", "DEPT-CAMPUS-LOGISTICS", "ROLE-MATERIALS-DUTY"),
    ("SVC-LINEN-01", "EQ-LINEN-08", "DEPT-TEXTILE-SERVICES", "ROLE-LINEN-SHIFT-COORD"),
)
SOURCES = {
    "catalog": {"id": "SRC-SVC-CATALOG", "title": "Synthetic Operations Service Catalog", "observed_at": "2026-10-14T21:00:00Z"},
    "equipment": {"id": "SRC-SVC-EQUIPMENT", "title": "Synthetic Equipment Dependency Register", "observed_at": "2026-10-14T21:02:00Z"},
    "department": {"id": "SRC-SVC-DEPT-MAP", "title": "Synthetic Service Department Coverage Register", "observed_at": "2026-10-14T21:03:00Z"},
    "roster": {"id": "SRC-SVC-ROSTER", "title": "Synthetic Operations Role Roster", "observed_at": "2026-10-14T21:04:00Z"},
}


class ContractFailure(ValueError):
    """Raised when a fixed expectation, mapping, or source hash has drifted."""


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ContractFailure(message)


def _load_json(path: Path) -> tuple[dict[str, Any], str]:
    try:
        raw = path.read_bytes()
        value = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ContractFailure(f"cannot load JSON artifact {path}: {exc}") from exc
    require(isinstance(value, dict), f"JSON artifact {path} must contain an object")
    return value, hashlib.sha256(raw).hexdigest()


def _unique_ids(rows: list[dict[str, Any]], label: str) -> list[str]:
    ids = [row.get("id") for row in rows]
    require(all(isinstance(item, str) and item for item in ids), f"{label} IDs must be nonempty strings")
    require(len(ids) == len(set(ids)), f"{label} IDs must be unique")
    return ids


def _resolve_pointer(document: Any, pointer: str) -> Any:
    require(isinstance(pointer, str) and pointer.startswith("/"), f"invalid expected pointer {pointer!r}")
    value = document
    for part in pointer[1:].split("/"):
        key = part.replace("~1", "/").replace("~0", "~")
        if isinstance(value, list):
            require(key.isdigit() and int(key) < len(value), f"matrix pointer does not resolve: {pointer}")
            value = value[int(key)]
        elif isinstance(value, dict) and key in value:
            value = value[key]
        else:
            raise ContractFailure(f"matrix pointer does not resolve: {pointer}")
    return value


def expected_pinned_sources() -> list[dict[str, Any]]:
    return [
        {
            "id": "service-coverage-packet",
            "file": f"planner-f3a45b2e499f44f6b202f14a16889105/plans/{PACKET_FILENAME}",
            "sha256": PACKET_SHA256,
            "outcome_lines": list(PACKET_OUTCOME_LINES),
        },
        *[
            {"id": path.replace("/", "-").removesuffix(".json"), "file": path, "sha256": digest}
            for path, digest in PINNED_EXISTING.items()
        ],
    ]


def _branch(from_id: str, relationship: str, to_id: str, source: dict[str, str], evidence_entity: str) -> dict[str, Any]:
    return {
        "from": from_id,
        "relationship": relationship,
        "to": to_id,
        "source": dict(source),
        "evidence_link": {
            "from": evidence_entity,
            "relationship": "EVIDENCED_BY",
            "to": source["id"],
            "evidence_node_type": "Evidence",
            "accessible": True,
        },
    }


def _row(service: tuple[str, str, str, str], removed: tuple[str, ...] = ()) -> dict[str, Any]:
    service_id, equipment_id, department_id, role_id = service
    branches = {
        "tracking": _branch(PORTFOLIO_ID, "TRACKS", service_id, SOURCES["catalog"], service_id),
        "dependency": _branch(service_id, "DEPENDS_ON", equipment_id, SOURCES["equipment"], equipment_id),
        "department": _branch(service_id, "SERVES", department_id, SOURCES["department"], department_id),
        "role": _branch(department_id, "OWNED_BY", role_id, SOURCES["roster"], role_id),
    }
    for branch_name in removed:
        branches[branch_name] = None
    source_order = ["tracking", "dependency", "department", "role"]
    return {
        "service_id": service_id,
        "coverage_status": "incomplete/unknown" if removed else "complete",
        "branches": branches,
        "source_provenance_in_relationship_order": [
            branches[name]["source"] for name in source_order if branches[name] is not None
        ],
    }


def _fixed_fixture() -> dict[str, Any]:
    baseline = [_row(service) for service in SERVICES]
    return {
        "portfolio_id": PORTFOLIO_ID,
        "relationship_order": list(RELATIONSHIP_ORDER),
        "sources": SOURCES,
        "services": [
            {"service_id": s, "equipment_id": e, "department_id": d, "role_id": r}
            for s, e, d, r in SERVICES
        ],
        "baseline_rows": baseline,
    }


def _fixed_cases() -> list[dict[str, Any]]:
    baseline = [_row(service) for service in SERVICES]
    dependency_removed = [baseline[0], _row(SERVICES[1], ("dependency",)), baseline[2]]
    role_removed = [baseline[0], _row(SERVICES[1], ("role",)), baseline[2]]
    evidence_removed = [baseline[0], _row(SERVICES[1], ("dependency",)), baseline[2]]
    outcomes: list[dict[str, Any]] = [
        {"rows": [], "coverage_status": "unknown", "evidence_state": "no_evidence", "no_count_or_existence_hint": True, "no_completeness_safety_or_availability_inference": True},
        {"portfolio_id": PORTFOLIO_ID, "rows": baseline, "exact_service_ids": [s[0] for s in SERVICES], "no_fourth_service": True, "relationship_order": list(RELATIONSHIP_ORDER), "each_returned_source_requires_accessible_evidence_node_and_link": True},
        {"mutation": {"from": "SVC-MATERIALS-01", "relationship": "DEPENDS_ON", "to": "EQ-TUG-05", "remove_only_this_edge": True}, "rows": dependency_removed, "materials_dependency_branch": "suppressed", "materials_status": "incomplete/unknown", "other_two_rows_byte_equivalent_to_baseline": True},
        {"mutation": {"from": "DEPT-CAMPUS-LOGISTICS", "relationship": "OWNED_BY", "to": "ROLE-MATERIALS-DUTY", "remove_only_this_edge": True}, "rows": role_removed, "materials_dependency_remains_supported": True, "materials_role": "omitted", "materials_status": "incomplete/unknown", "other_two_rows_byte_equivalent_to_baseline": True},
        {"mutation": {"from": "EQ-TUG-05", "relationship": "EVIDENCED_BY", "to": "SRC-SVC-EQUIPMENT", "remove_only_this_edge": True}, "dependency_graph_edge_remains": {"from": "SVC-MATERIALS-01", "relationship": "DEPENDS_ON", "to": "EQ-TUG-05"}, "dependency_relationship_source_id_properties_remain": {"source_id": "SRC-SVC-EQUIPMENT", "title": "Synthetic Equipment Dependency Register", "observed_at": "2026-10-14T21:02:00Z"}, "matching_accessible_evidence_node_and_link_required": True, "rows": evidence_removed, "materials_dependency_branch": "suppressed", "materials_department_and_role_branch": "retained", "materials_status": "incomplete/unknown", "no_property_only_citation_reconstruction": True, "other_two_rows_byte_equivalent_to_baseline": True},
        {"portfolio_id": PORTFOLIO_ID, "rows": baseline, "plausible_untracked_component_returned_as_service_row": False, "untracked_component_changes_baseline_response": False, "tracked_rows_equal_exact_baseline": True},
        {"viewer_scope": "operations", "full_api_projection_unchanged": True, "visible_counts_unchanged": True, "browser_text_unchanged": True, "accessibility_tree_semantically_unchanged": True, "hidden_identifier_title_fact_count_existence_or_paraphrase_disclosed": False},
        {"rows": baseline, "repeat_seed_duplicate_fixed_nodes": 0, "repeat_seed_duplicate_relationships": 0, "full_output_matches_exact_baseline": True},
        {"fresh_client_snapshot_matches_exact_baseline": True, "api_browser_reads_graph_writes": 0, "api_browser_reads_local_action_writes": 0, "desktop_mobile_api_results_agree": True, "cleanup_deletes_only_test_owned_graph": True, "cleanup_stops_only_owned_app_and_database_processes": True},
    ]
    return [
        {"id": case_id, "expected": outcome, "planner_refs": [f"service-coverage-packet#outcome-{index}"]}
        for index, (case_id, outcome) in enumerate(zip(REQUIRED_CASES, outcomes), start=1)
    ]


REQUIRED_ASSERTION_POINTERS = {
    REQUIRED_CASES[0]: {"/cases/0/expected/rows", "/cases/0/expected/coverage_status", "/cases/0/expected/evidence_state", "/cases/0/expected/no_count_or_existence_hint", "/cases/0/expected/no_completeness_safety_or_availability_inference"},
    REQUIRED_CASES[1]: {"/cases/1/expected/portfolio_id", "/cases/1/expected/rows", "/cases/1/expected/exact_service_ids", "/cases/1/expected/no_fourth_service", "/cases/1/expected/relationship_order", "/cases/1/expected/each_returned_source_requires_accessible_evidence_node_and_link"},
    REQUIRED_CASES[2]: {"/cases/2/expected/mutation", "/cases/2/expected/materials_dependency_branch", "/cases/2/expected/materials_status", "/cases/2/expected/other_two_rows_byte_equivalent_to_baseline", "/cases/2/expected/rows"},
    REQUIRED_CASES[3]: {"/cases/3/expected/mutation", "/cases/3/expected/materials_dependency_remains_supported", "/cases/3/expected/materials_role", "/cases/3/expected/materials_status", "/cases/3/expected/other_two_rows_byte_equivalent_to_baseline", "/cases/3/expected/rows"},
    REQUIRED_CASES[4]: {"/cases/4/expected/mutation", "/cases/4/expected/dependency_graph_edge_remains", "/cases/4/expected/dependency_relationship_source_id_properties_remain", "/cases/4/expected/matching_accessible_evidence_node_and_link_required", "/cases/4/expected/materials_dependency_branch", "/cases/4/expected/materials_department_and_role_branch", "/cases/4/expected/materials_status", "/cases/4/expected/no_property_only_citation_reconstruction", "/cases/4/expected/other_two_rows_byte_equivalent_to_baseline", "/cases/4/expected/rows"},
    REQUIRED_CASES[5]: {"/cases/5/expected/plausible_untracked_component_returned_as_service_row", "/cases/5/expected/untracked_component_changes_baseline_response", "/cases/5/expected/tracked_rows_equal_exact_baseline", "/cases/5/expected/rows"},
    REQUIRED_CASES[6]: {"/cases/6/expected/viewer_scope", "/cases/6/expected/full_api_projection_unchanged", "/cases/6/expected/visible_counts_unchanged", "/cases/6/expected/browser_text_unchanged", "/cases/6/expected/accessibility_tree_semantically_unchanged", "/cases/6/expected/hidden_identifier_title_fact_count_existence_or_paraphrase_disclosed"},
    REQUIRED_CASES[7]: {"/cases/7/expected/rows", "/cases/7/expected/repeat_seed_duplicate_fixed_nodes", "/cases/7/expected/repeat_seed_duplicate_relationships", "/cases/7/expected/full_output_matches_exact_baseline"},
    REQUIRED_CASES[8]: {"/cases/8/expected/fresh_client_snapshot_matches_exact_baseline", "/cases/8/expected/api_browser_reads_graph_writes", "/cases/8/expected/api_browser_reads_local_action_writes", "/cases/8/expected/desktop_mobile_api_results_agree", "/cases/8/expected/cleanup_deletes_only_test_owned_graph", "/cases/8/expected/cleanup_stops_only_owned_app_and_database_processes"},
}


def validate_fixed_outcomes(expected: dict[str, Any]) -> None:
    """Reject any drift in the exact graph paths, provenance, unknowns, or ACL."""
    require(expected.get("fixture") == _fixed_fixture(), "fixed service paths, evidence links, or source/time provenance changed")
    require(expected.get("cases") == _fixed_cases(), "fixed case path/source/time, incomplete/unknown, or ACL outcomes changed")


def validate_structure(expected: dict[str, Any], matrix: dict[str, Any], require_nine_groups: bool) -> int:
    require(expected.get("schema_version") == 1 and matrix.get("schema_version") == 1, "schema_version must be 1")
    require(expected.get("product") == "WardAtlas", "unexpected product")
    require("candidate" in expected.get("oracle_policy", "").lower() and "never" in expected.get("oracle_policy", "").lower(), "candidate output cannot be the oracle")
    scope = expected.get("scope")
    require(isinstance(scope, dict) and scope.get("synthetic_only") is True, "oracle must remain synthetic-only")
    require(scope.get("clinical_decisions") is False and scope.get("external_writes") is False, "clinical decisions and external writes must remain forbidden")
    require(scope.get("candidate_output_is_oracle") is False, "candidate output must not supply expectations")
    require(scope.get("portfolio_is_finite_not_hospital_census") is True, "finite portfolio scope must not be generalized")
    require("actual falkordb" in str(scope.get("database_requirement", "")).lower(), "actual FalkorDB must remain required")
    require(matrix.get("oracle_file") == "tests/service-coverage-expected-results.json", "matrix points at another oracle")
    require(expected.get("pinned_sources") == expected_pinned_sources(), "decision packet or pre-existing oracle/matrix pins changed")

    cases, matrix_cases = expected.get("cases"), matrix.get("cases")
    require(isinstance(cases, list) and isinstance(matrix_cases, list), "expected and matrix cases must be arrays")
    case_ids, matrix_ids = _unique_ids(cases, "expected case"), _unique_ids(matrix_cases, "matrix case")
    if require_nine_groups:
        require(len(cases) == 9 and len(matrix_cases) == 9, "--require-nine-groups requires exactly nine groups")
    require(tuple(case_ids) == REQUIRED_CASES, "the nine fixed packet outcome groups or order changed")
    require(matrix_ids == case_ids, "matrix cases must map one-to-one in the same order as outcomes")

    total = 0
    for index, (scenario, case) in enumerate(zip(cases, matrix_cases), start=1):
        case_id = REQUIRED_CASES[index - 1]
        require(scenario.get("planner_refs") == [f"service-coverage-packet#outcome-{index}"], f"{case_id} expected outcome reference changed")
        require(case.get("scenario_id") == case_id, f"matrix case {case_id} maps to a different scenario")
        require(case.get("planner_refs") == [f"{PACKET_FILENAME}:{PACKET_OUTCOME_LINES[index - 1]}"], f"{case_id} must map to its exact packet line")
        require(isinstance(scenario.get("expected"), dict) and scenario["expected"], f"{case_id} has no fixed expected object")
        assertions = case.get("pinned_assertions")
        count = case.get("nonzero_assertion_count")
        require(isinstance(assertions, list) and assertions, f"{case_id} has no pinned assertions")
        ids = [row.get("id") for row in assertions if isinstance(row, dict)]
        pointers = [row.get("expected_pointer") for row in assertions if isinstance(row, dict)]
        require(len(ids) == len(assertions) and all(isinstance(value, str) and value for value in ids), f"{case_id} has malformed assertion IDs")
        require(len(ids) == len(set(ids)), f"{case_id} assertion IDs must be unique")
        require(all(isinstance(pointer, str) and pointer for pointer in pointers), f"{case_id} has an unmapped assertion")
        require(set(pointers) == REQUIRED_ASSERTION_POINTERS[case_id], f"{case_id} assertions do not map the required fixed outcome fields")
        for pointer in pointers:
            _resolve_pointer(expected, pointer)
        require(type(count) is int and count > 0, f"{case_id} assertion count must be nonzero")
        require(count == len(assertions), f"{case_id} assertion count differs from its explicit mapping")
        require(all(isinstance(case.get(field), str) and case[field] for field in ("attack_or_edge_case", "setup", "failure_detected")), f"{case_id} lacks adversarial setup/failure semantics")
        total += count
    require(total > 0, "matrix must contain a nonzero assertion total")
    return total


def verify_pinned_packet(path: Path) -> str:
    require(path.name == PACKET_FILENAME, f"pinned packet must be named {PACKET_FILENAME}")
    try:
        raw = path.read_bytes()
        text = raw.decode("utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise ContractFailure(f"cannot read pinned decision packet {path}: {exc}") from exc
    digest = hashlib.sha256(raw).hexdigest()
    require(digest == PACKET_SHA256, "decision packet bytes differ from the independently pinned service-coverage packet")
    lines = text.splitlines()
    for line_number, prefix in zip(PACKET_OUTCOME_LINES, PACKET_OUTCOME_PREFIXES):
        require(len(lines) >= line_number and lines[line_number - 1].startswith(prefix), f"decision packet outcome heading changed at line {line_number}")
    return digest


def verify_existing_oracles(root: Path, expected: dict[str, Any]) -> dict[str, str]:
    require(expected.get("pinned_sources", [])[1:] == expected_pinned_sources()[1:], "frozen oracle/matrix manifest differs from fixed source hashes")
    digests: dict[str, str] = {}
    for relative, pinned in PINNED_EXISTING.items():
        try:
            raw = (root / relative).read_bytes()
        except OSError as exc:
            raise ContractFailure(f"cannot read frozen oracle/matrix {relative}: {exc}") from exc
        actual = hashlib.sha256(raw).hexdigest()
        require(actual == pinned, f"pre-existing oracle/matrix {relative} differs from its frozen byte hash")
        digests[relative] = actual
    return digests


def validate_artifacts(
    expected: dict[str, Any],
    matrix: dict[str, Any],
    expected_sha256: str,
    matrix_sha256: str,
    root: Path,
    pinned_plan: Path,
    require_nine_groups: bool,
) -> tuple[int, dict[str, str]]:
    assertions = validate_structure(expected, matrix, require_nine_groups)
    require(expected_sha256 == EXPECTED_ARTIFACT_SHA256, "service-coverage expected-results bytes differ from their fixed oracle hash")
    require(matrix_sha256 == MATRIX_ARTIFACT_SHA256, "service-coverage adversarial-matrix bytes differ from their fixed hash")
    packet_digest = verify_pinned_packet(pinned_plan)
    oracle_digests = verify_existing_oracles(root, expected)
    validate_fixed_outcomes(expected)
    return assertions, {"service-coverage-packet": packet_digest, **oracle_digests}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expected", required=True, type=Path)
    parser.add_argument("--matrix", required=True, type=Path)
    parser.add_argument("--pinned-plan", required=True, type=Path)
    parser.add_argument("--require-nine-groups", action="store_true")
    parser.add_argument("--check-frozen-oracle-hashes", action="store_true")
    args = parser.parse_args(argv)
    if not args.require_nine_groups or not args.check_frozen_oracle_hashes:
        print("FAIL: --require-nine-groups and --check-frozen-oracle-hashes are mandatory", file=sys.stderr)
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
            require_nine_groups=True,
        )
    except (OSError, TypeError, ValueError, KeyError) as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1
    print(
        "PASS: nine fixed service-coverage groups and exact packet mappings; "
        f"{assertion_count} mapped nonzero assertions; packet and {len(source_digests) - 1} "
        "pre-existing oracle/matrix byte hashes verified; no candidate behavior used."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

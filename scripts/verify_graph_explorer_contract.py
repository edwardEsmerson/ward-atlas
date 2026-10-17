#!/usr/bin/env python3
"""Validate WardAtlas's fixed, candidate-independent graph-explorer oracle.

This is an artifact contract, not a live FalkorDB or UI test. It reads only the
fixed planning packet and accepted result artifacts, never candidate output.
An optional review receipt is checked against the frozen hashes and all nine
matrix assertion sets; a well-formed receipt is not itself proof of reviewer
independence or product behavior.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any


PACKET_SHA256 = "1c247b818f3a68610ab726ddf861b20db7fb834dd3a8d668ddadd5d913548f2a"
PACKET_FILENAME = "planner-d7534434df6b4d01876c99a92bbb3f11.md"
PACKET_PATH = (
    "/workspace/worktrees/planner-d7534434df6b4d01876c99a92bbb3f11/plans/"
    "planner-d7534434df6b4d01876c99a92bbb3f11.md"
)
PACKET_OUTCOME_LINES = tuple(range(45, 54))
PACKET_OUTCOME_PREFIXES = (
    "1. **`cold-empty-no-phantom-graph`:",
    "2. **`inc204-outage-typed-path-and-provenance`:",
    "3. **`sop-witness-stays-claims-not-owners`:",
    "4. **`observed-time-vs-hypothetical-branch`:",
    "5. **`dock-ownership-boundary-and-review-edges`:",
    "6. **`edge-and-evidence-removal-change-only-dependent-witness`:",
    "7. **`restricted-subgraph-full-view-invariance`:",
    "8. **`graph-query-not-layout-and-selection-agreement`:",
    "9. **`repeat-reconnect-read-only-and-cleanup`:",
)
EXPECTED_ARTIFACT_SHA256 = "d3ecfd1d498a19d7355b09c424e4b0b00c8bb83975421b8ae235cc07ef92d1de"
MATRIX_ARTIFACT_SHA256 = "e6559d172b671375be25cb7c6d39d47bb286e9fedd77eab9a1f213803ed2f500"
EXPECTED_SEMANTIC_SHA256 = "5fa0be245f80006637f1debbca21280caeba956a584709f0f561710265795bd9"
MATRIX_SEMANTIC_SHA256 = "32de8e3bb5390378850376b4cbf4727301d2ecfeb61bfb98fdf9af069dfb2cc7"

# Freeze every then-present expected-results and adversarial-matrix artifact.
# Several older drafts were not the explorer's source oracle; pinning their
# bytes as well is conservative and prevents this increment from editing them.
PINNED_ORACLE_HASHES = {
    "tests/adversarial-matrix.json": "6d0edbc69620da85b494185879565c8260c9d2d3fe6afdd5bf56c5acd92205ba",
    "tests/cascade-adversarial-matrix.json": "c74431b0c2bebbdc25419bf58d39129d150779e1ef7418b64c80dee6979398db",
    "tests/cascade-expected-results.json": "52fd4abe069e850e4ee070b8e6d0f9ed26fff5ad1e5e0c47efb28ad78227581b",
    "tests/dock-bitemporal-adversarial-matrix.json": "84127f273043a03299823905bbecc06a755f79cb9a3b34c44793815eda7f5318",
    "tests/dock-bitemporal-expected-results.json": "243c92775d7c1366962dcd7c5c9acd9ea753f2924af84c2c5dc99b0b5c872205",
    "tests/escalation-adversarial-matrix.json": "7e8e6c9813e88544dd61b78e208a48d3edfd48d7b4f3a3b6dbbb1a119c51af17",
    "tests/escalation-expected-results.json": "b9db49c9398d76c063ac126b5234024ba32dbac1fb7806b443e4f6508868e929",
    "tests/expected-results.json": "c77e00e47c4f1e26d565c3d9583653838d5cc88695c4e6aa19ac583205d92b87",
    "tests/handover-brief-adversarial-matrix.json": "d084f31a1b542670ab99818852cba8e1141bfb56688845c439b01f5ac1d89003",
    "tests/handover-brief-expected-results.json": "932ada7d43688b8bd1e4f147ac06dd585982d784ef8577f05cc00b07a97199f1",
    "tests/handover-drift-adversarial-matrix.json": "439adc3ed2f32c4e310d397ddf671bfb835dbaf8086b4b940f7d7335d22582aa",
    "tests/handover-drift-expected-results.json": "76121f6061fe6bec13d007b2dc3f9dadd737809b1be52e895674c7c39e156c1d",
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

REQUIRED_CASES = (
    "cold-empty-no-phantom-graph",
    "inc204-outage-typed-path-and-provenance",
    "sop-witness-stays-claims-not-owners",
    "observed-time-vs-hypothetical-branch",
    "dock-ownership-boundary-and-review-edges",
    "edge-and-evidence-removal-change-only-dependent-witness",
    "restricted-subgraph-full-view-invariance",
    "graph-query-not-layout-and-selection-agreement",
    "repeat-reconnect-read-only-and-cleanup",
)

SOURCE_PINS = [
    {
        "id": "graph-explorer-planner-packet",
        "path": PACKET_PATH,
        "sha256": PACKET_SHA256,
        "outcome_lines": list(PACKET_OUTCOME_LINES),
    },
    {
        "id": "original-outage-oracle",
        "path": "tests/expected-results.json",
        "sha256": PINNED_ORACLE_HASHES["tests/expected-results.json"],
    },
    {
        "id": "shift-board-oracle",
        "path": "tests/shift-board-expected-results.json",
        "sha256": PINNED_ORACLE_HASHES["tests/shift-board-expected-results.json"],
    },
    {
        "id": "sop-impact-oracle",
        "path": "tests/sop-impact-expected-results.json",
        "sha256": PINNED_ORACLE_HASHES["tests/sop-impact-expected-results.json"],
    },
]


class ContractFailure(ValueError):
    """Raised when a fixed outcome, mapping, packet, or frozen hash drifts."""


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ContractFailure(message)


def semantic_sha256(document: dict[str, Any]) -> str:
    """Hash normalized JSON so in-memory mutation tests cannot lie about file hashes."""
    encoded = json.dumps(document, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def load_json(path: Path) -> tuple[dict[str, Any], str]:
    try:
        raw = path.read_bytes()
        value = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ContractFailure(f"cannot load JSON artifact {path}: {exc}") from exc
    require(isinstance(value, dict), f"JSON artifact {path} must be an object")
    return value, hashlib.sha256(raw).hexdigest()


def json_pointer(document: Any, pointer: str) -> Any:
    require(pointer.startswith("/"), f"invalid JSON pointer {pointer!r}")
    current = document
    for encoded in pointer[1:].split("/"):
        token = encoded.replace("~1", "/").replace("~0", "~")
        if isinstance(current, list):
            require(token.isdigit(), f"non-numeric JSON array index in {pointer}")
            index = int(token)
            require(index < len(current), f"JSON pointer index out of range: {pointer}")
            current = current[index]
        elif isinstance(current, dict):
            require(token in current, f"JSON pointer target is missing: {pointer}")
            current = current[token]
        else:
            raise ContractFailure(f"JSON pointer traverses a scalar: {pointer}")
    return current


def validate_structure(expected: dict[str, Any], matrix: dict[str, Any]) -> int:
    """Validate exact case coverage and nonzero, traceable assertion mapping."""
    require(expected.get("schema_version") == 1, "expected schema_version must be 1")
    require(matrix.get("schema_version") == 1, "matrix schema_version must be 1")
    require(expected.get("product") == "WardAtlas", "unexpected product")
    require("candidate query, API, or UI output never" in expected.get("oracle_policy", ""), "candidate output must not be an oracle")
    scope = expected.get("scope")
    require(isinstance(scope, dict) and scope.get("synthetic_only") is True, "oracle must remain synthetic-only")
    require(scope.get("clinical_decisions") is False, "clinical decisions must be excluded")
    require("actual falkordb" in str(scope.get("database_requirement", "")).lower(), "actual FalkorDB is required")
    require(scope.get("viewer_scope") == "operations" and scope.get("denied_acl") == "restricted", "pinned ACL boundary changed")
    require(scope.get("external_writes") is False and scope.get("candidate_output_is_oracle") is False, "unsafe scope or candidate-derived oracle")
    require(matrix.get("oracle_file") == "tests/graph-explorer-expected-results.json", "matrix points at a different expected-results file")
    require(expected.get("pinned_sources") == SOURCE_PINS, "packet or cited source-oracle pins changed")
    frozen_list = [{"path": path, "sha256": digest} for path, digest in PINNED_ORACLE_HASHES.items()]
    require(expected.get("frozen_preexisting_oracle_files") == frozen_list, "frozen preexisting expected/matrix file list or hashes changed")
    require(matrix.get("frozen_oracle_hashes") == PINNED_ORACLE_HASHES, "matrix is missing or changes a frozen oracle hash")

    cases = expected.get("cases")
    groups = matrix.get("groups")
    require(isinstance(cases, list) and isinstance(groups, list), "expected.cases and matrix.groups must be arrays")
    case_ids = [row.get("id") for row in cases if isinstance(row, dict)]
    group_ids = [row.get("case_id") for row in groups if isinstance(row, dict)]
    require(tuple(case_ids) == REQUIRED_CASES, "one or more of the nine fixed outcome groups is missing, changed, reordered, or duplicated")
    require(tuple(group_ids) == REQUIRED_CASES, "matrix must map all nine expected groups exactly once and in order")

    all_assertion_ids: set[str] = set()
    assertion_count = 0
    for index, (case_id, case, group) in enumerate(zip(REQUIRED_CASES, cases, groups)):
        require(case.get("id") == case_id, f"expected case identity changed: {case_id}")
        require(case.get("planner_refs") == [f"graph-explorer-packet#outcome-{index + 1}"], f"{case_id} packet mapping changed")
        require(isinstance(case.get("expected"), dict) and case["expected"], f"{case_id} has no fixed expected values")
        require(isinstance(case.get("oracle_refs"), list) and case["oracle_refs"], f"{case_id} has no accepted source references")

        require(group.get("case_id") == case_id, f"matrix group mapping differs for {case_id}")
        require(group.get("planner_refs") == [f"{PACKET_FILENAME}:{PACKET_OUTCOME_LINES[index]}"], f"{case_id} matrix packet line changed")
        require(bool(group.get("attack_or_edge_case")) and bool(group.get("setup")) and bool(group.get("failure_detected")), f"{case_id} adversarial setup is incomplete")
        assertions = group.get("pinned_assertions")
        count = group.get("nonzero_assertion_count")
        require(isinstance(assertions, list) and assertions, f"{case_id} maps zero assertions")
        require(type(count) is int and count > 0, f"{case_id} must have a nonzero assertion count")
        require(count == len(assertions), f"{case_id} assertion count differs from its mappings")
        required_case_prefix = f"/cases/{index}/expected"
        require(
            any(item.get("pointer") == required_case_prefix for item in assertions if isinstance(item, dict)),
            f"{case_id} does not map its full fixed outcome object",
        )
        require(
            any(
                item.get("pointer") == required_case_prefix or str(item.get("pointer", "")).startswith(required_case_prefix + "/")
                for item in assertions
                if isinstance(item, dict)
            ),
            f"{case_id} maps no assertion to its fixed expected values",
        )
        for assertion in assertions:
            require(isinstance(assertion, dict), f"{case_id} contains a non-object assertion")
            assertion_id = assertion.get("id")
            require(isinstance(assertion_id, str) and assertion_id.strip(), f"{case_id} contains an unnamed assertion")
            require(assertion_id not in all_assertion_ids, f"duplicate matrix assertion ID: {assertion_id}")
            all_assertion_ids.add(assertion_id)
            pointer = assertion.get("pointer")
            require(isinstance(pointer, str) and (pointer == required_case_prefix or pointer.startswith(required_case_prefix + "/")), f"{assertion_id} points outside its fixed case")
            require(assertion.get("equals_case_value") is True, f"{assertion_id} is not pinned to its case's fixed value")
            json_pointer(expected, pointer)
        assertion_count += count

    require(len(cases) == 9 and len(groups) == 9, "--require-nine-cases requires exactly nine groups")
    require(assertion_count > 0, "matrix has no nonzero assertions")
    require(semantic_sha256(expected) == EXPECTED_SEMANTIC_SHA256, "one or more literal graph-explorer outcomes changed")
    require(semantic_sha256(matrix) == MATRIX_SEMANTIC_SHA256, "one or more fixed graph-explorer matrix mappings changed")
    return assertion_count


def verify_pinned_packet(path: Path) -> str:
    try:
        raw = path.read_bytes()
        lines = raw.decode("utf-8").splitlines()
    except (OSError, UnicodeDecodeError) as exc:
        raise ContractFailure(f"cannot read graph-explorer planner packet {path}: {exc}") from exc
    digest = hashlib.sha256(raw).hexdigest()
    require(digest == PACKET_SHA256, "planner packet bytes differ from the independently pinned graph-explorer packet")
    for number, prefix in zip(PACKET_OUTCOME_LINES, PACKET_OUTCOME_PREFIXES):
        require(len(lines) >= number and lines[number - 1].startswith(prefix), f"planner packet outcome heading changed on line {number}")
    return digest


def verify_frozen_oracle_hashes(root: Path) -> dict[str, str]:
    observed: dict[str, str] = {}
    for relative, pinned in PINNED_ORACLE_HASHES.items():
        try:
            digest = hashlib.sha256((root / relative).read_bytes()).hexdigest()
        except OSError as exc:
            raise ContractFailure(f"cannot read frozen preexisting oracle {relative}: {exc}") from exc
        require(digest == pinned, f"frozen preexisting oracle {relative} is missing or has changed bytes")
        observed[relative] = digest
    return observed


def _case_map(document: dict[str, Any], collection: str) -> dict[str, dict[str, Any]]:
    rows = document.get(collection)
    require(isinstance(rows, list), f"accepted oracle has no {collection} list")
    result: dict[str, dict[str, Any]] = {}
    for row in rows:
        require(isinstance(row, dict) and isinstance(row.get("id"), str), f"invalid {collection} row in accepted oracle")
        require(row["id"] not in result, f"duplicate accepted outcome ID {row['id']}")
        result[row["id"]] = row
    return result


def verify_source_oracle_overlaps(root: Path, expected: dict[str, Any]) -> None:
    """Cross-check shared literals against original, shift-board, and SOP outcomes."""
    original, _ = load_json(root / "tests/expected-results.json")
    shift, _ = load_json(root / "tests/shift-board-expected-results.json")
    sop, _ = load_json(root / "tests/sop-impact-expected-results.json")
    original_cases = _case_map(original, "scenarios")
    shift_cases = _case_map(shift, "cases")
    sop_cases = _case_map(sop, "cases")
    cases = {case["id"]: case["expected"] for case in expected["cases"]}

    empty = cases[REQUIRED_CASES[0]]
    require(empty["board_state"] == shift_cases["cold-empty-board"]["expected"]["board_state"] == sop_cases["sop-impact-cold-empty"]["expected"]["board_state"] == "empty/unknown", "cold empty state differs from accepted oracles")
    require(empty["paths"] == original_cases["cold-empty-graph"]["expected"]["impact_paths"] == [], "cold graph must contain no impact path")

    outage = cases[REQUIRED_CASES[1]]
    old_outage = original_cases["multi-hop-outage-current-ownership"]["expected"]
    shift_outage = shift_cases["conveyor-observed-and-hypothetical-times"]["expected"]["observed_at_1830"]
    sop_outage = sop_cases["sop-impact-observed-outage-paths"]["expected"]
    require(outage["ordered_paths"] == old_outage["impact_paths"] == shift_outage["paths"] == sop_outage["impact_paths"], "INC-204 ordered paths differ from accepted oracles")
    require(outage["as_of"] == shift_outage["at"] == sop_outage["as_of"] == "2026-10-14T18:30:00Z", "INC-204 selected time differs from accepted oracles")
    for current, source in zip(outage["path_evidence"], original["expected_evidence_by_path"]):
        require(current["path"] == source["path"], "INC-204 path evidence path differs from original oracle")
        require([item["source_id"] for item in current["sources"]] == source["source_ids"], "INC-204 source IDs differ from original oracle")
        require([item["title"] for item in current["sources"]] == source["source_titles"], "INC-204 source titles differ from original oracle")
        require([item["observed_at"] for item in current["sources"]] == source["observed_at"], "INC-204 observation times differ from original oracle")
    require(shift_outage["canonical_path_evidence"] == sop_outage["path_evidence"], "INC-204 source-oracle provenance differs between shift-board and SOP-impact")

    witness = cases[REQUIRED_CASES[2]]
    sop_witness = sop_cases["sop-impact-conflict-provenance"]["expected"]
    old_conflict = original_cases["stale-reference-current-sop-conflict"]["expected"]
    require(witness["conflict_edge"]["status"] == sop_witness["conflict_status"] == old_conflict["conflict_status"] == "unresolved", "SOP conflict status differs from accepted oracle")
    require([item["sop_version_id"] for item in witness["claims"]] == old_conflict["current_conflicting_version_ids"], "SOP version IDs differ from original oracle")
    require([item["role_id"] for item in witness["claims"]] == old_conflict["current_conflicting_role_claims"], "SOP role claim IDs differ from original oracle")
    require([item["source_id"] for item in witness["claims"]] == old_conflict["current_source_ids"], "SOP claim source IDs differ from original oracle")
    require([item["source_title"] for item in witness["claims"]] == old_conflict["current_source_titles"], "SOP claim source titles differ from original oracle")
    require([item["observed_at"] for item in witness["claims"]] == old_conflict["current_observed_at"], "SOP claim observation times differ from original oracle")
    require(witness["winner_selected"] is sop_witness["winner_selected"] is False, "no SOP winner may be selected")
    require(witness["direct_incident_to_sop_causal_edge_asserted"] is False, "no direct incident-to-SOP causal edge may be invented")
    projected_claims = [
        {key: item[key] for key in ("sop_version_id", "claim_relationship", "role_id", "claim_label", "source_evidence_linked", "source_id", "source_title", "observed_at")}
        for item in witness["claims"]
    ]
    require(projected_claims == sop_witness["claim_evidence"], "typed SOP claims differ from SOP-impact oracle")
    stale = sop_cases["sop-impact-stale-v1-excluded"]["expected"]
    for key in ("version_id", "source_id", "source_title", "observed_at", "referenced_equipment_id"):
        new_key = {"version_id": "version_id", "source_id": "source_id", "source_title": "source_title", "observed_at": "observed_at", "referenced_equipment_id": "referenced_equipment_id"}[key]
        old_key = {"version_id": "stale_version_id", "source_id": "stale_source_id", "source_title": "stale_source_title", "observed_at": "stale_observed_at", "referenced_equipment_id": "stale_referenced_equipment_id"}[key]
        require(witness["stale_archive"][new_key] == stale[old_key], f"archived SOP value {key} differs from accepted oracle")

    time_case = cases[REQUIRED_CASES[3]]
    shift_time = shift_cases["conveyor-observed-and-hypothetical-times"]["expected"]
    restored = shift_time["observed_restoration_1930"]
    branch = shift_time["hypothetical_branch_1850"]
    sop_restored = sop_cases["sop-impact-restoration-snapshot"]["expected"]
    require(time_case["observed_restoration"]["as_of"] == restored["at"] == "2026-10-14T19:30:00Z", "observed restoration time differs from shift-board oracle")
    require(time_case["observed_restoration"]["active_paths"] == restored["active_paths"] == [], "observed restoration paths differ from shift-board oracle")
    require(time_case["observed_restoration"]["active_impact_edges"] == [], "observed restoration must remove active impact edges")
    require(time_case["observed_restoration"]["restoration_event_id"] == restored["restoration_event_id"], "restoration event ID differs from shift-board oracle")
    require(time_case["observed_restoration"]["source_id"] == restored["restoration_source_id"], "restoration source ID differs from shift-board oracle")
    require(time_case["observed_restoration"]["source_observed_at"] == restored["restoration_event_at"] == "2026-10-14T19:15:00Z", "restoration source observation time differs from shift-board oracle")
    require(time_case["observed_restoration"]["sop_conflict_status"] == sop_restored["sop_conflict_status"] == "unresolved", "observed restoration must retain the SOP conflict")
    retained_claims = [
        {key: item[key] for key in ("sop_version_id", "claim_edge_id", "evidence_edge_id", "role_id", "claim_label", "source_id", "source_title", "observed_at")}
        for item in time_case["observed_restoration"]["sop_claims_remain"]
    ]
    expected_retained_claims = [
        {key: item[key] for key in ("sop_version_id", "claim_edge_id", "evidence_edge_id", "role_id", "claim_label", "source_id", "source_title", "observed_at")}
        for item in witness["claims"]
    ]
    require(retained_claims == expected_retained_claims, "restoration must retain both exact SOP claims and evidence separately")
    require(time_case["hypothetical_branch"]["branch_id"] == branch["branch_id"], "hypothetical branch ID differs from shift-board oracle")
    require(time_case["hypothetical_branch"]["event_id"] == branch["hypothetical_event_id"], "hypothetical event ID differs from shift-board oracle")
    require(time_case["hypothetical_branch"]["branch_status"] == branch["branch_status"] and time_case["hypothetical_branch"]["branch_paths"] == branch["branch_paths"], "hypothetical branch status or paths differ from shift-board oracle")
    require(time_case["hypothetical_branch"]["mainline_status"] == branch["mainline_status"], "hypothetical branch changed the observed mainline status")
    require(time_case["hypothetical_branch"]["mainline_paths"] == branch["mainline_paths"], "branch must preserve the pinned observed mainline paths")
    require(time_case["hypothetical_branch"]["event_at"] == branch["hypothetical_event_at"] == "2026-10-14T18:45:00Z", "hypothetical event time differs from shift-board oracle")

    dock = cases[REQUIRED_CASES[4]]
    dock_case = shift_cases["dock-role-time-and-source-evidence"]["expected"]
    require(dock["incident_id"] == dock_case["incident_id"] == "INC-207", "dock incident ID differs from shift-board oracle")
    require(dock["ownership_boundary"][0]["role_id"] == dock_case["before"]["role_path"][-1] == "ROLE-DOCK-DUTY", "pre-effective dock role differs from shift-board oracle")
    require(dock["ownership_boundary"][1]["role_id"] == dock_case["at_effective_time"]["role_path"][-1] == "ROLE-DOCK-SWING", "effective dock role differs from shift-board oracle")
    require(dock["ownership_boundary"][0]["as_of"] == dock_case["before"]["as_of"] and dock["ownership_boundary"][1]["as_of"] == dock_case["at_effective_time"]["as_of"], "dock ownership boundary times differ from shift-board oracle")
    for boundary, source in zip(dock["ownership_boundary"], dock_case["source_versions"]):
        require(boundary["source_id"] == source["source_id"] and boundary["source_title"] == source["title"] and boundary["observed_at"] == source["observed_at"], "dock source tuple differs from shift-board oracle")
    require(dock["ownership_boundary"][1]["effective_at"] == dock_case["source_versions"][1]["effective_at"] == "2026-10-14T20:30:00Z", "dock effective timestamp differs from shift-board oracle")
    require(dock["ownership_boundary"][1]["supersedes_source_id"] == dock_case["source_versions"][1]["supersedes"], "dock source supersession differs from shift-board oracle")
    require(dock["review_candidates"] == shift_cases["explicit-edge-review-queue"]["expected"]["review_candidates"], "dock review candidate IDs differ from shift-board oracle")
    require(dock["excluded_lexical_only_artifact"] == shift_cases["explicit-edge-review-queue"]["expected"]["excluded_lexical_only_id"], "dock lexical-only decoy differs from shift-board oracle")
    cross_case = shift_cases["no-cross-case-path-or-causal-contamination"]["expected"]
    require(dock["inc204_and_inc207_joined"] is cross_case["path_joining_INC-207_to_CONVEYOR-C4"] is False, "INC-204 and INC-207 must remain disconnected")
    require(dock["dock_roster_revision_caused_inc204_outage"] is cross_case["dock_roster_revision_caused_INC-204_outage"] is False, "dock roster revision cannot cause the INC-204 outage")

    mutation = cases[REQUIRED_CASES[5]]
    sop_app = sop_cases["sop-impact-applicability-edge-counterfactual"]["expected"]
    sop_conflict_removed = sop_cases["sop-impact-conflict-edge-counterfactual"]["expected"]
    delete = mutation["delete_only_v2_source_node"]
    require(delete["source_tuple_before_delete"] == {
        "source_id": sop_witness["claim_evidence"][0]["source_id"],
        "title": sop_witness["claim_evidence"][0]["source_title"],
        "observed_at": sop_witness["claim_evidence"][0]["observed_at"],
    }, "V2 source tuple differs from accepted SOP evidence")
    require(sop_app["removed_edge"]["from"] == "SOP-OPS-9-V2" and sop_app["removed_edge"]["relationship"] == "APPLIES_TO" and sop_app["removed_edge"]["to"] == "CONVEYOR-C4", "V2 applicability counterfactual differs from accepted SOP oracle")
    require(sop_conflict_removed["removed_edge"]["from"] == "SOP-OPS-9-V2" and sop_conflict_removed["removed_edge"]["relationship"] == "SOP_CONFLICT" and sop_conflict_removed["removed_edge"]["to"] == "SOP-OPS-9-V3", "conflict counterfactual differs from accepted SOP oracle")
    require(mutation["test_owned_edge_removal_targets"]["sop_applicability_edges"] == ["REL-SOP-V2-APPLIES-C4", "REL-SOP-V3-APPLIES-C4"] and mutation["test_owned_edge_removal_targets"]["sop_conflict_edge"] == "REL-SOP-9-CONFLICT", "SOP mutation targets changed")
    require(delete["conflict_status_after_delete"] == sop_witness["conflict_status"] == "unresolved", "V2 evidence deletion must not resolve the accepted conflict")
    require(delete["incident_paths_after_delete"] == sop_outage["impact_paths"], "V2 evidence deletion must not change incident paths")
    require(delete["v3_claim_and_source_unchanged"]["source_id"] == sop_witness["claim_evidence"][1]["source_id"] and delete["v3_claim_and_source_unchanged"]["source_title"] == sop_witness["claim_evidence"][1]["source_title"] and delete["v3_claim_and_source_unchanged"]["observed_at"] == sop_witness["claim_evidence"][1]["observed_at"], "V3 source tuple differs from accepted SOP evidence")
    require(delete["removed_evidence_edge"] == "REL-SOP-V2-EVIDENCE" and delete["remaining_edges"] == ["REL-SOP-V2-APPLIES-C4", "REL-SOP-V2-CLAIMS-FACILITIES", "REL-SOP-9-CONFLICT"], "V2 source deletion edge behavior changed")
    require(delete["v2_source_evidence_status"] == "incomplete/unknown" and delete["v2_source_tuple_suppressed"] is True, "missing V2 source must stay incomplete/unknown and suppress only its tuple")
    require(delete["v2_source_row_returned"] is False and delete["reseed_restores_exact_v2_source_tuple_and_edge"] is True, "V2 source tuple must be suppressed until reseed restores its edge and source")

    acl = cases[REQUIRED_CASES[6]]
    original_denied = original_cases["denied-source-nondisclosure"]["expected"]
    shift_denied = shift_cases["denied-source-nondisclosure"]["expected"]
    sop_fixture = sop.get("fixture", {}).get("restricted_probe", {})
    require(acl["viewer_scope"] == original_denied["viewer_scope"] == shift_denied["viewer_scope"] == "operations", "operations viewer scope differs from accepted ACL oracles")
    require(acl["denied_acl"] == sop_fixture.get("acl") == "restricted", "restricted ACL value differs from SOP-impact oracle")
    first_probe = acl["restricted_probes"][0]
    require(first_probe["source_id"] == original_denied["denied_source_id"] and first_probe["source_title"] == original_denied["denied_source_title"] and first_probe["fact"] == original_denied["denied_fact_text"], "original restricted tripwires differ from original oracle")
    require([first_probe[key] for key in ("equipment_id", "department_id", "role_id")] == [original_denied["denied_equipment_id"], original_denied["denied_department_id"], original_denied["denied_role_id"]], "original restricted entity IDs differ from original oracle")
    require(original_denied["visible_paths_before_denied_fixture"] == original_denied["visible_paths_after_denied_fixture"] == outage["ordered_paths"], "original denied-source visible paths are not invariant")
    dock_probe = acl["restricted_probes"][1]
    require([dock_probe[key] for key in ("source_id", "source_title", "fact", "equipment_id", "department_id", "role_id")] == shift_denied["dock_restricted_literals"], "restricted dock tripwires differ from shift-board oracle")
    probe = acl["restricted_probes"][2]
    for target, source_key in (("sop_version_id", "version_id"), ("claim_role_id", "claimed_role_id"), ("source_id", "source_id"), ("source_title", "source_title"), ("fact", "source_text"), ("department_id", "department_id"), ("relationship_id", "relationship_id"), ("path", "path")):
        require(probe[target] == sop_fixture.get(source_key), f"restricted SOP probe {target} differs from SOP-impact oracle")
    sop_denied = sop_cases["sop-impact-denied-source-invariance"]["expected"]
    require(acl["visible_api_graph_detail_dom_accessibility_and_case_counts_semantically_identical_before_after"] is sop_denied["operations_response_semantically_identical_before_after"] is True, "restricted-source full-view invariance differs from SOP-impact oracle")
    require(shift_denied["visible_paths_and_evidence_unchanged"] is True, "restricted dock source must not change operations-visible paths/evidence")

    selection = cases[REQUIRED_CASES[7]]["selection_examples"]
    require(selection[2]["title"] == sop_outage["path_evidence"][0]["source_titles"][1], "graph selection source title differs from SOP-impact path evidence")
    require(selection[2]["observed_at"] == sop_outage["path_evidence"][0]["observed_at"][1], "graph selection source time differs from SOP-impact path evidence")
    require(selection[3]["id"] == time_case["hypothetical_branch"]["relationship_id"] and selection[3]["relationship_type"] == "HAS_HYPOTHETICAL_EVENT", "hypothetical selection differs from shift-board branch")

    repeat = cases[REQUIRED_CASES[8]]
    original_repeat = original_cases["seed-reseed-idempotence"]["expected"]
    sop_repeat = sop_cases["sop-impact-reseed-reconnect"]["expected"]
    require(repeat["seed_runs"] == original_repeat["seed_runs"] == 2, "repeat seed count differs from original oracle")
    require(repeat["node_count_delta_after_second_seed"] == original_repeat["node_count_delta_after_second_seed"] == 0, "repeat seed node delta differs from original oracle")
    require(repeat["relationship_count_delta_after_second_seed"] == original_repeat["relationship_count_delta_after_second_seed"] == 0, "repeat seed relationship delta differs from original oracle")
    require(repeat["duplicate_fixture_ids"] == original_repeat["duplicate_fixture_ids"] == [], "duplicate fixture IDs differ from original oracle")
    require(repeat["new_falkordb_client_required"] == sop_repeat["new_falkordb_client_required"] is True, "reconnect must use a new FalkorDB client")
    require(repeat["messages_sent"] is repeat["calls_made"] is repeat["tickets_created"] is False and repeat["external_writes"] == [], "read-only graph browsing must create no external actions")


def validate_plan_mappings(expected: dict[str, Any], matrix: dict[str, Any]) -> None:
    for index, (case, group) in enumerate(zip(expected["cases"], matrix["groups"])):
        wanted_line = PACKET_OUTCOME_LINES[index]
        require(case["planner_refs"] == [f"graph-explorer-packet#outcome-{index + 1}"], f"{case['id']} expected packet reference changed")
        require(group["planner_refs"] == [f"{PACKET_FILENAME}:{wanted_line}"], f"{case['id']} matrix packet line changed")


def validate_review_receipt(
    receipt: dict[str, Any],
    matrix: dict[str, Any],
    *,
    expected_sha256: str,
    matrix_sha256: str,
    packet_sha256: str,
) -> int:
    """Validate the shape and exact oracle coverage of a future review receipt.

    It intentionally rejects a receipt based only on a fresh same-model
    session. The caller remains responsible for assessing the cited evidence
    and whether the reviewer was genuinely independent.
    """
    require(isinstance(receipt, dict), "review receipt must be a JSON object")
    require(isinstance(matrix, dict), "review matrix must be a JSON object")
    require(receipt.get("schema_version") == 1, "review receipt schema_version must be 1")
    require(receipt.get("receipt_type") == "graph-explorer-independent-oracle-review", "unexpected graph-explorer review receipt type")
    require(receipt.get("packet_sha256") == packet_sha256 == PACKET_SHA256, "review receipt packet hash does not match")
    require(receipt.get("expected_sha256") == expected_sha256 == EXPECTED_ARTIFACT_SHA256, "review receipt expected-results hash does not match")
    require(receipt.get("matrix_sha256") == matrix_sha256 == MATRIX_ARTIFACT_SHA256, "review receipt matrix hash does not match")
    require(receipt.get("frozen_oracle_hashes") == PINNED_ORACLE_HASHES, "review receipt omits or changes a frozen oracle hash")
    author = receipt.get("oracle_author_id")
    reviewer = receipt.get("reviewer_id")
    require(isinstance(author, str) and author.strip() and isinstance(reviewer, str) and reviewer.strip() and author != reviewer, "review receipt must identify a reviewer other than the oracle author")
    require(receipt.get("independence_basis") in {"different-model", "human", "independent-source-audit"}, "review receipt independence basis is not sufficient")
    require(receipt.get("same_model_fresh_session_only") is False, "a fresh session of the same model alone is not an independent oracle")
    require(receipt.get("candidate_output_used_as_oracle") is False, "review receipt must not use candidate output as the oracle")

    reviews = receipt.get("group_reviews")
    require(isinstance(reviews, list) and len(reviews) == 9, "review receipt must cover all nine groups")
    for expected_group, review in zip(matrix["groups"], reviews):
        case_id = expected_group["case_id"]
        require(isinstance(review, dict), f"review receipt group is not an object: {case_id}")
        require(review.get("case_id") == case_id, f"review receipt group missing or out of order: {case_id}")
        require(review.get("verdict") == "pass", f"independent review did not pass {case_id}")
        expected_ids = [assertion["id"] for assertion in expected_group["pinned_assertions"]]
        require(review.get("reviewed_assertion_ids") == expected_ids, f"review receipt does not cover every mapped assertion in {case_id}")
        evidence = review.get("evidence_refs")
        require(isinstance(evidence, list) and evidence and all(isinstance(item, str) and item.strip() for item in evidence), f"review receipt has no evidence references for {case_id}")
    require(reviews[-1]["case_id"] == REQUIRED_CASES[-1], "review receipt omitted the final group")
    return sum(len(group["pinned_assertions"]) for group in matrix["groups"])


def validate_artifacts(
    expected: dict[str, Any],
    matrix: dict[str, Any],
    expected_sha256: str,
    matrix_sha256: str,
    *,
    root: Path,
    pinned_plan: Path,
    check_frozen_hashes: bool,
    review_receipt: dict[str, Any] | None = None,
) -> tuple[int, dict[str, str]]:
    require(check_frozen_hashes, "--check-frozen-oracle-hashes is mandatory")
    require(expected_sha256 == EXPECTED_ARTIFACT_SHA256, "graph-explorer expected-results bytes differ from the fixed oracle")
    require(matrix_sha256 == MATRIX_ARTIFACT_SHA256, "graph-explorer matrix bytes differ from the fixed matrix")
    assertion_count = validate_structure(expected, matrix)
    validate_plan_mappings(expected, matrix)
    observed_hashes = verify_frozen_oracle_hashes(root)
    packet_digest = verify_pinned_packet(pinned_plan)
    verify_source_oracle_overlaps(root, expected)
    if review_receipt is not None:
        validate_review_receipt(
            review_receipt,
            matrix,
            expected_sha256=expected_sha256,
            matrix_sha256=matrix_sha256,
            packet_sha256=packet_digest,
        )
    return assertion_count, {"graph-explorer-planner-packet": packet_digest, **observed_hashes}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expected", required=True, type=Path)
    parser.add_argument("--matrix", required=True, type=Path)
    parser.add_argument("--pinned-plan", required=True, type=Path)
    parser.add_argument("--require-nine-cases", action="store_true", required=True)
    parser.add_argument("--check-frozen-oracle-hashes", action="store_true", required=True)
    parser.add_argument("--review-receipt", type=Path, help="optional future independent-review JSON receipt")
    parser.add_argument("--repo-root", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args(argv)
    try:
        expected, expected_digest = load_json(args.expected)
        matrix, matrix_digest = load_json(args.matrix)
        receipt = load_json(args.review_receipt)[0] if args.review_receipt else None
        assertion_count, sources = validate_artifacts(
            expected,
            matrix,
            expected_digest,
            matrix_digest,
            root=args.repo_root.resolve(),
            pinned_plan=args.pinned_plan.resolve(),
            check_frozen_hashes=args.check_frozen_oracle_hashes,
            review_receipt=receipt,
        )
    except (OSError, TypeError, ValueError, KeyError) as exc:
        print(f"graph-explorer contract: FAIL: {exc}", file=sys.stderr)
        return 1
    receipt_note = "; future independent-review receipt validated" if receipt is not None else "; no review receipt supplied"
    print(
        "graph-explorer contract: PASS (9 fixed groups, "
        f"{assertion_count} mapped nonzero assertions, {len(sources) - 1} frozen preexisting oracle/matrix hashes; "
        f"candidate output not consulted{receipt_note})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

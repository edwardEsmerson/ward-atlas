#!/usr/bin/env python3
"""Validate the immutable dock bitemporal oracle without consulting candidate output.

This is a contract-artifact check only. It does not import product code, connect
to FalkorDB, or claim the product implements these outcomes. It verifies the
fixed ten-group oracle/matrix, their exact decision-packet mapping, the packet
digest, and byte hashes plus relevant overlaps for every pre-existing test
oracle and adversarial matrix.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


EXPECTED_ARTIFACT_SHA256 = "243c92775d7c1366962dcd7c5c9acd9ea753f2924af84c2c5dc99b0b5c872205"
MATRIX_ARTIFACT_SHA256 = "84127f273043a03299823905bbecc06a755f79cb9a3b34c44793815eda7f5318"
PACKET_SHA256 = "969a34724f74f8638a08c4f98137823fac06645994212d1ca4ac6d7dddc711bd"
PACKET_FILENAME = "planner-280b00074097409f80433898c7dcdac8.md"
PACKET_OUTCOME_LINES = tuple(range(66, 76))
PACKET_OUTCOME_PREFIXES = (
    "1. **Cold empty / unknown:**",
    "2. **Legacy compatibility:**",
    "3. **Earlier valid time, earlier knowledge:**",
    "4. **Same valid time, newer knowledge:**",
    "5. **Known future-effective assignment:**",
    "6. **Crossed axes prevent hindsight:**",
    "7. **Graph-edge counterfactuals:**",
    "8. **Denied-source invariance:**",
    "9. **Time validation:**",
    "10. **Repeat/reconnect and read-only boundary:**",
)

# All 18 test JSON oracles/matrices present before this task. Pinning their
# bytes prevents this new oracle from silently rewriting earlier acceptance.
PINNED_EXISTING = {
    "tests/adversarial-matrix.json": "6d0edbc69620da85b494185879565c8260c9d2d3fe6afdd5bf56c5acd92205ba",
    "tests/cascade-adversarial-matrix.json": "c74431b0c2bebbdc25419bf58d39129d150779e1ef7418b64c80dee6979398db",
    "tests/cascade-expected-results.json": "52fd4abe069e850e4ee070b8e6d0f9ed26fff5ad1e5e0c47efb28ad78227581b",
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
    "dock-bitemporal-cold-empty",
    "dock-bitemporal-legacy-compatibility",
    "dock-bitemporal-early-known-r1-duty",
    "dock-bitemporal-known-r2-pending-review",
    "dock-bitemporal-known-future-effective-swing",
    "dock-bitemporal-crossed-axes-unknown",
    "dock-bitemporal-edge-removal-reseed",
    "dock-bitemporal-denied-source-invariance",
    "dock-bitemporal-time-validation",
    "dock-bitemporal-reseed-reconnect-read-only",
)

REQUIRED_ASSERTION_POINTERS = {
    REQUIRED_CASES[0]: {
        "/cases/0/expected/role_path",
        "/cases/0/expected/role_status",
        "/cases/0/expected/source_versions",
        "/cases/0/expected/review_candidates",
        "/cases/0/expected/risk_or_currentness_conclusion",
    },
    REQUIRED_CASES[1]: {
        "/cases/1/expected/request/known_at",
        "/cases/1/expected/role_path",
        "/cases/1/expected/source_versions",
        "/cases/1/expected/review_candidates",
        "/cases/1/expected/legacy_projection_matches_accepted_shift_board_oracle",
    },
    REQUIRED_CASES[2]: {
        "/cases/2/expected/effective_at",
        "/cases/2/expected/known_at",
        "/cases/2/expected/role_path",
        "/cases/2/expected/source_versions",
        "/cases/2/expected/r2_identifier_title_effective_time_count_or_future_revision_wording_visible",
    },
    REQUIRED_CASES[3]: {
        "/cases/3/expected/effective_at",
        "/cases/3/expected/known_at",
        "/cases/3/expected/role_path",
        "/cases/3/expected/source_versions",
        "/cases/3/expected/review_candidates",
        "/cases/3/expected/candidate_disposition",
    },
    REQUIRED_CASES[4]: {
        "/cases/4/expected/effective_at",
        "/cases/4/expected/known_at",
        "/cases/4/expected/role_path",
        "/cases/4/expected/source_versions",
        "/cases/4/expected/interpretation",
    },
    REQUIRED_CASES[5]: {
        "/cases/5/expected/effective_at",
        "/cases/5/expected/known_at",
        "/cases/5/expected/role_path",
        "/cases/5/expected/source_versions",
        "/cases/5/expected/review_candidates",
        "/cases/5/expected/r2_identifier_title_effective_time_count_or_existence_hint_visible",
        "/cases/5/expected/unknown_not_guessed_as_duty_or_swing",
    },
    REQUIRED_CASES[6]: {
        "/cases/6/expected/baseline",
        "/cases/6/expected/remove_only_supersedes_edge",
        "/cases/6/expected/remove_only_supersedes_edge/linkage_status",
        "/cases/6/expected/remove_only_r2_ownership_edge",
        "/cases/6/expected/reseed",
    },
    REQUIRED_CASES[7]: {
        "/cases/7/expected/viewer_scope",
        "/cases/7/expected/axis_pairs",
        "/cases/7/expected/full_visible_response_unchanged_before_after_for_each_axis_pair",
        "/cases/7/expected/hidden_identifier_title_fact_count_existence_relationship_path_or_dependent_paraphrase_disclosed",
    },
    REQUIRED_CASES[8]: {
        "/cases/8/expected/offset_pair",
        "/cases/8/expected/normalized_pair",
        "/cases/8/expected/offset_result_matches_case",
        "/cases/8/expected/malformed_effective_at",
        "/cases/8/expected/malformed_known_at",
        "/cases/8/expected/each_malformed_axis_rejected_before_graph_or_output_mutation",
        "/cases/8/expected/naive_local_time_fallback",
    },
    REQUIRED_CASES[9]: {
        "/cases/9/expected/repeat_seed_duplicate_nodes",
        "/cases/9/expected/repeat_seed_duplicate_relationships",
        "/cases/9/expected/after_reconnect_result_matches",
        "/cases/9/expected/time_selection_graph_writes",
        "/cases/9/expected/time_selection_action_writes",
        "/cases/9/expected/messages_sent",
        "/cases/9/expected/calls_made",
        "/cases/9/expected/tickets_created",
        "/cases/9/expected/explicit_confirmation_is_separate_human_action",
        "/cases/9/expected/desktop_and_mobile_api_results_agree",
        "/cases/9/expected/cleanup_deletes_only_task_owned_graph_and_stops_only_owned_process",
    },
}


class ContractFailure(ValueError):
    """Raised when an independently fixed outcome or mapping has drifted."""


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


def _packet_pins() -> list[dict[str, Any]]:
    return [
        {
            "id": "dock-bitemporal-packet",
            "file": f"planner-280b00074097409f80433898c7dcdac8/plans/{PACKET_FILENAME}",
            "sha256": PACKET_SHA256,
            "outcome_lines": list(PACKET_OUTCOME_LINES),
        }
    ]


def expected_pinned_sources() -> list[dict[str, Any]]:
    return _packet_pins() + [
        {"id": path.replace("/", "-").removesuffix(".json"), "file": path, "sha256": digest}
        for path, digest in PINNED_EXISTING.items()
    ]


def validate_structure(expected: dict[str, Any], matrix: dict[str, Any], require_ten_groups: bool) -> int:
    """Check fixed groups, the two independent axes, and nonzero matrix mappings."""
    require(expected.get("schema_version") == 1 and matrix.get("schema_version") == 1, "schema_version must be 1")
    require(expected.get("product") == "WardAtlas", "unexpected product")
    require("candidate output is never" in expected.get("oracle_policy", "").lower(), "candidate output cannot be the oracle")
    scope = expected.get("scope")
    require(isinstance(scope, dict) and scope.get("synthetic_only") is True, "oracle must remain synthetic-only")
    require(scope.get("clinical_decisions") is False, "oracle must forbid clinical decisions")
    require(scope.get("candidate_output_is_oracle") is False, "candidate output must not supply expectations")
    require(scope.get("external_writes") is False, "external writes must remain forbidden")
    require("actual falkordb" in str(scope.get("database_requirement", "")).lower(), "actual FalkorDB must remain required")
    require(matrix.get("oracle_file") == "tests/dock-bitemporal-expected-results.json", "matrix points at another oracle")
    require(expected.get("pinned_sources") == expected_pinned_sources(), "packet or pre-existing oracle/matrix pins changed")

    cases = expected.get("cases")
    matrix_cases = matrix.get("cases")
    require(isinstance(cases, list) and isinstance(matrix_cases, list), "expected and matrix cases must be arrays")
    case_ids = _unique_ids(cases, "expected case")
    matrix_ids = _unique_ids(matrix_cases, "matrix case")
    if require_ten_groups:
        require(len(cases) == 10 and len(matrix_cases) == 10, "--require-ten-groups requires exactly ten groups")
    require(tuple(case_ids) == REQUIRED_CASES, "the ten fixed outcome groups or order changed")
    require(matrix_ids == case_ids, "matrix cases must map one-to-one in the same order as outcomes")

    by_id = {row["id"]: row["expected"] for row in cases}
    require(by_id[REQUIRED_CASES[1]]["request"] == {"dock_at": "2026-10-14T20:29:00Z", "known_at": "omitted"}, "legacy dock_at compatibility input changed")
    required_pairs = {
        REQUIRED_CASES[2]: ("2026-10-14T20:29:00Z", "2026-10-14T20:17:59Z"),
        REQUIRED_CASES[3]: ("2026-10-14T20:29:00Z", "2026-10-14T20:19:00Z"),
        REQUIRED_CASES[4]: ("2026-10-14T20:30:00Z", "2026-10-14T20:19:00Z"),
        REQUIRED_CASES[5]: ("2026-10-14T20:30:00Z", "2026-10-14T20:17:59Z"),
    }
    for case_id, pair in required_pairs.items():
        value = by_id[case_id]
        require((value.get("effective_at"), value.get("known_at")) == pair, f"{case_id} effective_at/known_at pair changed")

    total = 0
    for index, (scenario, case) in enumerate(zip(cases, matrix_cases), start=1):
        case_id = REQUIRED_CASES[index - 1]
        require(case.get("scenario_id") == case_id, f"matrix case {case_id} maps to a different scenario")
        require(isinstance(scenario.get("expected"), dict) and scenario["expected"], f"{case_id} has no fixed expected object")
        require(scenario.get("planner_refs") == [f"dock-bitemporal-packet#outcome-{index}"], f"{case_id} must map to packet outcome {index}")
        require(case.get("planner_refs") == [f"{PACKET_FILENAME}:{PACKET_OUTCOME_LINES[index - 1]}"], f"{case_id} must map to its exact packet line")
        assertions = case.get("pinned_assertions")
        count = case.get("nonzero_assertion_count")
        require(isinstance(assertions, list) and assertions, f"{case_id} has no pinned assertions")
        assertion_ids = [row.get("id") for row in assertions if isinstance(row, dict)]
        pointers = [row.get("expected_pointer") for row in assertions if isinstance(row, dict)]
        require(len(assertion_ids) == len(assertions) and all(isinstance(item, str) and item for item in assertion_ids), f"{case_id} has malformed assertion IDs")
        require(len(assertion_ids) == len(set(assertion_ids)), f"{case_id} assertion IDs must be unique")
        require(all(isinstance(pointer, str) and pointer for pointer in pointers), f"{case_id} has an unmapped assertion")
        require(set(pointers) == REQUIRED_ASSERTION_POINTERS[case_id], f"{case_id} assertions do not map the required fixed outcome fields")
        for pointer in pointers:
            _resolve_pointer(expected, pointer)
        require(type(count) is int and count > 0, f"{case_id} assertion count must be nonzero")
        require(count == len(assertions), f"{case_id} assertion count differs from its explicit mapping")
        require(bool(case.get("attack_or_edge_case")) and bool(case.get("setup")) and bool(case.get("failure_detected")), f"{case_id} lacks adversarial setup/failure semantics")
        total += count
    require(total > 0, "matrix must contain a nonzero assertion total")

    return total


def _normalize_utc(value: str) -> str:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ContractFailure(f"invalid pinned offset timestamp {value!r}") from exc
    require(parsed.tzinfo is not None and parsed.utcoffset() is not None, "pinned offset must include an explicit timezone")
    return parsed.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def validate_fixed_outcomes(expected: dict[str, Any], root: Path) -> None:
    """Cross-check dock values against the accepted shift-board/source oracles."""
    by_id = {case["id"]: case["expected"] for case in expected["cases"]}
    fixture = expected.get("fixture", {})
    require(fixture.get("incident_id") == "INC-207", "dock incident identity changed")
    require(fixture.get("role_path_prefix") == ["INC-207", "EQ-DOCK-CART-2", "SVC-INBOUND-TRANSFER", "DEPT-RECEIVING-DOCK"], "dock graph path prefix changed")
    try:
        shift, _ = _load_json(root / "tests/shift-board-expected-results.json")
        source, _ = _load_json(root / "tests/source-change-expected-results.json")
    except ContractFailure:
        raise
    shift_cases = {row["id"]: row["expected"] for row in shift["cases"]}
    source_cases = {row["id"]: row["expected"] for row in source["scenarios"]}
    dock = shift_cases["dock-role-time-and-source-evidence"]
    review = shift_cases["explicit-edge-review-queue"]
    source_dock = source["fixture"]
    source_paths = source["expected_paths"]
    r1, r2 = dock["source_versions"]
    duty_path = dock["before"]["role_path"]
    swing_path = dock["at_effective_time"]["role_path"]
    candidates = review["review_candidates"]

    require(fixture.get("equipment_id") == dock["equipment_id"] == source_dock["equipment_id"], "dock equipment differs from accepted oracles")
    require(fixture.get("service_id") == dock["service_id"] == source_dock["service_id"], "dock service differs from accepted oracles")
    require(fixture.get("department_id") == dock["department_id"] == source_dock["department_id"], "dock department differs from accepted oracles")
    require(fixture.get("r1") == {**r1, "role_id": "ROLE-DOCK-DUTY"}, "R1 provenance differs from the accepted shift-board oracle")
    require(fixture.get("r2") == {**r2, "role_id": "ROLE-DOCK-SWING"}, "R2 provenance differs from the accepted shift-board oracle")
    require(fixture.get("review_candidate_ids") == candidates == source["expected_review_candidates"], "review candidate IDs/order differ from accepted oracles")
    require(source_paths == {"before": duty_path, "after": swing_path}, "dock ownership paths differ from the accepted source-change oracle")

    legacy = by_id[REQUIRED_CASES[1]]
    require(legacy["role_path"] == duty_path and legacy["source_versions"] == dock["source_versions"], "legacy role/source projection differs from shift-board oracle")
    require(legacy["review_candidates"] == candidates, "legacy review queue differs from shift-board oracle")
    require(legacy["unchanged_handover_role_id"] == review["unchanged_handover_role_id"], "legacy handover role differs from shift-board oracle")
    require(legacy["unchanged_handover_source_id"] == review["unchanged_handover_source_id"], "legacy handover provenance differs from shift-board oracle")
    require(legacy["excluded_lexical_only_id"] == review["excluded_lexical_only_id"], "legacy lexical-only exclusion differs from shift-board oracle")
    require(legacy["coverage_status"] == review["coverage_status"] and legacy["unlinked_artifact_status"] == review["unlinked_artifact_status"], "legacy explicit-edge/unknown semantics drifted")

    early = by_id[REQUIRED_CASES[2]]
    require(early["role_path"] == duty_path and early["role_id"] == "ROLE-DOCK-DUTY", "early-knowledge duty path differs from accepted oracle")
    require(early["source_versions"] == [r1] and early["review_candidates"] == [], "pre-R2 knowledge must contain R1 only and no candidates")
    require(early["r2_identifier_title_effective_time_count_or_future_revision_wording_visible"] is False, "early knowledge must not hint at R2")

    known = by_id[REQUIRED_CASES[3]]
    known_versions = known["source_versions"]
    require(known["role_path"] == duty_path and known["role_id"] == "ROLE-DOCK-DUTY", "known-R2 pair changed the still-valid duty path")
    require(len(known_versions) == 2 and known_versions[0] == r1, "known-R2 source history must preserve R1 then R2")
    known_r2 = dict(known_versions[1])
    require(known_r2.pop("status", None) == "recorded/pending" and known_r2 == r2, "known R2 status/provenance changed")
    require(known["review_candidates"] == candidates and "invalid" in known["candidate_disposition"], "known-R2 candidates/status differ from packet")

    future = by_id[REQUIRED_CASES[4]]
    future_versions = [dict(row) for row in future["source_versions"]]
    require(future["role_path"] == swing_path and future["role_id"] == "ROLE-DOCK-SWING", "effective boundary path differs from accepted oracle")
    require(future_versions[0] == r1 and future_versions[1].pop("status", None) == "recorded/pending" and future_versions[1] == r2, "future-effective evidence differs from R2 source fact")
    require(future["review_candidates"] == candidates, "future-effective pair lost the known review candidates")
    require("valid at effective_at" in future["interpretation"] and "not claim" in future["interpretation"], "future-effective interpretation conflates the axes")

    crossed = by_id[REQUIRED_CASES[5]]
    require(crossed["role_path"] == [] and crossed["role_id"] is None and crossed["role_status"] == "unknown", "crossed axes must leave role unknown")
    require(crossed["source_versions"] == [r1] and crossed["review_candidates"] == [], "crossed axes must expose R1 only")
    require(crossed["r2_identifier_title_effective_time_count_or_existence_hint_visible"] is False, "crossed axes must not reveal R2 existence")
    require(crossed["unknown_not_guessed_as_duty_or_swing"] is True, "crossed-axis unknown state was weakened")

    edge = by_id[REQUIRED_CASES[6]]
    require(edge["baseline"]["role_path"] == swing_path and edge["baseline"]["review_candidates"] == candidates, "edge baseline differs from accepted outcomes")
    require(edge["remove_only_supersedes_edge"]["review_candidates"] == [] and edge["remove_only_supersedes_edge"]["linkage_status"] == "unknown" and edge["remove_only_supersedes_edge"]["currentness_conclusion"] is False, "supersedes-edge removal must remove queue and leave linkage unknown")
    require(edge["remove_only_r2_ownership_edge"]["role_path"] == [] and edge["remove_only_r2_ownership_edge"]["role_id"] is None, "R2 OWNED_BY removal must make future role unknown")
    require(edge["reseed"]["restores_exact_baseline"] is True, "reseed must restore exact baseline")

    denied = by_id[REQUIRED_CASES[7]]
    require(denied["viewer_scope"] == "operations" and denied["full_visible_response_unchanged_before_after_for_each_axis_pair"] is True, "denied-source response must be fully invariant")
    require(denied["hidden_identifier_title_fact_count_existence_relationship_path_or_dependent_paraphrase_disclosed"] is False, "denied-source non-disclosure expectation weakened")
    require(denied["restricted_fixture_oracle"] == "tests/shift-board-expected-results.json#denied-source-nondisclosure.expected", "denied fixture must reuse the accepted synthetic tripwire")
    require(denied["axis_pairs"] == [
        ["2026-10-14T20:29:00Z", "2026-10-14T20:17:59Z"],
        ["2026-10-14T20:29:00Z", "2026-10-14T20:19:00Z"],
        ["2026-10-14T20:30:00Z", "2026-10-14T20:19:00Z"],
        ["2026-10-14T20:30:00Z", "2026-10-14T20:17:59Z"],
    ], "ACL invariance must cover all four fixed axis pairs")

    time = by_id[REQUIRED_CASES[8]]
    require(_normalize_utc(time["offset_pair"]["effective_at"]) == time["normalized_pair"]["effective_at"], "effective offset does not normalize to expected UTC")
    require(_normalize_utc(time["offset_pair"]["known_at"]) == time["normalized_pair"]["known_at"], "knowledge offset does not normalize to expected UTC")
    require(time["normalized_pair"] == {"effective_at": "2026-10-14T20:29:00Z", "known_at": "2026-10-14T20:19:00Z"}, "normalized temporal pair changed")
    require(time["offset_result_matches_case"] == REQUIRED_CASES[3], "offset must match the same-valid/newer-knowledge fixed result")
    require(time["malformed_effective_at"] and time["malformed_known_at"] and time["each_malformed_axis_rejected_before_graph_or_output_mutation"] is True and time["naive_local_time_fallback"] is False, "malformed and timezone-naive input must reject before mutation")

    lifecycle = by_id[REQUIRED_CASES[9]]
    require(lifecycle["after_reconnect_result_matches"] == REQUIRED_CASES[3], "reconnect result must equal the fixed known-R2 result")
    for field in ("repeat_seed_duplicate_nodes", "repeat_seed_duplicate_relationships", "time_selection_graph_writes", "time_selection_action_writes", "messages_sent", "calls_made", "tickets_created"):
        require(lifecycle[field] == 0, f"read-only/reseed outcome {field} must be zero")
    require(lifecycle["explicit_confirmation_is_separate_human_action"] is True, "existing confirmation must remain separate and human-confirmed")
    require(lifecycle["desktop_and_mobile_api_results_agree"] is True and lifecycle["cleanup_deletes_only_task_owned_graph_and_stops_only_owned_process"] is True, "lifecycle UI/ownership guarantees changed")


def verify_pinned_packet(path: Path) -> str:
    require(path.name == PACKET_FILENAME, f"pinned packet must be named {PACKET_FILENAME}")
    try:
        raw = path.read_bytes()
        text = raw.decode("utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise ContractFailure(f"cannot read pinned decision packet {path}: {exc}") from exc
    digest = hashlib.sha256(raw).hexdigest()
    require(digest == PACKET_SHA256, "decision packet bytes differ from the independently pinned bitemporal packet")
    lines = text.splitlines()
    for line_number, prefix in zip(PACKET_OUTCOME_LINES, PACKET_OUTCOME_PREFIXES):
        require(len(lines) >= line_number and lines[line_number - 1].startswith(prefix), f"decision packet outcome heading changed at line {line_number}")
    return digest


def verify_existing_oracles(root: Path, expected: dict[str, Any]) -> dict[str, str]:
    """Check all pre-task oracle/matrix hashes and their embedded manifest pins."""
    require(expected.get("pinned_sources", [])[1:] == expected_pinned_sources()[1:], "oracle/matrix manifest differs from fixed source hashes")
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


def validate_plan_mappings(expected: dict[str, Any], matrix: dict[str, Any]) -> None:
    """Verify numbered packet outcome references agree one-to-one in both files."""
    for index, (scenario, case) in enumerate(zip(expected["cases"], matrix["cases"]), start=1):
        line = PACKET_OUTCOME_LINES[index - 1]
        require(scenario["planner_refs"] == [f"dock-bitemporal-packet#outcome-{index}"], f"{case['id']} expected outcome reference changed")
        require(case["planner_refs"] == [f"{PACKET_FILENAME}:{line}"], f"{case['id']} matrix line reference changed")


def validate_artifacts(
    expected: dict[str, Any],
    matrix: dict[str, Any],
    expected_sha256: str,
    matrix_sha256: str,
    root: Path,
    pinned_plan: Path,
    require_ten_groups: bool,
) -> tuple[int, dict[str, str]]:
    assertion_count = validate_structure(expected, matrix, require_ten_groups)
    require(expected_sha256 == EXPECTED_ARTIFACT_SHA256, "dock bitemporal expected-results bytes differ from their fixed oracle hash")
    require(matrix_sha256 == MATRIX_ARTIFACT_SHA256, "dock bitemporal adversarial matrix bytes differ from its fixed hash")
    packet_digest = verify_pinned_packet(pinned_plan)
    oracle_digests = verify_existing_oracles(root, expected)
    validate_plan_mappings(expected, matrix)
    validate_fixed_outcomes(expected, root)
    return assertion_count, {"dock-bitemporal-packet": packet_digest, **oracle_digests}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expected", required=True, type=Path)
    parser.add_argument("--matrix", required=True, type=Path)
    parser.add_argument("--pinned-plan", required=True, type=Path)
    parser.add_argument("--require-ten-groups", action="store_true")
    parser.add_argument("--check-frozen-oracle-hashes", action="store_true")
    args = parser.parse_args(argv)
    if not args.require_ten_groups or not args.check_frozen_oracle_hashes:
        print("FAIL: --require-ten-groups and --check-frozen-oracle-hashes are mandatory", file=sys.stderr)
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
            require_ten_groups=True,
        )
    except (OSError, TypeError, ValueError, KeyError) as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1
    print(
        "PASS: ten fixed bitemporal dock outcome groups, exact axis pairs and packet mapping; "
        f"{assertions} mapped nonzero assertions; pinned packet and {len(source_digests) - 1} "
        "pre-existing oracle/matrix byte hashes verified; no candidate output used."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

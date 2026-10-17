#!/usr/bin/env python3
"""Verify WardAtlas's fixed escalation oracle, adversarial matrix, and review receipts.

This is a candidate-independent contract check. It reads no product query,
fixture, API, browser, or graph output. The optional review-receipt format is
documented by ``validate_review_receipt`` and binds each reviewer assertion to
the exact fixed group and to the frozen source-oracle hashes.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any


PACKET_PATH = "/workspace/worktrees/planner-51d0c80bef174057bc10db9a64ec3d74/plans/planner-51d0c80bef174057bc10db9a64ec3d74.md"
PACKET_SHA256 = "e3c1b19a56aea779f65e4579669a1f6a175d6dac6ae755e22129122301490b0f"
PACKET_OUTCOME_LINES = tuple(range(75, 84))
PACKET_OUTCOME_PREFIXES = (
    "1. **`cold-empty-unknown`:**",
    "2. **`outage-day-coordinator-paths`:**",
    "3. **`effective-shift-boundary`:**",
    "4. **`observed-restoration-removes-routes`:**",
    "5. **`escalation-edge-removal-unknown`:**",
    "6. **`missing-escalation-source-unknown`:**",
    "7. **`denied-source-invariance`:**",
    "8. **`sop-claim-separation-and-time-validation`:**",
    "9. **`reseed-reconnect-browser-read-only`:**",
)

# Freeze every pre-existing expected-results and adversarial-matrix JSON file,
# including the recent dock bitemporal contract. This list was captured before
# adding either escalation artifact; no product or oracle output is consulted.
PINNED_ORACLE_HASHES = {
    "tests/adversarial-matrix.json": "6d0edbc69620da85b494185879565c8260c9d2d3fe6afdd5bf56c5acd92205ba",
    "tests/cascade-adversarial-matrix.json": "c74431b0c2bebbdc25419bf58d39129d150779e1ef7418b64c80dee6979398db",
    "tests/cascade-expected-results.json": "52fd4abe069e850e4ee070b8e6d0f9ed26fff5ad1e5e0c47efb28ad78227581b",
    "tests/dock-bitemporal-adversarial-matrix.json": "84127f273043a03299823905bbecc06a755f79cb9a3b34c44793815eda7f5318",
    "tests/dock-bitemporal-expected-results.json": "243c92775d7c1366962dcd7c5c9acd9ea753f2924af84c2c5dc99b0b5c872205",
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

CASE_IDS = (
    "cold-empty-unknown",
    "outage-day-coordinator-paths",
    "effective-shift-boundary",
    "observed-restoration-removes-routes",
    "escalation-edge-removal-unknown",
    "missing-escalation-source-unknown",
    "denied-source-invariance",
    "sop-claim-separation-and-time-validation",
    "reseed-reconnect-browser-read-only",
)

EXPECTED_ARTIFACT_SHA256 = "b9db49c9398d76c063ac126b5234024ba32dbac1fb7806b443e4f6508868e929"
MATRIX_ARTIFACT_SHA256 = "7e8e6c9813e88544dd61b78e208a48d3edfd48d7b4f3a3b6dbbb1a119c51af17"
EXPECTED_CANONICAL_SHA256 = "18111f823016c5079a75ecca782194d1731552554694af66284a7220e45b5e90"
MATRIX_CANONICAL_SHA256 = "55f916325ff9c0a5bbf06d79a043e15f652697ded4bec3ce829cda2204c46e13"

BASE_PATHS = [
    ["INC-204", "CONVEYOR-C4", "SVC-MATERIAL-TRANSFER", "DEPT-CENTRAL-STERILE", "ROLE-STERILE-OPS-COORD"],
    ["INC-204", "CONVEYOR-C4", "SVC-MATERIAL-TRANSFER", "DEPT-MATERIALS-LOGISTICS", "ROLE-MATERIALS-DUTY"],
]

REQUIRED_ASSERTION_POINTERS = {
    CASE_IDS[0]: {
        "/expected/routes", "/expected/route_count", "/expected/board_state",
        "/expected/coordinator_role_invented", "/expected/no_escalation_need_concluded",
    },
    CASE_IDS[1]: {
        "/expected/incident_id", "/expected/impact_paths", "/expected/routes",
        "/expected/routes/0/escalation_relationship/id",
        "/expected/routes/1/escalation_relationship/id",
        "/expected/routes/0/coordinator_role_id",
        "/expected/routes/1/coordinator_role_id",
        "/expected/routes/0/provenance",
        "/expected/routes/1/provenance/4/title",
        "/expected/routes/1/provenance/4/observed_at",
        "/expected/contact_supplied",
    },
    CASE_IDS[2]: {
        "/expected/before_boundary/as_of", "/expected/before_boundary/routes_ref",
        "/expected/at_boundary/as_of",
        "/expected/at_boundary/routes/0/escalation_relationship/id",
        "/expected/at_boundary/routes/1/escalation_relationship/id",
        "/expected/at_boundary/routes/0/coordinator_role_id",
        "/expected/at_boundary/routes/0/provenance/4/title",
        "/expected/at_boundary/routes/1/provenance/4/observed_at",
        "/expected/day_interval/semantics", "/expected/night_interval/valid_from",
        "/expected/unchanged_impact_paths_departments_and_current_roles",
    },
    CASE_IDS[3]: {
        "/expected/as_of", "/expected/restoration/event_id", "/expected/restoration/source_id",
        "/expected/restoration/equipment_status", "/expected/impact_paths", "/expected/routes",
        "/expected/sop_conflict_status", "/expected/safe_or_no_risk_conclusion",
    },
    CASE_IDS[4]: {
        "/expected/remove_only_relationship", "/expected/after_removal/missing_route/escalation_status",
        "/expected/after_removal/missing_route/complete_escalation_route_returned",
        "/expected/after_removal/routes_ref", "/expected/after_removal/underlying_impact_paths_unchanged",
        "/expected/after_removal/other_relationships_removed",
        "/expected/after_removal/existing_board_results_unchanged",
        "/expected/reseed/restores_exact_baseline",
    },
    CASE_IDS[5]: {
        "/expected/removed_evidence_link", "/expected/after_removal/missing_route/escalation_status",
        "/expected/after_removal/missing_route", "/expected/after_removal/role_text_cannot_reconstruct_route",
        "/expected/after_removal/other_roster_cannot_reconstruct_route",
        "/expected/after_removal/sop_claims_role_cannot_reconstruct_route",
        "/expected/after_removal/underlying_impact_paths_unchanged",
        "/expected/reseed_restores_exact_baseline",
    },
    CASE_IDS[6]: {
        "/expected/viewer_scope", "/expected/visible_response_before_ref",
        "/expected/visible_response_after_ref", "/expected/operations_response_semantically_identical_before_after",
        "/expected/visible_route_count_before", "/expected/visible_route_count_after",
        "/expected/restricted_probe/to_role_id", "/expected/restricted_probe/source_title",
        "/expected/restricted_identifier_title_fact_count_existence_relationship_path_or_paraphrase_disclosed",
    },
    CASE_IDS[7]: {
        "/expected/sop_conflict/status", "/expected/sop_claims/0", "/expected/sop_claims/1",
        "/expected/sop_claims_are_escalation_assignments", "/expected/escalation_assignments_from_sop_claims",
        "/expected/sop_witness_change_alters_escalation_routes", "/expected/offset_input",
        "/expected/offset_normalized_as_of", "/expected/offset_result_routes_ref",
        "/expected/malformed_timestamp_rejected", "/expected/rejection_precedes_graph_or_action_mutation",
    },
    CASE_IDS[8]: {
        "/expected/seed_runs", "/expected/duplicate_node_delta_after_second_seed",
        "/expected/duplicate_relationship_delta_after_second_seed", "/expected/fresh_falkordb_client_required",
        "/expected/reconnected_result_ref", "/expected/api_browser_role_paths_and_sources_agree",
        "/expected/read_only_route_query_action_writes", "/expected/external_writes",
        "/expected/cleanup_deletes_only_task_owned_graph", "/expected/cleanup_stops_only_task_owned_processes",
    },
}


class ContractError(ValueError):
    """Raised when a fixed outcome, source hash, or assertion mapping drifts."""


def _fail(message: str) -> None:
    raise ContractError(message)


def _need(condition: bool, message: str) -> None:
    if not condition:
        _fail(message)


def _json_equal(actual: Any, expected: Any) -> bool:
    """JSON equality with strict types (in particular, bool is not an int)."""
    if type(actual) is not type(expected):
        return False
    if isinstance(actual, dict):
        return actual.keys() == expected.keys() and all(_json_equal(actual[key], expected[key]) for key in actual)
    if isinstance(actual, list):
        return len(actual) == len(expected) and all(_json_equal(a, e) for a, e in zip(actual, expected))
    return actual == expected


def _same(actual: Any, expected: Any, label: str) -> None:
    if not _json_equal(actual, expected):
        _fail(f"{label} differs from the fixed escalation oracle")


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _canonical_sha256(value: Any) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _sha256(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError as exc:
        _fail(f"cannot read/hash pinned file {path}: {exc}")


def _read_json(path: Path, label: str) -> tuple[dict[str, Any], str]:
    try:
        raw = path.read_bytes()
        value = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        _fail(f"cannot read valid {label} JSON at {path}: {exc}")
    _need(isinstance(value, dict), f"{label} must be an object")
    return value, hashlib.sha256(raw).hexdigest()


def _json_pointer(document: Any, pointer: str, label: str) -> Any:
    _need(isinstance(pointer, str) and pointer.startswith("/"), f"{label}: JSON pointer must begin with '/'")
    value = document
    for token in pointer[1:].split("/"):
        key = token.replace("~1", "/").replace("~0", "~")
        if isinstance(value, list):
            _need(key.isdigit() and int(key) < len(value), f"{label}: invalid list pointer {pointer}")
            value = value[int(key)]
        elif isinstance(value, dict) and key in value:
            value = value[key]
        else:
            _fail(f"{label}: unresolved pointer {pointer}")
    return value


def _resolve_references(value: Any, document: dict[str, Any], label: str) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            _resolve_references(item, document, f"{label}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _resolve_references(item, document, f"{label}[{index}]")
    elif isinstance(value, str) and value.startswith("#/"):
        _json_pointer(document, value[1:], label)


def _expected_frozen_file_list() -> list[dict[str, str]]:
    return [{"path": path, "sha256": digest} for path, digest in PINNED_ORACLE_HASHES.items()]


def _validate_packet(path: Path) -> str:
    _same(str(path), PACKET_PATH, "pinned decision packet path")
    try:
        raw = path.read_bytes()
        text = raw.decode("utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        _fail(f"cannot read pinned planner packet {path}: {exc}")
    digest = hashlib.sha256(raw).hexdigest()
    _same(digest, PACKET_SHA256, "pinned decision packet SHA-256")
    lines = text.splitlines()
    _need(len(lines) >= max(PACKET_OUTCOME_LINES), "pinned packet is missing one or more outcomes")
    for line_number, prefix in zip(PACKET_OUTCOME_LINES, PACKET_OUTCOME_PREFIXES):
        _need(lines[line_number - 1].startswith(prefix), f"packet outcome {line_number} does not match group {prefix}")
    return digest


def _validate_frozen_files(expected: dict[str, Any], repository_root: Path) -> dict[str, str]:
    _same(expected.get("frozen_preexisting_contract_files"), _expected_frozen_file_list(), "frozen pre-existing oracle/matrix manifest")
    actuals: dict[str, str] = {}
    for relative, pinned_hash in PINNED_ORACLE_HASHES.items():
        actual_hash = _sha256(repository_root / relative)
        _same(actual_hash, pinned_hash, f"pre-existing oracle/matrix byte hash {relative}")
        actuals[relative] = actual_hash
    return actuals


def _validate_fixture_literals(expected: dict[str, Any]) -> None:
    fixture = expected.get("fixture")
    _need(isinstance(fixture, dict), "fixture must be an object")
    _same(fixture.get("incident_id"), "INC-204", "fixture incident ID")
    _same(fixture.get("equipment_id"), "CONVEYOR-C4", "fixture equipment ID")
    _same(fixture.get("service_id"), "SVC-MATERIAL-TRANSFER", "fixture service ID")
    _same(fixture.get("outage_as_of"), "2026-10-14T18:30:00Z", "outage time")
    _same(fixture.get("restoration_event_id"), "EVT-C4-RESTORED-01", "restoration event ID")
    _same(fixture.get("restoration_source_id"), "SRC-ASSET-C4-RESTORE", "restoration source ID")
    _same(fixture.get("restoration_event_at"), "2026-10-14T19:15:00Z", "restoration event time")
    _same(fixture.get("day_source"), {
        "source_id": "SRC-OPS-ESCALATION-DAY",
        "title": "Synthetic Operations Day Escalation Roster",
        "observed_at": "2026-10-14T17:50:00Z",
    }, "day escalation source")
    _same(fixture.get("night_source"), {
        "source_id": "SRC-OPS-ESCALATION-NIGHT",
        "title": "Synthetic Operations Night Escalation Roster",
        "observed_at": "2026-10-14T17:51:00Z",
    }, "night escalation source")
    _same(fixture.get("day_interval"), {
        "valid_from": "2026-10-14T07:00:00Z",
        "valid_to": "2026-10-14T19:00:00Z",
        "semantics": "half-open [valid_from, valid_to)",
    }, "day half-open interval")
    _same(fixture.get("night_interval"), {
        "valid_from": "2026-10-14T19:00:00Z",
        "valid_to": "2026-10-15T07:00:00Z",
        "semantics": "half-open [valid_from, valid_to)",
    }, "night half-open interval")


def _source_rows(role_observed_at: str, escalation_source: dict[str, str]) -> list[dict[str, str]]:
    return [
        {"source_id": "SRC-INC-204", "title": "Synthetic Incident Register INC-204", "observed_at": "2026-10-14T18:00:00Z"},
        {"source_id": "SRC-ASSET-C4", "title": "Synthetic Asset State Ledger", "observed_at": "2026-10-14T18:02:00Z"},
        {"source_id": "SRC-SERVICE-CATALOG", "title": "Synthetic Service Dependency Register", "observed_at": "2026-10-14T18:03:00Z"},
        {"source_id": "SRC-OPS-ROSTER", "title": "Synthetic Operations Role Roster", "observed_at": role_observed_at},
        dict(escalation_source),
    ]


def _route(index: int, shift: str) -> dict[str, Any]:
    impact_path = BASE_PATHS[index]
    current_role = impact_path[-1]
    sterile = index == 0
    if shift == "day":
        coordinator = "ROLE-OPS-DAY-COORD"
        relationship_id = "REL-ESC-STERILE-DAY" if sterile else "REL-ESC-MATERIALS-DAY"
        valid_from, valid_to = "2026-10-14T07:00:00Z", "2026-10-14T19:00:00Z"
        escalation_source = {
            "source_id": "SRC-OPS-ESCALATION-DAY",
            "title": "Synthetic Operations Day Escalation Roster",
            "observed_at": "2026-10-14T17:50:00Z",
        }
    elif shift == "night":
        coordinator = "ROLE-OPS-NIGHT-COORD"
        relationship_id = "REL-ESC-STERILE-NIGHT" if sterile else "REL-ESC-MATERIALS-NIGHT"
        valid_from, valid_to = "2026-10-14T19:00:00Z", "2026-10-15T07:00:00Z"
        escalation_source = {
            "source_id": "SRC-OPS-ESCALATION-NIGHT",
            "title": "Synthetic Operations Night Escalation Roster",
            "observed_at": "2026-10-14T17:51:00Z",
        }
    else:  # pragma: no cover - an internal programming error, not packet input
        raise AssertionError(shift)
    return {
        "status": "routed",
        "impact_path": list(impact_path),
        "route_path": [*impact_path, coordinator],
        "department_id": impact_path[-2],
        "current_role_id": current_role,
        "coordinator_role_id": coordinator,
        "escalation_relationship": {
            "id": relationship_id,
            "type": "ESCALATES_TO",
            "from": current_role,
            "to": coordinator,
            "valid_from": valid_from,
            "valid_to": valid_to,
            "interval_semantics": "half-open [valid_from, valid_to)",
        },
        "source_scope": "operations",
        "provenance": _source_rows(
            "2026-10-14T18:04:00Z" if sterile else "2026-10-14T18:05:00Z",
            escalation_source,
        ),
    }


def _validate_fixed_outcomes(expected: dict[str, Any]) -> dict[str, dict[str, Any]]:
    cases = expected.get("cases")
    _need(isinstance(cases, list), "expected.cases must be an array")
    ids = [case.get("id") for case in cases if isinstance(case, dict)]
    _same(ids, list(CASE_IDS), "nine fixed escalation group IDs/order")
    by_id = {case["id"]: case for case in cases}
    for index, case_id in enumerate(CASE_IDS, start=1):
        _same(by_id[case_id].get("planner_ref"), f"escalation-decision-packet#outcome-{index}", f"{case_id} packet outcome reference")
        _need(isinstance(by_id[case_id].get("evidence_semantics"), str) and by_id[case_id]["evidence_semantics"].strip(), f"{case_id} needs evidence semantics")
    day_routes = [_route(0, "day"), _route(1, "day")]
    night_routes = [_route(0, "night"), _route(1, "night")]

    cold = by_id[CASE_IDS[0]]["expected"]
    _same(cold, {
        "as_of": "2026-10-14T18:30:00Z", "incident_id": "INC-204", "routes": [],
        "route_count": 0, "board_state": "empty/unknown", "coordinator_role_invented": False,
        "no_escalation_need_concluded": False,
    }, "cold empty/unknown outcome")

    day = by_id[CASE_IDS[1]]["expected"]
    _same(day.get("as_of"), "2026-10-14T18:30:00Z", "day route time")
    _same(day.get("status"), "observed-impact", "active outage status")
    _same(day.get("incident_id"), "INC-204", "day route incident")
    _same(day.get("equipment_id"), "CONVEYOR-C4", "day route equipment")
    _same(day.get("service_id"), "SVC-MATERIAL-TRANSFER", "day route service")
    _same(day.get("route_count"), 2, "day route count")
    _same(day.get("impact_paths"), BASE_PATHS, "exact two existing impact paths")
    _same(day.get("routes"), day_routes, "exact day coordinator route paths and provenance")
    _same(day.get("contact_supplied"), False, "no contact supplied")
    _same(day.get("action_taken"), False, "no action taken")

    boundary = by_id[CASE_IDS[2]]["expected"]
    _same(boundary.get("before_boundary"), {
        "as_of": "2026-10-14T18:59:00Z", "routes_ref": "#/cases/1/expected/routes",
    }, "pre-boundary result")
    at = boundary.get("at_boundary")
    _need(isinstance(at, dict), "19:00 boundary result must be an object")
    _same(at.get("as_of"), "2026-10-14T19:00:00Z", "boundary instant")
    _same(at.get("status"), "observed-impact", "outage remains active at boundary")
    _same(at.get("route_count"), 2, "night route count")
    _same(at.get("impact_paths"), BASE_PATHS, "boundary preserves impact paths")
    _same(at.get("routes"), night_routes, "exact night role paths and provenance")
    _same(boundary.get("day_interval"), {
        "valid_from": "2026-10-14T07:00:00Z", "valid_to": "2026-10-14T19:00:00Z",
        "relationship_ids": ["REL-ESC-STERILE-DAY", "REL-ESC-MATERIALS-DAY"],
        "semantics": "half-open [valid_from, valid_to)",
    }, "day relationship IDs and interval")
    _same(boundary.get("night_interval"), {
        "valid_from": "2026-10-14T19:00:00Z", "valid_to": "2026-10-15T07:00:00Z",
        "relationship_ids": ["REL-ESC-STERILE-NIGHT", "REL-ESC-MATERIALS-NIGHT"],
        "semantics": "half-open [valid_from, valid_to)",
    }, "night relationship IDs and interval")
    _same(boundary.get("unchanged_impact_paths_departments_and_current_roles"), True, "fixed underlying path identities")
    _same(boundary.get("only_effective_escalation_destination_and_source_change"), True, "only scheduled destination/source changes")

    restored = by_id[CASE_IDS[3]]["expected"]
    _same(restored, {
        "as_of": "2026-10-14T19:15:00Z", "incident_id": "INC-204",
        "restoration": {"event_id": "EVT-C4-RESTORED-01", "source_id": "SRC-ASSET-C4-RESTORE", "event_at": "2026-10-14T19:15:00Z", "equipment_status": "available"},
        "impact_status": "restored", "impact_paths": [], "routes": [], "route_count": 0,
        "sop_conflict_status": "unresolved", "safe_or_no_risk_conclusion": False,
    }, "observed restoration outcome")

    edge = by_id[CASE_IDS[4]]["expected"]
    _same(edge.get("baseline_routes_ref"), "#/cases/1/expected/routes", "edge-removal baseline routes")
    _same(edge.get("remove_only_relationship"), {
        "id": "REL-ESC-MATERIALS-DAY", "type": "ESCALATES_TO", "from": "ROLE-MATERIALS-DUTY",
        "to": "ROLE-OPS-DAY-COORD", "ownership": "test-owned relationship only",
    }, "only removed escalation relationship")
    removal = edge.get("after_removal")
    _need(isinstance(removal, dict), "edge-removal result must be an object")
    _same(removal.get("route_count"), 1, "remaining route count")
    _same(removal.get("routes_ref"), "#/cases/1/expected/routes/0", "unchanged sterile route")
    _same(removal.get("missing_route"), {
        "impact_path": BASE_PATHS[1], "coordinator_role_id": None,
        "escalation_status": "unknown", "complete_escalation_route_returned": False,
    }, "missing route unknown state")
    _same(removal.get("underlying_impact_paths"), BASE_PATHS, "impact paths after edge removal")
    for field in ("underlying_impact_paths_unchanged", "other_times_unchanged", "existing_board_results_unchanged"):
        _same(removal.get(field), True, f"edge-removal isolation {field}")
    _same(removal.get("other_relationships_removed"), [], "no collateral relationship removals")
    _same(edge.get("reseed"), {"restores_exact_baseline": True, "routes_ref": "#/cases/1/expected/routes"}, "edge-removal reseed")

    evidence = by_id[CASE_IDS[5]]["expected"]
    _same(evidence.get("baseline_routes_ref"), "#/cases/1/expected/routes", "evidence baseline")
    _same(evidence.get("removed_evidence_link"), {
        "escalation_relationship_id": "REL-ESC-MATERIALS-DAY", "source_id": "SRC-OPS-ESCALATION-DAY",
        "ownership": "test-owned escalation-edge-to-source evidence relationship only",
    }, "removed source evidence link")
    missing_evidence = evidence.get("after_removal")
    _need(isinstance(missing_evidence, dict), "missing evidence result must be an object")
    _same(missing_evidence.get("route_count"), 1, "route count after missing evidence")
    _same(missing_evidence.get("routes_ref"), "#/cases/1/expected/routes/0", "unaffected route after missing evidence")
    _same(missing_evidence.get("missing_route"), {
        "impact_path": BASE_PATHS[1], "escalation_relationship_id": "REL-ESC-MATERIALS-DAY",
        "source_id": "SRC-OPS-ESCALATION-DAY", "escalation_status": "incomplete/unknown",
        "complete_escalation_route_returned": False,
    }, "missing evidence route")
    for field in ("other_roster_cannot_reconstruct_route", "role_text_cannot_reconstruct_route", "sop_claims_role_cannot_reconstruct_route", "underlying_impact_paths_unchanged"):
        _same(missing_evidence.get(field), True, f"missing evidence protection {field}")
    _same(evidence.get("reseed_restores_exact_baseline"), True, "missing evidence reseed")

    acl = by_id[CASE_IDS[6]]["expected"]
    _same(acl.get("viewer_scope"), "operations", "ACL viewer scope")
    _same(acl.get("visible_response_before_ref"), "#/cases/1/expected", "ACL response before")
    _same(acl.get("visible_response_after_ref"), "#/cases/1/expected", "ACL response after")
    _same(acl.get("operations_response_semantically_identical_before_after"), True, "full ACL response invariance")
    _same(acl.get("visible_route_count_before"), 2, "ACL visible route count before")
    _same(acl.get("visible_route_count_after"), 2, "ACL visible route count after")
    _same(acl.get("restricted_probe"), {
        "incident_id": "INC-204",
        "relationship_id": "REL-RESTRICTED-ESC-MATERIALS-PROBE-93",
        "relationship_type": "ESCALATES_TO",
        "from_role_id": "ROLE-MATERIALS-DUTY",
        "to_role_id": "ROLE-RESTRICTED-OPS-COORD-PROBE-93",
        "route_path": ["INC-204", "CONVEYOR-C4", "SVC-MATERIAL-TRANSFER", "DEPT-MATERIALS-LOGISTICS", "ROLE-MATERIALS-DUTY", "ROLE-RESTRICTED-OPS-COORD-PROBE-93"],
        "source_id": "SRC-RESTRICTED-OPS-ESCALATION-PROBE-93",
        "source_title": "Restricted Synthetic Operations Escalation Roster Probe 93",
        "source_text": "Restricted-only synthetic escalation assignment probe 93.",
        "observed_at": "2026-10-14T17:52:00Z",
        "acl": "restricted",
    }, "restricted-source non-disclosure tripwire")
    _same(acl.get("restricted_identifier_title_fact_count_existence_relationship_path_or_paraphrase_disclosed"), False, "full ACL nondisclosure")

    sop = by_id[CASE_IDS[7]]["expected"]
    _same(sop.get("sop_conflict"), {
        "from": "SOP-OPS-9-V2", "relationship": "SOP_CONFLICT", "to": "SOP-OPS-9-V3", "status": "unresolved",
    }, "unresolved SOP conflict")
    _same(sop.get("sop_claims"), [
        {"sop_version_id":"SOP-OPS-9-V2","relationship":"CLAIMS_ROLE","role_id":"ROLE-FACILITIES-DUTY","label":"unverified SOP claim","source_id":"SRC-SOP-CURRENT-V2","source_title":"Synthetic SOP-OPS-9 Current Text v2","observed_at":"2026-10-14T16:00:00Z"},
        {"sop_version_id":"SOP-OPS-9-V3","relationship":"CLAIMS_ROLE","role_id":"ROLE-MATERIALS-LEAD","label":"unverified SOP claim","source_id":"SRC-SOP-CURRENT-V3","source_title":"Synthetic SOP-OPS-9 Current Text v3","observed_at":"2026-10-14T16:04:00Z"},
    ], "source-backed unverified SOP role claims")
    _same(sop.get("sop_claims_are_escalation_assignments"), False, "CLAIMS_ROLE is not assignment")
    _same(sop.get("escalation_assignments_from_sop_claims"), [], "no escalation assignment from SOP claims")
    _same(sop.get("routes_after_removing_sop_conflict_or_applicability_witness_ref"), "#/cases/1/expected/routes", "SOP counterfactual baseline routes")
    _same(sop.get("sop_witness_change_alters_escalation_routes"), False, "SOP witness separation")
    _same(sop.get("offset_input"), "2026-10-14T20:30:00+02:00", "offset input")
    _same(sop.get("offset_normalized_as_of"), "2026-10-14T18:30:00Z", "offset normalized time")
    _same(sop.get("offset_result_routes_ref"), "#/cases/1/expected/routes", "offset result")
    _same(sop.get("malformed_timestamp"), "not-a-timestamp", "malformed time probe")
    _same(sop.get("malformed_timestamp_rejected"), True, "malformed time rejection")
    _same(sop.get("rejection_precedes_graph_or_action_mutation"), True, "malformed time rejected before mutation")
    _same(sop.get("graph_mutations_on_rejection"), 0, "no graph mutation on malformed time")
    _same(sop.get("action_mutations_on_rejection"), 0, "no action mutation on malformed time")

    lifecycle = by_id[CASE_IDS[8]]["expected"]
    _same(lifecycle.get("seed_runs"), 2, "repeat seed count")
    _same(lifecycle.get("duplicate_node_delta_after_second_seed"), 0, "repeat seed node delta")
    _same(lifecycle.get("duplicate_relationship_delta_after_second_seed"), 0, "repeat seed relationship delta")
    _same(lifecycle.get("fresh_falkordb_client_required"), True, "fresh FalkorDB client requirement")
    _same(lifecycle.get("reconnected_result_ref"), "#/cases/1/expected", "reconnected exact result")
    for field in ("desktop_api_result_semantics_match", "mobile_api_result_semantics_match", "api_browser_role_paths_and_sources_agree", "cleanup_deletes_only_task_owned_graph", "cleanup_stops_only_task_owned_processes"):
        _same(lifecycle.get(field), True, f"repeat/reconnect guarantee {field}")
    for field in ("read_only_route_query_graph_writes", "read_only_route_query_action_writes", "messages_sent", "calls_made", "tickets_created"):
        _same(lifecycle.get(field), 0, f"read-only side effect {field}")
    _same(lifecycle.get("external_writes"), [], "no external writes")

    for row in cases:
        _resolve_references(row.get("expected"), expected, f"case {row['id']}")
    return by_id


def _validate_matrix(expected: dict[str, Any], matrix: dict[str, Any], cases_by_id: dict[str, dict[str, Any]]) -> int:
    _same(matrix.get("schema_version"), 1, "matrix schema_version")
    _same(matrix.get("oracle_file"), "tests/escalation-expected-results.json", "matrix oracle path")
    _same(matrix.get("required_group_count"), 9, "matrix required group count")
    groups = matrix.get("groups")
    _need(isinstance(groups, list), "matrix.groups must be an array")
    group_ids = [group.get("group_id") for group in groups if isinstance(group, dict)]
    _same(group_ids, list(CASE_IDS), "matrix group order/coverage")
    _need(len(groups) == 9, "matrix must contain exactly nine groups")

    total_assertions = 0
    seen_assertion_ids: set[str] = set()
    for index, group in enumerate(groups):
        group_id = group["group_id"]
        _same(group.get("planner_ref"), f"planner-51d0c80bef174057bc10db9a64ec3d74.md:{PACKET_OUTCOME_LINES[index]}", f"{group_id} packet line")
        for field in ("attack_or_edge_case", "setup", "failure_detected"):
            _need(isinstance(group.get(field), str) and group[field].strip(), f"{group_id} missing adversarial {field}")
        assertions = group.get("assertions")
        _need(isinstance(assertions, list) and assertions, f"{group_id} has zero mapped assertions")
        assertion_ids: list[str] = []
        pointers: list[str] = []
        case_doc = cases_by_id[group_id]
        for assertion in assertions:
            _need(isinstance(assertion, dict), f"{group_id} assertion is not an object")
            assertion_id = assertion.get("id")
            pointer = assertion.get("pointer")
            _need(isinstance(assertion_id, str) and assertion_id, f"{group_id} assertion has invalid ID")
            _need(assertion_id not in seen_assertion_ids, f"duplicate matrix assertion ID {assertion_id}")
            seen_assertion_ids.add(assertion_id)
            assertion_ids.append(assertion_id)
            _need(isinstance(pointer, str) and pointer.startswith("/expected"), f"{assertion_id} does not map to this group's expected result")
            pointers.append(pointer)
            actual = _json_pointer(case_doc, pointer, f"matrix assertion {assertion_id}")
            if "equals" in assertion:
                _same(actual, assertion["equals"], f"matrix assertion {assertion_id}")
            elif assertion.get("equals_case_value") is True and type(assertion.get("equals_case_value")) is bool:
                pass  # The whole fixed expected document is independently literal-pinned above.
            else:
                _fail(f"matrix assertion {assertion_id} has no fixed expected-value mapping")
        _need(len(assertion_ids) == len(set(assertion_ids)), f"{group_id} has duplicate assertion IDs")
        required = REQUIRED_ASSERTION_POINTERS[group_id]
        _same(set(pointers), required | {"/expected"}, f"{group_id} required assertion pointer coverage")
        count = group.get("nonzero_assertion_count")
        _need(type(count) is int and count > 0, f"{group_id} nonzero assertion count must be a positive integer")
        _same(count, len(assertions), f"{group_id} assertion count")
        full_pointer = f"/expected"
        _need(any(a.get("pointer") == full_pointer and a.get("equals_case_value") is True for a in assertions), f"{group_id} must map its complete fixed expected object")
        total_assertions += count
    _need(total_assertions > 0, "matrix has zero total assertions")
    return total_assertions


def validate_documents(
    expected: dict[str, Any],
    matrix: dict[str, Any],
    *,
    pinned_plan_path: Path,
    repository_root: Path,
) -> dict[str, int]:
    """Validate in-memory documents without importing or invoking candidate code."""
    _same(_canonical_sha256(expected), EXPECTED_CANONICAL_SHA256, "canonical expected-results document hash")
    _same(_canonical_sha256(matrix), MATRIX_CANONICAL_SHA256, "canonical adversarial-matrix document hash")
    _same(expected.get("schema_version"), 1, "expected schema_version")
    _same(expected.get("product"), "WardAtlas", "expected product")
    _need("candidate" in expected.get("oracle_policy", "").lower(), "oracle policy must rule out candidate-derived expectations")
    _same(expected.get("pinned_sources"), [{
        "id": "escalation-decision-packet", "path": PACKET_PATH, "sha256": PACKET_SHA256,
        "outcome_lines": list(PACKET_OUTCOME_LINES),
    }], "pinned packet metadata")
    _same(expected.get("scope"), {
        "synthetic_only": True,
        "clinical_decisions": False,
        "real_staff_or_contacts": False,
        "real_schedule_claims": False,
        "database_requirement": "A future behavior verifier must exercise actual FalkorDB; this artifact oracle does not claim live database execution.",
        "external_writes": False,
        "candidate_output_is_oracle": False,
        "interpretation": "Role routing is a synthetic review reference, not a contact instruction, operational directive, or claim about a real hospital.",
    }, "synthetic-only scope/safety boundary")
    _same(str(pinned_plan_path), PACKET_PATH, "pinned packet argument")
    packet_digest = _validate_packet(pinned_plan_path)
    _validate_fixture_literals(expected)
    frozen = _validate_frozen_files(expected, repository_root)
    cases_by_id = _validate_fixed_outcomes(expected)
    assertion_count = _validate_matrix(expected, matrix, cases_by_id)
    return {
        "case_count": len(CASE_IDS),
        "assertion_count": assertion_count,
        "frozen_oracle_and_matrix_count": len(frozen),
        "packet_sha256_verified": int(packet_digest == PACKET_SHA256),
    }


def validate_review_receipt(receipt: dict[str, Any], expected: dict[str, Any], matrix: dict[str, Any]) -> dict[str, int]:
    """Check a future independent-review receipt's exact group and hash bindings.

    Receipt JSON schema: ``schema_version=1``, ``receipt_type`` equal to the
    value below, nonempty ``reviewer_id`` and ``review_method``,
    ``candidate_behavior_consulted=false``, exact ``artifact_hashes`` and
    ``frozen_preexisting_oracle_hashes`` objects, and nine ordered ``groups``.
    Each group has ``group_id``, canonical ``expected_sha256``, the exact ordered
    ``matrix_assertion_ids``, and ``result="pass"``. This verifies receipt
    bindings and its recorded claims; it cannot authenticate reviewer identity
    or make a fresh session of the same model an independent oracle.
    """
    _same(_canonical_sha256(expected), EXPECTED_CANONICAL_SHA256, "review receipt expected-results binding")
    _same(_canonical_sha256(matrix), MATRIX_CANONICAL_SHA256, "review receipt adversarial-matrix binding")
    _same(receipt.get("schema_version"), 1, "review receipt schema_version")
    _same(receipt.get("receipt_type"), "wardatlas-escalation-independent-review", "review receipt type")
    for field in ("reviewer_id", "review_method"):
        _need(isinstance(receipt.get(field), str) and receipt[field].strip(), f"review receipt {field} is required")
    _same(receipt.get("candidate_behavior_consulted"), False, "review receipt candidate-output boundary")
    _same(receipt.get("artifact_hashes"), {
        "tests/escalation-expected-results.json": EXPECTED_ARTIFACT_SHA256,
        "tests/escalation-adversarial-matrix.json": MATRIX_ARTIFACT_SHA256,
        "planner_packet": PACKET_SHA256,
    }, "review receipt artifact hashes")
    _same(receipt.get("frozen_preexisting_oracle_hashes"), PINNED_ORACLE_HASHES, "review receipt frozen source-oracle hashes")
    rows = receipt.get("groups")
    _need(isinstance(rows, list), "review receipt groups must be an array")
    _same([row.get("group_id") for row in rows if isinstance(row, dict)], list(CASE_IDS), "review receipt nine group IDs/order")
    matrix_by_id = {group["group_id"]: group for group in matrix["groups"]}
    expected_by_id = {case["id"]: case for case in expected["cases"]}
    for row in rows:
        group_id = row["group_id"]
        _same(row.get("expected_sha256"), _canonical_sha256(expected_by_id[group_id]), f"review receipt group hash {group_id}")
        _same(row.get("matrix_assertion_ids"), [assertion["id"] for assertion in matrix_by_id[group_id]["assertions"]], f"review receipt assertion mapping {group_id}")
        _same(row.get("result"), "pass", f"review receipt outcome {group_id}")
    return {"reviewed_group_count": len(rows), "receipt_oracle_hash_count": len(PINNED_ORACLE_HASHES)}


def validate_files(
    expected_path: Path,
    matrix_path: Path,
    pinned_plan_path: Path,
    repository_root: Path,
    review_receipt_path: Path | None = None,
) -> dict[str, int]:
    expected, expected_raw_hash = _read_json(expected_path, "escalation expected-results")
    matrix, matrix_raw_hash = _read_json(matrix_path, "escalation adversarial-matrix")
    _same(expected_raw_hash, EXPECTED_ARTIFACT_SHA256, "expected-results byte hash")
    _same(matrix_raw_hash, MATRIX_ARTIFACT_SHA256, "adversarial-matrix byte hash")
    result = validate_documents(expected, matrix, pinned_plan_path=pinned_plan_path, repository_root=repository_root)
    if review_receipt_path is not None:
        receipt, _ = _read_json(review_receipt_path, "independent review receipt")
        result.update(validate_review_receipt(receipt, expected, matrix))
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expected", type=Path, required=True, help="candidate-independent escalation expected-results JSON")
    parser.add_argument("--matrix", type=Path, required=True, help="escalation adversarial matrix JSON")
    parser.add_argument("--pinned-plan", type=Path, required=True, help="immutable escalation decision packet")
    parser.add_argument("--require-nine-groups", action="store_true", required=True, help="require the complete nine-group contract")
    parser.add_argument("--check-frozen-oracle-hashes", action="store_true", required=True, help="require all frozen pre-existing hashes")
    parser.add_argument("--review-receipt", type=Path, help="optionally validate a future independent-review receipt")
    parser.add_argument("--repo-root", type=Path, default=Path(__file__).resolve().parents[1], help="repository containing pre-existing oracle JSON files")
    args = parser.parse_args(argv)
    try:
        result = validate_files(
            args.expected.resolve(), args.matrix.resolve(), args.pinned_plan.resolve(),
            args.repo_root.resolve(), args.review_receipt.resolve() if args.review_receipt else None,
        )
    except (ContractError, OSError, TypeError, KeyError, ValueError) as exc:
        print(f"escalation contract: FAIL: {exc}", file=sys.stderr)
        return 1
    print(
        "escalation contract: PASS "
        f"({result['case_count']} fixed groups, {result['assertion_count']} nonzero mapped assertions, "
        f"{result['frozen_oracle_and_matrix_count']} unchanged pre-existing contract hashes; no candidate behavior consulted)"
    )
    if "reviewed_group_count" in result:
        print(f"independent review receipt: bindings verified for {result['reviewed_group_count']} groups and {result['receipt_oracle_hash_count']} frozen hashes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

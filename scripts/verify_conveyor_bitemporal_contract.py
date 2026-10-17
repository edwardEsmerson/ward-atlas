#!/usr/bin/env python3
"""Validate the pinned INC-204 valid-time / knowledge-time contract.

This is an oracle-artifact check, not a product behavior test: it imports no
WardAtlas code and never connects to FalkorDB. The literal expectations are
cross-checked against the immutable decision packet and accepted synthetic
source oracles; the candidate's output is never consulted.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


EXPECTED_ARTIFACT_SHA256 = "8ffa7ee25a52219808656ead871fac4779625300f9eea0eaa9543e8b0c7740e6"
MATRIX_ARTIFACT_SHA256 = "e036570a3db09c8b544cb339b2665beeee3d469416c4b5fe69e5be2b3597eb67"
PACKET_SHA256 = "34a40eb28fe5ea9283d65708533183f67f657bd107ec1139cb82f073e88b5a85"
PACKET_FILENAME = "planner-4b8f1393a48d40da83d21180edd4667f.md"
PACKET_RELATIVE_PATH = (
    "planner-4b8f1393a48d40da83d21180edd4667f/plans/"
    "planner-4b8f1393a48d40da83d21180edd4667f.md"
)
PACKET_OUTCOME_LINES = tuple(range(15, 27))
PACKET_OUTCOME_PREFIXES = (
    "1. **Cold empty:**",
    "2. **Legacy compatibility:**",
    "3. **Outage known before restoration:**",
    "4. **Restoration becomes known:**",
    "5. **Per-path evidence cutoff:**",
    "6. **No known event:**",
    "7. **Outage-link counterfactual:**",
    "8. **Restoration-evidence counterfactual:**",
    "9. **Observed/branch separation:**",
    "10. **Restricted-source invariance:**",
    "11. **Time validation:**",
    "12. **Repeat, reconnect, read-only, responsive UI, cleanup:**",
)

# Additive artifacts accepted after the original freeze are enumerated as
# complete oracle/matrix pairs, independently of the immutable 24-file
# pre-existing hash manifest below. Keeping this separate means that composing
# a reviewed contract does not rewrite what was frozen by the original oracle.
PINNED_ADDITIVE_ORACLE_MATRIX_PAIRS = (
    {
        "oracle": "tests/service-coverage-expected-results.json",
        "oracle_sha256": "2f5da05f710c3fc5514ceb4d4a89415e40db1b340b91160b8062db0404b02bd4",
        "matrix": "tests/service-coverage-adversarial-matrix.json",
        "matrix_sha256": "4e5b5d58218a280876654a53434f11acc89c30c3ed25bc44862c244bdfb17c81",
    },
    {
        "oracle": "tests/handover-known-time-expected-results.json",
        "oracle_sha256": "fd72df03c8e865cfe347839074ee8251bf57216deac33fcc9d5a4d1152513f93",
        "matrix": "tests/handover-known-time-adversarial-matrix.json",
        "matrix_sha256": "7b8450947696e55af0dc48aaa6d27fd0231f458b0f527b3fc00f643ad1f64f67",
    },
    {
        "oracle": "tests/cross-case-handover-expected-results.json",
        "oracle_sha256": "931b39dcac5e4315d773b84b6100865ad4a172fc64faecc6c758cb0b8598d22a",
        "matrix": "tests/cross-case-handover-adversarial-matrix.json",
        "matrix_sha256": "24b27185c7586694b97b5e86145b60638b2a1ddf327d1ce497cd229c42e83715",
    },
    {
        "oracle": "tests/decision-provenance-expected-results.json",
        "oracle_sha256": "4143bbc135b32ed6fb89444f2c32b01bfd5ca89d50127cea5e4099fdffed5c5d",
        "matrix": "tests/decision-provenance-adversarial-matrix.json",
        "matrix_sha256": "256048400ee304f59884ecf3393f9a59e30d76f29281c9c6ea5378740dd71ad3",
    },
)

# Exact bytes of every tests/*expected-results.json and
# tests/*adversarial-matrix.json present before this task added its two files.
PINNED_EXISTING = {
    "tests/adversarial-matrix.json": "6d0edbc69620da85b494185879565c8260c9d2d3fe6afdd5bf56c5acd92205ba",
    "tests/cascade-adversarial-matrix.json": "c74431b0c2bebbdc25419bf58d39129d150779e1ef7418b64c80dee6979398db",
    "tests/cascade-expected-results.json": "52fd4abe069e850e4ee070b8e6d0f9ed26fff5ad1e5e0c47efb28ad78227581b",
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

SOURCE_ORACLES = (
    ("expected-results", "tests/expected-results.json", "c77e00e47c4f1e26d565c3d9583653838d5cc88695c4e6aa19ac583205d92b87"),
    ("shift-board-expected-results", "tests/shift-board-expected-results.json", "5d53a8f93245ee8391ec7cffb6d45b58355536d30d649db19cd53ca6b1ce2428"),
    ("sop-impact-expected-results", "tests/sop-impact-expected-results.json", "f94e65850384e36e7200b321d14628f074fee37fb159adbcd73bef00c6e1acca"),
    ("dock-bitemporal-expected-results", "tests/dock-bitemporal-expected-results.json", "243c92775d7c1366962dcd7c5c9acd9ea753f2924af84c2c5dc99b0b5c872205"),
    ("handover-brief-expected-results", "tests/handover-brief-expected-results.json", "932ada7d43688b8bd1e4f147ac06dd585982d784ef8577f05cc00b07a97199f1"),
)

REQUIRED_CASES = (
    "conveyor-cold-empty-unknown",
    "conveyor-legacy-compatibility",
    "outage-known-before-restoration",
    "restoration-known-at-same-valid-time",
    "per-path-evidence-cutoff",
    "no-known-event-unknown-no-leak",
    "outage-event-edge-removal",
    "restoration-evidence-edge-removal",
    "observed-versus-hypothetical-branch",
    "restricted-source-invariance",
    "time-validation-and-offset-normalization",
    "repeat-reconnect-read-only-and-cleanup",
)

# Mappings are intentionally exact: adding a group without assertions or
# replacing a semantic assertion with a pointer to irrelevant metadata fails.
REQUIRED_POINTER_FIELDS = {
    REQUIRED_CASES[0]: {"state_status", "selected_event", "selected_event_source", "impact_paths", "path_evidence", "availability_conclusion", "risk_absence_conclusion", "event_or_source_existence_hint_emitted"},
    REQUIRED_CASES[1]: {"known_at", "knowledge_semantics", "at_1830", "at_1930", "legacy_query_shape_unchanged"},
    REQUIRED_CASES[2]: {"conveyor_at", "conveyor_known_at", "state_status", "selected_event", "impact_paths", "path_evidence", "independent_sop_witness"},
    REQUIRED_CASES[3]: {"conveyor_at", "conveyor_known_at", "state_status", "selected_event", "active_paths", "independent_sop_witness"},
    REQUIRED_CASES[4]: {"conveyor_at", "conveyor_known_at", "selected_event", "impact_paths", "path_evidence", "path_ownership_evidence_uses_relationship_observed_at", "excluded_later_ownership", "returned_path_provenance_times", "later_role_path_emitted", "later_ownership_evidence_emitted", "missing_path_count_emitted", "completeness_claim_emitted"},
    REQUIRED_CASES[5]: {"conveyor_at", "conveyor_known_at", "state_status", "selected_event", "selected_event_source", "impact_paths", "forbidden_disclosure_categories", "availability_conclusion", "no_risk_conclusion", "unseen_event_or_source_hint_emitted"},
    REQUIRED_CASES[6]: {"remove_only_edge", "after_removal", "reseed_restores_exact_baseline"},
    REQUIRED_CASES[7]: {"remove_only_edge", "after_removal", "reseed_restores_exact_baseline"},
    REQUIRED_CASES[8]: {"branch_at", "conveyor_known_at", "branch", "observed_mainline", "legacy_current_knowledge_mainline_paths", "branch_changes_observed_history", "hypothetical_event_in_mainline"},
    REQUIRED_CASES[9]: {"viewer_scope", "axis_pairs", "full_selected_event_response_and_accessible_event_panel_invariant_before_after", "hidden_id_title_fact_count_existence_relationship_path_or_paraphrase_disclosed"},
    REQUIRED_CASES[10]: {"canonical_pair", "offset_pair", "normalized_pair", "offset_result_matches_case", "malformed_conveyor_at", "timezone_naive_conveyor_known_at", "both_invalid_axes_rejected_before_graph_or_output_mutation", "local_time_fallback"},
    REQUIRED_CASES[11]: {"repeat_seed_duplicate_nodes", "repeat_seed_duplicate_relationships", "after_reconnect_result_matches", "query_graph_writes", "query_action_writes", "messages_sent", "calls_made", "tickets_created", "external_writes", "desktop_mobile_api_results_agree", "cleanup_deletes_only_task_owned_graph_and_stops_only_owned_processes"},
}


class ContractFailure(ValueError):
    """Raised when a packet outcome, source binding, or assertion map drifts."""


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
    require(all(isinstance(value, str) and value for value in ids), f"{label} IDs must be nonempty strings")
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


def _expected_source_manifest() -> list[dict[str, Any]]:
    packet = {
        "id": "conveyor-bitemporal-packet",
        "file": PACKET_RELATIVE_PATH,
        "sha256": PACKET_SHA256,
        "outcome_lines": list(PACKET_OUTCOME_LINES),
    }
    return [packet] + [
        {"id": source_id, "file": path, "sha256": digest}
        for source_id, path, digest in SOURCE_ORACLES
    ]


def _expected_frozen_manifest() -> list[dict[str, str]]:
    return [{"file": path, "sha256": digest} for path, digest in sorted(PINNED_EXISTING.items())]


def _pinned_additive_files() -> dict[str, str]:
    """Flatten the explicitly pinned additive pairs without changing the freeze."""
    files: dict[str, str] = {}
    for pair in PINNED_ADDITIVE_ORACLE_MATRIX_PAIRS:
        require(
            set(pair) == {"oracle", "oracle_sha256", "matrix", "matrix_sha256"},
            "additive inventory entries must be complete oracle/matrix pairs",
        )
        oracle, matrix = pair["oracle"], pair["matrix"]
        require(oracle.endswith("-expected-results.json") and matrix.endswith("-adversarial-matrix.json"), "additive inventory entry has invalid pair filenames")
        for path, digest in ((oracle, pair["oracle_sha256"]), (matrix, pair["matrix_sha256"])):
            require(path not in files and path not in PINNED_EXISTING, f"duplicate additive inventory path: {path}")
            require(isinstance(digest, str) and len(digest) == 64, f"invalid pinned SHA-256 for additive artifact {path}")
            files[path] = digest
    return files


def _discover_oracle_matrix_files(root: Path) -> set[str]:
    """Enumerate matching test files without following untrusted directory links.

    ``Path.rglob`` does not descend into symlinked directories. Rejecting those
    directories (and walking ordinary directories with ``followlinks=False``)
    prevents an unlisted oracle from being hidden behind an alias or cycle.
    """
    tests_root = root / "tests"
    require(not tests_root.is_symlink(), "test oracle/matrix inventory rejects a symlinked tests directory")
    discovered: set[str] = set()

    def fail_walk(error: OSError) -> None:
        raise ContractFailure(f"cannot enumerate test oracle/matrix inventory: {error}") from error

    for current, directories, filenames in os.walk(tests_root, topdown=True, onerror=fail_walk, followlinks=False):
        current_path = Path(current)
        for name in directories:
            linked_directory = current_path / name
            if linked_directory.is_symlink():
                relative = linked_directory.relative_to(root)
                raise ContractFailure(f"test oracle/matrix inventory rejects symlink directory: {relative}")
        for name in filenames:
            if name.endswith(("expected-results.json", "adversarial-matrix.json")):
                discovered.add(str((current_path / name).relative_to(root)))
    return discovered


def validate_structure(expected: dict[str, Any], matrix: dict[str, Any], require_twelve_groups: bool) -> int:
    """Verify twelve exact group names, packet mappings, and useful assertions."""
    require(expected.get("schema_version") == 1 and matrix.get("schema_version") == 1, "schema_version must be 1")
    require(expected.get("product") == "WardAtlas", "unexpected product")
    require("candidate behavior is never" in expected.get("oracle_policy", "").lower(), "candidate output cannot be the oracle")
    scope = expected.get("scope")
    require(isinstance(scope, dict) and scope.get("synthetic_only") is True, "oracle must remain synthetic-only")
    require(scope.get("clinical_decisions") is False and scope.get("external_writes") is False, "clinical and external-write boundaries changed")
    require(scope.get("candidate_output_is_oracle") is False, "candidate output must not supply expectations")
    require("actual falkordb" in str(scope.get("database_requirement", "")).lower(), "actual FalkorDB must remain required")
    require(scope.get("viewer_scope") == "operations", "viewer scope changed")
    require(matrix.get("oracle_file") == "tests/conveyor-bitemporal-expected-results.json", "matrix points to another oracle")
    require(expected.get("pinned_sources") == _expected_source_manifest(), "packet or accepted source-oracle pins changed")
    require(expected.get("frozen_preexisting_oracle_hashes") == _expected_frozen_manifest(), "pre-existing oracle hash manifest changed")

    cases = expected.get("cases")
    matrix_cases = matrix.get("cases")
    require(isinstance(cases, list) and isinstance(matrix_cases, list), "expected and matrix cases must be arrays")
    case_ids = _unique_ids(cases, "expected case")
    matrix_ids = _unique_ids(matrix_cases, "matrix case")
    if require_twelve_groups:
        require(len(cases) == 12 and len(matrix_cases) == 12, "--require-twelve-groups requires exactly twelve groups")
    require(tuple(case_ids) == REQUIRED_CASES, "the twelve fixed outcome groups or their order changed")
    require(matrix_ids == case_ids, "matrix cases must map one-to-one in the same order as outcomes")

    total = 0
    for index, (scenario, row) in enumerate(zip(cases, matrix_cases), start=1):
        case_id = REQUIRED_CASES[index - 1]
        require(scenario.get("scenario_id") == case_id and row.get("scenario_id") == case_id, f"{case_id} scenario mapping changed")
        oracle_refs = scenario.get("oracle_refs")
        require(isinstance(oracle_refs, list) and oracle_refs and oracle_refs[0] == f"conveyor-bitemporal-packet#outcome-{index}", f"{case_id} expected packet outcome mapping changed")
        require(row.get("planner_refs") == [f"{PACKET_FILENAME}:{PACKET_OUTCOME_LINES[index - 1]}"], f"{case_id} matrix packet line changed")
        require(isinstance(scenario.get("expected"), dict) and scenario["expected"], f"{case_id} has no fixed expected object")
        require(isinstance(row.get("attack_or_edge_case"), str) and row["attack_or_edge_case"], f"{case_id} lacks an adversarial description")
        require(isinstance(row.get("setup"), str) and row["setup"], f"{case_id} lacks an adversarial setup")
        require(isinstance(row.get("failure_detected"), str) and row["failure_detected"], f"{case_id} lacks failure semantics")
        assertions = row.get("pinned_assertions")
        require(isinstance(assertions, list) and assertions, f"{case_id} has zero mapped assertions")
        assertion_ids = [item.get("id") for item in assertions if isinstance(item, dict)]
        pointers = [item.get("expected_pointer") for item in assertions if isinstance(item, dict)]
        require(len(assertion_ids) == len(assertions) and all(isinstance(value, str) and value for value in assertion_ids), f"{case_id} has malformed assertion IDs")
        require(len(assertion_ids) == len(set(assertion_ids)), f"{case_id} assertion IDs must be unique")
        require(all(isinstance(pointer, str) and pointer for pointer in pointers), f"{case_id} contains an unmapped assertion")
        required_pointers = {f"/cases/{index - 1}/expected/{field}" for field in REQUIRED_POINTER_FIELDS[case_id]}
        require(set(pointers) == required_pointers, f"{case_id} does not map every required fixed outcome field")
        for pointer in pointers:
            _resolve_pointer(expected, pointer)
        count = row.get("nonzero_assertion_count")
        require(type(count) is int and count > 0, f"{case_id} assertion count must be nonzero")
        require(count == len(assertions), f"{case_id} assertion count differs from its explicit pointer mappings")
        total += count
    require(total > 0, "matrix must contain nonzero assertions")
    return total


def _case_map(expected: dict[str, Any]) -> dict[str, dict[str, Any]]:
    rows = expected.get("cases")
    require(isinstance(rows, list), "expected cases must be an array")
    return {row["id"]: row["expected"] for row in rows if isinstance(row, dict) and isinstance(row.get("id"), str) and isinstance(row.get("expected"), dict)}


def _source_cases(document: dict[str, Any]) -> dict[str, dict[str, Any]]:
    rows = document.get("cases", [])
    require(isinstance(rows, list), "accepted oracle cases must be an array")
    return {row["id"]: row["expected"] for row in rows if isinstance(row, dict) and isinstance(row.get("id"), str) and isinstance(row.get("expected"), dict)}


def _normalize_utc(value: str) -> str:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError) as exc:
        raise ContractFailure(f"invalid pinned offset timestamp {value!r}") from exc
    require(parsed.tzinfo is not None and parsed.utcoffset() is not None, "pinned offset must include explicit timezone")
    return parsed.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def validate_fixed_outcomes(expected: dict[str, Any], root: Path) -> None:
    """Cross-check every key result against fixed packet/source-oracle literals."""
    cases = _case_map(expected)
    require(tuple(cases) == REQUIRED_CASES, "fixed outcome groups or order changed")
    try:
        original, _ = _load_json(root / "tests/expected-results.json")
        shift_doc, _ = _load_json(root / "tests/shift-board-expected-results.json")
        sop_doc, _ = _load_json(root / "tests/sop-impact-expected-results.json")
        dock_doc, _ = _load_json(root / "tests/dock-bitemporal-expected-results.json")
        handover_doc, _ = _load_json(root / "tests/handover-brief-expected-results.json")
    except ContractFailure:
        raise

    shift = _source_cases(shift_doc)["conveyor-observed-and-hypothetical-times"]
    sop = _source_cases(sop_doc)
    handover = _source_cases(handover_doc)
    dock = _source_cases(dock_doc)
    original_paths = original["expected_paths"]
    original_evidence = original["expected_evidence_by_path"]
    outage = shift["observed_at_1830"]
    restoration = shift["observed_restoration_1930"]
    branch = shift["hypothetical_branch_1850"]
    sop_conflict = sop["sop-impact-conflict-provenance"]
    handover_witness = handover_doc["canonical_snapshot"]["sop_witness"]
    restricted = _source_cases(shift_doc)["denied-source-nondisclosure"]["conveyor_restricted_fixture"]

    fixture = expected.get("fixture", {})
    require(fixture.get("incident_id") == "INC-204" and fixture.get("equipment_id") == "CONVEYOR-C4" and fixture.get("service_id") == "SVC-MATERIAL-TRANSFER", "INC-204 fixture identity changed")
    require(fixture.get("canonical_paths") == original_paths == outage["paths"] == handover_doc["canonical_snapshot"]["impact"]["paths"], "canonical path values differ from accepted original/shift-board/handover outcomes")
    require(fixture.get("canonical_path_evidence") == original_evidence == outage["canonical_path_evidence"], "canonical per-hop evidence differs from accepted original/shift-board outcomes")
    require(fixture.get("outage_event_id") == outage["event_id"] == "EVT-C4-UNAVAILABLE-01", "outage event ID differs from the accepted shift-board outcome")
    require(fixture.get("outage_source_id") == outage["source_id"] == "SRC-ASSET-C4-OUTAGE", "outage source ID differs from the accepted shift-board outcome")
    require(fixture.get("outage_event_at") == outage["event_at"] == "2026-10-14T18:02:00Z", "outage valid time differs from the accepted shift-board outcome")
    require(fixture.get("restoration_event_id") == restoration["restoration_event_id"] == "EVT-C4-RESTORED-01", "restoration event ID differs from accepted source outcome")
    require(fixture.get("restoration_source_id") == restoration["restoration_source_id"] == "SRC-ASSET-C4-RESTORE", "restoration source ID differs from accepted source outcome")
    require(fixture.get("restoration_event_at") == restoration["restoration_event_at"] == "2026-10-14T19:15:00Z", "restoration valid time differs from accepted source outcome")
    require((fixture.get("branch_id"), fixture.get("branch_event_id"), fixture.get("branch_event_at")) == (branch["branch_id"], branch["hypothetical_event_id"], branch["hypothetical_event_at"]), "branch fixture differs from accepted shift-board outcome")

    cold = cases[REQUIRED_CASES[0]]
    cold_expected = dock["dock-bitemporal-cold-empty"]
    require(cold.get("state_status") == "unknown" and cold.get("selected_event") is None and cold.get("selected_event_source") is None, "cold empty must be unknown without event/source")
    require(cold.get("impact_paths") == [] and cold.get("path_evidence") == [], "cold empty must not invent paths or evidence")
    require(cold.get("availability_conclusion") is False and cold.get("risk_absence_conclusion") is False and cold.get("event_or_source_existence_hint_emitted") is False, "cold empty must not infer availability, safety, or hidden existence")
    require(cold_expected["board_state"] == "empty/unknown" and cold_expected["risk_or_currentness_conclusion"] is False, "accepted dock cold-empty unknown boundary changed")

    legacy = cases[REQUIRED_CASES[1]]
    require(legacy.get("known_at") == "omitted" and legacy.get("knowledge_semantics") == "current knowledge (legacy single-axis call)", "legacy omission/current-knowledge semantics changed")
    require(legacy.get("at_1830") == {
        "as_of": outage["at"], "status": outage["status"], "event_id": outage["event_id"], "source_id": outage["source_id"],
        "event_at": outage["event_at"], "impact_paths": outage["paths"], "path_evidence": outage["canonical_path_evidence"]}, "legacy 18:30 output differs from accepted shift-board outcome")
    require(legacy.get("at_1930") == {
        "as_of": restoration["at"], "status": restoration["status"], "restoration_event_id": restoration["restoration_event_id"],
        "restoration_source_id": restoration["restoration_source_id"], "restoration_event_at": restoration["restoration_event_at"],
        "active_paths": restoration["active_paths"], "historical_at": restoration["historical_at"], "historical_paths": restoration["historical_paths"],
        "historical_evidence_matches_canonical": restoration["historical_evidence_matches_canonical"]}, "legacy 19:30 output differs from accepted shift-board outcome")
    require(legacy.get("legacy_query_shape_unchanged") is True, "legacy response shape must remain compatible")

    expected_sop_separation = {"conflict_status": sop_conflict["conflict_status"], "claims_are_current_owner_facts": sop_conflict["claims_are_current_owner_facts"], "winner_selected": sop_conflict["winner_selected"]}
    require(expected_sop_separation == {"conflict_status": "unresolved", "claims_are_current_owner_facts": False, "winner_selected": False}, "accepted SOP conflict semantics changed")
    require(handover_witness["conflict_status"] == "unresolved" and handover_witness["claims_are_current_owner_facts"] is False and handover_witness["winner_selected"] is False, "accepted handover SOP witness changed")

    outage_case = cases[REQUIRED_CASES[2]]
    require((outage_case.get("conveyor_at"), outage_case.get("conveyor_known_at")) == ("2026-10-14T19:30:00Z", "2026-10-14T19:14:00Z"), "19:30/19:14 time pair changed or missing")
    require(outage_case.get("state_status") == outage["status"], "outage state/status differs from accepted event outcome")
    require(outage_case.get("selected_event") == {"event_id": outage["event_id"], "source_id": outage["source_id"], "event_at": outage["event_at"]}, "19:14 outage event/source differs from accepted outcome")
    require(outage_case.get("impact_paths") == original_paths and outage_case.get("path_evidence") == original_evidence, "19:14 paths or provenance differ from accepted original result")
    require(outage_case.get("independent_sop_witness") == expected_sop_separation, "unresolved SOP witness was conflated with event replay")

    restored_case = cases[REQUIRED_CASES[3]]
    require((restored_case.get("conveyor_at"), restored_case.get("conveyor_known_at")) == ("2026-10-14T19:30:00Z", "2026-10-14T19:16:00Z"), "19:30/19:16 time pair changed or missing")
    require(restored_case.get("state_status") == restoration["status"] and restored_case.get("selected_event") == {
        "event_id": restoration["restoration_event_id"], "source_id": restoration["restoration_source_id"], "event_at": restoration["restoration_event_at"]}, "19:16 restoration event/source/status differs from accepted outcome")
    require(restored_case.get("active_paths") == restoration["active_paths"] == [], "known restoration must have no active outage paths")
    require(restored_case.get("independent_sop_witness") == expected_sop_separation, "restoration must not resolve or select the SOP conflict")

    partial = cases[REQUIRED_CASES[4]]
    require((partial.get("conveyor_at"), partial.get("conveyor_known_at")) == ("2026-10-14T19:30:00Z", "2026-10-14T18:04:00Z"), "18:04 path-cutoff time pair changed or missing")
    require(partial.get("selected_event") == {"event_id": outage["event_id"], "source_id": outage["source_id"], "event_at": outage["event_at"]}, "outage event/source must be known at 18:04")
    require(partial.get("impact_paths") == [original_paths[0]] and partial.get("path_evidence") == [original_evidence[0]], "18:04 cutoff must keep exactly the first role path and provenance")
    require(partial.get("path_ownership_evidence_uses_relationship_observed_at") is True, "role knowledge must use OWNED_BY.observed_at, not a roster node timestamp")
    require(partial.get("excluded_later_ownership") == {"relationship": "OWNED_BY", "role_id": "ROLE-MATERIALS-DUTY", "observed_at": "2026-10-14T18:05:00Z"}, "second ownership edge's 18:05Z cutoff changed")
    require(partial.get("returned_path_provenance_times") == original_evidence[0]["observed_at"] == ["2026-10-14T18:00:00Z", "2026-10-14T18:02:00Z", "2026-10-14T18:03:00Z", "2026-10-14T18:04:00Z"], "18:04 path source cutoff/timestamps changed")
    require(original_evidence[1]["observed_at"][-1] == "2026-10-14T18:05:00Z", "accepted second OWNED_BY evidence cutoff changed")
    for field in ("later_role_path_emitted", "later_ownership_evidence_emitted", "missing_path_count_emitted", "completeness_claim_emitted"):
        require(partial.get(field) is False, f"18:04 cutoff must not expose or imply {field}")

    unknown = cases[REQUIRED_CASES[5]]
    require((unknown.get("conveyor_at"), unknown.get("conveyor_known_at")) == ("2026-10-14T19:30:00Z", "2026-10-14T18:01:00Z"), "18:01 unknown time pair changed or missing")
    require(unknown.get("state_status") == "unknown" and unknown.get("selected_event") is None and unknown.get("selected_event_source") is None, "18:01 must return unknown without selected event/source")
    require(unknown.get("impact_paths") == [] and unknown.get("path_evidence") == [], "18:01 unknown result must not invent impact paths")
    require(unknown.get("availability_conclusion") is False and unknown.get("no_risk_conclusion") is False and unknown.get("unseen_event_or_source_hint_emitted") is False, "unknown must not imply availability/no-risk or leak existence")
    require(unknown.get("forbidden_disclosure_categories") == ["outage/restoration event identifiers", "event-source identifiers or titles", "event/source timestamps", "hidden event/source counts", "event/source existence hints"], "unknown/no-leak categories changed")

    edge_loss = cases[REQUIRED_CASES[6]]
    require(edge_loss.get("baseline", {}).get("selected_event") == outage_case["selected_event"] and edge_loss.get("baseline", {}).get("impact_paths") == outage_case["impact_paths"], "outage-edge counterfactual baseline differs from pinned outage")
    require(edge_loss.get("remove_only_edge") == {"from": "INC-204", "relationship": "HAS_STATE_EVENT", "to": outage["event_id"], "ownership": "test-owned edge only"}, "outage counterfactual must remove only the required incident/event edge")
    require(edge_loss.get("after_removal") == {"state_status": "unknown", "selected_event": None, "selected_event_source": None, "impact_paths": [], "reconstructed_from_static_or_text": False}, "removing the outage event edge must make only its dependent evidence unknown")
    require(edge_loss.get("reseed_restores_exact_baseline") is True, "outage-edge reseed must restore baseline")

    restoration_loss = cases[REQUIRED_CASES[7]]
    require(restoration_loss.get("baseline", {}).get("selected_event") == restored_case["selected_event"] and restoration_loss.get("baseline", {}).get("active_paths") == [], "restoration-edge counterfactual baseline differs from accepted restoration")
    require(restoration_loss.get("remove_only_edge") == {"from": restoration["restoration_event_id"], "relationship": "EVIDENCED_BY", "to": restoration["restoration_source_id"], "ownership": "test-owned edge only"}, "restoration counterfactual must remove only its event/evidence edge")
    require(restoration_loss.get("after_removal") == {"state_status": outage["status"], "selected_event": outage_case["selected_event"], "impact_paths": original_paths, "restoration_event_or_source_details_emitted": False}, "unsupported restoration must leave only the last source-backed outage paths")
    require(restoration_loss.get("reseed_restores_exact_baseline") is True, "restoration-edge reseed must restore baseline")

    branch_case = cases[REQUIRED_CASES[8]]
    require((branch_case.get("branch_at"), branch_case.get("conveyor_known_at")) == (branch["at"], "2026-10-14T18:04:00Z"), "branch time/cutoff changed")
    require(branch_case.get("branch") == {"branch_id": branch["branch_id"], "label": "hypothetical", "event_id": branch["hypothetical_event_id"], "event_at": branch["hypothetical_event_at"], "status": branch["branch_status"], "impact_paths": branch["branch_paths"], "event_is_observed": False}, "branch label/ID/status/time/path differs from accepted hypothetical outcome")
    require(branch_case.get("observed_mainline") == {"status": branch["mainline_status"], "impact_paths": [original_paths[0]], "path_evidence": [original_evidence[0]]}, "branch baseline must apply the 18:04 evidence cutoff and remain observed")
    require(branch_case.get("legacy_current_knowledge_mainline_paths") == branch["mainline_paths"], "legacy branch baseline differs from accepted current-knowledge outcome")
    require(branch_case.get("branch_changes_observed_history") is False and branch_case.get("hypothetical_event_in_mainline") is False, "hypothetical branch must not mutate or enter observed mainline")

    acl = cases[REQUIRED_CASES[9]]
    require(acl.get("viewer_scope") == "operations" and acl.get("restricted_fixture") == restricted, "ACL test must use accepted restricted event/source tripwire")
    require(acl.get("axis_pairs") == [["2026-10-14T19:30:00Z", value] for value in ("2026-10-14T19:14:00Z", "2026-10-14T19:16:00Z", "2026-10-14T18:04:00Z", "2026-10-14T18:01:00Z")], "ACL invariance must cover all four fixed knowledge cutoffs")
    require(acl.get("full_selected_event_response_and_accessible_event_panel_invariant_before_after") is True, "ACL response invariance was weakened")
    require(acl.get("hidden_id_title_fact_count_existence_relationship_path_or_paraphrase_disclosed") is False, "ACL non-disclosure expectation was weakened")

    times = cases[REQUIRED_CASES[10]]
    canonical = {"conveyor_at": "2026-10-14T19:30:00Z", "conveyor_known_at": "2026-10-14T19:14:00Z"}
    offset = {"conveyor_at": "2026-10-14T21:30:00+02:00", "conveyor_known_at": "2026-10-14T21:14:00+02:00"}
    require(times.get("canonical_pair") == canonical and times.get("offset_pair") == offset, "valid/knowledge UTC-offset inputs changed")
    normalized = {"conveyor_at": _normalize_utc(offset["conveyor_at"]), "conveyor_known_at": _normalize_utc(offset["conveyor_known_at"])}
    require(times.get("normalized_pair") == normalized == canonical, "equivalent offsets do not normalize to the pinned outage result")
    require(times.get("offset_result_matches_case") == REQUIRED_CASES[2], "offset result must equal the 19:30/19:14 group")
    require(times.get("malformed_conveyor_at") == "not-a-timestamp" and times.get("timezone_naive_conveyor_known_at") == "2026-10-14T19:14:00", "malformed/naive probes changed")
    require(times.get("both_invalid_axes_rejected_before_graph_or_output_mutation") is True and times.get("local_time_fallback") is False, "invalid inputs must reject before mutation without local-time fallback")

    lifecycle = cases[REQUIRED_CASES[11]]
    for field in ("repeat_seed_duplicate_nodes", "repeat_seed_duplicate_relationships", "query_graph_writes", "query_action_writes", "messages_sent", "calls_made", "tickets_created"):
        require(lifecycle.get(field) == 0, f"lifecycle side effect {field} must remain zero")
    require(lifecycle.get("after_reconnect_result_matches") == REQUIRED_CASES[2], "fresh reconnect must reproduce the fixed outage-known result")
    require(lifecycle.get("desktop_mobile_api_results_agree") is True and lifecycle.get("explicit_confirmation_remains_separate") is True, "responsive API or explicit confirmation boundary changed")
    require(lifecycle.get("external_writes") == [] and lifecycle.get("cleanup_deletes_only_task_owned_graph_and_stops_only_owned_processes") is True, "external-write/owned-cleanup boundary changed")


def verify_pinned_packet(path: Path) -> str:
    require(path.name == PACKET_FILENAME, f"pinned plan must be named {PACKET_FILENAME}")
    try:
        raw = path.read_bytes()
        text = raw.decode("utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise ContractFailure(f"cannot read pinned decision packet {path}: {exc}") from exc
    digest = hashlib.sha256(raw).hexdigest()
    require(digest == PACKET_SHA256, "decision packet bytes differ from the independently pinned INC-204 packet")
    lines = text.splitlines()
    for line_number, prefix in zip(PACKET_OUTCOME_LINES, PACKET_OUTCOME_PREFIXES):
        require(len(lines) >= line_number and lines[line_number - 1].startswith(prefix), f"decision packet outcome heading changed at line {line_number}")
    return digest


def verify_existing_oracles(root: Path, expected: dict[str, Any]) -> dict[str, str]:
    """Check the 24-file freeze plus exact, byte-pinned additive pairs.

    The additive allowlist is deliberately separate from the oracle's frozen
    pre-task manifest. No matching file outside either inventory is accepted.
    """
    require(expected.get("frozen_preexisting_oracle_hashes") == _expected_frozen_manifest(), "frozen hash manifest differs from the independently pinned file list")
    additive_files = _pinned_additive_files()
    pinned_files = set(PINNED_EXISTING) | set(additive_files)
    discovered = _discover_oracle_matrix_files(root)
    discovered.difference_update(
        {
            "tests/conveyor-bitemporal-expected-results.json",
            "tests/conveyor-bitemporal-adversarial-matrix.json",
        }
    )
    require(discovered == pinned_files, "test oracle/matrix file set differs from the frozen and explicitly additive inventories")
    digests: dict[str, str] = {}
    for relative, pinned in {**PINNED_EXISTING, **additive_files}.items():
        try:
            raw = (root / relative).read_bytes()
        except OSError as exc:
            raise ContractFailure(f"cannot read pinned oracle/matrix {relative}: {exc}") from exc
        actual = hashlib.sha256(raw).hexdigest()
        if relative in PINNED_EXISTING:
            require(actual == pinned, f"pre-existing oracle/matrix {relative} differs from its frozen byte hash")
        else:
            require(actual == pinned, f"additive oracle/matrix {relative} differs from its pinned byte hash")
        digests[relative] = actual
    return digests


def validate_artifacts(
    expected: dict[str, Any],
    matrix: dict[str, Any],
    expected_sha256: str,
    matrix_sha256: str,
    root: Path,
    pinned_plan: Path,
    require_twelve_groups: bool,
) -> tuple[int, dict[str, str]]:
    assertion_count = validate_structure(expected, matrix, require_twelve_groups)
    require(expected_sha256 == EXPECTED_ARTIFACT_SHA256, "conveyor bitemporal expected-results bytes differ from the fixed oracle hash")
    require(matrix_sha256 == MATRIX_ARTIFACT_SHA256, "conveyor bitemporal matrix bytes differ from the fixed matrix hash")
    packet_digest = verify_pinned_packet(pinned_plan)
    frozen_digests = verify_existing_oracles(root, expected)
    validate_fixed_outcomes(expected, root)
    return assertion_count, {"conveyor-bitemporal-packet": packet_digest, **frozen_digests}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expected", required=True, type=Path)
    parser.add_argument("--matrix", required=True, type=Path)
    parser.add_argument("--pinned-plan", required=True, type=Path)
    parser.add_argument("--require-twelve-groups", action="store_true")
    parser.add_argument("--check-frozen-oracle-hashes", action="store_true")
    args = parser.parse_args(argv)
    if not args.require_twelve_groups or not args.check_frozen_oracle_hashes:
        print("FAIL: --require-twelve-groups and --check-frozen-oracle-hashes are mandatory", file=sys.stderr)
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
            require_twelve_groups=True,
        )
    except (OSError, TypeError, ValueError, KeyError) as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1
    print(
        "PASS: twelve fixed INC-204 event-valid/knowledge-known outcome groups; "
        f"{assertions} explicit nonzero assertions; packet, five accepted source outcomes, "
        f"all 24 frozen pre-existing oracle/matrix hashes, and "
        f"{len(PINNED_ADDITIVE_ORACLE_MATRIX_PAIRS)} explicitly pinned additive oracle/matrix pairs verified. "
        "No candidate behavior or output used."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

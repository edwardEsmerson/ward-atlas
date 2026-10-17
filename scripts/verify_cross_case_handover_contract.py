#!/usr/bin/env python3
"""Verify the frozen cross-case handover oracle, mappings, and source pins.

This is an artifact-only verifier. It never imports product code, reads
candidate output, or connects to FalkorDB. Expected values are byte-pinned,
checked against the decision packet, and cross-checked against immutable
accepted synthetic oracles before any future behavior implementation.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any


EXPECTED_ARTIFACT_SHA256 = "931b39dcac5e4315d773b84b6100865ad4a172fc64faecc6c758cb0b8598d22a"
MATRIX_ARTIFACT_SHA256 = "24b27185c7586694b97b5e86145b60638b2a1ddf327d1ce497cd229c42e83715"
FROZEN_HASH_MANIFEST_SHA256 = "d3e34775e0a616a51fd1981a1df244a04d0e5c7ac99fe329fd607ebf3b647819"
PACKET_SHA256 = "370431eb2defa6363a4e56d521aeb6aaae8b38a272389d21b41febb1e0dcc44c"
PACKET_FILENAME = "planner-b282158eb9404a67925e94e5c174a389.md"
PACKET_LINES = tuple(range(74, 83))
PACKET_PREFIXES = (
    "1. **Cold empty:**",
    "2. **Known outage + R1:**",
    "3. **Restoration becomes known:**",
    "4. **R2 not yet known:**",
    "5. **R2 becomes known:**",
    "6. **Case-isolated graph counterfactuals:**",
    "7. **Branch stays hypothetical:**",
    "8. **Denied-source invariance:**",
    "9. **Repeatability, read-only and responsive agreement:**",
)
FROZEN_FILE_COUNT = 30
REQUIRED_ASSERTION_COUNT = 66

REQUIRED_CASES = (
    "cold-empty-unknown",
    "outage-known-at-1914-with-dock-r1",
    "restoration-known-at-1916-same-valid-times",
    "dock-r2-not-yet-known-at-201759",
    "dock-r2-known-at-2019-effective-2030",
    "case-isolated-edge-removals",
    "hypothetical-branch-separate-from-mainline",
    "full-denied-source-response-invariance",
    "repeat-reconnect-read-only-responsive-owned-cleanup",
)

# Accepted oracle bytes used as the independent source of literal outcomes.
PINNED_ORACLES = {
    "tests/conveyor-bitemporal-expected-results.json": "8ffa7ee25a52219808656ead871fac4779625300f9eea0eaa9543e8b0c7740e6",
    "tests/conveyor-bitemporal-adversarial-matrix.json": "e036570a3db09c8b544cb339b2665beeee3d469416c4b5fe69e5be2b3597eb67",
    "tests/dock-bitemporal-expected-results.json": "243c92775d7c1366962dcd7c5c9acd9ea753f2924af84c2c5dc99b0b5c872205",
    "tests/dock-bitemporal-adversarial-matrix.json": "84127f273043a03299823905bbecc06a755f79cb9a3b34c44793815eda7f5318",
    "tests/handover-known-time-expected-results.json": "fd72df03c8e865cfe347839074ee8251bf57216deac33fcc9d5a4d1152513f93",
    "tests/handover-known-time-adversarial-matrix.json": "7b8450947696e55af0dc48aaa6d27fd0231f458b0f527b3fc00f643ad1f64f67",
    "tests/shift-board-expected-results.json": "5d53a8f93245ee8391ec7cffb6d45b58355536d30d649db19cd53ca6b1ce2428",
    "tests/shift-board-adversarial-matrix.json": "9cf1a4b144df3ff7357e389acec7c5e6b0e6ddcdab18577fa193c09541c0bbd7",
    "tests/sop-impact-expected-results.json": "f94e65850384e36e7200b321d14628f074fee37fb159adbcd73bef00c6e1acca",
    "tests/sop-impact-adversarial-matrix.json": "f5aa441a636b45b4222716d4a003afc91cf7598439833e36f390523b55f161c6",
    "tests/handover-brief-expected-results.json": "932ada7d43688b8bd1e4f147ac06dd585982d784ef8577f05cc00b07a97199f1",
    "tests/handover-brief-adversarial-matrix.json": "d084f31a1b542670ab99818852cba8e1141bfb56688845c439b01f5ac1d89003",
    "tests/expected-results.json": "c77e00e47c4f1e26d565c3d9583653838d5cc88695c4e6aa19ac583205d92b87",
    "tests/adversarial-matrix.json": "6d0edbc69620da85b494185879565c8260c9d2d3fe6afdd5bf56c5acd92205ba",
}

SOURCE_IDS = {
    "tests/conveyor-bitemporal-adversarial-matrix.json": "conveyor-bitemporal-adversarial-matrix",
    "tests/conveyor-bitemporal-expected-results.json": "conveyor-bitemporal-expected-results",
    "tests/dock-bitemporal-adversarial-matrix.json": "dock-bitemporal-adversarial-matrix",
    "tests/dock-bitemporal-expected-results.json": "dock-bitemporal-expected-results",
    "tests/expected-results.json": "expected-results",
    "tests/handover-brief-adversarial-matrix.json": "handover-brief-adversarial-matrix",
    "tests/handover-brief-expected-results.json": "handover-brief-expected-results",
    "tests/handover-known-time-adversarial-matrix.json": "handover-known-time-adversarial-matrix",
    "tests/handover-known-time-expected-results.json": "handover-known-time-expected-results",
    "tests/shift-board-adversarial-matrix.json": "shift-board-adversarial-matrix",
    "tests/shift-board-expected-results.json": "shift-board-expected-results",
    "tests/sop-impact-adversarial-matrix.json": "sop-impact-adversarial-matrix",
    "tests/sop-impact-expected-results.json": "sop-impact-expected-results",
    "tests/adversarial-matrix.json": "adversarial-matrix",
}

# A second, code-pinned digest for each outcome makes mutation checks sensitive
# to any changed or omitted nested value, even when a test recomputes a digest.
FIXED_CASE_SHA256 = {
    "cold-empty-unknown": "3a3aafe3645d6e1b4be30dfdb0f53dd0b93ceea48391b5bd3c25a51d9950f482",
    "outage-known-at-1914-with-dock-r1": "b5057cd401ddfa03c3192154f69e827fd7b21099e57d74ddced5c49d5b548a8e",
    "restoration-known-at-1916-same-valid-times": "9cae669adab95b7fa4a2c187a01fb34c162f7d8b8249f9ef41b0d918650bd950",
    "dock-r2-not-yet-known-at-201759": "025df15cd1973b8d3e05fddfb612b0c840c98ca40d634568d863daf678fa4e93",
    "dock-r2-known-at-2019-effective-2030": "08a52acd383bb3e98cce1266228c053df3381dd825540b175f331ba010277f83",
    "case-isolated-edge-removals": "348efeaf83a1da43292c8a3c778c5bdb81ed13f3995ec92a52cbe6452cf08a60",
    "hypothetical-branch-separate-from-mainline": "df85c21678cf5d2f8584f7a9e1aa4c4ddc6eb0c4c4a5e321709ce64ce631d317",
    "full-denied-source-response-invariance": "2577f01ae6ac2876fed578b9ad1854de1897e362afc1f54b6c962d02aea9841c",
    "repeat-reconnect-read-only-responsive-owned-cleanup": "0326db936d519754231524415e7d3218ac52354f3662826993a56139b1b3f488",
}


class ContractFailure(ValueError):
    """Raised when a pinned outcome, source, or assertion map has drifted."""


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ContractFailure(message)


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def canonical_sha(value: Any) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return _sha(raw)


def _load_json(path: Path) -> tuple[dict[str, Any], str]:
    try:
        raw = path.read_bytes()
        value = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ContractFailure(f"cannot load JSON artifact {path}: {exc}") from exc
    require(isinstance(value, dict), f"JSON artifact {path} must contain an object")
    return value, _sha(raw)


def _case_map(document: dict[str, Any]) -> dict[str, dict[str, Any]]:
    rows = document.get("cases", document.get("scenarios", []))
    return {row["id"]: row.get("expected", {}) for row in rows}


def _expected_pinned_sources() -> list[dict[str, Any]]:
    return [
        {
            "id": "cross-case-handover-packet",
            "file": f"plans/{PACKET_FILENAME}",
            "sha256": PACKET_SHA256,
            "outcome_lines": list(PACKET_LINES),
        },
        *[
            {"id": SOURCE_IDS[path], "file": path, "sha256": digest}
            for path, digest in PINNED_ORACLES.items()
        ],
    ]


def _resolve_pointer(document: Any, pointer: str) -> Any:
    require(isinstance(pointer, str) and pointer.startswith("/"), f"invalid JSON pointer {pointer!r}")
    value = document
    for component in pointer[1:].split("/"):
        part = component.replace("~1", "/").replace("~0", "~")
        if isinstance(value, list):
            require(part.isdigit() and int(part) < len(value), f"matrix pointer does not resolve: {pointer}")
            value = value[int(part)]
        elif isinstance(value, dict) and part in value:
            value = value[part]
        else:
            raise ContractFailure(f"matrix pointer does not resolve: {pointer}")
    return value


def validate_structure(expected: dict[str, Any], matrix: dict[str, Any], require_nine_groups: bool = True) -> int:
    """Validate exact case identities, packet mapping, and nonzero pointers."""
    require(expected.get("schema_version") == 1 and matrix.get("schema_version") == 1, "schema_version must be 1")
    require(expected.get("product") == "WardAtlas", "unexpected product")
    policy = expected.get("oracle_policy", "").lower()
    require("candidate behavior" in policy and "never" in policy and "amend" in policy, "candidate output cannot supply or amend expectations")
    scope = expected.get("scope")
    require(isinstance(scope, dict) and scope.get("synthetic_only") is True, "oracle must remain synthetic-only")
    require(scope.get("clinical_decisions") is False and scope.get("external_writes") is False, "clinical decisions/external writes must remain forbidden")
    require(scope.get("candidate_output_is_oracle") is False, "candidate output must not be an oracle")
    require(scope.get("viewer_scope") == "operations", "viewer scope changed")
    require("actual falkordb" in str(scope.get("database_requirement", "")).lower(), "actual FalkorDB remains required for future behavior work")
    require(matrix.get("oracle_file") == "tests/cross-case-handover-expected-results.json", "matrix points at a different oracle")
    require(expected.get("pinned_sources") == _expected_pinned_sources(), "pinned packet/source identities or hashes changed")
    require(expected.get("frozen_hash_manifest") == {
        "file": "tests/cross-case-handover-frozen-hashes.json",
        "sha256": FROZEN_HASH_MANIFEST_SHA256,
        "file_count": FROZEN_FILE_COUNT,
    }, "frozen hash manifest identity or digest changed")

    cases = expected.get("cases")
    matrix_cases = matrix.get("cases")
    require(isinstance(cases, list) and isinstance(matrix_cases, list), "expected and matrix cases must be arrays")
    case_ids = [row.get("id") for row in cases]
    matrix_ids = [row.get("id") for row in matrix_cases]
    require(all(isinstance(item, str) and item for item in case_ids + matrix_ids), "case IDs must be nonempty strings")
    require(len(case_ids) == len(set(case_ids)) and len(matrix_ids) == len(set(matrix_ids)), "case IDs must be unique")
    if require_nine_groups:
        require(len(cases) == 9 and len(matrix_cases) == 9, "--require-nine-groups requires exactly nine groups")
    require(tuple(case_ids) == REQUIRED_CASES, "the nine fixed outcomes or their order changed")
    require(matrix_ids == case_ids, "matrix groups must map one-to-one in the same order")

    total = 0
    assertion_ids: set[str] = set()
    for index, (scenario, group) in enumerate(zip(cases, matrix_cases), start=1):
        case_id = REQUIRED_CASES[index - 1]
        require(scenario.get("scenario_id") == case_id and group.get("scenario_id") == case_id, f"{case_id} scenario mapping changed")
        require(scenario.get("planner_refs") == [f"cross-case-handover-packet#outcome-{index}"], f"{case_id} must map to packet outcome {index}")
        require(group.get("planner_refs") == [f"{PACKET_FILENAME}:{PACKET_LINES[index - 1]}"], f"{case_id} matrix must cite its exact packet line")
        fixed = scenario.get("expected")
        require(isinstance(fixed, dict) and fixed, f"{case_id} has no fixed expected result")
        require(isinstance(scenario.get("evidence_semantics"), str) and scenario["evidence_semantics"].strip(), f"{case_id} needs fixed evidence semantics")
        require(bool(group.get("attack_or_edge_case")) and bool(group.get("setup")) and bool(group.get("failure_detected")), f"{case_id} matrix description is incomplete")
        assertions = group.get("pinned_assertions")
        count = group.get("nonzero_assertion_count")
        require(isinstance(assertions, list) and assertions, f"{case_id} has no pinned assertions")
        require(type(count) is int and count > 0, f"{case_id} must have a nonzero assertion count")
        require(count == len(assertions), f"{case_id} assertion count differs from its mappings")
        for assertion in assertions:
            require(isinstance(assertion, dict), f"{case_id} assertion must be an object")
            identifier = assertion.get("id")
            require(isinstance(identifier, str) and identifier.strip(), f"{case_id} assertion ID must be nonempty")
            require(identifier not in assertion_ids, f"duplicate assertion ID {identifier}")
            assertion_ids.add(identifier)
            require(isinstance(assertion.get("assertion"), str) and assertion["assertion"].strip(), f"{identifier} has no assertion description")
            pointer = assertion.get("expected_pointer")
            require(isinstance(pointer, str) and pointer.startswith(f"/cases/{index-1}/expected/"), f"{identifier} maps outside its own fixed outcome")
            _resolve_pointer(expected, pointer)
        total += count
    require(total == REQUIRED_ASSERTION_COUNT, f"expected exactly {REQUIRED_ASSERTION_COUNT} mapped assertions, found {total}")
    require(all(row.get("nonzero_assertion_count", 0) > 0 for row in matrix_cases), "every group must map at least one assertion")
    return total


def validate_fixed_outcomes(expected: dict[str, Any]) -> None:
    """Reject all content mutations to the nine literal outcome objects."""
    cases = expected.get("cases")
    require(isinstance(cases, list) and len(cases) == 9, "fixed outcomes must contain exactly nine groups")
    require(tuple(row.get("id") for row in cases) == REQUIRED_CASES, "fixed outcome IDs changed")
    for row in cases:
        case_id = row["id"]
        actual = canonical_sha(row.get("expected"))
        require(actual == FIXED_CASE_SHA256[case_id], f"fixed expected values changed or are missing for {case_id}")


def verify_artifact_digests(expected_sha: str, matrix_sha: str, manifest_sha: str) -> None:
    require(expected_sha == EXPECTED_ARTIFACT_SHA256, "cross-case expected-results bytes differ from frozen oracle")
    require(matrix_sha == MATRIX_ARTIFACT_SHA256, "cross-case adversarial-matrix bytes differ from frozen mapping")
    require(manifest_sha == FROZEN_HASH_MANIFEST_SHA256, "frozen-baseline hash manifest bytes differ from its pinned digest")


def verify_pinned_packet(path: Path) -> str:
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise ContractFailure(f"cannot read pinned decision packet {path}: {exc}") from exc
    digest = _sha(raw)
    require(digest == PACKET_SHA256, "planner packet bytes differ from the independently pinned cross-case packet")
    try:
        lines = raw.decode("utf-8").splitlines()
    except UnicodeDecodeError as exc:
        raise ContractFailure("pinned planner packet is not UTF-8") from exc
    for number, prefix in zip(PACKET_LINES, PACKET_PREFIXES):
        require(len(lines) >= number and lines[number - 1].startswith(prefix), f"planner packet line {number} no longer names its fixed group")
    return digest


def verify_pinned_sources(root: Path, expected: dict[str, Any], pinned_plan: Path) -> dict[str, str]:
    """Check accepted source bytes before comparing their fixed outcome overlaps."""
    require(pinned_plan.name == PACKET_FILENAME, "pinned planner packet has an unexpected filename")
    observed = {f"plans/{PACKET_FILENAME}": verify_pinned_packet(pinned_plan)}
    for relative, digest in PINNED_ORACLES.items():
        path = root / relative
        try:
            actual = _sha(path.read_bytes())
        except OSError as exc:
            raise ContractFailure(f"cannot read unchanged accepted oracle {path}: {exc}") from exc
        require(actual == digest, f"unchanged accepted oracle {relative} differs from its frozen byte hash")
        observed[relative] = actual
    require(expected.get("pinned_sources") == _expected_pinned_sources(), "expected artifact cites different pinned sources")
    return observed


def _load_source_documents(root: Path) -> dict[str, dict[str, Any]]:
    documents: dict[str, dict[str, Any]] = {}
    for relative in PINNED_ORACLES:
        if relative.endswith("expected-results.json"):
            documents[relative], _ = _load_json(root / relative)
    return documents


def verify_source_overlaps(expected: dict[str, Any], root: Path) -> None:
    """Cross-check every reused path/event/role/SOP/edge/ACL value to its oracle."""
    docs = _load_source_documents(root)
    hkt = _case_map(docs["tests/handover-known-time-expected-results.json"])
    dock = _case_map(docs["tests/dock-bitemporal-expected-results.json"])
    conveyor = _case_map(docs["tests/conveyor-bitemporal-expected-results.json"])
    shift = _case_map(docs["tests/shift-board-expected-results.json"])
    sop = _case_map(docs["tests/sop-impact-expected-results.json"])
    brief = _case_map(docs["tests/handover-brief-expected-results.json"])
    rows = {row["id"]: row["expected"] for row in expected["cases"]}

    hkt_outage = hkt["outage-at-1914-snapshot"]
    hkt_restore = hkt["restoration-at-1916-same-valid-time"]
    dock_r1 = dock["dock-bitemporal-early-known-r1-duty"]
    dock_unknown = dock["dock-bitemporal-crossed-axes-unknown"]
    dock_swing = dock["dock-bitemporal-known-future-effective-swing"]
    dock_pending = dock["dock-bitemporal-known-r2-pending-review"]
    dock_mutations = dock["dock-bitemporal-edge-removal-reseed"]

    fixture = expected["fixture"]
    conv_fixture = docs["tests/conveyor-bitemporal-expected-results.json"]["fixture"]
    dock_fixture = docs["tests/dock-bitemporal-expected-results.json"]["fixture"]
    require(fixture["inc204"]["canonical_paths"] == conv_fixture["canonical_paths"], "canonical INC-204 paths differ from accepted conveyor oracle")
    require(fixture["inc204"]["canonical_path_evidence"] == conv_fixture["canonical_path_evidence"], "canonical path source IDs/titles/times differ from accepted conveyor oracle")
    require(fixture["inc207"]["r1"] == dock_fixture["r1"] and fixture["inc207"]["r2"] == dock_fixture["r2"], "INC-207 roster source provenance differs from accepted dock oracle")
    require(fixture["inc207"]["review_candidates"] == dock_fixture["review_candidate_ids"], "fixed dock review candidate IDs differ from accepted dock oracle")

    cold = rows[REQUIRED_CASES[0]]
    require(cold["graph_state"] == hkt["cold-empty-is-unknown"]["graph_state"], "cold graph state differs from accepted handover oracle")
    require(cold["inc204"]["event"] is None and cold["inc204"]["impact_paths"] == [] and cold["inc204"]["sop_witness"] is None, "cold INC-204 must remain empty/unknown")
    require(cold["inc207"]["role_status"] == dock["dock-bitemporal-cold-empty"]["role_status"] == "unknown", "cold dock ownership must remain unknown")
    require(cold["saved_actions"] == hkt["cold-empty-is-unknown"]["saved_actions"] == [], "cold graph must not invent saved actions")

    outage = rows[REQUIRED_CASES[1]]
    require(outage["request"] == {"known_at":"2026-10-14T19:14:00Z","conveyor_at":"2026-10-14T19:30:00Z","dock_effective_at":"2026-10-14T20:29:00Z"}, "outage/R1 time pair changed")
    require(outage["inc207"]["known_at"] == outage["request"]["known_at"], "INC-204 and INC-207 must use the same 19:14Z knowledge cutoff")
    require(outage["inc204"]["event"] == hkt_outage["event"], "outage event/source ID/title/time differs from accepted handover oracle")
    require(outage["inc204"]["impact_paths"] == hkt_outage["impact_paths"] and outage["inc204"]["path_evidence"] == hkt_outage["path_evidence"], "outage paths/provenance differ from accepted handover oracle")
    require(outage["inc204"]["sop_witness"] == hkt_outage["sop_witness"], "outage SOP claims differ from accepted handover oracle")
    require(outage["inc207"]["role_path"] == dock_r1["role_path"] and outage["inc207"]["source_versions"] == dock_r1["source_versions"], "R1 duty path/source differs from accepted dock oracle")
    require(outage["inc207"]["review_candidates"] == [] and outage["inc207"]["r2_identifier_title_effective_time_count_or_future_revision_wording_visible"] is False, "R2 must not leak before observation")
    require(outage["cross_case"]["dock_roster_revision_caused_conveyor_outage"] is False and outage["cross_case"]["path_joining_INC_207_to_CONVEYOR_C4"] is False, "cross-case causal/path join must remain forbidden")

    restored = rows[REQUIRED_CASES[2]]
    require(restored["inc207"]["known_at"] == restored["request"]["known_at"], "INC-204 and INC-207 must use the same 19:16Z knowledge cutoff")
    require(restored["inc204"]["event"] == hkt_restore["event"], "restoration source ID/title/time differs from accepted handover oracle")
    require(restored["inc204"]["active_impact_paths"] == hkt_restore["active_impact_paths"] == [], "restoration must leave no active impact paths")
    require(restored["inc204"]["sop_witness"] == hkt_restore["sop_witness"], "restoration must preserve exact unresolved SOP witness")
    historical_source = shift["conveyor-observed-and-hypothetical-times"]["observed_at_1830"]
    require(restored["inc204"]["historical_replay"] == historical_source, "18:30Z historical replay paths/provenance differ from accepted shift-board oracle")
    require(restored["inc207"]["role_path"] == dock_r1["role_path"], "dock R1 duty path must remain unchanged at 20:29Z")

    unknown = rows[REQUIRED_CASES[3]]
    require(unknown["inc207"] == dock_unknown, "20:17:59Z unknown dock result differs from accepted bitemporal dock oracle")
    require(unknown["inc204"]["event"] == hkt_restore["event"] and unknown["inc204"]["sop_witness"] == hkt_restore["sop_witness"], "INC-204 restoration must remain unchanged in unknown dock case")
    require(unknown["r2_identifier_title_time_count_or_existence_hint_disclosed"] is False, "unknown dock role must not reveal an R2 existence hint")

    known = rows[REQUIRED_CASES[4]]
    known_inc207 = dict(known["inc207"])
    disposition = known_inc207.pop("candidate_disposition", None)
    require(known_inc207 == dock_swing, "20:19Z/20:30Z swing result differs from accepted dock oracle")
    require(disposition == dock_pending["candidate_disposition"], "R2 candidate disposition differs from accepted dock oracle")
    require(known["dock_before_effective"] == dock_pending, "20:29Z known-R2 pending result differs from accepted dock oracle")
    require(known["effective_boundary"] == {
        "at_2026_10_14T20_29_00Z":"ROLE-DOCK-DUTY",
        "at_2026_10_14T20_30_00Z":"ROLE-DOCK-SWING",
        "only_swing_at_selected_effective_boundary":True,
    }, "R2 must not appear effective before the selected instant")
    require(known["inc204"]["event"] == hkt_restore["event"] and known["inc204"]["sop_witness"] == hkt_restore["sop_witness"], "dock R2 knowledge must not change the restored INC-204 case")

    mutations = rows[REQUIRED_CASES[5]]
    require(mutations["r2_edge_mutation_times"] == {"known_at":"2026-10-14T20:19:00Z","conveyor_at":"2026-10-14T19:30:00Z","dock_effective_at":"2026-10-14T20:30:00Z"}, "R2 counterfactual time pair changed")
    require(mutations["restoration_evidence_mutation_times"] == {"known_at":"2026-10-14T19:16:00Z","conveyor_at":"2026-10-14T19:30:00Z","dock_effective_at":"2026-10-14T20:29:00Z"}, "restoration-edge counterfactual must preserve the 19:16Z/20:29Z shared baseline")
    restoration_edge = conveyor["restoration-evidence-edge-removal"]
    require(mutations["restoration_evidence_edge_removal"]["source_oracle_case"] == restoration_edge, "restoration-evidence removal differs from accepted conveyor edge case")
    require(mutations["restoration_evidence_edge_removal"]["unaffected_inc207_result"] == restored["inc207"], "restoration evidence removal must leave dock case unchanged")
    require(mutations["r2_supersedes_edge_removal"]["source_oracle_case"] == dock_mutations["remove_only_supersedes_edge"], "R2-SUPERSEDES removal differs from accepted dock edge case")
    require(mutations["r2_supersedes_edge_removal"]["baseline_inc207"] == dock_mutations["baseline"], "R2-SUPERSEDES baseline differs from accepted dock oracle")
    require(mutations["r2_supersedes_edge_removal"]["role_path_after_removal"] == dock_mutations["baseline"]["role_path"] and mutations["r2_supersedes_edge_removal"]["role_id_after_removal"] == "ROLE-DOCK-SWING", "removing only R2-SUPERSEDES must leave the R2-owned swing role path unchanged")
    require(mutations["r2_owned_by_edge_removal"]["source_oracle_case"] == dock_mutations["remove_only_r2_ownership_edge"], "R2-OWNED_BY removal differs from accepted dock edge case")
    require(mutations["reseed"]["restoration_evidence_restores_exact_inc204_baseline"] is True and mutations["reseed"]["dock_edge_removals_restore_exact_baseline"] is True, "edge-removal reseed must restore each baseline")
    require(mutations["restoration_evidence_edge_removal"]["only_inc204_case_changes"] is True and mutations["r2_supersedes_edge_removal"]["only_inc207_case_changes"] is True and mutations["r2_owned_by_edge_removal"]["only_inc207_case_changes"] is True, "edge mutations must remain case-isolated")

    branch = rows[REQUIRED_CASES[6]]
    branch_source = shift["conveyor-observed-and-hypothetical-times"]["hypothetical_branch_1850"]
    require(branch["branch"]["branch_id"] == branch_source["branch_id"] and branch["branch"]["event_id"] == branch_source["hypothetical_event_id"] and branch["branch"]["event_at"] == branch_source["hypothetical_event_at"], "hypothetical branch identity/time differs from accepted shift-board oracle")
    require(branch["branch"]["impact_paths"] == branch_source["branch_paths"] == [] and branch["branch"]["event_is_observed"] is False, "branch must remain hypothetical with zero paths")
    require(branch["observed_mainline"]["impact_paths"] == hkt_outage["impact_paths"] and len(branch["observed_mainline"]["impact_paths"]) == 2, "19:16Z mainline must retain exactly two observed outage paths")
    require(branch["observed_mainline"]["path_evidence"] == hkt_outage["path_evidence"], "branch mainline provenance differs from pinned outage paths")
    require(branch["inc207"]["role_path"] == [] and branch["inc207"]["known_at"] == "2026-10-14T19:16:00Z", "branch projection must keep pre-R2 dock output unchanged")
    require(branch["observed_mainline_mutated"] is False and branch["existing_saved_local_actions_unchanged"] is True and branch["cross_case_causality_asserted"] is False, "branch must not mutate observed/action state or assert cross-case causality")

    restricted = rows[REQUIRED_CASES[7]]
    shift_denied = shift["denied-source-nondisclosure"]
    brief_denied = brief["denied-source-response-invariance"]
    require(restricted["restricted_tripwire_fixtures"]["dock_literals"] == shift_denied["dock_restricted_literals"], "restricted dock literals differ from accepted ACL oracle")
    require(restricted["restricted_tripwire_fixtures"]["conveyor_event"] == shift_denied["conveyor_restricted_fixture"], "restricted conveyor fixture differs from accepted ACL oracle")
    require(restricted["restricted_tripwire_fixtures"]["sop_claim"] == brief_denied["restricted_probe"], "restricted SOP fixture differs from accepted handover oracle")
    inv = restricted["full_response_invariance"]
    require(inv == {
        "combined_api_projection_unchanged":True,
        "visible_count_fields_unchanged":True,
        "browser_visible_text_unchanged":True,
        "accessibility_tree_semantically_unchanged":True,
        "hidden_identifier_title_fact_source_time_count_existence_relationship_path_or_dependent_paraphrase_disclosed":False,
    }, "full denied-source invariance/no-leak matrix changed")

    repeat = rows[REQUIRED_CASES[8]]
    require(repeat["seed_runs"] == 2 and repeat["duplicate_fixed_node_count_after_second_seed"] == 0 and repeat["duplicate_fixed_relationship_count_after_second_seed"] == 0, "idempotent reseed values changed")
    require(repeat["fresh_falkordb_client_and_restarted_app_required"] is True and repeat["graph_write_delta_for_query_or_time_selection"] == 0 and repeat["local_action_write_delta_for_query_or_time_selection"] == 0, "reconnect/read-only values changed")
    require(repeat["api_desktop_mobile_snapshots_match_expected_case"] is True and repeat["cleanup_deletes_only_task_owned_graph_and_state"] is True and repeat["cleanup_stops_only_task_owned_processes"] is True, "responsive agreement/owned cleanup changed")


def verify_frozen_hashes(root: Path, manifest: dict[str, Any], expected: dict[str, Any]) -> dict[str, str]:
    """Verify every pre-task expected-results/matrix file and no hidden omissions."""
    require(manifest.get("schema_version") == 1 and manifest.get("hash_algorithm") == "sha256", "frozen hash manifest schema/algorithm changed")
    require(manifest.get("purpose", "").startswith("Byte hashes of every expected-results and adversarial-matrix JSON"), "frozen hash manifest purpose changed")
    files = manifest.get("files")
    require(isinstance(files, list) and len(files) == FROZEN_FILE_COUNT, f"frozen hash manifest must list exactly {FROZEN_FILE_COUNT} pre-task oracle/matrix files")
    paths = [row.get("file") for row in files]
    require(all(isinstance(path, str) for path in paths) and paths == sorted(paths) and len(paths) == len(set(paths)), "frozen hash files must be unique and sorted")
    observed: dict[str, str] = {}
    for row in files:
        relative = row["file"]
        require(relative.startswith("tests/") and ".." not in Path(relative).parts, f"invalid frozen path {relative}")
        filename = Path(relative).name
        require(
            filename in {"expected-results.json", "adversarial-matrix.json"}
            or filename.endswith("-expected-results.json")
            or filename.endswith("-adversarial-matrix.json"),
            f"frozen path is not an expected-results or adversarial-matrix file: {relative}",
        )
        try:
            digest = _sha((root / relative).read_bytes())
        except OSError as exc:
            raise ContractFailure(f"cannot read frozen baseline {relative}: {exc}") from exc
        require(digest == row.get("sha256"), f"frozen-baseline hash drift for {relative}")
        observed[relative] = digest
    require(expected.get("frozen_hash_manifest") == {
        "file":"tests/cross-case-handover-frozen-hashes.json",
        "sha256":FROZEN_HASH_MANIFEST_SHA256,
        "file_count":FROZEN_FILE_COUNT,
    }, "expected oracle does not bind the exact frozen baseline")
    return observed


def validate_artifacts(
    expected: dict[str, Any],
    matrix: dict[str, Any],
    manifest: dict[str, Any],
    expected_sha: str,
    matrix_sha: str,
    manifest_sha: str,
    *,
    root: Path,
    pinned_plan: Path,
    require_nine_groups: bool = True,
    check_frozen_oracle_hashes: bool = True,
) -> tuple[int, dict[str, str]]:
    verify_artifact_digests(expected_sha, matrix_sha, manifest_sha)
    total = validate_structure(expected, matrix, require_nine_groups)
    validate_fixed_outcomes(expected)
    source_digests = verify_pinned_sources(root, expected, pinned_plan)
    verify_source_overlaps(expected, root)
    frozen = verify_frozen_hashes(root, manifest, expected) if check_frozen_oracle_hashes else {}
    return total, {**source_digests, **frozen}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expected", type=Path, required=True)
    parser.add_argument("--matrix", type=Path, required=True)
    parser.add_argument("--hash-manifest", type=Path, required=True)
    parser.add_argument("--pinned-plan", type=Path, required=True)
    parser.add_argument("--require-nine-groups", action="store_true")
    parser.add_argument("--check-frozen-oracle-hashes", action="store_true")
    args = parser.parse_args(argv)
    root = Path(__file__).resolve().parents[1]
    try:
        expected, expected_sha = _load_json(args.expected)
        matrix, matrix_sha = _load_json(args.matrix)
        manifest, manifest_sha = _load_json(args.hash_manifest)
        total, hashes = validate_artifacts(
            expected, matrix, manifest, expected_sha, matrix_sha, manifest_sha,
            root=root,
            pinned_plan=args.pinned_plan,
            require_nine_groups=args.require_nine_groups,
            check_frozen_oracle_hashes=args.check_frozen_oracle_hashes,
        )
    except ContractFailure as exc:
        print(f"CROSS_CASE_CONTRACT_FAILED: {exc}", file=sys.stderr)
        return 1
    print(json.dumps({
        "status":"passed",
        "groups":len(expected["cases"]),
        "mapped_assertions":total,
        "expected_sha256":expected_sha,
        "matrix_sha256":matrix_sha,
        "hash_manifest_sha256":manifest_sha,
        "pinned_hashes_verified":len(hashes),
        "candidate_output_read":False,
        "live_database_claim":False,
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

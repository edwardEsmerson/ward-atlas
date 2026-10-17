#!/usr/bin/env python3
"""Validate the fixed twelve-outcome evidence-known-time handover contract.

Artifact-only verification: this module never imports product code, reads a
candidate response, or connects to FalkorDB. It checks literal expectations,
their packet/source provenance, every pre-existing oracle/matrix byte hash,
and nonzero matrix mappings. A review receipt is supplemental audit metadata;
it cannot change or replace any pinned expectation.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


EXPECTED_ARTIFACT_SHA256 = "fd72df03c8e865cfe347839074ee8251bf57216deac33fcc9d5a4d1152513f93"
MATRIX_ARTIFACT_SHA256 = "7b8450947696e55af0dc48aaa6d27fd0231f458b0f527b3fc00f643ad1f64f67"
PACKET_FILENAME = "planner-57ab552b3c3f400c9a5011e66e0b37a2.md"
PACKET_SHA256 = "d7752918affd9b1cc15a8e08ffb13f04647db16bf4e5aba037cead29546ac195"
EVENT_PACKET_FILENAME = "planner-4b8f1393a48d40da83d21180edd4667f.md"
EVENT_PACKET_SHA256 = "34a40eb28fe5ea9283d65708533183f67f657bd107ec1139cb82f073e88b5a85"
WITNESS_PACKET_FILENAME = "planner-d0017f7f160844fe9cf5a6036aa5068f.md"
WITNESS_PACKET_SHA256 = "82ee7daadb65238676478e1bea7e1229f3a7dee003d7d25620b7ebbf4c70d36b"
DRIFT_PACKET_FILENAME = "planner-828ef7b6a76e4128b9e1b2ecad33c4a7.md"
DRIFT_PACKET_SHA256 = "21a0b4cd4ae3b5a2fc7a1783a1964bc0bd47d68675a38b61b1a6bd14d606e6b5"
ACCEPTED_EVENT_ORACLE_SHA256 = "8ffa7ee25a52219808656ead871fac4779625300f9eea0eaa9543e8b0c7740e6"
ACCEPTED_EVENT_MATRIX_SHA256 = "e036570a3db09c8b544cb339b2665beeee3d469416c4b5fe69e5be2b3597eb67"
EVENT_SOURCE_RECORDS = [
    {"source_id": "SRC-ASSET-C4-OUTAGE", "title": "Synthetic C4 Outage Event Ledger", "observed_at": "2026-10-14T18:02:00Z"},
    {"source_id": "SRC-ASSET-C4-RESTORE", "title": "Synthetic C4 Restoration Event Ledger", "observed_at": "2026-10-14T19:15:00Z"},
]

# Every JSON oracle or adversarial matrix that existed before this task.
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

REQUIRED_CASES = (
    "cold-empty-is-unknown",
    "outage-at-1914-snapshot",
    "restoration-at-1916-same-valid-time",
    "unknown-at-1801-no-event-leak",
    "role-evidence-known-at-cutoff",
    "outage-link-removal",
    "restoration-source-link-removal",
    "sop-witness-independent-mutation",
    "hypothetical-branch-never-observed-snapshot",
    "confirmed-snapshot-is-immutable-and-comparable",
    "restricted-source-full-view-invariance",
    "offset-validation-reseed-reconnect-read-only-cleanup",
)

PACKET_PREFIXES = (
    "1. **`cold-empty-is-unknown`**",
    "2. **`outage-at-1914-snapshot`**",
    "3. **`restoration-at-1916-same-valid-time`**",
    "4. **`unknown-at-1801-no-event-leak`**",
    "5. **`role-evidence-known-at-cutoff`**",
    "6. **`outage-link-removal`**",
    "7. **`restoration-source-link-removal`**",
    "8. **`sop-witness-independent-mutation`**",
    "9. **`hypothetical-branch-never-observed-snapshot`**",
    "10. **`confirmed-snapshot-is-immutable-and-comparable`**",
    "11. **`restricted-source-full-view-invariance`**",
    "12. **`offset-validation-reseed-reconnect-read-only-cleanup`**",
)

EVENT_PACKET_PREFIXES = (
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

PINNED_SOURCE_ROWS = [
    {"id": "handover-known-time-plan", "file": f"plans/{PACKET_FILENAME}", "sha256": PACKET_SHA256, "outcome_lines": list(range(54, 66))},
    {"id": "inc204-bitemporal-plan", "file": f"plans/{EVENT_PACKET_FILENAME}", "sha256": EVENT_PACKET_SHA256, "outcome_lines": list(range(15, 27))},
    {"id": "handover-witness-plan", "file": f"plans/{WITNESS_PACKET_FILENAME}", "sha256": WITNESS_PACKET_SHA256, "outcome_lines": list(range(34, 43))},
    {"id": "handover-drift-plan", "file": f"plans/{DRIFT_PACKET_FILENAME}", "sha256": DRIFT_PACKET_SHA256, "outcome_lines": list(range(64, 73))},
    {"id": "inc204-bitemporal-expected-results", "file": "tests/conveyor-bitemporal-expected-results.json", "sha256": ACCEPTED_EVENT_ORACLE_SHA256},
    {"id": "inc204-bitemporal-adversarial-matrix", "file": "tests/conveyor-bitemporal-adversarial-matrix.json", "sha256": ACCEPTED_EVENT_MATRIX_SHA256},
    {"id": "original-expected-results", "file": "tests/expected-results.json", "sha256": PINNED_EXISTING["tests/expected-results.json"]},
    {"id": "shift-board-expected-results", "file": "tests/shift-board-expected-results.json", "sha256": PINNED_EXISTING["tests/shift-board-expected-results.json"]},
    {"id": "sop-impact-expected-results", "file": "tests/sop-impact-expected-results.json", "sha256": PINNED_EXISTING["tests/sop-impact-expected-results.json"]},
    {"id": "handover-witness-expected-results", "file": "tests/handover-brief-expected-results.json", "sha256": PINNED_EXISTING["tests/handover-brief-expected-results.json"]},
    {"id": "handover-witness-adversarial-matrix", "file": "tests/handover-brief-adversarial-matrix.json", "sha256": PINNED_EXISTING["tests/handover-brief-adversarial-matrix.json"]},
    {"id": "handover-drift-expected-results", "file": "tests/handover-drift-expected-results.json", "sha256": PINNED_EXISTING["tests/handover-drift-expected-results.json"]},
    {"id": "handover-drift-adversarial-matrix", "file": "tests/handover-drift-adversarial-matrix.json", "sha256": PINNED_EXISTING["tests/handover-drift-adversarial-matrix.json"]},
]

REQUIRED_ASSERTION_IDS = {
    REQUIRED_CASES[0]: {"cold.empty-events", "cold.no-paths", "cold.unknown-not-safety"},
    REQUIRED_CASES[1]: {"outage.exact-time-pair", "outage.exact-known-time", "outage.event-and-source", "outage.two-ordered-paths", "outage.path-provenance", "outage.sop-witness-unresolved"},
    REQUIRED_CASES[2]: {"restore.exact-source-time", "restore.exact-known-time", "restore.no-impact-paths", "restore.sop-remains-unresolved"},
    REQUIRED_CASES[3]: {"unknown.known-time", "unknown.no-event-facts", "unknown.no-id-title-time-leak", "unknown.no-existence-side-channel", "unknown.sop-still-visible"},
    REQUIRED_CASES[4]: {"cutoff.only-known-path", "cutoff.exact-evidence-time", "cutoff.exact-known-time", "cutoff.second-path-at-1805", "cutoff.no-completeness-claim"},
    REQUIRED_CASES[5]: {"outage-edge.exact-owned-edge", "outage-edge-removal-unknown", "outage-edge-reseed-baseline"},
    REQUIRED_CASES[6]: {"restore-edge.exact-owned-edge", "restore-edge-removal-falls-back-to-outage", "restore-edge.reseed"},
    REQUIRED_CASES[7]: {"sop.mutation-scope", "sop.event-path-unchanged", "sop.v3-source-preserved", "sop.reseed"},
    REQUIRED_CASES[8]: {"branch.fixed-branch-ids-times", "branch.valid-time", "branch.knowledge-cutoff", "branch.mainline-unchanged", "branch-not-in-snapshot"},
    REQUIRED_CASES[9]: {"snapshot.exact-review-draft", "snapshot.explicit-confirmation", "snapshot.immutable-saved-object", "snapshot.exact-compare-delta", "snapshot.delta-does-not-rewrite"},
    REQUIRED_CASES[10]: {"acl.operations-scope", "acl.probe-is-pinned", "acl.api-snapshot-dom-invariance", "acl.no-hidden-derived-leak"},
    REQUIRED_CASES[11]: {"time.offset-normalization", "time.normalized-pair", "time.invalid-both-axes", "time.reject-before-mutation", "time.reseed-reconnect", "time.read-only-side-effects", "time.cleanup-only-owned"},
}

REQUIRED_ASSERTION_POINTERS = {
    "cold.empty-events": "/cases/0/expected/event",
    "cold.no-paths": "/cases/0/expected/impact_paths",
    "cold.unknown-not-safety": "/cases/0/expected/absence_of_risk_concluded",
    "outage.exact-time-pair": "/cases/1/expected/valid_at",
    "outage.exact-known-time": "/cases/1/expected/known_at",
    "outage.event-and-source": "/cases/1/expected/event",
    "outage.two-ordered-paths": "/cases/1/expected/impact_paths",
    "outage.path-provenance": "/cases/1/expected/path_evidence",
    "outage.sop-witness-unresolved": "/cases/1/expected/sop_witness",
    "restore.exact-source-time": "/cases/2/expected/event",
    "restore.exact-known-time": "/cases/2/expected/known_at",
    "restore.no-impact-paths": "/cases/2/expected/active_impact_paths",
    "restore.sop-remains-unresolved": "/cases/2/expected/sop_witness",
    "unknown.known-time": "/cases/3/expected/known_at",
    "unknown.no-event-facts": "/cases/3/expected/event",
    "unknown.no-id-title-time-leak": "/cases/3/expected/forbidden_event_literals",
    "unknown.no-existence-side-channel": "/cases/3/expected/event_existence_or_count_hint_disclosed",
    "unknown.sop-still-visible": "/cases/3/expected/sop_witness",
    "cutoff.only-known-path": "/cases/4/expected/visible_paths",
    "cutoff.exact-evidence-time": "/cases/4/expected/path_evidence",
    "cutoff.exact-known-time": "/cases/4/expected/known_at",
    "cutoff.second-path-at-1805": "/cases/4/expected/at_1805_visible_paths",
    "cutoff.no-completeness-claim": "/cases/4/expected/completeness_claim",
    "outage-edge.exact-owned-edge": "/cases/5/expected/remove_only_edge",
    "outage-edge-removal-unknown": "/cases/5/expected/after_removal",
    "outage-edge-reseed-baseline": "/cases/5/expected/reseed_restores_exact_baseline",
    "restore-edge.exact-owned-edge": "/cases/6/expected/remove_only_edge",
    "restore-edge-removal-falls-back-to-outage": "/cases/6/expected/after_removal",
    "restore-edge.reseed": "/cases/6/expected/reseed_restores_exact_restoration_baseline",
    "sop.mutation-scope": "/cases/7/expected/mutations",
    "sop.event-path-unchanged": "/cases/7/expected/baseline_impact_paths",
    "sop.v3-source-preserved": "/cases/7/expected/mutations/2/v3_claim_evidence",
    "sop.reseed": "/cases/7/expected/reseed_restores_exact_sop_witness_and_event",
    "branch.fixed-branch-ids-times": "/cases/8/expected/branch",
    "branch.valid-time": "/cases/8/expected/valid_at",
    "branch.knowledge-cutoff": "/cases/8/expected/known_at",
    "branch.mainline-unchanged": "/cases/8/expected/observed_mainline_paths",
    "branch-not-in-snapshot": "/cases/8/expected/branch_in_confirmed_observed_snapshot",
    "snapshot.exact-review-draft": "/cases/9/expected/draft",
    "snapshot.explicit-confirmation": "/cases/9/expected/confirmation",
    "snapshot.immutable-saved-object": "/cases/9/expected/saved_snapshot",
    "snapshot.exact-compare-delta": "/cases/9/expected/comparison",
    "snapshot.delta-does-not-rewrite": "/cases/9/expected/saved_snapshot_byte_identical_after_graph_change_and_compare",
    "acl.operations-scope": "/cases/10/expected/viewer_scope",
    "acl.probe-is-pinned": "/cases/10/expected/restricted_probe",
    "acl.api-snapshot-dom-invariance": "/cases/10/expected/complete_relevant_browser_dom_and_accessibility_text_equal_before_after_probe",
    "acl.no-hidden-derived-leak": "/cases/10/expected/hidden_identifier_title_fact_count_existence_relationship_path_or_dependent_paraphrase_disclosed",
    "time.offset-normalization": "/cases/11/expected/offset_pair",
    "time.normalized-pair": "/cases/11/expected/normalized_pair",
    "time.invalid-both-axes": "/cases/11/expected/invalid_probes",
    "time.reject-before-mutation": "/cases/11/expected/each_invalid_axis_rejected_before_graph_output_or_action_mutation",
    "time.reseed-reconnect": "/cases/11/expected/reconnected_snapshot_matches",
    "time.read-only-side-effects": "/cases/11/expected/time_selection_graph_writes",
    "time.cleanup-only-owned": "/cases/11/expected/cleanup_deletes_only_task_owned_graph_and_stops_only_task_owned_processes",
}


class ContractFailure(ValueError):
    """Raised if an immutable outcome, source pin, or matrix mapping drifts."""


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ContractFailure(message)


def load_json(path: Path) -> tuple[dict[str, Any], str]:
    try:
        raw = path.read_bytes()
        value = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ContractFailure(f"cannot load JSON artifact {path}: {exc}") from exc
    require(isinstance(value, dict), f"JSON artifact {path} must be an object")
    return value, hashlib.sha256(raw).hexdigest()


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


def _case_map(expected: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {case["id"]: case["expected"] for case in expected.get("cases", [])}


def _expected_pointer_sets(matrix: dict[str, Any]) -> dict[str, set[str]]:
    result: dict[str, set[str]] = {}
    for case in matrix.get("cases", []):
        rows = case.get("pinned_assertions", [])
        result[case.get("id", "")] = {row.get("expected_pointer") for row in rows if isinstance(row, dict)}
    return result


def validate_structure(expected: dict[str, Any], matrix: dict[str, Any], require_twelve_groups: bool) -> int:
    require(expected.get("schema_version") == 1 and matrix.get("schema_version") == 1, "schema_version must be 1")
    require(expected.get("product") == "WardAtlas", "unexpected product")
    policy = expected.get("oracle_policy", "").lower()
    require("candidate" in policy and "never supplies or amends" in policy, "candidate output cannot be an oracle")
    scope = expected.get("scope")
    require(isinstance(scope, dict) and scope.get("synthetic_only") is True, "oracle must remain synthetic-only")
    require(scope.get("clinical_decisions") is False and scope.get("external_writes") is False, "clinical or external writes must remain forbidden")
    require(scope.get("candidate_output_is_oracle") is False, "candidate output must not supply expected values")
    require("actual falkordb" in str(scope.get("database_requirement", "")).lower(), "actual FalkorDB remains required for later behavior checks")
    require(matrix.get("oracle_file") == "tests/handover-known-time-expected-results.json", "matrix points at another oracle")
    require(expected.get("pinned_sources") == PINNED_SOURCE_ROWS, "pinned plan/source hashes changed")
    require(expected.get("frozen_existing_oracle_hashes") == [
        {"file": path, "sha256": digest} for path, digest in sorted(PINNED_EXISTING.items())
    ], "frozen pre-task oracle/matrix hashes changed")

    scenarios, matrix_cases = expected.get("cases"), matrix.get("cases")
    require(isinstance(scenarios, list) and isinstance(matrix_cases, list), "expected and matrix cases must be arrays")
    if require_twelve_groups:
        require(len(scenarios) == 12 and len(matrix_cases) == 12, "--require-twelve-groups requires exactly twelve groups")
    ids = [case.get("id") for case in scenarios]
    matrix_ids = [case.get("id") for case in matrix_cases]
    require(tuple(ids) == REQUIRED_CASES, "the twelve fixed outcome groups or their order changed")
    require(matrix_ids == list(REQUIRED_CASES), "matrix cases must map one-to-one in order")
    total = 0
    for index, (scenario, row) in enumerate(zip(scenarios, matrix_cases), start=1):
        case_id = REQUIRED_CASES[index - 1]
        require(row.get("scenario_id") == case_id, f"{case_id} maps to the wrong scenario")
        require(isinstance(scenario.get("expected"), dict) and scenario["expected"], f"{case_id} has no fixed expected object")
        require(scenario.get("planner_refs") == [f"handover-known-time-plan#outcome-{index}"], f"{case_id} expected packet mapping changed")
        require(row.get("planner_refs") == [f"{PACKET_FILENAME}:{53 + index}"], f"{case_id} matrix packet line changed")
        assertions = row.get("pinned_assertions")
        require(isinstance(assertions, list) and assertions, f"{case_id} has no nonzero assertion mappings")
        assertion_ids = [assertion.get("id") for assertion in assertions if isinstance(assertion, dict)]
        pointers = [assertion.get("expected_pointer") for assertion in assertions if isinstance(assertion, dict)]
        require(len(assertion_ids) == len(assertions) and all(isinstance(value, str) and value for value in assertion_ids), f"{case_id} has malformed assertion IDs")
        require(len(assertion_ids) == len(set(assertion_ids)), f"{case_id} assertion IDs are not unique")
        require(set(assertion_ids) == REQUIRED_ASSERTION_IDS[case_id], f"{case_id} assertion set changed")
        require(all(REQUIRED_ASSERTION_POINTERS.get(assertion_id) == pointer for assertion_id, pointer in zip(assertion_ids, pointers)), f"{case_id} assertion-to-outcome mapping changed")
        require(all(isinstance(pointer, str) and pointer for pointer in pointers), f"{case_id} contains an empty expected mapping")
        require(len(pointers) == len(set(pointers)), f"{case_id} maps an expected field more than once")
        for pointer in pointers:
            _resolve_pointer(expected, pointer)
        require(type(row.get("nonzero_assertion_count")) is int and row["nonzero_assertion_count"] == len(assertions) > 0, f"{case_id} must map a matching nonzero assertion count")
        require(all(isinstance(row.get(field), str) and row[field] for field in ("attack_or_edge_case", "setup", "failure_detected")), f"{case_id} lacks adversarial setup/failure semantics")
        total += len(assertions)
    require(total > 0, "matrix must contain a nonzero assertion total")
    return total


def _plan_path(plan_dir: Path, root: Path, relative: str) -> Path:
    if relative.startswith("plans/"):
        return plan_dir / Path(relative).name
    return root / relative


def verify_pinned_plans(pinned_plan: Path, expected: dict[str, Any]) -> dict[str, str]:
    require(pinned_plan.name == PACKET_FILENAME, f"--pinned-plan must be {PACKET_FILENAME}")
    digests: dict[str, str] = {}
    expected_prefixes = {
        "handover-known-time-plan": PACKET_PREFIXES,
        "inc204-bitemporal-plan": EVENT_PACKET_PREFIXES,
    }
    for source in PINNED_SOURCE_ROWS[:4]:
        path = _plan_path(pinned_plan.parent, Path(__file__).resolve().parents[1], source["file"])
        try:
            raw = path.read_bytes()
            text = raw.decode("utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            raise ContractFailure(f"cannot read pinned plan {path}: {exc}") from exc
        digest = hashlib.sha256(raw).hexdigest()
        require(digest == source["sha256"], f"pinned plan {source['id']} bytes changed")
        lines = text.splitlines()
        if source["id"] in expected_prefixes:
            prefixes = expected_prefixes[source["id"]]
            for line_no, prefix in zip(source["outcome_lines"], prefixes):
                require(len(lines) >= line_no and lines[line_no - 1].startswith(prefix), f"{source['id']} outcome heading changed at line {line_no}")
        digests[source["id"]] = digest
    return digests


def verify_existing_oracles(root: Path, expected: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Verify and load all pre-existing oracle/matrix files by fixed byte hash."""
    require(expected.get("frozen_existing_oracle_hashes") == [
        {"file": path, "sha256": digest} for path, digest in sorted(PINNED_EXISTING.items())
    ], "embedded frozen oracle/matrix hash list changed")
    loaded: dict[str, dict[str, Any]] = {}
    for relative, pinned in PINNED_EXISTING.items():
        path = root / relative
        try:
            raw = path.read_bytes()
        except OSError as exc:
            raise ContractFailure(f"cannot read frozen oracle/matrix {relative}: {exc}") from exc
        actual = hashlib.sha256(raw).hexdigest()
        require(actual == pinned, f"pre-existing oracle/matrix {relative} differs from frozen byte hash")
        if relative.endswith("expected-results.json"):
            try:
                loaded[relative] = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise ContractFailure(f"frozen oracle {relative} is invalid JSON: {exc}") from exc
    return loaded


def verify_accepted_event_time_sources(root: Path) -> dict[str, Any]:
    """Read the newly accepted INC-204 bitemporal oracle, separate from the
    24-file pre-task hash freeze, and fail closed if either accepted artifact
    changes after being merged into main.
    """
    expected, expected_digest = load_json(root / "tests/conveyor-bitemporal-expected-results.json")
    require(expected_digest == ACCEPTED_EVENT_ORACLE_SHA256, "accepted INC-204 bitemporal expected-results source hash changed")
    matrix, matrix_digest = load_json(root / "tests/conveyor-bitemporal-adversarial-matrix.json")
    require(matrix_digest == ACCEPTED_EVENT_MATRIX_SHA256, "accepted INC-204 bitemporal matrix source hash changed")
    require(matrix.get("oracle_file") == "tests/conveyor-bitemporal-expected-results.json", "accepted event matrix points to another oracle")
    return expected


def _normalize_utc(value: str) -> str:
    try:
        stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (AttributeError, ValueError) as exc:
        raise ContractFailure(f"invalid pinned timestamp {value!r}") from exc
    require(stamp.tzinfo is not None and stamp.utcoffset() is not None, "pinned timestamps must include timezone offsets")
    return stamp.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def validate_fixed_outcomes(expected: dict[str, Any], root: Path) -> None:
    """Cross-check every pinned INC-204 value with accepted immutable oracles."""
    loaded = verify_existing_oracles(root, expected)
    original = loaded["tests/expected-results.json"]
    shift = loaded["tests/shift-board-expected-results.json"]
    sop = loaded["tests/sop-impact-expected-results.json"]
    witness = loaded["tests/handover-brief-expected-results.json"]
    drift = loaded["tests/handover-drift-expected-results.json"]
    conveyor = verify_accepted_event_time_sources(root)
    cases = _case_map(expected)
    shift_cases = {row["id"]: row["expected"] for row in shift["cases"]}
    witness_cases = {row["id"]: row["expected"] for row in witness["cases"]}
    drift_cases = {row["id"]: row["expected"] for row in drift["cases"]}
    paths = original["expected_paths"]
    path_evidence = original["expected_evidence_by_path"]
    sop_witness = witness["canonical_snapshot"]["sop_witness"]
    outage = shift_cases["conveyor-observed-and-hypothetical-times"]["observed_at_1830"]
    restoration = shift_cases["conveyor-observed-and-hypothetical-times"]["observed_restoration_1930"]
    branch = shift_cases["conveyor-observed-and-hypothetical-times"]["hypothetical_branch_1850"]
    conveyor_cases = {row["id"]: row["expected"] for row in conveyor["cases"]}

    source_facts = expected.get("source_record_facts")
    require(source_facts == {
        "file": "scripts/wardatlas_shift_board.py",
        "provenance": "Static synthetic Evidence node declarations from the pre-existing INC-204 fixture; verified as literal source facts, never executed and never read from candidate output.",
        "records": EVENT_SOURCE_RECORDS,
    }, "event source-record provenance or its fixed literals changed")
    try:
        fixture_text = (root / "scripts/wardatlas_shift_board.py").read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise ContractFailure(f"cannot read static synthetic event-source fixture: {exc}") from exc
    record_pattern = re.compile(
        r"""_node\(\s*[\"']Evidence[\"']\s*,\s*[\"'](?P<id>[^\"']+)[\"']\s*,\s*title\s*=\s*[\"'](?P<title>[^\"']+)[\"']\s*,\s*observed_at\s*=\s*[\"'](?P<observed>[^\"']+)[\"']""",
        re.DOTALL,
    )
    fixture_records = {
        match.group("id"): {"source_id": match.group("id"), "title": match.group("title"), "observed_at": match.group("observed")}
        for match in record_pattern.finditer(fixture_text)
    }
    require([fixture_records.get(row["source_id"]) for row in EVENT_SOURCE_RECORDS] == EVENT_SOURCE_RECORDS, "event source ID/title/observation-time literals differ from the static synthetic fixture")

    require(expected["fixture"] == {
        "incident_id": "INC-204", "equipment_id": "CONVEYOR-C4", "service_id": "SVC-MATERIAL-TRANSFER",
        "valid_at": "2026-10-14T19:30:00Z", "known_before_restoration_at": "2026-10-14T19:14:00Z",
        "known_after_restoration_at": "2026-10-14T19:16:00Z", "unknown_known_at": "2026-10-14T18:01:00Z",
        "partial_path_known_at": "2026-10-14T18:04:00Z", "second_path_known_at": "2026-10-14T18:05:00Z",
        "outage_event_id": outage["event_id"], "outage_source_id": outage["source_id"],
        "restoration_event_id": restoration["restoration_event_id"], "restoration_source_id": restoration["restoration_source_id"],
        "role_paths": paths,
    }, "fixed INC-204 fixture identities or valid/known instants changed")

    outage_event = {
        "event_id": "EVT-C4-UNAVAILABLE-01", "value": "unavailable", "event_kind": "observed",
        "event_at": "2026-10-14T18:02:00Z", "evidence_label": "observed source fact",
        "source": {"source_id": "SRC-ASSET-C4-OUTAGE", "title": "Synthetic C4 Outage Event Ledger", "observed_at": "2026-10-14T18:02:00Z"},
    }
    restoration_event = {
        "event_id": "EVT-C4-RESTORED-01", "value": "available", "event_kind": "observed",
        "event_at": "2026-10-14T19:15:00Z", "evidence_label": "observed source fact",
        "source": {"source_id": "SRC-ASSET-C4-RESTORE", "title": "Synthetic C4 Restoration Event Ledger", "observed_at": "2026-10-14T19:15:00Z"},
    }
    snapshot = {
        "as_of": "2026-10-14T19:30:00Z", "known_at": "2026-10-14T19:14:00Z", "event": outage_event,
        "impact": {"status": "observed-impact", "paths": paths, "path_evidence": path_evidence}, "sop_witness": sop_witness,
    }
    require(expected["canonical_snapshot_1914"] == snapshot, "canonical 19:14Z-known snapshot differs from fixed sources")
    outage_case = cases[REQUIRED_CASES[1]]
    require(outage_case["valid_at"] == "2026-10-14T19:30:00Z" and outage_case["known_at"] == "2026-10-14T19:14:00Z", "outage snapshot axes changed")
    require(outage_case["event"] == outage_event and outage_case["impact_paths"] == paths and outage_case["path_evidence"] == path_evidence, "outage event/source/path provenance changed")
    require(outage_case["sop_witness"] == sop_witness and outage_case["sop_conflict_status"] == "unresolved", "outage snapshot SOP witness must remain the accepted unresolved witness")
    require(outage["event_id"] == outage_event["event_id"] and outage["source_id"] == outage_event["source"]["source_id"] and outage["event_at"] == outage_event["event_at"], "outage event differs from accepted shift-board source oracle")
    require(outage["paths"] == paths and outage["canonical_path_evidence"] == path_evidence, "INC-204 path order/provenance differs from original and shift-board oracles")
    accepted_outage = conveyor_cases["outage-known-before-restoration"]
    require(accepted_outage["conveyor_at"] == outage_case["valid_at"] and accepted_outage["conveyor_known_at"] == outage_case["known_at"] and accepted_outage["state_status"] == outage_case["impact_status"], "outage valid/known axes or state differ from accepted INC-204 bitemporal oracle")
    require(accepted_outage["selected_event"] == {"event_id": outage_event["event_id"], "source_id": outage_event["source"]["source_id"], "event_at": outage_event["event_at"]} and accepted_outage["impact_paths"] == outage_case["impact_paths"] and accepted_outage["path_evidence"] == outage_case["path_evidence"], "outage event/path evidence differs from accepted INC-204 bitemporal oracle")

    restore_case = cases[REQUIRED_CASES[2]]
    require(restore_case["valid_at"] == "2026-10-14T19:30:00Z" and restore_case["known_at"] == "2026-10-14T19:16:00Z", "restoration axes changed")
    require(restore_case["event"] == restoration_event and restore_case["active_impact_paths"] == [], "known restoration event or empty active path result changed")
    require(restore_case["sop_witness"] == sop_witness and restore_case["sop_conflict_status"] == "unresolved" and restore_case["sop_witness_unchanged"] is True, "restoration must not resolve or alter the SOP witness")
    require(restoration["restoration_event_id"] == restoration_event["event_id"] and restoration["restoration_source_id"] == restoration_event["source"]["source_id"] and restoration["restoration_event_at"] == restoration_event["event_at"], "restoration fact differs from accepted shift-board source oracle")
    accepted_restoration = conveyor_cases["restoration-known-at-same-valid-time"]
    require(accepted_restoration["conveyor_at"] == restore_case["valid_at"] and accepted_restoration["conveyor_known_at"] == restore_case["known_at"] and accepted_restoration["state_status"] == restore_case["impact_status"], "restoration valid/known axes or state differ from accepted bitemporal oracle")
    require(accepted_restoration["selected_event"] == {"event_id": restoration_event["event_id"], "source_id": restoration_event["source"]["source_id"], "event_at": restoration_event["event_at"]} and accepted_restoration["active_paths"] == restore_case["active_impact_paths"], "restoration source/path result differs from accepted bitemporal oracle")
    drift_restore = drift_cases["handover-drift-observed-restoration-delta"]["restoration_source_fact"]
    require(drift_restore == {"event_id": restoration_event["event_id"], "source_id": restoration_event["source"]["source_id"], "event_at": restoration_event["event_at"], "status": "observed"}, "restoration source differs from immutable handover-drift oracle")

    unknown = cases[REQUIRED_CASES[3]]
    hidden_literals = [outage_event["event_id"], outage_event["source"]["source_id"], outage_event["source"]["title"], outage_event["event_at"], restoration_event["event_id"], restoration_event["source"]["source_id"], restoration_event["source"]["title"], restoration_event["event_at"]]
    require(unknown["valid_at"] == "2026-10-14T19:30:00Z" and unknown["known_at"] == "2026-10-14T18:01:00Z", "unknown-at-18:01 time pair changed")
    require(unknown["event_state"] == "unknown" and unknown["event"] is None and unknown["impact_paths"] == [], "unknown cutoff must not guess a state or paths")
    require(unknown["forbidden_event_literals"] == hidden_literals and unknown["event_projection_discloses_forbidden_literal"] is False and unknown["event_existence_or_count_hint_disclosed"] is False, "unknown event/source no-leak expectation changed")
    require(all(unknown[field] is False for field in ("availability_concluded", "safety_concluded", "absence_of_risk_concluded")), "unknown event may not imply safety, availability, or no risk")
    require(unknown["sop_witness"] == sop_witness and unknown["sop_conflict_status"] == "unresolved", "unknown event must retain independently known unresolved SOP evidence")
    accepted_unknown = conveyor_cases["no-known-event-unknown-no-leak"]
    require(accepted_unknown["conveyor_at"] == unknown["valid_at"] and accepted_unknown["conveyor_known_at"] == unknown["known_at"] and accepted_unknown["state_status"] == unknown["event_state"] and accepted_unknown["selected_event"] is None and accepted_unknown["impact_paths"] == [], "unknown cutoff differs from the accepted bitemporal oracle")
    require(accepted_unknown["forbidden_disclosure_categories"] == ["outage/restoration event identifiers", "event-source identifiers or titles", "event/source timestamps", "hidden event/source counts", "event/source existence hints"] and accepted_unknown["unseen_event_or_source_hint_emitted"] is False and accepted_unknown["no_risk_conclusion"] is False, "unknown event no-leak/no-risk boundary differs from accepted bitemporal oracle")

    cutoff = cases[REQUIRED_CASES[4]]
    require(cutoff["valid_at"] == "2026-10-14T19:30:00Z" and cutoff["known_at"] == "2026-10-14T18:04:00Z", "per-path cutoff pair changed")
    require(cutoff["event"] == outage_event and cutoff["visible_paths"] == [paths[0]] and cutoff["path_evidence"] == [path_evidence[0]], "18:04Z must expose only the first exact evidence-backed path")
    require(cutoff["path_observed_at_cutoffs"] == path_evidence[0]["observed_at"], "first path's per-edge evidence cutoff changed")
    require(cutoff["at_1805_visible_paths"] == paths and cutoff["at_1805_path_evidence"] == path_evidence, "second path must appear only at its exact 18:05Z evidence boundary")
    require(cutoff["excluded_role_or_source_literals"] == ["ROLE-MATERIALS-DUTY", "2026-10-14T18:05:00Z"] and cutoff["omitted_second_path_and_evidence_disclosed"] is False and cutoff["missing_path_count_disclosed"] is False and cutoff["completeness_claim"] is False, "partial path result must not leak a later path/count or claim completeness")
    accepted_cutoff = conveyor_cases["per-path-evidence-cutoff"]
    require(accepted_cutoff["conveyor_at"] == cutoff["valid_at"] and accepted_cutoff["conveyor_known_at"] == cutoff["known_at"] and accepted_cutoff["impact_paths"] == cutoff["visible_paths"] and accepted_cutoff["path_evidence"] == cutoff["path_evidence"], "partial path/time boundary differs from accepted bitemporal oracle")
    require(accepted_cutoff["excluded_later_ownership"] == {"relationship": "OWNED_BY", "role_id": "ROLE-MATERIALS-DUTY", "observed_at": "2026-10-14T18:05:00Z"} and accepted_cutoff["missing_path_count_emitted"] is False and accepted_cutoff["completeness_claim_emitted"] is False, "partial path omission/count/completeness semantics differ from accepted bitemporal oracle")

    outage_remove = cases[REQUIRED_CASES[5]]
    require(outage_remove["remove_only_edge"] == {"from": "INC-204", "relationship": "HAS_STATE_EVENT", "to": outage_event["event_id"], "relationship_id": "REL-C4-UNAVAILABLE-EVENT", "ownership": "task-owned relation only"}, "outage counterfactual must remove exactly its owned incident/event edge")
    require(outage_remove["after_removal"] == {"event_state": "unknown", "event": None, "impact_paths": [], "outage_recreated_from_static_or_time_order": False} and outage_remove["reseed_restores_exact_baseline"] is True, "outage-edge removal/reseed expectations changed")
    accepted_outage_removal = conveyor_cases["outage-event-edge-removal"]
    require(accepted_outage_removal["remove_only_edge"]["from"] == outage_remove["remove_only_edge"]["from"] and accepted_outage_removal["remove_only_edge"]["relationship"] == outage_remove["remove_only_edge"]["relationship"] and accepted_outage_removal["remove_only_edge"]["to"] == outage_remove["remove_only_edge"]["to"] and accepted_outage_removal["after_removal"]["state_status"] == "unknown" and accepted_outage_removal["reseed_restores_exact_baseline"] is True, "outage-link mutation differs from accepted bitemporal counterfactual")
    restore_remove = cases[REQUIRED_CASES[6]]
    require(restore_remove["remove_only_edge"] == {"from": restoration_event["event_id"], "relationship": "EVIDENCED_BY", "to": restoration_event["source"]["source_id"], "relationship_id": "REL-C4-RESTORED-SOURCE", "ownership": "task-owned relation only"}, "restoration counterfactual must remove exactly its owned evidence edge")
    require(restore_remove["after_removal"] == {"event": outage_event, "impact_paths": paths, "path_evidence": path_evidence, "restoration_details_emitted": False} and restore_remove["reseed_restores_exact_restoration_baseline"] is True, "unsupported restoration must fall back to the last source-backed outage")
    accepted_restore_removal = conveyor_cases["restoration-evidence-edge-removal"]
    require(accepted_restore_removal["remove_only_edge"]["from"] == restore_remove["remove_only_edge"]["from"] and accepted_restore_removal["remove_only_edge"]["relationship"] == restore_remove["remove_only_edge"]["relationship"] and accepted_restore_removal["remove_only_edge"]["to"] == restore_remove["remove_only_edge"]["to"] and accepted_restore_removal["after_removal"]["selected_event"] == {"event_id": outage_event["event_id"], "source_id": outage_event["source"]["source_id"], "event_at": outage_event["event_at"]} and accepted_restore_removal["after_removal"]["impact_paths"] == paths and accepted_restore_removal["reseed_restores_exact_baseline"] is True, "restoration evidence mutation differs from accepted bitemporal counterfactual")

    sop_change = cases[REQUIRED_CASES[7]]
    require(sop_change["baseline_sop_witness"] == sop_witness and sop_change["baseline_impact_paths"] == paths and sop_change["baseline_path_evidence"] == path_evidence, "SOP mutation baseline changed")
    mutations = sop_change["mutations"]
    require(len(mutations) == 3, "SOP witness contract must include applicability, conflict, and source-edge mutations")
    require(mutations[0]["remove_only_edge"] == {"from": "SOP-OPS-9-V2", "relationship": "APPLIES_TO", "to": "CONVEYOR-C4", "relationship_id": "REL-SOP-V2-APPLIES-C4", "ownership": "task-owned relation only"} and mutations[0]["sop_linkage_status"] == "incomplete/unknown" and mutations[0]["event_and_paths_unchanged"] is True, "SOP applicability removal must only make the witness incomplete")
    require(mutations[1]["remove_only_edge"] == {"from": "SOP-OPS-9-V2", "relationship": "SOP_CONFLICT", "to": "SOP-OPS-9-V3", "relationship_id": "REL-SOP-9-CONFLICT", "ownership": "task-owned relation only"} and mutations[1]["sop_linkage_status"] == "unknown" and mutations[1]["source_claims_preserved"] is True and mutations[1]["event_and_paths_unchanged"] is True, "SOP conflict edge removal must be unknown, not no-conflict")
    require(mutations[2]["remove_only_source_and_evidence_edge"] == {"source_id": "SRC-SOP-CURRENT-V2", "relationship": "EVIDENCED_BY", "from": "SOP-OPS-9-V2", "relationship_id": "REL-SOP-V2-EVIDENCE", "ownership": "task-owned source and edge only"}, "SOP source counterfactual edge changed")
    expected_v3_source = {key: sop_witness["claim_evidence"][1][key] for key in ("source_id", "source_title", "observed_at")}
    require(mutations[2]["v2_claim_evidence"] == "missing/unlinked" and mutations[2]["v3_claim_evidence"] == expected_v3_source and mutations[2]["conflict_status"] == "unresolved" and mutations[2]["event_and_paths_unchanged"] is True, "removing V2 source must preserve unresolved conflict, V3 evidence, and event/path provenance")
    require(sop_change["reseed_restores_exact_sop_witness_and_event"] is True, "SOP reseed must restore fixed witness without changing event state")
    sop_cases = {row["id"]: row["expected"] for row in sop["cases"]}
    sop_provenance = sop_cases["sop-impact-conflict-provenance"]
    require(sop_provenance["conflict_status"] == "unresolved" and sop_provenance["claim_evidence"] == sop_witness["claim_evidence"] and sop_provenance["applicability_edges"] == sop_witness["applicability_edges"] and sop_provenance["conflict_edge"] == sop_witness["conflict_edge"], "SOP witness differs from accepted SOP-impact oracle")
    require(sop_cases["sop-impact-applicability-edge-counterfactual"]["outage_paths_after_removal"] == paths, "SOP mutation expectations conflict with accepted SOP-impact path oracle")

    branch_case = cases[REQUIRED_CASES[8]]
    accepted_branch = conveyor_cases["observed-versus-hypothetical-branch"]
    require(branch_case["valid_at"] == accepted_branch["branch_at"] == branch["at"] and branch_case["known_at"] == accepted_branch["conveyor_known_at"] == "2026-10-14T18:04:00Z", "hypothetical branch valid/knowledge axes differ from accepted bitemporal source")
    require(branch_case["branch"] == accepted_branch["branch"] and accepted_branch["branch"]["branch_id"] == branch["branch_id"] and accepted_branch["branch"]["event_id"] == branch["hypothetical_event_id"] and accepted_branch["branch"]["event_at"] == branch["hypothetical_event_at"], "hypothetical branch identifiers/times differ from accepted bitemporal and shift-board sources")
    require(branch_case["branch_is_labeled_hypothetical"] is True and branch_case["branch_event_is_observed_fact"] is False and branch_case["observed_mainline_event"] == {"event_id": outage_event["event_id"], "source_id": outage_event["source"]["source_id"], "event_at": outage_event["event_at"]}, "branch must remain distinct from the observed event")
    require(branch_case["observed_mainline_status"] == accepted_branch["observed_mainline"]["status"] and branch_case["observed_mainline_paths"] == accepted_branch["observed_mainline"]["impact_paths"] and branch_case["observed_mainline_path_evidence"] == accepted_branch["observed_mainline"]["path_evidence"] and branch_case["legacy_current_knowledge_mainline_paths"] == accepted_branch["legacy_current_knowledge_mainline_paths"] and branch_case["observed_mainline_mutated"] is accepted_branch["branch_changes_observed_history"] is False, "branch baseline differs from the exact accepted knowledge-cutoff query")
    require(branch_case["branch_in_confirmed_observed_snapshot"] is False and branch_case["confirmed_snapshot"] == snapshot, "hypothetical branch must never enter the independently confirmed observed snapshot")

    delta = {
        "as_of": "2026-10-14T19:30:00Z", "known_at": "2026-10-14T19:16:00Z", "current_status": "restored",
        "current_event": restoration_event, "current_active_paths": [], "removed_paths": paths, "added_paths": [], "new_role_paths": [],
        "sop_witness": sop_witness, "sop_conflict_status": "unresolved", "sop_witness_unchanged": True,
        "historical_snapshot": snapshot, "saved_snapshot_unchanged": True,
        "safe_conclusion_asserted": False, "no_operational_risk_conclusion_asserted": False,
    }
    require(expected["restoration_delta_1916"] == delta, "fixed later-known restoration comparison delta changed")
    handover_case = cases[REQUIRED_CASES[9]]
    require(handover_case["draft"] == snapshot and handover_case["saved_snapshot"] == snapshot and handover_case["saved_snapshot_equals_reviewed_draft"] is True, "preview/confirmed snapshot differs from the exact 19:14Z-known graph snapshot")
    require(handover_case["draft_graph_writes"] == 0 and handover_case["draft_local_action_writes"] == 0 and handover_case["confirmation"] == {"required": True, "gesture": "explicit local in-app confirmation", "external_writes": [], "messages_sent": False, "calls_made": False, "tickets_created": False} and handover_case["saved_action_count_after_one_confirmation"] == 1, "snapshot requires one explicit local confirmation and no external side effect")
    require(handover_case["comparison"] == delta and handover_case["saved_snapshot_after_compare"] == snapshot and handover_case["saved_snapshot_byte_identical_after_graph_change_and_compare"] is True and handover_case["compare_graph_writes"] == 0 and handover_case["compare_action_writes"] == 0, "exact compare delta or immutable saved snapshot changed")
    old_handover = witness_cases["read-only-preview-and-explicit-local-confirmation"]
    require(old_handover["explicit_confirmation_required"] is True and old_handover["external_writes"] == [] and old_handover["saved_snapshot_equals_preview"] is True, "confirmation semantics differ from accepted handover-witness oracle")
    old_delta = drift_cases["handover-drift-observed-restoration-delta"]
    require(old_delta["removed_paths"] == paths and old_delta["added_paths"] == [] and old_delta["sop_conflict_status"] == "unresolved" and old_delta["saved_action_unchanged"] is True, "exact delta differs from accepted handover-drift oracle")

    acl = cases[REQUIRED_CASES[10]]
    restricted = drift_cases["handover-drift-restricted-source-invariance"]["restricted_probe"]
    require(acl["viewer_scope"] == "operations" and acl["restricted_probe"] == restricted and restricted["acl"] == "restricted", "ACL scope/probe differs from accepted handover source")
    require(all(acl[field] is True for field in ("draft_api_equal_before_after_probe", "saved_snapshot_equal_before_after_probe", "compare_api_equal_before_after_probe", "complete_relevant_browser_dom_and_accessibility_text_equal_before_after_probe", "visible_counts_equal_before_after_probe")), "restricted source must leave every API/saved/DOM/count projection invariant")
    require(acl["restricted_literals_disclosed"] == [] and acl["hidden_identifier_title_fact_count_existence_relationship_path_or_dependent_paraphrase_disclosed"] is False, "restricted source non-disclosure was weakened")

    time_case = cases[REQUIRED_CASES[11]]
    require(time_case["canonical_pair"] == {"valid_at": "2026-10-14T19:30:00Z", "known_at": "2026-10-14T19:14:00Z"}, "canonical valid/known pair changed")
    require(time_case["offset_pair"] == {"valid_at": "2026-10-14T21:30:00+02:00", "known_at": "2026-10-14T21:14:00+02:00"} and time_case["normalized_pair"] == time_case["canonical_pair"], "offset values do not normalize to the exact pinned pair")
    require(_normalize_utc(time_case["offset_pair"]["valid_at"]) == time_case["normalized_pair"]["valid_at"] and _normalize_utc(time_case["offset_pair"]["known_at"]) == time_case["normalized_pair"]["known_at"], "UTC offset normalization is incorrect")
    require(time_case["normalized_result_matches"] == REQUIRED_CASES[1], "normalized query must match the pinned outage snapshot")
    accepted_time = conveyor_cases["time-validation-and-offset-normalization"]
    require(accepted_time["canonical_pair"] == {"conveyor_at": time_case["canonical_pair"]["valid_at"], "conveyor_known_at": time_case["canonical_pair"]["known_at"]} and accepted_time["offset_pair"] == {"conveyor_at": time_case["offset_pair"]["valid_at"], "conveyor_known_at": time_case["offset_pair"]["known_at"]} and accepted_time["normalized_pair"] == {"conveyor_at": time_case["normalized_pair"]["valid_at"], "conveyor_known_at": time_case["normalized_pair"]["known_at"]} and accepted_time["offset_result_matches_case"] == "outage-known-before-restoration", "offset normalization differs from accepted bitemporal oracle")
    require(time_case["invalid_probes"] == [{"axis": "valid_at", "value": "not-a-timestamp"}, {"axis": "valid_at", "value": "2026-10-14T19:30:00"}, {"axis": "known_at", "value": "not-a-timestamp"}, {"axis": "known_at", "value": "2026-10-14T19:14:00"}], "invalid and timezone-naive probes must cover both time axes")
    require(time_case["each_invalid_axis_rejected_before_graph_output_or_action_mutation"] is True and time_case["naive_local_time_fallback"] is False, "invalid times must reject before mutation without local-time fallback")
    require(time_case["reseed_runs"] == 2 and time_case["duplicate_nodes_after_repeat_seed"] == 0 and time_case["duplicate_relationships_after_repeat_seed"] == 0, "reseed must remain idempotent")
    require(time_case["fresh_client_and_restarted_app_required"] is True and time_case["reconnected_snapshot_matches"] == REQUIRED_CASES[1] and time_case["reconnected_compare_matches"] == REQUIRED_CASES[2], "fresh-client/app reconnect outcome changed")
    require(time_case["time_selection_graph_writes"] == 0 and time_case["read_only_action_writes"] == 0, "time selection and read-only queries must not write graph/action state")
    require(time_case["explicit_confirmation_is_separate_human_action"] is True and time_case["desktop_mobile_api_results_agree"] is True, "confirmation/responsive API semantics changed")
    require(time_case["cleanup_deletes_only_task_owned_graph_and_stops_only_task_owned_processes"] is True, "cleanup must remain limited to task-owned resources")
    accepted_lifecycle = conveyor_cases["repeat-reconnect-read-only-and-cleanup"]
    require(accepted_lifecycle["repeat_seed_duplicate_nodes"] == time_case["duplicate_nodes_after_repeat_seed"] == 0 and accepted_lifecycle["repeat_seed_duplicate_relationships"] == time_case["duplicate_relationships_after_repeat_seed"] == 0, "repeat-seed lifecycle differs from accepted event-time oracle")
    require(accepted_lifecycle["after_reconnect_result_matches"] == "outage-known-before-restoration" and time_case["reconnected_snapshot_matches"] == REQUIRED_CASES[1] and accepted_lifecycle["query_graph_writes"] == time_case["time_selection_graph_writes"] == 0 and accepted_lifecycle["query_action_writes"] == time_case["read_only_action_writes"] == 0 and accepted_lifecycle["desktop_mobile_api_results_agree"] is True and accepted_lifecycle["cleanup_deletes_only_task_owned_graph_and_stops_only_owned_processes"] is True, "read-only/reconnect/responsive/cleanup outcomes differ from accepted event-time oracle")


def verify_artifact_digests(expected_digest: str, matrix_digest: str) -> None:
    require(expected_digest == EXPECTED_ARTIFACT_SHA256, "handover-known-time expected-results bytes differ from their fixed oracle hash")
    require(matrix_digest == MATRIX_ARTIFACT_SHA256, "handover-known-time adversarial matrix bytes differ from its fixed hash")


def validate_review_receipt(path: Path, expected_digest: str, matrix_digest: str, assertion_count: int) -> None:
    """Validate a supplemental, self-attested receipt tied to these exact bytes.

    A receipt is not cryptographic proof of reviewer identity or independence.
    It records a claim for an external reviewer to own; it never supplies oracle
    values or relaxes the fixed artifact/source checks.
    """
    receipt, _ = load_json(path)
    require(receipt.get("schema_version") == 1 and receipt.get("status") == "passed", "review receipt must be schema 1/status passed")
    require(receipt.get("reviewer") and receipt.get("independent_of_oracle_author") is True, "review receipt must identify an independent reviewer claim")
    require(receipt.get("candidate_output_used") is False, "review receipt must state that candidate output was not used as the oracle")
    require(receipt.get("expected_sha256") == expected_digest and receipt.get("matrix_sha256") == matrix_digest, "review receipt does not match these exact oracle/matrix bytes")
    require(receipt.get("pinned_plan_sha256") == PACKET_SHA256, "review receipt does not match the pinned packet")
    require(receipt.get("reviewed_case_ids") == list(REQUIRED_CASES), "review receipt does not cover all twelve ordered outcome groups")
    require(type(receipt.get("mapped_assertion_count")) is int and receipt["mapped_assertion_count"] == assertion_count > 0, "review receipt assertion count differs from the matrix")


def validate_artifacts(
    expected: dict[str, Any], matrix: dict[str, Any], expected_digest: str, matrix_digest: str,
    root: Path, pinned_plan: Path, require_twelve_groups: bool,
) -> tuple[int, dict[str, str]]:
    assertion_count = validate_structure(expected, matrix, require_twelve_groups)
    verify_artifact_digests(expected_digest, matrix_digest)
    plan_digests = verify_pinned_plans(pinned_plan, expected)
    verify_existing_oracles(root, expected)
    validate_fixed_outcomes(expected, root)
    return assertion_count, plan_digests


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expected", required=True, type=Path)
    parser.add_argument("--matrix", required=True, type=Path)
    parser.add_argument("--pinned-plan", required=True, type=Path)
    parser.add_argument("--require-twelve-groups", action="store_true")
    parser.add_argument("--check-frozen-oracle-hashes", action="store_true")
    parser.add_argument("--review-receipt", type=Path, help="optional supplemental JSON review receipt tied to these exact artifact hashes")
    args = parser.parse_args(argv)
    if not args.require_twelve_groups or not args.check_frozen_oracle_hashes:
        print("FAIL: --require-twelve-groups and --check-frozen-oracle-hashes are mandatory", file=sys.stderr)
        return 1
    try:
        expected, expected_digest = load_json(args.expected)
        matrix, matrix_digest = load_json(args.matrix)
        count, plan_digests = validate_artifacts(
            expected, matrix, expected_digest, matrix_digest,
            root=Path(__file__).resolve().parents[1], pinned_plan=args.pinned_plan,
            require_twelve_groups=True,
        )
        if args.review_receipt:
            validate_review_receipt(args.review_receipt, expected_digest, matrix_digest, count)
    except (OSError, TypeError, ValueError, KeyError) as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1
    receipt_note = "; supplemental receipt checked (identity is self-attested)" if args.review_receipt else ""
    print(
        "PASS: twelve fixed handover-known-time outcome groups; "
        f"{count} mapped nonzero assertions; pinned plans and all {len(PINNED_EXISTING)} "
        f"pre-task oracle/matrix byte hashes verified{receipt_note}; no candidate output used."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Verify the frozen, candidate-independent decision-provenance contract.

This is an artifact/source contract only. It does not query the product, read
candidate output, or claim that live FalkorDB behavior has been implemented.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Any


PACKET_FILENAME = "planner-22e10d27ab804d3e872feefec257ae38.md"
PACKET_SHA256 = "992dba1b3dbbf4ee51e7c04a6fc1aa865a9460b84c1795fefd5931198bc5e1a0"
PACKET_OUTCOME_LINES = tuple(range(49, 58))
PACKET_OUTCOME_PREFIXES = (
    "1. **Cold empty:**",
    "2. **Exact finite docket:**",
    "3. **Considered-version edge removal:**",
    "4. **Decision-record evidence-link removal:**",
    "5. **Claim evidence-link removal:**",
    "6. **Untracked decoy:**",
    "7. **Restricted-source full-view invariance:**",
    "8. **Reseed idempotence:**",
    "9. **Fresh client, read-only and cleanup:**",
)
EXPECTED_ARTIFACT_SHA256 = "4143bbc135b32ed6fb89444f2c32b01bfd5ca89d50127cea5e4099fdffed5c5d"
MATRIX_ARTIFACT_SHA256 = "256048400ee304f59884ecf3393f9a59e30d76f29281c9c6ea5378740dd71ad3"
FROZEN_MANIFEST_SHA256 = "7e7cd9e87d9cd36bfae26ee49d32dc1efa09c0f1c27bc1660e72467aecd11eab"

SOURCE_ORACLES = {
    "tests/sop-impact-expected-results.json": "f94e65850384e36e7200b321d14628f074fee37fb159adbcd73bef00c6e1acca",
    "tests/handover-brief-expected-results.json": "932ada7d43688b8bd1e4f147ac06dd585982d784ef8577f05cc00b07a97199f1",
}
SOURCE_IDS = {
    "tests/sop-impact-expected-results.json": "sop-impact-source-oracle",
    "tests/handover-brief-expected-results.json": "handover-brief-source-oracle",
}
REQUIRED_GROUPS = (
    "empty-unknown",
    "exact-one-record-baseline",
    "considers-v2-edge-removed",
    "decision-evidence-link-removed",
    "v2-claim-evidence-link-removed",
    "disconnected-decoy-excluded",
    "restricted-source-operations-invariance",
    "idempotent-reseed-restores-baseline",
    "fresh-client-read-only-owned-cleanup",
)
ASSERTION_IDS = {
    "empty-unknown": (
        "empty.no-docket-row", "empty.generic-unknown", "empty.no-decision-identifiers",
        "empty.no-count-or-existence", "empty.no-negative-conclusion", "empty.no-owner-conclusion",
    ),
    "exact-one-record-baseline": (
        "baseline.root-track-edge", "baseline.one-record-only", "baseline.record-state-source",
        "baseline.record-evidence-edge", "baseline.considered-versions", "baseline.v2-claim-provenance",
        "baseline.v3-claim-provenance", "baseline.unresolved-conflict", "baseline.no-authority-or-action",
    ),
    "considers-v2-edge-removed": (
        "considered.only-v2-edge-removed", "considered.incomplete-unknown", "considered.no-complete-pair",
        "considered.no-v3-promotion", "considered.no-resolution-conclusion", "considered.impact-paths-preserved",
    ),
    "decision-evidence-link-removed": (
        "record-evidence.only-own-edge-removed", "record-evidence.source-properties-retained",
        "record-evidence.generic-unknown", "record-evidence.suppress-record-facts",
        "record-evidence.reject-property-citation", "record-evidence.sop-impact-unchanged",
    ),
    "v2-claim-evidence-link-removed": (
        "claim-evidence.only-v2-edge-removed", "claim-evidence.no-v2-property-reconstruction",
        "claim-evidence.v3-source-backed-claim-remains", "claim-evidence.incomplete-unknown",
        "claim-evidence.no-v3-winner", "claim-evidence.incident-paths-unchanged",
        "claim-evidence.only-unsupported-v2-omitted",
    ),
    "disconnected-decoy-excluded": (
        "decoy.no-root-tracks-path", "decoy.not-returned", "decoy.baseline-identical",
    ),
    "restricted-source-operations-invariance": (
        "acl.operations-projection-identical", "acl.counts-identical", "acl.browser-accessibility-identical",
        "acl.no-identifiers-or-source-text", "acl.no-count-existence-or-relationship", "acl.no-derived-paraphrase",
    ),
    "idempotent-reseed-restores-baseline": (
        "reseed.two-loads", "reseed.no-duplicate-identifiers", "reseed.no-duplicate-relationships",
        "reseed.exact-baseline-restored", "reseed.source-order-and-times",
    ),
    "fresh-client-read-only-owned-cleanup": (
        "fresh-client.new-connection", "fresh-client.exact-snapshot", "fresh-client.zero-graph-writes",
        "fresh-client.zero-local-action-writes", "fresh-client.owned-graph-state-cleanup",
        "fresh-client.owned-process-cleanup",
    ),
}

EVIDENCE_ROOT = Path(
    "/workspace/evidence/auto-1c9408a925010178df8b0ebc/3736092c1ef345cbb84880cdbefe8e27/decision-provenance-review"
)
RECEIPT_FILENAME = "independent-review.json"
ORACLE_AUTHOR_ID = "task-author:3736092c1ef345cbb84880cdbefe8e27"


class ContractError(ValueError):
    """Raised when an oracle, source pin, matrix, or review binding drifts."""


def _need(condition: bool, message: str) -> None:
    if not condition:
        raise ContractError(message)


def _strict_equal(actual: Any, expected: Any) -> bool:
    """JSON equality that does not conflate bool/int values."""
    if type(actual) is not type(expected):
        return False
    if isinstance(actual, dict):
        return actual.keys() == expected.keys() and all(
            _strict_equal(actual[key], expected[key]) for key in actual
        )
    if isinstance(actual, list):
        return len(actual) == len(expected) and all(
            _strict_equal(left, right) for left, right in zip(actual, expected)
        )
    return actual == expected


def _same(actual: Any, expected: Any, label: str) -> None:
    if not _strict_equal(actual, expected):
        raise ContractError(f"{label} differs from the independently pinned value")


def canonical_sha256(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _read_json(path: Path, label: str) -> tuple[dict[str, Any], bytes, str]:
    try:
        raw = path.read_bytes()
        document = json.loads(raw.decode("utf-8"))
    except OSError as exc:
        raise ContractError(f"cannot read {label} {path}: {exc}") from exc
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ContractError(f"cannot parse {label} {path}: {exc}") from exc
    _need(isinstance(document, dict), f"{label} must be a JSON object")
    return document, raw, hashlib.sha256(raw).hexdigest()


def _find_case(document: dict[str, Any], collection: str, key: str, value: str, label: str) -> dict[str, Any]:
    rows = document.get(collection)
    _need(isinstance(rows, list), f"{label}.{collection} must be an array")
    matches = [row for row in rows if isinstance(row, dict) and row.get(key) == value]
    _need(len(matches) == 1, f"expected exactly one {label} {key}={value!r}")
    return matches[0]


def lane_evidence_dir(lane: str | None = None) -> Path:
    value = lane if lane is not None else os.environ.get("WORKSHOP_LANE")
    _need(isinstance(value, str) and value.isdigit(), "WORKSHOP_LANE must be a decimal lane identifier")
    return EVIDENCE_ROOT / f"lane-{value}"


def receipt_path_from_workshop_lane(lane: str | None = None) -> Path:
    return lane_evidence_dir(lane) / RECEIPT_FILENAME


def validate_manifest(manifest: dict[str, Any], raw_hash: str, root: Path) -> dict[str, str]:
    _need(raw_hash == FROZEN_MANIFEST_SHA256, "frozen-hashes manifest bytes differ from the pre-task inventory pin")
    _need(manifest.get("schema_version") == 1, "frozen-hashes manifest must use schema_version 1")
    _need(manifest.get("frozen_before_adding_decision_provenance") is True, "manifest must declare pre-addition freeze")
    files = manifest.get("files")
    _need(isinstance(files, list) and len(files) == 30, "manifest must contain all 30 then-existing oracle/matrix files")
    paths = [row.get("path") for row in files if isinstance(row, dict)]
    _need(len(paths) == len(files) and len(paths) == len(set(paths)), "manifest paths must be unique file records")
    _need(paths == sorted(paths), "frozen file inventory must be deterministically sorted")
    observed: dict[str, str] = {}
    for row in files:
        relative = row.get("path")
        digest = row.get("sha256")
        _need(isinstance(relative, str) and relative.startswith("tests/"), "frozen path must be repository-relative under tests/")
        _need(".." not in Path(relative).parts, f"unsafe frozen path: {relative}")
        _need(relative.endswith(("-expected-results.json", "-adversarial-matrix.json")) or relative in {"tests/expected-results.json", "tests/adversarial-matrix.json"}, f"not an expected-results or adversarial-matrix file: {relative}")
        _need(isinstance(digest, str) and len(digest) == 64, f"invalid SHA-256 value for {relative}")
        target = root / relative
        _need(not target.is_symlink(), f"frozen file may not be a symlink: {relative}")
        try:
            observed_hash = hashlib.sha256(target.read_bytes()).hexdigest()
        except OSError as exc:
            raise ContractError(f"cannot read frozen oracle {relative}: {exc}") from exc
        _need(observed_hash == digest, f"preexisting oracle/matrix changed byte-for-byte: {relative}")
        observed[relative] = observed_hash
    _need("tests/decision-provenance-expected-results.json" not in observed, "new expected artifact cannot be included in its pre-addition inventory")
    _need("tests/decision-provenance-adversarial-matrix.json" not in observed, "new matrix cannot be included in its pre-addition inventory")
    return observed


def verify_pinned_packet(path: Path) -> str:
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise ContractError(f"cannot read pinned decision packet {path}: {exc}") from exc
    digest = hashlib.sha256(raw).hexdigest()
    _need(digest == PACKET_SHA256, "decision-provenance packet bytes differ from the independently pinned packet")
    lines = raw.decode("utf-8").splitlines()
    for line_number, prefix in zip(PACKET_OUTCOME_LINES, PACKET_OUTCOME_PREFIXES):
        _need(len(lines) >= line_number and lines[line_number - 1].startswith(prefix), f"packet outcome heading differs at line {line_number}")
    return digest


def _validate_fixed_shapes(expected: dict[str, Any], matrix: dict[str, Any]) -> int:
    _need(expected.get("schema_version") == matrix.get("schema_version") == 1, "expected/matrix schema_version must be 1")
    _need(expected.get("product") == "WardAtlas", "unexpected product")
    _need("candidate responses never" in expected.get("oracle_policy", "").lower(), "oracle policy must prohibit candidate-derived expectations")
    _need(matrix.get("oracle_file") == "tests/decision-provenance-expected-results.json", "matrix points at a different expected-results file")
    _same(expected.get("pinned_sources"), [
        {"id": "decision-provenance-packet", "path": PACKET_FILENAME, "sha256": PACKET_SHA256, "outcome_lines": list(PACKET_OUTCOME_LINES)},
        {"id": SOURCE_IDS["tests/sop-impact-expected-results.json"], "path": "tests/sop-impact-expected-results.json", "sha256": SOURCE_ORACLES["tests/sop-impact-expected-results.json"]},
        {"id": SOURCE_IDS["tests/handover-brief-expected-results.json"], "path": "tests/handover-brief-expected-results.json", "sha256": SOURCE_ORACLES["tests/handover-brief-expected-results.json"]},
    ], "pinned packet and unchanged source identities")
    _same(expected.get("frozen_hash_manifest"), "tests/decision-provenance-frozen-hashes.json", "frozen manifest path")
    scope = expected.get("scope")
    _need(isinstance(scope, dict), "scope must be an object")
    _need(scope.get("synthetic_only") is True and scope.get("clinical_decisions") is False and scope.get("external_writes") is False, "synthetic/non-clinical/no-external-write boundary changed")
    _need(scope.get("candidate_output_is_oracle") is False, "candidate output cannot be the oracle")
    _need("actual FalkorDB" in scope.get("database_requirement", ""), "actual FalkorDB requirement is missing")
    _need("node-to-node EVIDENCED_BY edge" in scope.get("citation_semantics", ""), "citation must require a node-to-node EVIDENCED_BY relationship")
    _need("invariant" in scope.get("acl_semantics", "").lower() and "restricted" in scope.get("acl_semantics", "").lower(), "restricted-source invariance semantics are missing")
    baseline = expected.get("baseline")
    _need(isinstance(baseline, dict), "baseline must be an object")
    decision = baseline.get("decision_record", {})
    record_source = decision.get("record_source", {})
    record_link = record_source.get("evidence_link", {})
    _need(
        record_link.get("relationship") == "EVIDENCED_BY"
        and record_link.get("from") == decision.get("id")
        and record_link.get("to") == record_source.get("id"),
        "decision record citation must be a node-to-node EVIDENCED_BY edge, not a property",
    )
    claims = baseline.get("claims")
    _need(isinstance(claims, list) and len(claims) == 2, "baseline must contain both explicitly sourced claims")
    for claim in claims:
        claim_source = claim.get("source", {})
        evidence_link = claim_source.get("evidence_link", {})
        _need(
            evidence_link.get("relationship") == "EVIDENCED_BY"
            and evidence_link.get("from") == claim.get("version_id")
            and evidence_link.get("to") == claim_source.get("id"),
            "claim citation must be an explicit node-to-node EVIDENCED_BY edge, not a property",
        )

    cases = expected.get("cases")
    groups = matrix.get("groups")
    _need(isinstance(cases, list) and isinstance(groups, list), "expected cases and matrix groups must be arrays")
    case_ids = [row.get("id") for row in cases if isinstance(row, dict)]
    group_ids = [row.get("group_id") for row in groups if isinstance(row, dict)]
    _need(len(case_ids) == len(cases) == 9, "expected-results must contain exactly nine groups")
    _need(len(group_ids) == len(groups) == 9, "matrix must contain exactly nine groups")
    _same(case_ids, list(REQUIRED_GROUPS), "expected group order")
    _same(group_ids, list(REQUIRED_GROUPS), "matrix group order")

    total = 0
    for index, (case, group, case_id) in enumerate(zip(cases, groups, REQUIRED_GROUPS), 1):
        _need(isinstance(case.get("expected"), dict) and bool(case["expected"]), f"{case_id} has no literal expected object")
        _need(case.get("planner_refs") == [f"decision-provenance-packet#outcome-{index}"], f"{case_id} expected packet mapping changed")
        _need(isinstance(case.get("source_oracle_refs"), list) and case["source_oracle_refs"], f"{case_id} must cite unchanged source oracle(s)")
        _need(group.get("group_id") == case_id, f"matrix mapping differs for {case_id}")
        _need(isinstance(group.get("setup"), str) and group["setup"].strip(), f"{case_id} lacks reproducible setup")
        _need(isinstance(group.get("attack_or_edge_case"), str) and group["attack_or_edge_case"].strip(), f"{case_id} lacks adversarial failure mode")
        assertions = group.get("pinned_assertions")
        _need(isinstance(assertions, list) and assertions, f"{case_id} has no mapped assertions")
        assertion_ids = [row.get("id") for row in assertions if isinstance(row, dict)]
        _need(len(assertion_ids) == len(assertions), f"{case_id} assertion records must be objects")
        _same(assertion_ids, list(ASSERTION_IDS[case_id]), f"{case_id} exact assertion IDs")
        _need(len(assertion_ids) == len(set(assertion_ids)), f"{case_id} assertion IDs must be unique")
        for assertion in assertions:
            _need(isinstance(assertion.get("assertion"), str) and assertion["assertion"].strip(), f"{case_id} has a blank assertion description")
        count = group.get("nonzero_assertion_count")
        _need(type(count) is int and count > 0 and count == len(assertions), f"{case_id} must map nonzero assertions exactly")
        _need(group.get("planner_refs") == [f"{PACKET_FILENAME}:{PACKET_OUTCOME_LINES[index - 1]}"], f"{case_id} matrix packet line changed")
        total += count
    _need(total > 0, "matrix assertion count must be nonzero")
    return total


def _verify_source_cross_checks(expected: dict[str, Any], root: Path) -> dict[str, str]:
    parsed: dict[str, dict[str, Any]] = {}
    digests: dict[str, str] = {}
    for relative, pinned in SOURCE_ORACLES.items():
        document, _, digest = _read_json(root / relative, f"unchanged source oracle {relative}")
        _need(digest == pinned, f"unchanged source oracle byte hash differs: {relative}")
        parsed[relative] = document
        digests[relative] = digest

    sop = parsed["tests/sop-impact-expected-results.json"]
    handover = parsed["tests/handover-brief-expected-results.json"]
    baseline = expected.get("baseline")
    _need(isinstance(baseline, dict), "baseline must be an object")
    conflict_case = _find_case(sop, "cases", "id", "sop-impact-conflict-provenance", "SOP-impact oracle")
    conflict = conflict_case.get("expected", {})
    _same(baseline.get("conflict_edge"), conflict.get("conflict_edge"), "decision docket conflict edge vs unchanged SOP-impact source")
    claims = baseline.get("claims")
    sop_claims = conflict.get("claim_evidence")
    _need(isinstance(claims, list) and isinstance(sop_claims, list) and len(claims) == len(sop_claims) == 2, "two SOP claim sources must be present")
    for docket_claim, source_claim in zip(claims, sop_claims):
        _same(docket_claim.get("version_id"), source_claim.get("sop_version_id"), "claim version cross-check")
        _same(docket_claim.get("role_id"), source_claim.get("role_id"), "claimed role cross-check")
        _same(docket_claim.get("claim_edge", {}).get("relationship"), source_claim.get("claim_relationship"), "claim relationship cross-check")
        _same(docket_claim.get("claim_label"), source_claim.get("claim_label"), "claim epistemic label cross-check")
        source = docket_claim.get("source", {})
        _same(source.get("id"), source_claim.get("source_id"), "claim source ID cross-check")
        _same(source.get("title"), source_claim.get("source_title"), "claim source title cross-check")
        _same(source.get("observed_at"), source_claim.get("observed_at"), "claim source timestamp cross-check")
        _need(source_claim.get("source_evidence_linked") is True, "unchanged SOP-impact oracle must independently pin an evidence-linked claim")
        link = source.get("evidence_link", {})
        _need(link.get("relationship") == "EVIDENCED_BY" and link.get("from") == docket_claim.get("version_id") and link.get("to") == source.get("id"), "claim citation must be an explicit node-to-node EVIDENCED_BY edge, not property text")

    sop_paths_case = _find_case(sop, "cases", "id", "sop-impact-observed-outage-paths", "SOP-impact oracle")
    sop_paths = sop_paths_case.get("expected", {}).get("impact_paths")
    handover_paths = handover.get("canonical_snapshot", {}).get("impact", {}).get("paths")
    handover_path_case = _find_case(handover, "cases", "id", "inc204-observed-paths-and-provenance", "handover-brief oracle")
    handover_case_paths = handover_path_case.get("expected", {}).get("impact_paths")
    _same(sop_paths, baseline.get("existing_incident_impact_paths"), "pinned existing incident impact paths vs SOP-impact oracle")
    _same(handover_paths, baseline.get("existing_incident_impact_paths"), "pinned existing incident impact paths vs handover canonical snapshot")
    _same(handover_case_paths, baseline.get("existing_incident_impact_paths"), "pinned existing incident impact paths vs handover outcome")

    cold = _find_case(handover, "cases", "id", "cold-empty-unknown", "handover-brief oracle")["expected"]
    _need(cold.get("board_state") == "empty/unknown" and cold.get("risk_or_conflict_absence_concluded") is False, "cold unknown semantics conflict with unchanged handover oracle")
    acl_case = _find_case(sop, "cases", "id", "sop-impact-denied-source-invariance", "SOP-impact oracle")["expected"]
    _need(acl_case.get("viewer_scope") == "operations" and acl_case.get("operations_response_semantically_identical_before_after") is True and acl_case.get("hidden_count_or_existence_disclosed") is False, "operations ACL invariance conflicts with unchanged SOP-impact oracle")
    for row in expected.get("cases", []):
        for source_ref in row.get("source_oracle_refs", []):
            _need(source_ref.startswith("tests/sop-impact-expected-results.json#") or source_ref.startswith("tests/handover-brief-expected-results.json#"), f"case references an unpinned source oracle: {source_ref}")
    return digests


def validate_artifacts(
    expected: dict[str, Any],
    matrix: dict[str, Any],
    expected_sha256: str,
    matrix_sha256: str,
    *,
    root: Path,
    pinned_plan: Path,
    frozen_manifest_path: Path | None = None,
) -> tuple[int, dict[str, str]]:
    """Validate fixed packet, source, artifact, and matrix pins without candidate output."""
    _need(expected_sha256 == EXPECTED_ARTIFACT_SHA256, "expected-results bytes differ from the fixed nine-group oracle")
    _need(matrix_sha256 == MATRIX_ARTIFACT_SHA256, "adversarial-matrix bytes differ from the fixed matrix")
    assertion_count = _validate_fixed_shapes(expected, matrix)
    packet_digest = verify_pinned_packet(pinned_plan)
    manifest_path = frozen_manifest_path or root / "tests/decision-provenance-frozen-hashes.json"
    manifest, _, manifest_digest = _read_json(manifest_path, "frozen pre-task oracle hash manifest")
    frozen_hashes = validate_manifest(manifest, manifest_digest, root)
    source_digests = _verify_source_cross_checks(expected, root)
    return assertion_count, {
        "decision-provenance-packet": packet_digest,
        **source_digests,
        "preexisting-oracle-and-matrix-count": str(len(frozen_hashes)),
    }


def _reviewed_facts(expected: dict[str, Any]) -> dict[str, Any]:
    """Small content-bound projection of sensitive literals audited by reviewer."""
    baseline = expected["baseline"]
    return {
        "root_tracking_edge": baseline["root_tracking_edge"],
        "decision_record": baseline["decision_record"],
        "considered_edges": baseline["considered_edges"],
        "claims": baseline["claims"],
        "conflict_edge": baseline["conflict_edge"],
        "source_order": baseline["source_order"],
        "existing_incident_impact_paths": baseline["existing_incident_impact_paths"],
        "unknown_semantics": expected["scope"]["unknown_semantics"],
        "citation_semantics": expected["scope"]["citation_semantics"],
        "acl_semantics": expected["scope"]["acl_semantics"],
        "cold_unknown_outcome": expected["cases"][0]["expected"],
        "restricted_operations_outcome": expected["cases"][6]["expected"],
    }


def validate_review_receipt(
    receipt: dict[str, Any],
    expected: dict[str, Any],
    matrix: dict[str, Any],
    *,
    expected_sha256: str,
    matrix_sha256: str,
    root: Path,
) -> int:
    """Validate exact receipt content; reviewer identity remains self-attested."""
    _need(receipt.get("schema_version") == 1, "independent review receipt schema_version must be 1")
    _need(receipt.get("receipt_type") == "wardatlas-decision-provenance-independent-review", "unexpected independent-review receipt type")
    _need(receipt.get("status") == "passed", "independent review receipt must record a passing source audit")
    author = receipt.get("oracle_author_id")
    reviewer = receipt.get("reviewer_id")
    _need(isinstance(author, str) and author.strip() and isinstance(reviewer, str) and reviewer.strip(), "review receipt must identify author and reviewer")
    _need(author == ORACLE_AUTHOR_ID and reviewer != author, "reviewer must differ from the pinned oracle author")
    _need(receipt.get("independence_basis") == "independent-source-audit", "review must be grounded in independent source comparison")
    _need(receipt.get("same_model_fresh_session_only") is False, "a fresh session/model alone is not independent oracle evidence")
    _need(receipt.get("candidate_output_used_as_oracle") is False, "candidate output cannot inform the independent oracle review")

    manifest, _, manifest_digest = _read_json(root / "tests/decision-provenance-frozen-hashes.json", "frozen hash manifest")
    frozen_files = manifest["files"]
    _same(receipt.get("artifact_hashes"), {
        "expected_results": expected_sha256,
        "adversarial_matrix": matrix_sha256,
        "frozen_hash_manifest": manifest_digest,
        "pinned_packet": PACKET_SHA256,
    }, "review receipt artifact hashes")
    _same(receipt.get("reviewed_source_oracles"), [
        {"path": path, "sha256": digest, "source_id": SOURCE_IDS[path]}
        for path, digest in SOURCE_ORACLES.items()
    ], "review receipt unchanged SOP-impact/handover source bindings")
    _same(receipt.get("frozen_preexisting_oracle_hashes"), frozen_files, "review receipt frozen pre-task hash inventory")

    facts = _reviewed_facts(expected)
    _same(receipt.get("reviewed_facts"), facts, "review receipt exact record IDs, claims, source IDs/titles/times, unknown and ACL facts")
    _same(receipt.get("reviewed_facts_sha256"), canonical_sha256(facts), "review receipt content-bound facts digest")

    reviews = receipt.get("group_reviews")
    _need(isinstance(reviews, list) and len(reviews) == len(REQUIRED_GROUPS), "review receipt must cover all nine groups")
    cases_by_id = {case["id"]: case for case in expected["cases"]}
    groups_by_id = {group["group_id"]: group for group in matrix["groups"]}
    for group_id, review in zip(REQUIRED_GROUPS, reviews):
        _need(isinstance(review, dict), f"review receipt entry is not an object: {group_id}")
        _need(review.get("group_id") == group_id, f"review receipt group order differs at {group_id}")
        _need(review.get("result") == "pass", f"independent source audit failed: {group_id}")
        _same(review.get("expected_outcome_sha256"), canonical_sha256(cases_by_id[group_id]["expected"]), f"review outcome binding {group_id}")
        _same(review.get("matrix_assertion_ids"), [row["id"] for row in groups_by_id[group_id]["pinned_assertions"]], f"review mapped assertion IDs {group_id}")
        refs = review.get("source_evidence_refs")
        _need(isinstance(refs, list) and refs and all(isinstance(item, str) and item.strip() for item in refs), f"review lacks source evidence references: {group_id}")
        expected_sources = cases_by_id[group_id]["source_oracle_refs"]
        _same(review.get("source_evidence_refs"), expected_sources, f"review source references {group_id}")
        _same(review.get("packet_outcome_ref"), cases_by_id[group_id]["planner_refs"][0], f"review packet outcome reference {group_id}")
    return len(reviews)


def _load_and_validate(args: argparse.Namespace) -> tuple[int, dict[str, str], dict[str, Any], dict[str, Any], str, str]:
    root = Path(__file__).resolve().parents[1]
    expected, _, expected_hash = _read_json(args.expected, "expected-results")
    matrix, _, matrix_hash = _read_json(args.matrix, "adversarial matrix")
    count, source_hashes = validate_artifacts(
        expected,
        matrix,
        expected_hash,
        matrix_hash,
        root=root,
        pinned_plan=args.pinned_plan,
    )
    return count, source_hashes, expected, matrix, expected_hash, matrix_hash


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expected", required=True, type=Path)
    parser.add_argument("--matrix", required=True, type=Path)
    parser.add_argument("--pinned-plan", required=True, type=Path)
    parser.add_argument("--require-nine-groups", action="store_true")
    parser.add_argument("--check-frozen-oracle-hashes", action="store_true")
    parser.add_argument("--require-review-receipt", action="store_true")
    parser.add_argument("--review-receipt", type=Path, help="override the lane-specific evidence receipt path")
    args = parser.parse_args(argv)
    if not (args.require_nine_groups and args.check_frozen_oracle_hashes and args.require_review_receipt):
        print("FAIL: --require-nine-groups, --check-frozen-oracle-hashes, and --require-review-receipt are mandatory", file=sys.stderr)
        return 1
    try:
        count, hashes, expected, matrix, expected_hash, matrix_hash = _load_and_validate(args)
        receipt_path = args.review_receipt or receipt_path_from_workshop_lane()
        receipt, _, _ = _read_json(receipt_path, "independent source-review receipt")
        reviewed = validate_review_receipt(
            receipt,
            expected,
            matrix,
            expected_sha256=expected_hash,
            matrix_sha256=matrix_hash,
            root=Path(__file__).resolve().parents[1],
        )
    except (ContractError, OSError, TypeError, KeyError, ValueError) as exc:
        print(f"decision-provenance contract: FAIL: {exc}", file=sys.stderr)
        return 1
    print(
        "decision-provenance contract: PASS "
        f"({len(REQUIRED_GROUPS)} fixed groups, {count} mapped nonzero assertions, "
        f"{hashes['preexisting-oracle-and-matrix-count']} unchanged pre-task oracle/matrix byte hashes, "
        f"{reviewed} independently reviewed groups; no candidate output used)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

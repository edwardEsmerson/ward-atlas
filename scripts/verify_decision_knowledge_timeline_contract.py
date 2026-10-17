#!/usr/bin/env python3
"""Verify the frozen, candidate-free source-time timeline oracle and its receipt.

This validates artifacts and pinned source files only. It never runs or reads
candidate product behavior, fixtures, graph output, API responses, or browser
content.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Any


EXPECTED_SHA256 = "8a9ca76ecf7b194ddf26b2aa9bf3d04d0d4eab966a4ee65cacdbccdf76f79076"
MATRIX_SHA256 = "75a88572303e8fe98e14eadddf7aeab1cb0caead48a8552cfa6551284eaa1de4"
MANIFEST_SHA256 = "46fd3792487da192dfd08fc26d15e3f2153d13e57ad1a2a7704bab125f0dacf8"
TASK_PACKET_FILENAME = "planner-24567af7e5ee4642a9b81ba8ce6f184c.md"
TASK_PACKET_SHA256 = "178a30a13d16a049a02caf45d7ba7715ce22c65adf265a5bb3a9508800ad28b3"
DOCKET_PACKET_FILENAME = "planner-22e10d27ab804d3e872feefec257ae38.md"
DOCKET_PACKET_SHA256 = "992dba1b3dbbf4ee51e7c04a6fc1aa865a9460b84c1795fefd5931198bc5e1a0"

SOURCE_HASHES = {
    "tests/decision-provenance-expected-results.json": "4143bbc135b32ed6fb89444f2c32b01bfd5ca89d50127cea5e4099fdffed5c5d",
    "tests/decision-provenance-adversarial-matrix.json": "256048400ee304f59884ecf3393f9a59e30d76f29281c9c6ea5378740dd71ad3",
    "tests/sop-impact-expected-results.json": "f94e65850384e36e7200b321d14628f074fee37fb159adbcd73bef00c6e1acca",
    "tests/handover-brief-expected-results.json": "932ada7d43688b8bd1e4f147ac06dd585982d784ef8577f05cc00b07a97199f1",
}
SOURCE_IDS = {
    "tests/decision-provenance-expected-results.json": "decision-provenance-expected-results",
    "tests/decision-provenance-adversarial-matrix.json": "decision-provenance-adversarial-matrix",
    "tests/sop-impact-expected-results.json": "sop-impact-expected-results",
    "tests/handover-brief-expected-results.json": "handover-brief-expected-results",
}
PACKET_GROUP_LINES = tuple(range(56, 68))
PACKET_GROUP_PREFIXES = (
    "1. **Cold empty:**", "2. **Before first observation:**", "3. **V2 observation boundary:**",
    "4. **Between first and second source:**", "5. **V3 observation boundary:**",
    "6. **Before decision-record evidence:**", "7. **Decision-record boundary:**",
    "8. **Timestamp mutation sensitivity:**", "9. **Evidence-edge removal / no property reconstruction:**",
    "10. **Decoy and denied-source invariance:**", "11. **Timestamp validation:**",
    "12. **Repeatability, reconnect, read-only and responsive agreement:**",
)
GROUP_IDS = (
    "cold-empty-unknown", "before-first-observation", "v2-observation-boundary",
    "v2-unchanged-before-v3", "v3-and-graph-derived-conflict",
    "pre-record-evidence-no-existence-leak", "record-source-boundary",
    "v3-observation-time-shift", "separate-evidence-edge-removals",
    "decoy-and-restricted-invariance", "timestamp-normalization-and-rejection",
    "reseed-reconnect-read-only-browser-cleanup",
)
ASSERTION_IDS = {
    "cold-empty-unknown": ("cold.generic-unknown", "cold.no-claim-rows", "cold.no-record", "cold.no-visible-ids", "cold.no-visible-titles", "cold.no-count-fields", "cold.no-count-or-existence", "cold.no-negative-conclusion"),
    "before-first-observation": ("before.time", "before.generic-unknown", "before.no-sources", "before.no-decision-ids", "before.no-count-fields", "before.no-count-or-existence", "before.not-negative"),
    "v2-observation-boundary": ("v2.exact-cutoff", "v2.only-source-id", "v2.source-path", "v2.claim-source-time", "v2.claim-edge", "v2.claim-role", "v2.applicability-edge", "v2.source-id-title", "v2.evidence-edge", "v2.record-hidden", "v2.no-conflict-conclusion"),
    "v2-unchanged-before-v3": ("v2-later.time", "v2-later.full-projection", "v2-later.same-projection", "v2-later.exact-source", "v2-later.no-future-leak"),
    "v3-and-graph-derived-conflict": ("v3.exact-cutoff", "v3.v2-and-v3-sources", "v3.v3-evidence-time", "v3.v3-claim-edge", "v3.v3-applicability-edge", "v3.v3-source-id-title", "v3.conflict-kind", "v3.conflict-edge", "v3.record-hidden", "v3.not-authoritative"),
    "pre-record-evidence-no-existence-leak": ("pre-record.time", "pre-record.full-projection", "pre-record.same-projection", "pre-record.no-identity", "pre-record.no-count", "pre-record.no-existence-hint", "pre-record.not-absence-claim", "pre-record.record-suppressed"),
    "record-source-boundary": ("record.cutoff", "record.exact-id", "record.own-source-edge", "record.source-time", "record.state", "record.register-tracks-edge", "record.register-source-path", "record.considers-v2-v3", "record.both-claims-known", "record.no-selection", "record.no-action"),
    "v3-observation-time-shift": ("shift.cutoff", "shift.16-05-full-projection", "shift.16-06-full-projection", "shift.16-05-full-claims", "shift.16-05-v2-only", "shift.16-05.record-retained", "shift.16-05.considers-v2-only", "shift.16-05.incomplete", "shift.16-05.forbidden-v3-literals", "shift.16-05.forbidden-v3-edges", "shift.16-05.forbidden-v3-paths", "shift.16-05.no-v3-count-path", "shift.16-05.no-conflict", "shift.16-06-v3-source-time", "shift.16-06-considers-return", "shift.existing-paths-unchanged"),
    "separate-evidence-edge-removals": ("remove-v3.only-edge", "remove-v3-v2-remains", "remove-v3.considers-v2-only", "remove-v3.no-v3-or-conflict", "remove-v3.record-state-retained", "remove-v3.incomplete", "remove-record.own-edge", "remove-record.facts-suppressed", "remove-record.preserved-claims", "remove-record.preserved-conflict", "remove-record.timeline-not-unknown", "remove-record.no-property-citation", "remove-record.generic-unknown", "remove-record.exact-unknown-outcome", "remove.edges.preserve-impact"),
    "decoy-and-restricted-invariance": ("acl.baseline-full-projection", "acl.decoy-full-projection", "acl.restricted-full-projection", "acl.decoy-invariant", "acl.full-view-invariant", "acl.browser-accessibility-invariant", "acl.no-leak", "acl.no-tripwires", "acl.no-hidden-count"),
    "timestamp-normalization-and-rejection": ("time.offset-input", "time.utc-normalization", "time.full-result", "time.same-result", "time.malformed-rejected", "time.naive-rejected", "time.reject-before-graph-write", "time.reject-before-action-write", "time.no-local-clock-fallback"),
    "reseed-reconnect-read-only-browser-cleanup": ("repeat.two-seeds", "repeat.zero-node-delta", "repeat.zero-edge-delta", "repeat.baseline-full-projection", "repeat.fresh-client", "repeat.reconnected-full-projection", "repeat.zero-graph-node-writes", "repeat.zero-edge-writes", "repeat.zero-action-writes", "repeat.api-desktop-mobile", "repeat.owned-cleanup"),
}
AUTHOR_ID = "task-author:c880db7d2db343a59d2a9a074e3ab915"
RECEIPT_TYPE = "wardatlas-source-time-independent-source-review"
EVIDENCE_ROOT = Path("/workspace/evidence/auto-f8ad6be512aa1d923581868e/c880db7d2db343a59d2a9a074e3ab915/independent-source-review")


class ContractError(ValueError):
    """A frozen source, artifact, mapping, or review binding differs."""


def need(condition: bool, message: str) -> None:
    if not condition:
        raise ContractError(message)


def strict_equal(actual: Any, expected: Any) -> bool:
    if type(actual) is not type(expected):
        return False
    if isinstance(actual, dict):
        return actual.keys() == expected.keys() and all(strict_equal(actual[k], expected[k]) for k in actual)
    if isinstance(actual, list):
        return len(actual) == len(expected) and all(strict_equal(a, b) for a, b in zip(actual, expected))
    return actual == expected


def same(actual: Any, expected: Any, label: str) -> None:
    need(strict_equal(actual, expected), f"{label} differs from pinned literal/source")


def canonical_sha256(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def read_json(path: Path, label: str) -> tuple[dict[str, Any], bytes, str]:
    try:
        raw = path.read_bytes()
        doc = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ContractError(f"cannot read {label} {path}: {exc}") from exc
    need(isinstance(doc, dict), f"{label} must be a JSON object")
    return doc, raw, hashlib.sha256(raw).hexdigest()


def _assert_no_symlink(path: Path, label: str) -> None:
    current = path
    while current != current.parent:
        need(not current.is_symlink(), f"{label} may not traverse symlink path {current}")
        current = current.parent


def _safe_repo_file(root: Path, relative: str) -> Path:
    rel = Path(relative)
    need(not rel.is_absolute() and ".." not in rel.parts, f"unsafe source path: {relative}")
    target = root / rel
    _assert_no_symlink(target, relative)
    try:
        need(target.resolve().is_relative_to(root.resolve()), f"source escapes repository root: {relative}")
    except OSError as exc:
        raise ContractError(f"cannot resolve source path {relative}: {exc}") from exc
    return target


def lane_receipt_path(lane: str | None = None) -> Path:
    value = lane if lane is not None else os.environ.get("WORKSHOP_LANE")
    need(isinstance(value, str) and value.isdigit(), "WORKSHOP_LANE must be a decimal lane identifier")
    return EVIDENCE_ROOT / f"lane-{value}" / "independent-review.json"


def _source_rows() -> list[dict[str, str]]:
    return [
        {"id": "decision-provenance-packet", "path": DOCKET_PACKET_FILENAME, "sha256": DOCKET_PACKET_SHA256, "kind": "accepted-planner-packet"},
        {"id": SOURCE_IDS["tests/decision-provenance-expected-results.json"], "path": "tests/decision-provenance-expected-results.json", "sha256": SOURCE_HASHES["tests/decision-provenance-expected-results.json"], "kind": "accepted-source-oracle"},
        {"id": SOURCE_IDS["tests/decision-provenance-adversarial-matrix.json"], "path": "tests/decision-provenance-adversarial-matrix.json", "sha256": SOURCE_HASHES["tests/decision-provenance-adversarial-matrix.json"], "kind": "accepted-source-matrix"},
        {"id": SOURCE_IDS["tests/sop-impact-expected-results.json"], "path": "tests/sop-impact-expected-results.json", "sha256": SOURCE_HASHES["tests/sop-impact-expected-results.json"], "kind": "accepted-source-oracle"},
        {"id": SOURCE_IDS["tests/handover-brief-expected-results.json"], "path": "tests/handover-brief-expected-results.json", "sha256": SOURCE_HASHES["tests/handover-brief-expected-results.json"], "kind": "accepted-source-oracle"},
    ]


def validate_manifest(manifest: dict[str, Any], raw_hash: str, root: Path, task_packet: Path) -> dict[str, Any]:
    need(raw_hash == MANIFEST_SHA256, "frozen source-manifest bytes changed")
    need(manifest.get("schema_version") == 1, "source manifest schema_version must be 1")
    same(manifest.get("oracle_sources"), _source_rows(), "frozen accepted source inventory")
    task = manifest.get("task_packet")
    same(task, {
        "id": "decision-knowledge-timeline-task-packet",
        "path": "planner-24567af7e5ee4642a9b81ba8ce6f184c/plans/planner-24567af7e5ee4642a9b81ba8ce6f184c.md",
        "sha256": TASK_PACKET_SHA256,
        "outcome_lines": list(PACKET_GROUP_LINES),
        "role": "acceptance-map-only-not-product-output",
    }, "timeline task packet binding")
    need(task_packet.name == TASK_PACKET_FILENAME, "--pinned-plan must be the accepted source-time timeline task packet")
    _assert_no_symlink(task_packet, "pinned task packet")
    need(hashlib.sha256(task_packet.read_bytes()).hexdigest() == TASK_PACKET_SHA256, "timeline task packet hash mismatch")
    plan_lines = task_packet.read_text(encoding="utf-8").splitlines()
    prefixes = (
        "1. **Cold empty:**", "2. **Before first observation:**", "3. **V2 observation boundary:**",
        "4. **Between first and second source:**", "5. **V3 observation boundary:**",
        "6. **Before decision-record evidence:**", "7. **Decision-record boundary:**",
        "8. **Timestamp mutation sensitivity:**", "9. **Evidence-edge removal / no property reconstruction:**",
        "10. **Decoy and denied-source invariance:**", "11. **Timestamp validation:**",
        "12. **Repeatability, reconnect, read-only and responsive agreement:**",
    )
    for line_no, prefix in zip(PACKET_GROUP_LINES, prefixes):
        need(len(plan_lines) >= line_no and plan_lines[line_no - 1].startswith(prefix), f"timeline task packet case heading drift at line {line_no}")

    docket_packet = task_packet.parent / DOCKET_PACKET_FILENAME
    _assert_no_symlink(docket_packet, "accepted decision-provenance packet")
    need(hashlib.sha256(docket_packet.read_bytes()).hexdigest() == DOCKET_PACKET_SHA256, "accepted decision-provenance packet hash mismatch")
    files: dict[str, dict[str, Any]] = {}
    for relative, expected_hash in SOURCE_HASHES.items():
        path = _safe_repo_file(root, relative)
        doc, _, digest = read_json(path, f"frozen source {relative}")
        need(digest == expected_hash, f"accepted source hash mismatch: {relative}")
        files[relative] = doc
    return files


def _case(document: dict[str, Any], key: str, label: str) -> dict[str, Any]:
    rows = document.get("cases")
    need(isinstance(rows, list), f"{label}.cases must be an array")
    matches = [x for x in rows if isinstance(x, dict) and x.get("id") == key]
    need(len(matches) == 1, f"expected one {label} case {key}")
    return matches[0]["expected"]


def _cross_check_sources(expected: dict[str, Any], files: dict[str, dict[str, Any]]) -> None:
    docket = files["tests/decision-provenance-expected-results.json"]
    sop = files["tests/sop-impact-expected-results.json"]
    handover = files["tests/handover-brief-expected-results.json"]
    source_baseline = docket["baseline"]
    sop_conflict = _case(sop, "sop-impact-conflict-provenance", "SOP-impact")
    handover_paths = handover["canonical_snapshot"]["impact"]["paths"]
    same(expected["baseline"]["existing_incident_impact_paths"], handover_paths, "pinned incident impact paths")
    same(expected["baseline"]["existing_incident_impact_paths"], _case(sop, "sop-impact-observed-outage-paths", "SOP-impact")["impact_paths"], "SOP-impact incident paths")
    same(expected["baseline"]["conflict"]["edge"], sop_conflict["conflict_edge"], "SOP_CONFLICT source edge")
    need(expected["baseline"]["conflict"]["epistemic_type"] == "graph-derived relationship", "conflict must be labeled graph-derived")
    need(expected["baseline"]["conflict"]["source_quote"] is False and expected["baseline"]["conflict"]["authoritative_rule"] is False, "conflict must not be presented as a source quote or rule")
    same(expected["baseline"]["decision_record"]["id"], source_baseline["decision_record"]["id"], "record ID")
    same(expected["baseline"]["decision_record"]["title"], source_baseline["decision_record"]["title"], "record title")
    same(expected["baseline"]["decision_record"]["recorded_state"], source_baseline["decision_record"]["recorded_state"], "record state")
    same(expected["baseline"]["decision_record"]["source"]["id"], source_baseline["decision_record"]["record_source"]["id"], "record source ID")
    same(expected["baseline"]["decision_record"]["source"]["title"], source_baseline["decision_record"]["record_source"]["title"], "record source title")
    same(expected["baseline"]["decision_record"]["source"]["observed_at"], source_baseline["decision_record"]["record_source"]["observed_at"], "record source observation time")
    same(expected["baseline"]["decision_record"]["source"]["evidence_link"], source_baseline["decision_record"]["record_source"]["evidence_link"], "record source node-to-node evidence edge")
    same(expected["baseline"]["decision_record"]["register_edge"], source_baseline["root_tracking_edge"], "register TRACKS edge")
    same(expected["baseline"]["considered_edges"], source_baseline["considered_edges"], "decision CONSIDERS edges")
    need(expected["baseline"]["decision_record"]["source"]["observed_at"] == "2026-10-14T16:05:00Z", "record source boundary must remain 16:05Z")

    source_claims = source_baseline["claims"]
    sop_claims = sop_conflict["claim_evidence"]
    need(len(source_claims) == len(sop_claims) == 2, "both V2 and V3 source-backed claims must exist")
    for index, field in enumerate(("v2_claim", "v3_claim")):
        claim = expected["baseline"][field]
        dp_claim = source_claims[index]
        si_claim = sop_claims[index]
        same(claim["version_id"], dp_claim["version_id"], f"{field} source version")
        same(claim["version_id"], si_claim["sop_version_id"], f"{field} SOP-impact version")
        same(claim["role_id"], dp_claim["role_id"], f"{field} role")
        same(claim["role_id"], si_claim["role_id"], f"{field} SOP-impact role")
        same(claim["claim_label"], dp_claim["claim_label"], f"{field} label")
        same(claim["claim_label"], si_claim["claim_label"], f"{field} label/source oracle")
        same(claim["source"]["id"], dp_claim["source"]["id"], f"{field} source ID")
        same(claim["source"]["id"], si_claim["source_id"], f"{field} SOP-impact source ID")
        same(claim["source"]["title"], dp_claim["source"]["title"], f"{field} source title")
        same(claim["source"]["title"], si_claim["source_title"], f"{field} SOP-impact source title")
        same(claim["source"]["observed_at"], dp_claim["source"]["observed_at"], f"{field} source time")
        same(claim["source"]["observed_at"], si_claim["observed_at"], f"{field} SOP-impact source time")
        same(claim["claim_edge"], dp_claim["claim_edge"], f"{field} CLAIMS_ROLE edge")
        same(claim["source"]["evidence_link"], dp_claim["source"]["evidence_link"], f"{field} node-to-node EVIDENCED_BY edge")
        same(claim["claim_edge"]["relationship"], si_claim["claim_relationship"], f"{field} SOP-impact claim relationship")
        same(claim["source"]["evidence_link"]["relationship"], dp_claim["source"]["evidence_link"]["relationship"], f"{field} evidence edge type")
        need(si_claim.get("source_evidence_linked") is True, f"{field} SOP-impact source must be linked by edge")
        same(claim["applicability_edge"], sop_conflict["applicability_edges"][index], f"{field} applicability edge")
        need(claim["discovery_path"] == "existing SOP-impact graph path", f"{field} must cite only the existing SOP-impact path without naming hidden record paths")
        for link_field in ("claim_edge", "source.evidence_link", "applicability_edge"):
            parts = link_field.split(".")
            value: Any = claim
            for part in parts:
                value = value[part]
            need(isinstance(value, dict) and set(value) == {"from", "relationship", "to"}, f"{field} {link_field} must be an explicit node-to-node edge")

    cold = _case(handover, "cold-empty-unknown", "handover")
    need(cold.get("board_state") == "empty/unknown" and cold.get("risk_or_conflict_absence_concluded") is False, "cold empty must remain unknown, not an absence claim")
    record_edge_case = next(case["expected"] for case in expected["cases"] if case["id"] == "separate-evidence-edge-removals")
    same(record_edge_case["decision_record_source_edge_removal"]["record_lookup_result"], _case(docket, "empty-unknown", "decision-provenance"), "record-source-edge removal must retain the exact docket generic-unknown result")
    acl = _case(sop, "sop-impact-denied-source-invariance", "SOP-impact")
    need(acl.get("viewer_scope") == "operations" and acl.get("operations_response_semantically_identical_before_after") is True and acl.get("hidden_count_or_existence_disclosed") is False, "source ACL oracle no longer pins operations invariance")


def _pointer(document: Any, pointer: str) -> Any:
    need(isinstance(pointer, str) and pointer.startswith("/"), f"invalid JSON pointer: {pointer!r}")
    value = document
    for raw in pointer[1:].split("/"):
        token = raw.replace("~1", "/").replace("~0", "~")
        if isinstance(value, list):
            value = value[int(token)]
        else:
            value = value[token]
    return value


def _contains_value(tree: Any, needle: Any) -> bool:
    if strict_equal(tree, needle):
        return True
    if isinstance(tree, list):
        return any(_contains_value(item, needle) for item in tree)
    if isinstance(tree, dict):
        return any(_contains_value(item, needle) for item in tree.values())
    return False


def _case_map(expected: dict[str, Any]) -> dict[str, dict[str, Any]]:
    rows = expected.get("cases")
    need(isinstance(rows, list) and len(rows) == 12, "expected-results must contain exactly twelve groups")
    ids = [row.get("id") for row in rows if isinstance(row, dict)]
    same(ids, list(GROUP_IDS), "fixed group order")
    return {row["id"]: row for row in rows}


def _validate_semantics(expected: dict[str, Any], matrix: dict[str, Any]) -> int:
    need(expected.get("schema_version") == matrix.get("schema_version") == 1, "schema_version must be 1")
    need(expected.get("product") == "WardAtlas", "unexpected product")
    need(expected.get("candidate_output_is_oracle") is False or expected.get("scope", {}).get("candidate_output_is_oracle") is False, "candidate output cannot be oracle")
    scope = expected.get("scope")
    need(isinstance(scope, dict), "scope must be an object")
    need(scope.get("synthetic_only") is True and scope.get("clinical_decisions") is False and scope.get("external_writes") is False, "synthetic/non-clinical/no-external-write boundary weakened")
    need("observed_at" in scope.get("knowledge_time", "") and "not effective time" in scope.get("knowledge_time", ""), "source observation time must remain distinct from effective time")
    pre_record_policy = scope.get("pre_record_claim_source", "").lower()
    need("only via the accepted sop-impact" in pre_record_policy and "never discover claims via a future decision record" in pre_record_policy, "pre-16:05 claims must come only from the SOP-impact source path")
    need("own EVIDENCED_BY source" in scope.get("record_visibility", "") and "only to independently evidence-known claim nodes" in scope.get("record_visibility", ""), "record and CONSIDERS visibility cutoff weakened")
    need("never proof of nonexistence" in scope.get("unknown_semantics", ""), "unknown semantics weakened")
    need("node-to-node EVIDENCED_BY edge" in scope.get("citation_semantics", ""), "property-only citation semantics admitted")
    need("invariant" in scope.get("acl_semantics", "") and "no inaccessible" in scope.get("acl_semantics", ""), "restricted-source non-disclosure semantics weakened")
    same(matrix.get("oracle_file"), "contracts/decision-knowledge-timeline/expected-results.json", "matrix oracle path")
    need("Candidate output" in matrix.get("oracle_policy", ""), "matrix must prohibit candidate-derived expectations")

    cases = _case_map(expected)
    groups = matrix.get("groups")
    need(isinstance(groups, list) and len(groups) == 12, "matrix must contain exactly twelve groups")
    group_ids = [row.get("group_id") for row in groups if isinstance(row, dict)]
    same(group_ids, list(GROUP_IDS), "matrix group order")
    total = 0
    for index, (group_id, group) in enumerate(zip(GROUP_IDS, groups)):
        case = cases[group_id]
        need(isinstance(case.get("expected"), dict) and bool(case["expected"]), f"{group_id} expected outcome must be a nonempty literal object")
        same(case.get("planner_refs"), [f"source-time-timeline-packet#outcome-{index + 1}"], f"{group_id} packet mapping")
        refs = case.get("source_oracle_refs")
        need(isinstance(refs, list) and refs, f"{group_id} requires accepted source-oracle references")
        need(all(any(ref.startswith(prefix) for prefix in SOURCE_HASHES) for ref in refs), f"{group_id} contains an unpinned source-oracle reference")
        same(group.get("group_id"), group_id, f"matrix group mapping {group_id}")
        same(group.get("planner_refs"), [f"{TASK_PACKET_FILENAME}:{PACKET_GROUP_LINES[index]}"], f"{group_id} planner line")
        same(group.get("source_oracle_refs"), refs, f"{group_id} source mapping")
        need(isinstance(group.get("setup"), str) and group["setup"].strip(), f"{group_id} lacks setup")
        need(isinstance(group.get("attack_or_edge_case"), str) and group["attack_or_edge_case"].strip(), f"{group_id} lacks adversarial attack")
        assertions = group.get("pinned_assertions")
        need(isinstance(assertions, list) and assertions, f"{group_id} has zero mapped assertions")
        ids = [item.get("id") for item in assertions if isinstance(item, dict)]
        same(ids, list(ASSERTION_IDS[group_id]), f"{group_id} exact nonzero assertion IDs")
        need(len(ids) == len(set(ids)), f"{group_id} assertion IDs must be unique")
        for assertion in assertions:
            need(isinstance(assertion.get("assertion"), str) and assertion["assertion"].strip(), f"{group_id} blank assertion description")
            literal = _pointer(expected, assertion.get("expected_pointer"))
            need("expected" in assertion["expected_pointer"], f"{group_id} assertion must map to a literal expected value")
            # Booleans, nulls and empty arrays are valid pinned negative/cold outcomes.
            need(literal is not ... , f"{group_id} assertion pointer has no literal")
        count = group.get("nonzero_assertion_count")
        need(type(count) is int and count == len(assertions) and count > 0, f"{group_id} nonzero assertion count mismatch")
        total += count
    need(total > 0, "matrix has no mapped assertions")

    # Semantic cross-case checks prevent time-insensitive, future-leaking, or
    # provenance-weakening mutations even when the artifact digest is bypassed.
    def exp(gid: str) -> dict[str, Any]:
        return cases[gid]["expected"]
    empty = exp(GROUP_IDS[0])
    need(empty["projection"]["generic_unknown"] is True and empty["projection"]["claims"] == [] and empty["projection"]["decision_record"] is None, "cold empty must be generic unknown")
    need(empty["exposed_ids"] == [] and empty["exposed_titles"] == [] and empty["exposed_count_fields"] == [] and empty["existence_hint_exposed"] is False and empty["no-decision-or-conflict-absence-concluded"] is False, "cold unknown may not leak existence or assert absence")
    before = exp(GROUP_IDS[1])
    need(before["projection"]["known_at"] == "2026-10-14T15:59:59Z" and before["projection"]["claims"] == [] and before["projection"]["decision_record"] is None, "pre-source cutoff must be unknown")
    v2 = exp(GROUP_IDS[2])["projection"]
    v2_later = exp(GROUP_IDS[3])["projection"]
    need(v2["known_at"] == "2026-10-14T16:00:00Z", "V2 boundary must remain exactly 16:00Z")
    need(v2_later["known_at"] == "2026-10-14T16:03:59Z", "unchanged V2 cutoff must remain exactly 16:03:59Z")
    v2_normalized = json.loads(json.dumps(v2)); v2_normalized["known_at"] = "2026-10-14T16:03:59Z"
    same(v2_later, v2_normalized, "16:03:59 full semantic projection must equal 16:00 V2-only projection")
    need(len(v2["claims"]) == 1 and v2["claims"][0]["version_id"] == "SOP-OPS-9-V2", "16:00 must expose only V2")
    need(v2["claims"][0]["discovery_path"].startswith("existing SOP-impact graph path"), "V2 claim must originate only from SOP-impact")
    need(v2["decision_record"] is None and v2["decision_considers_edges"] == [] and v2["conflict"] is None, "future record/CONSIDERS/conflict must be hidden at 16:00")
    at_v3 = exp(GROUP_IDS[4])["projection"]
    at_v3_later = exp(GROUP_IDS[5])["projection"]
    need(at_v3["known_at"] == "2026-10-14T16:04:00Z" and at_v3_later["known_at"] == "2026-10-14T16:04:59Z", "V3 and pre-record cutoffs must remain exact")
    same(at_v3_later, {**at_v3, "known_at": "2026-10-14T16:04:59Z"}, "16:04:59 must equal V3 cutoff apart from timestamp")
    need([c["version_id"] for c in at_v3["claims"]] == ["SOP-OPS-9-V2", "SOP-OPS-9-V3"], "16:04 must expose exactly the two claims")
    need(at_v3["conflict"]["epistemic_type"] == "graph-derived relationship" and at_v3["decision_record"] is None and at_v3["decision_considers_edges"] == [], "pre-record conflict must be graph-derived and record-independent")
    pre_record = exp(GROUP_IDS[5])
    need(pre_record["record_id_title_state_source_and_considers_suppressed"] is True and pre_record["exposed_record_literals"] == [] and pre_record["existence_hint_exposed"] is False and pre_record["no-record-exists_conclusion"] is False, "before 16:05 must not leak record existence or conclude absence")
    full = exp(GROUP_IDS[6])["projection"]
    need(full["known_at"] == "2026-10-14T16:05:00Z" and full["decision_record"]["source"]["observed_at"] == "2026-10-14T16:05:00Z", "record appears only at its own source boundary")
    need(len(full["decision_considers_edges"]) == 2 and full["decision_record"]["recorded_state"] == "open-unresolved", "record state and both evidence-known considered links are pinned")
    shift = exp(GROUP_IDS[7])
    at_1605 = shift["at_16_05"]
    at_1606 = shift["at_16_06"]
    need(at_1605["known_at"] == "2026-10-14T16:05:00Z" and at_1605["claim_context_state"] == "incomplete/unknown", "V3 shift 16:05 context must be incomplete/unknown")
    need([c["version_id"] for c in at_1605["claims"]] == ["SOP-OPS-9-V2"] and at_1605["decision_record"]["recorded_state"] == "open-unresolved", "V3 shift retains only V2 plus the separately evidenced record state")
    need(at_1605["decision_considers_edges"] == [full["decision_considers_edges"][0]] and at_1605["conflict"] is None, "V3 shift suppresses V3 CONSIDERS and conflict edge")
    need(not any(isinstance(v, str) and "V3" in v for v in at_1605["claims"][0].values()), "V3 identifiers may not appear in the shifted 16:05 claim")
    need(at_1606["claims"][1]["source"]["observed_at"] == "2026-10-14T16:06:00Z" and len(at_1606["decision_considers_edges"]) == 2, "shifted V3 returns only at 16:06")
    shifted_baseline = json.loads(json.dumps(full))
    shifted_baseline["known_at"] = "2026-10-14T16:06:00Z"
    shifted_baseline["claims"][1]["source"]["observed_at"] = "2026-10-14T16:06:00Z"
    same(at_1606, shifted_baseline, "16:06 restores all other baseline fields and paths")
    need(shift["at_16_05_v3_count_or_path_exposed"] is False and shift["at_16_05_conflict_edge_exposed"] is False, "no hidden V3 count/path/conflict at 16:05")
    at_1605_json = json.dumps(at_1605, sort_keys=True, ensure_ascii=False)
    need(all(literal not in at_1605_json for literal in shift["at_16_05_forbidden_v3_literals"]), "shifted 16:05 projection contains a suppressed V3 literal")
    for edge in shift["at_16_05_forbidden_v3_relationships"]:
        need(not _contains_value(at_1605, edge), "shifted 16:05 projection contains a suppressed V3 relationship")
    need(shift["at_16_05_forbidden_v3_paths"] and all("SOP-OPS-9-V3" in path for path in shift["at_16_05_forbidden_v3_paths"]), "V3-linked suppressed paths must be explicitly pinned")
    need(all(not _contains_value(at_1605, path) for path in shift["at_16_05_forbidden_v3_paths"]), "shifted 16:05 projection contains a suppressed V3 path")
    removals = exp(GROUP_IDS[8])
    v3_removed = removals["v3_source_evidence_edge_removal"]
    record_removed = removals["decision_record_source_edge_removal"]
    need(v3_removed["removed_edge"] == {"from":"SOP-OPS-9-V3","relationship":"EVIDENCED_BY","to":"SRC-SOP-CURRENT-V3"}, "V3 evidence removal must be a separate exact edge counterfactual")
    need(v3_removed["projection"]["decision_record"]["recorded_state"] == "open-unresolved" and v3_removed["projection"]["claim_context_state"] == "incomplete/unknown", "V3 evidence removal preserves record and unknown context")
    need(v3_removed["projection"]["conflict"] is None and [c["version_id"] for c in v3_removed["projection"]["claims"]] == ["SOP-OPS-9-V2"], "V3 edge removal suppresses V3/conflict without promoting V2")
    need(record_removed["removed_edge"] == {"from":"DEC-OPS-9-REVIEW-01","relationship":"EVIDENCED_BY","to":"SRC-DEC-OPS-9-REVIEW-01"}, "record evidence removal must be separately pinned")
    need(record_removed["projection"]["decision_record"] is None and record_removed["projection"]["generic_unknown"] is False and record_removed["record_facts_suppressed"] is True and record_removed["property_only_provenance_accepted"] is False, "record edge removal must keep independently known claims while not reconstructing record properties")
    same(record_removed["projection"]["claims"], full["claims"], "record edge removal preserves source-known SOP claims")
    same(record_removed["projection"]["conflict"], full["conflict"], "record edge removal preserves independent graph-derived conflict")
    need(record_removed["projection"]["decision_considers_edges"] == [] and record_removed["record_lookup_result"]["state"] == "unknown" and record_removed["record_lookup_result"]["evidence_state"] == "no-evidence", "record-only unknown must not erase timeline facts")
    need(record_removed["record_lookup_result"]["decision_id_title_source_exposed"] is False and record_removed["record_lookup_result"]["visible_count_exposed"] is False and record_removed["record_lookup_result"]["existence_hint_exposed"] is False and record_removed["record_lookup_result"]["no_decision_concluded"] is False, "record-edge removal must preserve exact no-leak unknown semantics")
    need(v3_removed["v3_or_conflict_exposed"] is False and v3_removed["projection"]["decision_considers_edges"] == removals["decision_record_source_edge_removal"]["considered_edges_after_v3_removal"], "V3 evidence removal must suppress V3 links while retaining the V2 considered edge")
    same(shift["existing_incident_impact_paths_unchanged"], expected["baseline"]["existing_incident_impact_paths"], "timestamp shift preserves incident paths")
    same(removals["existing_incident_impact_paths_unchanged"], expected["baseline"]["existing_incident_impact_paths"], "edge removals preserve incident paths")
    acl = exp(GROUP_IDS[9])
    same(acl["clean_operations_projection"], acl["after_disconnected_decoy_projection"], "disconnected decoy invariance")
    same(acl["clean_operations_projection"], acl["after_connected_restricted_injection_projection"], "restricted-source full-view invariance")
    need(acl["restricted_literals_disclosed"] == [] and acl["restricted_ids_titles_facts_counts_existence_edges_paths_or_paraphrases_exposed"] is False, "restricted-source non-disclosure weakened")
    timestamps = exp(GROUP_IDS[10])
    need(timestamps["normalized_known_at"] == "2026-10-14T16:05:00Z" and timestamps["normalized_projection"] == full, "offset time must normalize to exact 16:05 projection")
    need(timestamps["malformed_rejected"] is True and timestamps["naive_rejected"] is True and timestamps["rejection_precedes_graph_mutation"] is True and timestamps["rejection_precedes_local_action_mutation"] is True and timestamps["falls_back_to_local_or_current_time"] is False, "timestamp validation must reject before mutation without fallback")
    repeat = exp(GROUP_IDS[11])
    need(repeat["fixture_seed_runs"] == 2 and repeat["second_seed_node_delta"] == repeat["second_seed_relationship_delta"] == 0, "idempotent reseed literals changed")
    same(repeat["exact_projection_after_reseed"], full, "reseed baseline projection")
    same(repeat["fresh_client_projection"], full, "fresh client projection")
    need(repeat["query_and_browser_graph_node_writes"] == repeat["query_and_browser_graph_relationship_writes"] == repeat["query_and_browser_local_action_writes"] == 0, "timeline reads must remain read-only")
    return total


def validate_documents(expected: dict[str, Any], matrix: dict[str, Any], manifest: dict[str, Any], *, root: Path, task_packet: Path, expected_sha256: str, matrix_sha256: str, manifest_sha256: str) -> tuple[int, dict[str, Any]]:
    need(expected_sha256 == EXPECTED_SHA256, "expected-results bytes differ from the frozen twelve-group oracle")
    need(matrix_sha256 == MATRIX_SHA256, "adversarial-matrix bytes differ from the frozen matrix")
    files = validate_manifest(manifest, manifest_sha256, root, task_packet)
    same(expected.get("pinned_sources"), [
        {"id":"decision-provenance-packet","path":DOCKET_PACKET_FILENAME,"sha256":DOCKET_PACKET_SHA256,"outcome_lines":[49,50,51,52,53,54,55,56,57]},
        {"id":SOURCE_IDS["tests/decision-provenance-expected-results.json"],"path":"tests/decision-provenance-expected-results.json","sha256":SOURCE_HASHES["tests/decision-provenance-expected-results.json"]},
        {"id":SOURCE_IDS["tests/decision-provenance-adversarial-matrix.json"],"path":"tests/decision-provenance-adversarial-matrix.json","sha256":SOURCE_HASHES["tests/decision-provenance-adversarial-matrix.json"]},
        {"id":SOURCE_IDS["tests/sop-impact-expected-results.json"],"path":"tests/sop-impact-expected-results.json","sha256":SOURCE_HASHES["tests/sop-impact-expected-results.json"]},
        {"id":SOURCE_IDS["tests/handover-brief-expected-results.json"],"path":"tests/handover-brief-expected-results.json","sha256":SOURCE_HASHES["tests/handover-brief-expected-results.json"]},
    ], "expected-results accepted source identity binding")
    expected_task = expected.get("task_packet")
    manifest_task = manifest["task_packet"]
    same({key: expected_task.get(key) for key in ("id", "path", "sha256", "outcome_lines")},
         {key: manifest_task.get(key) for key in ("id", "path", "sha256", "outcome_lines")},
         "expected task packet mapping")
    _cross_check_sources(expected, files)
    count = _validate_semantics(expected, matrix)
    return count, files


def _read_inputs(args: argparse.Namespace) -> tuple[int, dict[str, Any], dict[str, Any], str, str, str, str]:
    root = Path(__file__).resolve().parents[1]
    expected, _, eh = read_json(args.expected, "timeline expected-results")
    matrix, _, mh = read_json(args.matrix, "timeline adversarial matrix")
    manifest_path = root / "contracts/decision-knowledge-timeline/frozen-sources.json"
    manifest, _, fh = read_json(manifest_path, "frozen source manifest")
    count, files = validate_documents(expected, matrix, manifest, root=root, task_packet=args.pinned_plan, expected_sha256=eh, matrix_sha256=mh, manifest_sha256=fh)
    return count, files, manifest, eh, mh, fh, root


def validate_receipt(receipt: dict[str, Any], expected: dict[str, Any], matrix: dict[str, Any], *, expected_sha256: str, matrix_sha256: str, manifest_sha256: str) -> int:
    need(receipt.get("schema_version") == 1 and receipt.get("receipt_type") == RECEIPT_TYPE and receipt.get("status") == "passed", "independent source-review receipt type/status/schema mismatch")
    author, reviewer = receipt.get("oracle_author_id"), receipt.get("reviewer_id")
    need(author == AUTHOR_ID and isinstance(reviewer, str) and reviewer.strip() and reviewer != author, "reviewer identity must be distinct from oracle author")
    need(receipt.get("independence_basis") == "independent-source-audit", "review receipt must record a source-based audit")
    need(receipt.get("same_model_fresh_session_only") is False, "fresh session alone is not independent review evidence")
    need(receipt.get("candidate_output_is_oracle") is False, "candidate output cannot inform review")
    need(receipt.get("source_audit_method") == "separate-source-reviewer-plus-independent-source-reconstruction", "review receipt must describe the separate source-audit method")
    need(receipt.get("reviewer_model") == "openai/gpt-6-luna", "reviewer model provenance must be transparent")
    need(isinstance(receipt.get("reviewer_execution_id"), str) and receipt["reviewer_execution_id"].strip(), "reviewer execution identity is required")
    disclosure = receipt.get("reviewer_independence_disclosure", "").lower()
    need("same-model" in disclosure and "not a different-model or human oracle" in disclosure and "session freshness alone" in disclosure, "receipt must disclose reviewer independence limits")
    audit_hash = receipt.get("source_audit_facts_sha256")
    need(isinstance(audit_hash, str) and len(audit_hash) == 64 and all(ch in "0123456789abcdef" for ch in audit_hash), "source audit must have a content-binding digest")
    same({key: receipt.get(key) for key in ("expected_results_sha256", "adversarial_matrix_sha256", "frozen_sources_sha256", "task_packet_sha256", "docket_packet_sha256")}, {
        "expected_results_sha256": expected_sha256,
        "adversarial_matrix_sha256": matrix_sha256,
        "frozen_sources_sha256": manifest_sha256,
        "task_packet_sha256": TASK_PACKET_SHA256,
        "docket_packet_sha256": DOCKET_PACKET_SHA256,
    }, "review artifact hash binding")
    same(receipt.get("reviewed_source_oracles"), [{"path":p,"sha256":h,"source_id":SOURCE_IDS[p]} for p,h in SOURCE_HASHES.items()], "reviewed source oracle hashes")
    facts = {"baseline":expected["baseline"],"scope":expected["scope"],"cases":expected["cases"]}
    same(receipt.get("reviewed_facts_sha256"), canonical_sha256(facts), "reviewed literal content digest")
    reviews = receipt.get("group_reviews")
    need(isinstance(reviews, list) and len(reviews) == 12, "receipt must review every one of twelve groups")
    cases = _case_map(expected)
    matrix_groups = {g["group_id"]:g for g in matrix["groups"]}
    assertion_count = sum(len(group["pinned_assertions"]) for group in matrix["groups"])
    same(receipt.get("review_summary"), {
        "all_group_assertion_counts_match": True,
        "all_matrix_pointers_resolve": True,
        "group8_full_shifted_cutoff_projections_pinned": True,
        "matrix_assertions": assertion_count,
        "outcomes_reconstructed": 12,
        "pre_1605_claim_paths_use_only_sop_impact": True,
        "record_edge_removal_keeps_timeline_claims_conflict_and_record_only_empty_unknown": True,
        "reseed_reconnect_read_only_and_cleanup_pinned": True,
        "restricted_acl_and_decoy_invariance_pinned": True,
        "timestamp_offset_and_rejection_pinned": True,
    }, "source-review coverage summary")
    for group_id, review in zip(GROUP_IDS, reviews):
        need(isinstance(review, dict) and review.get("group_id") == group_id and review.get("result") == "pass", f"source review missing or failed for {group_id}")
        same(review.get("expected_sha256"), canonical_sha256(cases[group_id]["expected"]), f"reviewed outcome digest {group_id}")
        same(review.get("matrix_assertion_ids"), [a["id"] for a in matrix_groups[group_id]["pinned_assertions"]], f"reviewed mapped assertions {group_id}")
        same(review.get("source_oracle_refs"), cases[group_id]["source_oracle_refs"], f"review source references {group_id}")
        same(review.get("packet_outcome_ref"), cases[group_id]["planner_refs"][0], f"review packet mapping {group_id}")
        need(isinstance(review.get("source_audit_note"), str) and review["source_audit_note"].strip(), f"review has no source comparison note: {group_id}")
    return len(reviews)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expected", required=True, type=Path)
    parser.add_argument("--matrix", required=True, type=Path)
    parser.add_argument("--pinned-plan", required=True, type=Path)
    parser.add_argument("--require-twelve-groups", action="store_true")
    parser.add_argument("--check-frozen-oracle-hashes", action="store_true")
    parser.add_argument("--require-review-receipt", action="store_true")
    parser.add_argument("--review-receipt", type=Path)
    args = parser.parse_args(argv)
    if not (args.require_twelve_groups and args.check_frozen_oracle_hashes and args.require_review_receipt):
        print("FAIL: all three acceptance flags are mandatory", file=sys.stderr)
        return 1
    try:
        count, files, manifest, eh, mh, fh, root = _read_inputs(args)
        expected, _, _ = read_json(args.expected, "timeline expected-results")
        matrix, _, _ = read_json(args.matrix, "timeline matrix")
        receipt_path = args.review_receipt or lane_receipt_path()
        receipt, _, _ = read_json(receipt_path, "independent source-review receipt")
        reviewed = validate_receipt(receipt, expected, matrix, expected_sha256=eh, matrix_sha256=mh, manifest_sha256=fh)
    except (ContractError, OSError, TypeError, KeyError, ValueError, IndexError) as exc:
        print(f"decision-knowledge-timeline contract: FAIL: {exc}", file=sys.stderr)
        return 1
    print(f"decision-knowledge-timeline contract: PASS (12 fixed groups, {count} literal assertions, {len(SOURCE_HASHES) + 1} unchanged accepted sources, {reviewed} source-reviewed groups; no candidate output used)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Independently recheck source mappings and validate the content-bound receipt.

The verifier reads the fixed planning/oracle files and the new contract only. It
does not execute or inspect product behavior or candidate output.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Any

import verify_decision_knowledge_timeline_contract as contract


def _need(condition: bool, message: str) -> None:
    contract.need(condition, message)


def _independent_source_audit(expected: dict[str, Any], files: dict[str, dict[str, Any]], plan_path: Path) -> dict[str, Any]:
    """Re-derive boundary facts from original source documents, not from output.

    This intentionally reads source-oracle structures directly rather than
    trusting the expected-results baseline or matrix as evidence.
    """
    plan_lines = plan_path.read_text(encoding="utf-8").splitlines()
    for line_no, heading in zip(contract.PACKET_GROUP_LINES, contract.PACKET_GROUP_PREFIXES):
        _need(plan_lines[line_no - 1].startswith(heading), f"task packet outcome mapping differs at line {line_no}")

    docket = files["tests/decision-provenance-expected-results.json"]
    sop = files["tests/sop-impact-expected-results.json"]
    handover = files["tests/handover-brief-expected-results.json"]
    docket_base = docket["baseline"]
    sop_case = next(case["expected"] for case in sop["cases"] if case["id"] == "sop-impact-conflict-provenance")
    handover_snapshot = handover["canonical_snapshot"]

    # Source facts independently read from the original (accepted) oracles.
    source_claims = []
    for docket_claim, sop_claim, applicability in zip(
        docket_base["claims"], sop_case["claim_evidence"], sop_case["applicability_edges"]
    ):
        _need(docket_claim["version_id"] == sop_claim["sop_version_id"], "docket/SOP source version mismatch")
        _need(docket_claim["role_id"] == sop_claim["role_id"], "docket/SOP source role mismatch")
        _need(docket_claim["source"]["id"] == sop_claim["source_id"], "docket/SOP source ID mismatch")
        _need(docket_claim["source"]["title"] == sop_claim["source_title"], "docket/SOP source title mismatch")
        _need(docket_claim["source"]["observed_at"] == sop_claim["observed_at"], "docket/SOP source observation time mismatch")
        _need(sop_claim["source_evidence_linked"] is True, "SOP claim source has no evidence link")
        _need(applicability["from"] == docket_claim["version_id"] and applicability["relationship"] == "APPLIES_TO", "applicability edge mismatch")
        _need(docket_claim["claim_edge"]["relationship"] == "CLAIMS_ROLE", "claim relationship mismatch")
        _need(docket_claim["source"]["evidence_link"]["relationship"] == "EVIDENCED_BY", "source evidence edge mismatch")
        source_claims.append({
            "version_id": docket_claim["version_id"],
            "role_id": docket_claim["role_id"],
            "source_id": docket_claim["source"]["id"],
            "source_title": docket_claim["source"]["title"],
            "observed_at": docket_claim["source"]["observed_at"],
            "claim_edge": docket_claim["claim_edge"],
            "source_edge": docket_claim["source"]["evidence_link"],
            "applicability_edge": applicability,
        })

    record = docket_base["decision_record"]
    record_time = record["record_source"]["observed_at"]
    _need(record["recorded_state"] == "open-unresolved" and record_time == "2026-10-14T16:05:00Z", "decision record/state source cutoff differs from accepted docket")
    _need(record["record_source"]["evidence_link"]["relationship"] == "EVIDENCED_BY", "decision record lacks its own evidence edge")
    _need(len(docket_base["considered_edges"]) == 2, "accepted docket must have exactly two considered-version edges")
    _need(sop_case["conflict_edge"]["status"] == "unresolved", "SOP conflict source status mismatch")
    _need(sop_case["claim_evidence"][0]["observed_at"] == "2026-10-14T16:00:00Z", "V2 observation boundary mismatch")
    _need(sop_case["claim_evidence"][1]["observed_at"] == "2026-10-14T16:04:00Z", "V3 observation boundary mismatch")
    paths = handover_snapshot["impact"]["paths"]
    _need(paths == next(c["expected"]["impact_paths"] for c in sop["cases"] if c["id"] == "sop-impact-observed-outage-paths"), "unchanged incident paths differ between accepted source oracles")

    cases = expected["cases"]
    _need(len(cases) == 12, "review must cover all twelve declared packet outcomes")
    by_id = {c["id"]: c["expected"] for c in cases}
    # Establish claims are retrieved through SOP-impact at early cutoffs.
    for group_id in ("v2-observation-boundary", "v2-unchanged-before-v3", "v3-and-graph-derived-conflict", "pre-record-evidence-no-existence-leak"):
        projection = by_id[group_id].get("projection")
        _need(isinstance(projection, dict), f"missing projection {group_id}")
        for visible in projection["claims"]:
            source = next((x for x in source_claims if x["version_id"] == visible["version_id"]), None)
            _need(source is not None, f"claim is not in accepted SOP-impact source path: {group_id}")
            _need(visible["discovery_path"].startswith("existing SOP-impact graph path"), f"claim discovery leaks future record path: {group_id}")
            for field, source_field in (("role_id","role_id"),("source","source_id")):
                if field == "role_id": _need(visible[field] == source[source_field], f"wrong claim role in {group_id}")
                else: _need(visible[field]["id"] == source[source_field], f"wrong claim source in {group_id}")
        if projection["known_at"] < record_time:
            _need(projection["decision_record"] is None and projection["decision_considers_edges"] == [], f"future record/CONSIDERS path leaks before source boundary: {group_id}")
    # Recheck the mutation cutoff from source values, including the hidden edge.
    shifted = by_id["v3-observation-time-shift"]
    pre_v3 = shifted["at_16_05"]
    _need(pre_v3["known_at"] == "2026-10-14T16:05:00Z", "shifted cutoff is wrong")
    _need([c["version_id"] for c in pre_v3["claims"]] == [source_claims[0]["version_id"]], "shifted pre-V3 output is not exactly source-known V2")
    _need(pre_v3["decision_record"]["recorded_state"] == record["recorded_state"], "record's own source-backed state must remain visible")
    _need(pre_v3["decision_considers_edges"] == [docket_base["considered_edges"][0]], "future V3 CONSIDERS link must be absent")
    _need(pre_v3["claim_context_state"] == "incomplete/unknown" and pre_v3["conflict"] is None, "shifted source gap must remain incomplete/unknown")
    _need(shifted["at_16_06"]["claims"][1]["source"]["observed_at"] == "2026-10-14T16:06:00Z", "V3 must return only at shifted source cutoff")
    # Permission/unknown/timestamp assertions are cross-checked against original source cases.
    cold = next(c["expected"] for c in handover["cases"] if c["id"] == "cold-empty-unknown")
    _need(cold["board_state"] == "empty/unknown" and cold["risk_or_conflict_absence_concluded"] is False, "accepted cold source no longer means unknown")
    denied = next(c["expected"] for c in sop["cases"] if c["id"] == "sop-impact-denied-source-invariance")
    _need(denied["operations_response_semantically_identical_before_after"] is True and denied["hidden_count_or_existence_disclosed"] is False, "accepted ACL source semantics drifted")
    return {
        "reviewed_source_facts": {
            "v2_source": source_claims[0], "v3_source": source_claims[1],
            "record_id": record["id"], "record_source_time": record_time,
            "record_state": record["recorded_state"], "record_source_edge": record["record_source"]["evidence_link"],
            "conflict_edge": sop_case["conflict_edge"], "incident_paths": paths,
        },
        "checked_separation": "pre-16:05 claims are read from the accepted SOP-impact APPLIES_TO/CLAIMS_ROLE/EVIDENCED_BY evidence path; no decision record/CONSIDERS is used until its own 16:05 source is known",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expected", required=True, type=Path)
    parser.add_argument("--matrix", required=True, type=Path)
    parser.add_argument("--pinned-plan", required=True, type=Path)
    parser.add_argument("--require-independent-source-audit", action="store_true")
    parser.add_argument("--evidence-dir-from-workshop-lane", action="store_true")
    parser.add_argument("--receipt", type=Path)
    args = parser.parse_args(argv)
    if not (args.require_independent_source_audit and args.evidence_dir_from_workshop_lane):
        print("FAIL: independent source audit and workshop-lane evidence flags are mandatory", file=sys.stderr)
        return 1
    try:
        root = Path(__file__).resolve().parents[1]
        expected, _, eh = contract.read_json(args.expected, "timeline expected-results")
        matrix, _, mh = contract.read_json(args.matrix, "timeline matrix")
        manifest, _, fh = contract.read_json(root / "contracts/decision-knowledge-timeline/frozen-sources.json", "frozen source manifest")
        count, files = contract.validate_documents(expected, matrix, manifest, root=root, task_packet=args.pinned_plan, expected_sha256=eh, matrix_sha256=mh, manifest_sha256=fh)
        audit = _independent_source_audit(expected, files, args.pinned_plan)
        receipt_path = args.receipt or contract.lane_receipt_path()
        receipt, _, _ = contract.read_json(receipt_path, "independent source-review receipt")
        reviewed = contract.validate_receipt(receipt, expected, matrix, expected_sha256=eh, matrix_sha256=mh, manifest_sha256=fh)
        _need(receipt.get("source_audit_method") == "separate-source-reviewer-plus-independent-source-reconstruction", "receipt must document an independent source reconstruction, not only session freshness")
        _need(receipt.get("source_audit_facts_sha256") == contract.canonical_sha256(audit), "receipt source-audit facts are not content-bound")
        _need(receipt.get("same_model_fresh_session_only") is False and receipt.get("reviewer_model") == "openai/gpt-6-luna", "review provenance must be transparent and not claim model diversity")
    except (contract.ContractError, OSError, TypeError, KeyError, ValueError, IndexError) as exc:
        print(f"decision-knowledge-timeline independent review: FAIL: {exc}", file=sys.stderr)
        return 1
    print(f"decision-knowledge-timeline independent review: PASS ({reviewed} groups source-audited, {count} mapped assertions, {len(audit['reviewed_source_facts'])} source-fact sections; candidate output not used)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

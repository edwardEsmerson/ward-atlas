#!/usr/bin/env python3
"""Validate a fresh independent receipt for the nine pinned cross-case cases.

The receipt is audit evidence, not a new oracle. This validator first re-runs
the immutable artifact/source checks, then binds every independent comparison
to the exact packet, expected-results bytes, matrix, and all frozen baseline
hashes. It does not read product behavior or candidate output.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

import verify_cross_case_handover_contract as contract


REVIEW_EVIDENCE_ROOT = Path("/workspace/evidence/cross-case-handover-oracle-review")


def require(condition: bool, message: str) -> None:
    if not condition:
        raise contract.ContractFailure(message)


def _load_report(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise contract.ContractFailure(f"cannot load independent review receipt {path}: {exc}") from exc
    require(isinstance(value, dict), "independent review receipt must be a JSON object")
    return value


def review_path_for_lane(lane: str | None) -> Path:
    require(isinstance(lane, str) and re.fullmatch(r"[A-Za-z0-9_-]+", lane) is not None, "WORKSHOP_LANE must be a nonempty safe lane name")
    return REVIEW_EVIDENCE_ROOT / lane / "review.json"


def _source_rows(expected: dict[str, Any]) -> list[dict[str, str]]:
    return [
        {"id": row["id"], "file": row["file"], "sha256": row["sha256"]}
        for row in expected["pinned_sources"]
    ]


def validate_review_record(
    report: dict[str, Any],
    expected: dict[str, Any],
    matrix: dict[str, Any],
    manifest: dict[str, Any],
    *,
    expected_sha: str,
    matrix_sha: str,
    manifest_sha: str,
    packet_sha: str,
) -> tuple[int, int]:
    """Validate identity, content bindings, and all nine assertion comparisons."""
    require(report.get("schema_version") == 1, "review receipt schema_version must be 1")
    require(report.get("status") == "passed", "independent review status is not passed")
    author = expected.get("oracle_author")
    reviewer = report.get("reviewer_id")
    require(isinstance(author, str) and author.strip(), "expected oracle author identity is absent")
    require(isinstance(reviewer, str) and reviewer.strip() and reviewer != author, "reviewer must be identified separately from the oracle author")
    require(report.get("reviewer_is_author") is False, "oracle author cannot review their own packet")
    require(report.get("fresh_independent_verifier") is True, "receipt is not marked as a fresh independent verification")
    require(report.get("independently_read_packet_and_sources") is True, "reviewer must read the pinned packet and cited source oracles")
    require(report.get("candidate_output_used") is False, "candidate output cannot be used as an oracle")
    require(isinstance(report.get("reviewer_session_id"), str) and report["reviewer_session_id"].strip(), "fresh verifier session identity is missing")
    require(isinstance(report.get("review_method"), str) and report["review_method"].strip(), "review method is missing")

    require(report.get("packet_sha256") == packet_sha == contract.PACKET_SHA256, "review receipt is not bound to the exact pinned packet")
    require(report.get("expected_sha256") == expected_sha == contract.EXPECTED_ARTIFACT_SHA256, "review receipt is not bound to exact expected-results bytes")
    require(report.get("matrix_sha256") == matrix_sha == contract.MATRIX_ARTIFACT_SHA256, "review receipt is not bound to exact matrix bytes")
    require(report.get("frozen_hash_manifest_sha256") == manifest_sha == contract.FROZEN_HASH_MANIFEST_SHA256, "review receipt is not bound to the exact frozen hash manifest")
    require(report.get("reviewed_pinned_sources") == _source_rows(expected), "review did not bind every pinned source file/hash")
    require(report.get("frozen_baseline_hashes") == manifest.get("files"), "review did not bind every frozen baseline hash")
    require(report.get("frozen_baseline_hash_count") == contract.FROZEN_FILE_COUNT, "reviewed frozen baseline count changed")

    expected_ids = list(contract.REQUIRED_CASES)
    require(report.get("reviewed_group_ids") == expected_ids, "review receipt is missing or reorders one of the nine groups")
    groups = report.get("group_comparisons")
    require(isinstance(groups, list) and len(groups) == 9, "review receipt must contain nine exact group comparisons")
    require(report.get("exact_group_comparison_count") == 9, "review receipt does not report all nine comparisons")

    total = 0
    all_comparisons = 0
    for index, (row, outcome, matrix_case) in enumerate(zip(groups, expected["cases"], matrix["cases"])):
        case_id = contract.REQUIRED_CASES[index]
        require(row.get("case_id") == case_id, f"review comparison group {index+1} is missing/changed: {case_id}")
        require(row.get("result") == "exact-match" and row.get("difference_count") == 0, f"review did not report an exact comparison for {case_id}")
        require(row.get("expected_outcome_sha256") == contract.canonical_sha(outcome["expected"]), f"review outcome fingerprint changed for {case_id}")
        require(row.get("matrix_case_sha256") == contract.canonical_sha(matrix_case), f"review matrix fingerprint changed for {case_id}")

        assertions = matrix_case["pinned_assertions"]
        reported = row.get("assertion_comparisons")
        require(isinstance(reported, list) and len(reported) == len(assertions) and len(reported) > 0, f"review must compare every nonzero mapped assertion for {case_id}")
        require(row.get("assertion_count") == len(assertions), f"reviewed assertion count differs for {case_id}")
        for actual, pinned in zip(reported, assertions):
            assertion_id = pinned["id"]
            pointer = pinned["expected_pointer"]
            fixed_value = contract._resolve_pointer(expected, pointer)
            value_hash = contract.canonical_sha(fixed_value)
            require(actual.get("assertion_id") == assertion_id, f"review omitted/reordered assertion {assertion_id}")
            require(actual.get("expected_pointer") == pointer, f"review changed assertion pointer for {assertion_id}")
            require(actual.get("comparison") == "exact-match" and actual.get("difference_count") == 0, f"review assertion is not an exact match: {assertion_id}")
            require(actual.get("pinned_value_sha256") == value_hash and actual.get("independently_read_value_sha256") == value_hash, f"review comparison is not content-bound for {assertion_id}")
            require(actual.get("candidate_output_used") is False, f"candidate output was used for {assertion_id}")
            all_comparisons += 1
        total += len(assertions)

    require(total == contract.REQUIRED_ASSERTION_COUNT and all_comparisons == contract.REQUIRED_ASSERTION_COUNT, "review receipt does not contain every mapped assertion comparison")
    require(report.get("exact_assertion_comparison_count") == all_comparisons, "review assertion comparison count changed")
    require(report.get("mapped_assertion_count") == total, "review mapped assertion total changed")
    require(report.get("all_groups_have_nonzero_mappings") is True, "review must confirm every group has a nonzero mapping")
    require(report.get("all_nine_expected_outcomes_exact") is True, "review must explicitly confirm exact outcomes across all groups")
    return 9, total


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report-from-workshop-lane", action="store_true")
    parser.add_argument("--expected", type=Path, required=True)
    parser.add_argument("--matrix", type=Path, required=True)
    parser.add_argument("--hash-manifest", type=Path, required=True)
    parser.add_argument("--pinned-plan", type=Path, required=True)
    parser.add_argument("--require-independent-review", action="store_true")
    parser.add_argument("--require-nine-groups", action="store_true")
    parser.add_argument("--require-nonzero-mappings", action="store_true")
    parser.add_argument("--check-frozen-oracle-hashes", action="store_true")
    args = parser.parse_args(argv)
    root = Path(__file__).resolve().parents[1]
    try:
        require(args.report_from_workshop_lane, "--report-from-workshop-lane is required")
        require(args.require_independent_review, "--require-independent-review is required")
        require(args.require_nine_groups, "--require-nine-groups is required")
        require(args.require_nonzero_mappings, "--require-nonzero-mappings is required")
        require(args.check_frozen_oracle_hashes, "--check-frozen-oracle-hashes is required")
        expected, expected_sha = contract._load_json(args.expected)
        matrix, matrix_sha = contract._load_json(args.matrix)
        manifest, manifest_sha = contract._load_json(args.hash_manifest)
        mapped, _ = contract.validate_artifacts(
            expected, matrix, manifest, expected_sha, matrix_sha, manifest_sha,
            root=root,
            pinned_plan=args.pinned_plan,
            require_nine_groups=True,
            check_frozen_oracle_hashes=True,
        )
        require(mapped == contract.REQUIRED_ASSERTION_COUNT, "contract validator returned an unexpected assertion count")
        packet_sha = contract.verify_pinned_packet(args.pinned_plan)
        lane = os.environ.get("WORKSHOP_LANE")
        report_path = review_path_for_lane(lane)
        report = _load_report(report_path)
        groups, assertions = validate_review_record(
            report, expected, matrix, manifest,
            expected_sha=expected_sha,
            matrix_sha=matrix_sha,
            manifest_sha=manifest_sha,
            packet_sha=packet_sha,
        )
    except contract.ContractFailure as exc:
        print(f"CROSS_CASE_REVIEW_FAILED: {exc}", file=sys.stderr)
        return 1
    print(json.dumps({
        "status":"passed",
        "review_receipt":str(report_path),
        "reviewed_groups":groups,
        "exact_assertion_comparisons":assertions,
        "packet_expected_matrix_and_frozen_hashes_bound":True,
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

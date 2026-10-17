#!/usr/bin/env python3
"""Validate an independent, content-bound audit receipt for the docket oracle.

The receipt is an auditable reviewer claim, not cryptographic identity proof.
This verifier binds it to the immutable packet, unchanged source oracles,
frozen pre-task inventory, all nine expected outcomes, and exact matrix IDs.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import verify_decision_provenance_contract as contract


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expected", required=True, type=Path)
    parser.add_argument("--matrix", required=True, type=Path)
    parser.add_argument("--pinned-plan", required=True, type=Path)
    parser.add_argument("--require-independent-review", action="store_true")
    parser.add_argument("--evidence-dir-from-workshop-lane", action="store_true")
    parser.add_argument("--review-receipt", type=Path, help="optional explicit receipt path for focused tests")
    args = parser.parse_args(argv)
    if not (args.require_independent_review and args.evidence_dir_from_workshop_lane):
        print("FAIL: --require-independent-review and --evidence-dir-from-workshop-lane are mandatory", file=sys.stderr)
        return 1
    root = Path(__file__).resolve().parents[1]
    try:
        expected, _, expected_hash = contract._read_json(args.expected, "expected-results")
        matrix, _, matrix_hash = contract._read_json(args.matrix, "adversarial matrix")
        count, sources = contract.validate_artifacts(
            expected,
            matrix,
            expected_hash,
            matrix_hash,
            root=root,
            pinned_plan=args.pinned_plan,
        )
        receipt_path = args.review_receipt or contract.receipt_path_from_workshop_lane()
        receipt, _, _ = contract._read_json(receipt_path, "independent source-review receipt")
        reviewed = contract.validate_review_receipt(
            receipt,
            expected,
            matrix,
            expected_sha256=expected_hash,
            matrix_sha256=matrix_hash,
            root=root,
        )
    except (contract.ContractError, OSError, TypeError, KeyError, ValueError) as exc:
        print(f"decision-provenance independent review: FAIL: {exc}", file=sys.stderr)
        return 1
    print(
        "decision-provenance independent review: PASS "
        f"({reviewed}/{len(contract.REQUIRED_GROUPS)} ordered groups bound to {count} assertions; "
        f"pinned packet, {len(contract.SOURCE_ORACLES)} unchanged source oracles, and "
        f"{sources['preexisting-oracle-and-matrix-count']} frozen source hashes verified; "
        f"receipt={receipt_path})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

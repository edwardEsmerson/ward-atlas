"""Mutation tests for the immutable source-observation branch oracle."""

from __future__ import annotations

import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from scripts import verify_decision_source_branch_contract as contract


ROOT = Path(__file__).resolve().parents[1]
EXPECTED_PATH = ROOT / "contracts/decision-source-branch/expected-results.json"
MATRIX_PATH = ROOT / "contracts/decision-source-branch/adversarial-matrix.json"
PLAN_PATH = Path(
    "/workspace/worktrees/planner-4889c5b5d5d542cb9a7d33b48d7ca1b0/plans/"
    "planner-4889c5b5d5d542cb9a7d33b48d7ca1b0.md"
)


def documents():
    return (
        json.loads(EXPECTED_PATH.read_text(encoding="utf-8")),
        json.loads(MATRIX_PATH.read_text(encoding="utf-8")),
    )


def group(document, group_id):
    return next(item for item in document["groups"] if item.get("id", item.get("group_id")) == group_id)


class DecisionSourceBranchContractTests(unittest.TestCase):
    def test_frozen_packet_has_eight_groups_and_74_literal_mappings(self):
        expected, matrix = documents()
        self.assertEqual(contract.validate_documents(expected, matrix, root=ROOT), 74)

    def test_rejects_changed_branch_timestamp(self):
        expected, matrix = documents()
        group(expected, "branch-at-16-06-restoration")["expected"]["projection"]["claims"][1]["source"][
            "observed_at"
        ] = "2026-10-14T16:05:00Z"
        with self.assertRaisesRegex(contract.ContractError, "accepted source-time mutation oracle"):
            contract.validate_documents(expected, matrix, root=ROOT)

    def test_rejects_hidden_branch_fallback_to_mainline(self):
        expected, matrix = documents()
        edge_case = group(expected, "missing-edge-no-fallback-and-reseed")["expected"]
        edge_case["missing_branch_to_override_edge_result"]["mainline_fallback_used"] = True
        with self.assertRaisesRegex(contract.ContractError, "without mainline fallback"):
            contract.validate_documents(expected, matrix, root=ROOT)

    def test_rejects_v3_fallback_in_1605_branch_projection(self):
        expected, matrix = documents()
        source_timeline = json.loads(
            (ROOT / "contracts/decision-knowledge-timeline/expected-results.json").read_text(encoding="utf-8")
        )
        shift = next(case for case in source_timeline["cases"] if case["id"] == "v3-observation-time-shift")
        v3 = copy.deepcopy(shift["expected"]["at_16_06"]["claims"][1])
        group(expected, "branch-at-16-05-suppression")["expected"]["projection"]["claims"].append(v3)
        with self.assertRaisesRegex(contract.ContractError, "source-time mutation oracle"):
            contract.validate_documents(expected, matrix, root=ROOT)

    def test_rejects_mainline_source_fact_mutation(self):
        expected, matrix = documents()
        mainline = group(expected, "mainline-isolation")["expected"]["mainline_at_16_05"]
        mainline["claims"][1]["source"]["observed_at"] = "2026-10-14T16:06:00Z"
        with self.assertRaisesRegex(contract.ContractError, "accepted timeline oracle"):
            contract.validate_documents(expected, matrix, root=ROOT)

    def test_rejects_missing_assertion_mapping(self):
        expected, matrix = documents()
        matrix["groups"][0]["pinned_assertions"].pop()
        with self.assertRaisesRegex(contract.ContractError, "assertion count"):
            contract.validate_documents(expected, matrix, root=ROOT)

    def test_rejects_candidate_derived_expectation_marker(self):
        expected, matrix = documents()
        group(expected, "mainline-at-16-05")["expected"]["candidate_output"] = "copied response"
        with self.assertRaisesRegex(contract.ContractError, "candidate-derived expectation marker"):
            contract.validate_documents(expected, matrix, root=ROOT)

    def test_rejects_changed_frozen_source_bytes(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "accepted-oracle.json"
            source.write_bytes(b'{"pinned": true}\n')
            manifest_entries = [{
                "path": "accepted-oracle.json",
                "sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
            }]
            source.write_bytes(b'{"pinned": false}\n')
            with self.assertRaisesRegex(contract.ContractError, "frozen source hash changed"):
                contract.validate_frozen_hash_entries(manifest_entries, root)

    def test_review_receipt_is_content_bound_to_frozen_files(self):
        total, _ = contract.verify_contract(
            EXPECTED_PATH,
            MATRIX_PATH,
            PLAN_PATH,
            ROOT / "contracts/decision-source-branch/independent-review.json",
            require_review_receipt=True,
            root=ROOT,
        )
        self.assertEqual(total, 74)


if __name__ == "__main__":
    unittest.main()

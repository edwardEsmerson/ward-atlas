"""Mutation tests for the frozen handover-known-time oracle and matrix."""

from __future__ import annotations

import copy
import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import verify_handover_known_time_contract as contract  # noqa: E402


class HandoverKnownTimeContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.expected_bytes = (ROOT / "tests/handover-known-time-expected-results.json").read_bytes()
        cls.matrix_bytes = (ROOT / "tests/handover-known-time-adversarial-matrix.json").read_bytes()
        cls.expected = json.loads(cls.expected_bytes)
        cls.matrix = json.loads(cls.matrix_bytes)
        cls.expected_sha = hashlib.sha256(cls.expected_bytes).hexdigest()
        cls.matrix_sha = hashlib.sha256(cls.matrix_bytes).hexdigest()

    def test_twelve_groups_and_nonzero_exact_mappings(self):
        count = contract.validate_structure(self.expected, self.matrix, require_twelve_groups=True)
        self.assertEqual(count, 54)
        self.assertEqual([row["id"] for row in self.expected["cases"]], list(contract.REQUIRED_CASES))
        self.assertEqual([row["scenario_id"] for row in self.matrix["cases"]], list(contract.REQUIRED_CASES))
        self.assertTrue(all(row["nonzero_assertion_count"] > 0 for row in self.matrix["cases"]))
        self.assertEqual(len(self.expected["frozen_existing_oracle_hashes"]), 24)

    def test_missing_group_empty_mapping_and_wrong_pointer_are_rejected(self):
        missing = copy.deepcopy(self.expected)
        missing["cases"].pop()
        with self.assertRaisesRegex(contract.ContractFailure, "exactly twelve groups"):
            contract.validate_structure(missing, self.matrix, True)

        empty = copy.deepcopy(self.matrix)
        empty["cases"][0]["pinned_assertions"] = []
        empty["cases"][0]["nonzero_assertion_count"] = 0
        with self.assertRaisesRegex(contract.ContractFailure, "no nonzero assertion mappings"):
            contract.validate_structure(self.expected, empty, True)

        wrong_pointer = copy.deepcopy(self.matrix)
        wrong_pointer["cases"][1]["pinned_assertions"][0]["expected_pointer"] = "/cases/0/expected/event"
        with self.assertRaisesRegex(contract.ContractFailure, "assertion-to-outcome mapping changed"):
            contract.validate_structure(self.expected, wrong_pointer, True)

    def test_changed_or_missing_fixed_outcomes_are_rejected(self):
        mutations = (
            ("path", lambda value: value["cases"][1]["expected"]["impact_paths"][0].__setitem__(-1, "ROLE-WRONG")),
            ("event source", lambda value: value["cases"][1]["expected"]["event"]["source"].__setitem__("source_id", "SRC-WRONG")),
            ("event title", lambda value: value["cases"][2]["expected"]["event"]["source"].__setitem__("title", "wrong title")),
            ("known time", lambda value: value["cases"][1]["expected"].__setitem__("known_at", "2026-10-14T19:15:00Z")),
            ("source cutoff", lambda value: value["cases"][4]["expected"]["path_evidence"][0]["observed_at"].__setitem__(-1, "2026-10-14T18:05:00Z")),
            ("unknown state", lambda value: value["cases"][3]["expected"].__setitem__("event_state", "available")),
            ("unknown leak", lambda value: value["cases"][3]["expected"].__setitem__("event_existence_or_count_hint_disclosed", True)),
            ("confirmation", lambda value: value["cases"][9]["expected"]["confirmation"].__setitem__("required", False)),
            ("ACL scope", lambda value: value["cases"][10]["expected"].__setitem__("viewer_scope", "public")),
        )
        for label, mutate in mutations:
            with self.subTest(label=label):
                changed = copy.deepcopy(self.expected)
                mutate(changed)
                with self.assertRaises((contract.ContractFailure, KeyError)):
                    contract.validate_fixed_outcomes(changed, ROOT)

        missing_field = copy.deepcopy(self.expected)
        del missing_field["cases"][1]["expected"]["path_evidence"]
        with self.assertRaises((contract.ContractFailure, KeyError)):
            contract.validate_fixed_outcomes(missing_field, ROOT)

    def test_pinned_source_and_frozen_baseline_hash_mutations_are_rejected(self):
        changed_source = copy.deepcopy(self.expected)
        changed_source["pinned_sources"][0]["sha256"] = "0" * 64
        with self.assertRaisesRegex(contract.ContractFailure, "pinned plan/source hashes changed"):
            contract.validate_structure(changed_source, self.matrix, True)

        changed_event_source = copy.deepcopy(self.expected)
        changed_event_source["pinned_sources"][4]["sha256"] = "0" * 64
        with self.assertRaisesRegex(contract.ContractFailure, "pinned plan/source hashes changed"):
            contract.validate_structure(changed_event_source, self.matrix, True)

        changed_baseline = copy.deepcopy(self.expected)
        changed_baseline["frozen_existing_oracle_hashes"][0]["sha256"] = "0" * 64
        with self.assertRaisesRegex(contract.ContractFailure, "frozen pre-task oracle/matrix hashes changed"):
            contract.validate_structure(changed_baseline, self.matrix, True)

        with self.assertRaisesRegex(contract.ContractFailure, "expected-results bytes differ"):
            contract.verify_artifact_digests("0" * 64, self.matrix_sha)
        with self.assertRaisesRegex(contract.ContractFailure, "adversarial matrix bytes differ"):
            contract.verify_artifact_digests(self.expected_sha, "0" * 64)

    def test_immutable_sources_are_byte_pinned(self):
        loaded = contract.verify_existing_oracles(ROOT, self.expected)
        self.assertIn("tests/expected-results.json", loaded)
        self.assertIn("tests/shift-board-expected-results.json", loaded)
        self.assertIn("tests/sop-impact-expected-results.json", loaded)
        self.assertIn("tests/handover-brief-expected-results.json", loaded)
        self.assertIn("tests/handover-drift-expected-results.json", loaded)
        event_oracle = contract.verify_accepted_event_time_sources(ROOT)
        self.assertEqual(len(event_oracle["cases"]), 12)

    def test_supported_review_receipt_is_supplemental_and_hash_bound(self):
        receipt = {
            "schema_version": 1,
            "status": "passed",
            "reviewer": "fresh-independent-review-session",
            "independent_of_oracle_author": True,
            "candidate_output_used": False,
            "expected_sha256": self.expected_sha,
            "matrix_sha256": self.matrix_sha,
            "pinned_plan_sha256": contract.PACKET_SHA256,
            "reviewed_case_ids": list(contract.REQUIRED_CASES),
            "mapped_assertion_count": 54,
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "review-receipt.json"
            path.write_text(json.dumps(receipt), encoding="utf-8")
            contract.validate_review_receipt(path, self.expected_sha, self.matrix_sha, 54)
            receipt["matrix_sha256"] = "0" * 64
            path.write_text(json.dumps(receipt), encoding="utf-8")
            with self.assertRaisesRegex(contract.ContractFailure, "does not match these exact oracle/matrix bytes"):
                contract.validate_review_receipt(path, self.expected_sha, self.matrix_sha, 54)


if __name__ == "__main__":
    unittest.main()

"""Mutation tests for the content-bound fresh cross-case review receipt."""

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

import verify_cross_case_handover_contract as contract  # noqa: E402
import verify_cross_case_handover_review as review  # noqa: E402


def _json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


class CrossCaseHandoverReviewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.expected_path = ROOT / "tests/cross-case-handover-expected-results.json"
        cls.matrix_path = ROOT / "tests/cross-case-handover-adversarial-matrix.json"
        cls.manifest_path = ROOT / "tests/cross-case-handover-frozen-hashes.json"
        cls.expected = _json(cls.expected_path)
        cls.matrix = _json(cls.matrix_path)
        cls.manifest = _json(cls.manifest_path)
        cls.expected_sha = hashlib.sha256(cls.expected_path.read_bytes()).hexdigest()
        cls.matrix_sha = hashlib.sha256(cls.matrix_path.read_bytes()).hexdigest()
        cls.manifest_sha = hashlib.sha256(cls.manifest_path.read_bytes()).hexdigest()
        cls.packet_sha = contract.PACKET_SHA256

    def make_report(self):
        groups = []
        total = 0
        for outcome, matrix_case in zip(self.expected["cases"], self.matrix["cases"]):
            comparisons = []
            for assertion in matrix_case["pinned_assertions"]:
                value_hash = contract.canonical_sha(contract._resolve_pointer(self.expected, assertion["expected_pointer"]))
                comparisons.append({
                    "assertion_id":assertion["id"],
                    "expected_pointer":assertion["expected_pointer"],
                    "pinned_value_sha256":value_hash,
                    "independently_read_value_sha256":value_hash,
                    "comparison":"exact-match",
                    "difference_count":0,
                    "candidate_output_used":False,
                })
            total += len(comparisons)
            groups.append({
                "case_id":outcome["id"],
                "expected_outcome_sha256":contract.canonical_sha(outcome["expected"]),
                "matrix_case_sha256":contract.canonical_sha(matrix_case),
                "assertion_count":len(comparisons),
                "difference_count":0,
                "result":"exact-match",
                "assertion_comparisons":comparisons,
            })
        return {
            "schema_version":1,
            "status":"passed",
            "reviewer_id":"fresh-independent-source-reviewer",
            "reviewer_session_id":"fresh-child-session-cross-case-oracle-review",
            "reviewer_is_author":False,
            "fresh_independent_verifier":True,
            "independently_read_packet_and_sources":True,
            "candidate_output_used":False,
            "review_method":"Read the pinned packet and unchanged source oracles independently; compared each expected field and every mapped assertion without candidate output.",
            "packet_sha256":self.packet_sha,
            "expected_sha256":self.expected_sha,
            "matrix_sha256":self.matrix_sha,
            "frozen_hash_manifest_sha256":self.manifest_sha,
            "reviewed_pinned_sources":[
                {"id":row["id"],"file":row["file"],"sha256":row["sha256"]}
                for row in self.expected["pinned_sources"]
            ],
            "frozen_baseline_hashes":copy.deepcopy(self.manifest["files"]),
            "frozen_baseline_hash_count":len(self.manifest["files"]),
            "reviewed_group_ids":list(contract.REQUIRED_CASES),
            "exact_group_comparison_count":9,
            "group_comparisons":groups,
            "exact_assertion_comparison_count":total,
            "mapped_assertion_count":total,
            "all_groups_have_nonzero_mappings":True,
            "all_nine_expected_outcomes_exact":True,
        }

    def validate(self, report):
        return review.validate_review_record(
            report,
            self.expected,
            self.matrix,
            self.manifest,
            expected_sha=self.expected_sha,
            matrix_sha=self.matrix_sha,
            manifest_sha=self.manifest_sha,
            packet_sha=self.packet_sha,
        )

    def test_all_nine_exact_groups_and_sixty_six_comparisons_pass(self):
        groups, assertions = self.validate(self.make_report())
        self.assertEqual(groups, 9)
        self.assertEqual(assertions, 66)

    def test_missing_group_and_missing_exact_assertion_evidence_fail(self):
        report = self.make_report()
        report["reviewed_group_ids"].pop()
        with self.assertRaisesRegex(contract.ContractFailure, "missing or reorders"):
            self.validate(report)

        report = self.make_report()
        report["group_comparisons"].pop()
        with self.assertRaisesRegex(contract.ContractFailure, "nine exact group comparisons"):
            self.validate(report)

        report = self.make_report()
        report["group_comparisons"][4]["assertion_comparisons"].pop()
        with self.assertRaisesRegex(contract.ContractFailure, "every nonzero mapped assertion"):
            self.validate(report)

        report = self.make_report()
        report["group_comparisons"][7]["assertion_comparisons"][0]["comparison"] = "not-compared"
        with self.assertRaisesRegex(contract.ContractFailure, "not an exact match"):
            self.validate(report)

    def test_changed_expected_matrix_packet_manifest_and_frozen_hash_bindings_fail(self):
        mutations = [
            ("packet", "packet_sha256", "0" * 64, "exact pinned packet"),
            ("expected", "expected_sha256", "0" * 64, "exact expected-results bytes"),
            ("matrix", "matrix_sha256", "0" * 64, "exact matrix bytes"),
            ("hash manifest", "frozen_hash_manifest_sha256", "0" * 64, "exact frozen hash manifest"),
        ]
        for label, field, value, error in mutations:
            with self.subTest(binding=label):
                altered = self.make_report()
                altered[field] = value
                with self.assertRaisesRegex(contract.ContractFailure, error):
                    self.validate(altered)

        missing_frozen_hash = self.make_report()
        missing_frozen_hash["frozen_baseline_hashes"].pop()
        with self.assertRaisesRegex(contract.ContractFailure, "every frozen baseline hash"):
            self.validate(missing_frozen_hash)

        changed_source_hash = self.make_report()
        changed_source_hash["reviewed_pinned_sources"][2]["sha256"] = "0" * 64
        with self.assertRaisesRegex(contract.ContractFailure, "every pinned source file/hash"):
            self.validate(changed_source_hash)

        changed_frozen_value = self.make_report()
        changed_frozen_value["frozen_baseline_hashes"][0]["sha256"] = "0" * 64
        with self.assertRaisesRegex(contract.ContractFailure, "every frozen baseline hash"):
            self.validate(changed_frozen_value)

    def test_reviewer_must_be_fresh_distinct_and_not_use_candidate_output(self):
        report = self.make_report()
        report["reviewer_id"] = self.expected["oracle_author"]
        with self.assertRaisesRegex(contract.ContractFailure, "identified separately"):
            self.validate(report)

        report = self.make_report()
        report["fresh_independent_verifier"] = False
        with self.assertRaisesRegex(contract.ContractFailure, "fresh independent"):
            self.validate(report)

        report = self.make_report()
        report["candidate_output_used"] = True
        with self.assertRaisesRegex(contract.ContractFailure, "candidate output"):
            self.validate(report)

        report = self.make_report()
        report["group_comparisons"][0]["assertion_comparisons"][0]["candidate_output_used"] = True
        with self.assertRaisesRegex(contract.ContractFailure, "candidate output was used"):
            self.validate(report)

    def test_changed_group_or_outcome_fingerprint_and_zero_mapping_fail(self):
        report = self.make_report()
        report["group_comparisons"][2]["expected_outcome_sha256"] = "0" * 64
        with self.assertRaisesRegex(contract.ContractFailure, "outcome fingerprint changed"):
            self.validate(report)

        report = self.make_report()
        report["group_comparisons"][0]["assertion_count"] = 0
        with self.assertRaisesRegex(contract.ContractFailure, "assertion count differs"):
            self.validate(report)

        report = self.make_report()
        report["all_groups_have_nonzero_mappings"] = False
        with self.assertRaisesRegex(contract.ContractFailure, "nonzero mapping"):
            self.validate(report)

    def test_missing_report_evidence_and_unsafe_lane_path_fail_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            missing = Path(directory) / "review.json"
            with self.assertRaisesRegex(contract.ContractFailure, "cannot load independent review receipt"):
                review._load_report(missing)
        with self.assertRaisesRegex(contract.ContractFailure, "safe lane name"):
            review.review_path_for_lane("../other-lane")
        self.assertEqual(
            review.review_path_for_lane("2"),
            Path("/workspace/evidence/cross-case-handover-oracle-review/2/review.json"),
        )


if __name__ == "__main__":
    unittest.main()

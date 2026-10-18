"""Mutation tests for the evidence-bound branch/timeline status checker."""

from __future__ import annotations

import copy
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import verify_source_branch_status_docs as checker  # noqa: E402


class SourceBranchStatusDocsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.morning = (ROOT / "MORNING-STATUS.md").read_text(encoding="utf-8")
        cls.matrix = (ROOT / "FEATURE-MATRIX.md").read_text(encoding="utf-8")
        cls.result, _ = checker._load_json(checker.PINNED_RESULT, "task result")
        cls.report, cls.report_bytes = checker._load_json(checker.PINNED_REPORT, "verification report")
        cls.sha_text = checker.PINNED_SHA256_FILE.read_text(encoding="utf-8")

    def reject_docs(self, morning=None, matrix=None):
        with self.assertRaises(checker.VerificationError):
            checker.validate_status_documents(
                self.morning if morning is None else morning,
                self.matrix if matrix is None else matrix,
                reject_pending_claims=True,
                reject_unverified_video_claims=True,
            )

    def test_accepted_docs_and_source_bound_report_pass(self):
        checker.validate_report_hash(self.report_bytes, self.sha_text)
        checker.validate_report(self.report, self.result, checker.PINNED_SOURCE_COMMIT)
        checker.validate_status_documents(
            self.morning,
            self.matrix,
            reject_pending_claims=True,
            reject_unverified_video_claims=True,
        )

    def test_report_byte_or_hash_mutation_is_rejected(self):
        with self.assertRaises(checker.VerificationError):
            checker.validate_report_hash(self.report_bytes + b" ", self.sha_text)
        altered_sidecar = self.sha_text.replace(checker.PINNED_REPORT_SHA256, "0" * 64)
        with self.assertRaises(checker.VerificationError):
            checker.validate_report_hash(self.report_bytes, altered_sidecar)
        changed_digest = checker.hashlib.sha256(self.report_bytes + b" ").hexdigest()
        changed_sidecar = f"{changed_digest}  verification-report.json\n"
        with self.assertRaises(checker.VerificationError):
            checker.validate_report_hash(self.report_bytes + b" ", changed_sidecar)

    def test_changed_expected_or_report_commit_is_rejected(self):
        with self.assertRaises(checker.VerificationError):
            checker.validate_report(self.report, self.result, "0" * 40)
        altered = copy.deepcopy(self.report)
        altered["candidate"]["head_commit"] = "0" * 40
        with self.assertRaises(checker.VerificationError):
            checker.validate_report(altered, self.result, checker.PINNED_SOURCE_COMMIT)

    def test_changed_accepted_counts_are_rejected(self):
        for path, field, changed in (
            (("source_branch",), "mapped_assertions", 73),
            (("decision_knowledge_timeline",), "group_count", 11),
            (("unit_tests",), "tests", 237),
            (("integrated_workflow",), "assertions", 78),
        ):
            with self.subTest(path=path, field=field):
                altered = copy.deepcopy(self.report)
                node = altered["verification"][path[0]]
                node[field] = changed
                with self.assertRaises(checker.VerificationError):
                    checker.validate_report(altered, self.result, checker.PINNED_SOURCE_COMMIT)

    def test_documented_238_test_count_cannot_drift(self):
        self.reject_docs(morning=self.morning.replace("**238 tests**", "**237 tests**", 1))

    def test_mainline_1604_source_fact_cannot_drift(self):
        altered = self.morning.replace(
            "mainline includes V3 with its actual 16:04Z source timestamp",
            "mainline includes V3 with its actual 16:05Z source timestamp",
            1,
        )
        self.assertNotEqual(altered, self.morning)
        self.reject_docs(morning=altered)

    def test_hypothetical_1605_cannot_leak_v3_or_conflict(self):
        altered = self.matrix.replace(
            "hypothetical projection suppresses V3 and that dependent conflict",
            "hypothetical projection retains V3 and that dependent conflict",
            1,
        )
        self.assertNotEqual(altered, self.matrix)
        self.reject_docs(matrix=altered)

    def test_hypothetical_1606_cannot_be_called_the_observed_source_time(self):
        altered = self.matrix.replace(
            "actual source observation still 16:04Z",
            "actual source observation at 16:06Z",
            1,
        )
        self.assertNotEqual(altered, self.matrix)
        self.reject_docs(matrix=altered)

    def test_missing_edge_cannot_fall_back_to_mainline(self):
        altered = self.matrix.replace(
            "it never falls back to mainline evidence",
            "it falls back to mainline evidence",
            1,
        )
        self.assertNotEqual(altered, self.matrix)
        self.reject_docs(matrix=altered)

    def test_denied_source_identity_and_metadata_must_remain_hidden(self):
        altered = self.matrix.replace(
            "identities, titles, facts and counts are not disclosed",
            "identities, titles, facts and counts are disclosed",
            1,
        )
        self.assertNotEqual(altered, self.matrix)
        self.reject_docs(matrix=altered)

    def test_unverified_video_existence_claim_is_rejected(self):
        self.reject_docs(morning=self.morning + "\nA demo video has been recorded and is available.\n")

    def test_clinical_validation_or_patient_safety_overclaim_is_rejected(self):
        self.reject_docs(morning=self.morning + "\nClinically validated and safe for patient care.\n")

    def test_stale_pending_reverification_claim_is_rejected(self):
        altered = self.morning.replace(
            "The independent source-only reverify is complete and accepted",
            "The independent source-only reverify remains required",
            1,
        )
        self.assertNotEqual(altered, self.morning)
        self.reject_docs(morning=altered)


if __name__ == "__main__":
    unittest.main()

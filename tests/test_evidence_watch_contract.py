"""Mutation tests for the candidate-independent dock evidence-watch contract."""

from __future__ import annotations

import copy
import hashlib
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import verify_evidence_watch_contract as contract  # noqa: E402


class EvidenceWatchContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.expected_bytes = (ROOT / "tests/evidence-watch-expected-results.json").read_bytes()
        cls.matrix_bytes = (ROOT / "tests/evidence-watch-adversarial-matrix.json").read_bytes()
        cls.receipt_bytes = (ROOT / "tests/evidence-watch-independent-review.json").read_bytes()
        cls.expected = json.loads(cls.expected_bytes)
        cls.matrix = json.loads(cls.matrix_bytes)
        cls.receipt = json.loads(cls.receipt_bytes)
        cls.expected_sha = hashlib.sha256(cls.expected_bytes).hexdigest()
        cls.matrix_sha = hashlib.sha256(cls.matrix_bytes).hexdigest()
        cls.receipt_sha = hashlib.sha256(cls.receipt_bytes).hexdigest()
        cls.plan = Path(
            "/workspace/worktrees/planner-3bfe3e5297d44e829f1f958a41c6c772/"
            "plans/planner-3bfe3e5297d44e829f1f958a41c6c772.md"
        )
        cls.sources = {
            source_id: json.loads((ROOT / relative).read_text(encoding="utf-8"))
            for source_id, relative in contract.SOURCE_IDS.items()
        }

    def validate(self, expected=None, matrix=None, receipt=None, expected_sha=None, matrix_sha=None, receipt_sha=None):
        return contract.validate_artifacts(
            self.expected if expected is None else expected,
            self.matrix if matrix is None else matrix,
            self.receipt if receipt is None else receipt,
            self.expected_sha if expected_sha is None else expected_sha,
            self.matrix_sha if matrix_sha is None else matrix_sha,
            self.receipt_sha if receipt_sha is None else receipt_sha,
            root=ROOT,
            pinned_plan=self.plan,
            require_nine_cases=True,
        )

    def test_fixed_nine_groups_and_independent_receipt_are_accepted(self):
        count, hashes = self.validate()
        self.assertEqual(count, 86)
        self.assertEqual(len(hashes), 7)
        self.assertEqual(len(self.receipt["case_reviews"]), 9)
        self.assertTrue(all(row["result"] == "PASS" for row in self.receipt["case_reviews"]))
        self.assertEqual(sum(len(row["assertion_mappings"]) for row in self.receipt["case_reviews"]), 86)

    def test_changed_or_missing_expected_values_and_recomputed_digest_fail(self):
        changed = copy.deepcopy(self.expected)
        changed["cases"][2]["expected"]["same_effective_later_knowledge"]["role_id"] = "ROLE-DOCK-SWING"
        with self.assertRaises(contract.ContractFailure):
            contract.validate_fixed_values(changed)

        missing = copy.deepcopy(self.expected)
        del missing["cases"][3]["expected"]["early_knowledge_at_effective_boundary"]["known_at"]
        with self.assertRaises(contract.ContractFailure):
            self.validate(expected=missing, expected_sha=self._digest_artifact(missing))

        ordinary_change = copy.deepcopy(self.expected)
        ordinary_change["purpose"] += " altered"
        with self.assertRaisesRegex(contract.ContractFailure, "expected-results bytes differ"):
            self.validate(expected=ordinary_change, expected_sha=self._digest_artifact(ordinary_change))

    def test_missing_zero_and_unmapped_assertions_fail(self):
        missing = copy.deepcopy(self.matrix)
        missing["cases"][0]["pinned_assertions"].pop()
        missing["cases"][0]["nonzero_assertion_count"] -= 1
        with self.assertRaisesRegex(contract.ContractFailure, "fixed assertion total changed"):
            contract.validate_structure(self.expected, missing, True)

        zero = copy.deepcopy(self.matrix)
        zero["cases"][0]["pinned_assertions"] = []
        zero["cases"][0]["nonzero_assertion_count"] = 0
        with self.assertRaisesRegex(contract.ContractFailure, "has no pinned assertions"):
            contract.validate_structure(self.expected, zero, True)

        unmapped = copy.deepcopy(self.matrix)
        assertion = unmapped["cases"][0]["pinned_assertions"][0]
        assertion["source_mappings"] = []
        assertion["planner_only"] = False
        with self.assertRaisesRegex(contract.ContractFailure, "neither source nor planner mapping"):
            contract.validate_structure(self.expected, unmapped, True)

    def test_source_mapping_and_review_assertion_mutations_fail(self):
        bad_mapping = copy.deepcopy(self.matrix)
        assertion = bad_mapping["cases"][3]["pinned_assertions"][1]
        assertion["source_mappings"][0]["source_assertion_id"] = "invented-assertion"
        with self.assertRaisesRegex(contract.ContractFailure, "missing dock matrix assertion"):
            contract.validate_source_mappings(self.expected, bad_mapping, self.sources)

        missing_receipt_mapping = copy.deepcopy(self.receipt)
        missing_receipt_mapping["case_reviews"][0]["assertion_mappings"].pop()
        plan_digest, line_hashes = contract.verify_pinned_plan(self.plan)
        frozen = {
            "planner": plan_digest,
            **{name: digest for name, digest in contract.PINNED_SOURCE_HASHES.items()},
            "tests/evidence-watch-expected-results.json": self.expected_sha,
            "tests/evidence-watch-adversarial-matrix.json": self.matrix_sha,
        }
        with self.assertRaisesRegex(contract.ContractFailure, "receipt misses or reorders assertion mappings"):
            contract.validate_review_receipt_structure(missing_receipt_mapping, self.expected, self.matrix, self.sources, line_hashes, frozen)

    def test_frozen_hash_mutations_are_rejected(self):
        changed_manifest = copy.deepcopy(self.expected)
        changed_manifest["pinned_sources"][1]["sha256"] = "0" * 64
        with self.assertRaisesRegex(contract.ContractFailure, "manifest changed"):
            contract.validate_structure(changed_manifest, self.matrix, True)

        changed_receipt = copy.deepcopy(self.receipt)
        changed_receipt["frozen_hashes"]["tests/shift-board-expected-results.json"] = "0" * 64
        plan_digest, line_hashes = contract.verify_pinned_plan(self.plan)
        frozen = {
            "planner": plan_digest,
            **{name: digest for name, digest in contract.PINNED_SOURCE_HASHES.items()},
            "tests/evidence-watch-expected-results.json": self.expected_sha,
            "tests/evidence-watch-adversarial-matrix.json": self.matrix_sha,
        }
        with self.assertRaisesRegex(contract.ContractFailure, "frozen hashes differ"):
            contract.validate_review_receipt_structure(changed_receipt, self.expected, self.matrix, self.sources, line_hashes, frozen)

        bad_receipt_bytes_hash = hashlib.sha256(self.receipt_bytes + b" ").hexdigest()
        with self.assertRaisesRegex(contract.ContractFailure, "receipt bytes differ"):
            self.validate(receipt_sha=bad_receipt_bytes_hash)

    def test_unsupported_status_promotion_and_confirmation_boundary_fail(self):
        changed = copy.deepcopy(self.expected)
        changed["cases"][3]["expected"]["early_knowledge_at_effective_boundary"]["status"] = "changed"
        with self.assertRaisesRegex(contract.ContractFailure, "must remain unknown"):
            contract.validate_fixed_values(changed)

        changed = copy.deepcopy(self.expected)
        changed["cases"][2]["expected"]["same_effective_later_knowledge"]["candidate_disposition"] = "invalid"
        with self.assertRaisesRegex(contract.ContractFailure, "pending candidates"):
            contract.validate_fixed_values(changed)

        changed = copy.deepcopy(self.expected)
        changed["scope"]["watch_state_is_separate_from_confirmed_actions"] = False
        with self.assertRaisesRegex(contract.ContractFailure, "scope watch_state_is_separate_from_confirmed_actions changed"):
            contract.validate_structure(changed, self.matrix, True)

    def test_hidden_fixture_leakage_is_rejected(self):
        leaked = copy.deepcopy(self.expected)
        leaked["cases"][6]["expected"]["forbidden_disclosure_categories"].append("SRC-RESTRICTED-DOCK-99")
        with self.assertRaisesRegex(contract.ContractFailure, "hidden-source literal leaked"):
            contract.validate_no_hidden_source_leakage([leaked, self.matrix, self.receipt], self.sources)

    def test_source_hashes_are_frozen_on_disk(self):
        with tempfile.TemporaryDirectory() as directory:
            temp_root = Path(directory)
            (temp_root / "tests").mkdir()
            for relative in contract.PINNED_SOURCE_HASHES:
                shutil.copyfile(ROOT / relative, temp_root / relative)
            changed = temp_root / "tests/shift-board-adversarial-matrix.json"
            changed.write_bytes(changed.read_bytes() + b" ")
            with self.assertRaisesRegex(contract.ContractFailure, "frozen source hash changed"):
                contract.verify_pinned_sources(temp_root)

    @staticmethod
    def _digest_artifact(value) -> str:
        raw = (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
        return hashlib.sha256(raw).hexdigest()


if __name__ == "__main__":
    unittest.main()

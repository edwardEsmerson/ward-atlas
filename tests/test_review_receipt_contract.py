"""Focused tests for the immutable review-receipt oracle contract."""

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

import verify_review_receipt_contract as contract  # noqa: E402


class ReviewReceiptContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.expected_bytes = (ROOT / "tests/review-receipt-expected-results.json").read_bytes()
        cls.matrix_bytes = (ROOT / "tests/review-receipt-adversarial-matrix.json").read_bytes()
        cls.expected = json.loads(cls.expected_bytes)
        cls.matrix = json.loads(cls.matrix_bytes)
        cls.expected_sha = hashlib.sha256(cls.expected_bytes).hexdigest()
        cls.matrix_sha = hashlib.sha256(cls.matrix_bytes).hexdigest()
        cls.plan = Path(
            "/workspace/worktrees/planner-bdb7b5ac66b041fe8e0a44e126dcf00c/"
            "plans/planner-bdb7b5ac66b041fe8e0a44e126dcf00c.md"
        )

    def validate(self, expected=None, matrix=None, expected_sha=None, matrix_sha=None):
        return contract.validate_artifacts(
            self.expected if expected is None else expected,
            self.matrix if matrix is None else matrix,
            self.expected_sha if expected_sha is None else expected_sha,
            self.matrix_sha if matrix_sha is None else matrix_sha,
            root=ROOT,
            pinned_plan=self.plan,
            require_ten_cases=True,
        )

    @staticmethod
    def _digest(value) -> str:
        return hashlib.sha256(json.dumps(value, sort_keys=True).encode("utf-8")).hexdigest()

    def test_exact_ten_groups_and_nonzero_mappings(self):
        assertions, source_digests = self.validate()
        self.assertEqual(assertions, 78)
        self.assertEqual([row["id"] for row in self.expected["cases"]], list(contract.REQUIRED_CASES))
        self.assertEqual([row["scenario_id"] for row in self.matrix["cases"]], list(contract.REQUIRED_CASES))
        self.assertEqual(len(source_digests), 4)  # packet plus three unchanged oracle files
        self.assertTrue(all(row["nonzero_assertion_count"] > 0 for row in self.matrix["cases"]))

    def test_fixed_ids_times_paths_flags_and_outcomes_are_pinned(self):
        mutations = [
            (0, "draft_open_or_preview_graph_node_delta", 1),
            (2, "receipt_id", "RCP-HND-DOCK-2020-02"),
            (2, "receipt_node_delta_after_one_acknowledgment", 2),
            (2, "recorded_at", "2026-10-14T20:36:00Z"),
            (1, "current_role_path", ["INC-207", "EQ-DOCK-CART-2", "SVC-INBOUND-TRANSFER", "DEPT-RECEIVING-DOCK", "ROLE-DOCK-DUTY"]),
            (2, "external_action", True),
            (7, "unrelated_hypothetical_branch_id", "BR-OTHER"),
            (9, "external_network_write", True),
        ]
        for case_index, key, value in mutations:
            with self.subTest(case=case_index, key=key):
                changed = copy.deepcopy(self.expected)
                changed["cases"][case_index]["expected"][key] = value
                with self.assertRaisesRegex(contract.ContractFailure, "expected-results bytes differ"):
                    self.validate(expected=changed, expected_sha=self._digest(changed))

    def test_matrix_assertion_text_is_fixed_not_candidate_derived(self):
        changed = copy.deepcopy(self.matrix)
        changed["cases"][0]["pinned_assertions"][0] = "candidate says the graph has data"
        with self.assertRaisesRegex(contract.ContractFailure, "matrix bytes differ"):
            self.validate(matrix=changed, matrix_sha=self._digest(changed))

    def test_missing_case_zero_assertions_and_count_mismatch_are_rejected(self):
        missing = copy.deepcopy(self.matrix)
        missing["cases"].pop()
        with self.assertRaisesRegex(contract.ContractFailure, "exactly ten cases"):
            self.validate(matrix=missing)

        zero = copy.deepcopy(self.matrix)
        zero["cases"][0]["pinned_assertions"] = []
        zero["cases"][0]["nonzero_assertion_count"] = 0
        with self.assertRaisesRegex(contract.ContractFailure, "has no assertions"):
            self.validate(matrix=zero)

        mismatch = copy.deepcopy(self.matrix)
        mismatch["cases"][1]["nonzero_assertion_count"] += 1
        with self.assertRaisesRegex(contract.ContractFailure, "does not match its assertions"):
            self.validate(matrix=mismatch)

    def test_expected_and_matrix_packet_line_mappings_match(self):
        contract.validate_plan_mappings(self.expected, self.matrix)
        changed = copy.deepcopy(self.matrix)
        changed["cases"][1]["planner_refs"] = ["planner-bdb7b5ac66b041fe8e0a44e126dcf00c.md:30"]
        with self.assertRaisesRegex(contract.ContractFailure, "same exact packet outcome line"):
            contract.validate_plan_mappings(self.expected, changed)

    def test_pinned_packet_digest_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            altered = Path(directory) / "wrong-plan.md"
            altered.write_text("not the pinned review-receipt packet\n", encoding="utf-8")
            with self.assertRaisesRegex(contract.ContractFailure, "independently pinned review-receipt packet"):
                contract.verify_pinned_packet(altered)

    def test_any_existing_oracle_drift_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for relative in contract.PINNED_ORACLES:
                target = root / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(ROOT / relative, target)
            altered = root / "tests/source-change-expected-results.json"
            altered.write_bytes(altered.read_bytes() + b" ")
            with self.assertRaisesRegex(contract.ContractFailure, "unchanged oracle source"):
                contract.verify_existing_oracles(root, self.expected)


if __name__ == "__main__":
    unittest.main()

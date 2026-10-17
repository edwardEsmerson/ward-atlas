"""Focused regression tests for the independently pinned shift-board contract."""

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

import verify_shift_board_contract as contract  # noqa: E402


class ShiftBoardContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.expected_bytes = (ROOT / "tests/shift-board-expected-results.json").read_bytes()
        cls.matrix_bytes = (ROOT / "tests/shift-board-adversarial-matrix.json").read_bytes()
        cls.expected = json.loads(cls.expected_bytes)
        cls.matrix = json.loads(cls.matrix_bytes)
        cls.expected_sha = hashlib.sha256(cls.expected_bytes).hexdigest()
        cls.matrix_sha = hashlib.sha256(cls.matrix_bytes).hexdigest()

    def validate(self, expected=None, matrix=None):
        return contract.validate_artifacts(
            self.expected if expected is None else expected,
            self.matrix if matrix is None else matrix,
            self.expected_sha,
            self.matrix_sha,
            require_eight_cases=True,
        )

    def test_exact_eight_cases_and_nonzero_mapping(self):
        self.assertEqual(self.validate(), 55)
        self.assertEqual(
            [row["id"] for row in self.expected["cases"]],
            list(contract.REQUIRED_CASES),
        )
        self.assertEqual(
            [row["scenario_id"] for row in self.matrix["cases"]],
            list(contract.REQUIRED_CASES),
        )

    def test_candidate_path_cannot_rewrite_pinned_oracle(self):
        changed = copy.deepcopy(self.expected)
        changed["cases"][1]["expected"]["before"]["role_path"][-1] = "ROLE-DOCK-SWING"
        with self.assertRaisesRegex(ValueError, "independently pinned packet"):
            contract.validate_artifacts(
                changed,
                self.matrix,
                hashlib.sha256(json.dumps(changed).encode("utf-8")).hexdigest(),
                self.matrix_sha,
                require_eight_cases=True,
            )

    def test_missing_case_mapping_is_rejected(self):
        changed = copy.deepcopy(self.matrix)
        changed["cases"].pop()
        with self.assertRaisesRegex(ValueError, "one-to-one"):
            self.validate(matrix=changed)

    def test_zero_assertion_case_is_rejected(self):
        changed = copy.deepcopy(self.matrix)
        changed["cases"][0]["nonzero_assertion_count"] = 0
        changed["cases"][0]["pinned_assertions"] = []
        with self.assertRaisesRegex(ValueError, "must have explicit assertions"):
            self.validate(matrix=changed)

    def test_nonzero_count_must_match_explicit_assertions(self):
        changed = copy.deepcopy(self.matrix)
        changed["cases"][0]["nonzero_assertion_count"] += 1
        with self.assertRaisesRegex(ValueError, "does not equal"):
            self.validate(matrix=changed)

    def test_matrix_plan_lines_match_expected_outcome_refs(self):
        contract.validate_plan_mappings(self.expected, self.matrix)
        changed = copy.deepcopy(self.matrix)
        changed["cases"][1]["planner_refs"][0] = "planner-5f75fb17810c463fb9d84bd5821b254f.md:33"
        with self.assertRaisesRegex(ValueError, "do not map to the same exact pinned"):
            contract.validate_plan_mappings(self.expected, changed)

    def test_pinned_plan_hash_fails_closed(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            wrong_plan = Path(temp_dir) / "wrong.md"
            wrong_plan.write_text("not the pinned planner packet\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "does not match pinned"):
                contract.verify_plan_file("pinned-plan", wrong_plan)


if __name__ == "__main__":
    unittest.main()

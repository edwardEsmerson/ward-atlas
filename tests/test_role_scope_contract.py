"""Focused mutation tests for the independent WardAtlas role-scope oracle."""

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

import verify_role_scope_contract as contract  # noqa: E402


class RoleScopeContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.expected_bytes = (ROOT / "tests/role-scope-expected-results.json").read_bytes()
        cls.matrix_bytes = (ROOT / "tests/role-scope-adversarial-matrix.json").read_bytes()
        cls.expected = json.loads(cls.expected_bytes)
        cls.matrix = json.loads(cls.matrix_bytes)
        cls.expected_sha = hashlib.sha256(cls.expected_bytes).hexdigest()
        cls.matrix_sha = hashlib.sha256(cls.matrix_bytes).hexdigest()

    def validate(self, expected=None, matrix=None, expected_sha=None, matrix_sha=None):
        return contract.validate_artifacts(
            self.expected if expected is None else expected,
            self.matrix if matrix is None else matrix,
            self.expected_sha if expected_sha is None else expected_sha,
            self.matrix_sha if matrix_sha is None else matrix_sha,
            require_eight_cases=True,
        )

    def test_exact_eight_groups_and_nonzero_assertions(self):
        self.assertEqual(self.validate(), 48)
        self.assertEqual(
            [row["id"] for row in self.expected["cases"]],
            list(contract.REQUIRED_CASES),
        )
        self.assertEqual(
            [row["scenario_id"] for row in self.matrix["cases"]],
            list(contract.REQUIRED_CASES),
        )
        self.assertTrue(all(row["nonzero_assertion_count"] > 0 for row in self.matrix["cases"]))

    def test_packet_pinned_fixture_and_three_scope_traces(self):
        self.assertEqual(
            self.expected["fixed_fixture"]["scope_results"],
            contract.PINNED_SCOPE_RESULTS,
        )
        self.assertEqual(
            self.expected["cases"][1]["expected"]["results_in_order"],
            contract.PINNED_SCOPE_RESULTS,
        )
        contract.validate_plan_mappings(self.expected, self.matrix)

    def test_changed_or_missing_path_grant_source_time_or_branch_is_rejected(self):
        mutations = (
            ("path", lambda value: value["cases"][1]["expected"]["results_in_order"][0]["paths"][0].__setitem__(-1, "ROLE-DOCK-OTHER")),
            ("grant", lambda value: value["fixed_fixture"]["scope_results"][0]["grant_ids"].clear()),
            ("source", lambda value: value["cases"][1]["expected"]["results_in_order"][1].__setitem__("source_id", "SRC-OTHER")),
            ("event timestamp", lambda value: value["cases"][1]["expected"]["results_in_order"][2].__setitem__("event_at", "2026-10-15T06:16:00Z")),
            ("branch", lambda value: value["fixed_fixture"].__setitem__("branch_id", "BR-OTHER")),
            ("missing provenance", lambda value: value["cases"][4]["expected"].pop("source_id")),
            ("denied path marker", lambda value: value["cases"][5]["expected"]["denied_fixture"]["distinctive_path_markers"].__setitem__(0, "EQ-LEAKED-OTHER")),
        )
        for label, mutate in mutations:
            with self.subTest(label=label):
                changed = copy.deepcopy(self.expected)
                mutate(changed)
                changed_sha = hashlib.sha256(json.dumps(changed).encode("utf-8")).hexdigest()
                with self.assertRaisesRegex(ValueError, "differ from the independent|fixed fixture"):
                    self.validate(expected=changed, expected_sha=changed_sha)

    def test_distinctive_nondisclosure_markers_are_independent_fixed_values(self):
        changed = copy.deepcopy(self.expected)
        denied = changed["cases"][5]["expected"]["denied_fixture"]
        denied["title"] = "Different hidden title"
        changed_sha = hashlib.sha256(json.dumps(changed).encode("utf-8")).hexdigest()
        with self.assertRaisesRegex(ValueError, "differ from the independent"):
            self.validate(expected=changed, expected_sha=changed_sha)

        changed = copy.deepcopy(self.expected)
        changed["cases"][5]["expected"]["hidden_count_or_existence_disclosed"] = True
        changed_sha = hashlib.sha256(json.dumps(changed).encode("utf-8")).hexdigest()
        with self.assertRaisesRegex(ValueError, "differ from the independent"):
            self.validate(expected=changed, expected_sha=changed_sha)

    def test_missing_matrix_case_wrong_mapping_and_zero_assertions_are_rejected(self):
        changed = copy.deepcopy(self.matrix)
        changed["cases"].pop()
        with self.assertRaisesRegex(ValueError, "one-to-one"):
            self.validate(matrix=changed)

        changed = copy.deepcopy(self.matrix)
        changed["cases"][1]["scenario_id"] = "grant-edge-removal-is-scope-specific"
        with self.assertRaisesRegex(ValueError, "does not map"):
            self.validate(matrix=changed)

        changed = copy.deepcopy(self.matrix)
        changed["cases"][0]["pinned_assertions"] = []
        changed["cases"][0]["nonzero_assertion_count"] = 0
        with self.assertRaisesRegex(ValueError, "do not match the pinned"):
            self.validate(matrix=changed)

    def test_plan_line_mapping_is_exact(self):
        changed = copy.deepcopy(self.matrix)
        changed["cases"][1]["planner_refs"][0] = (
            "planner-a4d9fd7447db467faab0a96048741775.md:27"
        )
        with self.assertRaisesRegex(ValueError, "mapping.*changed|same exact pinned packet lines"):
            self.validate(matrix=changed)

    def test_mutated_planner_packet_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            wrong = Path(directory) / "planner.md"
            wrong.write_text("different planner content\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "does not match the role-scope packet"):
                contract.verify_plan_file(wrong)


if __name__ == "__main__":
    unittest.main()

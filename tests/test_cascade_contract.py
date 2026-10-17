"""Regression tests for WardAtlas's separately pinned cascade oracle."""

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

import verify_cascade_contract as contract  # noqa: E402


class CascadeContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.expected_bytes = (ROOT / "tests/cascade-expected-results.json").read_bytes()
        cls.matrix_bytes = (ROOT / "tests/cascade-adversarial-matrix.json").read_bytes()
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

    def prepare_oracle_source_trees(self, temp: Path) -> tuple[Path, Path]:
        product_tests = temp / "product" / "tests"
        worktrees_root = temp / "worktrees"
        for source in contract.PINNED_ORACLE_SOURCES:
            if source["worktree"] == "product":
                source_root = ROOT / "tests"
                destination = product_tests
            else:
                source_root = Path("/workspace/worktrees") / source["worktree"] / "tests"
                destination = worktrees_root / source["worktree"] / "tests"
            destination.mkdir(parents=True, exist_ok=True)
            for filename in (source["expected_file"], source["matrix_file"]):
                shutil.copyfile(source_root / filename, destination / filename)
        return product_tests, worktrees_root

    def test_exact_eight_outcomes_and_nonzero_matrix_mappings(self):
        count = self.validate()
        self.assertGreater(count, 0)
        self.assertEqual([row["id"] for row in self.expected["cases"]], list(contract.REQUIRED_CASES))
        self.assertEqual([row["scenario_id"] for row in self.matrix["cases"]], list(contract.REQUIRED_CASES))
        self.assertTrue(all(row["nonzero_assertion_count"] > 0 for row in self.matrix["cases"]))

    def test_full_validator_checks_existing_oracles_and_companion_plan_sources(self):
        plan = Path("/workspace/worktrees/planner-8b15037dc21642129e4f0cbd47508f2f/plans/planner-8b15037dc21642129e4f0cbd47508f2f.md")
        result = contract.verify(ROOT / "tests/cascade-expected-results.json", ROOT / "tests/cascade-adversarial-matrix.json", plan)
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(result["outcome_groups"], 8)
        self.assertEqual(result["pinned_planner_packets_checked"], 5)
        self.assertEqual(result["existing_product_oracle_sources_checked"], 5)
        self.assertFalse(result["candidate_output_used"])
        self.assertFalse(result["live_database_behavior_claimed"])

    def test_changed_path_is_rejected_even_if_caller_rehashes_artifact(self):
        changed = copy.deepcopy(self.expected)
        changed["canonical_paths"]["dock"][-1] = "ROLE-OTHER"
        with self.assertRaisesRegex(ValueError, "exact six-hop cascade paths"):
            contract.validate_artifacts(changed, self.matrix, "0" * 64, self.matrix_sha, True)

        missing = copy.deepcopy(self.expected)
        missing["canonical_paths"].pop("linen")
        with self.assertRaisesRegex(ValueError, "exact six-hop cascade paths"):
            contract.validate_artifacts(missing, self.matrix, "0" * 64, self.matrix_sha, True)

    def test_changed_timestamp_or_branch_is_rejected(self):
        timestamp = copy.deepcopy(self.expected)
        timestamp["fixture"]["evaluation_at"] = "2026-10-15T06:31:00Z"
        with self.assertRaisesRegex(ValueError, "fixture.evaluation_at"):
            contract.validate_artifacts(timestamp, self.matrix, "0" * 64, self.matrix_sha, True)

        branch = copy.deepcopy(self.expected)
        branch["fixture"]["isolation_branch_id"] = "BR-LEAKED"
        with self.assertRaisesRegex(ValueError, "fixture.isolation_branch_id"):
            contract.validate_artifacts(branch, self.matrix, "0" * 64, self.matrix_sha, True)

    def test_changed_isolation_oracle_is_rejected(self):
        changed = copy.deepcopy(self.expected)
        changed["cases"][4]["expected"]["path_connects_unrelated_incident_to_cascade_equipment"] = True
        with self.assertRaisesRegex(ValueError, "cross-case branch isolation pin"):
            contract.validate_artifacts(changed, self.matrix, "0" * 64, self.matrix_sha, True)

    def test_missing_mapping_and_zero_assertions_are_rejected(self):
        missing = copy.deepcopy(self.matrix)
        missing["cases"].pop()
        with self.assertRaisesRegex(ValueError, "one-to-one"):
            contract.validate_artifacts(self.expected, missing, self.expected_sha, self.matrix_sha, True)

        zero = copy.deepcopy(self.matrix)
        zero["cases"][0]["nonzero_assertion_count"] = 0
        with self.assertRaisesRegex(ValueError, "positive integer"):
            contract.validate_artifacts(self.expected, zero, self.expected_sha, self.matrix_sha, True)

    def test_matrix_mapping_must_select_same_numbered_packet_outcomes(self):
        changed = copy.deepcopy(self.matrix)
        changed["cases"][2]["planner_refs"] = ["planner-8b15037dc21642129e4f0cbd47508f2f.md:44"]
        with self.assertRaisesRegex(ValueError, "exact expected packet outcome lines"):
            contract.validate_plan_mappings(self.expected, changed)

    def test_existing_oracle_snapshot_is_compared_not_self_derived(self):
        changed = copy.deepcopy(self.expected)
        changed["preserved_existing_outputs"][0]["paths"][0][-1] = "ROLE-DRIFT"
        with self.assertRaisesRegex(ValueError, "INC-204 paths differ"):
            contract.verify_existing_oracles(changed, ROOT / "tests")

    def test_missing_each_temporal_prior_oracle_file_fails_closed(self):
        external_sources = [source for source in contract.PINNED_ORACLE_SOURCES if source["worktree"] != "product"]
        for source in external_sources:
            for filename in (source["expected_file"], source["matrix_file"]):
                with self.subTest(source=source["id"], missing=filename), tempfile.TemporaryDirectory() as directory:
                    temp = Path(directory)
                    product_tests, worktrees_root = self.prepare_oracle_source_trees(temp)
                    missing = worktrees_root / source["worktree"] / "tests" / filename
                    missing.unlink()
                    with self.assertRaisesRegex(ValueError, f"cannot read required oracle source.*{filename}"):
                        contract.verify_existing_oracles(self.expected, product_tests, worktrees_root=worktrees_root)

    def test_mutating_each_temporal_prior_oracle_file_fails_closed(self):
        external_sources = [source for source in contract.PINNED_ORACLE_SOURCES if source["worktree"] != "product"]
        for source in external_sources:
            for filename in (source["expected_file"], source["matrix_file"]):
                with self.subTest(source=source["id"], mutated=filename), tempfile.TemporaryDirectory() as directory:
                    temp = Path(directory)
                    product_tests, worktrees_root = self.prepare_oracle_source_trees(temp)
                    mutated = worktrees_root / source["worktree"] / "tests" / filename
                    mutated.write_bytes(mutated.read_bytes() + b"\n")
                    with self.assertRaisesRegex(ValueError, f"{filename} differs from its pinned SHA-256"):
                        contract.verify_existing_oracles(self.expected, product_tests, worktrees_root=worktrees_root)

    def test_companion_planner_hash_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            wrong = Path(directory) / "planner-8b15037dc21642129e4f0cbd47508f2f.md"
            wrong.write_text("changed packet\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "does not match pinned SHA-256"):
                contract.verify_plan_file("cascade-packet", wrong)


if __name__ == "__main__":
    unittest.main()

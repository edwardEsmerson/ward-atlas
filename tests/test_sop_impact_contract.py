"""Mutation-focused tests for the candidate-free SOP-impact artifact contract."""

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

import verify_sop_impact_contract as contract  # noqa: E402


class SopImpactContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.expected_bytes = (ROOT / "tests/sop-impact-expected-results.json").read_bytes()
        cls.matrix_bytes = (ROOT / "tests/sop-impact-adversarial-matrix.json").read_bytes()
        cls.expected = json.loads(cls.expected_bytes)
        cls.matrix = json.loads(cls.matrix_bytes)
        cls.expected_sha = hashlib.sha256(cls.expected_bytes).hexdigest()
        cls.matrix_sha = hashlib.sha256(cls.matrix_bytes).hexdigest()
        cls.plan = Path(
            "/workspace/worktrees/planner-3bfcb0d8abbc45bc82e0fab8d715c2da/"
            "plans/planner-3bfcb0d8abbc45bc82e0fab8d715c2da.md"
        )

    def validate(self, expected=None, matrix=None, expected_sha=None, matrix_sha=None, root=ROOT, plan=None):
        return contract.validate_artifacts(
            self.expected if expected is None else expected,
            self.matrix if matrix is None else matrix,
            self.expected_sha if expected_sha is None else expected_sha,
            self.matrix_sha if matrix_sha is None else matrix_sha,
            root=root,
            pinned_plan=self.plan if plan is None else plan,
            require_ten_cases=True,
        )

    @staticmethod
    def _digest(value) -> str:
        raw = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return hashlib.sha256(raw).hexdigest()

    def test_exact_ten_groups_and_nonzero_mappings(self):
        assertions, source_digests = self.validate()
        self.assertEqual(assertions, 60)
        self.assertEqual([row["id"] for row in self.expected["cases"]], list(contract.REQUIRED_CASES))
        self.assertEqual([row["scenario_id"] for row in self.matrix["cases"]], list(contract.REQUIRED_CASES))
        self.assertEqual(len(source_digests), 6)  # packet plus five unchanged expected-result oracles
        self.assertTrue(all(row["nonzero_assertion_count"] > 0 for row in self.matrix["cases"]))
        self.assertEqual(
            [row["nonzero_assertion_count"] for row in self.matrix["cases"]],
            [5, 5, 9, 5, 5, 7, 6, 6, 6, 6],
        )

    def test_fixed_paths_ids_provenance_conflict_acl_counterfactual_and_time_are_pinned(self):
        mutations = [
            (1, "impact_paths", [["INC-204", "CONVEYOR-C4"]]),
            (2, "claim_evidence", []),
            (2, "conflict_status", "resolved"),
            (3, "stale_source_id", "SRC-OTHER"),
            (4, "removed_edge", {"from": "SOP-OPS-9-V3", "relationship": "APPLIES_TO", "to": "CONVEYOR-C4"}),
            (5, "reseed_restores_exact_conflict_witness", False),
            (7, "injected_probe_acl", "operations"),
            (9, "normalized_as_of", "2026-10-14T20:30:00Z"),
        ]
        for index, key, value in mutations:
            with self.subTest(case=index, key=key):
                changed = copy.deepcopy(self.expected)
                changed["cases"][index]["expected"][key] = value
                with self.assertRaises((contract.ContractFailure, KeyError)):
                    self.validate(expected=changed, expected_sha=self._digest(changed))

    def test_matrix_assertions_are_fixed_and_case_mapped(self):
        changed = copy.deepcopy(self.matrix)
        changed["cases"][0]["pinned_assertions"][0] = "candidate says the conflict does not exist"
        with self.assertRaisesRegex(contract.ContractFailure, "matrix bytes differ"):
            self.validate(matrix=changed, matrix_sha=self._digest(changed))

        wrong_line = copy.deepcopy(self.matrix)
        wrong_line["cases"][2]["planner_refs"] = [f"{contract.PACKET_FILENAME}:44"]
        with self.assertRaisesRegex(contract.ContractFailure, "same exact packet outcome line"):
            contract.validate_plan_mappings(self.expected, wrong_line)

        changed_expected_ref = copy.deepcopy(self.expected)
        changed_expected_ref["cases"][0]["planner_refs"] = ["sop-impact-packet#outcome-2"]
        with self.assertRaisesRegex(contract.ContractFailure, "same exact packet outcome line"):
            contract.validate_plan_mappings(changed_expected_ref, self.matrix)

    def test_missing_case_zero_assertions_and_mismatched_floor_fail(self):
        missing = copy.deepcopy(self.matrix)
        missing["cases"].pop()
        with self.assertRaisesRegex(contract.ContractFailure, "exactly ten cases"):
            contract.validate_structure(self.expected, missing, True)

        zero = copy.deepcopy(self.matrix)
        zero["cases"][0]["pinned_assertions"] = []
        zero["cases"][0]["nonzero_assertion_count"] = 0
        with self.assertRaisesRegex(contract.ContractFailure, "has no pinned assertions"):
            contract.validate_structure(self.expected, zero, True)

        mismatch = copy.deepcopy(self.matrix)
        mismatch["cases"][1]["nonzero_assertion_count"] += 1
        with self.assertRaisesRegex(contract.ContractFailure, "differs from its pinned assertions"):
            contract.validate_structure(self.expected, mismatch, True)

    def test_pinned_packet_digest_and_outcome_headings_fail_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            altered = Path(directory) / contract.PACKET_FILENAME
            altered.write_text("not the pinned packet\n", encoding="utf-8")
            with self.assertRaisesRegex(contract.ContractFailure, "independently pinned SOP-impact packet"):
                contract.verify_pinned_packet(altered)

    def test_any_existing_expected_oracle_hash_drift_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for relative in contract.PINNED_ORACLES:
                target = root / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(ROOT / relative, target)
            altered = root / "tests/source-change-expected-results.json"
            altered.write_bytes(altered.read_bytes() + b" ")
            with self.assertRaisesRegex(contract.ContractFailure, "unchanged oracle"):
                contract.verify_existing_oracles(root, self.expected)

    def test_actual_old_oracle_overlap_is_exact(self):
        digests = contract.verify_existing_oracles(ROOT, self.expected)
        self.assertEqual(set(digests), set(contract.PINNED_ORACLES))
        self.assertEqual(
            self.expected["cases"][2]["expected"]["claim_evidence"][1]["source_id"],
            "SRC-SOP-CURRENT-V3",
        )


if __name__ == "__main__":
    unittest.main()

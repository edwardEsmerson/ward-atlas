"""Mutation tests for the candidate-independent dock bitemporal contract."""

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

import verify_dock_bitemporal_contract as contract  # noqa: E402


class DockBitemporalContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.expected_bytes = (ROOT / "tests/dock-bitemporal-expected-results.json").read_bytes()
        cls.matrix_bytes = (ROOT / "tests/dock-bitemporal-adversarial-matrix.json").read_bytes()
        cls.expected = json.loads(cls.expected_bytes)
        cls.matrix = json.loads(cls.matrix_bytes)
        cls.expected_sha = hashlib.sha256(cls.expected_bytes).hexdigest()
        cls.matrix_sha = hashlib.sha256(cls.matrix_bytes).hexdigest()
        cls.plan = Path(
            "/workspace/worktrees/planner-280b00074097409f80433898c7dcdac8/"
            "plans/planner-280b00074097409f80433898c7dcdac8.md"
        )

    def validate(self, expected=None, matrix=None, expected_sha=None, matrix_sha=None, root=ROOT, plan=None):
        return contract.validate_artifacts(
            self.expected if expected is None else expected,
            self.matrix if matrix is None else matrix,
            self.expected_sha if expected_sha is None else expected_sha,
            self.matrix_sha if matrix_sha is None else matrix_sha,
            root=root,
            pinned_plan=self.plan if plan is None else plan,
            require_ten_groups=True,
        )

    @staticmethod
    def digest(value) -> str:
        return hashlib.sha256(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")).hexdigest()

    def test_exact_ten_groups_pairs_and_nonzero_assertion_mappings(self):
        assertions, digests = self.validate()
        self.assertEqual(assertions, 60)
        self.assertEqual([row["id"] for row in self.expected["cases"]], list(contract.REQUIRED_CASES))
        self.assertEqual([row["id"] for row in self.matrix["cases"]], list(contract.REQUIRED_CASES))
        self.assertEqual(len(digests), 19)  # packet plus every pre-existing test JSON oracle/matrix
        self.assertTrue(all(row["nonzero_assertion_count"] > 0 for row in self.matrix["cases"]))
        self.assertTrue(all(len(row["pinned_assertions"]) == row["nonzero_assertion_count"] for row in self.matrix["cases"]))

    def test_axis_pair_mutations_and_missing_axis_are_rejected(self):
        changed = copy.deepcopy(self.expected)
        changed["cases"][2]["expected"]["known_at"] = "2026-10-14T20:18:00Z"
        with self.assertRaisesRegex(contract.ContractFailure, "effective_at/known_at pair changed"):
            contract.validate_structure(changed, self.matrix, True)

        missing = copy.deepcopy(self.expected)
        del missing["cases"][3]["expected"]["known_at"]
        with self.assertRaisesRegex(contract.ContractFailure, "effective_at/known_at pair changed"):
            contract.validate_structure(missing, self.matrix, True)

        changed_legacy = copy.deepcopy(self.expected)
        changed_legacy["cases"][1]["expected"]["request"]["known_at"] = "2026-10-14T20:19:00Z"
        with self.assertRaisesRegex(contract.ContractFailure, "legacy dock_at compatibility input changed"):
            contract.validate_structure(changed_legacy, self.matrix, True)

    def test_changed_path_provenance_status_acl_and_unknown_expectations_fail(self):
        mutations = [
            (2, "role_path", ["INC-207", "ROLE-DOCK-SWING"]),
            (3, "source_versions", [{"source_id": "SRC-DOCK-ROSTER-R2", "observed_at": "2026-10-14T20:19:00Z"}]),
            (3, "source_versions", [*self.expected["cases"][3]["expected"]["source_versions"][:-1], {**self.expected["cases"][3]["expected"]["source_versions"][-1], "status": "resolved"}]),
            (5, "role_status", "current"),
            (5, "unknown_not_guessed_as_duty_or_swing", False),
            (7, "hidden_identifier_title_fact_count_existence_relationship_path_or_dependent_paraphrase_disclosed", True),
        ]
        for index, key, value in mutations:
            with self.subTest(case=contract.REQUIRED_CASES[index], field=key):
                changed = copy.deepcopy(self.expected)
                changed["cases"][index]["expected"][key] = value
                with self.assertRaises(contract.ContractFailure):
                    contract.validate_fixed_outcomes(changed, ROOT)

    def test_matrix_missing_group_zero_assertions_and_bad_pointer_fail(self):
        missing = copy.deepcopy(self.matrix)
        missing["cases"].pop()
        with self.assertRaisesRegex(contract.ContractFailure, "exactly ten groups"):
            contract.validate_structure(self.expected, missing, True)

        zero = copy.deepcopy(self.matrix)
        zero["cases"][0]["pinned_assertions"] = []
        zero["cases"][0]["nonzero_assertion_count"] = 0
        with self.assertRaisesRegex(contract.ContractFailure, "has no pinned assertions"):
            contract.validate_structure(self.expected, zero, True)

        bad_pointer = copy.deepcopy(self.matrix)
        bad_pointer["cases"][3]["pinned_assertions"][0]["expected_pointer"] = "/candidate/output"
        with self.assertRaisesRegex(contract.ContractFailure, "required fixed outcome fields"):
            contract.validate_structure(self.expected, bad_pointer, True)

    def test_recomputed_candidate_oracle_digest_cannot_rewrite_expected_bytes(self):
        changed = copy.deepcopy(self.expected)
        changed["cases"][3]["expected"]["role_path"][-1] = "ROLE-DOCK-SWING"
        with self.assertRaisesRegex(contract.ContractFailure, "expected-results bytes differ"):
            self.validate(expected=changed, expected_sha=self.digest(changed))

        changed_matrix = copy.deepcopy(self.matrix)
        changed_matrix["cases"][4]["nonzero_assertion_count"] += 1
        changed_matrix["cases"][4]["pinned_assertions"].append(
            {"id": "invented", "expected_pointer": "/cases/4/expected/role_path"}
        )
        with self.assertRaisesRegex(contract.ContractFailure, "matrix bytes differ"):
            self.validate(matrix=changed_matrix, matrix_sha=self.digest(changed_matrix))

    def test_every_preexisting_oracle_and_matrix_hash_is_frozen(self):
        digests = contract.verify_existing_oracles(ROOT, self.expected)
        self.assertEqual(set(digests), set(contract.PINNED_EXISTING))
        self.assertEqual(len(digests), 18)

        with tempfile.TemporaryDirectory() as directory:
            temp_root = Path(directory)
            for relative in contract.PINNED_EXISTING:
                target = temp_root / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(ROOT / relative, target)
            changed = temp_root / "tests/shift-board-adversarial-matrix.json"
            changed.write_bytes(changed.read_bytes() + b" ")
            with self.assertRaisesRegex(contract.ContractFailure, "differs from its frozen byte hash"):
                contract.verify_existing_oracles(temp_root, self.expected)

    def test_packet_digest_and_numbered_outcome_mapping_fail_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            altered = Path(directory) / contract.PACKET_FILENAME
            altered.write_text("not the pinned packet\n", encoding="utf-8")
            with self.assertRaisesRegex(contract.ContractFailure, "independently pinned bitemporal packet"):
                contract.verify_pinned_packet(altered)

        wrong_line = copy.deepcopy(self.matrix)
        wrong_line["cases"][0]["planner_refs"] = [f"{contract.PACKET_FILENAME}:67"]
        with self.assertRaisesRegex(contract.ContractFailure, "exact packet line"):
            contract.validate_structure(self.expected, wrong_line, True)


if __name__ == "__main__":
    unittest.main()

"""Mutation tests for the frozen decision-provenance oracle and review bindings."""

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

import verify_decision_provenance_contract as contract  # noqa: E402


class DecisionProvenanceContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.expected_raw = (ROOT / "tests/decision-provenance-expected-results.json").read_bytes()
        cls.matrix_raw = (ROOT / "tests/decision-provenance-adversarial-matrix.json").read_bytes()
        cls.expected = json.loads(cls.expected_raw)
        cls.matrix = json.loads(cls.matrix_raw)
        cls.expected_sha = hashlib.sha256(cls.expected_raw).hexdigest()
        cls.matrix_sha = hashlib.sha256(cls.matrix_raw).hexdigest()
        cls.plan = Path("/workspace/worktrees/planner-22e10d27ab804d3e872feefec257ae38/plans/planner-22e10d27ab804d3e872feefec257ae38.md")

    def validate(self, expected=None, matrix=None, expected_sha=None, matrix_sha=None, root=ROOT, plan=None):
        return contract.validate_artifacts(
            self.expected if expected is None else expected,
            self.matrix if matrix is None else matrix,
            self.expected_sha if expected_sha is None else expected_sha,
            self.matrix_sha if matrix_sha is None else matrix_sha,
            root=root,
            pinned_plan=self.plan if plan is None else plan,
        )

    @staticmethod
    def _receipt(expected, matrix):
        manifest = json.loads((ROOT / "tests/decision-provenance-frozen-hashes.json").read_text())
        expected_hash = hashlib.sha256((ROOT / "tests/decision-provenance-expected-results.json").read_bytes()).hexdigest()
        matrix_hash = hashlib.sha256((ROOT / "tests/decision-provenance-adversarial-matrix.json").read_bytes()).hexdigest()
        reviews = []
        case_by_id = {row["id"]: row for row in expected["cases"]}
        group_by_id = {row["group_id"]: row for row in matrix["groups"]}
        for group_id in contract.REQUIRED_GROUPS:
            reviews.append({
                "group_id": group_id,
                "result": "pass",
                "expected_outcome_sha256": contract.canonical_sha256(case_by_id[group_id]["expected"]),
                "matrix_assertion_ids": [row["id"] for row in group_by_id[group_id]["pinned_assertions"]],
                "packet_outcome_ref": case_by_id[group_id]["planner_refs"][0],
                "source_evidence_refs": case_by_id[group_id]["source_oracle_refs"],
            })
        facts = contract._reviewed_facts(expected)
        return {
            "schema_version": 1,
            "receipt_type": "wardatlas-decision-provenance-independent-review",
            "status": "passed",
            "oracle_author_id": contract.ORACLE_AUTHOR_ID,
            "reviewer_id": "unit-test-independent-source-reviewer",
            "independence_basis": "independent-source-audit",
            "same_model_fresh_session_only": False,
            "candidate_output_used_as_oracle": False,
            "artifact_hashes": {
                "expected_results": expected_hash,
                "adversarial_matrix": matrix_hash,
                "frozen_hash_manifest": hashlib.sha256((ROOT / "tests/decision-provenance-frozen-hashes.json").read_bytes()).hexdigest(),
                "pinned_packet": contract.PACKET_SHA256,
            },
            "reviewed_source_oracles": [
                {"path": path, "sha256": digest, "source_id": contract.SOURCE_IDS[path]}
                for path, digest in contract.SOURCE_ORACLES.items()
            ],
            "frozen_preexisting_oracle_hashes": manifest["files"],
            "reviewed_facts": facts,
            "reviewed_facts_sha256": contract.canonical_sha256(facts),
            "group_reviews": reviews,
        }

    def test_nine_literal_groups_have_nonzero_mapped_assertions(self):
        count, hashes = self.validate()
        self.assertEqual(tuple(row["id"] for row in self.expected["cases"]), contract.REQUIRED_GROUPS)
        self.assertEqual(tuple(row["group_id"] for row in self.matrix["groups"]), contract.REQUIRED_GROUPS)
        self.assertEqual(count, 54)
        self.assertEqual([len(group["pinned_assertions"]) for group in self.matrix["groups"]], [6, 9, 6, 6, 7, 3, 6, 5, 6])
        self.assertEqual(hashes["preexisting-oracle-and-matrix-count"], "30")

    def test_expected_value_mutations_are_rejected(self):
        changed = copy.deepcopy(self.expected)
        changed["baseline"]["decision_record"]["recorded_state"] = "resolved"
        changed_hash = contract.canonical_sha256(changed)
        with self.assertRaisesRegex(contract.ContractError, "expected-results bytes"):
            self.validate(expected=changed, expected_sha=changed_hash)

        changed = copy.deepcopy(self.expected)
        changed["baseline"]["claims"][1]["source"]["observed_at"] = "2026-10-14T16:05:00Z"
        with self.assertRaisesRegex(contract.ContractError, "expected-results bytes"):
            self.validate(expected=changed, expected_sha=contract.canonical_sha256(changed))

    def test_zero_assertion_and_changed_mapping_mutations_are_rejected(self):
        changed = copy.deepcopy(self.matrix)
        changed["groups"][0]["nonzero_assertion_count"] = 0
        with self.assertRaisesRegex(contract.ContractError, "nonzero assertions exactly"):
            contract._validate_fixed_shapes(self.expected, changed)

        changed = copy.deepcopy(self.matrix)
        changed["groups"][3]["pinned_assertions"] = []
        changed["groups"][3]["nonzero_assertion_count"] = 0
        with self.assertRaisesRegex(contract.ContractError, "has no mapped assertions"):
            contract._validate_fixed_shapes(self.expected, changed)

    def test_property_only_citation_mutant_is_rejected(self):
        changed = copy.deepcopy(self.expected)
        changed["baseline"]["claims"][0]["source"]["evidence_link"]["relationship"] = "source_id_property"
        with self.assertRaisesRegex(contract.ContractError, "node-to-node EVIDENCED_BY edge"):
            contract._validate_fixed_shapes(changed, self.matrix)

        # Exercise the source cross-check independently of the artifact byte pin.
        with self.assertRaisesRegex(contract.ContractError, "explicit node-to-node EVIDENCED_BY"):
            contract._verify_source_cross_checks(changed, ROOT)

    def test_frozen_manifest_and_preexisting_oracle_bytes_are_mutation_sensitive(self):
        manifest, raw, digest = contract._read_json(ROOT / "tests/decision-provenance-frozen-hashes.json", "manifest")
        self.assertEqual(len(contract.validate_manifest(manifest, digest, ROOT)), 30)
        changed_manifest = copy.deepcopy(manifest)
        changed_manifest["files"][0]["sha256"] = "0" * 64
        changed_raw_hash = contract.canonical_sha256(changed_manifest)
        with self.assertRaisesRegex(contract.ContractError, "manifest bytes"):
            contract.validate_manifest(changed_manifest, changed_raw_hash, ROOT)

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            (temp_root / "tests").mkdir()
            for row in manifest["files"]:
                source = ROOT / row["path"]
                target = temp_root / row["path"]
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, target)
            target = temp_root / manifest["files"][0]["path"]
            target.write_bytes(target.read_bytes() + b"\n")
            with self.assertRaisesRegex(contract.ContractError, "changed byte-for-byte"):
                contract.validate_manifest(manifest, digest, temp_root)

    def test_pinned_source_oracle_mutation_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            (temp_root / "tests").mkdir()
            for relative in contract.SOURCE_ORACLES:
                target = temp_root / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(ROOT / relative, target)
            source = temp_root / "tests/sop-impact-expected-results.json"
            source.write_bytes(source.read_bytes() + b"\n")
            with self.assertRaisesRegex(contract.ContractError, "byte hash differs"):
                contract._verify_source_cross_checks(self.expected, temp_root)

    def test_review_receipt_binds_all_groups_and_rejects_mutations(self):
        receipt = self._receipt(self.expected, self.matrix)
        count = contract.validate_review_receipt(
            receipt,
            self.expected,
            self.matrix,
            expected_sha256=self.expected_sha,
            matrix_sha256=self.matrix_sha,
            root=ROOT,
        )
        self.assertEqual(count, 9)

        mutations = [
            ("reviewer_id", contract.ORACLE_AUTHOR_ID),
            ("same_model_fresh_session_only", True),
            ("candidate_output_used_as_oracle", True),
        ]
        for key, value in mutations:
            changed = copy.deepcopy(receipt)
            changed[key] = value
            with self.subTest(field=key), self.assertRaises(contract.ContractError):
                contract.validate_review_receipt(
                    changed, self.expected, self.matrix,
                    expected_sha256=self.expected_sha, matrix_sha256=self.matrix_sha, root=ROOT,
                )

        changed = copy.deepcopy(receipt)
        changed["reviewed_facts"]["claims"][0]["source"]["title"] = "Candidate output title"
        with self.assertRaisesRegex(contract.ContractError, "exact record IDs"):
            contract.validate_review_receipt(
                changed, self.expected, self.matrix,
                expected_sha256=self.expected_sha, matrix_sha256=self.matrix_sha, root=ROOT,
            )

        changed = copy.deepcopy(receipt)
        changed["group_reviews"].pop()
        with self.assertRaisesRegex(contract.ContractError, "all nine groups"):
            contract.validate_review_receipt(
                changed, self.expected, self.matrix,
                expected_sha256=self.expected_sha, matrix_sha256=self.matrix_sha, root=ROOT,
            )


if __name__ == "__main__":
    unittest.main()

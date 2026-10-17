"""Mutation-focused tests for the candidate-independent handover-drift oracle."""

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

import verify_handover_drift_contract as contract  # noqa: E402


class HandoverDriftContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.expected_bytes = (ROOT / "tests/handover-drift-expected-results.json").read_bytes()
        cls.matrix_bytes = (ROOT / "tests/handover-drift-adversarial-matrix.json").read_bytes()
        cls.expected = json.loads(cls.expected_bytes)
        cls.matrix = json.loads(cls.matrix_bytes)
        cls.expected_sha = hashlib.sha256(cls.expected_bytes).hexdigest()
        cls.matrix_sha = hashlib.sha256(cls.matrix_bytes).hexdigest()
        cls.plan = Path(
            "/workspace/worktrees/planner-828ef7b6a76e4128b9e1b2ecad33c4a7/"
            "plans/planner-828ef7b6a76e4128b9e1b2ecad33c4a7.md"
        )
        cls.witness_plan = Path(
            "/workspace/worktrees/planner-d0017f7f160844fe9cf5a6036aa5068f/"
            "plans/planner-d0017f7f160844fe9cf5a6036aa5068f.md"
        )

    @staticmethod
    def _json_sha(value) -> str:
        raw = (json.dumps(value, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
        return hashlib.sha256(raw).hexdigest()

    def validate_structure(self, expected=None, matrix=None, require_nine_cases=True):
        return contract.validate_structure(
            self.expected if expected is None else expected,
            self.matrix if matrix is None else matrix,
            require_nine_cases,
        )

    def test_nine_groups_have_nonzero_traceable_assertions(self):
        count = self.validate_structure()
        self.assertEqual(count, 128)
        self.assertEqual([row["id"] for row in self.expected["cases"]], list(contract.REQUIRED_CASES))
        self.assertEqual([row["case_id"] for row in self.matrix["cases"]], list(contract.REQUIRED_CASES))
        self.assertEqual(
            [row["nonzero_assertion_count"] for row in self.matrix["cases"]],
            [15, 14, 14, 11, 30, 9, 12, 8, 15],
        )
        self.assertTrue(all(row["pinned_assertions"] for row in self.matrix["cases"]))
        self.assertEqual(
            self.expected["handover_witness_oracle_review"]["status"],
            "accepted-source-and-matrix-pinned",
        )

    def test_pinned_original_shift_sop_and_witness_sources_match_exact_literals(self):
        assertions, digests = contract.validate_artifacts(
            self.expected,
            self.matrix,
            self.expected_sha,
            self.matrix_sha,
            root=ROOT,
            pinned_plan=self.plan,
            handover_witness_plan=self.witness_plan,
            require_nine_cases=True,
        )
        self.assertEqual(assertions, 128)
        self.assertEqual(digests, contract.PINNED_ORACLES)
        self.assertEqual(contract.verify_pinned_packet(self.plan), contract.PACKET_SHA256)
        self.assertEqual(contract.verify_handover_witness_packet(self.witness_plan), contract.HANDOVER_WITNESS_PACKET_SHA256)
        witness_expected = json.loads((ROOT / "tests/handover-brief-expected-results.json").read_bytes())
        witness_matrix = json.loads((ROOT / "tests/handover-brief-adversarial-matrix.json").read_bytes())
        self.assertEqual(contract.verify_handover_witness_matrix(witness_expected, witness_matrix), 132)

    def test_accepted_handover_witness_matrix_literals_and_pointers_are_checked(self):
        witness_expected = json.loads((ROOT / "tests/handover-brief-expected-results.json").read_bytes())
        witness_matrix = json.loads((ROOT / "tests/handover-brief-adversarial-matrix.json").read_bytes())
        self.assertEqual(contract.verify_handover_witness_matrix(witness_expected, witness_matrix), 132)

        changed_literal = copy.deepcopy(witness_matrix)
        changed_literal["groups"][0]["assertions"][1]["equals"] = ["PHANTOM-PATH"]
        with self.assertRaisesRegex(contract.ContractFailure, "literal differs from its expected-results field"):
            contract.verify_handover_witness_matrix(witness_expected, changed_literal)

        changed_pointer = copy.deepcopy(witness_matrix)
        changed_pointer["groups"][0]["assertions"][1]["pointer"] = "/expected/missing-field"
        with self.assertRaisesRegex(contract.ContractFailure, "matrix JSON pointer does not resolve"):
            contract.verify_handover_witness_matrix(witness_expected, changed_pointer)

    def test_paths_provenance_typed_claims_and_confirmation_timestamp_are_pinned(self):
        mutations = [
            ("canonical_snapshot", "as_of", "2026-10-14T19:30:00Z"),
        ]
        for field, key, value in mutations:
            with self.subTest(field=field, key=key):
                changed = copy.deepcopy(self.expected)
                changed[field][key] = value
                with self.assertRaises(contract.ContractFailure):
                    self.validate_structure(expected=changed)

        changed = copy.deepcopy(self.expected)
        changed["canonical_snapshot"]["impact"]["path_evidence"][0]["source_ids"][0] = "SRC-FABRICATED"
        with self.assertRaisesRegex(contract.ContractFailure, "differs from accepted handover-witness oracle"):
            contract.verify_existing_oracles(ROOT, changed)

        changed = copy.deepcopy(self.expected)
        changed["canonical_snapshot"]["sop_witness"]["claim_evidence"][1]["claim_label"] = "current owner"
        with self.assertRaisesRegex(contract.ContractFailure, "typed role claims"):
            self.validate_structure(expected=changed)

        changed = copy.deepcopy(self.expected)
        changed["cases"][1]["expected"]["action_confirmation_timestamp_preserved_separately"] = False
        with self.assertRaisesRegex(contract.ContractFailure, "confirmation timestamp must be preserved"):
            contract.verify_existing_oracles(ROOT, changed)

    def test_observed_delta_and_hypothetical_branch_cannot_drift(self):
        changed = copy.deepcopy(self.expected)
        changed["cases"][2]["expected"]["removed_paths"].pop()
        with self.assertRaisesRegex(contract.ContractFailure, "remove exactly the historical paths"):
            contract.verify_existing_oracles(ROOT, changed)

        changed = copy.deepcopy(self.expected)
        changed["cases"][2]["expected"]["restoration_source_fact"]["source_id"] = "SRC-OTHER"
        with self.assertRaisesRegex(contract.ContractFailure, "restoration source ID"):
            contract.verify_existing_oracles(ROOT, changed)

        changed = copy.deepcopy(self.expected)
        changed["cases"][3]["expected"]["observed_mainline_paths"] = []
        with self.assertRaisesRegex(contract.ContractFailure, "preserve both observed mainline paths"):
            contract.verify_existing_oracles(ROOT, changed)

        changed = copy.deepcopy(self.expected)
        changed["cases"][3]["expected"]["branch_delta_label"] = "observed"
        with self.assertRaises(contract.ContractFailure):
            self.validate_structure(expected=changed)

    def test_each_edge_counterfactual_preserves_unknown_and_history(self):
        changed = copy.deepcopy(self.expected)
        changed["cases"][4]["expected"]["applicability_edge_removal"]["current_linkage_status"] = "resolved"
        with self.assertRaisesRegex(contract.ContractFailure, "linkage status differs from accepted handover-witness oracle"):
            contract.verify_existing_oracles(ROOT, changed)

        changed = copy.deepcopy(self.expected)
        changed["cases"][4]["expected"]["conflict_edge_removal"]["current_impact_paths"].pop()
        with self.assertRaisesRegex(contract.ContractFailure, "edge removal 2 outage paths differ from accepted handover-witness oracle"):
            contract.verify_existing_oracles(ROOT, changed)

        changed = copy.deepcopy(self.expected)
        changed["cases"][4]["expected"]["applicability_edge_removal"]["current_path_evidence"][0]["observed_at"][3] = "2026-10-14T19:00:00Z"
        with self.assertRaisesRegex(contract.ContractFailure, "edge removal 1 path provenance differs from accepted handover-witness oracle"):
            contract.verify_existing_oracles(ROOT, changed)

        changed = copy.deepcopy(self.expected)
        changed["cases"][4]["expected"]["conflict_edge_removal"]["current_path_evidence_unchanged"] = False
        with self.assertRaisesRegex(contract.ContractFailure, "conflict edge loss must preserve current ownership provenance"):
            self.validate_structure(expected=changed)

        changed = copy.deepcopy(self.expected)
        changed["cases"][4]["expected"]["conflict_edge_removal"]["current_path_evidence"][1]["source_titles"][3] = "Unpinned role source"
        with self.assertRaisesRegex(contract.ContractFailure, "path provenance differs from accepted handover-witness oracle"):
            contract.verify_existing_oracles(ROOT, changed)

        changed = copy.deepcopy(self.expected)
        changed["cases"][4]["expected"]["conflict_edge_removal"]["saved_snapshot_unchanged"] = False
        with self.assertRaisesRegex(contract.ContractFailure, "must preserve the historical snapshot"):
            contract.verify_existing_oracles(ROOT, changed)

        changed = copy.deepcopy(self.expected)
        changed["cases"][4]["expected"]["conflict_edge_removal"]["removed_edge"]["to"] = "SOP-OTHER"
        with self.assertRaisesRegex(contract.ContractFailure, "edge removal 2 differs from accepted handover-witness oracle"):
            contract.verify_existing_oracles(ROOT, changed)

    def test_restricted_probe_and_read_only_boundary_are_fixed(self):
        changed = copy.deepcopy(self.expected)
        changed["cases"][5]["expected"]["restricted_probe"]["acl"] = "operations"
        with self.assertRaisesRegex(contract.ContractFailure, "restricted-source probe differs from accepted handover-witness oracle"):
            contract.verify_existing_oracles(ROOT, changed)

        changed = copy.deepcopy(self.expected)
        changed["cases"][5]["expected"]["restricted_probe_literals_disclosed"] = ["SRC-RESTRICTED-SOP-PROBE-91"]
        with self.assertRaisesRegex(contract.ContractFailure, "restricted probe literals must not leak"):
            contract.verify_existing_oracles(ROOT, changed)

        changed = copy.deepcopy(self.expected)
        changed["cases"][6]["expected"]["graph_mutations"] = 1
        with self.assertRaisesRegex(contract.ContractFailure, "read-only"):
            contract.verify_existing_oracles(ROOT, changed)

        changed = copy.deepcopy(self.expected)
        changed["cases"][6]["expected"]["external_writes"] = ["ticket"]
        with self.assertRaisesRegex(contract.ContractFailure, "no external side effects"):
            contract.verify_existing_oracles(ROOT, changed)

    def test_time_rejection_and_repeat_reconnect_expectations_are_fixed(self):
        changed = copy.deepcopy(self.expected)
        changed["cases"][7]["expected"]["normalized_as_of"] = "2026-10-14T20:30:00Z"
        with self.assertRaisesRegex(contract.ContractFailure, "offset input/normalization differs from accepted handover-witness oracle"):
            contract.verify_existing_oracles(ROOT, changed)

        changed = copy.deepcopy(self.expected)
        changed["cases"][7]["expected"]["rejection_precedes_action_mutation"] = False
        with self.assertRaisesRegex(contract.ContractFailure, "rejected before mutation"):
            contract.verify_existing_oracles(ROOT, changed)

        changed = copy.deepcopy(self.expected)
        changed["cases"][8]["expected"]["relationship_delta_after_second_seed"] = 1
        with self.assertRaisesRegex(contract.ContractFailure, "repeat/reseed idempotence differs from accepted handover-witness oracle"):
            contract.verify_existing_oracles(ROOT, changed)

        changed = copy.deepcopy(self.expected)
        changed["cases"][8]["expected"]["repeated_query_result"] = "#/candidate-output"
        with self.assertRaisesRegex(contract.ContractFailure, "repeat query must reproduce the exact pinned snapshot"):
            contract.verify_existing_oracles(ROOT, changed)

        changed = copy.deepcopy(self.expected)
        changed["cases"][8]["expected"]["restarted_app_required"] = False
        with self.assertRaisesRegex(contract.ContractFailure, "fresh database client and restarted app"):
            self.validate_structure(expected=changed)

        changed = copy.deepcopy(self.expected)
        changed["cases"][8]["expected"]["cleanup_deletes_only_owned_graph"] = False
        with self.assertRaisesRegex(contract.ContractFailure, "repeat cleanup differs from accepted handover-witness oracle"):
            contract.verify_existing_oracles(ROOT, changed)

    def test_matrix_assertions_are_fixed_nonzero_and_resolve_to_their_case(self):
        changed = copy.deepcopy(self.matrix)
        changed["cases"][0]["pinned_assertions"][0]["expected_pointer"] = "/cases/8/expected/seed_runs"
        with self.assertRaisesRegex(contract.ContractFailure, "must point into its case"):
            self.validate_structure(matrix=changed)

        changed = copy.deepcopy(self.matrix)
        changed["cases"][1]["planner_refs"] = [f"{contract.PACKET_FILENAME}:64"]
        with self.assertRaisesRegex(contract.ContractFailure, "exact pinned packet line"):
            self.validate_structure(matrix=changed)

        changed = copy.deepcopy(self.matrix)
        changed["cases"][2]["pinned_assertions"] = []
        changed["cases"][2]["nonzero_assertion_count"] = 0
        with self.assertRaisesRegex(contract.ContractFailure, "has no pinned assertions"):
            self.validate_structure(matrix=changed)

        changed = copy.deepcopy(self.matrix)
        changed["cases"][3]["nonzero_assertion_count"] += 1
        with self.assertRaisesRegex(contract.ContractFailure, "differs from its pinned assertions"):
            self.validate_structure(matrix=changed)

        missing = copy.deepcopy(self.matrix)
        missing["cases"].pop()
        with self.assertRaisesRegex(contract.ContractFailure, "exactly nine groups"):
            self.validate_structure(matrix=missing)

    def test_matrix_must_cover_each_explicit_restoration_and_read_only_outcome(self):
        for case_id, pointers in contract.REQUIRED_MATRIX_POINTERS.items():
            case_index = contract.REQUIRED_CASES.index(case_id)
            for pointer in pointers:
                with self.subTest(case_id=case_id, pointer=pointer):
                    changed = copy.deepcopy(self.matrix)
                    case = changed["cases"][case_index]
                    case["pinned_assertions"] = [
                        assertion for assertion in case["pinned_assertions"]
                        if assertion["expected_pointer"] != pointer
                    ]
                    case["nonzero_assertion_count"] = len(case["pinned_assertions"])
                    with self.assertRaisesRegex(contract.ContractFailure, "required outcome field coverage missing"):
                        self.validate_structure(matrix=changed)

    def test_matrix_must_cover_every_fixed_expected_leaf(self):
        changed = copy.deepcopy(self.matrix)
        case = changed["cases"][0]
        case["pinned_assertions"] = [
            assertion for assertion in case["pinned_assertions"]
            if assertion["expected_pointer"] != "/cases/0/expected/cold_without_snapshot/saved_actions"
        ]
        case["nonzero_assertion_count"] = len(case["pinned_assertions"])
        with self.assertRaisesRegex(contract.ContractFailure, "expected fields lack matrix assertion coverage"):
            self.validate_structure(matrix=changed)

    def test_packet_hashes_and_required_outcome_headings_fail_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            altered_drift = Path(directory) / contract.PACKET_FILENAME
            altered_drift.write_text("not the pinned drift packet\n", encoding="utf-8")
            with self.assertRaisesRegex(contract.ContractFailure, "pinned handover-drift packet"):
                contract.verify_pinned_packet(altered_drift)

            altered_witness = Path(directory) / contract.HANDOVER_WITNESS_PACKET_FILENAME
            altered_witness.write_text("not the pinned witness packet\n", encoding="utf-8")
            with self.assertRaisesRegex(contract.ContractFailure, "pinned handover-witness packet"):
                contract.verify_handover_witness_packet(altered_witness)

    def test_any_changed_old_oracle_bytes_fail_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for relative in contract.PINNED_ORACLES:
                target = root / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(ROOT / relative, target)
            altered = root / "tests/sop-impact-expected-results.json"
            altered.write_bytes(altered.read_bytes() + b" ")
            with self.assertRaisesRegex(contract.ContractFailure, "unchanged oracle"):
                contract.verify_existing_oracles(root, self.expected)

            shutil.copyfile(ROOT / "tests/sop-impact-expected-results.json", altered)
            witness = root / "tests/handover-brief-expected-results.json"
            witness.write_bytes(witness.read_bytes() + b" ")
            with self.assertRaisesRegex(contract.ContractFailure, "unchanged oracle"):
                contract.verify_existing_oracles(root, self.expected)

            shutil.copyfile(ROOT / "tests/handover-brief-expected-results.json", witness)
            witness_matrix = root / "tests/handover-brief-adversarial-matrix.json"
            witness_matrix.write_bytes(witness_matrix.read_bytes() + b" ")
            with self.assertRaisesRegex(contract.ContractFailure, "unchanged oracle"):
                contract.verify_existing_oracles(root, self.expected)

    def test_expected_and_matrix_artifact_bytes_are_independently_pinned(self):
        changed_expected = copy.deepcopy(self.expected)
        changed_expected["cases"][1]["expected"]["winner_selected"] = True
        changed_matrix = copy.deepcopy(self.matrix)
        changed_matrix["cases"][0]["pinned_assertions"][0]["assertion"] = "candidate output decides"
        with self.assertRaisesRegex(contract.ContractFailure, "expected-results bytes"):
            contract.validate_artifacts(
                changed_expected,
                self.matrix,
                self._json_sha(changed_expected),
                self.matrix_sha,
                root=ROOT,
                pinned_plan=self.plan,
                handover_witness_plan=self.witness_plan,
                require_nine_cases=True,
            )
        with self.assertRaisesRegex(contract.ContractFailure, "matrix bytes"):
            contract.validate_artifacts(
                self.expected,
                changed_matrix,
                self.expected_sha,
                self._json_sha(changed_matrix),
                root=ROOT,
                pinned_plan=self.plan,
                handover_witness_plan=self.witness_plan,
                require_nine_cases=True,
            )


if __name__ == "__main__":
    unittest.main()

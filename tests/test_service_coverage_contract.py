"""Mutation tests for the immutable, candidate-independent service oracle."""

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

import verify_service_coverage_contract as contract  # noqa: E402


PLAN = Path(
    "/workspace/worktrees/planner-f3a45b2e499f44f6b202f14a16889105/"
    "plans/planner-f3a45b2e499f44f6b202f14a16889105.md"
)


class ServiceCoverageContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.expected_bytes = (ROOT / "tests/service-coverage-expected-results.json").read_bytes()
        cls.matrix_bytes = (ROOT / "tests/service-coverage-adversarial-matrix.json").read_bytes()
        cls.expected = json.loads(cls.expected_bytes)
        cls.matrix = json.loads(cls.matrix_bytes)
        cls.expected_sha = hashlib.sha256(cls.expected_bytes).hexdigest()
        cls.matrix_sha = hashlib.sha256(cls.matrix_bytes).hexdigest()

    def validate(self, expected=None, matrix=None, expected_sha=None, matrix_sha=None, root=ROOT, plan=PLAN):
        return contract.validate_artifacts(
            self.expected if expected is None else expected,
            self.matrix if matrix is None else matrix,
            self.expected_sha if expected_sha is None else expected_sha,
            self.matrix_sha if matrix_sha is None else matrix_sha,
            root=root,
            pinned_plan=plan,
            require_nine_groups=True,
        )

    def test_exact_nine_groups_and_nonzero_assertion_mappings(self):
        assertions, digests = self.validate()
        self.assertEqual(assertions, 52)
        self.assertEqual([row["id"] for row in self.expected["cases"]], list(contract.REQUIRED_CASES))
        self.assertEqual([row["id"] for row in self.matrix["cases"]], list(contract.REQUIRED_CASES))
        self.assertEqual(len(digests), 29)  # packet plus every pre-existing oracle/matrix
        self.assertTrue(all(row["nonzero_assertion_count"] > 0 for row in self.matrix["cases"]))
        self.assertTrue(all(len(row["pinned_assertions"]) == row["nonzero_assertion_count"] for row in self.matrix["cases"]))

    def test_all_fixed_service_paths_and_four_source_timestamps_are_pinned(self):
        fixture = self.expected["fixture"]
        self.assertEqual(fixture["portfolio_id"], "PORTFOLIO-OPS-01")
        self.assertEqual(fixture["relationship_order"], ["TRACKS", "DEPENDS_ON", "SERVES", "OWNED_BY"])
        self.assertEqual(
            [(row["service_id"], row["equipment_id"], row["department_id"], row["role_id"]) for row in fixture["services"]],
            list(contract.SERVICES),
        )
        self.assertEqual(
            [(s["id"], s["title"], s["observed_at"]) for s in fixture["sources"].values()],
            [
                ("SRC-SVC-CATALOG", "Synthetic Operations Service Catalog", "2026-10-14T21:00:00Z"),
                ("SRC-SVC-EQUIPMENT", "Synthetic Equipment Dependency Register", "2026-10-14T21:02:00Z"),
                ("SRC-SVC-DEPT-MAP", "Synthetic Service Department Coverage Register", "2026-10-14T21:03:00Z"),
                ("SRC-SVC-ROSTER", "Synthetic Operations Role Roster", "2026-10-14T21:04:00Z"),
            ],
        )
        for service_row in fixture["baseline_rows"]:
            self.assertEqual(
                [branch["relationship"] for branch in service_row["branches"].values()],
                list(contract.RELATIONSHIP_ORDER),
            )
            for branch in service_row["branches"].values():
                self.assertEqual(branch["evidence_link"]["relationship"], "EVIDENCED_BY")
                self.assertEqual(branch["evidence_link"]["to"], branch["source"]["id"])
                self.assertEqual(branch["evidence_link"]["evidence_node_type"], "Evidence")
                self.assertIs(branch["evidence_link"]["accessible"], True)

    def test_mutations_reject_changed_or_missing_path_source_time_and_unknown_values(self):
        mutations = []

        changed_path = copy.deepcopy(self.expected)
        changed_path["fixture"]["baseline_rows"][0]["branches"]["role"]["to"] = "ROLE-OTHER"
        mutations.append(("changed role path", changed_path))

        missing_path = copy.deepcopy(self.expected)
        del missing_path["fixture"]["baseline_rows"][1]["branches"]["role"]
        mutations.append(("missing role path", missing_path))

        changed_source = copy.deepcopy(self.expected)
        changed_source["cases"][1]["expected"]["rows"][1]["branches"]["dependency"]["source"]["id"] = "SRC-OTHER"
        mutations.append(("changed source ID", changed_source))

        missing_title = copy.deepcopy(self.expected)
        del missing_title["fixture"]["sources"]["equipment"]["title"]
        mutations.append(("missing source title", missing_title))

        missing_timestamp = copy.deepcopy(self.expected)
        del missing_timestamp["fixture"]["sources"]["equipment"]["observed_at"]
        mutations.append(("missing source timestamp", missing_timestamp))

        changed_time = copy.deepcopy(self.expected)
        changed_time["cases"][1]["expected"]["rows"][2]["source_provenance_in_relationship_order"][0]["observed_at"] = "2026-10-14T21:05:00Z"
        mutations.append(("changed source timestamp", changed_time))

        lost_evidence = copy.deepcopy(self.expected)
        lost_evidence["fixture"]["baseline_rows"][1]["branches"]["dependency"]["evidence_link"]["to"] = "SRC-OTHER"
        mutations.append(("changed evidence link", lost_evidence))

        changed_unknown = copy.deepcopy(self.expected)
        changed_unknown["cases"][0]["expected"]["coverage_status"] = "complete"
        mutations.append(("changed empty-graph unknown", changed_unknown))

        missing_unknown = copy.deepcopy(self.expected)
        del missing_unknown["cases"][0]["expected"]["no_count_or_existence_hint"]
        mutations.append(("missing empty-graph unknown guard", missing_unknown))

        changed_incomplete = copy.deepcopy(self.expected)
        changed_incomplete["cases"][4]["expected"]["materials_status"] = "complete"
        mutations.append(("changed incomplete status", changed_incomplete))

        for name, altered in mutations:
            with self.subTest(mutation=name):
                with self.assertRaises(contract.ContractFailure):
                    contract.validate_fixed_outcomes(altered)

    def test_equipment_evidence_edge_case_pins_intact_properties_and_suppressed_branch(self):
        evidence = self.expected["cases"][4]["expected"]
        self.assertEqual(evidence["mutation"], {
            "from": "EQ-TUG-05", "relationship": "EVIDENCED_BY", "to": "SRC-SVC-EQUIPMENT", "remove_only_this_edge": True
        })
        self.assertEqual(evidence["dependency_graph_edge_remains"], {
            "from": "SVC-MATERIALS-01", "relationship": "DEPENDS_ON", "to": "EQ-TUG-05"
        })
        self.assertEqual(evidence["dependency_relationship_source_id_properties_remain"], {
            "source_id": "SRC-SVC-EQUIPMENT",
            "title": "Synthetic Equipment Dependency Register",
            "observed_at": "2026-10-14T21:02:00Z",
        })
        materials = evidence["rows"][1]
        self.assertIsNone(materials["branches"]["dependency"])
        self.assertIsNotNone(materials["branches"]["department"])
        self.assertIsNotNone(materials["branches"]["role"])
        self.assertEqual(materials["coverage_status"], "incomplete/unknown")
        self.assertNotIn("SRC-SVC-EQUIPMENT", [row["id"] for row in materials["source_provenance_in_relationship_order"]])

    def test_role_mutation_and_acl_invariance_cannot_be_weakened(self):
        role = self.expected["cases"][3]["expected"]
        self.assertIsNone(role["rows"][1]["branches"]["role"])
        self.assertIsNotNone(role["rows"][1]["branches"]["dependency"])
        acl = self.expected["cases"][6]["expected"]
        self.assertEqual(acl["viewer_scope"], "operations")
        self.assertIs(acl["full_api_projection_unchanged"], True)
        self.assertIs(acl["visible_counts_unchanged"], True)
        self.assertIs(acl["browser_text_unchanged"], True)
        self.assertIs(acl["accessibility_tree_semantically_unchanged"], True)
        self.assertIs(acl["hidden_identifier_title_fact_count_existence_or_paraphrase_disclosed"], False)
        for key, value in (("viewer_scope", "admin"), ("full_api_projection_unchanged", False), ("accessibility_tree_semantically_unchanged", False), ("hidden_identifier_title_fact_count_existence_or_paraphrase_disclosed", True)):
            altered = copy.deepcopy(self.expected)
            altered["cases"][6]["expected"][key] = value
            with self.subTest(acl_field=key), self.assertRaises(contract.ContractFailure):
                contract.validate_fixed_outcomes(altered)
        missing_acl = copy.deepcopy(self.expected)
        del missing_acl["cases"][6]["expected"]["browser_text_unchanged"]
        with self.assertRaises(contract.ContractFailure):
            contract.validate_fixed_outcomes(missing_acl)

    def test_missing_group_zero_assertions_and_wrong_pointer_fail(self):
        missing = copy.deepcopy(self.matrix)
        missing["cases"].pop()
        with self.assertRaisesRegex(contract.ContractFailure, "exactly nine groups"):
            contract.validate_structure(self.expected, missing, True)

        zero = copy.deepcopy(self.matrix)
        zero["cases"][0]["pinned_assertions"] = []
        zero["cases"][0]["nonzero_assertion_count"] = 0
        with self.assertRaisesRegex(contract.ContractFailure, "has no pinned assertions"):
            contract.validate_structure(self.expected, zero, True)

        wrong_pointer = copy.deepcopy(self.matrix)
        wrong_pointer["cases"][4]["pinned_assertions"][0]["expected_pointer"] = "/candidate/output"
        with self.assertRaisesRegex(contract.ContractFailure, "required fixed outcome fields"):
            contract.validate_structure(self.expected, wrong_pointer, True)

        changed_mapping = copy.deepcopy(self.matrix)
        changed_mapping["cases"][8]["nonzero_assertion_count"] += 1
        with self.assertRaisesRegex(contract.ContractFailure, "count differs"):
            contract.validate_structure(self.expected, changed_mapping, True)

    def test_recomputed_artifact_digest_cannot_rewrite_pinned_expectations(self):
        changed = copy.deepcopy(self.expected)
        changed["cases"][1]["expected"]["rows"][0]["branches"]["dependency"]["to"] = "EQ-OTHER"
        changed_digest = hashlib.sha256(json.dumps(changed, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()
        with self.assertRaisesRegex(contract.ContractFailure, "expected-results bytes differ"):
            self.validate(expected=changed, expected_sha=changed_digest)

        altered_matrix = copy.deepcopy(self.matrix)
        altered_matrix["cases"][0]["failure_detected"] = "candidate output rewrites the oracle"
        changed_matrix_digest = hashlib.sha256(json.dumps(altered_matrix, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()
        with self.assertRaisesRegex(contract.ContractFailure, "adversarial-matrix bytes differ"):
            self.validate(matrix=altered_matrix, matrix_sha=changed_matrix_digest)

    def test_every_preexisting_oracle_and_matrix_hash_is_frozen(self):
        digests = contract.verify_existing_oracles(ROOT, self.expected)
        self.assertEqual(set(digests), set(contract.PINNED_EXISTING))
        self.assertEqual(len(digests), 28)

        with tempfile.TemporaryDirectory() as directory:
            temp_root = Path(directory)
            for relative in contract.PINNED_EXISTING:
                target = temp_root / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(ROOT / relative, target)
            changed = temp_root / "tests/shift-board-expected-results.json"
            changed.write_bytes(changed.read_bytes() + b" ")
            with self.assertRaisesRegex(contract.ContractFailure, "differs from its frozen byte hash"):
                contract.verify_existing_oracles(temp_root, self.expected)

    def test_packet_digest_and_outcome_mapping_fail_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            altered = Path(directory) / contract.PACKET_FILENAME
            altered.write_text("not the pinned packet\n", encoding="utf-8")
            with self.assertRaisesRegex(contract.ContractFailure, "independently pinned service-coverage packet"):
                contract.verify_pinned_packet(altered)

        wrong_line = copy.deepcopy(self.matrix)
        wrong_line["cases"][0]["planner_refs"] = [f"{contract.PACKET_FILENAME}:42"]
        with self.assertRaisesRegex(contract.ContractFailure, "exact packet line"):
            contract.validate_structure(self.expected, wrong_line, True)


if __name__ == "__main__":
    unittest.main()

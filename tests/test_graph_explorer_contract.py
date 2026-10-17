"""Mutation tests for the immutable candidate-independent graph-explorer oracle."""

from __future__ import annotations

import copy
import contextlib
import hashlib
import io
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import verify_graph_explorer_contract as contract  # noqa: E402


class GraphExplorerContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.expected_bytes = (ROOT / "tests/graph-explorer-expected-results.json").read_bytes()
        cls.matrix_bytes = (ROOT / "tests/graph-explorer-adversarial-matrix.json").read_bytes()
        cls.expected = json.loads(cls.expected_bytes)
        cls.matrix = json.loads(cls.matrix_bytes)
        cls.expected_sha = hashlib.sha256(cls.expected_bytes).hexdigest()
        cls.matrix_sha = hashlib.sha256(cls.matrix_bytes).hexdigest()
        cls.plan = Path(contract.PACKET_PATH)

    def validate(self, expected=None, matrix=None, expected_sha=None, matrix_sha=None, root=ROOT, plan=None, receipt=None):
        return contract.validate_artifacts(
            self.expected if expected is None else expected,
            self.matrix if matrix is None else matrix,
            self.expected_sha if expected_sha is None else expected_sha,
            self.matrix_sha if matrix_sha is None else matrix_sha,
            root=root,
            pinned_plan=self.plan if plan is None else plan,
            check_frozen_hashes=True,
            review_receipt=receipt,
        )

    @staticmethod
    def set_pointer(document, pointer, value):
        tokens = pointer[1:].split("/")
        current = document
        for encoded in tokens[:-1]:
            token = encoded.replace("~1", "/").replace("~0", "~")
            current = current[int(token)] if isinstance(current, list) else current[token]
        last = tokens[-1].replace("~1", "/").replace("~0", "~")
        if isinstance(current, list):
            current[int(last)] = value
        else:
            current[last] = value

    def test_nine_fixed_groups_have_nonzero_traceable_assertions(self):
        assertions, source_hashes = self.validate()
        self.assertEqual(tuple(case["id"] for case in self.expected["cases"]), contract.REQUIRED_CASES)
        self.assertEqual(tuple(group["case_id"] for group in self.matrix["groups"]), contract.REQUIRED_CASES)
        self.assertEqual(assertions, 77)
        self.assertEqual(sum(group["nonzero_assertion_count"] for group in self.matrix["groups"]), assertions)
        self.assertTrue(all(group["nonzero_assertion_count"] > 0 and group["pinned_assertions"] for group in self.matrix["groups"]))
        self.assertEqual(len(source_hashes), 23)  # planner packet plus every frozen expected/matrix artifact
        self.assertEqual(len(contract.PINNED_ORACLE_HASHES), 22)
        self.assertIn("SRC-SOP-CURRENT-V2", json.dumps(self.expected["cases"][5]))
        self.assertEqual(
            self.expected["cases"][5]["expected"]["delete_only_v2_source_node"]["remaining_edges"],
            ["REL-SOP-V2-APPLIES-C4", "REL-SOP-V2-CLAIMS-FACILITIES", "REL-SOP-9-CONFLICT"],
        )

    def test_each_pinned_path_edge_evidence_time_acl_and_unknown_value_is_mutation_sensitive(self):
        mutations = [
            ("/cases/0/expected/board_state", "empty"),
            ("/cases/0/expected/risk_or_conflict_absence_concluded", True),
            ("/cases/1/expected/as_of", "2026-10-14T18:31:00Z"),
            ("/cases/1/expected/ordered_paths/0/1", "CONVEYOR-C3"),
            ("/cases/1/expected/path_edges/0/0/id", "REL-WRONG"),
            ("/cases/1/expected/path_edges/0/1/type", "SERVES"),
            ("/cases/1/expected/evidence_edges/0/id", "REL-WRONG-EVIDENCE"),
            ("/cases/1/expected/path_evidence/0/sources/0/source_id", "SRC-WRONG"),
            ("/cases/1/expected/path_evidence/0/sources/0/title", "Candidate-generated title"),
            ("/cases/1/expected/path_evidence/0/sources/0/observed_at", "2026-10-14T18:01:00Z"),
            ("/cases/2/expected/conflict_edge/status", "resolved"),
            ("/cases/2/expected/claims/0/role_id", "ROLE-STERILE-OPS-COORD"),
            ("/cases/2/expected/claims/0/claim_label", "current owner"),
            ("/cases/2/expected/claims/0/source_title", "Wrong SOP title"),
            ("/cases/2/expected/claims/1/observed_at", "2026-10-14T16:05:00Z"),
            ("/cases/2/expected/stale_archive/referenced_equipment_id", "CONVEYOR-C4"),
            ("/cases/2/expected/winner_selected", True),
            ("/cases/2/expected/direct_incident_to_sop_causal_edge_asserted", True),
            ("/cases/3/expected/observed_restoration/active_impact_edges", ["REL-INC204-C4"]),
            ("/cases/3/expected/observed_restoration/sop_conflict_status", "resolved"),
            ("/cases/3/expected/observed_restoration/sop_claims_remain/0/source_id", "SRC-OTHER"),
            ("/cases/3/expected/observed_restoration/sop_claims_remain/0/evidence_edge_id", "REL-SOP-V3-EVIDENCE"),
            ("/cases/3/expected/hypothetical_branch/event_kind", "observed"),
            ("/cases/3/expected/hypothetical_branch/branch_status", "observed-restored"),
            ("/cases/3/expected/hypothetical_branch/observed_state_mutated", True),
            ("/cases/4/expected/ownership_boundary/0/role_id", "ROLE-DOCK-SWING"),
            ("/cases/4/expected/ownership_boundary/1/effective_at", "2026-10-14T20:29:00Z"),
            ("/cases/4/expected/ownership_boundary/1/source_title", "Wrong roster title"),
            ("/cases/4/expected/review_edges/0/id", "REL-LEXICAL-ONLY"),
            ("/cases/4/expected/unlinked_coverage_status", "current"),
            ("/cases/4/expected/inc204_and_inc207_joined", True),
            ("/cases/4/expected/dock_roster_revision_caused_inc204_outage", True),
            ("/cases/5/expected/test_owned_edge_removal_targets/dock_ownership_evidence_edges/0", "REL-NOT-OWNED"),
            ("/cases/5/expected/delete_only_v2_source_node/owned_graph_node_deleted", "SRC-SOP-CURRENT-V3"),
            ("/cases/5/expected/delete_only_v2_source_node/removed_evidence_edge", "REL-SOP-V3-EVIDENCE"),
            ("/cases/5/expected/delete_only_v2_source_node/remaining_edges/1", "REL-SOP-V3-CLAIMS-MATERIALS"),
            ("/cases/5/expected/delete_only_v2_source_node/v2_source_evidence_status", "known"),
            ("/cases/5/expected/delete_only_v2_source_node/v2_source_row_returned", True),
            ("/cases/5/expected/delete_only_v2_source_node/v2_source_tuple_after_delete/title", "Leaked title"),
            ("/cases/5/expected/delete_only_v2_source_node/v3_claim_and_source_unchanged/observed_at", "2026-10-14T16:05:00Z"),
            ("/cases/5/expected/delete_only_v2_source_node/incident_paths_after_delete/0/1", "CONVEYOR-C3"),
            ("/cases/5/expected/delete_only_v2_source_node/conflict_status_after_delete", "resolved"),
            ("/cases/5/expected/delete_only_v2_source_node/reseed_restores_exact_v2_source_tuple_and_edge", False),
            ("/cases/6/expected/viewer_scope", "restricted"),
            ("/cases/6/expected/denied_acl", "operations"),
            ("/cases/6/expected/restricted_probes/0/source_id", "SRC-VISIBLE"),
            ("/cases/6/expected/restricted_probes/0/source_title", "Visible source"),
            ("/cases/6/expected/restricted_probes/0/fact", "Public text"),
            ("/cases/6/expected/visible_api_graph_detail_dom_accessibility_and_case_counts_semantically_identical_before_after", False),
            ("/cases/6/expected/hidden_counts_exposed", True),
            ("/cases/7/expected/selection_examples/1/relationship_type", "SERVES"),
            ("/cases/7/expected/selection_examples/2/title", "Wrong title"),
            ("/cases/7/expected/selection_examples/2/observed_at", "2026-10-14T18:03:00Z"),
            ("/cases/7/expected/missing_relationship_or_source_label", "resolved"),
            ("/cases/7/expected/api_and_accessible_ui_semantically_agree", False),
            ("/cases/7/expected/keyboard_selection_and_focus_work_without_coordinates", False),
            ("/cases/7/expected/labels_rendered_as_safe_text", False),
            ("/cases/8/expected/relationship_count_delta_after_second_seed", 1),
            ("/cases/8/expected/duplicate_fixture_ids", ["DUPLICATE"]),
            ("/cases/8/expected/selected_time_graph_semantics_after_reseed", "different"),
            ("/cases/8/expected/new_falkordb_client_required", False),
            ("/cases/8/expected/selected_time_graph_semantics_after_reconnect", "different"),
            ("/cases/8/expected/graph_browse_node_relationship_action_or_file_mutations", 1),
            ("/cases/8/expected/messages_sent", True),
            ("/cases/8/expected/calls_made", True),
            ("/cases/8/expected/tickets_created", True),
            ("/cases/8/expected/external_writes", ["POST"]),
            ("/cases/8/expected/cleanup_deletes_only_unique_test_owned_graph", False),
            ("/cases/8/expected/cleanup_stops_only_test_owned_processes", False),
        ]
        for pointer, wrong_value in mutations:
            with self.subTest(pointer=pointer):
                changed = copy.deepcopy(self.expected)
                self.set_pointer(changed, pointer, wrong_value)
                with self.assertRaises(contract.ContractFailure):
                    contract.validate_structure(changed, self.matrix)

    def test_missing_changed_or_zero_assertion_groups_are_rejected(self):
        missing = copy.deepcopy(self.expected)
        missing["cases"].pop()
        with self.assertRaises(contract.ContractFailure):
            contract.validate_structure(missing, self.matrix)

        changed_id = copy.deepcopy(self.expected)
        changed_id["cases"][2]["id"] = "renamed-sop-outcome"
        with self.assertRaises(contract.ContractFailure):
            contract.validate_structure(changed_id, self.matrix)

        missing_group = copy.deepcopy(self.matrix)
        missing_group["groups"].pop()
        with self.assertRaises(contract.ContractFailure):
            contract.validate_structure(self.expected, missing_group)

        zero = copy.deepcopy(self.matrix)
        zero["groups"][5]["pinned_assertions"] = []
        zero["groups"][5]["nonzero_assertion_count"] = 0
        with self.assertRaisesRegex(contract.ContractFailure, "maps zero assertions"):
            contract.validate_structure(self.expected, zero)

        bad_pointer = copy.deepcopy(self.matrix)
        bad_pointer["groups"][1]["pinned_assertions"][0]["pointer"] = "/cases/2/expected"
        with self.assertRaises(contract.ContractFailure):
            contract.validate_structure(self.expected, bad_pointer)

        no_full_object = copy.deepcopy(self.matrix)
        no_full_object["groups"][2]["pinned_assertions"] = [
            assertion for assertion in no_full_object["groups"][2]["pinned_assertions"]
            if assertion["pointer"] != "/cases/2/expected"
        ]
        no_full_object["groups"][2]["nonzero_assertion_count"] = len(no_full_object["groups"][2]["pinned_assertions"])
        with self.assertRaisesRegex(contract.ContractFailure, "full fixed outcome object"):
            contract.validate_structure(self.expected, no_full_object)

        changed_mapping = copy.deepcopy(self.matrix)
        changed_mapping["groups"][7]["planner_refs"] = ["planner-d7534434df6b4d01876c99a92bbb3f11.md:51"]
        with self.assertRaises(contract.ContractFailure):
            contract.validate_structure(self.expected, changed_mapping)

    def test_missing_or_changed_pinned_hashes_and_old_oracle_bytes_fail_closed(self):
        missing_pin = copy.deepcopy(self.expected)
        missing_pin["frozen_preexisting_oracle_files"].pop()
        with self.assertRaisesRegex(contract.ContractFailure, "frozen preexisting"):
            contract.validate_structure(missing_pin, self.matrix)

        changed_expected_pin = copy.deepcopy(self.expected)
        changed_expected_pin["frozen_preexisting_oracle_files"][0]["sha256"] = "0" * 64
        with self.assertRaises(contract.ContractFailure):
            contract.validate_structure(changed_expected_pin, self.matrix)

        changed_matrix_pin = copy.deepcopy(self.matrix)
        changed_matrix_pin["frozen_oracle_hashes"]["tests/expected-results.json"] = "0" * 64
        with self.assertRaisesRegex(contract.ContractFailure, "matrix is missing or changes"):
            contract.validate_structure(self.expected, changed_matrix_pin)

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            for relative in contract.PINNED_ORACLE_HASHES:
                destination = root / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(ROOT / relative, destination)
            altered = root / "tests/shift-board-expected-results.json"
            altered.write_bytes(altered.read_bytes() + b" ")
            with self.assertRaisesRegex(contract.ContractFailure, "frozen preexisting oracle"):
                contract.verify_frozen_oracle_hashes(root)

            altered.unlink()
            with self.assertRaisesRegex(contract.ContractFailure, "cannot read frozen preexisting"):
                contract.verify_frozen_oracle_hashes(root)

    def test_plan_hash_and_exact_outcome_headings_are_pinned(self):
        self.assertEqual(contract.verify_pinned_packet(self.plan), contract.PACKET_SHA256)
        with tempfile.TemporaryDirectory() as temp_dir:
            altered = Path(temp_dir) / contract.PACKET_FILENAME
            altered.write_bytes(self.plan.read_bytes() + b"\nmutated\n")
            with self.assertRaisesRegex(contract.ContractFailure, "independently pinned"):
                contract.verify_pinned_packet(altered)

    def valid_review_receipt(self):
        return {
            "schema_version": 1,
            "receipt_type": "graph-explorer-independent-oracle-review",
            "packet_sha256": contract.PACKET_SHA256,
            "expected_sha256": self.expected_sha,
            "matrix_sha256": self.matrix_sha,
            "frozen_oracle_hashes": copy.deepcopy(contract.PINNED_ORACLE_HASHES),
            "oracle_author_id": "oracle-author",
            "reviewer_id": "independent-reviewer",
            "independence_basis": "independent-source-audit",
            "same_model_fresh_session_only": False,
            "candidate_output_used_as_oracle": False,
            "group_reviews": [
                {
                    "case_id": group["case_id"],
                    "verdict": "pass",
                    "reviewed_assertion_ids": [row["id"] for row in group["pinned_assertions"]],
                    "evidence_refs": [f"tests/graph-explorer-expected-results.json#/cases/{index}/expected"],
                }
                for index, group in enumerate(self.matrix["groups"])
            ],
        }

    def test_future_independent_review_receipt_is_supported_and_bound_to_all_groups(self):
        receipt = self.valid_review_receipt()
        count = contract.validate_review_receipt(
            receipt,
            self.matrix,
            expected_sha256=self.expected_sha,
            matrix_sha256=self.matrix_sha,
            packet_sha256=contract.PACKET_SHA256,
        )
        self.assertEqual(count, 77)

        changed_hash = copy.deepcopy(receipt)
        changed_hash["expected_sha256"] = "0" * 64
        with self.assertRaises(contract.ContractFailure):
            contract.validate_review_receipt(changed_hash, self.matrix, expected_sha256=self.expected_sha, matrix_sha256=self.matrix_sha, packet_sha256=contract.PACKET_SHA256)

        same_model_only = copy.deepcopy(receipt)
        same_model_only["same_model_fresh_session_only"] = True
        with self.assertRaisesRegex(contract.ContractFailure, "same model alone"):
            contract.validate_review_receipt(same_model_only, self.matrix, expected_sha256=self.expected_sha, matrix_sha256=self.matrix_sha, packet_sha256=contract.PACKET_SHA256)

        incomplete = copy.deepcopy(receipt)
        incomplete["group_reviews"][5]["reviewed_assertion_ids"].pop()
        with self.assertRaisesRegex(contract.ContractFailure, "does not cover every mapped assertion"):
            contract.validate_review_receipt(incomplete, self.matrix, expected_sha256=self.expected_sha, matrix_sha256=self.matrix_sha, packet_sha256=contract.PACKET_SHA256)

        no_evidence = copy.deepcopy(receipt)
        no_evidence["group_reviews"][0]["evidence_refs"] = []
        with self.assertRaisesRegex(contract.ContractFailure, "no evidence references"):
            contract.validate_review_receipt(no_evidence, self.matrix, expected_sha256=self.expected_sha, matrix_sha256=self.matrix_sha, packet_sha256=contract.PACKET_SHA256)

        non_object_group = copy.deepcopy(receipt)
        non_object_group["group_reviews"][0] = "not a group"
        with self.assertRaisesRegex(contract.ContractFailure, "not an object"):
            contract.validate_review_receipt(non_object_group, self.matrix, expected_sha256=self.expected_sha, matrix_sha256=self.matrix_sha, packet_sha256=contract.PACKET_SHA256)

    def test_cli_accepts_a_future_receipt_against_the_current_frozen_artifacts(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            receipt_path = Path(temp_dir) / "independent-review.json"
            receipt_path.write_text(json.dumps(self.valid_review_receipt()), encoding="utf-8")
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                result = contract.main([
                    "--expected", str(ROOT / "tests/graph-explorer-expected-results.json"),
                    "--matrix", str(ROOT / "tests/graph-explorer-adversarial-matrix.json"),
                    "--pinned-plan", str(self.plan),
                    "--require-nine-cases",
                    "--check-frozen-oracle-hashes",
                    "--review-receipt", str(receipt_path),
                ])
            self.assertEqual(result, 0)
            self.assertIn("future independent-review receipt validated", stdout.getvalue())


if __name__ == "__main__":
    unittest.main()

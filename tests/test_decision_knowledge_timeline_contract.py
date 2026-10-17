"""Mutation tests for the frozen, candidate-independent timeline artifacts."""

from __future__ import annotations

import copy
import contextlib
import hashlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import verify_decision_knowledge_timeline_contract as contract  # noqa: E402
import verify_decision_knowledge_timeline_review as review  # noqa: E402


EXPECTED_PATH = ROOT / "contracts/decision-knowledge-timeline/expected-results.json"
MATRIX_PATH = ROOT / "contracts/decision-knowledge-timeline/adversarial-matrix.json"
MANIFEST_PATH = ROOT / "contracts/decision-knowledge-timeline/frozen-sources.json"
TASK_PACKET = Path("/workspace/worktrees/planner-24567af7e5ee4642a9b81ba8ce6f184c/plans/planner-24567af7e5ee4642a9b81ba8ce6f184c.md")


def _read(path: Path):
    raw = path.read_bytes()
    return json.loads(raw), hashlib.sha256(raw).hexdigest()


class DecisionKnowledgeTimelineContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.expected, cls.expected_hash = _read(EXPECTED_PATH)
        cls.matrix, cls.matrix_hash = _read(MATRIX_PATH)
        cls.manifest, cls.manifest_hash = _read(MANIFEST_PATH)
        cls.source_files = {}
        for relative in contract.SOURCE_HASHES:
            cls.source_files[relative] = json.loads((ROOT / relative).read_text(encoding="utf-8"))

    def expect_rejected(self, expected=None, matrix=None, sources=None):
        expected = self.expected if expected is None else expected
        matrix = self.matrix if matrix is None else matrix
        sources = self.source_files if sources is None else sources
        rejected = False
        try:
            contract._validate_semantics(expected, matrix)
        except (contract.ContractError, KeyError, TypeError, IndexError):
            rejected = True
        # If the mutation targets a source-bound fact rather than shape, the
        # independent source join must reject it too.
        try:
            contract._cross_check_sources(expected, sources)
        except (contract.ContractError, KeyError, TypeError):
            rejected = True
        self.assertTrue(rejected, "the changed literal must be rejected by either the shape contract or source cross-check")

    def test_fixed_contract_has_twelve_groups_and_nonzero_literal_mappings(self):
        count = contract._validate_semantics(self.expected, self.matrix)
        self.assertEqual(len(self.expected["cases"]), 12)
        self.assertGreater(count, 0)
        self.assertTrue(all(group["nonzero_assertion_count"] > 0 for group in self.matrix["groups"]))
        contract._cross_check_sources(self.expected, self.source_files)

    def test_exact_frozen_artifact_and_source_hashes_pass(self):
        count, files = contract.validate_documents(
            self.expected, self.matrix, self.manifest, root=ROOT, task_packet=TASK_PACKET,
            expected_sha256=self.expected_hash, matrix_sha256=self.matrix_hash,
            manifest_sha256=self.manifest_hash,
        )
        self.assertGreater(count, 0)
        self.assertEqual(len(files), 4)

    def test_mutated_expected_or_matrix_bytes_fail_fixed_hash_pins(self):
        with self.assertRaises(contract.ContractError):
            contract.validate_documents(
                self.expected, self.matrix, self.manifest, root=ROOT, task_packet=TASK_PACKET,
                expected_sha256="0" * 64, matrix_sha256=self.matrix_hash,
                manifest_sha256=self.manifest_hash,
            )
        with self.assertRaises(contract.ContractError):
            contract.validate_documents(
                self.expected, self.matrix, self.manifest, root=ROOT, task_packet=TASK_PACKET,
                expected_sha256=self.expected_hash, matrix_sha256="0" * 64,
                manifest_sha256=self.manifest_hash,
            )

    def _receipt(self):
        facts = {"baseline": self.expected["baseline"], "scope": self.expected["scope"], "cases": self.expected["cases"]}
        audit = review._independent_source_audit(self.expected, self.source_files, TASK_PACKET)
        matrix_by_id = {g["group_id"]: g for g in self.matrix["groups"]}
        return {
            "schema_version": 1,
            "receipt_type": contract.RECEIPT_TYPE,
            "status": "passed",
            "oracle_author_id": contract.AUTHOR_ID,
            "reviewer_id": "test-reviewer-separate-source-audit",
            "reviewer_model": "openai/gpt-6-luna",
            "reviewer_execution_id": "test-only-audit-execution",
            "reviewer_independence_disclosure": "Separate same-model reviewer; not a different-model or human oracle. Session freshness alone is not the basis.",
            "source_audit_method": "separate-source-reviewer-plus-independent-source-reconstruction",
            "independence_basis": "independent-source-audit",
            "same_model_fresh_session_only": False,
            "candidate_output_is_oracle": False,
            "expected_results_sha256": self.expected_hash,
            "adversarial_matrix_sha256": self.matrix_hash,
            "frozen_sources_sha256": self.manifest_hash,
            "task_packet_sha256": contract.TASK_PACKET_SHA256,
            "docket_packet_sha256": contract.DOCKET_PACKET_SHA256,
            "review_summary": {
                "all_group_assertion_counts_match": True,
                "all_matrix_pointers_resolve": True,
                "group8_full_shifted_cutoff_projections_pinned": True,
                "matrix_assertions": sum(len(g["pinned_assertions"]) for g in self.matrix["groups"]),
                "outcomes_reconstructed": 12,
                "pre_1605_claim_paths_use_only_sop_impact": True,
                "record_edge_removal_keeps_timeline_claims_conflict_and_record_only_empty_unknown": True,
                "reseed_reconnect_read_only_and_cleanup_pinned": True,
                "restricted_acl_and_decoy_invariance_pinned": True,
                "timestamp_offset_and_rejection_pinned": True,
            },
            "reviewed_source_oracles": [
                {"path": path, "sha256": digest, "source_id": contract.SOURCE_IDS[path]}
                for path, digest in contract.SOURCE_HASHES.items()
            ],
            "reviewed_facts_sha256": contract.canonical_sha256(facts),
            "source_audit_facts_sha256": contract.canonical_sha256(audit),
            "group_reviews": [
                {
                    "group_id": case["id"],
                    "result": "pass",
                    "expected_sha256": contract.canonical_sha256(case["expected"]),
                    "matrix_assertion_ids": [a["id"] for a in matrix_by_id[case["id"]]["pinned_assertions"]],
                    "source_oracle_refs": case["source_oracle_refs"],
                    "packet_outcome_ref": case["planner_refs"][0],
                    "source_audit_note": "Test-only receipt fixture for source-reference binding checks.",
                }
                for case in self.expected["cases"]
            ],
        }

    def test_review_receipt_binds_twelve_source_audits(self):
        receipt = self._receipt()
        self.assertEqual(
            contract.validate_receipt(
                receipt, self.expected, self.matrix,
                expected_sha256=self.expected_hash, matrix_sha256=self.matrix_hash,
                manifest_sha256=self.manifest_hash,
            ),
            12,
        )

    def test_review_receipt_rejects_wrong_reviewer_or_changed_mapping(self):
        receipt = self._receipt()
        receipt["reviewer_id"] = contract.AUTHOR_ID
        with self.assertRaises(contract.ContractError):
            contract.validate_receipt(
                receipt, self.expected, self.matrix,
                expected_sha256=self.expected_hash, matrix_sha256=self.matrix_hash,
                manifest_sha256=self.manifest_hash,
            )
        receipt = self._receipt()
        receipt["group_reviews"][7]["matrix_assertion_ids"].pop()
        with self.assertRaises(contract.ContractError):
            contract.validate_receipt(
                receipt, self.expected, self.matrix,
                expected_sha256=self.expected_hash, matrix_sha256=self.matrix_hash,
                manifest_sha256=self.manifest_hash,
            )

    def test_fresh_session_claim_alone_is_not_independent_review(self):
        receipt = self._receipt()
        receipt["same_model_fresh_session_only"] = True
        with self.assertRaises(contract.ContractError):
            contract.validate_receipt(
                receipt, self.expected, self.matrix,
                expected_sha256=self.expected_hash, matrix_sha256=self.matrix_hash,
                manifest_sha256=self.manifest_hash,
            )

    def test_independent_review_cli_reconstructs_sources_and_rejects_bad_receipt(self):
        with tempfile.TemporaryDirectory(prefix="timeline-review-test-") as tmp:
            receipt_path = Path(tmp) / "receipt.json"
            receipt = self._receipt()
            receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
            argv = [
                "--expected", str(EXPECTED_PATH), "--matrix", str(MATRIX_PATH),
                "--pinned-plan", str(TASK_PACKET), "--require-independent-source-audit",
                "--evidence-dir-from-workshop-lane", "--receipt", str(receipt_path),
            ]
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(review.main(argv), 0)
            receipt["source_audit_facts_sha256"] = "0" * 64
            receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(review.main(argv), 1)

    def test_mutated_cutoff_is_rejected(self):
        changed = copy.deepcopy(self.expected)
        changed["cases"][2]["expected"]["projection"]["known_at"] = "2026-10-14T16:01:00Z"
        self.expect_rejected(expected=changed)

    def test_mutated_claim_or_provenance_path_is_rejected(self):
        changed = copy.deepcopy(self.expected)
        changed["baseline"]["v2_claim"]["role_id"] = "ROLE-INVENTED"
        self.expect_rejected(expected=changed)

    def test_mutated_graph_path_and_impact_path_are_rejected(self):
        changed = copy.deepcopy(self.expected)
        changed["baseline"]["v2_claim"]["applicability_edge"]["to"] = "CONVEYOR-OTHER"
        self.expect_rejected(expected=changed)
        changed = copy.deepcopy(self.expected)
        changed["cases"][7]["expected"]["existing_incident_impact_paths_unchanged"][0][-1] = "ROLE-INVENTED"
        self.expect_rejected(expected=changed)

    def test_timestamp_insensitive_v2_output_is_rejected(self):
        changed = copy.deepcopy(self.expected)
        changed["cases"][3]["expected"]["projection"]["claims"].append(
            copy.deepcopy(changed["baseline"]["v3_claim"])
        )
        self.expect_rejected(expected=changed)

    def test_zero_matrix_assertions_are_rejected(self):
        changed = copy.deepcopy(self.matrix)
        changed["groups"][0]["pinned_assertions"] = []
        changed["groups"][0]["nonzero_assertion_count"] = 0
        with self.assertRaises(contract.ContractError):
            contract._validate_semantics(self.expected, changed)

    def test_property_only_provenance_is_rejected(self):
        changed = copy.deepcopy(self.expected)
        changed["baseline"]["v2_claim"]["source"]["evidence_link"] = {
            "from": "SOP-OPS-9-V2", "relationship": "EVIDENCED_BY_PROPERTY", "to": "SRC-SOP-CURRENT-V2"
        }
        with self.assertRaises(contract.ContractError):
            contract._cross_check_sources(changed, self.source_files)

    def test_future_record_and_considers_leak_before_1605_is_rejected(self):
        changed = copy.deepcopy(self.expected)
        changed["cases"][4]["expected"]["projection"]["decision_record"] = copy.deepcopy(
            changed["baseline"]["decision_record"]
        )
        with self.assertRaises(contract.ContractError):
            contract._validate_semantics(changed, self.matrix)

    def test_shifted_future_v3_id_or_edge_leak_is_rejected(self):
        changed = copy.deepcopy(self.expected)
        changed["cases"][7]["expected"]["at_16_05"]["decision_considers_edges"].append(
            copy.deepcopy(changed["baseline"]["considered_edges"][1])
        )
        with self.assertRaises(contract.ContractError):
            contract._validate_semantics(changed, self.matrix)

    def test_weak_unknown_or_acl_semantics_are_rejected(self):
        changed = copy.deepcopy(self.expected)
        changed["scope"]["unknown_semantics"] = "Missing evidence proves there is no decision."
        with self.assertRaises(contract.ContractError):
            contract._validate_semantics(changed, self.matrix)
        changed = copy.deepcopy(self.expected)
        changed["cases"][9]["expected"]["restricted_ids_titles_facts_counts_existence_edges_paths_or_paraphrases_exposed"] = True
        with self.assertRaises(contract.ContractError):
            contract._validate_semantics(changed, self.matrix)

    def test_record_edge_removal_cannot_reconstruct_property_facts(self):
        changed = copy.deepcopy(self.expected)
        changed["cases"][8]["expected"]["decision_record_source_edge_removal"]["property_only_provenance_accepted"] = True
        with self.assertRaises(contract.ContractError):
            contract._validate_semantics(changed, self.matrix)

    def test_record_edge_removal_preserves_claims_conflict_and_record_only_unknown(self):
        for field, value in (
            ("claims", []),
            ("conflict", None),
            ("generic_unknown", True),
        ):
            changed = copy.deepcopy(self.expected)
            changed["cases"][8]["expected"]["decision_record_source_edge_removal"]["projection"][field] = value
            with self.assertRaises(contract.ContractError):
                contract._validate_semantics(changed, self.matrix)
        changed = copy.deepcopy(self.expected)
        changed["cases"][8]["expected"]["decision_record_source_edge_removal"]["record_lookup_result"]["state"] = "resolved"
        with self.assertRaises(contract.ContractError):
            contract._cross_check_sources(changed, self.source_files)

    def test_shifted_v3_title_and_path_are_pinned_as_suppressed(self):
        changed = copy.deepcopy(self.expected)
        changed["cases"][7]["expected"]["at_16_05"]["claims"].append(
            copy.deepcopy(changed["baseline"]["v3_claim"])
        )
        with self.assertRaises(contract.ContractError):
            contract._validate_semantics(changed, self.matrix)

    def test_v3_observation_time_shift_changes_only_the_pinned_boundary(self):
        changed = copy.deepcopy(self.expected)
        changed["cases"][7]["expected"]["at_16_06"]["claims"][1]["source"]["observed_at"] = "2026-10-14T16:04:00Z"
        with self.assertRaises(contract.ContractError):
            contract._validate_semantics(changed, self.matrix)

    def test_offset_and_naive_time_require_exact_rejection_semantics(self):
        changed = copy.deepcopy(self.expected)
        changed["cases"][10]["expected"]["naive_rejected"] = False
        with self.assertRaises(contract.ContractError):
            contract._validate_semantics(changed, self.matrix)

    def test_frozen_source_hash_manifest_rejects_changed_pin(self):
        changed = copy.deepcopy(self.manifest)
        changed["oracle_sources"][3]["sha256"] = "0" * 64
        with self.assertRaises(contract.ContractError):
            contract.validate_manifest(changed, self.manifest_hash, ROOT, TASK_PACKET)
        with self.assertRaises(contract.ContractError):
            contract.validate_manifest(self.manifest, "0" * 64, ROOT, TASK_PACKET)

    def test_source_manifest_refuses_symlink_or_unpinned_path(self):
        changed = copy.deepcopy(self.manifest)
        changed["oracle_sources"].append({"id":"untrusted","path":"tests/../README.md","sha256":"0"*64,"kind":"accepted-source-oracle"})
        with self.assertRaises(contract.ContractError):
            contract.validate_manifest(changed, self.manifest_hash, ROOT, TASK_PACKET)


if __name__ == "__main__":
    unittest.main()

"""Focused mutation tests for the independent handover outcome oracle."""

from __future__ import annotations

import copy
import json
import unittest
from pathlib import Path

from scripts.verify_handover_brief_contract import (
    CASE_IDS,
    PACKET_PATH,
    ContractError,
    validate_documents,
)


ROOT = Path(__file__).resolve().parents[1]
EXPECTED_PATH = ROOT / "tests/handover-brief-expected-results.json"
MATRIX_PATH = ROOT / "tests/handover-brief-adversarial-matrix.json"
PLAN_PATH = Path(PACKET_PATH)


def load_oracles() -> tuple[dict, dict]:
    return json.loads(EXPECTED_PATH.read_text()), json.loads(MATRIX_PATH.read_text())


def case(expected: dict, case_id: str) -> dict:
    return next(row for row in expected["cases"] if row["id"] == case_id)["expected"]


class HandoverBriefContractMutationTests(unittest.TestCase):
    def validate(self, expected: dict, matrix: dict) -> dict[str, int]:
        return validate_documents(
            expected,
            matrix,
            pinned_plan_path=PLAN_PATH,
            repository_root=ROOT,
        )

    def assert_rejected(self, mutate_expected=None, mutate_matrix=None) -> None:
        expected, matrix = load_oracles()
        expected = copy.deepcopy(expected)
        matrix = copy.deepcopy(matrix)
        if mutate_expected:
            mutate_expected(expected)
        if mutate_matrix:
            mutate_matrix(matrix)
        with self.assertRaises(ContractError):
            self.validate(expected, matrix)

    def test_pinned_nine_case_oracle_and_nonzero_matrix_validate(self) -> None:
        expected, matrix = load_oracles()
        result = self.validate(expected, matrix)
        self.assertEqual(result["case_count"], 9)
        self.assertGreater(result["assertion_count"], 0)
        self.assertEqual([row["id"] for row in expected["cases"]], CASE_IDS)

    def test_rejects_changed_or_missing_inc204_path(self) -> None:
        def alter(expected: dict) -> None:
            case(expected, "inc204-observed-paths-and-provenance")["impact_paths"][0][-1] = "ROLE-UNPINNED"

        def remove(expected: dict) -> None:
            case(expected, "inc204-observed-paths-and-provenance")["impact_paths"].pop()

        self.assert_rejected(mutate_expected=alter)
        self.assert_rejected(mutate_expected=remove)

    def test_rejects_witness_edge_endpoints_claim_label_and_stale_join(self) -> None:
        def mutate_endpoint(expected: dict) -> None:
            case(expected, "typed-current-sop-witness-and-stale-exclusion")["sop_witness"]["conflict_edge"]["to"] = "SOP-OPS-9-V1"

        def mutate_label(expected: dict) -> None:
            case(expected, "typed-current-sop-witness-and-stale-exclusion")["sop_witness"]["claim_evidence"][1]["claim_label"] = "current owner"

        def join_stale(expected: dict) -> None:
            case(expected, "typed-current-sop-witness-and-stale-exclusion")["sop_witness"]["stale_version_excluded_from_c4_witness"]["is_joined_to_inc204_conflict_witness"] = True

        self.assert_rejected(mutate_expected=mutate_endpoint)
        self.assert_rejected(mutate_expected=mutate_label)
        self.assert_rejected(mutate_expected=join_stale)

    def test_rejects_edge_removal_that_changes_outage_provenance_or_resolves_conflict(self) -> None:
        def change_owned_edge(expected: dict) -> None:
            edge = case(expected, "owned-edge-removal-is-unknown-only")["edge_removals"][0]["removed_edge"]
            edge["to"] = "CONVEYOR-C3"

        def change_provenance(expected: dict) -> None:
            result = case(expected, "owned-edge-removal-is-unknown-only")["edge_removals"][1]
            result["path_evidence_unchanged"] = False

        def claim_resolved(expected: dict) -> None:
            result = case(expected, "owned-edge-removal-is-unknown-only")["edge_removals"][0]
            result["linkage_status"] = "resolved"

        self.assert_rejected(mutate_expected=change_owned_edge)
        self.assert_rejected(mutate_expected=change_provenance)
        self.assert_rejected(mutate_expected=claim_resolved)

    def test_rejects_changed_source_ids_titles_or_observation_time(self) -> None:
        def change_source(expected: dict) -> None:
            case(expected, "inc204-observed-paths-and-provenance")["path_evidence"][0]["source_ids"][0] = "SRC-OTHER"

        def change_title(expected: dict) -> None:
            case(expected, "typed-current-sop-witness-and-stale-exclusion")["sop_witness"]["claim_evidence"][0]["source_title"] = "unverified title"

        def change_time(expected: dict) -> None:
            expected["canonical_snapshot"]["impact"]["path_evidence"][1]["observed_at"][-1] = "2026-10-14T18:06:00Z"

        self.assert_rejected(mutate_expected=change_source)
        self.assert_rejected(mutate_expected=change_title)
        self.assert_rejected(mutate_expected=change_time)

    def test_rejects_acl_disclosure_or_noninvariant_snapshot(self) -> None:
        def disclose_path(expected: dict) -> None:
            case(expected, "denied-source-response-invariance")["hidden_path_disclosed"] = True

        def break_invariance(expected: dict) -> None:
            case(expected, "denied-source-response-invariance")["canonical_snapshot_unchanged"] = False

        self.assert_rejected(mutate_expected=disclose_path)
        self.assert_rejected(mutate_expected=break_invariance)

    def test_rejects_boolean_number_type_substitution(self) -> None:
        def replace_false_with_zero(expected: dict) -> None:
            case(expected, "read-only-preview-and-explicit-local-confirmation")["source_truth_resolved"] = 0

        self.assert_rejected(mutate_expected=replace_false_with_zero)

    def test_rejects_snapshot_and_confirmation_mutations(self) -> None:
        def change_snapshot(expected: dict) -> None:
            expected["canonical_snapshot"]["as_of"] = "2026-10-14T19:30:00Z"

        def change_confirmed_action_count(expected: dict) -> None:
            case(expected, "read-only-preview-and-explicit-local-confirmation")["local_synthetic_actions_after_one_confirmation"] = 2

        def allow_preview_write(expected: dict) -> None:
            case(expected, "read-only-preview-and-explicit-local-confirmation")["preview_local_action_writes"] = 1

        self.assert_rejected(mutate_expected=change_snapshot)
        self.assert_rejected(mutate_expected=change_confirmed_action_count)
        self.assert_rejected(mutate_expected=allow_preview_write)

    def test_rejects_timestamp_normalization_or_mutation_order_changes(self) -> None:
        def change_offset_result(expected: dict) -> None:
            case(expected, "timestamp-normalization-and-pre-mutation-rejection")["normalized_as_of"] = "2026-10-14T20:30:00Z"

        def allow_malformed_timestamp(expected: dict) -> None:
            case(expected, "timestamp-normalization-and-pre-mutation-rejection")["malformed_rejected"] = False

        def mutate_before_reject(expected: dict) -> None:
            case(expected, "timestamp-normalization-and-pre-mutation-rejection")["rejection_precedes_action_mutation"] = False

        self.assert_rejected(mutate_expected=change_offset_result)
        self.assert_rejected(mutate_expected=allow_malformed_timestamp)
        self.assert_rejected(mutate_expected=mutate_before_reject)

    def test_rejects_reseed_reconnect_or_source_hash_drift(self) -> None:
        def break_reconnect(expected: dict) -> None:
            case(expected, "idempotent-reseed-and-fresh-client-equality")["ordered_paths_claims_and_provenance_preserved"] = False

        def change_source_hash(expected: dict) -> None:
            expected["frozen_preexisting_oracle_files"][3]["sha256"] = "0" * 64

        self.assert_rejected(mutate_expected=break_reconnect)
        self.assert_rejected(mutate_expected=change_source_hash)

    def test_rejects_missing_case_or_zero_assertion_group(self) -> None:
        def remove_case(expected: dict) -> None:
            expected["cases"].pop()

        def remove_group_assertions(matrix: dict) -> None:
            matrix["groups"][0]["assertions"] = []

        self.assert_rejected(mutate_expected=remove_case)
        self.assert_rejected(mutate_matrix=remove_group_assertions)

    def test_rejects_fabricated_packet_reference_and_malformed_assertion_id(self) -> None:
        def fabricate_reference(expected: dict) -> None:
            expected["cases"][0]["oracle_refs"] = ["handover-brief-packet#outcome-99"]

        def malformed_assertion_id(matrix: dict) -> None:
            matrix["groups"][0]["assertions"][0]["id"] = ["not", "a", "string"]

        self.assert_rejected(mutate_expected=fabricate_reference)
        self.assert_rejected(mutate_matrix=malformed_assertion_id)

    def test_matrix_pins_complete_snapshot_and_all_source_hashes(self) -> None:
        def alter_matrix_snapshot(matrix: dict) -> None:
            group = next(group for group in matrix["groups"] if group["case_id"] == "read-only-preview-and-explicit-local-confirmation")
            assertion = next(assertion for assertion in group["assertions"] if assertion["id"] == "handover.entire-timestamped-snapshot-is-pinned")
            assertion["equals"]["sop_witness"]["conflict_status"] = "resolved"

        def remove_hash_assertion(matrix: dict) -> None:
            group = next(group for group in matrix["groups"] if group["case_id"] == "idempotent-reseed-and-fresh-client-equality")
            group["assertions"] = [a for a in group["assertions"] if a["id"] != "repeat.source-change-oracle-unchanged"]

        self.assert_rejected(mutate_matrix=alter_matrix_snapshot)
        self.assert_rejected(mutate_matrix=remove_hash_assertion)


if __name__ == "__main__":
    unittest.main()

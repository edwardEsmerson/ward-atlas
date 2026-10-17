"""Focused adversarial mutations for the candidate-independent escalation oracle."""

from __future__ import annotations

import copy
import hashlib
import json
import unittest
from pathlib import Path

from scripts.verify_escalation_contract import (
    CASE_IDS,
    EXPECTED_ARTIFACT_SHA256,
    MATRIX_ARTIFACT_SHA256,
    PACKET_PATH,
    PINNED_ORACLE_HASHES,
    ContractError,
    validate_documents,
    validate_review_receipt,
)


ROOT = Path(__file__).resolve().parents[1]
EXPECTED_PATH = ROOT / "tests/escalation-expected-results.json"
MATRIX_PATH = ROOT / "tests/escalation-adversarial-matrix.json"
PLAN_PATH = Path(PACKET_PATH)


def load_artifacts() -> tuple[dict, dict]:
    return json.loads(EXPECTED_PATH.read_text(encoding="utf-8")), json.loads(MATRIX_PATH.read_text(encoding="utf-8"))


def group(expected: dict, group_id: str) -> dict:
    return next(row for row in expected["cases"] if row["id"] == group_id)["expected"]


def matrix_group(matrix: dict, group_id: str) -> dict:
    return next(row for row in matrix["groups"] if row["group_id"] == group_id)


def canonical_sha256(value: object) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def valid_receipt(expected: dict, matrix: dict) -> dict:
    return {
        "schema_version": 1,
        "receipt_type": "wardatlas-escalation-independent-review",
        "reviewer_id": "fresh-independent-reviewer-01",
        "review_method": "Compared each fixed group to the immutable planner packet and frozen source oracles before candidate behavior.",
        "candidate_behavior_consulted": False,
        "artifact_hashes": {
            "tests/escalation-expected-results.json": EXPECTED_ARTIFACT_SHA256,
            "tests/escalation-adversarial-matrix.json": MATRIX_ARTIFACT_SHA256,
            "planner_packet": "e3c1b19a56aea779f65e4579669a1f6a175d6dac6ae755e22129122301490b0f",
        },
        "frozen_preexisting_oracle_hashes": PINNED_ORACLE_HASHES.copy(),
        "groups": [
            {
                "group_id": case["id"],
                "expected_sha256": canonical_sha256(case),
                "matrix_assertion_ids": [item["id"] for item in matrix_group(matrix, case["id"])["assertions"]],
                "result": "pass",
            }
            for case in expected["cases"]
        ],
    }


class EscalationContractMutationTests(unittest.TestCase):
    def validate(self, expected: dict, matrix: dict) -> dict[str, int]:
        return validate_documents(expected, matrix, pinned_plan_path=PLAN_PATH, repository_root=ROOT)

    def assert_rejected(self, mutate_expected=None, mutate_matrix=None) -> None:
        expected, matrix = load_artifacts()
        expected = copy.deepcopy(expected)
        matrix = copy.deepcopy(matrix)
        if mutate_expected:
            mutate_expected(expected)
        if mutate_matrix:
            mutate_matrix(matrix)
        with self.assertRaises(ContractError):
            self.validate(expected, matrix)

    def test_nine_fixed_groups_all_have_nonzero_matrix_assertions_and_frozen_hashes(self) -> None:
        expected, matrix = load_artifacts()
        result = self.validate(expected, matrix)
        self.assertEqual([row["id"] for row in expected["cases"]], list(CASE_IDS))
        self.assertEqual(result["case_count"], 9)
        self.assertEqual(result["assertion_count"], 90)
        self.assertEqual(result["frozen_oracle_and_matrix_count"], 20)
        self.assertEqual(result["packet_sha256_verified"], 1)
        for item in matrix["groups"]:
            self.assertGreater(item["nonzero_assertion_count"], 0)

    def test_rejects_changed_or_missing_incident_role_paths_and_day_provenance(self) -> None:
        def change_role(expected: dict) -> None:
            group(expected, "outage-day-coordinator-paths")["routes"][0]["route_path"][-1] = "ROLE-UNPINNED"

        def remove_path(expected: dict) -> None:
            group(expected, "outage-day-coordinator-paths")["impact_paths"].pop()

        def change_source(expected: dict) -> None:
            group(expected, "outage-day-coordinator-paths")["routes"][1]["provenance"][4]["source_id"] = "SRC-OTHER"

        def change_title(expected: dict) -> None:
            group(expected, "outage-day-coordinator-paths")["routes"][0]["provenance"][4]["title"] = "Roster"

        def change_observed_time(expected: dict) -> None:
            group(expected, "outage-day-coordinator-paths")["routes"][1]["provenance"][4]["observed_at"] = "2026-10-14T17:49:00Z"

        for mutation in (change_role, remove_path, change_source, change_title, change_observed_time):
            with self.subTest(mutation=mutation.__name__):
                self.assert_rejected(mutate_expected=mutation)

    def test_rejects_wrong_half_open_boundary_relationship_ids_or_night_source(self) -> None:
        def change_start(expected: dict) -> None:
            group(expected, "effective-shift-boundary")["night_interval"]["valid_from"] = "2026-10-14T19:01:00Z"

        def change_end_semantics(expected: dict) -> None:
            group(expected, "effective-shift-boundary")["day_interval"]["semantics"] = "closed interval"

        def change_relationship(expected: dict) -> None:
            group(expected, "effective-shift-boundary")["at_boundary"]["routes"][1]["escalation_relationship"]["id"] = "REL-OTHER"

        def change_night_source(expected: dict) -> None:
            group(expected, "effective-shift-boundary")["at_boundary"]["routes"][0]["provenance"][4]["source_id"] = "SRC-OPS-ESCALATION-DAY"

        for mutation in (change_start, change_end_semantics, change_relationship, change_night_source):
            with self.subTest(mutation=mutation.__name__):
                self.assert_rejected(mutate_expected=mutation)

    def test_rejects_restoration_route_leak_or_false_safety_conclusion(self) -> None:
        def retain_route(expected: dict) -> None:
            group(expected, "observed-restoration-removes-routes")["routes"] = [group(expected, "outage-day-coordinator-paths")["routes"][0]]

        def conclude_safe(expected: dict) -> None:
            group(expected, "observed-restoration-removes-routes")["safe_or_no_risk_conclusion"] = True

        def change_time(expected: dict) -> None:
            group(expected, "observed-restoration-removes-routes")["as_of"] = "2026-10-14T19:30:00Z"

        for mutation in (retain_route, conclude_safe, change_time):
            with self.subTest(mutation=mutation.__name__):
                self.assert_rejected(mutate_expected=mutation)

    def test_rejects_edge_or_evidence_mutation_and_non_unknown_missing_state(self) -> None:
        def change_removed_edge(expected: dict) -> None:
            group(expected, "escalation-edge-removal-unknown")["remove_only_relationship"]["id"] = "REL-ESC-STERILE-DAY"

        def infer_missing_route(expected: dict) -> None:
            group(expected, "escalation-edge-removal-unknown")["after_removal"]["missing_route"]["escalation_status"] = "routed"

        def change_source_link(expected: dict) -> None:
            group(expected, "missing-escalation-source-unknown")["removed_evidence_link"]["source_id"] = "SRC-OPS-ESCALATION-NIGHT"

        def complete_without_evidence(expected: dict) -> None:
            group(expected, "missing-escalation-source-unknown")["after_removal"]["missing_route"]["escalation_status"] = "complete"

        def allow_claim_fallback(expected: dict) -> None:
            group(expected, "missing-escalation-source-unknown")["after_removal"]["sop_claims_role_cannot_reconstruct_route"] = False

        for mutation in (change_removed_edge, infer_missing_route, change_source_link, complete_without_evidence, allow_claim_fallback):
            with self.subTest(mutation=mutation.__name__):
                self.assert_rejected(mutate_expected=mutation)

    def test_rejects_acl_leak_or_noninvariant_full_response(self) -> None:
        def change_visible_response(expected: dict) -> None:
            group(expected, "denied-source-invariance")["visible_response_after_ref"] = "#/cases/5/expected/restricted_probe"

        def reveal_role(expected: dict) -> None:
            group(expected, "denied-source-invariance")["restricted_identifier_title_fact_count_existence_relationship_path_or_paraphrase_disclosed"] = True

        def change_acl(expected: dict) -> None:
            group(expected, "denied-source-invariance")["viewer_scope"] = "restricted"

        for mutation in (change_visible_response, reveal_role, change_acl):
            with self.subTest(mutation=mutation.__name__):
                self.assert_rejected(mutate_expected=mutation)

    def test_rejects_claims_role_as_assignment_offset_or_malformed_time_drift(self) -> None:
        def assign_claim(expected: dict) -> None:
            group(expected, "sop-claim-separation-and-time-validation")["sop_claims_are_escalation_assignments"] = True

        def change_claim_source_time(expected: dict) -> None:
            group(expected, "sop-claim-separation-and-time-validation")["sop_claims"][1]["observed_at"] = "2026-10-14T16:05:00Z"

        def change_offset(expected: dict) -> None:
            group(expected, "sop-claim-separation-and-time-validation")["offset_normalized_as_of"] = "2026-10-14T20:30:00Z"

        def allow_malformed_time(expected: dict) -> None:
            group(expected, "sop-claim-separation-and-time-validation")["malformed_timestamp_rejected"] = False

        def mutate_before_reject(expected: dict) -> None:
            group(expected, "sop-claim-separation-and-time-validation")["rejection_precedes_graph_or_action_mutation"] = False

        for mutation in (assign_claim, change_claim_source_time, change_offset, allow_malformed_time, mutate_before_reject):
            with self.subTest(mutation=mutation.__name__):
                self.assert_rejected(mutate_expected=mutation)

    def test_rejects_removed_group_zero_assertions_or_changed_matrix_binding(self) -> None:
        def remove_case(expected: dict) -> None:
            expected["cases"].pop()

        def zero_assertions(matrix: dict) -> None:
            matrix["groups"][0]["assertions"] = []

        def remove_packet_assertion(matrix: dict) -> None:
            matrix["groups"][2]["assertions"].pop()

        def point_at_unpinned_value(matrix: dict) -> None:
            matrix["groups"][3]["assertions"][1]["equals"] = "2026-10-14T19:30:00Z"

        for mutate_expected, mutate_matrix in (
            (remove_case, None), (None, zero_assertions), (None, remove_packet_assertion), (None, point_at_unpinned_value),
        ):
            with self.subTest(mutate_expected=mutate_expected, mutate_matrix=mutate_matrix):
                self.assert_rejected(mutate_expected=mutate_expected, mutate_matrix=mutate_matrix)

    def test_rejects_changed_or_missing_frozen_oracle_hash(self) -> None:
        def change_hash(expected: dict) -> None:
            expected["frozen_preexisting_contract_files"][0]["sha256"] = "0" * 64

        def remove_hash(expected: dict) -> None:
            expected["frozen_preexisting_contract_files"].pop()

        self.assert_rejected(mutate_expected=change_hash)
        self.assert_rejected(mutate_expected=remove_hash)

    def test_future_review_receipt_is_bound_to_all_groups_assertions_and_oracle_hashes(self) -> None:
        expected, matrix = load_artifacts()
        receipt = valid_receipt(expected, matrix)
        result = validate_review_receipt(receipt, expected, matrix)
        self.assertEqual(result, {"reviewed_group_count": 9, "receipt_oracle_hash_count": 20})

        altered_expected = copy.deepcopy(expected)
        group(altered_expected, "outage-day-coordinator-paths")["routes"][0]["coordinator_role_id"] = "ROLE-UNPINNED"
        with self.assertRaises(ContractError):
            validate_review_receipt(receipt, altered_expected, matrix)

        for mutate in (
            lambda value: value["groups"].pop(),
            lambda value: value["groups"][2].__setitem__("expected_sha256", "0" * 64),
            lambda value: value["groups"][4].__setitem__("matrix_assertion_ids", []),
            lambda value: value["frozen_preexisting_oracle_hashes"].pop("tests/expected-results.json"),
            lambda value: value["artifact_hashes"].__setitem__("planner_packet", "0" * 64),
            lambda value: value.__setitem__("candidate_behavior_consulted", True),
        ):
            altered = copy.deepcopy(receipt)
            mutate(altered)
            with self.subTest(receipt_mutation=mutate):
                with self.assertRaises(ContractError):
                    validate_review_receipt(altered, expected, matrix)


if __name__ == "__main__":
    unittest.main()

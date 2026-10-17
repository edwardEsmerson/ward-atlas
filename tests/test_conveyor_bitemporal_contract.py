"""Mutation tests for the candidate-independent INC-204 time contract."""

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

import verify_conveyor_bitemporal_contract as contract  # noqa: E402


class ConveyorBitemporalContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.expected_bytes = (ROOT / "tests/conveyor-bitemporal-expected-results.json").read_bytes()
        cls.matrix_bytes = (ROOT / "tests/conveyor-bitemporal-adversarial-matrix.json").read_bytes()
        cls.expected = json.loads(cls.expected_bytes)
        cls.matrix = json.loads(cls.matrix_bytes)
        cls.expected_sha = hashlib.sha256(cls.expected_bytes).hexdigest()
        cls.matrix_sha = hashlib.sha256(cls.matrix_bytes).hexdigest()
        cls.plan = Path(
            "/workspace/worktrees/planner-4b8f1393a48d40da83d21180edd4667f/"
            "plans/planner-4b8f1393a48d40da83d21180edd4667f.md"
        )

    def validate(self, expected=None, matrix=None, expected_sha=None, matrix_sha=None, root=ROOT, plan=None):
        return contract.validate_artifacts(
            self.expected if expected is None else expected,
            self.matrix if matrix is None else matrix,
            self.expected_sha if expected_sha is None else expected_sha,
            self.matrix_sha if matrix_sha is None else matrix_sha,
            root=root,
            pinned_plan=self.plan if plan is None else plan,
            require_twelve_groups=True,
        )

    def test_exact_twelve_groups_and_nonzero_assertion_mappings(self):
        assertions, digests = self.validate()
        self.assertEqual(assertions, 84)
        self.assertEqual(tuple(row["id"] for row in self.expected["cases"]), contract.REQUIRED_CASES)
        self.assertEqual(tuple(row["id"] for row in self.matrix["cases"]), contract.REQUIRED_CASES)
        self.assertEqual(len(digests), 33)  # packet, 24 frozen files, and four pinned additive pairs
        self.assertEqual(len(contract.PINNED_EXISTING), 24)
        self.assertEqual(
            set(contract.verify_existing_oracles(ROOT, self.expected)),
            set(contract.PINNED_EXISTING) | set(contract._pinned_additive_files()),
        )
        self.assertTrue(all(row["nonzero_assertion_count"] > 0 for row in self.matrix["cases"]))
        self.assertTrue(all(len(row["pinned_assertions"]) == row["nonzero_assertion_count"] for row in self.matrix["cases"]))
        self.assertEqual(
            [source["file"] for source in self.expected["pinned_sources"]][1:],
            [path for _, path, _ in contract.SOURCE_ORACLES],
        )

    def test_changed_or_missing_valid_and_knowledge_times_fail(self):
        for index, field, replacement in (
            (2, "conveyor_known_at", "2026-10-14T19:15:00Z"),
            (3, "conveyor_known_at", None),
            (4, "conveyor_known_at", "2026-10-14T18:05:00Z"),
            (5, "conveyor_known_at", "2026-10-14T18:02:00Z"),
        ):
            with self.subTest(case=self.expected["cases"][index]["id"], field=field):
                changed = copy.deepcopy(self.expected)
                if replacement is None:
                    del changed["cases"][index]["expected"][field]
                else:
                    changed["cases"][index]["expected"][field] = replacement
                with self.assertRaises(contract.ContractFailure):
                    contract.validate_fixed_outcomes(changed, ROOT)

        legacy = copy.deepcopy(self.expected)
        legacy["cases"][1]["expected"]["known_at"] = "2026-10-14T19:14:00Z"
        with self.assertRaisesRegex(contract.ContractFailure, "legacy omission/current-knowledge"):
            contract.validate_fixed_outcomes(legacy, ROOT)

    def test_event_source_status_path_and_provenance_mutations_fail(self):
        mutations = (
            (2, ("selected_event", "event_id"), "EVT-C4-RESTORED-01"),
            (2, ("selected_event", "source_id"), "SRC-ASSET-C4-RESTORE"),
            (2, ("selected_event", "event_at"), "2026-10-14T19:15:01Z"),
            (2, ("state_status",), "restored"),
            (3, ("selected_event", "source_id"), "SRC-ASSET-C4-OUTAGE"),
            (4, ("path_evidence", 0, "observed_at", 3), "2026-10-14T18:05:00Z"),
            (4, ("impact_paths", 0, 4), "ROLE-MATERIALS-DUTY"),
        )
        for index, path, value in mutations:
            with self.subTest(case=self.expected["cases"][index]["id"], path=path):
                changed = copy.deepcopy(self.expected)
                target = changed["cases"][index]["expected"]
                for key in path[:-1]:
                    target = target[key]
                target[path[-1]] = value
                with self.assertRaises(contract.ContractFailure):
                    contract.validate_fixed_outcomes(changed, ROOT)

    def test_unknown_no_leak_and_no_safety_claim_are_fixed(self):
        for field, value in (
            ("state_status", "available"),
            ("selected_event", {"event_id": "EVT-C4-UNAVAILABLE-01"}),
            ("selected_event_source", {"source_id": "SRC-ASSET-C4-OUTAGE"}),
            ("impact_paths", [["INC-204", "ROLE-MATERIALS-DUTY"]]),
            ("availability_conclusion", True),
            ("no_risk_conclusion", True),
            ("unseen_event_or_source_hint_emitted", True),
        ):
            with self.subTest(field=field):
                changed = copy.deepcopy(self.expected)
                changed["cases"][5]["expected"][field] = value
                with self.assertRaises(contract.ContractFailure):
                    contract.validate_fixed_outcomes(changed, ROOT)

    def test_path_cutoff_rejects_later_role_counts_completeness_and_changed_times(self):
        mutations = (
            ("impact_paths", self.expected["cases"][4]["expected"]["impact_paths"] + [self.expected["fixture"]["canonical_paths"][1]]),
            ("path_ownership_evidence_uses_relationship_observed_at", False),
            ("excluded_later_ownership", {"relationship": "OWNED_BY", "role_id": "ROLE-MATERIALS-DUTY", "observed_at": "2026-10-14T18:04:00Z"}),
            ("later_role_path_emitted", True),
            ("later_ownership_evidence_emitted", True),
            ("missing_path_count_emitted", True),
            ("completeness_claim_emitted", True),
            ("returned_path_provenance_times", ["2026-10-14T18:00:00Z", "2026-10-14T18:02:00Z", "2026-10-14T18:03:00Z", "2026-10-14T18:05:00Z"]),
        )
        for field, value in mutations:
            with self.subTest(field=field):
                changed = copy.deepcopy(self.expected)
                changed["cases"][4]["expected"][field] = value
                with self.assertRaises(contract.ContractFailure):
                    contract.validate_fixed_outcomes(changed, ROOT)

    def test_only_the_pinned_graph_edges_control_counterfactuals(self):
        for index, field, value in (
            (6, "remove_only_edge", {"from": "INC-204", "relationship": "AFFECTS", "to": "CONVEYOR-C4", "ownership": "test-owned edge only"}),
            (6, "after_removal", {"state_status": "observed-impact", "impact_paths": self.expected["fixture"]["canonical_paths"]}),
            (7, "remove_only_edge", {"from": "EVT-C4-RESTORED-01", "relationship": "HAS_STATE_EVENT", "to": "SRC-ASSET-C4-RESTORE", "ownership": "test-owned edge only"}),
            (7, "after_removal", {"state_status": "restored", "active_paths": []}),
        ):
            with self.subTest(group=index, field=field):
                changed = copy.deepcopy(self.expected)
                changed["cases"][index]["expected"][field] = value
                with self.assertRaises(contract.ContractFailure):
                    contract.validate_fixed_outcomes(changed, ROOT)

    def test_branch_label_and_mainline_isolation_are_fixed(self):
        for field, value in (
            ("label", "observed"),
            ("event_id", "EVT-C4-RESTORED-01"),
            ("event_is_observed", True),
        ):
            changed = copy.deepcopy(self.expected)
            changed["cases"][8]["expected"]["branch"][field] = value
            with self.subTest(branch_field=field), self.assertRaises(contract.ContractFailure):
                contract.validate_fixed_outcomes(changed, ROOT)

        changed = copy.deepcopy(self.expected)
        changed["cases"][8]["expected"]["hypothetical_event_in_mainline"] = True
        with self.assertRaises(contract.ContractFailure):
            contract.validate_fixed_outcomes(changed, ROOT)

    def test_acl_invariance_and_hidden_source_non_disclosure_are_fixed(self):
        for field, value in (
            ("viewer_scope", "restricted"),
            ("full_selected_event_response_and_accessible_event_panel_invariant_before_after", False),
            ("hidden_id_title_fact_count_existence_relationship_path_or_paraphrase_disclosed", True),
            ("axis_pairs", []),
        ):
            changed = copy.deepcopy(self.expected)
            changed["cases"][9]["expected"][field] = value
            with self.subTest(field=field), self.assertRaises(contract.ContractFailure):
                contract.validate_fixed_outcomes(changed, ROOT)

    def test_missing_groups_zero_assertions_and_bad_mappings_fail(self):
        short = copy.deepcopy(self.matrix)
        short["cases"].pop()
        with self.assertRaisesRegex(contract.ContractFailure, "exactly twelve groups"):
            contract.validate_structure(self.expected, short, True)

        zero = copy.deepcopy(self.matrix)
        zero["cases"][0]["pinned_assertions"] = []
        zero["cases"][0]["nonzero_assertion_count"] = 0
        with self.assertRaisesRegex(contract.ContractFailure, "zero mapped assertions"):
            contract.validate_structure(self.expected, zero, True)

        wrong_pointer = copy.deepcopy(self.matrix)
        wrong_pointer["cases"][4]["pinned_assertions"][0]["expected_pointer"] = "/candidate/output"
        with self.assertRaisesRegex(contract.ContractFailure, "does not map every required"):
            contract.validate_structure(self.expected, wrong_pointer, True)

        wrong_count = copy.deepcopy(self.matrix)
        wrong_count["cases"][3]["nonzero_assertion_count"] = 0
        with self.assertRaisesRegex(contract.ContractFailure, "assertion count must be nonzero"):
            contract.validate_structure(self.expected, wrong_count, True)

    def test_matrix_must_map_the_18_05_ownership_and_external_write_boundaries(self):
        for case_index, assertion_id in ((4, "per-path-evidence-cutoff.later-ownership-evidence-emitted"), (11, "repeat-reconnect-read-only-and-cleanup.external-writes")):
            with self.subTest(assertion=assertion_id):
                changed = copy.deepcopy(self.matrix)
                row = changed["cases"][case_index]
                row["pinned_assertions"] = [item for item in row["pinned_assertions"] if item["id"] != assertion_id]
                row["nonzero_assertion_count"] = len(row["pinned_assertions"])
                with self.assertRaisesRegex(contract.ContractFailure, "does not map every required fixed outcome field"):
                    contract.validate_structure(self.expected, changed, True)

    def test_frozen_hash_manifest_rejects_byte_drift_missing_or_new_oracle(self):
        self.assertEqual(len(contract.verify_existing_oracles(ROOT, self.expected)), 32)
        with tempfile.TemporaryDirectory() as directory:
            temp_root = Path(directory)
            pinned_files = {**contract.PINNED_EXISTING, **contract._pinned_additive_files()}
            for relative in pinned_files:
                target = temp_root / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(ROOT / relative, target)
            altered = temp_root / "tests/shift-board-expected-results.json"
            altered.write_bytes(altered.read_bytes() + b" ")
            with self.assertRaisesRegex(contract.ContractFailure, "differs from its frozen byte hash"):
                contract.verify_existing_oracles(temp_root, self.expected)

            altered.write_bytes((ROOT / "tests/shift-board-expected-results.json").read_bytes())
            extra = temp_root / "tests/rogue-expected-results.json"
            extra.write_text("{}\n", encoding="utf-8")
            with self.assertRaisesRegex(contract.ContractFailure, "file set differs"):
                contract.verify_existing_oracles(temp_root, self.expected)

            extra.unlink()
            nested = temp_root / "tests/unlisted/rogue-adversarial-matrix.json"
            nested.parent.mkdir()
            nested.write_text("{}\n", encoding="utf-8")
            with self.assertRaisesRegex(contract.ContractFailure, "file set differs"):
                contract.verify_existing_oracles(temp_root, self.expected)

            nested.unlink()
            nested.parent.rmdir()

            outside = temp_root / "outside-tests"
            outside.mkdir()
            (outside / "rogue-expected-results.json").write_text("{}\n", encoding="utf-8")
            linked_directory = temp_root / "tests/linked-oracles"
            linked_directory.symlink_to(outside, target_is_directory=True)
            with self.assertRaisesRegex(contract.ContractFailure, "rejects symlink directory"):
                contract.verify_existing_oracles(temp_root, self.expected)
            linked_directory.unlink()

            for relative in contract._pinned_additive_files():
                artifact = temp_root / relative
                original_bytes = (ROOT / relative).read_bytes()
                artifact.write_bytes(original_bytes + b" ")
                with self.subTest(changed_additive=relative), self.assertRaisesRegex(
                    contract.ContractFailure, "additive oracle/matrix .* pinned byte hash"
                ):
                    contract.verify_existing_oracles(temp_root, self.expected)
                artifact.write_bytes(original_bytes)

            for relative in contract._pinned_additive_files():
                artifact = temp_root / relative
                artifact.unlink()
                with self.subTest(missing_additive=relative), self.assertRaisesRegex(
                    contract.ContractFailure, "file set differs"
                ):
                    contract.verify_existing_oracles(temp_root, self.expected)
                shutil.copyfile(ROOT / relative, artifact)

        drifted_manifest = copy.deepcopy(self.expected)
        drifted_manifest["frozen_preexisting_oracle_hashes"][0]["sha256"] = "0" * 64
        with self.assertRaisesRegex(contract.ContractFailure, "manifest differs"):
            contract.verify_existing_oracles(ROOT, drifted_manifest)

    def test_recomputed_digest_cannot_rewrite_the_fixed_expected_artifacts(self):
        changed_expected = copy.deepcopy(self.expected)
        changed_expected["cases"][3]["expected"]["state_status"] = "observed-impact"
        expected_digest = hashlib.sha256(json.dumps(changed_expected, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()
        with self.assertRaisesRegex(contract.ContractFailure, "expected-results bytes differ"):
            self.validate(expected=changed_expected, expected_sha=expected_digest)

        changed_matrix = copy.deepcopy(self.matrix)
        changed_matrix["cases"][0]["nonzero_assertion_count"] += 1
        matrix_digest = hashlib.sha256(json.dumps(changed_matrix, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()
        with self.assertRaisesRegex(contract.ContractFailure, "assertion count differs"):
            self.validate(matrix=changed_matrix, matrix_sha=matrix_digest)

    def test_packet_hash_and_numbered_outcome_binding_fail_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            altered = Path(directory) / contract.PACKET_FILENAME
            altered.write_text("not the pinned packet\n", encoding="utf-8")
            with self.assertRaisesRegex(contract.ContractFailure, "independently pinned INC-204 packet"):
                contract.verify_pinned_packet(altered)

        wrong_line = copy.deepcopy(self.matrix)
        wrong_line["cases"][0]["planner_refs"] = [f"{contract.PACKET_FILENAME}:16"]
        with self.assertRaisesRegex(contract.ContractFailure, "matrix packet line changed"):
            contract.validate_structure(self.expected, wrong_line, True)


if __name__ == "__main__":
    unittest.main()

"""Mutation/unit tests for the frozen cross-case handover oracle."""

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

import verify_cross_case_handover_contract as contract  # noqa: E402


def _json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


class CrossCaseHandoverContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.expected_path = ROOT / "tests/cross-case-handover-expected-results.json"
        cls.matrix_path = ROOT / "tests/cross-case-handover-adversarial-matrix.json"
        cls.manifest_path = ROOT / "tests/cross-case-handover-frozen-hashes.json"
        cls.expected_bytes = cls.expected_path.read_bytes()
        cls.matrix_bytes = cls.matrix_path.read_bytes()
        cls.manifest_bytes = cls.manifest_path.read_bytes()
        cls.expected = json.loads(cls.expected_bytes)
        cls.matrix = json.loads(cls.matrix_bytes)
        cls.manifest = json.loads(cls.manifest_bytes)
        cls.expected_sha = hashlib.sha256(cls.expected_bytes).hexdigest()
        cls.matrix_sha = hashlib.sha256(cls.matrix_bytes).hexdigest()
        cls.manifest_sha = hashlib.sha256(cls.manifest_bytes).hexdigest()
        candidates = list(Path("/workspace/worktrees").glob(f"*/plans/{contract.PACKET_FILENAME}"))
        cls.plan = next((p for p in candidates if contract.verify_pinned_packet(p)), None)
        if cls.plan is None:
            raise RuntimeError("the pinned planner packet is not available in this workshop")

    def test_exact_nine_groups_and_every_nonzero_mapping(self):
        total = contract.validate_structure(self.expected, self.matrix, require_nine_groups=True)
        self.assertEqual(total, 66)
        self.assertEqual([row["id"] for row in self.expected["cases"]], list(contract.REQUIRED_CASES))
        self.assertEqual([row["id"] for row in self.matrix["cases"]], list(contract.REQUIRED_CASES))
        self.assertTrue(all(row["nonzero_assertion_count"] > 0 for row in self.matrix["cases"]))
        self.assertEqual(total, sum(row["nonzero_assertion_count"] for row in self.matrix["cases"]))
        self.assertEqual(len({item["id"] for row in self.matrix["cases"] for item in row["pinned_assertions"]}), total)

    def test_every_exact_outcome_mutation_is_rejected(self):
        mutations = []

        changed_known_time = copy.deepcopy(self.expected)
        changed_known_time["cases"][1]["expected"]["request"]["known_at"] = "2026-10-14T19:15:00Z"
        mutations.append(("known time", changed_known_time))

        changed_valid_time = copy.deepcopy(self.expected)
        changed_valid_time["cases"][4]["expected"]["request"]["dock_effective_at"] = "2026-10-14T20:29:00Z"
        mutations.append(("effective time", changed_valid_time))

        missing_time = copy.deepcopy(self.expected)
        del missing_time["cases"][3]["expected"]["request"]["known_at"]
        mutations.append(("missing time", missing_time))

        changed_path = copy.deepcopy(self.expected)
        changed_path["cases"][1]["expected"]["inc204"]["impact_paths"][0][-1] = "ROLE-WRONG"
        mutations.append(("path", changed_path))

        missing_path = copy.deepcopy(self.expected)
        del missing_path["cases"][2]["expected"]["inc204"]["historical_replay"]["paths"]
        mutations.append(("missing historical path", missing_path))

        changed_source_id = copy.deepcopy(self.expected)
        changed_source_id["cases"][1]["expected"]["inc204"]["event"]["source"]["source_id"] = "SRC-FAKE"
        mutations.append(("source ID", changed_source_id))

        changed_source_title = copy.deepcopy(self.expected)
        changed_source_title["cases"][1]["expected"]["inc204"]["event"]["source"]["title"] = "wrong title"
        mutations.append(("source title", changed_source_title))

        changed_source_time = copy.deepcopy(self.expected)
        changed_source_time["cases"][1]["expected"]["inc204"]["path_evidence"][1]["observed_at"][3] = "2026-10-14T18:06:00Z"
        mutations.append(("source observation timestamp", changed_source_time))

        changed_status = copy.deepcopy(self.expected)
        changed_status["cases"][2]["expected"]["inc204"]["sop_conflict_status"] = "resolved"
        mutations.append(("SOP status", changed_status))

        changed_candidate = copy.deepcopy(self.expected)
        changed_candidate["cases"][4]["expected"]["inc207"]["review_candidates"].append("HND-LEXICAL-DECOY")
        mutations.append(("review candidate", changed_candidate))

        weakened_no_leak = copy.deepcopy(self.expected)
        weakened_no_leak["cases"][7]["expected"]["full_response_invariance"]["hidden_identifier_title_fact_source_time_count_existence_relationship_path_or_dependent_paraphrase_disclosed"] = True
        mutations.append(("denied-source leak", weakened_no_leak))

        weakened_no_causality = copy.deepcopy(self.expected)
        weakened_no_causality["cases"][1]["expected"]["cross_case"]["dock_roster_revision_caused_conveyor_outage"] = True
        mutations.append(("causal join", weakened_no_causality))

        for label, changed in mutations:
            with self.subTest(mutation=label), self.assertRaises(contract.ContractFailure):
                contract.validate_fixed_outcomes(changed)

    def test_missing_group_zero_mapping_and_wrong_pointer_are_rejected(self):
        missing = copy.deepcopy(self.matrix)
        missing["cases"].pop()
        with self.assertRaisesRegex(contract.ContractFailure, "exactly nine groups"):
            contract.validate_structure(self.expected, missing, True)

        zero = copy.deepcopy(self.matrix)
        zero["cases"][0]["pinned_assertions"] = []
        zero["cases"][0]["nonzero_assertion_count"] = 0
        with self.assertRaisesRegex(contract.ContractFailure, "has no pinned assertions"):
            contract.validate_structure(self.expected, zero, True)

        missing_mapping = copy.deepcopy(self.matrix)
        missing_mapping["cases"][5]["pinned_assertions"].pop()
        missing_mapping["cases"][5]["nonzero_assertion_count"] -= 1
        with self.assertRaisesRegex(contract.ContractFailure, "exactly 66 mapped assertions"):
            contract.validate_structure(self.expected, missing_mapping, True)

        bad_pointer = copy.deepcopy(self.matrix)
        bad_pointer["cases"][3]["pinned_assertions"][0]["expected_pointer"] = "/candidate/output"
        with self.assertRaisesRegex(contract.ContractFailure, "maps outside its own fixed outcome"):
            contract.validate_structure(self.expected, bad_pointer, True)

        non_resolving_pointer = copy.deepcopy(self.matrix)
        non_resolving_pointer["cases"][1]["pinned_assertions"][0]["expected_pointer"] = "/cases/1/expected/no_such_value"
        with self.assertRaisesRegex(contract.ContractFailure, "matrix pointer does not resolve"):
            contract.validate_structure(self.expected, non_resolving_pointer, True)

    def test_recomputed_artifact_digests_cannot_rewrite_the_frozen_oracle(self):
        altered_expected = copy.deepcopy(self.expected)
        altered_expected["cases"][4]["expected"]["inc207"]["role_id"] = "ROLE-WRONG"
        altered_expected_digest = contract.canonical_sha(altered_expected)
        with self.assertRaisesRegex(contract.ContractFailure, "fixed expected values changed"):
            contract.validate_fixed_outcomes(altered_expected)
        with self.assertRaisesRegex(contract.ContractFailure, "expected-results bytes differ"):
            contract.verify_artifact_digests(altered_expected_digest, self.matrix_sha, self.manifest_sha)

        altered_matrix = copy.deepcopy(self.matrix)
        altered_matrix["cases"][0]["pinned_assertions"][0]["id"] = "invented"
        with self.assertRaisesRegex(contract.ContractFailure, "adversarial-matrix bytes differ"):
            contract.verify_artifact_digests(self.expected_sha, contract.canonical_sha(altered_matrix), self.manifest_sha)

        altered_manifest = copy.deepcopy(self.manifest)
        altered_manifest["files"][0]["sha256"] = "0" * 64
        with self.assertRaisesRegex(contract.ContractFailure, "hash manifest bytes differ"):
            contract.verify_artifact_digests(self.expected_sha, self.matrix_sha, contract.canonical_sha(altered_manifest))

    def test_all_frozen_baseline_hashes_are_present_and_verified(self):
        observed = contract.verify_frozen_hashes(ROOT, self.manifest, self.expected)
        self.assertEqual(len(observed), 30)
        self.assertEqual(set(observed), {row["file"] for row in self.manifest["files"]})

        with tempfile.TemporaryDirectory() as directory:
            temp_root = Path(directory)
            for row in self.manifest["files"]:
                target = temp_root / row["file"]
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(ROOT / row["file"], target)
            changed = temp_root / self.manifest["files"][9]["file"]
            changed.write_bytes(changed.read_bytes() + b" ")
            with self.assertRaisesRegex(contract.ContractFailure, "frozen-baseline hash drift"):
                contract.verify_frozen_hashes(temp_root, self.manifest, self.expected)

        altered = copy.deepcopy(self.manifest)
        altered["files"].pop()
        with self.assertRaisesRegex(contract.ContractFailure, "exactly 30"):
            contract.verify_frozen_hashes(ROOT, altered, self.expected)

    def test_packet_and_accepted_source_oracle_digests_fail_closed(self):
        self.assertEqual(contract.verify_pinned_packet(self.plan), contract.PACKET_SHA256)
        with tempfile.TemporaryDirectory() as directory:
            altered = Path(directory) / contract.PACKET_FILENAME
            altered.write_text("altered decision packet\n", encoding="utf-8")
            with self.assertRaisesRegex(contract.ContractFailure, "packet bytes differ"):
                contract.verify_pinned_packet(altered)

        with tempfile.TemporaryDirectory() as directory:
            temp_root = Path(directory)
            (temp_root / "tests").mkdir()
            (temp_root / "tests/conveyor-bitemporal-expected-results.json").write_text("{}", encoding="utf-8")
            with self.assertRaisesRegex(contract.ContractFailure, "unchanged accepted oracle"):
                contract.verify_pinned_sources(temp_root, self.expected, self.plan)

    def test_full_contract_checks_pinned_sources_and_all_nine_mappings(self):
        count, hashes = contract.validate_artifacts(
            self.expected,
            self.matrix,
            self.manifest,
            self.expected_sha,
            self.matrix_sha,
            self.manifest_sha,
            root=ROOT,
            pinned_plan=self.plan,
            require_nine_groups=True,
            check_frozen_oracle_hashes=True,
        )
        self.assertEqual(count, 66)
        self.assertEqual(len(hashes), 31)  # packet + all thirty pre-task oracle/matrix files


if __name__ == "__main__":
    unittest.main()

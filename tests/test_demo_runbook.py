"""Focused source/oracle-bound contract tests for the private demo runbook."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import verify_demo_runbook as runbook  # noqa: E402


class DemoRunbookTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.text = runbook.RUNBOOK.read_text(encoding="utf-8")

    def test_story_matches_all_exact_oracle_literals_and_hashes(self) -> None:
        pins = runbook.verify_oracle_bytes()
        self.assertEqual(pins, runbook.ORACLE_HASHES)
        runbook.validate_runbook_text(self.text)
        literals = runbook.oracle_story_literals()
        self.assertIn(
            "INC-204 → CONVEYOR-C4 → SVC-MATERIAL-TRANSFER → DEPT-CENTRAL-STERILE → ROLE-STERILE-OPS-COORD",
            literals,
        )
        self.assertIn(
            "INC-204 → CONVEYOR-C4 → SVC-MATERIAL-TRANSFER → DEPT-MATERIALS-LOGISTICS → ROLE-MATERIALS-DUTY",
            literals,
        )
        self.assertIn("SRC-OPS-ROSTER", literals)
        self.assertIn("2026-10-14T18:04:00Z", literals)
        self.assertIn("2026-10-14T18:05:00Z", literals)
        self.assertIn("SRC-SOP-CURRENT-V2", literals)
        self.assertIn("SRC-SOP-CURRENT-V3", literals)
        self.assertIn("2026-10-14T16:00:00Z", literals)
        self.assertIn("2026-10-14T16:04:00Z", literals)
        self.assertIn("EVT-C4-RESTORED-01", literals)
        self.assertIn("2026-10-14T19:15:00Z", literals)
        self.assertIn("EVT-BR-RESTORE-01", literals)
        self.assertIn("2026-10-14T18:45:00Z", literals)
        for clause in runbook.oracle_association_clauses():
            self.assertIn(clause.casefold(), self.text.casefold())

    def test_all_four_unchanged_contract_mappings_are_nonzero(self) -> None:
        mappings = runbook.inspect_nonzero_mappings()
        self.assertEqual(set(mappings), set(runbook.EXPECTED_CASE_IDS))
        self.assertEqual(len(mappings["tests/expected-results.json"]), 8)
        self.assertEqual(len(mappings["tests/shift-board-expected-results.json"]), 8)
        self.assertEqual(len(mappings["tests/sop-impact-expected-results.json"]), 10)
        self.assertEqual(len(mappings["tests/handover-brief-expected-results.json"]), 9)
        self.assertTrue(all(count > 0 for rows in mappings.values() for count in rows.values()))
        self.assertEqual(sum(mappings["tests/expected-results.json"].values()), 27)
        self.assertEqual(sum(mappings["tests/shift-board-expected-results.json"].values()), 55)
        self.assertEqual(sum(mappings["tests/sop-impact-expected-results.json"].values()), 60)
        self.assertEqual(sum(mappings["tests/handover-brief-expected-results.json"].values()), 132)

    def test_story_is_bound_to_source_fact_path_branch_and_unknown_vocabulary(self) -> None:
        for phrase in (
            "Observed source facts",
            "graph-derived paths",
            "hypothetical branch",
            "missing/unknown evidence",
            "empty/unknown",
            "not an observed restoration",
            "not “resolved” or “no conflict",
            "not owners",
        ):
            self.assertIn(phrase.casefold(), self.text.casefold())
        self.assertNotIn("no active paths proves no risk", self.text.casefold())
        self.assertNotIn("hypothetical-restored is observed", self.text.casefold())

    def test_allocated_ports_two_foreground_processes_and_owned_teardown_are_mandatory(self) -> None:
        self.assertIn("two foreground processes", self.text)
        self.assertIn("not a one-command launcher", self.text)
        self.assertIn('APP_PORT="$((WORKSHOP_PORT_BASE + 121))"', self.text)
        self.assertIn('DB_PORT="$((WORKSHOP_PORT_BASE + 120))"', self.text)
        self.assertIn('test "$((APP_PORT - WORKSHOP_PORT_BASE))" -lt "$WORKSHOP_PORT_SPAN"', self.text)
        self.assertIn("--bind 127.0.0.1", self.text)
        self.assertIn('--daemonize no --loadmodule "$MODULE"', self.text)
        self.assertIn('rm -rf -- "$DB_DATA_DIR" "$STATE_DIR"', self.text)
        self.assertIn('rm -f -- "$RUN_ENV"', self.text)

    def test_rejects_fixed_default_ports_and_non_loopback_service_binding(self) -> None:
        bad_fixed = self.text.replace(
            'APP_PORT="$((WORKSHOP_PORT_BASE + 121))"', 'APP_PORT="18731"'
        )
        with self.assertRaises(runbook.RunbookError):
            runbook.validate_runbook_text(bad_fixed)
        bad_bind = self.text.replace("--bind 127.0.0.1", "--bind 0.0.0.0")
        with self.assertRaises(runbook.RunbookError):
            runbook.validate_runbook_text(bad_bind)
        fixed_database_command = self.text.replace('--port "$DB_PORT"', '--port 9999')
        with self.assertRaises(runbook.RunbookError):
            runbook.validate_runbook_text(fixed_database_command)
        fixed_app_command = self.text.replace('--app-port "$APP_PORT"', '--app-port 9999')
        with self.assertRaises(runbook.RunbookError):
            runbook.validate_runbook_text(fixed_app_command)

    def test_rejects_unbounded_port_or_nonunique_graph_changes(self) -> None:
        no_range_check = self.text.replace(
            'test "$((DB_PORT - WORKSHOP_PORT_BASE))" -lt "$WORKSHOP_PORT_SPAN"\n', ''
        )
        with self.assertRaises(runbook.RunbookError):
            runbook.validate_runbook_text(no_range_check)
        shared_graph = self.text.replace(
            'GRAPH_NAME="wardatlas_ui_${WORKSHOP_LANE}_${RUN_ID}"',
            'GRAPH_NAME="wardatlas_ui_shared"',
        )
        with self.assertRaises(runbook.RunbookError):
            runbook.validate_runbook_text(shared_graph)
        no_lane_bound = self.text.replace('test "${#WORKSHOP_LANE}" -le 32\n', '')
        with self.assertRaises(runbook.RunbookError):
            runbook.validate_runbook_text(no_lane_bound)

    def test_rejects_missing_owned_cleanup_and_evidence_preservation(self) -> None:
        no_cleanup = self.text.replace(
            'rm -rf -- "$DB_DATA_DIR" "$STATE_DIR"', 'echo cleanup omitted'
        )
        with self.assertRaises(runbook.RunbookError):
            runbook.validate_runbook_text(no_cleanup)
        no_evidence = self.text.replace('test -d "$EVIDENCE_DIR"', 'true')
        with self.assertRaises(runbook.RunbookError):
            runbook.validate_runbook_text(no_evidence)
        broad_cleanup = self.text.replace(
            '   rm -rf -- "$DB_DATA_DIR" "$STATE_DIR"',
            '   rm -rf -- "$PROJECT_ROOT/.local"\n   rm -rf -- "$DB_DATA_DIR" "$STATE_DIR"',
        )
        with self.assertRaises(runbook.RunbookError):
            runbook.validate_runbook_text(broad_cleanup)

    def test_rejects_inflight_features_and_unsupported_claims(self) -> None:
        with self.assertRaises(runbook.RunbookError):
            runbook.validate_runbook_text(self.text + "\nThe decision knowledge timeline is accepted here.\n")
        with self.assertRaises(runbook.RunbookError):
            runbook.validate_runbook_text(self.text + "\nThe source review UI is accepted here.\n")
        with self.assertRaises(runbook.RunbookError):
            runbook.validate_runbook_text(self.text + "\nWardAtlas reduces downtime by 50%.\n")
        with self.assertRaises(runbook.RunbookError):
            runbook.validate_runbook_text(self.text + "\nThis system is HIPAA compliant.\n")

    def test_rejects_changed_path_roster_time_or_sop_source_association(self) -> None:
        wrong_roster_time = self.text.replace(
            "**Central-sterile path provenance (in order):**", "**Changed path provenance:**", 1
        ).replace(
            "SRC-OPS-ROSTER` — *Synthetic Operations Role Roster* — observed `2026-10-14T18:04:00Z`",
            "SRC-OPS-ROSTER` — *Synthetic Operations Role Roster* — observed `2026-10-14T18:05:00Z`",
            1,
        )
        with self.assertRaises(runbook.RunbookError):
            runbook.validate_runbook_text(wrong_roster_time)
        wrong_sop_source = self.text.replace(
            "V2 claims `ROLE-FACILITIES-DUTY` from `SRC-SOP-CURRENT-V2`",
            "V2 claims `ROLE-FACILITIES-DUTY` from `SRC-SOP-CURRENT-V3`",
            1,
        )
        with self.assertRaises(runbook.RunbookError):
            runbook.validate_runbook_text(wrong_sop_source)
        wrong_branch_state = self.text.replace("`hypothetical-restored`", "`observed-restored`", 1)
        with self.assertRaises(runbook.RunbookError):
            runbook.validate_runbook_text(wrong_branch_state)

    def test_does_not_leak_denied_source_fixture_tripwires(self) -> None:
        for private_probe in runbook.RESTRICTED_FIXTURE_TRIPWIRES:
            self.assertNotIn(private_probe.casefold(), self.text.casefold())

    def test_truthful_ai_track_scope_and_eligibility_disclosures(self) -> None:
        normalized = self.text.casefold()
        for phrase in (
            "no ai model is called at runtime",
            "ai assistants helped author",
            "company brain",
            "fictional synthetic",
            "non-clinical",
            "eligibility risk remains",
            "does not certify those requirements",
        ):
            self.assertIn(phrase, normalized)

    def test_static_verifier_invokes_each_original_oracle_contract(self) -> None:
        commands = dict(runbook._contract_commands())
        self.assertEqual(set(commands), {"original-outage", "shift-board", "sop-impact", "handover"})
        for command in commands.values():
            self.assertIn("scripts/verify_", " ".join(command))
        self.assertIn("--require-six-scenarios", commands["original-outage"])
        self.assertIn("--require-eight-cases", commands["shift-board"])
        self.assertIn("--require-ten-cases", commands["sop-impact"])
        self.assertIn("--require-nine-cases", commands["handover"])


if __name__ == "__main__":
    unittest.main()

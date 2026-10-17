"""Small pure tests for the additive shift-board graph model."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from wardatlas_shift_board import (  # noqa: E402
    BRANCH_ID,
    DOCK_SOURCE_QUERY,
    branch_reset_queries,
    dock_role_query,
    dock_review_query,
    dock_source_query,
    dock_supersession_query,
    normalize_timestamp,
    seed_shift_board_queries,
)


class ShiftBoardModelTests(unittest.TestCase):
    def test_explicit_times_normalize_to_the_same_utc_second(self):
        self.assertEqual(normalize_timestamp("2026-10-14T22:30:00+02:00"), "2026-10-14T20:30:00Z")
        self.assertEqual(normalize_timestamp("2026-10-14T20:30:00Z"), "2026-10-14T20:30:00Z")

    def test_missing_or_invalid_time_is_rejected_instead_of_guessed(self):
        for value in ("", "2026-10-14T20:30:00", "2026-10-14T18:61:00Z", "tomorrow"):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, "Invalid timestamp"):
                normalize_timestamp(value)

    def test_roster_query_uses_effective_time_and_connected_relationships(self):
        query = dock_role_query("2026-10-14T20:30:00Z")
        self.assertIn("owns.effective_from <= \"2026-10-14T20:30:00Z\"", query)
        self.assertIn("owns.effective_to > \"2026-10-14T20:30:00Z\"", query)
        for rel in ("AFFECTS", "SUPPORTS", "SERVES", "OWNED_BY", "EVIDENCED_BY"):
            self.assertIn(f":{rel}", query)
        self.assertIn("src.access = 'operations'", query)
        from wardatlas_shift_board import CONVEYOR_EVENT_QUERY, CONVEYOR_PATH_QUERY
        self.assertIn("event.event_kind = 'observed'", CONVEYOR_EVENT_QUERY)
        self.assertIn("event.branch_id IS NULL", CONVEYOR_EVENT_QUERY)
        self.assertIn("rsrc.access = 'operations'", CONVEYOR_PATH_QUERY)
        self.assertIn("owns.source_id = rsrc.id", CONVEYOR_PATH_QUERY)

    def test_dock_knowledge_cutoff_filters_every_visible_evidence_join(self):
        cutoff = "2026-10-14T20:17:59Z"
        self.assertIn(f'src.observed_at <= "{cutoff}"', dock_role_query("2026-10-14T20:30:00Z", cutoff))
        self.assertIn(f'src.observed_at <= "{cutoff}"', dock_source_query(cutoff))
        self.assertIn(f'newer.observed_at <= "{cutoff}"', dock_supersession_query(cutoff))
        self.assertIn(f'older.observed_at <= "{cutoff}"', dock_supersession_query(cutoff))
        self.assertIn(f'newer.observed_at <= "{cutoff}"', dock_review_query(cutoff))
        self.assertIn(f'older.observed_at <= "{cutoff}"', dock_review_query(cutoff))
        self.assertEqual(dock_source_query(), DOCK_SOURCE_QUERY)
        self.assertNotIn("observed_at <=", dock_role_query("2026-10-14T20:30:00Z"))

    def test_seed_and_branch_reset_are_idempotent_and_narrow(self):
        queries = seed_shift_board_queries()
        self.assertEqual(len(queries), 34)
        node_ids = [line.split("id: \"", 1)[1].split("\"", 1)[0] for line in queries if line.startswith("MERGE (n:")]
        edge_ids = [line.split("MERGE (a)-[r:", 1)[1].split("{id: \"", 1)[1].split("\"", 1)[0] for line in queries if line.startswith("MATCH ") and "MERGE (a)-[r:" in line]
        self.assertEqual(len(node_ids), len(set(node_ids)))
        self.assertEqual(len(edge_ids), len(set(edge_ids)))
        reset = "\n".join(branch_reset_queries())
        self.assertIn(BRANCH_ID, reset)
        self.assertIn("EVT-BR-RESTORE-01", reset)
        self.assertNotIn("INC-204", reset)
        self.assertNotIn("GRAPH.DELETE", reset)


if __name__ == "__main__":
    unittest.main()

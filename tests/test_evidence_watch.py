"""Focused projection/status tests for the graph-backed INC-207 watch."""

from __future__ import annotations

import copy
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from wardatlas_evidence_watch import comparison_status, normalize_watch_axes, public_refresh, witness_from_dock


class EvidenceWatchProjectionTests(unittest.TestCase):
    def setUp(self):
        self.r1 = {
            "effective_at": "2026-10-14T20:29:00Z",
            "known_at": "2026-10-14T20:17:59Z",
            "role_path": ["INC-207", "EQ-DOCK-CART-2", "SVC-INBOUND-TRANSFER", "DEPT-RECEIVING-DOCK", "ROLE-DOCK-DUTY"],
            "role_id": "ROLE-DOCK-DUTY",
            "role_status": "known",
            "source_versions": [{"source_id": "SRC-DOCK-ROSTER-R1", "title": "Synthetic Dock Operations Roster Revision 1", "observed_at": "2026-10-14T19:00:00Z"}],
            "review_candidates": [],
            "linkage_status": "unknown",
        }
        self.r2 = {
            "source_id": "SRC-DOCK-ROSTER-R2",
            "title": "Synthetic Dock Operations Roster Revision 2",
            "observed_at": "2026-10-14T20:18:00Z",
            "effective_at": "2026-10-14T20:30:00Z",
            "supersedes": "SRC-DOCK-ROSTER-R1",
            "status": "recorded/pending",
        }

    def test_four_temporal_comparison_outcomes(self):
        self.assertEqual(comparison_status(self.r1, self.r1), "unchanged")
        new_source = copy.deepcopy(self.r1)
        new_source["known_at"] = "2026-10-14T20:19:00Z"
        new_source["source_versions"].append(self.r2)
        new_source["review_candidates"] = ["HND-DOCK-2020", "TKT-DOCK-88"]
        new_source["linkage_status"] = "linked"
        self.assertEqual(comparison_status(self.r1, new_source), "new-source-known")
        saved_after_r2 = copy.deepcopy(new_source)
        # Moving knowledge backward removes a source relative to this baseline;
        # it is not a newly observed source or evidence of an unchanged view.
        self.assertEqual(comparison_status(saved_after_r2, self.r1), "unknown")
        rewritten_source = copy.deepcopy(self.r1)
        rewritten_source["source_versions"][0]["title"] = "Unexpected rewritten metadata"
        self.assertEqual(comparison_status(self.r1, rewritten_source), "unknown")
        unknown = copy.deepcopy(self.r1)
        unknown.update({"effective_at": "2026-10-14T20:30:00Z", "role_path": [], "role_id": None, "role_status": "unknown"})
        self.assertEqual(comparison_status(self.r1, unknown), "unknown")
        changed = copy.deepcopy(new_source)
        changed.update({"effective_at": "2026-10-14T20:30:00Z", "role_path": [*self.r1["role_path"][:-1], "ROLE-DOCK-SWING"], "role_id": "ROLE-DOCK-SWING"})
        self.assertEqual(comparison_status(self.r1, changed), "changed")

    def test_missing_r2_relationship_or_source_backing_stays_unknown(self):
        known_r2 = copy.deepcopy(self.r1)
        known_r2["source_versions"].append(self.r2)
        known_r2["role_path"][-1] = "ROLE-DOCK-SWING"
        known_r2["role_id"] = "ROLE-DOCK-SWING"
        known_r2["linkage_status"] = "unknown"
        self.assertEqual(comparison_status(self.r1, known_r2), "unknown")
        missing_source = witness_from_dock({
            "role_path": [*self.r1["role_path"][:-1], "ROLE-DOCK-SWING"],
            "role_id": "ROLE-DOCK-SWING",
            "role_status": "known",
            "source_versions": [self.r1["source_versions"][0]],
            "role_evidence": [{"source_id": "SRC-DOCK-ROSTER-R2", "title": "unavailable", "observed_at": "2026-10-14T20:18:00Z"}],
            "review_candidates": [],
            "linkage_status": "linked",
        }, "2026-10-14T20:30:00Z", "2026-10-14T20:19:00Z")
        self.assertEqual(missing_source["role_status"], "unknown")
        self.assertEqual(comparison_status(self.r1, missing_source), "unknown")

    def test_projection_uses_only_source_backed_operations_dock_fields(self):
        dock = {
            **self.r1,
            "as_of": "2026-10-14T20:29:00Z",
            "role_evidence": [{"source_id": "SRC-DOCK-ROSTER-R1", "title": "Synthetic Dock Operations Roster Revision 1", "observed_at": "2026-10-14T19:00:00Z"}],
            "review_candidates": [{"artifact_id": "HND-DOCK-2020"}],
            "restricted_decoy": "must not pass through",
        }
        witness = witness_from_dock(dock, self.r1["effective_at"], self.r1["known_at"])
        self.assertEqual(witness["role_path"], self.r1["role_path"])
        self.assertEqual(witness["review_candidates"], ["HND-DOCK-2020"])
        self.assertNotIn("restricted_decoy", witness)
        bad = copy.deepcopy(dock)
        bad["role_evidence"] = []
        self.assertEqual(witness_from_dock(bad, self.r1["effective_at"], self.r1["known_at"])["role_status"], "unknown")
        self.assertEqual(witness_from_dock(None, self.r1["effective_at"], self.r1["known_at"])["role_status"], "unknown")

    def test_time_axes_require_explicit_offsets_and_normalize_independently(self):
        self.assertEqual(normalize_watch_axes("2026-10-14T22:29:00+02:00", "2026-10-14T22:19:00+02:00"), ("2026-10-14T20:29:00Z", "2026-10-14T20:19:00Z"))
        for pair in (("not-a-timestamp", "2026-10-14T20:19:00Z"), ("2026-10-14T20:29:00Z", "2026-10-14T20:19:00")):
            with self.subTest(pair=pair), self.assertRaises(ValueError):
                normalize_watch_axes(*pair)

    def test_refresh_status_does_not_claim_action_changes(self):
        result = public_refresh({"baseline": self.r1}, self.r1)
        self.assertEqual(result["status"], "unchanged")
        self.assertIs(result["action_or_work_item_changed"], False)
        self.assertNotIn("risk_score", result)
        self.assertNotIn("contact", result)


if __name__ == "__main__":
    unittest.main()

"""Unit tests for the graph-response-to-handover snapshot projection."""

from __future__ import annotations

import copy
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from wardatlas_handover import has_complete_sop_witness, snapshot_from_impact, source_ids_from_snapshot


EXPECTED = json.loads((ROOT / "tests/handover-brief-expected-results.json").read_text(encoding="utf-8"))


def pinned_impact() -> dict:
    snapshot = copy.deepcopy(EXPECTED["canonical_snapshot"])
    witness = snapshot["sop_witness"]
    return {
        "as_of": snapshot["as_of"],
        "impact_state": "unavailable",
        "impact_paths": snapshot["impact"]["paths"],
        "path_evidence": snapshot["impact"]["path_evidence"],
        "complete_conflict_witness_returned": True,
        "conflict_linkage_status": "linked-unresolved",
        "conflict_status": witness["conflict_status"],
        "applicability_edges": witness["applicability_edges"],
        "conflict_edge": witness["conflict_edge"],
        "role_claims": witness["claim_evidence"],
        "claims_are_current_owner_facts": witness["claims_are_current_owner_facts"],
        "winner_selected": witness["winner_selected"],
        "stale_version": {
            "id": witness["stale_version_excluded_from_c4_witness"]["version_id"],
            "source_id": witness["stale_version_excluded_from_c4_witness"]["source_id"],
            "source_title": witness["stale_version_excluded_from_c4_witness"]["source_title"],
            "observed_at": witness["stale_version_excluded_from_c4_witness"]["observed_at"],
            "referenced_equipment_id": witness["stale_version_excluded_from_c4_witness"]["referenced_equipment_id"],
            "stale_is_applicable_to_c4": witness["stale_version_excluded_from_c4_witness"]["is_applicable_to_c4"],
            "stale_is_joined_to_inc204_conflict_witness": witness["stale_version_excluded_from_c4_witness"]["is_joined_to_inc204_conflict_witness"],
        },
    }


class HandoverSnapshotTests(unittest.TestCase):
    def test_complete_projection_equals_independent_canonical_snapshot(self):
        impact = pinned_impact()
        projected = snapshot_from_impact(impact)
        self.assertEqual(projected, EXPECTED["canonical_snapshot"])
        self.assertTrue(has_complete_sop_witness(projected))

    def test_missing_applicability_edge_preserves_paths_but_is_incomplete(self):
        impact = pinned_impact()
        impact["complete_conflict_witness_returned"] = False
        impact["conflict_linkage_status"] = "incomplete/unknown"
        impact["conflict_status"] = "unknown"
        impact["applicability_edges"] = []
        impact["conflict_edge"] = None
        impact["role_claims"] = [claim for claim in impact["role_claims"] if claim["sop_version_id"] == "SOP-OPS-9-V3"]

        projected = snapshot_from_impact(impact)
        self.assertEqual(projected["impact"], EXPECTED["canonical_snapshot"]["impact"])
        self.assertEqual(projected["sop_witness"]["linkage_status"], "incomplete/unknown")
        self.assertEqual(projected["sop_witness"]["conflict_status"], "unknown")
        self.assertEqual(projected["sop_witness"]["applicability_edges"], [])
        self.assertIsNone(projected["sop_witness"]["conflict_edge"])
        self.assertFalse(has_complete_sop_witness(projected))

    def test_missing_conflict_edge_does_not_erase_source_claims_or_claim_resolution(self):
        impact = pinned_impact()
        impact["complete_conflict_witness_returned"] = False
        impact["conflict_linkage_status"] = "unknown"
        impact["conflict_status"] = "unknown"
        impact["conflict_edge"] = None

        projected = snapshot_from_impact(impact)
        self.assertEqual(projected["impact"], EXPECTED["canonical_snapshot"]["impact"])
        self.assertEqual(projected["sop_witness"]["linkage_status"], "unknown")
        self.assertEqual(projected["sop_witness"]["claim_evidence"], EXPECTED["canonical_snapshot"]["sop_witness"]["claim_evidence"])
        self.assertFalse(has_complete_sop_witness(projected))

    def test_missing_witness_cannot_be_promoted_to_resolved_or_owner_truth(self):
        impact = pinned_impact()
        impact["complete_conflict_witness_returned"] = False
        impact["conflict_linkage_status"] = "resolved"
        impact["conflict_status"] = "resolved"
        impact["claims_are_current_owner_facts"] = True
        impact["winner_selected"] = True

        witness = snapshot_from_impact(impact)["sop_witness"]
        self.assertEqual(witness["linkage_status"], "unknown")
        self.assertEqual(witness["conflict_status"], "unknown")
        self.assertFalse(witness["claims_are_current_owner_facts"])
        self.assertFalse(witness["winner_selected"])
        self.assertFalse(has_complete_sop_witness({"sop_witness": witness}))

    def test_snapshot_is_a_detached_record_and_source_ids_come_from_visible_evidence(self):
        impact = pinned_impact()
        projected = snapshot_from_impact(impact)
        before = copy.deepcopy(projected)
        impact["path_evidence"][0]["source_ids"].clear()
        impact["role_claims"].clear()
        self.assertEqual(projected, before)
        self.assertEqual(source_ids_from_snapshot(projected), [
            "SRC-ASSET-C4",
            "SRC-INC-204",
            "SRC-OPS-ROSTER",
            "SRC-SERVICE-CATALOG",
            "SRC-SOP-ARCHIVE-9",
            "SRC-SOP-CURRENT-V2",
            "SRC-SOP-CURRENT-V3",
        ])

    def test_cold_graph_remains_unknown_and_has_no_sop_witness(self):
        result = snapshot_from_impact({
            "as_of": "2026-10-14T18:30:00Z",
            "impact_state": "unknown",
            "impact_paths": [],
            "path_evidence": [],
            "complete_conflict_witness_returned": False,
            "conflict_linkage_status": "unknown",
            "conflict_status": "unknown",
            "role_claims": [],
            "stale_version": None,
        })
        self.assertEqual(result["impact"], {"status": "unknown", "paths": [], "path_evidence": []})
        self.assertIsNone(result["sop_witness"])


if __name__ == "__main__":
    unittest.main()

"""Unit tests for historical/current handover delta semantics."""

from __future__ import annotations

import copy
import json
import socket
import unittest
import uuid
from tempfile import TemporaryDirectory
from pathlib import Path
import sys
from unittest.mock import Mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from wardatlas_handover import compare_graph_witness
from wardatlas_app import WardAtlasService


ORACLE = json.loads((ROOT / "tests/handover-drift-expected-results.json").read_text())
CANONICAL = ORACLE["canonical_snapshot"]


def impact_from_snapshot(snapshot: dict, *, as_of: str, state: str, event: dict | None) -> dict:
    witness = snapshot.get("sop_witness") or {}
    stale = witness.get("stale_version_excluded_from_c4_witness")
    stale_row = ({
        "id": stale.get("version_id"),
        "source_id": stale.get("source_id"),
        "source_title": stale.get("source_title"),
        "observed_at": stale.get("observed_at"),
        "referenced_equipment_id": stale.get("referenced_equipment_id"),
        "stale_is_applicable_to_c4": stale.get("is_applicable_to_c4", False),
        "stale_is_joined_to_inc204_conflict_witness": stale.get("is_joined_to_inc204_conflict_witness", False),
    } if stale else None)
    return {
        "scope": "operations",
        "as_of": as_of,
        "impact_state": state,
        "observed_event": event,
        "impact_paths": copy.deepcopy(snapshot.get("impact", {}).get("paths", [])),
        "path_evidence": copy.deepcopy(snapshot.get("impact", {}).get("path_evidence", [])),
        "role_claims": copy.deepcopy(witness.get("claim_evidence", [])),
        "stale_version": copy.deepcopy(stale_row),
        "applicability_edges": copy.deepcopy(witness.get("applicability_edges", [])),
        "conflict_edge": copy.deepcopy(witness.get("conflict_edge")),
        "conflict_status": witness.get("conflict_status", "unknown"),
        "conflict_linkage_status": "linked-unresolved" if witness.get("linkage_status") == "complete" else witness.get("linkage_status", "unknown"),
        "complete_conflict_witness_returned": witness.get("linkage_status") == "complete",
    }


def board_paths(snapshot: dict) -> list[dict]:
    rows = []
    for item in snapshot["impact"]["path_evidence"]:
        rows.append({
            "path": copy.deepcopy(item["path"]),
            "evidence": [
                {"source_id": source, "title": title, "observed_at": observed}
                for source, title, observed in zip(item["source_ids"], item["source_titles"], item["observed_at"])
            ],
        })
    return rows


def action() -> dict:
    return {
        "id": "HANDOVER-TEST",
        "incident_id": "INC-204",
        "confirmed_at": "2026-10-14T18:31:00Z",
        "storage": "local-only",
        "snapshot": copy.deepcopy(CANONICAL),
    }


def board(*, branch_status="hypothetical-restored", branch_paths=None) -> dict:
    return {
        "conveyor": {
            "status": "observed-impact",
            "event": {"event_id": "EVT-C4-UNAVAILABLE-01", "evidence_label": "observed source fact"},
            "paths": board_paths(CANONICAL),
            "branch": {
                "branch_id": "BR-C4-RESTORE-DRILL",
                "as_of": "2026-10-14T18:50:00Z",
                "status": branch_status,
                "event": {"event_id": "EVT-BR-RESTORE-01", "evidence_label": "hypothetical branch"},
                "paths": branch_paths or [],
            },
        }
    }


class HandoverDriftProjectionTests(unittest.TestCase):
    def test_observed_restoration_removes_exact_paths_with_historical_provenance(self):
        saved = action()
        original_bytes = json.dumps(saved, sort_keys=True)
        impact = impact_from_snapshot(
            {"sop_witness": CANONICAL["sop_witness"], "impact": {"paths": [], "path_evidence": []}},
            as_of=ORACLE["fixture"]["observed_restoration_as_of"],
            state="restored",
            event={
                "event_id": "EVT-C4-RESTORED-01",
                "value": "available",
                "event_at": "2026-10-14T19:15:00Z",
                "evidence_label": "observed source fact",
                "source_id": "SRC-ASSET-C4-RESTORE",
                "source_title": "Synthetic Asset State Ledger",
                "source_observed_at": "2026-10-14T19:16:00Z",
            },
        )
        result = compare_graph_witness(
            saved,
            impact,
            board(),
            impact_from_snapshot(CANONICAL, as_of="2026-10-14T18:50:00Z", state="observed-impact", event={"evidence_label": "observed source fact"}),
        )
        expected = ORACLE["cases"][2]["expected"]
        self.assertEqual(result["observed"]["current_state"], expected["current_status"])
        self.assertEqual(result["observed"]["current_impact_paths"], expected["current_active_paths"])
        self.assertEqual([row["path"] for row in result["observed"]["removed_paths"]], expected["removed_paths"])
        self.assertEqual(result["observed"]["added_paths"], expected["added_paths"])
        self.assertEqual(result["observed"]["removed_paths"][0]["provenance"], CANONICAL["impact"]["path_evidence"][0])
        self.assertEqual(result["observed"]["restoration_source_fact"]["event_id"], expected["restoration_source_fact"]["event_id"])
        self.assertEqual(result["observed"]["sop_witness"], CANONICAL["sop_witness"])
        self.assertTrue(result["observed"]["sop_witness_unchanged"])
        self.assertEqual(json.dumps(saved, sort_keys=True), original_bytes)

    def test_hypothetical_branch_is_separate_from_observed_mainline(self):
        result = compare_graph_witness(
            action(),
            impact_from_snapshot(CANONICAL, as_of="2026-10-14T19:30:00Z", state="restored", event={"evidence_label": "observed source fact"}),
            board(),
            impact_from_snapshot(CANONICAL, as_of="2026-10-14T18:50:00Z", state="observed-impact", event={"evidence_label": "observed source fact"}),
        )
        expected = ORACLE["cases"][3]["expected"]
        branch = result["hypothetical_branch"]
        self.assertEqual(branch["branch_id"], expected["branch_id"])
        self.assertEqual(branch["status"], expected["branch_status"])
        self.assertEqual(branch["delta_label"], expected["branch_delta_label"])
        self.assertEqual(branch["paths"], expected["branch_paths"])
        self.assertEqual(branch["observed_mainline_status"], expected["observed_mainline_status"])
        self.assertEqual([row["path"] for row in branch["observed_mainline_paths"]], expected["observed_mainline_paths"])
        self.assertFalse(branch["observed_state_mutated"])
        self.assertEqual(branch["path_delta"]["status"], "hypothetical")
        self.assertEqual([row["path"] for row in branch["path_delta"]["removed_paths"]], expected["observed_mainline_paths"])

    def test_empty_graph_and_absent_snapshot_are_unknown_not_empty_delta(self):
        unknown_impact = {
            "scope": "operations", "as_of": "2026-10-14T19:30:00Z", "impact_state": "unknown",
            "observed_event": None, "impact_paths": [], "path_evidence": [], "role_claims": [],
            "stale_version": None, "applicability_edges": [], "conflict_edge": None,
            "conflict_status": "unknown", "conflict_linkage_status": "incomplete/unknown",
            "complete_conflict_witness_returned": False,
        }
        no_branch = {"conveyor": {"status": "unknown", "paths": [], "branch": {"status": "unknown", "paths": []}}}
        result = compare_graph_witness(None, unknown_impact, no_branch, unknown_impact)
        self.assertIsNone(result["historical_snapshot"])
        self.assertEqual(result["comparison_status"], "unknown")
        self.assertEqual(result["observed"]["path_delta"]["status"], "unknown")
        self.assertEqual(result["observed"]["path_delta"]["removed_paths"], [])
        self.assertFalse(result["observed"]["no_operational_risk_conclusion_asserted"])

    def test_missing_sop_witness_keeps_impact_paths_but_marks_witness_unknown(self):
        snapshot = copy.deepcopy(CANONICAL)
        snapshot["sop_witness"]["linkage_status"] = "incomplete/unknown"
        snapshot["sop_witness"]["conflict_status"] = "unknown"
        snapshot["sop_witness"]["applicability_edges"] = []
        snapshot["sop_witness"]["conflict_edge"] = None
        impact = impact_from_snapshot(snapshot, as_of="2026-10-14T18:30:00Z", state="observed-impact", event={"evidence_label": "observed source fact"})
        impact["complete_conflict_witness_returned"] = False
        impact["conflict_linkage_status"] = "incomplete/unknown"
        result = compare_graph_witness(action(), impact, board(), impact)
        self.assertEqual(result["observed"]["current_impact_paths"], CANONICAL["impact"]["paths"])
        self.assertEqual(result["observed"]["path_delta"]["status"], "unchanged")
        self.assertEqual(result["observed"]["sop_witness"]["linkage_status"], "incomplete/unknown")
        self.assertIsNone(result["observed"]["sop_witness"]["conflict_edge"])
        self.assertFalse(result["observed"]["sop_witness_unchanged"])

    def test_service_normalizes_times_and_only_reads_the_saved_action(self):
        (ROOT / ".local").mkdir(exist_ok=True)
        with TemporaryDirectory(prefix="handover-drift-unit-", dir=ROOT / ".local") as directory:
            with socket.socket() as sock:
                sock.bind(("127.0.0.1", 0))
                ephemeral_port = sock.getsockname()[1]
            service = WardAtlasService(ephemeral_port, f"wardatlas_test_unit_{uuid.uuid4().hex}", Path(directory))
            record = action()
            action_file = Path(directory) / "confirmed-actions.json"
            action_file.write_text(json.dumps({"schema_version": 1, "items": [record]}))
            before = action_file.read_bytes()
            service.sop_impact = Mock(side_effect=[
                impact_from_snapshot({"impact": {"paths": [], "path_evidence": []}, "sop_witness": CANONICAL["sop_witness"]}, as_of="2026-10-14T19:30:00Z", state="restored", event={"evidence_label": "observed source fact"}),
                impact_from_snapshot(CANONICAL, as_of="2026-10-14T18:50:00Z", state="observed-impact", event={"evidence_label": "observed source fact"}),
            ])
            service.shift_board = Mock(return_value=board())
            result = service.compare_handover(
                record["id"],
                "2026-10-14T21:30:00+02:00",
                "2026-10-14T20:50:00+02:00",
            )
            self.assertEqual(service.sop_impact.call_args_list[0].args, ("2026-10-14T19:30:00Z",))
            self.assertEqual(service.shift_board.call_args.args, ("2026-10-14T20:30:00Z", "2026-10-14T18:50:00Z", "2026-10-14T18:50:00Z"))
            self.assertEqual(service.sop_impact.call_args_list[1].args, ("2026-10-14T18:50:00Z",))
            self.assertEqual(result["action"]["confirmed_at"], record["confirmed_at"])
            self.assertEqual(action_file.read_bytes(), before)

    def test_service_rejects_bad_time_before_action_or_graph_access(self):
        (ROOT / ".local").mkdir(exist_ok=True)
        with TemporaryDirectory(prefix="handover-drift-unit-", dir=ROOT / ".local") as directory:
            with socket.socket() as sock:
                sock.bind(("127.0.0.1", 0))
                ephemeral_port = sock.getsockname()[1]
            service = WardAtlasService(ephemeral_port, f"wardatlas_test_unit_{uuid.uuid4().hex}", Path(directory))
            service.saved_actions = Mock(side_effect=AssertionError("action state must not be read before timestamp validation"))
            service.sop_impact = Mock(side_effect=AssertionError("graph must not be queried before timestamp validation"))
            with self.assertRaises(ValueError):
                service.compare_handover("HANDOVER-TEST", "not-a-timestamp", "2026-10-14T18:50:00Z")
            service.saved_actions.assert_not_called()
            service.sop_impact.assert_not_called()


if __name__ == "__main__":
    unittest.main()

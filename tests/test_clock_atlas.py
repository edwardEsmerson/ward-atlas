"""Focused clock atlas isolation, validation and mutation cases.

Synthetic service doubles test projection only; live verification is separate.
Expected literals come from the pre-implementation source-pinned contract.
"""
from __future__ import annotations

import copy
import hashlib
import json
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
from urllib.parse import urlencode

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from wardatlas_clock_atlas import (
    EFFECTIVE_TIMES, KNOWN_TIMES, atlas_from_query, build_atlas,
    mount_clock_atlas, normalize_selection, project_cell, selection_from_query,
)

EXPECTED = json.loads((ROOT / "contracts/clock-atlas/expected-results.json").read_text())


class ClockAtlasTests(unittest.TestCase):
    def setUp(self):
        self.calls = []
        self.rows = copy.deepcopy(EXPECTED["cells"])

    def board(self, **axes):
        self.calls.append(axes)
        cell = next(row for row in self.rows if row["effective_at"] == axes["dock_at"] and row["known_at"] == axes["known_at"])
        dock = copy.deepcopy(cell)
        dock["review_candidates"] = [{"artifact_id": value} for value in dock["review_candidates"]]
        dock["role_evidence"] = [copy.deepcopy(dock["source_versions"][-1])] if dock["role_path"] else []
        dock["coverage_status"] = "explicit_edges_only"
        return {"dock": dock, "conveyor": {"secret_other_case": "must-not-project"}}

    def assert_no_queries(self, query, exception=ValueError):
        with self.assertRaises(exception):
            atlas_from_query(self.board, query)
        self.assertEqual(self.calls, [])

    def test_source_hashes_match_frozen_oracles(self):
        self.assertEqual({row["file"]: row["sha256"] for row in EXPECTED["pinned_sources"]}, {
            "tests/dock-bitemporal-expected-results.json": "243c92775d7c1366962dcd7c5c9acd9ea753f2924af84c2c5dc99b0b5c872205",
            "tests/evidence-watch-expected-results.json": "1ee83f8ad7f9ad52b08b3759b20b590343c44eb09a7aeb2b18f1cd9d79027e1c",
        })
        for source in EXPECTED["pinned_sources"]:
            self.assertEqual(hashlib.sha256((ROOT / source["file"]).read_bytes()).hexdigest(), source["sha256"])

    def test_frozen_matrix_maps_every_nonzero_group(self):
        from verify_clock_atlas import load_contract
        expected, matrix = load_contract()
        self.assertGreaterEqual(len(matrix["groups"]), 8)
        self.assertGreaterEqual(sum(len(row["assertions"]) for row in matrix["groups"]), 40)
        self.assertEqual(set(expected["groups"]), {row["id"] for row in matrix["groups"]})

    def test_exact_four_independent_pairs(self):
        result = build_atlas(self.board)
        self.assertEqual(self.calls, [{"dock_at": e, "known_at": k} for e in EFFECTIVE_TIMES for k in KNOWN_TIMES])
        self.assertEqual(len(result["cells"]), 4)
        for actual, expected in zip(result["cells"], EXPECTED["cells"]):
            self.assertEqual({key: actual[key] for key in expected}, expected)

    def test_defaults_are_pinned_not_local_clock(self):
        self.assertEqual(selection_from_query(""), (EFFECTIVE_TIMES[0], KNOWN_TIMES[0]))
        self.assertEqual(atlas_from_query(self.board, "scope=operations")["selected_cell_id"], "e0-k0")

    def test_offset_pair_normalizes_both_axes(self):
        query = urlencode({"effective_at":"2026-10-14T22:30:00+02:00", "known_at":"2026-10-14T22:19:00+02:00"})
        result = atlas_from_query(self.board, query)
        self.assertEqual(result["selected_cell_id"], "e1-k1")
        self.assertEqual(result["selected_cell"]["effective_at"], EFFECTIVE_TIMES[1])
        self.assertEqual(result["selected_cell"]["known_at"], KNOWN_TIMES[1])

    def test_malformed_effective_rejects_before_queries(self):
        self.assert_no_queries(urlencode({"effective_at":"bad", "known_at":KNOWN_TIMES[0]}))

    def test_naive_known_rejects_before_queries(self):
        self.assert_no_queries(urlencode({"effective_at":EFFECTIVE_TIMES[0], "known_at":"2026-10-14T20:19:00"}))

    def test_duplicate_equal_effective_rejects_before_queries(self):
        self.assert_no_queries(urlencode([("effective_at", EFFECTIVE_TIMES[0]), ("effective_at", EFFECTIVE_TIMES[0]), ("known_at",KNOWN_TIMES[0])]))

    def test_duplicate_known_rejects_before_queries(self):
        self.assert_no_queries(urlencode([("effective_at", EFFECTIVE_TIMES[0]), ("known_at",KNOWN_TIMES[0]), ("known_at",KNOWN_TIMES[1])]))

    def test_duplicate_scope_rejects_before_queries(self):
        self.assert_no_queries("scope=operations&scope=operations")

    def test_decoded_duplicate_and_blank_axes_reject_before_queries(self):
        self.assert_no_queries("scope=operations&%73cope=operations")
        self.assert_no_queries("effective_at=&known_at=2026-10-14T20:19:00Z")
        self.assert_no_queries("effective_at=2026-10-14T20:29:00Z&known_at=")

    def test_restricted_scope_rejects_before_queries(self):
        self.assert_no_queries("scope=restricted", PermissionError)

    def test_unsupported_pair_rejects_before_queries(self):
        self.assert_no_queries(urlencode({"effective_at":"2026-10-14T20:31:00Z", "known_at":KNOWN_TIMES[0]}))

    def test_unknown_parameter_and_partial_pair_reject_before_queries(self):
        for query in ("query=roster", "effective_at="+EFFECTIVE_TIMES[0], "known_at="+KNOWN_TIMES[0], "scope="):
            with self.subTest(query=query):
                self.assert_no_queries(query, PermissionError if query == "scope=" else ValueError)

    def test_cold_unknown_does_not_borrow_any_fact(self):
        result = build_atlas(lambda **axes: {"dock":None})
        for cell in result["cells"]:
            self.assertEqual(cell["role_path"], [])
            self.assertIsNone(cell["role_id"])
            self.assertEqual(cell["role_status"], "unknown")
            self.assertEqual(cell["source_versions"], [])
            self.assertEqual(cell["review_candidates"], [])

    def test_crossed_detail_never_borrows_later_evidence(self):
        result = build_atlas(self.board, EFFECTIVE_TIMES[1], KNOWN_TIMES[0])
        selected = result["selected_cell"]
        self.assertEqual(selected["id"], "e1-k0")
        self.assertEqual(selected["role_path"], [])
        serialized = json.dumps(selected)
        for marker in ("ROSTER-R2", "Revision 2", "20:18:00Z", "ROLE-DOCK-SWING", "HND-DOCK-2020", "TKT-DOCK-88"):
            self.assertNotIn(marker, serialized)
        self.assertIn("ROSTER-R2", json.dumps(result["cells"][3]))

    def test_selected_detail_is_deeply_detached(self):
        result = build_atlas(self.board)
        baseline = copy.deepcopy(result["cells"])
        result["selected_cell"]["source_versions"][0]["title"] = "mutated"
        result["selected_cell"]["role_path"].clear()
        self.assertEqual(result["cells"], baseline)

    def test_projection_never_mutates_service_output(self):
        dock = self.board(dock_at=EFFECTIVE_TIMES[0], known_at=KNOWN_TIMES[0])["dock"]
        before = copy.deepcopy(dock)
        projected = project_cell(dock, 0, 0)
        projected["source_versions"].clear()
        projected["role_path"].append("mutation")
        self.assertEqual(dock, before)

    def test_independent_missing_path_preserves_other_three_cells(self):
        original = build_atlas(self.board)
        self.rows[3]["role_path"] = []
        self.rows[3]["role_id"] = None
        changed = build_atlas(self.board)
        self.assertEqual(changed["cells"][:3], original["cells"][:3])
        self.assertEqual(changed["cells"][3]["role_status"], "unknown")
        self.assertEqual(changed["cells"][3]["role_evidence"], [])

    def test_source_omission_does_not_create_replacement_metadata(self):
        self.rows[3]["source_versions"] = self.rows[3]["source_versions"][:1]
        self.rows[3]["role_path"] = []
        self.rows[3]["role_id"] = None
        result = build_atlas(self.board)
        self.assertEqual(result["cells"][3]["source_versions"], EXPECTED["cells"][0]["source_versions"])
        self.assertNotIn("ROLE-DOCK-SWING", json.dumps(result["cells"][3]))

    def test_projection_does_not_include_unrelated_fields(self):
        dock = self.board(dock_at=EFFECTIVE_TIMES[0], known_at=KNOWN_TIMES[0])["dock"]
        dock["unrelated_restricted_decoy"] = "do-not-leak"
        dock["unchanged_handover"] = {"unrelated": "not-part-of-atlas"}
        result = project_cell(dock, 0, 0)
        self.assertNotIn("unrelated", json.dumps(result))
        self.assertNotIn("do-not-leak", json.dumps(result))

    def test_query_failure_propagates_no_partial_atlas(self):
        calls = []
        def failing(**axes):
            calls.append(axes)
            if len(calls) == 3:
                raise RuntimeError("synthetic database failure")
            return self.board(**axes)
        with self.assertRaises(RuntimeError):
            build_atlas(failing)
        self.assertEqual(len(calls), 3)

    def test_mount_routes_are_namespaced_and_fail_closed(self):
        handler = Mock()
        service = SimpleNamespace(shift_board=self.board)
        self.assertFalse(mount_clock_atlas(handler, service, SimpleNamespace(path="/api/shift-board", query="")))
        handler._send.assert_not_called()
        self.assertTrue(mount_clock_atlas(handler, service, SimpleNamespace(path="/api/clock-atlas", query="scope=restricted")))
        handler._error.assert_called_with(403, "This view is not available.")
        self.assertEqual(self.calls, [])
        mount_clock_atlas(handler, service, SimpleNamespace(path="/api/clock-atlas", query="known_at=bad"))
        self.assertEqual(handler._error.call_args.args[0], 400)
        self.assertEqual(self.calls, [])

    def test_page_validates_selection_without_querying_graph(self):
        handler = Mock()
        service = SimpleNamespace(shift_board=self.board)
        mount_clock_atlas(handler, service, SimpleNamespace(path="/clocks", query=""))
        self.assertEqual(handler._send.call_args.args[0], 200)
        self.assertIn("Ownership clock atlas", handler._send.call_args.args[1])
        self.assertEqual(self.calls, [])
        handler.reset_mock()
        mount_clock_atlas(handler, service, SimpleNamespace(path="/clocks", query="known_at=bad"))
        handler._send.assert_not_called()
        self.assertEqual(handler._error.call_args.args[0], 400)
        self.assertEqual(self.calls, [])

    def test_mount_database_error_is_generic_and_no_partial_data(self):
        handler = Mock()
        service = SimpleNamespace(shift_board=Mock(side_effect=RuntimeError("private failure detail")))
        mount_clock_atlas(handler, service, SimpleNamespace(path="/api/clock-atlas", query=""))
        handler._send.assert_not_called()
        self.assertEqual(handler._error.call_args.args[0], 503)
        self.assertNotIn("private failure", handler._error.call_args.args[1])

    def test_callback_value_and_permission_errors_never_become_validation_details(self):
        for exception in (ValueError, PermissionError):
            with self.subTest(exception=exception):
                handler = Mock()
                service = SimpleNamespace(shift_board=Mock(side_effect=exception("restricted-source-tripwire")))
                mount_clock_atlas(handler, service, SimpleNamespace(path="/api/clock-atlas", query=""))
                handler._send.assert_not_called()
                self.assertEqual(handler._error.call_args.args, (503, "Clock atlas graph evidence is unavailable. Retry the read-only query."))


if __name__ == "__main__":
    unittest.main()

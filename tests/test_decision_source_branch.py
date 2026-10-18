"""Focused checks for the additive source-observation graph branch."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from wardatlas_source_branch import (  # noqa: E402
    ACTUAL_SOURCE_OBSERVED_AT,
    ASSUMED_SOURCE_OBSERVED_AT,
    BRANCH_EDGES,
    BRANCH_ID,
    BRANCH_NODES,
    BRANCH_PATH_QUERY,
    OVERRIDE_ID,
    SOURCE_ID,
    branch_v3_claim_query,
    disconnected_decoy_branch_queries,
    restricted_source_branch_queries,
    source_branch_response,
    source_branch_seed_queries,
    unknown_branch_projection,
)


class DecisionSourceBranchTests(unittest.TestCase):
    def test_fixture_is_additive_synthetic_and_keeps_source_fact_untouched(self):
        self.assertEqual([item["label"] for item in BRANCH_NODES], ["ScenarioBranch", "ObservationOverride"])
        self.assertEqual(
            [(item["source"], item["kind"], item["target"]) for item in BRANCH_EDGES],
            [
                (BRANCH_ID, "HAS_HYPOTHETICAL_OBSERVATION", OVERRIDE_ID),
                (OVERRIDE_ID, "FOR_SOURCE", SOURCE_ID),
            ],
        )
        override = BRANCH_NODES[1]["properties"]
        self.assertEqual(override["assumed_observed_at"], ASSUMED_SOURCE_OBSERVED_AT)
        self.assertEqual(ASSUMED_SOURCE_OBSERVED_AT, "2026-10-14T16:06:00Z")
        self.assertEqual(ACTUAL_SOURCE_OBSERVED_AT, "2026-10-14T16:04:00Z")
        self.assertNotIn("observed_at", override)
        self.assertNotIn(SOURCE_ID, {row["id"] for row in BRANCH_NODES})
        self.assertTrue(all(row["properties"]["access"] == "operations" for row in BRANCH_NODES))
        self.assertTrue(all(row["properties"]["access"] == "operations" for row in BRANCH_EDGES))
        self.assertEqual(len(source_branch_seed_queries()), 4)

    def test_path_and_branch_claim_queries_require_graph_edges_and_scope(self):
        for token in (
            "ScenarioBranch", "HAS_HYPOTHETICAL_OBSERVATION", "ObservationOverride",
            "FOR_SOURCE", "Evidence", "branch.access = 'operations'",
            "select.access = 'operations'", "override.access = 'operations'",
            "for_source.access = 'operations'", "source.access = 'operations'",
        ):
            self.assertIn(token, BRANCH_PATH_QUERY)
        query = branch_v3_claim_query("2026-10-14T16:05:00Z")
        self.assertIn("override.assumed_observed_at <= \"2026-10-14T16:05:00Z\"", query)
        self.assertIn("override.assumed_observed_at AS source_observed_at", query)
        self.assertIn("branch_source.id = source.id", query)
        self.assertIn("supports.access = 'operations'", query)
        self.assertNotIn("SET ", query.upper())
        self.assertNotIn("source.observed_at <=", query)

    def test_missing_branch_projection_is_unknown_without_mainline_fallback(self):
        cold = unknown_branch_projection(None)
        self.assertEqual(cold["known_at"], None)
        self.assertEqual(cold["claim_context_state"], "unknown")
        self.assertEqual(cold["claims"], [])
        self.assertIsNone(cold["decision_record"])
        self.assertTrue(cold["generic_unknown"])
        self.assertFalse(cold["negative_conclusion_asserted"])
        self.assertFalse(cold["current_owner_or_procedure_selected"])
        self.assertFalse(cold["contact_or_action_supplied"])
        missing = unknown_branch_projection("2026-10-14T16:05:00Z")
        self.assertEqual(missing["claim_context_state"], "incomplete/unknown")
        self.assertEqual(missing["claims"], [])
        self.assertIsNone(missing["decision_record"])
        self.assertTrue(missing["generic_unknown"])

    def test_missing_path_hides_scenario_identity_while_valid_path_projects_assumption(self):
        mainline = {"known_at": None, "generic_unknown": True}
        missing = source_branch_response(mainline, None, [], None)
        self.assertEqual(set(missing), {"scope", "mainline", "hypothetical_branch"})
        self.assertEqual(missing["hypothetical_branch"], {"projection": unknown_branch_projection(None)})
        self.assertNotIn(BRANCH_ID, str(missing))
        self.assertNotIn(OVERRIDE_ID, str(missing))
        valid = source_branch_response(
            mainline,
            "2026-10-14T16:05:00Z",
            [{"label": "Hypothetical observation-time branch", "assumed_observed_at": ASSUMED_SOURCE_OBSERVED_AT,
              "actual_source_observed_at": ACTUAL_SOURCE_OBSERVED_AT, "branch_id": BRANCH_ID, "override_id": OVERRIDE_ID}],
            {"known_at": "2026-10-14T16:05:00Z", "claims": []},
        )
        self.assertEqual(set(valid["hypothetical_branch"]), {"scenario", "projection"})
        self.assertEqual(valid["hypothetical_branch"]["scenario"], {
            "label": "Hypothetical observation-time branch",
            "assumed_observed_at": "2026-10-14T16:06:00Z",
            "actual_source_observed_at": "2026-10-14T16:04:00Z",
            "source_fact_modified": False,
            "scenario_not_source_truth": True,
        })
        self.assertNotIn(BRANCH_ID, str(valid))
        self.assertNotIn(OVERRIDE_ID, str(valid))

    def test_test_only_decoy_and_restricted_paths_are_isolated_and_acl_tagged(self):
        decoy = "\n".join(disconnected_decoy_branch_queries())
        self.assertIn("BR-SOP-DECOY-91", decoy)
        self.assertIn("OBS-SOP-DECOY-91", decoy)
        self.assertIn("SRC-SOP-DECOY-91", decoy)
        self.assertNotIn(BRANCH_ID, decoy)
        restricted = "\n".join(restricted_source_branch_queries())
        for token in (
            "BR-RESTRICTED-OPS-77", "OBS-RESTRICTED-OPS-77", "SRC-RESTRICTED-OPS-77",
            "Restricted synthetic source observation canary 77", "ROLE-RESTRICTED-OPS-77",
            "REL-BR-RESTRICTED-OPS-77", "access = \"restricted\"",
        ):
            self.assertIn(token, restricted)


if __name__ == "__main__":
    unittest.main()

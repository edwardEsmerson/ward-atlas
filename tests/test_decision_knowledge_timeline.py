"""Focused unit checks for source-time query separation and projection semantics."""

from __future__ import annotations

import unittest

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from wardatlas_knowledge_timeline import (  # noqa: E402
    build_queries,
    considered_query,
    project_timeline,
)


def claim(version: str, role: str, source: str, title: str, observed: str) -> dict:
    return {
        "version_id": version,
        "role_id": role,
        "source_id": source,
        "source_title": title,
        "source_observed_at": observed,
        "equipment_id": "CONVEYOR-C4",
    }


def record() -> dict:
    return {
        "register_id": "REGISTER-OPS-DECISIONS-01",
        "decision_id": "DEC-OPS-9-REVIEW-01",
        "decision_title": "SOP-OPS-9 claim reconciliation review",
        "recorded_state": "open-unresolved",
        "statement": "No version is selected as authoritative in this synthetic record.",
        "source_id": "SRC-DEC-OPS-9-REVIEW-01",
        "source_title": "Synthetic Decision Register: SOP-OPS-9 Reconciliation",
        "source_observed_at": "2026-10-14T16:05:00Z",
    }


V2 = claim("SOP-OPS-9-V2", "ROLE-FACILITIES-DUTY", "SRC-SOP-CURRENT-V2", "Synthetic SOP-OPS-9 Current Text v2", "2026-10-14T16:00:00Z")
V3 = claim("SOP-OPS-9-V3", "ROLE-MATERIALS-LEAD", "SRC-SOP-CURRENT-V3", "Synthetic SOP-OPS-9 Current Text v3", "2026-10-14T16:04:00Z")
CONFLICT = [{"from_id": "SOP-OPS-9-V2", "to_id": "SOP-OPS-9-V3", "status": "unresolved"}]
CONSIDERED = [
    {"decision_id": "DEC-OPS-9-REVIEW-01", "version_id": "SOP-OPS-9-V2"},
    {"decision_id": "DEC-OPS-9-REVIEW-01", "version_id": "SOP-OPS-9-V3"},
]


class DecisionKnowledgeTimelineTests(unittest.TestCase):
    def test_explicit_offsets_normalize_and_bad_or_naive_values_reject(self):
        _, _, _, normalized = build_queries("2026-10-14T18:05:00+02:00")
        self.assertEqual(normalized, "2026-10-14T16:05:00Z")
        for value in ("not-a-time", "2026-10-14T16:05:00", "2026-02-30T16:05:00Z"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                build_queries(value)

    def test_claim_discovery_is_sop_impact_only_and_cutoff_is_in_query(self):
        claim_query, record_query, conflict_query, normalized = build_queries("2026-10-14T16:05:00Z")
        self.assertEqual(normalized, "2026-10-14T16:05:00Z")
        self.assertIn("Incident {id: 'INC-204'}", claim_query)
        self.assertIn("APPLIES_TO", claim_query)
        self.assertIn("CLAIMS_ROLE", claim_query)
        self.assertIn("EVIDENCED_BY", claim_query)
        self.assertIn("source.observed_at <=", claim_query)
        self.assertNotIn("DecisionRecord", claim_query)
        self.assertNotIn("DecisionRegister", claim_query)
        self.assertNotIn("CONSIDERS", claim_query)
        self.assertIn("DecisionRegister", record_query)
        self.assertIn("TRACKS", record_query)
        self.assertIn("evidence:EVIDENCED_BY", record_query)
        self.assertIn("source.observed_at <=", record_query)
        self.assertIn("source2.observed_at <=", conflict_query)
        self.assertIn("source3.observed_at <=", conflict_query)
        considered = considered_query("DEC-OPS-9-REVIEW-01", normalized)
        self.assertIn("record_evidence:EVIDENCED_BY", considered)
        self.assertIn("considered:CONSIDERS", considered)
        self.assertIn("source.observed_at <=", considered)

    def test_cold_cutoff_is_unknown_without_using_current_clock(self):
        self.assertEqual(project_timeline(None, [], [], [], []), {
            "known_at": None,
            "claim_context_state": "unknown",
            "claims": [],
            "conflict": None,
            "decision_record": None,
            "decision_considers_edges": [],
            "generic_unknown": True,
            "negative_conclusion_asserted": False,
            "current_owner_or_procedure_selected": False,
            "contact_or_action_supplied": False,
        })
        queries = build_queries(None)
        self.assertTrue(all("0001-01-01T00:00:00Z" in query for query in queries[:3]))

    def test_before_record_claims_are_not_discovered_from_considered_links(self):
        projection = project_timeline(
            "2026-10-14T16:04:00Z", [V2, V3], [], CONSIDERED, CONFLICT
        )
        self.assertEqual([item["version_id"] for item in projection["claims"]], ["SOP-OPS-9-V2", "SOP-OPS-9-V3"])
        self.assertEqual(projection["decision_record"], None)
        self.assertEqual(projection["decision_considers_edges"], [])
        self.assertEqual(projection["conflict"]["epistemic_type"], "graph-derived relationship")

    def test_shifted_future_claim_is_suppressed_but_record_and_v2_remain(self):
        projection = project_timeline(
            "2026-10-14T16:05:00Z",
            [V2], [record()], [*CONSIDERED], [],
        )
        text = str(projection)
        self.assertEqual(projection["claim_context_state"], "incomplete/unknown")
        self.assertEqual([item["version_id"] for item in projection["claims"]], ["SOP-OPS-9-V2"])
        self.assertEqual(projection["decision_record"]["recorded_state"], "open-unresolved")
        self.assertEqual(projection["decision_considers_edges"], [
            {"from": "DEC-OPS-9-REVIEW-01", "relationship": "CONSIDERS", "to": "SOP-OPS-9-V2"}
        ])
        self.assertIsNone(projection["conflict"])
        for forbidden in ("SOP-OPS-9-V3", "ROLE-MATERIALS-LEAD", "SRC-SOP-CURRENT-V3"):
            self.assertNotIn(forbidden, text)

    def test_missing_record_evidence_does_not_erase_independently_known_claims(self):
        projection = project_timeline(
            "2026-10-14T16:05:00Z", [V2, V3], [], [], CONFLICT
        )
        self.assertEqual(len(projection["claims"]), 2)
        self.assertIsNotNone(projection["conflict"])
        self.assertIsNone(projection["decision_record"])
        self.assertEqual(projection["decision_considers_edges"], [])
        self.assertFalse(projection["generic_unknown"])

    def test_fully_source_backed_projection_includes_only_visible_considered_links(self):
        projection = project_timeline(
            "2026-10-14T16:05:00Z", [V2, V3], [record()], [*CONSIDERED], CONFLICT
        )
        self.assertEqual(projection["claim_context_state"], "evidence-backed")
        self.assertEqual(projection["decision_record"]["provenance_path"], [
            {"from": "REGISTER-OPS-DECISIONS-01", "relationship": "TRACKS", "to": "DEC-OPS-9-REVIEW-01"},
            {"from": "DEC-OPS-9-REVIEW-01", "relationship": "EVIDENCED_BY", "to": "SRC-DEC-OPS-9-REVIEW-01"},
        ])
        self.assertEqual([item["to"] for item in projection["decision_considers_edges"]], ["SOP-OPS-9-V2", "SOP-OPS-9-V3"])
        self.assertFalse(projection["current_owner_or_procedure_selected"])
        self.assertFalse(projection["contact_or_action_supplied"])


if __name__ == "__main__":
    unittest.main()

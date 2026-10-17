"""Focused projection/query tests for the read-only decision docket."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from wardatlas_decisions import (  # noqa: E402
    RECORDS_QUERY,
    claims_query,
    conflict_query,
    considered_query,
    decision_seed_queries,
    project_docket,
    restricted_decision_queries,
    unknown_response,
)


def record_row() -> dict:
    return {
        "root_id": "REGISTER-OPS-DECISIONS-01",
        "tracking_edge_id": "REL-REGISTER-OPS-DECISIONS-01-TRACKS-DEC-OPS-9-REVIEW-01",
        "decision_id": "DEC-OPS-9-REVIEW-01",
        "decision_title": "SOP-OPS-9 claim reconciliation review",
        "recorded_state": "open-unresolved",
        "statement": "No version is selected as authoritative in this synthetic record.",
        "record_source_id": "SRC-DEC-OPS-9-REVIEW-01",
        "record_source_title": "Synthetic Decision Register: SOP-OPS-9 Reconciliation",
        "record_source_observed_at": "2026-10-14T16:05:00Z",
        "record_evidence_edge_id": "REL-DEC-OPS-9-REVIEW-01-EVIDENCE",
    }


def considered_rows(include_v2: bool = True) -> list[dict]:
    rows = []
    for version in (["SOP-OPS-9-V2", "SOP-OPS-9-V3"] if include_v2 else ["SOP-OPS-9-V3"]):
        rows.append({"decision_id": "DEC-OPS-9-REVIEW-01", "edge_id": f"REL-DEC-OPS-9-CONSIDERS-{version[-2:]}", "version_id": version})
    return rows


def claim_row(version: str) -> dict:
    if version == "SOP-OPS-9-V2":
        role, source_id, title, observed = "ROLE-FACILITIES-DUTY", "SRC-SOP-CURRENT-V2", "Synthetic SOP-OPS-9 Current Text v2", "2026-10-14T16:00:00Z"
    else:
        role, source_id, title, observed = "ROLE-MATERIALS-LEAD", "SRC-SOP-CURRENT-V3", "Synthetic SOP-OPS-9 Current Text v3", "2026-10-14T16:04:00Z"
    return {
        "version_id": version,
        "claim_edge_id": "claim-" + version,
        "role_id": role,
        "source_id": source_id,
        "source_title": title,
        "source_observed_at": observed,
        "evidence_edge_id": "evidence-" + version,
    }


def conflict_rows() -> list[dict]:
    return [{"edge_id": "REL-SOP-9-CONFLICT", "from_id": "SOP-OPS-9-V2", "to_id": "SOP-OPS-9-V3", "status": "unresolved"}]


class DecisionDocketProjectionTests(unittest.TestCase):
    def project(self, *, considered=None, claims=None, conflicts=None, records=None):
        records = [record_row()] if records is None else records
        queries: list[str] = []

        def query(text: str):
            queries.append(text)
            if "SOP_CONFLICT" in text:
                return conflicts if conflicts is not None else conflict_rows()
            if "CLAIMS_ROLE" in text:
                return claims if claims is not None else [claim_row("SOP-OPS-9-V2"), claim_row("SOP-OPS-9-V3")]
            if "CONSIDERS" in text:
                return considered if considered is not None else considered_rows()
            self.fail(f"unexpected docket query: {text}")

        result = project_docket(records, query)
        return result, queries

    def test_empty_and_missing_record_citation_are_same_generic_unknown(self):
        empty, _ = self.project(records=[])
        missing_link, _ = self.project(records=[])
        self.assertEqual(empty, unknown_response())
        self.assertEqual(missing_link, empty)
        self.assertNotIn("DEC-OPS-9-REVIEW-01", str(empty))
        self.assertNotIn("docket_row_count", empty)

    def test_exact_source_backed_baseline_preserves_edge_provenance_and_uncertainty(self):
        result, queries = self.project()
        row = result["docket_rows"][0]
        self.assertEqual(result["root_id"], "REGISTER-OPS-DECISIONS-01")
        self.assertEqual(result["root_tracking_edge"], {"from": "REGISTER-OPS-DECISIONS-01", "relationship": "TRACKS", "to": "DEC-OPS-9-REVIEW-01"})
        self.assertEqual(row["state"], "open-unresolved")
        self.assertEqual(row["source_order"], ["SRC-DEC-OPS-9-REVIEW-01", "SRC-SOP-CURRENT-V2", "SRC-SOP-CURRENT-V3"])
        self.assertEqual([claim["source"]["observed_at"] for claim in row["claims"]], ["2026-10-14T16:00:00Z", "2026-10-14T16:04:00Z"])
        self.assertEqual(row["decision_record"]["record_source"]["evidence_link"], {"from": "DEC-OPS-9-REVIEW-01", "relationship": "EVIDENCED_BY", "to": "SRC-DEC-OPS-9-REVIEW-01"})
        self.assertEqual(row["conflict_edge"]["status"], "unresolved")
        self.assertTrue(row["complete_two_claim_witness_present"])
        self.assertIsNone(row["selected_version"])
        self.assertFalse(result["current_owner_selected"])
        self.assertFalse(any("COUNT" in query.upper() for query in queries))

    def test_missing_considered_edge_keeps_conflict_unknown_not_promoted(self):
        result, _ = self.project(considered=considered_rows(include_v2=False), claims=[claim_row("SOP-OPS-9-V3")])
        row = result["docket_rows"][0]
        self.assertEqual(result["state"], "incomplete/unknown")
        self.assertEqual(row["state"], "incomplete/unknown")
        self.assertFalse(row["complete_two_claim_witness_present"])
        self.assertEqual(row["conflict_edge"]["status"], "unresolved")
        self.assertIsNone(row["selected_version"])
        self.assertEqual([claim["version_id"] for claim in row["claims"]], ["SOP-OPS-9-V3"])

    def test_missing_v2_evidence_never_reconstructs_claim_from_properties(self):
        result, _ = self.project(claims=[claim_row("SOP-OPS-9-V3")])
        row = result["docket_rows"][0]
        self.assertEqual({claim["version_id"] for claim in row["claims"]}, {"SOP-OPS-9-V3"})
        self.assertEqual(row["state"], "incomplete/unknown")
        self.assertEqual(row["conflict_edge"]["status"], "unresolved")
        self.assertIsNone(row["selected_version"])

    def test_query_contract_is_register_rooted_edge_and_acl_filtered(self):
        for query in (RECORDS_QUERY, considered_query("DEC-OPS-9-REVIEW-01"), claims_query("DEC-OPS-9-REVIEW-01"), conflict_query("DEC-OPS-9-REVIEW-01")):
            self.assertIn("access = 'operations'", query)
        self.assertIn("DecisionRegister {id: \"REGISTER-OPS-DECISIONS-01\"}", RECORDS_QUERY)
        self.assertIn("EVIDENCED_BY", RECORDS_QUERY)
        self.assertIn("EVIDENCED_BY", claims_query("DEC-OPS-9-REVIEW-01"))
        self.assertIn("CLAIMS_ROLE", claims_query("DEC-OPS-9-REVIEW-01"))
        hidden = "\n".join(restricted_decision_queries())
        self.assertIn("DEC-RESTRICTED-OPS-77", hidden)
        self.assertIn("access = \"restricted\"", hidden)

    def test_fixture_load_is_mergebased_and_restricted_fixture_stays_additive(self):
        statements = decision_seed_queries()
        self.assertTrue(statements)
        self.assertTrue(all("MERGE" in statement for statement in statements))
        self.assertIn("DEC-OPS-9-CONSIDERS-V2", "\n".join(statements))
        self.assertIn("DEC-OPS-9-CONSIDERS-V3", "\n".join(statements))


if __name__ == "__main__":
    unittest.main()

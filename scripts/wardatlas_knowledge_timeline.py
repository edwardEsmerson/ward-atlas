"""Read-only source-observation-time replay for the synthetic SOP docket.

The two retrieval roots stay independent: SOP claims come only from the
existing SOP-impact path, while recorded decision state begins at its
register/TRACKS/own-EVIDENCED_BY path. No decision link is used to discover an
earlier claim.
"""

from __future__ import annotations

from typing import Any

from wardatlas_decisions import DECISION_ID, REGISTER_ID
from wardatlas_fixtures import cypher_string
from wardatlas_shift_board import normalize_timestamp


_NO_CUTOFF = "0001-01-01T00:00:00Z"


def _claim_query(known_at: str) -> str:
    cutoff = cypher_string(known_at)
    # This is the accepted SOP-impact discovery path, with the observation
    # cutoff added to the source edge. It deliberately has no DecisionRecord,
    # DecisionRegister, or CONSIDERS traversal.
    return f"""
MATCH (i:Incident {{id: 'INC-204'}})-[aff:AFFECTS]->(e:Equipment {{id: 'CONVEYOR-C4'}})-[sup:SUPPORTS]->(s:Service {{id: 'SVC-MATERIAL-TRANSFER'}}),
      (v:SOPVersion)-[applies:APPLIES_TO]->(e),
      (v)-[claim:CLAIMS_ROLE]->(role:Role),
      (v)-[evidence:EVIDENCED_BY]->(source:Evidence)
WHERE i.access = 'operations' AND e.access = 'operations' AND s.access = 'operations'
  AND v.access = 'operations' AND role.access = 'operations' AND source.access = 'operations'
  AND aff.access = 'operations' AND sup.access = 'operations' AND applies.access = 'operations'
  AND claim.access = 'operations' AND evidence.access = 'operations'
  AND source.observed_at <= {cutoff}
RETURN v.id AS version_id, claim.id AS claim_edge_id, role.id AS role_id,
       applies.id AS applicability_edge_id, e.id AS equipment_id,
       source.id AS source_id, source.title AS source_title,
       source.observed_at AS source_observed_at,
       evidence.id AS evidence_edge_id
ORDER BY v.id, role.id
""".strip()


def _record_query(known_at: str) -> str:
    cutoff = cypher_string(known_at)
    # No DecisionRecord facts can escape without the register root and its own
    # source edge. This query is called independently of claim discovery.
    return f"""
MATCH (root:DecisionRegister {{id: {cypher_string(REGISTER_ID)}}})-[tracks:TRACKS]->(record:DecisionRecord {{id: {cypher_string(DECISION_ID)}}})-[evidence:EVIDENCED_BY]->(source:Evidence)
WHERE root.access = 'operations' AND tracks.access = 'operations'
  AND record.access = 'operations' AND evidence.access = 'operations' AND source.access = 'operations'
  AND source.observed_at <= {cutoff}
RETURN root.id AS register_id, tracks.id AS tracks_edge_id,
       record.id AS decision_id, record.title AS decision_title,
       record.recorded_state AS recorded_state, record.statement AS statement,
       source.id AS source_id, source.title AS source_title,
       source.observed_at AS source_observed_at, evidence.id AS evidence_edge_id
ORDER BY record.id
""".strip()


def _considered_query(record_id: str, known_at: str) -> str:
    cutoff = cypher_string(known_at)
    return f"""
MATCH (root:DecisionRegister {{id: {cypher_string(REGISTER_ID)}}})-[tracks:TRACKS]->(record:DecisionRecord {{id: {cypher_string(record_id)}}})-[record_evidence:EVIDENCED_BY]->(record_source:Evidence),
      (record)-[considered:CONSIDERS]->(version:SOPVersion)-[applies:APPLIES_TO]->(equipment:Equipment {{id: 'CONVEYOR-C4'}}),
      (version)-[claim:CLAIMS_ROLE]->(role:Role), (version)-[source_evidence:EVIDENCED_BY]->(source:Evidence),
      (incident:Incident {{id: 'INC-204'}})-[aff:AFFECTS]->(equipment)-[supports:SUPPORTS]->(service:Service {{id: 'SVC-MATERIAL-TRANSFER'}})
WHERE root.access = 'operations' AND tracks.access = 'operations'
  AND record.access = 'operations' AND record_evidence.access = 'operations'
  AND record_source.access = 'operations' AND record_source.observed_at <= {cutoff}
  AND considered.access = 'operations' AND version.access = 'operations' AND applies.access = 'operations'
  AND claim.access = 'operations' AND role.access = 'operations'
  AND source_evidence.access = 'operations' AND source.access = 'operations' AND source.observed_at <= {cutoff}
  AND incident.access = 'operations' AND aff.access = 'operations'
  AND equipment.access = 'operations' AND supports.access = 'operations' AND service.access = 'operations'
RETURN record.id AS decision_id, considered.id AS considered_edge_id, version.id AS version_id
ORDER BY version.id
""".strip()


def _conflict_query(known_at: str) -> str:
    cutoff = cypher_string(known_at)
    # A relationship is projected only when both endpoints have independently
    # source-backed claim paths visible at this cutoff.
    return f"""
MATCH (incident:Incident {{id: 'INC-204'}})-[aff:AFFECTS]->(equipment:Equipment {{id: 'CONVEYOR-C4'}})-[supports:SUPPORTS]->(service:Service {{id: 'SVC-MATERIAL-TRANSFER'}}),
      (v2:SOPVersion {{id: 'SOP-OPS-9-V2'}})-[apply2:APPLIES_TO]->(equipment),
      (v3:SOPVersion {{id: 'SOP-OPS-9-V3'}})-[apply3:APPLIES_TO]->(equipment),
      (v2)-[claim2:CLAIMS_ROLE]->(role2:Role), (v3)-[claim3:CLAIMS_ROLE]->(role3:Role),
      (v2)-[evidence2:EVIDENCED_BY]->(source2:Evidence),
      (v3)-[evidence3:EVIDENCED_BY]->(source3:Evidence),
      (v2)-[conflict:SOP_CONFLICT]->(v3)
WHERE incident.access = 'operations' AND equipment.access = 'operations' AND service.access = 'operations'
  AND v2.access = 'operations' AND v3.access = 'operations' AND role2.access = 'operations' AND role3.access = 'operations'
  AND source2.access = 'operations' AND source3.access = 'operations'
  AND aff.access = 'operations' AND supports.access = 'operations'
  AND apply2.access = 'operations' AND apply3.access = 'operations'
  AND claim2.access = 'operations' AND claim3.access = 'operations'
  AND evidence2.access = 'operations' AND evidence3.access = 'operations'
  AND conflict.access = 'operations' AND conflict.status = 'unresolved'
  AND source2.observed_at <= {cutoff} AND source3.observed_at <= {cutoff}
RETURN v2.id AS from_id, v3.id AS to_id, conflict.status AS status
ORDER BY from_id, to_id
""".strip()


def _link(source: str, relationship: str, target: str) -> dict[str, str]:
    return {"from": source, "relationship": relationship, "to": target}


def project_timeline(
    known_at: str | None,
    claim_rows: list[dict[str, Any]],
    record_rows: list[dict[str, Any]],
    considered_rows: list[dict[str, Any]],
    conflict_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    """Build the public as-known projection from already access/time-filtered rows."""
    claims = [
        {
            "version_id": row["version_id"],
            "claim_edge": _link(row["version_id"], "CLAIMS_ROLE", row["role_id"]),
            "role_id": row["role_id"],
            "claim_label": "unverified SOP claim",
            "source": {
                "id": row["source_id"],
                "title": row["source_title"],
                "observed_at": row["source_observed_at"],
                "evidence_link": _link(row["version_id"], "EVIDENCED_BY", row["source_id"]),
            },
            "applicability_edge": _link(row["version_id"], "APPLIES_TO", row["equipment_id"]),
            "discovery_path": "existing SOP-impact graph path",
        }
        for row in claim_rows
    ]
    claims.sort(key=lambda row: (row["version_id"], row["role_id"]))
    visible_claim_ids = {row["version_id"] for row in claims}

    decision_record = None
    decision_considers_edges: list[dict[str, str]] = []
    if record_rows:
        row = record_rows[0]
        record_id = row["decision_id"]
        record_source = row["source_id"]
        decision_record = {
            "id": record_id,
            "title": row["decision_title"],
            "recorded_state": row["recorded_state"],
            "recorded_statement": row["statement"],
            "register_id": row["register_id"],
            "register_edge": _link(row["register_id"], "TRACKS", record_id),
            "source": {
                "id": record_source,
                "title": row["source_title"],
                "observed_at": row["source_observed_at"],
                "evidence_link": _link(record_id, "EVIDENCED_BY", record_source),
            },
            "provenance_path": [
                _link(row["register_id"], "TRACKS", record_id),
                _link(record_id, "EVIDENCED_BY", record_source),
            ],
        }
        decision_considers_edges = [
            _link(record_id, "CONSIDERS", item["version_id"])
            for item in considered_rows
            if item["decision_id"] == record_id and item["version_id"] in visible_claim_ids
        ]
        decision_considers_edges.sort(key=lambda edge: edge["to"])

    conflict = None
    if visible_claim_ids >= {"SOP-OPS-9-V2", "SOP-OPS-9-V3"} and conflict_rows:
        row = conflict_rows[0]
        conflict = {
            "edge": {**_link(row["from_id"], "SOP_CONFLICT", row["to_id"]), "status": row["status"]},
            "epistemic_type": "graph-derived relationship",
            "source_quote": False,
            "authoritative_rule": False,
        }

    if not claims:
        claim_context_state = "unknown"
    elif len(visible_claim_ids) >= 2:
        claim_context_state = "evidence-backed"
    elif decision_record is not None:
        # The record itself is known, but the independently source-backed
        # claim context is incomplete. Do not expose the suppressed link.
        claim_context_state = "incomplete/unknown"
    else:
        claim_context_state = "partial/evidence-backed"

    return {
        "known_at": known_at,
        "claim_context_state": claim_context_state,
        "claims": claims,
        "conflict": conflict,
        "decision_record": decision_record,
        "decision_considers_edges": decision_considers_edges,
        "generic_unknown": not claims and decision_record is None,
        "negative_conclusion_asserted": False,
        "current_owner_or_procedure_selected": False,
        "contact_or_action_supplied": False,
    }


def build_queries(known_at: str | None) -> tuple[str, str, str, str | None]:
    """Validate a caller cutoff and return the independent read statements."""
    normalized = normalize_timestamp(known_at) if known_at is not None else None
    cutoff = normalized or _NO_CUTOFF
    claim = _claim_query(cutoff)
    record = _record_query(cutoff)
    conflict = _conflict_query(cutoff)
    # considered is intentionally deferred until the own-source record query
    # has returned at least one register-rooted row.
    return claim, record, conflict, normalized


def considered_query(record_id: str, known_at: str) -> str:
    return _considered_query(record_id, known_at)

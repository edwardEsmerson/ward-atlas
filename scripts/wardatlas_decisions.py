"""Register-rooted, source-backed synthetic decision docket for WardAtlas.

The docket is a read-only projection over actual FalkorDB nodes and explicit
relationships. It never turns a missing relationship into a negative fact or
chooses a disputed SOP version.
"""

from __future__ import annotations

from typing import Any, Callable

from wardatlas_fixtures import cypher_string, edge, node, seed_queries_for


REGISTER_ID = "REGISTER-OPS-DECISIONS-01"
DECISION_ID = "DEC-OPS-9-REVIEW-01"
RECORD_SOURCE_ID = "SRC-DEC-OPS-9-REVIEW-01"


DECISION_NODES = [
    node("DecisionRegister", REGISTER_ID, title="Synthetic Operations Decision Register", access="operations"),
    node(
        "DecisionRecord",
        DECISION_ID,
        title="SOP-OPS-9 claim reconciliation review",
        recorded_state="open-unresolved",
        statement="No version is selected as authoritative in this synthetic record.",
        access="operations",
    ),
    node(
        "Evidence",
        RECORD_SOURCE_ID,
        title="Synthetic Decision Register: SOP-OPS-9 Reconciliation",
        observed_at="2026-10-14T16:05:00Z",
        access="operations",
    ),
]

DECISION_EDGES = [
    edge(REGISTER_ID, "TRACKS", DECISION_ID, "REL-REGISTER-OPS-DECISIONS-01-TRACKS-DEC-OPS-9-REVIEW-01", access="operations"),
    edge(DECISION_ID, "EVIDENCED_BY", RECORD_SOURCE_ID, "REL-DEC-OPS-9-REVIEW-01-EVIDENCE", access="operations"),
    edge(DECISION_ID, "CONSIDERS", "SOP-OPS-9-V2", "REL-DEC-OPS-9-CONSIDERS-V2", access="operations"),
    edge(DECISION_ID, "CONSIDERS", "SOP-OPS-9-V3", "REL-DEC-OPS-9-CONSIDERS-V3", access="operations"),
]


def decision_seed_queries() -> list[str]:
    """Idempotently seed only the additive decision-register fixture."""
    return seed_queries_for(DECISION_NODES, DECISION_EDGES)


def restricted_decision_queries() -> list[str]:
    """Build a connected, ACL-denied adversarial branch for tests only."""
    hidden_nodes = [
        node("DecisionRecord", "DEC-RESTRICTED-OPS-77", title="Restricted Decision Docket Canary 77", recorded_state="open-unresolved", statement="Restricted-only docket note: north annex diversion remains unverified.", access="restricted"),
        node("SOPVersion", "SOP-RESTRICTED-77", sop_id="SOP-OPS-9", access="restricted"),
        node("Role", "ROLE-RESTRICTED-OPS-77", access="restricted"),
        node("Evidence", "SRC-RESTRICTED-OPS-77", title="Restricted Synthetic Decision Source 77", observed_at="2026-10-14T16:06:00Z", body="Restricted-only docket note: north annex diversion remains unverified.", access="restricted"),
    ]
    hidden_edges = [
        edge(REGISTER_ID, "TRACKS", "DEC-RESTRICTED-OPS-77", "REL-REGISTER-RESTRICTED-77", access="restricted"),
        edge("DEC-RESTRICTED-OPS-77", "EVIDENCED_BY", "SRC-RESTRICTED-OPS-77", "REL-RESTRICTED-77-EVIDENCE", access="restricted"),
        edge("DEC-RESTRICTED-OPS-77", "CONSIDERS", "SOP-RESTRICTED-77", "REL-RESTRICTED-77-CONSIDERS", access="restricted"),
        edge("SOP-RESTRICTED-77", "CLAIMS_ROLE", "ROLE-RESTRICTED-OPS-77", "REL-RESTRICTED-77-CLAIMS", access="restricted"),
        edge("SOP-RESTRICTED-77", "EVIDENCED_BY", "SRC-RESTRICTED-OPS-77", "REL-RESTRICTED-77-SOP-EVIDENCE", access="restricted"),
    ]
    return seed_queries_for(hidden_nodes, hidden_edges)


def disconnected_decoy_queries() -> list[str]:
    """Add a complete but root-disconnected synthetic decision component."""
    nodes = [
        node("DecisionRecord", "DEC-OPS-9-DECOY-91", title="Disconnected decoy decision", recorded_state="open-unresolved", statement="Not tracked by the operations register.", access="operations"),
        node("SOPVersion", "SOP-OPS-9-DECOY-91", sop_id="SOP-OPS-9", access="operations"),
        node("Role", "ROLE-OPS-9-DECOY-91", access="operations"),
        node("Evidence", "SRC-OPS-9-DECOY-91", title="Disconnected decoy source", observed_at="2026-10-14T16:07:00Z", access="operations"),
    ]
    edges = [
        edge("DEC-OPS-9-DECOY-91", "EVIDENCED_BY", "SRC-OPS-9-DECOY-91", "REL-DECOY-91-EVIDENCE", access="operations"),
        edge("DEC-OPS-9-DECOY-91", "CONSIDERS", "SOP-OPS-9-DECOY-91", "REL-DECOY-91-CONSIDERS", access="operations"),
        edge("SOP-OPS-9-DECOY-91", "CLAIMS_ROLE", "ROLE-OPS-9-DECOY-91", "REL-DECOY-91-CLAIMS", access="operations"),
        edge("SOP-OPS-9-DECOY-91", "EVIDENCED_BY", "SRC-OPS-9-DECOY-91", "REL-DECOY-91-SOP-EVIDENCE", access="operations"),
    ]
    return seed_queries_for(nodes, edges)


RECORDS_QUERY = f"""
MATCH (root:DecisionRegister {{id: {cypher_string(REGISTER_ID)}}})-[tracks:TRACKS]->(record:DecisionRecord)-[record_evidence:EVIDENCED_BY]->(record_source:Evidence)
WHERE root.access = 'operations' AND tracks.access = 'operations'
  AND record.access = 'operations' AND record_evidence.access = 'operations'
  AND record_source.access = 'operations'
RETURN root.id AS root_id, tracks.id AS tracking_edge_id,
       record.id AS decision_id, record.title AS decision_title,
       record.recorded_state AS recorded_state, record.statement AS statement,
       record_source.id AS record_source_id, record_source.title AS record_source_title,
       record_source.observed_at AS record_source_observed_at,
       record_evidence.id AS record_evidence_edge_id
ORDER BY record.id
""".strip()


def _record_anchor(record_id: str) -> str:
    """A graph-originated id is encoded as a Cypher string, never interpolated raw."""
    return (
        f"MATCH (root:DecisionRegister {{id: {cypher_string(REGISTER_ID)}}})"
        f"-[tracks:TRACKS]->(record:DecisionRecord {{id: {cypher_string(record_id)}}})"
        "-[record_evidence:EVIDENCED_BY]->(record_source:Evidence) "
        "WHERE root.access = 'operations' AND tracks.access = 'operations' "
        "AND record.access = 'operations' AND record_evidence.access = 'operations' "
        "AND record_source.access = 'operations' "
    )


def considered_query(record_id: str) -> str:
    return (
        _record_anchor(record_id)
        + "MATCH (record)-[considered:CONSIDERS]->(version:SOPVersion) "
        + "WHERE considered.access = 'operations' AND version.access = 'operations' "
        + "RETURN record.id AS decision_id, considered.id AS edge_id, version.id AS version_id "
        + "ORDER BY version.id"
    )


def claims_query(record_id: str) -> str:
    return (
        _record_anchor(record_id)
        + "MATCH (record)-[considered:CONSIDERS]->(version:SOPVersion)-[claim:CLAIMS_ROLE]->(role:Role), "
        + "(version)-[evidence:EVIDENCED_BY]->(source:Evidence) "
        + "WHERE considered.access = 'operations' AND version.access = 'operations' "
        + "AND claim.access = 'operations' AND role.access = 'operations' "
        + "AND evidence.access = 'operations' AND source.access = 'operations' "
        + "RETURN version.id AS version_id, claim.id AS claim_edge_id, role.id AS role_id, "
        + "source.id AS source_id, source.title AS source_title, source.observed_at AS source_observed_at, "
        + "evidence.id AS evidence_edge_id ORDER BY version.id, role.id"
    )


def conflict_query(record_id: str) -> str:
    # Anchor the edge to either still-considered endpoint. Thus losing one
    # CONSIDERS edge does not turn a known unresolved conflict into “absent”.
    return (
        _record_anchor(record_id)
        + "MATCH (record)-[considered:CONSIDERS]->(visible:SOPVersion), "
        + "(v2:SOPVersion)-[conflict:SOP_CONFLICT]->(v3:SOPVersion) "
        + "WHERE considered.access = 'operations' AND visible.access = 'operations' "
        + "AND v2.access = 'operations' AND v3.access = 'operations' "
        + "AND conflict.access = 'operations' AND (visible.id = v2.id OR visible.id = v3.id) "
        + "RETURN conflict.id AS edge_id, v2.id AS from_id, v3.id AS to_id, conflict.status AS status "
        + "ORDER BY edge_id"
    )


def unknown_response() -> dict[str, Any]:
    """No-record and missing-own-citation responses intentionally share shape."""
    return {
        "scope": "operations",
        "state": "unknown",
        "evidence_state": "no-evidence",
        "docket_rows": [],
        "current_owner_selected": False,
        "procedure_selected": False,
        "contact_supplied": False,
        "action_proposed": False,
        "conflict_resolution_concluded": False,
        "no_decision_concluded": False,
        "unknown_notice": "Decision evidence is unavailable or incomplete; no absence, resolution, or ownership is inferred.",
    }


def project_docket(
    record_rows: list[dict[str, Any]],
    query_rows: Callable[[str], list[dict[str, Any]]],
) -> dict[str, Any]:
    """Shape only register-rooted, source-cited graph rows for an operations viewer."""
    if not record_rows:
        return unknown_response()

    docket_rows: list[dict[str, Any]] = []
    for raw in record_rows:
        record_id = raw["decision_id"]
        considered = query_rows(considered_query(record_id))
        claim_rows = query_rows(claims_query(record_id))
        conflicts = query_rows(conflict_query(record_id))

        considered_edges = [
            {"from": record_id, "relationship": "CONSIDERS", "to": row["version_id"]}
            for row in considered
        ]
        claims = [
            {
                "version_id": row["version_id"],
                "claim_edge": {"from": row["version_id"], "relationship": "CLAIMS_ROLE", "to": row["role_id"]},
                "role_id": row["role_id"],
                "claim_label": "unverified SOP claim",
                "source": {
                    "id": row["source_id"],
                    "title": row["source_title"],
                    "observed_at": row["source_observed_at"],
                    "evidence_link": {"from": row["version_id"], "relationship": "EVIDENCED_BY", "to": row["source_id"]},
                },
                "is_current_owner_fact": False,
            }
            for row in claim_rows
        ]
        conflict = None
        if conflicts:
            row = conflicts[0]
            conflict = {
                "from": row["from_id"],
                "relationship": "SOP_CONFLICT",
                "to": row["to_id"],
                "status": row["status"],
            }

        full_pair = (
            {edge["to"] for edge in considered_edges} >= {"SOP-OPS-9-V2", "SOP-OPS-9-V3"}
            and {claim["version_id"] for claim in claims} >= {"SOP-OPS-9-V2", "SOP-OPS-9-V3"}
            and conflict is not None
            and conflict["status"] == "unresolved"
        )
        decision_record = {
            "id": record_id,
            "title": raw["decision_title"],
            "recorded_state": raw["recorded_state"],
            "recorded_statement": raw["statement"],
            "record_source": {
                "id": raw["record_source_id"],
                "title": raw["record_source_title"],
                "observed_at": raw["record_source_observed_at"],
                "evidence_relationship": "EVIDENCED_BY",
                "evidence_link": {
                    "from": record_id,
                    "relationship": "EVIDENCED_BY",
                    "to": raw["record_source_id"],
                },
            },
        }
        row = {
            "decision_record": decision_record,
            "considered_edges": considered_edges,
            "conflict_edge": conflict,
            "claims": claims,
            "complete_two_claim_witness_present": full_pair,
            "state": "open-unresolved" if full_pair else "incomplete/unknown",
            "source_order": [raw["record_source_id"], *[claim["source"]["id"] for claim in claims]],
            "selected_version": None,
            "current_owner_selected": False,
            "procedure_selected": False,
            "contact_supplied": False,
            "action_proposed": False,
            "conflict_resolution_concluded": False,
        }
        docket_rows.append(row)

    # The register is finite and records are exposed only after their own
    # evidence edge is traversed, so counts disclose visible rows only.
    first = docket_rows[0]
    if len(docket_rows) == 1 and first["state"] == "open-unresolved":
        evidence_state = "source-backed"
        state = "open-unresolved"
    else:
        evidence_state = "partial-evidence"
        state = "incomplete/unknown"
    root_row = record_rows[0]
    return {
        "scope": "operations",
        "state": state,
        "evidence_state": evidence_state,
        "root_id": root_row["root_id"],
        "root_tracking_edge": {
            "from": root_row["root_id"],
            "relationship": "TRACKS",
            "to": root_row["decision_id"],
        },
        "docket_row_count": len(docket_rows),
        "docket_rows": docket_rows,
        "current_owner_selected": False,
        "procedure_selected": False,
        "contact_supplied": False,
        "action_proposed": False,
        "conflict_resolution_concluded": False,
        "no_decision_concluded": False,
    }

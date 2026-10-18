"""Graph-backed hypothetical source-observation replay for WardAtlas.

The branch changes only the knowledge-time projection of one synthetic SOP
source.  The Evidence node remains the mainline fact; a separately linked
ScenarioBranch and ObservationOverride carry the assumption.
"""

from __future__ import annotations

from typing import Any

from wardatlas_fixtures import cypher_string, edge, node, seed_queries_for
from wardatlas_knowledge_timeline import project_timeline


BRANCH_ID = "BR-SOP-V3-LATE-OBS"
OVERRIDE_ID = "OBS-SOP-V3-LATE-OBS"
SOURCE_ID = "SRC-SOP-CURRENT-V3"
ACTUAL_SOURCE_OBSERVED_AT = "2026-10-14T16:04:00Z"
ASSUMED_SOURCE_OBSERVED_AT = "2026-10-14T16:06:00Z"
BRANCH_LABEL = "Hypothetical observation-time branch"
_NO_CUTOFF = "0001-01-01T00:00:00Z"

BRANCH_NODES = [
    node(
        "ScenarioBranch",
        BRANCH_ID,
        branch_label=BRANCH_LABEL,
        access="operations",
        synthetic=True,
    ),
    node(
        "ObservationOverride",
        OVERRIDE_ID,
        assumed_observed_at=ASSUMED_SOURCE_OBSERVED_AT,
        evidence_label="hypothetical observation-time assumption",
        access="operations",
        synthetic=True,
    ),
]

BRANCH_EDGES = [
    edge(
        BRANCH_ID,
        "HAS_HYPOTHETICAL_OBSERVATION",
        OVERRIDE_ID,
        "REL-BR-SOP-V3-LATE-OBS-OVERRIDE",
        access="operations",
    ),
    edge(
        OVERRIDE_ID,
        "FOR_SOURCE",
        SOURCE_ID,
        "REL-OBS-SOP-V3-LATE-OBS-SOURCE",
        access="operations",
    ),
]


def source_branch_seed_queries() -> list[str]:
    """Return idempotent additive MERGE statements for the branch overlay."""
    return seed_queries_for(BRANCH_NODES, BRANCH_EDGES)


BRANCH_PATH_QUERY = f"""
MATCH (branch:ScenarioBranch {{id: {cypher_string(BRANCH_ID)}}})
      -[select:HAS_HYPOTHETICAL_OBSERVATION]->(override:ObservationOverride {{id: {cypher_string(OVERRIDE_ID)}}})
      -[for_source:FOR_SOURCE]->(source:Evidence {{id: {cypher_string(SOURCE_ID)}}})
WHERE branch.access = 'operations' AND select.access = 'operations'
  AND override.access = 'operations' AND for_source.access = 'operations'
  AND source.access = 'operations'
RETURN branch.branch_label AS label, branch.id AS branch_id,
       override.id AS override_id, override.assumed_observed_at AS assumed_observed_at,
       source.id AS source_id, source.title AS source_title,
       source.observed_at AS actual_source_observed_at,
       select.id AS branch_edge_id, for_source.id AS source_edge_id
LIMIT 1
""".strip()


def branch_v3_claim_query(known_at: str | None) -> str:
    """Read V3 only through both branch edges and its ordinary evidence path.

    The hypothetical time is applied in FalkorDB before claim projection.  No
    source node or mainline relationship is updated or rewritten.
    """
    cutoff = cypher_string(known_at or _NO_CUTOFF)
    return f"""
MATCH (branch:ScenarioBranch {{id: {cypher_string(BRANCH_ID)}}})
      -[select:HAS_HYPOTHETICAL_OBSERVATION]->(override:ObservationOverride {{id: {cypher_string(OVERRIDE_ID)}}})
      -[for_source:FOR_SOURCE]->(branch_source:Evidence {{id: {cypher_string(SOURCE_ID)}}}),
      (incident:Incident {{id: 'INC-204'}})-[aff:AFFECTS]->(equipment:Equipment {{id: 'CONVEYOR-C4'}})
      -[supports:SUPPORTS]->(service:Service {{id: 'SVC-MATERIAL-TRANSFER'}}),
      (version:SOPVersion {{id: 'SOP-OPS-9-V3'}})-[applies:APPLIES_TO]->(equipment),
      (version)-[claim:CLAIMS_ROLE]->(role:Role),
      (version)-[evidence:EVIDENCED_BY]->(source:Evidence)
WHERE branch.access = 'operations' AND select.access = 'operations'
  AND override.access = 'operations' AND for_source.access = 'operations'
  AND branch_source.access = 'operations' AND incident.access = 'operations'
  AND aff.access = 'operations' AND equipment.access = 'operations'
  AND supports.access = 'operations' AND service.access = 'operations'
  AND version.access = 'operations' AND applies.access = 'operations'
  AND claim.access = 'operations' AND role.access = 'operations'
  AND evidence.access = 'operations' AND source.access = 'operations'
  AND branch_source.id = source.id
  AND override.assumed_observed_at <= {cutoff}
RETURN version.id AS version_id, claim.id AS claim_edge_id, role.id AS role_id,
       applies.id AS applicability_edge_id, equipment.id AS equipment_id,
       source.id AS source_id, source.title AS source_title,
       override.assumed_observed_at AS source_observed_at,
       evidence.id AS evidence_edge_id
ORDER BY version.id, role.id
""".strip()


def unknown_branch_projection(known_at: str | None) -> dict[str, Any]:
    """Return an identity-free branch result when its graph witness is absent."""
    return {
        "known_at": known_at,
        "claim_context_state": "unknown" if known_at is None else "incomplete/unknown",
        "claims": [],
        "conflict": None,
        "decision_record": None,
        "decision_considers_edges": [],
        "generic_unknown": True,
        "negative_conclusion_asserted": False,
        "current_owner_or_procedure_selected": False,
        "contact_or_action_supplied": False,
    }


def scenario_from_branch_row(row: dict[str, Any]) -> dict[str, Any]:
    """Project only the public scenario assumptions, never graph identities."""
    return {
        "label": row["label"],
        "assumed_observed_at": row["assumed_observed_at"],
        "actual_source_observed_at": row["actual_source_observed_at"],
        "source_fact_modified": False,
        "scenario_not_source_truth": True,
    }


def branch_claim_projection(
    known_at: str | None,
    claim_rows: list[dict[str, Any]],
    record_rows: list[dict[str, Any]],
    considered_rows: list[dict[str, Any]],
    conflict_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    """Use the accepted timeline projection over branch-filtered graph rows."""
    return project_timeline(known_at, claim_rows, record_rows, considered_rows, conflict_rows)


def source_branch_response(
    mainline: dict[str, Any],
    known_at: str | None,
    branch_rows: list[dict[str, Any]],
    branch_projection: dict[str, Any] | None,
) -> dict[str, Any]:
    """Assemble the paired public view without disclosing missing branch IDs."""
    if not branch_rows or branch_projection is None:
        hypothetical = {"projection": unknown_branch_projection(known_at)}
    else:
        hypothetical = {
            "scenario": scenario_from_branch_row(branch_rows[0]),
            "projection": branch_projection,
        }
    return {
        "scope": "operations",
        "mainline": mainline,
        "hypothetical_branch": hypothetical,
    }


def disconnected_decoy_branch_queries() -> list[str]:
    """Test-only complete branch component disconnected from the pinned root."""
    decoy_nodes = [
        node("ScenarioBranch", "BR-SOP-DECOY-91", branch_label="Disconnected source-time decoy", access="operations"),
        node("ObservationOverride", "OBS-SOP-DECOY-91", assumed_observed_at=ASSUMED_SOURCE_OBSERVED_AT, access="operations"),
        node("Evidence", "SRC-SOP-DECOY-91", title="Disconnected synthetic source decoy", observed_at=ACTUAL_SOURCE_OBSERVED_AT, access="operations"),
    ]
    decoy_edges = [
        edge("BR-SOP-DECOY-91", "HAS_HYPOTHETICAL_OBSERVATION", "OBS-SOP-DECOY-91", "REL-BR-SOP-DECOY-91-OVERRIDE", access="operations"),
        edge("OBS-SOP-DECOY-91", "FOR_SOURCE", "SRC-SOP-DECOY-91", "REL-OBS-SOP-DECOY-91-SOURCE", access="operations"),
    ]
    return seed_queries_for(decoy_nodes, decoy_edges)


def restricted_source_branch_queries() -> list[str]:
    """Test-only ACL adversary connected to the visible graph by denied edges."""
    hidden_nodes = [
        node("ScenarioBranch", "BR-RESTRICTED-OPS-77", branch_label="Restricted source-time branch canary 77", access="restricted"),
        node("ObservationOverride", "OBS-RESTRICTED-OPS-77", assumed_observed_at=ASSUMED_SOURCE_OBSERVED_AT, access="restricted"),
        node("Evidence", "SRC-RESTRICTED-OPS-77", title="Restricted synthetic source observation canary 77", observed_at=ACTUAL_SOURCE_OBSERVED_AT, access="restricted"),
        node("Role", "ROLE-RESTRICTED-OPS-77", name="Restricted source role canary 77", access="restricted"),
    ]
    hidden_edges = [
        edge(BRANCH_ID, "HAS_HYPOTHETICAL_OBSERVATION", "OBS-RESTRICTED-OPS-77", "REL-BR-RESTRICTED-OPS-77", access="restricted"),
        edge("BR-RESTRICTED-OPS-77", "HAS_HYPOTHETICAL_OBSERVATION", "OBS-RESTRICTED-OPS-77", "REL-BR-RESTRICTED-ROOT-77", access="restricted"),
        edge("OBS-RESTRICTED-OPS-77", "FOR_SOURCE", "SRC-RESTRICTED-OPS-77", "REL-OBS-RESTRICTED-SOURCE-77", access="restricted"),
        edge("SRC-RESTRICTED-OPS-77", "CLAIMS_ROLE", "ROLE-RESTRICTED-OPS-77", "REL-RESTRICTED-SOURCE-ROLE-77", access="restricted"),
    ]
    return seed_queries_for(hidden_nodes, hidden_edges)

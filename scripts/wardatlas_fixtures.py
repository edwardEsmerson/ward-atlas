"""Small deterministic synthetic fixture and query definitions for WardAtlas.

All records describe invented hospital operations.  They contain no patient,
real staff, clinical advice, or live-system data.
"""

from __future__ import annotations

import json


def cypher_string(value: str) -> str:
    """Render a Python string as a Cypher string literal."""
    return json.dumps(value, ensure_ascii=True)


def node(label: str, identifier: str, **properties: object) -> dict:
    return {"label": label, "id": identifier, "properties": properties}


def edge(source: str, kind: str, target: str, identifier: str, **properties: object) -> dict:
    return {
        "source": source,
        "kind": kind,
        "target": target,
        "id": identifier,
        "properties": properties,
    }


NODES = [
    node("Incident", "INC-204", status="open", access="operations"),
    node(
        "Equipment",
        "CONVEYOR-C4",
        status="unavailable",
        access="operations",
        name="Material transfer conveyor C4",
    ),
    node(
        "Service",
        "SVC-MATERIAL-TRANSFER",
        access="operations",
        name="Material transfer service",
    ),
    node("Department", "DEPT-CENTRAL-STERILE", access="operations", name="Central sterile operations"),
    node("Department", "DEPT-MATERIALS-LOGISTICS", access="operations", name="Materials logistics"),
    node("Department", "DEPT-OUTPATIENT-ADMIN", access="operations"),
    node("Role", "ROLE-STERILE-OPS-COORD", access="operations", name="Sterile operations coordinator", current=True),
    node("Role", "ROLE-MATERIALS-DUTY", access="operations", name="Materials duty role", current=True),
    node("Role", "ROLE-OLD-STERILE-ROTATION", access="operations", current=False),
    node("Role", "ROLE-OUTPATIENT-DESK", access="operations", current=True),
    node("Evidence", "SRC-INC-204", title="Synthetic Incident Register INC-204", observed_at="2026-10-14T18:00:00Z", access="operations"),
    node("Evidence", "SRC-ASSET-C4", title="Synthetic Asset State Ledger", observed_at="2026-10-14T18:02:00Z", access="operations"),
    node("Evidence", "SRC-SERVICE-CATALOG", title="Synthetic Service Dependency Register", observed_at="2026-10-14T18:03:00Z", access="operations"),
    node("Evidence", "SRC-OPS-ROSTER", title="Synthetic Operations Role Roster", access="operations"),
    node("Equipment", "CONVEYOR-C3", status="retired", access="operations"),
    node("SOPVersion", "SOP-OPS-9-V1", sop_id="SOP-OPS-9", current=False, referenced_equipment_id="CONVEYOR-C3", escalation_role_id="ROLE-FACILITIES-DUTY", conflict_status="superseded", access="operations"),
    node("SOPVersion", "SOP-OPS-9-V2", sop_id="SOP-OPS-9", current=True, referenced_equipment_id="CONVEYOR-C4", escalation_role_id="ROLE-FACILITIES-DUTY", conflict_status="unresolved", access="operations"),
    node("SOPVersion", "SOP-OPS-9-V3", sop_id="SOP-OPS-9", current=True, referenced_equipment_id="CONVEYOR-C4", escalation_role_id="ROLE-MATERIALS-LEAD", conflict_status="unresolved", access="operations"),
    # These are role identifiers named by source claims, not current owners or
    # verified contacts. Their only relationship to each SOP is CLAIMS_ROLE.
    node("Role", "ROLE-FACILITIES-DUTY", access="operations", claim_only=True),
    node("Role", "ROLE-MATERIALS-LEAD", access="operations", claim_only=True),
    node("Evidence", "SRC-SOP-ARCHIVE-9", title="Synthetic SOP-OPS-9 Archived Text v1", observed_at="2026-01-10T12:00:00Z", access="operations"),
    node("Evidence", "SRC-SOP-CURRENT-V2", title="Synthetic SOP-OPS-9 Current Text v2", observed_at="2026-10-14T16:00:00Z", access="operations"),
    node("Evidence", "SRC-SOP-CURRENT-V3", title="Synthetic SOP-OPS-9 Current Text v3", observed_at="2026-10-14T16:04:00Z", access="operations"),
    # An adversarial, fully connected restricted branch.  It deliberately has
    # distinctive identifiers and text so tests can catch direct or derived
    # disclosure, not just omit the source title from a response.
    node("Equipment", "EQUIP-RESTRICTED-92", status="unavailable", access="restricted"),
    node("Department", "DEPT-RESTRICTED-COMPLIANCE", access="restricted"),
    node("Role", "ROLE-RESTRICTED-OWNER", access="restricted"),
    node("Evidence", "SRC-RESTRICTED-92", title="Restricted Continuity Annex 92", observed_at="2026-10-14T18:06:00Z", access="restricted", fact="Restricted-only instruction: contact the secure lift coordinator."),
]

EDGES = [
    edge("INC-204", "AFFECTS", "CONVEYOR-C4", "REL-INC204-C4", access="operations"),
    edge("CONVEYOR-C4", "SUPPORTS", "SVC-MATERIAL-TRANSFER", "REL-C4-SVC", access="operations"),
    edge("SVC-MATERIAL-TRANSFER", "SERVES", "DEPT-CENTRAL-STERILE", "REL-SVC-CSS", access="operations"),
    edge("SVC-MATERIAL-TRANSFER", "SERVES", "DEPT-MATERIALS-LOGISTICS", "REL-SVC-MAT", access="operations"),
    edge("DEPT-CENTRAL-STERILE", "OWNED_BY", "ROLE-STERILE-OPS-COORD", "REL-CSS-CURRENT", current=True, source_id="SRC-OPS-ROSTER", source_title="Synthetic Operations Role Roster", observed_at="2026-10-14T18:04:00Z", access="operations"),
    edge("DEPT-CENTRAL-STERILE", "OWNED_BY", "ROLE-OLD-STERILE-ROTATION", "REL-CSS-STALE", current=False, source_id="SRC-OPS-ROSTER", source_title="Synthetic Operations Role Roster", observed_at="2026-02-01T09:00:00Z", access="operations"),
    edge("DEPT-MATERIALS-LOGISTICS", "OWNED_BY", "ROLE-MATERIALS-DUTY", "REL-MAT-CURRENT", current=True, source_id="SRC-OPS-ROSTER", source_title="Synthetic Operations Role Roster", observed_at="2026-10-14T18:05:00Z", access="operations"),
    # Plausible-looking decoy entities exist but there is no service edge to
    # the outpatient department, so no outage path can reach its role.
    edge("DEPT-OUTPATIENT-ADMIN", "OWNED_BY", "ROLE-OUTPATIENT-DESK", "REL-OUTPATIENT-OWNER", current=True, source_id="SRC-OPS-ROSTER", source_title="Synthetic Operations Role Roster", observed_at="2026-10-14T18:05:00Z", access="operations"),
    edge("INC-204", "EVIDENCED_BY", "SRC-INC-204", "REL-INC204-EVIDENCE", access="operations"),
    edge("CONVEYOR-C4", "EVIDENCED_BY", "SRC-ASSET-C4", "REL-C4-EVIDENCE", access="operations"),
    edge("SVC-MATERIAL-TRANSFER", "EVIDENCED_BY", "SRC-SERVICE-CATALOG", "REL-SVC-EVIDENCE", access="operations"),
    edge("SOP-OPS-9-V1", "EVIDENCED_BY", "SRC-SOP-ARCHIVE-9", "REL-SOP-V1-EVIDENCE", access="operations"),
    edge("SOP-OPS-9-V2", "EVIDENCED_BY", "SRC-SOP-CURRENT-V2", "REL-SOP-V2-EVIDENCE", access="operations"),
    edge("SOP-OPS-9-V3", "EVIDENCED_BY", "SRC-SOP-CURRENT-V3", "REL-SOP-V3-EVIDENCE", access="operations"),
    edge("SOP-OPS-9-V2", "APPLIES_TO", "CONVEYOR-C4", "REL-SOP-V2-APPLIES-C4", access="operations"),
    edge("SOP-OPS-9-V3", "APPLIES_TO", "CONVEYOR-C4", "REL-SOP-V3-APPLIES-C4", access="operations"),
    edge("SOP-OPS-9-V1", "APPLIES_TO", "CONVEYOR-C3", "REL-SOP-V1-APPLIES-C3", access="operations"),
    edge("SOP-OPS-9-V2", "CLAIMS_ROLE", "ROLE-FACILITIES-DUTY", "REL-SOP-V2-CLAIMS-FACILITIES", access="operations"),
    edge("SOP-OPS-9-V3", "CLAIMS_ROLE", "ROLE-MATERIALS-LEAD", "REL-SOP-V3-CLAIMS-MATERIALS", access="operations"),
    edge("SOP-OPS-9-V2", "SOP_CONFLICT", "SOP-OPS-9-V3", "REL-SOP-9-CONFLICT", status="unresolved", access="operations"),
    # Restricted branch is fully graph-connected from the visible incident
    # and service, but every hidden hop is access-filtered at query time.
    edge("INC-204", "AFFECTS", "EQUIP-RESTRICTED-92", "REL-INC204-RESTRICTED", access="restricted"),
    edge("EQUIP-RESTRICTED-92", "SUPPORTS", "SVC-MATERIAL-TRANSFER", "REL-RESTRICTED-SVC", access="restricted"),
    edge("EQUIP-RESTRICTED-92", "EVIDENCED_BY", "SRC-RESTRICTED-92", "REL-RESTRICTED-EVIDENCE", access="restricted"),
    edge("SVC-MATERIAL-TRANSFER", "SERVES", "DEPT-RESTRICTED-COMPLIANCE", "REL-SVC-RESTRICTED", access="restricted"),
    edge("DEPT-RESTRICTED-COMPLIANCE", "OWNED_BY", "ROLE-RESTRICTED-OWNER", "REL-RESTRICTED-OWNER", current=True, source_id="SRC-RESTRICTED-92", source_title="Restricted Continuity Annex 92", observed_at="2026-10-14T18:06:00Z", access="restricted"),
]


def _set_properties(variable: str, properties: dict[str, object]) -> str:
    return "\n".join(
        f"SET {variable}.{key} = {cypher_string(value) if isinstance(value, str) else str(value).lower() if isinstance(value, bool) else value}"
        for key, value in properties.items()
    )


def seed_queries() -> list[str]:
    """Return MERGE-based, repeatable Cypher statements for this fixture."""
    public_nodes = [
        item for item in NODES if item["properties"].get("access") == "operations"
    ]
    public_edges = [
        item for item in EDGES if item["properties"].get("access") == "operations"
    ]
    return seed_queries_for(public_nodes, public_edges)


IMPACT_QUERY = """
MATCH (i:Incident {id: 'INC-204'})-[aff:AFFECTS]->(e:Equipment)-[sup:SUPPORTS]->(s:Service)-[serves:SERVES]->(d:Department)-[owns:OWNED_BY]->(r:Role),
      (i)-[iev:EVIDENCED_BY]->(isrc:Evidence),
      (e)-[eev:EVIDENCED_BY]->(esrc:Evidence),
      (s)-[sev:EVIDENCED_BY]->(ssrc:Evidence)
WHERE i.status = 'open' AND e.status = 'unavailable' AND owns.current = true
  AND i.access = 'operations' AND e.access = 'operations' AND s.access = 'operations'
  AND d.access = 'operations' AND r.access = 'operations'
  AND aff.access = 'operations' AND sup.access = 'operations' AND serves.access = 'operations'
  AND owns.access = 'operations' AND iev.access = 'operations' AND eev.access = 'operations'
  AND sev.access = 'operations' AND isrc.access = 'operations' AND esrc.access = 'operations'
  AND ssrc.access = 'operations'
RETURN i.id AS incident_id, e.id AS equipment_id, s.id AS service_id,
       d.id AS department_id, r.id AS role_id,
       i.status AS incident_status, e.status AS equipment_status,
       e.name AS equipment_name, s.name AS service_name,
       d.name AS department_name, r.name AS role_name,
       isrc.id AS incident_source_id, esrc.id AS equipment_source_id,
       ssrc.id AS service_source_id, owns.source_id AS ownership_source_id,
       isrc.title AS incident_source_title, esrc.title AS equipment_source_title,
       ssrc.title AS service_source_title, owns.source_title AS ownership_source_title,
       isrc.observed_at AS incident_observed_at, esrc.observed_at AS equipment_observed_at,
       ssrc.observed_at AS service_observed_at, owns.observed_at AS ownership_observed_at
ORDER BY d.id
""".strip()


SOP_CONFLICT_QUERY = """
MATCH (stale:SOPVersion {id: 'SOP-OPS-9-V1'})-[sev1:EVIDENCED_BY]->(src1:Evidence),
      (v2:SOPVersion {id: 'SOP-OPS-9-V2'})-[sev2:EVIDENCED_BY]->(src2:Evidence),
      (v3:SOPVersion {id: 'SOP-OPS-9-V3'})-[sev3:EVIDENCED_BY]->(src3:Evidence),
      (v2)-[conf:SOP_CONFLICT]->(v3)
WHERE stale.access = 'operations' AND v2.access = 'operations' AND v3.access = 'operations'
  AND src1.access = 'operations' AND src2.access = 'operations' AND src3.access = 'operations'
  AND sev1.access = 'operations' AND sev2.access = 'operations' AND sev3.access = 'operations'
  AND conf.access = 'operations' AND conf.status = 'unresolved'
RETURN stale.id AS stale_version_id, v2.id AS current_v2_id, v3.id AS current_v3_id,
       stale.referenced_equipment_id AS stale_equipment_id,
       v2.escalation_role_id AS current_v2_role_claim,
       v3.escalation_role_id AS current_v3_role_claim,
       conf.status AS conflict_status,
       src1.id AS stale_source_id, src2.id AS current_v2_source_id,
       src3.id AS current_v3_source_id,
       src1.title AS stale_source_title, src2.title AS current_v2_source_title,
       src3.title AS current_v3_source_title,
       src1.observed_at AS stale_observed_at, src2.observed_at AS current_v2_observed_at,
       src3.observed_at AS current_v3_observed_at
""".strip()


def restricted_fixture_queries() -> list[str]:
    """Insert the separately timed restricted adversarial branch."""
    wanted = {
        "EQUIP-RESTRICTED-92",
        "DEPT-RESTRICTED-COMPLIANCE",
        "ROLE-RESTRICTED-OWNER",
        "SRC-RESTRICTED-92",
        "REL-INC204-RESTRICTED",
        "REL-RESTRICTED-SVC",
        "REL-RESTRICTED-EVIDENCE",
        "REL-SVC-RESTRICTED",
        "REL-RESTRICTED-OWNER",
    }
    selected_nodes = [item for item in NODES if item["id"] in wanted]
    selected_edges = [item for item in EDGES if item["id"] in wanted]
    # Keep the allowlists explicit and fail closed if the fixture changes.
    if len(selected_nodes) != 4 or len(selected_edges) != 5:
        raise AssertionError("restricted fixture inventory changed unexpectedly")
    return seed_queries_for(selected_nodes, selected_edges)


def seed_queries_for(nodes: list[dict], edges: list[dict]) -> list[str]:
    queries: list[str] = []
    for item in nodes:
        label, identifier = item["label"], item["id"]
        properties = {"id": identifier, **item["properties"]}
        queries.append(
            f"MERGE (n:{label} {{id: {cypher_string(identifier)}}})\n"
            + _set_properties("n", properties)
        )
    for item in edges:
        source, target, kind, identifier = (
            item["source"], item["target"], item["kind"], item["id"]
        )
        properties = {"id": identifier, **item["properties"]}
        queries.append(
            f"MATCH (a {{id: {cypher_string(source)}}}), (b {{id: {cypher_string(target)}}})\n"
            f"MERGE (a)-[r:{kind} {{id: {cypher_string(identifier)}}}]->(b)\n"
            + _set_properties("r", properties)
        )
    return queries

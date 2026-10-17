"""Pinned synthetic graph model and query builders for WardAtlas's shift board.

The dock roster and conveyor incident remain separate cases in one FalkorDB
graph. Dates, relationships, source records, and the one hypothetical branch
are synthetic. No clinical or external operational action is represented.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone


DOCK_BEFORE = "2026-10-14T20:29:00Z"
DOCK_EFFECTIVE = "2026-10-14T20:30:00Z"
CONVEYOR_OUTAGE_AT = "2026-10-14T18:30:00Z"
CONVEYOR_RESTORED_AT = "2026-10-14T19:30:00Z"
BRANCH_AT = "2026-10-14T18:50:00Z"
BRANCH_ID = "BR-C4-RESTORE-DRILL"

_TIMESTAMP_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:Z|[+-]\d{2}:\d{2})$"
)


def normalize_timestamp(value: str) -> str:
    """Validate an explicit ISO-8601 timestamp and normalize it to UTC seconds."""
    if not isinstance(value, str) or not _TIMESTAMP_RE.fullmatch(value):
        raise ValueError("Invalid timestamp; use an explicit ISO-8601 offset.")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00" if value.endswith("Z") else value)
    except ValueError as exc:
        raise ValueError("Invalid timestamp; use an explicit ISO-8601 offset.") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("Invalid timestamp; use an explicit ISO-8601 offset.")
    return parsed.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _literal(value: str) -> str:
    return json.dumps(value, ensure_ascii=True)


def _node(node_label: str, identifier: str, **properties: object) -> dict:
    return {"label": node_label, "id": identifier, "properties": properties}


def _edge(source: str, kind: str, target: str, identifier: str, **properties: object) -> dict:
    return {"source": source, "kind": kind, "target": target, "id": identifier, "properties": properties}


SHIFT_NODES = [
    _node("Incident", "INC-207", status="open", access="operations", synthetic=True),
    _node("Equipment", "EQ-DOCK-CART-2", status="in-service", name="Dock transfer cart 2", access="operations", synthetic=True),
    _node("Service", "SVC-INBOUND-TRANSFER", name="Inbound transfer service", access="operations", synthetic=True),
    _node("Department", "DEPT-RECEIVING-DOCK", name="Receiving dock", access="operations", synthetic=True),
    _node("Role", "ROLE-DOCK-DUTY", name="Dock duty role", access="operations", synthetic=True),
    _node("Role", "ROLE-DOCK-SWING", name="Dock swing role", access="operations", synthetic=True),
    _node("Evidence", "SRC-DOCK-ROSTER-R1", title="Synthetic Dock Operations Roster Revision 1", observed_at="2026-10-14T19:00:00Z", access="operations", synthetic=True),
    _node("Evidence", "SRC-DOCK-ROSTER-R2", title="Synthetic Dock Operations Roster Revision 2", observed_at="2026-10-14T20:18:00Z", effective_at=DOCK_EFFECTIVE, access="operations", synthetic=True),
    _node("Handover", "HND-DOCK-2020", role_id="ROLE-DOCK-DUTY", source_id="SRC-DOCK-ROSTER-R1", title="Synthetic dock handover at 20:20", access="operations", synthetic=True),
    _node("Ticket", "TKT-DOCK-88", source_id="SRC-DOCK-ROSTER-R1", title="Synthetic dock ownership review ticket", access="operations", synthetic=True),
    _node("Handover", "HND-DOCK-LEXICAL-ONLY", note="Roster revision 2 dock ownership update", title="Synthetic lexical decoy", access="operations", synthetic=True),
    _node("Evidence", "SRC-ASSET-C4-OUTAGE", title="Synthetic C4 Outage Event Ledger", observed_at="2026-10-14T18:02:00Z", access="operations", synthetic=True),
    _node("Evidence", "SRC-ASSET-C4-RESTORE", title="Synthetic C4 Restoration Event Ledger", observed_at="2026-10-14T19:15:00Z", access="operations", synthetic=True),
    _node("StateEvent", "EVT-C4-UNAVAILABLE-01", value="unavailable", event_at="2026-10-14T18:02:00Z", event_kind="observed", access="operations", synthetic=True),
    _node("StateEvent", "EVT-C4-RESTORED-01", value="available", event_at="2026-10-14T19:15:00Z", event_kind="observed", access="operations", synthetic=True),
    _node("ScenarioBranch", BRANCH_ID, label="hypothetical", access="operations", synthetic=True),
    _node("StateEvent", "EVT-BR-RESTORE-01", value="available", event_at="2026-10-14T18:45:00Z", event_kind="hypothetical", branch_id=BRANCH_ID, access="operations", synthetic=True),
]

SHIFT_EDGES = [
    _edge("INC-207", "AFFECTS", "EQ-DOCK-CART-2", "REL-INC207-DOCK-CART", access="operations"),
    _edge("EQ-DOCK-CART-2", "SUPPORTS", "SVC-INBOUND-TRANSFER", "REL-DOCK-CART-SVC", access="operations"),
    _edge("SVC-INBOUND-TRANSFER", "SERVES", "DEPT-RECEIVING-DOCK", "REL-INBOUND-RECEIVING", access="operations"),
    _edge("DEPT-RECEIVING-DOCK", "OWNED_BY", "ROLE-DOCK-DUTY", "REL-DOCK-OWNER-R1", effective_from="0001-01-01T00:00:00Z", effective_to=DOCK_EFFECTIVE, source_id="SRC-DOCK-ROSTER-R1", access="operations"),
    _edge("DEPT-RECEIVING-DOCK", "OWNED_BY", "ROLE-DOCK-SWING", "REL-DOCK-OWNER-R2", effective_from=DOCK_EFFECTIVE, effective_to="9999-12-31T23:59:59Z", source_id="SRC-DOCK-ROSTER-R2", access="operations"),
    _edge("ROLE-DOCK-DUTY", "EVIDENCED_BY", "SRC-DOCK-ROSTER-R1", "REL-DOCK-DUTY-R1", access="operations"),
    _edge("ROLE-DOCK-SWING", "EVIDENCED_BY", "SRC-DOCK-ROSTER-R2", "REL-DOCK-SWING-R2", access="operations"),
    _edge("ROLE-STERILE-OPS-COORD", "EVIDENCED_BY", "SRC-OPS-ROSTER", "REL-CSS-ROLE-ROSTER-SOURCE", access="operations"),
    _edge("ROLE-MATERIALS-DUTY", "EVIDENCED_BY", "SRC-OPS-ROSTER", "REL-MATERIALS-ROLE-ROSTER-SOURCE", access="operations"),
    _edge("SRC-DOCK-ROSTER-R2", "SUPERSEDES", "SRC-DOCK-ROSTER-R1", "REL-DOCK-R2-SUPERSEDES-R1", access="operations"),
    _edge("HND-DOCK-2020", "CITES_SOURCE", "SRC-DOCK-ROSTER-R1", "REL-HND-DOCK-2020-R1", access="operations"),
    _edge("TKT-DOCK-88", "CITES_SOURCE", "SRC-DOCK-ROSTER-R1", "REL-TKT-DOCK-88-R1", access="operations"),
    _edge("INC-204", "HAS_STATE_EVENT", "EVT-C4-UNAVAILABLE-01", "REL-C4-UNAVAILABLE-EVENT", access="operations"),
    _edge("INC-204", "HAS_STATE_EVENT", "EVT-C4-RESTORED-01", "REL-C4-RESTORED-EVENT", access="operations"),
    _edge("EVT-C4-UNAVAILABLE-01", "EVIDENCED_BY", "SRC-ASSET-C4-OUTAGE", "REL-C4-UNAVAILABLE-SOURCE", access="operations"),
    _edge("EVT-C4-RESTORED-01", "EVIDENCED_BY", "SRC-ASSET-C4-RESTORE", "REL-C4-RESTORED-SOURCE", access="operations"),
    _edge(BRANCH_ID, "HAS_HYPOTHETICAL_EVENT", "EVT-BR-RESTORE-01", "REL-BR-C4-RESTORE-EVENT", access="operations"),
]


def _property_lines(variable: str, properties: dict[str, object]) -> str:
    rendered = []
    for key, value in properties.items():
        if isinstance(value, str):
            expression = _literal(value)
        elif isinstance(value, bool):
            expression = str(value).lower()
        else:
            expression = str(value)
        rendered.append(f"SET {variable}.{key} = {expression}")
    return "\n".join(rendered)


def seed_shift_board_queries() -> list[str]:
    """Return idempotent MERGE statements for the integrated board additions."""
    queries: list[str] = []
    for item in SHIFT_NODES:
        identifier = item["id"]
        props = {"id": identifier, **item["properties"]}
        queries.append(
            f"MERGE (n:{item['label']} {{id: {_literal(identifier)}}})\n"
            + _property_lines("n", props)
        )
    for item in SHIFT_EDGES:
        props = {"id": item["id"], **item["properties"]}
        queries.append(
            f"MATCH (a {{id: {_literal(item['source'])}}}), (b {{id: {_literal(item['target'])}}})\n"
            f"MERGE (a)-[r:{item['kind']} {{id: {_literal(item['id'])}}}]->(b)\n"
            + _property_lines("r", props)
        )
    return queries


def dock_role_query(as_of: str, known_at: str | None = None) -> str:
    at = _literal(normalize_timestamp(as_of))
    knowledge_filter = (
        f"\n  AND src.observed_at <= {_literal(normalize_timestamp(known_at))}"
        if known_at is not None else ""
    )
    return f"""
MATCH (i:Incident {{id: 'INC-207'}})-[aff:AFFECTS]->(e:Equipment)
      -[sup:SUPPORTS]->(s:Service)-[serves:SERVES]->(d:Department)
      -[owns:OWNED_BY]->(r:Role)-[roster:EVIDENCED_BY]->(src:Evidence)
WHERE i.access = 'operations' AND e.access = 'operations' AND s.access = 'operations'
  AND d.access = 'operations' AND r.access = 'operations' AND src.access = 'operations'
  AND aff.access = 'operations' AND sup.access = 'operations' AND serves.access = 'operations'
  AND owns.access = 'operations' AND roster.access = 'operations'
  AND owns.source_id = src.id AND owns.effective_from <= {at} AND owns.effective_to > {at}{knowledge_filter}
RETURN i.id AS incident_id, e.id AS equipment_id, s.id AS service_id,
       d.id AS department_id, r.id AS role_id, r.name AS role_name,
       src.id AS source_id, src.title AS source_title, src.observed_at AS source_observed_at,
       src.effective_at AS source_effective_at, owns.effective_from AS effective_from,
       owns.effective_to AS effective_to
ORDER BY r.id
""".strip()


DOCK_SOURCE_QUERY = """
MATCH (src:Evidence)
WHERE src.id IN ['SRC-DOCK-ROSTER-R1', 'SRC-DOCK-ROSTER-R2'] AND src.access = 'operations'
RETURN src.id AS source_id, src.title AS title, src.observed_at AS observed_at,
       src.effective_at AS effective_at
ORDER BY src.observed_at
""".strip()


def dock_source_query(known_at: str | None = None) -> str:
    """Return operations-visible dock source rows, optionally as known then."""
    if known_at is None:
        return DOCK_SOURCE_QUERY
    cutoff = _literal(normalize_timestamp(known_at))
    return DOCK_SOURCE_QUERY.replace(
        "AND src.access = 'operations'",
        f"AND src.access = 'operations' AND src.observed_at <= {cutoff}",
    )


DOCK_SUPERSESSION_QUERY = """
MATCH (newer:Evidence {id: 'SRC-DOCK-ROSTER-R2'})-[rel:SUPERSEDES]->(older:Evidence {id: 'SRC-DOCK-ROSTER-R1'})
WHERE newer.access = 'operations' AND older.access = 'operations' AND rel.access = 'operations'
RETURN newer.id AS source_id, older.id AS supersedes
""".strip()


def dock_supersession_query(known_at: str | None = None) -> str:
    """Limit explicit supersession linkage to evidence observed by the cutoff."""
    if known_at is None:
        return DOCK_SUPERSESSION_QUERY
    cutoff = _literal(normalize_timestamp(known_at))
    return DOCK_SUPERSESSION_QUERY.replace(
        "WHERE newer.access = 'operations' AND older.access = 'operations' AND rel.access = 'operations'",
        "WHERE newer.access = 'operations' AND older.access = 'operations' AND rel.access = 'operations' "
        f"AND newer.observed_at <= {cutoff} AND older.observed_at <= {cutoff}",
    )


DOCK_REVIEW_QUERY = """
MATCH (artifact)-[cite:CITES_SOURCE]->(older:Evidence)<-[sup:SUPERSEDES]-(newer:Evidence)
WHERE artifact.id IN ['HND-DOCK-2020', 'TKT-DOCK-88', 'HND-DOCK-LEXICAL-ONLY']
  AND older.id = 'SRC-DOCK-ROSTER-R1' AND newer.id = 'SRC-DOCK-ROSTER-R2'
  AND artifact.access = 'operations' AND older.access = 'operations' AND newer.access = 'operations'
  AND cite.access = 'operations' AND sup.access = 'operations'
RETURN artifact.id AS artifact_id, artifact.role_id AS recorded_role_id,
       artifact.source_id AS recorded_source_id, older.id AS cited_source_id,
       newer.id AS superseding_source_id
ORDER BY artifact.id
""".strip()


def dock_review_query(known_at: str | None = None) -> str:
    """Return only explicit, operations-visible review edges known by the cutoff."""
    if known_at is None:
        return DOCK_REVIEW_QUERY
    cutoff = _literal(normalize_timestamp(known_at))
    return DOCK_REVIEW_QUERY.replace(
        "AND cite.access = 'operations' AND sup.access = 'operations'",
        "AND cite.access = 'operations' AND sup.access = 'operations' "
        f"AND newer.observed_at <= {cutoff} AND older.observed_at <= {cutoff}",
    )


DOCK_HANDOVER_SNAPSHOT_QUERY = """
MATCH (handover:Handover {id: 'HND-DOCK-2020'})
WHERE handover.access = 'operations'
RETURN handover.id AS artifact_id, handover.role_id AS role_id,
       handover.source_id AS source_id
""".strip()


CONVEYOR_EVENT_QUERY = """
MATCH (i:Incident {id: 'INC-204'})-[rel:HAS_STATE_EVENT]->(event:StateEvent)-[ev:EVIDENCED_BY]->(src:Evidence)
WHERE i.access = 'operations' AND rel.access = 'operations' AND event.access = 'operations'
  AND ev.access = 'operations' AND src.access = 'operations'
  AND event.event_kind = 'observed' AND event.branch_id IS NULL
RETURN event.id AS event_id, event.value AS value, event.event_at AS event_at,
       src.id AS source_id, src.title AS source_title, src.observed_at AS source_observed_at,
       'observed source fact' AS evidence_label
ORDER BY event.event_at
""".strip()


BRANCH_EVENT_QUERY = f"""
MATCH (branch:ScenarioBranch {{id: '{BRANCH_ID}'}})-[rel:HAS_HYPOTHETICAL_EVENT]->(event:StateEvent {{id: 'EVT-BR-RESTORE-01'}})
WHERE branch.access = 'operations' AND rel.access = 'operations' AND event.access = 'operations'
  AND event.branch_id = branch.id AND branch.label = 'hypothetical' AND event.event_kind = 'hypothetical'
RETURN branch.id AS branch_id, event.id AS event_id, event.value AS value,
       event.event_at AS event_at, 'hypothetical branch' AS evidence_label
""".strip()


CONVEYOR_PATH_QUERY = """
MATCH (i:Incident {id: 'INC-204'})-[aff:AFFECTS]->(e:Equipment {id: 'CONVEYOR-C4'})
      -[sup:SUPPORTS]->(s:Service {id: 'SVC-MATERIAL-TRANSFER'})
      -[serves:SERVES]->(d:Department)-[owns:OWNED_BY]->(r:Role),
      (r)-[roster:EVIDENCED_BY]->(rsrc:Evidence),
      (i)-[iev:EVIDENCED_BY]->(isrc:Evidence),
      (e)-[eev:EVIDENCED_BY]->(esrc:Evidence),
      (s)-[sev:EVIDENCED_BY]->(ssrc:Evidence)
WHERE owns.current = true
  AND i.access = 'operations' AND e.access = 'operations' AND s.access = 'operations'
  AND d.access = 'operations' AND r.access = 'operations'
  AND aff.access = 'operations' AND sup.access = 'operations' AND serves.access = 'operations'
  AND owns.access = 'operations' AND roster.access = 'operations' AND rsrc.access = 'operations'
  AND owns.source_id = rsrc.id
  AND iev.access = 'operations' AND eev.access = 'operations'
  AND sev.access = 'operations' AND isrc.access = 'operations' AND esrc.access = 'operations'
  AND ssrc.access = 'operations'
RETURN i.id AS incident_id, e.id AS equipment_id, s.id AS service_id,
       d.id AS department_id, r.id AS role_id,
       isrc.id AS incident_source_id, isrc.title AS incident_source_title,
       isrc.observed_at AS incident_observed_at,
       esrc.id AS equipment_source_id, esrc.title AS equipment_source_title,
       esrc.observed_at AS equipment_observed_at,
       ssrc.id AS service_source_id, ssrc.title AS service_source_title,
       ssrc.observed_at AS service_observed_at,
       rsrc.id AS ownership_source_id, rsrc.title AS ownership_source_title,
       owns.observed_at AS ownership_observed_at
ORDER BY d.id
""".strip()


def branch_reset_queries() -> list[str]:
    """Remove only the named hypothetical overlay; preserve every mainline row."""
    return [
        f"MATCH (branch:ScenarioBranch {{id: '{BRANCH_ID}'}})-[rel:HAS_HYPOTHETICAL_EVENT]->(event:StateEvent {{id: 'EVT-BR-RESTORE-01', branch_id: '{BRANCH_ID}'}}) DELETE rel",
        f"MATCH (event:StateEvent {{id: 'EVT-BR-RESTORE-01', branch_id: '{BRANCH_ID}'}}) DETACH DELETE event",
        f"MATCH (branch:ScenarioBranch {{id: '{BRANCH_ID}', label: 'hypothetical'}}) DETACH DELETE branch",
    ]


def restricted_shift_board_queries() -> list[str]:
    """Test-only exact denied-source tripwires from the pinned shift-board oracle."""
    hidden_nodes = [
        _node("Evidence", "SRC-RESTRICTED-DOCK-99", title="Restricted Dock Continuity Annex 99", fact="Restricted-only note: logistics bay status unresolved.", access="restricted", synthetic=True),
        _node("Equipment", "EQ-RESTRICTED-DOCK-99", status="unavailable", access="restricted", synthetic=True),
        _node("Department", "DEPT-RESTRICTED-DOCK-99", access="restricted", synthetic=True),
        _node("Role", "ROLE-RESTRICTED-DOCK-OWNER", access="restricted", synthetic=True),
        _node("StateEvent", "EV-C4-RESTRICTED-UPDATE", value="available", event_at="2026-10-14T18:20:00Z", event_kind="observed", access="operations", synthetic=True),
        _node("Evidence", "SRC-RESTRICTED-92", title="Restricted Continuity Annex 92", fact="Restricted-only instruction: contact the secure lift coordinator.", observed_at="2026-10-14T18:20:00Z", access="restricted", synthetic=True),
    ]
    hidden_edges = [
        _edge("INC-207", "AFFECTS", "EQ-RESTRICTED-DOCK-99", "REL-INC207-RESTRICTED-DOCK", access="restricted"),
        _edge("EQ-RESTRICTED-DOCK-99", "SUPPORTS", "SVC-INBOUND-TRANSFER", "REL-RESTRICTED-DOCK-SVC", access="restricted"),
        _edge("SVC-INBOUND-TRANSFER", "SERVES", "DEPT-RESTRICTED-DOCK-99", "REL-SVC-RESTRICTED-DOCK", access="restricted"),
        _edge("DEPT-RESTRICTED-DOCK-99", "OWNED_BY", "ROLE-RESTRICTED-DOCK-OWNER", "REL-RESTRICTED-DOCK-OWNER", effective_from="0001-01-01T00:00:00Z", effective_to="9999-12-31T23:59:59Z", source_id="SRC-RESTRICTED-DOCK-99", current=True, access="restricted"),
        _edge("ROLE-RESTRICTED-DOCK-OWNER", "EVIDENCED_BY", "SRC-RESTRICTED-DOCK-99", "REL-RESTRICTED-DOCK-SOURCE", access="restricted"),
        _edge("INC-204", "HAS_STATE_EVENT", "EV-C4-RESTRICTED-UPDATE", "REL-C4-RESTRICTED-UPDATE", access="operations"),
        _edge("EV-C4-RESTRICTED-UPDATE", "EVIDENCED_BY", "SRC-RESTRICTED-92", "REL-C4-RESTRICTED-SOURCE", access="operations"),
    ]
    queries: list[str] = []
    for item in hidden_nodes:
        props = {"id": item["id"], **item["properties"]}
        queries.append(f"MERGE (n:{item['label']} {{id: {_literal(item['id'])}}})\n" + _property_lines("n", props))
    for item in hidden_edges:
        props = {"id": item["id"], **item["properties"]}
        queries.append(
            f"MATCH (a {{id: {_literal(item['source'])}}}), (b {{id: {_literal(item['target'])}}})\n"
            f"MERGE (a)-[r:{item['kind']} {{id: {_literal(item['id'])}}}]->(b)\n"
            + _property_lines("r", props)
        )
    return queries

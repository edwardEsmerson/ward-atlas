#!/usr/bin/env python3
"""Strict synthetic source-revision CLI for WardAtlas (real FalkorDB only).

The command requires an explicitly selected loopback database port and an
owned, lane-scoped graph name. It accepts no arbitrary documents or external
source data. Revision facts are append-only; operational artifacts are only
queued for review when an explicit citation edge reaches a superseded source.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from typing import Any, Callable

from verify_falkordb_live import (
    RespConnection,
    module_list_maps,
    query_result,
    result_rows,
    text,
)


INCIDENT = "INC-207"
EQUIPMENT = "EQ-DOCK-CART-2"
SERVICE = "SVC-INBOUND-TRANSFER"
DEPARTMENT = "DEPT-RECEIVING-DOCK"
ROLE_BEFORE = "ROLE-DOCK-DUTY"
ROLE_AFTER = "ROLE-DOCK-SWING"
SOURCE_BEFORE = "SRC-DOCK-ROSTER-R1"
SOURCE_AFTER = "SRC-DOCK-ROSTER-R2"
OBSERVED_AT = "2026-10-14T20:18:00Z"
EFFECTIVE_AT = "2026-10-14T20:30:00Z"
HANDOVER = "HND-DOCK-2020"
TICKET = "TKT-DOCK-88"
LEXICAL_HANDOVER = "HND-DOCK-LEXICAL-ONLY"
SOP_V2 = "SOP-OPS-9-V2"
SOP_V3 = "SOP-OPS-9-V3"

Update = dict[str, Any]
GraphQuery = Callable[[str], dict[str, Any]]


class SourceChangeError(ValueError):
    """Input, ownership or query error for the fixture-only source workflow."""


def cypher_string(value: str) -> str:
    return json.dumps(value, ensure_ascii=True)


def normalize_time(value: str) -> str:
    """Parse an ISO-8601 timestamp with an explicit offset and normalize UTC."""
    if not isinstance(value, str) or not value:
        raise SourceChangeError("timestamp must be a non-empty ISO-8601 string")
    candidate = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError as exc:
        raise SourceChangeError(f"malformed ISO-8601 timestamp: {value!r}") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise SourceChangeError("timestamp must include an explicit UTC offset")
    normalized = parsed.astimezone(timezone.utc)
    return normalized.isoformat(timespec="seconds").replace("+00:00", "Z")


def update_fixture() -> dict[str, Any]:
    """The sole accepted source-change payload; no caller-controlled facts."""
    return {
        "schema_version": 1,
        "source": {
            "id": SOURCE_AFTER,
            "title": "Synthetic Dock Operations Roster Revision 2",
            "observed_at": OBSERVED_AT,
            "effective_at": EFFECTIVE_AT,
            "access": "operations",
        },
        "supersedes_source_id": SOURCE_BEFORE,
        "assignment": {
            "id": "ASSIGN-DOCK-R2",
            "department_id": DEPARTMENT,
            "role_id": ROLE_AFTER,
            "source_id": SOURCE_AFTER,
            "observed_at": OBSERVED_AT,
            "effective_at": EFFECTIVE_AT,
            "access": "operations",
        },
    }


def validate_update(payload: str | bytes | dict[str, Any]) -> Update:
    """Validate the entire fixed synthetic update before any graph write."""
    try:
        if isinstance(payload, bytes):
            parsed = json.loads(payload.decode("utf-8"))
        elif isinstance(payload, str):
            parsed = json.loads(payload)
        else:
            parsed = payload
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SourceChangeError(f"invalid synthetic update JSON: {exc}") from exc
    if not isinstance(parsed, dict) or set(parsed) != {
        "schema_version", "source", "supersedes_source_id", "assignment"
    }:
        raise SourceChangeError("update must have exactly schema_version, source, supersedes_source_id, and assignment")
    fixed = update_fixture()
    if parsed.get("schema_version") != 1:
        raise SourceChangeError("only synthetic update schema_version 1 is accepted")
    source = parsed.get("source")
    assignment = parsed.get("assignment")
    if not isinstance(source, dict) or not isinstance(assignment, dict):
        raise SourceChangeError("source and assignment must be JSON objects")
    if set(source) != set(fixed["source"]):
        raise SourceChangeError("source fields do not match the strict synthetic fixture schema")
    if set(assignment) != set(fixed["assignment"]):
        raise SourceChangeError("assignment fields do not match the strict synthetic fixture schema")
    # Parse both times before comparing fixed values. A missing, naive or bad
    # effective time is rejected here, before any GraphQuery can be invoked.
    source_observed = normalize_time(source.get("observed_at"))
    source_effective = normalize_time(source.get("effective_at"))
    assignment_observed = normalize_time(assignment.get("observed_at"))
    assignment_effective = normalize_time(assignment.get("effective_at"))
    canonical = json.loads(json.dumps(parsed))
    canonical["source"]["observed_at"] = source_observed
    canonical["source"]["effective_at"] = source_effective
    canonical["assignment"]["observed_at"] = assignment_observed
    canonical["assignment"]["effective_at"] = assignment_effective
    expected = fixed
    if canonical != expected:
        raise SourceChangeError("update values do not match the one authorized synthetic dock roster revision")
    return canonical


def _node(label: str, identifier: str, **properties: Any) -> dict[str, Any]:
    return {"label": label, "id": identifier, "properties": {"id": identifier, **properties}}


def _edge(source: str, kind: str, target: str, identifier: str, **properties: Any) -> dict[str, Any]:
    return {"source": source, "kind": kind, "target": target, "id": identifier, "properties": {"id": identifier, **properties}}


def _set_properties(variable: str, properties: dict[str, Any]) -> str:
    commands = []
    for key, value in properties.items():
        if isinstance(value, str):
            rendered = cypher_string(value)
        elif isinstance(value, bool):
            rendered = str(value).lower()
        elif isinstance(value, (int, float)):
            rendered = str(value)
        else:
            raise SourceChangeError(f"unsupported fixture property type for {key}")
        commands.append(f"SET {variable}.{key} = {rendered}")
    return "\n".join(commands)


def _node_query(item: dict[str, Any]) -> str:
    label, identifier = item["label"], item["id"]
    return (
        f"MERGE (n:{label} {{id: {cypher_string(identifier)}}})\n"
        + _set_properties("n", item["properties"])
    )


def _edge_query(item: dict[str, Any]) -> str:
    return (
        f"MATCH (a {{id: {cypher_string(item['source'])}}}), (b {{id: {cypher_string(item['target'])}}})\n"
        f"MERGE (a)-[r:{item['kind']} {{id: {cypher_string(item['id'])}}}]->(b)\n"
        + _set_properties("r", item["properties"])
    )


def base_nodes(graph_name: str, lane: str) -> list[dict[str, Any]]:
    op = {"access": "operations"}
    return [
        _node("SourceChangeRun", graph_name, owner="wardatlas-source-change-cli", lane=lane, fixture="synthetic-dock-v1"),
        _node("Incident", INCIDENT, status="open", **op),
        _node("Equipment", EQUIPMENT, status="unavailable", **op),
        _node("Service", SERVICE, **op),
        _node("Department", DEPARTMENT, **op),
        _node("Role", ROLE_BEFORE, **op),
        _node("Role", ROLE_AFTER, **op),
        _node("Evidence", SOURCE_BEFORE, title="Synthetic Dock Operations Roster Revision 1", observed_at="2026-10-14T19:00:00Z", access="operations"),
        _node("Assignment", "ASSIGN-DOCK-R1", department_id=DEPARTMENT, role_id=ROLE_BEFORE, source_id=SOURCE_BEFORE, observed_at="2026-10-14T19:00:00Z", effective_at="2026-10-14T19:00:00Z", access="operations"),
        _node("Artifact", HANDOVER, artifact_type="handover", status="open", role_id=ROLE_BEFORE, cited_source_id=SOURCE_BEFORE, body="Synthetic dock handover records ROLE-DOCK-DUTY under roster R1.", access="operations"),
        _node("Artifact", TICKET, artifact_type="ticket", status="open", cited_source_id=SOURCE_BEFORE, body="Synthetic dock operations review ticket cites roster R1.", access="operations"),
        _node("Artifact", LEXICAL_HANDOVER, artifact_type="handover", status="open", role_id=ROLE_BEFORE, cited_source_id="", body="Dock roster text mentions R1, but this record has no source citation edge.", access="operations"),
        _node("SOPVersion", SOP_V2, sop_id="SOP-OPS-9", current=True, escalation_role_id="ROLE-FACILITIES-DUTY", conflict_status="unresolved", access="operations"),
        _node("SOPVersion", SOP_V3, sop_id="SOP-OPS-9", current=True, escalation_role_id="ROLE-MATERIALS-LEAD", conflict_status="unresolved", access="operations"),
    ]


def base_edges() -> list[dict[str, Any]]:
    return [
        _edge(INCIDENT, "AFFECTS", EQUIPMENT, "REL-INC207-DOCKCART", access="operations"),
        _edge(EQUIPMENT, "SUPPORTS", SERVICE, "REL-DOCKCART-INBOUND", access="operations"),
        _edge(SERVICE, "SERVES", DEPARTMENT, "REL-INBOUND-RECEIVING", access="operations"),
        _edge(DEPARTMENT, "HAS_ASSIGNMENT", "ASSIGN-DOCK-R1", "REL-DOCK-R1-ASSIGNMENT", access="operations"),
        _edge("ASSIGN-DOCK-R1", "ASSIGNS", ROLE_BEFORE, "REL-DOCK-R1-ROLE", access="operations"),
        _edge("ASSIGN-DOCK-R1", "EVIDENCED_BY", SOURCE_BEFORE, "REL-DOCK-R1-SOURCE", access="operations"),
        _edge(HANDOVER, "CITES_SOURCE", SOURCE_BEFORE, "REL-HND2020-CITES-R1", access="operations"),
        _edge(TICKET, "CITES_SOURCE", SOURCE_BEFORE, "REL-TKT88-CITES-R1", access="operations"),
        _edge(SOP_V2, "SOP_CONFLICT", SOP_V3, "REL-SOP-OPS-9-CONFLICT", status="unresolved", access="operations"),
    ]


def update_nodes(payload: dict[str, Any]) -> list[dict[str, Any]]:
    source, assignment = payload["source"], payload["assignment"]
    return [
        _node("Evidence", source["id"], title=source["title"], observed_at=source["observed_at"], effective_at=source["effective_at"], access=source["access"]),
        _node("Assignment", assignment["id"], department_id=assignment["department_id"], role_id=assignment["role_id"], source_id=assignment["source_id"], observed_at=assignment["observed_at"], effective_at=assignment["effective_at"], access=assignment["access"]),
    ]


def update_edges(payload: dict[str, Any]) -> list[dict[str, Any]]:
    source, assignment = payload["source"], payload["assignment"]
    return [
        _edge(source["id"], "SUPERSEDES", payload["supersedes_source_id"], "REL-DOCK-R2-SUPERSEDES-R1", access="operations"),
        _edge(DEPARTMENT, "HAS_ASSIGNMENT", assignment["id"], "REL-DOCK-R2-ASSIGNMENT", access="operations"),
        _edge(assignment["id"], "ASSIGNS", assignment["role_id"], "REL-DOCK-R2-ROLE", access="operations"),
        _edge(assignment["id"], "EVIDENCED_BY", source["id"], "REL-DOCK-R2-SOURCE", access="operations"),
    ]


def _rows(result: dict[str, Any]) -> list[dict[str, Any]]:
    return result_rows(result)


class SourceChangeStore:
    """Graph-only operations over a caller-owned explicit FalkorDB graph."""

    def __init__(self, graph_name: str, lane: str, query: GraphQuery):
        self.graph_name = graph_name
        self.lane = lane
        self.query = query

    def count(self, statement: str) -> int:
        rows = _rows(self.query(statement))
        if len(rows) != 1:
            raise SourceChangeError(f"expected one count row, got {rows!r}")
        return int(next(iter(rows[0].values())))

    def counts(self) -> dict[str, int]:
        return {
            "nodes": self.count("MATCH (n) RETURN count(n) AS count"),
            "relationships": self.count("MATCH ()-[r]->() RETURN count(r) AS count"),
        }

    def _guard_owned_graph(self) -> None:
        node_ids = [
            row.get("id")
            for row in _rows(self.query("MATCH (n) RETURN n.id AS id ORDER BY id"))
        ]
        if not node_ids:
            return
        marker = [row for row in _rows(self.query(
            f"MATCH (r:SourceChangeRun {{id: {cypher_string(self.graph_name)}}}) RETURN r.owner AS owner, r.lane AS lane, r.fixture AS fixture"
        ))]
        if marker != [{"owner": "wardatlas-source-change-cli", "lane": self.lane, "fixture": "synthetic-dock-v1"}]:
            raise SourceChangeError("refusing to seed a nonempty graph without this lane's ownership marker")
        allowed_nodes = {item["id"] for item in base_nodes(self.graph_name, self.lane)}
        allowed_nodes.update(item["id"] for item in update_nodes(update_fixture()))
        if set(node_ids) != allowed_nodes or len(node_ids) != len(allowed_nodes):
            raise SourceChangeError("owned graph inventory differs from the fixed synthetic fixture; refusing mutation")
        rel_ids = [
            row.get("id")
            for row in _rows(self.query("MATCH ()-[r]->() RETURN r.id AS id ORDER BY id"))
        ]
        allowed_rels = {item["id"] for item in base_edges() + update_edges(update_fixture())}
        if set(rel_ids) != allowed_rels or len(rel_ids) != len(allowed_rels):
            raise SourceChangeError("owned graph relationship inventory differs from the fixed fixture; refusing mutation")

    def seed_synthetic(self, payload: str | bytes | dict[str, Any] | None = None) -> dict[str, int]:
        # Validate before the first graph query or mutation.
        canonical = validate_update(update_fixture() if payload is None else payload)
        self._guard_owned_graph()
        before = self.counts()
        for item in base_nodes(self.graph_name, self.lane):
            self.query(_node_query(item))
        for item in base_edges():
            self.query(_edge_query(item))
        apply_validated_update(self, canonical)
        return {"nodes": self.counts()["nodes"] - before["nodes"], "relationships": self.counts()["relationships"] - before["relationships"]}

    def role_path(self, as_of: str) -> dict[str, Any] | None:
        safe_time = cypher_string(as_of)
        statement = f"""
MATCH (i:Incident {{id: '{INCIDENT}'}})-[aff:AFFECTS]->(e:Equipment {{id: '{EQUIPMENT}'}})-[sup:SUPPORTS]->(s:Service {{id: '{SERVICE}'}})-[serves:SERVES]->(d:Department {{id: '{DEPARTMENT}'}})-[has:HAS_ASSIGNMENT]->(a:Assignment)-[assign:ASSIGNS]->(r:Role),
      (a)-[ev:EVIDENCED_BY]->(src:Evidence)
WHERE i.access = 'operations' AND e.access = 'operations' AND s.access = 'operations'
  AND d.access = 'operations' AND a.access = 'operations' AND r.access = 'operations' AND src.access = 'operations'
  AND aff.access = 'operations' AND sup.access = 'operations' AND serves.access = 'operations'
  AND has.access = 'operations' AND assign.access = 'operations' AND ev.access = 'operations'
  AND a.effective_at <= {safe_time}
WITH i, e, s, d, a, r, src ORDER BY a.effective_at DESC, a.observed_at DESC
RETURN i.id AS incident_id, e.id AS equipment_id, s.id AS service_id, d.id AS department_id,
       r.id AS role_id, a.id AS assignment_id, a.source_id AS source_id,
       a.observed_at AS observed_at, a.effective_at AS effective_at, src.title AS source_title
LIMIT 1
""".strip()
        rows = _rows(self.query(statement))
        return rows[0] if rows else None

    def source_versions(self) -> list[dict[str, Any]]:
        before = _rows(self.query(f"""
MATCH (r1:Evidence {{id: '{SOURCE_BEFORE}'}})
WHERE r1.access = 'operations'
RETURN r1.id AS source_id, r1.title AS title, r1.observed_at AS observed_at,
       r1.effective_at AS effective_at
""".strip()))
        after = _rows(self.query(f"""
MATCH (r2:Evidence {{id: '{SOURCE_AFTER}'}})-[sup:SUPERSEDES]->(r1:Evidence {{id: '{SOURCE_BEFORE}'}})
WHERE r1.access = 'operations' AND r2.access = 'operations' AND sup.access = 'operations'
RETURN r2.id AS source_id, r2.title AS title, r2.observed_at AS observed_at,
       r2.effective_at AS effective_at, r1.id AS supersedes
""".strip()))
        rows = before + after
        rows.sort(key=lambda row: (row.get("observed_at") or "", row.get("source_id") or ""))
        return rows

    def review_candidates(self, as_of: str) -> list[str]:
        statement = f"""
MATCH (r2:Evidence {{id: '{SOURCE_AFTER}'}})-[sup:SUPERSEDES]->(old:Evidence {{id: '{SOURCE_BEFORE}'}})<-[cite:CITES_SOURCE]-(artifact:Artifact)
WHERE r2.access = 'operations' AND old.access = 'operations' AND artifact.access = 'operations'
  AND sup.access = 'operations' AND cite.access = 'operations'
  AND artifact.status = 'open' AND r2.effective_at <= {cypher_string(as_of)}
RETURN artifact.id AS artifact_id ORDER BY artifact_id
""".strip()
        return [str(row["artifact_id"]) for row in _rows(self.query(statement))]

    def sop_conflicts(self) -> list[dict[str, Any]]:
        return _rows(self.query(f"""
MATCH (v2:SOPVersion {{id: '{SOP_V2}'}})-[conf:SOP_CONFLICT]->(v3:SOPVersion {{id: '{SOP_V3}'}})
WHERE v2.access = 'operations' AND v3.access = 'operations' AND conf.access = 'operations'
RETURN v2.id AS version_a, v3.id AS version_b, conf.status AS conflict_status,
       v2.escalation_role_id AS claim_a, v3.escalation_role_id AS claim_b
""".strip()))


def apply_validated_update(store: SourceChangeStore, canonical: Update) -> None:
    """Append the fixed validated R2 source and assignment via MERGE."""
    # Re-check an internal caller cannot bypass strict schema validation.
    canonical = validate_update(canonical)
    for item in update_nodes(canonical):
        store.query(_node_query(item))
    for item in update_edges(canonical):
        store.query(_edge_query(item))


def apply_update(store: SourceChangeStore, payload: str | bytes | dict[str, Any]) -> None:
    canonical = validate_update(payload)
    apply_validated_update(store, canonical)


def snapshot(store: SourceChangeStore, as_of: str) -> dict[str, Any]:
    normalized = normalize_time(as_of)
    path = store.role_path(normalized)
    path_ids = None
    evidence = None
    if path:
        path_ids = [path[key] for key in ("incident_id", "equipment_id", "service_id", "department_id", "role_id")]
        evidence = {key: path[key] for key in ("assignment_id", "source_id", "observed_at", "effective_at", "source_title")}
    return {
        "as_of": normalized,
        "role_path": path_ids,
        "role_path_evidence": evidence,
        "source_versions": store.source_versions(),
        "review_candidates": store.review_candidates(normalized),
        "sop_conflicts": store.sop_conflicts(),
        "coverage_status": "explicit_edges_only",
        "unlinked_artifact_status": "unknown",
        "external_effects": [],
    }


def validate_graph_name(graph_name: str, lane: str) -> None:
    if not re.fullmatch(rf"wardatlas_source_{re.escape(lane)}_[A-Za-z0-9_]{{4,64}}", graph_name):
        raise SourceChangeError("graph name must be unique, owned, and prefixed wardatlas_source_<WORKSHOP_LANE>_")


def lane_port(port: int) -> bool:
    try:
        base = int(os.environ["WORKSHOP_PORT_BASE"])
        span = int(os.environ["WORKSHOP_PORT_SPAN"])
    except (KeyError, ValueError) as exc:
        raise SourceChangeError("WORKSHOP_PORT_BASE and WORKSHOP_PORT_SPAN must define the lane database range") from exc
    return base <= port < base + span and 1024 <= port <= 65535


def connect_local_falkordb(port: int) -> RespConnection:
    if not lane_port(port):
        raise SourceChangeError("database port must be explicitly selected from this WORKSHOP lane's port range")
    client = RespConnection("127.0.0.1", port)
    try:
        modules = module_list_maps(text(client.command("MODULE", "LIST")))
        found = next((module for module in modules if str(module.get("name", "")).lower() in {"graph", "falkordb"}), None)
        if not found or int(found.get("ver", 0)) != 60002:
            raise SourceChangeError("connected loopback server does not report actual pinned FalkorDB 6.0.2 (60002)")
    except Exception:
        client.close()
        raise
    return client


def cli_main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db-port", type=int, required=True, help="explicit FalkorDB loopback port from this workshop lane")
    parser.add_argument("--graph-name", required=True, help="unique owned graph: wardatlas_source_<WORKSHOP_LANE>_<suffix>")
    parser.add_argument("--seed-synthetic", action="store_true", required=True, help="seed only the fixed synthetic dock fixture and roster update")
    parser.add_argument("--at", required=True, help="ISO-8601 timestamp with timezone; output normalizes to UTC")
    args = parser.parse_args(argv)
    client: RespConnection | None = None
    try:
        lane = os.environ.get("WORKSHOP_LANE", "")
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,32}", lane):
            raise SourceChangeError("WORKSHOP_LANE must be set to the active workshop lane")
        validate_graph_name(args.graph_name, lane)
        as_of = normalize_time(args.at)
        if not lane_port(args.db_port):
            raise SourceChangeError("--db-port must be explicitly selected from this WORKSHOP lane's port range")
        # Static payload validation occurs before opening the socket or issuing a graph query.
        update = validate_update(update_fixture())
        client = connect_local_falkordb(args.db_port)

        graph_query_timings: list[float] = []

        def query(cypher: str) -> dict[str, Any]:
            started = time.perf_counter()
            result = query_result(client.command("GRAPH.QUERY", args.graph_name, cypher))
            graph_query_timings.append((time.perf_counter() - started) * 1000)
            return result

        store = SourceChangeStore(args.graph_name, lane, query)
        store.seed_synthetic(update)
        result = snapshot(store, as_of)
        result["graph_query_count"] = len(graph_query_timings)
        result["graph_query_elapsed_ms"] = round(sum(graph_query_timings), 3)
        print(json.dumps(result, sort_keys=True, separators=(",", ":")))
        return 0
    except Exception as exc:
        print(f"source-change error: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    finally:
        if client is not None:
            client.close()


if __name__ == "__main__":
    raise SystemExit(cli_main())

"""Read-only, bounded comparison of explicit SOP evidence/relationships.

No version ordering implies lineage, currency, ownership or procedure. Query
parameters select a fixed synthetic SOP family, never arbitrary graph roots.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from urllib.parse import parse_qs

from wardatlas_shift_board import normalize_timestamp

VERSIONS = ("SOP-OPS-9-V1", "SOP-OPS-9-V2", "SOP-OPS-9-V3")
EQUIPMENT = ("CONVEYOR-C3", "CONVEYOR-C4")
MAX_ROWS = 64
HTML_PATH = Path(__file__).parent / "ui/sop-compare.html"


def parse_parameters(query: str) -> dict:
    values = parse_qs(query, keep_blank_values=True, max_num_fields=12)
    if set(values) - {"known_at", "left", "right", "equipment", "scope"}:
        raise ValueError("Invalid comparison request.")
    if "known_at" not in values or any(len(v) != 1 or not v[0] for v in values.values()):
        raise ValueError("Specify each selection exactly once.")
    if values.get("scope", ["operations"]) != ["operations"]:
        raise ValueError("Invalid comparison request.")
    for key in ("left", "right"):
        if key in values and values[key][0] not in VERSIONS:
            raise ValueError("Invalid comparison selection.")
    if "equipment" in values and values["equipment"][0] not in EQUIPMENT:
        raise ValueError("Invalid equipment selection.")
    value = values["known_at"][0]
    if not re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:Z|[+-](?:[01]\d|2[0-3]):[0-5]\d)", value):
        raise ValueError("Use an explicit ISO-8601 timestamp.")
    try:
        known = normalize_timestamp(value)
    except (ValueError, OverflowError) as exc:
        raise ValueError("Invalid timestamp.") from exc
    return {"known_at": known, **{k: values[k][0] for k in ("left", "right", "equipment") if k in values}}


def build_queries(known_at: str) -> dict[str, str]:
    # Values are encoded as Cypher parameters, never interpolated into patterns.
    known_at = parse_parameters("known_at=" + known_at.replace("+", "%2B"))["known_at"]
    params = "CYPHER " + " ".join(f"{k}={json.dumps(v)}" for k, v in {
        "cutoff": known_at, "versions": VERSIONS, "equipment": EQUIPMENT, "access": "operations"
    }.items()) + "\n"
    root = """MATCH (v:SOPVersion)-[ev:EVIDENCED_BY]->(src:Evidence)
WHERE v.id IN $versions AND v.access=$access AND ev.access=$access
  AND src.access=$access AND src.observed_at <= $cutoff
"""
    return {
        "versions": params + root + """RETURN v.id AS version_id, v.referenced_equipment_id AS referenced_equipment_id,
src.id AS source_id, src.title AS source_title, src.observed_at AS observed_at,
ev.id AS evidence_edge_id ORDER BY version_id, source_id LIMIT 65""",
        "applies": params + """MATCH (v:SOPVersion)-[ev:EVIDENCED_BY]->(src:Evidence),
(v)-[ap:APPLIES_TO]->(e:Equipment)
WHERE v.id IN $versions AND v.access=$access AND ev.access=$access
AND src.access=$access AND src.observed_at <= $cutoff
AND ap.access=$access AND e.access=$access AND e.id IN $equipment
RETURN v.id AS version_id, e.id AS equipment_id, ap.id AS edge_id
ORDER BY version_id, equipment_id LIMIT 65""",
        "claims": params + """MATCH (v:SOPVersion)-[ev:EVIDENCED_BY]->(src:Evidence),
(v)-[claim:CLAIMS_ROLE]->(r:Role)
WHERE v.id IN $versions AND v.access=$access AND ev.access=$access
AND src.access=$access AND src.observed_at <= $cutoff
AND claim.access=$access AND r.access=$access
RETURN v.id AS version_id, r.id AS role_id, claim.id AS edge_id
ORDER BY version_id, role_id LIMIT 65""",
        "conflicts": params + """MATCH (a:SOPVersion)-[ea:EVIDENCED_BY]->(sa:Evidence),
(b:SOPVersion)-[eb:EVIDENCED_BY]->(sb:Evidence),
(a)-[c:SOP_CONFLICT]->(b), (a)-[aa:APPLIES_TO]->(e:Equipment),
(b)-[ab:APPLIES_TO]->(e), (a)-[ca:CLAIMS_ROLE]->(ra:Role), (b)-[cb:CLAIMS_ROLE]->(rb:Role)
WHERE a.id IN $versions AND b.id IN $versions AND e.id IN $equipment
AND a.access=$access AND b.access=$access AND ea.access=$access AND eb.access=$access
AND sa.access=$access AND sb.access=$access AND sa.observed_at <= $cutoff AND sb.observed_at <= $cutoff
AND c.access=$access AND aa.access=$access AND ab.access=$access AND e.access=$access
AND ca.access=$access AND cb.access=$access AND ra.access=$access AND rb.access=$access
RETURN a.id AS from_id, b.id AS to_id, c.id AS edge_id, c.status AS status
ORDER BY from_id, to_id LIMIT 65""",
    }


def link(a: str, kind: str, b: str, edge_id: str | None) -> dict:
    return {"from": a, "relationship": kind, "to": b, "edge_id": edge_id}


def project(selection: dict, rows: dict[str, list[dict]]) -> dict:
    """Project only prefiltered rows; retain property/edge distinctions."""
    if any(len(v) > MAX_ROWS for v in rows.values()):
        raise RuntimeError("Comparison unavailable.")
    versions = {}
    for row in rows["versions"]:
        identifier = row["version_id"]
        # Fail closed for malformed source times, not a property-only fallback.
        try:
            observed = normalize_timestamp(row["observed_at"])
            if observed > selection["known_at"]:
                continue
        except (ValueError, TypeError, OverflowError):
            continue
        item = versions.setdefault(identifier, {
            "id": identifier, "sourced_properties": {"referenced_equipment_id": row.get("referenced_equipment_id")},
            "sources": [], "applicability_edges": [], "role_claims": [],
        })
        item["sources"].append({"id": row["source_id"], "title": row["source_title"], "observed_at": row["observed_at"],
                                "evidence_link": link(identifier, "EVIDENCED_BY", row["source_id"], row["evidence_edge_id"])})
    for row in rows["applies"]:
        if row["version_id"] in versions:
            value = link(row["version_id"], "APPLIES_TO", row["equipment_id"], row["edge_id"])
            if value not in versions[row["version_id"]]["applicability_edges"]:
                versions[row["version_id"]]["applicability_edges"].append(value)
    for row in rows["claims"]:
        if row["version_id"] in versions:
            claim = {"label": "unverified SOP claim", "edge": link(row["version_id"], "CLAIMS_ROLE", row["role_id"], row["edge_id"])}
            if claim not in versions[row["version_id"]]["role_claims"]:
                versions[row["version_id"]]["role_claims"].append(claim)
    identifiers = sorted(versions)
    equipment = sorted({e["to"] for v in versions.values() for e in v["applicability_edges"]})
    # Defaults merely arrange two panes. A hidden explicit selection is never
    # replaced with a visible 'winner', echoed, counted or offered as an option.
    left = selection.get("left", identifiers[-2] if len(identifiers) > 1 else (identifiers[0] if identifiers else None))
    right = selection.get("right", identifiers[-1] if len(identifiers) > 1 else None)
    left = left if left in versions else None
    right = right if right in versions else None
    focus = selection.get("equipment", equipment[-1] if equipment else None)
    focus = focus if focus in equipment else None
    for v in versions.values():
        v["focus_applicability"] = "explicit APPLIES_TO witness" if focus and any(e["to"] == focus for e in v["applicability_edges"]) else "unknown / not evidenced"
        v["role_claim_state"] = "unverified claims only" if v["role_claims"] else "unknown / no evidenced CLAIMS_ROLE"
    conflict = {"state": "unknown", "edges": []}
    if left and right and left != right:
        for row in rows["conflicts"]:
            if {row["from_id"], row["to_id"]} == {left, right}:
                edge = {**link(row["from_id"], "SOP_CONFLICT", row["to_id"], row["edge_id"]), "status": row["status"]}
                if edge not in conflict["edges"]:
                    conflict["edges"].append(edge)
        if conflict["edges"]:
            conflict["state"] = "unresolved" if all(e["status"] == "unresolved" for e in conflict["edges"]) else "explicit relation; status not established"
    a, b = versions.get(left), versions.get(right)
    differences = []
    for kind, key in (("version_identity", "id"), ("sourced_reference_property", "sourced_properties"),
                      ("applicability_relationships", "applicability_edges"), ("unverified_role_claims", "role_claims"),
                      ("source_observations", "sources")):
        differences.append({"type": kind, "left": a[key] if a else None, "right": b[key] if b else None,
                            "state": "unknown" if not a or not b else "same witness" if a[key] == b[key] else "different witnesses"})
    differences.append({"type": "explicit_conflict_relation", **conflict})
    return {"scope": "operations", "known_at": selection["known_at"], "state": "source-known evidence" if identifiers else "unknown / no source-known evidence",
            "options": {"versions": identifiers, "equipment": equipment}, "selection": {"left": left, "right": right, "equipment": focus},
            "versions": list(versions.values()), "left": a, "right": b, "pair_state": "comparable witnesses" if a and b else "comparison unavailable / source evidence unknown",
            "differences": differences, "conflict": conflict, "selected_winner": None, "procedure": None, "lineage_edges": [],
            "limitations": ["Sourced reference properties are not applicability witnesses.", "Missing relationships are unknown, not agreement or resolution.",
                            "Unverified role claims are not current owners or a procedure.", "No document redline or inferred version lineage."]}


def compare(service, selection: dict) -> dict:
    from verify_falkordb_live import query_result, result_rows
    queries = build_queries(selection["known_at"])
    with service.graph_lock:
        connection = service._connection()
        try:
            rows = {key: result_rows(query_result(connection.command("GRAPH.RO_QUERY", service.graph_name, query))) for key, query in queries.items()}
        finally:
            connection.close()
    return project(selection, rows)


def mount_sop_compare(handler, service, parsed) -> bool:
    if parsed.path == "/sop-compare":
        handler._send(200, HTML_PATH.read_text(encoding="utf-8"))
        return True
    if parsed.path != "/api/sop-claim-comparison":
        return False
    try:
        selection = parse_parameters(parsed.query)
    except ValueError:
        handler._error(400, "Invalid SOP comparison request.")
        return True
    handler._send(200, compare(service, selection))
    return True

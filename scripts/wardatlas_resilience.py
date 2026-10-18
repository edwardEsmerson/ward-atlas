"""Bounded read-only supporting-edge omission, not an operational outage model.

The two panes execute separate parameterized Cypher statements. An omission
never deletes an edge, copies a graph, or post-filters baseline result rows.
Only an independently visible baseline witness licenses naming the assumption.
"""
from __future__ import annotations

from copy import deepcopy
from urllib.parse import parse_qs

from wardatlas_knowledge_timeline import (
    _claim_query, _record_query, _considered_query, _conflict_query, project_timeline,
)
from wardatlas_shift_board import CONVEYOR_PATH_QUERY, CONVEYOR_EVENT_QUERY

AS_OF = "2026-10-14T18:30:00Z"
KNOWN_AT = "2026-10-14T16:05:00Z"
MODES = ("baseline", "omit-central-sterile-owner", "omit-v3-evidence")
OMISSIONS = {
    "omit-central-sterile-owner": {"from": "DEPT-CENTRAL-STERILE", "relationship": "OWNED_BY", "to": "ROLE-STERILE-OPS-COORD"},
    "omit-v3-evidence": {"from": "SOP-OPS-9-V3", "relationship": "EVIDENCED_BY", "to": "SRC-SOP-CURRENT-V3"},
}


def validate_request(query_string: str) -> str:
    """Reject arbitrary selectors and duplicates before any graph connection."""
    # parse_qs silently ignores malformed bare fields unless strict_parsing.
    try:
        values = parse_qs(query_string, keep_blank_values=True, strict_parsing=True, max_num_fields=4)
    except ValueError:
        raise ValueError("Invalid evidence experiment request.") from None
    if set(values) - {"scope", "mode"} or any(len(v) != 1 for v in values.values()):
        raise ValueError("Invalid evidence experiment request.")
    if values.get("scope", ["operations"]) != ["operations"]:
        raise ValueError("Invalid evidence experiment request.")
    mode = values.get("mode", ["baseline"])[0]
    if mode not in MODES:
        raise ValueError("Invalid evidence experiment request.")
    return mode


def _guard(query: str, predicate: str) -> str:
    before, separator, after = query.partition("\nRETURN ")
    if not separator:
        raise ValueError("Expected a bounded read query")
    return before + "\n  AND " + predicate + separator + after


def build_queries(mode: str) -> dict[str, str]:
    if mode not in MODES:
        raise ValueError("Invalid evidence experiment request.")
    # All values are allowlisted constants; parameters affect relationship
    # eligibility in FalkorDB, not Python/JS filtering of baseline output.
    params = (
        "CYPHER omit_owner=" + ("true" if mode == MODES[1] else "false")
        + " omit_v3=" + ("true" if mode == MODES[2] else "false")
        + f" as_of='{AS_OF}' known_at='{KNOWN_AT}'"
        + " omitted_department='DEPT-CENTRAL-STERILE' omitted_role='ROLE-STERILE-OPS-COORD'"
        + " omitted_version='SOP-OPS-9-V3' omitted_source='SRC-SOP-CURRENT-V3'\n"
    )
    ownership = _guard(CONVEYOR_PATH_QUERY,
        "NOT ($omit_owner AND d.id = $omitted_department AND r.id = $omitted_role)"
        " AND isrc.observed_at <= $as_of AND esrc.observed_at <= $as_of"
        " AND ssrc.observed_at <= $as_of AND owns.observed_at <= $as_of")
    claim = _guard(_claim_query(KNOWN_AT),
        "NOT ($omit_v3 AND v.id = $omitted_version AND source.id = $omitted_source)")
    considered = _guard(_considered_query("DEC-OPS-9-REVIEW-01", KNOWN_AT),
        "NOT ($omit_v3 AND version.id = $omitted_version AND source.id = $omitted_source)")
    conflict = _guard(_conflict_query(KNOWN_AT),
        "NOT ($omit_v3 AND ((v2.id = $omitted_version AND source2.id = $omitted_source)"
        " OR (v3.id = $omitted_version AND source3.id = $omitted_source)))")
    event = _guard(CONVEYOR_EVENT_QUERY, "event.event_at <= $as_of AND src.observed_at <= $as_of")
    event = event.replace("ORDER BY event.event_at", "ORDER BY event.event_at DESC")
    queries = {"ownership": ownership, "claims": claim, "record": _record_query(KNOWN_AT),
               "considered": considered, "conflict": conflict, "event": event}
    return {key: params + query.replace("'" + KNOWN_AT + "'", "$known_at")
            + ("\nLIMIT 1" if key == "event" else "\nLIMIT 64") for key, query in queries.items()}


def render_pane(ownership_rows, claim_rows, record_rows, considered_rows, conflict_rows, omitted_owner=False):
    paths = []
    for row in ownership_rows:
        paths.append({
            "path": [row[key] for key in ("incident_id", "equipment_id", "service_id", "department_id", "role_id")],
            "source_ids": [row[p + "_source_id"] for p in ("incident", "equipment", "service", "ownership")],
            "source_titles": [row[p + "_source_title"] for p in ("incident", "equipment", "service", "ownership")],
            "observed_at": [row[p + "_observed_at"] for p in ("incident", "equipment", "service", "ownership")],
        })
    paths.sort(key=lambda item: item["path"])
    knowledge = project_timeline(KNOWN_AT, claim_rows, record_rows, considered_rows, conflict_rows)
    sources = {}
    def add(identifier, title, observed):
        sources[(identifier, title, observed)] = {"source_id": identifier, "title": title, "observed_at": observed}
    for path in paths:
        for identifier, title, observed in zip(path["source_ids"], path["source_titles"], path["observed_at"]):
            add(identifier, title, observed)
    for source in [c["source"] for c in knowledge["claims"]] + ([knowledge["decision_record"]["source"]] if knowledge["decision_record"] else []):
        add(source["id"], source["title"], source["observed_at"])
    state = "unknown" if not paths else ("incomplete/unknown" if omitted_owner or len(paths) < 2 else "evidence-backed")
    return {"ownership": {"state": state, "paths": paths}, "knowledge": knowledge,
            "sources": [sources[key] for key in sorted(sources)]}


def _authorized(mode, pane):
    if mode == "baseline":
        return True
    if mode == "omit-central-sterile-owner":
        return any(p["path"][-2:] == ["DEPT-CENTRAL-STERILE", "ROLE-STERILE-OPS-COORD"] for p in pane["ownership"]["paths"])
    return any(c["version_id"] == "SOP-OPS-9-V3" and c["source"]["id"] == "SRC-SOP-CURRENT-V3" for c in pane["knowledge"]["claims"])


def response(mode, actual, hypothetical, authorized):
    if mode not in MODES:
        raise ValueError("Invalid evidence experiment request.")
    authorized = bool(authorized and _authorized(mode, actual))
    if not authorized:
        hypothetical = render_pane([], [], [], [], [])
    return {
        "scope": "operations", "mode": mode if authorized else None,
        "as_of": AS_OF, "known_at": KNOWN_AT,
        "actual": actual, "hypothetical": hypothetical,
        "omission": deepcopy(OMISSIONS.get(mode)) if authorized else None,
        "dependent_evidence_state": "not-selected" if mode == "baseline" else "unknown",
        "source_facts_unchanged": True, "selected_version": None,
        "resolution_asserted": False, "safe_asserted": False, "role_disappearance_asserted": False,
    }


def experiment(mode, read_rows):
    """Run against one locked live graph via a GRAPH.RO_QUERY callback."""
    if mode not in MODES:
        raise ValueError("Invalid evidence experiment request.")
    def read(which):
        queries = build_queries(which)
        event = read_rows(queries["event"])
        ownership = read_rows(queries["ownership"]) if event and event[0]["value"] == "unavailable" else []
        return render_pane(ownership, read_rows(queries["claims"]), read_rows(queries["record"]),
                           read_rows(queries["considered"]), read_rows(queries["conflict"]), which == MODES[1])
    actual = read("baseline")
    allowed = _authorized(mode, actual)
    hypothetical = read(mode) if mode != "baseline" and allowed else deepcopy(actual)
    return response(mode, actual, hypothetical, allowed)

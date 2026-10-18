"""Bounded, live-only evidence replay over the accepted timeline services.

No queries, seed data, caches, writes, search, or model route live here. The
service owns evidence/time/access filtering; only its returned links are drawn.
"""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from urllib.parse import parse_qs
from wardatlas_source_branch import SOURCE_ID

FRAMES = (
    ("mainline", "2026-10-14T15:59:59Z"),
    ("mainline", "2026-10-14T16:00:00Z"),
    ("mainline", "2026-10-14T16:04:00Z"),
    ("mainline", "2026-10-14T16:05:00Z"),
    ("hypothetical", "2026-10-14T16:05:00Z"),
    ("hypothetical", "2026-10-14T16:06:00Z"),
)
RELATIONSHIPS = frozenset(("APPLIES_TO", "CLAIMS_ROLE", "EVIDENCED_BY", "SOP_CONFLICT", "TRACKS", "CONSIDERS"))
HTML = Path(__file__).resolve().parent / "ui/evidence_replay.html"


def parse_parameters(query: str) -> tuple[int, str]:
    values = parse_qs(query, keep_blank_values=True, max_num_fields=8)
    if not {"frame", "mode"} <= values.keys() or values.keys() - {"frame", "mode", "scope"}:
        raise ValueError("Invalid replay selection.")
    if any(len(items) != 1 for items in values.values()) or values.get("scope", ["operations"]) != ["operations"]:
        raise ValueError("Invalid replay selection.")
    if values["frame"][0] not in tuple(str(i) for i in range(len(FRAMES))):
        raise ValueError("Invalid replay selection.")
    frame, mode = int(values["frame"][0]), values["mode"][0]
    if FRAMES[frame][0] != mode:
        raise ValueError("Invalid replay selection.")
    return frame, mode


def project_frame(frame: int, mode: str, projection: dict, scenario: dict | None = None) -> dict:
    """Detach a single projection; never use discovery labels as graph links."""
    if type(frame) is not int or not 0 <= frame < len(FRAMES) or FRAMES[frame][0] != mode:
        raise ValueError("Invalid replay selection.")
    result = {
        "frame": frame, "mode": mode, "known_at": FRAMES[frame][1],
        "state": "unknown", "generic_unknown": True,
        "nodes": [], "edges": [], "sources": [], "read_only": True,
        "source_fact_modified": False, "negative_conclusion_asserted": False,
    }
    # A branch without its graph-selected assumption is never a mainline fallback.
    if projection.get("generic_unknown", True) or (mode == "hypothetical" and not scenario):
        return result
    p = deepcopy(projection)
    nodes, edges, sources = {}, {}, {}

    def source(raw):
        value = {key: raw[key] for key in ("id", "title", "observed_at")}
        if mode == "hypothetical" and scenario and raw["id"] == SOURCE_ID:
            value["observed_at"] = scenario["actual_source_observed_at"]
            value["assumed_observed_at"] = scenario["assumed_observed_at"]
        sources[value["id"]] = value
        return value

    def node(identity, kind, **metadata):
        nodes[identity] = {"id": identity, "kind": kind, **metadata}

    def link(raw, citations):
        rel = raw["relationship"]
        if rel not in RELATIONSHIPS or raw["from"] not in nodes or raw["to"] not in nodes:
            raise ValueError("Unavailable relationship witness.")
        identity = "|".join((raw["from"], rel, raw["to"]))
        item = {
            "id": identity, "from": raw["from"], "relationship": rel, "to": raw["to"],
            "epistemic_type": "source-fact" if rel == "CLAIMS_ROLE" else "graph-derived",
            "label": "unverified SOP claim" if rel == "CLAIMS_ROLE" else "graph-derived relationship",
            "sources": sorted(citations, key=lambda item: item["id"]),
        }
        if rel == "SOP_CONFLICT":
            item["status"] = raw["status"]
        edges[identity] = item

    claims = {}
    for claim in p["claims"]:
        citation = source(claim["source"])
        claims[claim["version_id"]] = citation
        node(claim["version_id"], "claim", label=claim["claim_label"])
        node(claim["role_id"], "role")
        node(claim["applicability_edge"]["to"], "equipment")
        node(citation["id"], "evidence", **{k: v for k, v in citation.items() if k != "id"})
        for edge in (claim["claim_edge"], claim["applicability_edge"], claim["source"]["evidence_link"]):
            link(edge, [citation])
    record = p["decision_record"]
    if record:
        citation = source(record["source"])
        node(record["id"], "record", **{key: record[key] for key in ("title", "recorded_state", "recorded_statement")})
        # The root is justified by this explicit TRACKS edge, not a path label.
        node(record["register_edge"]["from"], "register")
        node(citation["id"], "evidence", **{k: v for k, v in citation.items() if k != "id"})
        link(record["register_edge"], [citation])
        link(record["source"]["evidence_link"], [citation])
        for edge in p["decision_considers_edges"]:
            if edge["to"] in claims:
                link(edge, [citation, claims[edge["to"]]])
    if p["conflict"]:
        edge = p["conflict"]["edge"]
        if edge["from"] in claims and edge["to"] in claims:
            link(edge, [claims[edge["from"]], claims[edge["to"]]])
    result.update({
        "state": p["claim_context_state"], "generic_unknown": False,
        "nodes": [nodes[key] for key in sorted(nodes)],
        "edges": [edges[key] for key in sorted(edges)],
        "sources": [sources[key] for key in sorted(sources)],
    })
    return result


def replay_frame(service, frame: int, mode: str) -> dict:
    # Validate even internal callers before a lock, connection or query.
    if type(frame) is not int or not 0 <= frame < len(FRAMES) or FRAMES[frame][0] != mode:
        raise ValueError("Invalid replay selection.")
    known_at = FRAMES[frame][1]
    if mode == "mainline":
        return project_frame(frame, mode, service.decision_timeline(known_at))
    branch = service.decision_source_branch(known_at)["hypothetical_branch"]
    return project_frame(frame, mode, branch["projection"], branch.get("scenario"))


def mount_replay(handler, service, parsed) -> bool:
    if parsed.path == "/time-machine":
        handler._send(200, HTML.read_text(encoding="utf-8"))
    elif parsed.path == "/api/replay-frame":
        try:
            frame, mode = parse_parameters(parsed.query)
        except ValueError:
            handler._error(400, "Invalid replay selection.")
            return True
        try:
            handler._send(200, replay_frame(service, frame, mode))
        except Exception:
            handler._send(503, {"state": "unavailable", "message": "Live graph unavailable. No replay evidence loaded."})
    else:
        return False
    return True

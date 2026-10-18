"""Bounded semantic comparison of live, public knowledge-time witnesses.

This composes the accepted timeline/branch services; it never runs Cypher or
writes graph/action state. The caller holds the service's reentrant graph lock
across both reads. That excludes this application's writers, not external DB
clients, and is deliberately NOT a database transaction/isolation guarantee.
"""

from __future__ import annotations

from copy import deepcopy
import re
from typing import Any

from wardatlas_shift_board import normalize_timestamp
from wardatlas_source_branch import unknown_branch_projection


MODES = frozenset({"mainline", "hypothetical"})
KINDS = ("claim", "source_observation", "conflict_relationship", "decision_record", "considered_link")
MAX_WITNESSES_PER_SIDE = 64
PARAMETERS = ("left_known_at", "right_known_at", "left_mode", "right_mode")


def normalize_selections(left_known_at: str, right_known_at: str, left_mode: str, right_mode: str) -> tuple[str, str, str, str]:
    """Validate both sides before either graph read, with no clock defaults."""
    if left_mode not in MODES or right_mode not in MODES:
        raise ValueError("Choose mainline or hypothetical for each snapshot.")
    # datetime.fromisoformat normalizes offsets such as +00:60 to +01:00.
    # They are not valid ISO offset components and must reject before reads.
    for value in (left_known_at, right_known_at):
        match = re.search(r"[+-](\d{2}):(\d{2})$", value) if isinstance(value, str) else None
        if match and (int(match[1]) > 23 or int(match[2]) > 59):
            raise ValueError("Invalid timestamp; use an explicit ISO-8601 offset.")
    try:
        return normalize_timestamp(left_known_at), normalize_timestamp(right_known_at), left_mode, right_mode
    except OverflowError as exc:
        raise ValueError("Invalid timestamp; use an explicit ISO-8601 offset.") from exc


def parse_parameters(values: dict[str, list[str]]) -> tuple[str, str, str, str]:
    if set(values) - {*PARAMETERS, "scope"}:
        raise ValueError("Unknown knowledge comparison parameter.")
    if any(key not in values or len(values[key]) != 1 or not values[key][0] for key in PARAMETERS):
        raise ValueError("Specify each snapshot time and mode exactly once.")
    return normalize_selections(*(values[key][0] for key in PARAMETERS))


def _edge(value: Any, source: str, kind: str, target: str) -> bool:
    return isinstance(value, dict) and all(value.get(key) == expected for key, expected in (
        ("from", source), ("relationship", kind), ("to", target),
    ))


def _source(value: Any, subject: str) -> bool:
    return isinstance(value, dict) and all(isinstance(value.get(key), str) and value[key] for key in ("id", "title", "observed_at")) and _edge(value.get("evidence_link"), subject, "EVIDENCED_BY", value["id"])


def _claim_valid(claim: dict) -> bool:
    version, role = claim.get("version_id"), claim.get("role_id")
    applicability = claim.get("applicability_edge", {})
    return bool(version and role and _edge(claim.get("claim_edge"), version, "CLAIMS_ROLE", role)
                and _source(claim.get("source"), version)
                and applicability.get("to")
                and _edge(applicability, version, "APPLIES_TO", applicability["to"]))


def _record_valid(record: dict) -> bool:
    identifier, register = record.get("id"), record.get("register_id")
    return bool(identifier and register and _source(record.get("source"), identifier)
                and _edge(record.get("register_edge"), register, "TRACKS", identifier)
                and record.get("provenance_path") == [record["register_edge"], record["source"]["evidence_link"]])


def snapshot_from_projection(known_at: str, mode: str, projection: dict, *, scenario: dict | None = None, mainline: dict | None = None) -> dict:
    """Copy a public projection and distinguish actual from hypothetical time.

    The override attaches only to a returned source-backed claim whose public
    evidence link matches mainline AND whose projected time actually differs.
    No hard-coded source/version ID or property-only fallback creates facts.
    """
    result = {"known_at": known_at, "mode": mode, "availability": "comparison-unavailable"}
    if mode == "hypothetical" and not scenario:
        result["projection"] = unknown_branch_projection(known_at)
        return result
    projected = deepcopy(projection)
    claims = projected.get("claims", [])
    record = projected.get("decision_record")
    if any(not _claim_valid(claim) for claim in claims) or (record and not _record_valid(record)):
        result["projection"] = unknown_branch_projection(known_at)
        return result
    if scenario:
        result["scenario"] = deepcopy(scenario)
        main_sources = {claim["source"]["id"]: claim["source"] for claim in (mainline or {}).get("claims", []) if _claim_valid(claim)}
        for claim in claims:
            source = claim["source"]
            actual = main_sources.get(source["id"])
            if (actual and actual["evidence_link"] == source["evidence_link"]
                    and actual["observed_at"] != source["observed_at"]
                    and actual["observed_at"] == scenario.get("actual_source_observed_at")
                    and source["observed_at"] == scenario.get("assumed_observed_at")):
                source["assumed_observed_at"] = source["observed_at"]
                source["observed_at"] = actual["observed_at"]
                source["observation_mode"] = "hypothetical"
    result["projection"] = projected
    if not projected.get("generic_unknown", True):
        result["availability"] = "available" if projected.get("claim_context_state") == "evidence-backed" else "partial"
    return result


def public_witnesses(snapshot: dict) -> dict[str, tuple[str, dict]]:
    """Collect five types ONLY from returned public relationships and sources."""
    if snapshot["availability"] == "comparison-unavailable":
        return {}
    projection = snapshot["projection"]
    witnesses: dict[str, tuple[str, dict]] = {}

    def put(kind: str, identity: str, value: dict) -> None:
        key = f"{kind}:{identity}"
        if key in witnesses and witnesses[key] != (kind, value):
            raise ValueError("Ambiguous public comparison witness.")
        witnesses[key] = (kind, deepcopy(value))
        if len(witnesses) > MAX_WITNESSES_PER_SIDE:
            raise ValueError("Comparison witness limit exceeded.")

    claims = [claim for claim in projection.get("claims", []) if _claim_valid(claim)]
    by_version: dict[str, list[dict]] = {}
    for claim in claims:
        by_version.setdefault(claim["version_id"], []).append(claim)
        put("claim", f"{claim['version_id']}:{claim['role_id']}", claim)
        put("source_observation", claim["source"]["id"], claim["source"])
    conflict = projection.get("conflict")
    if conflict:
        link = conflict.get("edge", {})
        if (link.get("from") in by_version and link.get("to") in by_version
                and _edge(link, link["from"], "SOP_CONFLICT", link["to"])):
            put("conflict_relationship", f"{link['from']}:SOP_CONFLICT:{link['to']}", {
                **conflict, "citations": [claim["source"] for identifier in (link["from"], link["to"]) for claim in by_version[identifier]],
            })
    record = projection.get("decision_record")
    if record and _record_valid(record):
        put("decision_record", record["id"], record)
        put("source_observation", record["source"]["id"], record["source"])
        for link in projection.get("decision_considers_edges", []):
            if link.get("to") in by_version and _edge(link, record["id"], "CONSIDERS", link["to"]):
                put("considered_link", f"{record['id']}:CONSIDERS:{link['to']}", {
                    **link, "citations": [record["source"], *(claim["source"] for claim in by_version[link["to"]])],
                })
    return witnesses


def _semantics(kind: str, witness: dict) -> dict:
    """Source times are compared once, in their own source-observation type."""
    value = deepcopy(witness)
    if kind in {"claim", "decision_record"}:
        value["source"] = {key: item for key, item in value["source"].items() if key not in {"observed_at", "assumed_observed_at", "observation_mode"}}
    if kind in {"conflict_relationship", "considered_link"}:
        value.pop("citations", None)
    return value


def compare_snapshots(left: dict, right: dict) -> dict:
    """One-sided means projected only, never absence in the other graph/world."""
    left_witnesses, right_witnesses = public_witnesses(left), public_witnesses(right)
    unavailable = "comparison-unavailable" in {left["availability"], right["availability"]}
    partial = "partial" in {left["availability"], right["availability"]}
    items = []
    for key in sorted(left_witnesses.keys() | right_witnesses.keys()):
        lvalue, rvalue = left_witnesses.get(key), right_witnesses.get(key)
        kind = (lvalue or rvalue)[0]
        if unavailable:
            status = "incomparable"
        elif lvalue is None:
            status = "projected-only-on-right"
        elif rvalue is None:
            status = "projected-only-on-left"
        else:
            status = "unchanged" if _semantics(kind, lvalue[1]) == _semantics(kind, rvalue[1]) else "changed"
        items.append({"kind": kind, "key": key, "status": status,
                      "left": lvalue[1] if lvalue else None, "right": rvalue[1] if rvalue else None})
    return {
        "scope": "operations", "left": deepcopy(left), "right": deepcopy(right),
        "delta": {
            "state": "comparison-unavailable" if unavailable else "partial-witness-comparison" if partial else "witness-comparison",
            "complete": False, "items": items,
            "substantive_delta_count": None if unavailable else sum(item["status"] != "unchanged" for item in items),
            "unchanged_count": sum(item["status"] == "unchanged" for item in items),
            "no_negative_conclusion": True,
        },
        "read_only": True,
    }


def knowledge_diff(service: Any, left_known_at: str, right_known_at: str, left_mode: str, right_mode: str) -> dict:
    left_known_at, right_known_at, left_mode, right_mode = normalize_selections(left_known_at, right_known_at, left_mode, right_mode)
    with service.graph_lock:
        # Application-level synchronization only: no transaction claim.
        cache = {}

        def select(known_at: str, mode: str) -> dict:
            key = known_at, mode
            if key not in cache:
                if mode == "mainline":
                    cache[key] = snapshot_from_projection(known_at, mode, service.decision_timeline(known_at))
                else:
                    paired = service.decision_source_branch(known_at)
                    branch = paired["hypothetical_branch"]
                    cache[key] = snapshot_from_projection(known_at, mode, branch["projection"],
                                                          scenario=branch.get("scenario"), mainline=paired["mainline"])
            return cache[key]

        return compare_snapshots(select(left_known_at, left_mode), select(right_known_at, right_mode))

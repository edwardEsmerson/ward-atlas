"""Pure projection of the accepted graph impact response into a handover snapshot.

This module does not query FalkorDB.  The application passes it the result of
the existing SOP-impact service so the handover stores the same evidence the
read-only trace exposes, without duplicating its graph traversal.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any


def snapshot_from_impact(impact: dict[str, Any]) -> dict[str, Any]:
    """Project an operations-visible SOP impact response into a stable snapshot.

    A missing graph edge remains incomplete/unknown.  In particular, this
    projection never manufactures a conflict edge, turns missing linkage into
    a resolution, or treats an SOP claim as current ownership.
    """
    paths = deepcopy(impact.get("impact_paths", []))
    evidence = deepcopy(impact.get("path_evidence", []))
    active = bool(paths)
    observed_impact_state = impact.get("impact_state", "unknown")
    impact_snapshot = {
        "status": "observed-impact" if active else (observed_impact_state if observed_impact_state in {"restored", "unknown", "unavailable"} else "unknown"),
        "paths": paths,
        "path_evidence": evidence,
    }

    claims = deepcopy(impact.get("role_claims", []))
    stale = impact.get("stale_version")
    witness_present = impact.get("complete_conflict_witness_returned") is True
    path_equipment = [path[1] for path in paths if isinstance(path, list) and len(path) > 1]
    edge_equipment = [row.get("to") for row in impact.get("applicability_edges", []) if row.get("relationship") == "APPLIES_TO"]
    candidates = path_equipment or edge_equipment
    shared_equipment_id = candidates[0] if candidates and all(item == candidates[0] for item in candidates) else None
    conflict_status = "unresolved" if witness_present and impact.get("conflict_status") == "unresolved" else "unknown"
    if witness_present and conflict_status == "unresolved":
        linkage_status = "complete"
    else:
        linkage_status = impact.get("conflict_linkage_status", "unknown")
        if linkage_status not in {"incomplete/unknown", "unknown"}:
            linkage_status = "unknown"

    if not (paths or claims or stale or witness_present):
        sop_witness = None
    else:
        stale_projection = None
        if stale:
            stale_projection = {
                "version_id": stale.get("id"),
                "source_id": stale.get("source_id"),
                "source_title": stale.get("source_title"),
                "observed_at": stale.get("observed_at"),
                "referenced_equipment_id": stale.get("referenced_equipment_id"),
                "is_applicable_to_c4": stale.get("stale_is_applicable_to_c4", False),
                "is_joined_to_inc204_conflict_witness": stale.get("stale_is_joined_to_inc204_conflict_witness", False),
            }
        sop_witness = {
            "shared_equipment_id": shared_equipment_id,
            "linkage_status": linkage_status,
            "conflict_status": conflict_status,
            "applicability_edges": deepcopy(impact.get("applicability_edges", [])),
            "conflict_edge": deepcopy(impact.get("conflict_edge")),
            "claim_evidence": claims,
            "stale_version_excluded_from_c4_witness": stale_projection,
            "claims_are_current_owner_facts": False,
            "winner_selected": False,
            "contact_supplied": False,
            "direct_incident_to_sop_causal_edge_asserted": False,
            "procedure_or_clinical_conclusion_supplied": False,
        }

    return {
        "as_of": impact["as_of"],
        "impact": impact_snapshot,
        "sop_witness": sop_witness,
    }


def source_ids_from_snapshot(snapshot: dict[str, Any]) -> list[str]:
    """Return ordered distinct source IDs present in the reviewed snapshot."""
    sources: set[str] = set()
    for path in snapshot.get("impact", {}).get("path_evidence", []):
        sources.update(path.get("source_ids", []))
    witness = snapshot.get("sop_witness") or {}
    for claim in witness.get("claim_evidence", []):
        if claim.get("source_id"):
            sources.add(claim["source_id"])
    stale = witness.get("stale_version_excluded_from_c4_witness") or {}
    if stale.get("source_id"):
        sources.add(stale["source_id"])
    return sorted(sources)


def has_complete_sop_witness(snapshot: dict[str, Any]) -> bool:
    witness = snapshot.get("sop_witness")
    return isinstance(witness, dict) and witness.get("linkage_status") == "complete" and witness.get("conflict_status") == "unresolved"


def _records_from_snapshot(snapshot: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not isinstance(snapshot, dict):
        return []
    impact = snapshot.get("impact") or {}
    evidence_by_path = {
        tuple(row.get("path", [])): row
        for row in impact.get("path_evidence", [])
        if isinstance(row, dict) and isinstance(row.get("path"), list)
    }
    return [
        {
            "path": deepcopy(path),
            "provenance": deepcopy(evidence_by_path.get(tuple(path))),
        }
        for path in impact.get("paths", [])
        if isinstance(path, list)
    ]


def _records_from_board(paths: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    records = []
    for row in paths or []:
        path = row.get("path")
        evidence = row.get("evidence")
        if not isinstance(path, list) or not isinstance(evidence, list):
            continue
        records.append({
            "path": deepcopy(path),
            "provenance": {
                "path": deepcopy(path),
                "source_ids": [item.get("source_id") for item in evidence],
                "source_titles": [item.get("title") for item in evidence],
                "observed_at": [item.get("observed_at") for item in evidence],
            },
        })
    return records


def _path_delta(
    historical: list[dict[str, Any]],
    current: list[dict[str, Any]],
    *,
    status: str,
    label: str,
) -> dict[str, Any]:
    """Compare ordered path identities while carrying each side's provenance.

    Unknown graph state deliberately returns no inferred additions or removals.
    A hypothetical comparison is explicitly labelled and never described as an
    observed delta.
    """
    if status == "unknown":
        return {"label": label, "status": "unknown", "added_paths": [], "removed_paths": [], "unchanged_paths": []}
    old = {tuple(row["path"]): row for row in historical}
    new = {tuple(row["path"]): row for row in current}
    return {
        "label": label,
        "status": status,
        "added_paths": [deepcopy(row) for row in current if tuple(row["path"]) not in old],
        "removed_paths": [deepcopy(row) for row in historical if tuple(row["path"]) not in new],
        "unchanged_paths": [deepcopy(row) for row in historical if tuple(row["path"]) in new],
    }


def compare_graph_witness(
    action: dict[str, Any] | None,
    observed_impact: dict[str, Any],
    branch_board: dict[str, Any],
    branch_impact: dict[str, Any],
) -> dict[str, Any]:
    """Build a read-only observed/hypothetical comparison from graph API data.

    The action's saved snapshot is always the historical side of the comparison.
    Current observations are projections of the accepted FalkorDB-backed APIs;
    no handover preview or confirmation is generated here.
    """
    historical = deepcopy(action.get("snapshot")) if action else None
    historical_records = _records_from_snapshot(historical)
    observed_snapshot = snapshot_from_impact(observed_impact)
    observed_records = _records_from_snapshot(observed_snapshot)
    observed_event = deepcopy(observed_impact.get("observed_event"))
    observed_state = observed_impact.get("impact_state", "unknown")
    if observed_impact.get("board_state") == "empty/unknown" or (observed_state == "unknown" and observed_event is None):
        observed_state = "empty/unknown"
    observed_known = bool(
        observed_impact.get("impact_paths")
        or (observed_impact.get("impact_state") == "restored" and observed_event)
    )
    observed_delta_status = "unknown"
    if historical is not None and observed_known:
        observed_delta_status = "changed" if (
            [row["path"] for row in historical_records] != [row["path"] for row in observed_records]
        ) else "unchanged"
    observed_delta = _path_delta(
        historical_records,
        observed_records,
        status=observed_delta_status,
        label="observed",
    )

    conveyor = (branch_board or {}).get("conveyor") or {}
    branch = conveyor.get("branch") or {}
    branch_paths = _records_from_board(branch.get("paths"))
    branch_known = bool(branch.get("event")) and branch.get("status") != "unknown"
    branch_delta_status = "unknown" if historical is None or not branch_known else "hypothetical"
    branch_delta = _path_delta(
        historical_records,
        branch_paths,
        status=branch_delta_status,
        label="hypothetical",
    )
    mainline_paths = _records_from_board(conveyor.get("paths"))
    mainline_known = bool(conveyor.get("event")) and conveyor.get("status") != "unknown"
    mainline_delta_status = "unknown"
    if historical is not None and mainline_known:
        mainline_delta_status = "changed" if (
            [row["path"] for row in historical_records] != [row["path"] for row in mainline_paths]
        ) else "unchanged"
    mainline_delta = _path_delta(
        historical_records,
        mainline_paths,
        status=mainline_delta_status,
        label="observed mainline",
    )

    observed_witness = deepcopy(observed_snapshot.get("sop_witness"))
    branch_snapshot = snapshot_from_impact(branch_impact)
    branch_witness = deepcopy(branch_snapshot.get("sop_witness"))
    historical_witness = (historical or {}).get("sop_witness")
    restoration_fact = None
    if observed_event and observed_event.get("evidence_label") == "observed source fact" and observed_event.get("value") == "available":
        restoration_fact = {
            "event_id": observed_event.get("event_id"),
            "source_id": observed_event.get("source_id"),
            "event_at": observed_event.get("event_at"),
            "status": "observed",
            "source_title": observed_event.get("source_title"),
            "source_observed_at": observed_event.get("source_observed_at"),
        }

    return {
        "scope": "operations",
        "action": ({
            "id": action.get("id"),
            "incident_id": action.get("incident_id"),
            "confirmed_at": action.get("confirmed_at"),
            "storage": action.get("storage"),
        } if action else None),
        "historical_snapshot_label": "saved historical evidence" if historical is not None else None,
        "historical_snapshot": historical,
        "comparison_status": observed_delta_status if historical is not None else "unknown",
        "observed": {
            "as_of": observed_snapshot.get("as_of"),
            "current_state": observed_state,
            "current_impact_paths": deepcopy(observed_impact.get("impact_paths", [])),
            "current_path_evidence": deepcopy(observed_impact.get("path_evidence", [])),
            "path_delta": observed_delta,
            "added_paths": deepcopy(observed_delta["added_paths"]),
            "removed_paths": deepcopy(observed_delta["removed_paths"]),
            "observed_event": observed_event,
            "restoration_source_fact": restoration_fact,
            "sop_witness": observed_witness,
            "sop_conflict_status": (observed_witness or {}).get("conflict_status", "unknown"),
            "sop_witness_unchanged": observed_witness == historical_witness if historical is not None else False,
            "safe_conclusion_asserted": False,
            "no_operational_risk_conclusion_asserted": False,
        },
        "hypothetical_branch": {
            "branch_id": branch.get("branch_id"),
            "as_of": branch.get("as_of"),
            "status": branch.get("status", "unknown"),
            "delta_label": "hypothetical",
            "event": deepcopy(branch.get("event")),
            "paths": branch_paths,
            "path_delta": branch_delta,
            "observed_mainline_status": conveyor.get("status", "unknown"),
            "observed_mainline_paths": mainline_paths,
            "observed_mainline_delta": mainline_delta,
            "observed_state_mutated": False,
            "sop_witness": branch_witness,
            "sop_witness_unchanged": branch_witness == historical_witness if historical is not None else False,
        },
        "saved_action_unchanged": True,
        "action_boundary": {
            "graph_mutations": 0,
            "confirmed_action_file_mutations": 0,
            "explicit_confirmation_performed": False,
            "automatic_confirmation": False,
            "messages_sent": False,
            "calls_made": False,
            "tickets_created": False,
            "external_writes": [],
            "clinical_action_proposed": False,
            "source_truth_resolved": False,
            "work_claimed_performed": False,
        },
    }

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

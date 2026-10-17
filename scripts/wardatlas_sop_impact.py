"""Graph-native query and projection for the pinned synthetic SOP impact trace.

This module deliberately has no document parser or conflict-resolution logic.
It joins only explicit operations-visible FalkorDB relationships and labels
the role endpoints as source claims, never current owners or contacts.
"""

from __future__ import annotations

from typing import Any


CLAIMS_QUERY = """
MATCH (i:Incident {id: 'INC-204'})-[aff:AFFECTS]->(e:Equipment {id: 'CONVEYOR-C4'})-[sup:SUPPORTS]->(s:Service {id: 'SVC-MATERIAL-TRANSFER'}),
      (v:SOPVersion)-[applies:APPLIES_TO]->(e),
      (v)-[claim:CLAIMS_ROLE]->(role:Role),
      (v)-[evidence:EVIDENCED_BY]->(src:Evidence)
WHERE i.access = 'operations' AND e.access = 'operations' AND s.access = 'operations'
  AND v.access = 'operations' AND role.access = 'operations' AND src.access = 'operations'
  AND aff.access = 'operations' AND sup.access = 'operations'
  AND applies.access = 'operations' AND claim.access = 'operations' AND evidence.access = 'operations'
RETURN v.id AS sop_version_id, applies.id AS applicability_id,
       e.id AS equipment_id, role.id AS role_id,
       src.id AS source_id, src.title AS source_title, src.observed_at AS observed_at
ORDER BY v.id, role.id
""".strip()


CONFLICT_WITNESS_QUERY = """
MATCH (i:Incident {id: 'INC-204'})-[aff:AFFECTS]->(e:Equipment {id: 'CONVEYOR-C4'})-[sup:SUPPORTS]->(s:Service {id: 'SVC-MATERIAL-TRANSFER'})-[serves:SERVES]->(d:Department)-[owns:OWNED_BY]->(current_role:Role),
      (v2:SOPVersion {id: 'SOP-OPS-9-V2'})-[applies2:APPLIES_TO]->(e),
      (v3:SOPVersion {id: 'SOP-OPS-9-V3'})-[applies3:APPLIES_TO]->(e),
      (v2)-[conflict:SOP_CONFLICT]->(v3),
      (v2)-[claim2:CLAIMS_ROLE]->(role2:Role), (v3)-[claim3:CLAIMS_ROLE]->(role3:Role),
      (v2)-[evidence2:EVIDENCED_BY]->(src2:Evidence),
      (v3)-[evidence3:EVIDENCED_BY]->(src3:Evidence)
WHERE i.access = 'operations' AND e.access = 'operations' AND s.access = 'operations'
  AND d.access = 'operations' AND current_role.access = 'operations' AND owns.current = true
  AND v2.access = 'operations' AND v3.access = 'operations'
  AND role2.access = 'operations' AND role3.access = 'operations'
  AND src2.access = 'operations' AND src3.access = 'operations'
  AND aff.access = 'operations' AND sup.access = 'operations'
  AND serves.access = 'operations' AND owns.access = 'operations'
  AND applies2.access = 'operations' AND applies3.access = 'operations'
  AND conflict.access = 'operations' AND conflict.status = 'unresolved'
  AND claim2.access = 'operations' AND claim3.access = 'operations'
  AND evidence2.access = 'operations' AND evidence3.access = 'operations'
RETURN i.id AS incident_id, e.id AS equipment_id, s.id AS service_id,
       d.id AS department_id, current_role.id AS current_role_id,
       v2.id AS version_v2_id, applies2.id AS applies_v2_id,
       v3.id AS version_v3_id, applies3.id AS applies_v3_id,
       conflict.id AS conflict_id, conflict.status AS conflict_status,
       role2.id AS role_v2_id, src2.id AS source_v2_id,
       src2.title AS title_v2, src2.observed_at AS observed_v2,
       role3.id AS role_v3_id, src3.id AS source_v3_id,
       src3.title AS title_v3, src3.observed_at AS observed_v3
ORDER BY d.id
""".strip()


STALE_QUERY = """
MATCH (stale:SOPVersion {id: 'SOP-OPS-9-V1'})-[evidence:EVIDENCED_BY]->(src:Evidence)
OPTIONAL MATCH (stale)-[c3:APPLIES_TO {access: 'operations'}]->(retired:Equipment {id: 'CONVEYOR-C3', access: 'operations'})
OPTIONAL MATCH (stale)-[c4:APPLIES_TO {access: 'operations'}]->(c4_equipment:Equipment {id: 'CONVEYOR-C4', access: 'operations'})
WHERE stale.access = 'operations' AND src.access = 'operations' AND evidence.access = 'operations'
RETURN stale.id AS version_id, stale.referenced_equipment_id AS referenced_equipment_id,
       src.id AS source_id, src.title AS source_title, src.observed_at AS observed_at,
       c3.id AS retired_applicability_id, c4.id AS c4_applicability_id
""".strip()


def path_evidence(paths: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Project the fixed ordered source chain without altering provenance."""
    rows = []
    for item in paths:
        evidence = item.get("evidence", [])
        rows.append({
            "path": item.get("path", []),
            "source_ids": [source["source_id"] for source in evidence],
            "source_titles": [source["title"] for source in evidence],
            "observed_at": [source["observed_at"] for source in evidence],
        })
    return rows


def role_claim_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Expose only source-evidenced, typed SOP claims with an explicit label."""
    claims = [
        {
            "sop_version_id": row["sop_version_id"],
            "claim_relationship": "CLAIMS_ROLE",
            "role_id": row["role_id"],
            "claim_label": "unverified SOP claim",
            "source_evidence_linked": True,
            "source_id": row["source_id"],
            "source_title": row["source_title"],
            "observed_at": row["observed_at"],
        }
        for row in rows
    ]
    return sorted(claims, key=lambda claim: (claim["sop_version_id"], claim["role_id"]))


def distinct_sources(
    paths: list[dict[str, Any]],
    claims: list[dict[str, Any]],
    stale: dict[str, Any] | None,
    event: dict[str, Any] | None = None,
) -> list[dict[str, str]]:
    sources: dict[str, dict[str, str]] = {}
    for item in paths:
        for evidence in item.get("evidence", []):
            sources[evidence["source_id"]] = {
                "source_id": evidence["source_id"],
                "title": evidence["title"],
                "observed_at": evidence["observed_at"],
            }
    for claim in claims:
        sources[claim["source_id"]] = {
            "source_id": claim["source_id"],
            "title": claim["source_title"],
            "observed_at": claim["observed_at"],
        }
    if stale:
        sources[stale["source_id"]] = {
            "source_id": stale["source_id"],
            "title": stale["source_title"],
            "observed_at": stale["observed_at"],
        }
    if event and event.get("source_id"):
        sources[event["source_id"]] = {
            "source_id": event["source_id"],
            "title": event["source_title"],
            "observed_at": event["source_observed_at"],
        }
    return [sources[key] for key in sorted(sources)]


def assemble_response(
    as_of: str,
    conveyor: dict[str, Any] | None,
    claim_rows: list[dict[str, Any]],
    witness_rows: list[dict[str, Any]],
    stale_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    """Build a fixed evidence view; absence never means no conflict or safe."""
    paths = list(conveyor.get("paths", [])) if conveyor else []
    path_lists = [item["path"] for item in paths]
    path_rows = path_evidence(paths)
    claims = role_claim_rows(claim_rows)
    event = conveyor.get("event") if conveyor else None
    stale = None
    if stale_rows:
        row = stale_rows[0]
        stale = {
            "id": row["version_id"],
            "source_id": row["source_id"],
            "source_title": row["source_title"],
            "observed_at": row["observed_at"],
            "referenced_equipment_id": row["referenced_equipment_id"],
            "stale_artifact_hidden": False,
            "stale_is_applicable_to_c4": bool(row.get("c4_applicability_id")),
            "stale_is_joined_to_inc204_conflict_witness": False,
            "retired_applicability_relationship_id": row.get("retired_applicability_id"),
        }

    complete = bool(witness_rows)
    witness = witness_rows[0] if complete else None
    has_both_applicable_claims = {claim["sop_version_id"] for claim in claims} >= {"SOP-OPS-9-V2", "SOP-OPS-9-V3"}
    applicability = []
    conflict_edge = None
    if witness:
        applicability = [
            {"from": witness["version_v2_id"], "relationship": "APPLIES_TO", "to": witness["equipment_id"]},
            {"from": witness["version_v3_id"], "relationship": "APPLIES_TO", "to": witness["equipment_id"]},
        ]
        conflict_edge = {
            "from": witness["version_v2_id"],
            "relationship": "SOP_CONFLICT",
            "to": witness["version_v3_id"],
            "status": witness["conflict_status"],
        }

    visible_versions = sorted({claim["sop_version_id"] for claim in claims} | ({stale["id"]} if stale else set()))
    response: dict[str, Any] = {
        "scope": "operations",
        "as_of": as_of,
        "board_state": "ready" if (conveyor is not None or paths or claims or stale) else "empty/unknown",
        "impact_state": conveyor.get("status", "unknown") if conveyor else "unknown",
        "observed_event": event,
        "impact_paths": path_lists,
        "path_evidence": path_rows,
        "current_ownership_source_ids": [row["source_ids"][-1] for row in path_rows if row["source_ids"]],
        "sop_versions": visible_versions,
        "role_claims": claims,
        "source_rows": distinct_sources(paths, claims, stale, event),
        "applicability_edges": applicability,
        "conflict_edge": conflict_edge,
        "conflict_status": witness["conflict_status"] if witness else "unknown",
        "conflict_linkage_status": "linked-unresolved" if witness else ("unknown" if has_both_applicable_claims else "incomplete/unknown"),
        "conflict_conclusion": witness["conflict_status"] if witness else None,
        "complete_conflict_witness_returned": complete,
        "impact_linkage_status": "complete" if witness else ("incomplete/unknown" if (conveyor is not None or paths or claims) else "unknown"),
        "stale_version": stale,
        "c4_conflict_version_ids": [witness["version_v2_id"], witness["version_v3_id"]] if witness else [],
        "claims_are_unverified": bool(claims) and all(claim["claim_label"] == "unverified SOP claim" for claim in claims),
        "claims_are_current_owner_facts": False,
        "winner_selected": False,
        "event_state_and_sop_conflict_are_distinct": True,
        "sop_safe_conclusion_asserted": False,
        "no_operational_risk_conclusion_asserted": False,
        "action_boundary": {
            "clinical_inference": False,
            "procedure_selected": False,
            "person_assigned": False,
            "messages_sent": False,
            "calls_made": False,
            "tickets_created": False,
            "external_writes": [],
            "automatically_confirmed": False,
        },
    }
    if claims:
        response["returned_claim_count"] = len(claims)
    if witness:
        response["claim_evidence"] = claims
    return response

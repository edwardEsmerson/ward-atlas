"""Pure, bounded projection helpers for the INC-207 local evidence watch.

The graph witness is always supplied by WardAtlasService's existing real
FalkorDB shift-board query.  This module only normalizes/project/comparisons;
it never creates graph facts or proposes an operational action.
"""

from __future__ import annotations

from typing import Any

from wardatlas_shift_board import normalize_timestamp


DEFAULT_EFFECTIVE_AT = "2026-10-14T20:29:00Z"
DEFAULT_KNOWN_AT = "2026-10-14T20:17:59Z"
WATCH_ID = "WATCH-INC207-DOCK"
STATUS_MESSAGES = {
    "unchanged": "The selected role path and visible source versions match the saved baseline.",
    "new-source-known": "new source observed; selected role unchanged",
    "unknown": "The available graph evidence is incomplete for this time pair; no owner or safety conclusion is inferred.",
    "changed": "The source-backed role path differs from the saved baseline at the selected effective time.",
}


def normalize_watch_axes(effective_at: Any, known_at: Any) -> tuple[str, str]:
    """Require and independently normalize both explicit time axes."""
    return normalize_timestamp(effective_at), normalize_timestamp(known_at)


def _source_versions(rows: Any) -> list[dict[str, Any]]:
    if not isinstance(rows, list):
        return []
    fields = ("source_id", "title", "observed_at", "effective_at", "supersedes", "status")
    result = []
    for row in rows:
        if not isinstance(row, dict) or not all(isinstance(row.get(key), str) for key in ("source_id", "title", "observed_at")):
            continue
        result.append({key: row[key] for key in fields if key in row and row[key] is not None})
    return result


def witness_from_dock(dock: Any, effective_at: str, known_at: str) -> dict[str, Any]:
    """Return only operations-visible dock evidence needed by a watch."""
    if not isinstance(dock, dict):
        return {
            "effective_at": effective_at,
            "known_at": known_at,
            "role_path": [],
            "role_id": None,
            "role_status": "unknown",
            "source_versions": [],
            "review_candidates": [],
            "linkage_status": "unknown",
        }

    raw_path = dock.get("role_path")
    path = list(raw_path) if isinstance(raw_path, list) and all(isinstance(item, str) for item in raw_path) else []
    role_id = dock.get("role_id") if isinstance(dock.get("role_id"), str) else None
    sources = _source_versions(dock.get("source_versions"))
    role_evidence = dock.get("role_evidence") if isinstance(dock.get("role_evidence"), list) else []
    source_ids = {row["source_id"] for row in sources}
    source_backed = any(
        isinstance(row, dict) and row.get("source_id") in source_ids
        and isinstance(row.get("title"), str) and isinstance(row.get("observed_at"), str)
        for row in role_evidence
    )
    role_known = (
        dock.get("role_status") == "known" and len(path) == 5
        and role_id == path[-1] and source_backed
    )
    review_rows = dock.get("review_candidates") if isinstance(dock.get("review_candidates"), list) else []
    review_candidates = [
        row["artifact_id"] for row in review_rows
        if isinstance(row, dict) and isinstance(row.get("artifact_id"), str)
    ]
    # If R2 is visible but the explicit supersession edge is missing, do not
    # promote its status or silently classify the watch as a complete change.
    linkage_status = dock.get("linkage_status") if dock.get("linkage_status") in {"linked", "unknown"} else "unknown"
    projected = {
        "effective_at": effective_at,
        "known_at": known_at,
        "role_path": path if role_known else [],
        "role_id": role_id if role_known else None,
        "role_status": "known" if role_known else "unknown",
        "source_versions": sources,
        "review_candidates": review_candidates,
        "linkage_status": linkage_status,
        "currentness_conclusion": False,
    }
    if any(source["source_id"] == "SRC-DOCK-ROSTER-R2" for source in sources) and linkage_status == "linked":
        if isinstance(dock.get("candidate_disposition"), str):
            projected["candidate_disposition"] = dock["candidate_disposition"]
        projected["HND-DOCK-2020_invalidated"] = False if "HND-DOCK-2020" in review_candidates else False
    if isinstance(dock.get("interpretation"), str) and role_known:
        projected["interpretation"] = dock["interpretation"]
    return projected


def comparison_status(baseline: dict[str, Any], current: dict[str, Any]) -> str:
    """Compare ordered path and source provenance without promoting unknowns."""
    path = current.get("role_path")
    sources = current.get("source_versions")
    if (
        current.get("role_status") != "known" or not isinstance(path, list) or len(path) != 5
        or not isinstance(sources, list) or not sources
    ):
        return "unknown"
    if any(row.get("source_id") == "SRC-DOCK-ROSTER-R2" for row in sources) and current.get("linkage_status") != "linked":
        return "unknown"

    baseline_path = baseline.get("role_path")
    baseline_sources = baseline.get("source_versions")
    if not isinstance(baseline_path, list) or not baseline_path or not isinstance(baseline_sources, list) or not baseline_sources:
        return "unknown"
    if path != baseline_path:
        return "changed"
    if sources == baseline_sources:
        return "unchanged"
    old_ids = {row.get("source_id") for row in baseline_sources if isinstance(row, dict)}
    new_ids = {row.get("source_id") for row in sources if isinstance(row, dict)}
    if new_ids - old_ids:
        return "new-source-known"
    # A historical cutoff that hides a previously visible source, or changed
    # metadata without a newly observed source, is incomplete/ambiguous rather
    # than evidence that a new source arrived.
    return "unknown"


def public_refresh(watch: dict[str, Any], current: dict[str, Any]) -> dict[str, Any]:
    status = comparison_status(watch.get("baseline", {}), current)
    return {
        "effective_at": current["effective_at"],
        "known_at": current["known_at"],
        "role_path": current["role_path"],
        "role_id": current["role_id"],
        "role_status": current["role_status"],
        "source_versions": current["source_versions"],
        "review_candidates": current["review_candidates"],
        "linkage_status": current["linkage_status"],
        "status": status,
        "status_message": STATUS_MESSAGES[status],
        "action_or_work_item_changed": False,
        "currentness_conclusion": False,
        **({key: current[key] for key in ("candidate_disposition", "HND-DOCK-2020_invalidated", "interpretation") if key in current}),
    }

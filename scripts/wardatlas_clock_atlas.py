"""Bounded, read-only INC-207 clock matrix over the accepted shift-board query.

No graph traversal, fixture answers, persistence or source rewriting lives here.
Each cell is a separate call with explicit valid-time and observation-time axes.
"""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from urllib.parse import parse_qs

from wardatlas_shift_board import normalize_timestamp

EFFECTIVE_TIMES = ("2026-10-14T20:29:00Z", "2026-10-14T20:30:00Z")
KNOWN_TIMES = ("2026-10-14T20:17:59Z", "2026-10-14T20:19:00Z")
HTML_PATH = Path(__file__).resolve().parent / "ui" / "clock_atlas.html"


def normalize_selection(effective_at: str, known_at: str) -> tuple[str, str]:
    effective_at = normalize_timestamp(effective_at)
    known_at = normalize_timestamp(known_at)
    if effective_at not in EFFECTIVE_TIMES or known_at not in KNOWN_TIMES:
        raise ValueError("Select one of the four atlas clock pairs.")
    return effective_at, known_at


def selection_from_query(query: str) -> tuple[str, str]:
    values = parse_qs(query, keep_blank_values=True)
    if any(len(items) != 1 for items in values.values()):
        raise ValueError("Supply each clock atlas parameter exactly once.")
    if set(values) - {"scope", "effective_at", "known_at"}:
        raise ValueError("Unsupported clock atlas parameter.")
    if values.get("scope", ["operations"])[0] != "operations":
        raise PermissionError("This view is not available.")
    if ("effective_at" in values) != ("known_at" in values):
        raise ValueError("Supply both effective_at and known_at.")
    return normalize_selection(
        values.get("effective_at", [EFFECTIVE_TIMES[0]])[0],
        values.get("known_at", [KNOWN_TIMES[0]])[0],
    )


def project_cell(dock: dict | None, row: int, column: int) -> dict:
    """Detach this cell's own dock result; never consult another cell."""
    dock = dock or {}
    path = deepcopy(dock.get("role_path", []))
    return {
        "id": f"e{row}-k{column}", "row": row, "column": column,
        "effective_at": EFFECTIVE_TIMES[row], "known_at": KNOWN_TIMES[column],
        "role_path": path, "role_id": dock.get("role_id") if path else None,
        "role_status": "known" if path else "unknown",
        "status": "known" if path else "unknown",
        "source_versions": deepcopy(dock.get("source_versions", [])),
        "role_evidence": deepcopy(dock.get("role_evidence", [])) if path else [],
        "review_candidates": [item["artifact_id"] for item in dock.get("review_candidates", [])],
        "candidate_disposition": dock.get("candidate_disposition"),
        "linkage_status": dock.get("linkage_status", "unknown"),
        "coverage_status": dock.get("coverage_status", "unknown"),
    }


def build_atlas(shift_board, effective_at: str = EFFECTIVE_TIMES[0], known_at: str = KNOWN_TIMES[0]) -> dict:
    """Invoke the accepted live service four times, after validating selection."""
    effective_at, known_at = normalize_selection(effective_at, known_at)
    cells = []
    for row, effective in enumerate(EFFECTIVE_TIMES):
        for column, known in enumerate(KNOWN_TIMES):
            board = shift_board(dock_at=effective, known_at=known)
            cells.append(project_cell(board.get("dock"), row, column))
    selected = next(cell for cell in cells if cell["effective_at"] == effective_at and cell["known_at"] == known_at)
    return {
        "scope": "operations", "effective_times": list(EFFECTIVE_TIMES),
        "known_times": list(KNOWN_TIMES), "cells": cells,
        "selected_cell_id": selected["id"], "selected_cell": deepcopy(selected),
    }


def atlas_from_query(shift_board, query: str) -> dict:
    # Validation completes before even the first database callback.
    return build_atlas(shift_board, *selection_from_query(query))


def mount_clock_atlas(handler, service, parsed) -> bool:
    """Namespaced app adapter. All other routes remain untouched."""
    if parsed.path not in {"/clocks", "/api/clock-atlas"}:
        return False
    try:
        selection = selection_from_query(parsed.query)
    except PermissionError:
        handler._error(403, "This view is not available.")
        return True
    except ValueError as exc:
        handler._error(400, str(exc))
        return True
    # Callback errors (including ValueError) are backend failures, not invalid
    # user input. Never expose database/source failure details in the response.
    try:
        if parsed.path == "/clocks":
            handler._send(200, HTML_PATH.read_text(encoding="utf-8"))
        else:
            handler._send(200, build_atlas(service.shift_board, *selection))
    except Exception:
        handler._error(503, "Clock atlas graph evidence is unavailable. Retry the read-only query.")
    return True

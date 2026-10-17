#!/usr/bin/env python3
"""Check the WardAtlas runbook against unchanged oracle contracts and live suites.

Static mode invokes the four original candidate-independent contract checks.
Live mode runs their accepted original/shift, SOP-impact and handover behavior
suites sequentially against each suite's own actual-FalkorDB graph and browser.
This verifier is not the two-terminal demo launcher.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import signal
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
RUNBOOK = ROOT / "docs/demo-runbook.md"
SHIFT_PLAN = Path(
    "/workspace/worktrees/planner-5f75fb17810c463fb9d84bd5821b254f/"
    "plans/planner-5f75fb17810c463fb9d84bd5821b254f.md"
)
SHIFT_SOURCE_PLAN = Path(
    "/workspace/worktrees/planner-9b1a51761889446c8b5525bde02bc5d6/"
    "plans/planner-9b1a51761889446c8b5525bde02bc5d6.md"
)
SHIFT_TEMPORAL_PLAN = Path(
    "/workspace/worktrees/planner-5f75fb17810c463fb9d84bd5821b254f/"
    "plans/planner-13609527bde94e87ae5bcacaa2c86a70.md"
)
SHIFT_PRIOR_PLAN = Path(
    "/workspace/worktrees/planner-5f75fb17810c463fb9d84bd5821b254f/"
    "plans/planner-ed8ab68cd001411d97ee43310862ae22.md"
)
SOP_PLAN = Path(
    "/workspace/worktrees/planner-3bfcb0d8abbc45bc82e0fab8d715c2da/"
    "plans/planner-3bfcb0d8abbc45bc82e0fab8d715c2da.md"
)
HANDOVER_PLAN = Path(
    "/workspace/worktrees/planner-d0017f7f160844fe9cf5a6036aa5068f/"
    "plans/planner-d0017f7f160844fe9cf5a6036aa5068f.md"
)

# Independent byte pins for the unchanged oracle/matrix pairs this guide names.
ORACLE_HASHES = {
    "tests/expected-results.json": "c77e00e47c4f1e26d565c3d9583653838d5cc88695c4e6aa19ac583205d92b87",
    "tests/adversarial-matrix.json": "6d0edbc69620da85b494185879565c8260c9d2d3fe6afdd5bf56c5acd92205ba",
    "tests/shift-board-expected-results.json": "5d53a8f93245ee8391ec7cffb6d45b58355536d30d649db19cd53ca6b1ce2428",
    "tests/shift-board-adversarial-matrix.json": "9cf1a4b144df3ff7357e389acec7c5e6b0e6ddcdab18577fa193c09541c0bbd7",
    "tests/sop-impact-expected-results.json": "f94e65850384e36e7200b321d14628f074fee37fb159adbcd73bef00c6e1acca",
    "tests/sop-impact-adversarial-matrix.json": "f5aa441a636b45b4222716d4a003afc91cf7598439833e36f390523b55f161c6",
    "tests/handover-brief-expected-results.json": "932ada7d43688b8bd1e4f147ac06dd585982d784ef8577f05cc00b07a97199f1",
    "tests/handover-brief-adversarial-matrix.json": "d084f31a1b542670ab99818852cba8e1141bfb56688845c439b01f5ac1d89003",
}

EXPECTED_CASE_IDS = {
    "tests/expected-results.json": [
        "cold-empty-graph",
        "multi-hop-outage-current-ownership",
        "disconnected-decoy-exclusion",
        "equipment-outage-counterfactual",
        "stale-reference-current-sop-conflict",
        "denied-source-nondisclosure",
        "seed-reseed-idempotence",
        "reconnect-preserves-results",
    ],
    "tests/shift-board-expected-results.json": [
        "cold-empty-board",
        "dock-role-time-and-source-evidence",
        "explicit-edge-review-queue",
        "conveyor-observed-and-hypothetical-times",
        "no-cross-case-path-or-causal-contamination",
        "denied-source-nondisclosure",
        "explicit-local-confirmation-no-external-writes",
        "empty-reseed-reconnect-and-owned-cleanup",
    ],
    "tests/sop-impact-expected-results.json": [
        "sop-impact-cold-empty",
        "sop-impact-observed-outage-paths",
        "sop-impact-conflict-provenance",
        "sop-impact-stale-v1-excluded",
        "sop-impact-applicability-edge-counterfactual",
        "sop-impact-conflict-edge-counterfactual",
        "sop-impact-restoration-snapshot",
        "sop-impact-denied-source-invariance",
        "sop-impact-reseed-reconnect",
        "sop-impact-time-browser-local-boundary",
    ],
    "tests/handover-brief-expected-results.json": [
        "cold-empty-unknown",
        "inc204-observed-paths-and-provenance",
        "typed-current-sop-witness-and-stale-exclusion",
        "owned-edge-removal-is-unknown-only",
        "restoration-keeps-sop-claims-unresolved",
        "denied-source-response-invariance",
        "read-only-preview-and-explicit-local-confirmation",
        "timestamp-normalization-and-pre-mutation-rejection",
        "idempotent-reseed-and-fresh-client-equality",
    ],
}

ORACLE_CASE_LINKS = {
    "tests/expected-results.json": ("scenarios", "cases"),
    "tests/shift-board-expected-results.json": ("cases", "cases"),
    "tests/sop-impact-expected-results.json": ("cases", "cases"),
}

REQUIRED_RUNBOOK_TEXT = (
    "90-second story",
    "Four-minute technical walkthrough",
    "two foreground processes",
    "not a one-command launcher",
    "WORKSHOP_PORT_BASE",
    "WORKSHOP_PORT_SPAN",
    "WORKSHOP_LANE",
    "redis-server --bind 127.0.0.1 --protected-mode yes --port",
    "--daemonize no --loadmodule",
    "--save \"\" --appendonly no",
    "wardatlas_ui_${WORKSHOP_LANE}_${RUN_ID}",
    ".local/demo-runs/${WORKSHOP_LANE}/${RUN_ID}",
    "/workspace/evidence/wardatlas/demo-runbook/${WORKSHOP_LANE}/${RUN_ID}",
    "empty/unknown",
    "no risk",
    "Reset synthetic scenario",
    "Confirm & save locally",
    "Exact owned teardown",
    "rm -rf -- \"$DB_DATA_DIR\" \"$STATE_DIR\"",
    "rm -f -- \"$RUN_ENV\"",
    "preserve the evidence directory",
    "No AI model is called at runtime",
    "AI assistants helped author",
    "Company Brain",
    "fictional synthetic",
    "non-clinical",
    "Eligibility risk remains",
    "does not certify those requirements",
    "--static-contract --fail-on-zero",
    "--live --desktop-mobile --fail-on-zero --assert-owned-cleanup",
)

OUT_OF_RUNBOOK_FEATURES = (
    "decision provenance docket",
    "decision-docket",
    "decision knowledge timeline",
    "source review ui",
    "source ingestion",
    "source-change review",
    "conveyor_known_at",
    "model-backed agent",
    "general-purpose ingestion",
    "hospital-wide watchboard",
    "digital twin",
    "dock bitemporal",
    "handover drift comparison",
)

UNSUPPORTED_CLAIM_PATTERNS = (
    re.compile(r"\b(?:reduces?|reduced|cuts?|saves?)\s+(?:hospital\s+)?(?:outage|downtime|cost|labor|incidents?)", re.I),
    re.compile(r"\b(?:HIPAA|GDPR)\s+compliant\b", re.I),
    re.compile(r"\bproduction[- ]ready\b", re.I),
    re.compile(r"\bvalidated\s+(?:at|in|by)\s+(?:a\s+)?real hospitals?\b", re.I),
    re.compile(r"\b(?:improved?|better)\s+patient outcomes?\b", re.I),
    re.compile(r"\breal[- ]hospital\s+(?:deployment|integration|validation)\b", re.I),
)

RESTRICTED_FIXTURE_TRIPWIRES = (
    "SRC-RESTRICTED-92",
    "Restricted Continuity Annex 92",
    "EQUIP-RESTRICTED-92",
    "DEPT-RESTRICTED-COMPLIANCE",
    "ROLE-RESTRICTED-OWNER",
    "Restricted-only instruction: contact the secure lift coordinator.",
    "SRC-RESTRICTED-SOP-PROBE-91",
)


class RunbookError(ValueError):
    """Raised for an unpinned or unsafe demo guide/check invocation."""


def _read_json(relative: str) -> dict[str, Any]:
    try:
        value = json.loads((ROOT / relative).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RunbookError(f"cannot load pinned oracle {relative}: {exc}") from exc
    if not isinstance(value, dict):
        raise RunbookError(f"oracle {relative} is not an object")
    return value


def verify_oracle_bytes() -> dict[str, str]:
    """Fail closed if an oracle or adversarial matrix changed from its pin."""
    observed: dict[str, str] = {}
    for relative, expected in ORACLE_HASHES.items():
        try:
            actual = hashlib.sha256((ROOT / relative).read_bytes()).hexdigest()
        except OSError as exc:
            raise RunbookError(f"cannot read pinned oracle {relative}: {exc}") from exc
        if actual != expected:
            raise RunbookError(f"unchanged oracle bytes differ from independent pin: {relative}")
        observed[relative] = actual
    return observed


def inspect_nonzero_mappings() -> dict[str, dict[str, int]]:
    """Check expected-case order and positive matrix assertion counts."""
    counts: dict[str, dict[str, int]] = {}
    for expected_path, (expected_collection, matrix_collection) in ORACLE_CASE_LINKS.items():
        expected = _read_json(expected_path)
        matrix_path = expected_path.replace("expected-results", "adversarial-matrix")
        matrix = _read_json(matrix_path)
        expected_rows = expected.get(expected_collection)
        matrix_rows = matrix.get(matrix_collection)
        if not isinstance(expected_rows, list) or not isinstance(matrix_rows, list):
            raise RunbookError(f"missing expected cases/matrix rows for {expected_path}")
        expected_ids = [row.get("id") for row in expected_rows]
        matrix_ids = [row.get("id") for row in matrix_rows]
        wanted_ids = EXPECTED_CASE_IDS[expected_path]
        if expected_ids != wanted_ids or matrix_ids != wanted_ids:
            raise RunbookError(f"scenario order or one-to-one mapping differs for {expected_path}")
        per_case: dict[str, int] = {}
        for outcome, mapping in zip(expected_rows, matrix_rows):
            amount = mapping.get("nonzero_assertion_count")
            assertions = mapping.get("pinned_assertions")
            if type(amount) is not int or amount <= 0 or not isinstance(assertions, list) or len(assertions) != amount:
                raise RunbookError(f"case {mapping.get('id')} has a zero or malformed assertion mapping")
            if mapping.get("scenario_id") != outcome.get("id"):
                raise RunbookError(f"matrix row {mapping.get('id')} is not mapped to its expected outcome")
            per_case[mapping["id"]] = amount
        if sum(per_case.values()) <= 0:
            raise RunbookError(f"all mapped assertion counts are zero for {expected_path}")
        counts[expected_path] = per_case

    expected = _read_json("tests/handover-brief-expected-results.json")
    matrix = _read_json("tests/handover-brief-adversarial-matrix.json")
    outcomes = expected.get("cases")
    groups = matrix.get("groups")
    wanted_ids = EXPECTED_CASE_IDS["tests/handover-brief-expected-results.json"]
    if not isinstance(outcomes, list) or not isinstance(groups, list):
        raise RunbookError("handover outcomes and groups must be arrays")
    if [row.get("id") for row in outcomes] != wanted_ids or [row.get("case_id") for row in groups] != wanted_ids:
        raise RunbookError("handover outcome order or group mapping differs from its fixed nine cases")
    per_case = {}
    for outcome, group in zip(outcomes, groups):
        assertions = group.get("assertions")
        if group.get("case_id") != outcome.get("id") or not isinstance(assertions, list) or not assertions:
            raise RunbookError(f"handover group {group.get('case_id')} has no nonzero mapping")
        if any(not isinstance(row, dict) or not isinstance(row.get("id"), str) or not row["id"] for row in assertions):
            raise RunbookError(f"handover group {group.get('case_id')} has an unpinned assertion")
        per_case[group["case_id"]] = len(assertions)
    if sum(per_case.values()) <= 0:
        raise RunbookError("handover matrix total assertion mapping is zero")
    counts["tests/handover-brief-expected-results.json"] = per_case
    return counts


def oracle_story_literals() -> set[str]:
    """Return exact user-visible story facts from the four accepted oracles."""
    original = _read_json("tests/expected-results.json")
    original_by_id = {row["id"]: row["expected"] for row in original["scenarios"]}
    outage = original_by_id["multi-hop-outage-current-ownership"]
    paths = outage["impact_paths"]
    evidence = original["expected_evidence_by_path"]
    literals: set[str] = set()
    for path, record in zip(paths, evidence):
        literals.add(" → ".join(path))
        for source_id, title, observed_at in zip(
            record["source_ids"], record["source_titles"], record["observed_at"]
        ):
            literals.add(source_id)
            literals.add(title)
            literals.add(observed_at)
    for source_id in outage["source_ids_by_path"][0]:
        literals.add(source_id)
    for role_id in outage["current_role_ids"]:
        literals.add(role_id)

    shift = _read_json("tests/shift-board-expected-results.json")
    shift_by_id = {row["id"]: row["expected"] for row in shift["cases"]}
    replay = shift_by_id["conveyor-observed-and-hypothetical-times"]
    observed = replay["observed_at_1830"]
    restoration = replay["observed_restoration_1930"]
    branch = replay["hypothetical_branch_1850"]
    literals.update((
        observed["at"], observed["event_id"], observed["source_id"], observed["event_at"],
        restoration["at"], restoration["restoration_event_id"], restoration["restoration_source_id"],
        restoration["restoration_event_at"], branch["at"], branch["branch_id"],
        branch["hypothetical_event_id"], branch["hypothetical_event_at"],
    ))
    literals.update(" → ".join(path) for path in observed["paths"])
    literals.update(" → ".join(path) for path in branch["mainline_paths"])

    sop = _read_json("tests/sop-impact-expected-results.json")
    sop_by_id = {row["id"]: row["expected"] for row in sop["cases"]}
    conflict = sop_by_id["sop-impact-conflict-provenance"]
    for claim in conflict["claim_evidence"]:
        literals.update((
            claim["sop_version_id"], claim["role_id"], claim["source_id"],
            claim["source_title"], claim["observed_at"], claim["claim_label"],
        ))
    for edge in conflict["applicability_edges"]:
        literals.update((edge["from"], edge["relationship"], edge["to"]))
    literals.update((
        conflict["conflict_edge"]["from"], conflict["conflict_edge"]["relationship"],
        conflict["conflict_edge"]["to"], conflict["conflict_edge"]["status"],
    ))
    stale = sop_by_id["sop-impact-stale-v1-excluded"]
    literals.update((
        stale["stale_version_id"], stale["stale_source_id"], stale["stale_source_title"],
        stale["stale_observed_at"], stale["stale_referenced_equipment_id"],
    ))

    handover = _read_json("tests/handover-brief-expected-results.json")
    snapshot = handover["canonical_snapshot"]
    literals.add(snapshot["as_of"])
    literals.update(" → ".join(path) for path in snapshot["impact"]["paths"])
    for path_evidence in snapshot["impact"]["path_evidence"]:
        literals.update(path_evidence["source_ids"])
        literals.update(path_evidence["source_titles"])
        literals.update(path_evidence["observed_at"])
    return literals


def oracle_association_clauses() -> list[str]:
    """Build path/evidence and event/SOP clauses with oracle-owned associations."""
    clauses: list[str] = []
    original = _read_json("tests/expected-results.json")
    expected = next(
        row["expected"] for row in original["scenarios"]
        if row["id"] == "multi-hop-outage-current-ownership"
    )
    labels = ("Central-sterile", "Materials-logistics")
    for label, evidence in zip(labels, original["expected_evidence_by_path"]):
        trail = "; ".join(
            f"`{source_id}` — *{title}* — observed `{observed_at}`"
            for source_id, title, observed_at in zip(
                evidence["source_ids"], evidence["source_titles"], evidence["observed_at"]
            )
        )
        clauses.append(f"**{label} path provenance (in order):** {trail}.")

    shift = _read_json("tests/shift-board-expected-results.json")
    replay = next(
        row["expected"] for row in shift["cases"]
        if row["id"] == "conveyor-observed-and-hypothetical-times"
    )
    observed = replay["observed_at_1830"]
    restoration = replay["observed_restoration_1930"]
    branch = replay["hypothetical_branch_1850"]
    clauses.extend((
        f"The observed status is `{observed['status']}`; outage event `{observed['event_id']}` cites source "
        f"`{observed['source_id']}` at event time `{observed['event_at']}`.",
        f"At observed `{restoration['at']}`, restoration event `{restoration['restoration_event_id']}` from "
        f"`{restoration['restoration_source_id']}` has status `{restoration['status']}` and event time "
        f"`{restoration['restoration_event_at']}`; active paths are empty.",
        f"At `{branch['at']}`, branch `{branch['branch_id']}` has status `{branch['branch_status']}` from hypothetical "
        f"event `{branch['hypothetical_event_id']}` at `{branch['hypothetical_event_at']}`: branch paths are empty "
        f"while the mainline remains `{branch['mainline_status']}` with both paths.",
    ))

    sop = _read_json("tests/sop-impact-expected-results.json")
    sop_cases = {row["id"]: row["expected"] for row in sop["cases"]}
    for claim in sop_cases["sop-impact-conflict-provenance"]["claim_evidence"]:
        version_number = claim["sop_version_id"].rsplit("V", 1)[1]
        clauses.append(
            f"V{version_number} claims `{claim['role_id']}` from `{claim['source_id']}` "
            f"(*{claim['source_title']}*, observed `{claim['observed_at']}`)"
        )
    stale = sop_cases["sop-impact-stale-v1-excluded"]
    clauses.append(
        f"Archived `{stale['stale_version_id']}` / `{stale['stale_source_id']}` "
        f"(*{stale['stale_source_title']}*, observed `{stale['stale_observed_at']}`) references "
        f"`{stale['stale_referenced_equipment_id']}`"
    )
    return clauses


def validate_runbook_text(text: str) -> None:
    """Reject a runbook that drifts from or overstates the accepted evidence."""
    normalized = text.casefold()
    missing = [item for item in REQUIRED_RUNBOOK_TEXT if item.casefold() not in normalized]
    if missing:
        raise RunbookError(f"runbook omits required operator/story material: {missing}")
    for literal in oracle_story_literals():
        if literal.casefold() not in normalized:
            raise RunbookError(f"runbook omits an exact ID, provenance value, or time from its oracle: {literal}")
    for clause in oracle_association_clauses():
        if clause.casefold() not in normalized:
            raise RunbookError(f"runbook changes an oracle path/source/time association: {clause}")
    if "2026-10-14t19:30:00z" not in normalized or "2026-10-14t18:30:00z" not in normalized:
        raise RunbookError("runbook must pin both observed outage and restoration instants")
    if not re.search(r"APP_PORT=\"\$\(\(WORKSHOP_PORT_BASE\s*\+\s*121\)\)\"", text):
        raise RunbookError("app port must be derived from WORKSHOP_PORT_BASE, never a fixed/default port")
    if not re.search(r"DB_PORT=\"\$\(\(WORKSHOP_PORT_BASE\s*\+\s*120\)\)\"", text):
        raise RunbookError("database port must be derived from WORKSHOP_PORT_BASE, never a fixed/default port")
    if 'test "${#WORKSHOP_LANE}" -le 32' not in text:
        raise RunbookError("WORKSHOP_LANE length must fit the app's graph-name limit")
    expected_assignments = {
        "APP_PORT": '"$((WORKSHOP_PORT_BASE + 121))"',
        "DB_PORT": '"$((WORKSHOP_PORT_BASE + 120))"',
    }
    actual_assignments = re.findall(r"(?m)^(APP_PORT|DB_PORT)=(.+)$", text)
    if dict(actual_assignments) != expected_assignments or len(actual_assignments) != 2:
        raise RunbookError("app/database port assignments must be unique and allocated, never fixed/default")
    for variable in ("APP_PORT", "DB_PORT"):
        range_guard = rf'test "\$\(\(\s*{variable}\s*-\s*WORKSHOP_PORT_BASE\s*\)\)" -lt "\$WORKSHOP_PORT_SPAN"'
        if not re.search(range_guard, text):
            raise RunbookError(f"{variable} must be checked against WORKSHOP_PORT_SPAN")
        if f'test "${variable}" -ge 1024' not in text or f'test "${variable}" -le 65535' not in text:
            raise RunbookError(f"{variable} must be a selected high port within the TCP range")
    if not re.search(r'redis-server[^\n]*--bind 127\.0\.0\.1[^\n]*--port "\$DB_PORT"', text):
        raise RunbookError("redis-server must bind only the allocated loopback DB_PORT")
    if not re.search(r'python3 scripts/wardatlas_app\.py --app-port "\$APP_PORT" --db-port "\$DB_PORT"', text):
        raise RunbookError("app must receive the allocated APP_PORT and DB_PORT")
    if not re.search(r'--graph-name "\$GRAPH_NAME" --state-dir "\$STATE_DIR"', text):
        raise RunbookError("app must receive this run's graph and state paths")
    if "127.0.0.1" not in normalized or re.search(r"--bind\s+0\.0\.0\.0", text, re.I):
        raise RunbookError("services must bind loopback only")
    if """--save "" --appendonly no""" not in text or "--dir \"$DB_DATA_DIR\"" not in text:
        raise RunbookError("database must use a run-owned data directory and volatile storage")
    if "--state-dir \"$STATE_DIR\"" not in text or "wardatlas_ui_${WORKSHOP_LANE}_${RUN_ID}" not in text:
        raise RunbookError("graph and app state must be lane/run-unique")
    required_artifact_pins = (
        "93bc3c1d484f7fcad45c9409c32034c79e876596b18ce5b03922be007dba889c",
        "sha256:107455e862a824e45e8e9f5a7fbfdf5e00a08b14e13eae2577c72fd9e3f59ba6",
        "sha256:288aaa7d0583c7c15fb28509544c0418186ef064f551bfb477f3df26b1a892fb",
        "sha256:6c11c298608011dfeb77a91598799a97939bed5dbe17a80e7997356d6e81846b",
        "facts.get(\"source\") == \"https://hub.docker.com/r/falkordb/falkordb\"",
        "module.resolve().is_relative_to(root) and receipt.resolve().is_relative_to(root)",
    )
    if "--loadmodule \"$MODULE\"" not in text or any(pin not in text for pin in required_artifact_pins):
        raise RunbookError("startup must load and verify the digest-pinned actual FalkorDB module")
    if "Ctrl-C" not in text or "rm -rf -- \"$DB_DATA_DIR\" \"$STATE_DIR\"" not in text:
        raise RunbookError("owned teardown must stop the foreground processes and remove exact run paths")
    if "rmdir -- \"$RUN_ROOT\"" not in text or "test -d \"$EVIDENCE_DIR\"" not in text:
        raise RunbookError("teardown must remove the owned run envelope but preserve evidence")
    if 'RUN_ID="$(python3 -c \'import uuid; print(uuid.uuid4().hex)\')"' not in text:
        raise RunbookError("each demo graph/data/state/evidence path must include a fresh run ID")
    broad_removal = [
        line.strip() for line in text.splitlines()
        if re.match(r"^\s*rm\s+-rf\b", line)
        and line.strip() != 'rm -rf -- "$DB_DATA_DIR" "$STATE_DIR"'
    ]
    if broad_removal:
        raise RunbookError(f"teardown contains non-owned broad deletion: {broad_removal}")
    if any(term in normalized for term in OUT_OF_RUNBOOK_FEATURES):
        raise RunbookError("runbook includes a feature outside the four pinned accepted workflows")
    for pattern in UNSUPPORTED_CLAIM_PATTERNS:
        if pattern.search(text):
            raise RunbookError(f"unsupported outcome/compliance/deployment claim: {pattern.pattern}")
    for literal in RESTRICTED_FIXTURE_TRIPWIRES:
        if literal.casefold() in normalized:
            raise RunbookError("runbook leaks a denied-source test tripwire")
    if any(port in text for port in ("18731", "18732", "6379", "8080", "8000")):
        raise RunbookError("runbook includes a fixed/default service port")
    if re.search(r"\b(?:pkill|killall|FLUSHDB|SHUTDOWN)\b", text, re.I):
        # These appear only in a negative warning and are safe there; the
        # specific check rejects any command line that would execute them.
        if re.search(r"^\s*(?:pkill|killall|redis-cli[^\n]*(?:FLUSHDB|SHUTDOWN)|redis-cli[^\n]*GRAPH\.DELETE)\b", text, re.I | re.M):
            raise RunbookError("teardown must not kill or delete anything outside the exact owned paths")
    if "no ai model is called at runtime" not in normalized or "eligibility risk remains" not in normalized:
        raise RunbookError("truthful AI-use and eligibility-risk disclosures are mandatory")


def _contract_commands() -> list[tuple[str, list[str]]]:
    py = sys.executable
    return [
        ("original-outage", [
            py, "scripts/verify_expected_results.py", "--expected", "tests/expected-results.json",
            "--matrix", "tests/adversarial-matrix.json", "--require-six-scenarios",
        ]),
        ("shift-board", [
            py, "scripts/verify_shift_board_contract.py", "--expected", "tests/shift-board-expected-results.json",
            "--matrix", "tests/shift-board-adversarial-matrix.json", "--pinned-plan", str(SHIFT_PLAN),
            "--source-plan", str(SHIFT_SOURCE_PLAN), "--temporal-plan", str(SHIFT_TEMPORAL_PLAN),
            "--prior-plan", str(SHIFT_PRIOR_PLAN), "--require-eight-cases",
        ]),
        ("sop-impact", [
            py, "scripts/verify_sop_impact_contract.py", "--expected", "tests/sop-impact-expected-results.json",
            "--matrix", "tests/sop-impact-adversarial-matrix.json", "--pinned-plan", str(SOP_PLAN),
            "--require-ten-cases",
        ]),
        ("handover", [
            py, "scripts/verify_handover_brief_contract.py", "--expected", "tests/handover-brief-expected-results.json",
            "--matrix", "tests/handover-brief-adversarial-matrix.json", "--pinned-plan", str(HANDOVER_PLAN),
            "--require-nine-cases",
        ]),
    ]


def run_static_contracts(*, fail_on_zero: bool) -> dict[str, Any]:
    if not fail_on_zero:
        raise RunbookError("static acceptance requires --fail-on-zero")
    hashes = verify_oracle_bytes()
    mapped = inspect_nonzero_mappings()
    runbook = RUNBOOK.read_text(encoding="utf-8")
    validate_runbook_text(runbook)
    results: list[dict[str, Any]] = []
    for name, command in _contract_commands():
        print("+ " + " ".join(command), flush=True)
        completed = subprocess.run(command, cwd=ROOT, text=True, capture_output=True, check=False)
        print(completed.stdout, end="", flush=True)
        if completed.stderr:
            print(completed.stderr, end="", file=sys.stderr, flush=True)
        if completed.returncode != 0:
            raise RunbookError(f"unchanged {name} oracle-contract command failed ({completed.returncode})")
        results.append({"name": name, "returncode": completed.returncode})
    mapped_total = sum(sum(rows.values()) for rows in mapped.values())
    if mapped_total <= 0:
        raise RunbookError("all oracle mappings must contain nonzero assertions")
    return {
        "status": "PASS",
        "oracle_sha256": hashes,
        "mapped_assertions": mapped,
        "mapped_assertion_total": mapped_total,
        "contract_commands": results,
        "runbook": str(RUNBOOK),
    }


def _selected_port_pairs() -> list[tuple[int, int]]:
    try:
        base = int(os.environ["WORKSHOP_PORT_BASE"])
        span = int(os.environ["WORKSHOP_PORT_SPAN"])
        lane = os.environ["WORKSHOP_LANE"]
    except (KeyError, ValueError) as exc:
        raise RunbookError("WORKSHOP_PORT_BASE, WORKSHOP_PORT_SPAN and WORKSHOP_LANE are required") from exc
    if base < 1024 or span < 6 or base + span > 65536 or not re.fullmatch(r"[A-Za-z0-9_-]{1,32}", lane):
        raise RunbookError("allocated port range or WORKSHOP_LANE is invalid")
    pairs = [(base + start, base + start + 1) for start in (0, 2, 4)]
    if any(db == app or db < base or app >= base + span for db, app in pairs):
        raise RunbookError("live verifier ports must be distinct and stay inside the allocated range")
    return pairs


def _run_child(name: str, command: list[str], log_dir: Path) -> tuple[int, str, str]:
    stdout_path = log_dir / f"{name}.stdout.txt"
    stderr_path = log_dir / f"{name}.stderr.txt"
    print("+ " + " ".join(command), flush=True)
    process = subprocess.Popen(
        command, cwd=ROOT, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        start_new_session=True,
    )
    try:
        stdout, stderr = process.communicate(timeout=720)
    except subprocess.TimeoutExpired:
        # Give verifier finally blocks time to stop only their own app/DB and
        # remove only their own graph/state before escalating the timeout.
        try:
            os.killpg(process.pid, signal.SIGINT)
        except ProcessLookupError:
            pass
        try:
            stdout, stderr = process.communicate(timeout=35)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            stdout, stderr = process.communicate(timeout=10)
            stdout_path.write_text(stdout, encoding="utf-8")
            stderr_path.write_text(stderr, encoding="utf-8")
            raise RunbookError(f"{name} verification timed out; timeout and cleanup evidence retained")
        stdout_path.write_text(stdout, encoding="utf-8")
        stderr_path.write_text(stderr, encoding="utf-8")
        raise RunbookError(f"{name} verification timed out after graceful cleanup signal")
    stdout_path.write_text(stdout, encoding="utf-8")
    stderr_path.write_text(stderr, encoding="utf-8")
    print(stdout, end="", flush=True)
    if stderr:
        print(stderr, end="", file=sys.stderr, flush=True)
    return process.returncode, stdout, stderr


def _load_report(path: Path) -> dict[str, Any]:
    try:
        report = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RunbookError(f"missing/invalid live report {path}: {exc}") from exc
    if not isinstance(report, dict):
        raise RunbookError(f"live report {path} is not an object")
    return report


def _check_browser(report_path: Path, *, screenshot_names: tuple[str, str] | None = None) -> dict[str, Any]:
    browser = _load_report(report_path)
    if browser.get("status") != "PASS" or not isinstance(browser.get("assertion_count"), int) or browser["assertion_count"] <= 0:
        raise RunbookError(f"desktop/mobile browser report did not pass nonzero assertions: {report_path}")
    screenshots = browser.get("screenshots", [])
    if not isinstance(screenshots, list):
        raise RunbookError(f"browser screenshot evidence is malformed: {report_path}")
    screenshot_paths = [Path(item) for item in screenshots if isinstance(item, str)]
    if screenshot_names is not None:
        names = {path.name for path in screenshot_paths}
        if not any("desktop" in name.casefold() for name in names) or not any("mobile" in name.casefold() for name in names):
            raise RunbookError(f"browser report does not pin both desktop and mobile screenshots: {report_path}")
        if not screenshot_paths or not all(path.is_file() for path in screenshot_paths):
            raise RunbookError(f"browser-reported desktop/mobile screenshots are missing: {report_path}")
    if screenshot_names is not None:
        reported_names = {path.name for path in screenshot_paths}
        if not set(screenshot_names).issubset(reported_names):
            raise RunbookError(f"desktop/mobile screenshots are missing beside {report_path}")
    return browser


def run_live(*, desktop_mobile: bool, fail_on_zero: bool, assert_owned_cleanup: bool, evidence_root: Path | None = None) -> dict[str, Any]:
    if not (desktop_mobile and fail_on_zero and assert_owned_cleanup):
        raise RunbookError("live acceptance requires --desktop-mobile --fail-on-zero --assert-owned-cleanup")
    hashes = verify_oracle_bytes()
    inspect_nonzero_mappings()
    validate_runbook_text(RUNBOOK.read_text(encoding="utf-8"))
    pairs = _selected_port_pairs()
    lane = os.environ["WORKSHOP_LANE"]
    run_id = uuid.uuid4().hex
    base = evidence_root or Path("/workspace/evidence/wardatlas/demo-runbook") / lane / run_id
    base = base.resolve()
    if not base.is_relative_to(Path("/workspace/evidence")) or base.exists():
        raise RunbookError("live evidence must use a new lane/run-unique directory under /workspace/evidence")
    base.mkdir(parents=True, mode=0o700)
    results: dict[str, Any] = {
        "schema_version": 1,
        "status": "RUNNING",
        "lane": lane,
        "run_id": run_id,
        "ports": [{"db_port": db, "app_port": app} for db, app in pairs],
        "oracle_sha256": hashes,
        "report_directory": str(base),
        "suites": {},
        "owned_cleanup_asserted": False,
    }

    def invoke(name: str, command: list[str], report_path: Path) -> dict[str, Any]:
        rc, _stdout, _stderr = _run_child(name, command, base)
        if rc != 0:
            raise RunbookError(f"{name} actual-FalkorDB/API/browser suite failed with exit {rc}")
        report = _load_report(report_path)
        if report.get("status") != "PASS" or not isinstance(report.get("assertion_count"), int) or report["assertion_count"] <= 0:
            raise RunbookError(f"{name} passed without nonzero live assertions")
        return report

    try:
        db, app = pairs[0]
        original_dir = base / "original-shift"
        original_dir.mkdir(mode=0o700)
        original_report_path = original_dir / "wardatlas-workflow.json"
        original_report = invoke("original-shift-live", [
            sys.executable, "scripts/verify_hospital_workflow.py", "--launch-owned-app", "--loopback-only",
            "--app-port", str(app), "--require-live-falkordb", "--db-port", str(db), "--fail-on-zero",
            "--assert-api", "--assert-browser", "--assert-provenance", "--assert-permissions",
            "--assert-confirmation", "--browser-smoke-tool", "/tools/browser-smoke.py", "--desktop-mobile",
            "--verify-morning-status-and-feature-matrix", "--evidence-dir", str(original_dir),
        ], original_report_path)
        original_cleanup = original_report.get("cleanup", {})
        original_cleanup_keys = (
            "owned_app_stopped", "owned_graph_deleted", "owned_database_process_stopped",
            "temporary_local_state_removed",
        )
        if not isinstance(original_cleanup, dict) or any(original_cleanup.get(key) is not True for key in original_cleanup_keys):
            raise RunbookError("original/shift workflow did not prove exact app, graph, DB, and state cleanup")
        original_browser_path = Path(original_report["browser_assertions_report"])
        original_browser = _check_browser(original_browser_path)
        if not (Path(original_report["desktop_screenshot"]).is_file() and Path(original_report["mobile_screenshot"]).is_file()):
            raise RunbookError("original/shift desktop/mobile screenshots are missing")
        results["suites"]["original-shift"] = {
            "report": str(original_report_path), "assertions": original_report["assertion_count"],
            "browser_report": str(original_browser_path), "browser_assertions": original_browser["assertion_count"],
            "cleanup": original_cleanup,
        }

        db, app = pairs[1]
        sop_dir = base / "sop-impact"
        sop_dir.mkdir(mode=0o700)
        sop_report_path = sop_dir / "live.json"
        sop_report = invoke("sop-impact-live", [
            sys.executable, "scripts/verify_sop_impact_live.py", "--db-port", str(db), "--app-port", str(app),
            "--pinned-plan", str(SOP_PLAN), "--report", str(sop_report_path), "--evidence-dir", str(sop_dir / "browser"),
        ], sop_report_path)
        sop_cleanup = sop_report.get("graph_cleanup", {})
        if not isinstance(sop_cleanup, dict) or sop_cleanup.get("owned_graph_deleted") is not True or sop_cleanup.get("owned_process_stopped") is not True:
            raise RunbookError("SOP-impact suite did not prove its owned graph/process cleanup")
        sop_browser_path = Path(sop_report["browser_report"])
        sop_browser = _check_browser(sop_browser_path, screenshot_names=("sop-impact-desktop.png", "sop-impact-mobile.png"))
        results["suites"]["sop-impact"] = {
            "report": str(sop_report_path), "assertions": sop_report["assertion_count"],
            "browser_report": str(sop_browser_path), "browser_assertions": sop_browser["assertion_count"],
            "cleanup": sop_cleanup,
        }

        db, app = pairs[2]
        handover_dir = base / "handover"
        handover_dir.mkdir(mode=0o700)
        handover_report_path = handover_dir / "live.json"
        handover_report = invoke("handover-live", [
            sys.executable, "scripts/verify_handover_brief_live.py", "--db-port", str(db), "--app-port", str(app),
            "--report", str(handover_report_path), "--evidence-dir", str(handover_dir / "browser"),
        ], handover_report_path)
        nested_cleanup = handover_report.get("database_report", {}).get("graph_cleanup", {})
        if (
            not isinstance(nested_cleanup, dict)
            or nested_cleanup.get("owned_graph_deleted") is not True
            or nested_cleanup.get("owned_process_stopped") is not True
            or handover_report.get("app_stopped") is not True
            or handover_report.get("owned_state_removed") is not True
        ):
            raise RunbookError("handover suite did not prove its app, graph, DB, and state cleanup")
        handover_browser_path = Path(handover_report["browser_report"])
        handover_browser = _check_browser(
            handover_browser_path,
            screenshot_names=("handover-preview-desktop.png", "handover-saved-mobile.png"),
        )
        results["suites"]["handover"] = {
            "report": str(handover_report_path), "assertions": handover_report["assertion_count"],
            "browser_report": str(handover_browser_path), "browser_assertions": handover_browser["assertion_count"],
            "cleanup": {
                **nested_cleanup, "owned_app_stopped": handover_report["app_stopped"],
                "owned_state_removed": handover_report["owned_state_removed"],
            },
        }
        results["owned_cleanup_asserted"] = True
        results["status"] = "PASS"
    except Exception as exc:
        results["status"] = "FAIL"
        results["error"] = f"{type(exc).__name__}: {exc}"
        raise
    finally:
        results["completed_at_utc"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        (base / "runbook-live.json").write_text(json.dumps(results, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"PASS: actual FalkorDB/API, desktop/mobile, exact owned cleanup; evidence: {base / 'runbook-live.json'}")
    return results


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--static-contract", action="store_true")
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--desktop-mobile", action="store_true")
    parser.add_argument("--fail-on-zero", action="store_true")
    parser.add_argument("--assert-owned-cleanup", action="store_true")
    args = parser.parse_args(argv)
    if args.static_contract == args.live:
        parser.error("choose exactly one of --static-contract or --live")
    try:
        if args.static_contract:
            if args.desktop_mobile or args.assert_owned_cleanup:
                raise RunbookError("browser/cleanup options apply only to --live")
            result = run_static_contracts(fail_on_zero=args.fail_on_zero)
            print(
                "PASS: unchanged original, shift-board, SOP-impact and handover oracle checks; "
                f"{result['mapped_assertion_total']} nonzero mapped assertions across four oracles."
            )
        else:
            run_live(
                desktop_mobile=args.desktop_mobile,
                fail_on_zero=args.fail_on_zero,
                assert_owned_cleanup=args.assert_owned_cleanup,
            )
        return 0
    except Exception as exc:
        print(f"FAIL: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

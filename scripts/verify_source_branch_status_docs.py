#!/usr/bin/env python3
"""Bind the branch/timeline status docs to the accepted fresh-source report.

This checker reads only the pinned verification result/report and two status
documents. It does not inspect or execute candidate product behavior.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any


EVIDENCE_DIR = Path(
    "/workspace/evidence/fresh-source-branch-reverify/"
    "67d924f1aec44c22beb3d711c5264be6"
)
PINNED_RESULT = EVIDENCE_DIR / "result.json"
PINNED_REPORT = EVIDENCE_DIR / "verification-report.json"
PINNED_SHA256_FILE = EVIDENCE_DIR / "verification-report.sha256"
PINNED_REPORT_SHA256 = "5eea66d76f13baa419ee8e01b39b3cbe4ef447b2c0881ed31814905e838544e8"
PINNED_SOURCE_COMMIT = "b540f032a50d14384602e638abc1ff59f50160cf"


class VerificationError(ValueError):
    """A pinned source artifact or documented claim differs from acceptance."""


def need(condition: bool, message: str) -> None:
    if not condition:
        raise VerificationError(message)


def _load_json(path: Path, label: str) -> tuple[dict[str, Any], bytes]:
    try:
        raw = path.read_bytes()
        value = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise VerificationError(f"cannot read {label} {path}: {exc}") from exc
    need(isinstance(value, dict), f"{label} must be a JSON object")
    return value, raw


def _check_exact_evidence_paths(result_path: Path, report_path: Path, sha256_file: Path) -> None:
    expected = (
        (result_path, PINNED_RESULT, "result"),
        (report_path, PINNED_REPORT, "report"),
        (sha256_file, PINNED_SHA256_FILE, "report SHA-256 sidecar"),
    )
    for supplied, pinned, label in expected:
        need(supplied.absolute() == pinned, f"{label} must be the pinned evidence path {pinned}")
        need(supplied.is_file() and not supplied.is_symlink(), f"{label} must be a regular file: {supplied}")


def validate_report_hash(report_bytes: bytes, sidecar_text: str) -> str:
    digest = hashlib.sha256(report_bytes).hexdigest()
    need(digest == PINNED_REPORT_SHA256, "verification-report.json differs from the accepted SHA-256")
    expected_sidecar = f"{PINNED_REPORT_SHA256}  verification-report.json"
    need(sidecar_text.strip() == expected_sidecar, "report SHA-256 sidecar differs from its pinned digest/name")
    return digest


def _require(condition: Any, message: str) -> None:
    need(condition is True, message)


def validate_report(report: dict[str, Any], result: dict[str, Any], expected_source_commit: str) -> None:
    """Check evidence payload facts independently of byte-hash/path checks."""
    need(expected_source_commit == PINNED_SOURCE_COMMIT, "expected source commit argument is not the pinned commit")
    need(result.get("status") == "complete", "task result is not complete")

    candidate = report.get("candidate", {})
    need(candidate.get("head_commit") == PINNED_SOURCE_COMMIT, "verified candidate commit differs from the pinned source")
    need(candidate.get("origin_main_after_fetch") == PINNED_SOURCE_COMMIT, "fetched origin/main differs from the pinned source")
    _require(candidate.get("head_equals_latest_origin_main"), "verified candidate was not latest origin/main")
    _require(candidate.get("source_only_at_start"), "fresh checkout was not source-only at start")
    _require(candidate.get("project_local_database_artifact_absent_at_start"), "clean-source database bootstrap was not verified")
    _require(candidate.get("parent_worktree_clean"), "parent worktree was not clean")
    need(candidate.get("tracked_source_changes") is False, "candidate sources changed during verification")

    _require(report.get("status") == "PASS", "fresh-source verification report did not pass")
    oracle = report.get("oracle_gate", {})
    need(oracle.get("result", "").startswith("PASS:"), "frozen branch oracle/review gate did not pass")
    receipt = oracle.get("receipt", {})
    need(receipt.get("result") == "accepted", "content-bound independent-review receipt was not accepted")
    need(receipt.get("literal_mapping_total") == 74, "frozen branch oracle is not 74 mapped assertions")

    verification = report.get("verification", {})
    db = verification.get("actual_falkordb", {})
    need(db.get("status") == "PASS" and db.get("version") == "6.0.2", "actual FalkorDB 6.0.2 was not verified")
    need(db.get("module_version") == 60002, "verified FalkorDB module version is not 60002")
    _require(db.get("cold_empty_owned_graph"), "cold empty owned graph was not verified")
    _require(db.get("module_digest_provenance_verified"), "database module provenance was not verified")

    branch = verification.get("source_branch", {})
    need(branch.get("status") == "PASS", "source-branch live verifier did not pass")
    _require(branch.get("all_assertions_passed"), "source-branch assertions did not all pass")
    need(branch.get("group_count") == 8 and branch.get("mapped_assertions") == 74,
         "source-branch live result differs from 8 groups / 74 assertions")
    _require(branch.get("owned_resources_cleaned"), "source-branch owned resources were not cleaned")
    need(isinstance(branch.get("core_assertions"), dict) and len(branch["core_assertions"]) > 0,
         "source-branch verifier has no nonzero core assertions")
    _require_all(branch.get("cleanup", {}), "source-branch cleanup")

    timeline = verification.get("decision_knowledge_timeline", {})
    need(timeline.get("status") == "PASS", "decision timeline live verifier did not pass")
    _require(timeline.get("all_assertions_passed"), "decision timeline assertions did not all pass")
    need(timeline.get("group_count") == 12 and timeline.get("mapped_assertions") == 120,
         "decision timeline result differs from 12 groups / 120 assertions")
    _require(timeline.get("owned_resources_cleaned"), "timeline owned resources were not cleaned")
    _require_all(timeline.get("cleanup", {}), "timeline cleanup")

    unit = verification.get("unit_tests", {})
    need(unit.get("status") == "PASS" and unit.get("tests") == 238,
         "fresh-source unit test result differs from 238 passing tests")
    need(unit.get("errors") == 0 and unit.get("failures") == 0, "unit test errors/failures are nonzero")

    integrated = verification.get("integrated_workflow", {})
    need(integrated.get("status") == "PASS" and integrated.get("assertions") == 79,
         "integrated API/browser result differs from 79 passing assertions")
    _require(integrated.get("all_assertions_passed"), "integrated workflow assertions did not all pass")
    need(integrated.get("falkordb_module_version") == 60002, "integrated workflow did not use FalkorDB 6.0.2")
    need(integrated.get("failures") == [], "integrated workflow records failures")
    _require_all(integrated.get("cleanup", {}), "integrated workflow cleanup")
    for field in ("desktop_screenshot", "mobile_screenshot"):
        need(isinstance(integrated.get(field), str) and integrated[field].startswith(str(EVIDENCE_DIR) + "/"),
             f"integrated workflow lacks task-owned {field}")

    result_evidence = result.get("evidence", [])
    need(isinstance(result_evidence, list), "task result evidence must be an array")
    joined_result = f"{result.get('summary', '')}\n" + "\n".join(str(item) for item in result_evidence)
    for required in (
        str(PINNED_REPORT),
        "SHA-256 sidecar verification-report.sha256",
        PINNED_SOURCE_COMMIT,
        "74 branch assertions",
        "120 existing decision-timeline assertions",
        "238 unit tests",
        "79 integrated desktop/mobile assertions",
        "FalkorDB 6.0.2",
    ):
        need(required in joined_result, f"task result evidence omits accepted fact: {required}")


def _require_all(cleanup: dict[str, Any], label: str) -> None:
    need(isinstance(cleanup, dict) and bool(cleanup), f"{label} receipt is missing")
    for name, value in cleanup.items():
        _require(value, f"{label} failed at {name}")


def _normalized(value: str) -> str:
    return " ".join(value.casefold().split())


def _require_phrases(text: str, phrases: tuple[str, ...], label: str) -> None:
    normalized = _normalized(text)
    for phrase in phrases:
        need(_normalized(phrase) in normalized, f"{label} omits required accepted fact: {phrase}")


def _section(text: str, heading: str) -> str:
    lines = text.splitlines()
    matching = [index for index, line in enumerate(lines) if line.strip() == heading]
    need(len(matching) == 1, f"status document must contain exactly one {heading!r} section")
    start = matching[0] + 1
    end = next((i for i in range(start, len(lines)) if lines[i].startswith("## ")), len(lines))
    return "\n".join(lines[start:end])


def _table_row(text: str, name: str) -> str:
    prefix = f"| {name} |"
    rows = [line for line in text.splitlines() if line.startswith(prefix)]
    need(len(rows) == 1, f"feature matrix must have exactly one distinct {name!r} row")
    return rows[0]


def _reject_stale_pending_claims(morning_section: str, timeline_row: str) -> None:
    stale = re.compile(
        r"\b(?:fresh|clean[- ]source|independent)\b.{0,90}\b(?:reverify|re-verification|verification)\b"
        r".{0,55}\b(?:pending|remains required|still required|not yet complete)\b",
        re.IGNORECASE,
    )
    for label, text in (("current branch status", morning_section), ("timeline feature row", timeline_row)):
        need(not stale.search(text), f"{label} still claims an accepted source-branch/timeline reverify is pending")


def _reject_unverified_video_claims(text: str) -> None:
    positive_video = re.compile(
        r"\b(?:demo\s+)?video\s+(?:exists|is\s+(?:available|recorded|published|ready|complete|done)|"
        r"was\s+recorded|has\s+been\s+(?:recorded|published|verified|released))\b|"
        r"\b(?:recorded|published|available|released)\s+(?:demo\s+)?video\b",
        re.IGNORECASE,
    )
    for match in positive_video.finditer(text):
        prefix = text[max(0, match.start() - 32):match.start()].casefold()
        need(bool(re.search(r"\b(?:no|not|never|unverified|unclaimed)\b[^.!?]{0,28}$", prefix)),
             "status documents claim a demo video/recording exists without accepted video evidence")


def _reject_safety_overclaims(text: str) -> None:
    patterns = (
        r"\bclinically\s+validated\b",
        r"\bclinical\s+validation\s+(?:passed|complete|confirmed)\b",
        r"\b(?:HIPAA|GDPR)[- ]compliant\b",
        r"\b(?:safe|approved|ready)\s+(?:for\s+)?(?:patient\s+care|clinical\s+use|real[- ]hospital\s+use)\b",
        r"\bmedical\s+device\s+(?:approved|certified|validated)\b",
    )
    for pattern in patterns:
        for match in re.finditer(pattern, text, re.IGNORECASE):
            prefix = text[max(0, match.start() - 28):match.start()].casefold()
            need(bool(re.search(r"\b(?:not|never|no)\b[^.!?]{0,24}$", prefix)),
                 "status documents make a clinical, compliance, safety or real-hospital validation claim")


def _reject_count_contradictions(morning_section: str, timeline_row: str, branch_row: str) -> None:
    for label, text in (("fresh-source branch status", morning_section), ("branch matrix row", branch_row)):
        observed_test_counts = [int(value) for value in re.findall(r"\b(\d+)\s+tests?\b", text, re.IGNORECASE)]
        need(bool(observed_test_counts) and all(value == 238 for value in observed_test_counts),
             f"{label} contains a non-accepted or contradictory unit-test count")
    branch_pairs = re.findall(
        r"\b(\d+)\s+groups?\s*/\s*(\d+)\s+(?:(?:mapped|literal)\s+)?assertions?\b",
        f"{morning_section}\n{branch_row}",
        re.IGNORECASE,
    )
    need(bool(branch_pairs) and all((int(groups), int(assertions)) == (8, 74) for groups, assertions in branch_pairs),
         "branch status contains a non-accepted or contradictory group/assertion count")
    timeline_pairs = re.findall(
        r"\b(\d+)\s+groups?\s*/\s*(\d+)\s+(?:(?:mapped|literal)\s+)?assertions?\b",
        timeline_row,
        re.IGNORECASE,
    )
    need(bool(timeline_pairs) and all((int(groups), int(assertions)) == (12, 120) for groups, assertions in timeline_pairs),
         "timeline row contains a non-accepted or contradictory group/assertion count")


def validate_status_documents(
    morning_status: str,
    feature_matrix: str,
    *,
    reject_pending_claims: bool,
    reject_unverified_video_claims: bool,
) -> None:
    branch_heading = "## Hypothetical source-observation branch — 2026-10-18 build and fresh-source verification"
    current_branch = _section(morning_status, branch_heading)
    timeline_row = _table_row(feature_matrix, "Source-observation-time decision replay")
    branch_row = _table_row(feature_matrix, "Hypothetical source-observation branch")
    need(branch_row != timeline_row, "hypothetical branch must be a distinct matrix row from timeline replay")

    _require_phrases(
        current_branch,
        (
            "original source fact remains observed at **16:04Z**",
            "at the shared **16:05Z** cutoff, unchanged mainline includes V3 with its actual 16:04Z source timestamp and the graph-derived unresolved V2/V3 conflict",
            "the hypothetical branch instead assumes V3 at 16:06Z",
            "keeps V2 and the separately evidenced `open-unresolved` record but suppresses V3 and its dependent conflict",
            "at **16:06Z**, V3 returns only in the explicitly hypothetical pane with assumed time 16:06Z and actual source observation still 16:04Z",
            "mainline remains unchanged",
            "independent fresh-source evidence is accepted",
            PINNED_REPORT_SHA256,
            PINNED_SOURCE_COMMIT,
            "**8 groups / 74 assertions**",
            "timeline (**12 / 120**)",
            "**238 tests**",
            "**79 assertions**",
            "actual FalkorDB **6.0.2**",
            "generic `incomplete/unknown`",
            "does not fall back to mainline facts",
            "restricted-source identities, titles, facts, or counts",
            "synthetic, operations-scoped, read-only and non-clinical",
            "not clinical validation, not a medical device, and not real-hospital validation",
            "No demo video is claimed to have been recorded or verified",
            "The independent source-only reverify is complete and accepted",
        ),
        "morning branch status",
    )

    _require_phrases(
        timeline_row,
        (
            "original V3 Evidence fact remains observed at 16:04Z",
            "unchanged mainline at 16:05Z includes V3 and its graph-derived unresolved conflict",
            "in the separate adversarial timestamp-shift mutation only",
            "V3's test observation time moves to 16:06Z",
            "suppressing its claim, conflict and considered link at 16:05",
            "retaining V2 and the separately evidenced record",
            "shifted test projection includes them at 16:06",
            "Fresh-source independent verification also passed the timeline suite (12/120)",
            PINNED_SOURCE_COMMIT,
        ),
        "timeline matrix row",
    )

    _require_phrases(
        branch_row,
        (
            "original source observation at **16:04Z**",
            "at the shared **16:05Z** cutoff, mainline retains source-backed V3 and its graph-derived unresolved conflict",
            "hypothetical projection suppresses V3 and that dependent conflict while retaining V2 and the separately evidenced `open-unresolved` record",
            "at **16:06Z**, V3 returns only in the hypothetical pane with assumed time 16:06Z and actual source observation still 16:04Z",
            PINNED_REPORT_SHA256,
            PINNED_SOURCE_COMMIT,
            "actual FalkorDB 6.0.2",
            "**8 groups / 74 mapped assertions**",
            "**12 / 120**",
            "**79 assertions**",
            "**238 tests**",
            "generic `incomplete/unknown`",
            "never falls back to mainline evidence",
            "restricted-source identities, titles, facts and counts are not disclosed",
            "synthetic",
            "read-only",
            "non-clinical",
            "not clinical validation",
            "not a medical device",
            "not real-hospital validation",
            "No video is claimed by this evidence",
        ),
        "hypothetical branch matrix row",
    )

    _reject_count_contradictions(current_branch, timeline_row, branch_row)

    combined = f"{morning_status}\n{feature_matrix}"
    if reject_pending_claims:
        _reject_stale_pending_claims(current_branch, timeline_row)
    if reject_unverified_video_claims:
        _reject_unverified_video_claims(combined)
    _reject_safety_overclaims(combined)
    _require_phrases(combined, ("denied-source", "non-clinical"), "combined status documents")


def verify(
    morning_path: Path,
    feature_matrix_path: Path,
    result_path: Path,
    report_path: Path,
    sha256_file: Path,
    expected_source_commit: str,
    *,
    assert_accepted_counts: bool,
    reject_pending_claims: bool,
    reject_unverified_video_claims: bool,
) -> str:
    _check_exact_evidence_paths(result_path, report_path, sha256_file)
    result, _ = _load_json(result_path, "task result")
    report, report_bytes = _load_json(report_path, "verification report")
    try:
        sha_text = sha256_file.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise VerificationError(f"cannot read report SHA-256 sidecar: {exc}") from exc
    digest = validate_report_hash(report_bytes, sha_text)
    validate_report(report, result, expected_source_commit)
    need(assert_accepted_counts, "--assert-accepted-counts is required")
    try:
        morning = morning_path.read_text(encoding="utf-8")
        matrix = feature_matrix_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise VerificationError(f"cannot read status documents: {exc}") from exc
    validate_status_documents(
        morning,
        matrix,
        reject_pending_claims=reject_pending_claims,
        reject_unverified_video_claims=reject_unverified_video_claims,
    )
    return digest


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--morning-status", type=Path, required=True)
    parser.add_argument("--feature-matrix", type=Path, required=True)
    parser.add_argument("--result", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--sha256-file", type=Path, required=True)
    parser.add_argument("--expected-source-commit", required=True)
    parser.add_argument("--assert-accepted-counts", action="store_true", required=True)
    parser.add_argument("--reject-pending-claims", action="store_true", required=True)
    parser.add_argument("--reject-unverified-video-claims", action="store_true", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        digest = verify(
            args.morning_status,
            args.feature_matrix,
            args.result,
            args.report,
            args.sha256_file,
            args.expected_source_commit,
            assert_accepted_counts=args.assert_accepted_counts,
            reject_pending_claims=args.reject_pending_claims,
            reject_unverified_video_claims=args.reject_unverified_video_claims,
        )
    except VerificationError as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1
    print(
        "PASS: status docs match accepted fresh-source evidence "
        f"(commit {PINNED_SOURCE_COMMIT}, report SHA-256 {digest}, FalkorDB 6.0.2, "
        "branch 8/74, timeline 12/120, unit tests 238, integrated assertions 79)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

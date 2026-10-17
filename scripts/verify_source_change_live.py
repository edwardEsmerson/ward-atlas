#!/usr/bin/env python3
"""Run eight pinned source-change cases against an owned real FalkorDB 6.0.2."""

from __future__ import annotations

import argparse
import ast
import json
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Any

from verify_falkordb_live import (
    LiveVerifier,
    RespConnection,
    VerificationFailure,
    query_result,
    result_rows,
    text,
)
from wardatlas_source_changes import (
    DEPARTMENT,
    EFFECTIVE_AT,
    EQUIPMENT,
    HANDOVER,
    INCIDENT,
    LEXICAL_HANDOVER,
    ROLE_AFTER,
    ROLE_BEFORE,
    SERVICE,
    SOURCE_AFTER,
    SOURCE_BEFORE,
    SOP_V2,
    SOP_V3,
    TICKET,
    SourceChangeError,
    SourceChangeStore,
    apply_update,
    normalize_time,
    snapshot,
    update_fixture,
    validate_update,
)


ROOT = Path(__file__).resolve().parents[1]
EXPECTED_DEFAULT = ROOT / "tests/source-change-expected-results.json"
MATRIX_DEFAULT = ROOT / "tests/source-change-adversarial-matrix.json"
MORNING_STATUS = ROOT / "MORNING-STATUS.md"
FEATURE_MATRIX = ROOT / "FEATURE-MATRIX.md"
CLI_PATH = ROOT / "scripts/wardatlas_source_changes.py"


class SourceLiveVerifier:
    def __init__(self, port: int, expected: dict[str, Any], matrix: dict[str, Any], evidence_dir: Path):
        self.live = LiveVerifier(port, expected, matrix)
        self.lane = os.environ.get("WORKSHOP_LANE", "")
        self.live.graph = f"wardatlas_source_{self.lane}_{uuid.uuid4().hex[:12]}"
        self.expected = expected
        self.matrix = matrix
        self.evidence_dir = evidence_dir
        self.cli_runs: list[dict[str, Any]] = []

    def query(self, cypher: str) -> dict[str, Any]:
        return self.live.graph_query("source-change-query", cypher)

    @staticmethod
    def rows(result: dict[str, Any]) -> list[dict[str, Any]]:
        return result_rows(result)

    def scenario(self, identifier: str, callback) -> None:
        self.live.scenario(identifier, callback)

    def check(self, case: dict[str, Any], name: str, condition: bool, detail: Any = None) -> None:
        self.live.check(case, name, condition, detail)

    def cli(self, at: str, expected_return: int = 0) -> tuple[dict[str, Any] | None, subprocess.CompletedProcess[str]]:
        env = os.environ.copy()
        command = [
            sys.executable, str(CLI_PATH),
            "--db-port", str(self.live.port),
            "--graph-name", self.live.graph,
            "--seed-synthetic",
            "--at", at,
        ]
        completed = subprocess.run(command, cwd=ROOT, env=env, capture_output=True, text=True, timeout=40)
        row: dict[str, Any] = {
            "argv": command,
            "returncode": completed.returncode,
            "stdout": completed.stdout.strip(),
            "stderr": completed.stderr.strip(),
        }
        self.cli_runs.append(row)
        if expected_return == 0:
            if completed.returncode != 0:
                raise VerificationFailure(f"source-change CLI failed: {completed.stderr.strip()}")
            try:
                parsed = json.loads(completed.stdout)
            except json.JSONDecodeError as exc:
                raise VerificationFailure(f"source-change CLI did not emit JSON: {completed.stdout!r}") from exc
            # The CLI process created/seeded only the unique graph assigned to
            # this verifier. Mark it owned for the parent verifier's cleanup.
            self.live.graph_created = True
            return parsed, completed
        if completed.returncode != expected_return:
            raise VerificationFailure(f"source-change CLI returned {completed.returncode}, expected {expected_return}: {completed.stderr.strip()}")
        return None, completed

    def run(self) -> None:
        if not self.lane:
            raise VerificationFailure("WORKSHOP_LANE is required for unique graph names")
        self.live.launch()
        store = SourceChangeStore(self.live.graph, self.lane, self.query)
        scenarios = {item["id"]: item for item in self.expected["scenarios"]}
        fixed_before = self.expected["expected_paths"]["before"]
        fixed_after = self.expected["expected_paths"]["after"]

        def empty_graph(case: dict[str, Any]) -> None:
            counts = store.counts()
            result = snapshot(store, "2026-10-14T20:29:00Z")
            self.check(case, "empty graph has zero nodes and relationships", counts == {"nodes": 0, "relationships": 0}, counts)
            self.check(case, "empty graph has no role path or source versions", result["role_path"] is None and result["source_versions"] == [], result)
            self.check(case, "empty graph has no review candidates", result["review_candidates"] == [], result["review_candidates"])
            self.check(case, "empty graph labels explicit-edge coverage", result["coverage_status"] == "explicit_edges_only", result["coverage_status"])
            self.check(case, "empty graph leaves unlinked status unknown", result["unlinked_artifact_status"] == "unknown", result["unlinked_artifact_status"])

        self.scenario("source-empty-graph", empty_graph)

        before_result: dict[str, Any] | None = None

        def before_boundary(case: dict[str, Any]) -> None:
            nonlocal before_result
            before_result, _ = self.cli("2026-10-14T20:29:00Z")
            self.check(case, "CLI emits exact pinned as-of time", before_result.get("as_of") == "2026-10-14T20:29:00Z", before_result.get("as_of"))
            self.check(case, "pre-effective five-hop role path is exactly R1", before_result.get("role_path") == fixed_before, before_result.get("role_path"))
            self.check(case, "new role is not applied before its effective instant", before_result.get("role_path", [])[-1:] == [ROLE_BEFORE], before_result.get("role_path"))

        self.scenario("dock-role-path-before-effective-time", before_boundary)

        after_result: dict[str, Any] | None = None

        def after_boundary(case: dict[str, Any]) -> None:
            nonlocal after_result
            after_result, _ = self.cli(EFFECTIVE_AT)
            self.check(case, "at the effective boundary exact R2 five-hop path is returned", after_result.get("role_path") == fixed_after, after_result.get("role_path"))
            path = after_result.get("role_path") or []
            self.check(case, "only the final role endpoint changes", path[:-1] == fixed_before[:-1] and path[-1:] == [ROLE_AFTER], path)
            versions = after_result.get("source_versions")
            expected_versions = scenarios["dock-role-path-after-effective-time"]["expected"]["source_versions"]
            projection = [{key: row.get(key) for key in expected.keys()} for row, expected in zip(versions or [], expected_versions)]
            self.check(case, "both append-only source versions and timestamps remain inspectable", projection == expected_versions and len(versions or []) == 2, versions)
            self.check(case, "R2 explicitly supersedes R1", any(row.get("source_id") == SOURCE_AFTER and row.get("supersedes") == SOURCE_BEFORE for row in versions or []), versions)
            self.check(case, "R2 observed/effective timestamps are preserved", any(row.get("observed_at") == "2026-10-14T20:18:00Z" and row.get("effective_at") == EFFECTIVE_AT for row in versions or []), versions)

        self.scenario("dock-role-path-after-effective-time", after_boundary)

        def linked_queue(case: dict[str, Any]) -> None:
            assert after_result is not None
            self.check(case, "only explicitly cited HND-DOCK-2020 and TKT-DOCK-88 are queued", after_result.get("review_candidates") == [HANDOVER, TICKET], after_result.get("review_candidates"))
            old = self.rows(self.query(f"MATCH (h:Artifact {{id: '{HANDOVER}'}}) RETURN h.role_id AS role_id, h.cited_source_id AS source_id, h.body AS body"))
            self.check(case, "old handover keeps its R1 role/source evidence unchanged", old == [{"role_id": ROLE_BEFORE, "source_id": SOURCE_BEFORE, "body": "Synthetic dock handover records ROLE-DOCK-DUTY under roster R1."}], old)
            self.check(case, "lexical-only handover is absent from exact queue", LEXICAL_HANDOVER not in after_result.get("review_candidates", []), after_result.get("review_candidates"))
            self.check(case, "coverage status says explicit edges only", after_result.get("coverage_status") == "explicit_edges_only", after_result.get("coverage_status"))
            self.check(case, "unlinked artifact status is unknown, not current", after_result.get("unlinked_artifact_status") == "unknown", after_result.get("unlinked_artifact_status"))

        self.scenario("explicit-linked-review-queue", linked_queue)

        def conflict_preserved(case: dict[str, Any]) -> None:
            assert after_result is not None
            conflicts = after_result.get("sop_conflicts", [])
            self.check(case, "both conflicting SOP versions remain visible", len(conflicts) == 1 and [conflicts[0].get("version_a"), conflicts[0].get("version_b")] == [SOP_V2, SOP_V3], conflicts)
            self.check(case, "SOP dispute remains unresolved", len(conflicts) == 1 and conflicts[0].get("conflict_status") == "unresolved", conflicts)
            self.check(case, "neither conflicting claim is promoted", len(conflicts) == 1 and {conflicts[0].get("claim_a"), conflicts[0].get("claim_b")} == {"ROLE-FACILITIES-DUTY", "ROLE-MATERIALS-LEAD"}, conflicts)
            self.check(case, "no action proposal is emitted", "actions_proposed" not in after_result, list(after_result))

        self.scenario("unresolved-sop-conflict-preserved", conflict_preserved)

        def denied_fixture() -> None:
            node_props = [
                ("Equipment", "EQ-RESTRICTED-DOCK-99", "n.status = 'unavailable', n.access = 'restricted'"),
                ("Department", "DEPT-RESTRICTED-DOCK-99", "n.access = 'restricted'"),
                ("Role", "ROLE-RESTRICTED-DOCK-OWNER", "n.access = 'restricted'"),
                ("Evidence", "SRC-RESTRICTED-DOCK-99", "n.title = 'Restricted Dock Continuity Annex 99', n.fact = 'Restricted-only note: logistics bay status unresolved.', n.observed_at = '2026-10-14T20:19:00Z', n.access = 'restricted'"),
            ]
            for label, identifier, props in node_props:
                self.query(f"MERGE (n:{label} {{id: '{identifier}'}}) SET n.id = '{identifier}', {props}")
            edge_specs = [
                (INCIDENT, "AFFECTS", "EQ-RESTRICTED-DOCK-99", "REL-INC207-RESTRICTED", "restricted"),
                ("EQ-RESTRICTED-DOCK-99", "SUPPORTS", SERVICE, "REL-RESTRICTED-INBOUND", "restricted"),
                (SERVICE, "SERVES", "DEPT-RESTRICTED-DOCK-99", "REL-INBOUND-RESTRICTED", "restricted"),
                ("DEPT-RESTRICTED-DOCK-99", "OWNED_BY", "ROLE-RESTRICTED-DOCK-OWNER", "REL-RESTRICTED-DOCK-OWNER", "restricted"),
                ("EQ-RESTRICTED-DOCK-99", "EVIDENCED_BY", "SRC-RESTRICTED-DOCK-99", "REL-RESTRICTED-DOCK-EVIDENCE", "restricted"),
            ]
            for source, kind, target, rel_id, access in edge_specs:
                self.query(f"MATCH (a {{id: '{source}'}}), (b {{id: '{target}'}}) MERGE (a)-[r:{kind} {{id: '{rel_id}'}}]->(b) SET r.id = '{rel_id}', r.access = '{access}'")

        def non_disclosure(case: dict[str, Any]) -> None:
            assert after_result is not None
            before_semantic = {key: value for key, value in after_result.items() if key not in {"graph_query_count", "graph_query_elapsed_ms"}}
            before = json.dumps(before_semantic, sort_keys=True, separators=(",", ":"))
            denied_fixture()
            after = snapshot(store, EFFECTIVE_AT)
            serialized = json.dumps(after, sort_keys=True, separators=(",", ":"))
            self.check(case, "operations output remains exactly unchanged after restricted branch injection", before == serialized, after)
            hidden = [
                "SRC-RESTRICTED-DOCK-99", "Restricted Dock Continuity Annex 99",
                "Restricted-only note: logistics bay status unresolved.", "EQ-RESTRICTED-DOCK-99",
                "DEPT-RESTRICTED-DOCK-99", "ROLE-RESTRICTED-DOCK-OWNER",
            ]
            self.check(case, "all fixed denied-source IDs/title/fact are absent", all(value not in serialized for value in hidden), {value: value in serialized for value in hidden})
            self.check(case, "no denied graph counts or existence signal is returned", not any(key in after for key in ("hidden_count", "restricted_count", "denied_count", "hidden_exists")), list(after))
            self.check(case, "no restricted or derived role path is returned", after.get("role_path") == self.expected["expected_paths"]["after"] and after.get("review_candidates") == [HANDOVER, TICKET], after)
            self.check(case, "no restricted artifact enters the explicit review queue", all("RESTRICTED" not in value for value in after.get("review_candidates", [])), after.get("review_candidates"))

        self.scenario("restricted-source-nondisclosure", non_disclosure)

        def idempotent_and_removed_edge(case: dict[str, Any]) -> None:
            before_counts = store.counts()
            valid = update_fixture()
            apply_update(store, json.dumps(valid))
            once_counts = store.counts()
            apply_update(store, json.dumps(valid))
            twice_counts = store.counts()
            self.check(case, "repeated synthetic JSON update creates zero nodes", once_counts["nodes"] == before_counts["nodes"] and twice_counts["nodes"] == before_counts["nodes"], {"before": before_counts, "once": once_counts, "twice": twice_counts})
            self.check(case, "repeated synthetic JSON update creates zero relationships", once_counts["relationships"] == before_counts["relationships"] and twice_counts["relationships"] == before_counts["relationships"], {"before": before_counts, "once": once_counts, "twice": twice_counts})
            self.query(f"MATCH (:Artifact {{id: '{HANDOVER}'}})-[r:CITES_SOURCE {{id: 'REL-HND2020-CITES-R1'}}]->(:Evidence {{id: '{SOURCE_BEFORE}'}}) DELETE r")
            queue = store.review_candidates(EFFECTIVE_AT)
            self.check(case, "removing handover citation removes only that candidate", HANDOVER not in queue and queue == [TICKET], queue)
            self.check(case, "unrelated explicit ticket dependency remains", queue == [TICKET], queue)
            old = self.rows(self.query(f"MATCH (h:Artifact {{id: '{HANDOVER}'}}) RETURN h.role_id AS role_id, h.cited_source_id AS source_id"))
            sources = store.source_versions()
            self.check(case, "counterfactual does not rewrite old handover or source history", old == [{"role_id": ROLE_BEFORE, "source_id": SOURCE_BEFORE}] and len(sources) == 2, {"handover": old, "source_versions": sources})

        self.scenario("append-only-idempotent-edge-counterfactual", idempotent_and_removed_edge)

        def normalize_reject_reconnect(case: dict[str, Any]) -> None:
            offset = snapshot(store, "2026-10-14T22:30:00+02:00")
            self.check(case, "offset timestamp normalizes to exact pinned UTC instant", offset.get("as_of") == "2026-10-14T20:30:00Z", offset.get("as_of"))
            self.check(case, "offset instant selects the exact R2 role path", offset.get("role_path") == fixed_after, offset.get("role_path"))
            before_counts = store.counts()
            malformed = update_fixture()
            malformed["source"]["effective_at"] = "tomorrow"
            malformed["assignment"]["effective_at"] = "tomorrow"
            malformed_rejected = False
            try:
                apply_update(store, malformed)
            except SourceChangeError:
                malformed_rejected = True
            after_malformed_counts = store.counts()
            self.check(case, "malformed effective timestamp is rejected before mutation", malformed_rejected and before_counts == after_malformed_counts, {"rejected": malformed_rejected, "before": before_counts, "after": after_malformed_counts})
            missing = update_fixture()
            del missing["source"]["effective_at"]
            missing_rejected = False
            try:
                apply_update(store, missing)
            except SourceChangeError:
                missing_rejected = True
            after_missing_counts = store.counts()
            self.check(case, "missing effective timestamp is rejected before mutation", missing_rejected and before_counts == after_missing_counts, {"rejected": missing_rejected, "before": before_counts, "after": after_missing_counts})
            cli_invalid, _ = self.cli("not-a-timestamp", expected_return=2)
            self.check(case, "malformed CLI --at is rejected before output or graph mutation", cli_invalid is None and store.counts() == before_counts, store.counts())
            assert self.live.client is not None
            old_token = self.live.client.connection_token
            old_client_id = int(self.live.command("old source-change CLIENT ID", "CLIENT", "ID"))
            self.live.client.close()
            self.live.client = RespConnection("127.0.0.1", self.live.port)
            new_token = self.live.client.connection_token
            new_client_id = int(self.live.command("new source-change CLIENT ID", "CLIENT", "ID"))
            reconnected = snapshot(store, EFFECTIVE_AT)
            self.check(case, "new Redis socket/client reconnects to same FalkorDB graph", old_token != new_token and old_client_id != new_client_id, {"old_client_id": old_client_id, "new_client_id": new_client_id})
            self.check(case, "reconnect preserves current role path and exact remaining candidate queue", reconnected.get("role_path") == fixed_after and reconnected.get("review_candidates") == [TICKET], reconnected)
            self.check(case, "no external effect was performed or represented", reconnected.get("external_effects") == [] and "actions_proposed" not in reconnected, reconnected.get("external_effects"))
            source_text = CLI_PATH.read_text(encoding="utf-8")
            tree = ast.parse(source_text)
            forbidden = {"requests", "httpx", "urllib", "smtplib", "imaplib", "boto3", "openai"}
            imported = set()
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    imported.update(alias.name.split(".", 1)[0] for alias in node.names)
                elif isinstance(node, ast.ImportFrom) and node.module:
                    imported.add(node.module.split(".", 1)[0])
            self.check(case, "CLI has no external connector/account/client dependency", not (imported & forbidden), sorted(imported & forbidden))

        self.scenario("reconnect-time-normalization-and-rejection", normalize_reject_reconnect)

    def stop(self) -> None:
        self.live.stop()

    def report(self, status: str, error: str | None = None) -> dict[str, Any]:
        data = self.live.report(status, error)
        data["verifier_version"] = "1.0-source-change"
        parsed_cli_outputs = []
        for run in self.cli_runs:
            try:
                parsed_cli_outputs.append(json.loads(run.get("stdout", "")))
            except json.JSONDecodeError:
                continue
        cli_query_count = sum(int(output.get("graph_query_count", 0)) for output in parsed_cli_outputs)
        data["source_change"] = {
            "cli_runs": self.cli_runs,
            "cli_graph_query_count": cli_query_count,
            "total_observed_graph_query_count": self.live.query_count + cli_query_count,
            "coverage_status": "explicit_edges_only",
            "unlinked_artifact_status": "unknown",
            "external_effects": [],
            "scenario_ids": [row.get("id") for row in self.live.scenarios],
        }
        return data


def verify_status_documents() -> None:
    status = MORNING_STATUS.read_text(encoding="utf-8") if MORNING_STATUS.is_file() else ""
    features = FEATURE_MATRIX.read_text(encoding="utf-8") if FEATURE_MATRIX.is_file() else ""
    required_status = [
        "Source-change CLI-only increment",
        "python3 scripts/verify_source_change_oracle.py",
        "python3 scripts/verify_source_change_live.py",
        "explicit_edges_only",
        "unlinked_artifact_status=unknown",
        "UI integration limitation",
    ]
    required_features = [
        "Source revision / dependency review CLI",
        "CLI-only",
        "explicit_edges_only",
        "unknown",
        "UI integration limitation",
    ]
    missing = [item for item in required_status if item not in status] + [item for item in required_features if item not in features]
    if missing:
        raise VerificationFailure(f"MORNING-STATUS.md or FEATURE-MATRIX.md omits honest source-change status: {missing}")
    if "integrated into the UI" in status or "integrated into the UI" in features:
        raise VerificationFailure("CLI-only source-change behavior is incorrectly claimed as UI-integrated")


def lane_port_check(port: int) -> None:
    try:
        base = int(os.environ["WORKSHOP_PORT_BASE"])
        span = int(os.environ["WORKSHOP_PORT_SPAN"])
    except (KeyError, ValueError) as exc:
        raise VerificationFailure("WORKSHOP_PORT_BASE and WORKSHOP_PORT_SPAN are required") from exc
    if not (base <= port < base + span and 1024 <= port <= 65535):
        raise VerificationFailure("--db-port must fall inside this lane's explicit port range")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--require-live", action="store_true", required=True)
    parser.add_argument("--fail-on-zero", action="store_true", required=True)
    parser.add_argument("--fresh-owned-graph", action="store_true", required=True)
    parser.add_argument("--db-port", type=int, required=True)
    parser.add_argument("--expected", type=Path, default=EXPECTED_DEFAULT)
    parser.add_argument("--matrix", type=Path, default=MATRIX_DEFAULT)
    parser.add_argument("--verify-morning-status-and-feature-matrix", action="store_true", required=True)
    parser.add_argument("--evidence-dir", type=Path, required=True)
    args = parser.parse_args()
    verifier: SourceLiveVerifier | None = None
    error: str | None = None
    status = "FAIL"
    report_path = args.evidence_dir / "source-change-live.json"
    try:
        lane_port_check(args.db_port)
        expected = json.loads(args.expected.read_text(encoding="utf-8"))
        matrix = json.loads(args.matrix.read_text(encoding="utf-8"))
        if expected.get("product") != "WardAtlas" or matrix.get("oracle_file") != "tests/source-change-expected-results.json":
            raise VerificationFailure("source-change live verifier received an unexpected oracle or matrix")
        if len(expected.get("scenarios", [])) != 8 or len(matrix.get("cases", [])) != 8:
            raise VerificationFailure("all eight nonzero source-change scenarios are required")
        verifier = SourceLiveVerifier(args.db_port, expected, matrix, args.evidence_dir)
        verifier.run()
        if verifier.live.assertion_count <= 0:
            raise VerificationFailure("--fail-on-zero: no live assertions were made")
        actual = [item["id"] for item in verifier.live.scenarios]
        pinned = [item["id"] for item in expected["scenarios"]]
        if actual != pinned or len(set(actual)) != 8:
            raise VerificationFailure(f"live cases do not cover all eight pinned scenarios exactly once: {actual!r}")
        if any(item["status"] != "pass" or item["assertions"] <= 0 for item in verifier.live.scenarios):
            raise VerificationFailure("each pinned scenario must pass with at least one live assertion")
        if args.verify_morning_status_and_feature_matrix:
            verify_status_documents()
        status = "PASS"
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
        if verifier:
            verifier.live.failures.append(error)
    finally:
        if verifier:
            verifier.stop()
            cleanup_ok = verifier.live.cleanup.get("owned_graph_deleted") is True and verifier.live.cleanup.get("owned_process_stopped") is True
            if status == "PASS" and not cleanup_ok:
                error = "VerificationFailure: owned graph/process cleanup was not confirmed"
                verifier.live.failures.append(error)
                status = "FAIL"
            report = verifier.report(status, error)
        else:
            report = {"schema_version": 1, "verifier_version": "1.0-source-change", "status": status, "error": error}
        args.evidence_dir.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(json.dumps({"status": status, "report": str(report_path), "assertions": report.get("assertion_count", 0), "queries": report.get("graph_query_count", 0), "error": error}, sort_keys=True))
    return 0 if status == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Live API, browser, permissions, and confirmation checks for WardAtlas UI.

This verifier owns one pinned FalkorDB process/graph and one loopback app
process. It never contacts an already-listening port and stops both owned
processes after saving evidence.  The expected-results artifact is the
candidate-independent oracle for the synthetic paths and SOP conflict.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from typing import Any

from verify_falkordb_live import (
    LiveVerifier,
    VerificationFailure,
    explicit_bind_preflight,
    host_facts,
    query_result,
    result_rows,
)
from wardatlas_app import WardAtlasService
from wardatlas_fixtures import restricted_fixture_queries


ROOT = Path(__file__).resolve().parents[1]
CANONICAL_COMMAND = (
    "python3 scripts/verify_hospital_workflow.py --launch-owned-app --loopback-only "
    "--app-port 18731 --require-live-falkordb --db-port 18732 --fail-on-zero "
    "--assert-api --assert-browser --assert-provenance --assert-permissions "
    "--assert-confirmation --browser-smoke-tool /tools/browser-smoke.py "
    "--desktop-mobile --verify-morning-status-and-feature-matrix "
    "--evidence-dir /workspace/evidence/wardatlas/ui"
)
RESTRICTED_MARKERS = [
    "SRC-RESTRICTED-92",
    "DEPT-RESTRICTED-COMPLIANCE",
    "ROLE-RESTRICTED-OWNER",
    "Restricted Continuity Annex 92",
    "Restricted-only instruction: contact the secure lift coordinator.",
    "EQUIP-RESTRICTED-92",
]


def api_request(base_url: str, path: str, method: str = "GET", payload: dict | None = None) -> tuple[int, dict | str]:
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        base_url + path,
        data=body,
        method=method,
        headers={"Content-Type": "application/json", "Accept": "application/json"},
    )
    try:
        response = urllib.request.urlopen(request, timeout=8)
        status = response.status
        raw = response.read()
    except urllib.error.HTTPError as exc:
        status = exc.code
        raw = exc.read()
    try:
        return status, json.loads(raw.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return status, raw.decode("utf-8", "replace")


def wait_for_owned_app(process: subprocess.Popen, log_path: Path, timeout: float = 15) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise VerificationFailure(f"owned WardAtlas app exited before readiness (code {process.returncode}); see app log")
        try:
            log = log_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            log = ""
        if "WARDATLAS_READY http://127.0.0.1:" in log:
            return
        time.sleep(0.05)
    raise VerificationFailure("owned WardAtlas app did not report its selected loopback bind")


def validate_docs() -> list[str]:
    status_path = ROOT / "MORNING-STATUS.md"
    matrix_path = ROOT / "FEATURE-MATRIX.md"
    if not status_path.is_file() or not matrix_path.is_file():
        raise VerificationFailure("MORNING-STATUS.md and FEATURE-MATRIX.md must both exist")
    status = status_path.read_text(encoding="utf-8")
    matrix = matrix_path.read_text(encoding="utf-8")
    required_status = [
        CANONICAL_COMMAND,
        "python3 scripts/verify_expected_results.py --expected tests/expected-results.json --matrix tests/adversarial-matrix.json --require-six-scenarios",
        "python3 scripts/verify_falkordb_live.py --require-live --fail-on-zero --fresh-owned-graph --db-port 18732 --empty-reseed-idempotence-reconnect --report /workspace/evidence/wardatlas/falkordb-scenarios.json --verify-morning-status",
        "/workspace/evidence/wardatlas/ui/wardatlas-workflow.json",
        "/workspace/evidence/wardatlas/ui/desktop.png",
        "/workspace/evidence/wardatlas/ui/mobile.png",
        "Redis 8.0.5",
        "FalkorDB 6.0.2",
        "27 assertions",
        "Pentium Silver N5030",
        "Deferred",
        "Next batch",
        "eligibility risk",
        "not a validated hospital system",
    ]
    missing = [entry for entry in required_status if entry.casefold() not in status.casefold()]
    required_matrix = ["Implemented", "Verified", "Planned", "not configured", "local-only"]
    missing.extend(f"FEATURE-MATRIX.md missing {entry}" for entry in required_matrix if entry.casefold() not in matrix.casefold())
    if missing:
        raise VerificationFailure(f"status or feature matrix omits required honest delivery details: {missing}")
    return [str(status_path), str(matrix_path)]


class HospitalWorkflowVerifier:
    def __init__(self, args: argparse.Namespace, expected: dict, matrix: dict):
        self.args = args
        self.expected = expected
        self.matrix = matrix
        self.run_id = uuid.uuid4().hex
        self.db: LiveVerifier | None = None
        self.app: subprocess.Popen | None = None
        self.app_log_path = args.evidence_dir / "wardatlas-app.log"
        self.browser_report_path = args.evidence_dir / "browser-assertions.json"
        self.workflow_report_path = args.evidence_dir / "wardatlas-workflow.json"
        self.state_dir = ROOT / ".local" / "wardatlas-ui-runs" / self.run_id
        self.actions_evidence_path = args.evidence_dir / "confirmed-actions.json"
        self.assertions: list[dict[str, Any]] = []
        self.failures: list[str] = []
        self.cleanup = {"owned_app_stopped": False, "owned_graph_deleted": False, "owned_database_process_stopped": False, "temporary_local_state_removed": False}
        self.browser_smoke: dict[str, Any] = {"status": "not-run"}
        self.database: dict[str, Any] = {}
        self.docs: list[str] = []
        self.app_url = f"http://127.0.0.1:{args.app_port}"

    @property
    def assertion_count(self) -> int:
        return len(self.assertions)

    def check(self, name: str, condition: bool, detail: Any = None) -> None:
        row = {"assertion": name, "passed": bool(condition)}
        if detail is not None:
            row["detail"] = detail
        self.assertions.append(row)
        if not condition:
            raise VerificationFailure(f"{name}; observed={detail!r}")

    def get(self, path: str) -> tuple[int, dict | str]:
        return api_request(self.app_url, path)

    def post(self, path: str, body: dict | None = None) -> tuple[int, dict | str]:
        return api_request(self.app_url, path, "POST", body or {})

    def start(self) -> None:
        # Preflight the UI port first, and the database verifier independently
        # preflights the selected DB port before it launches or connects.
        explicit_bind_preflight(self.args.app_port)
        self.db = LiveVerifier(self.args.db_port, self.expected, self.matrix)
        self.db.launch()
        # A graph query on this uniquely named, owned graph both establishes
        # the cold-empty precondition and makes cleanup ownership explicit.
        cold_rows = self.db.graph_rows("ui-cold-empty", "MATCH (n) RETURN n.id AS id LIMIT 1")
        self.db.graph_created = True
        self.check("fresh UI graph starts empty", cold_rows == [], cold_rows)

        # Exercise the app's deletion guard against a separate graph created
        # and owned only by this verifier. It must refuse to reset nonempty
        # content that this app process did not seed, then we delete only our
        # uniquely named guard graph.
        guard_graph = f"wardatlas_test_guard_{self.run_id}"
        guard_marker = f"WARDATLAS-UNOWNED-{self.run_id}"
        try:
            guard_reply = self.db.client.command(
                "GRAPH.QUERY", guard_graph,
                f"CREATE (:GuardMarker {{id: '{guard_marker}'}})",
            )
            query_result(guard_reply)
            guard_service = WardAtlasService(self.args.db_port, guard_graph, self.state_dir)
            refused = False
            try:
                guard_service.reset_scenario()
            except RuntimeError as exc:
                refused = "not seeded by this WardAtlas process" in str(exc)
            remaining_reply = self.db.client.command(
                "GRAPH.QUERY", guard_graph,
                f"MATCH (n:GuardMarker {{id: '{guard_marker}'}}) RETURN n.id AS id",
            )
            remaining = result_rows(query_result(remaining_reply))
            self.check("app reset refuses to delete a preexisting unowned graph", refused and remaining == [{"id": guard_marker}], {"refused": refused, "remaining_marker": remaining})
        finally:
            self.db.client.command("GRAPH.DELETE", guard_graph)

        self.args.evidence_dir.mkdir(parents=True, exist_ok=True)
        self.state_dir.parent.mkdir(parents=True, exist_ok=True)
        if self.state_dir.exists():
            raise VerificationFailure("unique verifier state directory already exists")
        command = [
            sys.executable,
            str(ROOT / "scripts/wardatlas_app.py"),
            "--app-port", str(self.args.app_port),
            "--db-port", str(self.args.db_port),
            "--graph-name", self.db.graph,
            "--state-dir", str(self.state_dir),
        ]
        with self.app_log_path.open("wb") as log:
            self.app = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
        wait_for_owned_app(self.app, self.app_log_path)
        self.check("owned app reports exact loopback listener", "WARDATLAS_READY http://127.0.0.1:" + str(self.args.app_port) in self.app_log_path.read_text(encoding="utf-8"))

    def api_suite(self) -> None:
        status, health = self.get("/api/health")
        self.check("health proves live FalkorDB 6.0.2 query path", status == 200 and health.get("database") == "FalkorDB" and health.get("module_version") == 60002, health)
        self.check("app reports no external integrations", health.get("external_integrations") == [], health)

        status, empty = self.get("/api/briefing?scope=operations")
        self.check("empty graph returns no impact path or SOP finding through API", status == 200 and empty.get("impact_paths") == [] and empty.get("sop_finding") is None, empty)

        status, reset = self.post("/api/scenario/reset")
        self.check("in-app reset reseeds deterministic scenario", status == 200 and reset.get("status") == "reset" and reset.get("scenario") == "INC-204 synthetic outage", reset)
        status, briefing = self.get("/api/briefing?scope=operations")
        self.check("briefing endpoint returns live seeded impact", status == 200 and len(briefing.get("impact_paths", [])) == 2, {"status": status, "paths": briefing.get("impact_paths")})
        multi = next(row["expected"] for row in self.expected["scenarios"] if row["id"] == "multi-hop-outage-current-ownership")
        expected_paths = self.expected["expected_paths"]
        actual_paths = [row["path"] for row in briefing.get("impact_paths", [])]
        self.check("API returns exact pinned two multi-hop outage paths", actual_paths == expected_paths == multi["impact_paths"], actual_paths)
        self.check("API returns only current synthetic accountable roles", [row["role_id"] for row in briefing["impact_paths"]] == multi["current_role_ids"] and all(row["role_is_current"] is True for row in briefing["impact_paths"]), [row["role_id"] for row in briefing["impact_paths"]])

        actual_evidence = [{
            "path": row["path"],
            "source_ids": [source["source_id"] for source in row["evidence"]],
            "source_titles": [source["title"] for source in row["evidence"]],
            "observed_at": [source["observed_at"] for source in row["evidence"]],
        } for row in briefing["impact_paths"]]
        self.check("API exposes exact source IDs, titles, and observed timestamps per path", actual_evidence == self.expected["expected_evidence_by_path"], actual_evidence)
        self.check("source facts, graph inference, and missing evidence are separate", bool(briefing.get("source_facts")) and bool(briefing.get("inferences")) and bool(briefing.get("missing_evidence")) and "no product model route configured" in briefing.get("answer_method", "").lower(), {key: briefing.get(key) for key in ("source_facts", "inferences", "missing_evidence", "answer_method")})
        self.check("incident and equipment facts retain graph-backed status", briefing.get("incident") == {"id": "INC-204", "status": "open", "equipment_name": "Material transfer conveyor C4"} and all(row["equipment_status"] == "unavailable" for row in briefing["impact_paths"]), briefing.get("incident"))

        expected_sop = next(row["expected"] for row in self.expected["scenarios"] if row["id"] == "stale-reference-current-sop-conflict")
        sop = briefing.get("sop_finding") or {}
        sop_versions = [sop.get("stale_version", {}).get("id"), *[row.get("id") for row in sop.get("current_versions", [])]]
        self.check("API reveals stale SOP reference and both current conflicting versions", sop_versions == [expected_sop["stale_version_id"], *expected_sop["current_conflicting_version_ids"]] and sop.get("stale_version", {}).get("referenced_equipment_id") == expected_sop["stale_referenced_equipment_id"], sop)
        self.check("API leaves the current SOP disagreement unresolved without a winner", [row.get("escalation_role_claim") for row in sop.get("current_versions", [])] == expected_sop["current_conflicting_role_claims"] and sop.get("conflict_status") == "unresolved" and sop.get("authoritative_claim") is None, sop)
        actual_sop_evidence = [{"source_id": row["source_id"], "title": row["title"], "observed_at": row["observed_at"]} for row in sop.get("evidence", [])]
        expected_sop_evidence = [
            {"source_id": expected_sop["stale_source_id"], "title": expected_sop["stale_source_title"], "observed_at": expected_sop["stale_observed_at"]},
            *[{"source_id": source_id, "title": title, "observed_at": observed} for source_id, title, observed in zip(expected_sop["current_source_ids"], expected_sop["current_source_titles"], expected_sop["current_observed_at"])],
        ]
        self.check("stale/current SOP provenance and freshness match the independent oracle", actual_sop_evidence == expected_sop_evidence, actual_sop_evidence)

        status, graph_before = self.get("/api/graph?scope=operations")
        self.check("visual graph API returns path relationships and unresolved conflict edge", status == 200 and graph_before.get("impact_path_count") == 2 and {edge.get("kind") for edge in graph_before.get("edges", [])} >= {"AFFECTS", "SUPPORTS", "SERVES", "OWNED_BY", "SOP_CONFLICT"}, graph_before)
        self.check("visual graph marks explicitly current roles and does not include stale role", all(node.get("current") is True for node in graph_before.get("nodes", []) if node.get("kind") == "Role") and "ROLE-OLD-STERILE-ROTATION" not in json.dumps(graph_before), graph_before.get("nodes"))

        before_serialized = json.dumps({"briefing": briefing, "graph": graph_before}, sort_keys=True)
        hidden_query_count = 0
        for query in restricted_fixture_queries():
            self.db.graph_query(f"ui-denied-source-{hidden_query_count + 1}", query)
            hidden_query_count += 1
        status_after, after = self.get("/api/briefing?scope=operations")
        graph_status_after, graph_after = self.get("/api/graph?scope=operations")
        after_serialized = json.dumps({"briefing": after, "graph": graph_after}, sort_keys=True)
        self.check("adding restricted graph branch leaves visible API and visual graph byte-equivalent", status_after == 200 and graph_status_after == 200 and before_serialized == after_serialized, {"briefing_before": briefing, "briefing_after": after, "graph_before": graph_before, "graph_after": graph_after})
        self.check("no restricted source, fact, identifier, or derived owner appears in response", all(marker not in after_serialized for marker in RESTRICTED_MARKERS), {"markers_absent": [marker for marker in RESTRICTED_MARKERS if marker not in after_serialized]})
        self.check("restricted graph facts do not change visible paths or expose count/existence fields", len(after.get("impact_paths", [])) == 2 and not any("restricted" in key.lower() or "hidden_count" in key.lower() for key in after), after)
        denied_status, denied = self.get("/api/briefing?scope=restricted")
        self.check("unauthorized scope receives generic denial without source disclosure", denied_status == 403 and denied == {"error": "This view is not available."} and all(marker not in json.dumps(denied) for marker in RESTRICTED_MARKERS), {"status": denied_status, "body": denied})

        # Reset only this verifier-owned graph after its hidden adversarial
        # branch. The user-visible reset route must return the pinned graph.
        status, _ = self.post("/api/scenario/reset")
        reset_status, reset_briefing = self.get("/api/briefing?scope=operations")
        self.check("reset removes adversarial branch and restores exact visible scenario", status == 200 and reset_status == 200 and [row["path"] for row in reset_briefing.get("impact_paths", [])] == expected_paths, reset_briefing.get("impact_paths"))

        actions_status, actions = self.get("/api/actions")
        self.check("no confirmed handover exists before draft", actions_status == 200 and actions.get("actions") == [] and actions.get("count") == 0 and not self.state_dir.exists(), actions)
        draft_status, draft = self.post("/api/handover/draft", {"incident_id": "INC-204"})
        self.check("API prepares a source-linked in-memory preview", draft_status == 201 and bool(draft.get("confirmation_token")) and {"SRC-ASSET-C4", "SRC-SOP-CURRENT-V2", "SRC-SOP-CURRENT-V3"} <= set(draft.get("source_ids", [])), draft)
        after_draft_status, after_draft = self.get("/api/actions")
        self.check("unconfirmed preview is absent from persisted action list and disk", after_draft_status == 200 and after_draft.get("count") == 0 and not self.state_dir.exists(), after_draft)
        rejected_status, rejected = self.post("/api/handover/confirm", {"token": draft["confirmation_token"], "confirm": False})
        rejected_list_status, rejected_list = self.get("/api/actions")
        self.check("false confirmation cannot persist an action", rejected_status == 400 and rejected.get("error") == "Explicit in-app confirmation is required; nothing was saved." and rejected_list_status == 200 and rejected_list.get("count") == 0 and not self.state_dir.exists(), {"status": rejected_status, "error": rejected, "actions": rejected_list})

    def browser_suite(self) -> None:
        node = shutil.which("node")
        self.check("local Node runtime is available for browser assertions", node is not None, node)
        command = [node, str(ROOT / "scripts/wardatlas_browser_assertions.cjs"), self.app_url, str(self.browser_report_path)]
        result = subprocess.run(command, cwd=ROOT, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=90)
        self.check("browser interaction assertions pass", result.returncode == 0, result.stdout[-6000:])
        browser_report = json.loads(self.browser_report_path.read_text(encoding="utf-8"))
        self.check("browser report contains nonzero passing desktop/mobile assertions", browser_report.get("status") == "PASS" and browser_report.get("assertion_count", 0) >= 10 and len(browser_report.get("checks", [])) == browser_report.get("assertion_count"), browser_report)
        self.check("browser requested only the local product origin", browser_report.get("requested_hosts") == [f"127.0.0.1:{self.args.app_port}"], browser_report.get("requested_hosts"))
        state = self.state_dir / "confirmed-actions.json"
        self.check("confirmed handover is persisted only after in-app confirmation", state.is_file(), str(state))
        persisted = json.loads(state.read_text(encoding="utf-8")) if state.is_file() else {}
        self.check("confirmed local action cites the fixture and has no external side effect", persisted.get("schema_version") == 1 and len(persisted.get("items", [])) == 1 and persisted["items"][0].get("storage") == "local-only" and {"SRC-ASSET-C4", "SRC-SOP-CURRENT-V2", "SRC-SOP-CURRENT-V3"} <= set(persisted["items"][0].get("source_ids", [])), persisted)
        self.check("confirmed action contains no restricted source material", all(marker not in json.dumps(persisted) for marker in RESTRICTED_MARKERS), persisted)
        self.actions_evidence_path.write_text(json.dumps(persisted, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    def run_browser_smoke(self) -> None:
        tool = self.args.browser_smoke_tool
        if not tool.is_file():
            raise VerificationFailure(f"supplied browser smoke tool is unavailable: {tool}")
        command = [sys.executable, str(tool), self.app_url, "--output", str(self.args.evidence_dir)]
        result = subprocess.run(command, cwd=ROOT, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=120)
        smoke_path = self.args.evidence_dir / "browser.json"
        smoke = json.loads(smoke_path.read_text(encoding="utf-8")) if smoke_path.is_file() else {}
        self.browser_smoke = {"command": command, "returncode": result.returncode, "stdout": result.stdout[-6000:], "report": smoke}
        self.check("supplied browser-smoke.py passes and saves its report", result.returncode == 0 and smoke.get("status") == 200 and smoke.get("errors") == [], self.browser_smoke)
        required_files = ["desktop.png", "mobile.png", "trace.zip", "browser.json"]
        missing = [name for name in required_files if not (self.args.evidence_dir / name).is_file() or (self.args.evidence_dir / name).stat().st_size == 0]
        self.check("desktop/mobile screenshots and Playwright trace are saved under evidence", not missing, {"missing": missing, "files": [str(self.args.evidence_dir / name) for name in required_files]})

    def run(self) -> None:
        self.start()
        self.api_suite()
        self.browser_suite()
        self.run_browser_smoke()
        if self.args.verify_morning_status_and_feature_matrix:
            self.docs = validate_docs()
            self.check("morning status and implemented/verified/planned matrix are current", len(self.docs) == 2, self.docs)
        self.check("workflow verifier has nonzero assertions", self.assertion_count > 0, self.assertion_count)

    def stop_app(self) -> None:
        if self.app is not None:
            if self.app.poll() is None:
                self.app.terminate()
                try:
                    self.app.wait(timeout=6)
                except subprocess.TimeoutExpired:
                    self.app.kill()
                    self.app.wait(timeout=6)
            self.cleanup["owned_app_stopped"] = self.app.poll() is not None
        if self.db is not None:
            self.db.stop()
            self.cleanup["owned_graph_deleted"] = self.db.cleanup.get("owned_graph_deleted") is True
            self.cleanup["owned_database_process_stopped"] = self.db.cleanup.get("owned_process_stopped") is True
        if self.state_dir.exists():
            resolved_parent = self.state_dir.parent.resolve()
            expected_parent = (ROOT / ".local/wardatlas-ui-runs").resolve()
            if resolved_parent == expected_parent and self.state_dir.resolve().is_relative_to(expected_parent):
                shutil.rmtree(self.state_dir)
                self.cleanup["temporary_local_state_removed"] = True
        else:
            self.cleanup["temporary_local_state_removed"] = True

    def report(self, status: str, error: str | None) -> dict:
        db_report = self.db.report("PASS" if self.db and not self.db.failures else "RUN", error=None) if self.db else None
        if db_report is not None:
            db_report["graph_query_count_scope"] = (
                "Counts direct LiveVerifier graph_query calls only; separate app-process queries are exercised through the HTTP API and are not included."
            )
        return {
            "schema_version": 1,
            "verifier_version": "1.0",
            "status": status,
            "error": error,
            "run_id": self.run_id,
            "created_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "host": host_facts(),
            "app": {"url": self.app_url, "bind": f"127.0.0.1:{self.args.app_port}", "log": str(self.app_log_path), "process_id": self.app.pid if self.app else None},
            "database": db_report,
            "assertion_count": self.assertion_count,
            "assertions": self.assertions,
            "browser_smoke": self.browser_smoke,
            "browser_assertions_report": str(self.browser_report_path),
            "desktop_screenshot": str(self.args.evidence_dir / "desktop.png"),
            "mobile_screenshot": str(self.args.evidence_dir / "mobile.png"),
            "playwright_trace": str(self.args.evidence_dir / "trace.zip"),
            "confirmed_action_evidence": str(self.actions_evidence_path) if self.actions_evidence_path.is_file() else None,
            "docs_verified": self.docs,
            "cleanup": self.cleanup,
            "failures": self.failures,
            "side_effect_policy": {"allowed": ["FalkorDB query on unique owned graph", "local confirmed-actions JSON after explicit confirm"], "external_writes": [], "message_sent": False, "call_made": False, "ticket_created": False},
        }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--launch-owned-app", action="store_true", required=True)
    parser.add_argument("--loopback-only", action="store_true", required=True)
    parser.add_argument("--app-port", type=int, required=True)
    parser.add_argument("--require-live-falkordb", action="store_true", required=True)
    parser.add_argument("--db-port", type=int, required=True)
    parser.add_argument("--fail-on-zero", action="store_true", required=True)
    parser.add_argument("--assert-api", action="store_true", required=True)
    parser.add_argument("--assert-browser", action="store_true", required=True)
    parser.add_argument("--assert-provenance", action="store_true", required=True)
    parser.add_argument("--assert-permissions", action="store_true", required=True)
    parser.add_argument("--assert-confirmation", action="store_true", required=True)
    parser.add_argument("--browser-smoke-tool", type=Path, required=True)
    parser.add_argument("--desktop-mobile", action="store_true", required=True)
    parser.add_argument("--verify-morning-status-and-feature-matrix", action="store_true", required=True)
    parser.add_argument("--evidence-dir", type=Path, required=True)
    parser.add_argument("--expected", type=Path, default=ROOT / "tests/expected-results.json")
    parser.add_argument("--matrix", type=Path, default=ROOT / "tests/adversarial-matrix.json")
    args = parser.parse_args()
    if not (1024 <= args.app_port <= 65535 and 1024 <= args.db_port <= 65535):
        parser.error("both ports must be explicitly selected high ports")
    evidence = args.evidence_dir.resolve()
    if not evidence.is_relative_to(Path("/workspace/evidence")):
        parser.error("evidence directory must stay under /workspace/evidence")
    args.evidence_dir = evidence

    verifier: HospitalWorkflowVerifier | None = None
    error: str | None = None
    status = "FAIL"
    try:
        expected = json.loads(args.expected.read_text(encoding="utf-8"))
        matrix = json.loads(args.matrix.read_text(encoding="utf-8"))
        if expected.get("product") != "WardAtlas" or len(expected.get("scenarios", [])) != 8 or len(matrix.get("cases", [])) != 8:
            raise VerificationFailure("the pinned eight-scenario WardAtlas oracle and matrix are required")
        if args.assert_provenance is not True or args.assert_permissions is not True or args.assert_confirmation is not True:
            raise VerificationFailure("original provenance, permissions, and confirmation assertions are mandatory")
        args.evidence_dir.mkdir(parents=True, exist_ok=True)
        verifier = HospitalWorkflowVerifier(args, expected, matrix)
        verifier.run()
        if args.fail_on_zero and verifier.assertion_count <= 0:
            raise VerificationFailure("--fail-on-zero: workflow completed with zero assertions")
        status = "PASS"
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
        if verifier:
            verifier.failures.append(error)
    finally:
        if verifier:
            verifier.stop_app()
            if status == "PASS" and not all(verifier.cleanup.values()):
                error = f"VerificationFailure: owned cleanup incomplete: {verifier.cleanup}"
                verifier.failures.append(error)
                status = "FAIL"
            report = verifier.report(status, error)
            verifier.workflow_report_path.parent.mkdir(parents=True, exist_ok=True)
            verifier.workflow_report_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        else:
            args.evidence_dir.mkdir(parents=True, exist_ok=True)
            report = {"schema_version": 1, "verifier_version": "1.0", "status": status, "error": error, "host": host_facts(), "assertion_count": 0, "failures": [error] if error else []}
            (args.evidence_dir / "wardatlas-workflow.json").write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    if status != "PASS":
        print(f"FAIL: {error}; report: {args.evidence_dir / 'wardatlas-workflow.json'}", file=sys.stderr)
        return 1
    print(f"PASS: WardAtlas local workflow; {verifier.assertion_count} assertions; API/browser/provenance/permission/confirmation passed; owned app, graph and DB stopped; evidence: {verifier.workflow_report_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

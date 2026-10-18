#!/usr/bin/env python3
"""Verify all ten frozen knowledge-diff groups on owned, real FalkorDB.

Only checked-in contracts supply expectations. In particular, this command
never follows planner paths mentioned in older contracts, nor uses endpoint,
fixture, browser, or query output as an oracle. The accepted hypothetical V3
snapshot is normalized to retain the actual observation and separately label
its assumed observation; no other source is normalized.

Run: python3 scripts/verify_knowledge_diff.py --all --require-live-falkordb
     --desktop-mobile --fail-on-zero
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import re
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Any
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from verify_falkordb_live import LiveVerifier, RespConnection, VerificationFailure, query_result, result_rows
from wardatlas_app import Handler, WardAtlasHTTPServer, WardAtlasService
from wardatlas_decisions import decision_seed_queries, disconnected_decoy_queries, restricted_decision_queries
from wardatlas_fixtures import restricted_fixture_queries, seed_queries
from wardatlas_shift_board import seed_shift_board_queries
from wardatlas_source_branch import (
    disconnected_decoy_branch_queries,
    restricted_source_branch_queries,
    source_branch_seed_queries,
)


ROOT = Path(__file__).resolve().parents[1]
EXPECTED_PATH = ROOT / "contracts/knowledge-diff/expected-results.json"
MATRIX_PATH = ROOT / "contracts/knowledge-diff/adversarial-matrix.json"
EXPECTED_SHA256 = "295b80664f5eb9ad7743f6d7277c5d846e165928bb41deda08a3762f68ad5a2c"
MATRIX_SHA256 = "277909ed21537b2671914d146532efaebb33dcb74efb2aeff4f616fed11a14f7"
REQUIRED_ASSERTIONS = {
    "identical": ["equal.zero", "equal.nine-unchanged", "equal.projections", "equal.types", "equal.no-completeness"],
    "mainline-boundary": ["boundary.four-only-right", "boundary.five-unchanged", "boundary.claims-conflict-preserved", "boundary.record-own-source", "boundary.considers", "boundary.no-negative-conclusion"],
    "modes-at-1605": ["modes.four-only-left", "modes.five-unchanged", "modes.v2-record-preserved", "modes.no-v3-right", "modes.partial-not-unavailable", "modes.reversal"],
    "branch-boundary": ["branch.four-only-right", "branch.five-unchanged", "branch.record-preserved", "branch.v3-conflict-link", "branch.partial-labeled"],
    "actual-assumed": ["time.one-observation-change", "time.actual-1604", "time.assumed-1606", "time.v2-unmodified", "time.citations-exact", "time.stored-fact-unchanged"],
    "missing-edges": ["missing.branch-edge-unavailable", "missing.source-edge-unavailable", "missing.no-fallback", "missing.no-mass-removal", "missing.reversal", "missing.reseed-restores", "missing.browser-unknown"],
    "cold": ["cold.unavailable", "cold.no-delta", "cold.no-identities", "cold.browser-unknown", "cold.no-absence-claim"],
    "privacy": ["acl.decoy-response", "acl.restricted-response", "acl.desktop-dom", "acl.mobile-dom", "acl.accessibility", "acl.no-tripwire"],
    "repeat-readonly": ["repeat.idempotent", "repeat.reconnect", "repeat.graph-readonly", "repeat.actions-readonly", "repeat.desktop-mobile", "repeat.keyboard", "repeat.stale-request", "repeat.owned-cleanup"],
    "validation": ["valid.utc", "valid.invalid-time", "valid.naive-time", "valid.missing", "valid.duplicate-time", "valid.invalid-mode", "valid.duplicate-mode", "valid.scope", "valid.readonly"],
}
T04, T05, T06 = (f"2026-10-14T16:0{minute}:00Z" for minute in (4, 5, 6))
V2, V3 = "SOP-OPS-9-V2", "SOP-OPS-9-V3"
BRANCH_ID, OVERRIDE_ID, SOURCE_ID = "BR-SOP-V3-LATE-OBS", "OBS-SOP-V3-LATE-OBS", "SRC-SOP-CURRENT-V3"
TRIPWIRES = (
    "BR-RESTRICTED", "OBS-RESTRICTED", "SRC-RESTRICTED", "ROLE-RESTRICTED",
    "DEC-RESTRICTED", "Restricted Synthetic", "Restricted Decision", "canary 77",
    "north annex diversion", "BR-SOP-DECOY-91", "OBS-SOP-DECOY-91", "SRC-SOP-DECOY-91",
)


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def same(left: Any, right: Any) -> bool:
    # JSON dictionaries are unordered, but arrays in the accepted snapshots are
    # ordered. Do not sort away duplicate or unexpected witnesses/claim paths.
    return left == right


def require(condition: bool, message: str) -> None:
    if not condition:
        raise VerificationFailure(message)


def load_contracts() -> tuple[dict, dict, dict, dict]:
    require(digest(EXPECTED_PATH) == EXPECTED_SHA256, "knowledge-diff expected-results bytes differ from frozen aab0409/01796d2")
    require(digest(MATRIX_PATH) == MATRIX_SHA256, "knowledge-diff matrix bytes differ from frozen aab0409/01796d2")
    expected = json.loads(EXPECTED_PATH.read_text(encoding="utf-8"))
    matrix = json.loads(MATRIX_PATH.read_text(encoding="utf-8"))
    require(expected.get("candidate_output_is_oracle") is False and matrix.get("candidate_output_is_oracle") is False, "candidate output must never be the oracle")
    require([row["id"] for row in expected["groups"]] == list(REQUIRED_ASSERTIONS), "oracle group coverage differs from the ten frozen groups")
    require([row["group_id"] for row in matrix["groups"]] == list(REQUIRED_ASSERTIONS), "matrix group coverage differs from the ten frozen groups")
    require(all(row.get("assertions") == REQUIRED_ASSERTIONS[row["group_id"]] for row in matrix["groups"]), "matrix assertion IDs differ from the exact frozen mapping")
    require(sum(len(row["assertions"]) for row in matrix["groups"]) == 63, "expected exactly 63 nonzero mapped assertions")
    # Deliberately only these two direct, repository-local oracle references.
    # Older oracle metadata includes external planner paths: never open them.
    sources = {}
    allowed = {"contracts/decision-knowledge-timeline/expected-results.json", "contracts/decision-source-branch/expected-results.json"}
    require({row["path"] for row in expected["pinned_sources"]} == allowed, "unexpected source oracle reference")
    for row in expected["pinned_sources"]:
        path = (ROOT / row["path"]).resolve()
        require(path.is_relative_to(ROOT) and digest(path) == row["sha256"], f"source oracle hash mismatch: {row['path']}")
        sources[row["path"]] = json.loads(path.read_text(encoding="utf-8"))
    return expected, matrix, sources["contracts/decision-knowledge-timeline/expected-results.json"], sources["contracts/decision-source-branch/expected-results.json"]


class SourceOracle:
    def __init__(self, timeline: dict, branch: dict):
        self.timeline = {row["id"]: row["expected"] for row in timeline["cases"]}
        self.branch = {row["id"]: row["expected"] for row in branch["groups"]}

    def projection(self, mode: str, known_at: str, *, unavailable: bool = False) -> dict:
        if unavailable:
            source = self.branch["cold-empty-branch-lookup"]["projection"] if mode == "hypothetical" else self.timeline["cold-empty-unknown"]["projection"]
            value = copy.deepcopy(source)
            value["known_at"] = known_at
            if mode == "hypothetical":
                value["claim_context_state"] = "incomplete/unknown"
            return value
        if mode == "mainline":
            if known_at == T04:
                source = self.timeline["v3-and-graph-derived-conflict"]["projection"]
            else:
                source = self.branch["mainline-isolation"][f"mainline_at_16_0{5 if known_at == T05 else 6}"]
        else:
            source = self.branch["branch-at-16-05-suppression" if known_at == T05 else "branch-at-16-06-restoration"]["projection"]
        value = copy.deepcopy(source)
        require(value["known_at"] == known_at, "no literal source snapshot for requested cutoff")
        if mode == "hypothetical" and known_at == T06:
            # The accepted branch oracle represented the projected clock as
            # observed_at. The diff contract explicitly separates fact/assumption.
            source = next(row["source"] for row in value["claims"] if row["version_id"] == V3)
            require(source["observed_at"] == T06, "accepted branch snapshot changed")
            source["observed_at"] = T04
            source["assumed_observed_at"] = T06
            source["observation_mode"] = "hypothetical"
        return value

    def scenario(self, known_at: str) -> dict:
        return copy.deepcopy(self.branch["branch-at-16-05-suppression" if known_at == T05 else "branch-at-16-06-restoration"]["branch_assumption"])


def witness_keys(projection: dict) -> set[str]:
    """Independent inventory of the five public witness kinds in source pins."""
    keys = set()
    for row in projection["claims"]:
        keys.add(f"claim:{row['version_id']}:{row['role_id']}")
        keys.add(f"source_observation:{row['source']['id']}")
    if projection["conflict"]:
        edge = projection["conflict"]["edge"]
        keys.add(f"conflict_relationship:{edge['from']}:{edge['relationship']}:{edge['to']}")
    if projection["decision_record"]:
        record = projection["decision_record"]
        keys.add(f"decision_record:{record['id']}")
        keys.add(f"source_observation:{record['source']['id']}")
    for edge in projection["decision_considers_edges"]:
        keys.add(f"considered_link:{edge['from']}:{edge['relationship']}:{edge['to']}")
    return keys


def oracle_witnesses(projection: dict) -> dict[str, list[dict]]:
    """Literal oracle components, with optional public relationship citations.

    The witness envelope is new; its facts must still be exactly the accepted
    claim, source, relationship or record. A relationship may additionally
    carry its independently pinned source citations, never candidate citations.
    """
    witnesses = {}
    claims = {row["version_id"]: row for row in projection["claims"]}
    for row in claims.values():
        witnesses[f"claim:{row['version_id']}:{row['role_id']}"] = [row]
        witnesses[f"source_observation:{row['source']['id']}"] = [row["source"]]
    if projection["conflict"]:
        conflict = projection["conflict"]
        edge = conflict["edge"]
        citations = [claims[edge["from"]]["source"], claims[edge["to"]]["source"]]
        witnesses[f"conflict_relationship:{edge['from']}:{edge['relationship']}:{edge['to']}"] = [conflict, {**conflict, "citations": citations}]
    record = projection["decision_record"]
    if record:
        witnesses[f"decision_record:{record['id']}"] = [record]
        witnesses[f"source_observation:{record['source']['id']}"] = [record["source"]]
        for edge in projection["decision_considers_edges"]:
            citations = [record["source"], claims[edge["to"]]["source"]]
            witnesses[f"considered_link:{edge['from']}:{edge['relationship']}:{edge['to']}"] = [edge, {**edge, "citations": citations}]
    return witnesses


def request(base_url: str, path: str, method: str = "GET") -> tuple[int, Any]:
    req = Request(base_url + path, method=method, headers={"Accept": "application/json"})
    try:
        with urlopen(req, timeout=25) as response:
            status, raw = response.status, response.read()
    except HTTPError as exc:
        status, raw = exc.code, exc.read()
    try:
        return status, json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return status, raw.decode("utf-8", "replace")


def reserve_ports() -> tuple[list[socket.socket], int, int]:
    """Bind two free allocated ports, or two OS-assigned ephemeral ports.

    Never connect to a pre-existing service or choose a fixed default/offset.
    Reservations remain held until immediately before each owned launch.
    """
    reservations: list[socket.socket] = []
    base_raw, span_raw = os.environ.get("WORKSHOP_PORT_BASE"), os.environ.get("WORKSHOP_PORT_SPAN")
    try:
        if base_raw is None and span_raw is None:
            candidates = (0, 0)
        else:
            require(base_raw is not None and span_raw is not None, "set both WORKSHOP_PORT_BASE and WORKSHOP_PORT_SPAN")
            base, span = int(base_raw), int(span_raw)
            require(base >= 1024 and span >= 2 and base + span <= 65536, "invalid allocated port range")
            candidates = range(base, base + span)
        for port in candidates:
            listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            try:
                listener.bind(("127.0.0.1", port))
            except OSError:
                listener.close()
                continue
            reservations.append(listener)
            if len(reservations) == 2:
                return reservations, reservations[0].getsockname()[1], reservations[1].getsockname()[1]
        raise VerificationFailure("allocated range has fewer than two free loopback ports")
    except BaseException:
        for listener in reservations:
            listener.close()
        raise


class KnowledgeDiffLive:
    def __init__(self, args: argparse.Namespace, expected: dict, matrix: dict, oracle: SourceOracle, reservations: list[socket.socket]):
        self.args, self.expected, self.matrix, self.oracle = args, expected, matrix, oracle
        self.cases = {row["id"]: row for row in expected["groups"]}
        lane = re.sub(r"[^A-Za-z0-9_]", "_", os.environ.get("WORKSHOP_LANE", "ephemeral"))[:40]
        self.run_id = f"lane{lane}_{uuid.uuid4().hex}"
        self.db = LiveVerifier(args.db_port, expected, matrix)
        self.db.run_id = self.run_id
        self.db.graph = f"wardatlas_test_knowledge_diff_{self.run_id}"
        self.reservations = reservations
        self.state_dir = ROOT / ".local/knowledge-diff-state" / self.run_id
        self.state_owned = False
        self.base_url = f"http://127.0.0.1:{args.app_port}"
        self.service: WardAtlasService | None = None
        self.httpd: WardAtlasHTTPServer | None = None
        self.http_thread: threading.Thread | None = None
        self.browser_processes: list[subprocess.Popen] = []
        self.browser_groups_stopped: set[int] = set()
        self.coverage = {group: set() for group in REQUIRED_ASSERTIONS}
        self.assertions: list[dict] = []
        self.scenarios: list[dict] = []
        self.observed: dict[str, Any] = {}
        self.requests: list[dict] = []
        self.cleanup = {key: False for key in ("owned_graph_deleted", "owned_database_process_stopped", "owned_database_runtime_removed", "owned_app_stopped", "owned_state_removed", "owned_browser_processes_stopped", "port_reservations_closed")}

    def check(self, group: str, assertion: str, passed: bool, detail: Any) -> None:
        require(assertion in REQUIRED_ASSERTIONS[group], f"unmapped assertion {group}/{assertion}")
        require(assertion not in self.coverage[group], f"duplicate assertion {group}/{assertion}")
        self.coverage[group].add(assertion)
        self.assertions.append({"group_id": group, "assertion_id": assertion, "passed": bool(passed), "detail": detail})
        require(bool(passed), f"{group}/{assertion} failed: {str(detail)[:3000]}")

    def scenario(self, group: str, callback) -> None:
        record = {"id": group, "status": "running"}
        self.scenarios.append(record)
        started = time.monotonic()
        try:
            callback()
            missing = set(REQUIRED_ASSERTIONS[group]) - self.coverage[group]
            if group == "repeat-readonly":
                missing.discard("repeat.owned-cleanup")
            require(not missing, f"unexecuted mapped assertions in {group}: {sorted(missing)}")
            record["status"] = "pass-pending-owned-cleanup" if group == "repeat-readonly" else "pass"
        except BaseException as exc:
            record.update(status="fail", error=f"{type(exc).__name__}: {exc}")
            raise
        finally:
            record["assertion_count"] = len(self.coverage[group])
            record["elapsed_ms"] = round((time.monotonic() - started) * 1000, 3)

    def raw(self, pairs: dict | list[tuple[str, str]], method: str = "GET") -> tuple[int, Any]:
        path = "/api/knowledge-diff?" + urlencode(pairs)
        status, body = request(self.base_url, path, method)
        self.requests.append({"method": method, "path": path, "status": status, "response": body})
        return status, body

    @staticmethod
    def params(left_at: str = T05, right_at: str = T05, left_mode: str = "mainline", right_mode: str = "hypothetical") -> dict:
        return {"scope": "operations", "left_known_at": left_at, "right_known_at": right_at, "left_mode": left_mode, "right_mode": right_mode}

    def compare(self, group: str | None = None, *, params: dict | None = None, unavailable: set[str] | None = None) -> dict:
        values = params or {"scope": "operations", **self.cases[group]["input"]}
        status, body = self.raw(values)
        require(status == 200 and isinstance(body, dict), f"knowledge-diff failed: {status} {body}")
        self.validate_response(body, values, unavailable or set())
        return body

    def validate_response(self, body: dict, params: dict, unavailable: set[str]) -> None:
        require(set(body) == {"scope", "left", "right", "delta", "read_only"}, "unexpected full-response fields")
        require(body["scope"] == "operations" and body["read_only"] is True, "wrong scope or read-only boundary")
        inventories, pinned_witnesses = {}, {}
        for name in ("left", "right"):
            side = body[name]
            mode, known_at = params[f"{name}_mode"], params[f"{name}_known_at"]
            projection = self.oracle.projection(mode, known_at, unavailable=name in unavailable)
            require(isinstance(side, dict), f"invalid {name} side")
            required = {"known_at", "mode", "availability", "projection"}
            if mode == "hypothetical" and name not in unavailable:
                required.add("scenario")
            require(set(side) == required, f"wrong {name} fields/scenario presence: {list(side)}")
            require(side["known_at"] == known_at and side["mode"] == mode, f"{name} selector not independently honored")
            require(same(side["projection"], projection), f"{name} full projection differs from literal source oracle: {side['projection']!r}")
            availability = "comparison-unavailable" if name in unavailable else ("partial" if projection["claim_context_state"] != "evidence-backed" else "available")
            require(side["availability"] == availability, f"{name} availability is not {availability}")
            if "scenario" in required:
                require(side["scenario"] == self.oracle.scenario(known_at), f"{name} scenario differs from accepted source oracle")
            inventories[name] = witness_keys(projection)
            pinned_witnesses[name] = oracle_witnesses(projection)
            require(len(inventories[name]) <= self.expected["policy"]["max_witnesses_per_side"], "unbounded side inventory")
        delta = body["delta"]
        require(isinstance(delta, dict) and set(delta) == {"state", "complete", "items", "substantive_delta_count", "unchanged_count", "no_negative_conclusion"}, "invalid delta schema")
        require(delta["complete"] is False and delta["no_negative_conclusion"] is True, "delta claims completeness or a negative conclusion")
        require(type(delta["unchanged_count"]) is int and delta["unchanged_count"] >= 0, "invalid unchanged count")
        require(isinstance(delta["items"], list), "delta items must be an array")
        keys = []
        for item in delta["items"]:
            require(isinstance(item, dict) and set(item) == {"kind", "key", "status", "left", "right"}, "invalid witness item schema")
            require(isinstance(item["key"], str) and item["kind"] in self.expected["policy"]["kinds"] and item["key"].startswith(item["kind"] + ":"), "invalid witness kind/key")
            require(item["status"] in self.expected["policy"]["statuses"], "invalid witness status")
            keys.append(item["key"])
            for side in ("left", "right"):
                present = item["key"] in inventories[side]
                require((isinstance(item[side], dict) and bool(item[side])) if present else item[side] is None, f"{item['key']} {side} witness/null disagrees with source inventory")
                if present:
                    require(item[side] in pinned_witnesses[side][item["key"]], f"{item['key']} {side} witness facts/citations differ from the literal source oracle")
            if item["status"] == "unchanged":
                require(item["left"] is not None and item["right"] is not None, "unchanged item needs both witnesses")
            elif item["status"] == "changed":
                require(item["left"] is not None and item["right"] is not None and item["left"] != item["right"], "changed item needs distinct witnesses")
            elif item["status"].startswith("projected-only-on-"):
                side = item["status"].removeprefix("projected-only-on-")
                require(item[side] is not None and item["right" if side == "left" else "left"] is None, "one-sided status/witness disagreement")
        require(len(keys) == len(set(keys)) and set(keys) == inventories["left"] | inventories["right"], "item key inventory is not exactly the source witness union")
        require(delta["unchanged_count"] == sum(row["status"] == "unchanged" for row in delta["items"]), "unchanged count disagrees with executed inventory")
        if unavailable:
            require(delta["state"] == "comparison-unavailable" and delta["substantive_delta_count"] is None and all(row["status"] == "incomparable" for row in delta["items"]), "unavailable comparison falsely reports a numeric/one-sided delta")
        else:
            state = "partial-witness-comparison" if any(body[side]["availability"] == "partial" for side in ("left", "right")) else "witness-comparison"
            require(delta["state"] == state, "delta state does not reflect both side availabilities")
            require(type(delta["substantive_delta_count"]) is int and delta["substantive_delta_count"] == sum(row["status"] not in {"unchanged", "incomparable"} for row in delta["items"]), "invalid substantive count")
            require(not any(row["status"] == "incomparable" for row in delta["items"]), "available witnesses mislabeled incomparable")

    @staticmethod
    def status_keys(body: dict, status: str) -> list[str]:
        return sorted(row["key"] for row in body["delta"]["items"] if row["status"] == status)

    def expected_delta(self, group: str, body: dict) -> bool:
        expected = self.cases[group]["expected"]
        delta = body["delta"]
        return (
            all(delta[key] == expected[key] for key in ("substantive_delta_count", "unchanged_count", "state") if key in expected)
            and self.status_keys(body, "projected-only-on-left") == sorted(expected.get("only_left", []))
            and self.status_keys(body, "projected-only-on-right") == sorted(expected.get("only_right", []))
            and self.status_keys(body, "changed") == sorted(expected.get("changed", []))
        )

    def graph_snapshot(self, label: str) -> dict:
        return {
            "nodes": self.db.graph_rows(label + "-nodes", "MATCH (n) RETURN n.id AS id, labels(n) AS labels, properties(n) AS props ORDER BY id"),
            "edges": self.db.graph_rows(label + "-edges", "MATCH (a)-[r]->(b) RETURN a.id AS from_id, type(r) AS kind, r.id AS id, properties(r) AS props, b.id AS to_id ORDER BY id, from_id, to_id, kind"),
        }

    def state_snapshot(self) -> dict:
        files = {str(path.relative_to(self.state_dir)): path.read_bytes().hex() for path in self.state_dir.rglob("*") if path.is_file()}
        require(self.service is not None, "app service unavailable")
        return {"files": files, "pending": copy.deepcopy(self.service.pending), "pending_watches": copy.deepcopy(self.service.pending_watches), "actions": request(self.base_url, "/api/actions")}

    def seed(self, queries: list[str] | None = None, label: str = "seed") -> None:
        for index, cypher in enumerate(queries if queries is not None else [*seed_queries(), *seed_shift_board_queries(), *decision_seed_queries(), *source_branch_seed_queries()]):
            self.db.graph_query(f"{label}-{index}", cypher)

    def start_http(self, *, reserve: bool = False) -> None:
        self.service = WardAtlasService(self.args.db_port, self.db.graph, self.state_dir)
        if reserve:
            self.reservations[1].close()
        self.httpd = WardAtlasHTTPServer(("127.0.0.1", self.args.app_port), Handler, self.service)
        self.http_thread = threading.Thread(target=self.httpd.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
        self.http_thread.start()
        status, health = request(self.base_url, "/api/health")
        require(status == 200 and health.get("database") == "FalkorDB" and health.get("module_version") == 60002, "owned HTTP/FalkorDB health failed")

    def stop_http(self) -> None:
        if self.httpd is not None:
            self.httpd.shutdown()
            self.httpd.server_close()
            if self.http_thread is not None:
                self.http_thread.join(timeout=10)
                require(not self.http_thread.is_alive(), "owned HTTP thread did not stop")
            self.httpd = None

    def start(self) -> None:
        self.reservations[0].close()
        self.db.launch()
        self.db.graph_rows("owned-cold-graph", "MATCH (n) RETURN n.id AS id LIMIT 1")
        self.state_dir.parent.mkdir(parents=True, exist_ok=True)
        self.state_dir.mkdir(mode=0o700)
        self.state_owned = True
        self.start_http(reserve=True)

    @staticmethod
    def kill_browser(process: subprocess.Popen) -> None:
        # Chromium descendants can outlive a failed node runner. Kill only the
        # session/process-group created for this exact owned Popen, never names.
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait(timeout=5)

    def browser(self, mode: str, label: str) -> dict:
        report_path = self.args.evidence_dir / f"browser-{label}.json"
        screenshots = self.args.evidence_dir / f"screenshots-{label}"
        # Discard only this invocation's previously owned output, so a crashed
        # runner cannot accidentally consume a stale PASS on a repeated command.
        if report_path.exists():
            report_path.unlink()
        command = ["node", str(ROOT / "scripts/knowledge_diff_browser_assertions.cjs"), self.base_url, mode, str(report_path), str(screenshots)]
        process = subprocess.Popen(command, cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, start_new_session=True)
        self.browser_processes.append(process)
        try:
            stdout, stderr = process.communicate(timeout=240)
            require(process.returncode == 0 and report_path.is_file(), f"browser {label} failed: {(stdout + stderr)[-5000:]}")
        finally:
            self.kill_browser(process)
            self.browser_groups_stopped.add(process.pid)
        report = json.loads(report_path.read_text(encoding="utf-8"))
        assertions = report.get("assertions", [])
        require(report.get("status") == "PASS" and type(report.get("assertion_count")) is int and report["assertion_count"] > 0 and report["assertion_count"] == len(assertions) and all(row.get("passed") is True for row in assertions), f"browser {label} has zero, failed, or unexecuted assertions")
        for viewport in ("desktop", "mobile"):
            view = report.get(viewport, {})
            require(isinstance(view.get("text"), str) and bool(view["text"]) and bool(view.get("accessibility_tree")) and isinstance(view.get("api"), dict), f"browser {label} missing {viewport} DOM/accessibility/API evidence")
            missing = {"left", "right"} if mode == "cold" else ({"right"} if mode == "missing" else set())
            self.validate_response(view["api"], self.params(), missing)
        self.observed[f"browser-{label}"] = report
        return report

    def run(self) -> None:
        self.start()
        # Cold first: no reset/seed operation is available to this browser run.
        self.scenario("cold", self.cold)
        self.seed()
        self.scenario("identical", self.identical)
        self.scenario("mainline-boundary", self.mainline_boundary)
        self.scenario("modes-at-1605", self.modes)
        self.scenario("branch-boundary", self.branch_boundary)
        self.scenario("actual-assumed", self.actual_assumed)
        self.scenario("missing-edges", self.missing_edges)
        self.scenario("privacy", self.privacy)
        self.scenario("repeat-readonly", self.repeat_readonly)
        self.scenario("validation", self.validation)

    def cold(self) -> None:
        group = "cold"
        graph_before, state_before = self.graph_snapshot("cold-before"), self.state_snapshot()
        body = self.compare(params=self.params(), unavailable={"left", "right"})
        browser = self.browser("cold", "cold")
        self.check(group, "cold.unavailable", body["delta"]["state"] == "comparison-unavailable" and all(body[side]["availability"] == "comparison-unavailable" for side in ("left", "right")), body)
        self.check(group, "cold.no-delta", body["delta"]["items"] == [] and body["delta"]["substantive_delta_count"] is None and body["delta"]["unchanged_count"] == 0, body["delta"])
        serialized = json.dumps({"api": body, "browser": browser}, sort_keys=True)
        identities = ["SOP-OPS-9", "DEC-OPS-9", "REGISTER-OPS", "SRC-SOP", BRANCH_ID, OVERRIDE_ID, "ROLE-", "Synthetic SOP", "Reconciliation"]
        self.check(group, "cold.no-identities", not any(value in serialized for value in identities), {"forbidden_literals": identities, "absent": not any(value in serialized for value in identities)})
        self.check(group, "cold.browser-unknown", all(browser[view]["api"] == body and "unknown" in browser[view]["text"].lower() for view in ("desktop", "mobile")) and graph_before == self.graph_snapshot("cold-after") and state_before == self.state_snapshot(), {"browser": browser, "empty_graph_before": graph_before})
        self.check(group, "cold.no-absence-claim", body["delta"]["no_negative_conclusion"] is True and all(body[side]["projection"]["generic_unknown"] is True and body[side]["projection"]["negative_conclusion_asserted"] is False for side in ("left", "right")), body)
        self.observed[group] = body

    def identical(self) -> None:
        group = "identical"
        body = self.compare(group)
        self.check(group, "equal.zero", self.expected_delta(group, body), body["delta"])
        self.check(group, "equal.nine-unchanged", len(body["delta"]["items"]) == 9 and len(self.status_keys(body, "unchanged")) == 9, body["delta"])
        self.check(group, "equal.projections", body["left"]["projection"] == self.oracle.projection("mainline", T05) == body["right"]["projection"], {side: body[side] for side in ("left", "right")})
        self.check(group, "equal.types", {row["kind"] for row in body["delta"]["items"]} == set(self.expected["policy"]["kinds"]), body["delta"]["items"])
        self.check(group, "equal.no-completeness", body["delta"]["complete"] is False and body["delta"]["no_negative_conclusion"] is True and body["read_only"] is True, body["delta"])
        self.observed[group] = body

    def mainline_boundary(self) -> None:
        group = "mainline-boundary"
        body = self.compare(group)
        left, right = (body[side]["projection"] for side in ("left", "right"))
        self.check(group, "boundary.four-only-right", self.expected_delta(group, body), body["delta"])
        self.check(group, "boundary.five-unchanged", len(self.status_keys(body, "unchanged")) == 5, body["delta"])
        self.check(group, "boundary.claims-conflict-preserved", left["claims"] == right["claims"] and left["conflict"] == right["conflict"] == self.oracle.projection("mainline", T04)["conflict"], {"left": left, "right": right})
        self.check(group, "boundary.record-own-source", left["decision_record"] is None and right["decision_record"] == self.oracle.projection("mainline", T05)["decision_record"] and "source_observation:SRC-DEC-OPS-9-REVIEW-01" in self.status_keys(body, "projected-only-on-right"), right["decision_record"])
        self.check(group, "boundary.considers", left["decision_considers_edges"] == [] and right["decision_considers_edges"] == self.oracle.projection("mainline", T05)["decision_considers_edges"], {"left": left["decision_considers_edges"], "right": right["decision_considers_edges"]})
        self.check(group, "boundary.no-negative-conclusion", body["delta"]["no_negative_conclusion"] is True and all(projection["negative_conclusion_asserted"] is False and projection["current_owner_or_procedure_selected"] is False for projection in (left, right)), body)
        self.observed[group] = body

    @staticmethod
    def reversed_equal(body: dict, reverse: dict) -> bool:
        translation = {"projected-only-on-left": "projected-only-on-right", "projected-only-on-right": "projected-only-on-left"}
        expected = {row["key"]: {**row, "left": row["right"], "right": row["left"], "status": translation.get(row["status"], row["status"])} for row in body["delta"]["items"]}
        actual = {row["key"]: row for row in reverse["delta"]["items"]}
        return body["left"] == reverse["right"] and body["right"] == reverse["left"] and expected == actual and {key: value for key, value in body["delta"].items() if key != "items"} == {key: value for key, value in reverse["delta"].items() if key != "items"}

    def modes(self) -> None:
        group = "modes-at-1605"
        body = self.compare(group)
        left, right = (body[side]["projection"] for side in ("left", "right"))
        self.check(group, "modes.four-only-left", self.expected_delta(group, body), body["delta"])
        self.check(group, "modes.five-unchanged", len(self.status_keys(body, "unchanged")) == 5, body["delta"])
        self.check(group, "modes.v2-record-preserved", right["claims"] == [left["claims"][0]] and right["decision_record"] == left["decision_record"] and right["decision_considers_edges"] == self.oracle.projection("hypothetical", T05)["decision_considers_edges"], right)
        forbidden = (V3, "SRC-SOP-CURRENT-V3", "ROLE-MATERIALS-LEAD", "Current Text v3")
        self.check(group, "modes.no-v3-right", not any(token in json.dumps(right) for token in forbidden) and right["conflict"] is None, right)
        self.check(group, "modes.partial-not-unavailable", body["right"]["availability"] == "partial" and body["delta"]["state"] == "partial-witness-comparison" and body["right"]["scenario"] == self.oracle.scenario(T05), body["right"])
        reverse = self.compare(params=self.params(left_mode="hypothetical", right_mode="mainline"))
        self.check(group, "modes.reversal", self.reversed_equal(body, reverse), reverse)
        self.observed[group] = {"forward": body, "reverse": reverse}

    def branch_boundary(self) -> None:
        group = "branch-boundary"
        body = self.compare(group)
        left, right = (body[side]["projection"] for side in ("left", "right"))
        self.check(group, "branch.four-only-right", self.expected_delta(group, body), body["delta"])
        self.check(group, "branch.five-unchanged", len(self.status_keys(body, "unchanged")) == 5, body["delta"])
        self.check(group, "branch.record-preserved", left["decision_record"] == right["decision_record"] == self.oracle.projection("mainline", T05)["decision_record"], right["decision_record"])
        self.check(group, "branch.v3-conflict-link", right == self.oracle.projection("hypothetical", T06) and right["conflict"]["edge"]["status"] == "unresolved" and len(right["decision_considers_edges"]) == 2, right)
        self.check(group, "branch.partial-labeled", body["left"]["availability"] == "partial" and body["right"]["availability"] == "available" and body["delta"]["state"] == "partial-witness-comparison", body)
        reverse = self.compare(params=self.params(T06, T05, "hypothetical", "hypothetical"))
        require(self.reversed_equal(body, reverse), "branch boundary reversal failed")
        self.observed[group] = {"forward": body, "reverse": reverse}

    def actual_assumed(self) -> None:
        group = "actual-assumed"
        body = self.compare(group)
        left, right = (body[side]["projection"] for side in ("left", "right"))
        source = next(row["source"] for row in right["claims"] if row["version_id"] == V3)
        self.check(group, "time.one-observation-change", self.expected_delta(group, body) and len(body["delta"]["items"]) == 9, body["delta"])
        self.check(group, "time.actual-1604", source["observed_at"] == T04 and left["claims"][1]["source"]["observed_at"] == T04, {"left": left["claims"][1]["source"], "right": source})
        self.check(group, "time.assumed-1606", source.get("assumed_observed_at") == T06 and source.get("observation_mode") == "hypothetical" and body["right"]["scenario"] == self.oracle.scenario(T06), source)
        self.check(group, "time.v2-unmodified", left["claims"][0] == right["claims"][0] == self.oracle.projection("mainline", T06)["claims"][0] and "assumed_observed_at" not in right["claims"][0]["source"] and "observation_mode" not in right["claims"][0]["source"], right["claims"][0])
        citations = [{key: row["source"][key] for key in ("id", "title", "observed_at")} for row in right["claims"]] + [{key: right["decision_record"]["source"][key] for key in ("id", "title", "observed_at")}]
        self.check(group, "time.citations-exact", citations == self.expected["citations"], citations)
        stored = self.db.graph_rows("actual-source-fact", f"MATCH (s:Evidence {{id: '{SOURCE_ID}'}}) RETURN s.observed_at AS observed_at")
        self.check(group, "time.stored-fact-unchanged", stored == [{"observed_at": T04}], stored)
        self.observed[group] = body

    def missing_edges(self) -> None:
        group = "missing-edges"
        baseline = self.compare("modes-at-1605")
        results = []
        reseeds = []
        browser = None
        edges = self.oracle.branch["missing-edge-no-fallback-and-reseed"]["required_branch_edges"]
        require([edge["relationship"] for edge in edges] == self.cases[group]["expected"]["edge_types"], "source oracle required branch edges changed")
        edge_ids = ("REL-BR-SOP-V3-LATE-OBS-OVERRIDE", "REL-OBS-SOP-V3-LATE-OBS-SOURCE")
        for index, required_edge in enumerate(edges):
            edge = {"source": required_edge["from"], "kind": required_edge["relationship"], "target": required_edge["to"], "id": edge_ids[index]}
            before = self.graph_snapshot(f"missing-{index}-before")
            rows = self.db.graph_rows(f"owned-edge-{index}", f"MATCH (a {{id: '{edge['source']}'}})-[r:{edge['kind']} {{id: '{edge['id']}'}}]->(b {{id: '{edge['target']}'}}) RETURN r.id AS id")
            require(rows == [{"id": edge["id"]}], "required owned branch edge missing before removal")
            self.db.graph_query(f"remove-owned-edge-{index}", f"MATCH (a {{id: '{edge['source']}'}})-[r:{edge['kind']} {{id: '{edge['id']}'}}]->(b {{id: '{edge['target']}'}}) DELETE r")
            after = self.graph_snapshot(f"missing-{index}-after-delete")
            require(before["nodes"] == after["nodes"] and [row for row in before["edges"] if row["id"] != edge["id"]] == after["edges"], "edge adversary modified more than its one owned edge")
            body = self.compare(params=self.params(), unavailable={"right"})
            reverse = self.compare(params=self.params(left_mode="hypothetical", right_mode="mainline"), unavailable={"left"})
            results.append({"edge": edge["kind"], "body": body, "reverse": reverse})
            self.check(group, "missing.branch-edge-unavailable" if index == 0 else "missing.source-edge-unavailable", body["right"]["availability"] == "comparison-unavailable" and body["right"]["projection"] == self.oracle.projection("hypothetical", T05, unavailable=True) and "scenario" not in body["right"], body)
            if edge["kind"] == "FOR_SOURCE":
                state_before = self.state_snapshot()
                browser = self.browser("missing", "missing-source-edge")
                require(after == self.graph_snapshot("missing-browser-after") and state_before == self.state_snapshot(), "missing-edge browser wrote graph/state")
            self.seed(source_branch_seed_queries(), f"reseed-edge-{index}")
            restored = self.compare("modes-at-1605")
            restored_reverse = self.compare(params=self.params(left_mode="hypothetical", right_mode="mainline"))
            reseeds.append({"forward": restored, "reverse": restored_reverse, "graph_restored": before == self.graph_snapshot(f"reseed-{index}-after")})
        self.check(group, "missing.no-fallback", all(row["body"]["left"] == baseline["left"] and row["body"]["right"]["projection"] != baseline["left"]["projection"] and row["body"]["right"]["projection"]["generic_unknown"] is True for row in results), results)
        self.check(group, "missing.no-mass-removal", all(row["body"]["delta"]["substantive_delta_count"] is None and len(row["body"]["delta"]["items"]) == 9 and all(item["status"] == "incomparable" for item in row["body"]["delta"]["items"]) for row in results), results)
        self.check(group, "missing.reversal", all(self.reversed_equal(row["body"], row["reverse"]) for row in results), results)
        self.check(group, "missing.reseed-restores", len(reseeds) == 2 and all(row["forward"] == baseline and self.reversed_equal(baseline, row["reverse"]) and row["graph_restored"] for row in reseeds), reseeds)
        self.check(group, "missing.browser-unknown", browser is not None and all(browser[view]["api"] == results[1]["body"] and "unknown" in browser[view]["text"].lower() for view in ("desktop", "mobile")), browser)
        self.observed[group] = {"removed": results, "reseeded": reseeds}

    def privacy(self) -> None:
        group = "privacy"
        queries = {name: {"scope": "operations", **row["input"]} for name, row in self.cases.items() if "input" in row}
        baseline = {name: self.compare(params=params) for name, params in queries.items()}
        browser_before = self.browser("existing", "acl-before")
        before_decoy = self.graph_snapshot("acl-before-decoy")
        self.seed([*disconnected_decoy_queries(), *disconnected_decoy_branch_queries()], "decoy")
        decoy = {name: self.compare(params=params) for name, params in queries.items()}
        after_decoy = self.graph_snapshot("acl-after-decoy")
        self.check(group, "acl.decoy-response", baseline == decoy and len(after_decoy["nodes"]) > len(before_decoy["nodes"]), {"all_five_full_responses_equal": baseline == decoy, "before_nodes": len(before_decoy["nodes"]), "after_nodes": len(after_decoy["nodes"])})
        browser_decoy = self.browser("existing", "acl-decoy")
        self.seed([*restricted_fixture_queries(), *restricted_decision_queries(), *restricted_source_branch_queries()], "restricted")
        restricted = {name: self.compare(params=params) for name, params in queries.items()}
        after_restricted = self.graph_snapshot("acl-after-restricted")
        self.check(group, "acl.restricted-response", baseline == restricted and len(after_restricted["nodes"]) > len(after_decoy["nodes"]) and len(after_restricted["edges"]) > len(after_decoy["edges"]), {"all_five_full_responses_equal": baseline == restricted, "before_nodes": len(after_decoy["nodes"]), "after_nodes": len(after_restricted["nodes"])})
        browser_after = self.browser("existing", "acl-after")
        self.check(group, "acl.desktop-dom", all(browser_before["desktop"][key] == browser_decoy["desktop"][key] == browser_after["desktop"][key] for key in ("text", "api")) and all(browser_before["cases"][case][key] == browser_decoy["cases"][case][key] == browser_after["cases"][case][key] for case in queries for key in ("text", "api")), {"before": browser_before["desktop"], "decoy": browser_decoy["desktop"], "after": browser_after["desktop"], "all_five_selections_compared": list(queries)})
        self.check(group, "acl.mobile-dom", all(browser_before["mobile"][key] == browser_decoy["mobile"][key] == browser_after["mobile"][key] for key in ("text", "api")), {"before": browser_before["mobile"], "decoy": browser_decoy["mobile"], "after": browser_after["mobile"]})
        self.check(group, "acl.accessibility", all(browser_before[view]["accessibility_tree"] == browser_decoy[view]["accessibility_tree"] == browser_after[view]["accessibility_tree"] for view in ("desktop", "mobile")) and all(browser_before["cases"][case]["accessibility_tree"] == browser_decoy["cases"][case]["accessibility_tree"] == browser_after["cases"][case]["accessibility_tree"] for case in queries), {view: {"before": browser_before[view]["accessibility_tree"], "decoy": browser_decoy[view]["accessibility_tree"], "after": browser_after[view]["accessibility_tree"]} for view in ("desktop", "mobile")})
        serialized = json.dumps({"api": restricted, "browser": browser_after}, sort_keys=True).lower()
        leaked = [token for token in TRIPWIRES if token.lower() in serialized]
        self.check(group, "acl.no-tripwire", not leaked and baseline == restricted and all("existence" not in key.lower() and "restricted" not in key.lower() for response in restricted.values() for key in response), {"leaked": leaked, "response_keys": list(next(iter(restricted.values())))})
        self.observed[group] = {"baseline": baseline, "decoy": decoy, "restricted": restricted}

    def repeat_readonly(self) -> None:
        group = "repeat-readonly"
        # Additive MERGE twice, not destructive resets that hide duplicates.
        self.seed(label="repeat-first")
        first = self.graph_snapshot("repeat-first")
        baseline = {name: self.compare(name) for name in ("identical", "mainline-boundary", "modes-at-1605", "branch-boundary", "actual-assumed")}
        self.seed(label="repeat-second")
        second = self.graph_snapshot("repeat-second")
        self.check(group, "repeat.idempotent", first == second, {"first": first, "second": second})
        query = f"MATCH (b:ScenarioBranch {{id:'{BRANCH_ID}'}})-[:HAS_HYPOTHETICAL_OBSERVATION]->(o:ObservationOverride {{id:'{OVERRIDE_ID}'}})-[:FOR_SOURCE]->(s:Evidence {{id:'{SOURCE_ID}'}}) RETURN b.id AS branch_id, o.id AS override_id, s.observed_at AS observed_at"
        reconnect_rows = []
        tokens = []
        for _ in range(2):
            client = RespConnection("127.0.0.1", self.args.db_port, timeout=5)
            try:
                tokens.append(client.connection_token)
                reconnect_rows.append(result_rows(query_result(client.command("GRAPH.QUERY", self.db.graph, query))))
            finally:
                client.close()
        old_service = self.service
        self.stop_http()
        self.start_http()
        reconnected = {name: self.compare(name) for name in baseline}
        self.check(group, "repeat.reconnect", self.service is not old_service and tokens[0] != tokens[1] and reconnect_rows == [[{"branch_id": BRANCH_ID, "override_id": OVERRIDE_ID, "observed_at": T04}]] * 2 and baseline == reconnected, {"client_tokens": tokens, "rows": reconnect_rows, "all_five_responses_equal": baseline == reconnected, "new_service_instance": self.service is not old_service})
        graph_before, state_before = self.graph_snapshot("readonly-before"), self.state_snapshot()
        for name in baseline:
            self.compare(name)
        browser = self.browser("existing", "repeat")
        graph_after, state_after = self.graph_snapshot("readonly-after"), self.state_snapshot()
        self.check(group, "repeat.graph-readonly", graph_before == graph_after, {"before": graph_before, "after": graph_after})
        self.check(group, "repeat.actions-readonly", state_before == state_after and state_after["pending"] == {} and state_after["pending_watches"] == {} and not state_after["files"], {"before": state_before, "after": state_after})
        self.check(group, "repeat.desktop-mobile", browser["desktop"]["api"] == browser["mobile"]["api"] == baseline["modes-at-1605"] and browser["desktop"]["text"] == browser["mobile"]["text"] and browser["desktop"]["accessibility_tree"] == browser["mobile"]["accessibility_tree"], {"desktop": browser["desktop"], "mobile": browser["mobile"]})
        names = [str(row.get("name", row.get("assertion", ""))).lower() for row in browser["assertions"]]
        self.check(group, "repeat.keyboard", browser.get("keyboard") is True and any("keyboard" in name for name in names), {"keyboard": browser.get("keyboard"), "executed_assertions": names})
        self.check(group, "repeat.stale-request", browser.get("stale_request_ignored") is True and any("stale" in name or "late response" in name for name in names), {"stale_request_ignored": browser.get("stale_request_ignored"), "executed_assertions": names})
        self.observed[group] = {"baseline": baseline, "reconnected": reconnected}

    def validation(self) -> None:
        group = "validation"
        graph_before, state_before = self.graph_snapshot("validation-before"), self.state_snapshot()
        baseline = self.compare("modes-at-1605")
        offsets = self.params("2026-10-14T18:05:00+02:00", "2026-10-14T11:05:00-05:00")
        status, equivalent = self.raw(offsets)
        self.check(group, "valid.utc", status == 200 and equivalent == baseline, {"params": offsets, "status": status, "response": equivalent})

        def rejects(assertion: str, cases: list[dict | list[tuple[str, str]]], expected_status: int = 400) -> None:
            observations = []
            for params in cases:
                result, body = self.raw(params)
                observations.append({"params": params, "status": result, "response": body})
            self.check(group, assertion, bool(observations) and all(row["status"] == expected_status and isinstance(row["response"], dict) and isinstance(row["response"].get("error"), str) for row in observations), observations)

        rejects("valid.invalid-time", [{**self.params(), key: value} for key in ("left_known_at", "right_known_at") for value in ("not-a-timestamp", "2026-02-30T16:05:00Z", "2026-10-14T16:05:00+00:60", "2026-10-14T16:05:00-24:00", "0001-01-01T00:00:00+01:00", "")])
        rejects("valid.naive-time", [{**self.params(), key: value} for key in ("left_known_at", "right_known_at") for value in ("2026-10-14T16:05:00", "2026-10-14")])
        rejects("valid.missing", [{key: value for key, value in self.params().items() if key != missing} for missing in self.expected["policy"]["required_parameters"]])
        rejects("valid.duplicate-time", [[*self.params().items(), (key, value)] for key in ("left_known_at", "right_known_at") for value in (T05, T06, "")])
        rejects("valid.invalid-mode", [{**self.params(), key: value} for key in ("left_mode", "right_mode") for value in ("other", "Mainline", "", "mainline,hypothetical")])
        rejects("valid.duplicate-mode", [[*self.params().items(), (key, value)] for key in ("left_mode", "right_mode") for value in ("mainline", "hypothetical", "")])
        rejects("valid.scope", [{**self.params(), "scope": scope} for scope in ("restricted", "all", "")] + [[*self.params().items(), ("scope", scope)] for scope in ("operations", "restricted")], 403)
        # The new endpoint is a read, not a second mutation surface.
        post_status, post_body = self.raw(self.params(), "POST")
        self.check(group, "valid.readonly", post_status in (404, 405) and baseline == self.compare("modes-at-1605") and graph_before == self.graph_snapshot("validation-after") and state_before == self.state_snapshot(), {"post_status": post_status, "post_response": post_body, "state_after": self.state_snapshot()})

    def stop(self) -> None:
        errors = []
        for listener in self.reservations:
            listener.close()
        self.cleanup["port_reservations_closed"] = all(listener.fileno() == -1 for listener in self.reservations)
        try:
            self.stop_http()
            self.cleanup["owned_app_stopped"] = self.http_thread is None or not self.http_thread.is_alive()
        except BaseException as exc:
            errors.append(f"HTTP cleanup: {exc}")
        try:
            for process in self.browser_processes:
                if process.pid not in self.browser_groups_stopped:
                    self.kill_browser(process)
                    self.browser_groups_stopped.add(process.pid)
            self.cleanup["owned_browser_processes_stopped"] = all(process.poll() is not None for process in self.browser_processes)
        except BaseException as exc:
            errors.append(f"browser cleanup: {exc}")
        try:
            self.db.stop()
        except BaseException as exc:
            errors.append(f"database cleanup: {exc}")
        self.cleanup["owned_graph_deleted"] = self.db.cleanup.get("owned_graph_deleted") is True
        self.cleanup["owned_database_process_stopped"] = self.db.process is None or self.db.process.poll() is not None
        try:
            runtime = ROOT / ".local/falkordb-runs" / self.run_id
            if self.db.server_log_path is not None and self.db.server_log_path.parent.resolve() == runtime.resolve():
                # Keep log evidence outside the disposable owned runtime.
                try:
                    if self.db.server_log_path.is_file():
                        shutil.copyfile(self.db.server_log_path, self.args.evidence_dir / "owned-falkordb.log")
                finally:
                    shutil.rmtree(runtime)
            self.cleanup["owned_database_runtime_removed"] = not runtime.exists()
        except BaseException as exc:
            errors.append(f"runtime cleanup: {exc}")
        try:
            if self.state_owned and self.state_dir.exists():
                shutil.rmtree(self.state_dir)
            self.cleanup["owned_state_removed"] = not self.state_dir.exists()
        except BaseException as exc:
            errors.append(f"state cleanup: {exc}")
        self.observed["cleanup_errors"] = errors

    def finish_coverage(self) -> None:
        self.check("repeat-readonly", "repeat.owned-cleanup", all(self.cleanup.values()), dict(self.cleanup))
        record = next(row for row in self.scenarios if row["id"] == "repeat-readonly")
        record.update(status="pass", assertion_count=len(self.coverage["repeat-readonly"]))
        require(len(self.scenarios) == 10 and {row["id"] for row in self.scenarios} == set(REQUIRED_ASSERTIONS), "not all ten focused groups ran")
        require(len(self.assertions) == 63 and all(self.coverage[group] == set(ids) for group, ids in REQUIRED_ASSERTIONS.items()), "exact 63-assertion matrix coverage incomplete")
        require(all(row["passed"] for row in self.assertions) and all(row["status"] == "pass" and row["assertion_count"] > 0 for row in self.scenarios), "zero/failed/unexecuted mapped group")

    def report(self, status: str, error: str | None) -> dict:
        return {
            "schema_version": 1, "status": status, "error": error, "run_id": self.run_id,
            "oracle_sha256": digest(EXPECTED_PATH), "matrix_sha256": digest(MATRIX_PATH),
            "frozen_contract_commits": ["aab0409", "01796d2"],
            "source_oracles": self.expected["pinned_sources"], "external_planner_reads": [],
            "graph": self.db.graph, "binds": {"database": f"127.0.0.1:{self.args.db_port}", "app": self.base_url},
            "falkordb": self.db.database, "groups": self.scenarios,
            "assertion_count": len(self.assertions), "mapped_assertion_count": len(self.assertions),
            "mapped_assertions": self.assertions,
            "missing_matrix_assertions": {group: sorted(set(ids) - self.coverage[group]) for group, ids in REQUIRED_ASSERTIONS.items()},
            "requests": self.requests, "observed": self.observed, "cleanup": self.cleanup,
            "owned_resources_cleaned": all(self.cleanup.values()), "query_count": self.db.query_count,
            "timings": self.db.timings, "resource_samples": self.db.resource_samples,
        }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    for flag in ("all", "require-live-falkordb", "desktop-mobile", "fail-on-zero"):
        parser.add_argument("--" + flag, action="store_true", required=True)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--evidence-dir", type=Path)
    args = parser.parse_args(argv)
    run_root = Path(os.environ.get("WORKSHOP_RUN_DIR", str(ROOT / ".local/knowledge-diff-runs"))).expanduser().resolve()
    args.evidence_dir = (args.evidence_dir or run_root / "knowledge-diff").resolve()
    args.report = (args.report or args.evidence_dir / "report.json").resolve()
    verifier = None
    reservations: list[socket.socket] = []
    status, error = "FAIL", None
    report = {"status": "FAIL", "assertion_count": 0, "mapped_assertion_count": 0}
    try:
        args.evidence_dir.mkdir(parents=True, exist_ok=True)
        expected, matrix, timeline, branch = load_contracts()
        reservations, args.db_port, args.app_port = reserve_ports()
        verifier = KnowledgeDiffLive(args, expected, matrix, SourceOracle(timeline, branch), reservations)
        verifier.run()
        status = "PASS"
    except BaseException as exc:
        error = f"{type(exc).__name__}: {exc}"
    finally:
        if verifier is not None:
            verifier.stop()
            if status == "PASS":
                try:
                    verifier.finish_coverage()
                except BaseException as exc:
                    status, error = "FAIL", f"{type(exc).__name__}: {exc}"
            report = verifier.report(status, error)
        else:
            for listener in reservations:
                listener.close()
            report["error"] = error
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    if status != "PASS":
        print(f"knowledge diff: FAIL: {error}; report={args.report}", file=sys.stderr)
        return 1
    print(f"knowledge diff: PASS (10 groups, 63 mapped assertions, real FalkorDB, desktop/mobile, owned cleanup); report={args.report}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

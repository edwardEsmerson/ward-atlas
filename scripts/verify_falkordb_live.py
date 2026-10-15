#!/usr/bin/env python3
"""Exercise WardAtlas against an owned local Redis process with real FalkorDB.

This harness uses a tiny RESP2 client instead of adding a Python database
dependency.  It starts only its own process, binds only to 127.0.0.1, creates a
unique graph name, and deletes only that graph.  The query outcomes are checked
against tests/expected-results.json, which is authored separately from these
candidate queries.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import re
import shutil
import socket
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Any

from install_falkordb_module import (
    AMD64_MANIFEST_DIGEST,
    ARTIFACT,
    INDEX_DIGEST,
    MODULE_LAYER_DIGEST,
    MODULE_MEMBER,
    MODULE_SHA256,
    PLATFORM,
    SOURCE,
    install as install_falkordb_module,
)
from wardatlas_fixtures import (
    IMPACT_QUERY,
    SOP_CONFLICT_QUERY,
    restricted_fixture_queries,
    seed_queries,
)


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / ".local/falkordb/falkordb.so"
PROVENANCE_PATH = ROOT / ".local/falkordb/PROVENANCE.json"
RUNS_PATH = ROOT / ".local/falkordb-runs"
MORNING_STATUS = ROOT / "MORNING-STATUS.md"
README_PATH = ROOT / "README.md"
VERSION = "1.0"


class VerificationFailure(RuntimeError):
    pass


class RedisCommandError(RuntimeError):
    pass


class RespConnection:
    """Minimal RESP2 client: only the framing used by this local test harness."""

    def __init__(self, host: str, port: int, timeout: float = 2.0):
        self.socket = socket.create_connection((host, port), timeout=timeout)
        self.socket.settimeout(timeout)
        self.reader = self.socket.makefile("rb")
        self.connection_token = uuid.uuid4().hex

    def close(self) -> None:
        try:
            self.reader.close()
        finally:
            self.socket.close()

    def command(self, *args: str | bytes) -> Any:
        encoded = [arg if isinstance(arg, bytes) else str(arg).encode("utf-8") for arg in args]
        payload = bytearray(f"*{len(encoded)}\r\n".encode("ascii"))
        for item in encoded:
            payload.extend(f"${len(item)}\r\n".encode("ascii"))
            payload.extend(item)
            payload.extend(b"\r\n")
        self.socket.sendall(payload)
        return self._read_reply()

    def _line(self) -> bytes:
        line = self.reader.readline()
        if not line.endswith(b"\r\n"):
            raise ConnectionError("truncated RESP line")
        return line[:-2]

    def _read_reply(self) -> Any:
        marker = self.reader.read(1)
        if not marker:
            raise ConnectionError("server closed the RESP connection")
        line = self._line()
        if marker == b"+":
            return line
        if marker == b"-":
            raise RedisCommandError(line.decode("utf-8", "replace"))
        if marker == b":":
            return int(line)
        if marker == b"$":
            length = int(line)
            if length == -1:
                return None
            body = self.reader.read(length)
            if len(body) != length or self.reader.read(2) != b"\r\n":
                raise ConnectionError("truncated RESP bulk string")
            return body
        if marker == b"*":
            count = int(line)
            if count == -1:
                return None
            return [self._read_reply() for _ in range(count)]
        raise ConnectionError(f"unsupported RESP2 marker {marker!r}")


def text(value: Any) -> Any:
    if isinstance(value, bytes):
        return value.decode("utf-8", "replace")
    if isinstance(value, list):
        return [text(item) for item in value]
    return value


def query_result(reply: Any) -> dict[str, Any]:
    reply = text(reply)
    # FalkorDB returns only a statistics array for write-only statements (no
    # RETURN clause); reads with RETURN use [headers, rows, statistics].
    if (
        isinstance(reply, list)
        and len(reply) == 1
        and isinstance(reply[0], list)
        and all(isinstance(item, str) for item in reply[0])
        and any(
            item.startswith((
                "Nodes created:", "Relationships created:", "Cached execution:",
                "Query internal execution time:", "Graph version:",
            ))
            for item in reply[0]
        )
    ):
        return {"headers": [], "rows": [], "statistics": reply}
    if not isinstance(reply, list) or len(reply) < 2:
        raise VerificationFailure(f"unexpected GRAPH.QUERY reply envelope: {reply!r}")
    headers, rows = reply[0], reply[1]
    if not isinstance(headers, list) or not isinstance(rows, list):
        raise VerificationFailure(f"unexpected GRAPH.QUERY headers/rows: {reply!r}")
    return {"headers": headers, "rows": rows, "statistics": reply[2:]}


def result_rows(result: dict[str, Any]) -> list[dict[str, Any]]:
    headers = result["headers"]
    rows = result["rows"]
    normalized: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, list) or len(row) != len(headers):
            raise VerificationFailure(f"query row does not match header: {row!r}")
        normalized.append(dict(zip(headers, row)))
    return normalized


def status_map(reply: Any) -> dict[str, Any]:
    reply = text(reply)
    if not isinstance(reply, list) or not reply:
        return {}
    result: dict[str, Any] = {}
    for row in reply:
        if isinstance(row, list):
            for index in range(0, len(row) - 1, 2):
                result[str(row[index]).lower()] = row[index + 1]
    return result


def module_list_maps(reply: Any) -> list[dict[str, Any]]:
    """Normalize Redis MODULE LIST's RESP2 map shape across server versions."""
    reply = text(reply)

    def scalar(value: Any) -> bool:
        return not isinstance(value, (list, dict))

    def direct_map(value: Any) -> dict[str, Any] | None:
        if isinstance(value, dict):
            return {str(key).lower(): item for key, item in value.items()}
        if not isinstance(value, list):
            return None
        # Some RESP2 versions encode a map as [[key, value], ...].
        if value and all(
            isinstance(pair, list) and len(pair) == 2 and scalar(pair[0])
            for pair in value
        ):
            return {str(pair[0]).lower(): pair[1] for pair in value}
        # Other versions encode it as [key, value, key, value, ...].
        if len(value) >= 2 and len(value) % 2 == 0 and all(scalar(key) for key in value[::2]):
            return {str(value[index]).lower(): value[index + 1] for index in range(0, len(value), 2)}
        return None

    def visit(value: Any) -> list[dict[str, Any]]:
        candidate = direct_map(value)
        if candidate is not None and "name" in candidate:
            return [candidate]
        if isinstance(value, list):
            # A module list can itself be represented as an array of key/value
            # pairs, which is one map rather than one map per pair.
            if value and all(
                isinstance(pair, list) and len(pair) == 2 and scalar(pair[0])
                for pair in value
            ):
                aggregate = {str(pair[0]).lower(): pair[1] for pair in value}
                if "name" in aggregate:
                    return [aggregate]
            found: list[dict[str, Any]] = []
            for child in value:
                found.extend(visit(child))
            return found
        return []

    return visit(reply)


def host_facts() -> dict[str, Any]:
    cpuinfo = Path("/proc/cpuinfo").read_text(encoding="utf-8", errors="replace")
    first = cpuinfo.split("\n\n", 1)[0]
    model_match = re.search(r"^model name\s*:\s*(.+)$", first, re.MULTILINE)
    flags_match = re.search(r"^flags\s*:\s*(.+)$", first, re.MULTILINE)
    flags = flags_match.group(1).split() if flags_match else []
    meminfo = Path("/proc/meminfo").read_text(encoding="utf-8", errors="replace")

    def mem_kib(key: str) -> int | None:
        match = re.search(rf"^{re.escape(key)}:\s+(\d+) kB$", meminfo, re.MULTILINE)
        return int(match.group(1)) if match else None

    cgroup = {}
    for file in (Path("/sys/fs/cgroup/memory.max"), Path("/sys/fs/cgroup/memory.current"), Path("/sys/fs/cgroup/cpu.max")):
        try:
            cgroup[file.name] = file.read_text(encoding="ascii").strip()
        except OSError:
            cgroup[file.name] = "not exposed"
    return {
        "os": platform.platform(),
        "architecture": platform.machine(),
        "cpu_model": model_match.group(1).strip() if model_match else "not exposed",
        "cpu_flags_observed": flags,
        "avx": "avx" in flags,
        "avx2": "avx2" in flags,
        "mem_total_kib": mem_kib("MemTotal"),
        "mem_available_kib": mem_kib("MemAvailable"),
        "cgroup_read_only_facts": cgroup,
    }


def read_process_memory(pid: int) -> dict[str, int]:
    result = {}
    try:
        status = Path(f"/proc/{pid}/status").read_text(encoding="ascii", errors="replace")
    except OSError:
        return result
    for label, name in (("VmRSS", "rss_kib"), ("VmHWM", "high_water_rss_kib")):
        match = re.search(rf"^{label}:\s+(\d+) kB$", status, re.MULTILINE)
        if match:
            result[name] = int(match.group(1))
    return result


def explicit_bind_preflight(port: int) -> None:
    """Bind-check the requested address; on failure, never probe its occupant."""
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        probe.bind(("127.0.0.1", port))
    except OSError as exc:
        raise VerificationFailure(
            f"cannot bind the explicitly selected 127.0.0.1:{port}; stopped without connecting: {exc}"
        ) from exc
    finally:
        probe.close()


def assert_provenance() -> dict[str, Any]:
    if not MODULE_PATH.is_file() or not PROVENANCE_PATH.is_file():
        raise VerificationFailure(
            "project-local FalkorDB module/provenance missing after bootstrap"
        )
    if not MODULE_PATH.resolve().is_relative_to(ROOT) or not PROVENANCE_PATH.resolve().is_relative_to(ROOT):
        raise VerificationFailure("FalkorDB module and provenance must remain project-local")
    receipt = json.loads(PROVENANCE_PATH.read_text(encoding="utf-8"))
    actual_hash = hashlib.sha256(MODULE_PATH.read_bytes()).hexdigest()
    required_receipt = {
        "source": SOURCE,
        "artifact": ARTIFACT,
        "platform": PLATFORM,
        "image_index_digest": INDEX_DIGEST,
        "platform_manifest_digest": AMD64_MANIFEST_DIGEST,
        "module_layer_digest": MODULE_LAYER_DIGEST,
        "member": MODULE_MEMBER,
        "module_sha256": MODULE_SHA256,
        "destination": str(MODULE_PATH.resolve()),
    }
    mismatched = [key for key, value in required_receipt.items() if receipt.get(key) != value]
    if mismatched:
        raise VerificationFailure(f"FalkorDB provenance receipt differs from the pinned artifact: {mismatched}")
    if actual_hash != MODULE_SHA256 or receipt.get("module_sha256") != actual_hash:
        raise VerificationFailure("FalkorDB module SHA-256 does not match its provenance receipt")
    return {**receipt, "module_sha256_verified": actual_hash}


def ensure_project_local_module() -> dict[str, Any]:
    """Bootstrap the pinned module when a clean source checkout has no .local/."""
    if not MODULE_PATH.is_file() or not PROVENANCE_PATH.is_file():
        try:
            installed = install_falkordb_module(MODULE_PATH)
        except Exception as exc:
            raise VerificationFailure(
                "automatic project-local FalkorDB bootstrap failed; no database was started: "
                f"{type(exc).__name__}: {exc}"
            ) from exc
        if installed.get("module_sha256") != MODULE_SHA256:
            raise VerificationFailure("bootstrap returned an unexpected FalkorDB module digest")
    return assert_provenance()


class LiveVerifier:
    def __init__(self, port: int, expected: dict[str, Any], matrix: dict[str, Any]):
        self.port = port
        self.expected = expected
        self.matrix = matrix
        self.run_id = uuid.uuid4().hex
        self.graph = f"wardatlas_test_{self.run_id}"
        self.process: subprocess.Popen | None = None
        self.server_log_path: Path | None = None
        self.client: RespConnection | None = None
        self.graph_created = False
        self.timings: list[dict[str, Any]] = []
        self.resource_samples: list[dict[str, int]] = []
        self.scenarios: list[dict[str, Any]] = []
        self.assertion_count = 0
        self.assertion_log: list[dict[str, Any]] = []
        self.failures: list[str] = []
        self.query_count = 0
        self.database: dict[str, Any] = {}
        self.cleanup: dict[str, Any] = {"owned_graph_deleted": False, "owned_process_stopped": False}

    def observe(self) -> None:
        if self.process:
            memory = read_process_memory(self.process.pid)
            if memory:
                self.resource_samples.append({"elapsed_ms": round((time.monotonic() - self.started_at) * 1000, 3), **memory})

    def command(self, name: str, *args: str) -> Any:
        if self.client is None:
            raise VerificationFailure("no owned Redis client is connected")
        started = time.perf_counter()
        result = self.client.command(*args)
        elapsed = (time.perf_counter() - started) * 1000
        self.timings.append({"command": name, "elapsed_ms": round(elapsed, 3)})
        self.observe()
        return result

    def graph_query(self, label: str, cypher: str) -> dict[str, Any]:
        self.query_count += 1
        reply = self.command(f"GRAPH.QUERY {label}", "GRAPH.QUERY", self.graph, cypher)
        self.graph_created = True
        return query_result(reply)

    def graph_rows(self, label: str, cypher: str) -> list[dict[str, Any]]:
        return result_rows(self.graph_query(label, cypher))

    def check(self, scenario: dict[str, Any], name: str, condition: bool, detail: Any = None) -> None:
        outcome = {"assertion": name, "passed": bool(condition)}
        if detail is not None:
            outcome["detail"] = detail
        self.assertion_log.append({"scenario": scenario["id"], **outcome})
        if not condition:
            self.assertion_count += 1
            scenario["assertions"] = scenario.get("assertions", 0) + 1
            raise VerificationFailure(f"{scenario['id']}: assertion failed: {name}; observed={detail!r}")
        self.assertion_count += 1
        scenario["assertions"] = scenario.get("assertions", 0) + 1

    def scenario(self, identifier: str, callback) -> None:
        case = {"id": identifier, "status": "running", "assertions": 0}
        self.scenarios.append(case)
        started = time.perf_counter()
        try:
            callback(case)
        except Exception as exc:
            case["status"] = "fail"
            case["error"] = f"{type(exc).__name__}: {exc}"
            raise
        else:
            case["status"] = "pass"
        finally:
            case["elapsed_ms"] = round((time.perf_counter() - started) * 1000, 3)

    def launch(self) -> None:
        if not (1024 <= self.port <= 65535):
            raise VerificationFailure("database port must be an explicitly selected high port")
        explicit_bind_preflight(self.port)
        server = shutil.which("redis-server")
        if server is None:
            raise VerificationFailure("redis-server executable is unavailable; no server was installed")
        provenance = ensure_project_local_module()
        RUNS_PATH.mkdir(parents=True, exist_ok=True)
        run_dir = RUNS_PATH / self.run_id
        run_dir.mkdir(mode=0o700)
        self.server_log_path = run_dir / "redis-server.log"
        log = self.server_log_path.open("wb")
        argv = [
            server,
            "--bind", "127.0.0.1",
            "--port", str(self.port),
            "--protected-mode", "yes",
            "--daemonize", "no",
            "--save", "",
            "--appendonly", "no",
            "--dir", str(run_dir),
            "--dbfilename", "owned-test-dump.rdb",
            "--loglevel", "notice",
            "--loadmodule", str(MODULE_PATH),
        ]
        self.started_at = time.monotonic()
        self.process = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
        self.database = {
            "redis_server_executable": server,
            "redis_server_version": None,
            "falkordb_module_name": None,
            "falkordb_module_version": None,
            "module_provenance": provenance,
            "run_id": self.run_id,
            "graph_name": self.graph,
            "process_id": self.process.pid,
            "bind": f"127.0.0.1:{self.port}",
            "server_log": str(self.server_log_path),
        }
        deadline = time.monotonic() + 15
        ready = False
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                log.close()
                self.database["startup_exit_code"] = self.process.returncode
                raise VerificationFailure(
                    "owned redis-server exited before readiness; no connection was attempted (see server log)"
                )
            try:
                log.flush()
                contents = self.server_log_path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                contents = ""
            if "Ready to accept connections" in contents:
                ready = True
                break
            time.sleep(0.05)
        log.close()
        if not ready:
            if self.process.poll() is None:
                self.process.terminate()
                self.process.wait(timeout=5)
                self.cleanup["owned_process_stopped"] = True
            raise VerificationFailure("owned Redis/FalkorDB process did not become ready; connection was not attempted")
        # This connect is permitted only after this verifier's exact Popen
        # process logged that its requested loopback listener is ready.
        self.client = RespConnection("127.0.0.1", self.port)
        self.observe()
        ping = text(self.command("PING", "PING"))
        if ping != "PONG":
            raise VerificationFailure(f"owned local process returned unexpected PING response: {ping!r}")
        module_entries = text(self.command("MODULE LIST", "MODULE", "LIST"))
        self.database["module_list_reply"] = module_entries
        modules = module_list_maps(module_entries)
        # The official FalkorDB graph module reports its Redis module name as
        # `graph` (also visible in the owned server's module-load log), not as
        # the Docker image/product name `FalkorDB`.
        found = next(
            (m for m in modules if str(m.get("name", "")).lower() in {"falkordb", "graph"}),
            None,
        )
        if not found:
            raise VerificationFailure(f"MODULE LIST did not report the actual FalkorDB graph module: {modules!r}")
        if int(found.get("ver", 0)) <= 0:
            raise VerificationFailure(f"FalkorDB module has no runtime version: {found!r}")
        server_log = self.server_log_path.read_text(encoding="utf-8", errors="replace") if self.server_log_path else ""
        if f"Module '{found.get('name')}' loaded from {MODULE_PATH}" not in server_log:
            raise VerificationFailure("owned server log does not identify the loaded graph module path")
        # The FalkorDB Redis module encodes its version numerically as
        # major*10000 + minor*100 + patch (6.0.2 -> 60002).
        if int(found["ver"]) != 60002:
            raise VerificationFailure(f"runtime FalkorDB module version does not match pinned 6.0.2 artifact: {found!r}")
        self.database["falkordb_module_name"] = found.get("name")
        self.database["falkordb_module_version"] = int(found["ver"])
        self.database["falkordb_module_load_confirmed_in_owned_log"] = True
        self.database["module_list_entries"] = modules
        info = text(self.command("INFO server", "INFO", "server"))
        match = re.search(r"^redis_version:([^\r\n]+)", str(info), re.MULTILINE)
        self.database["redis_server_version"] = match.group(1) if match else "version not parsed"

    def impact(self) -> list[dict[str, Any]]:
        rows = self.graph_rows("outage-impact", IMPACT_QUERY)
        return [
            {
                "path": [
                    row["incident_id"], row["equipment_id"], row["service_id"],
                    row["department_id"], row["role_id"],
                ],
                "source_ids": [
                    row["incident_source_id"], row["equipment_source_id"],
                    row["service_source_id"], row["ownership_source_id"],
                ],
                "source_titles": [
                    row["incident_source_title"], row["equipment_source_title"],
                    row["service_source_title"], row["ownership_source_title"],
                ],
                "observed_at": [
                    row["incident_observed_at"], row["equipment_observed_at"],
                    row["service_observed_at"], row["ownership_observed_at"],
                ],
            }
            for row in rows
        ]

    def count(self, label: str, cypher: str) -> int:
        rows = self.graph_rows(label, cypher)
        if len(rows) != 1:
            raise VerificationFailure(f"expected one count row for {label}, got {rows!r}")
        value = next(iter(rows[0].values()))
        return int(value)

    def seed_all(self) -> None:
        for index, cypher in enumerate(seed_queries()):
            self.graph_query(f"seed-{index + 1}", cypher)

    def run(self) -> None:
        self.launch()
        expected_by_id = {item["id"]: item for item in self.expected["scenarios"]}

        def cold_empty(case: dict[str, Any]) -> None:
            impact_rows = self.impact()
            sop_rows = self.graph_rows("cold-empty-sop", SOP_CONFLICT_QUERY)
            self.check(case, "empty graph returns zero impact rows", impact_rows == [], impact_rows)
            self.check(case, "empty graph returns zero SOP findings", sop_rows == [], sop_rows)

        self.scenario("cold-empty-graph", cold_empty)

        # The graph starts with an empty graph, then only the unrestricted,
        # deterministic fixture is seeded.  The restricted branch is added in
        # its adversarial scenario after visible results have been captured.
        self.seed_all()
        initial_paths = self.impact()
        multi_expected = expected_by_id["multi-hop-outage-current-ownership"]["expected"]
        canonical_paths = self.expected["expected_paths"]

        def multi_hop(case: dict[str, Any]) -> None:
            paths = [row["path"] for row in initial_paths]
            departments = [path[3] for path in paths]
            roles = [path[4] for path in paths]
            stale_role = multi_expected["excluded_stale_role_id"]
            evidence = [
                {
                    "path": row["path"],
                    "source_ids": row["source_ids"],
                    "source_titles": row["source_titles"],
                    "observed_at": row["observed_at"],
                }
                for row in initial_paths
            ]
            pinned = self.expected["expected_evidence_by_path"]
            self.check(case, "exact ordered multi-hop paths", paths == canonical_paths, paths)
            self.check(
                case,
                "exact affected departments",
                departments == multi_expected["department_ids"],
                departments,
            )
            self.check(
                case,
                "exact current owning roles",
                roles == multi_expected["current_role_ids"],
                roles,
            )
            self.check(case, "stale role excluded from current ownership", all(stale_role not in path for path in paths), paths)
            self.check(case, "exact per-path provenance IDs, titles, and timestamps", evidence == pinned, evidence)
            if len(paths) != multi_expected["row_count"]:
                raise VerificationFailure(f"unexpected multi-hop row count {len(paths)}")

        self.scenario("multi-hop-outage-current-ownership", multi_hop)

        def decoy(case: dict[str, Any]) -> None:
            decoy_id = multi_expected["excluded_disconnected_department_id"]
            decoy_query = IMPACT_QUERY.replace(
                "WHERE i.status = 'open'",
                f"WHERE i.status = 'open' AND d.id = '{decoy_id}'",
            )
            decoy_rows = self.graph_rows("disconnected-decoy-path", decoy_query)
            self.check(case, "disconnected outpatient decoy contributes zero path", decoy_rows == [], decoy_rows)
            self.check(
                case,
                "only the two pinned incident impact paths are returned",
                [row["path"] for row in initial_paths] == canonical_paths and len(initial_paths) == 2,
                [row["path"] for row in initial_paths],
            )

        self.scenario("disconnected-decoy-exclusion", decoy)

        def counterfactual(case: dict[str, Any]) -> None:
            baseline = self.impact()
            update = self.graph_rows(
                "counterfactual-equipment-available",
                "MATCH (e:Equipment {id: 'CONVEYOR-C4'}) SET e.status = 'available' RETURN e.id AS id, e.status AS status",
            )
            if len(update) != 1 or update[0].get("status") != "available":
                raise VerificationFailure(f"owned equipment counterfactual did not apply: {update!r}")
            after_change = self.impact()
            self.check(case, "baseline has the pinned two impact paths", [row["path"] for row in baseline] == canonical_paths, [row["path"] for row in baseline])
            self.check(case, "available counterfactual removes all impact paths", after_change == [], after_change)
            restore = self.graph_rows(
                "restore-equipment-outage",
                "MATCH (e:Equipment {id: 'CONVEYOR-C4'}) SET e.status = 'unavailable' RETURN e.id AS id, e.status AS status",
            )
            restored = self.impact()
            self.check(
                case,
                "explicit rollback restores the exact pinned paths",
                len(restore) == 1 and restore[0].get("status") == "unavailable" and [row["path"] for row in restored] == canonical_paths,
                [row["path"] for row in restored],
            )

        self.scenario("equipment-outage-counterfactual", counterfactual)

        def stale_sop(case: dict[str, Any]) -> None:
            raw_rows = self.graph_rows("stale-and-current-sop-conflict", SOP_CONFLICT_QUERY)
            expected = expected_by_id["stale-reference-current-sop-conflict"]["expected"]
            if len(raw_rows) != 1:
                raise VerificationFailure(f"expected one explicit SOP conflict finding, got {raw_rows!r}")
            raw = raw_rows[0]
            row = {
                "version_ids": [raw["stale_version_id"], raw["current_v2_id"], raw["current_v3_id"]],
                "stale_equipment_id": raw["stale_equipment_id"],
                "current_role_claims": [raw["current_v2_role_claim"], raw["current_v3_role_claim"]],
                "conflict_status": raw["conflict_status"],
                "source_ids": [raw["stale_source_id"], raw["current_v2_source_id"], raw["current_v3_source_id"]],
                "source_titles": [raw["stale_source_title"], raw["current_v2_source_title"], raw["current_v3_source_title"]],
                "observed_at": [raw["stale_observed_at"], raw["current_v2_observed_at"], raw["current_v3_observed_at"]],
            }
            self.check(
                case,
                "stale SOP reference and both current version IDs are returned",
                row.get("version_ids") == [expected["stale_version_id"], *expected["current_conflicting_version_ids"]]
                and row.get("stale_equipment_id") == expected["stale_referenced_equipment_id"],
                row,
            )
            self.check(
                case,
                "both contradictory current role claims remain visible without a winner",
                row.get("current_role_claims") == expected["current_conflicting_role_claims"]
                and len(row.get("current_role_claims", [])) == 2
                and "winner" not in row,
                row,
            )
            self.check(case, "current SOP disagreement remains explicitly unresolved", row.get("conflict_status") == expected["conflict_status"], row)
            provenance = {
                "source_ids": row.get("source_ids"),
                "source_titles": row.get("source_titles"),
                "observed_at": row.get("observed_at"),
            }
            expected_provenance = {
                "source_ids": [expected["stale_source_id"], *expected["current_source_ids"]],
                "source_titles": [expected["stale_source_title"], *expected["current_source_titles"]],
                "observed_at": [expected["stale_observed_at"], *expected["current_observed_at"]],
            }
            self.check(case, "stale and current source provenance/timestamps match the pin", provenance == expected_provenance, provenance)
            self.check(case, "no current SOP claim is silently promoted to authoritative", row.get("conflict_status") == "unresolved" and len(row.get("current_role_claims", [])) == 2, row)

        self.scenario("stale-reference-current-sop-conflict", stale_sop)

        first_counts = {
            "nodes": self.count("node-count-before-reseed", "MATCH (n) RETURN count(n) AS count"),
            "relationships": self.count("relationship-count-before-reseed", "MATCH ()-[r]->() RETURN count(r) AS count"),
        }

        def reseed(case: dict[str, Any]) -> None:
            before_paths = self.impact()
            self.seed_all()
            after_counts = {
                "nodes": self.count("node-count-after-reseed", "MATCH (n) RETURN count(n) AS count"),
                "relationships": self.count("relationship-count-after-reseed", "MATCH ()-[r]->() RETURN count(r) AS count"),
            }
            after_paths = self.impact()
            duplicate_nodes = self.graph_rows(
                "duplicate-fixture-node-ids",
                "MATCH (n) WITH n.id AS id, count(*) AS c WHERE c > 1 RETURN id",
            )
            duplicate_edges = self.graph_rows(
                "duplicate-fixture-relationship-ids",
                "MATCH ()-[r]->() WITH r.id AS id, count(*) AS c WHERE c > 1 RETURN id",
            )
            self.check(case, "node count unchanged after second seed", after_counts["nodes"] - first_counts["nodes"] == 0, {"before": first_counts["nodes"], "after": after_counts["nodes"]})
            self.check(case, "relationship count unchanged after second seed", after_counts["relationships"] - first_counts["relationships"] == 0, {"before": first_counts["relationships"], "after": after_counts["relationships"]})
            self.check(case, "fixture nodes and relationships have no duplicate IDs", duplicate_nodes == [] and duplicate_edges == [], {"nodes": duplicate_nodes, "relationships": duplicate_edges})
            self.check(
                case,
                "pinned impact paths are unchanged after each seed",
                [row["path"] for row in before_paths] == canonical_paths
                and [row["path"] for row in after_paths] == canonical_paths,
                {"first_seed": [row["path"] for row in before_paths], "second_seed": [row["path"] for row in after_paths]},
            )

        self.scenario("seed-reseed-idempotence", reseed)

        def denied_source(case: dict[str, Any]) -> None:
            before = self.impact()
            for cypher in restricted_fixture_queries():
                self.graph_query("add-denied-source-fixture", cypher)
            after = self.impact()
            expected = expected_by_id["denied-source-nondisclosure"]["expected"]
            before_payload = [
                {"path": row["path"], "source_ids": row["source_ids"], "source_titles": row["source_titles"], "observed_at": row["observed_at"]}
                for row in before
            ]
            after_payload = [
                {"path": row["path"], "source_ids": row["source_ids"], "source_titles": row["source_titles"], "observed_at": row["observed_at"]}
                for row in after
            ]
            self.check(
                case,
                "visible path and evidence payload unchanged after restricted branch is added",
                before_payload == after_payload == expected["visible_evidence_before_denied_fixture"]
                and after_payload == expected["visible_evidence_after_denied_fixture"],
                {"before": before_payload, "after": after_payload},
            )
            serialized = json.dumps(after_payload, sort_keys=True)
            hidden_markers = [
                expected["denied_source_id"],
                expected["denied_department_id"],
                expected["denied_role_id"],
                expected["denied_source_title"],
                expected["denied_fact_text"],
                expected["denied_equipment_id"],
            ]
            self.check(
                case,
                "no denied identifiers, title, equipment ID, or distinctive fact is disclosed",
                all(marker not in serialized for marker in hidden_markers),
                {"hidden_markers_absent": all(marker not in serialized for marker in hidden_markers)},
            )
            visible_delta = len(after_payload) - len(before_payload)
            returned_count_signals = [key for row in after_payload for key in row if "count" in key.lower() or "exists" in key.lower()]
            self.check(
                case,
                "no hidden-count or existence signal is added to visible output",
                visible_delta == expected["visible_row_count_delta"] == 0 and returned_count_signals == [],
                {"visible_row_count_delta": visible_delta, "count_fields": returned_count_signals},
            )
            paths = [row["path"] for row in after]
            denied_path = ["INC-204", expected["denied_equipment_id"], "SVC-MATERIAL-TRANSFER", expected["denied_department_id"], expected["denied_role_id"]]
            self.check(
                case,
                "no derived restricted department/role path is disclosed",
                denied_path not in paths and all(expected["denied_department_id"] not in path and expected["denied_role_id"] not in path for path in paths),
                paths,
            )

        self.scenario("denied-source-nondisclosure", denied_source)

        def reconnect(case: dict[str, Any]) -> None:
            assert self.client is not None
            old_client_id = int(self.command("old CLIENT ID", "CLIENT", "ID"))
            old_token = self.client.connection_token
            self.client.close()
            self.client = RespConnection("127.0.0.1", self.port)
            new_client_id = int(self.command("new CLIENT ID", "CLIENT", "ID"))
            paths = [row["path"] for row in self.impact()]
            self.check(case, "reconnect uses a fresh client connection", old_token != self.client.connection_token and old_client_id != new_client_id, {"old_client_id": old_client_id, "new_client_id": new_client_id})
            self.check(case, "exact pinned two paths remain after reconnect", paths == canonical_paths, paths)

        self.scenario("reconnect-preserves-results", reconnect)

    def stop(self) -> None:
        if self.client is not None and self.graph_created:
            try:
                reply = text(self.client.command("GRAPH.DELETE", self.graph))
                self.cleanup["owned_graph_deleted"] = True
                self.cleanup["graph_delete_reply"] = reply
            except Exception as exc:
                self.cleanup["graph_delete_error"] = f"{type(exc).__name__}: {exc}"
            try:
                self.client.close()
            except Exception:
                pass
            self.client = None
        elif self.client is not None:
            self.cleanup["owned_graph_deleted"] = False
            self.cleanup["graph_delete_skipped"] = "no graph query created this run's graph"
            try:
                self.client.close()
            except Exception:
                pass
            self.client = None
        if self.process is not None and self.process.poll() is None:
            try:
                self.process.terminate()
                self.process.wait(timeout=5)
                self.cleanup["owned_process_stopped"] = True
            except Exception as exc:
                self.cleanup["process_stop_error"] = f"{type(exc).__name__}: {exc}"
                try:
                    self.process.kill()
                    self.process.wait(timeout=5)
                    self.cleanup["owned_process_stopped"] = True
                except Exception as kill_exc:
                    self.cleanup["process_kill_error"] = f"{type(kill_exc).__name__}: {kill_exc}"
        elif self.process is not None:
            self.cleanup["owned_process_stopped"] = True

    def report(self, status: str, error: str | None = None) -> dict[str, Any]:
        memory = read_process_memory(self.process.pid) if self.process and self.process.poll() is None else {}
        peaks = [sample.get("high_water_rss_kib", 0) for sample in self.resource_samples]
        if memory.get("high_water_rss_kib"):
            peaks.append(memory["high_water_rss_kib"])
        report = {
            "schema_version": 1,
            "verifier_version": VERSION,
            "status": status,
            "error": error,
            "created_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "host": host_facts(),
            "database": self.database,
            "process_resource_evidence": {
                "owned_process_only": True,
                "rss_observation_source": "/proc/<owned-pid>/status VmRSS/VmHWM",
                "sample_count": len(self.resource_samples),
                "samples": self.resource_samples,
                "observed_high_water_rss_kib": max(peaks, default=None),
                "observed_high_water_rss_bytes": max(peaks, default=0) * 1024 if peaks else None,
            },
            "graph_cleanup": self.cleanup,
            "scenario_results": self.scenarios,
            "assertion_count": self.assertion_count,
            "assertion_log": self.assertion_log,
            "graph_query_count": self.query_count,
            "query_timings_ms": self.timings,
            "failures": self.failures,
        }
        if self.server_log_path:
            report["database"]["server_log"] = str(self.server_log_path)
        return report


def validate_morning_status() -> list[str]:
    if not MORNING_STATUS.is_file():
        raise VerificationFailure("MORNING-STATUS.md is missing")
    content = MORNING_STATUS.read_text(encoding="utf-8")
    required = [
        "python3 scripts/verify_expected_results.py --expected tests/expected-results.json --matrix tests/adversarial-matrix.json --require-six-scenarios",
        "python3 scripts/verify_falkordb_live.py --require-live --fail-on-zero --fresh-owned-graph --db-port 18732 --empty-reseed-idempotence-reconnect --report /workspace/evidence/wardatlas/falkordb-scenarios.json --verify-morning-status",
        "/workspace/evidence/wardatlas/falkordb-scenarios.json",
    ]
    missing = [item for item in required if item not in content]
    setup_order = required[:2]
    positions = [content.find(item) for item in setup_order]
    if any(position < 0 for position in positions) or positions != sorted(positions):
        missing.append("MORNING-STATUS.md must run the pinned oracle before the live verifier")
    if "automatically bootstraps" not in content.casefold():
        missing.append("MORNING-STATUS.md must explain clean-checkout automatic module bootstrap")
    if not README_PATH.is_file():
        missing.append("README.md is missing the clean setup instructions")
    else:
        readme = README_PATH.read_text(encoding="utf-8")
        readme_steps = [
            "automatically bootstraps",
            "python3 scripts/verify_expected_results.py",
            "python3 scripts/verify_falkordb_live.py --require-live",
        ]
        readme_positions = [readme.find(item) for item in readme_steps]
        if any(position < 0 for position in readme_positions) or readme_positions != sorted(readme_positions):
            missing.append("README.md must document automatic bootstrap, oracle, and live verifier in that order")
    concepts = [
        "Pentium Silver N5030",
        "AVX",
        "RSS",
        "Deferred",
        "Next",
        "Redis 8.0.5",
        "60002",
        "27 assertions",
    ]
    missing.extend(item for item in concepts if item.casefold() not in content.casefold())
    if missing:
        raise VerificationFailure(f"MORNING-STATUS.md omits required run/evidence/blocker/next details: {missing}")
    return required


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--require-live", action="store_true", required=True)
    parser.add_argument("--fail-on-zero", action="store_true", required=True)
    parser.add_argument("--fresh-owned-graph", action="store_true", required=True)
    parser.add_argument("--db-port", type=int, required=True)
    parser.add_argument("--empty-reseed-idempotence-reconnect", action="store_true", required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--verify-morning-status", action="store_true", required=True)
    parser.add_argument("--expected", type=Path, default=ROOT / "tests/expected-results.json")
    parser.add_argument("--matrix", type=Path, default=ROOT / "tests/adversarial-matrix.json")
    args = parser.parse_args()

    verifier: LiveVerifier | None = None
    error: str | None = None
    status = "FAIL"
    try:
        expected = json.loads(args.expected.read_text(encoding="utf-8"))
        matrix = json.loads(args.matrix.read_text(encoding="utf-8"))
        if expected.get("product") != "WardAtlas" or matrix.get("oracle_file") != "tests/expected-results.json":
            raise VerificationFailure("expected-results/matrix does not describe the pinned WardAtlas verifier oracle")
        if len(expected.get("scenarios", [])) != 8 or len(matrix.get("cases", [])) != 8:
            raise VerificationFailure("the live verifier requires all eight pinned scenarios and adversarial cases")
        verifier = LiveVerifier(args.db_port, expected, matrix)
        verifier.run()
        if args.fail_on_zero and verifier.assertion_count <= 0:
            raise VerificationFailure("--fail-on-zero: verifier completed with zero assertions")
        actual_ids = [row["id"] for row in verifier.scenarios]
        expected_ids = [row["id"] for row in expected["scenarios"]]
        if len(actual_ids) != len(set(actual_ids)) or set(actual_ids) != set(expected_ids):
            raise VerificationFailure("live scenario results do not cover each pinned scenario exactly once")
        if any(row["status"] != "pass" or row["assertions"] <= 0 for row in verifier.scenarios):
            raise VerificationFailure("every scenario must pass with nonzero assertions")
        if args.verify_morning_status:
            validate_morning_status()
        status = "PASS"
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
        if verifier:
            verifier.failures.append(error)
    finally:
        if verifier:
            verifier.stop()
            if status == "PASS" and not (
                verifier.cleanup.get("owned_graph_deleted") is True
                and verifier.cleanup.get("owned_process_stopped") is True
            ):
                error = "VerificationFailure: owned graph/process cleanup was not confirmed"
                verifier.failures.append(error)
                status = "FAIL"
            report_data = verifier.report(status, error)
        else:
            report_data = {
                "schema_version": 1,
                "verifier_version": VERSION,
                "status": status,
                "error": error,
                "created_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "host": host_facts(),
                "scenario_results": [],
                "assertion_count": 0,
                "failures": [error] if error else [],
            }
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report_data, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    if status != "PASS":
        print(f"FAIL: {error}; report: {args.report}", file=sys.stderr)
        return 1
    scenario_count = len(verifier.scenarios) if verifier else 0
    print(
        f"PASS: live FalkorDB; {scenario_count} scenarios, {verifier.assertion_count} assertions, "
        f"{verifier.query_count} graph queries; owned graph/process cleaned; report: {args.report}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

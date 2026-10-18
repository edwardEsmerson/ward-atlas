#!/usr/bin/env python3
"""Nine-group checks on owned actual FalkorDB; reports under WORKSHOP_RUN_DIR.

Python, redis-server and Node are runtime prerequisites. The accepted installer
bootstraps the digest-pinned official FalkorDB module project-locally. No fixed
ports, Docker daemon, credentials, warmed graph or shared state are required.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import re
import shutil
import socket
import subprocess
import sys
import time
import uuid
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from urllib.error import HTTPError

from verify_falkordb_live import LiveVerifier, RespConnection, VerificationFailure, explicit_bind_preflight
from wardatlas_app import WardAtlasService
from wardatlas_clock_atlas import atlas_from_query, build_atlas
from wardatlas_fixtures import seed_queries
from wardatlas_shift_board import restricted_shift_board_queries, seed_shift_board_queries

ROOT = Path(__file__).resolve().parents[1]
CONTRACT = ROOT / "contracts/clock-atlas"
PINS = {
    "expected-results.json": "c9ed3bab279295063996ad58c515cc1ffd800c997178c8c4bcf7a1c1992223ce",
    "adversarial-matrix.json": "368f4c0b14ca15678c694761e651af3f8ab7e32904413e2f39f11eeade494e2e",
}
HIDDEN = ("SRC-RESTRICTED-DOCK-99", "Restricted Dock Continuity Annex 99",
          "Restricted-only note: logistics bay status unresolved.", "EQ-RESTRICTED-DOCK-99",
          "DEPT-RESTRICTED-DOCK-99", "ROLE-RESTRICTED-DOCK-OWNER", "SRC-RESTRICTED-92",
          "Restricted Continuity Annex 92", "Restricted-only instruction: contact the secure lift coordinator.")
FUTURE = ("SRC-DOCK-ROSTER-R2", "Revision 2", "ROLE-DOCK-SWING", "20:18:00Z", "HND-DOCK-2020", "TKT-DOCK-88")


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def request(url, method="GET", payload=None):
    data = None if payload is None else json.dumps(payload).encode()
    req = Request(url, data=data, method=method, headers={"Content-Type": "application/json"})
    try:
        with urlopen(req, timeout=15) as response:
            status, body = response.status, response.read()
    except HTTPError as exc:
        status, body = exc.code, exc.read()
    try:
        return status, json.loads(body)
    except ValueError:
        return status, body.decode("utf-8", "replace")


def free_ports():
    """Bind, never connect to/probe an existing service; launch rechecks."""
    base, span = os.environ.get("WORKSHOP_PORT_BASE"), os.environ.get("WORKSHOP_PORT_SPAN")
    candidates = list(range(int(base), int(base) + int(span))) if base and span else [0] * 100
    random.SystemRandom().shuffle(candidates)
    ports = []
    for port in candidates:
        if port and not 1024 <= port <= 65535:
            continue
        try:
            with socket.socket() as sock:
                sock.bind(("127.0.0.1", port))
                found = sock.getsockname()[1]
                if found not in ports:
                    ports.append(found)
        except OSError:
            continue
        if len(ports) == 2:
            return ports
    raise VerificationFailure("No two free high loopback ports in the allocated range")


def load_contract():
    documents = {}
    for name, digest in PINS.items():
        raw = (CONTRACT / name).read_bytes()
        if hashlib.sha256(raw).hexdigest() != digest:
            raise VerificationFailure(f"Pre-implementation clock atlas oracle changed: {name}")
        documents[name] = json.loads(raw)
    expected, matrix = documents["expected-results.json"], documents["adversarial-matrix.json"]
    for pin in expected["pinned_sources"]:
        if hashlib.sha256((ROOT / pin["file"]).read_bytes()).hexdigest() != pin["sha256"]:
            raise VerificationFailure(f"Accepted source oracle changed: {pin['file']}")
    if {row["id"] for row in matrix["groups"]} != set(expected["groups"]):
        raise VerificationFailure("Matrix omits an expected group")
    for row in matrix["groups"]:
        if row["expected_pointer"] != f"/groups/{row['id']}" or set(row["assertions"]) != set(expected["groups"][row["id"]]):
            raise VerificationFailure("Incomplete assertion mapping")
    if len(matrix["groups"]) < 8 or sum(len(row["assertions"]) for row in matrix["groups"]) < 40:
        raise VerificationFailure("Zero/insufficient live assertion coverage")
    return expected, matrix


def browser_environment(node, evidence_dir):
    """Reuse approved tools, otherwise bootstrap pinned browser bits locally.

    No install scripts, OS dependency installer, global npm install or credentials.
    Python/Node/redis-server remain the documented runtime prerequisites.
    """
    env = os.environ.copy()
    tools_module = Path("/tools/node/lib/node_modules/playwright-core")
    tools_browsers = Path("/tools/browsers")
    if tools_module.is_dir() and any(tools_browsers.glob("chromium_headless_shell-*/*/chrome-headless-shell")):
        env.setdefault("PLAYWRIGHT_BROWSERS_PATH", str(tools_browsers))
        return env
    npm = shutil.which("npm")
    if not npm:
        raise VerificationFailure("Node/npm required for project-local browser bootstrap")
    local = ROOT / ".local/clock-atlas-browser"
    local.mkdir(parents=True, exist_ok=True)
    core = local / "node_modules/playwright-core"
    package = core / "package.json"
    log_path = evidence_dir / "clock-atlas-browser-setup.log"
    with log_path.open("w") as log:
        if not package.is_file() or json.loads(package.read_text()).get("version") != "1.58.0":
            subprocess.run([npm, "install", "--prefix", str(local), "--no-save", "--no-audit", "--no-fund",
                            "--ignore-scripts", "playwright-core@1.58.0"], check=True, timeout=180,
                           stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT)
        env["PLAYWRIGHT_BROWSERS_PATH"] = str(local / "browsers")
        env["CLOCK_ATLAS_PLAYWRIGHT_MODULE"] = str(core)
        subprocess.run([node, str(core / "cli.js"), "install", "chromium", "--only-shell"],
                       env=env, check=True, timeout=240, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT)
    return env


class ClockAtlasVerifier:
    def __init__(self, args, expected, matrix):
        self.args, self.expected, self.matrix = args, expected, matrix
        lane = re.sub(r"[^A-Za-z0-9_-]", "_", os.environ.get("WORKSHOP_LANE", "local"))
        self.run_id = f"atlas_lane{lane}_{uuid.uuid4().hex}"
        self.db = LiveVerifier(args.db_port, {}, {})
        self.db.run_id = self.run_id
        self.db.graph = f"wardatlas_test_{self.run_id}"
        self.state = ROOT / ".local/clock-atlas-runs" / self.run_id
        self.state.mkdir(parents=True, exist_ok=False)
        for name in ("evidence-watch.json", "confirmed-actions.json"):
            (self.state / name).write_text('{"schema_version":1,"items":[]}\n')
        self.service = WardAtlasService(args.db_port, self.db.graph, self.state)
        self.url = f"http://127.0.0.1:{args.app_port}"
        self.app = self.app_log = None
        self.checks, self.mapped, self.observed = [], [], {}
        self.browser_report = {}
        self.cleanup = {"app_stopped": False, "graph_deleted": False, "db_stopped": False,
                        "db_runtime_removed": False, "state_removed": False}

    def check(self, name, condition, detail=None):
        self.checks.append({"name": name, "passed": bool(condition), "detail": detail})
        if not condition:
            raise VerificationFailure(f"{name}: {detail!r}")

    def group(self, name, observed):
        self.observed[name] = observed
        row = next(row for row in self.matrix["groups"] if row["id"] == name)
        self.mapped = [item for item in self.mapped if item["group"] != name]
        for key in row["assertions"]:
            actual, expected = observed[key], self.expected["groups"][name][key]
            passed = type(actual) is type(expected) and actual == expected
            self.mapped.append({"group": name, "assertion": key, "expected": expected, "observed": actual, "passed": passed})
            if not passed:
                raise VerificationFailure(f"{name}/{key}: expected {expected!r}, got {actual!r}")

    def start_app(self):
        explicit_bind_preflight(self.args.app_port)
        log_path = self.args.evidence_dir / "clock-atlas-app.log"
        # A rerun must never accept an earlier run's readiness line. The old
        # process has been stopped before this method may be called again.
        log_path.write_text("")
        self.app_log = log_path.open("ab")
        self.app = subprocess.Popen([sys.executable, str(ROOT / "scripts/wardatlas_app.py"),
                                    "--db-port", str(self.args.db_port), "--app-port", str(self.args.app_port),
                                    "--graph-name", self.db.graph, "--state-dir", str(self.state)],
                                   cwd=ROOT, stdin=subprocess.DEVNULL, stdout=self.app_log,
                                   stderr=subprocess.STDOUT, start_new_session=True)
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            if self.app.poll() is not None:
                raise VerificationFailure(f"App exited; see {log_path}")
            if f"WARDATLAS_READY http://127.0.0.1:{self.args.app_port}" in log_path.read_text():
                status, body = request(self.url + "/api/health")
                self.check("App uses actual FalkorDB 6.0.2", status == 200 and body.get("database") == "FalkorDB" and body.get("module_version") == 60002, body)
                return
            time.sleep(.05)
        raise VerificationFailure("Owned app not ready")

    def stop_app(self):
        if self.app is not None:
            if self.app.poll() is None:
                self.app.terminate()
                try:
                    self.app.wait(timeout=6)
                except subprocess.TimeoutExpired:
                    self.app.kill()
                    self.app.wait(timeout=6)
            self.cleanup["app_stopped"] = self.app.poll() is not None
            self.app = None
        if self.app_log:
            self.app_log.close()
            self.app_log = None

    def atlas(self, query=""):
        status, body = request(self.url + "/api/clock-atlas?" + query)
        self.check("Atlas API success", status == 200 and isinstance(body, dict), {"status": status, "body": body})
        return body

    def mutate(self, query):
        self.db.graph_query("owned-atlas-adversary", query)

    def seed(self):
        for query in [*seed_queries(), *seed_shift_board_queries()]:
            self.mutate(query)

    def snapshot(self):
        return {
            "nodes": self.db.graph_rows("atlas-node-properties", "MATCH (n) RETURN n.id AS id, labels(n) AS labels, properties(n) AS props ORDER BY id"),
            "edges": self.db.graph_rows("atlas-edge-properties", "MATCH (a)-[r]->(b) RETURN a.id AS a, b.id AS b, type(r) AS kind, properties(r) AS props ORDER BY r.id"),
        }

    def state_bytes(self):
        return {str(path.relative_to(self.state)): path.read_bytes() for path in sorted(self.state.rglob("*")) if path.is_file()}

    def cold(self):
        empty = self.atlas()
        cells = empty["cells"]
        self.check("Cold graph contains zero nodes", self.db.graph_rows("cold", "MATCH (n) RETURN count(n) AS count") == [{"count": 0}])
        self.group("cold-graph", {
            "cell_count": len(cells), "all_roles_unknown": all(c["role_status"] == "unknown" and c["role_id"] is None for c in cells),
            "all_paths_empty": all(c["role_path"] == [] for c in cells), "all_sources_empty": all(c["source_versions"] == [] for c in cells),
            "all_reviews_empty": all(c["review_candidates"] == [] for c in cells), "selected_unknown": empty["selected_cell"]["role_status"] == "unknown",
        })
        (self.args.evidence_dir / "cold-api.json").write_text(json.dumps(empty, indent=2) + "\n")

    def four_cells(self):
        self.baseline = self.atlas()
        cells = self.baseline["cells"]
        self.check("The lexical decoy is present as a live adversary", self.db.graph_rows(
            "atlas-decoy-exists", "MATCH (n:Handover {id:'HND-DOCK-LEXICAL-ONLY'}) RETURN n.id AS id"
        ) == [{"id": "HND-DOCK-LEXICAL-ONLY"}])
        calls = []
        def real_service(**axes):
            calls.append(axes)
            return self.service.shift_board(**axes)
        direct = build_atlas(real_service)
        self.check("Four callbacks execute real queries and agree with API", direct == self.baseline, calls)
        for actual, wanted in zip(cells, self.expected["cells"]):
            self.check(f"Exact pinned fields for {wanted['id']}", {key: actual[key] for key in wanted} == wanted, actual)
        self.group("four-cells", {
            "cell_count": len(cells), "independent_axis_pairs": len({(c["dock_at"], c["known_at"]) for c in calls}),
            "early_no_r2_hint": all(not any(marker in canonical(c) for marker in FUTURE) for c in (cells[0], cells[2])),
            "crossed_unknown": cells[2]["role_status"] == "unknown" and cells[2]["role_path"] == [] and cells[2]["role_id"] is None,
            "exact_paths": [c["role_path"] for c in cells] == [c["role_path"] for c in self.expected["cells"]],
            "exact_sources": [c["source_versions"] for c in cells] == [c["source_versions"] for c in self.expected["cells"]],
            "pending_not_invalidated": all(cells[i]["review_candidates"] == self.expected["cells"][i]["review_candidates"] and cells[i]["candidate_disposition"] == "pending review; not declared-invalid artifacts" and cells[i]["source_versions"][1]["status"] == "recorded/pending" for i in (1, 3)),
            "lexical_decoy_excluded": "HND-DOCK-LEXICAL-ONLY" not in canonical(self.baseline),
        })
        (self.args.evidence_dir / "four-cells-api.json").write_text(json.dumps(self.baseline, indent=2) + "\n")

    def omissions(self):
        mutations = {
            "supersedes": "MATCH ()-[r:SUPERSEDES {id:'REL-DOCK-R2-SUPERSEDES-R1'}]->() DELETE r",
            "ownership": "MATCH ()-[r:OWNED_BY {id:'REL-DOCK-OWNER-R2'}]->() DELETE r",
            "role_evidence": "MATCH ()-[r:EVIDENCED_BY {id:'REL-DOCK-SWING-R2'}]->() DELETE r",
            "prefix": "MATCH ()-[r:SUPPORTS {id:'REL-DOCK-CART-SVC'}]->() DELETE r",
        }
        changed = {}
        for name, query in mutations.items():
            self.mutate(query)
            changed[name] = self.atlas()
            self.seed()
            self.check(f"Reseed restores exact projection after {name} omission", self.atlas() == self.baseline)
        self.group("edge-omissions", {
            "supersedes_reviews_empty": all(c["review_candidates"] == [] for c in changed["supersedes"]["cells"]),
            "supersedes_linkage_unknown": all(c["linkage_status"] == "unknown" for c in changed["supersedes"]["cells"]),
            "ownership_path_empty": changed["ownership"]["cells"][3]["role_path"] == [],
            "role_evidence_path_empty": changed["role_evidence"]["cells"][3]["role_path"] == [] and changed["role_evidence"]["cells"][3]["role_evidence"] == [],
            "prefix_path_empty": all(c["role_path"] == [] for c in changed["prefix"]["cells"]),
            "early_cell_unchanged": all(changed[name]["cells"][0] == self.baseline["cells"][0] for name in ("supersedes", "ownership", "role_evidence")),
        })
        self.mutate("MATCH (src:Evidence {id:'SRC-DOCK-ROSTER-R2'}) DETACH DELETE src")
        missing = self.atlas()
        self.seed()
        restored = self.atlas()
        self.group("source-omission", {
            "r2_metadata_absent": all("SRC-DOCK-ROSTER-R2" not in canonical(c) and "Revision 2" not in canonical(c) and "20:18:00Z" not in canonical(c) for c in missing["cells"]),
            "swing_path_empty": missing["cells"][3]["role_path"] == [] and missing["cells"][3]["role_id"] is None,
            "reviews_empty": all(c["review_candidates"] == [] for c in missing["cells"]),
            "early_cell_unchanged": missing["cells"][0] == self.baseline["cells"][0], "reseed_restores_exact": restored == self.baseline,
        })
        (self.args.evidence_dir / "omission-api.json").write_text(json.dumps({"edges": changed, "source": missing}, indent=2) + "\n")

    def validation(self):
        params = {"effective_at": "2026-10-14T22:29:00+02:00", "known_at": "2026-10-14T22:17:59+02:00"}
        observed = {"offset_equal": self.atlas(urlencode(params)) == self.baseline}
        queries = {
            "malformed_effective_rejected": "effective_at=bad&known_at=2026-10-14T20:19:00Z",
            "naive_known_rejected": "effective_at=2026-10-14T20:29:00Z&known_at=2026-10-14T20:19:00",
            "duplicate_effective_rejected": "effective_at=2026-10-14T20:29:00Z&effective_at=2026-10-14T20:29:00Z&known_at=2026-10-14T20:19:00Z",
            "duplicate_known_rejected": "effective_at=2026-10-14T20:29:00Z&known_at=2026-10-14T20:19:00Z&known_at=2026-10-14T20:19:00Z",
            "duplicate_scope_rejected": "scope=operations&scope=operations",
            "unsupported_pair_rejected": "effective_at=2026-10-14T20:31:00Z&known_at=2026-10-14T20:19:00Z",
        }
        calls, rejected = [], []
        for key, query in queries.items():
            observed[key] = request(self.url + "/api/clock-atlas?" + query)[0] == 400
            try:
                atlas_from_query(lambda **axes: calls.append(axes), query)
            except ValueError:
                rejected.append(key)
        observed["invalid_before_queries"] = len(rejected) == len(queries) and calls == []
        self.check("Restricted scope denied generically", request(self.url + "/api/clock-atlas?scope=restricted") == (403, {"error": "This view is not available."}))
        self.group("time-validation", observed)

    def browser(self):
        node = shutil.which("node")
        self.check("Node runtime available", node is not None)
        fixture_path = self.args.evidence_dir / "restricted-fixtures.json"
        # The original accepted denied dock source lacks an observation time.
        # Add one only to this owned adversarial graph: otherwise the knowledge
        # cutoff would hide it even if access filtering were broken.
        fixture_path.write_text(json.dumps([*restricted_shift_board_queries(),
            "MATCH (src:Evidence {id:'SRC-RESTRICTED-DOCK-99'}) SET src.observed_at='2026-10-14T19:00:00Z'"
        ]))
        command = [node, str(ROOT / "scripts/clock_atlas_browser_assertions.cjs"), self.url,
                   str(self.args.db_port), self.db.graph, str(self.args.evidence_dir), str(fixture_path)]
        report_path = self.args.evidence_dir / "clock-atlas-browser.json"
        report_path.write_text(json.dumps({"status": "NOT_RUN", "run_id": self.run_id}) + "\n")
        completed = subprocess.run(command, cwd=ROOT, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                   stderr=subprocess.STDOUT, text=True, timeout=240,
                                   env=browser_environment(node, self.args.evidence_dir))
        (self.args.evidence_dir / "clock-atlas-browser.log").write_text(completed.stdout)
        if report_path.exists():
            self.browser_report = json.loads(report_path.read_text())
        self.check("Desktop/mobile live browser completed", completed.returncode == 0 and self.browser_report.get("status") == "PASS", completed.stdout[-6000:])
        self.group("keyboard-mobile", self.browser_report["groups"]["keyboard-mobile"])
        self.group("request-isolation", self.browser_report["groups"]["request-isolation"])
        for name in ("clock-atlas-desktop.png", "clock-atlas-mobile.png"):
            self.check(f"Screenshot {name}", (self.args.evidence_dir / name).stat().st_size > 0)

    def acl(self):
        # Browser injects the accepted fixture while capturing all four
        # selections' full visible + accessible desktop/mobile views.
        after = self.atlas()
        connected = self.db.graph_rows("restricted-connected", "MATCH (:Incident {id:'INC-207'})-[:AFFECTS]->(:Equipment {id:'EQ-RESTRICTED-DOCK-99'})-[:SUPPORTS]->(:Service)-[:SERVES]->(:Department)-[owns:OWNED_BY]->(:Role)-[:EVIDENCED_BY]->(src:Evidence {id:'SRC-RESTRICTED-DOCK-99'}) WHERE owns.source_id=src.id AND owns.effective_from <= '2026-10-14T20:29:00Z' AND owns.effective_to > '2026-10-14T20:30:00Z' AND src.observed_at <= '2026-10-14T20:17:59Z' RETURN count(*) AS count")
        self.group("restricted-invariance", {
            "fixture_connected": connected == [{"count": 1}], "full_api_equal": after == self.baseline,
            "desktop_view_equal": self.browser_report["acl"]["desktop_view_equal"],
            "mobile_view_equal": self.browser_report["acl"]["mobile_view_equal"],
            "no_hidden_markers": self.browser_report["acl"]["no_hidden_markers"] and not any(marker.casefold() in canonical(after).casefold() for marker in HIDDEN),
        })

    def run(self):
        self.db.launch()
        self.db.graph_rows("create-owned-cold-graph", "MATCH (n) RETURN n.id AS id LIMIT 1")
        self.start_app()
        self.cold()
        self.seed()
        self.four_cells()
        self.omissions()
        self.validation()
        seeded = self.snapshot()
        self.seed()
        twice = self.snapshot()
        state_before = self.state_bytes()
        before_browser = self.snapshot()
        self.browser()
        self.acl()
        # Browser fixture injection is test setup, not an atlas mutation.
        after_fixture = self.snapshot()
        for _ in range(2):
            self.atlas()
        read_only = self.snapshot() == after_fixture
        old_connection = self.db.client.connection_token
        self.db.client.close()
        self.db.client = RespConnection("127.0.0.1", self.args.db_port)
        fresh_client_equal = self.db.client.connection_token != old_connection and self.snapshot() == after_fixture and build_atlas(self.service.shift_board) == self.baseline
        self.stop_app()
        self.start_app()
        restarted_equal = self.atlas() == self.baseline
        self.lifecycle = {
            "idempotent_nodes": twice["nodes"] == seeded["nodes"], "idempotent_edges": twice["edges"] == seeded["edges"],
            "fresh_client_equal": fresh_client_equal, "restarted_app_equal": restarted_equal,
            "graph_read_only": read_only and self.snapshot() == after_fixture and self.browser_report.get("graph_read_only") is True,
            "state_bytes_unchanged": self.state_bytes() == state_before, "owned_cleanup": False,
        }
        self.check("Restricted fixture actually adds graph records", before_browser != after_fixture)

    def stop(self):
        self.stop_app()
        self.db.stop()
        self.cleanup["graph_deleted"] = self.db.cleanup.get("owned_graph_deleted") is True
        self.cleanup["db_stopped"] = self.db.cleanup.get("owned_process_stopped") is True
        if self.db.server_log_path:
            runtime = self.db.server_log_path.parent
            if runtime.name == self.run_id and runtime.parent.resolve() == (ROOT / ".local/falkordb-runs").resolve():
                shutil.copyfile(self.db.server_log_path, self.args.evidence_dir / "clock-atlas-db.log")
                shutil.rmtree(runtime)
                self.cleanup["db_runtime_removed"] = not runtime.exists()
        if self.state.name == self.run_id and self.state.parent.resolve() == (ROOT / ".local/clock-atlas-runs").resolve():
            shutil.rmtree(self.state)
            self.cleanup["state_removed"] = not self.state.exists()

    def report(self, status, error):
        return {"status": status, "error": error, "verifier": "verify_clock_atlas.py", "run_id": self.run_id,
                "graph": self.db.graph, "ports": {"app": self.args.app_port, "db": self.args.db_port},
                "oracle_pins": PINS, "pinned_sources": self.expected["pinned_sources"],
                "mapped_assertion_count": len(self.mapped), "mapped_assertions": self.mapped,
                "group_count": len(self.observed), "observed": self.observed, "checks": self.checks,
                "browser": self.browser_report, "cleanup": self.cleanup,
                "database": self.db.database, "resources": self.db.resource_samples,
                "verifier_graph_query_count": self.db.query_count,
                "source_sha256": {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in (
                    "scripts/wardatlas_clock_atlas.py", "scripts/ui/clock_atlas.html", "scripts/wardatlas_app.py",
                    "scripts/verify_clock_atlas.py", "scripts/clock_atlas_browser_assertions.cjs", "tests/test_clock_atlas.py",
                ) if (ROOT / name).is_file()},
                "scope": "synthetic operations, read-only atlas; only the verifier mutates its owned graph"}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for flag in ("--all", "--require-live-falkordb", "--desktop-mobile", "--fail-on-zero"):
        parser.add_argument(flag, action="store_true", required=True)
    parser.add_argument("--db-port", type=int)
    parser.add_argument("--app-port", type=int)
    parser.add_argument("--evidence-dir", type=Path)
    args = parser.parse_args(argv)
    args.evidence_dir = (args.evidence_dir or Path(os.environ.get("WORKSHOP_RUN_DIR", ROOT / ".local/clock-atlas-evidence"))).resolve()
    args.evidence_dir.mkdir(parents=True, exist_ok=True)
    verifier, status, error = None, "FAIL", None
    try:
        expected, matrix = load_contract()
        ports = free_ports()
        args.db_port, args.app_port = args.db_port or ports[0], args.app_port or ports[1]
        if args.db_port == args.app_port or any(not 1024 <= p <= 65535 for p in (args.db_port, args.app_port)):
            raise VerificationFailure("Select distinct high loopback ports")
        verifier = ClockAtlasVerifier(args, expected, matrix)
        verifier.run()
        status = "PASS"
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
    finally:
        if verifier:
            try:
                verifier.stop()
                if status == "PASS":
                    verifier.lifecycle["owned_cleanup"] = all(verifier.cleanup.values())
                    verifier.group("lifecycle", verifier.lifecycle)
                    wanted = sum(len(row["assertions"]) for row in verifier.matrix["groups"])
                    verifier.check("All pinned groups and nonzero mapped assertions executed", len(verifier.mapped) == wanted and len(verifier.observed) == len(verifier.matrix["groups"]) and all(row["passed"] for row in verifier.mapped))
            except Exception as exc:
                status, error = "FAIL", f"cleanup/finalization: {type(exc).__name__}: {exc}"
            report = verifier.report(status, error)
        else:
            report = {"status": status, "error": error}
        report_path = args.evidence_dir / "clock-atlas-live.json"
        report_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    detail = error or f"{report.get('group_count')} groups / {report.get('mapped_assertion_count')} mapped assertions; actual FalkorDB + desktop/mobile"
    print(f"{status}: {detail}; {report_path}")
    return 0 if status == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())

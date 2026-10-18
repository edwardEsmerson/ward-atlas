#!/usr/bin/env python3
"""Verify the documented app-only command in a clean, committed source snapshot.

Only this verifier starts a database: a digest-pinned, loopback FalkorDB plus a
separate plain Redis negative control. Neither is a product runtime dependency.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import signal
import socket
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
import uuid
from pathlib import Path
from urllib.request import ProxyHandler, Request, build_opener

from verify_falkordb_live import LiveVerifier, RespConnection, VerificationFailure, query_result, result_rows, text

ROOT = Path(__file__).resolve().parents[1]
PINS = {
    "contracts/existing-server/expected-results.json": "4b4a8cb2736de2fae8d1f671d315fed41bee3c4ab8214def7d1ee7a2bfeac6a5",
    "contracts/existing-server/adversarial-matrix.json": "172c965d27c8327658d7bb9e811b71f9237e3b4dede494282f3b9669677f1d43",
    "tests/expected-results.json": "c77e00e47c4f1e26d565c3d9583653838d5cc88695c4e6aa19ac583205d92b87",
}


def free_port():
    base = int(os.environ.get("WORKSHOP_PORT_BASE", "0"))
    span = int(os.environ.get("WORKSHOP_PORT_SPAN", "0"))
    if base and span:
        start = uuid.uuid4().int % span
        for index in range(span):
            with socket.socket() as sock:
                try:
                    sock.bind(("127.0.0.1", base + (start + index) % span))
                    return sock.getsockname()[1]
                except OSError:
                    continue
        raise VerificationFailure("no free lane port")
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def packet():
    for relative, digest in PINS.items():
        if hashlib.sha256((ROOT / relative).read_bytes()).hexdigest() != digest:
            raise VerificationFailure("frozen expectation changed: " + relative)
    expected = json.loads((ROOT / "contracts/existing-server/expected-results.json").read_text())
    matrix = json.loads((ROOT / "contracts/existing-server/adversarial-matrix.json").read_text())
    if len(expected["groups"]) < 8 or [g["id"] for g in expected["groups"]] != [g["id"] for g in matrix["groups"]]:
        raise VerificationFailure("missing/mismatched lifecycle groups")
    return expected


# Browser assertions are self-contained and read-only. One browser at a time.
BROWSER = r"""
const fs=require('node:fs');
let pw;
for(const name of [process.env.PLAYWRIGHT_MODULE,'playwright','playwright-core','/tools/node/lib/node_modules/playwright-core'].filter(Boolean)) {
  try { pw=require(name); break; } catch(e) { if(e.code!=='MODULE_NOT_FOUND') throw e; }
}
if(!pw) throw Error('Install project-local Playwright and Chromium; see judge guide');
const [url,dir,run,oraclePath]=process.argv.slice(1);
const oracle=JSON.parse(fs.readFileSync(oraclePath));
const expected=oracle.expected_paths;
const results={};
(async()=>{
  const browser=await pw.chromium.launch({args:['--no-sandbox','--disable-dev-shm-usage']});
  for(const signal of ['SIGINT','SIGTERM']) process.once(signal,async()=>{await browser.close();process.exit(130);});
  try {
    for(const [name,width,height] of [['desktop',1440,1000],['mobile',390,844]]) {
      const context=await browser.newContext({viewport:{width,height},deviceScaleFactor:1});
      const page=await context.newPage(); const errors=[]; const foreign=[]; const failures=[];
      page.on('pageerror',e=>errors.push(String(e)));
      page.on('response',r=>{if(r.status()>=400) failures.push([r.url(),r.status()]);});
      await page.route('**/*',route=>{
        if(new URL(route.request().url()).origin!==new URL(url).origin) {foreign.push(route.request().url());return route.abort();}
        return route.continue();
      });
      await page.goto(url,{waitUntil:'networkidle'});
      await page.waitForFunction(()=>document.querySelector('#db-status').textContent.includes('Live FalkorDB'));
      await page.waitForFunction(()=>document.querySelector('#impact-count').textContent==='2 impact paths');
      const api=await page.evaluate(async()=>{const r=await fetch('/api/briefing?scope=operations');if(!r.ok)throw Error('API failed');return r.json();});
      const paths=api.impact_paths.map(p=>p.path);
      if(JSON.stringify(paths)!==JSON.stringify(expected)) throw Error(name+' independent path oracle mismatch');
      const rendered=await page.locator('#paths').innerText();
      for(const path of expected) for(const id of path) if(!rendered.includes(id)) throw Error(name+' missing DOM path '+id);
      const title=await page.title(); const body=await page.locator('body').innerText();
      const overflow=await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth+1);
      if(!title.includes('WardAtlas')||!body.includes('INC-204')||overflow||errors.length||foreign.length||failures.length) throw Error(JSON.stringify({title,overflow,errors,foreign,failures}));
      const screenshot=dir+'/'+run+'-'+name+'.png';
      await page.screenshot({path:screenshot,fullPage:true});
      results[name]={viewport:[width,height],paths,rendered,title,no_horizontal_overflow:!overflow,page_errors:errors,foreign_requests:foreign,failed_responses:failures,screenshot};
      await context.close();
    }
    fs.writeFileSync(dir+'/'+run+'-browser.json',JSON.stringify({status:'passed',results},null,2)+'\n');
  } finally {await browser.close();}
})().catch(e=>{console.error(e.stack);process.exitCode=1;});
"""


class Check:
    def __init__(self, args):
        self.args = args
        self.expected = packet()
        self.oracle = json.loads((ROOT / "tests/expected-results.json").read_text())
        self.db = LiveVerifier(free_port(), {}, {})
        self.sentinel = "wardatlas_test_attach_sentinel_" + uuid.uuid4().hex
        self.sentinel_created = False
        self.sentinel_rows = [{"id": "ATTACH-SENTINEL", "note": "unrelated verifier-owned graph; never seed/reset from app"}]
        self.processes = []
        self.assertions = []
        self.runs = []
        self.browser = []
        self.cleanup = {}
        self.opener = build_opener(ProxyHandler({}))

    def check(self, group, name, condition, detail=None):
        self.assertions.append({"group": group, "name": name, "passed": bool(condition), "detail": detail})
        if not condition:
            raise VerificationFailure(f"{group}/{name}: {detail!r}")

    def command(self, *args):
        return self.db.client.command(*args)

    def sentinel_value(self):
        return result_rows(query_result(self.command("GRAPH.QUERY", self.sentinel,
            "MATCH (n:AttachSentinel) RETURN n.id AS id, n.note AS note")))

    def survives(self, group):
        self.check(group, "supplied server process still running", self.db.process.poll() is None)
        # Use a new connection, not a warm client's cached data.
        connection = RespConnection("127.0.0.1", self.db.port)
        try:
            self.check(group, "supplied server responds after reconnect", text(connection.command("PING")) == "PONG")
            rows = result_rows(query_result(connection.command("GRAPH.QUERY", self.sentinel,
                "MATCH (n:AttachSentinel) RETURN n.id AS id, n.note AS note")))
            self.check(group, "unrelated sentinel is unchanged", rows == self.sentinel_rows, rows)
        finally:
            connection.close()

    def spawn(self, name, command):
        log_path = self.args.evidence_dir / (name + ".log")
        with log_path.open("w") as log:
            process = subprocess.Popen(command, cwd=ROOT, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT, text=True, start_new_session=True)
        self.processes.append(process)
        return process, log_path

    def wait_ready(self, process, log):
        deadline = time.monotonic() + 40
        while time.monotonic() < deadline:
            data = log.read_text()
            for line in data.splitlines():
                if line.startswith("WARDATLAS_READY "):
                    owned = next(json.loads(row.split(" ", 1)[1]) for row in data.splitlines() if row.startswith("WARDATLAS_OWNED "))
                    return {**owned, "url": line.split(" ", 1)[1]}
            if process.poll() is not None:
                raise VerificationFailure("app exited before readiness: " + data[-5000:])
            time.sleep(0.05)
        raise VerificationFailure("app readiness timeout: " + log.read_text()[-5000:])

    def api(self, url):
        with self.opener.open(url, timeout=10) as response:
            return json.load(response)

    def assert_removed(self, group, owned):
        self.check(group, "only owned graph was removed", self.command("EXISTS", owned["graph"]) == 0)
        self.check(group, "owned state removed", not Path(owned["state"]).exists())
        if "url" in owned:
            port = int(owned["url"].rsplit(":", 1)[1])
            with socket.socket() as sock:
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                sock.bind(("127.0.0.1", port))
            self.check(group, "app listener closed", True)

    def interrupt(self, process):
        process.send_signal(signal.SIGINT)
        return process.wait(timeout=15)

    def run_browser(self, owned, index):
        command = ["node", "-e", BROWSER, owned["url"], str(self.args.evidence_dir), f"run-{index}", str(ROOT / "tests/expected-results.json")]
        process, log = self.spawn(f"browser-{index}", command)
        code = process.wait(timeout=100)
        self.check("desktop-mobile-home", f"run {index} browser exited successfully", code == 0, log.read_text()[-5000:])
        report = json.loads((self.args.evidence_dir / f"run-{index}-browser.json").read_text())
        self.browser.append(report)
        for size in ("desktop", "mobile"):
            item = report["results"][size]
            self.check("desktop-mobile-home", f"run {index} {size} API/DOM parity", item["paths"] == self.oracle["expected_paths"] and item["no_horizontal_overflow"] and not item["page_errors"])

    def success_runs(self):
        for index, host in enumerate(("127.0.0.1", "localhost"), 1):
            # This is exactly the README command, substituting the explicitly
            # supplied verifier-owned DB host/port. App port remains default 0.
            command = [sys.executable, "scripts/wardatlas_attach.py", "--db-host", host, "--db-port", str(self.db.port)]
            process, log = self.spawn(f"attach-{index}", command)
            owned = self.wait_ready(process, log)
            self.runs.append({**owned, "command": command})
            self.check("unique-owned-resources", "run-unique owned graph exists", self.command("EXISTS", owned["graph"]) == 1 and owned["graph"].startswith("wardatlas_attach_"))
            self.check("unique-owned-resources", "owned project-local state exists", Path(owned["state"]).is_relative_to(ROOT) and Path(owned["state"]).is_dir())
            health = self.api(owned["url"] + "/api/health")
            briefing = self.api(owned["url"] + "/api/briefing?scope=operations")
            paths = [item["path"] for item in briefing["impact_paths"]]
            self.check("actual-supplied-server", "real FalkorDB identity and independent path IDs", health["database"] == "FalkorDB" and health["module_version"] == 60002 and paths == self.oracle["expected_paths"], {"health": health, "paths": paths})
            self.check("actual-supplied-server", "unresolved SOP remains unresolved", briefing["sop_finding"]["conflict_status"] == "unresolved")
            self.check("explicit-host-port", f"explicit {host} and DB port used", process.poll() is None)
            self.run_browser(owned, index)
            self.survives("unique-owned-resources")
            self.check("sigint-cleanup", "foreground SIGINT exits zero", self.interrupt(process) == 0, log.read_text()[-1500:])
            self.assert_removed("sigint-cleanup", owned)
            self.survives("sigint-cleanup")
        self.check("repeat-run", "two distinct cold graphs and states", len(self.runs) == 2 and self.runs[0]["graph"] != self.runs[1]["graph"] and self.runs[0]["state"] != self.runs[1]["state"])
        self.check("repeat-run", "browser/API paths repeat exactly", self.browser[0]["results"]["desktop"]["paths"] == self.browser[1]["results"]["mobile"]["paths"] == self.oracle["expected_paths"])
        self.survives("repeat-run")

    def collision(self):
        with socket.socket() as occupant:
            occupant.bind(("127.0.0.1", 0))
            occupant.listen()
            before = text(self.command("INFO", "commandstats"))
            process, log = self.spawn("app-port-collision", [sys.executable, "scripts/wardatlas_attach.py", "--db-host", "127.0.0.1", "--db-port", str(self.db.port), "--app-port", str(occupant.getsockname()[1])])
            self.check("app-port-collision", "collision exits two", process.wait(timeout=15) == 2, log.read_text())
            after = text(self.command("INFO", "commandstats"))
            # Ignore INFO itself; module/graph commands must not change.
            filtered = lambda value: [line for line in value.splitlines() if line.lower().startswith(("cmdstat_graph", "cmdstat_module", "cmdstat_exists"))]
            self.check("app-port-collision", "no supplied DB graph/module/key commands", filtered(before) == filtered(after))
            occupant.settimeout(0.1)
            try:
                client, _ = occupant.accept()
                client.close()
                contacted = True
            except socket.timeout:
                contacted = False
            self.check("app-port-collision", "no occupant contact", not contacted)
            self.check("app-port-collision", "no graph/state adopted", "WARDATLAS_OWNED" not in log.read_text())
        self.survives("app-port-collision")

    def reset_interrupts(self):
        # Regression for independent verifier's real-request race: no injected
        # handler delay, DB shim or application patch. Observe live seed progress
        # on our own DB before interrupting, not just an idle ready application.
        def query_calls():
            stats = text(self.command("INFO", "commandstats"))
            match = re.search(r"cmdstat_graph.query:calls=(\d+)", stats, re.I)
            return int(match.group(1)) if match else 0

        for attempt in range(5):
            process, log = self.spawn(f"reset-interrupt-{attempt}", [sys.executable, "scripts/wardatlas_attach.py", "--db-host", "127.0.0.1", "--db-port", str(self.db.port)])
            owned = self.wait_ready(process, log)
            response = {}
            def reset():
                try:
                    request = Request(owned["url"] + "/api/scenario/reset", data=b"{}", headers={"Content-Type": "application/json"})
                    with build_opener(ProxyHandler({})).open(request, timeout=15) as result:
                        response["status"] = result.status
                        response["body"] = json.load(result)
                except Exception as exc:
                    response["error"] = str(exc)
            baseline = query_calls()
            request_thread = threading.Thread(target=reset)
            request_thread.start()
            try:
                deadline = time.monotonic() + 5
                observed = 0
                while time.monotonic() < deadline and request_thread.is_alive():
                    observed = query_calls() - baseline
                    if observed >= 10:
                        break
                    time.sleep(0.0005)
                in_flight = request_thread.is_alive()
                self.check("sigint-cleanup", f"attempt {attempt} real reset is in flight after seed progress", observed >= 10 and in_flight, {"seed_commands_observed": observed, "request_in_flight": in_flight})
                code = self.interrupt(process)
                request_thread.join(15)
                self.check("sigint-cleanup", f"attempt {attempt} SIGINT drains accepted reset", code == 0 and not request_thread.is_alive() and response.get("status") == 200, {"app_exit": code, "response": response, "log": log.read_text()[-1500:]})
                self.assert_removed("sigint-cleanup", owned)
                self.survives("sigint-cleanup")
            finally:
                request_thread.join(20)

    def failures(self):
        # Deliberately fail AFTER a real partial seed on the actual supplied DB.
        # This is a fault injection into the unchanged launcher, not a fake DB.
        code = """import sys;sys.path.insert(0,'scripts')
import wardatlas_attach as a
def fail(self):
 c=self._connection()
 try:c.command('GRAPH.QUERY',self.graph_name,\"CREATE (:AttachPartial {id:'partial'})\")
 finally:c.close()
 raise RuntimeError('verifier injected partial seed failure')
a.WardAtlasService.reset_scenario=fail
raise SystemExit(a.main(sys.argv[1:]))
"""
        process, log = self.spawn("partial-startup-failure", [sys.executable, "-c", code, "--db-host", "127.0.0.1", "--db-port", str(self.db.port)])
        self.check("startup-failure", "partial seed failure exits two", process.wait(timeout=15) == 2, log.read_text())
        data = log.read_text()
        owned = next(json.loads(line.split(" ", 1)[1]) for line in data.splitlines() if line.startswith("WARDATLAS_OWNED "))
        self.check("startup-failure", "no readiness after seed failure", "WARDATLAS_READY" not in data and "injected partial seed failure" in data)
        self.assert_removed("startup-failure", owned)
        self.survives("startup-failure")

        # Negative identity control: our own separate plain Redis, never an
        # unrelated local service. Wait for its own startup log before contact.
        port = free_port()
        directory = ROOT / ".local" / ("attach-plain-" + uuid.uuid4().hex)
        directory.mkdir()
        plain, plain_log = self.spawn("plain-redis", [self.db.database["redis_server_executable"], "--bind", "127.0.0.1", "--port", str(port), "--daemonize", "no", "--save", "", "--appendonly", "no", "--dir", str(directory)])
        deadline = time.monotonic() + 10
        while "Ready to accept connections" not in plain_log.read_text():
            if plain.poll() is not None or time.monotonic() > deadline:
                raise VerificationFailure("owned plain Redis not ready")
            time.sleep(0.05)
        process, log = self.spawn("reject-plain-redis", [sys.executable, "scripts/wardatlas_attach.py", "--db-host", "127.0.0.1", "--db-port", str(port)])
        self.check("startup-failure", "plain Redis rejected before seeding", process.wait(timeout=15) == 2 and "not FalkorDB" in log.read_text() and "WARDATLAS_READY" not in log.read_text(), log.read_text())
        connection = RespConnection("127.0.0.1", port)
        try:
            self.check("startup-failure", "plain negative control untouched and alive", connection.command("DBSIZE") == 0 and text(connection.command("PING")) == "PONG")
        finally:
            connection.close()
        plain.terminate()
        plain.wait(timeout=10)
        directory.rmdir()
        self.survives("startup-failure")

    def run(self):
        self.db.launch()
        self.command("GRAPH.QUERY", self.sentinel, "CREATE (:AttachSentinel {id:'ATTACH-SENTINEL', note:'unrelated verifier-owned graph; never seed/reset from app'})")
        self.sentinel_created = True
        self.check("actual-supplied-server", "sentinel initialized independently", self.sentinel_value() == self.sentinel_rows)
        self.collision()
        self.failures()
        self.success_runs()
        self.reset_interrupts()
        groups = {row["group"] for row in self.assertions if row["passed"]}
        self.check("repeat-run", "all eight groups executed with nonzero assertions", groups == {g["id"] for g in self.expected["groups"]}, sorted(groups))

    def stop(self):
        for process in reversed(self.processes):
            if process.poll() is None:
                process.send_signal(signal.SIGINT)
                try:
                    process.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait(timeout=5)
        if self.db.client is not None and self.sentinel_created:
            self.command("GRAPH.DELETE", self.sentinel)
            self.cleanup["verifier_sentinel_removed_at_end_only"] = True
        self.db.stop()
        self.cleanup["all_owned_children_reaped"] = all(p.poll() is not None for p in self.processes)
        self.cleanup["verifier_db_stopped"] = self.db.process is not None and self.db.process.poll() is not None


def worker(args):
    args.evidence_dir.mkdir(parents=True, exist_ok=True)
    verifier = Check(args)
    status, error = "passed", None
    try:
        verifier.run()
    except Exception as exc:
        status, error = "failed", f"{type(exc).__name__}: {exc}"
    finally:
        try:
            verifier.stop()
        except Exception as exc:
            status, error = "failed", f"{error or ''}; cleanup {type(exc).__name__}: {exc}"
    report = {"status": status, "error": error, "source_commit": args.source_commit, "clean_source_snapshot": True,
        "oracle_hashes": PINS, "assertion_count": len(verifier.assertions), "assertions": verifier.assertions,
        "database": verifier.db.database, "runs": verifier.runs, "cleanup": verifier.cleanup}
    (args.evidence_dir / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({"status": status, "assertions": len(verifier.assertions), "error": error, "report": str(args.evidence_dir / "report.json")}))
    return 0 if status == "passed" and verifier.assertions and all(verifier.cleanup.values()) else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for flag in ("--all", "--require-live-falkordb", "--assert-server-and-sentinel-survive", "--desktop-mobile", "--fail-on-zero"):
        parser.add_argument(flag, action="store_true", help="Always enforced; retained as explicit acceptance flags")
    parser.add_argument("--evidence-dir", type=Path, default=Path(os.environ.get("WORKSHOP_RUN_DIR", str(ROOT / ".local" / "verification"))) / "existing-server")
    parser.add_argument("--source-worker", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--source-commit", default="", help=argparse.SUPPRESS)
    args = parser.parse_args()
    args.evidence_dir = args.evidence_dir.resolve()
    if args.source_worker:
        return worker(args)
    # Archive only committed product source; no ignored dependencies, mutable
    # evidence, or preparation files are copied into this verification run.
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    (ROOT / ".local").mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="existing-server-source-", dir=ROOT / ".local") as temporary:
        destination = Path(temporary)
        archive = subprocess.Popen(["git", "archive", "--format=tar", commit], cwd=ROOT, stdout=subprocess.PIPE)
        try:
            with tarfile.open(fileobj=archive.stdout, mode="r|") as tar:
                for member in tar:
                    target = (destination / member.name).resolve()
                    if not target.is_relative_to(destination) or not (member.isfile() or member.isdir()):
                        raise VerificationFailure("unsafe source archive member")
                    tar.extract(member, destination)
            if archive.wait(timeout=30) != 0:
                raise VerificationFailure("git archive failed")
        finally:
            archive.stdout.close()
            if archive.poll() is None:
                archive.kill()
                archive.wait()
        command = [sys.executable, "scripts/verify_existing_server.py", "--source-worker", "--source-commit", commit, "--evidence-dir", str(args.evidence_dir)]
        return subprocess.run(command, cwd=destination).returncode


if __name__ == "__main__":
    raise SystemExit(main())

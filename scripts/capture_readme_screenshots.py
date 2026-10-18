#!/usr/bin/env python3
"""Capture eight unaltered dark-theme UI crops on an owned actual FalkorDB run.

Requires Python 3.10+, redis-server and Node/npm. The pinned official graph
module, Playwright and its headless browser bootstrap project-locally if absent.
No supplied database, default port, existing graph or shared app is used.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import threading
import uuid
from pathlib import Path

from verify_falkordb_live import LiveVerifier
from wardatlas_attach import Attachment, AttachmentHandler, AttachmentHTTPServer

ROOT = Path(__file__).resolve().parents[1]


def free_db_port():
    base = int(os.environ.get("WORKSHOP_PORT_BASE", "0"))
    span = int(os.environ.get("WORKSHOP_PORT_SPAN", "1000"))
    for port in range(base, base + span) if base else [0]:
        with socket.socket() as sock:
            try:
                sock.bind(("127.0.0.1", port))
            except OSError:
                continue
            return sock.getsockname()[1]
    raise RuntimeError("No free port in this lane's allocation")


def browser_environment():
    env = os.environ.copy()
    probe = """for(const m of [process.env.README_PLAYWRIGHT_MODULE,'playwright','playwright-core','/tools/node/lib/node_modules/playwright-core']){if(!m)continue;try{console.log(require.resolve(m));process.exit(0)}catch{}}process.exit(1)"""
    result = subprocess.run(["node", "-e", probe], cwd=ROOT, env=env, capture_output=True, text=True)
    if result.returncode:
        prefix = ROOT / ".local/readme-browser"
        subprocess.run(["npm", "install", "--prefix", str(prefix), "--no-audit", "--no-fund",
                        "playwright-core@1.58.2"], check=True)
        module = prefix / "node_modules/playwright-core/index.js"
    else:
        module = Path(result.stdout.strip())
    env["README_PLAYWRIGHT_MODULE"] = str(module)
    launch = "const {chromium}=require(process.env.README_PLAYWRIGHT_MODULE);chromium.launch({headless:true,args:['--no-sandbox','--disable-dev-shm-usage']}).then(b=>b.close()).catch(e=>{console.error(String(e));process.exitCode=1})"
    probe = subprocess.run(["node", "-e", launch], env=env, capture_output=True, text=True)
    if probe.returncode:
        env["PLAYWRIGHT_BROWSERS_PATH"] = str(ROOT / ".local/readme-browsers")
        env.pop("PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD", None)
        subprocess.run(["node", str(module.parent / "cli.js"), "install", "chromium", "--only-shell"], env=env, check=True)
    return env


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=ROOT / "docs/media")
    parser.add_argument("--report", type=Path, default=Path(os.environ.get("WORKSHOP_RUN_DIR", ROOT / ".local/readme-media-evidence")) / "capture.json")
    args = parser.parse_args()
    args.output_dir = args.output_dir.resolve()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    report = {"status": "FAIL", "cleanup": {}, "viewport_width": 1440, "synthetic": True}
    db = LiveVerifier(free_db_port(), {}, {})
    db.run_id = f"readme_lane{os.environ.get('WORKSHOP_LANE', 'local')}_{uuid.uuid4().hex}"
    attachment = server = thread = None
    old_signals = {}

    def interrupt(_sig, _frame):
        raise KeyboardInterrupt

    try:
        for sig in (signal.SIGINT, signal.SIGTERM):
            old_signals[sig] = signal.signal(sig, interrupt)
        env = browser_environment()
        db.launch()
        attachment = Attachment("127.0.0.1", db.port)
        server = AttachmentHTTPServer(("127.0.0.1", 0), AttachmentHandler, attachment.service)
        attachment.prepare()
        thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.1})
        thread.start()
        url = f"http://127.0.0.1:{server.server_port}"
        browser_report = args.report.with_name(args.report.stem + "-browser.json")
        result = subprocess.run(["node", str(ROOT / "scripts/capture_readme_screenshots.cjs"), url,
                                 str(args.output_dir), str(browser_report)], env=env, timeout=240)
        report.update(json.loads(browser_report.read_text()))
        if result.returncode:
            raise RuntimeError(report.get("error", "Browser capture failed"))
        report["database"] = {"name": db.database["falkordb_module_name"],
                              "version": db.database["falkordb_module_version"],
                              "module_sha256": db.database["module_provenance"]["module_sha256"]}
    except BaseException as exc:
        report["status"] = "FAIL"
        report["error"] = f"{type(exc).__name__}: {exc}"
        raise
    finally:
        cleanup = report["cleanup"]
        try:
            if server:
                if thread:
                    server.shutdown()
                    thread.join(timeout=10)
                server.server_close()  # drain requests BEFORE deleting graph/state
                cleanup["app_stopped"] = thread is None or not thread.is_alive()
            if attachment:
                attachment.cleanup()
                cleanup["graph_deleted"] = db.client.command("EXISTS", attachment.graph) == 0
                cleanup["state_removed"] = not attachment.state.exists()
        finally:
            db.stop()
            cleanup["database_stopped"] = bool(db.cleanup["owned_process_stopped"])
            if db.server_log_path:
                runtime = ROOT / ".local/falkordb-runs" / db.run_id
                if db.server_log_path.parent == runtime and runtime.exists():
                    shutil.rmtree(runtime)
                cleanup["database_runtime_removed"] = not runtime.exists()
            if not cleanup or not all(cleanup.values()):
                report["status"] = "FAIL"
            args.report.write_text(json.dumps(report, indent=2) + "\n")
            for sig, handler in old_signals.items():
                signal.signal(sig, handler)
    print(json.dumps({"status": report["status"], "report": str(args.report), "cleanup": report["cleanup"]}))
    return report["status"] != "PASS"


if __name__ == "__main__":
    sys.exit(main())

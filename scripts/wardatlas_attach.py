#!/usr/bin/env python3
"""Foreground app-only attachment to an explicitly supplied FalkorDB server.

No database process is started or managed. Only a new run-unique graph and
project-local action state belong to this process. Ctrl-C removes both.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import signal
import sys
import uuid
from pathlib import Path

from verify_falkordb_live import module_list_maps
from wardatlas_app import Handler, WardAtlasHTTPServer, WardAtlasService

ROOT = Path(__file__).resolve().parents[1]


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--db-host", required=True, help="Supplied FalkorDB hostname/IP (no URL or credentials)")
    result.add_argument("--db-port", type=int, required=True)
    result.add_argument("--app-port", type=int, default=0, help="Loopback app port; default 0 asks the OS for a free port")
    return result


def validate_app_port(port: int) -> int:
    if not 0 <= port <= 65535:
        raise ValueError("app port must be 0 (ephemeral) or an explicit port in 1..65535")
    return port


class AttachmentHandler(Handler):
    # One request per connection: draining must not wait for an idle HTTP/1.1
    # keepalive client. API payloads, routes and the legacy server are unchanged.
    protocol_version = "HTTP/1.0"

    def setup(self):
        self.request.settimeout(3.0)
        super().setup()


class AttachmentHTTPServer(WardAtlasHTTPServer):
    # ThreadingMixIn.server_close closes the listener and joins every accepted
    # request when these are non-daemon. In-flight resets/confirmations must
    # finish BEFORE deleting their graph/state, not recreate them afterwards.
    daemon_threads = False
    block_on_close = True


class Attachment:
    def __init__(self, host: str, port: int):
        lane = re.sub(r"[^a-zA-Z0-9_]", "_", os.environ.get("WORKSHOP_LANE", "local"))[:24]
        identity = f"{lane}_{uuid.uuid4().hex}"
        self.graph = f"wardatlas_attach_{identity}"
        self.state = ROOT / ".local" / "attachments" / identity
        self.service = WardAtlasService(port, self.graph, self.state, db_host=host)
        self.graph_owned = False
        self.state_owned = False

    def prepare(self) -> None:
        connection = self.service._connection()
        try:
            # Identity is checked BEFORE any graph creation, query or fixture write.
            modules = module_list_maps(connection.command("MODULE", "LIST"))
            module = next((row for row in modules if str(row.get("name", "")).lower() in {"graph", "falkordb"}), None)
            if module is None or int(module.get("ver", 0)) < 60000:
                raise RuntimeError("Supplied server is not FalkorDB 6.x or newer; no graph was seeded")
            if connection.command("EXISTS", self.graph):
                raise RuntimeError("Generated graph name already exists; refusing to touch it")
            # UUID identity and absent-key check establish this run's ownership.
            # Mark before the query so a partial server-side creation is cleaned.
            self.graph_owned = True
            connection.command("GRAPH.QUERY", self.graph, "RETURN 1 AS ready")
        finally:
            connection.close()
        self.state.parent.mkdir(parents=True, exist_ok=True)
        self.state.mkdir(mode=0o700)  # never adopt an existing directory
        self.state_owned = True
        self.service.reset_scenario()
        self.service.health()

    def cleanup(self) -> None:
        errors = []
        if self.graph_owned:
            connection = None
            try:
                connection = self.service._connection()
                # No key listing, global reset, SHUTDOWN, CONFIG, or other graph.
                connection.command("GRAPH.DELETE", self.graph)
                self.graph_owned = False
            except Exception as exc:
                errors.append(f"owned graph cleanup failed: {exc}")
            finally:
                if connection is not None:
                    connection.close()
        if self.state_owned:
            try:
                shutil.rmtree(self.state)
                self.state_owned = False
            except Exception as exc:
                errors.append(f"owned state cleanup failed: {exc}")
        if errors:
            raise RuntimeError("; ".join(errors))


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    attachment = None
    server = None
    code = 0
    old_signals = {}

    def interrupt(_signum, _frame):
        raise KeyboardInterrupt

    try:
        validate_app_port(args.app_port)
        attachment = Attachment(args.db_host, args.db_port)
        # Bind first. An app-port collision must not contact either occupant or DB.
        server = AttachmentHTTPServer(("127.0.0.1", args.app_port), AttachmentHandler, attachment.service)
        for sig in (signal.SIGINT, signal.SIGTERM):
            old_signals[sig] = signal.signal(sig, interrupt)
        print("WARDATLAS_OWNED " + json.dumps({"graph": attachment.graph, "state": str(attachment.state)}), flush=True)
        attachment.prepare()
        url = f"http://127.0.0.1:{server.server_port}"
        print(f"WARDATLAS_READY {url}", flush=True)
        server.serve_forever(poll_interval=0.1)
    except KeyboardInterrupt:
        pass
    except Exception as exc:
        print(f"WARDATLAS_ERROR {type(exc).__name__}: {exc}", file=sys.stderr, flush=True)
        code = 2
    finally:
        # A second interrupt must not interrupt the bounded ownership cleanup.
        for sig in old_signals:
            signal.signal(sig, signal.SIG_IGN)
        if server is not None:
            # serve_forever has stopped accepting. Drain both graph writes and
            # local action/watch writes, including queued accepted requests.
            server.server_close()
        if attachment is not None:
            try:
                attachment.cleanup()
            except Exception as exc:
                print(f"WARDATLAS_ERROR {exc}", file=sys.stderr, flush=True)
                code = 2
        for sig, handler in old_signals.items():
            signal.signal(sig, handler)
    return code


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Local-only WardAtlas outage briefing and confirmed handover draft UI.

The application serves a single loopback HTTP origin and talks only to the
explicitly selected loopback FalkorDB instance.  It has no model, messaging,
calling, ticketing, or external-write integration.  Confirmed handovers are
stored as a local JSON file; previews remain in process memory.
"""

from __future__ import annotations

import argparse
import json
import re
import secrets
import threading
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from verify_falkordb_live import (
    RespConnection,
    module_list_maps,
    query_result,
    result_rows,
    text,
)
from wardatlas_fixtures import IMPACT_QUERY, SOP_CONFLICT_QUERY, seed_queries
from wardatlas_sop_impact import (
    CLAIMS_QUERY as SOP_IMPACT_CLAIMS_QUERY,
    CONFLICT_WITNESS_QUERY,
    STALE_QUERY,
    assemble_response as assemble_sop_impact_response,
)
from wardatlas_handover import has_complete_sop_witness, snapshot_from_impact, source_ids_from_snapshot
from wardatlas_shift_board import (
    BRANCH_AT,
    BRANCH_EVENT_QUERY,
    BRANCH_ID,
    branch_reset_queries,
    CONVEYOR_EVENT_QUERY,
    CONVEYOR_OUTAGE_AT,
    CONVEYOR_PATH_QUERY,
    CONVEYOR_RESTORED_AT,
    DOCK_BEFORE,
    DOCK_EFFECTIVE,
    DOCK_HANDOVER_SNAPSHOT_QUERY,
    DOCK_REVIEW_QUERY,
    DOCK_SOURCE_QUERY,
    DOCK_SUPERSESSION_QUERY,
    dock_review_query,
    dock_role_query,
    dock_source_query,
    dock_supersession_query,
    normalize_timestamp,
    seed_shift_board_queries,
)


ROOT = Path(__file__).resolve().parents[1]
GRAPH_NAME_RE = re.compile(r"^wardatlas_(?:test|ui)_[A-Za-z0-9_-]{12,80}$")
APP_HTML = r'''<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="theme-color" content="#0d1924">
  <title>WardAtlas · Shift control</title>
  <style>
    :root { color-scheme:dark; --bg:#0d1924; --panel:#142431; --panel2:#192c39; --line:#29404c; --ink:#edf4f2; --muted:#9db0b5; --mint:#86e1be; --amber:#f1be70; --red:#ef8a7b; --blue:#9bc7f0; --shadow:0 18px 50px #050b1090; font-family:Inter,ui-sans-serif,system-ui,-apple-system,"Segoe UI",sans-serif; }
    * { box-sizing:border-box; }
    body { margin:0; color:var(--ink); background:radial-gradient(ellipse at 15% -12%,#1f3a46 0,transparent 43%),var(--bg); min-height:100vh; }
    button { font:inherit; cursor:pointer; }
    .shell { max-width:1480px; margin:auto; padding:22px clamp(16px,3vw,44px) 54px; }
    .topbar { display:flex; align-items:center; justify-content:space-between; gap:18px; padding:4px 0 22px; border-bottom:1px solid #29404c99; }
    .brand { display:flex; align-items:center; gap:12px; letter-spacing:.11em; font-size:12px; font-weight:800; color:#c5d6d6; }
    .brand-mark { display:grid; place-items:center; width:34px; height:34px; border:1px solid #72caa7; border-radius:11px; color:var(--mint); font-size:15px; background:#16372f; }
    .brand small { display:block; color:var(--muted); letter-spacing:.05em; font-weight:500; margin-top:3px; }
    .top-actions { display:flex; align-items:center; gap:12px; }
    .pill { border:1px solid #36535e; background:#172b36; border-radius:999px; padding:8px 12px; color:#c8d7d7; font-size:12px; white-space:nowrap; }
    .dot { display:inline-block; width:7px; height:7px; margin-right:7px; border-radius:50%; background:var(--mint); box-shadow:0 0 9px #86e1be88; }
    .button { border:1px solid #3a5964; border-radius:9px; padding:10px 14px; color:var(--ink); background:#203541; transition:transform .15s,border-color .15s,background .15s; }
    .button:hover { transform:translateY(-1px); border-color:#78c9a9; background:#28443f; }
    .button.primary { color:#0b2019; background:var(--mint); border-color:var(--mint); font-weight:750; }
    .button.primary:hover { background:#a4f0d1; }
    .button:disabled { opacity:.55; cursor:wait; transform:none; }
    .eyebrow { color:var(--mint); text-transform:uppercase; letter-spacing:.16em; font-size:10px; font-weight:800; }
    .hero { padding:34px 0 25px; display:grid; grid-template-columns:minmax(0,1.5fr) minmax(220px,.75fr); gap:30px; align-items:end; }
    h1 { max-width:800px; font-size:clamp(30px,4vw,54px); line-height:1.06; letter-spacing:-.045em; margin:10px 0 12px; }
    .hero p { margin:0; max-width:720px; color:#aec0c2; line-height:1.6; font-size:15px; }
    .incident-card { padding:18px; border:1px solid #493d31; border-radius:14px; background:linear-gradient(135deg,#342c24,#1b2a33); box-shadow:var(--shadow); }
    .incident-card .status { color:var(--amber); text-transform:uppercase; letter-spacing:.12em; font-size:10px; font-weight:800; }
    .incident-card strong { display:block; font-size:18px; margin:7px 0 5px; }
    .incident-card p { color:#c0c6c0; font-size:12px; line-height:1.5; }
    .grid { display:grid; grid-template-columns:minmax(0,1.55fr) minmax(310px,.85fr); gap:18px; align-items:start; }
    .stack { display:grid; gap:18px; }
    .card { min-width:0; background:linear-gradient(145deg,#172a37,#13232e); border:1px solid var(--line); border-radius:15px; padding:20px; box-shadow:var(--shadow); }
    .card-head { display:flex; align-items:flex-start; justify-content:space-between; gap:14px; margin-bottom:15px; }
    .card h2 { font-size:17px; margin:4px 0 0; letter-spacing:-.02em; }
    .card h3 { font-size:13px; margin:0; }
    .subtle { color:var(--muted); font-size:12px; line-height:1.55; }
    .tag { display:inline-flex; align-items:center; border:1px solid #315348; background:#16362e; color:#a5e8ca; border-radius:999px; padding:5px 9px; font-size:10px; font-weight:700; white-space:nowrap; }
    .tag.amber { color:#ffd08a; border-color:#6b5031; background:#3d3021; }
    .tag.blue { color:#c4dcf3; border-color:#39566c; background:#1b3040; }
    .graph-wrap { overflow-x:auto; padding:4px 0 9px; scrollbar-color:#42626d #13232e; }
    .graph-svg { width:100%; min-width:690px; height:auto; overflow:visible; }
    .graph-edge { stroke:#55717b; stroke-width:2.5; fill:none; }
    .graph-edge.ownership { stroke:var(--mint); }
    .graph-edge.conflict { stroke:var(--amber); stroke-dasharray:7 6; }
    .graph-node rect { fill:#1d3541; stroke:#4c6a74; stroke-width:1.4; rx:10; }
    .graph-node.incident rect { stroke:#d29d54; fill:#382f24; }
    .graph-node.role rect { stroke:#59a987; fill:#19372f; }
    .graph-node.sop rect { stroke:#bb955b; fill:#382f24; }
    .graph-node text { fill:#eaf2f0; font-size:11px; text-anchor:middle; pointer-events:none; }
    .graph-node .kind { fill:#9eb4b8; font-size:8px; letter-spacing:1px; text-transform:uppercase; }
    .graph-caption { display:flex; flex-wrap:wrap; gap:12px 18px; padding-top:8px; color:#a9bec0; font-size:11px; }
    .legend { display:inline-flex; align-items:center; gap:7px; }
    .legend-line { display:inline-block; width:20px; border-top:2px solid #75d5ae; }
    .legend-line.conflict { border-top:2px dashed var(--amber); }
    .path-list { display:grid; gap:10px; margin-top:15px; }
    .path-row { display:grid; grid-template-columns:1fr auto; align-items:center; gap:10px; padding:11px 12px; border:1px solid #2a424d; border-radius:10px; background:#11212b; }
    .path-row strong { font-size:12px; }
    .path-row .path-meta { color:#aabbbd; font-size:11px; margin-top:4px; line-height:1.45; }
    .role-label { color:#a5e8ca; font-size:11px; text-align:right; max-width:150px; }
    .evidence-list { display:grid; gap:9px; }
    .evidence-item { border-left:2px solid #5fc59a; padding:3px 0 4px 11px; min-width:0; }
    .evidence-item strong { display:block; overflow-wrap:anywhere; font-size:11px; }
    .evidence-item span { display:block; color:#9fb3b7; font-size:10px; margin-top:4px; }
    .evidence-id { color:#9bd6bf!important; letter-spacing:.025em; }
    .sop-stale { padding:13px; border-radius:10px; background:#332d24; border:1px solid #5e4b31; margin-bottom:12px; }
    .sop-current { padding:13px; border-radius:10px; background:#172c38; border:1px solid #314b59; }
    .claim-list { display:grid; gap:8px; margin-top:10px; }
    .claim { display:flex; justify-content:space-between; align-items:center; gap:12px; font-size:11px; border-top:1px solid #314550; padding-top:8px; }
    .claim strong { overflow-wrap:anywhere; color:#dce8e5; }
    .fact-groups { display:grid; gap:14px; }
    .fact-group { border-top:1px solid #2b424c; padding-top:12px; }
    .fact-group h3 { color:#b7c9c9; text-transform:uppercase; font-size:10px; letter-spacing:.12em; margin-bottom:9px; }
    .fact-list { list-style:none; padding:0; margin:0; display:grid; gap:8px; }
    .fact-list li { color:#d3dfdc; font-size:12px; line-height:1.5; }
    .fact-list li::before { content:"•"; color:var(--mint); margin-right:8px; }
    .fact-group.inference li::before { color:var(--blue); }
    .fact-group.missing li::before { color:var(--amber); }
    .handover-actions { display:flex; flex-wrap:wrap; gap:9px; margin:15px 0 0; }
    .handover-controls { display:grid; gap:7px; margin-top:12px; }
    .handover-controls label { color:#afc2bf; font-size:10px; }
    .handover-controls input { width:100%; min-width:0; color:var(--ink); background:#1a3038; border:1px solid #426067; border-radius:7px; padding:9px 10px; font:inherit; font-size:11px; }
    .preview { margin-top:14px; border:1px solid #3a5964; background:#10212b; border-radius:11px; padding:14px; }
    .preview[hidden] { display:none; }
    .preview h3 { margin:0 0 8px; }
    .preview p { color:#cfddda; font-size:12px; line-height:1.5; }
    .task-list { margin:9px 0 14px; padding-left:18px; color:#d4e1dd; font-size:11px; line-height:1.65; }
    .saved-list { display:grid; gap:9px; margin-top:12px; }
    .saved-item { border:1px solid #2f4c45; border-radius:10px; padding:12px; background:#142b27; }
    .saved-item strong { font-size:12px; }
    .saved-item p { margin:6px 0 0; color:#a9c2bb; font-size:11px; line-height:1.5; }
    .handover-snapshot { border:1px solid #35534e; border-radius:9px; padding:11px; margin:11px 0; background:#10231f; min-width:0; }
    .handover-snapshot h4 { margin:0 0 8px; color:#a9e9cd; font-size:10px; letter-spacing:.09em; text-transform:uppercase; }
    .handover-snapshot h5 { margin:10px 0 5px; color:#c0d1cc; font-size:9px; letter-spacing:.08em; text-transform:uppercase; }
    .handover-snapshot p { overflow-wrap:anywhere; }
    .handover-witness-status { color:#f0cf91; font-size:10px; }
    .handover-path { overflow-wrap:anywhere; border-left:2px solid var(--mint); padding:7px 8px; margin:7px 0; background:#102d2680; color:#d8e9e2; font-size:10px; line-height:1.55; }
    .handover-source { color:#a9c2bb; font-size:9px; line-height:1.5; overflow-wrap:anywhere; padding:2px 0; }
    .handover-claim { border-top:1px solid #344b43; padding:6px 0; color:#d4e1dd; font-size:10px; line-height:1.5; overflow-wrap:anywhere; }
    .handover-claim strong { color:#f0d9a9; }
    .safety { margin-top:18px; padding:13px 15px; border:1px solid #314650; border-radius:11px; color:#a9bec0; font-size:11px; line-height:1.55; background:#111f29; }
    .safety strong { color:#d3e2dc; }
    .notice { min-height:20px; margin-top:10px; color:#f1c278; font-size:12px; }
    .empty { color:#9fb3b7; padding:14px 0; font-size:13px; }
    .shift-board { margin:0 0 18px; border-color:#41605d; background:linear-gradient(145deg,#18332f,#142631 66%); }
    .shift-intro { max-width:760px; color:#b6c9c5; font-size:12px; line-height:1.55; margin:9px 0 17px; }
    .shift-cases { display:grid; grid-template-columns:minmax(0,1fr) minmax(0,1.2fr); gap:13px; }
    .shift-case { min-width:0; border:1px solid #33514f; background:#10231f88; border-radius:12px; padding:15px; }
    .shift-case header { display:flex; justify-content:space-between; align-items:flex-start; gap:10px; }
    .shift-case h3 { font-size:14px; margin:1px 0 4px; }
    .shift-kicker { color:var(--mint); font-size:9px; letter-spacing:.13em; text-transform:uppercase; font-weight:800; }
    .shift-controls { display:flex; flex-wrap:wrap; align-items:center; gap:7px; margin:11px 0; }
    .shift-controls label { color:#afc2bf; font-size:10px; }
    .shift-controls select { color:var(--ink); background:#1a3038; border:1px solid #426067; border-radius:7px; padding:7px 8px; font:inherit; font-size:10px; }
    .shift-controls .button { padding:7px 9px; font-size:10px; }
    .shift-result { min-height:35px; color:#d6e3df; font-size:11px; line-height:1.55; }
    .shift-path { overflow-wrap:anywhere; border-left:2px solid var(--mint); padding:7px 9px; margin:7px 0; background:#102d26; color:#d8e9e2; font-size:10px; line-height:1.6; }
    .shift-path strong { color:#a9e9cd; }
    .shift-evidence { margin-top:10px; border-top:1px solid #304b49; padding-top:9px; }
    .shift-evidence h4 { margin:0 0 6px; color:#9fc0b5; font-size:9px; letter-spacing:.12em; text-transform:uppercase; }
    .shift-source { display:flex; justify-content:space-between; flex-wrap:wrap; gap:4px 10px; color:#c8d8d2; font-size:10px; padding:4px 0; }
    .shift-source span { color:#9eb7b4; }
    .review-item { display:flex; align-items:center; gap:8px; margin:6px 0; color:#dbe8e2; font-size:10px; }
    .review-item::before { content:"↻"; color:var(--amber); font-size:14px; }
    .branch-box { border:1px dashed #9e8055; border-radius:9px; padding:10px; margin-top:10px; background:#2b261d88; }
    .branch-box strong { color:#ffd08a; font-size:10px; }
    .branch-box p { margin:5px 0 0; color:#c4c8bd; font-size:10px; line-height:1.5; }
    .shift-unknown { color:#f1c278; }
    .shift-case[hidden] { display:none; }
    .sop-impact { margin:0 0 18px; border-color:#5b4d38; background:linear-gradient(145deg,#302a22,#182832 64%); }
    .sop-impact-intro { color:#bfcbc7; font-size:12px; line-height:1.55; margin:4px 0 15px; max-width:880px; }
    .sop-impact-grid { display:grid; grid-template-columns:minmax(0,1.05fr) minmax(0,.95fr); gap:14px; }
    .sop-impact-pane { min-width:0; border:1px solid #554735; border-radius:11px; padding:13px; background:#171f2188; }
    .sop-impact-pane h3 { color:#d6c6a9; text-transform:uppercase; font-size:9px; letter-spacing:.13em; margin:0 0 9px; }
    .sop-impact-path { overflow-wrap:anywhere; border-left:2px solid var(--mint); padding:8px 10px; margin:8px 0; background:#102d2680; color:#d9e8e2; font-size:10px; line-height:1.65; }
    .sop-impact-path small { display:block; color:#a5bbb5; margin-top:4px; }
    .sop-impact-claim { border-top:1px solid #504632; padding:9px 0; font-size:11px; line-height:1.55; }
    .sop-impact-claim strong { color:#f0d9a9; }
    .sop-impact-claim span { display:block; color:#afc0bb; font-size:10px; overflow-wrap:anywhere; }
    .sop-impact-edge { color:#f4cf8c; font-size:10px; line-height:1.6; overflow-wrap:anywhere; }
    .sop-impact-stale { border:1px solid #665035; border-radius:9px; padding:10px 12px; margin-top:12px; color:#d8c6a8; font-size:10px; line-height:1.55; background:#30281d88; }
    .sop-impact-empty { color:#f1c278; font-size:12px; padding:6px 0; }
    @media (max-width:760px) { .sop-impact-grid { grid-template-columns:1fr; } }
    #legacy-grid[hidden] { display:none; }
    .footer { color:#758c92; font-size:10px; padding:21px 2px 0; display:flex; justify-content:space-between; gap:12px; }
    @media (max-width:900px) { .grid { grid-template-columns:1fr; } .hero { grid-template-columns:1fr; align-items:start; } .incident-card { max-width:420px; } }
    @media (max-width:760px) { .shift-cases { grid-template-columns:1fr; } }
    @media (max-width:560px) { .shell { padding:13px 13px 35px; } .topbar { align-items:flex-start; } .top-actions { flex-direction:column-reverse; align-items:flex-end; gap:8px; } .pill { font-size:10px; padding:7px 9px; } .button { padding:9px 10px; font-size:12px; } .hero { padding:25px 0 17px; gap:17px; } h1 { font-size:35px; } .hero p { font-size:13px; } .card { padding:15px; border-radius:12px; } .card-head { flex-direction:column; } .path-row { grid-template-columns:1fr; } .role-label { text-align:left; max-width:none; } .footer { flex-direction:column; } .shift-case { padding:12px; } .shift-controls { align-items:stretch; } .shift-controls select { max-width:100%; } }
  </style>
</head>
<body>
  <div class="shell">
    <header class="topbar">
      <div class="brand"><span class="brand-mark" aria-hidden="true">W</span><span>WARDATLAS<small>HOSPITAL OPERATIONS BRAIN</small></span></div>
      <div class="top-actions"><span id="db-status" class="pill"><span class="dot"></span>Connecting to local graph…</span><button id="reset" class="button" type="button">Reset synthetic scenario</button></div>
    </header>
    <main>
      <section class="hero" aria-labelledby="main-title">
        <div><div class="eyebrow">Shift control · synthetic exercise</div><h1 id="main-title">A board with no defaults.</h1><p id="hero-description">The local graph has not established an incident or accountable path. Reset the synthetic scenario to load its pinned cases.</p></div>
        <aside class="incident-card"><div id="incident-status" class="status">No graph-backed incident</div><strong id="incident-title">No incident in graph</strong><p id="incident-summary">Empty/unknown is not evidence of safety or no operational risk.</p></aside>
      </section>
      <section class="card shift-board" aria-labelledby="shift-board-title">
        <div class="card-head"><div><div class="eyebrow">Integrated board · two separate synthetic cases</div><h2 id="shift-board-title">Shift changes, with provenance</h2></div><span id="shift-board-badge" class="tag blue">Loading live graph…</span></div>
  <p class="shift-intro">Dock ownership has two independent clocks: when a role is effective and when its source was known. Advancing knowledge never applies a future assignment early; unavailable historical knowledge stays unknown. Conveyor status is replayed from observed state events; the drill branch is hypothetical.</p>
        <div id="shift-board-empty" class="empty" hidden>Board empty / unknown. No incident, source, role, or review item is inferred from a blank graph. Reset the synthetic scenario to load the two pinned cases.</div>
        <div class="shift-cases">
          <article id="dock-case" class="shift-case" hidden aria-labelledby="dock-case-title">
            <header><div><div class="shift-kicker">Ownership revision · source change</div><h3 id="dock-case-title">INC-207 · Receiving dock</h3></div><span id="dock-role-badge" class="tag">Role unknown</span></header>
            <div class="shift-controls"><label for="dock-time">Effective at</label><select id="dock-time" aria-label="Dock ownership effective time"><option value="2026-10-14T20:29:00Z">20:29Z · before boundary</option><option value="2026-10-14T20:30:00Z" selected>20:30Z · boundary</option></select><label for="dock-known-time">Sources known by</label><select id="dock-known-time" aria-label="Dock source knowledge time"><option value="">Current knowledge · legacy view</option><option value="2026-10-14T20:17:59Z">20:17:59Z · earlier knowledge cutoff</option><option value="2026-10-14T20:19:00Z">20:19Z · later knowledge cutoff</option></select></div>
            <p id="dock-time-context" class="subtle" aria-live="polite"></p>
            <div id="dock-path" class="shift-result" aria-live="polite"></div>
            <div class="shift-evidence"><h4>Observed roster sources</h4><div id="dock-sources"></div></div>
            <div class="shift-evidence"><h4>Explicit-edge review queue</h4><div id="dock-review"></div><p id="dock-coverage" class="subtle"></p></div>
          </article>
          <article id="conveyor-case" class="shift-case" hidden aria-labelledby="conveyor-case-title">
            <header><div><div class="shift-kicker">Event replay · observed mainline</div><h3 id="conveyor-case-title">INC-204 · Conveyor C4</h3></div><span id="conveyor-status-badge" class="tag amber">State unknown</span></header>
            <div class="shift-controls" aria-label="Observed timeline controls"><button class="button" type="button" data-conveyor-at="2026-10-14T18:30:00Z">Observed · 18:30Z</button><button class="button" type="button" data-conveyor-at="2026-10-14T19:30:00Z">Restored · 19:30Z</button><button id="branch-reset" class="button" type="button">Reset branch overlay</button></div>
            <div id="conveyor-event" class="shift-result" aria-live="polite"></div>
            <div id="conveyor-paths" class="shift-result"></div>
            <div class="branch-box"><strong>HYPOTHETICAL · BR-C4-RESTORE-DRILL · 18:50Z</strong><p id="branch-result" aria-live="polite">Loading isolated branch…</p></div>
            <div class="shift-evidence"><h4>Path provenance · source facts</h4><div id="conveyor-sources"></div></div>
            <div class="shift-evidence"><h4>Evidence boundary</h4><p id="conveyor-evidence-label" class="subtle"></p></div>
          </article>
        </div>
        <div id="shift-board-notice" class="notice" aria-live="polite"></div>
      </section>
      <section id="sop-impact" class="card sop-impact" aria-labelledby="sop-impact-title">
        <div class="card-head"><div><div class="eyebrow">Graph-native evidence trace</div><h2 id="sop-impact-title">When an SOP conflict touches an outage</h2></div><span id="sop-impact-badge" class="tag amber">Loading linked evidence…</span></div>
        <p class="sop-impact-intro">Only explicit APPLIES_TO, SOP_CONFLICT and CLAIMS_ROLE edges with operations-visible source evidence join these records. SOP claims are not current owners, contacts, instructions, or a selected winner.</p>
        <div id="sop-impact-empty" class="sop-impact-empty" hidden>Empty / unknown. No incident path, SOP version, role claim, or source is inferred from a blank graph.</div>
        <div id="sop-impact-content" hidden>
          <div class="sop-impact-grid">
            <div class="sop-impact-pane"><h3>Observed outage impact · <span id="sop-impact-as-of"></span></h3><div id="sop-impact-event" class="subtle"></div><div id="sop-impact-paths"></div></div>
            <div class="sop-impact-pane"><h3>Explicit unresolved conflict witness</h3><div id="sop-impact-edge" class="sop-impact-edge"></div><div id="sop-impact-claims"></div></div>
          </div>
          <div id="sop-impact-stale" class="sop-impact-stale"></div>
          <p id="sop-impact-unknown" class="subtle" hidden></p>
        </div>
        <div id="sop-impact-notice" class="notice" aria-live="polite"></div>
      </section>
      <div id="legacy-grid" class="grid" hidden>
        <div class="stack">
          <section class="card" aria-labelledby="graph-title">
            <div class="card-head"><div><div class="eyebrow">Live FalkorDB relationship path</div><h2 id="graph-title">Impact map</h2></div><span id="impact-count" class="tag">Waiting for graph</span></div>
            <p class="subtle">Only connected, operations-visible paths are shown. The dashed link marks an unresolved source conflict; it is not a selected winner.</p>
            <div class="graph-wrap"><svg id="graph" class="graph-svg" viewBox="0 0 1000 380" role="img" aria-label="Graph path from outage incident through affected service to departments and current roles"></svg></div>
            <div class="graph-caption"><span class="legend"><i class="legend-line"></i>Live impact / ownership edge</span><span class="legend"><i class="legend-line conflict"></i>Unresolved SOP conflict</span><span>Evidence timestamps shown alongside each path</span></div>
            <div id="paths" class="path-list" aria-live="polite"></div>
          </section>
          <section class="card" aria-labelledby="evidence-title">
            <div class="card-head"><div><div class="eyebrow">Inspectable source trail</div><h2 id="evidence-title">Why these paths appear</h2></div><span class="tag blue">Facts ≠ inference</span></div>
            <div class="fact-groups">
              <div class="fact-group"><h3>Source facts</h3><ul id="facts" class="fact-list"></ul></div>
              <div class="fact-group inference"><h3>Graph inference</h3><ul id="inferences" class="fact-list"></ul></div>
              <div class="fact-group missing"><h3>Missing / uncertain evidence</h3><ul id="missing" class="fact-list"></ul></div>
            </div>
          </section>
        </div>
        <aside class="stack">
          <section class="card" aria-labelledby="sop-title">
            <div class="card-head"><div><div class="eyebrow">Freshness & disagreement</div><h2 id="sop-title">SOP-OPS-9</h2></div><span id="conflict-badge" class="tag amber">Conflict unresolved</span></div>
            <div id="sop-panel"><div class="empty">Loading version evidence…</div></div>
            <div id="sop-evidence" class="evidence-list" style="margin-top:13px"></div>
          </section>
          <section class="card" aria-labelledby="handover-title">
            <div class="card-head"><div><div class="eyebrow">Human-confirmed local action</div><h2 id="handover-title">Shift handover</h2></div><span class="tag">No external send</span></div>
            <p class="subtle">Prepare an evidence-linked draft from the current graph. Nothing is saved until you explicitly confirm.</p>
            <div class="handover-controls"><label for="handover-as-of">Evidence snapshot time · ISO-8601 with offset</label><input id="handover-as-of" type="text" value="2026-10-14T18:30:00Z" autocomplete="off" spellcheck="false" aria-describedby="handover-time-help"><span id="handover-time-help" class="subtle">The preview normalizes this instant to UTC. Invalid timestamps are rejected without a write.</span></div>
            <div class="handover-actions"><button id="prepare" class="button primary" type="button">Prepare handover draft</button></div>
            <div id="preview" class="preview" hidden></div>
            <div id="notice" class="notice" aria-live="polite"></div>
            <div class="safety"><strong>Local demo only.</strong> Confirmed drafts are written to this app's local state. This app sends no message, makes no call, creates no ticket, and performs no external write. Synthetic, non-clinical scenario; not a validated hospital system.</div>
          </section>
          <section class="card" aria-labelledby="saved-title">
            <div class="card-head"><div><div class="eyebrow">Confirmed locally</div><h2 id="saved-title">Handover board</h2></div><span id="saved-count" class="tag">0 saved</span></div>
            <div id="saved" class="saved-list"><div class="empty">No confirmed handovers yet.</div></div>
          </section>
        </aside>
      </div>
      <div class="footer"><span>WardAtlas · Northstar Demo Hospital (fictional)</span><span>Graph-backed retrieval + deterministic briefing template · no product model route configured</span></div>
    </main>
  </div>
  <script>
    const $ = id => document.getElementById(id);
    let briefing = null;
    let conveyorAt = '2026-10-14T18:30:00Z';
    const branchAt = '2026-10-14T18:50:00Z';
    let shiftRequestSequence = 0;
    function element(tag, cls, text) { const node=document.createElement(tag); if(cls) node.className=cls; if(text!==undefined) node.textContent=text; return node; }
    async function api(path, options={}) { const response=await fetch(path,{...options,headers:{'Content-Type':'application/json',...(options.headers||{})}}); const data=await response.json(); if(!response.ok) throw new Error(data.error||`HTTP ${response.status}`); return data; }
    function sourceName(id,title,observed) { const box=element('div','evidence-item'); box.append(element('strong','',title)); box.append(element('span','evidence-id',id)); box.append(element('span','',`Observed ${observed}`)); return box; }
    function renderShiftBoard(data) {
      const empty=data.board_state==='empty/unknown';
      $('shift-board-badge').textContent=empty?'Empty · unknown':'Live · two graph cases';
      $('shift-board-empty').hidden=!empty;
      $('dock-case').hidden=!data.dock; $('conveyor-case').hidden=!data.conveyor;
      if(!data.dock && !data.conveyor) return;
     if(data.dock) {
       const dock=data.dock; $('dock-role-badge').textContent=dock.role_id||'Role unknown';
        $('dock-time-context').textContent=dock.known_at
          ? `Effective at ${dock.effective_at} · knowledge limited to sources observed by ${dock.known_at}.`
          : `Effective at ${dock.as_of} · current knowledge (legacy single-axis view).`;
        const rolePath=$('dock-path'); rolePath.replaceChildren();
        if(dock.role_path.length) { rolePath.append(element('div','shift-path',dock.role_path.join('  →  '))); }
        else rolePath.append(element('div','shift-unknown','Ownership path is missing / unknown at this time.'));
        const sources=$('dock-sources'); sources.replaceChildren();
        for(const source of dock.source_versions) { const row=element('div','shift-source'); row.append(element('strong','',`${source.source_id} · ${source.title}`)); row.append(element('span','',`Observed ${source.observed_at}${source.effective_at?` · effective ${source.effective_at}`:''}${source.status?` · ${source.status}`:''}`)); sources.append(row); }
        const queue=$('dock-review'); queue.replaceChildren();
        if(!dock.review_candidates.length) queue.append(element('div','subtle','No explicitly linked review candidate is available.'));
        for(const item of dock.review_candidates) { const row=element('div','review-item',`${item.artifact_id} · cites ${item.cited_source_id}`); queue.append(row); }
        if(dock.unchanged_handover) queue.append(element('p','subtle',`Historical handover unchanged: ${dock.unchanged_handover.role_id} · ${dock.unchanged_handover.source_id}.`));
        $('dock-coverage').textContent=`Coverage: ${dock.coverage_status}. Unlinked artifact status: ${dock.unlinked_artifact_status}; no link is not evidence of currentness.${dock.candidate_disposition?` Review candidates remain ${dock.candidate_disposition}.`:''}`;
      }
      if(data.conveyor) {
        const conveyor=data.conveyor; $('conveyor-status-badge').textContent=conveyor.status;
        const event=$('conveyor-event'); event.replaceChildren();
        if(conveyor.event) {
          const observed=conveyor.event.evidence_label==='observed source fact';
          event.append(element('div',observed?'shift-result':'shift-unknown',`${observed?'Observed source fact':'Hypothetical event'} · ${conveyor.event.event_id} · ${conveyor.event.value} at ${conveyor.event.event_at}${conveyor.event.source_id?` · ${conveyor.event.source_id}`:''}`));
        } else event.append(element('div','shift-unknown','No effective state event; status is unknown, not safe or restored.'));
        const paths=$('conveyor-paths'); paths.replaceChildren();
        if(!conveyor.paths.length) paths.append(element('div',conveyor.status==='unknown'?'shift-unknown':'subtle',conveyor.status==='unknown'?'No active path is established from available evidence.':'No active impact paths at this observed time.'));
        for(const item of conveyor.paths) paths.append(element('div','shift-path',item.path.join('  →  ')));
        const sources=$('conveyor-sources'); sources.replaceChildren();
        for(const item of conveyor.paths) for(const source of item.evidence) {
          const row=element('div','shift-source'); row.append(element('strong','',`${source.source_id} · ${source.title}`)); row.append(element('span','',`Observed ${source.observed_at}`)); sources.append(row);
        }
        const branch=conveyor.branch; const branchBox=$('branch-result');
        if(!branch || !branch.overlay_present) branchBox.textContent='No branch overlay is seeded. Observed mainline is unchanged.';
        else branchBox.textContent=`${branch.branch_id} · ${branch.status} · ${branch.event?`${branch.event.event_id} at ${branch.event.event_at} · ${branch.event.evidence_label}`:'no branch event'} · ${branch.paths.length} branch impact paths. Mainline remains ${branch.mainline_status} with ${branch.mainline_paths.length} paths; observed state mutated: ${branch.observed_state_mutated}.`;
        $('conveyor-evidence-label').textContent='Observed event/source facts are separate from graph-derived impact paths. Branch assumptions are hypothetical only; an unknown or unlinked fact remains unknown.';
      }
    }
    function renderSopImpact(data) {
      const empty=data.board_state==='empty/unknown';
      $('sop-impact-empty').hidden=!empty; $('sop-impact-content').hidden=empty;
      $('sop-impact-badge').textContent=empty?'Empty · unknown':data.conflict_linkage_status==='linked-unresolved'?'Source-linked · unresolved':'Conflict linkage · unknown';
      if(empty) return;
      $('sop-impact-as-of').textContent=data.as_of;
      const eventBox=$('sop-impact-event'); eventBox.replaceChildren();
      if(data.observed_event) eventBox.append(element('div','',`Observed source fact · ${data.observed_event.event_id} · ${data.observed_event.value} at ${data.observed_event.event_at} · ${data.observed_event.source_id} · ${data.observed_event.source_title} · evidence observed ${data.observed_event.source_observed_at}`));
      else eventBox.append(element('div','','No source-backed observed state event is available.'));
      const paths=$('sop-impact-paths'); paths.replaceChildren();
      if(!data.impact_paths.length) paths.append(element('div',data.impact_state==='restored'?'subtle':'shift-unknown',data.impact_state==='restored'?'No active outage paths at this observed time. The SOP source conflict remains a separate unresolved fact.':data.observed_event?`An observed ${data.observed_event.value} event exists, but no complete graph-derived impact path is available; linkage is incomplete / unknown.`:'No active path is established; impact is unknown, not safe.'));
      for(const [index,path] of data.impact_paths.entries()) {
        const row=element('div','sop-impact-path'); row.append(document.createTextNode(path.join('  →  ')));
        const evidence=data.path_evidence[index];
        const evidenceTrail=evidence.source_ids.map((id,sourceIndex)=>`${id} · ${evidence.source_titles[sourceIndex]} · observed ${evidence.observed_at[sourceIndex]}`).join(' → ');
        row.append(element('small','',`CURRENT OWNER PATH · ${evidenceTrail}`)); paths.append(row);
      }
      const edge=$('sop-impact-edge'); edge.replaceChildren();
      if(data.conflict_edge) {
        const apps=data.applicability_edges.map(item=>`${item.from} -[${item.relationship}]-> ${item.to}`).join(' · ');
        edge.append(element('div','',apps));
        edge.append(element('div','',`${data.conflict_edge.from} -[${data.conflict_edge.relationship} · ${data.conflict_edge.status}]-> ${data.conflict_edge.to}`));
      } else edge.append(element('div','shift-unknown','Conflict linkage is incomplete / unknown. No absence or resolution is inferred.'));
      const claims=$('sop-impact-claims'); claims.replaceChildren();
      if(!data.role_claims.length) claims.append(element('div','subtle','No source-backed role claim is available.'));
      for(const claim of data.role_claims) {
        const row=element('div','sop-impact-claim');
        row.append(element('strong','',`${claim.sop_version_id} · ${claim.claim_label} → ${claim.role_id}`));
        row.append(element('span','',`${claim.claim_relationship} · ${claim.source_id} · ${claim.source_title} · observed ${claim.observed_at}`)); claims.append(row);
      }
      const stale=data.stale_version;
      $('sop-impact-stale').textContent=stale?`Archived history · ${stale.id} references retired ${stale.referenced_equipment_id} · ${stale.source_id} · observed ${stale.observed_at}. Visible history, not joined to the C4 conflict witness.`:'No archived SOP version is visible in this graph.';
      const unknown=$('sop-impact-unknown'); unknown.hidden=Boolean(data.complete_conflict_witness_returned); unknown.textContent=data.complete_conflict_witness_returned?'':`No complete conflict-to-impact witness is available. Status remains ${data.conflict_linkage_status}; no “no conflict” or “resolved” conclusion is asserted.`;
    }
    async function loadShiftBoard() {
      const sequence=++shiftRequestSequence;
      const knownAt=$('dock-known-time').value;
      const dockAxis=knownAt?{effective_at:$('dock-time').value,known_at:knownAt}:{dock_at:$('dock-time').value};
      const params=new URLSearchParams({scope:'operations',...dockAxis,conveyor_at:conveyorAt,branch_at:branchAt});
      const board=await api(`/api/shift-board?${params.toString()}`);
      if(sequence!==shiftRequestSequence) return board;
      renderShiftBoard(board);
      const impactParams=new URLSearchParams({scope:'operations',as_of:conveyorAt});
      loadSopImpact(impactParams.toString()); return board;
    }
    async function loadSopImpact(query) {
      try {
        const impact=await api(`/api/sop-impact?${query}`); renderSopImpact(impact);
        $('sop-impact-notice').textContent='';
      } catch(e) {
        $('sop-impact-badge').textContent='Trace unavailable';
        $('sop-impact-empty').hidden=true; $('sop-impact-content').hidden=true;
        $('sop-impact-notice').textContent=`SOP impact trace unavailable: ${e.message}. The existing briefing and shift board remain available.`;
      }
    }
    function renderPaths(data) {
      const svg=$('graph'); svg.replaceChildren();
      const positions={
        'INC-204':[85,185], 'CONVEYOR-C4':[270,185], 'SVC-MATERIAL-TRANSFER':[465,185],
        'DEPT-CENTRAL-STERILE':[655,110], 'ROLE-STERILE-OPS-COORD':[875,110],
        'DEPT-MATERIALS-LOGISTICS':[655,260], 'ROLE-MATERIALS-DUTY':[875,260],
        'SOP-OPS-9-V2':[385,340], 'SOP-OPS-9-V3':[605,340]
      };
      const labels={
        'INC-204':['OPEN INCIDENT','INC-204'], 'CONVEYOR-C4':['UNAVAILABLE EQUIPMENT','Material transfer C4'],
        'SVC-MATERIAL-TRANSFER':['SERVICE','Material transfer'], 'DEPT-CENTRAL-STERILE':['AFFECTED DEPARTMENT','Central sterile ops'],
        'ROLE-STERILE-OPS-COORD':['CURRENT ROLE','Sterile ops coordinator'], 'DEPT-MATERIALS-LOGISTICS':['AFFECTED DEPARTMENT','Materials logistics'],
        'ROLE-MATERIALS-DUTY':['CURRENT ROLE','Materials duty role'], 'SOP-OPS-9-V2':['UNVERIFIED SOP CLAIM','Facilities duty'],
        'SOP-OPS-9-V3':['UNVERIFIED SOP CLAIM','Materials lead']
      };
      const ns='http://www.w3.org/2000/svg';
      for(const edge of data.edges) {
        const a=positions[edge.source], b=positions[edge.target]; if(!a||!b) continue;
        const line=document.createElementNS(ns,'line'); line.setAttribute('x1',a[0]+65); line.setAttribute('y1',a[1]); line.setAttribute('x2',b[0]-65); line.setAttribute('y2',b[1]); line.setAttribute('class',`graph-edge ${edge.kind==='OWNED_BY'?'ownership':''} ${edge.kind==='SOP_CONFLICT'?'conflict':''}`); svg.append(line);
      }
      for(const node of data.nodes) {
        const pos=positions[node.id]; if(!pos) continue;
        const group=document.createElementNS(ns,'g'); group.setAttribute('class',`graph-node ${node.kind.toLowerCase()}`);
        const rect=document.createElementNS(ns,'rect'); rect.setAttribute('x',pos[0]-65); rect.setAttribute('y',pos[1]-27); rect.setAttribute('width','130'); rect.setAttribute('height','54'); group.append(rect);
        const lines=labels[node.id]||[node.kind.toUpperCase(),node.id];
        const top=document.createElementNS(ns,'text'); top.setAttribute('x',pos[0]); top.setAttribute('y',pos[1]-5); top.setAttribute('class','kind'); top.textContent=lines[0]; group.append(top);
        const bottom=document.createElementNS(ns,'text'); bottom.setAttribute('x',pos[0]); bottom.setAttribute('y',pos[1]+13); bottom.textContent=lines[1]; group.append(bottom); svg.append(group);
      }
      const pathBox=$('paths'); pathBox.replaceChildren();
      if(!briefing.impact_paths.length) { pathBox.append(element('div','empty','No open outage impact paths in this graph. Reset the synthetic scenario to reseed it.')); return; }
      for(const path of briefing.impact_paths) {
        const row=element('div','path-row'); const left=element('div');
        left.append(element('strong','',`${path.department_name} ← ${path.service_name}`));
        left.append(element('div','path-meta',path.path.join('  →  ')));
        row.append(left); row.append(element('div','role-label',`CURRENT · ${path.role_name}`)); pathBox.append(row);
      }
    }
    function renderBriefing(data, graph) {
      briefing=data;
      $('legacy-grid').hidden=!data.incident;
      $('main-title').textContent=data.incident?'One outage. Two accountable paths.':'A board with no defaults.';
      $('hero-description').textContent=data.incident?'Trace the conveyor outage through service dependencies to the operating roles that are explicitly current. Inspect the evidence behind each hop before preparing a local handover.':'The local graph has not established an incident or accountable path. Reset the synthetic scenario to load its pinned cases.';
      $('incident-status').textContent=data.incident?'Open operations incident':'No graph-backed incident';
      $('incident-title').textContent=data.incident ? `${data.incident.id} · ${data.incident.status.toUpperCase()}` : 'No open incident in graph';
      $('incident-summary').textContent=data.incident ? `${data.incident.equipment_name} is unavailable. Synthetic operations evidence only; no patient or clinical information.` : 'The graph is empty. Reset the synthetic scenario to load the fictional outage.';
      $('impact-count').textContent=`${data.impact_paths.length} impact paths`;
      renderPaths(graph);
      const facts=$('facts'); facts.replaceChildren();
      for(const item of data.source_facts) { const li=element('li','',item.statement); li.append(document.createTextNode(` · ${item.evidence.source_id} (${item.evidence.observed_at})`)); facts.append(li); }
      const infer=$('inferences'); infer.replaceChildren(); for(const item of data.inferences) infer.append(element('li','',item.statement));
      const missing=$('missing'); missing.replaceChildren(); for(const item of data.missing_evidence) missing.append(element('li','',item));
      const panel=$('sop-panel'); panel.replaceChildren(); const evidence=$('sop-evidence'); evidence.replaceChildren();
      if(!data.sop_finding) { panel.append(element('div','empty','No SOP evidence found in this graph.')); }
      else {
        const stale=element('div','sop-stale'); stale.append(element('h3','',`Stale reference · ${data.sop_finding.stale_version.id}`));
        stale.append(element('p','subtle',`References replaced equipment ${data.sop_finding.stale_version.referenced_equipment_id}. Not a current instruction.`)); panel.append(stale);
        const current=element('div','sop-current'); current.append(element('h3','',`Two current versions · ${data.sop_finding.conflict_status}`));
        const claims=element('div','claim-list'); for(const version of data.sop_finding.current_versions) { const claim=element('div','claim'); claim.append(element('strong','',`${version.id} claims ${version.escalation_role_claim}`)); claim.append(element('span','tag amber','UNRESOLVED')); claims.append(claim); } current.append(claims); panel.append(current);
        for(const item of data.sop_finding.evidence) evidence.append(sourceName(item.source_id,item.title,item.observed_at));
      }
    }
     async function loadAll() {
      const [health, data, graph, actions, shiftBoard]=await Promise.all([api('/api/health'),api('/api/briefing?scope=operations'),api('/api/graph?scope=operations'),api('/api/actions'),loadShiftBoard()]);
      $('db-status').innerHTML='<span class="dot"></span>Live FalkorDB · local';
      renderBriefing(data,graph); renderActions(actions.actions);
    }
    function renderHandoverSnapshot(snapshot, saved=false) {
      const root=element('section',saved?'handover-snapshot saved-snapshot':'handover-snapshot');
      if(!saved) root.id='handover-snapshot';
      root.dataset.snapshot=JSON.stringify(snapshot); root.dataset.asOf=snapshot.as_of;
      root.append(element('h4','',`Graph witness snapshot · ${snapshot.as_of}`));
      root.append(element('div','handover-witness-status',`Impact · ${snapshot.impact.status}`));
      root.append(element('h5','','Connected impact paths'));
      if(!snapshot.impact.paths.length) root.append(element('p','subtle','No active graph path is present at this time; this does not establish safety or absence of risk.'));
      for(const item of snapshot.impact.path_evidence) {
        root.append(element('div','handover-path',item.path.join('  →  ')));
        for(let i=0;i<item.source_ids.length;i++) root.append(element('div','handover-source',`${item.source_ids[i]} · ${item.source_titles[i]} · observed ${item.observed_at[i]}`));
      }
      const witness=snapshot.sop_witness;
      root.append(element('h5','','SOP-OPS-9 relationship witness'));
      if(!witness) root.append(element('div','handover-witness-status','Linkage unknown · no complete source-backed witness is available.'));
      else {
        root.append(element('div','handover-witness-status',`${witness.linkage_status} · ${witness.conflict_status} · shared equipment ${witness.shared_equipment_id||'unknown'}`));
        for(const edge of witness.applicability_edges) root.append(element('div','handover-source',`${edge.from} —[${edge.relationship}]→ ${edge.to}`));
        if(witness.conflict_edge) root.append(element('div','handover-source',`${witness.conflict_edge.from} —[${witness.conflict_edge.relationship}]→ ${witness.conflict_edge.to} · ${witness.conflict_edge.status}`));
        for(const claim of witness.claim_evidence) root.append(element('div','handover-claim',`${claim.sop_version_id} · ${claim.claim_label} · ${claim.role_id} · ${claim.source_id} · ${claim.source_title} · observed ${claim.observed_at}`));
        const stale=witness.stale_version_excluded_from_c4_witness;
        if(stale) root.append(element('div','handover-source',`Archived history excluded from this C4 witness: ${stale.version_id} · ${stale.source_id} · ${stale.source_title} · observed ${stale.observed_at} · references ${stale.referenced_equipment_id}`));
      }
      return root;
    }
    function renderActions(actions) {
      $('saved-count').textContent=`${actions.length} saved`;
      const root=$('saved'); root.replaceChildren();
      if(!actions.length) { root.append(element('div','empty','No confirmed handovers yet.')); return; }
      for(const item of actions.slice().reverse()) { const card=element('div','saved-item'); card.append(element('strong','',`${item.id} · ${item.incident_id}`)); card.append(element('p','',`Confirmed ${item.confirmed_at}. Stored only in local app state.`)); if(item.snapshot) card.append(renderHandoverSnapshot(item.snapshot,true)); root.append(card); }
    }
    $('reset').addEventListener('click',async()=>{ const button=$('reset'); button.disabled=true; $('notice').textContent='Resetting the owned synthetic scenario…'; try { await api('/api/scenario/reset',{method:'POST',body:'{}'}); $('preview').hidden=true; $('preview').replaceChildren(); $('notice').textContent='Synthetic scenario reseeded from deterministic fixtures.'; await loadAll(); } catch(e) { $('notice').textContent=`Reset failed: ${e.message}`; } finally { button.disabled=false; } });
    for(const input of [$('dock-time'),$('dock-known-time')]) input.addEventListener('change',()=>loadShiftBoard().catch(e=>$('shift-board-notice').textContent=`Board unavailable: ${e.message}`));
    for(const button of document.querySelectorAll('[data-conveyor-at]')) button.addEventListener('click',()=>{ conveyorAt=button.dataset.conveyorAt; loadShiftBoard().catch(e=>$('shift-board-notice').textContent=`Board unavailable: ${e.message}`); });
    $('branch-reset').addEventListener('click',async()=>{ const button=$('branch-reset'); button.disabled=true; try { await api('/api/shift-board/branch/reset',{method:'POST',body:'{}'}); $('shift-board-notice').textContent='Only the named hypothetical overlay was reset; observed events were preserved.'; await loadShiftBoard(); } catch(e) { $('shift-board-notice').textContent=`Branch reset failed: ${e.message}`; } finally { button.disabled=false; } });
    $('prepare').addEventListener('click',async()=>{ const button=$('prepare'); button.disabled=true; $('notice').textContent='Preparing a source-linked draft…'; try {
      const draft=await api('/api/handover/draft',{method:'POST',body:JSON.stringify({incident_id:'INC-204',as_of:$('handover-as-of').value})}); const box=$('preview'); box.replaceChildren(); box.append(element('h3','',`Preview · ${draft.incident_id}`)); box.append(element('p','',draft.summary)); box.append(renderHandoverSnapshot(draft.snapshot));
      const list=element('ul','task-list'); for(const task of draft.proposed_actions) list.append(element('li','',`${task.text} [${task.source_ids.join(', ')}]`)); box.append(list);
      box.append(element('p','subtle','This preview is not persisted. Confirm below to save it locally.'));
      const confirm=element('button','button primary','Confirm & save locally'); confirm.type='button'; confirm.id='confirm-handover'; confirm.addEventListener('click',async()=>{ confirm.disabled=true; try { const result=await api('/api/handover/confirm',{method:'POST',body:JSON.stringify({token:draft.confirmation_token,confirm:true})}); $('notice').textContent=`Saved locally as ${result.action.id}. No external action was taken.`; box.hidden=true; await loadAll(); } catch(e) { $('notice').textContent=`Not saved: ${e.message}`; confirm.disabled=false; } }); box.append(confirm); box.hidden=false; $('notice').textContent='Draft prepared in memory only. Nothing has been saved.';
    } catch(e) { $('notice').textContent=`Could not prepare draft: ${e.message}`; } finally { button.disabled=false; } });
    loadAll().catch(e=>{ $('db-status').textContent='Local graph unavailable'; $('notice').textContent=`Could not load graph-backed briefing: ${e.message}`; });
  </script>
</body>
</html>'''


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


class WardAtlasService:
    def __init__(self, db_port: int, graph_name: str, state_dir: Path):
        if not (1024 <= db_port <= 65535):
            raise ValueError("database port must be an explicitly selected high port")
        if not GRAPH_NAME_RE.fullmatch(graph_name):
            raise ValueError("graph name must use the wardatlas_ prefix and safe characters")
        resolved_state = state_dir.resolve()
        if not resolved_state.is_relative_to(ROOT):
            raise ValueError("local action state must remain inside the project workspace")
        self.db_port = db_port
        self.graph_name = graph_name
        self.state_dir = resolved_state
        self.action_file = self.state_dir / "confirmed-actions.json"
        self.graph_lock = threading.RLock()
        self.action_lock = threading.RLock()
        self.pending: dict[str, dict] = {}
        self.scenario_seeded_by_this_process = False

    def _connection(self) -> RespConnection:
        return RespConnection("127.0.0.1", self.db_port, timeout=3.0)

    def _rows(self, connection: RespConnection, cypher: str) -> list[dict]:
        return result_rows(query_result(connection.command("GRAPH.QUERY", self.graph_name, cypher)))

    def health(self) -> dict:
        with self.graph_lock:
            connection = self._connection()
            try:
                modules = module_list_maps(connection.command("MODULE", "LIST"))
                module = next((row for row in modules if str(row.get("name", "")).lower() in {"graph", "falkordb"}), None)
                if not module or int(module.get("ver", 0)) != 60002:
                    raise RuntimeError("FalkorDB 6.0.2 graph module is not available")
                rows = self._rows(connection, "RETURN 1 AS ready")
                if len(rows) != 1 or int(rows[0].get("ready", 0)) != 1:
                    raise RuntimeError("FalkorDB graph query health check failed")
                return {"status": "ok", "database": "FalkorDB", "module_version": int(module["ver"]), "scope": "operations", "external_integrations": []}
            finally:
                connection.close()

    def _briefing_rows(self) -> tuple[list[dict], list[dict]]:
        with self.graph_lock:
            connection = self._connection()
            try:
                return self._rows(connection, IMPACT_QUERY), self._rows(connection, SOP_CONFLICT_QUERY)
            finally:
                connection.close()

    def briefing(self) -> dict:
        impact_rows, sop_rows = self._briefing_rows()
        paths = []
        source_facts: list[dict] = []
        fact_keys: set[tuple] = set()
        inferences = []
        for row in impact_rows:
            path = [row["incident_id"], row["equipment_id"], row["service_id"], row["department_id"], row["role_id"]]
            evidence = [
                {"source_id": row[f"{prefix}_source_id"], "title": row[f"{prefix}_source_title"], "observed_at": row[f"{prefix}_observed_at"]}
                for prefix in ("incident", "equipment", "service")
            ] + [{"source_id": row["ownership_source_id"], "title": row["ownership_source_title"], "observed_at": row["ownership_observed_at"]}]
            paths.append({
                "path": path,
                "incident_id": row["incident_id"],
                "incident_status": row["incident_status"],
                "equipment_id": row["equipment_id"],
                "equipment_status": row["equipment_status"],
                "equipment_name": row["equipment_name"],
                "service_id": row["service_id"],
                "service_name": row["service_name"],
                "department_id": row["department_id"],
                "department_name": row["department_name"],
                "role_id": row["role_id"],
                "role_name": row["role_name"],
                "role_is_current": True,
                "evidence": evidence,
            })
            fact_specs = [
                (row["incident_id"], f"Incident {row['incident_id']} is {row['incident_status']}.", evidence[0]),
                (row["equipment_id"], f"{row['equipment_name']} ({row['equipment_id']}) is {row['equipment_status']}.", evidence[1]),
                (row["service_id"], f"{row['service_name']} lists the affected equipment dependency.", evidence[2]),
            ]
            for key, statement, source in fact_specs:
                if key not in fact_keys:
                    source_facts.append({"statement": statement, "evidence": source})
                    fact_keys.add(key)
            inferences.append({
                "statement": f"The connected outage path reaches {row['department_name']} through {row['service_name']}; {row['role_name']} is the explicitly current synthetic operating role on the ownership edge.",
                "basis_path": path,
                "basis_source_ids": [entry["source_id"] for entry in evidence],
            })

        sop = None
        if sop_rows:
            raw = sop_rows[0]
            sop = {
                "stale_version": {
                    "id": raw["stale_version_id"],
                    "referenced_equipment_id": raw["stale_equipment_id"],
                    "source_id": raw["stale_source_id"],
                    "source_title": raw["stale_source_title"],
                    "observed_at": raw["stale_observed_at"],
                },
                "current_versions": [
                    {"id": raw["current_v2_id"], "escalation_role_claim": raw["current_v2_role_claim"], "source_id": raw["current_v2_source_id"], "source_title": raw["current_v2_source_title"], "observed_at": raw["current_v2_observed_at"]},
                    {"id": raw["current_v3_id"], "escalation_role_claim": raw["current_v3_role_claim"], "source_id": raw["current_v3_source_id"], "source_title": raw["current_v3_source_title"], "observed_at": raw["current_v3_observed_at"]},
                ],
                "conflict_status": raw["conflict_status"],
                "authoritative_claim": None,
                "evidence": [
                    {"source_id": raw["stale_source_id"], "title": raw["stale_source_title"], "observed_at": raw["stale_observed_at"]},
                    {"source_id": raw["current_v2_source_id"], "title": raw["current_v2_source_title"], "observed_at": raw["current_v2_observed_at"]},
                    {"source_id": raw["current_v3_source_id"], "title": raw["current_v3_source_title"], "observed_at": raw["current_v3_observed_at"]},
                ],
            }
        source_facts.sort(key=lambda item: item["evidence"]["source_id"])
        return {
            "scope": "operations",
            "incident": ({"id": impact_rows[0]["incident_id"], "status": impact_rows[0]["incident_status"], "equipment_name": impact_rows[0]["equipment_name"]} if impact_rows else None),
            "impact_paths": paths,
            "source_facts": source_facts,
            "inferences": inferences,
            "missing_evidence": ([
                "No restoration time or estimate appears in the retrieved outage evidence.",
                "The two current SOP escalation claims conflict; the sources do not establish a single authoritative claim.",
            ] if sop else ["No SOP finding is available in the current graph; do not infer an escalation owner."]),
            "sop_finding": sop,
            "answer_method": "Live FalkorDB query plus deterministic, source-linked template; no product model route configured.",
        }

    def graph_view(self) -> dict:
        data = self.briefing()
        nodes: dict[str, dict] = {}
        edges: dict[tuple[str, str, str], dict] = {}
        for path in data["impact_paths"]:
            attributes = [
                (path["incident_id"], "Incident", path["incident_id"], None),
                (path["equipment_id"], "Equipment", path["equipment_name"], None),
                (path["service_id"], "Service", path["service_name"], None),
                (path["department_id"], "Department", path["department_name"], None),
                (path["role_id"], "Role", path["role_name"], True),
            ]
            for identifier, kind, name, current in attributes:
                nodes[identifier] = {"id": identifier, "kind": kind, "name": name, **({"current": current} if current is not None else {})}
            for index in range(4):
                edge_kind = ["AFFECTS", "SUPPORTS", "SERVES", "OWNED_BY"][index]
                key = (path["path"][index], path["path"][index + 1], edge_kind)
                edges[key] = {"source": key[0], "target": key[1], "kind": edge_kind}
        sop = data["sop_finding"]
        if sop:
            for version in [sop["stale_version"], *sop["current_versions"]]:
                nodes[version["id"]] = {"id": version["id"], "kind": "SOPVersion", "name": version["id"], "current": version["id"] in {v["id"] for v in sop["current_versions"]}}
            edges[(sop["current_versions"][0]["id"], sop["current_versions"][1]["id"], "SOP_CONFLICT")] = {
                "source": sop["current_versions"][0]["id"], "target": sop["current_versions"][1]["id"], "kind": "SOP_CONFLICT", "status": "unresolved"
            }
        return {"scope": "operations", "nodes": list(nodes.values()), "edges": list(edges.values()), "impact_path_count": len(data["impact_paths"]), "sop_conflict_status": sop["conflict_status"] if sop else None}

    def sop_impact(self, as_of: str = CONVEYOR_OUTAGE_AT) -> dict:
        """Return the edge-sensitive, source-linked SOP-to-impact trace.

        Timestamp validation is completed before any FalkorDB query. The
        outage timeline and SOP relationship witness are deliberately queried
        separately so observed restoration cannot resolve a source conflict.
        """
        normalized = normalize_timestamp(as_of)
        board = self.shift_board(DOCK_EFFECTIVE, normalized, BRANCH_AT)
        with self.graph_lock:
            connection = self._connection()
            try:
                claims = self._rows(connection, SOP_IMPACT_CLAIMS_QUERY)
                witness = self._rows(connection, CONFLICT_WITNESS_QUERY)
                stale = self._rows(connection, STALE_QUERY)
            finally:
                connection.close()
        return assemble_sop_impact_response(
            normalized,
            board.get("conveyor"),
            claims,
            witness,
            stale,
        )

    @staticmethod
    def _event_order(row: dict) -> datetime:
        return datetime.fromisoformat(row["event_at"].replace("Z", "+00:00"))

    @staticmethod
    def _event_view(row: dict, hypothetical: bool = False) -> dict:
        event = {
            "event_id": row["event_id"],
            "value": row["value"],
            "event_at": row["event_at"],
            "evidence_label": "hypothetical branch" if hypothetical else "observed source fact",
        }
        if hypothetical:
            event["branch_id"] = row["branch_id"]
        else:
            event.update({
                "source_id": row["source_id"],
                "source_title": row["source_title"],
                "source_observed_at": row["source_observed_at"],
            })
        return event

    @staticmethod
    def _conveyor_paths(rows: list[dict], active: bool) -> list[dict]:
        if not active:
            return []
        paths = []
        for row in rows:
            path = [row["incident_id"], row["equipment_id"], row["service_id"], row["department_id"], row["role_id"]]
            evidence = [
                {"source_id": row["incident_source_id"], "title": row["incident_source_title"], "observed_at": row["incident_observed_at"]},
                {"source_id": row["equipment_source_id"], "title": row["equipment_source_title"], "observed_at": row["equipment_observed_at"]},
                {"source_id": row["service_source_id"], "title": row["service_source_title"], "observed_at": row["service_observed_at"]},
                {"source_id": row["ownership_source_id"], "title": row["ownership_source_title"], "observed_at": row["ownership_observed_at"]},
            ]
            paths.append({"path": path, "evidence": evidence})
        return paths

    def shift_board(
        self,
        dock_at: str = DOCK_EFFECTIVE,
        conveyor_at: str = CONVEYOR_OUTAGE_AT,
        branch_at: str = BRANCH_AT,
        known_at: str | None = None,
    ) -> dict:
        """Query the board with independent valid/knowledge time for dock ownership.

        Omitting ``known_at`` deliberately preserves the pre-existing
        historical-effective/current-knowledge response shape.
        """
        dock_at = normalize_timestamp(dock_at)
        conveyor_at = normalize_timestamp(conveyor_at)
        branch_at = normalize_timestamp(branch_at)
        known_at = normalize_timestamp(known_at) if known_at is not None else None
        with self.graph_lock:
            connection = self._connection()
            try:
                dock_paths = self._rows(connection, dock_role_query(dock_at, known_at))
                dock_sources = self._rows(connection, dock_source_query(known_at))
                supersession_rows = self._rows(connection, dock_supersession_query(known_at))
                review_rows = self._rows(connection, dock_review_query(known_at))
                handover_rows = self._rows(connection, DOCK_HANDOVER_SNAPSHOT_QUERY)
                observed_events = self._rows(connection, CONVEYOR_EVENT_QUERY)
                branch_events = self._rows(connection, BRANCH_EVENT_QUERY)
                conveyor_rows = self._rows(connection, CONVEYOR_PATH_QUERY)
            finally:
                connection.close()

        supersedes = {row["source_id"]: row["supersedes"] for row in supersession_rows}
        dock_case_present = bool(dock_paths or dock_sources or review_rows)
        dock_case = None
        if dock_case_present:
            source_versions = []
            for row in dock_sources:
                source = {
                    "source_id": row["source_id"],
                    "title": row["title"],
                    "observed_at": row["observed_at"],
                }
                if row.get("effective_at"):
                    source["effective_at"] = row["effective_at"]
                if row["source_id"] in supersedes:
                    source["supersedes"] = supersedes[row["source_id"]]
                if known_at is not None and row["source_id"] == "SRC-DOCK-ROSTER-R2":
                    source["status"] = "recorded/pending"
                source_versions.append(source)
            handover = handover_rows[0] if handover_rows else None
            role_path = []
            role_id = None
            if dock_paths:
                row = dock_paths[0]
                role_path = [row["incident_id"], row["equipment_id"], row["service_id"], row["department_id"], row["role_id"]]
                role_id = row["role_id"]
            dock_case = {
                "incident_id": "INC-207",
                "as_of": dock_at,
                "role_path": role_path,
                "role_id": role_id,
                "source_versions": source_versions,
                "review_candidates": review_rows,
                "coverage_status": "explicit_edges_only",
                "unlinked_artifact_status": "unknown",
                "unchanged_handover": ({"artifact_id": handover["artifact_id"], "role_id": handover["role_id"], "source_id": handover["source_id"]} if handover else None),
                "role_evidence": ([{"source_id": row["source_id"], "title": row["source_title"], "observed_at": row["source_observed_at"], **({"effective_at": row["source_effective_at"]} if row.get("source_effective_at") else {})} for row in dock_paths]),
            }
            if known_at is not None:
                dock_case.update({
                    "effective_at": dock_at,
                    "known_at": known_at,
                    "knowledge_mode": "as_known_at",
                    "role_status": "known" if role_id is not None else "unknown",
                    "linkage_status": "linked" if "SRC-DOCK-ROSTER-R2" in supersedes else "unknown",
                    "currentness_conclusion": False,
                })
                if any(source["source_id"] == "SRC-DOCK-ROSTER-R2" for source in source_versions):
                    dock_case["candidate_disposition"] = "pending review; not declared-invalid artifacts"
                if role_id == "ROLE-DOCK-SWING":
                    dock_case["interpretation"] = (
                        "ROLE-DOCK-SWING is valid at effective_at and known by known_at; "
                        "this does not claim swing was effective at known_at"
                    )

        observed_before_main = [row for row in observed_events if row["event_at"] <= conveyor_at]
        main_event_row = max(observed_before_main, key=self._event_order) if observed_before_main else None
        main_status = (
            "unknown" if main_event_row is None
            else "observed-impact" if main_event_row["value"] == "unavailable"
            else "restored"
        )
        main_event = self._event_view(main_event_row) if main_event_row else None
        main_paths = self._conveyor_paths(conveyor_rows, main_status == "observed-impact")

        observed_before_branch = [row for row in observed_events if row["event_at"] <= branch_at]
        branch_rows = [
            {**row, "_hypothetical": False}
            for row in observed_before_branch
        ] + [
            {**row, "_hypothetical": True}
            for row in branch_events
            if row["event_at"] <= branch_at
        ]
        overlay_present = bool(branch_events)
        branch_event_row = max(branch_rows, key=self._event_order) if branch_rows and overlay_present else None
        branch_status = (
            "unknown" if branch_event_row is None
            else "hypothetical-restored" if branch_event_row.get("_hypothetical") and branch_event_row["value"] == "available"
            else "hypothetical-impact" if branch_event_row.get("_hypothetical")
            else "observed-impact" if branch_event_row["value"] == "unavailable"
            else "restored"
        )
        branch_event = self._event_view(branch_event_row, hypothetical=bool(branch_event_row and branch_event_row.get("_hypothetical"))) if branch_event_row else None
        branch_paths = self._conveyor_paths(conveyor_rows, branch_event_row is not None and branch_event_row["value"] == "unavailable")
        branch_main_event = max(observed_before_branch, key=self._event_order) if observed_before_branch else None
        branch_main_status = (
            "unknown" if branch_main_event is None
            else "observed-impact" if branch_main_event["value"] == "unavailable"
            else "restored"
        )
        branch_main_paths = self._conveyor_paths(conveyor_rows, branch_main_status == "observed-impact")
        branch_case = {
            "branch_id": BRANCH_ID,
            "as_of": branch_at,
            "overlay_present": overlay_present,
            "status": branch_status,
            "event": branch_event,
            "paths": branch_paths,
            "mainline_status": branch_main_status,
            "mainline_paths": branch_main_paths,
            "observed_state_mutated": False,
        }
        conveyor_present = bool(observed_events or conveyor_rows)
        conveyor_case = None
        if conveyor_present:
            conveyor_case = {
                "incident_id": "INC-204",
                "equipment_id": "CONVEYOR-C4",
                "as_of": conveyor_at,
                "status": main_status,
                "event": main_event,
                "paths": main_paths,
                "branch": branch_case,
                "evidence_semantics": {
                    "observed_event": "source fact",
                    "paths": "graph-derived path",
                    "branch_event": "hypothetical branch",
                    "missing_or_unlinked": "missing or unknown evidence",
                },
            }

        cases = [case_id for case_id, present in (("INC-207", dock_case_present), ("INC-204", conveyor_present)) if present]
        return {
            "scope": "operations",
            "board_state": "ready" if cases else "empty/unknown",
            "seeded_board_row_count": len(cases),
            "case_ids": cases,
            "cases": ([dock_case] if dock_case else []) + ([conveyor_case] if conveyor_case else []),
            "dock": dock_case,
            "conveyor": conveyor_case,
        }

    def reset_shift_board_branch(self) -> dict:
        if not self.scenario_seeded_by_this_process:
            raise RuntimeError("Refusing to reset a branch in a graph not seeded by this WardAtlas process.")
        with self.graph_lock:
            connection = self._connection()
            try:
                existing = self._rows(connection, f"MATCH (branch:ScenarioBranch {{id: '{BRANCH_ID}'}}) RETURN branch.id AS id")
                for query in branch_reset_queries():
                    query_result(connection.command("GRAPH.QUERY", self.graph_name, query))
            finally:
                connection.close()
        return {"status": "reset", "branch_id": BRANCH_ID, "overlay_removed": bool(existing), "external_effects": []}

    def reset_scenario(self) -> dict:
        with self.graph_lock:
            connection = self._connection()
            try:
                if self.scenario_seeded_by_this_process:
                    # Only this process may clear a graph after it has seeded
                    # that graph from the deterministic fixture itself.
                    connection.command("GRAPH.DELETE", self.graph_name)
                else:
                    existing = self._rows(connection, "MATCH (n) RETURN n.id AS id LIMIT 1")
                    if existing:
                        raise RuntimeError("Refusing to reset a nonempty graph not seeded by this WardAtlas process.")
                for query in [*seed_queries(), *seed_shift_board_queries()]:
                    query_result(connection.command("GRAPH.QUERY", self.graph_name, query))
                self.scenario_seeded_by_this_process = True
            finally:
                connection.close()
        with self.action_lock:
            self.pending.clear()
        return {"status": "reset", "scenario": "INC-204 synthetic outage", "scope": "operations", "included_shift_board_cases": ["INC-207", "INC-204"]}

    def saved_actions(self) -> list[dict]:
        with self.action_lock:
            if not self.action_file.exists():
                return []
            value = json.loads(self.action_file.read_text(encoding="utf-8"))
            if value.get("schema_version") != 1 or not isinstance(value.get("items"), list):
                raise RuntimeError("local action state has an unsupported format")
            return value["items"]

    def preview_handover(self, incident_id: str, as_of: str = CONVEYOR_OUTAGE_AT) -> dict:
        if incident_id != "INC-204":
            raise ValueError("Only the pinned synthetic incident is available in this demo.")
        # Validate and query through the accepted read-only SOP-impact service
        # before creating even an in-memory pending draft. The handover only
        # projects that service result; graph traversal stays in one place.
        impact = self.sop_impact(as_of)
        snapshot = snapshot_from_impact(impact)
        witness = snapshot["sop_witness"]
        if not snapshot["impact"]["paths"] and witness is None:
            raise RuntimeError("No graph-backed handover evidence is available; reset the synthetic scenario first.")
        if has_complete_sop_witness(snapshot) and snapshot["as_of"] == CONVEYOR_OUTAGE_AT and len(snapshot["impact"]["paths"]) == 2:
            # Keep the original handover content byte-for-byte for its default
            # workflow; the additive graph snapshot is the only default change.
            summary = "Open material-transfer outage affects two operations departments. Confirmed role paths and source timestamps are included; SOP-OPS-9 remains unresolved and no escalation winner is asserted."
        elif has_complete_sop_witness(snapshot):
            summary = f"The evidence snapshot contains {len(snapshot['impact']['paths'])} active impact paths as of {snapshot['as_of']}. The source-backed SOP-OPS-9 disagreement remains unresolved; no escalation winner is selected."
        elif witness:
            summary = "Graph-backed handover evidence is available, but the SOP-OPS-9 linkage is incomplete/unknown. No resolved, absent, or authoritative-conflict conclusion is asserted."
        else:
            summary = "Graph-backed impact evidence is available. No complete SOP witness is linked, and no conflict-resolution conclusion is asserted."
        path_sources = (snapshot["impact"]["path_evidence"][0].get("source_ids", [])[:2]
                        if snapshot["impact"]["path_evidence"] else [])
        proposed_actions = []
        if snapshot["impact"]["paths"]:
            proposed_actions.append({
                "text": "Carry forward the equipment status for next-shift verification; do not infer a restoration time.",
                "source_ids": path_sources,
            })
        if has_complete_sop_witness(snapshot):
            proposed_actions.append({
                "text": "Resolve the SOP-OPS-9 escalation disagreement before treating either current claim as authoritative.",
                "source_ids": sorted({claim["source_id"] for claim in witness["claim_evidence"]}),
            })
        token = secrets.token_urlsafe(24)
        draft = {
            "confirmation_token": token,
            "incident_id": "INC-204",
            "summary": summary,
            "proposed_actions": proposed_actions,
            "source_ids": source_ids_from_snapshot(snapshot),
            "snapshot": snapshot,
            "action_boundary": {
                "source_truth_resolved": False,
                "work_claimed_performed": False,
                "clinical_action_proposed": False,
                "messages_sent": False,
                "calls_made": False,
                "external_tickets_created": False,
                "external_writes": [],
                "automatically_confirmed": False,
            },
            "prepared_at": utc_now(),
        }
        with self.action_lock:
            now = time.time()
            self.pending = {key: value for key, value in self.pending.items() if now - value["created_epoch"] < 1800}
            self.pending[token] = {"draft": draft, "created_epoch": now}
        return draft

    def confirm_handover(self, token: str, confirmed: bool) -> dict:
        if not confirmed:
            raise ValueError("Explicit in-app confirmation is required; nothing was saved.")
        with self.action_lock:
            pending = self.pending.get(token)
            if pending is None:
                raise ValueError("Draft is unavailable or expired; prepare it again.")
            draft = pending["draft"]
            items = self.saved_actions()
            action = {
                "id": f"HANDOVER-{secrets.token_hex(4).upper()}",
                "incident_id": draft["incident_id"],
                "summary": draft["summary"],
                "proposed_actions": draft["proposed_actions"],
                "source_ids": draft["source_ids"],
                "snapshot": draft["snapshot"],
                "action_boundary": draft["action_boundary"],
                "confirmed_at": utc_now(),
                "storage": "local-only",
            }
            state = {"schema_version": 1, "items": [*items, action]}
            self.state_dir.mkdir(parents=True, exist_ok=True)
            temporary = self.state_dir / f".confirmed-actions-{secrets.token_hex(6)}.tmp"
            try:
                temporary.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")
                temporary.replace(self.action_file)
            finally:
                if temporary.exists():
                    temporary.unlink()
            del self.pending[token]
            return action


class WardAtlasHTTPServer(ThreadingHTTPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, address, handler, service: WardAtlasService):
        self.service = service
        super().__init__(address, handler)


class Handler(BaseHTTPRequestHandler):
    server: WardAtlasHTTPServer
    protocol_version = "HTTP/1.1"

    def log_message(self, format, *args):
        print("WARDATLAS_HTTP " + (format % args), flush=True)

    def _send(self, status: int, data: dict | str, content_type: str | None = None) -> None:
        if isinstance(data, str):
            body = data.encode("utf-8")
            mime = content_type or "text/html; charset=utf-8"
        else:
            body = (json.dumps(data, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8")
            mime = "application/json; charset=utf-8"
        self.send_response(status)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; connect-src 'self'; img-src 'self' data:; object-src 'none'; base-uri 'none'")
        self.end_headers()
        self.wfile.write(body)

    def _error(self, status: int, message: str) -> None:
        self._send(status, {"error": message})

    def _json_body(self) -> dict:
        raw_length = self.headers.get("Content-Length", "0")
        try:
            length = int(raw_length)
        except ValueError as exc:
            raise ValueError("Invalid request body.") from exc
        if length < 0 or length > 16_384:
            raise ValueError("Request body is too large.")
        raw = self.rfile.read(length) if length else b"{}"
        try:
            value = json.loads(raw)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise ValueError("Expected a JSON request body.") from exc
        if not isinstance(value, dict):
            raise ValueError("Expected a JSON object.")
        return value

    def _scope_allowed(self, query: str) -> bool:
        values = parse_qs(query, keep_blank_values=True).get("scope", ["operations"])
        return values == ["operations"]

    def do_GET(self):
        parsed = urlsplit(self.path)
        service = self.server.service
        try:
            if parsed.path == "/":
                self._send(200, APP_HTML)
            elif parsed.path in {"/api/briefing", "/api/graph", "/api/shift-board", "/api/sop-impact"} and not self._scope_allowed(parsed.query):
                self._error(403, "This view is not available.")
            elif parsed.path == "/api/health":
                self._send(200, service.health())
            elif parsed.path == "/api/briefing":
                self._send(200, service.briefing())
            elif parsed.path == "/api/graph":
                self._send(200, service.graph_view())
            elif parsed.path == "/api/shift-board":
                values = parse_qs(parsed.query, keep_blank_values=True)
                for key in ("dock_at", "effective_at", "known_at", "conveyor_at", "branch_at"):
                    if key in values and len(values[key]) != 1:
                        raise ValueError("Invalid timestamp; use an explicit ISO-8601 offset.")
                if "dock_at" in values and "effective_at" in values:
                    raise ValueError("Specify only one dock effective-time parameter.")
                dock_at = values.get("effective_at", values.get("dock_at", [DOCK_EFFECTIVE]))[0]
                data = service.shift_board(
                    dock_at,
                    values.get("conveyor_at", [CONVEYOR_OUTAGE_AT])[0],
                    values.get("branch_at", [BRANCH_AT])[0],
                    values.get("known_at", [None])[0],
                )
                self._send(200, data)
            elif parsed.path == "/api/sop-impact":
                values = parse_qs(parsed.query, keep_blank_values=True)
                if "as_of" in values and len(values["as_of"]) != 1:
                    raise ValueError("Invalid timestamp; use an explicit ISO-8601 offset.")
                data = service.sop_impact(values.get("as_of", [CONVEYOR_OUTAGE_AT])[0])
                self._send(200, data)
            elif parsed.path == "/api/actions":
                items = service.saved_actions()
                self._send(200, {"actions": items, "count": len(items), "storage": "local-only"})
            else:
                self._error(404, "Not found.")
        except ValueError:
            if parsed.path in {"/api/shift-board", "/api/sop-impact"}:
                self._error(400, "Invalid timestamp; use an explicit ISO-8601 offset.")
            else:
                self._error(503, "Local graph or action state is unavailable.")
        except Exception:
            self._error(503, "Local graph or action state is unavailable.")

    def do_POST(self):
        parsed = urlsplit(self.path)
        service = self.server.service
        if parsed.path == "/api/shift-board/branch/reset":
            try:
                self._json_body()
                self._send(200, service.reset_shift_board_branch())
            except ValueError as exc:
                self._error(400, str(exc))
            except RuntimeError as exc:
                self._error(409, str(exc))
            except Exception:
                self._error(503, "The hypothetical shift-board branch could not be reset.")
            return
        if parsed.path == "/api/scenario/reset":
            try:
                # Consume the submitted JSON body before reusing this HTTP/1.1
                # connection; otherwise its bytes could prefix the next verb.
                self._json_body()
                self._send(200, service.reset_scenario())
            except ValueError as exc:
                self._error(400, str(exc))
            except Exception:
                self._error(503, "The synthetic scenario could not be reset.")
            return
        if parsed.path == "/api/handover/draft":
            try:
                body = self._json_body()
                self._send(201, service.preview_handover(body.get("incident_id", "INC-204"), body.get("as_of", CONVEYOR_OUTAGE_AT)))
            except ValueError as exc:
                self._error(400, str(exc))
            except RuntimeError as exc:
                self._error(409, str(exc))
            except Exception:
                self._error(503, "A local draft could not be prepared.")
            return
        if parsed.path == "/api/handover/confirm":
            try:
                body = self._json_body()
                token = body.get("token")
                if not isinstance(token, str) or len(token) > 100:
                    raise ValueError("Draft token is invalid.")
                action = service.confirm_handover(token, body.get("confirm") is True)
                self._send(201, {"action": action, "persisted": True, "storage": "local-only", "external_effects": []})
            except ValueError as exc:
                self._error(400, str(exc))
            except Exception:
                self._error(503, "The local handover could not be saved.")
            return
        self._error(404, "Not found.")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--app-port", type=int, required=True)
    parser.add_argument("--db-port", type=int, required=True)
    parser.add_argument("--graph-name", required=True)
    parser.add_argument("--state-dir", type=Path, default=ROOT / ".local/wardatlas-state")
    args = parser.parse_args()
    if not (1024 <= args.app_port <= 65535):
        parser.error("app port must be an explicitly selected high port")
    service = WardAtlasService(args.db_port, args.graph_name, args.state_dir)
    try:
        server = WardAtlasHTTPServer(("127.0.0.1", args.app_port), Handler, service)
    except OSError as exc:
        print(f"WardAtlas did not bind 127.0.0.1:{args.app_port}: {exc}", flush=True)
        return 2
    print(f"WARDATLAS_READY http://127.0.0.1:{args.app_port}", flush=True)
    try:
        server.serve_forever(poll_interval=0.2)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

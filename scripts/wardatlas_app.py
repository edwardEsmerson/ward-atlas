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

from wardatlas_questions import HTML_PATH as QUESTIONS_HTML_PATH, post_questions

from verify_falkordb_live import (
    RespConnection,
    module_list_maps,
    query_result,
    result_rows,
    text,
)
from wardatlas_fixtures import IMPACT_QUERY, SOP_CONFLICT_QUERY, seed_queries
from wardatlas_resilience import experiment as resilience_experiment, validate_request as validate_resilience_request
from wardatlas_briefcase import mount_briefcase
from wardatlas_decisions import RECORDS_QUERY, decision_seed_queries, project_docket
from wardatlas_knowledge_timeline import (
    build_queries as build_knowledge_timeline_queries,
    considered_query as knowledge_timeline_considered_query,
    project_timeline as project_knowledge_timeline,
)
from wardatlas_source_branch import (
    BRANCH_PATH_QUERY as SOURCE_BRANCH_PATH_QUERY,
    branch_claim_projection,
    branch_v3_claim_query,
    source_branch_response,
    source_branch_seed_queries,
)
from wardatlas_knowledge_diff import knowledge_diff, parse_parameters as parse_knowledge_diff_parameters
from wardatlas_sop_impact import (
    CLAIMS_QUERY as SOP_IMPACT_CLAIMS_QUERY,
    CONFLICT_WITNESS_QUERY,
    STALE_QUERY,
    assemble_response as assemble_sop_impact_response,
)
from wardatlas_handover import compare_graph_witness, has_complete_sop_witness, snapshot_from_impact, source_ids_from_snapshot
from wardatlas_evidence_watch import (
    DEFAULT_EFFECTIVE_AT as WATCH_DEFAULT_EFFECTIVE_AT,
    DEFAULT_KNOWN_AT as WATCH_DEFAULT_KNOWN_AT,
    STATUS_MESSAGES as WATCH_STATUS_MESSAGES,
    WATCH_ID,
    normalize_watch_axes,
    public_refresh as public_watch_refresh,
    witness_from_dock,
)
from wardatlas_clock_atlas import mount_clock_atlas
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
GRAPH_NAME_RE = re.compile(r"^wardatlas_(?:test|ui|attach)_[A-Za-z0-9_-]{12,80}$")
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
    .drift-controls { display:flex; flex-wrap:wrap; align-items:center; gap:8px; margin-top:12px; }
    .drift-controls .button { font-size:10px; padding:8px 10px; }
    .drift-comparison { margin-top:12px; border:1px solid #5b533d; border-radius:10px; padding:12px; background:#211f1b; min-width:0; }
    .drift-comparison[hidden] { display:none; }
    .drift-grid { display:grid; grid-template-columns:minmax(0,1fr) minmax(0,1fr); gap:10px; }
    .drift-pane { min-width:0; border:1px solid #4a493a; border-radius:8px; padding:10px; background:#171f21; }
    .drift-pane h4 { margin:0 0 8px; color:#f0d391; font-size:10px; letter-spacing:.08em; text-transform:uppercase; }
    .drift-pane h5 { margin:11px 0 5px; color:#c3d0c9; font-size:9px; letter-spacing:.06em; text-transform:uppercase; }
    .drift-status { color:#f1d28f; font-size:10px; line-height:1.5; overflow-wrap:anywhere; }
    .drift-path { overflow-wrap:anywhere; border-left:2px solid var(--mint); padding:7px 8px; margin:7px 0; background:#102d2680; color:#d8e9e2; font-size:10px; line-height:1.55; }
    .drift-path.removed { border-left-color:var(--red); background:#3a252280; }
    .drift-provenance { display:block; margin-top:4px; color:#9fb8b0; font-size:9px; line-height:1.5; overflow-wrap:anywhere; }
    .drift-unknown { color:var(--amber); font-size:10px; line-height:1.5; }
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
    .evidence-watch { margin:0 0 18px; border-color:#46616a; background:linear-gradient(145deg,#1c3039,#14242e 68%); }
    .watch-intro { max-width:900px; color:#bfcecf; font-size:12px; line-height:1.6; margin:4px 0 13px; }
    .watch-controls { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:10px; margin:12px 0; }
    .watch-controls label { display:grid; gap:6px; color:#afc2bf; font-size:10px; }
    .watch-controls input { width:100%; min-width:0; color:var(--ink); background:#1a3038; border:1px solid #426067; border-radius:7px; padding:9px 10px; font:inherit; font-size:11px; }
    .watch-actions { display:flex; flex-wrap:wrap; gap:8px; margin:10px 0; }
    .watch-preview,.watch-result { border:1px solid #35534e; border-radius:10px; padding:12px; margin-top:11px; background:#10231f; overflow-wrap:anywhere; }
    .watch-preview[hidden] { display:none; }
    .watch-result h3,.watch-preview h3 { margin:0 0 8px; font-size:12px; }
    .watch-status { color:#a9e9cd; font-size:11px; font-weight:800; }
    .watch-status.unknown { color:#f1c278; }
    .watch-witness { color:#d1dfdb; font-size:10px; line-height:1.6; margin-top:7px; }
    .watch-boundary { color:#9fb3b7; font-size:10px; line-height:1.55; }
    .watch-result[hidden] { display:none; }
    @media (max-width:560px) { .watch-controls { grid-template-columns:1fr; } }
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
    .decision-docket { margin:0 0 18px; border-color:#3f5f60; background:linear-gradient(145deg,#193333,#172731 68%); }
    .decision-docket-intro { color:#bfcbc7; font-size:12px; line-height:1.55; margin:4px 0 14px; max-width:920px; }
    .decision-record { border:1px solid #426363; border-radius:10px; padding:13px; margin:10px 0; background:#10272988; }
    .decision-record h3 { margin:0 0 6px; color:#d7eee6; font-size:14px; }
    .decision-meta { color:#a9c2bd; font-size:10px; line-height:1.7; overflow-wrap:anywhere; }
    .decision-edges { font:10px/1.7 ui-monospace,SFMono-Regular,Menlo,monospace; color:#cde4dc; overflow-wrap:anywhere; }
    .decision-claim { border-top:1px solid #355052; padding:8px 0; color:#f0d9a9; font-size:11px; line-height:1.6; }
    .decision-claim small { display:block; color:#afc0bb; overflow-wrap:anywhere; }
    .decision-unknown { color:#f1c278; font-size:12px; padding:7px 0; }
    .knowledge-timeline { margin:0 0 18px; border-color:#49616d; background:linear-gradient(145deg,#1b2d3b,#152630 72%); }
    .knowledge-controls { display:grid; grid-template-columns:minmax(220px,1fr) auto; gap:9px 12px; align-items:end; margin:14px 0; }
    .knowledge-controls label { display:block; color:#b6c7ca; font-size:10px; margin-bottom:6px; }
    .knowledge-controls input { width:100%; min-width:0; color:var(--ink); background:#10232d; border:1px solid #49616d; border-radius:7px; padding:9px 10px; font:inherit; font-size:11px; }
    .knowledge-presets { display:flex; flex-wrap:wrap; gap:7px; grid-column:1/-1; }
    .knowledge-presets .button { padding:7px 9px; font-size:10px; }
    .knowledge-state { color:#b7d6d0; font-size:10px; line-height:1.6; margin:8px 0; }
    .knowledge-claims { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:10px; }
    .knowledge-claim { min-width:0; border:1px solid #354f5b; border-radius:9px; padding:10px; background:#10212b; overflow-wrap:anywhere; }
    .knowledge-claim strong { display:block; color:#f0d9a9; font-size:11px; line-height:1.5; }
    .knowledge-claim small,.knowledge-record small { display:block; color:#a9bec0; font-size:9px; line-height:1.6; margin-top:5px; }
    .knowledge-path { font:9px/1.6 ui-monospace,SFMono-Regular,Menlo,monospace; color:#bdd5d2; overflow-wrap:anywhere; margin-top:6px; }
    .knowledge-conflict { border-left:2px dashed var(--amber); padding:7px 9px; margin-top:10px; color:#f2cf91; font-size:10px; line-height:1.6; overflow-wrap:anywhere; }
    .knowledge-record { border:1px solid #426363; border-radius:9px; padding:10px; margin-top:10px; color:#cfe0db; font-size:10px; line-height:1.6; overflow-wrap:anywhere; }
    .knowledge-unknown { color:var(--amber); font-size:10px; line-height:1.6; margin-top:9px; }
    @media (max-width:680px) { .knowledge-controls { grid-template-columns:1fr; } .knowledge-presets { grid-column:auto; } .knowledge-claims { grid-template-columns:1fr; } }
    .source-branch { margin:0 0 18px; border-color:#6b5268; background:linear-gradient(145deg,#30253a,#172630 72%); }
    .source-branch-controls { display:grid; grid-template-columns:minmax(230px,420px) minmax(0,1fr); gap:12px; align-items:end; margin:13px 0; }
    .source-branch-controls label { display:grid; gap:6px; color:#d1bfd0; font-size:11px; }
    .source-branch-controls select { width:100%; min-width:0; color:var(--ink); background:#211d2a; border:1px solid #705978; border-radius:7px; padding:10px; font:inherit; font-size:12px; }
    .source-branch-grid { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:12px; }
    .source-branch-pane { min-width:0; border:1px solid #4a4354; border-radius:11px; padding:13px; background:#111d2788; overflow-wrap:anywhere; }
    .source-branch-pane h3 { margin:0 0 9px; color:#e7d6e9; font-size:12px; letter-spacing:.06em; text-transform:uppercase; }
    .source-branch-scenario { color:#f3d39a; border-left:2px solid var(--amber); padding:7px 9px; margin:8px 0 11px; font-size:10px; line-height:1.65; }
    .source-branch-claim { border-top:1px solid #3e3d4c; padding:9px 0; color:#f1ddae; font-size:11px; line-height:1.6; overflow-wrap:anywhere; }
    .source-branch-claim small { display:block; color:#b7c2c8; font-size:9px; line-height:1.6; margin-top:4px; }
    .source-branch-state { color:#c7d9d7; font-size:10px; line-height:1.6; margin:8px 0; }
    .source-branch-record { border:1px solid #426363; border-radius:8px; padding:9px; margin-top:8px; color:#d0e2de; font-size:10px; line-height:1.6; }
    .source-branch-conflict { border-left:2px dashed var(--amber); padding:7px 9px; margin-top:8px; color:#f2cf91; font-size:10px; line-height:1.6; }
    .source-branch-unknown { color:var(--amber); font-size:10px; line-height:1.6; margin-top:8px; }
    @media (max-width:760px) { .source-branch-grid { grid-template-columns:1fr; } .source-branch-controls { grid-template-columns:1fr; } }
    @media (max-width:760px) { .sop-impact-grid { grid-template-columns:1fr; } }
    #legacy-grid[hidden] { display:none; }
    .footer { color:#758c92; font-size:10px; padding:21px 2px 0; display:flex; justify-content:space-between; gap:12px; }
    @media (max-width:900px) { .grid { grid-template-columns:1fr; } .hero { grid-template-columns:1fr; align-items:start; } .incident-card { max-width:420px; } }
    @media (max-width:760px) { .shift-cases { grid-template-columns:1fr; } .drift-grid { grid-template-columns:1fr; } }
    @media (max-width:560px) { .shell { padding:13px 13px 35px; } .topbar { align-items:flex-start; } .top-actions { flex-direction:column-reverse; align-items:flex-end; gap:8px; } .pill { font-size:10px; padding:7px 9px; } .button { padding:9px 10px; font-size:12px; } .hero { padding:25px 0 17px; gap:17px; } h1 { font-size:35px; } .hero p { font-size:13px; } .card { padding:15px; border-radius:12px; } .card-head { flex-direction:column; } .path-row { grid-template-columns:1fr; } .role-label { text-align:left; max-width:none; } .footer { flex-direction:column; } .shift-case { padding:12px; } .shift-controls { align-items:stretch; } .shift-controls select { max-width:100%; } }
  </style>
</head>
<body>
  <div class="shell">
    <header class="topbar">
      <div class="brand"><span class="brand-mark" aria-hidden="true">W</span><span>WARDATLAS<small>HOSPITAL OPERATIONS BRAIN</small></span></div>
      <div class="top-actions"><a class="button" href="/knowledge-diff" aria-label="Explain answer differences">Knowledge diff</a><a class="button" href="/ask">Ask WardAtlas</a><span id="db-status" class="pill"><span class="dot"></span>Connecting to local graph…</span><button id="reset" class="button" type="button">Reset synthetic scenario</button></div>
    </header>
    <main>
      <p><a href="/resilience" style="color:#86e1be">Evidence resilience · inspect one missing supporting edge →</a></p>
      <p><a href="/briefcase" style="color:#86e1be">Historical evidence briefcase · inspect and download offline →</a></p>
      <nav aria-label="Clock workbench" style="margin-top:20px"><a href="/clocks" class="button">Ownership clock atlas →</a></nav>
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
      <section id="evidence-watch" class="card evidence-watch" aria-labelledby="evidence-watch-title">
        <div class="card-head"><div><div class="eyebrow">Graph-backed continuity watch · local only</div><h2 id="evidence-watch-title">Next-shift dock evidence watch</h2></div><span id="watch-badge" class="tag blue">No saved watch</span></div>
        <p class="watch-intro">Preview the receiving-dock ownership witness, then explicitly confirm one local watch. Refresh compares its saved baseline with new FalkorDB evidence using independent effective-at and known-at times. Unknown means insufficient evidence—not no owner, safe, or resolved.</p>
        <div class="watch-controls">
          <label for="watch-effective-at">Effective at · ISO-8601 with explicit offset<input id="watch-effective-at" type="text" value="2026-10-14T20:29:00Z" autocomplete="off" spellcheck="false"></label>
          <label for="watch-known-at">Sources known by · ISO-8601 with explicit offset<input id="watch-known-at" type="text" value="2026-10-14T20:17:59Z" autocomplete="off" spellcheck="false"></label>
        </div>
        <div class="watch-actions"><button id="watch-preview-button" class="button primary" type="button">Preview R1 witness</button><button id="watch-refresh-button" class="button" type="button">Refresh saved watch</button></div>
        <div id="watch-preview" class="watch-preview" hidden></div>
        <div id="watch-live" class="watch-result" aria-live="polite">No confirmed watch. Previewing does not save a watch.</div>
        <div id="watch-notice" class="notice" aria-live="polite"></div>
        <p class="watch-boundary">Confirmation writes one record to a separate local watch-state file; it never edits confirmed handovers, writes to FalkorDB, sends messages, makes calls, creates tickets, or performs external writes.</p>
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
      <section id="decision-docket" class="card decision-docket" aria-labelledby="decision-docket-title">
        <div class="card-head"><div><div class="eyebrow">Register-rooted provenance · read only</div><h2 id="decision-docket-title">Decision provenance docket</h2></div><span id="decision-docket-badge" class="tag blue">Loading register evidence…</span></div>
        <p class="decision-docket-intro">This docket starts at the synthetic operations register and follows explicit TRACKS, CONSIDERS, CLAIMS_ROLE and EVIDENCED_BY relationships. Recorded state is not a selected procedure: neither disputed version is made authoritative, and claim roles are not current owners or contacts.</p>
        <div id="decision-docket-empty" class="decision-unknown" hidden>Unknown / no evidence. No absence, resolution, procedure, or owner is inferred from missing source links.</div>
        <div id="decision-docket-content" aria-live="polite"></div>
        <div id="decision-docket-notice" class="notice" aria-live="polite"></div>
      </section>
      <section id="decision-knowledge-timeline" class="card knowledge-timeline" aria-labelledby="knowledge-timeline-title">
        <div class="card-head"><div><div class="eyebrow">Source observation · read only</div><h2 id="knowledge-timeline-title">Replay what was knowable</h2></div><span id="knowledge-timeline-badge" class="tag blue">Loading source-time view…</span></div>
        <p class="decision-docket-intro">Choose a UTC observation cutoff. The replay filters each graph fact through its own source observation time; a missing or later source does not prove a fact is absent. This is knowledge time, not an effective date, current owner, or procedure.</p>
        <div class="knowledge-controls">
          <div><label for="knowledge-time">Known-at cutoff · ISO-8601 with explicit offset</label><input id="knowledge-time" type="text" value="2026-10-14T16:05:00Z" autocomplete="off" spellcheck="false" aria-describedby="knowledge-time-help"></div>
          <button id="knowledge-replay" class="button primary" type="button">Replay cutoff</button>
          <div id="knowledge-time-help" class="subtle">Invalid or timezone-naive values are rejected; no local-clock fallback is used.</div>
          <div class="knowledge-presets" aria-label="Pinned observation cutoffs">
            <button class="button" type="button" data-known-at="2026-10-14T15:59:59Z">15:59:59Z</button>
            <button class="button" type="button" data-known-at="2026-10-14T16:00:00Z">16:00Z</button>
            <button class="button" type="button" data-known-at="2026-10-14T16:04:00Z">16:04Z</button>
            <button class="button" type="button" data-known-at="2026-10-14T16:05:00Z">16:05Z</button>
          </div>
        </div>
        <div id="knowledge-timeline-context" class="knowledge-state" aria-live="polite"></div>
        <div id="knowledge-timeline-claims" class="knowledge-claims"></div>
        <div id="knowledge-timeline-conflict" class="knowledge-conflict" hidden></div>
        <div id="knowledge-timeline-record" class="knowledge-record" hidden></div>
        <div id="knowledge-timeline-unknown" class="knowledge-unknown" hidden></div>
        <div id="knowledge-timeline-notice" class="notice" aria-live="polite"></div>
      </section>
      <section id="decision-source-branch" class="card source-branch" aria-labelledby="source-branch-title" hidden>
        <div class="card-head"><div><div class="eyebrow">Graph-stored counterfactual · read only</div><h2 id="source-branch-title">Branch the knowledge clock</h2></div><span id="source-branch-badge" class="tag blue">Loading mainline and scenario…</span></div>
        <p class="decision-docket-intro">Compare mainline source evidence with an optional graph-stored hypothetical. Missing links remain unknown; this view never substitutes mainline facts for missing branch evidence.</p>
        <div class="source-branch-controls">
          <label for="source-branch-cutoff">Shared knowledge cutoff · keyboard-selectable ISO-8601 UTC<select id="source-branch-cutoff" aria-label="Shared mainline and hypothetical knowledge cutoff">
            <option value="2026-10-14T16:05:00Z" selected>16:05Z · decision record known</option>
            <option value="2026-10-14T16:06:00Z">16:06Z · hypothetical V3 observation</option>
          </select></label>
          <div id="source-branch-guidance" class="subtle">Both panes use the same cutoff. Use the select with a keyboard to replay 16:05Z or 16:06Z; missing branch links stay unknown and never fall back to mainline.</div>
        </div>
        <div class="source-branch-grid" aria-label="Side-by-side mainline and hypothetical replay">
          <article id="source-branch-mainline" class="source-branch-pane" aria-labelledby="source-branch-mainline-title" aria-live="polite">
            <h3 id="source-branch-mainline-title">Mainline · source facts</h3><div class="source-branch-state"></div><div class="source-branch-claims"></div><div class="source-branch-conflict" hidden></div><div class="source-branch-record" hidden></div><div class="source-branch-unknown" hidden></div>
          </article>
          <article id="source-branch-hypothetical" class="source-branch-pane" aria-labelledby="source-branch-hypothetical-title" aria-live="polite">
            <h3 id="source-branch-hypothetical-title">Hypothetical branch · explicit assumption</h3><div class="source-branch-scenario" hidden></div><div class="source-branch-state"></div><div class="source-branch-claims"></div><div class="source-branch-conflict" hidden></div><div class="source-branch-record" hidden></div><div class="source-branch-unknown" hidden></div>
          </article>
        </div>
        <div id="source-branch-notice" class="notice" aria-live="polite"></div>
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
    let knowledgeRequestSequence = 0;
    let sourceBranchRequestSequence = 0;
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
    function appendWatchWitness(parent, title, witness) {
      const section=element('div','watch-witness'); section.append(element('strong','',`${title} · Effective ${witness.effective_at} · known by ${witness.known_at}`));
      section.append(element('div','',witness.role_path.length?`Path · ${witness.role_path.join('  →  ')}`:'Ownership path · unknown / incomplete'));
      if(witness.source_versions.length) for(const source of witness.source_versions) section.append(element('div','',`Source · ${source.source_id} · ${source.title} · observed ${source.observed_at}${source.status?` · ${source.status}`:''}`));
      else section.append(element('div','','No source versions are visible for this cutoff.'));
      section.append(element('div','',`Explicit review candidates · ${witness.review_candidates.length?witness.review_candidates.join(', '):'none visible'}`));
      parent.append(section);
    }
    function renderEvidenceWatch(data) {
      const root=$('watch-live'); root.replaceChildren();
      if(!data.watch) {
        $('watch-badge').textContent='No saved watch';
        root.append(element('div','subtle','No confirmed watch. Previewing does not save a watch.'));
        return;
      }
      const current=data.current_result; const status=current?.status||'unknown';
      $('watch-badge').textContent=status;
      $('watch-badge').className=`tag${status==='unknown'?' amber':''}`;
      root.append(element('h3','',`Saved local watch · ${data.watch.id}`));
      appendWatchWitness(root,'Saved baseline',data.watch.baseline);
      if(current) {
        const statusBox=element('div',`watch-status${status==='unknown'?' unknown':''}`,`${status} · ${current.status_message}`); root.append(statusBox);
        appendWatchWitness(root,'Fresh graph result',current);
      } else root.append(element('div','watch-status unknown','Current graph result · unknown'));
      root.append(element('p','watch-boundary','This is a comparison of source-backed evidence only. It does not change a handover, task, ticket, owner assignment, or other work item.'));
    }
    async function loadEvidenceWatch() {
      const data=await api('/api/evidence-watch?scope=operations');
      renderEvidenceWatch(data); $('watch-notice').textContent=''; return data;
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
    function renderDecisionDocket(data) {
      const unknown=data.state==='unknown';
      $('decision-docket-badge').textContent=unknown?'Unknown · no evidence':data.state==='open-unresolved'?'Source-backed · unresolved':'Incomplete · unknown';
      $('decision-docket-empty').hidden=!unknown;
      const content=$('decision-docket-content'); content.replaceChildren();
      if(unknown) return;
      for(const docket of data.docket_rows) {
        const record=docket.decision_record; const card=element('article','decision-record');
        card.dataset.decisionId=record.id;
        card.append(element('h3','',`${record.id} · ${record.title}`));
        card.append(element('div','decision-meta',`Register ${data.root_id} · TRACKS · ${docket.state} · recorded state ${record.recorded_state}`));
        card.append(element('p','decision-meta',record.recorded_statement));
        const source=record.record_source;
        card.append(element('div','decision-meta',`Decision-register evidence · ${source.id} · ${source.title} · observed ${source.observed_at} · ${record.id} -[EVIDENCED_BY]-> ${source.id}`));
        const considered=element('div','decision-edges');
        considered.textContent=docket.considered_edges.length
          ? docket.considered_edges.map(edge=>`${edge.from} -[${edge.relationship}]-> ${edge.to}`).join(' · ')
          : 'No considered-version edge is established; linkage is unknown.';
        card.append(considered);
        if(docket.conflict_edge) card.append(element('div','decision-edges',`${docket.conflict_edge.from} -[${docket.conflict_edge.relationship} · ${docket.conflict_edge.status}]-> ${docket.conflict_edge.to}`));
        else card.append(element('div','decision-unknown','Conflict linkage unknown; no absence or resolution is inferred.'));
        for(const claim of docket.claims) {
          const item=element('div','decision-claim',`${claim.version_id} · ${claim.claim_label} → ${claim.role_id}`);
          item.append(element('small','',`${claim.source.id} · ${claim.source.title} · observed ${claim.source.observed_at} · explicit ${claim.source.evidence_link.from} -[EVIDENCED_BY]-> ${claim.source.evidence_link.to}`));
          card.append(item);
        }
        const stateText=docket.complete_two_claim_witness_present
          ? 'Both source-backed claims and the unresolved conflict edge are present. Neither claim is selected as authoritative.'
          : 'Evidence witness is incomplete / unknown. A surviving claim is not promoted; no conflict resolution or current owner is inferred.';
        card.append(element('p','decision-meta',stateText));
        content.append(card);
      }
    }
    async function loadDecisionDocket(sequence=knowledgeRequestSequence) {
      try {
        const data=await api('/api/decision-docket?scope=operations');
        if(sequence!==knowledgeRequestSequence) return null;
        renderDecisionDocket(data);
        $('decision-docket-notice').textContent='';
        return data;
      } catch(e) {
        if(sequence!==knowledgeRequestSequence) return null;
        $('decision-docket-badge').textContent='Docket unavailable';
        $('decision-docket-empty').hidden=true;
        $('decision-docket-content').replaceChildren();
        $('decision-docket-notice').textContent=`Decision provenance unavailable: ${e.message}. The existing incident briefing and shift board remain available.`;
        return null;
      }
    }
    function renderKnowledgeTimeline(data) {
      const badge=$('knowledge-timeline-badge');
      badge.textContent=data.generic_unknown?'Unknown · no evidence':data.decision_record?'Register source known · '+data.claim_context_state:data.claim_context_state;
      const context=$('knowledge-timeline-context'); context.textContent=`Known-at ${data.known_at||'not selected'} · claim context ${data.claim_context_state}. Retrieved claim facts and graph-derived relationships are shown separately; missing evidence stays unknown.`;
      const claims=$('knowledge-timeline-claims'); claims.replaceChildren();
      for(const claim of data.claims) {
        const card=element('article','knowledge-claim');
        card.append(element('strong','',`${claim.version_id} · ${claim.claim_label} → ${claim.role_id}`));
        card.append(element('small','',`Source fact · ${claim.source.id} · ${claim.source.title} · observed ${claim.source.observed_at}`));
        card.append(element('div','knowledge-path',`${claim.applicability_edge.from} -[${claim.applicability_edge.relationship}]-> ${claim.applicability_edge.to} · ${claim.claim_edge.from} -[${claim.claim_edge.relationship}]-> ${claim.claim_edge.to} · ${claim.source.evidence_link.from} -[${claim.source.evidence_link.relationship}]-> ${claim.source.evidence_link.to}`));
        claims.append(card);
      }
      const conflict=$('knowledge-timeline-conflict'); conflict.hidden=!data.conflict;
      conflict.textContent=data.conflict?`Graph-derived relationship · ${data.conflict.edge.from} -[${data.conflict.edge.relationship} · ${data.conflict.edge.status}]-> ${data.conflict.edge.to}. Not a source quotation or authoritative procedure.`:'';
      const record=$('knowledge-timeline-record'); record.hidden=!data.decision_record;
      if(data.decision_record) {
        const item=data.decision_record; record.replaceChildren();
        record.append(element('strong','',`Separately evidenced recorded state · ${item.id} · ${item.title} · ${item.recorded_state}`));
        record.append(element('small','',`${item.register_id} -[TRACKS]-> ${item.id} -[EVIDENCED_BY]-> ${item.source.id} · ${item.source.title} · observed ${item.source.observed_at}`));
        record.append(element('small','',item.recorded_statement));
        const edges=item.provenance_path.map(edge=>`${edge.from} -[${edge.relationship}]-> ${edge.to}`).join(' · ');
        record.append(element('div','knowledge-path',edges));
        for(const edge of data.decision_considers_edges) record.append(element('div','knowledge-path',`${edge.from} -[${edge.relationship}]-> ${edge.to} · independently source-known claim`));
      }
      const unknown=$('knowledge-timeline-unknown');
      unknown.hidden=Boolean(data.claims.length||data.decision_record);
      unknown.textContent=unknown.hidden?'':'No source-backed fact is available at this cutoff. This is not a conclusion that a fact does not exist.';
    }
    async function loadKnowledgeTimeline() {
      const sequence=++knowledgeRequestSequence;
      try {
        const knownAt=$('knowledge-time').value;
        const params=new URLSearchParams({scope:'operations'});
        if(knownAt) params.set('known_at',knownAt);
        const data=await api(`/api/decision-timeline?${params.toString()}`);
        if(sequence!==knowledgeRequestSequence) return data;
        renderKnowledgeTimeline(data); $('knowledge-timeline-notice').textContent='';
        if(data.decision_record && data.claim_context_state==='evidence-backed') await loadDecisionDocket(sequence);
        else {
          $('decision-docket-badge').textContent='Unknown · no evidence';
          $('decision-docket-empty').hidden=false;
          $('decision-docket-content').replaceChildren();
          $('decision-docket-notice').textContent='';
        }
        return data;
      } catch(e) {
        if(sequence!==knowledgeRequestSequence) return null;
        $('knowledge-timeline-badge').textContent='Replay unavailable';
        $('knowledge-timeline-context').textContent='No projection was retrieved for this cutoff; the previous result has been cleared.';
        $('knowledge-timeline-claims').replaceChildren();
        $('knowledge-timeline-conflict').hidden=true; $('knowledge-timeline-conflict').textContent='';
        $('knowledge-timeline-record').hidden=true; $('knowledge-timeline-record').replaceChildren();
        $('knowledge-timeline-unknown').hidden=false;
        $('knowledge-timeline-unknown').textContent='No source-time view is available for this request.';
        $('decision-docket-badge').textContent='Unknown · no evidence';
        $('decision-docket-empty').hidden=false; $('decision-docket-content').replaceChildren();
        $('decision-docket-notice').textContent='';
        $('knowledge-timeline-notice').textContent=`Source-time replay unavailable: ${e.message}. No current-time substitution was attempted.`;
        return null;
      }
    }
    function renderBranchPane(root, projection, scenario=null, hypothetical=false) {
      const state=root.querySelector('.source-branch-state');
      state.textContent=`Known-at ${projection.known_at||'not selected'} · claim context ${projection.claim_context_state}. Missing evidence is unknown, not an absence, resolution, or selected procedure.`;
      const scenarioBox=root.querySelector('.source-branch-scenario');
      if(scenarioBox) {
        scenarioBox.hidden=!scenario;
        scenarioBox.textContent=scenario?`${scenario.label}. Assumed observation ${scenario.assumed_observed_at}; the source fact remains observed ${scenario.actual_source_observed_at}. This branch is not source truth.`:'';
      }
      const claims=root.querySelector('.source-branch-claims'); claims.replaceChildren();
      for(const claim of projection.claims) {
        const row=element('div','source-branch-claim',`${claim.version_id} · ${claim.claim_label} → ${claim.role_id}`);
        const sourceTime=hypothetical?`Hypothetical observation ${claim.source.observed_at} · actual source observation ${scenario?.actual_source_observed_at||'not independently available'}`:`Source observed ${claim.source.observed_at}`;
        row.append(element('small','',`${claim.source.id} · ${claim.source.title} · ${sourceTime}`));
        row.append(element('small','',`${claim.applicability_edge.from} -[${claim.applicability_edge.relationship}]-> ${claim.applicability_edge.to} · ${claim.claim_edge.from} -[${claim.claim_edge.relationship}]-> ${claim.claim_edge.to} · ${claim.source.evidence_link.from} -[${claim.source.evidence_link.relationship}]-> ${claim.source.evidence_link.to}`));
        claims.append(row);
      }
      const conflict=root.querySelector('.source-branch-conflict'); conflict.hidden=!projection.conflict;
      conflict.textContent=projection.conflict?`Graph-derived unresolved relationship · ${projection.conflict.edge.from} -[${projection.conflict.edge.relationship} · ${projection.conflict.edge.status}]-> ${projection.conflict.edge.to}. Not an authoritative rule.`:'';
      const record=root.querySelector('.source-branch-record'); record.hidden=!projection.decision_record;
      if(projection.decision_record) {
        const item=projection.decision_record; record.replaceChildren();
        record.append(element('strong','',`Separately evidenced record · ${item.id} · ${item.recorded_state}`));
        record.append(element('div','',`${item.title} · ${item.recorded_statement}`));
        record.append(element('small','',`${item.register_id} -[TRACKS]-> ${item.id} -[EVIDENCED_BY]-> ${item.source.id} · observed ${item.source.observed_at}`));
        for(const edge of projection.decision_considers_edges) record.append(element('small','',`${edge.from} -[${edge.relationship}]-> ${edge.to} · independently source-known claim`));
      }
      const unknown=root.querySelector('.source-branch-unknown'); unknown.hidden=Boolean(projection.claims.length||projection.decision_record);
      unknown.textContent=unknown.hidden?'':'Unknown / incomplete. No branch fact is projected from missing links; mainline is shown separately and is not substituted here.';
    }
    function renderSourceBranch(data) {
      renderBranchPane($('source-branch-mainline'),data.mainline,null,false);
      const branch=data.hypothetical_branch||{};
      const visible=Boolean(branch.scenario||data.mainline?.claims?.length||data.mainline?.decision_record);
      $('decision-source-branch').hidden=!visible;
      renderBranchPane($('source-branch-hypothetical'),branch.projection||{
        known_at:null,claim_context_state:'unknown',claims:[],conflict:null,decision_record:null,decision_considers_edges:[]
      },branch.scenario||null,true);
      $('source-branch-badge').textContent=branch.scenario?'Graph-linked hypothetical · '+(branch.projection?.claim_context_state||'unknown'):'Branch evidence unknown · no fallback';
    }
    async function loadSourceBranch() {
      const sequence=++sourceBranchRequestSequence;
      try {
        const params=new URLSearchParams({scope:'operations',known_at:$('source-branch-cutoff').value});
        const data=await api(`/api/decision-source-branch?${params.toString()}`);
        if(sequence!==sourceBranchRequestSequence) return data;
        renderSourceBranch(data); $('source-branch-notice').textContent='Read-only FalkorDB replay complete. Mainline source facts were not changed.';
        return data;
      } catch(error) {
        if(sequence!==sourceBranchRequestSequence) return null;
        const unknown={known_at:$('source-branch-cutoff').value,claim_context_state:'unknown',claims:[],conflict:null,decision_record:null,decision_considers_edges:[]};
        // Keep the isolated failure notice visible even if the graph is cold;
        // do not hide the only explanation behind the empty-state card.
        $('decision-source-branch').hidden=false;
        renderSourceBranch({mainline:unknown,hypothetical_branch:{projection:unknown}});
        $('source-branch-notice').textContent=`No source-time branch projection is available: ${error.message}. No mainline fallback was attempted.`;
        return null;
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
       const [health, data, graph, actions, shiftBoard, timeline, watch, sourceBranch]=await Promise.all([api('/api/health'),api('/api/briefing?scope=operations'),api('/api/graph?scope=operations'),api('/api/actions'),loadShiftBoard(),loadKnowledgeTimeline(),loadEvidenceWatch(),loadSourceBranch()]);
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
    function renderDriftPath(container, row, removed=false) {
      const path=element('div',removed?'drift-path removed':'drift-path',row.path.join('  →  '));
      const provenance=row.provenance;
      if(provenance) {
        const evidence=(provenance.source_ids||[]).map((id,index)=>`${id} · ${provenance.source_titles?.[index]||'title unknown'} · observed ${provenance.observed_at?.[index]||'time unknown'}`).join(' → ');
        path.append(element('small','drift-provenance',evidence||'Path provenance unavailable / unknown.'));
      } else path.append(element('small','drift-provenance','Path provenance unavailable / unknown.'));
      container.append(path);
    }
    function renderDriftComparison(root,data) {
      root.replaceChildren(); root.dataset.comparison=JSON.stringify(data); root.dataset.actionId=data.action?.id||'';
      const grid=element('div','drift-grid');
      const observed=element('section','drift-pane');
      observed.append(element('h4','',`Observed graph · ${data.observed.as_of} · ${data.observed.current_state}`));
      observed.append(element('div','drift-status',`Comparison · ${data.comparison_status}. Historical evidence remains saved at ${data.historical_snapshot?.as_of||'unknown time'}; this view is read-only.`));
      observed.append(element('h5','','Added paths'));
      if(!data.observed.added_paths.length) observed.append(element('p','subtle',data.observed.path_delta.status==='unknown'?'Current path delta is unknown; no additions are inferred.':'No added paths.'));
      for(const row of data.observed.added_paths) renderDriftPath(observed,row,false);
      observed.append(element('h5','','Removed paths'));
      if(!data.observed.removed_paths.length) observed.append(element('p','subtle',data.observed.path_delta.status==='unknown'?'Current path delta is unknown; no removals are inferred.':'No removed paths.'));
      for(const row of data.observed.removed_paths) renderDriftPath(observed,row,true);
      if(data.observed.restoration_source_fact) {
        const fact=data.observed.restoration_source_fact;
        observed.append(element('div','drift-status',`Observed restoration · ${fact.event_id} · ${fact.source_id} · event ${fact.event_at}${fact.source_title?` · ${fact.source_title}`:''}${fact.source_observed_at?` · source observed ${fact.source_observed_at}`:''}`));
      }
      observed.append(element('h5','','Separate SOP witness'));
      const witness=data.observed.sop_witness;
      if(!witness) observed.append(element('div','drift-unknown','Current SOP witness is empty / unknown; the saved historical witness is preserved.'));
      else {
        observed.append(element('div','drift-status',`${witness.linkage_status} · ${witness.conflict_status}${data.observed.sop_witness_unchanged?' · unchanged from reviewed evidence':''}`));
        if(witness.conflict_edge) observed.append(element('div','drift-provenance',`${witness.conflict_edge.from} —[${witness.conflict_edge.relationship} · ${witness.conflict_edge.status}]→ ${witness.conflict_edge.to}`));
        for(const claim of witness.claim_evidence||[]) observed.append(element('div','drift-provenance',`${claim.sop_version_id} · ${claim.claim_label} · ${claim.role_id} · ${claim.source_id} · ${claim.source_title} · observed ${claim.observed_at}`));
        if(!witness.conflict_edge) observed.append(element('div','drift-unknown','Missing witness edge means unknown, not resolved or absent.'));
      }
      const branch=element('section','drift-pane');
      branch.append(element('h4','',`Hypothetical branch · ${data.hypothetical_branch.as_of} · ${data.hypothetical_branch.status}`));
      branch.append(element('div','drift-status',`${data.hypothetical_branch.branch_id||'Branch unknown'} · HYPOTHETICAL ONLY · ${data.hypothetical_branch.event?.event_id||'no branch event'}`));
      branch.append(element('h5','','Branch impact paths'));
      if(!data.hypothetical_branch.paths.length) branch.append(element('p','subtle','No branch paths in this hypothetical state. This is not an observed restoration or a safety conclusion.'));
      for(const row of data.hypothetical_branch.paths) renderDriftPath(branch,row,false);
      branch.append(element('h5','','Observed mainline at the same instant'));
      branch.append(element('div','drift-status',`${data.hypothetical_branch.observed_mainline_status} · ${data.hypothetical_branch.observed_mainline_paths.length} paths · observed state mutated: ${data.hypothetical_branch.observed_state_mutated}`));
      for(const row of data.hypothetical_branch.observed_mainline_paths) renderDriftPath(branch,row,false);
      branch.append(element('div','drift-provenance',`SOP witness unchanged from saved snapshot: ${data.hypothetical_branch.sop_witness_unchanged}. No saved action was rewritten.`));
      grid.append(observed,branch); root.append(grid);
      root.setAttribute('aria-label','Read-only comparison of saved handover evidence with observed and hypothetical graph state');
    }
    function renderActions(actions) {
      $('saved-count').textContent=`${actions.length} saved`;
      const root=$('saved'); root.replaceChildren();
      if(!actions.length) { root.append(element('div','empty','No confirmed handovers yet.')); return; }
      for(const item of actions.slice().reverse()) {
        const card=element('div','saved-item'); card.append(element('strong','',`${item.id} · ${item.incident_id}`));
        card.append(element('p','',`Confirmed ${item.confirmed_at}. Stored only in local app state.`));
        if(item.snapshot) {
          card.append(renderHandoverSnapshot(item.snapshot,true));
          const controls=element('div','drift-controls');
          const compare=element('button','button','Compare with live graph'); compare.type='button'; compare.dataset.compareAction=item.id;
          const result=element('div','drift-comparison'); result.dataset.compareResult=item.id; result.hidden=true; result.setAttribute('aria-live','polite');
          compare.addEventListener('click',async()=>{
            compare.disabled=true; result.hidden=false; result.replaceChildren(element('div','subtle','Reading current operations graph…'));
            try {
              const params=new URLSearchParams({scope:'operations',action_id:item.id});
              const comparison=await api(`/api/handover/compare?${params.toString()}`);
              renderDriftComparison(result,comparison);
            } catch(e) { result.replaceChildren(element('div','drift-unknown',`Comparison unavailable / unknown: ${e.message}. Saved evidence is unchanged.`)); }
            finally { compare.disabled=false; }
          });
          controls.append(compare); card.append(controls,result);
        }
        root.append(card);
      }
    }
    $('reset').addEventListener('click',async()=>{ const button=$('reset'); button.disabled=true; $('notice').textContent='Resetting the owned synthetic scenario…'; try { await api('/api/scenario/reset',{method:'POST',body:'{}'}); $('preview').hidden=true; $('preview').replaceChildren(); $('notice').textContent='Synthetic scenario reseeded from deterministic fixtures.'; await loadAll(); } catch(e) { $('notice').textContent=`Reset failed: ${e.message}`; } finally { button.disabled=false; } });
    $('knowledge-replay').addEventListener('click',()=>loadKnowledgeTimeline());
    for(const button of document.querySelectorAll('[data-known-at]')) button.addEventListener('click',()=>{ $('knowledge-time').value=button.dataset.knownAt; loadKnowledgeTimeline(); });
    $('source-branch-cutoff').addEventListener('change',()=>loadSourceBranch());
    $('watch-preview-button').addEventListener('click',async()=>{
      const button=$('watch-preview-button'); button.disabled=true; $('watch-notice').textContent='';
      try {
        const draft=await api('/api/evidence-watch/draft',{method:'POST',body:JSON.stringify({effective_at:$('watch-effective-at').value,known_at:$('watch-known-at').value})});
        const box=$('watch-preview'); box.replaceChildren(element('h3','',`Preview · ${draft.preview.role_id||'ownership unknown'}`));
        appendWatchWitness(box,'Unsaved graph witness',draft.preview);
        box.append(element('p','watch-boundary','Preview only · no watch state or graph was written. Confirm explicitly to save this one baseline separately from confirmed handovers.'));
        const confirm=element('button','button primary','Confirm & save watch locally'); confirm.type='button'; confirm.id='confirm-evidence-watch';
        confirm.addEventListener('click',async()=>{
          confirm.disabled=true;
          try { const saved=await api('/api/evidence-watch/confirm',{method:'POST',body:JSON.stringify({token:draft.confirmation_token,confirm:true})}); $('watch-notice').textContent=`Saved ${saved.watch.id} locally. No external action was taken.`; box.hidden=true; await loadEvidenceWatch(); }
          catch(error) { $('watch-notice').textContent=`Watch not saved: ${error.message}`; confirm.disabled=false; }
        });
        box.append(confirm); box.hidden=false; $('watch-notice').textContent='Preview prepared in memory only. Nothing has been saved.';
      } catch(error) { $('watch-notice').textContent=`Could not prepare watch preview: ${error.message}`; }
      finally { button.disabled=false; }
    });
    $('watch-refresh-button').addEventListener('click',async()=>{
      const button=$('watch-refresh-button'); button.disabled=true;
      try { const params=new URLSearchParams({scope:'operations',effective_at:$('watch-effective-at').value,known_at:$('watch-known-at').value}); renderEvidenceWatch(await api(`/api/evidence-watch?${params.toString()}`)); $('watch-notice').textContent='Read-only FalkorDB refresh complete. The saved baseline is unchanged.'; }
      catch(error) { $('watch-notice').textContent=`Watch refresh rejected: ${error.message}`; }
      finally { button.disabled=false; }
    });
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
    def __init__(self, db_port: int, graph_name: str, state_dir: Path, db_host: str = "127.0.0.1"):
        if not (1024 <= db_port <= 65535):
            raise ValueError("database port must be an explicitly selected high port")
        if not GRAPH_NAME_RE.fullmatch(graph_name):
            raise ValueError("graph name must use the wardatlas_ prefix and safe characters")
        if not db_host or any(c.isspace() for c in db_host) or "/" in db_host or "://" in db_host:
            raise ValueError("database host must be an explicit hostname or IP, not a URL")
        resolved_state = state_dir.resolve()
        if not resolved_state.is_relative_to(ROOT):
            raise ValueError("local action state must remain inside the project workspace")
        self.db_port = db_port
        self.db_host = db_host
        self.graph_name = graph_name
        self.state_dir = resolved_state
        self.action_file = self.state_dir / "confirmed-actions.json"
        self.watch_file = self.state_dir / "evidence-watch.json"
        self.graph_lock = threading.RLock()
        self.action_lock = threading.RLock()
        self.watch_lock = threading.RLock()
        self.pending: dict[str, dict] = {}
        self.pending_watches: dict[str, dict] = {}
        self.scenario_seeded_by_this_process = False

    def _connection(self) -> RespConnection:
        return RespConnection(self.db_host, self.db_port, timeout=3.0)

    def _rows(self, connection: RespConnection, cypher: str) -> list[dict]:
        return result_rows(query_result(connection.command("GRAPH.QUERY", self.graph_name, cypher)))

    def health(self) -> dict:
        with self.graph_lock:
            connection = self._connection()
            try:
                modules = module_list_maps(connection.command("MODULE", "LIST"))
                module = next((row for row in modules if str(row.get("name", "")).lower() in {"graph", "falkordb"}), None)
                if not module or int(module.get("ver", 0)) < 60000:
                    raise RuntimeError("FalkorDB 6.x or newer graph module is not available")
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

    def evidence_resilience(self, mode: str = "baseline") -> dict:
        # Input validation precedes opening a connection. GRAPH.RO_QUERY adds
        # a database-enforced read-only boundary around both pane traversals.
        validate_resilience_request("mode=" + mode)
        with self.graph_lock:
            connection = self._connection()
            try:
                return resilience_experiment(mode, lambda query: result_rows(query_result(
                    connection.command("GRAPH.RO_QUERY", self.graph_name, query))))
            finally:
                connection.close()

    def decision_docket(self) -> dict:
        """Read the operations docket through its register and source edges."""
        with self.graph_lock:
            connection = self._connection()
            try:
                records = self._rows(connection, RECORDS_QUERY)
                return project_docket(records, lambda query: self._rows(connection, query))
            finally:
                connection.close()

    def decision_timeline(self, known_at: str | None = None) -> dict:
        """Replay source-visible claims and separately sourced record state.

        The cutoff is normalized before the graph connection is opened. Claim
        discovery never traverses the record or its CONSIDERS edges; those are
        queried only after the register-rooted record's own source is visible.
        """
        claim_query, record_query, conflict_query, normalized = build_knowledge_timeline_queries(known_at)
        with self.graph_lock:
            connection = self._connection()
            try:
                claims = self._rows(connection, claim_query)
                records = self._rows(connection, record_query)
                considered = []
                if records:
                    considered = self._rows(
                        connection,
                        knowledge_timeline_considered_query(records[0]["decision_id"], normalized or "0001-01-01T00:00:00Z"),
                    )
                conflict = self._rows(connection, conflict_query) if len({row["version_id"] for row in claims}) >= 2 else []
            finally:
                connection.close()
        return project_knowledge_timeline(normalized, claims, records, considered, conflict)

    def decision_source_branch(self, known_at: str | None = None) -> dict:
        """Compare the accepted source-time timeline with a graph-linked fork.

        The existing timeline remains the mainline projection. Branch V3 is
        discovered only through ScenarioBranch -> ObservationOverride ->
        Evidence, and FalkorDB applies the assumed observation time before
        the claim/conflict/CONSIDERS projection. This endpoint never writes.
        """
        claim_query, record_query, conflict_query, normalized = build_knowledge_timeline_queries(known_at)
        with self.graph_lock:
            # The re-entrant lock spans both projections so in-process graph
            # operations cannot interleave between the mainline and fork.
            mainline = self.decision_timeline(normalized)
            connection = self._connection()
            try:
                branch_rows = self._rows(connection, SOURCE_BRANCH_PATH_QUERY)
                if not branch_rows:
                    return source_branch_response(mainline, normalized, [], None)

                # V2 uses its unchanged source clock. V3 is removed from this
                # result and can re-enter only via the branch-anchored query.
                claims = [
                    row for row in self._rows(connection, claim_query)
                    if row.get("version_id") != "SOP-OPS-9-V3"
                ]
                branch_v3 = self._rows(connection, branch_v3_claim_query(normalized))
                claims.extend(branch_v3)
                records = self._rows(connection, record_query)
                considered = []
                if records:
                    considered = self._rows(
                        connection,
                        knowledge_timeline_considered_query(
                            records[0]["decision_id"], normalized or "0001-01-01T00:00:00Z"
                        ),
                    )
                visible_versions = {row["version_id"] for row in claims}
                conflict = self._rows(connection, conflict_query) if len(visible_versions) >= 2 else []
                projection = branch_claim_projection(normalized, claims, records, considered, conflict)
            finally:
                connection.close()
        return source_branch_response(mainline, normalized, branch_rows, projection)

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
                for query in [*seed_queries(), *seed_shift_board_queries(), *decision_seed_queries(), *source_branch_seed_queries()]:
                    query_result(connection.command("GRAPH.QUERY", self.graph_name, query))
                self.scenario_seeded_by_this_process = True
            finally:
                connection.close()
        with self.action_lock:
            self.pending.clear()
        with self.watch_lock:
            self.pending_watches.clear()
        return {"status": "reset", "scenario": "INC-204 synthetic outage", "scope": "operations", "included_shift_board_cases": ["INC-207", "INC-204"]}

    def _watch_document(self) -> dict:
        if not self.watch_file.exists():
            return {"schema_version": 1, "items": []}
        value = json.loads(self.watch_file.read_text(encoding="utf-8"))
        if value.get("schema_version") != 1 or not isinstance(value.get("items"), list) or len(value["items"]) > 1:
            raise RuntimeError("local evidence-watch state has an unsupported format")
        return value

    @staticmethod
    def _public_watch(record: dict) -> dict:
        return {key: value for key, value in record.items() if key != "confirmation_token_sha256"}

    def saved_evidence_watch(self) -> dict | None:
        with self.watch_lock:
            items = self._watch_document()["items"]
            return self._public_watch(items[0]) if items else None

    def evidence_watch_preview(self, effective_at: str, known_at: str) -> dict:
        effective_at, known_at = normalize_watch_axes(effective_at, known_at)
        # The projection reuses the accepted two-axis FalkorDB board query. No
        # graph or local watch-state write occurs before explicit confirmation.
        board = self.shift_board(dock_at=effective_at, known_at=known_at)
        witness = witness_from_dock(board.get("dock"), effective_at, known_at)
        if witness["role_status"] != "known":
            raise RuntimeError("No complete operations-visible INC-207 ownership witness is available to preview.")
        token = secrets.token_urlsafe(32)
        now = time.time()
        with self.watch_lock:
            self.pending_watches = {
                key: item for key, item in self.pending_watches.items()
                if now - item["created_epoch"] < 1800
            }
            self.pending_watches[token] = {"baseline": witness, "created_epoch": now}
        return {
            "confirmation_token": token,
            "preview": witness,
            "confirmation_required": True,
            "storage_boundary": "separate-local-evidence-watch",
            "graph_writes": 0,
            "external_writes": [],
        }

    def confirm_evidence_watch(self, token: str, confirmed: bool) -> dict:
        if not confirmed:
            raise ValueError("Explicit in-app confirmation is required; no watch was saved.")
        if not isinstance(token, str) or not token or len(token) > 100:
            raise ValueError("Evidence-watch confirmation token is invalid.")
        import hashlib

        token_digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
        with self.watch_lock:
            document = self._watch_document()
            if document["items"]:
                existing = document["items"][0]
                if existing.get("confirmation_token_sha256") == token_digest:
                    return {"watch": self._public_watch(existing), "persisted": True, "idempotent_replay": True, "external_writes": []}
                raise ValueError("The single INC-207 evidence watch is already configured.")
            pending = self.pending_watches.get(token)
            if pending is None or time.time() - pending["created_epoch"] >= 1800:
                raise ValueError("Evidence-watch preview is unavailable or expired; preview it again.")
            record = {
                "id": WATCH_ID,
                "incident_id": "INC-207",
                "baseline": pending["baseline"],
                "confirmed_at": utc_now(),
                "storage": "local-only",
                "confirmation_token_sha256": token_digest,
            }
            self.state_dir.mkdir(parents=True, exist_ok=True)
            temporary = self.state_dir / f".evidence-watch-{secrets.token_hex(6)}.tmp"
            try:
                temporary.write_text(json.dumps({"schema_version": 1, "items": [record]}, indent=2, sort_keys=True) + "\n", encoding="utf-8")
                temporary.replace(self.watch_file)
            finally:
                if temporary.exists():
                    temporary.unlink()
            del self.pending_watches[token]
            return {"watch": self._public_watch(record), "persisted": True, "idempotent_replay": False, "external_writes": []}

    def refresh_evidence_watch(self, effective_at: str | None = None, known_at: str | None = None) -> dict:
        # Validate supplied axes before reading the graph. A missing half of the
        # pair is never replaced with a local-clock or guessed value.
        if (effective_at is None) != (known_at is None):
            raise ValueError("Both effective_at and known_at are required together.")
        normalized = normalize_watch_axes(effective_at, known_at) if effective_at is not None else None
        watch = self.saved_evidence_watch()
        if watch is None:
            return {"scope": "operations", "watch_count": 0, "watch": None, "current_result": None, "external_writes": []}
        if normalized is None:
            effective_at = watch["baseline"]["effective_at"]
            known_at = watch["baseline"]["known_at"]
        else:
            effective_at, known_at = normalized
        board = self.shift_board(dock_at=effective_at, known_at=known_at)
        current = witness_from_dock(board.get("dock"), effective_at, known_at)
        result = public_watch_refresh(watch, current)
        return {"scope": "operations", "watch_count": 1, "watch": watch, "current_result": result, "external_writes": []}

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

    def compare_handover(
        self,
        action_id: str | None = None,
        observed_at: str = CONVEYOR_RESTORED_AT,
        branch_at: str = BRANCH_AT,
    ) -> dict:
        """Compare a saved witness with current graph facts without any writes."""
        # Normalize all caller-supplied time before reading action state or
        # issuing graph queries, so malformed input is a side-effect-free 400.
        observed_at = normalize_timestamp(observed_at)
        branch_at = normalize_timestamp(branch_at)
        actions = self.saved_actions()
        if action_id is None:
            action = actions[-1] if actions else None
        else:
            if not isinstance(action_id, str) or not action_id or len(action_id) > 80:
                raise ValueError("Confirmed handover identifier is invalid.")
            action = next((item for item in actions if item.get("id") == action_id), None)
            if action is None:
                raise LookupError("Confirmed handover not found.")
            if not isinstance(action.get("snapshot"), dict):
                raise ValueError("Confirmed handover has no saved graph witness.")

        observed_impact = self.sop_impact(observed_at)
        branch_board = self.shift_board(DOCK_EFFECTIVE, branch_at, branch_at)
        branch_impact = self.sop_impact(branch_at)
        return compare_graph_witness(action, observed_impact, branch_board, branch_impact)


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
            if mount_briefcase(self, service, parsed):
                return
            if mount_clock_atlas(self, service, parsed):
                return
            if parsed.path == "/":
                self._send(200, APP_HTML)
            elif parsed.path == "/resilience":
                self._send(200, (ROOT / "scripts/ui/resilience.html").read_text(encoding="utf-8"))
            elif parsed.path == "/api/evidence-resilience":
                try:
                    mode = validate_resilience_request(parsed.query)
                except ValueError:
                    self._error(400, "Invalid evidence experiment request.")
                    return
                self._send(200, service.evidence_resilience(mode))
            elif parsed.path == "/knowledge-diff":
                self._send(200, (ROOT / "scripts/ui/knowledge_diff.html").read_text(encoding="utf-8"))
            elif parsed.path == "/api/knowledge-diff":
                if not self._scope_allowed(parsed.query):
                    self._error(403, "This view is not available.")
                else:
                    parameters = parse_knowledge_diff_parameters(parse_qs(parsed.query, keep_blank_values=True))
                    self._send(200, knowledge_diff(service, *parameters))
            elif parsed.path == "/ask":
                self._send(200, QUESTIONS_HTML_PATH.read_text(encoding="utf-8"))
            elif parsed.path in {"/api/briefing", "/api/graph", "/api/shift-board", "/api/sop-impact", "/api/decision-docket", "/api/decision-timeline", "/api/decision-source-branch", "/api/handover/compare", "/api/evidence-watch"} and not self._scope_allowed(parsed.query):
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
            elif parsed.path == "/api/decision-docket":
                self._send(200, service.decision_docket())
            elif parsed.path == "/api/decision-timeline":
                values = parse_qs(parsed.query, keep_blank_values=True)
                if "known_at" in values and len(values["known_at"]) != 1:
                    raise ValueError("Invalid timestamp; use an explicit ISO-8601 offset.")
                data = service.decision_timeline(values.get("known_at", [None])[0])
                self._send(200, data)
            elif parsed.path == "/api/decision-source-branch":
                values = parse_qs(parsed.query, keep_blank_values=True)
                if "known_at" in values and len(values["known_at"]) != 1:
                    raise ValueError("Invalid timestamp; use an explicit ISO-8601 offset.")
                data = service.decision_source_branch(values.get("known_at", [None])[0])
                self._send(200, data)
            elif parsed.path == "/api/handover/compare":
                values = parse_qs(parsed.query, keep_blank_values=True)
                for key in ("action_id", "observed_at", "branch_at"):
                    if key in values and len(values[key]) != 1:
                        raise ValueError("Each comparison parameter must be supplied exactly once.")
                action_id = values.get("action_id", [None])[0]
                if action_id == "":
                    raise ValueError("Confirmed handover identifier is invalid.")
                data = service.compare_handover(
                    action_id,
                    values.get("observed_at", [CONVEYOR_RESTORED_AT])[0],
                    values.get("branch_at", [BRANCH_AT])[0],
                )
                self._send(200, data)
            elif parsed.path == "/api/evidence-watch":
                values = parse_qs(parsed.query, keep_blank_values=True)
                for key in ("effective_at", "known_at"):
                    if key in values and len(values[key]) != 1:
                        raise ValueError("Invalid timestamp; use an explicit ISO-8601 offset.")
                effective_at = values.get("effective_at", [None])[0]
                known_at = values.get("known_at", [None])[0]
                self._send(200, service.refresh_evidence_watch(effective_at, known_at))
            elif parsed.path == "/api/actions":
                items = service.saved_actions()
                self._send(200, {"actions": items, "count": len(items), "storage": "local-only"})
            else:
                self._error(404, "Not found.")
        except LookupError:
            if parsed.path == "/api/handover/compare":
                self._error(404, "Confirmed handover not found.")
            else:
                self._error(503, "Local graph or action state is unavailable.")
        except ValueError as exc:
            if parsed.path == "/api/knowledge-diff":
                self._error(400, "Specify explicit ISO-8601 snapshot times and mainline/hypothetical modes exactly once.")
                return
            if parsed.path in {"/api/shift-board", "/api/sop-impact", "/api/decision-docket", "/api/decision-timeline", "/api/decision-source-branch", "/api/handover/compare", "/api/evidence-watch"}:
                message = str(exc)
                if parsed.path not in {"/api/handover/compare", "/api/evidence-watch"} and message not in {"Invalid timestamp; use an explicit ISO-8601 offset.", "Invalid timestamp"}:
                    message = "Invalid timestamp; use an explicit ISO-8601 offset."
                self._error(400, message or "Invalid timestamp; use an explicit ISO-8601 offset.")
            else:
                self._error(503, "Local graph or action state is unavailable.")
        except Exception:
            self._error(503, "Local graph or action state is unavailable.")

    def do_POST(self):
        parsed = urlsplit(self.path)
        service = self.server.service
        if parsed.path == "/api/questions":
            post_questions(self, service)
            return
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
        if parsed.path == "/api/evidence-watch/draft":
            try:
                body = self._json_body()
                self._send(201, service.evidence_watch_preview(
                    body.get("effective_at", WATCH_DEFAULT_EFFECTIVE_AT),
                    body.get("known_at", WATCH_DEFAULT_KNOWN_AT),
                ))
            except ValueError as exc:
                self._error(400, str(exc))
            except RuntimeError as exc:
                self._error(409, str(exc))
            except Exception:
                self._error(503, "A local evidence-watch preview could not be prepared.")
            return
        if parsed.path == "/api/evidence-watch/confirm":
            try:
                body = self._json_body()
                token = body.get("token")
                if not isinstance(token, str) or len(token) > 100:
                    raise ValueError("Evidence-watch confirmation token is invalid.")
                self._send(201, service.confirm_evidence_watch(token, body.get("confirm") is True))
            except ValueError as exc:
                self._error(400, str(exc))
            except Exception:
                self._error(503, "The local evidence watch could not be saved.")
            return
        self._error(404, "Not found.")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--app-port", type=int, required=True)
    parser.add_argument("--db-port", type=int, required=True)
    parser.add_argument("--db-host", default="127.0.0.1")
    parser.add_argument("--graph-name", required=True)
    parser.add_argument("--state-dir", type=Path, default=ROOT / ".local/wardatlas-state")
    args = parser.parse_args()
    if not (1024 <= args.app_port <= 65535):
        parser.error("app port must be an explicitly selected high port")
    service = WardAtlasService(args.db_port, args.graph_name, args.state_dir, db_host=args.db_host)
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

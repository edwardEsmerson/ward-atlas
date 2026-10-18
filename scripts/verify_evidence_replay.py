#!/usr/bin/env python3
"""Check frozen replay inventories on owned real FalkorDB, API and browsers.

Bootstrap/cleanup uses the accepted source-branch harness, not a new query
engine. Oracle facts come only from the two byte-pinned source contracts.
"""
from __future__ import annotations
import argparse
import copy
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import traceback

from verify_decision_source_branch_live import SourceBranchLive
from wardatlas_app import WardAtlasService
from wardatlas_replay import replay_frame
from wardatlas_decisions import disconnected_decoy_queries, restricted_decision_queries
from wardatlas_fixtures import restricted_fixture_queries
from wardatlas_source_branch import disconnected_decoy_branch_queries, restricted_source_branch_queries

ROOT = Path(__file__).resolve().parents[1]
CONTRACT = "contracts/evidence-replay/"
PINS = {"expected-results.json": "bfdcadbe54320db7f84412fa1eb5446043c0f7fa12161d4d3da685c4f7ee1598", "adversarial-matrix.json": "8d5d19577ab80c6a889c02817310b068df48203ea82fe889cd02d3f1f8b8df24"}


def load_contracts():
    for name, digest in PINS.items():
        assert hashlib.sha256((ROOT / CONTRACT / name).read_bytes()).hexdigest() == digest, "frozen replay contract changed"
    expected = json.loads((ROOT / CONTRACT / "expected-results.json").read_text())
    matrix = json.loads((ROOT / CONTRACT / "adversarial-matrix.json").read_text())
    assert expected["candidate_output_is_oracle"] is False and matrix["candidate_output_is_oracle"] is False
    for pin in expected["pinned_sources"]:
        assert hashlib.sha256((ROOT / pin["path"]).read_bytes()).hexdigest() == pin["sha256"], "source oracle changed"
    assert len(matrix["groups"]) >= 8 and sum(len(g["assertions"]) for g in matrix["groups"]) >= 40
    assert len({g["id"] for g in matrix["groups"]}) == len(matrix["groups"])
    assert all(g["assertions"] and len(g["assertions"]) == len(set(g["assertions"])) for g in matrix["groups"])
    return expected, matrix


def oracle_projection(spec):
    """Read a literal accepted snapshot; normalize only the branch clock label."""
    namespace, identity = spec["source_ref"].split("#")
    data = json.loads((ROOT / "contracts" / namespace / "expected-results.json").read_text())
    cases = data["cases" if namespace == "decision-knowledge-timeline" else "groups"]
    p = copy.deepcopy(next(c["expected"]["projection"] for c in cases if c["id"] == identity))
    if spec["frame"] == 5:
        s = p["claims"][1]["source"]
        assert s["observed_at"] == spec["assumed_observed_at"]
        s["observed_at"] = spec["actual_observed_at"]
        s["assumed_observed_at"] = spec["assumed_observed_at"]
    return p


def oracle_inventory(spec, expected):
    """Independent literal edge inventory, with exact accepted node metadata."""
    p = oracle_projection(spec)
    source_fields = lambda s: {k: s[k] for k in ("id", "title", "observed_at", "assumed_observed_at") if k in s}
    nodes, support = {}, {}
    for c in p["claims"]:
        s = source_fields(c["source"])
        nodes[c["version_id"]] = {"id": c["version_id"], "kind": "claim", "label": "unverified SOP claim"}
        nodes[c["role_id"]] = {"id": c["role_id"], "kind": "role"}
        eq = c["applicability_edge"]["to"]
        nodes[eq] = {"id": eq, "kind": "equipment"}
        nodes[s["id"]] = {**s, "kind": "evidence"}
        support[c["version_id"]] = s
    r = p["decision_record"]
    if r:
        s = source_fields(r["source"])
        nodes[r["id"]] = {**expected["record"], "kind": "record"}
        nodes[r["register_id"]] = {"id": r["register_id"], "kind": "register"}
        nodes[s["id"]] = {**s, "kind": "evidence"}
        support[r["id"]] = support[r["register_id"]] = s
    edges = []
    for key in spec["edges"]:
        a, rel, b = expected["edge_definitions"][key].split("|")
        citations = [support[a]]
        if rel in ("CONSIDERS", "SOP_CONFLICT"):
            citations.append(support[b])
        edge = {"id": "|".join((a, rel, b)), "from": a, "relationship": rel, "to": b,
                "epistemic_type": "source-fact" if rel == "CLAIMS_ROLE" else "graph-derived",
                "label": "unverified SOP claim" if rel == "CLAIMS_ROLE" else "graph-derived relationship",
                "sources": sorted(citations, key=lambda s: s["id"])}
        if rel == "SOP_CONFLICT": edge["status"] = "unresolved"
        edges.append(edge)
    sources = sorted({s["id"]: s for s in support.values()}.values(), key=lambda s: s["id"])
    assert {s["id"] for s in sources} == set(spec["sources"])
    assert [c["version_id"] for c in p["claims"]] == spec["claims"]
    assert {e["id"] for e in edges} == {"|".join((e["from"], e["relationship"], e["to"])) for e in edges}
    return {"nodes": sorted(nodes.values(), key=lambda n: n["id"]), "edges": sorted(edges, key=lambda e: e["id"]), "sources": sources}


def assert_frame(value, spec, expected):
    inventory = oracle_inventory(spec, expected)
    for k in inventory: assert value[k] == inventory[k], f"frame {spec['frame']} {k} differs from pinned witnesses"
    assert value["frame"] == spec["frame"] and value["mode"] == spec["mode"] and value["known_at"] == spec["known_at"]
    assert value["generic_unknown"] == spec["unknown"]
    assert value["read_only"] and not value["source_fact_modified"] and not value["negative_conclusion_asserted"]
    assert set(value) == {"frame", "mode", "known_at", "state", "generic_unknown", "nodes", "edges", "sources", "read_only", "source_fact_modified", "negative_conclusion_asserted"}


def is_unknown(value):
    return value.get("generic_unknown") is True and value["state"] == "unknown" and all(value[k] == [] for k in ("nodes", "edges", "sources")) and not any(s in json.dumps(value) for s in ("SRC-", "DEC-", "SOP-", "ROLE-", "REGISTER-", "CONVEYOR-"))


def free_ports(db_port=None, app_port=None):
    """Explicit ports are honored; otherwise reserve free allocated/ephemeral ones."""
    sockets, ports = [], []
    base, span = int(os.environ.get("WORKSHOP_PORT_BASE", 0)), int(os.environ.get("WORKSHOP_PORT_SPAN", 1000))
    try:
        for explicit in (db_port, app_port):
            candidates = [explicit] if explicit is not None else (range(base, base + span) if base else [0])
            for port in candidates:
                s = socket.socket()
                try: s.bind(("127.0.0.1", port))
                except OSError:
                    s.close()
                    if explicit is not None: raise
                    continue
                sockets.append(s); ports.append(s.getsockname()[1]); break
            else: raise RuntimeError("No free lane ports")
        return ports
    finally:
        for s in sockets: s.close()


class ReplayLive(SourceBranchLive):
    def __init__(self, args, expected, matrix):
        adapted = {"groups": [{"group_id": g["id"], "pinned_assertions": [{"id": a} for a in g["assertions"]]} for g in matrix["groups"]]}
        super().__init__(args, dict(expected, groups=[]), adapted)
        self.coverage = {g["id"]: set() for g in matrix["groups"]}
        self.db.graph = "wardatlas_test_replay_" + self.run_id
        self.state_dir = ROOT / ".local" / "evidence-replay-runs" / self.run_id
        self.expected = expected

    def checks(self, group, values):
        for key, value in values.items(): self.check(group, key, value)

    def api(self, frame):
        status, body = self.get(f"/api/replay-frame?frame={frame}&mode={self.expected['frames'][frame]['mode']}&scope=operations")
        assert status == 200, (status, body)
        return body

    def browser(self, label, mode="existing"):
        path = self.args.evidence_dir / (label + ".json")
        result = subprocess.run(["node", str(ROOT / "scripts/replay_browser_assertions.cjs"), self.base_url, mode, str(path), str(self.args.evidence_dir / label)], capture_output=True, text=True, timeout=180)
        assert result.returncode == 0, (result.stdout + result.stderr)[-5000:]
        report = json.loads(path.read_text())
        assert report["status"] == "PASS" and report["assertion_count"] > 0
        return report

    def delete(self, a, rel, b):
        self.db.graph_query("owned-edge-mutation", f"MATCH (a {{id:'{a}'}})-[r:{rel}]->(b {{id:'{b}'}}) DELETE r")

    def state(self): return {p.name: p.read_bytes() for p in self.state_dir.iterdir() if p.is_file()}

    def run(self):
        self.start()
        cold = [self.api(i) for i in range(6)]
        browser = self.browser("cold", "unknown")
        self.checks("cold", {"all-unknown": all(is_unknown(v) for v in cold), "identity-free": all(not v['nodes'] for v in cold), "no-counts": not any('count' in k for v in cold for k in v), "browser-empty": browser['flags']['unknown'], "not-canned": cold[3]['edges'] == [] and cold[5]['edges'] == []})
        self.reset()
        clean = [self.api(i) for i in range(6)]
        for spec, value in zip(self.expected['frames'], clean):
            assert_frame(value, spec, self.expected)
            inv = oracle_inventory(spec, self.expected)
            future = [s for s in self.expected['sources'] if s['id'] not in spec['sources']]
            self.checks('frame-'+str(spec['frame']), {
                'exact-edges': value['edges'] == inv['edges'], 'exact-nodes': value['nodes'] == inv['nodes'], 'exact-sources': value['sources'] == inv['sources'],
                'record-gated': [n for n in value['nodes'] if n['kind']=='record'] == ([dict(self.expected['record'],kind='record')] if spec['record'] else []),
                'epistemic-types': all(e['epistemic_type'] == ('source-fact' if e['relationship']=='CLAIMS_ROLE' else 'graph-derived') for e in value['edges']),
                'no-hindsight': not any(s['id'] in json.dumps(value) or s['title'] in json.dumps(value) for s in future) and not any(e['relationship'] in ('AFFECTS','SUPPORTS') for e in value['edges'])})
        sources = {s['id']:s for s in clean[5]['sources']}
        self.checks('frame-5', {'actual-time': sources['SRC-SOP-CURRENT-V3']['observed_at']=='2026-10-14T16:04:00Z', 'assumed-time':sources['SRC-SOP-CURRENT-V3']['assumed_observed_at']=='2026-10-14T16:06:00Z', 'v2-unchanged': sources['SRC-SOP-CURRENT-V2']==self.expected['sources'][0]})
        mainline = clean[:4]
        unknowns=[]
        for i, (a,rel,b) in enumerate([('BR-SOP-V3-LATE-OBS','HAS_HYPOTHETICAL_OBSERVATION','OBS-SOP-V3-LATE-OBS'),('OBS-SOP-V3-LATE-OBS','FOR_SOURCE','SRC-SOP-CURRENT-V3')]):
            self.delete(a,rel,b); values=[self.api(4),self.api(5)];unknowns.extend(values)
            self.check('mutations', 'branch-edge-unknown' if i==0 else 'source-edge-unknown', all(is_unknown(v) for v in values))
            assert [self.api(j) for j in range(4)] == mainline
            if i==0:
                missing = self.browser('missing','missing')
                self.check('mutations','browser-unknown',missing['flags']['unknown'])
            self.reset()
        self.check('mutations','no-fallback',all(is_unknown(v) for v in unknowns))
        self.check('mutations','mainline-isolated',[self.api(j) for j in range(4)]==mainline)
        self.delete('SOP-OPS-9-V3','EVIDENCED_BY','SRC-SOP-CURRENT-V3')
        reduced=self.api(3)
        self.check('mutations','v3-evidence-suppressed', all(reduced[k]==clean[4][k] for k in ('nodes','edges','sources')) and self.api(5)['edges']==clean[4]['edges'])
        self.check('mutations','v2-record-retained', any(n.get('recorded_state')=='open-unresolved' for n in reduced['nodes']) and any(n['id']=='SOP-OPS-9-V2' for n in reduced['nodes']))
        self.reset();self.check('mutations','reseed-restores',[self.api(i) for i in range(6)]==clean)
        # Nonempty local sentinels make read-only checks non-vacuous.
        self.service.action_file.write_text('[{"id":"owned-replay-action-canary"}]\n')
        self.service.watch_file.write_text('{"id":"owned-replay-watch-canary"}\n')
        before=self.graph_snapshot('before-ui');state=self.state()
        baseline=self.browser('baseline')
        self.checks('browser',{key:baseline['flags'][key] for key in ('six-live-frames','desktop','mobile','keyboard-nodes','keyboard-edges','evidence-list','reduced-motion','stale-response','rapid-controls','unload-cancels','no-future-dom','unavailable')})
        self.checks('lifecycle',{'graph-readonly':before==self.graph_snapshot('after-ui'),'actions-readonly':state[self.service.action_file.name]==self.service.action_file.read_bytes(),'watches-readonly':state[self.service.watch_file.name]==self.service.watch_file.read_bytes(),'reset-selector-only':baseline['flags']['reset-selector-only'] and state==self.state()})
        self.seed('decoys', [*disconnected_decoy_queries(),*disconnected_decoy_branch_queries()])
        self.check('privacy','decoy-api',[self.api(i) for i in range(6)]==clean)
        self.seed('restricted', [*restricted_fixture_queries(),*restricted_decision_queries(),*restricted_source_branch_queries()])
        injected=[self.api(i) for i in range(6)];after=self.browser('injected')
        self.checks('privacy',{'restricted-api':injected==clean,'desktop-invariant':baseline['desktop']==after['desktop'],'mobile-invariant':baseline['mobile']==after['mobile'],'accessibility-invariant':baseline['accessibility']==after['accessibility'],'no-tripwires':not any(t in json.dumps(injected)+json.dumps(after) for t in self.expected['tripwires'])})
        self.db.graph_query('owned-deny',"MATCH (s:Evidence {id:'SRC-SOP-CURRENT-V3'}) SET s.access='restricted'")
        self.check('privacy','denied-source-suppressed',all(self.api(3)[k]==clean[4][k] for k in ('nodes','edges','sources')) and is_unknown(self.api(5)))
        self.reset()
        calls=[]; original=(self.service.decision_timeline,self.service.decision_source_branch)
        self.service.decision_timeline=lambda *a:calls.append(a)
        self.service.decision_source_branch=lambda *a:calls.append(a)
        try:statuses=[self.get('/api/replay-frame?'+q)[0] for q in self.expected['invalid_queries']]
        finally:self.service.decision_timeline,self.service.decision_source_branch=original
        self.checks('validation',{'all-rejected':all(s==400 for s in statuses),'duplicate-frame':statuses[6]==400,'duplicate-mode':statuses[7]==400,'duplicate-scope':statuses[12]==400,'zero-queries':calls==[],'no-state-writes':state==self.state()})
        first=self.graph_snapshot('seed-first');self.seed('repeat',self.fixture_queries());second=self.graph_snapshot('seed-repeat')
        fresh=WardAtlasService(self.args.db_port,self.db.graph,self.state_dir)
        self.checks('lifecycle',{'repeat-seed':first==second and [self.api(i) for i in range(6)]==clean,'reconnect':[replay_frame(fresh,i,s['mode']) for i,s in enumerate(self.expected['frames'])]==clean,'root-link':'href="/time-machine"' in self.get('/')[1]})
        # Force an actual failed DB connection, not a canned response or fake DB.
        held=socket.socket();held.bind(('127.0.0.1',0));original_port=self.service.db_port;self.service.db_port=held.getsockname()[1]
        try:
            status,value=self.get('/api/replay-frame?frame=3&mode=mainline')
            assert status==503 and value['state']=='unavailable' and 'SRC-' not in json.dumps(value)
        finally:self.service.db_port=original_port;held.close()


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    for flag in ('all','require-live-falkordb','desktop-mobile','assert-keyboard','assert-reduced-motion','fail-on-zero'):parser.add_argument('--'+flag,action='store_true',required=True)
    parser.add_argument('--db-port',type=int);parser.add_argument('--app-port',type=int);parser.add_argument('--evidence-dir',type=Path)
    args=parser.parse_args(argv);v=None;error=None
    args.evidence_dir=args.evidence_dir or Path(os.environ.get('WORKSHOP_RUN_DIR',str(ROOT/'.local/replay-evidence')))/'evidence-replay'
    args.evidence_dir.mkdir(parents=True,exist_ok=True)
    try:
        expected,matrix=load_contracts();args.db_port,args.app_port=free_ports(args.db_port,args.app_port);args.lane=os.environ.get('WORKSHOP_LANE','local')
        v=ReplayLive(args,expected,matrix);v.run()
    except BaseException as exc:error=f'{type(exc).__name__}: {exc}';traceback.print_exc()
    finally:
        if v:
            v.stop()
            if not error:
                try:
                    v.check('lifecycle','owned-cleanup',all(v.cleanup.values()))
                    assert all(v.coverage[g['id']]==set(g['assertions']) for g in matrix['groups']), 'incomplete mapped coverage'
                except Exception as exc:error=str(exc)
        report={'status':'FAIL' if error else 'PASS','error':error,'oracle_sha256':PINS,'groups':v.assertions if v else [],'mapped_assertion_count':len(v.assertions) if v else 0,'coverage':{k:sorted(s) for k,s in v.coverage.items()} if v else {},'cleanup':v.cleanup if v else {},'database':v.db.database if v else {},'query_count':v.db.query_count if v else 0}
        path=args.evidence_dir/'live.json';path.write_text(json.dumps(report,indent=2)+'\n');print(json.dumps({'status':report['status'],'assertions':report['mapped_assertion_count'],'report':str(path)}))
    return bool(error)

if __name__=='__main__':sys.exit(main())

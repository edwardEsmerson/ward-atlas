#!/usr/bin/env python3
"""Frozen-oracle API/real FalkorDB/browser check. Owns and cleans every resource."""
from __future__ import annotations
import argparse, copy, hashlib, json, os, socket, subprocess, sys, uuid
from pathlib import Path
from verify_decision_source_branch_live import SourceBranchLive, same
from wardatlas_app import WardAtlasService
from wardatlas_fixtures import node, edge, seed_queries_for
from wardatlas_resilience import AS_OF, KNOWN_AT, MODES

ROOT=Path(__file__).resolve().parents[1]
def load(p): return json.loads((ROOT/p).read_text())
def validate_contract(matrix=None):
    expected=load('contracts/resilience/expected-results.json')
    matrix=matrix if matrix is not None else load('contracts/resilience/adversarial-matrix.json')
    for path,digest in expected['sources'].items():
        assert hashlib.sha256((ROOT/path).read_bytes()).hexdigest()==digest, 'source oracle changed'
    assert matrix['candidate_output_is_oracle'] is False
    assert len(matrix['groups'])>=8 and len({g['id'] for g in matrix['groups']})==len(matrix['groups'])
    assert all(g['assertions'] and len(set(g['assertions']))==len(g['assertions']) for g in matrix['groups'])
    assert sum(len(g['assertions']) for g in matrix['groups'])>=40, 'zero/insufficient mappings'
    return expected,matrix

def source_projection(case,key=None):
    cases={c['id']:c['expected'] for c in load('contracts/decision-knowledge-timeline/expected-results.json')['cases']}
    value=cases[case]
    if key: value=value[key]
    return copy.deepcopy(value.get('projection',value))

def expected_pane(mode):
    e=load('contracts/resilience/expected-results.json'); spec=e[mode]
    paths=load('tests/expected-results.json')['expected_evidence_by_path']
    paths=[p for p in paths if p['path'] in spec['paths']]
    k=source_projection(spec['knowledge_source_case'],spec.get('knowledge_source_key'))
    if mode=='unsupported': k['known_at']=KNOWN_AT
    src={}
    for p in paths:
        for i,t,o in zip(p['source_ids'],p['source_titles'],p['observed_at']): src[(i,t,o)]={'source_id':i,'title':t,'observed_at':o}
    for s in [c['source'] for c in k['claims']]+([k['decision_record']['source']] if k['decision_record'] else []):
        src[(s['id'],s['title'],s['observed_at'])]={'source_id':s['id'],'title':s['title'],'observed_at':s['observed_at']}
    return {'ownership':{'state':spec['ownership_state'],'paths':paths},'knowledge':k,'sources':[src[x] for x in sorted(src)]}

def assert_projection(mode,value,expected=None):
    e=expected or load('contracts/resilience/expected-results.json')
    assert value['scope']=='operations' and value['mode']==mode
    assert value['as_of']==e['as_of'] and value['known_at']==e['known_at']
    assert value['actual']==expected_pane('baseline'), 'actual witness differs'
    assert value['hypothetical']==expected_pane(mode), 'hypothesis witness differs'
    assert value['omission']==e[mode].get('omission'), 'unsupported assumption'
    for key in ('selected_version','resolution_asserted','safe_asserted','role_disappearance_asserted','source_facts_unchanged'):
        assert value[key]==e['invariants'][key], key
    assert value['dependent_evidence_state']==('not-selected' if mode=='baseline' else 'unknown')

def assert_unknown(value):
    assert value['omission'] is None and value['mode'] is None
    assert value['hypothetical']==expected_pane('unsupported')
    assert value['safe_asserted'] is False and value['resolution_asserted'] is False

def free_ports():
    sockets=[]
    try:
        base=int(os.environ.get('WORKSHOP_PORT_BASE',0)); span=int(os.environ.get('WORKSHOP_PORT_SPAN',1000))
        for port in (range(base+110,base+span) if base else [0,0]):
            s=socket.socket()
            try:s.bind(('127.0.0.1',port))
            except OSError:s.close();continue
            sockets.append(s)
            if len(sockets)==2:return tuple(s.getsockname()[1] for s in sockets)
        raise RuntimeError('No free owned ports')
    finally:
        for s in sockets:s.close()

class Verify(SourceBranchLive):
    def __init__(self,args,e,m):
        adapted={'groups':[{'group_id':g['id'],'pinned_assertions':[{'id':i} for i in g['assertions']]} for g in m['groups']]}
        super().__init__(args,dict(e,groups=[]),adapted)
        self.coverage={g['id']:set() for g in m['groups']};self.original_matrix=m
        self.db.graph='wardatlas_test_resilience_'+self.run_id
    def browser(self,label,mode='existing'):
        report=self.args.evidence_dir/(label+'.json')
        subprocess.run(['node',str(ROOT/'scripts/resilience_browser_assertions.cjs'),self.base_url,mode,str(report),str(self.args.evidence_dir/label)],check=True,timeout=180)
        value=json.loads(report.read_text());assert value['status']=='PASS' and value['assertion_count']>0
        return value
    def api(self,mode='baseline'):
        status,value=self.get('/api/evidence-resilience?mode='+mode);assert status==200,(status,value);return value
    def checks(self,g,values):
        for key,value in values.items(): self.check(g,key,value)
    def run(self):
        self.start()
        empty={m:self.api(m) for m in MODES}
        for m in MODES[1:]:assert_unknown(empty[m])
        self.checks('empty-unknown',{'baseline-unknown':empty['baseline']['actual']==expected_pane('unsupported'),'owner-unknown':empty[MODES[1]]['mode'] is None,'v3-unknown':empty[MODES[2]]['mode'] is None,'no-identities':not any(x in json.dumps(empty) for x in ['SOP-OPS','INC-204','SRC-','ROLE-']),'no-existence-hints':all(v['omission'] is None for v in empty.values())})
        self.browser('cold-empty','unknown')
        self.reset(); baseline=self.api();owner=self.api(MODES[1]);v3=self.api(MODES[2])
        for m,v in zip(MODES,[baseline,owner,v3]):assert_projection(m,v)
        self.checks('exact-owner',{'baseline-paths':baseline['actual']['ownership']==expected_pane('baseline')['ownership'],'baseline-sources':baseline['actual']['sources']==expected_pane('baseline')['sources'],'baseline-knowledge':baseline['actual']['knowledge']==expected_pane('baseline')['knowledge'],'omitted-paths':owner['hypothetical']['ownership']==expected_pane(MODES[1])['ownership'],'omission-edge':owner['omission']==self.expected[MODES[1]]['omission'],'dependent-unknown':owner['dependent_evidence_state']=='unknown','unchanged-knowledge':owner['actual']['knowledge']==owner['hypothetical']['knowledge']})
        k=v3['hypothetical']['knowledge']
        self.checks('exact-v3',{'baseline-two-claims':len(baseline['actual']['knowledge']['claims'])==2,'baseline-conflict':baseline['actual']['knowledge']['conflict']['edge']['status']=='unresolved','omitted-projection':k==expected_pane(MODES[2])['knowledge'],'record-retained':k['decision_record']['recorded_state']=='open-unresolved','only-v2-considered':[e['to'] for e in k['decision_considers_edges']]==['SOP-OPS-9-V2'],'no-winner-resolution':v3['selected_version'] is None and not v3['resolution_asserted'],'support-cards':v3['hypothetical']['sources']==expected_pane(MODES[2])['sources']})
        # Existing local action/watch bytes must survive GETs and UI reset;
        # nonempty sentinels prevent a vacuous empty-directory assertion.
        self.service.action_file.write_text('[{"id":"owned-read-only-canary"}]\n')
        self.service.watch_file.write_text('{"owned-read-only-canary":true}\n')
        before=self.graph_snapshot('before'); state={p.name:p.read_bytes() for p in self.state_dir.iterdir()}
        main=self.get('/api/decision-timeline?known_at='+KNOWN_AT)[1]; self.api(MODES[1]);self.api(MODES[2]);after=self.graph_snapshot('after')
        self.checks('reset-invariance',{'reset-baseline':self.api()==baseline,'mainline-api':self.get('/api/decision-timeline?known_at='+KNOWN_AT)[1]==main,'stored-facts':before==after,'graph-unchanged':before==after,'state-unchanged':state=={p.name:p.read_bytes() for p in self.state_dir.iterdir()}})
        def delete(a,rel,b):self.db.graph_query('owned-mutation',f"MATCH (a {{id:'{a}'}})-[r:{rel}]->(b {{id:'{b}'}}) DELETE r")
        delete('DEPT-CENTRAL-STERILE','OWNED_BY','ROLE-STERILE-OPS-COORD');o=self.api();unsupported=self.api(MODES[1]);assert_unknown(unsupported)
        self.check('mutation-sensitivity','owner-edge-real',[p['path'] for p in o['actual']['ownership']['paths']]==self.expected['mutations']['owner_edge_removed_paths'])
        self.check('mutation-sensitivity','owner-unsupported',unsupported['omission'] is None)
        self.reset();delete('SVC-MATERIAL-TRANSFER','EVIDENCED_BY','SRC-SERVICE-CATALOG');self.check('mutation-sensitivity','service-source-required',self.api()['actual']['ownership']['paths']==[])
        self.reset();delete('SOP-OPS-9-V3','EVIDENCED_BY','SRC-SOP-CURRENT-V3');v=self.api();assert_unknown(self.api(MODES[2]))
        self.check('mutation-sensitivity','v3-edge-real',v['actual']['knowledge']==expected_pane(MODES[2])['knowledge']);self.check('mutation-sensitivity','v3-unsupported',self.api(MODES[2])['mode'] is None)
        self.reset();self.db.graph_query('owned-time-mutation',"MATCH (s:Evidence {id:'SRC-SOP-CURRENT-V3'}) SET s.observed_at='2026-10-14T16:06:00Z'")
        self.check('mutation-sensitivity','timestamp-sensitive',self.api()['actual']['knowledge']==source_projection('v3-observation-time-shift')['at_16_05']);self.reset();self.check('mutation-sensitivity','reseed-restores',self.api()==baseline)
        clean={m:self.api(m) for m in MODES};browser_before=self.browser('clean')
        self.seed('decoy',seed_queries_for([node('SOPVersion','DECOY-RESILIENCE',access='operations'),node('Evidence','DECOY-SOURCE',title='Disconnected resilience canary',observed_at=KNOWN_AT,access='operations')],[edge('DECOY-RESILIENCE','EVIDENCED_BY','DECOY-SOURCE','DECOY-EDGE',access='operations')]))
        self.check('decoy-denied','decoy-excluded',all(self.api(m)==clean[m] for m in MODES))
        self.seed('denied',seed_queries_for([node('Evidence','SECRET-RESILIENCE',title='Restricted resilience canary',fact='hidden supporting instruction',observed_at='2026-10-14T15:00:00Z',access='restricted')],[edge('SOP-OPS-9-V3','EVIDENCED_BY','SECRET-RESILIENCE','SECRET-EDGE',access='operations')]))
        self.check('decoy-denied','injection-connected',bool(self.graph_rows('injection-check',"MATCH (:SOPVersion {id:'SOP-OPS-9-V3'})-[:EVIDENCED_BY]->(s:Evidence {id:'SECRET-RESILIENCE'}) RETURN s.id AS id")))
        injected={m:self.api(m) for m in MODES};browser_after=self.browser('injected')
        self.checks('decoy-denied',{'full-api-invariant':clean==injected,'desktop-dom-invariant':browser_before['desktop']==browser_after['desktop'],'mobile-dom-invariant':browser_before['mobile']==browser_after['mobile'],'no-tripwires':not any(t in json.dumps(injected)+json.dumps(browser_after) for t in self.expected['restricted_tripwires'])})
        self.db.graph_query('deny-witness',"MATCH (s:Evidence {id:'SRC-SOP-CURRENT-V3'}) SET s.access='restricted'");assert_unknown(self.api(MODES[2]));self.check('decoy-denied','denied-witness-generic',self.api(MODES[2])['omission'] is None);self.reset()
        calls=[];original=self.service.evidence_resilience;self.service.evidence_resilience=lambda *a:calls.append(a)
        try: statuses=[self.get('/api/evidence-resilience?'+q)[0] for q in self.expected['invalid_queries']]
        finally:self.service.evidence_resilience=original
        self.checks('reject-before-query',{'invalid-mode':statuses[0]==400,'duplicate-mode':statuses[2]==400,'invalid-scope':statuses[3]==400,'duplicate-scope':statuses[4]==400,'arbitrary-input':all(s==400 for s in statuses),'zero-queries':calls==[],'state-unchanged':state=={p.name:p.read_bytes() for p in self.state_dir.iterdir()}})
        c1=self.graph_counts('first');self.reset();c2=self.graph_counts('second');snap=self.graph_snapshot('browser-before');browser=self.browser('repeat');fresh=WardAtlasService(self.args.db_port,self.db.graph,self.state_dir)
        self.checks('repeat-browser-cleanup',{'repeat-exact':self.api()==baseline,'reseed-node-delta':c1['nodes']==c2['nodes'],'reseed-edge-delta':c1['relationships']==c2['relationships'],'reconnect':all(fresh.evidence_resilience(m)==clean[m] for m in MODES),'read-only':snap==self.graph_snapshot('browser-after') and state=={p.name:p.read_bytes() for p in self.state_dir.iterdir()},'keyboard':all(a['passed'] for a in browser['assertions'] if 'keyboard' in a['name']),'desktop':bool(browser['desktop']),'mobile':bool(browser['mobile']),'root-link':'href="/resilience"' in self.get('/')[1]})

def main():
    p=argparse.ArgumentParser();[p.add_argument('--'+f,action='store_true',required=True) for f in ['all','require-live-falkordb','desktop-mobile','fail-on-zero']];args=p.parse_args()
    e,m=validate_contract();args.lane=os.environ.get('WORKSHOP_LANE','local');args.db_port,args.app_port=free_ports();args.evidence_dir=Path(os.environ.get('WORKSHOP_RUN_DIR',str(ROOT/'.local/resilience-evidence')))/'resilience';args.evidence_dir.mkdir(parents=True,exist_ok=True)
    v=Verify(args,e,m);error=None
    try:v.run()
    except Exception as exc:error=f'{type(exc).__name__}: {exc}';import traceback;traceback.print_exc()
    finally:
        v.stop()
        if not error:
            try:v.check('repeat-browser-cleanup','owned-cleanup',all(v.cleanup.values()));assert all(v.coverage[g['id']]==set(g['assertions']) for g in m['groups'])
            except Exception as exc:error=str(exc)
        report={'status':'FAIL' if error else 'PASS','error':error,'groups':v.assertions,'mapped_assertion_count':len(v.assertions),'coverage':{k:sorted(s) for k,s in v.coverage.items()},'cleanup':v.cleanup,'database':v.db.database,'query_count':v.db.query_count}
        (args.evidence_dir/'live.json').write_text(json.dumps(report,indent=2));print(json.dumps({'status':report['status'],'assertions':len(v.assertions),'report':str(args.evidence_dir/'live.json')}))
    return bool(error)
if __name__=='__main__':sys.exit(main())

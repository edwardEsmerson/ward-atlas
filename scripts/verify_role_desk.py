#!/usr/bin/env python3
"""Original role-desk checks: pinned oracles, actual isolated FalkorDB and browser."""
from __future__ import annotations
import argparse, hashlib, json, os, socket, subprocess, sys
from pathlib import Path
from urllib.parse import urlencode
from verify_decision_source_branch_live import SourceBranchLive
from wardatlas_app import WardAtlasService
from wardatlas_fixtures import node, edge, seed_queries_for
from wardatlas_role_desk import parse_request, queries

ROOT=Path(__file__).resolve().parents[1]
def load(path):return json.loads((ROOT/path).read_text())
def validate_contract(matrix=None):
    e=load('contracts/role-desk/expected-results.json');m=matrix if matrix is not None else load('contracts/role-desk/adversarial-matrix.json')
    for path,digest in e['sources'].items():assert hashlib.sha256((ROOT/path).read_bytes()).hexdigest()==digest,'changed original oracle'
    for ref in e['provenance'].values():
        path,pointer=ref.split('#');v=load(path)
        for part in pointer.strip('/').split('/'):v=v[int(part)] if isinstance(v,list) else v[part.replace('~1','/').replace('~0','~')]
        assert v is not None
    assert m['candidate_output_is_oracle'] is False
    assert len(m['groups'])>=8 and len({g['id'] for g in m['groups']})==len(m['groups'])
    assert all(g['assertions'] and len(set(g['assertions']))==len(g['assertions']) for g in m['groups'])
    assert sum(len(g['assertions']) for g in m['groups'])>=40
    return e,m

def expected_conveyor():
    e=load('contracts/role-desk/expected-results.json')['conveyor'];source=load('tests/expected-results.json')['expected_evidence_by_path'];result=[]
    for i,p in enumerate(source):
        assert p['path']==e['paths'][i] and p['source_ids']==e['source_ids'] and p['source_titles']==e['source_titles']
        assert p['observed_at']==e['source_observed_at']+[e['roster_occurrences'][i]]
        result.append({'path':p['path'],'sources':[dict(source_id=a,title=b,observed_at=c) for a,b,c in zip(p['source_ids'],p['source_titles'],p['observed_at'])]})
    return result

def assert_conveyor(value,index=None,restored=False):
    p=expected_conveyor();p=p if index is None else [p[index]]
    assert value['ownership_witnesses']==p,'exact ownership occurrence differs'
    assert value['responsibilities']==([] if restored else p),'active responsibility differs'
    assert value['roles']==sorted({x['path'][-1] for x in p}),'role boundary differs'

def free_ports():
    held=[]
    try:
        base=int(os.environ.get('WORKSHOP_PORT_BASE',0));span=int(os.environ.get('WORKSHOP_PORT_SPAN',1000))
        for port in (range(base,base+span) if base else [0,0]):
            s=socket.socket()
            try:s.bind(('127.0.0.1',port))
            except OSError:s.close();continue
            held.append(s)
            if len(held)==2:return tuple(s.getsockname()[1] for s in held)
        raise RuntimeError('No free owned loopback ports')
    finally:
        for s in held:s.close()

class Verify(SourceBranchLive):
    def __init__(self,args,e,m):
        adapted={'groups':[{'group_id':g['id'],'pinned_assertions':[{'id':a} for a in g['assertions']]} for g in m['groups']]}
        super().__init__(args,dict(e,groups=[]),adapted)
        self.coverage={g['id']:set() for g in m['groups']};self.db.graph='wardatlas_test_role_desk_'+self.run_id
    def api(self,context='conveyor',role=None,**clocks):
        q=dict(context=context,**clocks)
        if role:q['role']=role
        status,v=self.get('/api/role-responsibilities?'+urlencode(q));assert status==200,(status,v)
        return v
    def dock(self,index,role=None):
        case=self.expected['dock']['cases'][index]
        return self.api('dock',role,**{k:case[k] for k in ('effective_at','known_at')})
    def checks(self,g,values):
        for k,v in values.items():self.check(g,k,v)
    def browser(self,label,mode='existing'):
        out=self.args.evidence_dir/(label+'.json');shots=self.args.evidence_dir/label
        subprocess.run(['node',str(ROOT/'contracts/role-desk/browser.cjs'),self.base_url,mode,str(out),str(shots)],check=True,timeout=180)
        v=json.loads(out.read_text());assert v['status']=='PASS' and v['assertion_count']>0
        return v
    def delete(self,a,rel,b):
        self.db.graph_query('owned-delete',f"MATCH (a {{id:'{a}'}})-[r:{rel}]->(b {{id:'{b}'}}) DELETE r")
    def run(self):
        self.start();unknown=self.expected['unavailable'];e=self.expected['conveyor'];roles=[p[-1] for p in e['paths']]
        cold=self.api();cold_selected=self.api(role=roles[0]);cold_browser=self.browser('cold','unknown')
        self.checks('cold',{'unknown':cold==unknown,'no-identities':not any(x in json.dumps(cold) for x in ['INC-','ROLE-','SRC-']),'same-unavailable':cold_selected==unknown,'no-workload-claim':cold['workload_complete'] is False,'cold-browser':cold_browser['status']=='PASS'})
        self.reset();baseline=self.api();selected=[self.api(role=r) for r in roles]
        assert_conveyor(baseline)
        for i,v in enumerate(selected):assert_conveyor(v,i)
        self.checks('conveyor',{'exact-roles':baseline['roles']==sorted(roles),'sterile-path':selected[0]['responsibilities']==[expected_conveyor()[0]],'materials-path':selected[1]['responsibilities']==[expected_conveyor()[1]],'exact-cards':baseline['ownership_witnesses']==expected_conveyor(),'separate-occurrences':[p['sources'][-1]['observed_at'] for p in baseline['ownership_witnesses']]==e['roster_occurrences'],'no-sop-owner':all(self.api(role=r)==unknown for r in e['never_owners']),'reverse-query':'r.id = $role' in queries(parse_request('role='+roles[0]),True)['ownership']})
        restored=self.api(role=roles[0],as_of=e['restored_at']);assert_conveyor(restored,0,True)
        # Event truth is checked against the pre-existing source oracle, not API output.
        shift=load('tests/shift-board-expected-results.json')['cases'][3]['expected']
        self.observed['shift-oracle']=shift
        restore_expected=shift['observed_restoration_1930']
        self.checks('restoration',{'roles-retained':restored['roles']==[roles[0]],'active-removed':restored['responsibilities']==[] and restored['state']=='restored-observed','event-evidenced':restored['event']['value']=='available' and restored['event']['id']==restore_expected['restoration_event_id'] and restored['event']['source']['source_id']==restore_expected['restoration_source_id'] and restored['event']['source']['observed_at']==restore_expected['restoration_event_at'] and restored['event']['event_at']==restore_expected['restoration_event_at'],'no-safe-resolution':not restored['safe_asserted'] and not restored['resolution_asserted'],'not-common-shift':restored['clocks']=={'as_of':e['restored_at']} and restored['context']=='conveyor'})
        docks=[self.dock(i) for i in range(3)];cases=self.expected['dock']['cases'];prefix=self.expected['dock']['prefix']
        paths=[{'path':prefix+[c['role_id']],'sources':[{k:c[k] for k in ('source_id','title','observed_at')}]} for c in cases[:2]]
        early=self.api('dock',effective_at=cases[0]['effective_at'],known_at=cases[2]['known_at'])
        self.checks('dock',{'duty-only':docks[0]['roles']==[cases[0]['role_id']],'swing-only':docks[1]['roles']==[cases[1]['role_id']],'crossed-unknown':docks[2]==unknown,'exact-paths':all(docks[i]['ownership_witnesses']==[paths[i]] for i in range(2)),'exact-cards':all(self.dock(i,cases[i]['role_id'])['responsibilities']==[paths[i]] for i in range(2)),'independent-axes':all(docks[i]['clocks']=={k:cases[i][k] for k in ('effective_at','known_at')} for i in range(2)),'early-no-r2':'SRC-DOCK-ROSTER-R2' not in json.dumps(early) and early['roles']==['ROLE-DOCK-DUTY']})
        self.delete('DEPT-CENTRAL-STERILE','OWNED_BY',roles[0]);v=self.api();self.check('mutation','owner-edge',self.api(role=roles[0])==unknown and v['ownership_witnesses']==[expected_conveyor()[1]])
        self.reset();self.delete('SVC-MATERIAL-TRANSFER','EVIDENCED_BY','SRC-SERVICE-CATALOG');self.check('mutation','source-edge',self.api()==unknown)
        self.reset();self.db.graph_query('owned-events-delete',"MATCH (:Incident {id:'INC-204'})-[r:HAS_STATE_EVENT]->() DELETE r");self.check('mutation','event-edge',self.api()==unknown)
        self.reset();event_source=baseline['event']['source']['source_id'];self.db.graph_query('owned-event-time',f"MATCH (s:Evidence {{id:'{event_source}'}}) SET s.observed_at='2026-10-14T21:00:00Z'");self.check('mutation','event-source-time',self.api()==unknown)
        self.reset();self.delete('DEPT-RECEIVING-DOCK','OWNED_BY','ROLE-DOCK-SWING');self.check('mutation','dock-owner-edge',self.dock(1)==unknown)
        self.reset();self.db.graph_query('owned-dock-time',"MATCH (s:Evidence {id:'SRC-DOCK-ROSTER-R2'}) SET s.observed_at='2026-10-14T21:00:00Z'");self.check('mutation','dock-source-time',self.dock(1)==unknown)
        self.reset();self.check('mutation','reseed-restores',self.api()==baseline and self.dock(1)==docks[1])
        clean=[self.api(),*selected,*docks];browser_before=self.browser('clean')
        self.seed('decoy',seed_queries_for([node('Role','ROLE-DECOY-DESK',access='operations'),node('Evidence','SRC-DECOY-DESK',title='Disconnected desk source',observed_at=e['as_of'],access='operations')],[edge('ROLE-DECOY-DESK','EVIDENCED_BY','SRC-DECOY-DESK','DESK-DECOY-EDGE',access='operations')]))
        self.check('denied-decoy','decoy-invariant',self.api()==baseline and self.api(role='ROLE-DECOY-DESK')==unknown)
        self.seed('denied',seed_queries_for([node('Role','ROLE-SECRET-DESK',access='restricted'),node('Evidence','SRC-SECRET-DESK',title='Restricted desk source',observed_at=e['as_of'],access='restricted')],[edge('DEPT-CENTRAL-STERILE','OWNED_BY','ROLE-SECRET-DESK','DESK-SECRET-OWNER',current=True,source_id='SRC-SECRET-DESK',observed_at=e['as_of'],access='operations'),edge('ROLE-SECRET-DESK','EVIDENCED_BY','SRC-SECRET-DESK','DESK-SECRET-SOURCE',access='operations')]))
        self.check('denied-decoy','injection-connected',bool(self.graph_rows('injection',"MATCH (:Department {id:'DEPT-CENTRAL-STERILE'})-[:OWNED_BY]->(:Role {id:'ROLE-SECRET-DESK'}) RETURN 1 AS connected")))
        injected=[self.api(),*[self.api(role=r) for r in roles],*[self.dock(i) for i in range(3)]];browser_after=self.browser('injected')
        self.checks('denied-decoy',{'full-response-invariant':injected==clean,'desktop-dom-aria':browser_before['desktop']==browser_after['desktop'],'mobile-dom-aria':browser_before['mobile']==browser_after['mobile'],'generic-role-boundary':all(self.api(role=r)==unknown for r in ['ROLE-SECRET-DESK','ROLE-DECOY-DESK','ROLE-ARBITRARY',*e['never_owners']])})
        self.db.graph_query('owned-denial',"MATCH (s:Evidence {id:'SRC-OPS-ROSTER'}) SET s.access='restricted'");self.check('denied-decoy','denied-witness',self.api(role=roles[0])==unknown and self.api()==unknown);self.reset()
        calls=[];original=self.service.role_responsibilities;self.service.role_responsibilities=lambda *a:calls.append(a)
        try:statuses=[self.get('/api/role-responsibilities?'+q)[0] for q in self.expected['invalid_queries']]
        finally:self.service.role_responsibilities=original
        bad=self.service._connection;self.service._connection=lambda:(_ for _ in ()).throw(RuntimeError('SECRET-BACKEND-DETAIL'))
        try:offline=self.api(role=roles[0])
        finally:self.service._connection=bad
        self.checks('input',{'all-invalid':all(s==400 for s in statuses),'duplicate-role':statuses[1]==400,'malformed-time':statuses[4]==400,'zero-reads':calls==[],'unavailable-graph':offline==unknown,'no-details':'SECRET' not in json.dumps(offline)})
        self.service.action_file.write_text('[{"owned-role-desk-canary":true}]\n');self.service.watch_file.write_text('{"owned-role-desk-canary":true}\n')
        counts=self.graph_counts('seed-first');self.reset();counts2=self.graph_counts('seed-again');snap=self.graph_snapshot('before-read');state={p.name:p.read_bytes() for p in self.state_dir.iterdir()}
        browser=self.browser('repeat');fresh=WardAtlasService(self.args.db_port,self.db.graph,self.state_dir)
        fresh_result=fresh.role_responsibilities(parse_request('role='+roles[0]))
        def digest(v):return hashlib.sha256(json.dumps(v,sort_keys=True).encode()).hexdigest()
        self.checks('repeat-browser-cleanup',{'repeat-seed':counts==counts2 and self.api()==baseline,'reconnect':fresh_result==selected[0],'graph-hash':digest(snap)==digest(self.graph_snapshot('after-read')),'state-hash':state=={p.name:p.read_bytes() for p in self.state_dir.iterdir()},'keyboard':browser['keyboard_passed'],'desktop-mobile':bool(browser['desktop']) and bool(browser['mobile']),'race-errors-escape':browser['race_errors_escape_passed'],'root-link':'href="/roles"' in self.get('/')[1]})
        self.observed.update(baseline=baseline,selected=selected,dock=docks,restored=restored,browser=browser)

def main():
    p=argparse.ArgumentParser()
    for f in ['all','require-live-falkordb','desktop-mobile','fail-on-zero']:p.add_argument('--'+f,action='store_true',required=True)
    p.add_argument('--db-port',type=int);p.add_argument('--app-port',type=int);p.add_argument('--evidence-dir',type=Path);args=p.parse_args()
    e,m=validate_contract();ports=free_ports() if args.db_port is None or args.app_port is None else (None,None);args.db_port=args.db_port if args.db_port is not None else ports[0];args.app_port=args.app_port if args.app_port is not None else ports[1]
    args.lane=os.environ.get('WORKSHOP_LANE','local');args.evidence_dir=args.evidence_dir or Path(os.environ.get('WORKSHOP_RUN_DIR',str(ROOT/'.local/role-desk-evidence')))/'role-desk';args.evidence_dir.mkdir(parents=True,exist_ok=True)
    v=Verify(args,e,m);error=None
    try:v.run()
    except Exception as exc:
        error=f'{type(exc).__name__}: {exc}';import traceback;traceback.print_exc()
    finally:
        v.stop()
        if not error:
            try:
                v.check('repeat-browser-cleanup','owned-cleanup',all(v.cleanup.values()))
                assert all(v.coverage[g['id']]==set(g['assertions']) for g in m['groups'])
            except Exception as exc:error=str(exc)
        report={'status':'FAIL' if error else 'PASS','error':error,'run_id':v.run_id,'assertions':v.assertions,'mapped_assertion_count':len(v.assertions),'group_count':len(m['groups']),'coverage':{g:sorted(s) for g,s in v.coverage.items()},'cleanup':v.cleanup,'database':v.db.database,'query_count':v.db.query_count,'observed':v.observed}
        (args.evidence_dir/'live.json').write_text(json.dumps(report,indent=2));print(json.dumps({'status':report['status'],'assertions':len(v.assertions),'report':str(args.evidence_dir/'live.json')}))
    return bool(error)
if __name__=='__main__':sys.exit(main())

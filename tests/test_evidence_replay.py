"""Focused replay tests; accepted literal oracles, not candidate-derived facts."""
from copy import deepcopy
from http.client import HTTPConnection
import json
from pathlib import Path
import subprocess
import sys
import threading
import unittest
from unittest.mock import Mock

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
from wardatlas_app import Handler, WardAtlasHTTPServer
from wardatlas_replay import FRAMES, parse_parameters, project_frame, replay_frame
from verify_evidence_replay import assert_frame, is_unknown, load_contracts, oracle_projection

EXPECTED,MATRIX=load_contracts()


def value(frame):
    spec=EXPECTED['frames'][frame];p=oracle_projection(spec)
    if frame==5:
        p['claims'][1]['source']['observed_at']=spec['assumed_observed_at']
        p['claims'][1]['source'].pop('assumed_observed_at')
    scenario={'assumed_observed_at':'2026-10-14T16:06:00Z','actual_source_observed_at':'2026-10-14T16:04:00Z'} if frame>=4 else None
    return project_frame(frame,spec['mode'],p,scenario)


class EvidenceReplayTests(unittest.TestCase):
    def test_contract_hashes_and_nonzero_matrix(self):
        e,m=load_contracts();self.assertEqual(len(e['frames']),6);self.assertGreaterEqual(len(m['groups']),8);self.assertGreaterEqual(sum(len(g['assertions']) for g in m['groups']),40)

    def test_six_literal_typed_frames(self):
        for spec in EXPECTED['frames']:
            with self.subTest(frame=spec['frame']):assert_frame(value(spec['frame']),spec,EXPECTED)

    def test_early_unknown_no_source_or_record_hints(self):
        v=value(0);self.assertTrue(is_unknown(v));self.assertNotIn('count',json.dumps(v));self.assertEqual(v['sources'],[])

    def test_v2_only_frame_no_hindsight(self):
        v=value(1);self.assertEqual(len(v['edges']),3);self.assertNotIn('V3',json.dumps(v));self.assertNotIn('DEC-OPS-',json.dumps(v))

    def test_conflict_before_record_source(self):
        v=value(2);self.assertEqual(next(e['status'] for e in v['edges'] if e['relationship']=='SOP_CONFLICT'),'unresolved');self.assertNotIn('TRACKS',[e['relationship'] for e in v['edges']])

    def test_record_own_evidence_and_both_considered_links(self):
        v=value(3);self.assertEqual(len([e for e in v['edges'] if e['relationship']=='CONSIDERS']),2);self.assertEqual(next(n for n in v['nodes'] if n['kind']=='record')['recorded_state'],'open-unresolved')

    def test_hypothetical_suppresses_dependent_edges_only(self):
        v=value(4);self.assertNotIn('V3',json.dumps(v));self.assertNotIn('SOP_CONFLICT',[e['relationship'] for e in v['edges']]);self.assertEqual([e['to'] for e in v['edges'] if e['relationship']=='CONSIDERS'],['SOP-OPS-9-V2']);self.assertTrue(any(n['kind']=='record' for n in v['nodes']))

    def test_actual_and_assumed_times_remain_separate(self):
        v=value(5);sources={s['id']:s for s in v['sources']};self.assertEqual(sources['SRC-SOP-CURRENT-V3']['observed_at'],'2026-10-14T16:04:00Z');self.assertEqual(sources['SRC-SOP-CURRENT-V3']['assumed_observed_at'],'2026-10-14T16:06:00Z');self.assertNotIn('assumed_observed_at',sources['SRC-SOP-CURRENT-V2']);self.assertFalse(v['source_fact_modified'])

    def test_missing_branch_scenario_has_no_mainline_fallback(self):
        p=oracle_projection(EXPECTED['frames'][3]);self.assertTrue(is_unknown(project_frame(4,'hypothetical',p)));self.assertTrue(is_unknown(project_frame(5,'hypothetical',p)))

    def test_assumption_cannot_rewrite_a_different_source_with_same_clock(self):
        p=oracle_projection(EXPECTED['frames'][4]);p['claims'][0]['source']['observed_at']=FRAMES[5][1]
        v=project_frame(5,'hypothetical',p,{'assumed_observed_at':FRAMES[5][1],'actual_source_observed_at':FRAMES[2][1]})
        v2=next(s for s in v['sources'] if s['id']=='SRC-SOP-CURRENT-V2')
        self.assertEqual(v2['observed_at'],FRAMES[5][1]);self.assertNotIn('assumed_observed_at',v2)

    def test_accepted_service_dispatch_and_no_new_engine(self):
        service=Mock();service.decision_timeline.return_value=oracle_projection(EXPECTED['frames'][1]);v=replay_frame(service,1,'mainline');assert_frame(v,EXPECTED['frames'][1],EXPECTED);service.decision_timeline.assert_called_once_with(FRAMES[1][1]);service.decision_source_branch.assert_not_called()
        service.reset_mock();service.decision_source_branch.return_value={'mainline':{'secret':'must not leak'},'hypothetical_branch':{'projection':oracle_projection(EXPECTED['frames'][4]),'scenario':{'assumed_observed_at':FRAMES[5][1],'actual_source_observed_at':FRAMES[2][1]}}};assert_frame(replay_frame(service,4,'hypothetical'),EXPECTED['frames'][4],EXPECTED);service.decision_source_branch.assert_called_once_with(FRAMES[4][1]);service.decision_timeline.assert_not_called()

    def test_invalid_duplicate_selection_rejects_before_service(self):
        for query in EXPECTED['invalid_queries']:
            with self.subTest(query=query),self.assertRaises(ValueError):parse_parameters(query)
        service=Mock()
        for frame,mode in [(-1,'mainline'),(6,'mainline'),(0,'hypothetical'),(True,'mainline'),('0','mainline')]:
            with self.assertRaises(ValueError):replay_frame(service,frame,mode)
        self.assertEqual(service.mock_calls,[])

    def test_valid_deterministic_selection(self):
        for spec in EXPECTED['frames']:
            self.assertEqual(parse_parameters(f"frame={spec['frame']}&mode={spec['mode']}&scope=operations"),(spec['frame'],spec['mode']))

    def test_no_discovery_label_invented_edges_or_roots(self):
        p=oracle_projection(EXPECTED['frames'][1]);p['claims'][0]['discovery_path']='INC-204 AFFECTS CONVEYOR-C4 SUPPORTS SVC-MATERIAL-TRANSFER REGISTER-SECRET TRACKS DEC-SECRET'
        v=project_frame(1,'mainline',p);assert_frame(v,EXPECTED['frames'][1],EXPECTED);self.assertNotIn('SECRET',json.dumps(v));self.assertNotIn('INC-204',json.dumps(v))

    def test_projection_does_not_mutate_service_facts(self):
        p=oracle_projection(EXPECTED['frames'][3]);before=deepcopy(p);v=project_frame(3,'mainline',p);v['sources'][0]['title']='mutated';self.assertEqual(p,before)

    def test_mutant_draws_allowed_but_unevidenced_edge_is_rejected(self):
        v=value(1);v['edges'].append({'id':'CONVEYOR-C4|TRACKS|SOP-OPS-9-V2','from':'CONVEYOR-C4','relationship':'TRACKS','to':'SOP-OPS-9-V2','epistemic_type':'graph-derived','label':'graph-derived relationship','sources':v['sources']})
        with self.assertRaises(AssertionError):assert_frame(v,EXPECTED['frames'][1],EXPECTED)

    def test_stale_response_race_and_unload_execute_actual_ui_script(self):
        result=subprocess.run(['node',str(ROOT/'scripts/replay_browser_assertions.cjs'),'--unit-race'],text=True,capture_output=True,timeout=20)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr);report=json.loads(result.stdout);self.assertTrue(report['stale_response']);self.assertTrue(report['stale_failure']);self.assertTrue(report['unload_cancels']);self.assertTrue(report['pause_clears_timer'])

    def test_api_rejects_before_queries_and_database_failure_is_unavailable(self):
        service=Mock();server=WardAtlasHTTPServer(('127.0.0.1',0),Handler,service);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        try:
            conn=HTTPConnection('127.0.0.1',server.server_port,timeout=5)
            for query in EXPECTED['invalid_queries']:
                conn.request('GET','/api/replay-frame?'+query);response=conn.getresponse();response.read();self.assertEqual(response.status,400)
            self.assertEqual(service.mock_calls,[]);service.decision_timeline.side_effect=ConnectionError('private database details must not leak')
            conn.request('GET','/api/replay-frame?frame=3&mode=mainline');response=conn.getresponse();body=json.loads(response.read());self.assertEqual(response.status,503);self.assertEqual(body['state'],'unavailable');self.assertNotIn('private',json.dumps(body));self.assertNotIn('SRC-',json.dumps(body));conn.close()
        finally:server.shutdown();server.server_close();thread.join()


if __name__=='__main__':unittest.main()

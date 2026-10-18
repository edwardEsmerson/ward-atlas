"""Literal-oracle and adversarial mutation checks; no candidate-derived oracle."""
import copy, sys, unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from verify_resilience import validate_contract, expected_pane, assert_projection, assert_unknown, load
from wardatlas_resilience import validate_request, build_queries, response, MODES

def literal(mode):
    e=load('contracts/resilience/expected-results.json')
    return {'scope':'operations','mode':mode,'as_of':e['as_of'],'known_at':e['known_at'],'actual':expected_pane('baseline'),'hypothetical':expected_pane(mode),'omission':e[mode].get('omission'),'dependent_evidence_state':'not-selected' if mode=='baseline' else 'unknown','source_facts_unchanged':True,'selected_version':None,'resolution_asserted':False,'safe_asserted':False,'role_disappearance_asserted':False}

class ResilienceTests(unittest.TestCase):
    def test_contract_nonzero(self):validate_contract()
    def test_zero_mappings_rejected(self):
        _,m=validate_contract();m['groups'][0]['assertions']=[]
        with self.assertRaises(AssertionError):validate_contract(m)
    def test_exact_literal_scenarios(self):
        for m in MODES:assert_projection(m,literal(m))
    def test_baseline_as_hypothesis_rejected(self):
        v=literal(MODES[2]);v['hypothetical']=copy.deepcopy(v['actual'])
        with self.assertRaises(AssertionError):assert_projection(MODES[2],v)
    def test_hardcoded_unchanged_paths_rejected(self):
        v=literal(MODES[1]);v['hypothetical']['ownership']=v['actual']['ownership']
        with self.assertRaises(AssertionError):assert_projection(MODES[1],v)
    def test_unknown_as_safe_rejected(self):
        v=response(MODES[1],expected_pane('unsupported'),{},False);v['safe_asserted']=True
        with self.assertRaises(AssertionError):assert_unknown(v)
    def test_unsupported_omission_rejected(self):
        v=response(MODES[2],expected_pane('unsupported'),{},True)
        assert_unknown(v);self.assertIsNone(v['omission'])
    def test_changed_timestamp_rejected(self):
        v=literal(MODES[2]);v['hypothetical']['knowledge']['claims'][0]['source']['observed_at']='2026-10-14T16:06:00Z'
        with self.assertRaises(AssertionError):assert_projection(MODES[2],v)
    def test_duplicate_mode_rejected(self):
        with self.assertRaises(ValueError):validate_request('mode=baseline&mode=baseline')
    def test_invalid_scope_rejected(self):
        for q in ['scope=restricted','scope=operations&scope=operations','scope=']:
            with self.assertRaises(ValueError):validate_request(q)
    def test_arbitrary_parameters_rejected(self):
        for q in load('contracts/resilience/expected-results.json')['invalid_queries']:
            with self.assertRaises(ValueError):validate_request(q)
    def test_bounded_parameterized_read_queries(self):
        for m in MODES:
            q=build_queries(m)
            for s in q.values():
                self.assertTrue(s.startswith('CYPHER '));self.assertIn('LIMIT ',s)
                self.assertNotRegex(s,r'\b(DELETE|CREATE|MERGE|SET)\b')
            self.assertIn('NOT ($omit_owner',q['ownership'])
            for k in ['claims','considered','conflict']:self.assertIn('NOT ($omit_v3',q[k])
    def test_invalid_mode_before_query(self):
        with self.assertRaises(ValueError):build_queries('MATCH (n) DELETE n')
    def test_missing_source_cards_rejected(self):
        v=literal(MODES[1]);v['hypothetical']['sources']=[]
        with self.assertRaises(AssertionError):assert_projection(MODES[1],v)

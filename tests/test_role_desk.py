"""Focused contract and boundary checks, independent literal source expectations."""
import copy
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from wardatlas_role_desk import parse_request, queries, unavailable, read_responsibilities, witnesses
from verify_role_desk import validate_contract, expected_conveyor, assert_conveyor, load


class RoleDeskTests(unittest.TestCase):
    def test_nonzero_contract(self): validate_contract()
    def test_empty_mapping_rejected(self):
        e,m=validate_contract();m['groups'][0]['assertions']=[]
        with self.assertRaises(AssertionError):validate_contract(m)
    def test_literal_paths(self):
        e,_=validate_contract();self.assertEqual([p['path'] for p in expected_conveyor()],e['conveyor']['paths'])
    def test_literal_occurrences(self):
        self.assertEqual([p['sources'][-1]['observed_at'] for p in expected_conveyor()],['2026-10-14T18:04:00Z','2026-10-14T18:05:00Z'])
    def test_all_invalid(self):
        for q in load('contracts/role-desk/expected-results.json')['invalid_queries']:
            with self.subTest(q=q),self.assertRaises(ValueError):parse_request(q)
    def test_unknown_ids_not_syntax_errors(self):
        self.assertEqual(parse_request('role=ROLE-UNKNOWN')['role'],'ROLE-UNKNOWN')
    def test_offsets_normalize(self):
        self.assertEqual(parse_request('as_of=2026-10-14T20:30:00%2B02:00')['clocks']['as_of'],'2026-10-14T18:30:00Z')
    def test_independent_axes(self):
        s=parse_request('context=dock&effective_at=2026-10-14T20:30:00Z&known_at=2026-10-14T20:17:59Z')
        self.assertNotEqual(s['clocks']['effective_at'],s['clocks']['known_at'])
    def test_role_query_is_live_predicate(self):
        q=queries(parse_request('role=ROLE-MATERIALS-DUTY'),True)['ownership']
        self.assertIn('r.id = $role',q);self.assertIn('MATCH ',q);self.assertIn('OWNED_BY',q)
    def test_read_queries_only(self):
        for s in ['','context=dock&effective_at=2026-10-14T20:30:00Z&known_at=2026-10-14T20:19:00Z']:
            for q in queries(parse_request(s)).values():
                self.assertNotRegex(q,r'\b(CREATE|SET|MERGE|DELETE)\b');self.assertIn('LIMIT ',q);self.assertIn("access = 'operations'",q)
    def test_source_and_event_time_guards(self):
        q=queries(parse_request(''))
        self.assertIn('owns.observed_at <=',q['ownership']);self.assertIn('src.observed_at <=',q['event'])
    def test_unavailable_exact(self): self.assertEqual(unavailable(),load('contracts/role-desk/expected-results.json')['unavailable'])
    def test_backend_failure_generic(self):
        import threading
        service=Mock();service.graph_lock=threading.RLock();service._connection.side_effect=RuntimeError('SECRET')
        self.assertEqual(read_responsibilities(service,parse_request('')),unavailable())
    def test_copied_all_role_paths_rejected(self):
        e=expected_conveyor();v={'ownership_witnesses':e,'responsibilities':e,'roles':[p['path'][-1] for p in e]}
        with self.assertRaises(AssertionError):assert_conveyor(v,0)
    def test_changed_roster_occurrence_rejected(self):
        e=expected_conveyor();e[0]['sources'][-1]['observed_at']='2026-10-14T18:05:00Z'
        with self.assertRaises(AssertionError):assert_conveyor({'ownership_witnesses':[e[0]],'responsibilities':[e[0]],'roles':[e[0]['path'][-1]]},0)
    def test_unknown_never_complete(self):
        v=unavailable();self.assertFalse(v['safe_asserted']);self.assertFalse(v['resolution_asserted']);self.assertFalse(v['workload_complete'])

if __name__=='__main__':unittest.main()

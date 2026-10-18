"""Literal accepted-oracle expectations plus hostile package/selection inputs.

These focused tests use explicit service doubles. The separate mandatory live
verifier proves actual FalkorDB execution; doubles are not advertised as a DB.
"""
import base64
import copy
from html.parser import HTMLParser
import io
import json
from pathlib import Path
import sys
import threading
import unittest
import warnings
import zipfile
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from wardatlas_briefcase import capture_evidence, package_evidence, parse_selection, render_report
from verify_evidence_briefcase import (validate_contract, load, source_case, selection,
                                      oracle_projection, assert_semantics, read_archive_bytes)

SPEC = load('contracts/evidence-briefcase/expected-results.json')
CASES = {c['id']: c for c in SPEC['cases']}


class LiteralService:
    """Accepted expected values, not candidate service/query output."""
    def __init__(self, key='mainline-1605'):
        self.graph_lock = threading.RLock()
        self.key = key
        self.calls = []
    def decision_timeline(self, stamp):
        self.calls.append(('timeline', stamp))
        return oracle_projection(CASES[self.key])
    def decision_source_branch(self, stamp):
        self.calls.append(('branch', stamp))
        return {'mainline':oracle_projection(CASES['mainline-1605']),
                'hypothetical_branch':{'projection':oracle_projection(CASES[self.key]),
                                       'scenario':source_case('branch', CASES[self.key]['case'])['branch_assumption']}}
    def sop_impact(self, stamp):
        self.calls.append(('impact', stamp))
        return {'impact_state':'unavailable',
                'path_evidence':load('tests/expected-results.json')['expected_evidence_by_path'],
                'observed_event':{'event_id':'EVT-C4-UNAVAILABLE-01','value':'unavailable','event_at':'2026-10-14T18:02:00Z',
                    'evidence_label':'observed source fact','source_id':'SRC-ASSET-C4-OUTAGE',
                    'source_title':'Synthetic C4 Outage Event Ledger','source_observed_at':'2026-10-14T18:02:00Z'}}


def bundle(key='mainline-1605', service=None):
    return package_evidence(capture_evidence(service or LiteralService(key), selection(CASES[key]), captured_at=SPEC['capture_clock']))


def rewrite_zip(contents, names=None):
    out = io.BytesIO()
    with zipfile.ZipFile(out, 'w', zipfile.ZIP_DEFLATED) as z:
        with warnings.catch_warnings():
            warnings.simplefilter('ignore', UserWarning)
            for name in names or contents:
                z.writestr(name, contents.get(name,b'hostile'))
    return out.getvalue()


class BriefcaseTests(unittest.TestCase):
    def test_frozen_oracles_and_nonzero_matrix(self):
        e,m=validate_contract()
        self.assertGreaterEqual(len(m['groups']),8)
        self.assertGreaterEqual(sum(len(g['assertions']) for g in m['groups']),40)
    def test_empty_mapping_rejected(self):
        e,m=validate_contract();m['groups'][0]['assertions']=[]
        with self.assertRaises(AssertionError):validate_contract(m)
    def test_literal_full_projections(self):
        for key in ('mainline-1604','mainline-1605','hypothetical-1605','hypothetical-1606'):
            assert_semantics(CASES[key],bundle(key))
    def test_same_id_distinct_observations_are_not_deduplicated(self):
        e=bundle('outage-1830')['evidence']
        roster=[s for s in e['source_occurrences'] if s['source_id']=='SRC-OPS-ROSTER']
        self.assertEqual([s['observed_at'] for s in roster],CASES['outage-1830']['roster_occurrences'])
        self.assertEqual(len({s['occurrence'] for s in e['source_occurrences']}),9)
    def test_hypothetical_actual_and_assumed_clocks_separate(self):
        e=bundle('hypothetical-1606')['evidence']
        s=next(s for s in e['source_occurrences'] if s['source_id']=='SRC-SOP-CURRENT-V3')
        self.assertEqual(s['observed_at'],'2026-10-14T16:04:00Z')
        self.assertEqual(s['projected_observed_at'],'2026-10-14T16:06:00Z')
        self.assertEqual(e['source_facts'][1]['claim']['source']['observed_at'],'2026-10-14T16:04:00Z')
    def test_hypothetical_1605_no_future_id_or_record_fallback(self):
        e=bundle('hypothetical-1605')['evidence']
        self.assertNotIn('SOP-OPS-9-V3',json.dumps(e))
        self.assertEqual(e['witnesses'][0]['projection']['decision_record']['recorded_state'],'open-unresolved')
    def test_source_title_escaping(self):
        e=bundle()['evidence'];title='<script>alert(1)</script><img src="https://invalid.test/x" onerror="alert(2)"> & \'quote\''
        e['source_occurrences'][0]['title']=title
        e['source_facts'][0]['claim']['source']['title']=title
        b=package_evidence(e);self.assertNotIn('<script>',b['report_html']);self.assertNotIn('<img ',b['report_html'])
        self.assertIn('&lt;script&gt;',b['report_html'])
        self.assertEqual(read_archive_bytes(base64.b64decode(b['zip_base64']))['report.html'],b['report_html'].encode())
    def test_manifest_hashes_exact_bytes_and_no_authentication_claim(self):
        b=bundle();files=read_archive_bytes(base64.b64decode(b['zip_base64']))
        self.assertEqual(set(files),{'evidence.json','report.html','manifest.json'})
        self.assertEqual(files['report.html'],b['report_html'].encode())
        self.assertIn('not a signature',b['manifest']['integrity_notice'])
    def test_tamper_rejected(self):
        files=read_archive_bytes(base64.b64decode(bundle()['zip_base64']));files['report.html']+=b'changed'
        with self.assertRaises(ValueError):read_archive_bytes(rewrite_zip(files))
    def test_wrong_hash_rejected(self):
        files=read_archive_bytes(base64.b64decode(bundle()['zip_base64']));m=json.loads(files['manifest.json']);m['files']['evidence.json']['sha256']='0'*64;files['manifest.json']=json.dumps(m).encode()
        with self.assertRaises(ValueError):read_archive_bytes(rewrite_zip(files))
    def test_duplicate_zip_entry_rejected(self):
        files=read_archive_bytes(base64.b64decode(bundle()['zip_base64']))
        with self.assertRaises(ValueError):read_archive_bytes(rewrite_zip(files,list(files)+['report.html']))
    def test_zip_traversal_rejected(self):
        for name in ('../report.html','/report.html','a/../../report.html','..\\report.html'):
            files=read_archive_bytes(base64.b64decode(bundle()['zip_base64']));files[name]=files.pop('report.html')
            with self.assertRaises(ValueError):read_archive_bytes(rewrite_zip(files))
    def test_oversize_compressed_entry_rejected(self):
        files=read_archive_bytes(base64.b64decode(bundle()['zip_base64']));files['report.html']=b'x'*(1024*1024+1)
        with self.assertRaises(ValueError):read_archive_bytes(rewrite_zip(files))
    def test_oversize_archive_rejected(self):
        with self.assertRaises(ValueError):read_archive_bytes(b'x'*(4*1024*1024+1))
    def test_unknown_context_and_arbitrary_parameters_generic(self):
        errors=[]
        for query in SPEC['invalid_queries']:
            with self.assertRaises(ValueError) as cm:parse_selection(query)
            errors.append(str(cm.exception))
        self.assertEqual(set(errors),{'Invalid historical evidence selection.'})
    def test_offset_normalization_and_duplicate_axis_rejection(self):
        self.assertEqual(parse_selection('context=sop-mainline&known_at=2026-10-14T21:35:00%2B05:30'),selection(CASES['mainline-1605']))
        with self.assertRaises(ValueError):parse_selection('context=sop-mainline&known_at=2026-10-14T16:05:00Z&known_at=2026-10-14T16:05:00Z')
    def test_direct_call_rejects_before_any_query(self):
        s=LiteralService()
        with self.assertRaises(ValueError):capture_evidence(s,{'context':'invalid','known_at':'bad'})
        self.assertEqual(s.calls,[])
    def test_only_selected_service_called(self):
        for key,method in [('outage-1830','impact'),('mainline-1605','timeline'),('hypothetical-1605','branch')]:
            s=LiteralService(key);bundle(key,s);self.assertEqual(len(s.calls),1);self.assertEqual(s.calls[0][0],method)
    def test_no_action_semantics(self):
        for key in ('outage-1830','mainline-1605','hypothetical-1605'):
            self.assertEqual(bundle(key)['evidence']['invariants'],SPEC['invariants'])
            self.assertIn('Not a handover proposal',bundle(key)['report_html'])
    def test_capture_is_separately_labeled(self):
        b=bundle();self.assertNotEqual(b['evidence']['captured_at'],b['evidence']['selection']['known_at']);self.assertIn('not selected historical time',b['report_html'])
    def test_missing_branch_is_identity_free_no_mainline_fallback(self):
        class Missing(LiteralService):
            def decision_source_branch(self,stamp):
                return {'mainline':oracle_projection(CASES['mainline-1605']), 'hypothetical_branch':{'projection':source_case('branch','cold-empty-branch-lookup')['projection']}}
        e=bundle('hypothetical-1605',Missing())['evidence']
        self.assertEqual(e['witnesses'],[]);self.assertEqual(e['source_occurrences'],[])
        self.assertFalse(any(t in json.dumps(e) for t in ['SRC-','SOP-OPS','DEC-OPS','ROLE-']))
    def test_changed_source_time_is_detected_by_oracle(self):
        b=bundle();b['evidence']['witnesses'][0]['projection']['claims'][1]['source']['observed_at']='2026-10-14T16:06:00Z'
        with self.assertRaises(AssertionError):assert_semantics(CASES['mainline-1605'],b)
    def test_package_repeat_is_byte_identical_for_same_capture(self):
        self.assertEqual(bundle(),bundle())


if __name__=='__main__':unittest.main()

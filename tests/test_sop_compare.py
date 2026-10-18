"""Focused checks use pinned literal rows, never response-derived expectations."""
import copy
from pathlib import Path
import sys
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"scripts"))
from verify_sop_compare import validate_contract, literal_rows, assert_version, load, CONTRACT, KNOWN
from wardatlas_sop_compare import parse_parameters, build_queries, project

class SOPCompareTests(unittest.TestCase):
    def view(self, rows=None, **selection):
        return project(dict(known_at=KNOWN,**selection),rows if rows is not None else literal_rows())
    def test_frozen_hashes_and_exact_pointers(self):validate_contract()
    def test_zero_matrix_rejected(self):
        e,m=validate_contract();m['groups'][0]['assertions']=[]
        with self.assertRaises(AssertionError):validate_contract(m)
    def test_all_pairs_exact(self):
        for a,b in load(CONTRACT+'expected-results.json')['pairs']:
            v=self.view(left=a,right=b);assert_version(v['left']);assert_version(v['right'])
    def test_archive_c3_not_c4(self):
        v=self.view(left='SOP-OPS-9-V1',equipment='CONVEYOR-C4')['left']
        self.assertEqual(v['applicability_edges'][0]['to'],'CONVEYOR-C3');self.assertEqual(v['focus_applicability'],'unknown / not evidenced')
    def test_archive_no_role_fallback(self):self.assertEqual(self.view(left='SOP-OPS-9-V1')['left']['role_claims'],[])
    def test_property_not_edge(self):
        rows=literal_rows();rows['applies']=rows['applies'][1:];v=self.view(rows,left='SOP-OPS-9-V1')['left']
        self.assertEqual(v['sourced_properties']['referenced_equipment_id'],'CONVEYOR-C3');self.assertEqual(v['applicability_edges'],[])
    def test_v3_missing_source_no_winner(self):
        rows=literal_rows();rows['versions']=rows['versions'][:2];v=self.view(rows,left='SOP-OPS-9-V2',right='SOP-OPS-9-V3')
        self.assertIsNone(v['right']);self.assertIsNone(v['selected_winner']);self.assertEqual(v['conflict']['state'],'unknown');self.assertNotIn('SOP-OPS-9-V3',str(v))
    def test_conflict_removal_not_agreement(self):
        rows=literal_rows();rows['conflicts']=[];self.assertEqual(self.view(rows)['conflict'],{'state':'unknown','edges':[]})
    def test_before_boundary_archive_independent(self):
        v=project({'known_at':'2026-10-14T15:59:59Z'},literal_rows())
        self.assertEqual(v['options']['versions'],['SOP-OPS-9-V1']);self.assertEqual(v['state'],'source-known evidence')
    def test_v2_boundary(self):
        v=project({'known_at':'2026-10-14T16:00:00Z'},literal_rows());self.assertEqual(v['options']['versions'],['SOP-OPS-9-V1','SOP-OPS-9-V2'])
    def test_v3_boundary(self):self.assertEqual(self.view()['conflict']['state'],'unresolved')
    def test_hidden_selected_id_not_echoed(self):
        v=project({'known_at':'2026-10-14T15:59:59Z','left':'SOP-OPS-9-V2','right':'SOP-OPS-9-V3'},literal_rows())
        self.assertIsNone(v['left']);self.assertIsNone(v['right']);self.assertNotIn('SOP-OPS-9-V2',str(v))
    def test_malformed_duplicate_inputs(self):
        for q in load(CONTRACT+'expected-results.json')['invalid_queries']:
            with self.subTest(q=q),self.assertRaises(ValueError):parse_parameters(q)
    def test_offsets_normalized(self):self.assertEqual(parse_parameters('known_at=2026-10-14T18:04:00%2B02:00')['known_at'],KNOWN)
    def test_same_pair_allowed_not_agreement(self):self.assertEqual(self.view(left='SOP-OPS-9-V2',right='SOP-OPS-9-V2')['conflict']['state'],'unknown')
    def test_cold_has_no_identities(self):
        v=self.view({k:[] for k in literal_rows()});self.assertEqual(v['versions'],[]);self.assertNotIn('SOP-OPS-9',str(v))
    def test_query_bounded_parameterized_and_read_only(self):
        for q in build_queries(KNOWN).values():
            self.assertTrue(q.startswith('CYPHER '));self.assertIn('LIMIT 65',q);self.assertIn('$cutoff',q);self.assertIn('$access',q);self.assertNotRegex(q,r'\b(CREATE|DELETE|MERGE|SET)\b')
    def test_overflow_rejected_not_truncated_counts(self):
        rows=literal_rows();rows['versions']*=22
        with self.assertRaises(RuntimeError):self.view(rows)
    def test_exact_source_tamper_rejected(self):
        v=self.view()['left'];v['sources'][0]['title']='fabrication'
        with self.assertRaises(AssertionError):assert_version(v)
    def test_ui_uses_text_not_html_and_generation_guard(self):
        html=(Path(__file__).resolve().parents[1]/'scripts/ui/sop-compare.html').read_text()
        self.assertNotIn('innerHTML',html);self.assertIn('textContent',html);self.assertGreaterEqual(html.count('token!==generation'),3)

if __name__=='__main__':unittest.main()

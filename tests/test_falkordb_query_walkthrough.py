"""Static source/oracle/fence checks, including nonzero adversarial mutations."""
from __future__ import annotations

import ast
import copy
import json
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import verify_falkordb_query_walkthrough as check  # noqa: E402


class QueryWalkthroughTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.raw = (ROOT / check.EXPECTED_PATH).read_bytes()
        cls.expected = check.load_expected(cls.raw, check.SOURCE_COMMIT)
        cls.sources = check.load_sources(ROOT, cls.expected)
        cls.queries = check.generated_queries(cls.sources, cls.expected)
        cls.oracles = check.verify_oracles(ROOT, cls.expected)
        cls.doc = (ROOT / check.DOC_PATH).read_bytes().decode("utf-8")

    def validate(self, doc):
        check.validate_document(doc, self.expected, self.queries, self.oracles)

    def test_accepted_appendix_is_exact_and_safe(self):
        self.validate(self.doc)
        self.assertEqual(len(self.queries), 5)
        self.assertEqual(len(self.expected["outcomes"]), 7)
        self.assertEqual(sum(len(q["generated_sha256"]) for q in self.expected["queries"]), 11)

    def test_whole_static_entrypoint_and_readme_link(self):
        report = check.verify(ROOT, ROOT / check.EXPECTED_PATH, check.SOURCE_COMMIT)
        self.assertFalse(report["graph_or_browser_run"])
        self.assertGreaterEqual(len(report["rejected_mutations"]), 10)

    def test_benign_prose_is_allowed_not_a_whole_document_checksum(self):
        self.validate(self.doc + "\nThe appendix can be read alongside the source files.\n")

    def test_changed_manifest_bytes_even_whitespace_fail_closed(self):
        with self.assertRaisesRegex(check.WalkthroughError, "pre-test pin"):
            check.load_expected(self.raw + b"\n", check.SOURCE_COMMIT)

    def test_repinned_candidate_query_does_not_replace_accepted_oracle(self):
        expected = json.loads(self.raw)
        expected["queries"][0]["generated_sha256"]["constant"] = "0" * 64
        with self.assertRaisesRegex(check.WalkthroughError, "pre-test pin"):
            check.load_expected(json.dumps(expected).encode(), check.SOURCE_COMMIT)

    def test_wrong_expected_commit_fails(self):
        with self.assertRaisesRegex(check.WalkthroughError, "source commit"):
            check.load_expected(self.raw, "0" * 40)

    def test_current_source_bytes_must_match_pin(self):
        for path in self.sources:
            with self.subTest(path=path):
                sources = dict(self.sources)
                sources[path] += "\n# unverified change\n"
                with self.assertRaisesRegex(check.WalkthroughError, "query source hash drift"):
                    check.verify_source_bytes(sources, self.expected)

    def test_exact_source_symbol_pin_is_independently_checked(self):
        expected = copy.deepcopy(self.expected)
        expected["queries"][0]["symbol_sha256"] = "0" * 64
        with self.assertRaisesRegex(check.WalkthroughError, "source symbol hash drift"):
            check.verify_source_bytes(self.sources, expected)

    def test_all_generated_query_pins_are_checked_not_only_fence_cutoff(self):
        for qi, query in enumerate(self.expected["queries"]):
            for cutoff in query["generated_sha256"]:
                with self.subTest(query=query["id"], cutoff=cutoff):
                    expected = copy.deepcopy(self.expected)
                    expected["queries"][qi]["generated_sha256"][cutoff] = "0" * 64
                    with self.assertRaisesRegex(check.WalkthroughError, "literal generated query hash drift"):
                        check.generated_queries(self.sources, expected)

    def test_query_rendering_holds_inclusive_actual_and_assumed_cutoffs_apart(self):
        by_id = {q["id"]: q for q in self.expected["queries"]}
        for cutoff in ("2026-10-14T16:04:00Z", "2026-10-14T16:05:00Z", "2026-10-14T16:06:00Z"):
            mainline = check.render_query(self.sources, by_id["timeline-claim"], cutoff)
            branch = check.render_query(self.sources, by_id["branch-v3-claim"], cutoff)
            self.assertIn(f'source.observed_at <= "{cutoff}"', mainline)
            self.assertNotIn("assumed_observed_at", mainline)
            self.assertIn(f'override.assumed_observed_at <= "{cutoff}"', branch)
            self.assertNotIn("source.observed_at <=", branch)
            self.assertIn("branch_source.id = source.id", branch)
        no_cutoff = check.render_query(self.sources, by_id["branch-v3-claim"], None)
        self.assertIn('override.assumed_observed_at <= "0001-01-01T00:00:00Z"', no_cutoff)

    def test_record_root_does_not_discover_claims(self):
        record = self.queries["timeline-record"]
        claim = self.queries["timeline-claim"]
        self.assertIn("DecisionRegister", record)
        self.assertIn("TRACKS", record)
        self.assertIn("EVIDENCED_BY", record)
        self.assertNotIn("CONSIDERS", record)
        self.assertNotIn("SOPVersion", record)
        self.assertNotIn("DecisionRecord", claim)
        self.assertNotIn("DecisionRegister", claim)
        self.assertNotIn("CONSIDERS", claim)

    def test_missing_git_history_fails_closed_not_current_code_fallback(self):
        with patch.object(check.subprocess, "run", side_effect=subprocess.CalledProcessError(128, "git")):
            with self.assertRaisesRegex(check.WalkthroughError, "accepted source commit unavailable"):
                check.load_sources(ROOT, self.expected)

    def test_git_source_drift_rejected(self):
        with patch.object(check.subprocess, "run", return_value=subprocess.CompletedProcess("git", 0, b"different source")):
            with self.assertRaisesRegex(check.WalkthroughError, "working source differs"):
                check.load_sources(ROOT, self.expected)

    def test_renderer_rejects_arbitrary_execution_or_method_calls(self):
        for expression in ("__import__('os').system('false')", "open('README.md').read()", "'x'.replace('x', 'y')", "[x for x in 'abc']"):
            with self.subTest(expression=expression):
                with self.assertRaisesRegex(check.WalkthroughError, "unsupported string-builder AST"):
                    check.string_expression(ast.parse(expression, mode="eval").body, {})

    def test_renderer_rejects_formatted_fstrings_and_unbound_names(self):
        for expression in ("f'{value!r}'", "f'{value:20}'", "unknown_name"):
            with self.subTest(expression=expression):
                with self.assertRaises(check.WalkthroughError):
                    check.string_expression(ast.parse(expression, mode="eval").body, {"value": "x"})

    def test_duplicate_symbol_rejected(self):
        with self.assertRaisesRegex(check.WalkthroughError, "ambiguous pinned source symbol"):
            check.source_symbol("QUERY = 'one'\nQUERY = 'two'\n", "QUERY")

    def test_frozen_oracle_byte_drift_rejected(self):
        expected = copy.deepcopy(self.expected)
        expected["oracle_sha256"]["contracts/decision-source-branch/expected-results.json"] = "0" * 64
        with self.assertRaisesRegex(check.WalkthroughError, "frozen oracle/report hash drift"):
            check.verify_oracles(ROOT, expected)

    def test_each_frozen_outcome_fragment_is_checked(self):
        for i, outcome in enumerate(self.expected["outcomes"]):
            with self.subTest(outcome=outcome["id"]):
                expected = copy.deepcopy(self.expected)
                expected["outcomes"][i]["oracle_value_sha256"] = "0" * 64
                with self.assertRaisesRegex(check.WalkthroughError, "pinned outcome drift"):
                    check.verify_oracles(ROOT, expected)

    def test_no_duplicate_or_extra_cypher_fences(self):
        match = next(check.FENCE.finditer(self.doc))
        for extra in (match[0], "```cypher\nRETURN 1\n```\n"):
            with self.subTest(extra=extra[:60]):
                with self.assertRaisesRegex(check.WalkthroughError, "fence"):
                    self.validate(self.doc + "\n" + extra)

    def test_crlf_is_not_silently_normalized(self):
        with self.assertRaisesRegex(check.WalkthroughError, "literal query fences"):
            self.validate(self.doc.replace("\n", "\r\n"))

    def test_missing_setup_link_fails(self):
        with self.assertRaisesRegex(check.WalkthroughError, "setup/runbook link"):
            self.validate(self.doc.replace("(demo-runbook.md)", "(missing.md)"))

    def test_outage_source_provenance_cannot_drift(self):
        for before, after in (("Synthetic Operations Role Roster", "Made-up roster"), ("2026-10-14T18:04:00Z", "2026-10-14T18:06:00Z"), ("DEPT-CENTRAL-STERILE → ROLE-STERILE-OPS-COORD", "DEPT-CENTRAL-STERILE → ROLE-FACILITIES-DUTY")):
            with self.subTest(before=before):
                self.assertIn(before, self.doc)
                with self.assertRaisesRegex(check.WalkthroughError, "outage"):
                    self.validate(self.doc.replace(before, after))

    def test_mutation_inventory_is_nonzero_and_every_probe_rejects_for_its_reason(self):
        mutations = check.document_mutations(self.doc, self.expected)
        self.assertGreaterEqual(len(mutations), 10)
        self.assertEqual(len({name for name, _, _ in mutations}), len(mutations))
        rejected = check.verify_mutations(self.doc, self.expected, self.queries, self.oracles)
        self.assertEqual(rejected, [name for name, _, _ in mutations])

    def test_all_required_statements_are_enforced_individually(self):
        for text in self.expected["required_statements"]:
            with self.subTest(statement=text):
                with self.assertRaisesRegex(check.WalkthroughError, "required semantic statement"):
                    self.validate(self.doc.replace(text, "", 1))


# Give each probe a named unittest in addition to the command-line mutation run.
# A no-op replacement or wrong failure reason is itself a test failure.
def _mutation_test(name):
    def test(self):
        mutations = {n: (doc, reason) for n, doc, reason in check.document_mutations(self.doc, self.expected)}
        doc, reason = mutations[name]
        self.assertNotEqual(doc, self.doc)
        with self.assertRaisesRegex(check.WalkthroughError, reason):
            self.validate(doc)
    return test


for _name in (
    "dependency-path", "ownership-direction", "missing-node-access", "missing-edge-access",
    "missing-observed-cutoff", "exclusive-boundary", "missing-for-source",
    "branch-mainline-clock-conflation", "wrong-branch-cutoff", "missing-source-join",
    "false-current-ownership", "query-whitespace", "future-record-leak", "branch-1605-v3-leak",
    "hypothetical-as-actual", "missing-edge-fallback", "missing-eligibility-risk",
    "missing-ai-disclosure", "missing-human-confirmation", "wrong-report-hash",
    "invented-runtime-model", "invented-publication", "invented-video", "invented-compliance",
    "invented-clinical-validation", "invented-clinical-action", "false-sop-authority",
    "false-owner-authority", "unknown-as-absence", "branch-rewrites-mainline",
    "restricted-metadata-leak", "false-eligibility-certification", "private-path", "private-media",
):
    setattr(QueryWalkthroughTests, "test_mutation_" + _name.replace("-", "_"), _mutation_test(_name))


if __name__ == "__main__":
    unittest.main()

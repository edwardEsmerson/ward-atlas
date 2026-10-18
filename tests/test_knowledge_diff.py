"""Focused behavior/mutation tests using byte-pinned, pre-implementation facts."""

from __future__ import annotations

from copy import deepcopy
import hashlib
from http.client import HTTPConnection
import json
from pathlib import Path
import sys
import threading
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from wardatlas_knowledge_diff import (  # noqa: E402
    KINDS, MAX_WITNESSES_PER_SIDE, compare_snapshots, knowledge_diff,
    normalize_selections, parse_parameters, public_witnesses, snapshot_from_projection,
)
from wardatlas_source_branch import unknown_branch_projection  # noqa: E402
from wardatlas_app import Handler, WardAtlasHTTPServer  # noqa: E402


def load(path):
    return json.loads((ROOT / path).read_text())


SOURCE = {row["id"]: row["expected"] for row in load("contracts/decision-source-branch/expected-results.json")["groups"]}
EXPECTED = load("contracts/knowledge-diff/expected-results.json")
GROUPS = {row["id"]: row for row in EXPECTED["groups"]}
AT05 = "2026-10-14T16:05:00Z"
AT06 = "2026-10-14T16:06:00Z"


def main(at=AT05):
    return deepcopy(SOURCE["mainline-isolation"]["mainline_at_16_" + at[14:16]])


def snapshot(mode="mainline", at=AT05):
    if mode == "mainline":
        return snapshot_from_projection(at, mode, main(at))
    group = SOURCE["branch-at-16-05-suppression" if at == AT05 else "branch-at-16-06-restoration"]
    return snapshot_from_projection(at, mode, group["projection"], scenario=group["branch_assumption"], mainline=main(at))


class KnowledgeDiffTests(unittest.TestCase):
    def test_contract_sources_are_exact_and_matrix_is_nonzero(self):
        for source in EXPECTED["pinned_sources"]:
            self.assertEqual(hashlib.sha256((ROOT / source["path"]).read_bytes()).hexdigest(), source["sha256"])
        matrix = load("contracts/knowledge-diff/adversarial-matrix.json")
        self.assertFalse(EXPECTED["candidate_output_is_oracle"])
        self.assertFalse(matrix["candidate_output_is_oracle"])
        self.assertEqual([g["id"] for g in EXPECTED["groups"]], [g["group_id"] for g in matrix["groups"]])
        self.assertGreaterEqual(len(matrix["groups"]), 8)
        assertions = [item for g in matrix["groups"] for item in g["assertions"]]
        self.assertGreaterEqual(len(assertions), 40)
        self.assertEqual(len(assertions), len(set(assertions)))
        self.assertTrue(all(g["assertions"] for g in matrix["groups"]))
        # References are source-oracle case names, not invented acceptance data.
        references = {
            "decision-source-branch": {g["id"] for g in load("contracts/decision-source-branch/expected-results.json")["groups"]},
            "decision-knowledge-timeline": {g["id"] for g in load("contracts/decision-knowledge-timeline/expected-results.json")["cases"]},
        }
        for group in EXPECTED["groups"]:
            for ref in group["source_refs"]:
                source, case = ref.split("#")
                self.assertIn(case, references[source])

    def test_equal_input_zero_substantive_delta_separates_five_types(self):
        value = compare_snapshots(snapshot(), snapshot())
        self.assertEqual(value["delta"]["substantive_delta_count"], 0)
        self.assertEqual(value["delta"]["unchanged_count"], 9)
        self.assertEqual({item["kind"] for item in value["delta"]["items"]}, set(KINDS))
        self.assertFalse(value["delta"]["complete"])

    def test_mainline_boundary_only_separately_sourced_record_links_and_observation(self):
        group = GROUPS["mainline-boundary"]
        value = compare_snapshots(snapshot(at=group["input"]["left_known_at"]), snapshot())
        delta = value["delta"]
        self.assertEqual(delta["substantive_delta_count"], 4)
        self.assertEqual(delta["unchanged_count"], 5)
        self.assertEqual(sorted(item["key"] for item in delta["items"] if item["status"] == "projected-only-on-right"), sorted(group["expected"]["only_right"]))
        self.assertIsNone(value["left"]["projection"]["decision_record"])
        self.assertEqual(value["right"]["projection"]["decision_record"]["recorded_state"], "open-unresolved")

    def test_mode_difference_is_projection_not_resolution(self):
        value = compare_snapshots(snapshot(), snapshot("hypothetical"))
        expected = GROUPS["modes-at-1605"]["expected"]
        self.assertEqual(sorted(item["key"] for item in value["delta"]["items"] if item["status"] == "projected-only-on-left"), sorted(expected["only_left"]))
        self.assertEqual(value["delta"]["state"], "partial-witness-comparison")
        self.assertTrue(value["delta"]["no_negative_conclusion"])
        self.assertEqual(value["right"]["availability"], "partial")

    def test_mode_reversal_flips_only_direction_preserves_witnesses(self):
        forward = compare_snapshots(snapshot(), snapshot("hypothetical"))
        reverse = compare_snapshots(snapshot("hypothetical"), snapshot())
        for left, right in zip(forward["delta"]["items"], reverse["delta"]["items"]):
            self.assertEqual(left["key"], right["key"])
            self.assertEqual(left["left"], right["right"])
            self.assertEqual(left["right"], right["left"])
            self.assertEqual(right["status"], "projected-only-on-right" if left["status"] == "projected-only-on-left" else "unchanged")

    def test_hypothetical_later_cutoff_preserves_record(self):
        value = compare_snapshots(snapshot("hypothetical"), snapshot("hypothetical", AT06))
        self.assertEqual(value["delta"]["substantive_delta_count"], 4)
        self.assertEqual(value["delta"]["unchanged_count"], 5)
        expected = GROUPS["branch-boundary"]["expected"]
        self.assertEqual(sorted(item["key"] for item in value["delta"]["items"] if item["status"] == "projected-only-on-right"), sorted(expected["only_right"]))
        self.assertEqual(value["left"]["projection"]["decision_record"], value["right"]["projection"]["decision_record"])

    def test_actual_and_assumed_time_only_change_observation_not_claim_or_v2(self):
        before = deepcopy(SOURCE)
        value = compare_snapshots(snapshot(at=AT06), snapshot("hypothetical", AT06))
        self.assertEqual([item["key"] for item in value["delta"]["items"] if item["status"] == "changed"], GROUPS["actual-assumed"]["expected"]["changed"])
        sources = [claim["source"] for claim in value["right"]["projection"]["claims"]]
        self.assertEqual(sources[1]["observed_at"], "2026-10-14T16:04:00Z")
        self.assertEqual(sources[1]["assumed_observed_at"], AT06)
        self.assertEqual(sources[0]["observed_at"], "2026-10-14T16:00:00Z")
        self.assertNotIn("assumed_observed_at", sources[0])
        self.assertEqual(SOURCE, before)

    def test_unknown_is_incomparable_never_mass_removal(self):
        unknown = snapshot_from_projection(AT05, "mainline", unknown_branch_projection(AT05))
        value = compare_snapshots(snapshot(), unknown)
        self.assertIsNone(value["delta"]["substantive_delta_count"])
        self.assertEqual(value["delta"]["state"], "comparison-unavailable")
        self.assertEqual({item["status"] for item in value["delta"]["items"]}, {"incomparable"})
        self.assertEqual(compare_snapshots(unknown, unknown)["delta"]["items"], [])

    def test_missing_branch_witness_does_not_fallback_even_with_full_projection(self):
        missing = snapshot_from_projection(AT05, "hypothetical", main(), mainline=main())
        self.assertEqual(missing["availability"], "comparison-unavailable")
        self.assertTrue(missing["projection"]["generic_unknown"])
        self.assertEqual(missing["projection"]["claims"], [])
        self.assertNotIn("scenario", missing)
        self.assertNotIn("SOP-OPS-9", json.dumps(missing))

    def test_property_only_evidence_cannot_become_a_witness(self):
        projection = main()
        del projection["claims"][1]["source"]["evidence_link"]
        value = snapshot_from_projection(AT05, "mainline", projection)
        self.assertEqual(value["availability"], "comparison-unavailable")
        self.assertEqual(public_witnesses(value), {})

    def test_conflict_and_considered_links_require_returned_edges(self):
        projection = main()
        projection["conflict"] = None
        projection["decision_considers_edges"].pop()
        witnesses = public_witnesses(snapshot_from_projection(AT05, "mainline", projection))
        self.assertFalse(any(key.startswith("conflict_relationship:") for key in witnesses))
        self.assertNotIn("considered_link:DEC-OPS-9-REVIEW-01:CONSIDERS:SOP-OPS-9-V3", witnesses)
        self.assertIn("claim:SOP-OPS-9-V3:ROLE-MATERIALS-LEAD", witnesses)

    def test_utc_normalization_is_independent_per_side(self):
        self.assertEqual(normalize_selections("2026-10-14T21:35:00+05:30", "2026-10-14T12:06:00-04:00", "mainline", "hypothetical"), (AT05, AT06, "mainline", "hypothetical"))

    def test_invalid_offset_components_reject_on_either_side(self):
        for malformed in ("2026-10-14T16:05:00+00:60", "2026-10-14T16:05:00-24:00", "0001-01-01T00:00:00+01:00"):
            for side in (0, 1):
                args = [AT05, AT06, "mainline", "hypothetical"]
                args[side] = malformed
                with self.subTest(value=malformed, side=side), self.assertRaises(ValueError):
                    normalize_selections(*args)

    def test_record_source_time_is_compared_once_as_observation(self):
        left = snapshot()
        right = deepcopy(left)
        right["projection"]["decision_record"]["source"]["observed_at"] = AT06
        value = compare_snapshots(left, right)
        self.assertEqual([(item["kind"], item["status"]) for item in value["delta"]["items"] if item["status"] != "unchanged"], [("source_observation", "changed")])

    def test_invalid_duplicate_naive_missing_modes_and_times_reject(self):
        valid = {key: [value] for key, value in GROUPS["identical"]["input"].items()}
        for key in valid:
            for mutation in ([], [""], valid[key] * 2, ["wrong"]):
                with self.subTest(key=key, mutation=mutation), self.assertRaises(ValueError):
                    parse_parameters({**valid, key: mutation})
        for key in ("left_known_at", "right_known_at"):
            with self.subTest(key=key), self.assertRaises(ValueError):
                parse_parameters({**valid, key: ["2026-10-14T16:05:00"]})
        with self.assertRaises(ValueError):
            parse_parameters({**valid, "source_id": ["anything"]})

    def test_validation_precedes_reads_and_composition_holds_reentrant_lock(self):
        class Service:
            graph_lock = threading.RLock()
            calls = []

            def decision_timeline(self, known_at):
                self.assert_owned()
                self.calls.append(known_at)
                with self.graph_lock:
                    return main(known_at)

            def assert_owned(self):
                if not self.graph_lock._is_owned():
                    raise AssertionError("comparison must span reads under application lock")

        service = Service()
        with self.assertRaises(ValueError):
            knowledge_diff(service, AT05, "naive", "mainline", "mainline")
        self.assertEqual(service.calls, [])
        value = knowledge_diff(service, AT05, AT05, "mainline", "mainline")
        self.assertEqual(value["delta"]["substantive_delta_count"], 0)
        self.assertEqual(service.calls, [AT05])

    def test_bounded_witnesses_fail_instead_of_silent_truncation(self):
        projection = main()
        original = projection["claims"][0]
        projection["claims"] = []
        for index in range(MAX_WITNESSES_PER_SIDE + 1):
            claim = deepcopy(original)
            version, source = f"SOP-{index}", f"SRC-{index}"
            claim["version_id"] = version
            claim["claim_edge"]["from"] = version
            claim["applicability_edge"]["from"] = version
            claim["source"]["id"] = source
            claim["source"]["evidence_link"] = {"from": version, "relationship": "EVIDENCED_BY", "to": source}
            projection["claims"].append(claim)
        with self.assertRaisesRegex(ValueError, "limit"):
            public_witnesses(snapshot_from_projection(AT05, "mainline", projection))

    def test_mixed_modes_dispatch_distinct_normalized_cutoffs_inside_lock(self):
        class Service:
            def __init__(self):
                self.graph_lock = threading.RLock()
                self.calls = []

            def decision_timeline(self, known_at):
                self.assert_owned()
                self.calls.append(("mainline", known_at))
                return main(known_at)

            def decision_source_branch(self, known_at):
                self.assert_owned()
                self.calls.append(("hypothetical", known_at))
                with self.graph_lock:
                    group = SOURCE["branch-at-16-06-restoration"]
                    return {"mainline": main(known_at), "hypothetical_branch": {"projection": group["projection"], "scenario": group["branch_assumption"]}}

            def assert_owned(self):
                if not self.graph_lock._is_owned():
                    raise AssertionError("lock must span both reads")

        service = Service()
        value = knowledge_diff(service, "2026-10-14T21:35:00+05:30", AT06, "mainline", "hypothetical")
        self.assertEqual(service.calls, [("mainline", AT05), ("hypothetical", AT06)])
        self.assertEqual((value["left"]["known_at"], value["right"]["known_at"]), (AT05, AT06))

    def test_http_mount_rejects_invalid_inputs_and_scope_before_read(self):
        class Service:
            graph_lock = threading.RLock()

            def decision_timeline(self, known_at):
                raise AssertionError("must not read graph for invalid request")

            decision_source_branch = decision_timeline

        class QuietHandler(Handler):
            def log_message(self, *_args):
                pass

        server = WardAtlasHTTPServer(("127.0.0.1", 0), QuietHandler, Service())
        thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01})
        thread.start()
        try:
            for query, expected in (("", 400), ("scope=restricted", 403), ("scope=operations&scope=operations", 403), ("left_mode=mainline&left_mode=hypothetical", 400)):
                connection = HTTPConnection("127.0.0.1", server.server_address[1], timeout=3)
                try:
                    connection.request("GET", "/api/knowledge-diff?" + query)
                    response = connection.getresponse()
                    self.assertEqual(response.status, expected)
                    self.assertEqual(set(json.loads(response.read())), {"error"})
                finally:
                    connection.close()
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=3)
        self.assertFalse(thread.is_alive())

    def test_diff_returns_detached_copies(self):
        left, right = snapshot(), snapshot("hypothetical")
        before = deepcopy((left, right))
        value = compare_snapshots(left, right)
        value["left"]["projection"]["claims"].clear()
        value["delta"]["items"][0]["left"]["source"]["title"] = "changed by consumer"
        self.assertEqual((left, right), before)


if __name__ == "__main__":
    unittest.main()

#!/usr/bin/env python3
"""Product-only frozen-oracle verification; owns live DB/app/browser resources.

read_archive_bytes is a test/verification reader, NOT a product upload/importer.
It accepts bounded bytes, never extracts archive paths or executes HTML.
"""
from __future__ import annotations
import argparse
import base64
import copy
import hashlib
from html.parser import HTMLParser
import io
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
from urllib.parse import urlencode
import zipfile

from verify_decision_source_branch_live import SourceBranchLive
from wardatlas_app import WardAtlasService
from wardatlas_fixtures import node, edge, seed_queries_for
from wardatlas_source_branch import disconnected_decoy_branch_queries, restricted_source_branch_queries
import wardatlas_briefcase as product

ROOT = Path(__file__).resolve().parents[1]
EXPECTED = "contracts/evidence-briefcase/expected-results.json"
MATRIX = "contracts/evidence-briefcase/adversarial-matrix.json"
PINNED = {
    "tests/expected-results.json": "c77e00e47c4f1e26d565c3d9583653838d5cc88695c4e6aa19ac583205d92b87",
    "contracts/decision-knowledge-timeline/expected-results.json": "8a9ca76ecf7b194ddf26b2aa9bf3d04d0d4eab966a4ee65cacdbccdf76f79076",
    "contracts/decision-source-branch/expected-results.json": "e209aeab5af266abd6940619727295ea50c0ad2707f47c2d4df37d24cb37da23",
}

def load(path):
    return json.loads((ROOT / path).read_text())


def free_ports():
    held=[]
    try:
        base=int(os.environ.get("WORKSHOP_PORT_BASE",0));span=int(os.environ.get("WORKSHOP_PORT_SPAN",1000))
        for port in (range(base+130,base+span) if base else [0,0]):
            s=socket.socket()
            try:s.bind(("127.0.0.1",port))
            except OSError:s.close();continue
            held.append(s)
            if len(held)==2:return tuple(s.getsockname()[1] for s in held)
        raise RuntimeError("No free allocated ports")
    finally:
        for s in held:s.close()


def validate_contract(matrix=None):
    expected = load(EXPECTED)
    matrix = load(MATRIX) if matrix is None else matrix
    assert expected["sources"] == PINNED and expected["candidate_output_is_oracle"] is False
    for path, digest in PINNED.items():
        assert hashlib.sha256((ROOT / path).read_bytes()).hexdigest() == digest, "accepted oracle changed"
    assert matrix["candidate_output_is_oracle"] is False
    assert len(matrix["groups"]) >= 8
    assert len({g["id"] for g in matrix["groups"]}) == len(matrix["groups"])
    assert all(g["assertions"] and len(set(g["assertions"])) == len(g["assertions"]) for g in matrix["groups"])
    assert sum(len(g["assertions"]) for g in matrix["groups"]) >= 40
    for case in expected["cases"]:
        if "oracle" in case:
            assert oracle_projection(case), "missing oracle pointer"
    return expected, matrix


def source_case(oracle, case):
    path = "contracts/decision-" + ("knowledge-timeline" if oracle == "timeline" else "source-branch") + "/expected-results.json"
    data = load(path)
    return copy.deepcopy(next(c["expected"] for c in data.get("cases", data.get("groups", [])) if c["id"] == case))


def oracle_projection(case):
    return source_case(case["oracle"], case["case"])["projection"]


class PassiveHTML(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.tags = []
        self.text = []
    def handle_starttag(self, tag, attrs):
        self.tags.append(tag)
        assert tag not in {"script", "iframe", "object", "embed", "link", "img", "video", "audio", "source", "form", "input", "button", "a", "base", "svg", "math"}, "active HTML"
        assert not any(k.lower().startswith("on") or k.lower() in {"src", "href", "srcdoc", "action", "formaction", "background"} for k, v in attrs), "active HTML attribute"
        if tag == "meta":
            assert not any(k == "http-equiv" and v.lower() == "refresh" for k, v in attrs), "HTML refresh"
    def handle_data(self, text):
        self.text.append(text)


def read_archive_bytes(data):
    """Strict non-extracting verification reader, with ZIP-bomb and path guards."""
    if not isinstance(data, bytes) or len(data) > 4 * 1024 * 1024:
        raise ValueError("archive too large")
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            entries = archive.infolist()
            names = [e.filename for e in entries]
            if len(entries) != 3 or set(names) != {"evidence.json", "report.html", "manifest.json"} or len(set(names)) != 3:
                raise ValueError("unsafe or duplicate ZIP entries")
            if any(e.file_size > 1024 * 1024 or e.flag_bits & 1 or (e.external_attr >> 16) & 0o170000 == 0o120000 for e in entries) or sum(e.file_size for e in entries) > 3 * 1024 * 1024:
                raise ValueError("oversize or unsafe ZIP entry")
            contents = {e.filename: archive.read(e) for e in entries}
    except (zipfile.BadZipFile, RuntimeError, NotImplementedError) as exc:
        raise ValueError("invalid ZIP") from exc
    try:
        evidence = json.loads(contents["evidence.json"])
        manifest = json.loads(contents["manifest.json"])
        if set(manifest["files"]) != {"evidence.json", "report.html"}:
            raise ValueError("invalid manifest members")
        for name in ("evidence.json", "report.html"):
            if manifest["files"][name] != {"sha256": hashlib.sha256(contents[name]).hexdigest(), "bytes": len(contents[name])}:
                raise ValueError("hash/length mismatch")
        if manifest["selection"] != evidence["selection"] or manifest["captured_at"] != evidence["captured_at"]:
            raise ValueError("selection mismatch")
        if manifest["integrity_notice"] != "Accidental-change detection only; not a signature, authenticity guarantee or proof of correctness.":
            raise ValueError("misleading integrity claim")
        report = contents["report.html"].decode("utf-8")
        parser = PassiveHTML(); parser.feed(report)
        if "url(" in report.lower() or "@import" in report.lower():
            raise ValueError("external CSS")
        for needle in ("/workspace/", "/home/", "file://", "localhost:", "127.0.0.1:"):
            if needle in report:
                raise ValueError("private host/path in report")
    except (KeyError, TypeError, UnicodeError, json.JSONDecodeError, AssertionError) as exc:
        raise ValueError("invalid evidence package") from exc
    return contents


def selection(case):
    return {k: case[k] for k in ("context", "as_of", "known_at") if k in case} | {"scope": "operations"}


def expected_citations(projection):
    result = []
    for i, c in enumerate(projection["claims"]):
        s = c["source"]
        item = {"occurrence": f"claim/{i}", "source_id": s["id"], "title": s["title"], "observed_at": s["observed_at"]}
        if s["id"] == "SRC-SOP-CURRENT-V3" and s["observed_at"] == "2026-10-14T16:06:00Z":
            item.update(observed_at="2026-10-14T16:04:00Z", projected_observed_at="2026-10-14T16:06:00Z", time_kind="hypothetical assumption, not source truth")
        result.append(item)
    if projection["decision_record"]:
        s = projection["decision_record"]["source"]
        result.append({"occurrence": "decision-record", "source_id": s["id"], "title": s["title"], "observed_at": s["observed_at"]})
    return result


def assert_semantics(case, bundle):
    evidence = bundle["evidence"]
    assert evidence["selection"] == selection(case), "wrong selected axes"
    assert evidence["invariants"] == load(EXPECTED)["invariants"], "operational side effect/conclusion"
    assert evidence["artifact_type"] == "historical-evidence"
    assert evidence["captured_at"] == load(EXPECTED)["capture_clock"]
    assert "not current truth" in evidence["warning"]
    if "oracle" in case:
        projection = oracle_projection(case)
        assert evidence["witnesses"][0]["type"] == "sop-source-time"
        assert evidence["witnesses"][0]["projection"] == projection, "projection differs from pinned oracle"
        assert evidence["source_occurrences"] == expected_citations(projection), "citations differ"
        expected_facts = []
        for claim in projection["claims"]:
            claim = copy.deepcopy(claim)
            if claim["source"]["id"] == "SRC-SOP-CURRENT-V3":
                claim["source"]["observed_at"] = "2026-10-14T16:04:00Z"
            expected_facts.append({"type": "unverified-sop-claim", "claim": claim})
        if projection["decision_record"]:
            expected_facts.append({"type": "independently-evidenced-record", "record": projection["decision_record"]})
        assert evidence["source_facts"] == expected_facts, "source facts changed"
    return evidence


class Verify(SourceBranchLive):
    def __init__(self, args, expected, matrix):
        adapted = {"groups": [{"group_id": g["id"], "pinned_assertions": [{"id": i} for i in g["assertions"]]} for g in matrix["groups"]]}
        super().__init__(args, dict(expected, groups=[]), adapted)
        self.coverage = {g["id"]: set() for g in matrix["groups"]}
        self.db.graph = "wardatlas_test_briefcase_" + self.run_id
        self.state_dir = ROOT / ".local/briefcase-runs" / self.run_id
        self.cases = {c["id"]: c for c in expected["cases"]}
    def checks(self, group, **checks):
        for key, value in checks.items():
            self.check(group, key.replace("_", "-"), value)
    def api(self, case):
        case = self.cases[case] if isinstance(case, str) else case
        status, value = self.get("/api/evidence-briefcase?" + urlencode(selection(case)))
        assert status == 200, (status, value)
        return value
    def browser(self, label, mode="seeded"):
        report = self.args.evidence_dir / (label + ".json")
        subprocess.run(["node", str(ROOT / "scripts/briefcase_browser_assertions.cjs"), self.base_url, mode, str(report), str(self.args.evidence_dir / label)], check=True, timeout=240)
        value = json.loads(report.read_text())
        assert value["status"] == "PASS" and value["assertion_count"] > 0
        for artifact in value["downloads"]:
            contents = read_archive_bytes(Path(artifact["zip"]).read_bytes())
            assert contents["report.html"] == Path(artifact["html"]).read_bytes()
        return value
    def delete(self, a, rel, b):
        self.db.graph_query("owned-edge-removal", f"MATCH (a {{id:'{a}'}})-[r:{rel}]->(b {{id:'{b}'}}) DELETE r")
    def run(self):
        self.start()
        cold = [self.api(c) for c in self.expected["cases"]]
        cold_browser = self.browser("cold", "cold")
        self.checks("cold-unknown", all_contexts_unknown=all(not b["evidence"]["witnesses"] for b in cold),
                    no_identities=not any(t in json.dumps(cold) for t in ("SRC-", "SOP-OPS-", "INC-204", "ROLE-", "DEC-OPS-", "REGISTER-")),
                    no_sources=all(b["evidence"]["source_occurrences"] == [] for b in cold),
                    no_fallback=all(b["evidence"]["source_facts"] == [] and b["evidence"]["inferences"] == [] for b in cold),
                    no_safety_resolution=all(b["evidence"]["invariants"] == self.expected["invariants"] for b in cold), offline_unknown=cold_browser["offline_count"] >= 2)
        self.reset()
        baseline = {key: self.api(key) for key in self.cases}
        for key, bundle in baseline.items():
            assert_semantics(self.cases[key], bundle)
            contents = read_archive_bytes(base64.b64decode(bundle["zip_base64"]))
            assert json.loads(contents["evidence.json"]) == bundle["evidence"]
            assert contents["report.html"] == bundle["report_html"].encode()
            assert json.loads(contents["manifest.json"]) == bundle["manifest"]
        e = baseline["outage-1830"]["evidence"]; paths = e["witnesses"][0]["path_evidence"]
        expected_paths = load("tests/expected-results.json")["expected_evidence_by_path"]
        expected_occurrences = [{"occurrence": f"path/{i}/source/{j}", "source_id": p["source_ids"][j], "title": p["source_titles"][j], "observed_at": p["observed_at"][j]} for i,p in enumerate(expected_paths) for j in range(4)]
        self.checks("outage-1830", exact_paths=paths == expected_paths, five_nodes=len(paths) == 2 and all(len(p["path"]) == 5 for p in paths),
                    every_source_occurrence=e["source_occurrences"][1:] == expected_occurrences,
                    distinct_roster_times=[s["observed_at"] for s in e["source_occurrences"] if s["source_id"] == "SRC-OPS-ROSTER"] == self.cases["outage-1830"]["roster_occurrences"],
                    typed_witness=e["witnesses"][0]["type"] == "outage-snapshot" and all(i["type"] == "ownership-path" for i in e["inferences"]), selected_axis=e["selection"] == selection(self.cases["outage-1830"]))
        e = baseline["outage-1930"]["evidence"]
        self.checks("outage-1930", no_active_paths=e["witnesses"][0]["path_evidence"] == [], observed_restoration=e["witnesses"][0]["impact_state"] == "restored" and e["source_facts"][0]["event"]["value"] == "available", no_safety=e["invariants"]["safe_asserted"] is False, no_resolution=e["invariants"]["resolution_asserted"] is False, separate_context=not any(t in json.dumps(e) for t in ["SOP-OPS-9", "DEC-OPS-9", "INC-207"]))
        for key in ("mainline-1604", "mainline-1605", "hypothetical-1605", "hypothetical-1606"):
            e = baseline[key]["evidence"]; p = e["witnesses"][0]["projection"]
            self.check(key, "whole-projection", p == oracle_projection(self.cases[key]))
            if key == "mainline-1604":
                self.checks(key, two_claims=len(p["claims"]) == 2, graph_conflict=p["conflict"]["epistemic_type"] == "graph-derived relationship" and p["conflict"]["source_quote"] is False, no_record=p["decision_record"] is None and p["decision_considers_edges"] == [], exact_citations=e["source_occurrences"] == expected_citations(p))
            elif key == "mainline-1605":
                self.checks(key, independent_record=p["decision_record"]["recorded_state"] == "open-unresolved", record_source_time=p["decision_record"]["source"]["observed_at"] == "2026-10-14T16:05:00Z", considers_links=p["decision_considers_edges"] == oracle_projection(self.cases[key])["decision_considers_edges"], no_winner=e["invariants"]["selected_version"] is None and p["current_owner_or_procedure_selected"] is False)
            elif key == "hypothetical-1605":
                self.checks(key, v2_retained=[c["version_id"] for c in p["claims"]] == ["SOP-OPS-9-V2"], record_retained=p["decision_record"] == baseline["mainline-1605"]["evidence"]["witnesses"][0]["projection"]["decision_record"], no_v3_dependent_output=not any(t in json.dumps(e) for t in source_case("branch", "branch-at-16-05-suppression")["forbidden_v3_literals"]), assumption_labeled=e["witnesses"][0]["scenario"] == source_case("branch", "branch-at-16-05-suppression")["branch_assumption"])
            else:
                s = next(s for s in e["source_occurrences"] if s["source_id"] == "SRC-SOP-CURRENT-V3")
                self.checks(key, v3_restored=len(p["claims"]) == 2 and p["conflict"] is not None, actual_clock=s["observed_at"] == "2026-10-14T16:04:00Z", assumed_clock=s["projected_observed_at"] == "2026-10-14T16:06:00Z", mainline_unchanged=self.api("mainline-1605") == baseline["mainline-1605"], not_source_truth=e["witnesses"][0]["scenario"]["scenario_not_source_truth"] is True)
        self.delete("SOP-OPS-9-V3", "EVIDENCED_BY", "SRC-SOP-CURRENT-V3")
        mutated = self.api("mainline-1605")["evidence"]["witnesses"][0]["projection"]
        self.check("mutation-sensitivity", "v3-source-dependent", mutated == source_case("timeline", "separate-evidence-edge-removals")["v3_source_evidence_edge_removal"]["projection"])
        unchanged = self.api("outage-1830") == baseline["outage-1830"]
        self.reset(); self.delete("DEC-OPS-9-REVIEW-01", "EVIDENCED_BY", "SRC-DEC-OPS-9-REVIEW-01")
        self.check("mutation-sensitivity", "record-source-dependent", self.api("mainline-1605")["evidence"]["witnesses"][0]["projection"] == source_case("timeline", "separate-evidence-edge-removals")["decision_record_source_edge_removal"]["projection"])
        self.reset(); self.delete("DEPT-CENTRAL-STERILE", "OWNED_BY", "ROLE-STERILE-OPS-COORD")
        self.check("mutation-sensitivity", "ownership-dependent", self.api("outage-1830")["evidence"]["witnesses"][0]["path_evidence"] == expected_paths[1:])
        unchanged &= self.api("mainline-1605") == baseline["mainline-1605"]
        self.reset(); self.delete("SVC-MATERIAL-TRANSFER", "EVIDENCED_BY", "SRC-SERVICE-CATALOG")
        self.check("mutation-sensitivity", "service-source-required", self.api("outage-1830")["evidence"]["witnesses"][0]["path_evidence"] == [])
        for identifier,a,rel,b in [("branch-edge-one-unknown", "BR-SOP-V3-LATE-OBS", "HAS_HYPOTHETICAL_OBSERVATION", "OBS-SOP-V3-LATE-OBS"), ("branch-edge-two-unknown", "OBS-SOP-V3-LATE-OBS", "FOR_SOURCE", "SRC-SOP-CURRENT-V3")]:
            self.reset(); self.delete(a,rel,b)
            unknown = self.api("hypothetical-1605")["evidence"]
            self.check("mutation-sensitivity", identifier, unknown["witnesses"] == [] and unknown["source_occurrences"] == [] and not any(t in json.dumps(unknown) for t in ["SOP-OPS", "SRC-", "DEC-OPS", "ROLE-"]))
        self.check("mutation-sensitivity", "unrelated-context-unchanged", unchanged)
        self.reset()
        self.seed("decoy-branch", disconnected_decoy_branch_queries()); self.seed("restricted-branch", restricted_source_branch_queries())
        self.seed("briefcase-injection", seed_queries_for([
            node("Evidence", "SECRET-BRIEFCASE", title="Restricted briefcase canary", fact="private instruction canary", observed_at="2026-10-14T15:00:00Z", access="restricted"),
            node("Evidence", "DECOY-BRIEFCASE", title="Disconnected briefcase canary", observed_at="2026-10-14T15:00:00Z", access="operations")],
            [edge("SOP-OPS-9-V3", "EVIDENCED_BY", "SECRET-BRIEFCASE", "REL-SECRET-BRIEFCASE", access="operations"), edge("ROLE-STERILE-OPS-COORD", "EVIDENCED_BY", "SECRET-BRIEFCASE", "REL-SECRET-ROLE-BRIEFCASE", access="operations"), edge("DEC-OPS-9-REVIEW-01", "EVIDENCED_BY", "SECRET-BRIEFCASE", "REL-SECRET-RECORD-BRIEFCASE", access="operations")]))
        injected = {key:self.api(key) for key in self.cases}
        self.checks("permission-invariance", connected_injection_real=bool(self.graph_rows("injection-exists", "MATCH (:SOPVersion {id:'SOP-OPS-9-V3'})-[:EVIDENCED_BY]->(s:Evidence {id:'SECRET-BRIEFCASE'}) RETURN s.id AS id")), whole_json_invariant=all(injected[k]["evidence"] == baseline[k]["evidence"] for k in baseline), html_invariant=all(injected[k]["report_html"] == baseline[k]["report_html"] for k in baseline), occurrences_invariant=all(injected[k]["evidence"]["source_occurrences"] == baseline[k]["evidence"]["source_occurrences"] for k in baseline), manifest_invariant=all(injected[k]["manifest"] == baseline[k]["manifest"] and injected[k]["zip_base64"] == baseline[k]["zip_base64"] for k in baseline), no_tripwires=not any(t in json.dumps(injected) for t in self.expected["injection_tripwires"]))
        self.reset()
        self.service.action_file.write_bytes(b'[{"id":"owned-read-only-action-canary"}]\n')
        self.service.watch_file.write_bytes(b'{"owned-read-only-watch-canary":true}\n')
        state = {p.name:p.read_bytes() for p in self.state_dir.iterdir()}
        calls=[]; original=product.capture_evidence
        product.capture_evidence=lambda *a,**kw:calls.append((a,kw))
        try:
            responses=[self.get("/api/evidence-briefcase?"+q) for q in self.expected["invalid_queries"]]
        finally: product.capture_evidence=original
        offset_case=dict(self.cases["mainline-1605"], known_at="2026-10-14T21:35:00+05:30")
        self.checks("reject-before-query", all_invalid_400=all(s == 400 for s,b in responses), generic_error=all(b == {"error":"Invalid historical evidence selection."} for s,b in responses), no_service_calls=calls == [], normalized_offset=self.api(offset_case) == baseline["mainline-1605"], no_arbitrary_input=len(responses) == len(self.expected["invalid_queries"]) and all(s == 400 for s,b in responses), state_unchanged=state == {p.name:p.read_bytes() for p in self.state_dir.iterdir()})
        before = self.graph_snapshot("read-only-before"); pending=copy.deepcopy((self.service.pending,self.service.pending_watches))
        browser = self.browser("seeded")
        for key in self.cases: assert self.api(key) == baseline[key]
        archives=[read_archive_bytes(base64.b64decode(v["zip_base64"])) for v in baseline.values()]
        self.checks("archive-browser", fixed_members=all(set(a) == {"evidence.json","report.html","manifest.json"} for a in archives), exact_hashes=len(archives) == 6, downloaded_bytes=browser["downloaded_bytes_equal"] is True, offline_desktop=browser["offline_desktop"] >= 6, offline_mobile=browser["offline_mobile"] >= 6, network_blocked=browser["offline_network_requests"] == [], stale_selection=browser["stale_selection_passed"] is True, keyboard_responsive=browser["keyboard_passed"] and browser["responsive_passed"])
        after=self.graph_snapshot("read-only-after")
        counts=self.graph_counts("first-seed"); self.seed("idempotent-repeat",self.fixture_queries()); counts2=self.graph_counts("second-seed")
        fresh=WardAtlasService(self.args.db_port,self.db.graph,self.state_dir)
        reconnected={key:product.package_evidence(product.capture_evidence(fresh,selection(case))) for key,case in self.cases.items()}
        self.checks("repeat-reconnect-read-only", repeat_whole_snapshot=all(self.api(k) == baseline[k] for k in baseline), idempotent_reseed=counts == counts2 and before == self.graph_snapshot("reseed-snapshot"), new_client_reconnect=reconnected == baseline, graph_unchanged=before == after, action_watch_bytes_unchanged=state == {p.name:p.read_bytes() for p in self.state_dir.iterdir()} and pending == (self.service.pending,self.service.pending_watches), root_link='href="/briefcase"' in self.get("/")[1])
        # Rerunnable per-run evidence contains no runtime paths inside packages.
        for key,bundle in baseline.items():
            (self.args.evidence_dir/(key+".zip")).write_bytes(base64.b64decode(bundle["zip_base64"]))


def main():
    p=argparse.ArgumentParser()
    for flag in ("all","require-live-falkordb","desktop-mobile","assert-downloaded-bytes","assert-offline","fail-on-zero"):
        p.add_argument("--"+flag, action="store_true", required=True)
    args=p.parse_args(); expected,matrix=validate_contract()
    args.lane=os.environ.get("WORKSHOP_LANE","local"); args.db_port,args.app_port=free_ports()
    args.evidence_dir=Path(os.environ.get("WORKSHOP_RUN_DIR",str(ROOT/".local/briefcase-evidence")))/"briefcase"
    args.evidence_dir.mkdir(parents=True,exist_ok=True)
    v=Verify(args,expected,matrix); error=None; old=product.utc_now; product.utc_now=lambda:expected["capture_clock"]
    try: v.run()
    except Exception as exc:
        error=f"{type(exc).__name__}: {exc}"; import traceback; traceback.print_exc()
    finally:
        product.utc_now=old; v.stop()
        if not error:
            try:
                v.check("repeat-reconnect-read-only","owned-cleanup",all(v.cleanup.values()))
                assert all(v.coverage[g["id"]] == set(g["assertions"]) for g in matrix["groups"]), "missing mapped assertions"
            except Exception as exc: error=str(exc)
        report={"status":"FAIL" if error else "PASS","error":error,"mapped_assertion_count":len(v.assertions),"assertions":v.assertions,"coverage":{k:sorted(s) for k,s in v.coverage.items()},"cleanup":v.cleanup,"database":v.db.database,"query_count":v.db.query_count,"oracle_hashes":PINNED}
        (args.evidence_dir/"live.json").write_text(json.dumps(report,indent=2))
        print(json.dumps({"status":report["status"],"assertions":len(v.assertions),"report":str(args.evidence_dir/"live.json")}))
    return bool(error)

if __name__ == "__main__": sys.exit(main())

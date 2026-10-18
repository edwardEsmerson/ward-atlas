#!/usr/bin/env python3
"""Product-local frozen-oracle checks against an owned actual FalkorDB."""
from __future__ import annotations
import argparse
import copy
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
from urllib.parse import urlencode
from unittest.mock import patch

from verify_decision_source_branch_live import SourceBranchLive
from wardatlas_app import WardAtlasService
from wardatlas_fixtures import node, edge, seed_queries_for
from wardatlas_sop_compare import build_queries, compare, parse_parameters

ROOT = Path(__file__).resolve().parents[1]
CONTRACT = "contracts/sop-compare/"
KNOWN = "2026-10-14T16:04:00Z"

def load(path):
    return json.loads((ROOT / path).read_text())

def pointer(reference):
    path, fragment = reference.split("#", 1)
    value = load(path)
    for part in fragment.lstrip("/").split("/"):
        value = value[int(part)] if isinstance(value, list) else value[part.replace("~1", "/").replace("~0", "~")]
    return copy.deepcopy(value)

def validate_contract(matrix=None):
    expected = load(CONTRACT + "expected-results.json")
    matrix = matrix if matrix is not None else load(CONTRACT + "adversarial-matrix.json")
    for path, digest in expected["sources"].items():
        assert hashlib.sha256((ROOT/path).read_bytes()).hexdigest() == digest, "frozen source changed"
    assert not expected["candidate_output_is_oracle"] and not matrix["candidate_output_is_oracle"]
    for ref in expected["source_pointers"].values():
        assert pointer(ref) is not None
    assert len(matrix["groups"]) >= 8 and len({g["id"] for g in matrix["groups"]}) == len(matrix["groups"])
    assert all(g["assertions"] and len(set(g["assertions"])) == len(g["assertions"]) for g in matrix["groups"])
    assert sum(len(g["assertions"]) for g in matrix["groups"]) >= 40
    assert len(matrix["focused_cases"]) >= 12
    return expected, matrix

def facts():
    e = load(CONTRACT + "expected-results.json")
    return {k: pointer(v) for k, v in e["source_pointers"].items()}

def expected_version(identifier):
    """Literal expectation from pinned product facts, NOT candidate output."""
    f = facts()
    if identifier == "SOP-OPS-9-V1":
        a = f["archive"]
        return {"id": identifier, "reference": a["stale_referenced_equipment_id"],
                "source": {"id": a["stale_source_id"], "title": a["stale_source_title"], "observed_at": a["stale_observed_at"]},
                "applies": [load(CONTRACT+"expected-results.json")["task_fixed_edge"]], "claims": []}
    c = f["v2" if identifier == "SOP-OPS-9-V2" else "v3"]
    return {"id": identifier, "reference": c["applicability_edge"]["to"],
            "source": {k: c["source"][k] for k in ("id", "title", "observed_at")},
            "applies": [c["applicability_edge"]], "claims": [c["claim_edge"]]}

def assert_version(value):
    e = expected_version(value["id"])
    assert value["sourced_properties"] == {"referenced_equipment_id": e["reference"]}
    assert [{k: s[k] for k in ("id", "title", "observed_at")} for s in value["sources"]] == [e["source"]]
    assert [{k: a[k] for k in ("from", "relationship", "to")} for a in value["applicability_edges"]] == e["applies"]
    assert [{k: c["edge"][k] for k in ("from", "relationship", "to")} for c in value["role_claims"]] == e["claims"]
    assert all(c["label"] == "unverified SOP claim" for c in value["role_claims"])
    for s in value["sources"]:
        assert s["evidence_link"]["from"] == value["id"] and s["evidence_link"]["relationship"] == "EVIDENCED_BY" and s["evidence_link"]["to"] == s["id"]

def literal_rows():
    # Unit-input rows pinned to committed fixture relationship IDs; not query output.
    rows = {k: [] for k in ("versions", "applies", "claims", "conflicts")}
    for i in (1, 2, 3):
        v = expected_version(f"SOP-OPS-9-V{i}")
        rows["versions"].append({"version_id": v["id"], "referenced_equipment_id": v["reference"],
            "source_id": v["source"]["id"], "source_title": v["source"]["title"], "observed_at": v["source"]["observed_at"], "evidence_edge_id": f"REL-SOP-V{i}-EVIDENCE"})
        rows["applies"].append({"version_id": v["id"], "equipment_id": v["applies"][0]["to"], "edge_id": f"REL-SOP-V{i}-APPLIES-C{3 if i==1 else 4}"})
        if i > 1:
            rows["claims"].append({"version_id": v["id"], "role_id": v["claims"][0]["to"], "edge_id": f"REL-SOP-V{i}-CLAIMS-{'FACILITIES' if i==2 else 'MATERIALS'}"})
    rows["conflicts"] = [{"from_id": "SOP-OPS-9-V2", "to_id": "SOP-OPS-9-V3", "edge_id": "REL-SOP-9-CONFLICT", "status": "unresolved"}]
    return rows

def free_ports(explicit_db=None, explicit_app=None):
    sockets = []
    ports = []
    try:
        base = int(os.environ.get("WORKSHOP_PORT_BASE", 0))
        span = int(os.environ.get("WORKSHOP_PORT_SPAN", 1000))
        for explicit in (explicit_db, explicit_app):
            for port in ([explicit] if explicit is not None else range(base, base+span) if base else [0]):
                s = socket.socket()
                try: s.bind(("127.0.0.1", port))
                except OSError:
                    s.close()
                    if explicit is not None: raise
                    continue
                sockets.append(s); ports.append(s.getsockname()[1]); break
            else: raise RuntimeError("No free lane ports")
        return ports
    finally:
        for s in sockets: s.close()

class Verify(SourceBranchLive):
    def __init__(self, args, expected, matrix):
        adapted = {"groups": [{"group_id": g["id"], "pinned_assertions": [{"id": a} for a in g["assertions"]]} for g in matrix["groups"]]}
        super().__init__(args, dict(expected, groups=[]), adapted)
        self.coverage = {g["id"]: set() for g in matrix["groups"]}
        self.db.graph = "wardatlas_test_sop_compare_" + self.run_id
        self.state_dir = ROOT / ".local/sop-compare-runs" / self.run_id
    def checks(self, group, values):
        for key, value in values.items(): self.check(group, key, value)
    def api(self, **values):
        values.setdefault("known_at", KNOWN)
        status, value = self.get("/api/sop-claim-comparison?" + urlencode(values))
        assert status == 200, (status, value)
        return value
    def browser(self, label, mode="seeded"):
        report = self.args.evidence_dir / (label + ".json")
        subprocess.run(["node", str(ROOT/CONTRACT/"browser.cjs"), self.base_url, mode, str(report), str(self.args.evidence_dir/label)], check=True, timeout=180)
        value = json.loads(report.read_text())
        assert value["status"] == "PASS" and value["assertion_count"] > 0, value
        return value
    def delete(self, a, kind, b):
        self.db.graph_query("owned-edge-removal", f"MATCH (a {{id:'{a}'}})-[r:{kind}]->(b {{id:'{b}'}}) DELETE r")
    def all_views(self):
        return {urlencode(v): self.api(**v) for b in self.expected["boundaries"] for v in (
            {"known_at": b["known_at"]}, *({"known_at": b["known_at"], "left": p[0], "right": p[1], "equipment": e}
                for p in self.expected["pairs"] for e in ("CONVEYOR-C3", "CONVEYOR-C4")))}
    def run(self):
        self.start()
        cold = self.api(); cold_browser = self.browser("cold", "cold")
        self.checks("cold", {"empty-versions": cold["options"]["versions"] == [], "empty-equipment": cold["options"]["equipment"] == [],
            "unknown-pair": cold["left"] is None and cold["right"] is None, "no-identities": not any(x in json.dumps(cold) for x in ("SOP-OPS-9", "CONVEYOR", "SRC-", "ROLE-")), "cold-browser": cold_browser["status"] == "PASS"})
        self.reset()
        views = [self.api(known_at=b["known_at"]) for b in self.expected["boundaries"]]
        before, v2, pre, current = views
        for v in views:
            for version in v["versions"]: assert_version(version)
        self.checks("boundaries", {"before-options": before["options"] == {"versions": self.expected["boundaries"][0]["versions"], "equipment": self.expected["boundaries"][0]["equipment"]},
            "before-no-future": not any(s in json.dumps(before) for s in ("SOP-OPS-9-V2", "SOP-OPS-9-V3", "SRC-SOP-CURRENT", "Current Text")),
            "archive-independent": before["state"] == "source-known evidence" and before["left"]["id"] == "SOP-OPS-9-V1",
            "at-v2-options": v2["options"]["versions"] == self.expected["boundaries"][1]["versions"], "at-v2-unknown": v2["conflict"] == {"state":"unknown", "edges":[]},
            "pre-v3-invariant": dict(pre, known_at=v2["known_at"]) == v2, "at-v3-options": current["options"]["versions"] == self.expected["boundaries"][3]["versions"], "at-v3-unresolved": current["conflict"]["state"] == "unresolved"})
        pairs = [self.api(left=a, right=b, equipment="CONVEYOR-C4") for a,b in self.expected["pairs"]]
        for v in pairs:
            assert_version(v["left"]); assert_version(v["right"])
        archive = pairs[0]["left"]; c2 = pairs[2]["left"]; c3 = pairs[2]["right"]
        self.checks("pairs", {"all-pairs": all(v["selection"]["left"] == p[0] and v["selection"]["right"] == p[1] for p,v in zip(self.expected["pairs"], pairs)),
            "exact-archive-source": {k:archive["sources"][0][k] for k in ("id","title","observed_at")} == expected_version(archive["id"])["source"],
            "exact-current-sources": all({k:c["sources"][0][k] for k in ("id","title","observed_at")} == expected_version(c["id"])["source"] for c in (c2,c3)),
            "archive-no-role": archive["role_claims"] == [], "archive-c3-edge": archive["applicability_edges"][0]["to"] == "CONVEYOR-C3", "archive-c4-unknown": archive["focus_applicability"] == "unknown / not evidenced",
            "v2-role-edge": c2["role_claims"][0]["edge"]["to"] == facts()["v2"]["role_id"], "v3-role-edge": c3["role_claims"][0]["edge"]["to"] == facts()["v3"]["role_id"],
            "typed-differences": all([d["type"] for d in v["differences"]] == ["version_identity","sourced_reference_property","applicability_relationships","unverified_role_claims","source_observations","explicit_conflict_relation"] for v in pairs),
            "no-winner-lineage": all(v["selected_winner"] is None and v["procedure"] is None and v["lineage_edges"] == [] for v in pairs)})
        self.delete("SOP-OPS-9-V1","APPLIES_TO","CONVEYOR-C3")
        v = self.api(left="SOP-OPS-9-V1", right="SOP-OPS-9-V2")["left"]
        self.checks("mutations", {"property-retained": v["sourced_properties"]["referenced_equipment_id"] == "CONVEYOR-C3", "property-not-applicability": v["applicability_edges"] == [] and v["focus_applicability"] == "unknown / not evidenced", "no-archive-role-fallback": v["role_claims"] == []})
        self.reset(); self.delete("SOP-OPS-9-V3","EVIDENCED_BY","SRC-SOP-CURRENT-V3")
        missing = self.api(left="SOP-OPS-9-V2", right="SOP-OPS-9-V3")
        self.checks("mutations", {"v3-source-removal": missing["right"] is None and "SOP-OPS-9-V3" not in json.dumps(missing), "no-default-winner": missing["selected_winner"] is None and missing["conflict"]["state"] == "unknown"})
        self.reset(); self.delete("SOP-OPS-9-V2","SOP_CONFLICT","SOP-OPS-9-V3")
        v=self.api();self.check("mutations","conflict-removal-unknown",v["conflict"] == {"state":"unknown","edges":[]} and len(v["versions"])==3)
        self.reset(); self.delete("SOP-OPS-9-V3","CLAIMS_ROLE","ROLE-MATERIALS-LEAD")
        v=self.api();self.check("mutations","claim-edge-required",v["right"]["role_claims"] == [] and v["conflict"]["state"]=="unknown")
        self.reset(); self.db.graph_query("owned-observation-shift","MATCH (s:Evidence {id:'SRC-SOP-CURRENT-V3'}) SET s.observed_at='2026-10-14T16:06:00Z'")
        self.check("mutations","source-cutoff-sensitive",self.api()["options"]["versions"]==self.expected["boundaries"][1]["versions"])
        self.reset(); self.check("mutations","reseed-restores",self.api()==current)
        clean=self.all_views(); browser_before=self.browser("clean")
        self.seed("decoy",seed_queries_for([node("SOPVersion","DECOY-SOP-COMPARE",sop_id="SOP-OPS-9",access="operations"),node("Evidence","DECOY-SOURCE-COMPARE",title="Disconnected decoy",observed_at=KNOWN,access="operations")],[edge("DECOY-SOP-COMPARE","EVIDENCED_BY","DECOY-SOURCE-COMPARE","DECOY-COMPARE-EDGE",access="operations"),edge("DECOY-SOP-COMPARE","APPLIES_TO","CONVEYOR-C4","DECOY-COMPARE-APPLIES",access="operations")]))
        self.check("visibility","decoy-invariant",self.all_views()==clean)
        self.seed("restricted",seed_queries_for([node("Evidence","SECRET-COMPARE-SOURCE",title="Restricted comparison tripwire",observed_at="2026-01-01T00:00:00Z",access="restricted"),node("Role","SECRET-COMPARE-ROLE",access="restricted")],[edge("SOP-OPS-9-V2","EVIDENCED_BY","SECRET-COMPARE-SOURCE","SECRET-COMPARE-EDGE",access="operations"),edge("SOP-OPS-9-V2","CLAIMS_ROLE","SECRET-COMPARE-ROLE","SECRET-COMPARE-CLAIM",access="operations")]))
        self.check("visibility","denied-connected",bool(self.graph_rows("injection-witness","MATCH (:SOPVersion {id:'SOP-OPS-9-V2'})-[:EVIDENCED_BY]->(s:Evidence {id:'SECRET-COMPARE-SOURCE'}) RETURN s.id AS id")))
        injected=self.all_views(); browser_after=self.browser("injected")
        self.checks("visibility", {"full-api-invariant": injected==clean, "desktop-dom-aria-options": browser_before["desktop"]==browser_after["desktop"], "mobile-dom-aria-options": browser_before["mobile"]==browser_after["mobile"],
            "hidden-selection-generic": self.api(known_at=self.expected["boundaries"][0]["known_at"],left="SOP-OPS-9-V2",right="SOP-OPS-9-V3")["selection"]=={"left":None,"right":None,"equipment":"CONVEYOR-C3"}})
        self.reset(); self.db.graph_query("deny-edge","MATCH (:SOPVersion {id:'SOP-OPS-9-V3'})-[r:EVIDENCED_BY]->() SET r.access='restricted'")
        self.check("visibility","edge-acl","SOP-OPS-9-V3" not in json.dumps(self.api()))
        self.reset(); self.db.graph_query("deny-node","MATCH (v:SOPVersion {id:'SOP-OPS-9-V3'}) SET v.access='restricted'")
        self.check("visibility","node-acl","SOP-OPS-9-V3" not in json.dumps(self.api()))
        self.reset(); self.db.graph_query("deny-source","MATCH (s:Evidence {id:'SRC-SOP-CURRENT-V3'}) SET s.access='restricted'")
        self.check("visibility","source-acl","SOP-OPS-9-V3" not in json.dumps(self.api()))
        self.reset(); self.db.graph_query("deny-equipment","MATCH (e:Equipment {id:'CONVEYOR-C4'}) SET e.access='restricted'")
        v=self.api(equipment="CONVEYOR-C4");self.check("visibility","equipment-independent",v["options"]["equipment"]==["CONVEYOR-C3"] and v["selection"]["equipment"] is None and v["conflict"]["state"]=="unknown")
        self.reset()
        with patch("wardatlas_sop_compare.compare", side_effect=AssertionError("invalid request queried")) as mocked:
            statuses=[self.get("/api/sop-claim-comparison?"+q)[0] for q in self.expected["invalid_queries"]]
        self.checks("validation", {"invalid-inputs": all(s==400 for s in statuses), "zero-reads": mocked.call_count==0,
            "offset-normalized": self.api(known_at="2026-10-14T18:04:00+02:00")==current,
            "duplicate-selection": statuses[7]==400,"unknown-equipment":statuses[8]==400,
            "bounded-parameterized":all(q.startswith("CYPHER ") and "LIMIT 65" in q and "$cutoff" in q for q in build_queries(KNOWN).values())})
        self.service.action_file.write_text('[{"id":"sop-compare-read-only-canary"}]\n')
        self.service.watch_file.write_text('{"sop-compare-read-only-canary":true}\n')
        graph_before=self.graph_snapshot("before-browser");state_before={p.name:p.read_bytes() for p in self.state_dir.iterdir()}
        final_browser=self.browser("final")
        self.checks("browser", {"keyboard-pairs":final_browser["keyboard"],"desktop":bool(final_browser["desktop"]),"mobile":bool(final_browser["mobile"]),"race-guard":final_browser["race_guard"],"escaped-values":final_browser["escaped_values"],"no-overflow":final_browser["no_overflow"],"root-link":'href="/sop-compare"' in self.get('/')[1]})
        self.checks("lifecycle", {"graph-read-only":graph_before==self.graph_snapshot("after-browser"),"state-read-only":state_before=={p.name:p.read_bytes() for p in self.state_dir.iterdir()}})
        count=self.graph_counts("first");self.reset();second=self.graph_counts("second")
        self.check("lifecycle","repeat-seed",count==second and self.api()==current)
        fresh=WardAtlasService(self.args.db_port,self.db.graph,self.state_dir)
        self.check("lifecycle","reconnect",compare(fresh,parse_parameters("known_at="+KNOWN))==current)
        (self.args.evidence_dir/"api-views.json").write_text(json.dumps(clean,indent=2))

def main():
    p=argparse.ArgumentParser()
    for flag in ("all","require-live-falkordb","desktop-mobile","fail-on-zero"):p.add_argument("--"+flag,action="store_true",required=True)
    p.add_argument("--db-port",type=int);p.add_argument("--app-port",type=int)
    args=p.parse_args();expected,matrix=validate_contract()
    args.lane=os.environ.get("WORKSHOP_LANE","local");args.db_port,args.app_port=free_ports(args.db_port,args.app_port)
    args.evidence_dir=Path(os.environ.get("WORKSHOP_RUN_DIR",str(ROOT/".local/sop-compare-evidence")))/"sop-compare";args.evidence_dir.mkdir(parents=True,exist_ok=True)
    verifier=Verify(args,expected,matrix);error=None
    try:verifier.run()
    except Exception as exc:
        error=f"{type(exc).__name__}: {exc}"
        import traceback;traceback.print_exc()
    finally:
        verifier.stop()
        if not error:
            try:
                verifier.check("lifecycle","owned-cleanup",all(verifier.cleanup.values()))
                assert all(verifier.coverage[g["id"]]==set(g["assertions"]) for g in matrix["groups"]),"missing assertion mappings"
            except Exception as exc:error=str(exc)
        report={"status":"FAIL" if error else "PASS","error":error,"group_count":len(matrix["groups"]),"mapped_assertion_count":len(verifier.assertions),"assertions":verifier.assertions,"coverage":{k:sorted(v) for k,v in verifier.coverage.items()},"cleanup":verifier.cleanup,"database":verifier.db.database,"resources":verifier.db.resource_samples[-5:],"source_hashes":expected["sources"]}
        path=args.evidence_dir/"live.json";path.write_text(json.dumps(report,indent=2));print(json.dumps({"status":report["status"],"assertions":len(verifier.assertions),"report":str(path)}))
    return bool(error)
if __name__=="__main__":sys.exit(main())

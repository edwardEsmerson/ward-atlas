"""Read-only historical evidence packages. No source files, model or write service.

One package is built in memory on preview. The browser downloads those same
bytes, not a second query. Hashes detect accidental changes, not authenticity.
"""
from __future__ import annotations

import base64
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import html
import io
import json
from pathlib import Path
import re
from urllib.parse import parse_qs
import zipfile

from wardatlas_shift_board import normalize_timestamp
from wardatlas_source_branch import SOURCE_ID as BRANCH_SOURCE_ID

CONTEXTS = ("outage-snapshot", "sop-mainline", "sop-hypothetical")
FILES = ("evidence.json", "report.html", "manifest.json")
WARNING = ("Synthetic, read-only historical evidence; not current truth. "
           "Not a handover proposal, confirmed action, watch, acknowledgment or hospital communication. "
           "No SOP winner, safety or resolution conclusion is supplied.")
INTEGRITY = "Accidental-change detection only; not a signature, authenticity guarantee or proof of correctness."
INVARIANTS = dict(read_only=True, synthetic=True, not_current_truth=True,
                  selected_version=None, safe_asserted=False, resolution_asserted=False,
                  action_proposed=False, action_confirmed=False, watch_created=False,
                  acknowledgment_created=False, hospital_communication=False, external_writes=False)
ERROR = "Invalid historical evidence selection."
PAGE = Path(__file__).parent / "ui/evidence_briefcase.html"


def utc_now():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def normalize_selection(values):
    """The public API has one context and one explicit time axis; no defaults."""
    if not isinstance(values, dict) or set(values) - {"scope", "context", "as_of", "known_at"}:
        raise ValueError(ERROR)
    if any(not isinstance(v, list) or len(v) != 1 or not isinstance(v[0], str) or not v[0] for v in values.values()):
        raise ValueError(ERROR)
    if values.get("scope", ["operations"]) != ["operations"]:
        raise ValueError(ERROR)
    context = values.get("context", [None])[0]
    if context not in CONTEXTS:
        raise ValueError(ERROR)
    axis = "as_of" if context == CONTEXTS[0] else "known_at"
    if set(values) - {"scope", "context", axis} or axis not in values:
        raise ValueError(ERROR)
    stamp = values[axis][0]
    offset = re.search(r"[+-](\d{2}):(\d{2})$", stamp)
    if offset and (int(offset[1]) > 23 or int(offset[2]) > 59):
        raise ValueError(ERROR)
    try:
        stamp = normalize_timestamp(stamp)
    except (ValueError, OverflowError):
        raise ValueError(ERROR) from None
    return {"scope": "operations", "context": context, axis: stamp}


def parse_selection(query):
    try:
        values = parse_qs(query, keep_blank_values=True, strict_parsing=True, max_num_fields=5)
    except ValueError:
        raise ValueError(ERROR) from None
    return normalize_selection(values)


def capture_evidence(service, selection, *, captured_at=None):
    # Revalidate direct callers as well as HTTP before opening any graph client.
    selection = normalize_selection({k: [v] for k, v in selection.items()})
    evidence = {"schema_version": 1, "artifact_type": "historical-evidence",
                "selection": selection, "captured_at": captured_at or utc_now(),
                "capture_clock_label": "Capture UTC time, not selected historical time or a freshness guarantee",
                "warning": WARNING, "invariants": deepcopy(INVARIANTS),
                "source_facts": [], "inferences": [], "unknowns": [],
                "witnesses": [], "source_occurrences": []}
    facts, inferred, sources = evidence["source_facts"], evidence["inferences"], evidence["source_occurrences"]
    def cite(source, occurrence, *, actual=None):
        # An occurrence is intentionally NOT indexed/deduplicated by source ID.
        item = {"occurrence": occurrence, "source_id": source["id"],
                "title": source["title"], "observed_at": actual or source["observed_at"]}
        if actual and actual != source["observed_at"]:
            item.update(projected_observed_at=source["observed_at"], time_kind="hypothetical assumption, not source truth")
        sources.append(item)
    with service.graph_lock:
        if selection["context"] == "outage-snapshot":
            impact = service.sop_impact(selection["as_of"])
            paths, event = deepcopy(impact["path_evidence"]), deepcopy(impact["observed_event"])
            if paths or event:
                evidence["witnesses"].append({"type": "outage-snapshot", "impact_state": impact["impact_state"],
                                               "path_evidence": paths, "observed_event": event})
            if event:
                facts.append({"type": "observed-event", "event": event})
                cite({"id": event["source_id"], "title": event["source_title"], "observed_at": event["source_observed_at"]}, "event")
            for index, path in enumerate(paths):
                inferred.append({"type": "ownership-path", "nodes": path["path"],
                                 "relationships": ["AFFECTS", "SUPPORTS", "SERVES", "OWNED_BY"],
                                 "meaning": "Graph-derived active impact and roster-role path at selected impact time; not a suggested action"})
                for j, (identifier, title, observed) in enumerate(zip(path["source_ids"], path["source_titles"], path["observed_at"])):
                    cite({"id": identifier, "title": title, "observed_at": observed}, f"path/{index}/source/{j}")
            evidence["unknowns"].append("No safety or resolution conclusion follows from the returned or missing active paths. This context does not project source-time SOP knowledge.")
        else:
            scenario, mainline = None, None
            if selection["context"] == "sop-mainline":
                projection = deepcopy(service.decision_timeline(selection["known_at"]))
            else:
                branch = service.decision_source_branch(selection["known_at"])
                selected = branch["hypothetical_branch"]
                scenario = deepcopy(selected.get("scenario"))
                projection = deepcopy(selected["projection"])
                mainline = branch["mainline"]
            if not projection["generic_unknown"]:
                witness = {"type": "sop-source-time", "projection": projection}
                if scenario:
                    witness["scenario"] = scenario
                evidence["witnesses"].append(witness)
            for index, claim in enumerate(projection["claims"]):
                source = claim["source"]
                actual = None
                if mainline:
                    matching = next((c for c in mainline["claims"] if c["version_id"] == claim["version_id"] and c["source"]["id"] == source["id"]), None)
                    if matching:
                        actual = matching["source"]["observed_at"]
                # Mainline claims are cutoff-filtered: absence there does not
                # turn an assumed observation into a source fact. The accepted
                # branch witness carries the actual clock even past cutoff.
                # Its one-source scope must never rewrite V2 or another source
                # merely because they happen to share the assumed timestamp.
                if scenario and source["id"] == BRANCH_SOURCE_ID:
                    actual = scenario["actual_source_observed_at"]
                fact = deepcopy(claim)
                if actual and actual != source["observed_at"]:
                    fact["source"]["observed_at"] = actual
                facts.append({"type": "unverified-sop-claim", "claim": fact})
                cite(source, f"claim/{index}", actual=actual)
            record = projection["decision_record"]
            if record:
                facts.append({"type": "independently-evidenced-record", "record": deepcopy(record)})
                cite(record["source"], "decision-record")
            if projection["conflict"]:
                inferred.append({"type": "graph-derived-conflict", "witness": deepcopy(projection["conflict"])})
            if projection["decision_considers_edges"]:
                inferred.append({"type": "considered-links", "relationships": deepcopy(projection["decision_considers_edges"])})
            if scenario and not projection["generic_unknown"]:
                inferred.append({"type": "source-time-hypothesis", "assumption": scenario})
            evidence["unknowns"].append("Missing source evidence is unknown, not absence, resolution, an SOP winner, current ownership or a procedure. This context does not project outage impact.")
    if not evidence["witnesses"]:
        evidence["unknowns"] = ["Unknown: supporting evidence is unavailable for this selection. No fallback or negative conclusion is supplied."]
    return evidence


def json_bytes(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")


STYLE = """body{font:16px/1.55 system-ui,sans-serif;margin:0;background:#f1f5f6;color:#172d36}main{max-width:1080px;margin:auto;padding:clamp(16px,4vw,48px)}h1{font-size:clamp(28px,5vw,44px);line-height:1.15}h2{margin-top:32px}section,.notice{background:white;border:1px solid #c1d4d9;border-radius:14px;padding:18px;margin:16px 0}.notice{border-left:6px solid #257e78}ol{padding-left:24px}li{margin:8px 0}dl{margin:8px 0}dt{font-weight:700;color:#365a65}dd{margin:0 0 8px 18px}code{font-size:.9em}*{box-sizing:border-box;overflow-wrap:anywhere}.path{border-left:3px solid #257e78;padding-left:28px}small{color:#435e68}@media(max-width:600px){dd{margin-left:10px}section{padding:12px}}"""


def _tree(value):
    """Every value is text, never markup, a URL, an asset or an active link."""
    if isinstance(value, dict):
        return "<dl>" + "".join("<dt>" + html.escape(k.replace("_", " ")) + "</dt><dd>" + _tree(v) + "</dd>" for k, v in value.items()) + "</dl>"
    if isinstance(value, list):
        return "<ol>" + "".join("<li>" + _tree(v) + "</li>" for v in value) + "</ol>" if value else "<p>None returned; not an absence conclusion.</p>"
    return html.escape(json.dumps(value) if not isinstance(value, str) else value, quote=True)


def render_report(evidence):
    e = html.escape
    sections = ""
    for key, label in [("source_facts", "Source facts · unverified claims remain claims"), ("inferences", "Graph inference and hypothetical assumptions"), ("unknowns", "Unknown / missing evidence"), ("witnesses", "Typed witnesses and accessible relationship paths"), ("source_occurrences", "Citation occurrences · ID / title / observation UTC")]:
        sections += "<section><h2>" + label + "</h2>" + _tree(evidence[key]) + "</section>"
    paths = ""
    for item in evidence["inferences"]:
        if item["type"] == "ownership-path":
            paths += '<ol class="path" aria-label="Impact relationship path">' + "".join("<li><code>" + e(n) + "</code>" + (" — " + e(item["relationships"][i]) + " →" if i < len(item["relationships"]) else "") + "</li>" for i, n in enumerate(item["nodes"])) + "</ol>"
    return ('<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
            '<meta http-equiv="Content-Security-Policy" content="default-src \'none\'; style-src \'unsafe-inline\'; base-uri \'none\'; form-action \'none\'">'
            '<title>WardAtlas historical evidence briefcase</title><style>' + STYLE + '</style></head><body><main>'
            '<p>WARDATLAS / OFFLINE EVIDENCE</p><h1>Historical evidence briefcase</h1><p class="notice">' + e(evidence["warning"]) + '</p>'
            '<section><h2>Selected historical UTC axes</h2>' + _tree(evidence["selection"]) + '<p><strong>Captured at (UTC): </strong>' + e(evidence["captured_at"]) + '</p><p>' + e(evidence["capture_clock_label"]) + '</p></section>'
            + paths + sections + '<section><h2>Read-only boundary</h2>' + _tree(evidence["invariants"]) + '<p>' + e(INTEGRITY) + '</p></section></main></body></html>')


def package_evidence(evidence):
    """Fixed names, memory-only ZIP. No path argument or filesystem attachments."""
    report = render_report(evidence)
    contents = {"evidence.json": json_bytes(evidence), "report.html": report.encode("utf-8")}
    manifest = {"schema_version": 1, "artifact_type": "historical-evidence", "selection": evidence["selection"],
                "captured_at": evidence["captured_at"], "warning": WARNING, "integrity_notice": INTEGRITY,
                "files": {name: {"sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)} for name, data in contents.items()}}
    contents["manifest.json"] = json_bytes(manifest)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name in FILES:
            entry = zipfile.ZipInfo(name, date_time=(2026, 10, 14, 0, 0, 0))
            entry.compress_type = zipfile.ZIP_DEFLATED
            entry.external_attr = 0o100644 << 16
            archive.writestr(entry, contents[name])
    return {"evidence": evidence, "report_html": report, "manifest": manifest,
            "zip_base64": base64.b64encode(buffer.getvalue()).decode("ascii")}


def mount_briefcase(handler, service, parsed):
    if parsed.path == "/briefcase":
        handler._send(200, PAGE.read_text(encoding="utf-8"))
    elif parsed.path == "/api/evidence-briefcase":
        try:
            selection = parse_selection(parsed.query)
        except ValueError:
            handler._error(400, ERROR)
            return True
        handler._send(200, package_evidence(capture_evidence(service, selection)))
    else:
        return False
    return True

#!/usr/bin/env python3
"""Static, fail-closed verification of the accepted query appendix.

No application imports, exec/eval, graph, browser, network or evidence-directory
dependency. Frozen oracle values, not candidate behavior, specify the outcomes.
The deliberately small AST renderer supports only the pinned string builders.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SOURCE_COMMIT = "b540f032a50d14384602e638abc1ff59f50160cf"
EXPECTED_SHA256 = "2f842d6018043eed4231153e64274ab2b1388b3bee97b9789f8343f5ae56e819"
EXPECTED_PATH = "tests/falkordb-query-walkthrough-expected.json"
DOC_PATH = "docs/falkordb-query-walkthrough.md"
FENCE = re.compile(r"<!-- query:([a-z0-9-]+) -->\n```cypher\n(.*?)\n```", re.S)


class WalkthroughError(ValueError):
    """A pin, literal query or documented safety boundary has drifted."""


def require(ok: bool, message: str) -> None:
    if not ok:
        raise WalkthroughError(message)


def sha256(value: bytes | str) -> str:
    return hashlib.sha256(value.encode("utf-8") if isinstance(value, str) else value).hexdigest()


def canonical_hash(value: Any) -> str:
    return sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True))


def load_expected(raw: bytes, expected_source_commit: str) -> dict[str, Any]:
    require(expected_source_commit == SOURCE_COMMIT, "expected source commit differs from accepted pin")
    require(sha256(raw) == EXPECTED_SHA256, "expectation manifest bytes differ from pre-test pin")
    data = json.loads(raw)
    require(data["source_commit"] == SOURCE_COMMIT, "manifest source commit drift")
    require(len(data["queries"]) == 5 and len(data["outcomes"]) == 7, "zero/missing query or outcome cases")
    require(sum(len(q["generated_sha256"]) for q in data["queries"]) == 11, "missing literal query hashes")
    return data


def source_symbol(source: str, symbol: str) -> ast.Assign | ast.FunctionDef:
    matches = []
    for node in ast.parse(source).body:
        if isinstance(node, ast.FunctionDef) and node.name == symbol:
            matches.append(node)
        elif isinstance(node, ast.Assign) and len(node.targets) == 1:
            if isinstance(node.targets[0], ast.Name) and node.targets[0].id == symbol:
                matches.append(node)
    require(len(matches) == 1, f"missing/ambiguous pinned source symbol: {symbol}")
    return matches[0]


def string_expression(node: ast.AST, env: dict[str, Any]) -> Any:
    """Interpret data expressions only; never execute repository code."""
    if isinstance(node, ast.Constant) and (isinstance(node.value, str) or node.value is None):
        return node.value
    if isinstance(node, ast.Name):
        require(node.id in env, f"unbound string-builder name: {node.id}")
        return env[node.id]
    if isinstance(node, ast.JoinedStr):
        return "".join(string_expression(item, env) for item in node.values)
    if isinstance(node, ast.FormattedValue):
        require(node.conversion == -1 and node.format_spec is None, "unsupported f-string conversion")
        return str(string_expression(node.value, env))
    if isinstance(node, ast.BoolOp) and isinstance(node.op, ast.Or):
        for item in node.values:
            value = string_expression(item, env)
            if value:
                return value
        return value
    if isinstance(node, ast.Call) and not node.keywords:
        if isinstance(node.func, ast.Name) and node.func.id == "cypher_string" and len(node.args) == 1:
            value = string_expression(node.args[0], env)
            require(isinstance(value, str), "cypher_string argument is not a string")
            return json.dumps(value, ensure_ascii=True)
        if isinstance(node.func, ast.Attribute) and node.func.attr == "strip" and not node.args:
            value = string_expression(node.func.value, env)
            require(isinstance(value, str), "strip receiver is not a string")
            return value.strip()
    raise WalkthroughError(f"unsupported string-builder AST: {type(node).__name__}")


def render_query(sources: dict[str, str], query: dict[str, Any], cutoff: str | None) -> str:
    env: dict[str, Any] = {}
    # Only literal constants are admitted from the dependency and current file.
    # This avoids importing even hash-verified modules with possible side effects.
    for path in ("scripts/wardatlas_decisions.py", query["file"]):
        for node in ast.parse(sources[path]).body:
            if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
                if isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
                    env[node.targets[0].id] = node.value.value
    node = source_symbol(sources[query["file"]], query["symbol"])
    if isinstance(node, ast.Assign):
        result = string_expression(node.value, env)
    else:
        require([arg.arg for arg in node.args.args] == ["known_at"], "unexpected query-builder arguments")
        env["known_at"] = cutoff
        result = None
        for statement in node.body:
            if isinstance(statement, ast.Expr) and isinstance(statement.value, ast.Constant) and isinstance(statement.value.value, str):
                continue  # function docstring
            if isinstance(statement, ast.Assign) and len(statement.targets) == 1:
                target = statement.targets[0]
                require(isinstance(target, ast.Name) and target.id == "cutoff", "unexpected builder assignment")
                env[target.id] = string_expression(statement.value, env)
            elif isinstance(statement, ast.Return):
                result = string_expression(statement.value, env)
                break
            else:
                raise WalkthroughError("unexpected query-builder statement")
    require(isinstance(result, str) and bool(result), "zero/invalid generated query")
    return result


def verify_source_bytes(sources: dict[str, str], expected: dict[str, Any]) -> None:
    for path, digest in expected["source_sha256"].items():
        require(path in sources and sha256(sources[path]) == digest, f"query source hash drift: {path}")
    for query in expected["queries"]:
        source = sources[query["file"]]
        segment = ast.get_source_segment(source, source_symbol(source, query["symbol"]))
        require(sha256(segment) == query["symbol_sha256"], f"source symbol hash drift: {query['symbol']}")


def load_sources(root: Path, expected: dict[str, Any]) -> dict[str, str]:
    sources = {}
    for path in expected["source_sha256"]:
        current = (root / path).read_bytes()
        try:
            accepted = subprocess.run(
                ["git", "show", f"{SOURCE_COMMIT}:{path}"], cwd=root,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True, timeout=15,
            ).stdout
        except (subprocess.SubprocessError, OSError) as exc:
            raise WalkthroughError("accepted source commit unavailable; clone its Git history, do not regenerate pins") from exc
        require(current == accepted, f"working source differs from accepted commit: {path}")
        sources[path] = accepted.decode("utf-8")
    verify_source_bytes(sources, expected)
    return sources


def generated_queries(sources: dict[str, str], expected: dict[str, Any]) -> dict[str, str]:
    verify_source_bytes(sources, expected)
    fences = {}
    for query in expected["queries"]:
        for cutoff, digest in query["generated_sha256"].items():
            literal = render_query(sources, query, None if cutoff == "constant" else cutoff)
            require(sha256(literal) == digest, f"literal generated query hash drift: {query['id']} @ {cutoff}")
            if cutoff in ("constant", expected["fence_cutoff"]):
                fences[query["id"]] = literal
    require(len(fences) == 5, "zero/missing literal query fences")
    return fences


def verify_oracles(root: Path, expected: dict[str, Any]) -> dict[str, Any]:
    documents = {}
    for path, digest in expected["oracle_sha256"].items():
        raw = (root / path).read_bytes()
        require(sha256(raw) == digest, f"frozen oracle/report hash drift: {path}")
        documents[path] = json.loads(raw)
    branch = documents["contracts/decision-source-branch/expected-results.json"]
    groups = {row["id"]: row["expected"] for row in branch["groups"]}
    for outcome in expected["outcomes"]:
        value = groups[outcome["group"]][outcome["key"]]
        require(canonical_hash(value) == outcome["oracle_value_sha256"], f"pinned outcome drift: {outcome['id']}")
    return documents


def oracle_provenance_statements(oracles: dict[str, Any]) -> list[str]:
    """Bind prose associations to independently frozen facts, not mere IDs."""
    branch = oracles["contracts/decision-source-branch/expected-results.json"]
    groups = {row["id"]: row["expected"] for row in branch["groups"]}
    projection = groups["mainline-at-16-05"]["projection"]
    statements = []
    for claim in projection["claims"]:
        version = claim["version_id"].rsplit("-", 1)[-1]
        source = claim["source"]
        observation_label = "actually observed" if version == "V3" else "observed"
        statements.append(
            f"{version} claims `{claim['role_id']}` from `{source['id']}` "
            f"(*{source['title']}*, {observation_label} `{source['observed_at']}`)."
        )
    record = projection["decision_record"]
    source = record["source"]
    statements.append(
        f"The separately sourced decision `{record['id']}` is recorded as "
        f"`{record['recorded_state']}` by `{source['id']}` "
        f"(*{source['title']}*, observed `{source['observed_at']}`)."
    )
    return statements


# These detect positive overclaims even if the correct caveats remain elsewhere.
# Required statements independently reject omission/negation of each boundary.
UNSAFE_PATTERNS = (
    r"\b(?:HIPAA|GDPR)[ -]compliant\b",
    r"\b(?:clinically validated|production[ -]ready|safe for patient care)\b",
    r"\b(?:provides?|performs?|automates?)\s+(?:clinical triage|diagnosis|treatment selection|medication dosing)\b",
    r"\b(?:improves?|improved|guarantees?)\s+(?:patient outcomes?|patient safety|a win|eligibility)\b",
    r"\b(?:a |the |our )?(?:runtime |AI |language )?(?:model|LLM)\s+(?:is called|runs|generates|selects|reasons|answers)\b",
    r"\b(?:is|was|has been|are|were)\s+(?:publicly\s+)?(?:published|submitted|deployed)\b",
    r"\b(?:public deployment|public repository|demo video|working video)\s+(?:is live|is available|has been recorded|exists)\b",
    r"\b(?:SOP[ -]OPS[ -]9[ -]V[23]|V[23])\s+(?:is|becomes)\s+(?:the )?(?:authoritative|current owner|selected procedure|winner)\b",
    r"\bCLAIMS_ROLE\s+(?:identifies|selects|proves)\s+(?:the )?current owner\b",
    r"\b(?:unknown|missing evidence|a missing path)\s+(?:means|proves|confirms)\s+(?:no |absence|resolved|safe|current)\b",
    r"\b(?:missing|removed)\s+(?:branch )?edges?[^.\n]*\b(?:reuse|fall back to|falls back to|use)\s+(?:the )?mainline\b",
    r"\b(?:branch|override)\s+(?:rewrites|updates|mutates|replaces)\s+(?:the )?(?:stored |actual )?(?:source fact|mainline)\b",
    r"\b(?:restricted|inaccessible|hidden)[^.\n]*(?:identities|titles|facts|counts|existence)[^.\n]*\b(?:are disclosed|are visible|is disclosed|is visible)\b",
    r"\b(?:participant contribution|technical understanding|eligibility)\s+(?:is satisfied|is certified|has been certified)\b",
)


def validate_document(doc: str, expected: dict[str, Any], queries: dict[str, str], oracles: dict[str, Any]) -> None:
    matches = list(FENCE.finditer(doc))
    require([m[1] for m in matches] == list(queries), "missing, duplicated or reordered literal query fences")
    require(len(re.findall(r"^```cypher\s*$", doc, re.M)) == 5, "unbound executable Cypher fence")
    for match in matches:
        require(match[2].encode("utf-8") == queries[match[1]].encode("utf-8"), f"literal query fence differs byte-for-byte: {match[1]}")
    prose = FENCE.sub("", doc)
    require(SOURCE_COMMIT in prose, "accepted source commit missing from appendix")
    for text in expected["required_statements"]:
        require(prose.count(text) == 1, f"required semantic statement missing/duplicated: {text}")
    actual_rows = [line for line in prose.splitlines() if re.match(r"\| (?:Mainline|Hypothetical|Missing )", line)]
    require(actual_rows == [o["doc_row"] for o in expected["outcomes"]], "pinned boundary/outcome table differs")
    for path, digest in expected["oracle_sha256"].items():
        citation = r"\[[^\]\n]+\]\(\.\./" + re.escape(path) + r"\): `" + digest + r"`"
        require(re.search(citation, prose) is not None, f"accepted oracle/report digest attribution drift: {path}")
    for label, digest in expected["accepted_reports"].items():
        citation = f"- {label[0].upper() + label[1:]}: `{digest}`."
        require(citation in prose, f"accepted oracle/report digest attribution drift: {label}")
    for query in expected["queries"]:
        key = "constant" if "constant" in query["generated_sha256"] else expected["fence_cutoff"]
        symbol = query["symbol"] + ("" if key == "constant" else f"({key[11:16]}Z)")
        row = f"| {symbol} | `{query['generated_sha256'][key]}` |"
        require(prose.count(row) == 1, f"documented query hash attribution drift: {query['id']}")
        link = f"[`{Path(query['file']).name}:{query['symbol']}`](../{query['file']})"
        require(prose.count(link) == 1, f"source symbol link attribution drift: {query['id']}")
    for path, digest in expected["source_sha256"].items():
        row = f"| [`{path}`](../{path}) | `{digest}` |"
        require(prose.count(row) == 1, f"documented source hash attribution drift: {path}")
    for statement in oracle_provenance_statements(oracles):
        require(prose.count(statement) == 1, f"frozen claim/record provenance prose drift: {statement}")
    original = oracles["tests/expected-results.json"]
    for evidence in original["expected_evidence_by_path"]:
        require(" → ".join(evidence["path"]) in prose, "outage ownership path missing/changed")
        for value in evidence["source_ids"] + evidence["source_titles"] + evidence["observed_at"]:
            require(value in prose, "outage path provenance missing/changed")
    require("(../README.md#clean-setup-and-verification)" in prose and "(demo-runbook.md)" in prose, "setup/runbook link missing")
    require(not re.search(r"(?:/workspace/|/srv/|/home/|/tmp/|file://|https?://|!\[|<img\b|<video\b|\.(?:png|webm|mp4)\b)", doc, re.I), "private path, external endpoint or media in appendix")
    # The exact required caveats are already checked, including their negation.
    # Exclude only those known-safe sentences before scanning added prose; do
    # not globally ignore lines containing "no" (which could hide a new claim).
    for text in expected["required_statements"]:
        prose = prose.replace(text, "")
    for pattern in UNSAFE_PATTERNS:
        require(re.search(pattern, prose, re.I) is None, f"unsafe affirmative claim: {pattern}")


def document_mutations(doc: str, expected: dict[str, Any]) -> list[tuple[str, str, str]]:
    """Nonzero adversarial probes; each carries the diagnostic it must trigger."""
    changes = [
        ("dependency-path", "[sup:SUPPORTS]", "[sup:SERVES]", "literal query fence"),
        ("ownership-direction", "-[owns:OWNED_BY]->(r:Role)", "<-[owns:OWNED_BY]-(r:Role)", "literal query fence"),
        ("missing-node-access", " AND source.access = 'operations'", "", "literal query fence"),
        ("missing-edge-access", " AND aff.access = 'operations'", "", "literal query fence"),
        ("missing-observed-cutoff", '  AND source.observed_at <= "2026-10-14T16:05:00Z"\n', "", "literal query fence"),
        ("exclusive-boundary", 'source.observed_at <= "2026-10-14T16:05:00Z"', 'source.observed_at < "2026-10-14T16:05:00Z"', "literal query fence"),
        ("missing-for-source", "[for_source:FOR_SOURCE]", "[for_source:EVIDENCED_BY]", "literal query fence"),
        ("branch-mainline-clock-conflation", 'override.assumed_observed_at <= "2026-10-14T16:05:00Z"', 'source.observed_at <= "2026-10-14T16:05:00Z"', "literal query fence"),
        ("wrong-branch-cutoff", 'override.assumed_observed_at <= "2026-10-14T16:05:00Z"', 'override.assumed_observed_at <= "2026-10-14T16:06:00Z"', "literal query fence"),
        ("missing-source-join", "  AND branch_source.id = source.id\n", "", "literal query fence"),
        ("false-current-ownership", "WHERE owns.current = true", "WHERE owns.current = false", "literal query fence"),
        ("query-whitespace", "ORDER BY d.id\n```", "ORDER BY d.id \n```", "literal query fence"),
        ("future-record-leak", expected["outcomes"][0]["doc_row"], expected["outcomes"][0]["doc_row"].replace("unknown; no record/existence claim", "open-unresolved"), "boundary/outcome"),
        ("branch-1605-v3-leak", expected["outcomes"][3]["doc_row"], expected["outcomes"][3]["doc_row"].replace("V2 only; incomplete/unknown", "V2 + V3; evidence-backed"), "boundary/outcome"),
        ("hypothetical-as-actual", expected["outcomes"][4]["doc_row"], expected["outcomes"][4]["doc_row"].replace("assumed 16:06Z", "actual 16:06Z"), "boundary/outcome"),
        ("missing-edge-fallback", expected["outcomes"][5]["doc_row"], expected["outcomes"][5]["doc_row"].replace("none; no mainline fallback", "V2 + V3; mainline fallback"), "boundary/outcome"),
        ("missing-eligibility-risk", expected["required_statements"][-2], "", "required semantic statement"),
        ("missing-ai-disclosure", expected["required_statements"][-5], "", "required semantic statement"),
        ("missing-human-confirmation", expected["required_statements"][-4], "", "required semantic statement"),
        ("wrong-report-hash", next(iter(expected["accepted_reports"].values())), "0" * 64, "oracle/report digest"),
        ("wrong-v2-role-provenance", "V2 claims `ROLE-FACILITIES-DUTY`", "V2 claims `ROLE-MATERIALS-LEAD`", "frozen claim/record provenance"),
        ("wrong-record-observation-provenance", "(*Synthetic Decision Register: SOP-OPS-9 Reconciliation*, observed `2026-10-14T16:05:00Z`)", "(*Synthetic Decision Register: SOP-OPS-9 Reconciliation*, observed `2026-10-14T16:06:00Z`)", "frozen claim/record provenance"),
    ]
    results = []
    for name, before, after, diagnostic in changes:
        require(before in doc and before != after, f"zero mutation: {name}")
        results.append((name, doc.replace(before, after, 1), diagnostic))
    for name, first, second, diagnostic in (
        ("swapped-query-hash-attribution", expected["queries"][1]["generated_sha256"][expected["fence_cutoff"]], expected["queries"][2]["generated_sha256"][expected["fence_cutoff"]], "documented query hash attribution"),
        ("swapped-source-hash-attribution", expected["source_sha256"]["scripts/wardatlas_shift_board.py"], expected["source_sha256"]["scripts/wardatlas_knowledge_timeline.py"], "documented source hash attribution"),
        ("swapped-oracle-hash-attribution", expected["oracle_sha256"]["contracts/decision-knowledge-timeline/expected-results.json"], expected["oracle_sha256"]["contracts/decision-source-branch/expected-results.json"], "oracle/report digest attribution"),
        ("swapped-report-hash-attribution", *expected["accepted_reports"].values(), "oracle/report digest attribution"),
    ):
        require(first in doc and second in doc and first != second, f"zero mutation: {name}")
        mutant = doc.replace(first, "\x00HASH_SWAP\x00", 1).replace(second, first, 1).replace("\x00HASH_SWAP\x00", second, 1)
        results.append((name, mutant, diagnostic))
    for name, claim in (
        ("invented-runtime-model", "A runtime model generates the answer."),
        ("invented-publication", "The entry has been publicly published."),
        ("invented-video", "A working video is available."),
        ("invented-compliance", "WardAtlas is HIPAA compliant."),
        ("invented-clinical-validation", "WardAtlas is clinically validated."),
        ("invented-clinical-action", "WardAtlas performs clinical triage."),
        ("false-sop-authority", "SOP-OPS-9-V3 is authoritative."),
        ("false-owner-authority", "CLAIMS_ROLE identifies the current owner."),
        ("unknown-as-absence", "Unknown means no conflict."),
        ("branch-rewrites-mainline", "The branch rewrites the mainline."),
        ("restricted-metadata-leak", "Restricted titles and counts are disclosed."),
        ("false-eligibility-certification", "Participant contribution is certified."),
    ):
        results.append((name, doc + "\n" + claim + "\n", "unsafe affirmative claim"))
    results.append(("private-path", doc + "\nEvidence: /workspace/evidence/private.json\n", "private path"))
    results.append(("private-media", doc + "\n![demo](capture.webm)\n", "private path"))
    require(len({name for name, _, _ in results}) == len(results) >= 10, "zero/duplicate mutation inventory")
    return results


def verify_mutations(doc: str, expected: dict[str, Any], queries: dict[str, str], oracles: dict[str, Any]) -> list[str]:
    rejected = []
    for name, mutant, diagnostic in document_mutations(doc, expected):
        require(mutant != doc, f"zero mutation: {name}")
        try:
            validate_document(mutant, expected, queries, oracles)
        except WalkthroughError as exc:
            require(diagnostic in str(exc), f"mutation rejected for wrong reason: {name}: {exc}")
            rejected.append(name)
        else:
            raise WalkthroughError(f"unsafe mutation survived: {name}")
    return rejected


def verify(root: Path, expected_path: Path, expected_source_commit: str) -> dict[str, Any]:
    expected = load_expected(expected_path.read_bytes(), expected_source_commit)
    sources = load_sources(root, expected)
    queries = generated_queries(sources, expected)
    oracles = verify_oracles(root, expected)
    doc = (root / DOC_PATH).read_bytes().decode("utf-8")
    validate_document(doc, expected, queries, oracles)
    require((root / "README.md").read_text().count("(docs/falkordb-query-walkthrough.md)") == 1, "README needs exactly one walkthrough link")
    rejected = verify_mutations(doc, expected, queries, oracles)
    return {"source_commit": SOURCE_COMMIT, "literal_fences": len(queries), "generated_hashes": 11, "oracle_outcomes": 7, "rejected_mutations": rejected, "graph_or_browser_run": False}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expected", type=Path, default=ROOT / EXPECTED_PATH)
    parser.add_argument("--expected-source-commit", default=SOURCE_COMMIT)
    # All invariants always run; these explicit contract flags cannot weaken them.
    for flag in ("require-pinned-query-hashes", "assert-literal-query-fences", "reject-unsafe-claims", "require-ten-mutations", "fail-on-zero"):
        parser.add_argument("--" + flag, action="store_true")
    args = parser.parse_args(argv)
    try:
        report = verify(ROOT, args.expected, args.expected_source_commit)
    except (WalkthroughError, OSError, ValueError, KeyError, TypeError) as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1
    print(f"PASS: {report['literal_fences']} literal fences; {report['generated_hashes']} pinned generated hashes; {report['oracle_outcomes']} frozen outcome fragments; {len(report['rejected_mutations'])} nonzero mutations rejected; static only")
    print(json.dumps(report, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

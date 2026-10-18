#!/usr/bin/env python3
"""WardAtlas agent: an LLM that answers and acts only through WardAtlas graph tools.

The model never sees the database directly. Each tool is one existing WardAtlas
API call, and every API call is a live FalkorDB read (or, for the handover, a
local write that a human must confirm at the terminal). The agent prints the
tool trace behind each answer.

Standard library only. Works with any OpenAI-compatible chat-completions
endpoint that supports tool calls (OpenAI, OpenRouter, Ollama, vLLM, ...).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

LIVE_DEMO = "https://ayaansh-hoeserver.tail730cfd.ts.net:10000"
MAX_STEPS = 8
MAX_TOOL_CHARS = 12000

SYSTEM_PROMPT = """You are WardAtlas, an operations agent for a fictional hospital. All data is synthetic.
You can only learn facts by calling the WardAtlas tools; each one runs a live query over a FalkorDB graph of
incidents, equipment, services, departments, roles, SOP versions, decision records and dated sources.

Rules:
- Answer only from tool results. If the tools do not establish something, say it is unknown.
- Cite the source IDs and observed_at times behind each claim, e.g. (SRC-OPS-ROSTER, 18:04Z).
- Keep "took effect" (effective/as-of time) separate from "was known" (known_at / source observed time).
- SOP versions V2 and V3 disagree; never pick a winner or present an SOP claim as the current owner.
- Never invent contacts, procedures, clinical advice or actions. A handover is saved only after the human confirms.

Scenario times (UTC, 2026-10-14): INC-204 conveyor C4 outage, impact at 18:30Z, observed restoration 19:30Z,
hypothetical restore drill 18:50Z. SOP evidence: V3 observed 16:04Z, decision record 16:05Z. INC-207 dock roster
change: R2 observed 20:18Z, effective 20:30Z. Always pass explicit ISO-8601 UTC times such as 2026-10-14T18:30:00Z."""


def _fn(name, description, properties=None, required=()):
    return {"type": "function", "function": {
        "name": name, "description": description,
        "parameters": {"type": "object", "properties": properties or {}, "required": list(required),
                       "additionalProperties": False}}}


TIME = {"type": "string", "description": "Explicit ISO-8601 UTC time, e.g. 2026-10-14T18:30:00Z"}

TOOLS = [
    _fn("trace_outage_impact",
        "Trace INC-204 through equipment -> service -> department -> current operating role at a time, with the "
        "dated source behind every hop, plus the SOP-OPS-9 claims that apply to the equipment.",
        {"as_of": TIME}, ["as_of"]),
    _fn("shift_board",
        "Shift board: INC-207 dock ownership at an effective time and a knowledge time (both clocks), explicit "
        "review candidates, and the INC-204 conveyor state replay.",
        {"effective_at": TIME, "known_at": TIME, "conveyor_at": TIME}),
    _fn("decision_timeline",
        "What the graph could support about the SOP-OPS-9 decision at a knowledge cutoff: visible claims, the "
        "conflict edge and the decision record, each only after its source was observed.",
        {"known_at": TIME}, ["known_at"]),
    _fn("decision_docket", "The register-rooted SOP-OPS-9 decision record, both role claims and their sources."),
    _fn("ask_wardatlas",
        "Deterministic cited answer from the graph. Supported questions: 'Who is affected by INC-204?' (needs as_of), "
        "'Explain the SOP-OPS-9 disagreement' (needs known_at), 'What was known about the decision?' (needs known_at).",
        {"question": {"type": "string"}, "as_of": TIME, "known_at": TIME}, ["question"]),
    _fn("draft_handover",
        "Prepare (but do not save) a shift handover for INC-204: a snapshot of the impact paths and SOP witness at "
        "a time. Returns a token for confirm_handover.",
        {"as_of": TIME}),
    _fn("confirm_handover",
        "Save a drafted handover. The human operator is asked to confirm at the terminal first; if they decline, "
        "nothing is saved.",
        {"token": {"type": "string"}}, ["token"]),
]


class WardAtlas:
    """Thin client for the WardAtlas app's JSON API."""

    def __init__(self, base_url, confirm=None):
        self.base_url = base_url.rstrip("/")
        self.confirm = confirm or (lambda summary: False)

    def _request(self, method, path, params=None, body=None):
        url = self.base_url + path
        if params:
            url += "?" + urllib.parse.urlencode({k: v for k, v in params.items() if v})
        data = None if body is None else json.dumps(body).encode()
        request = urllib.request.Request(url, data=data, method=method, headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return json.loads(response.read())
        except urllib.error.HTTPError as exc:
            return {"error": exc.code, "detail": exc.read().decode(errors="replace")[:500]}

    def call(self, name, args):
        ops = "operations"
        if name == "trace_outage_impact":
            return self._request("GET", "/api/sop-impact", {"scope": ops, "as_of": args.get("as_of")})
        if name == "shift_board":
            return self._request("GET", "/api/shift-board", {"scope": ops, **{
                k: args.get(k) for k in ("effective_at", "known_at", "conveyor_at")}})
        if name == "decision_timeline":
            return self._request("GET", "/api/decision-timeline", {"scope": ops, "known_at": args.get("known_at")})
        if name == "decision_docket":
            return self._request("GET", "/api/decision-docket", {"scope": ops})
        if name == "ask_wardatlas":
            body = {"scope": ops, "context": "inc204-operations", "question": args.get("question", "")}
            for key in ("as_of", "known_at"):
                if args.get(key):
                    body[key] = args[key]
            return self._request("POST", "/api/questions", body=body)
        if name == "draft_handover":
            body = {"incident_id": "INC-204"}
            if args.get("as_of"):
                body["as_of"] = args["as_of"]
            return self._request("POST", "/api/handover/draft", body=body)
        if name == "confirm_handover":
            if not self.confirm(f"Save the drafted INC-204 handover (token {args.get('token', '')[:12]}...)?"):
                return {"saved": False, "reason": "The human operator declined or could not be asked."}
            return self._request("POST", "/api/handover/confirm", body={"token": args.get("token", ""), "confirm": True})
        return {"error": f"unknown tool {name}"}


def chat(base_url, api_key, model, messages, reasoning_effort=None):
    payload = {"model": model, "messages": messages, "tools": TOOLS}
    if reasoning_effort:
        # Some reasoning models (e.g. GPT-6 on Azure chat completions) only allow tools with "none".
        payload["reasoning_effort"] = reasoning_effort
    request = urllib.request.Request(
        base_url.rstrip("/") + "/chat/completions",
        data=json.dumps(payload).encode(),
        # Bearer for OpenAI-style hosts, api-key for Azure OpenAI's v1 endpoint.
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}", "api-key": api_key})
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            return json.loads(response.read())["choices"][0]["message"]
    except urllib.error.HTTPError as exc:
        raise SystemExit(f"model endpoint returned {exc.code}: {exc.read().decode(errors='replace')[:500]}")


def run(question, wardatlas, llm, history=None, log=print):
    """Answer one question. Returns (answer, trace); history carries earlier turns of the session."""
    messages = history if history is not None else [{"role": "system", "content": SYSTEM_PROMPT}]
    messages.append({"role": "user", "content": question})
    trace = []
    for _ in range(MAX_STEPS):
        message = llm(messages)
        calls = message.get("tool_calls") or []
        messages.append({k: v for k, v in message.items() if k in ("role", "content", "tool_calls")})
        if not calls:
            return message.get("content") or "", trace
        for call in calls:
            name = call["function"]["name"]
            try:
                args = json.loads(call["function"].get("arguments") or "{}")
            except json.JSONDecodeError:
                args = {}
            log(f"  -> {name}({', '.join(f'{k}={v}' for k, v in args.items())})")
            result = wardatlas.call(name, args)
            trace.append({"tool": name, "args": args})
            messages.append({"role": "tool", "tool_call_id": call["id"],
                             "content": json.dumps(result)[:MAX_TOOL_CHARS]})
    return "Stopped after the step limit without a final answer.", trace


def ask_terminal(summary):
    if not sys.stdin.isatty():
        return False
    return input(f"\n{summary} [y/N] ").strip().lower() == "y"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("question", nargs="*", help="one question; omit for an interactive session")
    parser.add_argument("--app-url", default=os.environ.get("WARDATLAS_APP_URL", LIVE_DEMO),
                        help="WardAtlas app to query (default: the public live demo)")
    parser.add_argument("--llm-base-url", default=os.environ.get("WARDATLAS_AGENT_BASE_URL", "https://api.openai.com/v1"))
    parser.add_argument("--model", default=os.environ.get("WARDATLAS_AGENT_MODEL"))
    parser.add_argument("--reasoning-effort", default=os.environ.get("WARDATLAS_AGENT_REASONING_EFFORT"),
                        help="optional reasoning_effort sent to the model, e.g. none")
    args = parser.parse_args(argv)
    api_key = os.environ.get("WARDATLAS_AGENT_API_KEY") or os.environ.get("OPENAI_API_KEY") or "none"
    if not args.model:
        parser.error("set WARDATLAS_AGENT_MODEL or pass --model (any OpenAI-compatible model with tool calls)")

    wardatlas = WardAtlas(args.app_url, confirm=ask_terminal)
    llm = lambda messages: chat(args.llm_base_url, api_key, args.model, messages, args.reasoning_effort)
    history = [{"role": "system", "content": SYSTEM_PROMPT}]
    questions = [" ".join(args.question)] if args.question else None
    while True:
        if questions is None:
            try:
                question = input("\nwardatlas> ").strip()
            except EOFError:
                return 0
            if not question:
                continue
        else:
            if not questions:
                return 0
            question = questions.pop()
        answer, trace = run(question, wardatlas, llm, history)
        print("\n" + answer)
        print(f"\n[{len(trace)} graph tool call(s): {', '.join(t['tool'] for t in trace) or 'none'}]")


if __name__ == "__main__":
    sys.exit(main())

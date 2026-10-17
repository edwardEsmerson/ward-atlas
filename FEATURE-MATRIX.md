# WardAtlas feature matrix — build checkpoint

This matrix distinguishes implemented product behavior from behavior exercised by the local acceptance checks. All scenario data is synthetic and non-clinical. “Verified” refers only to the local reproducible evidence named here, not a real hospital deployment or validation.

| Capability | Implemented | Verified in this checkpoint | Planned / limitation |
|---|---|---|---|
| FalkorDB outage impact | Live graph path: open incident → unavailable equipment → service → affected departments → explicitly current operating roles; no disconnected decoy path | Original eight-scenario live suite plus app API checks against pinned paths and current role IDs; actual module FalkorDB 6.0.2 | No real hospital data or production integration |
| Provenance / freshness | Source IDs, titles, and observed timestamps accompany every impact path; stale SOP reference and both current versions remain visible | Pinned source IDs/titles/timestamps compared to `tests/expected-results.json`; desktop API/browser assertions | No automated source ingestion or general document version parser |
| Conflicting knowledge | SOP-OPS-9 V2/V3 disagree on escalation; conflict remains unresolved and `authoritative_claim` stays null | API and browser checks require both role claims and the unresolved badge; no claim winner | No write-back workflow to reconcile source-of-truth documents |
| Source visibility | Fixed `operations` retrieval scope; inaccessible graph branch is excluded from both impact and visual graph API; unsupported scope gets generic denial | Restricted-source fixture injected into the owned graph; before/after API and visual-graph output must match; denied identifiers/text are checked | Demo scope is one synthetic role; this is not a general-purpose authorization system |
| Desktop/mobile control room | Local responsive dashboard, relationship-path map, provenance cards, facts/inference/missing-evidence groups, SOP conflict panel | Browser interaction assertions at desktop/mobile viewport; `/tools/browser-smoke.py` captures screenshots and trace under `/workspace/evidence/wardatlas/ui/` | No hosted deployment, authentication, or public exposure |
| Handover proposal | Deterministic graph-backed template proposes evidence-linked next-shift follow-ups; preview held in memory | API and browser prove preview is absent from persisted action list/disk; false confirmation rejected; explicit in-app confirmation persists exactly one local-only action | Product model route is **not configured**; output is not a model-backed agent. No messages, calls, tickets, or external writes |
| Scenario reset | In-app reset reseeds only the app's uniquely configured empty graph; later reset can clear it only after this process seeded it from deterministic operations fixtures | API/browser reset paths compared to pinned paths; hidden adversarial branch removed; separate guard test proves a preexisting unowned graph is refused and preserved | No branching simulation or event-replay timeline in this slice |
| Source revision / dependency review CLI | New strict synthetic CLI appends an effective-dated dock roster source/assignment to an explicitly owned FalkorDB graph; source provenance history remains append-only; explicit citations to superseded evidence wake handover/ticket review candidates | Eight pinned live scenarios on actual FalkorDB 6.0.2; exact before/after paths, source provenance, explicit queue, lexical decoy, conflict/ACL, idempotence, edge removal, offset/invalid-time checks, and full semantic snapshot equality across reconnect. The verifier also injects a `source_versions=[]` mutant and requires its comparator to reject it | **CLI-only; not integrated into the current UI.** The app/fixture/UI do not invoke this module. Only the fixed synthetic fixture is accepted; coverage is `explicit_edges_only`; unlinked artifact freshness is unknown |

## Current evidence

- Original graph contract: `/workspace/evidence/wardatlas/falkordb-scenarios.json` and clean source-checkout reproduction `/workspace/evidence/wardatlas/falkordb-scenarios-clean-checkout.json`.
- Clean source-only integration for this UI slice: `/workspace/evidence/wardatlas/clean-source-integration-be729a6.log`, `/workspace/evidence/wardatlas/falkordb-scenarios-clean-final.json`, and `/workspace/evidence/wardatlas/ui-clean-source/wardatlas-workflow.json`.
- UI workflow contract: `/workspace/evidence/wardatlas/ui/wardatlas-workflow.json`.
- Browser assertions: `/workspace/evidence/wardatlas/ui/browser-assertions.json`.
- Desktop/mobile screenshots and Playwright trace: `/workspace/evidence/wardatlas/ui/desktop.png`, `mobile.png`, and `trace.zip`.
- The workflow report records the owned bind addresses, exact FalkorDB runtime module, per-assertion results, local-only side-effect policy, and cleanup status. A green build alone is not treated as product success.
- Source-change CLI contract: `docs/source-change-cli.md`; independent oracle: `tests/source-change-expected-results.json` plus `tests/source-change-adversarial-matrix.json`; live evidence is written beneath `/workspace/evidence/wardatlas/source-change-build/$WORKSHOP_LANE/`. The source-change behavior is **CLI-only**, not current UI integration.

## Deferred and risk

- No broad ingestion, event replay, real roster, hosted account, real staff schedule, clinical workflow, authentication, or external model/API is included.
- These fixtures are for a local hackathon operations demo, not a validated hospital system or medical device. No HIPAA/GDPR or real-hospital validation claim is made.
- Competition eligibility risk remains: official rules require participant technical understanding and meaningful contribution; this autonomous artifact does not certify those requirements for the owner.

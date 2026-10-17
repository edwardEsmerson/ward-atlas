# WardAtlas

WardAtlas is a local synthetic hospital-operations control room for the Company Brain track. It uses actual FalkorDB queries to trace a fictional equipment outage through a service to impacted departments and explicitly current operating roles, preserve source provenance, reveal a stale SOP reference and unresolved conflict, and exclude inaccessible evidence. Its integrated shift board keeps the INC-207 dock roster revision separate from INC-204 conveyor event replay: effective-dated ownership, explicit-edge review candidates, observed restoration, and a forked hypothetical drill are inspectable at pinned times. The responsive UI separates source facts, graph inference and missing evidence. It can prepare a source-linked shift handover, but writes it to local app state only after an explicit confirmation click. It does not send messages, make calls, create tickets, or perform external writes. Fixtures are invented and non-clinical; WardAtlas is not a validated hospital system or medical device.

## Clean setup and verification

Requirements: Python 3.10+, an existing `redis-server` executable, Node.js and the workshop's local Playwright browser for browser acceptance, and outbound access to the public Docker Hub OCI registry on the first run from a source-only checkout. No Docker daemon/socket, Python package install, account, credentials, `sudo`, or host service change is used. The database binary and provenance receipt are installed under ignored `.local/` only.

The live verifier automatically bootstraps the pinned official `falkordb/falkordb:6.0.2` Linux/amd64 module when `.local/falkordb/` is absent, verifies the OCI index/manifest/layer digests and fixed module SHA-256, and validates the resulting provenance receipt before launch. It uses at most three network attempts with short backoff for transient registry failures; download or provenance errors fail the check truthfully without starting a database or falling back to a fake. A present but altered module/receipt fails closed. `python3 scripts/install_falkordb_module.py` remains available for explicit prefetching but is not required to warm a fresh checkout.

Choose both ports from the current worker's allocated private range; never probe or reuse an occupied listener. For example, the assigned `WORKSHOP_PORT_BASE` is the app port and the next port is the database port:

```sh
: "${WORKSHOP_PORT_BASE:?set the allocated private port base}"
: "${WORKSHOP_PORT_SPAN:?set the allocated private port span}"
APP_PORT="$WORKSHOP_PORT_BASE"
DB_PORT="$((WORKSHOP_PORT_BASE + 1))"
test "$((DB_PORT - WORKSHOP_PORT_BASE))" -lt "$WORKSHOP_PORT_SPAN"
```

Run the independent expected-results checks and then the live integration suite directly from a fresh source-only checkout:

```sh
python3 scripts/verify_expected_results.py --expected tests/expected-results.json --matrix tests/adversarial-matrix.json --require-six-scenarios
python3 scripts/verify_falkordb_live.py --require-live --fail-on-zero --fresh-owned-graph --db-port "$DB_PORT" --empty-reseed-idempotence-reconnect --report /workspace/evidence/wardatlas/falkordb-scenarios.json --verify-morning-status
```

The live verifier starts its own `redis-server` with the verified module, binds only to `127.0.0.1:$DB_PORT`, and uses a fresh unique graph. If that address cannot be bound, it stops without connecting to the occupant. It deletes only its owned graph and stops its own process. The JSON report contains runtime module provenance/version, scenario assertions, read-only CPU/memory facts, owned-process RSS samples and individual command timings. The report is evidence for that single run, not a generalized benchmark.

Run the complete desktop/mobile app workflow check (it starts its own real FalkorDB and loopback UI, tests the live API and browser, saves evidence, then stops both owned processes):

```sh
python3 scripts/verify_hospital_workflow.py --launch-owned-app --loopback-only --app-port "$APP_PORT" --require-live-falkordb --db-port "$DB_PORT" --fail-on-zero --assert-api --assert-browser --assert-provenance --assert-permissions --assert-confirmation --browser-smoke-tool /tools/browser-smoke.py --desktop-mobile --verify-morning-status-and-feature-matrix --evidence-dir /workspace/evidence/wardatlas/ui
```

The latest exact results and the honest implemented/verified/planned feature matrix are in [`MORNING-STATUS.md`](MORNING-STATUS.md) and [`FEATURE-MATRIX.md`](FEATURE-MATRIX.md). `scripts/wardatlas_app.py` is the local app entrypoint when an explicitly selected, empty, unique FalkorDB graph is already running (use a name such as `wardatlas_ui_<unique-id>`); it refuses to reset nonempty data it did not seed. The workflow verifier above is the reproducible, isolated demo/evidence path.

## Pinned behavior

`tests/expected-results.json` is the original eight-outcome oracle for the current outage flow. The independent shift-board oracle is `tests/shift-board-expected-results.json`; `tests/shift-board-adversarial-matrix.json` maps all eight integrated outcomes to nonzero checks. Run `python3 scripts/verify_shift_board_contract.py` with the four pinned planner packet paths (see the operator-approved command) to verify the immutable JSON/matrix mapping without consulting candidate behavior. The live UI verifier applies that oracle to actual FalkorDB results, including cold empty, INC-207's 20:29/20:30 ownership boundary and explicit-edge queue, INC-204's 18:30/19:30 replay and 18:50 branch, cross-case isolation, restricted-source invariance, local confirmation, idempotent reseed/reconnect, and owned cleanup. Its browser run includes the cold/empty view and responsive board. All entities and sources are synthetic. No patient data, clinical advice, real hospital integration, or external writes are included.

## Shift-board graph walkthrough

- **Dock ownership:** `Incident(INC-207) -[:AFFECTS]-> Equipment -[:SUPPORTS]-> Service -[:SERVES]-> Department -[:OWNED_BY]-> Role -[:EVIDENCED_BY]-> Evidence`. The ownership relationship has a half-open `[effective_from, effective_to)` interval and an explicit source ID. `SRC-DOCK-ROSTER-R2` is observed at 20:18Z, effective at 20:30Z, and points to R1 with `SUPERSEDES`; the 20:29 and 20:30 queries therefore change only the final role node.
- **Review candidates:** `Handover/Ticket -[:CITES_SOURCE]-> R1 <-[:SUPERSEDES]- R2`. The query requires both edges, so `HND-DOCK-2020` and `TKT-DOCK-88` are returned but the lexical-only handover is not. Historical role/source properties remain on the handover; missing links are reported as unknown rather than current.
- **Conveyor replay:** observed `StateEvent` nodes connect from INC-204 through `HAS_STATE_EVENT` and to their `Evidence` via `EVIDENCED_BY`. The API selects the latest source-backed event at or before the requested UTC time; the separate multi-hop impact query returns connected service/department/current-role paths only while that event says unavailable. The `ScenarioBranch(BR-C4-RESTORE-DRILL)` relation points only to its hypothetical 18:45Z event, leaving the observed event history unchanged. Branch reset deletes only that event/branch overlay.
- **Access and side effects:** all traversed nodes, relationships and source records are filtered to `operations`; the restricted fixtures are deliberately connected adversarially but do not change operations results. `/api/shift-board?scope=operations&dock_at=...&conveyor_at=...&branch_at=...` is read-only. Reset and handover confirmation remain separate local endpoints; confirmation writes one synthetic JSON action only after an explicit click.

# WardAtlas

WardAtlas is a local synthetic hospital-operations control room for the Company Brain track. It uses actual FalkorDB queries to trace a fictional equipment outage through a service to impacted departments and explicitly current operating roles, preserve source provenance, reveal a stale SOP reference and unresolved conflict, and exclude inaccessible evidence. The responsive UI separates source facts, graph inference and missing evidence. It can prepare a source-linked shift handover, but writes it to local app state only after an explicit confirmation click. It does not send messages, make calls, create tickets, or perform external writes. Fixtures are invented and non-clinical; WardAtlas is not a validated hospital system or medical device.

## Clean setup and verification

Requirements: Python 3.10+, an existing `redis-server` executable, Node.js and the workshop's local Playwright browser for browser acceptance, and outbound access to the public Docker Hub OCI registry on the first run from a source-only checkout. No Docker daemon/socket, Python package install, account, credentials, `sudo`, or host service change is used. The database binary and provenance receipt are installed under ignored `.local/` only.

The live verifier automatically bootstraps the pinned official `falkordb/falkordb:6.0.2` Linux/amd64 module when `.local/falkordb/` is absent, verifies the OCI index/manifest/layer digests and fixed module SHA-256, and validates the resulting provenance receipt before launch. It uses at most three network attempts with short backoff for transient registry failures; download or provenance errors fail the check truthfully without starting a database or falling back to a fake. A present but altered module/receipt fails closed. `python3 scripts/install_falkordb_module.py` remains available for explicit prefetching but is not required to warm a fresh checkout.

Run the independent expected-results check and then the live integration suite directly from a fresh source-only checkout:

```sh
python3 scripts/verify_expected_results.py --expected tests/expected-results.json --matrix tests/adversarial-matrix.json --require-six-scenarios
python3 scripts/verify_falkordb_live.py --require-live --fail-on-zero --fresh-owned-graph --db-port 18732 --empty-reseed-idempotence-reconnect --report /workspace/evidence/wardatlas/falkordb-scenarios.json --verify-morning-status
```

The live verifier starts its own `redis-server` with the verified module, binds only to `127.0.0.1:18732`, and uses a fresh unique graph. If that address cannot be bound, it stops without connecting to the occupant. It deletes only its owned graph and stops its own process. Choose a different explicitly unused high port with `--db-port` if necessary. The JSON report contains runtime module provenance/version, scenario assertions, read-only CPU/memory facts, owned-process RSS samples and individual command timings. The report is evidence for that single run, not a generalized benchmark.

Run the complete desktop/mobile app workflow check (it starts its own real FalkorDB and loopback UI, tests the live API and browser, saves evidence, then stops both owned processes):

```sh
python3 scripts/verify_hospital_workflow.py --launch-owned-app --loopback-only --app-port 18731 --require-live-falkordb --db-port 18732 --fail-on-zero --assert-api --assert-browser --assert-provenance --assert-permissions --assert-confirmation --browser-smoke-tool /tools/browser-smoke.py --desktop-mobile --verify-morning-status-and-feature-matrix --evidence-dir /workspace/evidence/wardatlas/ui
```

The latest exact results and the honest implemented/verified/planned feature matrix are in [`MORNING-STATUS.md`](MORNING-STATUS.md) and [`FEATURE-MATRIX.md`](FEATURE-MATRIX.md). `scripts/wardatlas_app.py` is the local app entrypoint when an explicitly selected, empty, unique FalkorDB graph is already running (use a name such as `wardatlas_ui_<unique-id>`); it refuses to reset nonempty data it did not seed. The workflow verifier above is the reproducible, isolated demo/evidence path.

## Pinned behavior

`tests/expected-results.json` is the outcome oracle, authored before graph-query behavior. `tests/adversarial-matrix.json` maps each of the eight database scenarios to explicit nonzero assertions. The live suite covers an empty graph, exact multi-hop outage ownership and provenance, disconnected decoy exclusion, equipment-state counterfactual with rollback, stale reference plus unresolved current SOP conflict, denied-source non-disclosure, idempotent reseeding, and reconnect. The UI verifier independently checks the live API, visual path response, permissions, local-only confirmation flow, reset, and desktop/mobile browser behavior against that pinned oracle. All entities and sources are synthetic. No patient data, clinical advice, real hospital integration, or external writes are included.

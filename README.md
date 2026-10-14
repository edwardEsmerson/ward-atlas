# WardAtlas

WardAtlas is a synthetic hospital-operations Company Brain feasibility slice. It uses actual FalkorDB graph queries to trace an equipment outage through a service to affected departments and current operating roles, preserve source provenance, reveal conflicting SOP versions, and exclude inaccessible evidence. The fixtures are invented and non-clinical; this is not a validated hospital system or medical device.

## Clean setup and verification

Requirements: Python 3.10+, an existing `redis-server` executable, and outbound access to the public Docker Hub OCI registry to fetch the pinned FalkorDB shared module. No Docker daemon/socket, Python package install, account, credentials, `sudo`, or host service change is used. The binary and provenance receipt are installed under ignored `.local/` only.

From a fresh checkout, first bootstrap the project-local module:

```sh
python3 scripts/install_falkordb_module.py
```

The installer downloads only the pinned official `falkordb/falkordb:6.0.2` Linux/amd64 module layer, verifies the OCI digests and extracted ELF SHA-256, and writes `.local/falkordb/falkordb.so` plus `PROVENANCE.json`. Run this bootstrap once per clean checkout; validation intentionally fails closed if the module or receipt is missing or altered.

Then run the independent expected-results check and the live integration suite:

```sh
python3 scripts/verify_expected_results.py --expected tests/expected-results.json --matrix tests/adversarial-matrix.json --require-six-scenarios
python3 scripts/verify_falkordb_live.py --require-live --fail-on-zero --fresh-owned-graph --db-port 18732 --empty-reseed-idempotence-reconnect --report ./.local/falkordb-scenarios.json --verify-morning-status
```

The live verifier starts its own `redis-server` with the verified FalkorDB module, binds only to `127.0.0.1:18732`, and uses a fresh unique graph. If that address cannot be bound, it stops without connecting to the occupant. It deletes only its owned graph and stops its own process. Choose a different explicitly unused high port with `--db-port` if necessary. The JSON report contains runtime module provenance/version, scenario assertions, read-only CPU/memory facts, owned-process RSS samples and individual command timings. The report is evidence for that single run, not a generalized benchmark.

The canonical workshop commands and report location are also recorded in [`MORNING-STATUS.md`](MORNING-STATUS.md). That status includes the eight scenario outcomes, prior bootstrap/verifier failures, deferred product work, and the next task.

## Pinned behavior

`tests/expected-results.json` is the outcome oracle, authored before graph-query behavior. `tests/adversarial-matrix.json` maps each of the eight scenarios to explicit nonzero assertions. The live suite covers an empty graph, exact multi-hop outage ownership and provenance, disconnected decoy exclusion, equipment-state counterfactual with rollback, stale reference plus unresolved current SOP conflict, denied-source non-disclosure, idempotent reseeding, and reconnect. All entities and sources are synthetic. No patient data, clinical advice, external writes, UI, or production integrations are included in this slice.

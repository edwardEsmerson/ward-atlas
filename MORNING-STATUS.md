# WardAtlas morning status — 2026-10-15

**Slice:** synthetic, non-clinical outage → equipment → service → departments → current operating roles, with source evidence and SOP conflict handling. No UI, medical recommendation, live hospital data, patient data, or real staff data is included in this bounded build.

## Accepted work in this checkpoint

- Commit `ad8b708` pinned `tests/expected-results.json` and `tests/adversarial-matrix.json` before graph-query behavior was implemented.
- The build adds a small repeatable fixture loader and live FalkorDB verifier; it does not use a fake graph and does not add a UI.
- Public artifact provenance: `falkordb/falkordb:6.0.2`; project-local module SHA-256 is `93bc3c1d484f7fcad45c9409c32034c79e876596b18ce5b03922be007dba889c`. `MODULE LIST` on the owned Redis server reported module name `graph`, runtime module version `60002` (FalkorDB 6.0.2), and the owned server log confirmed that exact module path. **Redis 8.0.5** was the server runtime.

## Exact clean setup and validation commands

```sh
python3 scripts/verify_expected_results.py --expected tests/expected-results.json --matrix tests/adversarial-matrix.json --require-six-scenarios
python3 scripts/verify_falkordb_live.py --require-live --fail-on-zero --fresh-owned-graph --db-port 18732 --empty-reseed-idempotence-reconnect --report /workspace/evidence/wardatlas/falkordb-scenarios.json --verify-morning-status
```

The live verifier automatically bootstraps the pinned public module project-locally when `.local/` is missing, then verifies the OCI index, manifest, layer and fixed module SHA-256 plus provenance receipt before launching its owned database. Network bootstrap uses at most three attempts with short backoff for transient failures; an error is reported and no server starts. Existing but altered module/provenance fails closed. `python3 scripts/install_falkordb_module.py` is an optional manual prefetch, not a clean-checkout prerequisite. No global dependency or Docker daemon is used. `README.md` records this self-contained command sequence.

The live verifier starts only its owned Redis process with the actual FalkorDB module, binds only `127.0.0.1:18732`, uses a unique graph name, deletes only that graph, and stops its own process. If the selected port cannot be bound, it exits without connecting to an occupant.

## Host and resource facts (read-only observations)

- Linux x86_64; `/proc/cpuinfo` reports **Intel Pentium Silver N5030 CPU @ 1.10GHz**; observed flags omit **AVX and AVX2**.
- Passing run `31c2c4b7ece24b168d7f4814762a4f74` host sample: `MemTotal=3,477,356 kB`, `MemAvailable=2,238,288 kB`; the environment does not expose the queried cgroup memory/cpu limit files.
- That owned FalkorDB process reached an **RSS peak (`VmHWM`) of 39,120 KiB**. Its report records 102 graph queries and individual client-side command timing samples (including PING/module discovery), without making generalized benchmark claims. This exact run is preserved at `/workspace/evidence/wardatlas/falkordb-scenarios-success-pre-status-recheck.json`.
- The successful-run sample above is preserved in `/workspace/evidence/wardatlas/falkordb-scenarios-success-pre-status-recheck.json`. Separate harness attempts 8 and 9 passed all eight live scenarios and 27 assertions but exited nonzero on morning-status wording checks, not graph behavior: attempt 8 found a case-sensitive `deferred`/`Deferred` mismatch; attempt 9 found that `RSS` and `Redis 8.0.5` were required terms not present literally. Both failure reports are retained, and the status text plus semantic validation are now repaired. Earlier live verifier defects and their repair evidence are retained in `/workspace/evidence/wardatlas/falkordb-scenarios-attempt-1-failure.json` through `falkordb-scenarios-attempt-7-failure.json`.
- A prior fresh source-only integration caught the actual bootstrap defect: its original live check failed closed because ignored `.local/` was absent, despite the earlier documented manual-install reproduction. Repair now makes the unchanged live verifier command bootstrap automatically through the digest-pinned installer; final clean-source-only run evidence is recorded below.
- Clean tracked-source reproduction passed from `/tmp/opencode/wardatlas-clean-MxCSkS` (snapshot at commit `b9a41ec`): `.local/` was absent before bootstrap, all 9 tracked files were byte-identical to `HEAD`, the installer fetched and verified the pinned module into that snapshot's `.local/`, the oracle passed, and the exact live command passed 8 scenarios / 27 assertions / 102 graph queries. Its module provenance destination points into the clean snapshot; graph and process cleanup both passed. Preserved evidence: `/workspace/evidence/wardatlas/clean-checkout-reproduction.log` and `/workspace/evidence/wardatlas/falkordb-scenarios-clean-checkout.json`.
- Creating the source snapshot with GNU `tar` initially failed for script/test files with `Function not implemented`; the partial extraction was preserved and completed using copies of tracked files. A byte-for-byte comparison against `HEAD` verified the recovery. This was a temporary filesystem/tooling hiccup, not a database or product blocker; details are in the clean reproduction log.

## Scenario and evidence status

- Eight independent synthetic expected outcomes are pinned: cold empty graph; exact multi-hop current ownership; disconnected decoy exclusion; equipment-state counterfactual; stale reference and unresolved current SOP conflict; denied-source non-disclosure; reseed idempotence; reconnect.
- **Live verification previously passed in the primary worktree and a manually bootstrapped clean source snapshot:** all 8 scenarios, 27 nonzero assertions, and 102 live graph queries. Each run deleted its owned graph and stopped its owned process; runtime module, resource samples, and query timings are recorded. The current repair's fresh automatic-bootstrap check is being rerun from source-only tracked files; do not treat the earlier manual-bootstrap snapshot as proof of that behavior.
- Final report: `/workspace/evidence/wardatlas/falkordb-scenarios.json`.
- Failures remain preserved; no scenario is skipped and no expected value is relaxed.

## Deferred work and blockers

- Deferred: read-only API/browser workflow, visual graph explorer, dashboard, handover/action drafting, timeline/replay and mobile/desktop evidence. This task is restricted to the fixture and live-query feasibility slice; those features remain future work after live database feasibility.
- No host/database blocker was encountered; the documented bootstrap chain is now reproduced from a clean tracked-source snapshot.
- Competition eligibility risk: official rules require participant technical understanding and meaningful contribution; autonomous AI output alone may be rejected. This status does not claim to satisfy that rule for the owner.

## Next

The documented three-command setup has now passed from a clean tracked-source snapshot with no `.local/` initially. Next, integrate the verified graph workflow behind a minimal read-only API and synthetic outage briefing; then add browser/API and desktop/mobile evidence while keeping restricted-source non-disclosure and provenance checks intact.

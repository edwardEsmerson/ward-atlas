# Role desk implementation / reproduction

Pre-behavior contract commit: `6de0198`. Three original oracle files remain
hash-pinned and unchanged. Their JSON pointers are in `expected-results.json`.
This is builder evidence, not supervisor acceptance or human eligibility proof.

```
python3 -m unittest tests.test_role_desk
python3 scripts/verify_role_desk.py --all --require-live-falkordb --desktop-mobile --fail-on-zero
```

The verifier bootstraps the existing digest-pinned FalkorDB 6.0.2 module under
project-local `.local` from the source-only checkout. It reuses the accepted
Python/RESP runtime and installer; no Docker socket, account, model or host
change. Python, redis-server, Node and the workshop Playwright browser are the
existing prerequisites. Optional `--db-port`, `--app-port` accept explicit free
loopback ports; otherwise configured lane ports or ephemeral ports are chosen.
Reports default to `$WORKSHOP_RUN_DIR/role-desk/` (project-local fallback),
overwrite only owned outputs, and include real module provenance and cleanup.
One browser runs at a time. No preparation plans or another run's evidence are read.

Visit `/roles` from Shift control. Select a role using the native keyboard
control. Conveyor `as_of` defaults to October 14 18:30Z; 19:30Z shows the 19:15Z
observed restoration with no active responsibilities and retained ownership
witnesses. Dock has independent effective/knowledge clocks; the crossed pair is
unknown, not no work. This is a bounded incident context, not a common shift or
hospital-wide workload count. A selected role returns only its witnessed path.

API examples:

```
GET /api/role-responsibilities?context=conveyor&role=ROLE-STERILE-OPS-COORD&as_of=2026-10-14T18:30:00Z
GET /api/role-responsibilities?context=dock&effective_at=2026-10-14T20:30:00Z&known_at=2026-10-14T20:19:00Z&role=ROLE-DOCK-SWING
```

All fields are singleton and bounded; dock requires both clocks. Discovery omits
`role`. Roles come only from visible full ownership paths; selected queries add
`r.id = $role` within FalkorDB. Only `GRAPH.RO_QUERY` is used. Accepted existing
Cypher/fixtures are imported, not rewritten. ACL predicates cover all nodes,
edges and sources. Source occurrence cards preserve ownership-edge clocks,
including the same roster source's original 18:04Z and 18:05Z observations.
Discovery/selection share the app's reentrant graph lock, not DB transaction
isolation. Arbitrary/denied/unestablished role IDs, missing witnesses and graph
failure share one exact generic unknown body with no input echo. Malformed
selectors and duplicate fields return generic 400 before a service read.

## Failures and repairs retained truthfully

- First browser check compared JSON object insertion order: failed despite an
  equal unknown body. Repaired the checker to use deep object equality; no
  literal value or assertion was removed. Failure receipt: `first-failure.json`.
- Next check caught an incorrect candidate guard on roster Evidence.observed_at:
  that source intentionally carries observation times on OWNED_BY occurrences,
  not its shared source node. Removed only that invalid candidate guard; retained
  edge occurrence cutoff, all ACL checks and exact original source expectations.
  Failure receipt: `second-failure.json`. The restoration assertion reads the
  original 19:15Z event/source boundary, separately from the requested 19:30Z.
- These were product/test implementation defects, not missing authority or a
  hindering harness defect. Both failures cleaned all owned graph/process/state
  resources. Original oracles and the pinned nonzero matrix were never weakened.

No clinical advice, person scoring, contacts, new data, model call or write is
introduced. No completeness, safety, resolution or real-hospital claim is made.

## Final builder receipt

Implementation `04bf5cc` passed both original commands in the builder and a fresh
source-only clone with no preinstalled `.local/falkordb`. Each run passed 16
focused tests and all 8 live groups / 53 mapped assertions. Each repeat browser
report passed 99 assertions with desktop/mobile screenshots, keyboard evidence,
escaping, loading/error recovery and an abort-ignoring response race. All five
owned cleanup flags passed in both runs; the source-only checkout stayed clean.
Actual module version: 60002; verified module SHA-256:
`93bc3c1d484f7fcad45c9409c32034c79e876596b18ce5b03922be007dba889c`.

Builder reports: `$WORKSHOP_RUN_DIR/role-desk/live.json`, `repeat.json` and
`repeat/{desktop,mobile}.png`. Source-only reports for this task are in
`$WORKSHOP_RUN_DIR/source-only/role-desk/`; unit logs are `role-desk-unit.log`
and `source-only/unit.log`. Failure receipts remain alongside the builder report.
Latest origin/main was merged before final build checks and rechecked before
this evidence-only receipt. This records builder reproduction, not supervisor
acceptance; no source oracle, acceptance requirement or concurrent mount changed.

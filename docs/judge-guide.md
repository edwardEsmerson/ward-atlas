# Judge guide — supplied-server WardAtlas

**Track: Company Brain.** A fictional hospital's outage crosses equipment, services, departments and roster ownership; SOP versions disagree. WardAtlas makes the impact/owner/evidence path inspectable rather than guessing a contact or procedure. All data is synthetic and non-clinical. No real messages or integrations are used.

## One app-only command

Have Python 3.10+ and an existing, reachable FalkorDB **6.x or newer**, with `MODULE LIST`, graph commands and access to create/delete a new graph. The app is standard-library Python; no pip installation is needed. No credentials, accounts or external model are needed or accepted by this launcher. Do not use a real hospital database.

```sh
python3 scripts/wardatlas_attach.py --db-host 127.0.0.1 --db-port 6379
```

Use the supplied server's explicit hostname and port instead of the example. Open the printed `WARDATLAS_READY` URL. The OS allocates an unused loopback app port. An explicit `--app-port YOUR_FREE_PORT` is also accepted; an occupied port fails without connecting to its occupant or the DB. App binding is always `127.0.0.1`; database host configuration does not expose the app publicly.

Every launch creates a UUID/lane-qualified `wardatlas_attach_…` graph and private `.local/attachments/…` directory. It checks module identity before creating/seeding that graph, refuses a generated-name collision, and never enumerates or resets other graphs. Ctrl-C (also SIGTERM) closes the foreground app and removes **only its own graph and state**. No background app/database daemon is launched. Existing database processes, configuration and unrelated graphs survive. Demo actions disappear at exit by design. SIGKILL, host failure or a disconnected server can prevent cleanup; errors are reported, not represented as successful cleanup. There is no recovery supervisor or automatic deletion of old runs.

### If you do not already have FalkorDB

Judges may run the official server themselves in a separate terminal, for example:

```sh
docker run --rm -p 127.0.0.1:6379:6379 falkordb/falkordb:6.0.2
```

WardAtlas does **not** invoke Docker. Do not run the image against existing data; the example uses a fresh container without host data mounts. Other recent versions pass the identity gate but only the digest-pinned 6.0.2 Linux/amd64 artifact is covered by this attachment verifier.

The existing **project-local Linux/amd64 alternative** remains available, without Docker or a managed launcher. It requires an existing `redis-server` executable and outbound public registry access for first download:

```sh
python3 scripts/install_falkordb_module.py
# In a separate terminal, choose an explicit free DB port and keep this foreground:
DB_PORT=6379
redis-server --bind 127.0.0.1 --port "$DB_PORT" --save "" --appendonly no \
  --daemonize no --loadmodule "$PWD/.local/falkordb/falkordb.so"
# In the app terminal, supply that same DB port to wardatlas_attach.py.
```

The existing installer verifies the pinned official image/layer and module checksums, installing under ignored `.local/` only. It does not install or change a host service. Stop a server you started yourself separately after exiting the app. The historical [demo runbook](demo-runbook.md) and original README acceptance commands are preserved; neither is replaced by a new managed database launcher.

## A short, reproducible product route

1. The seeded home shows **INC-204**, two graph-derived impact paths, current operating roles and timestamped evidence. Inspect `Incident → Equipment → Service → Department → Role` in the impact map.
2. Inspect stale **SOP-OPS-9 V1** beside conflicting **V2/V3** claims. Neither claim is promoted into the roster owner or an authoritative procedure.
3. Compare the conveyor's **18:30Z** outage and **19:30Z** observed restoration; restoration does not resolve the independent SOP disagreement. The dock roster is a separate synthetic case.
4. Preview a handover, inspect its exact graph/evidence snapshot, then explicitly confirm a local-only action. No external messages or tickets are sent.
5. Explore [Ask WardAtlas](../README.md#ask-wardatlas), [knowledge diff](../README.md#explain-why-two-graph-answers-differ), and the Clock atlas link on home. These are present in the repository; attachment acceptance below checks home/API rendering, not every feature's original semantic suite.

See [FalkorDB query/model/schema walkthrough](falkordb-query-walkthrough.md) for actual source-bound queries and limitations. The private recording/transcript belongs to the separate media delivery; it is not required to start this app and is never an oracle for these checks.

## Repeatable attachment acceptance

In addition to runtime Python, verification requires an existing `redis-server`, Node.js, Playwright/Chromium and first-run public registry access for the digest-pinned module. In the workshop the existing local browser is discovered automatically. Outside it, install Playwright project-locally and its Chromium browser, or set `PLAYWRIGHT_MODULE` to an existing local package. Browser dependencies are for checks, not app startup.

```sh
npm install --no-save --package-lock=false playwright
npx playwright install chromium
python3 -m unittest tests.test_existing_server
python3 scripts/verify_existing_server.py --all --require-live-falkordb --assert-server-and-sentinel-survive --desktop-mobile --fail-on-zero
```

Commit candidate edits before the behavioral command: it tests `git archive HEAD` in an empty project-local source snapshot, not uncommitted changes or warmed dependencies. The verifier bootstraps the established digest-pinned actual FalkorDB, starts only verifier-owned loopback processes on free lane/ephemeral ports, creates an unrelated sentinel graph, and runs the README attach command **twice**, including `localhost` host plumbing. It checks fresh API reads against the independently pinned original graph oracle; desktop/mobile live home rendering and screenshots; occupied-app-port non-contact; plain-Redis rejection; partial-seeding failure; run-unique graph/state; foreground SIGINT cleanup; and sentinel/server survival after every lifecycle case. It then removes only verifier-owned resources and stops its own servers. It never probes unrelated services or copies preparation files or mutable media evidence.

Reports overwrite their own named outputs under `$WORKSHOP_RUN_DIR/existing-server/`, or `.local/verification/existing-server/` without that environment variable. A failing or zero-case run is not success. Module identity alone is not arbitrary-server certification: runtime asserts a graph module reporting version 6.x+, while this verifier additionally validates fixed module bytes and its owned server's load log.

## Disclosure and eligibility

Substantial implementation, fixtures, tests and documentation were AI-generated. The core workflow uses deterministic graph queries/templates; optional Ask layout composition is not claimed live-model verified. This is not a validated hospital system or medical device, provides no clinical advice and makes no HIPAA/GDPR claim. Participant technical understanding, meaningful human contribution and submission eligibility remain **unverified**; official rules warn that AI-only entries may be rejected. A green automated check is not eligibility certification. Public publishing, video/submission actions and account/payment decisions are separate approvals, not performed by this launcher.

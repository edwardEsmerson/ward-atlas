# WardAtlas demo runbook

This is the short operator path for the four accepted synthetic workflows: the original INC-204 outage trace, its shift-board event/branch view, the SOP-impact witness, and the confirmed handover snapshot. It does not cover other product areas. Start in the repository root. The demo uses two foreground processes; this is not a one-command launcher.

## 90-second story

1. **0–10s — prove the blank is blank.** Open the loopback UI before seeding. It says `empty/unknown`; this means there is no evidence in this new graph, not “no risk,” “no incident,” or “no conflict.” Click **Reset synthetic scenario** to load only the deterministic synthetic fixtures.
2. **10–35s — show the graph, not a text match.** At the observed INC-204 time `2026-10-14T18:30:00Z`, the actual FalkorDB path query returns exactly:

   - `INC-204 → CONVEYOR-C4 → SVC-MATERIAL-TRANSFER → DEPT-CENTRAL-STERILE → ROLE-STERILE-OPS-COORD`
   - `INC-204 → CONVEYOR-C4 → SVC-MATERIAL-TRANSFER → DEPT-MATERIALS-LOGISTICS → ROLE-MATERIALS-DUTY`

   - **Central-sterile path provenance (in order):** `SRC-INC-204` — *Synthetic Incident Register INC-204* — observed `2026-10-14T18:00:00Z`; `SRC-ASSET-C4` — *Synthetic Asset State Ledger* — observed `2026-10-14T18:02:00Z`; `SRC-SERVICE-CATALOG` — *Synthetic Service Dependency Register* — observed `2026-10-14T18:03:00Z`; `SRC-OPS-ROSTER` — *Synthetic Operations Role Roster* — observed `2026-10-14T18:04:00Z`.
   - **Materials-logistics path provenance (in order):** `SRC-INC-204` — *Synthetic Incident Register INC-204* — observed `2026-10-14T18:00:00Z`; `SRC-ASSET-C4` — *Synthetic Asset State Ledger* — observed `2026-10-14T18:02:00Z`; `SRC-SERVICE-CATALOG` — *Synthetic Service Dependency Register* — observed `2026-10-14T18:03:00Z`; `SRC-OPS-ROSTER` — *Synthetic Operations Role Roster* — observed `2026-10-14T18:05:00Z`.
   These timestamps are source observation times, not inferred event times.
3. **35–55s — separate observed time from a drill.** The observed status is `observed-impact`; outage event `EVT-C4-UNAVAILABLE-01` cites source `SRC-ASSET-C4-OUTAGE` at event time `2026-10-14T18:02:00Z`. At observed `2026-10-14T19:30:00Z`, restoration event `EVT-C4-RESTORED-01` from `SRC-ASSET-C4-RESTORE` has status `restored` and event time `2026-10-14T19:15:00Z`; active paths are empty. At `2026-10-14T18:50:00Z`, branch `BR-C4-RESTORE-DRILL` has status `hypothetical-restored` from hypothetical event `EVT-BR-RESTORE-01` at `2026-10-14T18:45:00Z`: branch paths are empty while the mainline remains `observed-impact` with both paths. The branch does not mutate observed state and is not an observed restoration or safety conclusion.
4. **55–72s — show unresolved knowledge honestly.** `SOP-OPS-9-V2` and `SOP-OPS-9-V3` have explicit `APPLIES_TO` edges to C4 and an unresolved V2 `SOP_CONFLICT` V3 edge. Their claims are labeled **unverified SOP claim**, not owners: V2 claims `ROLE-FACILITIES-DUTY` from `SRC-SOP-CURRENT-V2` (*Synthetic SOP-OPS-9 Current Text v2*, observed `2026-10-14T16:00:00Z`); V3 claims `ROLE-MATERIALS-LEAD` from `SRC-SOP-CURRENT-V3` (*Synthetic SOP-OPS-9 Current Text v3*, observed `2026-10-14T16:04:00Z`). Neither wins. Archived `SOP-OPS-9-V1` / `SRC-SOP-ARCHIVE-9` (*Synthetic SOP-OPS-9 Archived Text v1*, observed `2026-01-10T12:00:00Z`) references `CONVEYOR-C3` and is not part of the `CONVEYOR-C4` witness.
5. **72–90s — make a reviewable handover, not an external action.** Preview a handover at `2026-10-14T18:30:00Z`; it carries the same two ordered paths, per-hop source IDs/titles/times, and the explicit unresolved SOP witness. Preview is read-only. Click **Confirm & save locally** only to store one synthetic action in this run's local state. Its generated action ID/time varies by run; no oracle pins a constant action ID. No message, call, ticket, or external write occurs.

## Four-minute technical walkthrough

- **0:00–0:40 — cold graph and reset.** The first requests to `/api/briefing?scope=operations`, `/api/shift-board?scope=operations`, and `/api/sop-impact?scope=operations` query a newly named graph with zero fixtures. Show `empty/unknown`; then use the UI reset, not a manually seeded warm graph.
- **0:40–1:35 — explain the multi-hop result.** `/api/briefing` is backed by the actual FalkorDB `AFFECTS → SUPPORTS → SERVES → OWNED_BY` traversal and explicit `EVIDENCED_BY` sources. The query requires connected operations-visible nodes and edges; a department with no `SERVES` edge is not reachable. Separate observed source facts, graph-derived paths, branch assumptions, and missing/unknown evidence. The two path IDs and exact provenance are pinned above.
- **1:35–2:30 — replay without rewriting history.** `/api/shift-board?scope=operations&conveyor_at=2026-10-14T18:30:00Z&branch_at=2026-10-14T18:50:00Z` returns the 18:02 outage event, both observed paths, and the distinct hypothetical branch. Change the observed time to `2026-10-14T19:30:00Z`: the 19:15 restoration event removes active paths but does not resolve the SOP disagreement. The branch remains hypothetical.
- **2:30–3:15 — inspect the SOP witness.** `/api/sop-impact?scope=operations&as_of=2026-10-14T18:30:00Z` joins the outage to the two C4 SOP versions only through explicit applicability/conflict/claim/evidence relationships. The claims are not roster-derived current ownership. Operations scope does not disclose inaccessible source titles, IDs, counts, existence, paths, or derived facts. A removed/missing witness edge means incomplete/unknown, not “resolved” or “no conflict.”
- **3:15–4:00 — review and confirm.** The handover preview projects that existing read-only SOP-impact response; it does not run a second traversal or write a draft. Confirmation requires an explicit UI action and writes one local `confirmed-actions.json` snapshot under this run's state directory. A later graph change does not rewrite the saved snapshot.

## Start: two terminals, one isolated run

Prerequisites: Python 3.10+, the already-available `redis-server`, Node.js and the workshop's local Playwright browser for the automated browser check. Run from a source checkout. The official FalkorDB `6.0.2` module is installed under ignored `.local/` only when absent; the install helper verifies the OCI index, manifest, layer, module SHA-256, and provenance receipt. No Docker daemon, account, credentials, paid API, external data, or host service is used.

### Terminal A — allocate paths, then run the database in the foreground

```sh
set -eu
umask 077
: "${WORKSHOP_PORT_BASE:?set the allocated private port base}"
: "${WORKSHOP_PORT_SPAN:?set the allocated private port span}"
: "${WORKSHOP_LANE:?set the workshop lane}"
case "$WORKSHOP_LANE" in ''|*[!A-Za-z0-9_-]*) echo 'invalid WORKSHOP_LANE' >&2; exit 2;; esac
test "${#WORKSHOP_LANE}" -le 32
PROJECT_ROOT="$(pwd -P)"
test -f "$PROJECT_ROOT/scripts/wardatlas_app.py"
DB_PORT="$((WORKSHOP_PORT_BASE + 120))"
APP_PORT="$((WORKSHOP_PORT_BASE + 121))"
test "$((DB_PORT - WORKSHOP_PORT_BASE))" -lt "$WORKSHOP_PORT_SPAN"
test "$((APP_PORT - WORKSHOP_PORT_BASE))" -lt "$WORKSHOP_PORT_SPAN"
test "$DB_PORT" -ge 1024 && test "$DB_PORT" -le 65535
test "$APP_PORT" -ge 1024 && test "$APP_PORT" -le 65535
RUN_ID="$(python3 -c 'import uuid; print(uuid.uuid4().hex)')"
RUN_ROOT="$PROJECT_ROOT/.local/demo-runs/${WORKSHOP_LANE}/${RUN_ID}"
DB_DATA_DIR="$RUN_ROOT/db"
STATE_DIR="$RUN_ROOT/state"
RUN_ENV="$RUN_ROOT/run.env"
GRAPH_NAME="wardatlas_ui_${WORKSHOP_LANE}_${RUN_ID}"
EVIDENCE_DIR="/workspace/evidence/wardatlas/demo-runbook/${WORKSHOP_LANE}/${RUN_ID}"
test ! -e "$RUN_ROOT" && test ! -e "$EVIDENCE_DIR"
mkdir -p "$DB_DATA_DIR" "$STATE_DIR" "$EVIDENCE_DIR"
printf 'export PROJECT_ROOT=%q\nexport WORKSHOP_LANE=%q\nexport RUN_ID=%q\nexport RUN_ROOT=%q\nexport DB_DATA_DIR=%q\nexport STATE_DIR=%q\nexport RUN_ENV=%q\nexport GRAPH_NAME=%q\nexport EVIDENCE_DIR=%q\nexport DB_PORT=%q\nexport APP_PORT=%q\n' \
  "$PROJECT_ROOT" "$WORKSHOP_LANE" "$RUN_ID" "$RUN_ROOT" "$DB_DATA_DIR" "$STATE_DIR" "$RUN_ENV" "$GRAPH_NAME" "$EVIDENCE_DIR" "$DB_PORT" "$APP_PORT" > "$RUN_ENV"
MODULE="$PROJECT_ROOT/.local/falkordb/falkordb.so"
RECEIPT="$PROJECT_ROOT/.local/falkordb/PROVENANCE.json"
if test ! -f "$MODULE" || test ! -f "$RECEIPT"; then
  python3 scripts/install_falkordb_module.py
fi
python3 - "$MODULE" "$RECEIPT" <<'PY'
import hashlib, json, pathlib, sys
module, receipt = map(pathlib.Path, sys.argv[1:])
expected = "93bc3c1d484f7fcad45c9409c32034c79e876596b18ce5b03922be007dba889c"
facts = json.loads(receipt.read_text())
root = module.parent.parent.parent.resolve()
assert module.resolve().is_relative_to(root) and receipt.resolve().is_relative_to(root)
assert hashlib.sha256(module.read_bytes()).hexdigest() == expected
assert facts.get("source") == "https://hub.docker.com/r/falkordb/falkordb"
assert facts.get("artifact") == "falkordb/falkordb:6.0.2"
assert facts.get("platform") == "linux/amd64"
assert facts.get("image_index_digest") == "sha256:107455e862a824e45e8e9f5a7fbfdf5e00a08b14e13eae2577c72fd9e3f59ba6"
assert facts.get("platform_manifest_digest") == "sha256:288aaa7d0583c7c15fb28509544c0418186ef064f551bfb477f3df26b1a892fb"
assert facts.get("module_layer_digest") == "sha256:6c11c298608011dfeb77a91598799a97939bed5dbe17a80e7997356d6e81846b"
assert facts.get("module_sha256") == expected
print("Verified official FalkorDB 6.0.2 module SHA-256 and project-local provenance receipt.")
PY
command -v redis-server
printf 'For Terminal B, run: source %q\n' "$RUN_ENV"
redis-server --bind 127.0.0.1 --protected-mode yes --port "$DB_PORT" \
  --dir "$DB_DATA_DIR" --dbfilename demo.rdb --save "" --appendonly no \
  --daemonize no --loadmodule "$MODULE"
```

This database has no persistence; only its foreground process owns the in-memory graph. If the explicit bind fails, stop and choose another port inside the allocated range—do not connect to or stop the occupant. Never bind to `0.0.0.0`.

### Terminal B — run the app in the foreground

Copy the exact `run.env` path printed by Terminal A:

```sh
source /absolute/path/printed/by/terminal-a/run.env
cd "$PROJECT_ROOT"
python3 scripts/wardatlas_app.py --app-port "$APP_PORT" --db-port "$DB_PORT" \
  --graph-name "$GRAPH_NAME" --state-dir "$STATE_DIR"
```

Open `http://127.0.0.1:${APP_PORT}`. Confirm the first screen is cold `empty/unknown`, then click **Reset synthetic scenario** once. Run the 90-second story. Keep both terminals open; the app and database are separate foreground processes.

## Exact owned teardown

1. In Terminal B press **Ctrl-C** and wait for the app to exit.
2. In Terminal A press **Ctrl-C** and wait for its `redis-server` process to exit. Because this private server has persistence disabled, its in-memory graph disappears with that process; do not run `FLUSHDB`, a broad graph delete, `pkill`, or `killall`.
3. In Terminal A, remove only this run's database and confirmed-action state. Preserve the evidence directory:

   ```sh
   source "$RUN_ENV"
   rm -rf -- "$DB_DATA_DIR" "$STATE_DIR"
   rm -f -- "$RUN_ENV"
   rmdir -- "$RUN_ROOT" 2>/dev/null || true
   test -d "$EVIDENCE_DIR"
   ```

No unrelated process, graph, data directory, or evidence is touched.

## Truthful disclosure

WardAtlas is a deterministic actual-FalkorDB query and template demo; no AI model is called at runtime. AI assistants helped author the code, tests, and documentation. It is a **Company Brain** track entry using only fictional synthetic hospital-operations data, not patient/real-staff data. It is non-clinical and is not a validated hospital system or medical device. No demand, savings, outage-reduction, patient-outcome, compliance, deployment, or real-hospital impact claim is made. Eligibility risk remains: official rules require participant technical understanding and meaningful contribution; this autonomous work does not certify those requirements on the owner's behalf.

## Oracle and browser evidence

The exact values above are bound to the unchanged original outage, shift-board, SOP-impact, and handover oracles and their nonzero adversarial mappings. From the repository root, run static contracts and focused story checks:

The unchanged contract scripts also require the original pinned decision-packet files at their exact absolute `/workspace/worktrees/planner-*/plans/` paths. If those workshop artifacts are unavailable, the static contract command fails closed; do not substitute candidate output or regenerate expectations. The private UI startup uses the source checkout, existing `redis-server`, and digest-pinned module independently of those planning packets.

```sh
python3 -m unittest tests.test_demo_runbook
python3 scripts/verify_demo_runbook.py --static-contract --fail-on-zero
```

After those checks, the actual-FalkorDB/API/desktop-mobile run is separately reproducible with `python3 scripts/verify_demo_runbook.py --live --desktop-mobile --fail-on-zero --assert-owned-cleanup`. It runs the accepted original+shift-board, SOP-impact, and handover live suites sequentially, allocates ports only from `WORKSHOP_PORT_BASE`/`WORKSHOP_PORT_SPAN`, writes lane/run-unique reports under `/workspace/evidence/wardatlas/demo-runbook/`, and verifies each suite's owned graph/process/state cleanup. It is a verifier, not the two-terminal demo launcher. The accepted app, query and oracle behavior is unchanged by this runbook.

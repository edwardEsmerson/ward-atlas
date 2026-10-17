# Synthetic roster source-change CLI

`scripts/wardatlas_source_changes.py` is a deliberately narrow, graph-backed CLI. It accepts only the fixed fictional dock roster revision in its code; it is not a document parser or external connector. It requires an existing **actual FalkorDB 6.0.2** server listening on `127.0.0.1` at an explicitly selected port in the current workshop lane. It never starts a server, contacts a non-loopback host, sends a message, calls anyone, creates a ticket, or writes to another service.

## Reproducible live check

From a fresh source-only checkout, the live verifier below bootstraps the digest-pinned public FalkorDB module project-locally when `.local/` is absent, starts one verifier-owned server on loopback, creates a unique `WORKSHOP_LANE` graph, runs the real CLI twice, checks all eight pinned scenarios, deletes only its graph, and stops only its server:

```sh
python3 scripts/verify_source_change_oracle.py \
  --expected tests/source-change-expected-results.json \
  --matrix tests/source-change-adversarial-matrix.json \
  --require-eight-scenarios \
  --pinned-plan /workspace/worktrees/planner-9b1a51761889446c8b5525bde02bc5d6/plans/planner-9b1a51761889446c8b5525bde02bc5d6.md

python3 scripts/verify_source_change_live.py \
  --require-live --fail-on-zero --fresh-owned-graph \
  --db-port "$((WORKSHOP_PORT_BASE + 1))" \
  --expected tests/source-change-expected-results.json \
  --matrix tests/source-change-adversarial-matrix.json \
  --verify-morning-status-and-feature-matrix \
  --evidence-dir "/workspace/evidence/wardatlas/source-change-build/${WORKSHOP_LANE}"
```

The port is mandatory and the live verifier rejects a port outside `WORKSHOP_PORT_BASE .. WORKSHOP_PORT_BASE + WORKSHOP_PORT_SPAN - 1`. It performs a bind-only preflight before starting its own process and never connects to an occupant. Evidence records the runtime module/version/provenance, query and assertion counts, process resource observations, CLI output, and owned graph/process cleanup.

## Run the CLI against an explicitly owned graph

For an already running local FalkorDB 6.0.2 instance, choose an unused lane port and a new graph name containing the active lane plus a unique suffix. Do not reuse an app/test graph or one owned by another run.

```sh
export WORKSHOP_LANE WORKSHOP_PORT_BASE
GRAPH_NAME="wardatlas_source_${WORKSHOP_LANE}_demo_$(python3 -c 'import uuid; print(uuid.uuid4().hex[:12])')"

python3 scripts/wardatlas_source_changes.py \
  --db-port "$WORKSHOP_PORT_BASE" \
  --graph-name "$GRAPH_NAME" \
  --seed-synthetic \
  --at '2026-10-14T20:29:00Z'

python3 scripts/wardatlas_source_changes.py \
  --db-port "$WORKSHOP_PORT_BASE" \
  --graph-name "$GRAPH_NAME" \
  --seed-synthetic \
  --at '2026-10-14T20:30:00Z'
```

The CLI requires all four arguments; it has **no default database port**. `WORKSHOP_LANE`, `WORKSHOP_PORT_BASE`, and `WORKSHOP_PORT_SPAN` must be present. The graph name must match `wardatlas_source_<WORKSHOP_LANE>_<unique-suffix>`. The CLI checks `MODULE LIST` for runtime module version `60002`, connects only to `127.0.0.1`, and prints one JSON object per invocation.

The JSON includes `as_of`, `role_path`, `source_versions`, `review_candidates`, `coverage_status`, and `unlinked_artifact_status`. The output also includes the selected assignment's provenance, the still-unresolved SOP conflict, and an empty `external_effects` list. At `20:29Z`, the exact role path ends in `ROLE-DOCK-DUTY`; at `20:30Z`, only the final role changes to `ROLE-DOCK-SWING`. Both source versions and their observed/effective timestamps remain inspectable. At/after the new effective time, only explicitly citation-linked `HND-DOCK-2020` and `TKT-DOCK-88` are review candidates. `HND-DOCK-2020` is not rewritten and `HND-DOCK-LEXICAL-ONLY` is excluded. Results state `coverage_status=explicit_edges_only` and `unlinked_artifact_status=unknown`: no missing edge is treated as evidence that an artifact is current.

`--seed-synthetic` is an idempotent `MERGE`-based seed of only the fixed fixture. An empty graph is accepted; a nonempty graph must carry the matching lane/graph ownership marker and have the exact fixture inventory, or the CLI fails closed. It never clears a graph. A graph created by the standalone example remains the operator's owned graph; delete only that exact graph when finished, and only if you still own it. The automated verifier uses a separate unique graph and owns its cleanup.

This increment is **CLI/graph-engine only**. The existing app and UI are unchanged and do not invoke this source-change CLI. No arbitrary source ingestion, real schedule/person, SOP resolution, automatic reassignment, outgoing action, or external write is implemented.

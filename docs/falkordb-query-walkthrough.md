# WardAtlas: the actual accepted FalkorDB queries

For judges reading the Company Brain entry: the graph connects an operational dependency, its accountable role and evidence; a separate knowledge clock explains why an SOP disagreement and a decision can become visible at different times. It is not text similarity dressed as a graph.

Accepted query source: **`b540f032a50d14384602e638abc1ff59f50160cf`**. These are literal returned query strings, not illustrative Cypher; the three function examples use known_at = 2026-10-14T16:05:00Z.

Start with [clean setup](../README.md#clean-setup-and-verification) and the [demo runbook](demo-runbook.md). The runbook covers the outage/shift/SOP/handover story; this appendix additionally explains the decision knowledge replay and its source-observation branch. No private evidence or media is needed to read this appendix.

## 1. Outage → service → department → current role

[`wardatlas_shift_board.py:CONVEYOR_PATH_QUERY`](../scripts/wardatlas_shift_board.py) returns the connected paths, not a precomputed department list:

<!-- query:conveyor-path -->
```cypher
MATCH (i:Incident {id: 'INC-204'})-[aff:AFFECTS]->(e:Equipment {id: 'CONVEYOR-C4'})
      -[sup:SUPPORTS]->(s:Service {id: 'SVC-MATERIAL-TRANSFER'})
      -[serves:SERVES]->(d:Department)-[owns:OWNED_BY]->(r:Role),
      (r)-[roster:EVIDENCED_BY]->(rsrc:Evidence),
      (i)-[iev:EVIDENCED_BY]->(isrc:Evidence),
      (e)-[eev:EVIDENCED_BY]->(esrc:Evidence),
      (s)-[sev:EVIDENCED_BY]->(ssrc:Evidence)
WHERE owns.current = true
  AND i.access = 'operations' AND e.access = 'operations' AND s.access = 'operations'
  AND d.access = 'operations' AND r.access = 'operations'
  AND aff.access = 'operations' AND sup.access = 'operations' AND serves.access = 'operations'
  AND owns.access = 'operations' AND roster.access = 'operations' AND rsrc.access = 'operations'
  AND owns.source_id = rsrc.id
  AND iev.access = 'operations' AND eev.access = 'operations'
  AND sev.access = 'operations' AND isrc.access = 'operations' AND esrc.access = 'operations'
  AND ssrc.access = 'operations'
RETURN i.id AS incident_id, e.id AS equipment_id, s.id AS service_id,
       d.id AS department_id, r.id AS role_id,
       isrc.id AS incident_source_id, isrc.title AS incident_source_title,
       isrc.observed_at AS incident_observed_at,
       esrc.id AS equipment_source_id, esrc.title AS equipment_source_title,
       esrc.observed_at AS equipment_observed_at,
       ssrc.id AS service_source_id, ssrc.title AS service_source_title,
       ssrc.observed_at AS service_observed_at,
       rsrc.id AS ownership_source_id, rsrc.title AS ownership_source_title,
       owns.observed_at AS ownership_observed_at
ORDER BY d.id
```

The conveyor query selects explicitly current OWNED_BY edges, joins owns.source_id = rsrc.id, and returns incident, equipment, service and roster provenance; it does not itself apply a knowledge-time cutoff or select an outage event.

The service layer selects the separately evidenced observed event and exposes active impact paths only while it is unavailable; the 18:30Z outage and 19:30Z restoration are not the 16:04/16:05/16:06Z SOP knowledge clock.

The accepted outage paths at `2026-10-14T18:30:00Z` are:

- `INC-204 → CONVEYOR-C4 → SVC-MATERIAL-TRANSFER → DEPT-CENTRAL-STERILE → ROLE-STERILE-OPS-COORD`
- `INC-204 → CONVEYOR-C4 → SVC-MATERIAL-TRANSFER → DEPT-MATERIALS-LOGISTICS → ROLE-MATERIALS-DUTY`

Each path returns `SRC-INC-204` (*Synthetic Incident Register INC-204*, `2026-10-14T18:00:00Z`), `SRC-ASSET-C4` (*Synthetic Asset State Ledger*, `2026-10-14T18:02:00Z`), `SRC-SERVICE-CATALOG` (*Synthetic Service Dependency Register*, `2026-10-14T18:03:00Z`), and `SRC-OPS-ROSTER` (*Synthetic Operations Role Roster*). The ownership-edge observations are `2026-10-14T18:04:00Z` for central sterile and `2026-10-14T18:05:00Z` for materials logistics; they are not fabricated observation properties on the roster source node. Removing a required dependency does not authorize a lexical replacement path.

## 2. SOP knowledge is not decision authority

[`wardatlas_knowledge_timeline.py:_claim_query`](../scripts/wardatlas_knowledge_timeline.py), generated at the declared cutoff:

<!-- query:timeline-claim -->
```cypher
MATCH (i:Incident {id: 'INC-204'})-[aff:AFFECTS]->(e:Equipment {id: 'CONVEYOR-C4'})-[sup:SUPPORTS]->(s:Service {id: 'SVC-MATERIAL-TRANSFER'}),
      (v:SOPVersion)-[applies:APPLIES_TO]->(e),
      (v)-[claim:CLAIMS_ROLE]->(role:Role),
      (v)-[evidence:EVIDENCED_BY]->(source:Evidence)
WHERE i.access = 'operations' AND e.access = 'operations' AND s.access = 'operations'
  AND v.access = 'operations' AND role.access = 'operations' AND source.access = 'operations'
  AND aff.access = 'operations' AND sup.access = 'operations' AND applies.access = 'operations'
  AND claim.access = 'operations' AND evidence.access = 'operations'
  AND source.observed_at <= "2026-10-14T16:05:00Z"
RETURN v.id AS version_id, claim.id AS claim_edge_id, role.id AS role_id,
       applies.id AS applicability_edge_id, e.id AS equipment_id,
       source.id AS source_id, source.title AS source_title,
       source.observed_at AS source_observed_at,
       evidence.id AS evidence_edge_id
ORDER BY v.id, role.id
```

Claims are discovered only through the incident/equipment/service, APPLIES_TO, CLAIMS_ROLE and EVIDENCED_BY path; a DecisionRecord or CONSIDERS edge never discovers a claim.

[`wardatlas_knowledge_timeline.py:_record_query`](../scripts/wardatlas_knowledge_timeline.py), independently generated at the same cutoff:

<!-- query:timeline-record -->
```cypher
MATCH (root:DecisionRegister {id: "REGISTER-OPS-DECISIONS-01"})-[tracks:TRACKS]->(record:DecisionRecord {id: "DEC-OPS-9-REVIEW-01"})-[evidence:EVIDENCED_BY]->(source:Evidence)
WHERE root.access = 'operations' AND tracks.access = 'operations'
  AND record.access = 'operations' AND evidence.access = 'operations' AND source.access = 'operations'
  AND source.observed_at <= "2026-10-14T16:05:00Z"
RETURN root.id AS register_id, tracks.id AS tracks_edge_id,
       record.id AS decision_id, record.title AS decision_title,
       record.recorded_state AS recorded_state, record.statement AS statement,
       source.id AS source_id, source.title AS source_title,
       source.observed_at AS source_observed_at, evidence.id AS evidence_edge_id
ORDER BY record.id
```

The register-rooted query requires TRACKS and the record's own EVIDENCED_BY source with source.observed_at <= known_at before disclosing record facts or existence.

SOP rows are unverified SOP claim records, not roster-derived current owners, selected procedures, verified contacts or authoritative SOP winners.

V2 claims `ROLE-FACILITIES-DUTY` from `SRC-SOP-CURRENT-V2` (*Synthetic SOP-OPS-9 Current Text v2*, observed `2026-10-14T16:00:00Z`). V3 claims `ROLE-MATERIALS-LEAD` from `SRC-SOP-CURRENT-V3` (*Synthetic SOP-OPS-9 Current Text v3*, actually observed `2026-10-14T16:04:00Z`). The separately sourced decision `DEC-OPS-9-REVIEW-01` is recorded as `open-unresolved` by `SRC-DEC-OPS-9-REVIEW-01` (*Synthetic Decision Register: SOP-OPS-9 Reconciliation*, observed `2026-10-14T16:05:00Z`). Archived V1 refers to C3, not the C4 applicability witness.

SOP_CONFLICT is a graph-derived unresolved relationship, not a source quotation or authoritative rule; it requires both independently evidenced claims. CONSIDERS is projected only for independently known claims after the record's own boundary.

## 3. Branch the observation assumption, not the source fact

[`wardatlas_source_branch.py:BRANCH_PATH_QUERY`](../scripts/wardatlas_source_branch.py) discovers the graph-stored assumption:

<!-- query:branch-path -->
```cypher
MATCH (branch:ScenarioBranch {id: "BR-SOP-V3-LATE-OBS"})
      -[select:HAS_HYPOTHETICAL_OBSERVATION]->(override:ObservationOverride {id: "OBS-SOP-V3-LATE-OBS"})
      -[for_source:FOR_SOURCE]->(source:Evidence {id: "SRC-SOP-CURRENT-V3"})
WHERE branch.access = 'operations' AND select.access = 'operations'
  AND override.access = 'operations' AND for_source.access = 'operations'
  AND source.access = 'operations'
RETURN branch.branch_label AS label, branch.id AS branch_id,
       override.id AS override_id, override.assumed_observed_at AS assumed_observed_at,
       source.id AS source_id, source.title AS source_title,
       source.observed_at AS actual_source_observed_at,
       select.id AS branch_edge_id, for_source.id AS source_edge_id
LIMIT 1
```

[`wardatlas_source_branch.py:branch_v3_claim_query`](../scripts/wardatlas_source_branch.py), generated at the declared cutoff:

<!-- query:branch-v3-claim -->
```cypher
MATCH (branch:ScenarioBranch {id: "BR-SOP-V3-LATE-OBS"})
      -[select:HAS_HYPOTHETICAL_OBSERVATION]->(override:ObservationOverride {id: "OBS-SOP-V3-LATE-OBS"})
      -[for_source:FOR_SOURCE]->(branch_source:Evidence {id: "SRC-SOP-CURRENT-V3"}),
      (incident:Incident {id: 'INC-204'})-[aff:AFFECTS]->(equipment:Equipment {id: 'CONVEYOR-C4'})
      -[supports:SUPPORTS]->(service:Service {id: 'SVC-MATERIAL-TRANSFER'}),
      (version:SOPVersion {id: 'SOP-OPS-9-V3'})-[applies:APPLIES_TO]->(equipment),
      (version)-[claim:CLAIMS_ROLE]->(role:Role),
      (version)-[evidence:EVIDENCED_BY]->(source:Evidence)
WHERE branch.access = 'operations' AND select.access = 'operations'
  AND override.access = 'operations' AND for_source.access = 'operations'
  AND branch_source.access = 'operations' AND incident.access = 'operations'
  AND aff.access = 'operations' AND equipment.access = 'operations'
  AND supports.access = 'operations' AND service.access = 'operations'
  AND version.access = 'operations' AND applies.access = 'operations'
  AND claim.access = 'operations' AND role.access = 'operations'
  AND evidence.access = 'operations' AND source.access = 'operations'
  AND branch_source.id = source.id
  AND override.assumed_observed_at <= "2026-10-14T16:05:00Z"
RETURN version.id AS version_id, claim.id AS claim_edge_id, role.id AS role_id,
       applies.id AS applicability_edge_id, equipment.id AS equipment_id,
       source.id AS source_id, source.title AS source_title,
       override.assumed_observed_at AS source_observed_at,
       evidence.id AS evidence_edge_id
ORDER BY version.id, role.id
```

Both branch edges are mandatory: ScenarioBranch -> HAS_HYPOTHETICAL_OBSERVATION -> ObservationOverride -> FOR_SOURCE -> Evidence; branch_source.id = source.id connects the override to the ordinary V3 evidence path.

The branch query tests override.assumed_observed_at <= known_at inside FalkorDB, not source.observed_at; only the hypothetical projection uses assumed 16:06Z. V3's stored actual observation remains 16:04Z and mainline is immutable at all three cutoffs.

The paired `/api/decision-source-branch` calls the unchanged mainline `/api/decision-timeline` service independently. The following are accepted projections, not results of just one snippet: record, conflict and CONSIDERS queries are additionally evidence-gated by the service. All times below are on `2026-10-14` in UTC. V2/V3 abbreviate `SOP-OPS-9-V2`/`SOP-OPS-9-V3`.

| Projection | known_at | Visible claims / V3 observation | Conflict | Separate record state | CONSIDERS targets |
| --- | --- | --- | --- | --- | --- |
| Mainline | 16:04Z | V2 + V3 (actual 16:04Z) | unresolved | unknown; no record/existence claim | none |
| Mainline | 16:05Z | V2 + V3 (actual 16:04Z) | unresolved | open-unresolved | V2 + V3 |
| Mainline | 16:06Z | V2 + V3 (actual 16:04Z) | unresolved | open-unresolved | V2 + V3 |
| Hypothetical | 16:05Z | V2 only; incomplete/unknown | unknown; not resolved | open-unresolved | V2 only |
| Hypothetical | 16:06Z | V2 + V3 (assumed 16:06Z) | unresolved, hypothetical | open-unresolved | V2 + V3 |
| Missing HAS_HYPOTHETICAL_OBSERVATION | 16:05Z | generic incomplete/unknown; no claims | unknown | unknown; no record | none; no mainline fallback |
| Missing FOR_SOURCE | 16:05Z | generic incomplete/unknown; no claims | unknown | unknown; no record | none; no mainline fallback |

If either required branch edge is missing, the hypothetical result is generic incomplete/unknown: no claims, record, conflict, counts, source/branch identities or mainline fallback; mainline stays intact.

## 4. Access, uncertainty and action boundaries

Every traversed node, relationship and evidence source is filtered with access = 'operations' before projection. Connected restricted-source injections and disconnected decoys leave the entire visible operations projection unchanged, including titles, facts, counts and existence hints.

Unknown is not absence, resolution, safety or currentness. A missing path is insufficient evidence, not a negative operational conclusion.

Explicit ISO-8601 offsets normalize to UTC; malformed or timezone-naive cutoffs reject. An omitted known_at yields generic unknown, never a local-clock fallback.

These queries and replay endpoints are read-only. Any handover action requires explicit human confirmation and saves only a local synthetic snapshot; no messages, calls, external tickets or external writes occur.

## 5. Byte pins and previously accepted verification

SHA-256 of the exact UTF-8 query strings above (the fence-delimiter newline is not part of the returned string):

| Source symbol | Query SHA-256 |
| --- | --- |
| CONVEYOR_PATH_QUERY | `2e5096e13a278c64e3ceb22a472bec1d1ab06ae81502850fe953ba17836114cf` |
| _claim_query(16:05Z) | `6bec2409db81e7d7daf8afc0d6104fd345e02feb070111397b5b4a828f5b86e8` |
| _record_query(16:05Z) | `e437185a7463762161ad55c02ef184edcfa644363b60efed7fedf7803650f1ac` |
| BRANCH_PATH_QUERY | `8869af7fcfa42074db2d0ee64098ff51622d41043a7a7aef46eaaccd6a7fff64` |
| branch_v3_claim_query(16:05Z) | `ff577a59cca64e34a0ffbd005ed5592ca1e3575c43943dd6a543115786f6e4b0` |

The [pre-test expectation manifest](../tests/falkordb-query-walkthrough-expected.json) also pins source files, exact symbol-source hashes, all eleven constant/16:04/16:05/16:06 generated-query hashes and seven oracle outcome fragments. The static checker parses only a small allowlisted AST expression subset; it never imports or executes the application. It compares working and accepted-commit source bytes, generated strings and fences without whitespace normalization. Any drift fails closed, rather than regenerating expectations.

Accepted oracle/receipt SHA-256 identifiers:

- [Original outage oracle](../tests/expected-results.json): `c77e00e47c4f1e26d565c3d9583653838d5cc88695c4e6aa19ac583205d92b87`.
- [Shift-board oracle](../tests/shift-board-expected-results.json): `5d53a8f93245ee8391ec7cffb6d45b58355536d30d649db19cd53ca6b1ce2428`.
- [Timeline oracle](../contracts/decision-knowledge-timeline/expected-results.json): `8a9ca76ecf7b194ddf26b2aa9bf3d04d0d4eab966a4ee65cacdbccdf76f79076`.
- [Timeline adversarial matrix](../contracts/decision-knowledge-timeline/adversarial-matrix.json): `75a88572303e8fe98e14eadddf7aeab1cb0caead48a8552cfa6551284eaa1de4`.
- [Branch oracle](../contracts/decision-source-branch/expected-results.json): `e209aeab5af266abd6940619727295ea50c0ad2707f47c2d4df37d24cb37da23`.
- [Branch adversarial matrix](../contracts/decision-source-branch/adversarial-matrix.json): `4a07df2def7244b3008e912a5c617fbc75d080999071f3b48a556f27b65698c5`.
- [Independent source-oracle review receipt](../contracts/decision-source-branch/independent-review.json): `36e3c8bdc29019da67f06af605f188cb1c0c6662c81e5ea3efbad666242b0d55`.
- Fresh-source verification report (actual FalkorDB 6.0.2; branch 8/74, timeline 12/120, integrated 79 assertions, 238 unit tests): `5eea66d76f13baa419ee8e01b39b3cbe4ef447b2c0881ed31814905e838544e8`.
- Fresh source-review receipt (same source commit): `7292076f6a88e6218944f57ccf00c835559300fb894ec4ddaba9527e4975668e`.

The accepted report digests identify prior verification, not a new graph/browser run or publicly accessible media. This appendix's checker is static and cannot re-prove database behavior.

From a clone with the accepted source commit in Git history, run this dependency-free static check (no database, browser, network or credentials):

```sh
python3 scripts/verify_falkordb_query_walkthrough.py --expected tests/falkordb-query-walkthrough-expected.json --expected-source-commit b540f032a50d14384602e638abc1ff59f50160cf --require-pinned-query-hashes --assert-literal-query-fences --reject-unsafe-claims --require-ten-mutations --fail-on-zero
python3 -m unittest tests.test_falkordb_query_walkthrough
```

## 6. Truthful disclosure

This Company Brain demo uses fictional synthetic hospital-operations data only: no patients, identifiable real staff or live hospital integration. It is non-clinical, not a validated hospital system or medical device, and makes no compliance or clinical-outcome claim.

At the pinned source this is deterministic FalkorDB retrieval and template projection; no AI model is called at runtime. AI assistants helped author code, tests and documentation; this is not a model-backed clinical agent.

This appendix performs no publication or submission and makes no public deployment or video-availability claim; publication and submission require owner authorization.

Eligibility risk remains: official rules require participant technical understanding and meaningful contribution and warn that AI-only entries may be rejected. Autonomous work does not certify these requirements on the owner's behalf; winning is not guaranteed.

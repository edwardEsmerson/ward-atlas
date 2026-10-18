# Knowledge-diff recovery receipt

## Preserved work, not a replacement feature

The recovery fetched `/workspace/stores/auto-5b3c9978636fc7c9fd9a2a91`
branch `workshop/auto-5b3c9978636fc7c9fd9a2a91` for provenance. At this
execution it resolves to `bcdec174ed2e35f12ef0442f147ffa5d481d82b6`, not
the task admission's earlier clean `4c61625` checkpoint. The branch has
advanced and already contains the feature; current main at
`bd3196e4271bf36fdf27bf38b091a2f6ecd6fc7f` contains it too.
No earlier task status, budget, history or acceptance requirement was changed.

Literal expectations precede behavior: `aab0409` froze the new oracle/matrix,
`01796d2` corrected its timeline reference, and `859037a` implemented the
feature. `76da60b` records its earlier source-only reproduction. All seven
task-owned implementation/contract files listed below are byte-identical
between the fetched preserved branch and the recovery source revision.
Existing app mounts, fixtures, accepted services and source oracles are retained.

The retained `/knowledge-diff` and `GET /api/knowledge-diff` independently
select explicit left/right UTC knowledge cutoffs and allowlisted mainline or
hypothetical modes. Five witness types are separately compared: claims, source
observations, conflict relationship, decision record and considered links.
Identical 16:05 mainline snapshots have zero substantive delta; the 16:04→16:05
boundary adds only the separately sourced record/observation/CONSIDERS links.
At 16:05 the hypothetical suppresses V3/conflict/V3-considered-link while
retaining V2 and the open-unresolved record; at hypothetical 16:06 those
witnesses are projected again. V3's actual observation stays 16:04 and its
assumed time is separately 16:06, never applied to V2. Dates are 2026-10-14 UTC.

Missing either branch edge yields comparison-unavailable/generic unknown,
without fallback or mass-removal conclusions. Cold, partial and unknown sides
are not evidence of deletion, resolution, safety or completeness. Bounded typed
deltas use live public edge/source witnesses under the accepted services'
reentrant application lock, **not database transaction isolation**. The
responsive keyboard-accessible workspace includes exact source citations and
guards against stale requests. No saved action, graph write, SOP selection,
source correction, model, private data or public service is involved.

## Frozen source and feature hashes (SHA-256)

| File | SHA-256 |
| --- | --- |
| `contracts/decision-knowledge-timeline/expected-results.json` | `8a9ca76ecf7b194ddf26b2aa9bf3d04d0d4eab966a4ee65cacdbccdf76f79076` |
| `contracts/decision-source-branch/expected-results.json` | `e209aeab5af266abd6940619727295ea50c0ad2707f47c2d4df37d24cb37da23` |
| `contracts/knowledge-diff/expected-results.json` | `295b80664f5eb9ad7743f6d7277c5d846e165928bb41deda08a3762f68ad5a2c` |
| `contracts/knowledge-diff/adversarial-matrix.json` | `277909ed21537b2671914d146532efaebb33dcb74efb2aeff4f616fed11a14f7` |
| `scripts/wardatlas_knowledge_diff.py` | `b51e3880cae82b2da93162504d71efc9af751159c6c0b18400bf9d5a83624661` |
| `scripts/ui/knowledge_diff.html` | `a35c803624211fa8ecbac661d0546473d02737a79aa0a8887e6883f7f0d08707` |
| `tests/test_knowledge_diff.py` | `a399cba756eb21616e4cd3dd4d814a06fe6de9e110a243798199e3b205fa81b4` |
| `scripts/knowledge_diff_browser_assertions.cjs` | `453f441ab281b32ddf082c78cc66f035c75673da0a0d13d4973d1bd87c6a63ec` |
| `scripts/verify_knowledge_diff.py` | `385c5ebc2eca488d80b584ddc95ab9fe6ae67ac86e892eb2b97b6766140c3262` |

## Exact original checks and fresh evidence

```sh
python3 scripts/verify_knowledge_diff.py --all --require-live-falkordb --desktop-mobile --fail-on-zero
python3 -m unittest tests.test_knowledge_diff
```

Both passed in the recovery checkout and in a separate source-only export of
the merged revision (initially no `.local/` dependencies). Actual FalkorDB
module **60002**, **10 groups / 63 mapped assertions**, **20 focused tests**;
both source-oracle hashes remain pinned. Coverage includes unknown-as-removal,
equal inputs, mode reversal, missing branch witnesses, UTC normalization,
restricted/decoy full-response and DOM invariance, cold/reseed/reconnect,
read-only graph/action state, desktop/mobile, keyboard and stale requests.
All seven owned cleanup checks passed. No external planner reads were made by
the verifier. The verifier chooses free lane ports and unique graph/state and
overwrites its own reports; only owned resources are removed.

Recovery evidence root:
`/workspace/evidence/auto-16c1f4814e2931434d33a441/d10b5163e7034ae2961b2a59a3935901/`.

- `unit-knowledge-diff.log`, `live-knowledge-diff.log`, `knowledge-diff/report.json`
- `final-unit.log`, `final-live.log`: both original checks passed again after
  merging current main; existing report/browser output paths were reused
  successfully, demonstrating rerunnability without stale-file collisions.
- `source-only-revision.txt`, `fresh-unit.log`, `fresh-live.log`, `fresh/knowledge-diff/report.json`
- Browser JSON, accessibility/DOM evidence and desktop/mobile screenshots are
  adjacent to each report; `screenshots-repeat/` shows the selected comparison.
- Final recovery report SHA-256: `328e51567dbcb27cfa5dd83cc69afa29de0f7506ccc4470604a2616785598192`.
- Source-only report SHA-256: `de6a808338183d014bde63a80ba5b82fca08f64272cbbab3cec662c647084c4d`.

Source extraction note: this sandbox's `tar` extraction returned `Function not
implemented` for nested file creation (both evidence and project-local paths).
Using Python `ZipFile` on `git archive --format=zip` produced the source-only
export successfully; no host/control changes or acceptance relaxation were
needed. The failed extraction did not exercise product behavior.

These are builder recovery/reproduction observations, not supervisor acceptance
or an independent human oracle. Fresh supervisor and merged-integration checks
remain the supervisor's separate stage. No publication, deployment, clinical
validation or participant submission eligibility is certified.

## Restart reconciliation and newer-main integration

The resumed builder preserved `a0a91ee` and merged canonical main `29478f7`
(merged source revision `0a52f70da2d60e7187dad3724f6e10061cb68e66`). The seven
feature/contract files above remain byte-identical to the fetched preserved
branch; other lanes' resilience and supplied-server app mounts are retained.
No product behavior, fixture, oracle, earlier task status or budget was changed.

The interrupted supervisor evidence was inspected rather than discarded:
`/workspace/evidence/auto-16c1f4814e2931434d33a441/e41ec63bbcfe455ab336933a3b3d16d4/assessment.json`
records a pass assessment for pinned `a0a91ee`, no observed defects, both original
checks, 18 accepted-service regression tests and a separately specified
**100-combination / 667-check** probe. Its `independent-probe.json` is PASS
(SHA-256 `4a4b9299b7c0888d0f8a6859cd7cb3dcb691c4684585cf8b8ff34183d919baa9`).
The supervisor result receipts remain incomplete; this builder does not change
them or promote an interrupted rerun to acceptance. The preserved second
verification's execution receipt records quota exhaustion, not a product defect.

Both exact original commands above passed again against the newer merged app
and its dependency-cold source-only export: **20 focused tests; actual FalkorDB
60002; 10 groups / 63 mapped assertions; desktop/mobile and seven owned cleanup
checks**. The three accepted decision-service suites also passed **18 tests**.
This run's evidence root is
`/workspace/evidence/auto-16c1f4814e2931434d33a441/69b0f8db2ed7415387d4d04a2d334603/`:
`original-{unit,live}.log`, `fresh-{unit,live}.log`, `service-regression.log`,
`merged-source-revision.txt`, and `knowledge-diff/` / `fresh/knowledge-diff/`
API/browser reports and screenshots. The fresh report SHA-256 is
`2f97e717d88bab988f232905bd983d7d246ca82d1a974080956bd0749138c996`.
The original commands are rerun after final receipt edits; supervisor acceptance
and merged integration remain distinct from builder evidence.

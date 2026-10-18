# Ask recovery receipt

This bounded recovery does not reset or reclassify the earlier failed task.
Its three original failure receipts report subscription limits, not passing
product checks. The preserved draft was reviewed as unaccepted implementation,
never as the behavioral oracle.

Fetched the supplied workshop branch into this fresh product checkout. Oracle
commit `887715818292f30e48aa704867afab80d654224f` is already an ancestor of main;
both `contracts/questions/` files remain byte-identical to that commit (11 groups,
78 mapped assertions). The two accepted source-oracle hashes remain unchanged.

## Non-destructive reconciliation

By recovery admission main already contained the subsequent Ask implementation
(`8b798c5`) and UI/live checker. The preserved worktree was clean when inspected.
The useful supplied Python files are already identical in this checkout; retain
those exact bytes rather than overwrite completed work with an older draft.
No source worktree files were written or deleted. It is not consulted by checks.

SHA-256 of the reviewed preserved source and identical recovered files:

| File | SHA-256 |
| --- | --- |
| `scripts/wardatlas_questions.py` | `0546037a0baeb180292a4d4b01642dba2fff7f531dbbfb726d27d6d41eed7093` |
| `tests/test_questions.py` | `94d6fbbdb4a167c33cc4747762db5777c5fa2b8aeb955d53892ceece4376c834` |

The preserved full app hash was
`1f02ab9c513f03febb10f421b7c8556be8a37df5acd789a99b152684b425be8d`.
The full app is deliberately **not** copied: main includes other lanes' mounts.
Only the namespaced import, Ask link, GET route and POST route were compared;
each already matches. Their individual exact-text SHA-256 values are:

- import: `089045457b563307290ec7073d1e36bb06ed1aabbacf4357d019f150b0420ca4`
- root link: `4da49acd86ff82e8ab911a06a3092b68a62f6dccea42ad2e8a96ae92c15aa46a`
- GET block: `eb1ce2d1d15c77a7f6fd8b709cc0870d2966e7a26240ab086f8e5eac02521994`
- POST block: `e27ff7f0577d929729b089a64db9688ca3675af64c59493546b4d3fff25e3287`

## Acceptance, not inherited claims

Run the two original commands unchanged from fresh tracked source:

```sh
python3 scripts/verify_questions.py --all --stub-model --require-live-falkordb --desktop-mobile --fail-on-zero
python3 -m unittest tests.test_questions
```

The verifier uses only repository-pinned oracles, an owned loopback FalkorDB,
ephemeral/allocated ports, local model stubs and owned run state. Reports and
screenshots belong under `$WORKSHOP_RUN_DIR`, not this receipt. A separate fresh
verifier process is not an independent human oracle: independence comes from the
frozen accepted source expectations and adversarial checks. Supervisor integration
is a further acceptance step. No live inference, credentials, publication or
human eligibility certification is required or claimed here.

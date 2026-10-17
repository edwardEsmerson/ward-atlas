# Reversible implementation decisions

- Keep source-revision ingestion and its CLI/live verifier in new, isolated modules rather than the concurrently owned app/fixture files; this proves graph behavior without claiming UI integration. Revert by removing the new module/docs/tests and their status rows.
- Treat roster changes as append-only, effective-dated facts and wake only artifacts with explicit source/citation edges; missing edges mean coverage is unknown, never “current.” Revert the behavior only by changing the new pinned oracle and re-approving its scope (not during this task).
- Keep the SOP V2/V3 disagreement unresolved and require no external or automatic operational action. Revert by a separately authorized product decision; this task does not select an authority.

# Reversible implementation decisions

- Keep the existing incident briefing APIs and their pinned behavior intact; add a separate graph-backed `/api/shift-board` surface and dedicated fixture/query module so dock ownership history and conveyor event replay stay independently inspectable. Revert by removing the additive module/routes/UI and associated verifier assertions.
- Model the dock roster with effective-dated ownership edges and the conveyor history with observed state-event nodes plus a branch-only hypothetical event; evaluate timestamps in UTC and query connected paths from FalkorDB rather than precomputed lists. Revert by replacing the temporal graph records/query selection while retaining the pinned outcomes.

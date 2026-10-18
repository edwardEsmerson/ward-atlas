# Reversible build decisions

- Clock atlas: fixed two effective instants × two knowledge cutoffs, selection defaults to the earliest pair; reject unsupported pairs after UTC normalization, because this is the bounded accepted INC-207 workbench, not a new explorer; revert by removing the namespaced mount and atlas files.
- Clock atlas: reuse the accepted `shift_board(dock_at=..., known_at=...)` for each cell separately; no source/role fixture writes or cross-cell projection, preserving existing graph semantics; revert the additive import/routes only.
- Clock atlas: use a dedicated `/clocks` page with a root navigation link, selected pair in the URL, arrow-key matrix and stacked mobile cards; no confirmation or saved state; revert by unmounting the page.

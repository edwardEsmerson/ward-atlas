# SOP claim comparison (pinned before behavior)

This is a typed relationship/evidence comparison, not a document redline or time-snapshot diff. Frozen product-local oracle hashes and JSON pointers are in expected-results.json. Archive C3 applicability and absence of a V1 claim are also explicit task facts. Expected values are not harvested from candidate output.

GET /api/sop-claim-comparison requires known_at; optional left/right version selections, equipment focus and operations scope must occur exactly once. Selectors contain only independently source-known versions and equipment witnessed by a public APPLIES_TO edge from a source-known version. Hidden selections return a generic unavailable selection, without echoing it or auto-selecting a winner. Defaults are presentation choices, not procedural recommendations. Independent archive evidence remains visible before current claim observations.

Each pane shows sourced version properties, explicit equipment/role edges and exact citations. The comparison categorizes version identity, sourced reference property, applicability witnesses, unverified role claims, source observations and explicit conflict relation. Missing edges mean unknown, never agreement, applicability, resolution or inferred lineage. Application read locking is not a database transaction isolation claim.

Eight live groups map nonzero assertions plus cleanup; focused tests cover frozen facts, adversarial inputs, missing edges and UI safety. Browser evidence includes main text, DOM/options and ARIA on desktop/mobile; API invariance includes every source-time boundary and pair. Owned mutations are verifier-only and use existing synthetic fixtures, not new product fixtures.

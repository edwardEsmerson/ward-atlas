# README screenshots

These are real element/clipped screenshots of WardAtlas, rendered at a **1440 px
desktop viewport**, device scale 1, in the light theme (redesigned 2026-10-18). No captions,
overlays, mock responses, DOM edits, compositing or pixel rescaling are used.
Each crop focuses on the relevant panel so GitHub need not shrink a full desktop.

## Regenerate

Requirements: Python 3.10+, Node.js/npm and `redis-server` on PATH. Run:

```sh
python3 scripts/capture_readme_screenshots.py
python3 scripts/verify_readme_media.py --fail-on-zero
python3 -m unittest tests.test_readme_media
```

The capture entrypoint reuses the repository's digest-verified official FalkorDB
6.0.2 bootstrap. Playwright/browser and the pinned Pillow image decoder bootstrap
project-locally when needed; nothing is installed system-wide. The app, actual
database process, graph and state all belong to this run. Ports are free lane
ports (or OS-assigned outside the workshop), never a fixed/default database port.
The capture presses **Reset synthetic scenario** before reading the app. It closes
the browser, drains/stops the app, deletes its graph/state and stops its database
in `finally`, including on failure. It never attaches to a shared preview/database.
Reports overwrite their own file under `$WORKSHOP_RUN_DIR` or ignored
`.local/readme-media-evidence/`; `--report` and `--output-dir` are also supported.

## Captured states

| File stem | Actual UI state |
| --- | --- |
| `control-room` | INC-204 live impact map and the adjacent source-trail card after reset |
| `sop-conflict` | Unresolved V2/V3 claims, stale V1 referencing C3, dated sources |
| `shift-board` | INC-207 effective 20:30Z boundary; sources known by 20:19Z |
| `ownership-clocks` | Four effective/knowledge pairs, later pair selected |
| `knowledge-diff` | Mainline versus hypothetical at 16:05Z; first semantic difference |
| `ask-citations` | Deterministic INC-204 impact answer at 18:30Z; citations, no model requested |
| `evidence-resilience` | One hypothetically missing Central Sterile ownership-support edge; unchanged two-path live baseline |
| `handover` | A second unsaved preview's confirmation control above the first genuinely confirmed local handover |

The README gallery shows `ask-citations-top.png` and `handover-top.png`: the top
1225 px and 635 px of `ask-citations.png` and `handover.png`, cropped with Pillow
so the gallery rows stay balanced; the full captures are linked beside them. Re-crop
them after regenerating.

The handover crop is one continuous part of the running page, not a before/after
montage. The capture verifies zero saved actions before preview confirmation and
exactly one after it. The second preview remains unsaved and is discarded on exit.
Resilience is a read-only hypothesis, not a graph mutation or equipment failure.
All scenarios and people are synthetic and non-clinical.

PNGs are preferred. If a PNG exceeds **600,000 bytes**, the capture takes the same
crop directly with Playwright as JPEG quality **85**. The report lists filenames;
update the corresponding README extension if this fallback is needed. The total
must stay below **5,000,000 bytes**. Images are ordinary Git files, not LFS.
The lightweight media check fully decodes PNG/JPEG/WebP, checks README relative
paths, and compares the opening's Cypher with the accepted query literal.

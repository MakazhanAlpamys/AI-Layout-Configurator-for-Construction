# Context Handoff

## Current product goal — 2026-09-05

The project is a **Facility Layout Compiler**, not a general-purpose floor plan
generator. Target niche: pharmaceuticals, cleanrooms, laboratories, hospitals and
industrial facilities, where verifiable flows, equipment clearance, zones and
evidence matter more than a pretty picture. The first narrow wedge is pharma-like
clean production. Full statement and boundaries:
[FACILITY_PRODUCT_BRIEF.md](FACILITY_PRODUCT_BRIEF.md); technical plan:
[PRODUCT_PLAN.md](PRODUCT_PLAN.md).

## Current stage and how to continue

The first acceptance example is `examples/pharma_cleanroom_pilot.yaml`: 13 rooms,
6 zones, 5 equipment items, 7 flows and the composite profile
`rules/pharma_cleanroom_pilot.yaml`. The program of 20–40 rooms and 10–30
equipment items in `PRODUCT_PLAN.md` is the next scale stage after its
acceptance.

`facility_generation.py` passes CP-SAT the required/forbidden zone relations,
the minimum shared boundary with an allowance for walls, and the required
equipment dimensions. Independent geometry/equipment/flow/profile checks select
the full set of variants. The shared-boundary length in CP-SAT was also fixed:
both sides must accommodate the required opening, including when one projection
is nested inside the other.

`manifest.json` stores the search settings and the obtained and checked
candidates. When the set is incomplete, `generation-failure.json` stores the
program, the profile and the failure reasons; a failure of the bounded search is
not proof of mathematical infeasibility. The matrix keeps this report and does
not mark the identifier comparison `PASS` without successful QA for all seeds.

153 tests pass locally (2026-09-26) via `python -m unittest discover -s tests -v`
(re-verified 2026-09-10 on the new development machine).
CI is defined in `.github/workflows/ci.yml`: tests on Windows/Linux, then a
three-variant matrix for seeds 1/7/42 with a 30-second solver limit and nine
candidates per seed. CI saves artifacts and reports on both success and failure.

Commit `9cd1d27` was pushed to `origin/main` on 2026-09-05. The workflow
`33979389646` it triggered ended in `failure` **without reaching any steps**:
GitHub returned `The job was not started because recent account payments have
failed or your spending limit needs to be increased`. This is an account billing
block, not a defect in the code or the workflow; the versions `actions/checkout@v7`,
`actions/setup-python@v7` and `actions/upload-artifact@v7` exist and are correct.
Until billing is restored there is no CI confirmation of the matrix on GitHub,
and it cannot be presented as evidence.

The full local matrix passed on 2026-09-05: seeds 1/7/42, three variants each,
30 seconds per solver run, at most nine candidates, two equipment retries.
All nine bundles passed bundle QA and cross-seed identity checks.
For seeds 1/7/42, 3/6/5 candidates were checked respectively; 0/3/2 were rejected.
Report: `out/pharma_cleanroom_stabilized_2026-09-05/acceptance-matrix-report.json`;
next to it is `environment.json` with the environment versions. These local
artifacts are excluded from Git; CI produces and stores its own bundle. The
environment of that run: Python 3.11.15, OR-Tools 9.15.6755, Shapely 2.1.2,
IfcOpenShell 0.8.5 — this is the development machine before the 2026-09-10
migration; the current environment is described below.

## Acceptance package — 2026-09-06

The current external acceptance package is `out/acceptance-2026-09-06/`
(not versioned; rebuilt with the commands from
[ACCEPTANCE_REVIEW_PACKAGE.md](ACCEPTANCE_REVIEW_PACKAGE.md)): the three accepted
seed 1 variants in all projections, the seeds 1/7/42 matrix with status `PASS`,
a dossier for each external gate, PDF sheet previews (200 dpi), SVG renders of
the DXF files and 23 browser QA screenshots.

Each of the three human gates is now served by a generated dossier:
`layout-configurator review-dossier` projects the accepted bundle into one
document per role. The process engineer gets the declared flows with stages,
the derived routes, the incompatible-pair policy and the room program; the
cleanroom engineer gets zones with classes and pressures, the cascade and airlock
roles; the architect gets the IFC contents, DXF layers, equipment with service
clearances and the SHA-256 of the files. Each dossier has a decisions block and
an explicit list of what the tool did not check. Module: `review.py`; tests:
`tests/test_review.py`.

Machine checks of the package: 136 tests OK; `qa-building` — `PASS` for all three
variants; IDS `ids/layout_baseline.ids` — `PASS` for all three IFC files.

The 2026-09-05 package is kept as historical: it contains the set of viewer
screenshots from before the projection-unit fix.

Viewer screenshot QA was **performed and closed** in a real browser (Chrome
headless shell + puppeteer-core 23.11.1, Node 22.13.0) — the earlier note about
the missing browser runtime is no longer current. Confirmed: rendering of the
three variants, highlighting of route and equipment conflicts, the
`ALL`/`OPEN`/`RESOLVED` filters, the full issue lifecycle with audit trail,
resolved viewpoints of closed BCF topics, and the read-only guard `HTTP 405`.

The first run showed that the SVG plan projection was unreadable. The cause: in
`static/style.css` line widths were set with `vector-effect:
non-scaling-stroke` and treated as screen pixels, while text and marker radii
were in millimetres, and the values had been chosen the other way round: a
route of 120 px ≈ 7248 mm against the declared 1200–1800 mm, an axis of 16 px ≈
966 mm, a room label ≈ 2.98 px, an issue marker ≈ 4.64 px.

Fixed: `drawPlan` publishes the projection unit `--u` (user units per rendered
CSS pixel), CSS sets text as `calc(var(--u) * Npx)`, markers and offsets are
computed via `px()`, the flow corridor is drawn as a separate `.flow-corridor`
at its declared width with a hairline centerline on top, the grid step is raised
to at least 9 px, the plan is redrawn on resize, room labels shrink to fit their
room, and the flow label offset is now in screen units. Measured after the fix:
corridor 1800 mm (29.8 px) and 1200 mm (19.9 px), centerline 2 px, axis 1 px,
room label 13.0 px, marker 7.0 px. The regression is guarded by
`tests/test_ui.py::PlanProjectionUnitTests`. The non-blocking VQ-05…VQ-13 are
fixed — see [VIEWER_QA_2026-09-05.md](VIEWER_QA_2026-09-05.md).

The defects concern the projection, not validation: panel values, check
statuses, route coordinates and BCF identifiers match the deterministic report.

## Remaining pilot acceptance conditions

| # | Condition | Owner | Status |
| --- | --- | --- | --- |
| 1 | Process and quality review | process engineer / pharma manufacturing QA | open; dossier `review/building_0N.technologist.md` is ready, review not yet held |
| 2 | Cleanroom/HVAC review | cleanroom / HVAC engineer | open; dossier `review/building_0N.cleanroom-hvac.md` is ready, review not yet held |
| 3 | Architectural/BIM review: open the DXF and IFC in the receiving CAD/BIM tools | architect / BIM coordinator | open; dossier is ready, but the environment has no CAD/BIM tool at all, and machine read-back does not replace this |
| 4 | Green CI matrix run on GitHub | account owner | blocked by GitHub Actions billing; locally the same matrix passes after the 2026-09-26 fix |
| 5 | Calibrate the `--deterministic-budget` budget for CI and decide whether to switch the matrix to that mode | product owner | open; the mode itself is ready |
| 6 | Minor defects VQ-07…VQ-13 | development | **closed 2026-09-26**, verified in the browser |

Closed: readability of the SVG projection (2026-09-05), label overlap on sheet
`DR-01`, BCF topic identifier matching `BCF-01`, and consistency of issue
statuses `VQ-05`/`VQ-06` (2026-09-06). `DR-02` was withdrawn as an observation
error.

### RP-01 — closed 2026-09-06 with a separate mode

`--time-limit` kept its meaning as a wall-clock budget. For reproducibility,
`--deterministic-budget UNITS` was added: a machine-independent amount of CP-SAT
work, single-threaded search, with the wall-clock cap removed. The flag is
available on `generate`, `generate-building` and `acceptance-building-matrix`;
`manifest.json` and the matrix report store `deterministic_units` and the
`repeatable` flag.

A budget alone turned out to be insufficient. `LayoutIR.relation_pairs` returned
a `set`, and the iteration order of a set of strings depends on `PYTHONHASHSEED`,
which Python picks randomly for each process. Two processes with the same seed
built different models, and CP-SAT explores the model in construction order.
Within a single process the effect is invisible, so the unit test passed while
real runs diverged. The method now returns a sorted tuple; the invariant is
guarded by `tests/test_search_budget.py::ModelOrderStabilityTests`.

Calibration on the development machine: ≈ 3.4 s per unit, 10 units ≈ the former
`--time-limit 30`. The limitations — units are not seconds, repeatability is tied
to the OR-Tools version and does not imply optimality — are described in
[DETERMINISTIC_BUDGET.md](DETERMINISTIC_BUDGET.md).

## Scaling — first measurement 2026-09-06

A series of 13 → 20 → 30 → 40 rooms and 5 → 30 equipment items was run with the
same checks and the same profile; constraints were not relaxed. The programs are
`examples/scale_20_rooms.yaml`, `scale_30_rooms.yaml`, `scale_40_rooms.yaml`,
keeping the pilot's process core and a boundary fill ratio of 0.57.
Details and figures are in [SCALING_STUDY.md](SCALING_STUDY.md).

In short:

- the success rate drops 2/3 → 1/3 → 0/3 → 0/3; the single success at 20 rooms
  took 64 minutes;
- at 30 and 40 rooms the room solver finds no feasible solution at all within
  300 seconds — this is a failure to find a first solution, not an optimization
  failure, and not proof of infeasibility;
- the room solver is the only bottleneck: all other stages together fit within
  2 seconds even at 20 rooms, and model construction takes 0.1 s at 40;
- `--time-limit` is not a hard ceiling: a requested 40 s took 102 s at
  20 rooms;
- **the objective is not aligned with the gates**: at 20 rooms a 120 s budget
  produced a passing result, while 300 s produced a solution 28 times better by
  objective that failed `FLOW_COMPLETENESS`.

Implication for the plan: increasing the budget is not a strategy. What is needed
is the hierarchical solver from `PRODUCT_PLAN.md` (zones → rooms within a zone)
instead of a single monolithic `AddNoOverlap2D` for the whole facility, and
bringing the objective function closer to the gates. The first version of the
scale programs hung all auxiliary rooms off a single corridor and was infeasible
by construction; this was fixed with scalable circulation, not by relaxing the
checks.

Scaling and the laboratory/hospital/industrial packs continue only after
items 1–3 are closed.

## Migration to a new laptop — 2026-09-10

The working copy was moved to another machine. The repository matches
`origin/main` at commit `1e1a993`, with no divergence in either direction —
there was nothing to pull from GitHub. The only uncommitted change is section 12
"Future Cycle I" in [PRODUCT_PLAN.md](PRODUCT_PLAN.md).

The transferred `.venv` turned out to be broken: it pointed to an interpreter in
the previous machine's user profile (`C:\Users\tokmo\...`), which does not exist
here, and every run failed with `uv trampoline failed to spawn Python child
process`. The environment was recreated with the quick-start commands from the
README; no README changes were needed. Lesson for the future: `.venv` is not
portable between machines; it must be recreated, not copied.

Full verification on the new machine:

| Check | Result |
| --- | --- |
| `python -m unittest discover -s tests -v` | 142 tests OK, 162 s |
| `generate examples/basic.yaml --variants 2` | DXF, PDF, IFC, JSON and `manifest.json` for both variants |
| `generate-building` of the pilot, seed 1, `--time-limit 60` | full bundle, including `.coordination.json` and `.bcf` |
| `qa-building` of that bundle | `PASS`, all seven checks |
| `validate --ids ids/layout_baseline.ids` | `PASS`: 13 spaces, 49 walls, 12 doors, 1 window, 13 openings |

New machine environment: Windows 11, Python 3.11.16, OR-Tools 9.15.6755,
Shapely 2.1.2, ezdxf 1.4.4, IfcOpenShell 0.8.5, ifctester 0.8.5,
jsonschema 4.26.0, reportlab 5.0.1, uv 0.12.11. Differences from the 2026-09-06
measurement environment: Python 3.11.15 → 3.11.16, ezdxf → 1.4.4,
jsonschema → 4.26.0, reportlab → 5.0.1.

Because of the machine change, **the timings in
[DETERMINISTIC_BUDGET.md](DETERMINISTIC_BUDGET.md) and
[SCALING_STUDY.md](SCALING_STUDY.md) no longer describe the current environment.**
The "≈ 3.4 s per budget unit" ratio and the scaling series figures were measured
on the previous machine, and they cannot be relied on for CI calibration
(acceptance condition 5) without re-measurement. The conclusions themselves do
not depend on the machine: the bottleneck is the room solver, and the objective
is misaligned with the gates.

Not performed on this machine and still unconfirmed here: browser viewer QA,
the full seeds 1/7/42 matrix, and opening the DXF and IFC in CAD/BIM. The runtime
for viewer QA is present on the machine — Node 24.19.0 and Chrome are installed,
but the versions differ from the 2026-09-06 run (Node 22.13.0), so the run needs
to be repeated rather than carrying its result over.

A separate observation that does not affect the gates: `ruff check src tests`
reports 123 issues, and `ruff format --check` flags 44 files for reformatting.
Ruff is not wired into the project (no section in `pyproject.toml`, CI does not
run it); the repository only contains `.ruff_cache/`. This is not a regression
but an unmade decision: either adopt ruff as a gate and bring the code in line
once, or remove the cache.

## CI and equipment placement near doors — 2026-09-26

**Why CI is red.** All seven workflow runs ended in `failure` without a single
step: every job has the GitHub annotation `The job was not started because
recent account payments have failed or your spending limit needs to be
increased`, and no runner was assigned. This is still the account billing block
(acceptance condition 4); it cannot be lifted by code — payment or the spending
limit must be restored in GitHub → Settings → Billing & plans.

**What would have been red once the block is lifted.** A local run of exactly the
same command as in CI (`acceptance-building-matrix … --seeds 1 7 42 --max-attempts 9
--time-limit 30`, Linux, Python 3.11.15, OR-Tools 9.15.6755) exited with code 4:
seed 1 produced only 2 of 3 variants from 9 candidates. The seven rejections
were identical — the `material_to_production` corridor intersects the clearance
of `mixer_01`.

Cause: the equipment packer minimized `x + y` and knew nothing about doors, so
the mixer always ended up in the lower-left corner of `preparation` and in 7 of
9 layouts covered the approach to the door from `material_airlock`; the route's
starting point in the room ended up inside the clearance, with no way around it.
Equipment retries did not help: a different seed returns the same optimum.

Fix: `door_approach_zones` builds, for each door, an approach zone sized for the
widest required flow (with the same portal offset as the router),
`place_equipment(keep_out=…)` keeps the clearance of free-standing equipment out
of those zones, and `place_equipment_clear_of_doors` drops the zones only in
rooms where the equipment does not fit with them, recording them in
`generation.door_approach_exceptions`. Wherever the old packer found a solution,
the new one finds one too.

Result on this machine:

| Run | seed 1 | seed 7 | seed 42 | Matrix |
| --- | --- | --- | --- | --- |
| before the fix | 2/3, 7 of 9 rejected | 3/3 | 3/3 | FAIL, code 4 |
| after the fix | 3/3 from the first 3 candidates, 0 rejections | 3/3, 0 rejections | 3/3, 0 rejections | **PASS**, bundle QA and cross-seed identity |

For comparison, on 2026-09-05 seeds 1/7/42 checked 3/6/5 candidates with 0/3/2
rejections. The full run took 13.5 min, almost all of it in the room solver (the
9 candidates are solved up front). This is local confirmation, not CI evidence:
item 4 remains blocked by billing.

## CI green, English documentation, hierarchical solver — 2026-09-27

- The repository was made public, which lifted the Actions billing block. Run
  11 on `db831f4` was the first fully green CI run: tests on Ubuntu and Windows
  and the Windows pharma-cleanroom matrix. Condition 4 above is no longer
  blocked by billing.
- README, `docs/`, the Russian parts of `research/`, the viewer and error
  messages are English. Russian stays only where it is behaviour (the text-brief
  parser vocabulary, citation search over the Russian-language KZ profile) or a
  legal source (official SN RK 3.02-02-2023 title and quoted clauses).
- `hierarchy.py` adds a hierarchical room solver (clusters → cluster shapes →
  top-level placement) and facility generation uses it by default with a
  monolithic fallback (`--room-solver auto|hierarchical|monolithic`; the
  manifest records `room_solver_used`). The local CI matrix now passes in 11 s
  instead of 13.5 min; the 20-room program passes on all three seeds instead of
  one in three. 30 and 40 rooms are still unsolved — details and the next step
  are in [SCALING_STUDY.md](SCALING_STUDY.md).
- 160 tests pass locally.

## Archive of the original MVP

The old text below is historical context for the original residential MVP. It
is useful as evidence for the architectural decisions made, but it does not
constrain the new facility-focused roadmap.

## Core principle

**Solver-first hybrid.** Geometry is computed by a deterministic solver, not a
neural network. This is the consensus of all 8 independent research reports, not
a preference.

The LLM is connected last (subproject 5) and **never** outputs coordinates,
never writes DXF directly and never issues a verdict on building codes. Reason:
no open neural network (House-GAN++, HouseDiffusion, Graph2Plan) guarantees hard
constraints — they produce duplicate rooms, overlaps, and areas off by ±30%.

## What already works

Subproject 1 MVP, commit `b9badb4`. Approach: CP-SAT rectangle packing:

- `models.py` — `LayoutIR`, the single source of truth. DXF/PDF are derived projections
- `solver.py` — OR-Tools CP-SAT: `AddNoOverlap2D`, area via `AddMultiplicationEquality`,
  adjacencies via edge equality + minimum overlap, subtracted outline zones.
  Variants via `AddForbiddenAssignments` on the previous solution
- `validation.py` — independent re-check of the solver result, including room reachability from the entrance
- `export.py` — DXF (ezdxf, layers `A-WALL`/`A-DOOR`/…) and vector PDF (ReportLab)
- `cli.py` — `layout-configurator generate spec.yaml --output out --variants N`

Validation deliberately **does not trust** the solver and re-checks everything. Keep it that way.

## Honest gap between the plan and the code

The decision was: "walls with thickness, corner cleanup, openings". In the code,
walls are room polylines with thickness expressed via `lineweight`. Real double
lines with corner cleanup and cut-out openings **do not exist yet**. This is the
first thing worth finishing, and it is not minor: ~2 weeks of work that `ezdxf`
and `ReportLab` do not do for you.

## Decisions made (do not reopen without a reason)

| Decision | Why |
|---|---|
| Single-storey residential layout | At the core level a house and an apartment are the same problem |
| CLI + files, no web UI | Fastest path to a working result |
| Outline = bbox minus subtracted zones | Covers L-, U- and T-shapes almost for free |
| The corridor is specified in the brief as an ordinary room | Less magic; the solver only checks reachability |
| The project is personal, not for sale | Licenses are not a constraint: GPL/AGPL and research datasets are available |
| Rectangular rooms only | 90% of real housing is like this; L-shaped rooms later by joining rectangles |

## Next steps

Items 2–6 have working slices and tests. Further changes should be extensions of
these contracts, not a change of architecture:

2. UI editor → 3. IFC/BIM and IDS profiles → 4. KZ ruleset → 5. constrained
LLM parser and RAG → 6. multi-floor coordination.

Each subproject is its own cycle: spec → plan → implementation.

## Do not waste time on (unanimous across all 8 reports)

1. A neural network that generates DXF/DWG directly — the format is byte-sensitive, the files will not open
2. Machine-readable building codes do not exist. IDS checks data, not geometry —
   geometric rules are written by hand, one jurisdiction at a time
3. Open DWG writing: LibreDWG is stable only up to R2000. Export DXF, convert with ODA
4. RAG that "checks" codes — LLMs hallucinate precisely on numeric thresholds.
   RAG finds and cites the clause; code makes the decision
5. CP-SAT will start to slow down beyond ~15–20 rooms. Fine for housing, not for a hospital
6. Computing floors independently — upper load-bearing walls will hang over empty space.
   Compute all floors on a single grid of axes

## Current status — 2026-08-31

After the base commit, the following were added: real wall geometry and an editable CLI,
manual `DoorSpec`/`WindowSpec`, IFC4 export, an IDS baseline, a deterministic
ruleset and a JSON Schema boundary. IFC now contains `IfcRelSpaceBoundary`,
`IfcOpeningElement`, `IfcRelVoidsElement`, `IfcRelFillsElement` and the types
`IfcWallType`/`IfcDoorType`/`IfcWindowType`, linked via
`IfcRelDefinesByType`; `LayoutIR` remains the single source of geometry.

Verified: 52 tests pass before the next cycle; the ruleset loader now
supports relative `extends`, targeted rule overrides and source
provenance; `check --require-provenance` can require a complete audit set. KZ has already
been chosen and confirmed against the official PDF; the profile is recorded as partial and
is extended only after manual verification of each new clause. The LLM brief parser is to be connected only after this contract and only as a
producer of canonical JSON. For this boundary a strict CLI normalization
`normalize` was added, which strips generated data and coordinates according to the JSON Schema.
`generate --strict-input` uses the same boundary before running CP-SAT.
A visual DXF audit was performed on fresh basic, KZ-entry and multi-floor files via
the native ezdxf SVG renderer: double walls, corner cleanup, gaps for
doors/windows, swing arcs and label readability were checked. AutoCAD/LibreCAD/ODA Viewer are
not available in the environment, so this does not replace opening the file in a full CAD tool.
The partial KZ profile `rules/kz_sn_3_02_02_2023_partial.yaml` was found and recorded;
it checks cl. 7.8, cl. 6.2.13, cl. 6.2.8, cl. 6.2.12 and cl. 8.19 and deliberately does not pretend to be a full code check.
For a positive smoke test, `examples/kz_daylight.yaml` was added: the generated
layout passes this profile with windows in the living room, kitchen and bedroom.
The `edit` CLI now also accepts `--rules` and, after a successful edit, runs
the same code post-check; FAIL returns code 4, with the export already saved.
`LayoutIR` gained an optional `external_entry` and a room flag
`is_heated`; the external entrance is taken into account in the solver, the wall geometry and IFC.
The CLI editor supports `--set-external-entry` and `--remove-external-entry`.
Positive and negative KZ examples check cl. 6.2.8; a code FAIL
keeps the export and returns code 4.
The local UI is started with the `ui` command and uses the same `EditorState`: the browser
does not compute coordinates, and the server applies only known typed commands and
re-exports the derived files. The optional `--rules` shows the
deterministic post-check in the UI, including the KZ profile and provenance. Drag and resize on the SVG
are only a preview; releasing triggers exactly one typed command, not a
direct write of coordinates to JSON. The undo/redo buttons work on server-side snapshots
of `EditorState`, each transition re-exports the derived files, and a new
command after undo starts a new branch. The UI also gained a log of payload
commands, a grid based on `grid_mm` and a dimension label during drag/resize; browser
visual QA remains manual, since there is no browser connector attached in
the current environment. The KZ ruleset now inherits the generic baseline, and
`ids/kz_layout_exchange.ids` checks the IFC properties of KZ-oriented exchange.
`parse-brief` turns a compact text brief into canonical JSON via the Schema
boundary; `parse-llm` is an optional JSON-only adapter for an OpenAI-compatible
endpoint; `cite` only extracts rule/clause/source and does not make a code
decision. For multi-storey buildings, `generate-multifloor` fixes vertical cores
on a single grid, checks the structural axes and exports a shared IFC with storeys and
stairs. Structural axes are applied inside CP-SAT as hard constraints,
and an IDS specification with no applicable entities is treated as a vacuous PASS.
For the next product scope, versioned domain rule packs were added:
`rules/pharma_clean_production.yaml`, `rules/cleanroom_pilot.yaml`,
`rules/laboratory_pilot.yaml`, `rules/hospital_pilot.yaml` and
`rules/industrial_pilot.yaml`. Their rules are typed and carry their own
`source`, `edition`, `effective_date`, `evidence` and `parameters`; the cleanroom pack
also checks the vocabulary/order of zone classes, airlock roles and parent, and pressure
with an explicitly sourced guidance value. They explicitly do not
issue a GMP or any other regulatory verdict. `BuildingIR` can now link zones with hard required and
forbidden adjacency groups, required flows automatically increase
the effective door opening width, and equipment and routes use
verifiable access points. `generate-building` saves a single
`facility_validation` report; `check-building` recomputes it independently from
the saved result. The commercial pilot contains explicit process stages and a
waste branch with a dedicated `waste_hold` room for waste routing, and the
generated three-variant acceptance set passes deterministic facility/bundle QA;
IFC now contains derived flow route proxies with `Pset_LayoutFlow` and a check
status. Each variant also gets a `*.coordination.json` with BCF-like issue
records and a `*.bcf` with BCF-XML 2.1 topics/viewpoints and external references to
the program/DXF/PDF/IFC; DXF/PDF carry room dimensions, sheet metadata and labels for
flow types/clear width; the schema sidecar is located in
`schemas/coordination_issues.schema.json`; the profile structure is validated via
`schemas/facility_profile.schema.json`.
After the IFC is written, an independent read-back via IfcOpenShell is performed, checking
entity counts and flow metadata; the result is saved in `manifest.json` as
`ifc_readback`.
For a repeat iteration, `generate-building --bcf-input` carries topics that have disappeared
into the new BCF as `Closed`, preserving issue history without changing the canonical
facility validation report.
The `qa-building` command was added: in read-only mode it reconciles the JSON sidecar,
DXF/PDF, IFC read-back and BCF 2.1 against the current `BuildingIR` result.
Viewer QA and a BCF issue-management workflow were added to the read-only facility review:
open flow/equipment conflicts are automatically highlighted on the SVG, BCF
topics are normalized to `OPEN`/`RESOLVED`, and current issues and closed history
are displayed through status filters. BCF viewpoint coordinates are restored
for visual marking of closed issues. For a selected issue, Resolve,
Reopen, comment and assign are available; the audit trail is stored in a separate
`building_01.issue-management.json`, and the BCF/coordination JSON and manifest counters
are updated. Geometry remains read-only: edit/undo/redo/reset return HTTP 405.
The pharma and cleanroom profiles are now at version 0.2: the pharma sequence requires a flow type,
a mandatory declaration and a derived route for each stage, and cleanroom checks
ISO classes, parent-child ordering, personnel/material airlock roles, the parent link and
the 10 Pa guidance value from Annex 1. For missing input evidence,
`UNKNOWN` is kept; this is still project policy, not a regulatory verdict.
After the current pharma-cleanroom pilot is closed — a separate
spec → implementation → acceptance cycle for laboratory, hospital and industrial
with confirmed sources. The current order is given at the beginning of this document.
### Facility review surface

The `ui` command now auto-detects a generated `BuildingIR` result (`building_01.json`) and opens a read-only facility review. The server independently recomputes equipment, flow, and facility validation; the SVG projection overlays equipment footprints, service-clearance envelopes, derived flow routes, open conflict markers, and resolved BCF viewpoints. The side panel shows profile evidence, `OPEN`/`RESOLVED` filters, current coordination issues, BCF issue history, and JSON/DXF/PDF/IFC/BCF artifacts. A selected issue can be resolved, reopened, assigned, or commented; the audit trail is stored in `building_01.issue-management.json` and refreshes the BCF/coordination JSON projections. Geometry edit, undo, redo, and reset endpoints still return HTTP 405 in this mode.

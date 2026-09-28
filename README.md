# Facility Layout Compiler

## Domain rule packs

Rules for regulated facilities are defined by YAML profiles rather than hard-coded
into the validator. The `cleanroom`, `pharma`, `laboratory`, `hospital` and `industrial`
packs contain typed flow rules and their own `source`, `edition`,
`effective_date`, `evidence` and `parameters`.

The profile structure is checked against the schema
[`schemas/facility_profile.schema.json`](schemas/facility_profile.schema.json).
Results go into `facility_validation` and BCF-like issues together with a reference
to the applied rule. If the input data is insufficient, the result is
`UNKNOWN`; the system does not substitute regulatory numbers and does not issue a regulatory verdict.

A solver-first compiler of verifiable layouts for regulated and
technology-intensive facilities: pharmaceuticals, cleanrooms,
laboratories, hospitals and industry. It takes a facility program,
places rooms and equipment under hard constraints, checks
flows, clearances and geometry, then produces editable DXF, vector PDF, IFC and a
machine-readable audit trail.

This is not a generic "AI generates building layouts". The product's niche is cases where
a plausible picture is not enough: you need reproducible coordinates,
separation of people/materials/waste, equipment clearance and evidence for
every deterministic check. The full product statement and boundaries are
in the [Facility Product Brief](docs/FACILITY_PRODUCT_BRIEF.md).

## Quick start

```powershell
uv venv --python 3.11 .venv
uv pip install --python .venv\Scripts\python.exe -e .
.venv\Scripts\python.exe -m layout_configurator.cli generate examples/basic.yaml --output out --variants 2
```

`.venv` is created anew on every machine: it contains an absolute path to the
interpreter and stops working when the working copy is copied to another computer.
The directory is excluded from Git — just delete it and repeat the first two
commands.

Results will appear in `out/`: `layout_01.dxf`, `layout_01.pdf`,
`layout_01.ifc`, a JSON snapshot and `manifest.json`. If the environment already has a regular Python with `pip`, it is enough
to replace the first two commands with `python -m pip install -e .`. The same thing can
be run without installing the entry point:

```powershell
python -m layout_configurator.cli generate examples/basic.yaml -o out
```

Typed edit of an existing result:

```powershell
.venv\Scripts\python.exe -m layout_configurator.cli edit out\layout_01.json `
  --move-room hall 100 0 --output edited
```

Available options are `--move-room ROOM DX DY`, `--resize-room ROOM WIDTH HEIGHT`,
`--add-door ROOM_A ROOM_B`, `--add-door-at ROOM_A ROOM_B OFFSET_MM WIDTH_MM`,
`--remove-door DOOR_ID`, `--add-window ROOM SIDE OFFSET_MM WIDTH_MM` and
`--remove-window WINDOW_ID`, `--set-external-entry ROOM SIDE OFFSET_MM WIDTH_MM`
and `--remove-external-entry`. For `--add-door-at`, the external entry and a manual window,
`OFFSET_MM` is
the center of the opening measured from the bottom/left end of the corresponding side. After the command,
the modified geometry is fixed,
the remaining rooms are partially re-solved by CP-SAT and re-checked;
if constraints are violated the command is rejected and the original JSON is not
overwritten.

After an edit you can immediately run a ruleset; export results are saved even
on a regulatory FAIL, and exit code `4` lets you use the command in CI:

```powershell
.venv\Scripts\python.exe -m layout_configurator.cli edit out\layout_01.json `
  --move-room hall 100 0 --output edited `
  --rules rules\baseline.yaml
```

Checking IFC against an IDS template:

```powershell
.venv\Scripts\python.exe -m layout_configurator.cli validate `
  out\layout_01.ifc --ids ids\layout_baseline.ids
```

For the KZ projection a separate IFC4 exchange profile is available (this is a data contract,
not a regulatory verdict):

```powershell
.venv\Scripts\python.exe -m layout_configurator.cli validate `
  out\kz_entry_pass\layout_01.ifc --ids ids\kz_layout_exchange.ids
```

A local browser editor on top of the same typed commands:

```powershell
.venv\Scripts\python.exe -m layout_configurator.cli ui out\layout_01.json `
  --output out\ui --port 8765 `
  --rules rules\kz_sn_3_02_02_2023_partial.yaml --require-provenance
```

Open `http://127.0.0.1:8765/`. The UI shows an SVG projection of the current
`LayoutIR`, the validation status and links to the re-exported DXF/PDF/IFC/JSON;
the server accepts only known commands (`MoveRoom`, `ResizeRoom`, doors,
windows and the external entry) and does not accept coordinates as the source of truth. If
`--rules` is passed, the deterministic ruleset results are shown after each action.
On the canvas, dragging a room and resizing via the bottom-right handle work as a preview;
on release exactly one `MoveRoom` or `ResizeRoom` is sent, snapped to
`grid_mm`, after which the server validates and re-exports the result. The
undo/redo buttons restore valid `EditorState` snapshots, and the log shows the
sequence of typed commands and their payloads.

The core follows the solver-first principle: coordinates are produced by OR-Tools CP-SAT,
geometry is independently checked by the validator, and DXF/PDF are derived
representations of `LayoutIR`. IFC/BIM covers structure, walls, openings, space
relationships, semantic types and multi-storey projection; the UI and the LLM boundary
work as separate layers according to the [current product vision](docs/FACILITY_PRODUCT_BRIEF.md).

## Core product layer: FacilityIR on top of BuildingIR

The first narrow wedge is pharmaceutical clean production / cleanroom planning.
Its acceptance scenario includes separate personnel/material airlocks,
declared zone classes and a pressure cascade, equipment clearance, personnel/material/waste
routes, structural axes and verifiable checking.
Laboratories, hospitals and industrial profiles are plugged in as separate
domain/rule packs on top of the same contract, rather than being mixed into one set of
unverifiable rules.

The current `BuildingIR` is a coordinate-free canonical input: zones, equipment with
service envelopes, directed process flows and
structural axes. The full acceptance input is in
[`examples/pharma_cleanroom_pilot.yaml`](examples/pharma_cleanroom_pilot.yaml),
and its boundaries and external review gates are in
[`docs/PILOT_ACCEPTANCE_SPEC.md`](docs/PILOT_ACCEPTANCE_SPEC.md).

`generate-building` already solves rooms with CP-SAT, then places equipment
with a second CP-SAT pass that accounts for clearance, and produces `building_01.json`. For dense
facility programs, the default time limit of this command is 60 seconds:

```powershell
.venv\Scripts\python.exe -m layout_configurator.cli generate-building `
  examples\pharma_cleanroom_pilot.yaml --output out\pharma_cleanroom --variants 1 `
  --profile rules\pharma_cleanroom_pilot.yaml
```

By default the command applies the YAML-backed compatibility profile
`rules/default_facility.yaml`. For a regulated facility it must be replaced with
an explicit domain profile — for example:

```powershell
.venv\Scripts\python.exe -m layout_configurator.cli generate-building `
  examples\pharma_cleanroom_pilot.yaml --profile rules\pharma_cleanroom_pilot.yaml `
  --output out\pharma_cleanroom --variants 3 --max-attempts 9
```

The repository also contains separate domain rule packs
`rules\cleanroom_pilot.yaml`, `rules\laboratory_pilot.yaml`,
`rules\hospital_pilot.yaml` and `rules\industrial_pilot.yaml`. They contain
typed flow checks, and the cleanroom pack also contains the vocabulary/order of zone classes,
airlock roles and parent links, and pressure ordering with an explicitly stated sourced
guidance value. The pharma pack checks stage-flow types and required derived
routes. These are project policies, not GMP, healthcare, HSE, ISO or building
code checks.
Each profile may contain a `drawing` section (`sheet_id`, `discipline`,
`title`, `revision` and annotation flags) that controls the DXF/PDF projection.

Zone relationships become hard CP-SAT constraints: a required relationship requires
at least one pair of rooms to touch, and a forbidden one does not allow contact. For
required flows, the effective width of generated doors is automatically
raised to the maximum flow width; the result stores this value in the
canonical `spec` and evidence.

`generate-building` produces program/solver output and drawing projections: an editable DXF
with equipment blocks on `A-EQUIP` and dashed service clearances on `A-CLEARANCE`,
as well as a vector PDF. `building_01.json` additionally stores
`equipment_validation`, derived `flow_routes`, an independent `flow_validation` and
a unified `facility_validation` with the profile, statuses and evidence. Optional
flows (`required: false`) do not make the result FAIL when a route is missing;
if a route is built, it goes through the same geometric checks.
Next to it the command saves `building_01.coordination.json` — an explicit
`FLC-BCF-like-json` sidecar with stable issue IDs, severity, source,
endpoints and the route coordinate if the problem relates to a flow. The DXF/PDF
additionally contain room dimensions and flow type/clear width labels.
The sidecar format is described in [`schemas/coordination_issues.schema.json`](schemas/coordination_issues.schema.json);
in addition, a real BCF-XML 2.1 ZIP
`building_01.bcf` is automatically created alongside it: one topic per issue, `markup.bcf`, a `*.bcfv` viewpoint,
a snapshot and external references to the program/DXF/PDF/IFC. The JSON remains a compact
sidecar for automated processing.
In IFC, equipment is represented as `IfcBuildingElementProxy` with two property
sets. Derived flow routes are also exported as `IfcBuildingElementProxy` with
`Curve3D` and `Pset_LayoutFlow`: type, endpoints, room path, width, number of problems
and the status of the independent check. This is a coordination/evidence projection, not an MEP
or process-system model, not a GMP/medical approval and not an automatic
regulatory verdict.

After writing, the IFC is automatically re-opened via IfcOpenShell and
key entity counts and flow metadata are cross-checked. In `manifest.json` this is reflected in
`ifc_readback`; to manually check an existing result you can pass
`check-building --ifc path\to\building.ifc`.

A saved result can be re-checked with a separate deterministic run:

```powershell
.venv\Scripts\python.exe -m layout_configurator.cli check-building `
  out\commercial_pilot\building_01.json --json
```

For a re-check with a separate issue file, add
`--issues-output out\commercial_pilot\recheck.coordination.json`.
For a separate BCF package on re-check, add
`--bcf-output out\commercial_pilot\recheck.bcf`.
On the next iteration you can pass the previous package via
`generate-building --bcf-input out\commercial_pilot\building_01.bcf`:
topics that have disappeared from the current validation report go into the new BCF as
`Closed`, and errors that return will be `Open` again.

For a full check of an already generated bundle, use:

```powershell
.venv\Scripts\python.exe -m layout_configurator.cli qa-building `
  out\commercial_pilot\building_01.json --json
```

The command cross-checks the JSON sidecar, DXF, PDF, IFC read-back and BCF 2.1 against the current
facility validation report.

For a full acceptance check, use the seed matrix. The command publishes
only variants that passed the room/equipment/flow/profile gates; rejected
candidates and the seeds used remain in `manifest.json`. If for any
seed the full requested set is not found within the given search budget, the command
returns an error. This is not a proof that the program is mathematically infeasible.
`generation-failure.json` stores the input program, the profile, the search parameters
and all rejection reasons for the checked candidates. The matrix includes this report
in the seed result; without a successful check of all seeds, the identifier comparison
does not get `PASS`.

```powershell
.venv\Scripts\python.exe -m layout_configurator.cli acceptance-building-matrix `
  examples\pharma_cleanroom_pilot.yaml `
  --profile rules\pharma_cleanroom_pilot.yaml `
  --output out\pharma_cleanroom_acceptance `
  --variants 3 --seeds 1 7 42 --max-attempts 9 --time-limit 30
```

For each seed the command runs `qa-building-set`: it checks that
semantic room/equipment/flow IDs, route topologies, IFC read-back
identifiers and BCF 2.1 topic identities match across variants. The shared
`acceptance-matrix-report.json` stores the results of each run, SHA-256 of
artifacts, search parameters and the check of semantic IDs across seeds.
`attempted_candidates` is the number of candidates obtained from the room solver;
`evaluated_candidates` is the number of candidates that went through selection until the
required set was obtained. The time limit applies to each solver run, not to the whole
matrix.

The room stage is chosen with `--room-solver` (on `generate-building` and
`acceptance-building-matrix`):

- `auto` (default) — the hierarchical solver in `hierarchy.py` first: rooms are
  grouped into hub-and-leaf clusters (a corridor with the rooms that hang off
  it), each cluster is solved as a small layout, and the top level only places
  the clusters (offset, shape, mirror, quarter turn) with every cross-cluster
  contact, daylight and entry constraint posted exactly on the rooms. If the
  hierarchy does not apply (for example boundary cutouts) or returns too few
  candidates, it falls back to the monolithic model.
- `hierarchical` — hierarchy only; fails instead of falling back.
- `monolithic` — the original single CP-SAT model for all rooms.

`manifest.json` records `room_solver`, `room_solver_used` and, after a fallback,
`hierarchy_fallback_reason`. The independent gates judge every candidate the
same way whichever solver produced it. On the pilot, the seeds 1/7/42 matrix
drops from about 13.5 minutes to about 11 seconds; measurements are in
[`docs/SCALING_STUDY.md`](docs/SCALING_STUDY.md).

For a reproducible run there is a separate mode: `--deterministic-budget UNITS`
spends a machine-independent amount of CP-SAT work instead of seconds, forces
single-threaded search and removes the wall-clock limit. The meaning of
`--time-limit` does not change — it remains a wall-clock budget.

```powershell
.venv\Scripts\python.exe -m layout_configurator.cli generate-building `
  examples\pharma_cleanroom_pilot.yaml --profile rules\pharma_cleanroom_pilot.yaml `
  --output out\repeatable --variants 2 --max-attempts 9 --deterministic-budget 10 --seed 1
```

A budget unit is not a second: on the development machine it is ≈ 3.4 s, so 10 units
roughly correspond to the former `--time-limit 30`. Calibration, the condition for stable
model order and the full list of limitations are
in [`docs/DETERMINISTIC_BUDGET.md`](docs/DETERMINISTIC_BUDGET.md).

To choose between accepted variants, `compare-variants` ranks them and writes
`comparison.json`, `comparison.csv` and a one-page client PDF with a plan
thumbnail per variant:

```powershell
.venv\Scripts\python.exe -m layout_configurator.cli compare-variants out\pharma_cleanroom_acceptance\seed_1
```

The ranking key is printed with the result: failed gates, then open issues,
then total route length, then deviation from the programmed areas. Every number
comes from the saved `building_NN.json`; nothing is re-solved. It is a design
aid for choosing between valid options, not a quality or compliance verdict.

The hierarchical solver asks each further variant to be a different option: at
least a quarter of the placed clusters and rooms must move by 3 m or change
shape (relaxed only when no such layout is found in the budget), and a short
second pass brings single rooms back to their programmed areas.

For the three external gates, the accepted bundle is projected into role-specific dossiers:

```powershell
.venv\Scripts\python.exe -m layout_configurator.cli review-dossier `
  out\acceptance-2026-09-06\bundle --profile rules\pharma_cleanroom_pilot.yaml `
  --output out\acceptance-2026-09-06\review
```

The command writes `building_0N.technologist.md`, `building_0N.cleanroom-hvac.md`,
`building_0N.architect-bim.md` and `building_0N.artifact-inventory.json` with
IFC entities, DXF layers and SHA-256 of the variant's files. A dossier contains only what
is already in the canonical result, a decision block for the reviewer and an explicit
list of what has not been checked; `PASS` in it does not become a regulatory verdict.

The contents of the package for external reviewers, the checklists for the process technologist/QA, cleanroom/HVAC and
architect/BIM, and the reproduction commands are in
[`docs/ACCEPTANCE_REVIEW_PACKAGE.md`](docs/ACCEPTANCE_REVIEW_PACKAGE.md).
The results of browser QA of the read-only viewer, including open projection defects, are
in [`docs/VIEWER_QA_2026-09-05.md`](docs/VIEWER_QA_2026-09-05.md).

Local regression tests: `.venv\Scripts\python.exe -m unittest discover -s tests -v`.
The `.github/workflows/ci.yml` workflow runs the tests on Windows/Linux and then
the full pharma-cleanroom matrix on Windows. Bundles and reports, including errors,
are saved as CI artifacts. External expert acceptance conditions remain
a separate stage.

For the read-only viewer you can pass the whole acceptance directory and select a variant:

```powershell
.venv\Scripts\python.exe -m layout_configurator.cli ui `
  out\pharma_cleanroom_acceptance\seed_1 --variant 2 `
  --profile rules\pharma_cleanroom_pilot.yaml
```

`--variant` selects `building_02.json` and its neighboring DXF/PDF/IFC/BCF/JSON
artifacts; variants 1 and 3 are opened the same way.

### Facility review and BCF issue history

The `ui` command automatically opens an existing `building_01.json` as a
read-only facility review. The SVG shows derived routes, equipment service
clearances and automatically highlights current flow/equipment
conflicts. The side panel shows `OPEN`/`RESOLVED` statuses, an issue filter,
the current list of conflicts and the history of BCF topics; closed viewpoints are marked
on the plan using BCF coordinates. For the selected issue, `Resolve`, `Reopen`,
comment and assign are available; actions are saved to `building_01.issue-management.json`
and update the BCF/coordination JSON and manifest counters. Geometry remains
read-only: edit/undo/redo/reset in review return HTTP 405.

## MVP limitations

- a single storey and orthogonal rectangular rooms;
- the default coordinate grid is 100 mm;
- the outline is a bounding box with rectangular subtracted zones;
- adjacency means a shared boundary no shorter than the door width;
- walls are exported as double lines with thickness and door cutouts;
- windows for rooms with `needs_daylight` are exported automatically; manual windows
  can be added via `edit --add-window ROOM SIDE OFFSET_MM WIDTH_MM`;
- for `needs_daylight` the solver requires the room to touch an exterior side or
  a cutout, so that the automatic window does not disappear from the result;
- the external entry is set via `external_entry`, fixed by the solver on an exterior
  side of the room and exported as a separate `IfcDoor`/opening;
- `is_heated` is stored on the room and used by jurisdiction checks, but does not
  replace heating or building-services calculations; the KZ profile only checks
  the completeness of this declaration for the selected room types;
- IFC contains the spatial structure, rooms, continuous walls, doors,
  windows and `IfcOpeningElement` with `IfcRelVoidsElement`/`IfcRelFillsElement`;
  `IfcRelSpaceBoundary`, basic property sets, materials and an IDS template are already present;
  jurisdictional
  regulatory checks will be a separate stage;
- DXF/PDF are drawing output, not a building permit and not a final
  building-code check.

## Deterministic ruleset check

A versioned set of project rules can be run against layout JSON:

```powershell
.venv\Scripts\python.exe -m layout_configurator.cli check `
  out\layout_01.json --rules rules\baseline.yaml
```

For a CI check of the regulatory profile's source, add
`--require-provenance`; it requires authority, edition, effective_date,
source_url and document_hash.

The first partial profile for Kazakhstan (verified slices on daylighting
per cl. 7.8, forbidden adjacency per cl. 6.2.13, vestibule per cl. 6.2.8
and connection of auxiliary rooms per cl. 6.2.12, as well as the heating declaration
per cl. 8.19
of SN RK 3.02-02-2023):

```powershell
.venv\Scripts\python.exe -m layout_configurator.cli check `
  out\layout_01.json --rules rules\kz_sn_3_02_02_2023_partial.yaml `
  --require-provenance
```

To demonstrate a passing profile, use the example where the kitchen is also
marked as requiring daylight:

```powershell
.venv\Scripts\python.exe -m layout_configurator.cli generate `
  examples\kz_daylight.yaml --output out\kz_daylight --variants 1
.venv\Scripts\python.exe -m layout_configurator.cli check `
  out\kz_daylight\layout_01.json --rules rules\kz_sn_3_02_02_2023_partial.yaml `
  --require-provenance
```

The vestibule requirement check per cl. 6.2.8 is demonstrated by separate examples:

```powershell
.venv\Scripts\python.exe -m layout_configurator.cli generate `
  examples\kz_entry_pass.yaml --output out\kz_entry_pass --variants 1
.venv\Scripts\python.exe -m layout_configurator.cli check `
  out\kz_entry_pass\layout_01.json --rules rules\kz_sn_3_02_02_2023_partial.yaml `
  --require-provenance

.venv\Scripts\python.exe -m layout_configurator.cli generate `
  examples\kz_entry_fail.yaml --output out\kz_entry_fail --variants 1
.venv\Scripts\python.exe -m layout_configurator.cli check `
  out\kz_entry_fail\layout_01.json --rules rules\kz_sn_3_02_02_2023_partial.yaml `
  --require-provenance
# Expected exit code of the last command: 4.
```

`rules/baseline.yaml` checks geometry, minimum areas by room type,
corridor width, windows for `needs_daylight`, reachability from the entry and maximum
route length. The result contains `PASS`/`FAIL`/`NOT_APPLICABLE`, the source,
the ruleset clause and evidence for each rule; `--json` produces a machine-readable report.

A jurisdictional profile can be kept as a separate YAML file and inherit from baseline
via `extends: baseline.yaml`, overriding only the needed rules and thresholds;
nested `params` are merged with the base ones.
For auditing, a profile can store `provenance` with the issuing authority, edition,
effective date, URL and hash of the source document; this data goes into the JSON report.
The profile must be selected and verified by a human; without that, baseline remains
a project self-check, not a building code.

This is a configurable generic baseline for project self-checking, not a universal
building code and not a building-permit decision. Jurisdictional
profiles should be added as separate ruleset files once a specific
jurisdiction has been fixed. Numerical decisions are made by code; LLM/RAG do not take part in the verdict.

## JSON Schema boundary

The canonical `LayoutIR` contract is described in
[`schemas/layout_ir.schema.json`](schemas/layout_ir.schema.json). Checking
the normalized convenience YAML:

```powershell
.venv\Scripts\python.exe -m layout_configurator.cli schema examples\basic.yaml
```

Strict check of the file as is, without normalization and without silently dropping
unknown fields:

```powershell
.venv\Scripts\python.exe -m layout_configurator.cli schema input.json --raw
```

`--raw` is the boundary of the future LLM parser. The parser may only output JSON conforming to the
schema; coordinates, DXF and the regulatory verdict are not given to it.

Strict normalization of canonical JSON/YAML can be run separately:

```powershell
.venv\Scripts\python.exe -m layout_configurator.cli normalize input.json `
  --output canonical.json
```

For a ready-made example, use `examples/basic_canonical.json`; the regular
`examples/basic.yaml` remains shorthand for the `generate` command.

For a text brief there is a local constrained parser without access to coordinates:

```powershell
.venv\Scripts\python.exe -m layout_configurator.cli parse-brief `
  examples\brief.txt --output out\brief_canonical.json
.venv\Scripts\python.exe -m layout_configurator.cli generate `
  out\brief_canonical.json --output out\brief --strict-input
```

A future LLM provider must return only such canonical JSON; the
`parse_llm_mapping` function first runs it through JSON Schema and rejects
coordinates, generated data and unknown fields. For the RAG layer there is a safe
reference search that does not check the layout (the query below is Russian because
it is matched against the Russian-language text of the KZ rule profile; it means
"kitchen daylighting"):

```powershell
.venv\Scripts\python.exe -m layout_configurator.cli cite `
  естественное освещение кухни --rules rules\kz_sn_3_02_02_2023_partial.yaml
```

If an external OpenAI-compatible provider is available, you can enable a real
LLM call without passing it the solver result or the regulatory verdict:

```powershell
$env:LAYOUT_LLM_ENDPOINT = "https://provider.example/v1/chat/completions"
$env:LAYOUT_LLM_MODEL = "your-model"
$env:LAYOUT_LLM_API_KEY = "your-key"
.venv\Scripts\python.exe -m layout_configurator.cli parse-llm `
  examples\brief.txt --output out\llm_canonical.json
```

The provider's response is accepted only after `parse_llm_mapping` and JSON Schema.

For two or more levels, shared axis coordination is used: repeated
vertical-core rooms are fixed on a single rectangular grid, and the stair
width and declared axes are checked after solving:

```powershell
.venv\Scripts\python.exe -m layout_configurator.cli generate-multifloor `
  examples\multifloor.yaml --output out\multifloor
```

The command also creates a shared `out\multifloor\multifloor.ifc` with several
`IfcBuildingStorey` and `IfcStair`; separate per-storey folders are kept for
local editing and round-trip.

The command first checks the schema, then creates `LayoutIR`; the solver result
and coordinates are not accepted at this input contract boundary.

To run the solver directly with the same strict input, use
`generate --strict-input`; shorthand without the required canonical fields will be
rejected before CP-SAT is run.
### Facility review UI

Opening a generated `BuildingIR` result automatically selects the read-only facility review:

```powershell
.venv\Scripts\python.exe -m layout_configurator.cli ui `
  out\commercial_pilot\building_01.json --profile rules\pharma_clean_production.yaml
```

The review projects rooms, process equipment, service-clearance envelopes, derived people/material/waste routes, structural axes, deterministic facility evidence, coordination issues, and the generated JSON/DXF/PDF/IFC/BCF artifacts. It recomputes the facility report on open and rejects edit, undo, redo, and reset commands with HTTP 405.

### Facility editor

`ui --edit` opens the same bundle as an editor:

```powershell
.venv\Scripts\python.exe -m layout_configurator.cli ui `
  out\pharma_cleanroom_acceptance\seed_1 --profile rules\pharma_cleanroom_pilot.yaml --edit
```

Dragging or resizing a room sends one typed `move_room`/`resize_room` command.
The edited room stays where it was put; the other rooms stay put when the
geometry allows it, otherwise the room solver repairs the layout starting from
their current positions. Equipment is re-packed, flows are re-routed and every
facility check is recomputed, usually within about a second on the pilot. An
edit that leaves invalid room geometry is refused and nothing changes; an edit
that only fails a facility gate is kept and shown as failing. Undo, redo and
reset work on server-side states. Door, window and flow changes belong in the
program, not in the editor.

**Save revision** writes the current state as a complete bundle to
`revisions/rev_NN/` beside the original (IFC with read-back, DXF, PDF, JSON,
coordination issues and a BCF that continues the original issue history). The
generated bundle itself is never modified, and `generation.edits` in the
revision JSON records the commands that produced it.

### Multi-storey facilities

`generate-facility-floors` solves a facility over several floors. Each floor is
an ordinary facility program with its own profile; a small file adds what
connects them — vertical cores and cross-floor flows
(`examples/pharma_two_floor.yaml`):

```powershell
.venv\Scripts\python.exe -m layout_configurator.cli generate-facility-floors `
  examples\pharma_two_floor.yaml --output out\two_floor --variants 2 --time-limit 30
```

- **Vertical cores** (`stair`, `passenger_lift`, `goods_lift`, `service_shaft`)
  are rooms declared on every floor they serve. The lowest floor places them;
  the other floors are solved with those rooms pinned to the same rectangle.
- **Cross-floor flows** become an ordinary flow on each end — to the core on
  the departure floor, from the core on the arrival floor — so the normal
  routing and flow gates check both legs (`<flow>@L0`, `<flow>@L1`).
- **Vertical checks** (`CORE_ALIGNMENT`, `CORE_FLOW_TYPES`, `CORE_CLEAR_WIDTH`,
  `CROSS_FLOOR_ROUTES`) are recomputed from the results. Which flow types a core
  kind may carry is a project policy (`CORE_FLOW_TYPES` in
  `facility_floors.py`), not a regulatory statement.

The output has a normal bundle per floor (`floor_00/`, `floor_01/`, where
variant *n* of every floor belongs to the same stack) and
`multi_floor_report.json`. `ui`, `qa-building` and `compare-variants` work on
each floor directory as on any bundle.

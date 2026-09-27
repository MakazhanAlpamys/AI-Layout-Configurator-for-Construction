# Original MVP roadmap archive

> This document preserves the decisions and history of the original residential MVP. It **does not
> define the current product goal**. The current direction is the
> [Facility Product Brief](FACILITY_PRODUCT_BRIEF.md) and the
> [Regulated Facility Layout Compiler plan](PRODUCT_PLAN.md).
> Based on a synthesis of 8 independent research reports in [`research/`](../research/).

## Current decision — 2026-09-02

The project has been refocused on a solver-first compiler of verifiable layouts for
pharmaceuticals, cleanrooms, laboratories, hospitals and industrial facilities.
The first narrow wedge is pharma-like clean production; the other typologies will be
added as separate domain/rule packs. The principles of solver-first, canonical IR,
independent validation and the ban on LLMs producing coordinates/DXF/normative verdicts remain
unchanged.

## What was built in the MVP

A layout configurator: design brief (overall dimensions, room list,
target areas, adjacencies) → several layout variants →
editable DXF + vector PDF.

**Architectural principle (consensus of all 8 reports): solver-first hybrid.**
Geometry is computed by a deterministic solver, not a neural network.
The LLM is connected last and never outputs coordinates,
never writes DXF directly and never issues a verdict on building codes.

## Historical decisions of the original MVP

| Date | Decision | Rationale |
|---|---|---|
| 2026-08-29 | Building type: single-storey residential layout (house or apartment) | At the core level this is one problem; an apartment building and test-fit are extensions on top of the same core |
| 2026-08-29 | Interface: CLI + files (YAML/JSON as input, a folder with DXF/PDF as output) | Fastest path to a working result; zero time spent on UI layout |
| 2026-08-29 | Level of detail: a real architectural plan — walls with thickness, openings, dimension chains, title block | Without this the DXF is useless |
| 2026-08-29 | Project for personal use, not for sale | Removes licensing restrictions: GPL/AGPL and research-only datasets are available |
| 2026-08-29 | Building outline: bounding box minus subtracted rectangular zones | Covers L-, U- and T-shaped houses while barely complicating the solver |
| 2026-08-29 | A corridor is specified in the brief as an ordinary room rather than carved out by the solver | Less magic, more predictable result. The solver checks that all rooms are reachable from the entrance |

## Decomposition into subprojects

Each subproject has its own cycle: spec → plan → implementation.
The order is not arbitrary: each subsequent one builds on the previous.

- [x] **1. Core: LayoutIR + solver + drawing** ← *MVP implemented 2026-08-30*
  - Typed `LayoutIR` model (rooms, areas, adjacencies, openings, walls) as the single source of truth
  - Solver on OR-Tools CP-SAT: packing of rectangular rooms in a rectangular outline
  - Wall module: centerlines → thicknesses → corner cleanup → cutting openings
  - DXF export (ezdxf) and vector PDF (ReportLab)
  - Geometry validation (Shapely): areas, overlaps, boundaries, adjacencies
  - CLI: `layout-configurator generate spec.yaml --output out --variants N`
  - Implemented: wall band via buffer with double lines, mitred corners
    and door cut-outs on required adjacencies
  - Limitation of the current slice: windows are generated automatically for
    `needs_daylight`, or specified explicitly in `LayoutIR.windows`; rooms with
    `needs_daylight` are anchored by the solver to an exterior face or cut-out; full
    interactive refinement in the editor UI is implemented in subproject 2
- [x] **2. Editor** ← *command MVP, local UI, history and visual preview implemented 2026-08-31*
  - Typed `MoveRoom`, `ResizeRoom`, `AddDoor`, explicit `DoorSpec`, `RemoveDoor`, `AddWindow`, `RemoveWindow` with atomic validation
  - CLI editing of saved JSON and re-export of DXF/PDF
  - optional regulatory post-check via `edit --rules` with exit code 4 on FAIL
  - external entrance `external_entry`, `is_heated` flag, solver anchoring and IFC metadata
  - local browser UI with an SVG plan, typed commands and re-export
  - optional ruleset post-check in the UI after each command
  - drag/resize on the SVG with preview and a single `MoveRoom`/`ResizeRoom` sent on release
  - undo/redo with re-export of derived files and history branching
  - typed command log and a visible preview grid/dimensions based on `grid_mm`
- [x] **3. IFC / BIM** ← *IFC4, baseline IDS and KZ exchange profile implemented 2026-08-31*
  - `IfcProject` → `IfcSite` → `IfcBuilding` → `IfcBuildingStorey`
  - `IfcSpace`/`IfcWall`/`IfcDoor`/`IfcWindow`, geometry, areas and CLI round-trip
  - Implemented: `IfcRelSpaceBoundary`, `IfcOpeningElement`, `IfcRelVoidsElement`
    and `IfcRelFillsElement`; walls in IFC are now continuous, openings are linked to their host
  - Implemented: windows for `needs_daylight`, property sets, basic materials
    and `ifctester` checking against `ids/layout_baseline.ids`
  - Implemented: semantic `IfcWallType`/`IfcDoorType`/`IfcWindowType` and
    `IfcRelDefinesByType`; a jurisdictional exchange profile added
  - `ids/kz_layout_exchange.ids` pins the KZ-oriented exchange of room, door and
    window properties; this is a data contract, not a building code check
- [x] **4. Building codes** ← *generic baseline and a partial verified KZ profile implemented 2026-08-31* — deterministic checks: minimum areas, corridor widths, egress. One jurisdiction at a time
- [x] **5. LLM brief parser + RAG over codes** ← *constrained text parser, Schema boundary, optional OpenAI-compatible adapter and citation retrieval implemented 2026-08-31* — text → JSON with a schema; RAG cites the clause, code makes the decision
- [x] **6. Multi-storey** ← *floor coordination, vertical cores, grid lines, stair check and a shared IFC multi-storey projection implemented 2026-08-31* — vertical cores, alignment of load-bearing walls, stairs

### Archived MVP status (2026-08-31)

A generic baseline ruleset is implemented: `GEOMETRY_VALID`, `MIN_ROOM_AREA`,
`MIN_CORRIDOR_WIDTH`, `DAYLIGHT_OPENING`, `EGRESS_REACHABILITY` and
`MAX_EGRESS_DISTANCE`. The CLI command `check` outputs evidence for each rule
and does not issue a legal verdict. The loader supports separate ruleset files
for specific jurisdictions, `extends`, targeted rule overrides and document source
provenance on top of the baseline. The first partial KZ profile has been added: clauses 7.8,
6.2.13, 6.2.8, 6.2.12 and the declaration of clause 8.19 of SN RK 3.02-02-2023,
without claiming a full regulatory check.
A passing smoke test `examples/kz_daylight.yaml` has been added; the basic demo with a kitchen
without `needs_daylight` intentionally shows a FAIL under the same rule.
`examples/kz_entry_pass.yaml` and `examples/kz_entry_fail.yaml` have been added for
checking the entrance vestibule: the external entrance is pinned by the solver, and a regulatory FAIL
returns exit code 4 after the derived files are saved.

The KZ profile inherits the generic baseline, so the project checks for area,
corridor width and reachability are also available in a jurisdictional run. A
separate IFC4 exchange profile `ids/kz_layout_exchange.ids` has been added for the semantic
properties of rooms, doors and windows. It checks the data contract, not building
codes.

Ahead of the LLM layer, a strict JSON Schema boundary was prepared for the canonical
`LayoutIR` (`schemas/layout_ir.schema.json`) and the CLI `schema --raw`; the text
parser must pass through it and has no access to coordinates,
exports or code decisions.

After the core stabilized, `parse-brief` was added for constrained text-to-LayoutIR,
`parse_llm_mapping` for a future provider, and `cite` for retrieving references to
rules. The parser does not accept coordinates, and `cite` does not run `check` and does not
issue a verdict. For the multi-storey extension, `generate-multifloor`,
vertical cores, structural grid lines, a stair check and a shared IFC were added.

## Fixed project boundaries

- The KZ ruleset remains partial and is extended only by individually confirmed
  clauses of the official source; the current model does not pretend to be a full legal
  code check.
- The neural network layer (HouseDiffusion as a seed) is not part of the closed loop:
  coordinates are always produced by CP-SAT, and the LLM is used only as an opt-in
  producer of canonical JSON.
- Visual checking of DXF is done in a CAD viewer (AutoCAD/LibreCAD/ODA Viewer)
  when available; the automated loop already checks round-trip and exported
  entities, but does not substitute for a human looking at the drawing.

## Dead ends — do not waste time

Unanimous across all 8 reports:

1. **A neural network generating DXF/DWG directly** — the format is byte-sensitive, the files will not open
2. **Machine-readable building codes do not exist** — IDS checks data, not geometry; geometric rules are written by hand
3. **Open DWG writing** — LibreDWG is stable only up to R2000; export DXF and convert with ODA File Converter
4. **RAG that "checks" codes** — LLMs hallucinate precisely on numeric thresholds
5. **CP-SAT beyond ~15 rooms with a non-orthogonal outline** — NP-hard; fine for housing, not for a hospital
6. **Computing floors independently** — load-bearing walls of the upper floor will hang over empty space; compute all floors on a single grid

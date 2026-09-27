# Facility Layout Compiler: product goal

## Decision

The project is no longer positioned as a generic "AI generates building floor plans".
Its goal is a **solver-first compiler of verifiable layouts for regulated and
technology-intensive facilities**:

- pharmaceutical production and cleanroom areas;
- laboratories;
- hospital and medical functional units;
- industrial and warehouse/production facilities.

Product name: **Facility Layout Compiler**. `BuildingIR` remains the current
canonical contract in the code; `FacilityIR` is the direction in which it will evolve, not
a rename for the sake of renaming.

## Why this product is different

A generic layout generator competes on image speed, style and number of
variants. For the target niche that is not enough. A solution has value only
if it is possible to verify and explain:

1. where the equipment is placed and whether the service clearance is sufficient;
2. whether people, raw materials, product, waste and service follow permitted routes;
3. whether the required zones are separated and which relations between them are mandatory;
4. which rules were checked, with which version and with what evidence;
5. how one and the same result ends up in editable DXF, vector PDF, IFC and JSON.

Therefore an LLM may help extract the program from a design brief or find a reference to a rule,
but it does not produce coordinates, DXF/IFC or a regulatory verdict. Geometry and
checks remain deterministic.

## First wedge

The first profile is **pharma-like clean production / cleanroom planning**. It is
narrow enough to validate the model with a process engineer and an architect, and
complex enough to distinguish the product from a residential floor planner:

- receiving, raw storage, preparation, clean production, packaging,
  finished storage, dispatch and staff zones;
- equipment footprints, fixed wall anchors and service zones;
- flows of personnel, materials, finished goods, waste and service;
- corridors and door openings with a minimum width;
- structural grid lines, rooms, walls, openings and output drawings.

This is **not** a claim of GMP compliance, room cleanliness classification or
the right to issue a design. Such statements appear only with a confirmed
rule profile, its version and expert review.

## Users and outcome

| Role | What they get |
|---|---|
| Process engineer / facility planner | Layout variants with equipment and flows instead of manual trial and error. |
| Architect / BIM coordinator | Editable DXF and IFC, not a raster image. |
| QA / validation / project manager | Versioned JSON evidence: constraints, PASS/FAIL/UNKNOWN and a stable seed. |
| Client's engineer | Clear boundaries of automation and a list of conditions that require an expert. |

## Result contract

```text
design brief / existing DXF / tabular program
                    ↓
canonical BuildingIR → future facility domain packs
                    ↓
CP-SAT: rooms → equipment → routes
                    ↓
independent geometry and rule validation
                    ↓
DXF + vector PDF + IFC + JSON evidence bundle
```

Each arrow preserves identifiers and does not turn a derived artifact into a
source of coordinates. Changes go through typed commands and re-validation.

## Current state

Already implemented for the single-floor pilot:

- `BuildingIR`: zones, equipment, flows and structural grid without input coordinates;
- CP-SAT room layout and separate CP-SAT equipment packing;
- hard zone adjacency groups for required/forbidden functional relations;
- service clearance, rotations, wall anchors and `NoOverlap2D`;
- independent equipment/flow/facility validator and JSON audit evidence;
- automatic effective door width promotion for required process flows and
  equipment front-edge access points for routing;
- derived IFC flow route proxies with route metadata and validation status;
- stable BCF-like JSON sidecars plus BCF-XML 2.1 ZIP topics/viewpoints;
- IFC read-back of entity counts and route metadata before a bundle is accepted;
- readable drawing annotations for room sizes, flow types and clear widths;
- YAML-driven sheet metadata and annotation toggles for DXF/PDF;
- editable DXF, vector PDF and IFC equipment proxies;
- structural grid lines in DXF/PDF;
- versioned YAML rule packs for `pharma`, `cleanroom`, `laboratory`, `hospital`
  and `industrial` domains;
- `check-building` round-trip validation command;
- a full acceptance example `examples/pharma_cleanroom_pilot.yaml`: 13 rooms,
  personnel/material airlocks, parent-linked ISO class labels, declared pressure
  cascade, process stages and waste branch. `rules/pharma_cleanroom_pilot.yaml`
  combines process, cleanroom and flow checks with provenance; a variant can be
  published only after independent geometry/equipment/flow/profile checking.
  `acceptance-building-matrix` runs several seeds and does not report a partial
  set of variants as a successful result.

Technical check of 2026-09-05: 126 tests passed; the standard matrix of
seeds 1/7/42 produced three accepted variants each, with successful bundle QA and identifier
checks. Parameters: 30 seconds per solver, up to nine candidates per seed.
Details and the remaining external conditions are in [HANDOFF.md](HANDOFF.md).

This is not yet a finished pharmaceutical or medical system. Current rooms are
rectangular, flow routing is limited to 2D pilot logic, and rule packs do not replace
regulatory expert review.

## Auditable domain rule packs

All facility rules are stored in YAML profiles. Every rule must have
`id`, `kind`, `source`, `edition`, `effective_date`, `evidence` and `parameters`;
the check result keeps the same provenance link in `facility_validation` and
BCF-like issues.

The repository contains separate packs:

- `rules/pharma_cleanroom_pilot.yaml` — composite acceptance profile of the first
  wedge: process stages, dirty/clean route separation, airlock roles, ISO
  vocabulary, pressure ordering and waste branch;
- `rules/cleanroom_pilot.yaml` — vocabulary/order of ISO zone classes, pressure
  ordering with a sourced guidance value, parent-linked personnel/material airlocks and
  clean/dirty flow separation;
- `rules/pharma_clean_production.yaml` — material, personnel, finished goods and
  waste declarations, process-stage type contract and mandatory checking of
  derived routes;
- `rules/laboratory_pilot.yaml` — specimen, personnel, clean supply and waste;
- `rules/hospital_pilot.yaml` — patient, personnel, clean supply, dirty supply
  and waste;
- `rules/industrial_pilot.yaml` — material, personnel, vehicles, hazardous
  materials, maintenance and waste.

`schemas/facility_profile.schema.json` validates the pack structure. Missing
input evidence yields `UNKNOWN`, not an invented PASS. Regulatory numbers
are not added by default: a numeric parameter is allowed only as an explicitly
set parameter of a specific project profile with its own reference and
evidence. All profiles are explicitly marked as project policy, not a regulatory verdict.

## Product profiles — not one universal code database

A profile consists of three independent parts:

1. **Domain vocabulary** — types of zones, rooms, equipment and flows.
2. **Deterministic geometry checks** — clearance, separation, routes,
   openings, egress and other confirmed constraints.
3. **Data/IFC exchange contract** — required properties and IDS checks.

Pharma/cleanroom, laboratory, hospital and industrial profiles are versioned
separately. An inapplicable rule returns `NOT_APPLICABLE`, and an unknown
condition returns `UNKNOWN`; the system does not mask them as PASS.

## Next sequence

1. **External acceptance.** Pass expert review of the pharma process/quality and
   cleanroom/HVAC assumptions against the contracts in
   [`PILOT_ACCEPTANCE_SPEC.md`](PILOT_ACCEPTANCE_SPEC.md). Until then the profile
   remains project policy, not a regulatory verdict.
2. **Coordination exchange.** Re-reading IFC flow proxies, BCF-XML 2.1
   topics/viewpoints and read-only viewer screenshot QA: conflicting
   routes/equipment are highlighted, `OPEN`/`RESOLVED` are filterable, and BCF
   history is visible on the plan.
3. **Confirmed domain packs.** Only after the pharma-cleanroom pilot is closed,
   start laboratory, hospital and industrial as separate spec → implementation
   → acceptance cycles together with domain experts and confirmed sources.

## What we do not promise

- a neural network that draws the final drawing;
- "full compliance with GMP", a hospital code or any other regulation without
  a specific confirmed profile and expert sign-off;
- a universal database of codes for all countries;
- issuing a building permit;
- full MEP, process engineering or structural calculation.

It is precisely these boundaries that make the product verifiable rather than merely
convincing-looking.

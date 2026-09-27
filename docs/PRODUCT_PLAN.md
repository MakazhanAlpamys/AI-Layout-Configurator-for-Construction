# Product Plan: Regulated Facility Layout Compiler

## 1. Why we are expanding the project

The current `LayoutIR` MVP remains the technical foundation: it has proven
deterministic packing, wall geometry, DXF/PDF/IFC and independent verification.
It is no longer the product goal.

The product goal is a system for regulated and technology-intensive
facilities: pharmaceuticals, cleanrooms, laboratories, hospitals and
industry. In such facilities the main output is not a picture but a
reproducible and verifiable variant: zones, equipment, flows,
constraints, DXF/PDF/IFC and evidence.

The first commercially meaningful wedge is **pharmaceutical clean production /
cleanroom planning**. It requires exactly the capabilities that set the
product apart: flow segregation, clearance, fixed equipment, strict
zones and an audit trail. Laboratory, hospital and other industrial profiles will
be added as separate contract/rule packs once their constraints are confirmed.

`ROADMAP.md` is considered closed as the plan for the original MVP. This document describes
the next product layer and does not revoke the accepted constraints: solver-first,
a canonical model, derived DXF/PDF/IFC, and no regulatory verdict
from an LLM.

## 2. Product statement

The **Regulated Facility Layout Compiler** takes a facility program and produces
verifiable spatial layout variants.

```text
Brief / PDF / existing DXF
        ↓
Structured facility program
        ↓
BuildingIR: zones, rooms, equipment, flows, structure, rules
        ↓
Hierarchical solver and geometry engine
        ↓
Geometric, functional and regulatory checks
        ↓
DXF + vector PDF + IFC + evidence report
```

This is not a universal "pretty picture generator", not a general AI floor planner, and
not an automatic issuer of construction documents or GMP/medical approval
without an engineer. It is a design aid and variant compiler that makes decisions
reproducible, editable and verifiable.

## 3. What the target product includes

### Input

- building dimensions and outline, or an existing floor plate;
- a list of rooms with areas, minimum dimensions and purpose;
- a hierarchy of functional zones;
- required, preferred and forbidden relations;
- equipment and service clearances;
- flows of people, raw materials, product, waste and services;
- columns, axes, shafts, stairs and other fixed constraints;
- requirements for doors, windows, passages, lighting and accessibility;
- jurisdiction, rules version and drawing standard.

### Output

- several feasible variants with an explanation of the trade-offs;
- a canonical BuildingIR with stable identifiers;
- editable DXF with layers, blocks, dimensions, labels and a title block;
- a vector PDF sheet set;
- IFC4 with spaces, walls, openings, types and properties;
- a check report: PASS / FAIL / UNKNOWN, evidence and a reference to the rule;
- edit history and a repeatable seed.

## 4. Trust layers

| Layer | Responsibility | Permitted trust level |
|---|---|---|
| LLM / OCR / RAG | program extraction, classification, questions, explanation | probabilistic assistant |
| BuildingIR | the single contract between input and computation | typed truth |
| Solver | coordinates, dimensions, topology and optimization | deterministic |
| Geometry kernel | polygons, buffers, intersections, clearance | deterministic |
| Rules engine | applicability and PASS/FAIL/UNKNOWN | deterministic and auditable |
| DXF/PDF/IFC exporters | projections of the BuildingIR/result | derived artifacts |

The LLM is not granted the right to output final coordinates, write DXF/IFC or
confirm code compliance. Even when importing a drawing, its result is
only a draft structured model, which is checked by the geometry
engine and confirmed by a human.

## 5. Target solver architecture

A single huge CP-SAT model for the whole facility will scale poorly. What is needed is a
hierarchy of problems with separate validation at each level:

1. **Building/floor fit** — outline, storeys, structural grid and cores.
2. **Zone planning** — functional zones and large flow buffers.
3. **Room planning** — rooms, areas, adjacencies and accessibility.
4. **Equipment packing** — equipment, rotations, service zones and passages.
5. **Circulation/flow routing** — human and process routes.
6. **Documentation projection** — walls, openings, symbols, dimensions and sheets.

At each level, hard constraints are separated from soft objectives. First we
obtain a feasible candidate, then rank it by area, route length,
compactness, number of conflicts and documentation quality.

## 6. Pilot typology

The first acceptance example is `examples/pharma_cleanroom_pilot.yaml`: 13 rooms,
6 zones, 5 equipment items and 7 flows. Its technical and external acceptance
conditions are defined in [PILOT_ACCEPTANCE_SPEC.md](PILOT_ACCEPTANCE_SPEC.md).

The next stage after its acceptance is an extended single-storey **pharma-like
clean-production facility**, without any claim of GMP compliance or of a specific
cleanliness class:

- 20–40 rooms;
- 4–8 functional zones;
- 10–30 equipment items or fixed objects;
- separate flows of personnel, raw materials, product, waste and services;
- exterior walls, interior partitions, doors, windows;
- structural axes and at least one service zone;
- no automatic issuing of a building permit.

A successful check of the 13-room example does not close this scale stage.
The extended pilot does not attempt to solve a full plant, hospital, laboratory or
all of MEP at once. It proves only the infrastructure needed for their profiles:
constrained placement, flow segregation, clearance, audit evidence and
engineer-editable artifacts.

## 7. Implementation sequence

### Cycle A — domain model

- add a backward-compatible `BuildingIR` on top of `LayoutIR`;
- describe zones, equipment, flows and structural axes;
- introduce a separate JSON Schema;
- add a canonical example and referential-integrity tests.

### Cycle B — dense 2D geometry

- move from rooms as `Rect` only to orthogonal polygons;
- make separate wall/door/window/equipment/clearance components;
- add deterministic clearance and equipment collision checks;
- keep the current rectangular mode as a fast backend.

### Cycle C — hierarchical layout solver

- zone placement;
- room placement within zones;
- equipment packing with clearance;
- flow graph and routing;
- per-level infeasibility explanation.

### Cycle D — professional drawing output

- a set of drawing profiles;
- full blocks for equipment and sanitary fixtures;
- dimension chains and grid axes;
- zone, equipment, flow and evacuation plans;
- sheet set, legend, title block and revision history.

### Cycle E — IFC/BIM and coordination

- IFC objects for equipment, zones, types and systems;
- spatial relationships and properties;
- clash/clearance report;
- BCF issues;
- round-trip via IfcOpenShell and independent viewer QA.

### Cycle F — codes and jurisdictions

- first fix the pharma clean-production domain profile: vocabulary,
  flow separation and only confirmed geometric checks;
- separate geometric checks, data/IDS and unknown conditions;
- add KZ, Saudi or any other jurisdiction only as a separate versioned profile,
  once the country, facility type and primary source are determined;
- never call any pack a complete legal check without expert
  verification.

### Cycle G — import of existing plans and AI assist

- DXF import as the preferred source of semantics;
- PDF/vector import;
- OCR and vision only for draft recognition;
- human-in-the-loop confirmation of objects;
- LLM commands are converted into typed edits and go through the solver/checks again.

### Cycle H — scaling

- multiple storeys with a shared structural grid;
- vertical cores, stairs, elevators and shafts;
- multi-storey facility blocks and linked service zones;
- performance, caching and batch generation;
- API and local/private deployment.

## 8. Pilot readiness criteria

The pilot is considered ready when, on a single fixed example, it can:

- accept a canonical BuildingIR without coordinates that refer to the future result;
- build at least three feasible variants or explain infeasibility;
- prevent overlap of equipment and its clearance zones;
- prove the required zones, adjacencies, flows and accessibility;
- produce DXF, PDF and IFC from a single result;
- read the artifacts back and keep stable IDs;
- produce a report with evidence for each checked constraint;
- pass automated tests and a manual CAD/BIM review.

## 9. What we are deliberately deferring

- an end-to-end neural network that draws the final geometry;
- a native DWG writer;
- a universal database of building codes;
- fully automatic project approval;
- curved and free-form shapes until the orthogonal backend is stable;
- full MEP and structural engineering calculations;
- training on RPLAN/CubiCasa/FloorPlanCAD without checking rights and licenses;
- replacing the architect, process engineer or engineer.

## 10. Technology choices

We keep the proven foundation:

- Python and typed dataclasses/JSON Schema;
- OR-Tools CP-SAT for discrete layout problems;
- Shapely/GEOS for 2D predicates and clearance;
- ezdxf for editable DXF;
- ReportLab/SVG for vector PDF;
- IfcOpenShell for IFC and IDS;
- FreeCAD/OCCT only as an optional heavyweight QA/3D bridge;
- LLM/RAG only after the deterministic contract and with human review.

Neural floor-plan datasets and HouseGAN/HouseDiffusion are not the
basis of the pilot: research shows a residential bias and a lack of
guarantees for areas, flows, equipment and construction documentation.

## 11. First run after plan approval

Cycle A and the core part of B, as well as the first slice of C, are already implemented: there is a canonical
`BuildingIR`, a dense single-floor pilot, deterministic equipment packing,
clearance/collision checks, hard required/forbidden zone adjacency groups,
room-graph flow routing with deterministic avoidance of rectangular obstacles,
front-edge equipment endpoints, automatic process-door sizing, an initial
DXF/PDF projection of equipment and flows, IFC equipment proxies with
dimension/clearance property sets, IFC flow route proxies and a single facility
evidence report. Versioned pharma, cleanroom, laboratory, hospital and industrial
starter profiles are already fixed without unverified regulatory numbers.
BCF-XML 2.1 exchange is already generated on top of the exported IFC routes:
each variant gets a ZIP with topics/viewpoints and external references to the models,
while the BCF-like JSON sidecar and the YAML-driven drawing profile remain local
machine-readable projections. `--bcf-input` preserves disappeared topics as
`Closed` on the next iteration. Read-only viewer QA already shows derived flow
and equipment conflicts, `OPEN`/`RESOLVED` statuses, an issue filter and BCF history;
the issue workflow supports Resolve/Reopen/comment/assign with an audit sidecar and
updates to the BCF/coordination JSON. For checking the full bundle there is also
`qa-building`. For the acceptance set, a read-only command
`qa-building-set` was added: it runs the full bundle QA for each variant and
compares semantic IDs, route topology, IFC read-back IDs and BCF 2.1 topic
identities across variants.

## 12. Future Cycle I — Process-Driven Manufacturing Design

**Status: planned after the current cycles A–H; implementation has not started.**
This section adds the next development stage: Process-driven Facility Design,
or process-based manufacturing layout. It does not change the order, scope,
priorities or readiness criteria of the existing sections, does not close their
unfinished tasks and does not replace the current roadmap.

The goal is to obtain a consistent manufacturing layout in which rooms,
equipment, clean zones, transfers and routes are explained by the manufacturing
process. The result must be suitable for engineering review and further
development, with dimensions, specifications and sources of requirements.

The canonical `BuildingIR` remains the foundation: new entities extend it and
use shared stable identifiers. The hierarchical solver, geometric
validators, requirement profiles and export from cycles A–H are reused.
A separate competing building model or a rewrite of the current core is not
required for this stage.

### 12.1. Starting point — the production program

Before placing rooms, the system receives a structured program:
what is produced, by which operations, on which equipment and with which
environmental requirements. An example sequence for discussion:
receiving → storage → preparation → manufacturing → assembly → quality control →
packaging → shipping. The actual sequence is defined for the specific
project and may contain branches, returns and intermediate storage.

For each operation the following are recorded:

- incoming and outgoing materials, products, waste and significant states;
- equipment, number of operators and points of interaction with the equipment;
- requirements for environment, isolation, cleaning and intermediate storage;
- target throughput, operating mode and frequency of movements;
- operation durations and loads, if known and confirmed;
- the source of the requirement, assumptions and confirmation by the process engineer.

Requirements for rooms, adjacencies, equipment, transfers and flows are
derived from the program. Required and forbidden adjacencies retain their reason
and a reference to the originating requirement. Missing throughput
or cycle data is not replaced by assumptions without an explicit flag.

This order matches the structure of pre-design work described by
[Glatt](https://pharma-engineering.glatt.com/services/planning/): process diagrams,
equipment and capacities, logistics, GMP zoning and layout variants.
This is a reference point for the product architecture, not a universal set of codes.

### 12.2. Unified manufacturing data model

| Entity | What we store |
| --- | --- |
| Building | Outline, columns, heights, fixed openings, available utility connections, coordinate system and units of measurement. |
| Room | Purpose, operations performed, geometry, area, personnel, finish and cleaning requirements. |
| Clean zone | Its own geometric boundary, ISO class and GMP grade as separate fields, `at_rest` / `in_operation` state, basis of applicability. |
| Equipment | Manufacturer and model, dimensions, working zone, service zone, tooling changeover, loading and unloading points, utility connections. |
| Transfer | Door, personnel airlock, material airlock, pass box, conveyor window; connected spaces, dimensions, directions and permitted movement types. |
| Flow | Personnel, materials, product or waste; start and end points, load state, container, transport method and frequency. |
| Pressure requirement | Linked spaces and the boundary between them, differential, direction, scenario, source and confirmation status. |
| Constraint | Required or forbidden adjacency, passage, clearance, access or other condition; scope, strictness, source and reason. |
| Evidence | Document, version, page or excerpt, link, usage rights, confidence level, assumption and verification status. |

For significant parameters, the requirement, the design value and the
measured result are distinguished. A number entered into the model is not, by itself,
a confirmed characteristic of the built facility.

The functional zone, the clean environment boundary and the ventilation zone are modeled
separately: their boundaries may not coincide. A single room may contain
several local clean enclosures. The assignment of equipment and operations
to these zones is specified explicitly.

An ISO class is not automatically converted into a GMP grade. The room
state, applicability of requirements and additional conditions of the selected
profile are stored. The medical device manufacturing profile is kept separate from the
sterile medicinal products profile. The requirements of
[EU GMP Annex 1](https://health.ec.europa.eu/document/download/e05af55b-38e9-42bf-8495-194bbf0b9262_en?filename=20220825_gmp-an1_en_0.pdf)
are applied taking into account the document's scope and edition.

### 12.3. Equipment and transfers as computational objects

Equipment placement accounts not only for the rectangle of the housing but also for
its interaction with the process and surroundings:

- loading, unloading, operator work, service and connection points;
- panel opening, tooling removal and space for changing it;
- personnel approaches and access for the selected means of transport;
- the route for delivering, installing and later replacing equipment;
- removable building panels and permitted crossings of clean zone boundaries.

Routes connect to specific interaction points. A pass box may
pass only the items provided for in the design and is not a personnel
passage. Airlocks and doors have a defined purpose, direction and usage
rules; interlocks are assigned according to the applicable requirement.

Reference example: an injection molding machine sits on the technical side,
components enter the clean zone through a conveyor window, and servicing is
performed outside that zone. If equipment crosses its boundary, an
explicitly described interface with verifiable geometry is required.

For a library of such solutions, real examples are useful:
[MECART](https://www.mecart-cleanrooms.com/projects/case-studies/medical-device-manufacturing-clean-room-20000-sqft/)
with conveyor windows and removable panels, and
[Optimold](https://connect2cleanrooms.com/client-stories/case-studies/optimold/)
with local modular clean zones and access for tooling changeover.
The parameters of a specific project are confirmed by its source data.

### 12.4. Operating scenarios and the pressure differential network

Checks are performed for at least three scenarios:

| Scenario | What we check |
| --- | --- |
| Production | Equipment working zones, routes of personnel, materials, product and waste, accessibility of transfers and passages. |
| Maintenance | Open panels, access to assemblies, tooling changeover, space for service work and restrictions on adjacent operations. |
| Installation and replacement | The path from external access to the installation location, opening dimensions, turns, temporarily removable panels and required clear zones. |

Each scenario has its own occupied areas and permitted actions. If
maintenance is allowed only when production is stopped, that is an explicit
condition of the scenario. A conflict must not be explained away by an unrecorded assumption
that the neighboring machine or route is not in use at that moment.

The pressure differential network is built from the spaces that are actually connected and the
transfers between them, including local clean zones. A single
"parent zone → child zone" hierarchy is not sufficient for this.

The first implementation scope is checking the specified directions, differentials,
network consistency and the requirements of the selected profile. Values and conditions
are specified with a source; no universal differential for all manufacturing is
introduced. Ventilation calculations and confirmation of the actual operation of the air
cascade remain a separate task for the HVAC engineer.

### 12.5. Linking the process, the solver and independent checks

Computation sequence:

1. Production program and confirmed source data.
2. Operations, equipment, flows and environmental requirements.
3. Zones, rooms, transfers and placement constraints.
4. Hierarchical placement using the existing solver.
5. Independent verification of geometry, routes and scenarios.
6. Conflict explanation, constraint refinement and recomputation.
7. Comparison of feasible variants and release of a consistent bundle.

Feedback must be concrete: a narrow opening, an unsuitable transfer
type, insufficient room to turn, a blocked service zone
or the impossibility of replacing equipment. Each message is linked to stable
object IDs and the originating requirement.

Hard constraints are not compensated by an improvement in the overall ranking.
Missing required data yields `UNKNOWN` with a list of the missing
evidence, not a positive check result.

Feasible variants are compared by frequency-weighted travel distance,
clean zone area, service accessibility and expansion reserve.
Throughput and queues are assessed only when data on
operation times and loads is available. Detailed dynamic simulation is split off
into a later scope of work and is not a condition for the first result of cycle I.

### 12.6. Engineering library and working with sources

Library materials are separated by purpose:

- `real_case_study` — a description of a completed facility;
- `functional_diagram` — a diagram of operations, relationships or flows;
- `engineering_rule` — a requirement with its scope and edition;
- `visual_reference` — a visual example;
- `cad_geometry` — geometry and CAD components;
- `manufacturer_spec` — manufacturer characteristics and requirements.

Parametric templates are created from confirmed materials: gowning,
material airlock, injection molding cell, packaging cell, service zone.
Each template contains parameters, permitted variants, interfaces,
constraints, scope and sources.

Facts from the source, assumptions made while reconstructing a
plan, and information confirmed by an engineer are stored separately. A dimension estimated from a blurry
screenshot remains an estimate. Scale and coordinates are anchored to known
geometry and confirmed dimensions. A case study does not become a regulatory
rule; a functional diagram is not considered a finished architectural plan.

For each material, its origin, version, link, confidence
level and usage rights are recorded. A material without an identifiable source
is not considered verified. Use in the library or in a training set
is determined by permissions; paid access by itself does not replace them.

The first result does not depend on mass training of a model on drawings.
AI can help extract requirements and propose templates with sources,
while the canonical geometry is produced by the solver and undergoes independent verification.

### 12.7. Next reference facility

After the current plan is completed, the next benchmark is **plastic medical
device manufacturing: molding → clean assembly → packaging**.
It complements the current pharma-cleanroom pilot and does not replace its acceptance.

Contents of the benchmark:

- a fixed building outline, columns and external access points;
- injection molding equipment and local clean zones;
- a conveyor transfer, material and personnel transfers;
- assembly, inspection, packaging and the necessary storage locations;
- flows of personnel, materials, product and waste;
- production, maintenance and equipment replacement scenarios;
- a separate applicable requirements profile for the selected manufacturing type.

Source data is taken from a real project with permission, or created
as an explicitly labeled reconstruction with an assumptions log. Similarity to the
provided screenshots is used as a reference for composition and level of
detail; exact reconstruction of unknown parameters is not claimed.

### 12.8. Consistent output bundle

All views are produced from a single model version:

- general layout with the building outline, columns, rooms and areas;
- equipment placement with working and service zones;
- clean zone plan with ISO class, GMP grade, state and applicability;
- plan of specified pressure differentials and connected spaces;
- flow plans for personnel, materials, product and waste;
- access diagrams for maintenance, installation and replacement;
- schedules of rooms, equipment, doors, airlocks and pass boxes;
- dimensions, scale, axes, legends, revision, stable IDs and evidence report.

The export and QA from cycles D–E are used: DXF, PDF, IFC and BCF within the
supported semantics of the formats. Projections and schedules are updated
consistently after a model change. Semantics that cannot be transferred
directly are stored in documented properties or linked data.

Reference for documentation contents —
[EU Site Master File](https://health.ec.europa.eu/document/download/95af86f8-c82d-4ad0-85cb-27c7f56531b4_en?filename=2011_site_master_file_en.pdf):
room classification, pressure differentials between adjacent areas,
production operations, flows and main equipment. The applicability
of this document is assessed for the specific type of manufacturing.

### 12.9. Cycle I implementation sequence

| Sub-stage | Result |
| --- | --- |
| I.1. Production program | A versioned contract for operations, materials, states, equipment, loads and requirements; an agreed example from the process engineer. |
| I.2. BuildingIR extension | Separate clean and ventilation zones, typed transfers, equipment interaction points, scenarios, pressure and data provenance. |
| I.3. Engineering library | Verifiable equipment cards and parametric templates with sources, usage rights and applicability conditions. |
| I.4. Computation and verification | Constraints from the process, placement, routing through permitted transfers, checks of the three scenarios and explainable solver feedback. |
| I.5. Documentation bundle | Consistent plans, schedules and evidence report from a single model, reusing the exporters and QA. |
| I.6. Benchmark and acceptance | Three verifiable variants of medical plastics manufacturing, a process or equipment change scenario, and an engineering review. |

### 12.10. Readiness criteria and scope boundaries

Main criterion: **from an approved production program, the system
produces three verifiable variants, and after a machine is replaced or the
process changes, it recomputes the affected rooms, transfers, routes and
documentation**. Existing semantic IDs are preserved for objects
whose identity has not changed; the set of changes is available for review.

Acceptance covers feasible, conflicting and incomplete source data:

- a personnel route through a pass box is rejected with an explanation;
- an insufficient opening, blocked servicing or impossible
  tooling changeover is detected in the corresponding scenario;
- installation and replacement are checked along the entire path from external access;
- differentials are checked between spaces that are actually connected;
- conditions for stopping production and for temporary actions are stated explicitly;
- missing required evidence yields `UNKNOWN`;
- a hard conflict is not hidden by a high variant ranking;
- after a process or equipment change, plans, schedules, routes
  and reports correspond to a single model revision;
- the benchmark is reproducible, and sources and assumptions are available to the reviewer.

Software checks and engineering acceptance are counted separately.
The process engineer/QA checks the process and the applicability of requirements, the HVAC engineer
checks air regimes, and the architect/CAD/BIM specialist checks the layout and the bundle.
The result of cycle I is a verifiable process concept and documentation
for further design. GMP certification, a complete building services design
and confirmation of the built facility are not part of this cycle.

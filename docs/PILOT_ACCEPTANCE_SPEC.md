# Pharma-cleanroom pilot: acceptance specification

## Purpose and boundary

This document fixes one auditable early-design scenario for the Facility Layout
Compiler. It is a **pharma-like clean-production coordination pilot**, not a
GMP certificate, a cleanroom qualification, an HVAC design, a contamination
control strategy (CCS), or permission to build.

The pilot compiles a controlled program into several feasible layout options.
Each published option must have the same canonical identifiers and be backed by
deterministic geometry, equipment, route, rule, DXF, PDF, IFC and BCF evidence.
Any missing mandatory input becomes `UNKNOWN`, which prevents publication as a
feasible acceptance variant.

The project policy uses EU GMP Annex 1 (2022 revision) as evidence for the
need to consider facility/process design, material/personnel airlocks,
technical/operational separation, pressure differentials and contamination
control. The policy interpretation remains subject to a qualified process,
quality and cleanroom/HVAC expert review.

## Canonical pilot input

The canonical source is
[`examples/pharma_cleanroom_pilot.yaml`](../examples/pharma_cleanroom_pilot.yaml).
It is intentionally a representative program, not a claim about a specific
licensed facility.

| Input group | Required model evidence | Acceptance meaning |
| --- | --- | --- |
| Building program | Orthogonal boundary, rooms, area ranges, minimum dimensions, required adjacencies | Solver can establish a valid room topology. |
| Process stages | `raw_material`, `production`, `packaging`, `finished_goods`, `waste` flow-stage declarations | The material path and waste branch are explicit. |
| Functional zones | Logistics, cleanroom core, personnel airlock and material airlock zones | Zone membership and parent relationships are explicit. |
| Cleanroom evidence | ISO class labels and project-declared pressure values on applicable zones | The tool can check the declared vocabulary and order; it does not calculate classification or airflow. |
| Airlocks | Separate personnel/material roles, parent cleanroom link, room membership and adjacency | The physical gateway intent is represented for review. Door interlocking is out of scope until a door-control model exists. |
| Equipment | Footprints, service envelopes, fixed anchors where applicable | Every item can be packed without overlap or clearance collision. |
| Flows | Endpoint IDs, type, stage, required flag and clear-width requirement | Deterministic routes can be derived and revalidated. |
| Source evidence | Rule ID, source, edition, effective date, evidence text and parameters | A result can be traced to a declared project policy. |

## Composite profile contract

The pilot uses
[`rules/pharma_cleanroom_pilot.yaml`](../rules/pharma_cleanroom_pilot.yaml).
It composes the following project checks:

1. Required process flow categories and connected process-stage sequence.
2. Separate material, personnel, finished-goods, dirty-material and waste
   paths according to the declared project policy.
3. Required cleanroom class and pressure fields on applicable zones.
4. ISO class vocabulary and declared parent/child cleanliness order.
5. Separate personnel and material airlock roles with parent cleanroom links.
6. Declared pressure ordering and the Annex 1 10 Pa guidance value where that
   project strategy applies.
7. Required derived routes for every required process stage, including waste.

`PASS` means only that the stated project predicate passed. `FAIL` is a
coordination issue. `UNKNOWN` means the model lacks the evidence required to
evaluate the predicate; it must not be presented as compliance or feasible
pilot acceptance.

## Publication gates

Every candidate must satisfy all gates before it is exported as
`building_01..N`.

| Gate | Required result |
| --- | --- |
| Program | `BuildingIR` and the selected facility profile pass their schemas and source metadata is present. |
| Geometry | Independent room validation passes. |
| Equipment | Every equipment item is placed; footprints, wall offsets and service envelopes pass independent validation. |
| Flow | Every required flow has a derived route, suitable openings and no route/clearance/wall conflicts. |
| Domain policy | Every mandatory profile rule is `PASS`; `FAIL` and `UNKNOWN` reject the candidate. |
| Variant set | The requested number of candidates is fully accepted. A smaller set is a recorded search-budget failure, never a partial success. |
| Exchange | DXF, vector PDF, IFC read-back, BCF 2.1 and coordination JSON pass bundle QA. |
| Identity | Room/equipment/flow IDs, route topology, IFC metadata and BCF topic identities remain consistent across accepted variants. |

The generator records rejected room candidates and the seed/retry evidence for
every published variant in `manifest.json` and the result JSON. This makes a
failed candidate diagnosable instead of hiding it behind a favourable seed.

## Seed-matrix acceptance

The standard regression matrix is seeds `1`, `7` and `42`, with three variants,
nine room candidates per seed and a 30-second limit per solver invocation.
For every seed, the system must produce the requested three fully accepted
variants or record why the configured search budget did not supply the set.
A budget failure is not proof that no feasible layout exists. The matrix is an engineering
repeatability gate, not a regulatory threshold.

Each run is followed by `qa-building-set`; its report is retained with artifact
hashes. The acceptance test suite also contains positive, negative and
insufficient-evidence cases for every composite rule.

Successful manifests retain search settings and candidate rejection evidence.
Failed runs write `generation-failure.json`, including the canonical program,
profile and rejected-candidate reasons; the matrix embeds that report. Without
successful QA for all seeds, cross-seed identity evidence remains `UNKNOWN`
unless a detected mismatch already establishes `FAIL`.

The CI workflow runs unit/integration tests on Windows and Linux, then executes
this matrix on Windows and retains generated bundles and reports even on failure.
The canonical acceptance example has 13 rooms, six zones, five equipment items
and seven flows. The 20–40-room program in `PRODUCT_PLAN.md` is a later scaling
milestone, not an acceptance claim for this example.

## External acceptance gates

These gates cannot be replaced by unit tests and remain open until performed by
the named human/runtime:

| Gate | Owner | Required evidence |
| --- | --- | --- |
| Process and quality review | Pharmaceutical technologist / QA representative | Signed review of vocabulary, stage transitions, route conflict policy and exceptions. |
| Cleanroom/HVAC review | Cleanroom/HVAC engineer | Review of class strategy, pressure/airflow assumptions, airlock arrangement and containment exceptions. |
| Architectural/BIM review | Architect / BIM coordinator | Open DXF and IFC in the receiving tools and record findings through BCF. |
| Viewer screenshot QA | Browser runtime | Captured screenshots of all variants, visible route conflicts, issue filters and resolved-history viewpoints. |

Until these gates are closed, the deliverable remains an auditable early-design
coordination package.

## Explicit exclusions

- Airflow simulation, particle count calculation and HVAC sizing.
- Airlock door interlocking/control logic and time-based separation procedures.
- Sterilisation validation, cleaning validation, process qualification or CCS approval.
- Fire, accessibility, structural and MEP design approval.
- Full GMP, ISO, hospital, HSE or jurisdictional code compliance.

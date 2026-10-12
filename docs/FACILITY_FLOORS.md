# Shared-grid facility floors and unified IFC

`generate-facility-floors` accepts separate canonical BuildingIR programs,
declared elevations, typed vertical-core room mappings and cross-floor flows.
Each accepted stack keeps a complete checked bundle per floor.

The solver tries at most `--max-attempts` independently accepted base-floor
candidates. If a pinned core placement prevents a later floor from passing,
the failed attempt is retained and the next base placement is tried. Accepted
floor variants are renumbered together. Exhausting this bounded pool reports
`proof_scope: selected_base_floor_candidates` and
`proven_building_infeasible: false`; it does not prove the whole facility
impossible. The time limit applies to each solve, not to the complete stack.

Declare `structural_grid` at the top level of `facility_multi_floor` to produce
one additional `facility_stack_NN.ifc` per stack automatically. For example:

```yaml
facility_multi_floor:
  project_name: Shared-grid coordination example
  floor_height_mm: 4500
  structural_grid:
    axes_x_mm: [0, 3000, 6000, 9000]
    axes_y_mm: [0, 3000, 6000]
    labels_x: [A, B, C, D]
    labels_y: ['1', '2', '3']
  floors:
    - {level: 0, elevation_mm: 0, program: ground.yaml}
    - {level: 1, elevation_mm: 4500, program: upper.yaml}
  vertical_cores:
    - {id: staff_core, kind: stair, rooms: {0: core, 1: core}}
```

The programs must fit these bounds and declare the mapped rooms. Shared axes
must lie on every floor's solver grid. Floor-specific grids, when present,
must exactly match the shared axes and labels. Core-room edges are constrained
to the shared axes during solving and checked independently afterward;
coaxial rooms alone do not satisfy this constraint. Elevations must increase.

```powershell
layout-configurator generate-facility-floors stack.yaml --output out/stack `
  --variants 1 --time-limit 30 --unified-ifc
```

`--unified-ifc` additionally makes a missing shared-grid declaration an input
error before generation. Older programs without a shared grid retain per-floor
bundles and do not invent axes. The report records shared axes, core/flow checks,
IFC counts, read-back, artifact hash and its semantic identity contract.

The IFC contains one project/site/building, storeys at declared elevations,
spaces, walls/openings, equipment proxies, native zone groups and declared
process systems. IDs such as `L1/room/core` distinguish repeated local IDs;
GUIDs are stable and unique. One native IfcGrid is referenced by all floors.
Core groups link their declared room volumes; cross-floor systems link both
floor-local flow legs and the selected core.

These groups describe coordination intent. Room volumes use the existing
2,800 mm exchange height, equipment proxies 1,000 mm; detailed stairs, lifts,
structural analysis and designed MEP are separate work. Receiving-tool and
engineering review remain open.

## Independent stack acceptance

`validate_facility_floors_bundle(spec, output_dir, expected_stacks=1)` reopens
the source programs/profiles and every saved floor bundle. It recomputes all
floor and vertical predicates, reconciles the exact variant inventory, hashes
artifacts, runs IFC4 EXPRESS checks, compares actual world room footprints and
storey elevations, and checks physical grid curves/references and native group
membership. Saved PASS flags are not acceptance evidence. IFC wall/equipment
solids and detailed engineering have separate scope.

The synthetic `examples/pharma_40_two_floor.yaml` benchmark extends the earlier
pilot to 20 rooms per floor, with declared shared axes, stair/goods-lift cores
and both cross-floor flow types. It contains no future placement coordinates
and is not an approved real project. On 2026-10-12 all three seeds 1/7/42 passed
independent floor/stack QA at 40 seconds per solve. Total generation plus QA
took 141.313/70.750/88.203 seconds on the local machine under concurrent work;
this is not an 80-room CI performance measurement.

```powershell
python scripts/facility_floors_acceptance.py examples/pharma_40_two_floor.yaml `
  --output out/pharma40-stack --seeds 1 7 42 --variants 1 `
  --max-attempts 3 --time-limit 40 --minimum-rooms 40
```

The runner refuses fewer than 40 declared rooms by default and requires a
shared grid. Its JSON report records source/effective-profile hashes, executable
and runner fingerprints, dependency versions, CI commit when supplied, settings,
elapsed times and independent QA evidence. Source or implementation changes
during a run prevent PASS. `--check-only` rechecks existing `seed_N` bundles;
preserve an earlier timing report before rechecking the same output directory.
The nightly/on-demand scale workflow runs a separate job for each seed and
uploads failures as well as accepted bundles. The new CI series remains pending
until a pinned remote run completes.

## Multi-storey blocks and service-zone links

`facility_blocks` declares functional groups of nonempty canonical zones on
at least two floors. Each block has an ID and a `zones` mapping from floor
level to zone IDs. It is a grouping within this facility's shared coordinate
system, not a separate building, structural volume or inferred clean boundary.

`service_zone_links` explicitly connects a zone on one floor to a zone on
another through one declared `service_shaft`. The compiler emits stable
`service-link:<id>` cross-floor flow IDs and ordinary floor-local legs; existing
equipment, routing, clear-width and vertical gates check the selected geometry.
Missing/empty zones, wrong core kind, duplicate IDs, local-leg collisions,
nonfinite widths and mistyped booleans fail before solving. The shaft room
must be separate from each linked zone. See
`schemas/facility_connections.schema.json` for the strict optional contract.

The synthetic `examples/service_zone_two_floor.yaml` generates a complete
checked bundle. Native IFC `facility-block/<id>` groups link the floor zones;
`service-zone-link/<id>` systems link both zones, the core and the compiled
cross-floor system. Independent stack QA compares the declarations and actual
memberships to their sources. This describes geometrically routed service
coordination; it does not design pipes/ducts or permit personnel shaft access.

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
engineering review remain open. The small shared-grid and existing 25-room
fixtures pass; the separate two-floor 40-room acceptance criterion remains open.

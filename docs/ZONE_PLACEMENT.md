# Explicit root and recursive zone placement

`solve_zone_layouts(BuildingIR, ..., evidence={})` in `zone_hierarchy.py` is an
opt-in rectangular backend. It compiles canonical descendant memberships into
one physical container per nonempty root zone. Unassigned rooms become explicit
singleton units; unrelated overlapping memberships are rejected.

Local room/cluster CP-SAT solves produce bounded shapes. Global CP-SAT selects
their shapes and anchors with actual room containment, disjoint root containers,
cutout avoidance and hard room/zone relations. Necessary room-bound equipment
envelopes constrain local shapes. Real automatic/explicit window facades and
exterior-entry edges constrain global placement. Final room geometry,
connectivity, containers and hard relations are independently checked by
`validate_zone_layout`.

Explicit `zone_size_options_mm` are grid-aligned width/height pairs; they are
never silently rotated or scaled. Counts and budgets are checked before work.
The budget applies per CP-SAT solve, including multiple local options, rather
than to a total orchestration deadline.

Evidence records units, local attempts, global model status, accepted container
coordinates and failures with `stage`, `code`, `model_status`, `proof_scope`.
`proven_building_infeasible` is always false: a failure of selected rectangular
containers or shortlisted shapes does not prove the unrestricted BuildingIR
impossible. A regression fixture demonstrates a monolithic-valid interleaved
membership that cannot fit this container model.

Select this backend explicitly in facility commands:

```powershell
layout-configurator generate-building program.yaml --output out/zones `
  --room-solver zone_hierarchical --variants 1
```

The facility API accepts the same `room_solver` value. Accepted results still
pass room, equipment, route and profile gates; generation metadata retains
`room_solver_evidence`. CLI failure JSON and failed HTTP jobs preserve scoped
stage explanations. Both zone strategies accept `fixed_rects`,
`axis_aligned_room_ids` and structural X/Y axes, including pinned multi-floor
cores. Fixed rectangles must use finite positive grid dimensions and canonical
room size/area limits. Local shapes retain fixed dimensions; global placement
constrains their exact coordinates and both selected room endpoints to the axes.
Independent validation checks the same constraints. There is no fallback for
an explicit strategy.

## Recursive physical descendants

Select `--room-solver zone_recursive`, or call `solve_zone_layouts` with
`recursive_zones=True`, to place physical containers for every nonempty
canonical descendant zone. Leaves are solved first; each parent CP-SAT model
packs bounded child-shape alternatives and its direct rooms. Global placement
then positions the complete root subtrees. Container geometry remains explicit
even when a selected container is larger than the bounding box of its rooms.

Ancestor containers may contain descendants. Siblings and unrelated containers
must be disjoint; rooms cannot occupy unrelated containers. Independent QA
checks missing descendants, child containment, hard relations and connectivity.
Rooms repeated in ancestor membership retain their deepest canonical ownership;
empty zones are recorded without inventing physical geometry.

Evidence includes leaf/subtree/global attempts, selected physical coordinates
and scoped failures. Recursive shape candidates can use a bounded boundary strip
when the original aspect shortlist is empty. This mode still uses rectangular
containers: split/interleaved functional memberships and 80-room CI acceptance
require subsequent work. Bounded local shape choices also limit fixed/axis
search: an unrestricted monolithic solution can be absent from the shortlist.
Such failures retain the `selected_zone_shapes` proof scope. Calls without the
new constraints and `auto` generation retain their existing behavior. External
engineering acceptance remains necessary after automatic checks.

Direct calls still demand exactly the requested number of independently valid
variants. Facility candidate enumeration explicitly uses `allow_partial=True`:
if enumeration exhausts its bounded shapes after finding a smaller nonempty
valid pool, those candidates reach the existing facility gates. The final
facility request must still obtain all requested accepted variants. Evidence
records returned/requested pool size and the scoped stop reason; an empty or
independently invalid pool never becomes successful output.

## Explicit root-zone block unions

`solve_block_zone_layouts` in `zone_blocks.py` accepts an explicit partition,
for example `zone_blocks={'logistics': {'inbound': ['receiving', 'raw'],
'outbound': ['finished', 'dispatch']}}`. Block IDs are globally unique. Each
partition must cover its root's canonical descendant room membership exactly
once. Unsupplied roots retain one container; unassigned rooms remain singletons.

Local room/cluster solves produce physical rectangular blocks; global CP-SAT
positions them under the original hard relations, cutouts, entry/window,
fixed-room and axis constraints. The root zone's geometry is the exact union
of its solved blocks, including any gap between them. It is never replaced
with a bounding rectangle. Independent `validate_block_zone_layout` checks
block connectivity, geometry, containment and exclusions, exact unions and
global constraints. Evidence retains actual `block_rects` and `zone_geometry`,
including accepted stage candidates before an exact-count failure.

This separate direct API does not change facility/CLI/API dispatch or auto.
Separate descendant containers and nested block partitions require further
work; supplied nested partitions are rejected before a model. Failure remains
about the bounded selected shapes. The six-room logistics/production regression
proves a valid split union that the single-container model excludes; it is not
a large-scale performance claim.

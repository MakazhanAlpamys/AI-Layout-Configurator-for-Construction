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
stage explanations. Fixed rectangles and explicit axis constraints are currently
rejected as `unsupported_constraints` before constructing a model, including
multi-floor pinned cores. There is no fallback for this explicit strategy.

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
containers: split/interleaved functional memberships, fixed rooms/axes and
80-room CI acceptance require subsequent work. Both explicit zone strategies
currently reject fixed/axis inputs before a model, without fallback. Default
root-only calls and `auto` generation retain their existing behavior. External
engineering acceptance remains necessary after automatic checks.

# Actual Production-Program Room Adjacency

Checkpoint I.4.3.1 independently checks supplied required/forbidden room
adjacencies from the [production-program contract](PRODUCTION_PROGRAM.md)
against actual room contours. It accepts original canonical BuildingIR and
layout-result JSON frames; it does not generate a layout or alter a program.

```python
from layout_configurator.production_layout_review import (
    validate_production_program_layout,
)

report = validate_production_program_layout(
    production_program, building_raw=building, layout_raw=layout,
)
```

The returned detached dictionary separates `geometry_status`, `binding_status`
and `provenance_status`. Admitted reports retain the original building, layout
and production-program hashes and frames. The program's base-building binding
has a separate hash excluding only the embedded program for that binding.
The whole embedded program remains present; Python-equal integer/float values
do not become interchangeable raw identities. Building sources must preserve
their canonical identity through admitted model construction.

Required adjacency needs one actual maximal contiguous shared edge at least
the canonical project's `layout.door_width_mm`. Rectangles and orthogonal
polygons share the same exact contour predicate. Corners, bounding-box contact,
wall-thickness-sized gaps and sums of disjoint spans cannot provide it.
Forbidden adjacency fails on any positive shared span. Actual positive room
overlap is an independent failure. This is the project's room-relation
contract, not a universal regulatory door rule or an inferred transfer opening.

Witnesses retain original requirement/operation/source/room IDs, source SHA,
reason, excerpt, basis, actual shared segments, maximum single span and the
applicable project threshold. Missing other room geometry or unknown source
rights/assumptions remain UNKNOWN without erasing a checked conflict.
Empty requirements make no positive production-geometry claim.

Source-layout checks cover original room identity, declared area, building
boundary coverage and interior overlap. Minimum room dimensions, opening
geometry, corridor reservations, flow permissions, capacity, engineering
calculations and complete facility/scenario validation remain separate.
Existing closed process-review/document contracts and embedded-program
producer, incremental and facility capability barriers remain unchanged.

Separate tighten-only limits inherit all production-program bounds and add
128 rooms, 16,384 vertices, 131,072 edge-product checks, 256 retained findings
and one MiB report delivery, with a minimum 4,096-byte cap. Original combined
UTF-8/node/depth/reference/source work, room vertices and predicate work are
admitted before constructors or GEOS. The supported finite coordinate domain
is at most 1,000,000 mm in absolute value. Unrepresentable endpoints/area stop
UNKNOWN with an unevaluated inventory. An exceptional zero floating polygon
area receives bounded exact represented-coordinate arithmetic: lost nonzero
area is a precision gap; genuinely zero-area invalid contours remain FAIL.

Finding/report caps retain originating failures and their source identities,
room pairs, measured contact and thresholds. Incomplete evidence cannot become
complete or lose a known FAIL. Scope remains DRAFT with `.ok == False`, full
facility/scenario and 3D feasibility UNKNOWN, engineering acceptance PENDING,
authenticated approval UNKNOWN and building infeasibility unproven.

Fresh root sequential single-file verification passes sixteen tests/seventeen
subtests in 1.13 seconds and eighteen original program tests/thirty-four
subtests in 0.94 seconds. Test fixtures use canonical millimetre field names
and explicit polygon units. An isolated original finite-only precision predicate
reproduces the valid tiny-polygon constructor escape; the current predicate
stops before constructors, without changing the source file. A truly
degenerate contour still fails actual geometry.

A regular wheel from `a8f1e44` plus the two frozen files runs eight cases
outside the checkout: clear and whole-embedded inputs PASS locally; corner,
overlap, mixed provenance, disconnected contact and degeneracy retain FAIL;
area underflow stays UNKNOWN/NOT_EVALUATED. Two 600 mm spans cannot satisfy
900 mm. The exact 3,000,000 mm2 overlap is retained. A 4,096-byte cap preserves
the original requirement failure in 2,921 bytes; no solver runs.
[Compact evidence](acceptance/a8f1e44-production-layout.json) records exact
source/wheel identities and witnesses. Local proof remains under
`out/checkpoints/production-layout-review-i431/`.

The pushed `16d3691` checkpoint passes
[quick CI](https://github.com/MakazhanAlpamys/AI-Layout-Configurator-for-Construction/actions/runs/38578402195)
in forty seconds including setup. Full acceptance jobs are skipped; changed
full-suite acceptance remains separate.

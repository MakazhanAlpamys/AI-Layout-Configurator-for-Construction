# Orthogonal geometry foundation

`OrthogonalPolygon` in `layout_configurator.orthogonal` represents one connected,
simple, axis-aligned ring in millimetres. It rejects holes, disconnected parts,
diagonal edges, self-intersections and nonfinite coordinates. It normalizes
orientation and starting vertex, removes redundant collinear points, and never
snaps or repairs invalid input. The input limit is 256 vertices.

```python
from layout_configurator.orthogonal import OrthogonalPolygon

room = OrthogonalPolygon(((0, 0), (4000, 0), (4000, 1000),
                          (1000, 1000), (1000, 4000), (0, 4000)))
assert room.area_m2 == 7
assert not room.contains_point((2000, 2000))
rectangles = room.decompose()
```

`bounds_rect` is a derived index. Area, containment, rectangle clearance,
intersection and shared edges use the actual footprint. `decompose()` returns
an exact disjoint horizontal-slab partition; it does not minimize rectangle
count. Translation and quarter-turn rotation preserve the ring.

`inset_components(distance)` returns every surviving component, including a
split at a narrow neck. Its mitre offset implements an axis-aligned clearance
policy. It is not a regulatory clearance verdict or a round-corner Euclidean
offset. Predicates use floating-point GEOS geometry.

Result JSON can contain both `Rect` and `OrthogonalPolygon`. Polygon results
declare `geometry_version: '0.2'` and `units: 'mm'`, with typed vertex arrays;
an all-rectangle result retains the legacy JSON representation. Loaders preserve
the original footprint and reject unknown fields, versions, units, duplicate
keys, invalid coordinates and incorrect derived areas.

`validate_result_geometry(spec, result)` reports exact room identity, area,
boundary/cutout coverage and overlap evidence. Its overall acceptance remains
`UNKNOWN` or `FAIL`: minimum dimensions, opening topology and full connectivity
are still unevaluated. It cannot issue a full layout PASS.

Full rectangular consumers use `require_rectangular_layout` before work. A
polygon currently raises `UnsupportedGeometryError` with structured `UNKNOWN`
capability evidence at walls/openings, equipment, routes, rules, ranking, viewer
and DXF/PDF/IFC export boundaries. Solver selection and polygon consumer
integration remain separate steps; rectangular generation remains supported.

## Exact DRAFT wall and DXF projection

`build_orthogonal_wall_plan(spec, result)` and
`export_orthogonal_draft_dxf(path, spec, result)` provide a separate bounded
polygon consumer. Walls follow actual orthogonal edges; Boolean differences
cut openings on real shared boundaries. Automatic required-adjacency doors
must fit one continuous segment, never a sum of disconnected contacts. An
explicit shared/exterior opening is supported only when its side/offset selects
one unambiguous actual segment. Window/daylight generation and ambiguous manual
selectors return structured UNKNOWN capability evidence before writing output.

The semantic DXF preserves closed room rings, true areas, stable room/door IDs,
millimetre units and hashes of canonical spec/result and delivered artifact.
Concave-room labels use an interior slab anchor. Invalid source contracts,
area/overlap/boundary failures and forbidden contacts are rejected before output.
The artifact is always **DRAFT**, with `ok: false`: usable room dimensions,
full circulation and equipment clearance still need their own checks. Complete
exporters keep their existing capability guards. Rectangular plan import rejects
these polygon outlines rather than replacing them with bounding rectangles.

## Independent room and equipment predicates

`validate_orthogonal_layout(spec, result)` adds actual minimum dimensions,
wall-inset connectivity and a graph built from the generated usable doors.
The project policy `axis_aligned_coverage_v1` requires every footprint point
to be covered by a contained axis-aligned rectangle meeting the source room's
minimum width and depth. A rotatable room chooses one orientation for its
whole footprint. This explicit geometry policy is not a regulatory width rule.
It accepts qualifying L-shaped arms and rejects thin spurs or narrow necks
even when their bounding box is large. Scan/candidate exhaustion yields UNKNOWN.

The usable footprint must remain one nonempty component after the wall-half
mitre inset. Actual doors must reach that component across their full width;
shared contact alone does not establish circulation. Unsupported window or
ambiguous opening selectors retain UNKNOWN evidence.

`validate_orthogonal_equipment(building, layout, equipment)` independently
checks supplied equipment identities, hosts, orientation and directional
service envelopes against actual inset components and wall bands, including
pair collisions. Anchors use actual unique inset edges; an ambiguous edge
selector is UNKNOWN. This checker does not place equipment.

These reports certify their stated predicates only. They do not establish
process-route acceptance, engineering sign-off or a complete facility bundle.
The full rectangular consumers retain their capability guards until separate
solver/export/route integration is independently verified.

## Opt-in finite room catalog solver

`solve_orthogonal_candidates(spec, ...)` in `orthogonal_solver` generates
grid-aligned rectangles and single-notch L footprints in the floor window,
preserving representable source cutouts. An explicit complete room catalog and
separately hashed fixed footprints can be supplied instead. This direct API does
not change facility/CLI dispatch.

CP-SAT selects one actual footprint per room. Pair constraints use true polygon
overlap, forbidden contact and one continuous required door segment. Independent
room, dimension, inset and door QA checks each selected combination; rejected
combinations are excluded before another bounded attempt. Area accuracy ranks
ahead of preferred contacts and position.

```python
from layout_configurator.orthogonal_solver import solve_orthogonal_candidates

draft = solve_orthogonal_candidates(spec, seed=1, deterministic_units=.05)
for candidate in draft.candidates:
    assert candidate.room_report.ok
    print(candidate.result.to_dict())
```

The 6 m × 6 m counterexample tiles a genuine 20 m² L and 16 m² rectangle even
though the corresponding exact-area rectangular model has no solution. Actual
footprints do not overlap despite overlapping bounding boxes.

`OrthogonalSolverBudget` defaults to 128 retained candidates and 8,192 enumeration
steps per room, 100,000 pair checks and 32 model attempts. Reports retain source,
fixed-constraint and catalog hashes, truncation/counts and bounded negative
samples, including the latest independent QA failure. Exhaustion is UNKNOWN about
this finite pattern family. A complete requested pool is DRAFT with `ok: false`;
partial pools stay UNKNOWN. Equipment packing, routes, rule packs and complete
exports remain separate integration gates.

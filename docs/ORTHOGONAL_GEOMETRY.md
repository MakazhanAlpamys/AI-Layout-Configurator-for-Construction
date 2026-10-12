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

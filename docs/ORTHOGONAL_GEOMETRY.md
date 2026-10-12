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

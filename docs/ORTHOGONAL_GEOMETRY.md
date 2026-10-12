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

This checkpoint supplies the kernel. Versioned result serialization, solver
selection, wall/opening topology, equipment/routes and complete polygon export
acceptance are separate integration steps; the rectangular backend remains the
working generation path.

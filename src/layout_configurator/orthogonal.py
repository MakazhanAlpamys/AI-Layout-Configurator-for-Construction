"""Canonical simple orthogonal footprints, independent of solver backends.

Bounds are a derived index only. Containment, decomposition and offsets use
the complete footprint; this module does not turn an L-shape into its bounds.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Mapping

from shapely.geometry import Point, Polygon, box

from .models import Rect

PointMM = tuple[float, float]
EdgeMM = tuple[PointMM, PointMM]
_MAX_VERTICES = 256


@dataclass(frozen=True)
class OrthogonalPolygon:
    """One connected, hole-free simple axis-aligned ring in millimetres.

    Vertices omit closing repetition, are counterclockwise, start at the
    lexicographically smallest point, and contain only actual corners.
    Input is validated before normalization; invalid rings are never healed.
    """

    vertices: tuple[PointMM, ...]

    def __post_init__(self):
        if not isinstance(self.vertices, (tuple, list)) or not 4 <= len(self.vertices) <= _MAX_VERTICES + 1:
            raise ValueError("Orthogonal ring requires 4 to 256 vertices")
        points = tuple(_point(point) for point in self.vertices)
        if points[-1] == points[0]:
            points = points[:-1]
        if not 4 <= len(points) <= _MAX_VERTICES or len(set(points)) != len(points):
            raise ValueError("Orthogonal ring has duplicate or insufficient vertices")
        for first, second in _edges(points):
            if first[0] != second[0] and first[1] != second[1]:
                raise ValueError("Orthogonal edges must be axis-aligned; diagonals are unsupported")
        geometry = Polygon(points)
        if not geometry.is_valid or geometry.is_empty or geometry.area <= 0:
            raise ValueError("Orthogonal ring must be simple, non-self-intersecting and have positive area")
        points = tuple(point for index, point in enumerate(points)
                       if not _collinear(points[index - 1], point, points[(index + 1) % len(points)]))
        signed = _signed_area(points)
        if not math.isfinite(signed) or signed == 0:
            raise ValueError("Orthogonal footprint area must be finite and positive")
        if signed < 0:
            points = tuple(reversed(points))
        start = min(range(len(points)), key=points.__getitem__)
        object.__setattr__(self, "vertices", points[start:] + points[:start])

    @classmethod
    def from_mapping(cls, raw: Mapping) -> "OrthogonalPolygon":
        if not isinstance(raw, Mapping) or set(raw) != {"kind", "units", "vertices"}:
            raise ValueError("Orthogonal footprint requires only kind, units and vertices")
        if raw["kind"] != "orthogonal" or raw["units"] != "mm":
            raise ValueError("Orthogonal footprint kind/units must be orthogonal/mm")
        return cls(raw["vertices"])

    @classmethod
    def from_rect(cls, rect: Rect) -> "OrthogonalPolygon":
        _rect_geometry(rect)
        return cls(((rect.x, rect.y), (rect.right, rect.y), (rect.right, rect.top), (rect.x, rect.top)))

    @classmethod
    def from_geometry(cls, geometry) -> "OrthogonalPolygon":
        if not isinstance(geometry, Polygon) or geometry.is_empty or not geometry.is_valid:
            raise ValueError("Footprint requires one valid single Polygon")
        if geometry.interiors:
            raise ValueError("Orthogonal footprint holes are unsupported")
        return cls(tuple(geometry.exterior.coords))

    def to_dict(self) -> dict:
        return {"kind": "orthogonal", "units": "mm", "vertices": [list(point) for point in self.vertices]}

    @property
    def geometry(self) -> Polygon:
        return Polygon(self.vertices)

    @property
    def area_mm2(self) -> float:
        return _signed_area(self.vertices)

    @property
    def area_m2(self) -> float:
        return self.area_mm2 / 1_000_000

    @property
    def bounds_rect(self) -> Rect:
        """Derived bounds; never substitute these for actual containment."""
        x, y, right, top = self.geometry.bounds
        return Rect(x, y, right - x, top - y)

    @property
    def edges(self) -> tuple[EdgeMM, ...]:
        return tuple(_edges(self.vertices))

    def decompose(self) -> tuple[Rect, ...]:
        """Exact disjoint horizontal-slab partition, not a minimum partition.

        Adjacent slabs with identical x intervals merge. Input vertex bounds
        bound the slab count and interval enumeration; no grid approximation.
        """
        levels = sorted({y for _, y in self.vertices})
        completed, active = [], {}
        for bottom, top in zip(levels, levels[1:]):
            middle = bottom + (top - bottom) / 2
            crossings = sorted(first[0] for first, second in self.edges
                               if first[0] == second[0] and min(first[1], second[1]) < middle < max(first[1], second[1]))
            if len(crossings) % 2:
                raise ValueError("Orthogonal decomposition has an invalid scanline parity")
            current = {}
            for left, right in zip(crossings[::2], crossings[1::2]):
                key = (left, right)
                prior = active.pop(key, None)
                current[key] = Rect(left, prior.y if prior else bottom, right - left,
                                    top - (prior.y if prior else bottom))
            completed.extend(active.values())
            active = current
        completed.extend(active.values())
        return tuple(sorted(completed, key=lambda rect: (rect.y, rect.x, rect.height, rect.width)))

    def contains_point(self, point: PointMM) -> bool:
        """Boundary-inclusive containment in the actual polygon."""
        return bool(self.geometry.covers(Point(_point(point))))

    def covers_rect(self, rect: Rect, *, clearance_mm: float = 0) -> bool:
        """Cover an equipment rectangle after an orthogonal wall offset.

        The mitre inset is an axis-aligned clearance policy, not a claim of
        Euclidean round-corner clearance or a regulatory dimension check.
        """
        target = _rect_geometry(rect)
        return any(component.geometry.covers(target) for component in self.inset_components(clearance_mm))

    def inset_components(self, clearance_mm: float) -> tuple["OrthogonalPolygon", ...]:
        """Mitre inset: may return zero, one or multiple usable components."""
        _number(clearance_mm, "clearance_mm")
        if clearance_mm < 0:
            raise ValueError("clearance_mm must be nonnegative")
        if clearance_mm == 0:
            return (self,)
        inset = self.geometry.buffer(-clearance_mm, join_style="mitre", mitre_limit=5)
        if inset.is_empty:
            return ()
        geometries = (inset,) if isinstance(inset, Polygon) else tuple(inset.geoms)
        parts = tuple(OrthogonalPolygon.from_geometry(geometry) for geometry in geometries)
        return tuple(sorted(parts, key=lambda part: part.vertices))

    def intersection_area(self, other: "OrthogonalPolygon") -> float:
        return self.geometry.intersection(_footprint(other).geometry).area

    def shared_edges(self, other: "OrthogonalPolygon") -> tuple[EdgeMM, ...]:
        """Maximal collinear shared boundary segments; corner contact omitted."""
        other = _footprint(other)
        intervals = {}
        for first, second in self.edges:
            vertical = first[0] == second[0]
            fixed = first[0] if vertical else first[1]
            axis = 1 if vertical else 0
            for third, fourth in other.edges:
                if (third[0] == fourth[0]) != vertical or (third[0] if vertical else third[1]) != fixed:
                    continue
                low = max(min(first[axis], second[axis]), min(third[axis], fourth[axis]))
                high = min(max(first[axis], second[axis]), max(third[axis], fourth[axis]))
                if high > low:
                    intervals.setdefault((vertical, fixed), []).append((low, high))
        result = []
        for (vertical, fixed), spans in sorted(intervals.items()):
            merged = []
            for low, high in sorted(spans):
                if merged and low <= merged[-1][1]:
                    merged[-1] = (merged[-1][0], max(high, merged[-1][1]))
                else:
                    merged.append((low, high))
            result.extend(((fixed, low), (fixed, high)) if vertical else ((low, fixed), (high, fixed))
                          for low, high in merged)
        return tuple(sorted(result))

    def shared_boundary(self, other: "OrthogonalPolygon") -> float:
        return math.fsum(abs(second[0] - first[0]) + abs(second[1] - first[1]) for first, second in self.shared_edges(other))

    def translated(self, dx_mm: float, dy_mm: float) -> "OrthogonalPolygon":
        _number(dx_mm, "dx_mm")
        _number(dy_mm, "dy_mm")
        return OrthogonalPolygon(tuple((x + dx_mm, y + dy_mm) for x, y in self.vertices))

    def rotated_quarter_turns(self, turns: int, *, origin: PointMM = (0, 0)) -> "OrthogonalPolygon":
        if not isinstance(turns, int) or isinstance(turns, bool):
            raise ValueError("Rotation requires an integer number of quarter turns")
        ox, oy = _point(origin)
        points = []
        for x, y in self.vertices:
            x, y = x - ox, y - oy
            for _ in range(turns % 4):
                x, y = -y, x
            points.append((x + ox, y + oy))
        return OrthogonalPolygon(tuple(points))


def _point(value) -> PointMM:
    if not isinstance(value, (tuple, list)) or len(value) != 2:
        raise ValueError("Vertices require two finite numeric coordinates")
    for coordinate in value:
        _number(coordinate, "coordinate")
    return tuple(value)


def _number(value, name):
    try:
        valid = isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
    except OverflowError:
        valid = False
    if not valid:
        raise ValueError(f"{name} requires a finite number without coercion")


def _edges(points):
    return zip(points, (*points[1:], points[0]))


def _collinear(first, second, third):
    return first[0] == second[0] == third[0] or first[1] == second[1] == third[1]


def _signed_area(points):
    ox, oy = points[0]
    return math.fsum((x - ox) * (next_y - oy) - (next_x - ox) * (y - oy)
                     for (x, y), (next_x, next_y) in _edges(points)) / 2


def _rect_geometry(rect):
    if not isinstance(rect, Rect):
        raise ValueError("Rectangle containment requires a Rect")
    for value in (rect.x, rect.y, rect.width, rect.height):
        _number(value, "rectangle coordinate/dimension")
    for value in (rect.right, rect.top):
        _number(value, "rectangle extent")
    if rect.width <= 0 or rect.height <= 0:
        raise ValueError("Rectangle dimensions must be positive")
    return box(rect.x, rect.y, rect.right, rect.top)


def _footprint(value):
    if not isinstance(value, OrthogonalPolygon):
        raise ValueError("Operation requires an OrthogonalPolygon")
    return value

"""Construct real wall bands from room centre lines.

Room rectangles describe clear room boundaries.  This module turns their
unique edges into a wall network: a buffered line network gives two clean,
parallel wall faces, mitered corners, and explicit gaps for required doors.
"""

from __future__ import annotations

from dataclasses import dataclass

from shapely.geometry import LineString
from shapely.ops import unary_union

from .models import DoorSpec, ExternalEntrySpec, LayoutIR, LayoutResult, Rect, WindowSpec


@dataclass(frozen=True)
class DoorOpening:
    room_a: str
    room_b: str
    orientation: str
    fixed: float
    start: float
    end: float
    id: str = ""
    external: bool = False

    @property
    def width(self) -> float:
        return self.end - self.start

    @property
    def center(self) -> tuple[float, float]:
        midpoint = (self.start + self.end) / 2
        return (self.fixed, midpoint) if self.orientation == "vertical" else (midpoint, self.fixed)

    @property
    def centerline(self) -> LineString:
        if self.orientation == "vertical":
            return LineString([(self.fixed, self.start), (self.fixed, self.end)])
        return LineString([(self.start, self.fixed), (self.end, self.fixed)])


@dataclass(frozen=True)
class WindowOpening:
    room_id: str
    orientation: str
    fixed: float
    start: float
    end: float
    id: str = ""

    @property
    def width(self) -> float:
        return self.end - self.start

    @property
    def center(self) -> tuple[float, float]:
        midpoint = (self.start + self.end) / 2
        return (self.fixed, midpoint) if self.orientation == "vertical" else (midpoint, self.fixed)

    @property
    def centerline(self) -> LineString:
        if self.orientation == "vertical":
            return LineString([(self.fixed, self.start), (self.fixed, self.end)])
        return LineString([(self.start, self.fixed), (self.end, self.fixed)])


@dataclass(frozen=True)
class WallPlan:
    geometry: object
    openings: tuple[DoorOpening, ...]
    windows: tuple[WindowOpening, ...] = ()

    def rings(self) -> list[list[tuple[float, float]]]:
        """Return exterior and hole rings suitable for CAD/PDF linework."""

        from shapely.geometry import GeometryCollection, MultiPolygon, Polygon

        def collect(geometry) -> list[list[tuple[float, float]]]:
            if geometry.is_empty:
                return []
            if isinstance(geometry, Polygon):
                rings = [list(geometry.exterior.coords)]
                rings.extend(list(interior.coords) for interior in geometry.interiors)
                return rings
            if isinstance(geometry, (MultiPolygon, GeometryCollection)):
                rings: list[list[tuple[float, float]]] = []
                for child in geometry.geoms:
                    rings.extend(collect(child))
                return rings
            return []

        return collect(self.geometry)


def build_wall_plan(spec: LayoutIR, result: LayoutResult) -> WallPlan:
    """Build a valid wall band with a gap for every required adjacency door."""

    centerlines = []
    for room in spec.rooms:
        rect = result.placements[room.id]
        centerlines.extend(
            [
                LineString([(rect.x, rect.y), (rect.right, rect.y)]),
                LineString([(rect.right, rect.y), (rect.right, rect.top)]),
                LineString([(rect.right, rect.top), (rect.x, rect.top)]),
                LineString([(rect.x, rect.top), (rect.x, rect.y)]),
            ]
        )

    wall_band = unary_union(centerlines).buffer(
        spec.wall_thickness_mm / 2,
        cap_style=2,
        join_style=2,
    )
    explicit_doors = tuple(_manual_door_opening(door, result.placements[door.room_a], result.placements[door.room_b]) for door in spec.doors)
    explicit_pairs = {tuple(sorted((door.room_a, door.room_b))) for door in spec.doors}
    automatic_doors = tuple(
        opening
        for room_a, room_b in sorted(spec.relation_pairs("required_adjacency"))
        if (room_a, room_b) not in explicit_pairs
        for opening in [_door_opening(room_a, room_b, result.placements[room_a], result.placements[room_b], spec.door_width_mm)]
        if opening is not None
    )
    external_entry = (
        (_external_entry_opening(spec.external_entry, result.placements[spec.external_entry.room_id], spec),)
        if spec.external_entry is not None
        else ()
    )
    openings = external_entry + explicit_doors + automatic_doors
    explicit_windows = tuple(_manual_window_opening(window, result.placements[window.room_id], spec) for window in spec.windows)
    explicit_rooms = {window.room_id for window in spec.windows}
    automatic_windows = tuple(
        opening
        for room in spec.rooms
        if room.needs_daylight and room.id not in explicit_rooms
        for opening in [_window_opening(room.id, result.placements[room.id], spec)]
        if opening is not None
    )
    windows = explicit_windows + automatic_windows
    for opening in (*openings, *windows):
        wall_band = wall_band.difference(
            opening.centerline.buffer(
                spec.wall_thickness_mm / 2 + 1,
                cap_style=2,
                join_style=2,
            )
        )
    return WallPlan(geometry=wall_band, openings=openings, windows=windows)


def _door_opening(room_a: str, room_b: str, a: Rect, b: Rect, requested_width: float) -> DoorOpening | None:
    shared = _shared_edge(a, b)
    if shared is None:
        return None
    orientation, fixed, low, high = shared
    width = min(requested_width, high - low)
    if width <= 0:
        return None
    center = (low + high) / 2
    return DoorOpening(
        room_a=room_a,
        room_b=room_b,
        orientation=orientation,
        fixed=fixed,
        start=center - width / 2,
        end=center + width / 2,
        id=f"auto-{room_a}-{room_b}",
    )


def _manual_door_opening(door: DoorSpec, a: Rect, b: Rect) -> DoorOpening:
    shared = _shared_edge(a, b)
    if shared is None:
        raise ValueError(f"Door {door.id} rooms {door.room_a}-{door.room_b} have no shared edge")
    orientation, fixed, low, high = shared
    start = low + door.offset_mm - door.width_mm / 2
    end = start + door.width_mm
    if start + 1e-6 < low or end - 1e-6 > high or end <= start:
        raise ValueError(
            f"Door {door.id} does not fit shared edge of {door.room_a}-{door.room_b} "
            f"at offset {door.offset_mm:g} mm with width {door.width_mm:g} mm"
        )
    return DoorOpening(door.room_a, door.room_b, orientation, fixed, start, end, door.id)


def _external_entry_opening(entry: ExternalEntrySpec, rect: Rect, spec: LayoutIR) -> DoorOpening:
    edge_by_side = {
        "left": ("vertical", rect.x),
        "right": ("vertical", rect.right),
        "bottom": ("horizontal", rect.y),
        "top": ("horizontal", rect.top),
    }
    orientation, fixed = edge_by_side[entry.side]
    candidates = [
        candidate
        for candidate in _exterior_edges(rect, spec)
        if candidate[0] == orientation and abs(candidate[1] - fixed) <= 1e-6
    ]
    if not candidates:
        raise ValueError(
            f"External entry {entry.id} does not fit an exterior {entry.side} edge of room {entry.room_id}"
        )
    _, fixed, low, high = max(candidates, key=lambda edge: edge[3] - edge[2])
    start = low + entry.offset_mm - entry.width_mm / 2
    end = start + entry.width_mm
    if start + 1e-6 < low or end - 1e-6 > high or end <= start:
        raise ValueError(
            f"External entry {entry.id} does not fit exterior {entry.side} edge of room {entry.room_id} "
            f"at offset {entry.offset_mm:g} mm with width {entry.width_mm:g} mm"
        )
    return DoorOpening(entry.room_id, "EXTERIOR", orientation, fixed, start, end, entry.id, external=True)


def _window_opening(room_id: str, rect: Rect, spec: LayoutIR) -> WindowOpening | None:
    candidates = _exterior_edges(rect, spec)
    if not candidates:
        return None
    orientation, fixed, low, high = max(candidates, key=lambda edge: edge[3] - edge[2])
    span = high - low
    width = min(spec.window_width_mm, span - 400)
    if width < 600:
        return None
    center = (low + high) / 2
    return WindowOpening(
        room_id=room_id,
        orientation=orientation,
        fixed=fixed,
        start=center - width / 2,
        end=center + width / 2,
        id=f"auto-{room_id}",
    )


def _manual_window_opening(window: WindowSpec, rect: Rect, spec: LayoutIR) -> WindowOpening:
    edge_by_side = {
        "left": ("vertical", rect.x),
        "right": ("vertical", rect.right),
        "bottom": ("horizontal", rect.y),
        "top": ("horizontal", rect.top),
    }
    orientation, fixed = edge_by_side[window.side]
    for candidate_orientation, candidate_fixed, low, high in _exterior_edges(rect, spec):
        if candidate_orientation != orientation or abs(candidate_fixed - fixed) > 1e-6:
            continue
        start = low + window.offset_mm - window.width_mm / 2
        end = start + window.width_mm
        if start + 1e-6 >= low and end - 1e-6 <= high and end > start:
            return WindowOpening(window.room_id, orientation, fixed, start, end, window.id)
    raise ValueError(
        f"Window {window.id} does not fit an exterior {window.side} edge of room {window.room_id} "
        f"at offset {window.offset_mm:g} mm with width {window.width_mm:g} mm"
    )


def _exterior_edges(rect: Rect, spec: LayoutIR):
    tolerance = 1e-6
    width = spec.boundary.width_mm
    height = spec.boundary.height_mm
    edges = []
    if abs(rect.x) <= tolerance:
        edges.append(("vertical", rect.x, rect.y, rect.top))
    if abs(rect.right - width) <= tolerance:
        edges.append(("vertical", rect.right, rect.y, rect.top))
    if abs(rect.y) <= tolerance:
        edges.append(("horizontal", rect.y, rect.x, rect.right))
    if abs(rect.top - height) <= tolerance:
        edges.append(("horizontal", rect.top, rect.x, rect.right))
    for cutout in spec.boundary.cutouts:
        if abs(rect.right - cutout.x) <= tolerance:
            edges.append(("vertical", rect.right, max(rect.y, cutout.y), min(rect.top, cutout.top)))
        if abs(rect.x - cutout.right) <= tolerance:
            edges.append(("vertical", rect.x, max(rect.y, cutout.y), min(rect.top, cutout.top)))
        if abs(rect.top - cutout.y) <= tolerance:
            edges.append(("horizontal", rect.top, max(rect.x, cutout.x), min(rect.right, cutout.right)))
        if abs(rect.y - cutout.top) <= tolerance:
            edges.append(("horizontal", rect.y, max(rect.x, cutout.x), min(rect.right, cutout.right)))
    return [edge for edge in edges if edge[3] - edge[2] > 0]


def _shared_edge(a: Rect, b: Rect):
    tolerance = 1e-6
    if abs(a.right - b.x) <= tolerance:
        return "vertical", a.right, max(a.y, b.y), min(a.top, b.top)
    if abs(b.right - a.x) <= tolerance:
        return "vertical", a.x, max(a.y, b.y), min(a.top, b.top)
    if abs(a.top - b.y) <= tolerance:
        return "horizontal", a.top, max(a.x, b.x), min(a.right, b.right)
    if abs(b.top - a.y) <= tolerance:
        return "horizontal", a.y, max(a.x, b.x), min(a.right, b.right)
    return None

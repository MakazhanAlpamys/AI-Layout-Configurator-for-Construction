"""Source-backed windows on actual orthogonal exterior facade segments.

This standalone component does not remove existing exporter capability guards.
Automatic sizes retain the existing project opening policy, not a daylight,
thermal, structural or regulatory calculation.
"""

from dataclasses import asdict, dataclass
import hashlib
import json
import math

from shapely.geometry import LineString, box

from .models import LayoutIR, LayoutResult, Rect
from .orthogonal import OrthogonalPolygon
from .orthogonal_projection import _exterior_edges
from .result_geometry import GeometryCheck, parse_layout_result, validate_result_geometry
from .schema import normalize_mapping
from .walls import WindowOpening


_MAX_SOURCE_BYTES = 1_048_576
_MAX_VERTICES = 4096
_MAX_EXTENT_MM = 1_000_000


@dataclass(frozen=True)
class OrthogonalWindowReport:
    windows: tuple[WindowOpening, ...]
    checks: tuple[GeometryCheck, ...]
    source: dict

    @property
    def status(self):
        statuses = {check.status for check in self.checks}
        return "FAIL" if "FAIL" in statuses else "UNKNOWN" if "UNKNOWN" in statuses else "PASS"

    @property
    def ok(self):
        return self.status == "PASS"

    def to_dict(self):
        return {"status": self.status, "ok": self.ok,
                "scope": "Actual exterior window lines and declared project opening policy; not a daylight or engineering calculation",
                "source": dict(self.source), "windows": [asdict(item) for item in self.windows],
                "checks": [{"id": item.id, "status": item.status, "evidence": list(item.evidence)} for item in self.checks],
                "policy": {"automatic_minimum_width_mm": 600, "automatic_end_margin_mm": 200,
                           "source": "Existing project window-opening policy; not a regulatory threshold",
                           "manual_offset": "From the lower endpoint of the single selected actual exterior segment",
                           "ambiguous_manual_side": "UNKNOWN; canonical source needs a segment selector"},
                "limits": {"total_canonical_source_bytes": _MAX_SOURCE_BYTES,
                           "total_source_and_result_vertices": _MAX_VERTICES,
                           "absolute_coordinate_or_dimension_mm": _MAX_EXTENT_MM},
                "full_facility_validation": {"status": "UNKNOWN", "ok": False}, "engineering_acceptance": "PENDING"}


def project_orthogonal_windows(spec: LayoutIR, layout: LayoutResult) -> OrthogonalWindowReport:
    """Project explicit windows and optional deterministic daylight openings."""
    spec, layout, shapes, source = _inputs(spec, layout)
    windows, checks = [], []

    def explicit(record, room_id):
        edges = _exterior_edges(shapes[room_id], record.side, spec)
        if len(edges) > 1:
            checks.append(GeometryCheck(record.id, "UNKNOWN", ("Multiple actual exterior segments need a source segment selector",)))
            return None
        if not edges:
            checks.append(GeometryCheck(record.id, "FAIL", ("Selected room side has no actual exterior facade",)))
            return None
        first, second = edges[0]
        vertical = first[0] == second[0]
        axis = 1 if vertical else 0
        low, high = sorted((first[axis], second[axis]))
        start = low + record.offset_mm - record.width_mm / 2
        end = start + record.width_mm
        if start < low - 1e-6 or end > high + 1e-6:
            checks.append(GeometryCheck(record.id, "FAIL", ("Source window/entry does not fit one actual exterior segment",)))
            return None
        return WindowOpening(room_id, "vertical" if vertical else "horizontal", first[1 - axis], start, end, record.id)

    entry = explicit(spec.external_entry, spec.external_entry.room_id) if spec.external_entry else None
    for record in sorted(spec.windows, key=lambda item: item.id):
        window = explicit(record, record.room_id)
        if window is not None:
            windows.append(window)
    explicit_rooms = {record.room_id for record in spec.windows}
    for room in sorted(spec.rooms, key=lambda item: item.id):
        if not room.needs_daylight or room.id in explicit_rooms:
            continue
        candidates = []
        for side in ("left", "right", "bottom", "top"):
            for edge in _exterior_edges(shapes[room.id], side, spec):
                line = LineString(edge)
                if entry is not None:
                    line = line.difference(entry.centerline)
                for part in _lines(line):
                    if part.length >= 1000 - 1e-6:
                        points = tuple(sorted((tuple(part.coords[0]), tuple(part.coords[-1]))))
                        candidates.append((part.length, points))
        if not candidates:
            checks.append(GeometryCheck("auto-" + room.id, "FAIL", ("No actual facade span satisfies the declared automatic opening policy",)))
            continue
        span, edge = min(candidates, key=lambda item: (-item[0], item[1]))
        width = min(spec.window_width_mm, span - 400)
        if width < 600:
            checks.append(GeometryCheck("auto-" + room.id, "FAIL", ("Source automatic window width is below the project minimum",)))
            continue
        first, second = edge
        vertical = first[0] == second[0]
        axis = 1 if vertical else 0
        center = (first[axis] + second[axis]) / 2
        windows.append(WindowOpening(room.id, "vertical" if vertical else "horizontal", first[1 - axis],
                                     center - width / 2, center + width / 2, "auto-" + room.id))
    identities = set()
    for index, window in enumerate(windows):
        if window.id in identities:
            checks.append(GeometryCheck(window.id, "FAIL", ("Duplicate projected source window identity",)))
        identities.add(window.id)
        if entry is not None and window.centerline.intersection(entry.centerline).length > 1e-6:
            checks.append(GeometryCheck(window.id, "FAIL", ("Window overlaps the actual declared exterior entry",)))
        for previous in windows[:index]:
            if window.centerline.intersection(previous.centerline).length > 1e-6:
                checks.append(GeometryCheck(window.id, "FAIL", (f"Window overlaps actual window {previous.id}",)))
    if not checks:
        checks.append(GeometryCheck("WINDOWS", "PASS", ("Actual source facade, opening width and collision predicates passed",)))
    return OrthogonalWindowReport(tuple(sorted(windows, key=lambda item: item.id)), tuple(checks), source)


def validate_orthogonal_windows(spec: LayoutIR, layout: LayoutResult, windows) -> OrthogonalWindowReport:
    """Check supplied actual lines against canonical source policy and facades."""
    if not isinstance(windows, (list, tuple)) or len(windows) > 128:
        raise ValueError("Window readback requires at most 128 supplied WindowOpening objects")
    for window in windows:
        if (not isinstance(window, WindowOpening) or not isinstance(window.id, str) or not window.id or
                not isinstance(window.room_id, str) or not window.room_id or
                not isinstance(window.orientation, str) or window.orientation not in {"vertical", "horizontal"}):
            raise ValueError("Supplied window geometry requires canonical finite nonempty WindowOpening values")
        for value in (window.fixed, window.start, window.end):
            _extent(value)
        if window.end <= window.start:
            raise ValueError("Supplied window geometry requires a positive nonempty line span")
        _extent(window.width)
    spec, layout, shapes, source = _inputs(spec, layout)
    expected = project_orthogonal_windows(spec, layout)
    checks = [check for check in expected.checks if check.status != "PASS"]
    allowed = box(0, 0, spec.boundary.width_mm, spec.boundary.height_mm)
    for cutout in spec.boundary.cutouts:
        allowed = allowed.difference(box(cutout.x, cutout.y, cutout.right, cutout.top))
    actual = {}
    for window in windows:
        if window.id in actual:
            checks.append(GeometryCheck(window.id, "FAIL", ("Duplicate supplied source window identity",)))
        actual[window.id] = window
        host = shapes.get(window.room_id)
        line = window.centerline
        if host is None or not host.geometry.boundary.covers(line) or not allowed.boundary.covers(line):
            checks.append(GeometryCheck(window.id, "FAIL", ("Actual supplied line is not on its host and exterior facade",)))
    intended = {window.id: window for window in expected.windows}
    if set(actual) != set(intended):
        checks.append(GeometryCheck("WINDOW_IDENTITIES", "FAIL", ("Supplied window IDs differ from resolved canonical source openings",)))
    for identifier in actual.keys() & intended.keys():
        if asdict(actual[identifier]) != asdict(intended[identifier]):
            checks.append(GeometryCheck(identifier, "FAIL", ("Actual supplied host, width, position or orientation differs from canonical source",)))
    if not checks:
        checks.append(GeometryCheck("WINDOWS", "PASS", ("Actual supplied source identities and full facade spans independently checked",)))
    return OrthogonalWindowReport(tuple(windows), tuple(checks), source)


def _inputs(spec, layout):
    if not isinstance(spec, LayoutIR) or not isinstance(layout, LayoutResult):
        raise ValueError("Canonical LayoutIR and LayoutResult required")
    if len(spec.rooms) > 64 or len(layout.placements) > 64 or len(spec.windows) > 128:
        raise ValueError("Window component supports at most 64 rooms and 128 declared windows")
    _source_extents(spec, layout)
    raw_spec, raw_layout = spec.to_dict(), layout.to_dict()
    spec_bytes, layout_bytes = _json(raw_spec), _json(raw_layout)
    if len(spec_bytes) + len(layout_bytes) > _MAX_SOURCE_BYTES:
        raise ValueError("Total canonical window source and layout bytes exceed 1 MiB")
    try:
        normalized = normalize_mapping(raw_spec)
    except (OverflowError, TypeError) as exc:
        raise ValueError("Window source requires finite canonical numeric values") from exc
    if normalized.to_dict() != raw_spec:
        raise ValueError("Window source serialization must already be canonical")
    explicit_rooms = {record.room_id for record in spec.windows}
    automatic_count = sum(room.needs_daylight and room.id not in explicit_rooms for room in spec.rooms)
    if len(spec.windows) + automatic_count > 128:
        raise ValueError("Explicit and required automatic window sources together exceed 128 openings")
    layout = parse_layout_result(raw_layout)
    geometry = validate_result_geometry(spec, layout)
    if any(check.status != "PASS" for check in geometry.checks[:4]):
        raise ValueError("Window component requires valid actual room identity/area/boundary/overlap")
    shapes = {identifier: OrthogonalPolygon.from_rect(shape) if isinstance(shape, Rect) else shape
              for identifier, shape in layout.placements.items()}
    source = {"spec_sha256": hashlib.sha256(spec_bytes).hexdigest(),
              "layout_sha256": hashlib.sha256(layout_bytes).hexdigest()}
    return spec, layout, shapes, source


def _json(value):
    try:
        return json.dumps(value, sort_keys=True, ensure_ascii=False,
            allow_nan=False, separators=(",", ":")).encode("utf-8")
    except (ValueError, TypeError, OverflowError, UnicodeError) as exc:
        raise ValueError("Window source requires finite canonical UTF-8 JSON values") from exc


def _extent(value):
    # Compare huge integers before math.isfinite converts them to floats.
    if (not isinstance(value, (int, float)) or isinstance(value, bool) or
            abs(value) > _MAX_EXTENT_MM or not math.isfinite(value)):
        raise ValueError("Window geometry requires finite numeric extents of at most 1000000 mm")


def _source_extents(spec, layout):
    """Apply component size/extent bounds before parsing or GEOS predicates."""
    vertices = 4 * (1 + len(spec.boundary.cutouts))
    for shape in layout.placements.values():
        if not isinstance(shape, (Rect, OrthogonalPolygon)):
            raise ValueError("Window component requires canonical room footprints")
        vertices += 4 if isinstance(shape, Rect) else len(shape.vertices)
    if vertices > _MAX_VERTICES:
        raise ValueError("Total source boundary and result room vertices exceed 4096")
    for value in (spec.boundary.width_mm, spec.boundary.height_mm, spec.grid_mm,
                  spec.wall_thickness_mm, spec.door_width_mm, spec.window_width_mm,
                  spec.window_height_mm, spec.window_sill_mm):
        _extent(value)
    for room in spec.rooms:
        _extent(room.min_width_mm)
        _extent(room.min_depth_mm)
    for opening in (*spec.windows, *spec.doors, *((spec.external_entry,) if spec.external_entry else ())):
        _extent(opening.offset_mm)
        _extent(opening.width_mm)
    for shape in (*spec.boundary.cutouts, *layout.placements.values()):
        if isinstance(shape, Rect):
            for value in (shape.x, shape.y, shape.width, shape.height):
                _extent(value)
            for value in (shape.right, shape.top):
                _extent(value)
        else:
            for point in shape.vertices:
                for value in point:
                    _extent(value)


def _lines(geometry):
    if geometry.geom_type == "LineString":
        return (geometry,)
    return tuple(line for child in getattr(geometry, "geoms", ()) for line in _lines(child))

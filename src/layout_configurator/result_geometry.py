"""Versioned result geometry and explicit capability boundaries.

Exact polygon evidence is deliberately narrower than full layout validation.
Unsupported topology/opening predicates cannot become a full acceptance PASS.
"""

from dataclasses import dataclass
import json
import math
from typing import Mapping

from jsonschema import Draft202012Validator
from shapely.geometry import box

from .models import LayoutIR, LayoutResult, Rect
from .orthogonal import OrthogonalPolygon
from .resources import resource_path


class UnsupportedGeometryError(ValueError):
    def __init__(self, operation: str, unsupported_rooms: tuple[str, ...]):
        self.operation, self.unsupported_rooms = operation, unsupported_rooms
        super().__init__(f"{operation} does not yet support orthogonal room geometry: {', '.join(unsupported_rooms)}")

    def to_dict(self):
        return {"status": "UNKNOWN", "code": "UNSUPPORTED_GEOMETRY", "operation": self.operation,
                "room_ids": list(self.unsupported_rooms), "message": str(self)}


def require_rectangular_layout(result: LayoutResult, operation: str) -> None:
    rooms = tuple(sorted(room_id for room_id, shape in result.placements.items() if not isinstance(shape, Rect)))
    if rooms:
        raise UnsupportedGeometryError(operation, rooms)


def layout_result_to_dict(result: LayoutResult) -> dict:
    rooms = {}
    polygon_present = False
    for room_id, shape in result.placements.items():
        if isinstance(shape, Rect):
            rooms[room_id] = {"x": shape.x, "y": shape.y, "width": shape.width,
                              "height": shape.height, "area_m2": shape.area_m2}
        elif isinstance(shape, OrthogonalPolygon):
            polygon_present = True
            rooms[room_id] = {**shape.to_dict(), "area_m2": shape.area_m2}
        else:
            raise ValueError(f"Unsupported canonical result geometry for room {room_id}")
    payload = {"variant": result.variant, "objective_value": result.objective_value}
    if polygon_present or result.geometry_version is not None:
        if result.geometry_version not in (None, "0.2"):
            raise ValueError("Unsupported layout result geometry_version")
        payload.update({"geometry_version": "0.2", "units": "mm"})
    payload["rooms"] = rooms
    return payload


def parse_layout_result(raw: Mapping) -> LayoutResult:
    try:
        json.dumps(raw, allow_nan=False)
    except (ValueError, TypeError, OverflowError) as exc:
        raise ValueError("Layout result requires finite JSON values") from exc
    schema = json.loads(resource_path("schemas/layout_result.schema.json").read_text(encoding="utf-8"))
    errors = list(Draft202012Validator(schema).iter_errors(raw))
    if errors:
        raise ValueError("Invalid layout result schema: " + errors[0].message)
    placements = {}
    for room_id, values in raw["rooms"].items():
        if values.get("kind") == "orthogonal":
            shape = OrthogonalPolygon.from_mapping({key: values[key] for key in ("kind", "units", "vertices")})
        else:
            shape = Rect(**{key: values[key] for key in ("x", "y", "width", "height")})
        _geometry(shape)
        if "area_m2" in values and not math.isclose(values["area_m2"], shape.area_m2, rel_tol=1e-12, abs_tol=1e-9):
            raise ValueError(f"Result room {room_id} derived area_m2 differs from its geometry")
        placements[room_id] = shape
    return LayoutResult(raw["variant"], placements, raw.get("objective_value"), raw.get("geometry_version"))


@dataclass(frozen=True)
class GeometryCheck:
    id: str
    status: str
    evidence: tuple[str, ...]


@dataclass(frozen=True)
class GeometryEvidence:
    checks: tuple[GeometryCheck, ...]

    @property
    def status(self):
        return "FAIL" if any(check.status == "FAIL" for check in self.checks) else "UNKNOWN"

    @property
    def ok(self):
        return False  # This scoped checker never evaluates every layout predicate.

    def to_dict(self):
        return {"status": self.status, "ok": self.ok, "scope": "Exact room identity/area/bounds/overlap only; not engineering sign-off",
                "checks": [{"id": check.id, "status": check.status, "evidence": list(check.evidence)} for check in self.checks]}


def validate_result_geometry(spec: LayoutIR, result: LayoutResult) -> GeometryEvidence:
    """Partial deterministic evidence using original polygon coverage and area."""
    identity = []
    expected, actual = set(spec.room_by_id), set(result.placements)
    if expected - actual:
        identity.append("Missing rooms: " + ", ".join(sorted(expected - actual)))
    if actual - expected:
        identity.append("Unknown rooms: " + ", ".join(sorted(actual - expected)))
    allowed = box(0, 0, spec.boundary.width_mm, spec.boundary.height_mm)
    for cutout in spec.boundary.cutouts:
        allowed = allowed.difference(box(cutout.x, cutout.y, cutout.right, cutout.top))
    geometries, areas, outside, overlaps = {}, [], [], []
    for room_id, shape in sorted(result.placements.items()):
        try:
            geometries[room_id] = _geometry(shape)
        except ValueError as exc:
            identity.append(f"{room_id}: {exc}")
            continue
        if room_id in spec.room_by_id:
            room = spec.room_by_id[room_id]
            if not room.min_area_m2 - 1e-6 <= geometries[room_id].area / 1e6 <= room.max_area_m2 + 1e-6:
                areas.append(f"{room_id}: actual polygon area outside declared range")
        if not allowed.covers(geometries[room_id]):
            outside.append(f"{room_id}: actual geometry exceeds boundary or overlaps a cutout")
    for index, first in enumerate(sorted(geometries)):
        for second in sorted(geometries)[:index]:
            area = geometries[first].intersection(geometries[second]).area
            if area > 0:
                overlaps.append(f"{first}/{second}: overlap_area_mm2={area:g}")
    checked = (("RESULT_IDENTITIES", identity), ("ROOM_AREA", areas), ("BOUNDARY", outside), ("ROOM_OVERLAP", overlaps))
    checks = []
    for name, failures in checked:
        status = "FAIL" if failures else "UNKNOWN" if identity and name != "RESULT_IDENTITIES" else "PASS"
        note = "Geometry inputs incomplete; predicate not fully evaluated" if status == "UNKNOWN" else "Scoped exact-geometry predicate passed"
        checks.append(GeometryCheck(name, status, tuple(failures) or (note,)))
    checks.extend(GeometryCheck(name, "UNKNOWN", ("Not evaluated by scoped result geometry checker",))
                  for name in ("MIN_DIMENSION", "TOPOLOGY", "OPENINGS"))
    return GeometryEvidence(tuple(checks))


def _geometry(shape):
    if isinstance(shape, OrthogonalPolygon):
        return shape.geometry
    if not isinstance(shape, Rect):
        raise ValueError("Unknown room geometry type")
    values = (shape.x, shape.y, shape.width, shape.height)
    try:
        valid = all(isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) for value in values)
    except OverflowError:
        valid = False
    if not valid or shape.width <= 0 or shape.height <= 0:
        raise ValueError("Rectangle coordinates/dimensions require finite numbers and positive dimensions")
    if not math.isfinite(shape.right) or not math.isfinite(shape.top) or not math.isfinite(shape.area_mm2):
        raise ValueError("Rectangle extent/area exceeds finite geometry")
    return box(shape.x, shape.y, shape.right, shape.top)

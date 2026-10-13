"""Exact orthogonal wall and semantic DXF projection with DRAFT evidence.

This component consumes canonical result geometry. It does not run a polygon
solver, establish minimum usable room widths, or evaluate circulation and
equipment clearance. Complete exporters and acceptance gates remain separate.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
from pathlib import Path

from shapely.geometry import LineString, box
from shapely.ops import unary_union

from .building import StructuralGridSpec
from .models import LayoutIR, LayoutResult, Rect
from .orthogonal import OrthogonalPolygon
from .result_geometry import GeometryEvidence, parse_layout_result, validate_result_geometry
from .schema import normalize_mapping
from .walls import DoorOpening, WallPlan


class ProjectionCapabilityError(ValueError):
    """An opening needs a selector or component this projection cannot supply."""

    def __init__(self, component: str, message: str):
        self.component = component
        super().__init__(message)

    def to_dict(self):
        return {"status": "UNKNOWN", "code": "UNSUPPORTED_POLYGON_COMPONENT",
                "component": self.component, "message": str(self)}


@dataclass(frozen=True)
class DraftProjectionEvidence:
    spec_sha256: str
    layout_sha256: str
    artifact_sha256: str
    geometry: GeometryEvidence
    opening_ids: tuple[str, ...]
    window_capability: dict | None = None
    grid_capability: dict | None = None

    @property
    def status(self):
        return "DRAFT"

    @property
    def ok(self):
        return False

    def to_dict(self):
        result = {"status": self.status, "ok": self.ok, "units": "mm",
                "scope": "Exact room/wall/opening projection; not engineering sign-off or full layout acceptance",
                "source": {"spec_sha256": self.spec_sha256, "layout_sha256": self.layout_sha256},
                "artifact_sha256": self.artifact_sha256,
                "opening_ids": list(self.opening_ids), "geometry": self.geometry.to_dict(),
                "unknown": ["minimum usable room dimensions", "circulation topology",
                            "equipment clearance", "complete opening acceptance"]}
        if self.window_capability is not None:
            result["window_capability"] = json.loads(json.dumps(self.window_capability))
        if self.grid_capability is not None:
            result["grid_capability"] = json.loads(json.dumps(self.grid_capability))
        return result


def build_orthogonal_wall_plan(spec: LayoutIR, result: LayoutResult, *, include_windows: bool = False) -> WallPlan:
    """Buffer actual polygon edges and cut doors on real contiguous segments.

    Automatic doors choose the longest adequate shared segment, breaking ties
    by canonical endpoints. An explicit door preserves the existing offset
    from the lower endpoint only when there is one shared segment. Exterior
    entries require one exterior segment on the selected outward-facing side.
    Windows remain unsupported by default. Explicit opt-in attaches fresh
    source facade windows after coordination-height eligibility passes. The
    wall band remains a closed facade circulation barrier with door cuts only;
    windows never become door graph or routing portals.
    """
    if not isinstance(include_windows, bool):
        raise ValueError("include_windows requires a boolean")
    windows = ()
    if include_windows:
        # Lazy import avoids the B13 exterior-edge helper's module dependency.
        from .orthogonal_window_capability import OrthogonalWindowCapabilityError, validate_orthogonal_window_capability
        capability = validate_orthogonal_window_capability(spec, result)
        if not capability.ok:
            raise OrthogonalWindowCapabilityError(capability)
        windows = capability.windows
    footprints, _ = _prepare(spec, result)
    unsupported = []
    if spec.windows:
        unsupported.append("explicit windows")
    if any(room.needs_daylight for room in spec.rooms):
        unsupported.append("daylight windows")
    if unsupported and not include_windows:
        raise ProjectionCapabilityError("windows", "Orthogonal DRAFT projection does not yet implement " + ", ".join(unsupported))
    centerlines = [LineString(edge) for room_id in sorted(footprints) for edge in footprints[room_id].edges]
    band = unary_union(centerlines).buffer(spec.wall_thickness_mm / 2, cap_style=2, join_style=2)
    openings = []
    explicit_pairs = set()
    for door in sorted(spec.doors, key=lambda item: item.id):
        pair = tuple(sorted((door.room_a, door.room_b)))
        edges = footprints[pair[0]].shared_edges(footprints[pair[1]])
        if len(edges) > 1:
            raise ProjectionCapabilityError("shared-edge selector", f"Door {door.id} requires an explicit shared-edge selector for multiple segments")
        if not edges:
            raise ValueError(f"Door {door.id} has no actual shared edge")
        openings.append(_offset_opening(door.room_a, door.room_b, edges[0], door.offset_mm, door.width_mm, door.id))
        explicit_pairs.add(pair)
    for first, second in spec.relation_pairs("required_adjacency"):
        if (first, second) in explicit_pairs:
            continue
        edges = footprints[first].shared_edges(footprints[second])
        adequate = tuple(edge for edge in edges if _length(edge) + 1e-6 >= spec.door_width_mm)
        if not adequate:
            raise ValueError(f"Required adjacency {first}/{second} needs one contiguous shared edge of {spec.door_width_mm:g} mm")
        edge = min(adequate, key=lambda item: (-_length(item), item))
        openings.append(_offset_opening(first, second, edge, _length(edge) / 2, spec.door_width_mm, f"auto-{first}-{second}"))
    if spec.external_entry is not None:
        entry = spec.external_entry
        edges = _exterior_edges(footprints[entry.room_id], entry.side, spec)
        if len(edges) > 1:
            raise ProjectionCapabilityError("exterior-edge selector", f"External entry {entry.id} requires an exterior-edge selector for multiple {entry.side} segments")
        if not edges:
            raise ValueError(f"External entry {entry.id} has no actual exterior {entry.side} edge")
        openings.append(_offset_opening(entry.room_id, "EXTERIOR", edges[0], entry.offset_mm, entry.width_mm, entry.id, external=True))
    openings = tuple(sorted(openings, key=lambda item: (not item.external, item.room_a, item.room_b, item.id)))
    for opening in openings:
        band = band.difference(opening.centerline.buffer(spec.wall_thickness_mm / 2 + 1, cap_style=2, join_style=2))
    if band.is_empty or not band.is_valid:
        raise ValueError("Orthogonal wall projection produced invalid or empty wall geometry")
    return WallPlan(band, openings, windows)


def export_orthogonal_draft_dxf(path: str | Path, spec: LayoutIR, result: LayoutResult, *,
                                include_windows: bool = False, structural_grid: StructuralGridSpec | None = None,
                                include_grid: bool = False) -> DraftProjectionEvidence:
    """Write semantic room contours, exact wall bands and DRAFT provenance.

    All geometry and capability checks happen before opening the destination.
    Source hashes identify canonical values, rather than file path aliases or
    derived bounding boxes. Full layout acceptance is deliberately UNKNOWN.
    """
    import ezdxf
    from ezdxf.enums import TextEntityAlignment

    if type(include_windows) is not bool:
        raise ValueError("include_windows requires a boolean")
    if type(include_grid) is not bool:
        raise ValueError("include_grid requires a boolean")
    if structural_grid is not None and not include_grid:
        raise ProjectionCapabilityError("structural_grid", "Source reference axes require explicit include_grid=True")
    grid_capability = None
    if include_grid:
        from .orthogonal_grid import OrthogonalGridCapabilityError, validate_orthogonal_grid
        fresh = validate_orthogonal_grid(spec, structural_grid)
        if not fresh.ok:
            raise OrthogonalGridCapabilityError(fresh)
        grid_capability = fresh.to_dict()
    capability = None
    if include_windows:
        from .orthogonal_window_capability import OrthogonalWindowCapabilityError, validate_orthogonal_window_capability
        current = validate_orthogonal_window_capability(spec, result)
        if not current.ok:
            raise OrthogonalWindowCapabilityError(current)
        capability = current.to_dict()
    footprints, geometry = _prepare(spec, result)
    wall_plan = build_orthogonal_wall_plan(spec, result, include_windows=include_windows)
    spec_sha256, layout_sha256 = _hash_json(spec.to_dict()), _hash_json(result.to_dict())
    document = ezdxf.new("R2018")
    document.header["$INSUNITS"] = 4
    for name, color in (("A-ROOM", 8), ("A-WALL", 7), ("A-DOOR", 3), ("A-TEXT", 7), ("A-TITLE", 4)):
        document.layers.add(name, dxfattribs={"color": color})
    document.layers.get("A-ROOM").dxf.plot = 0
    if include_windows:
        document.layers.add("A-WINDOW", dxfattribs={"color": 4})
        document.layers.add("A-WINDOW-TEXT", dxfattribs={"color": 7})
    if include_grid:
        document.layers.add("A-GRID", dxfattribs={"color": 9})
        document.layers.add("A-GRID-TEXT", dxfattribs={"color": 7})
    document.appids.add("LAYOUT_IR")
    modelspace = document.modelspace()
    for ring in wall_plan.rings():
        modelspace.add_lwpolyline(ring[:-1], close=True, dxfattribs={"layer": "A-WALL"})
    for room_id in sorted(footprints):
        footprint = footprints[room_id]
        outline = modelspace.add_lwpolyline(footprint.vertices, close=True, dxfattribs={"layer": "A-ROOM"})
        outline.set_xdata("LAYOUT_IR", [(1000, f"room_id={room_id}"), (1000, f"room_type={spec.room_by_id[room_id].type}")])
        # Exact slab partition supplies an interior anchor; a concave room's
        # bounding-box centre can lie outside the room.
        anchor = max(footprint.decompose(), key=lambda item: (item.area_mm2, -item.y, -item.x))
        label = f"{room_id} ({footprint.area_m2:.2f} m2)"
        height = min(150, anchor.height / 5, anchor.width / max(1, len(label)))
        modelspace.add_text(label, dxfattribs={"height": height, "layer": "A-TEXT"}).set_placement(anchor.center, align=TextEntityAlignment.MIDDLE_CENTER)
    for opening in wall_plan.openings:
        first, second = tuple(opening.centerline.coords)
        door = modelspace.add_line(first, second, dxfattribs={"layer": "A-DOOR"})
        door.set_xdata("LAYOUT_IR", [(1000, f"door_id={opening.id}"), (1000, f"room_a={opening.room_a}"),
                                    (1000, f"room_b={opening.room_b}"), (1040, opening.width)])
    for position, window in enumerate(wall_plan.windows):
        line = modelspace.add_line(*window.centerline.coords, dxfattribs={"layer": "A-WINDOW"})
        line.set_xdata("LAYOUT_IR", [(1000, "kind=window"), (1000, "id=" + window.id),
            (1000, "room_id=" + window.room_id), (1000, "orientation=" + window.orientation),
            (1040, window.width), (1040, spec.window_sill_mm), (1040, spec.window_height_mm)])
        label = (f"Window {window.id} | host {window.room_id} | width {window.width:g} mm | "
                 f"sill {spec.window_sill_mm:g} mm | height {spec.window_height_mm:g} mm")
        modelspace.add_text(label, dxfattribs={"height": 100, "layer": "A-WINDOW-TEXT"}).set_placement(
            (spec.boundary.width_mm + 500, spec.boundary.height_mm - 200 * position))
    if grid_capability is not None:
        for position, axis in enumerate(grid_capability["axes"]):
            line = modelspace.add_line(axis["start"], axis["end"], dxfattribs={"layer": "A-GRID"})
            line.set_xdata("LAYOUT_IR", [(1000, "kind=grid-axis"), (1000, "id=" + axis["id"]),
                (1000, "direction=" + axis["direction"]), (1000, "label=" + axis["label"]),
                (1040, axis["coordinate_mm"])])
            label = (f"Grid {axis['id']} | label {axis['label']} | direction {axis['direction']} | "
                     f"coordinate {axis['coordinate_mm']:g} mm | boundary reference")
            modelspace.add_text(label, dxfattribs={"height": 100, "layer": "A-GRID-TEXT"}).set_placement(
                (spec.boundary.width_mm + 500, spec.boundary.height_mm - 200 * (len(wall_plan.windows) + position)))
    metadata = modelspace.add_point((0, 0), dxfattribs={"layer": "A-TITLE"})
    tags = [(1000, "schema=orthogonal-draft/0.1"), (1000, "status=DRAFT"),
                                    (1000, "units=mm"), (1000, f"spec_sha256={spec_sha256}"),
                                    (1000, f"layout_sha256={layout_sha256}")]
    if capability is not None:
        tags.extend(((1000, "window_capability=source_facade_v1"),
                     (1000, "window_capability_sha256=" + _hash_json(capability))))
    if grid_capability is not None:
        tags.extend(((1000, "grid_capability=boundary_spanning_axes_v1"),
                     (1000, "grid_capability_sha256=" + _hash_json(grid_capability))))
    metadata.set_xdata("LAYOUT_IR", tags)
    modelspace.add_text(f"DRAFT | {spec.project_name} | Variant {result.variant}", dxfattribs={"height": 180, "layer": "A-TITLE"}).set_placement((0, spec.boundary.height_mm + 500))
    modelspace.add_text("Room dimensions, circulation and equipment clearance require further checks", dxfattribs={"height": 100, "layer": "A-TITLE"}).set_placement((0, spec.boundary.height_mm + 250))
    document.saveas(path)
    return DraftProjectionEvidence(spec_sha256, layout_sha256, hashlib.sha256(Path(path).read_bytes()).hexdigest(),
                                   geometry, tuple(opening.id for opening in wall_plan.openings), capability, grid_capability)


def _prepare(spec, result):
    # Roundtrip the strict canonical result schema before geometry projection.
    result = parse_layout_result(result.to_dict())
    _hash_json(spec.to_dict())
    normalize_mapping(spec.to_dict())  # Strict source types and semantic references, before hashing provenance.
    for name, value in (("wall thickness", spec.wall_thickness_mm), ("door width", spec.door_width_mm)):
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
            raise ValueError(f"{name} requires a positive finite millimetre value")
    geometry = validate_result_geometry(spec, result)
    failures = [message for check in geometry.checks if check.status == "FAIL" for message in check.evidence]
    if failures:
        raise ValueError("Invalid source geometry: " + "; ".join(failures))
    footprints = {room_id: OrthogonalPolygon.from_rect(shape) if isinstance(shape, Rect) else shape
                  for room_id, shape in result.placements.items()}
    for first, second in spec.relation_pairs("forbidden_adjacency"):
        if footprints[first].shared_edges(footprints[second]):
            raise ValueError(f"Forbidden adjacency {first}/{second} has actual shared boundary")
    return footprints, geometry


def _offset_opening(first, second, edge, offset, width, opening_id, external=False):
    if any(isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) for value in (offset, width)):
        raise ValueError(f"Opening {opening_id} requires finite numeric offset and width")
    vertical = edge[0][0] == edge[1][0]
    axis = 1 if vertical else 0
    low, high = sorted((edge[0][axis], edge[1][axis]))
    start = low + offset - width / 2
    end = start + width
    if width <= 0 or offset < 0 or start < low - 1e-6 or end > high + 1e-6:
        raise ValueError(f"Opening {opening_id} width {width:g} mm does not fit actual contiguous edge")
    return DoorOpening(first, second, "vertical" if vertical else "horizontal", edge[0][1 - axis], start, end, opening_id, external)


def _exterior_edges(footprint, side, spec):
    allowed = box(0, 0, spec.boundary.width_mm, spec.boundary.height_mm)
    for cutout in spec.boundary.cutouts:
        allowed = allowed.difference(box(cutout.x, cutout.y, cutout.right, cutout.top))
    selected = []
    for first, second in footprint.edges:
        if first[0] == second[0]:
            outward = "right" if second[1] > first[1] else "left"
        else:
            outward = "bottom" if second[0] > first[0] else "top"
        if outward != side:
            continue
        contact = LineString((first, second)).intersection(allowed.boundary)
        for line in _lines(contact):
            if line.length > 0:
                selected.append(tuple(sorted((tuple(line.coords[0]), tuple(line.coords[-1])))))
    return tuple(sorted(set(selected)))


def _lines(geometry):
    if isinstance(geometry, LineString):
        return (geometry,)
    return tuple(line for child in getattr(geometry, "geoms", ()) for line in _lines(child))


def _length(edge):
    return abs(edge[1][0] - edge[0][0]) + abs(edge[1][1] - edge[0][1])


def _hash_json(payload):
    try:
        data = json.dumps(payload, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    except (ValueError, TypeError, OverflowError) as exc:
        raise ValueError("Projection source requires finite canonical JSON values") from exc
    return hashlib.sha256(data).hexdigest()

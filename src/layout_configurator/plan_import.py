"""Source-backed rectangular plan drafts; coordinates never enter canonical IR."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
import math
from pathlib import Path
import re
from typing import Mapping

from shapely.geometry import Polygon, box
from shapely.ops import unary_union

from .models import LayoutIR, Rect

_EPS = 1e-4  # Millimetres: numerical geometry tolerance, never grid snapping.
_MAX_ROOMS = 1000


@dataclass(frozen=True)
class ImportedRoom:
    id: str
    type: str
    rect: Rect
    source_entity_id: str
    provisional: bool = False


@dataclass(frozen=True)
class ImportDraft:
    source_path: str
    source_sha256: str
    source_format: str
    source_units: str
    unit_scale_mm: float | None
    rooms: tuple[ImportedRoom, ...]
    issues: tuple[str, ...] = ()
    assumptions: tuple[str, ...] = ()
    storey: str | None = None
    state: str = "DRAFT"

    def to_dict(self) -> dict:
        return {
            "format": "FLC-import-draft", "version": "0.1", "state": self.state,
            "source": {"path": self.source_path, "sha256": self.source_sha256, "format": self.source_format},
            "units": {"source_units": self.source_units, "unit_scale_mm": self.unit_scale_mm, "geometry_units": "mm"},
            "storey": self.storey,
            "rooms": [{**asdict(room), "rect": asdict(room.rect)} for room in self.rooms],
            "issues": list(self.issues), "assumptions": list(self.assumptions),
        }


def import_plan(path: str | Path, *, unit_scale_mm: float | None = None, storey: str | None = None) -> ImportDraft:
    """Read only named DXF room outlines or one storey's IFC space footprints.

    Unsupported geometry and undefined units remain explicit draft issues.
    No bounding-box substitution, inference from text, OCR or grid snapping.
    """

    source = Path(path).resolve()
    if source.suffix.lower() not in {".dxf", ".ifc"}:
        raise ValueError("Plan import supports .dxf and .ifc sources only")
    if unit_scale_mm is not None and (not math.isfinite(unit_scale_mm) or unit_scale_mm <= 0):
        raise ValueError("unit_scale_mm must be finite and positive")
    draft = ImportDraft(str(source), _file_hash(source), source.suffix[1:].upper(), "unknown", None, ())
    if draft.source_format == "DXF":
        if storey is not None:
            raise ValueError("storey filtering is available for IFC sources only")
        return _import_dxf(draft, unit_scale_mm)
    if unit_scale_mm is not None:
        raise ValueError("IFC units come from its declared length unit; unit_scale_mm applies to DXF only")
    return _import_ifc(draft, storey)


def write_import_draft(path: str | Path, draft: ImportDraft) -> None:
    _write_json(Path(path), draft.to_dict())


def read_import_draft(path: str | Path) -> ImportDraft:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or raw.get("format") != "FLC-import-draft" or raw.get("version") != "0.1" or raw.get("state") != "DRAFT":
        raise ValueError("Expected a version 0.1 DRAFT import document")
    if not {"source", "units", "rooms", "issues", "assumptions"} <= raw.keys():
        raise ValueError("Malformed import draft: required fields are missing")
    source, units = raw["source"], raw["units"]
    if not isinstance(source, dict) or not {"path", "sha256", "format"} <= source.keys() or not isinstance(units, dict) or not {"source_units", "unit_scale_mm"} <= units.keys():
        raise ValueError("Malformed import draft source or units")
    if not isinstance(source["path"], str) or not source["path"].strip():
        raise ValueError("Malformed import draft source path must be a nonempty string")
    if not isinstance(source["sha256"], str) or re.fullmatch(r"[0-9a-fA-F]{64}", source["sha256"]) is None:
        raise ValueError("Malformed import draft source sha256 must contain 64 hexadecimal characters")
    if not isinstance(source["format"], str) or source["format"] not in {"DXF", "IFC"} or units.get("geometry_units") != "mm":
        raise ValueError("Invalid draft format or geometry units")
    if not isinstance(units["source_units"], str) or not units["source_units"].strip():
        raise ValueError("Malformed import draft source_units must be a nonempty string")
    scale, storey = units["unit_scale_mm"], raw.get("storey")
    try:
        valid_scale = scale is None or (isinstance(scale, (int, float)) and not isinstance(scale, bool)
                                       and math.isfinite(scale) and scale > 0)
    except OverflowError:
        valid_scale = False
    if not valid_scale:
        raise ValueError("Malformed import draft unit_scale_mm must be a finite positive number or null")
    if storey is not None and (not isinstance(storey, str) or not storey.strip()):
        raise ValueError("Malformed import draft storey must be a nonempty string or null")
    if not isinstance(raw["rooms"], list) or not all(isinstance(raw[key], list) and all(isinstance(value, str) for value in raw[key]) for key in ("issues", "assumptions")):
        raise ValueError("Malformed import draft rooms/issues/assumptions")
    if len(raw["rooms"]) > _MAX_ROOMS:
        raise ValueError("Draft exceeds the rectangular-room import limit")
    try:
        rooms = tuple(ImportedRoom(
            room["id"], room["type"], Rect(**room["rect"]), room["source_entity_id"], room["provisional"],
        ) for room in raw["rooms"])
    except (KeyError, TypeError) as exc:
        raise ValueError(f"Malformed import draft room: {exc}") from exc
    issues = tuple(dict.fromkeys((*raw["issues"], *_room_issues(rooms))))
    return ImportDraft(source["path"], source["sha256"], source["format"], units["source_units"],
                       units["unit_scale_mm"], rooms, issues, tuple(raw["assumptions"]), raw.get("storey"))


def load_imported_constraints(
    path: str | Path,
    layout_spec: LayoutIR,
    *,
    confirmed: bool = False,
    reviewer: str | None = None,
    room_mapping: Mapping[str, str] | None = None,
) -> dict[str, Rect]:
    """Validate confirmed source geometry and return only solver fixed_rects.

    Confirmation is required on every call. Its separate evidence hashes this
    draft and canonical program; it is geometry acceptance, not engineering
    sign-off. The original source must remain available and unchanged.
    """

    if confirmed is not True:
        raise ValueError("Imported geometry requires explicit human confirmation")
    if not reviewer or not reviewer.strip():
        raise ValueError("Imported geometry requires a named reviewer")
    draft_path = Path(path)
    draft = read_import_draft(draft_path)
    if _file_hash(Path(draft.source_path)) != draft.source_sha256:
        raise ValueError("Original source changed: source hash does not match the draft")
    if draft.issues:
        raise ValueError("Import draft has unresolved issues: " + "; ".join(draft.issues))
    original = import_plan(draft.source_path, storey=draft.storey,
                           unit_scale_mm=draft.unit_scale_mm if draft.source_format == "DXF" else None)
    if (original.source_format, original.source_units, original.unit_scale_mm, original.storey) != (
        draft.source_format, draft.source_units, draft.unit_scale_mm, draft.storey,
    ):
        raise ValueError("Draft source format, units or storey metadata no longer matches its original source")
    if original.issues or original.rooms != draft.rooms:
        raise ValueError("Draft geometry no longer matches its original source; import it again")
    mapping = dict(room_mapping or {})
    fixed = validate_fixed_rooms(draft.rooms, layout_spec, mapping)
    _write_json(draft_path.with_suffix(".confirmation.json"), {
        "format": "FLC-import-confirmation", "confirmed": True, "reviewer": reviewer.strip(),
        "source_sha256": draft.source_sha256, "draft_sha256": _file_hash(draft_path),
        "layout_sha256": hashlib.sha256(_canonical_json(layout_spec.to_dict())).hexdigest(),
        "room_mapping": {room.id: mapping.get(room.id, room.id) for room in draft.rooms},
        "scope": "Imported geometry acceptance; not engineering sign-off",
    })
    return fixed


def validate_fixed_rooms(rooms: tuple[ImportedRoom, ...], layout_spec: LayoutIR,
                         room_mapping: Mapping[str, str] | None = None) -> dict[str, Rect]:
    """Shared source-geometry mapping and solver fixed-rectangle constraints."""
    mapping = dict(room_mapping or {})
    if set(mapping) - {room.id for room in rooms}:
        raise ValueError("Room mapping references an unknown source room")
    fixed: dict[str, Rect] = {}
    issues: list[str] = _room_issues(rooms)
    for imported in rooms:
        room_id = mapping.get(imported.id, imported.id)
        if room_id not in layout_spec.room_by_id:
            issues.append(f"unknown room: {imported.id} maps to {room_id}")
            continue
        if room_id in fixed:
            issues.append(f"duplicate mapping: more than one source room maps to {room_id}")
            continue
        rect, room = imported.rect, layout_spec.room_by_id[room_id]
        fixed[room_id] = rect
        if any(abs(value - round(value / layout_spec.grid_mm) * layout_spec.grid_mm) > _EPS
               for value in (rect.x, rect.y, rect.width, rect.height)):
            issues.append(f"grid: {room_id} is not aligned to grid_mm={layout_spec.grid_mm}")
        if rect.x < -_EPS or rect.y < -_EPS or rect.right > layout_spec.boundary.width_mm + _EPS or rect.top > layout_spec.boundary.height_mm + _EPS:
            issues.append(f"bounds: {room_id} is outside the program boundary")
        if any(rect.intersection_area(cutout) > 1e-6 for cutout in layout_spec.boundary.cutouts):
            issues.append(f"cutout: {room_id} overlaps a forbidden boundary cutout")
        if not room.fits(rect.width, rect.height):
            issues.append(f"dimension: {room_id} violates minimum room dimensions")
        if not room.min_area_m2 - 1e-6 <= rect.area_m2 <= room.max_area_m2 + 1e-6:
            issues.append(f"area: {room_id} is outside its allowed area range")
    if issues:
        raise ValueError("Imported constraints rejected: " + "; ".join(issues))
    return fixed


def _import_dxf(draft: ImportDraft, explicit_scale: float | None) -> ImportDraft:
    import ezdxf
    from ezdxf.units import conversion_factor

    issues: list[str] = []
    rooms: list[ImportedRoom] = []
    assumptions = ["Only A-ROOM modelspace closed polylines; no text or block inference; no grid snapping."]
    try:
        document = ezdxf.readfile(draft.source_path)
        units = int(document.header.get("$INSUNITS", 0))
        try:
            scale = conversion_factor(units, 4) if units > 0 else None
        except (TypeError, ValueError, IndexError):
            scale = None
        if scale is None:
            scale = explicit_scale
            if scale is None:
                issues.append("unknown_units: DXF INSUNITS is undefined/unsupported; provide explicit unit_scale_mm")
            else:
                assumptions.append(f"Explicit source unit scale accepted for INSUNITS={units}: {scale:g} mm per source unit.")
        elif explicit_scale is not None and not math.isclose(scale, explicit_scale, rel_tol=1e-12):
            issues.append("conflicting_units: explicit scale contradicts DXF INSUNITS")
        entities = [entity for entity in document.modelspace() if entity.dxf.layer == "A-ROOM"]
        if len(entities) > _MAX_ROOMS:
            issues.append("room_limit: too many A-ROOM entities")
        elif scale is not None and not issues:
            for entity in entities:
                handle = entity.dxf.handle
                try:
                    if entity.dxftype() == "LWPOLYLINE":
                        if not entity.closed or any(abs(point[2]) > 0 for point in entity.get_points("xyb")):
                            raise ValueError("open_or_bulged: expected a closed polygon with straight edges")
                        vertices = list(entity.vertices_in_wcs())
                    elif entity.dxftype() == "POLYLINE" and entity.is_2d_polyline:
                        if not entity.is_closed or any(vertex.dxf.get("bulge", 0) != 0 for vertex in entity.vertices):
                            raise ValueError("open_or_bulged: expected a closed polygon with straight edges")
                        vertices = list(entity.points_in_wcs())
                    else:
                        raise ValueError(f"unsupported_entity: {entity.dxftype()}")
                    if any(abs(vertex.z * scale) > _EPS for vertex in vertices):
                        raise ValueError("unsupported_elevation: DXF room outlines must be in the world XY plane")
                    points = [(vertex.x * scale, vertex.y * scale) for vertex in vertices]
                    if points and points[0] == points[-1]:
                        points.pop()
                    if len(points) != 4:
                        raise ValueError("nonrectangular: expected exactly four room corners")
                    rect = _rectangle(Polygon(points))
                    room_id, room_type = f"dxf:{handle}", "room"
                    provisional = True
                    if entity.has_xdata("LAYOUT_IR"):
                        tags = entity.get_xdata("LAYOUT_IR")
                        values = [tag.value for tag in tags if tag.code == 1000]
                        if len(values) != len(tags) or len(values) != 2 or not values[0].startswith("room_id=") or not values[1].startswith("room_type="):
                            raise ValueError("malformed_xdata: expected room_id and room_type strings")
                        room_id, room_type, provisional = values[0][8:], values[1][10:], False
                    rooms.append(ImportedRoom(room_id, room_type, rect, handle, provisional))
                except (ValueError, ezdxf.DXFError) as exc:
                    issues.append(f"{handle}: {exc}")
        return _finish(draft, f"DXF INSUNITS={units}", scale, rooms, issues, assumptions)
    except (OSError, ValueError, ezdxf.DXFError) as exc:
        return _finish(draft, "unknown", None, [], [f"malformed_source: {exc}"], assumptions)


def _import_ifc(draft: ImportDraft, selector: str | None) -> ImportDraft:
    import ifcopenshell
    import ifcopenshell.geom
    import ifcopenshell.util.element
    import ifcopenshell.util.unit

    issues: list[str] = []
    rooms: list[ImportedRoom] = []
    assumptions = ["IFC tessellation uses world coordinates in metres, converted to mm; rectangular XY footprint only; no bbox substitution."]
    try:
        document = ifcopenshell.open(draft.source_path)
        projects = document.by_type("IfcProject")
        length_units = [unit for project in projects if project.UnitsInContext
                        for unit in project.UnitsInContext.Units if getattr(unit, "UnitType", None) == "LENGTHUNIT"]
        if len(length_units) != 1:
            return _finish(draft, "unknown", None, [], ["unknown_units: IFC requires one declared project length unit"], assumptions)
        scale = ifcopenshell.util.unit.calculate_unit_scale(document) * 1000
        source_units = str(length_units[0])
        spaces = document.by_type("IfcSpace")
        if len(spaces) > _MAX_ROOMS:
            return _finish(draft, source_units, scale, [], ["room_limit: too many IFC spaces"], assumptions)
        direct_containers = {element.id(): relation.RelatingStructure
                             for relation in document.by_type("IfcRelContainedInSpatialStructure")
                             for element in relation.RelatedElements}
        containers = {}
        for space in spaces:
            current, visited = space, set()
            while current and current.id() not in visited and not current.is_a("IfcBuildingStorey"):
                visited.add(current.id())
                current = (direct_containers.get(current.id()) or ifcopenshell.util.element.get_container(current)
                           or ifcopenshell.util.element.get_aggregate(current))
            containers[space.id()] = current if current and current.is_a("IfcBuildingStorey") else None
        storeys = {item.GlobalId: item for item in containers.values() if item is not None}
        selected = [item for item in storeys.values() if selector is None or selector in {item.GlobalId, item.Name}]
        if len(selected) != 1:
            return _finish(draft, source_units, scale, [], ["storey_selection: select exactly one storey by GlobalId or name"], assumptions)
        selected_storey = selected[0]
        settings = ifcopenshell.geom.settings()
        settings.set("use-world-coords", True)
        settings.set("convert-back-units", False)
        for space in sorted(spaces, key=lambda item: item.GlobalId):
            container = containers[space.id()]
            if container is None:
                issues.append(f"missing_storey: {space.GlobalId}")
                continue
            if container.GlobalId != selected_storey.GlobalId:
                continue
            try:
                shape = ifcopenshell.geom.create_shape(settings, space)
                verts, faces = shape.geometry.verts, shape.geometry.faces
                if len(faces) > 300000 or len(verts) % 3 or len(faces) % 3:
                    raise ValueError("unsupported_mesh: invalid or excessive tessellation")
                points = [(verts[index] * 1000, verts[index + 1] * 1000) for index in range(0, len(verts), 3)]
                triangles = [Polygon([points[faces[index + offset]] for offset in range(3)]) for index in range(0, len(faces), 3)]
                rect = _rectangle(unary_union([triangle for triangle in triangles if triangle.area > 1e-6]))
                properties = ifcopenshell.util.element.get_psets(space).get("Pset_LayoutRoom", {})
                room_id = str(properties.get("RoomId") or space.Name or f"ifc:{space.GlobalId}")
                room_type = str(properties.get("RoomType") or space.LongName or "room")
                rooms.append(ImportedRoom(room_id, room_type, rect, space.GlobalId, not bool(properties.get("RoomId") or space.Name)))
            except (ValueError, RuntimeError, IndexError) as exc:
                issues.append(f"{space.GlobalId}: unsupported_geometry: {exc}")
        return _finish(draft, source_units, scale, rooms, issues, assumptions, selected_storey.GlobalId)
    except (OSError, ValueError, RuntimeError) as exc:
        return _finish(draft, "unknown", None, [], [f"malformed_source: {exc}"], assumptions)


def _rectangle(geometry) -> Rect:
    if geometry.geom_type != "Polygon" or not geometry.is_valid or geometry.is_empty or geometry.interiors:
        raise ValueError("nonrectangular: invalid, disconnected or holed footprint")
    x, y, right, top = geometry.bounds
    if not all(math.isfinite(value) for value in (x, y, right, top)) or right <= x or top <= y:
        raise ValueError("nonrectangular: nonfinite or zero-area footprint")
    if geometry.symmetric_difference(box(x, y, right, top)).area > 1e-6:
        raise ValueError("nonrectangular: footprint differs from its axis-aligned rectangle")
    return Rect(x, y, right - x, top - y)


def _room_issues(rooms: tuple[ImportedRoom, ...]) -> list[str]:
    issues, seen = [], set()
    for index, room in enumerate(rooms):
        if not all(isinstance(value, str) for value in (room.id, room.type, room.source_entity_id)) or not isinstance(room.provisional, bool):
            raise ValueError("Malformed import draft room identity/type/provisional flag")
        if not all(isinstance(value, (int, float)) and not isinstance(value, bool) for value in asdict(room.rect).values()):
            raise ValueError("Malformed import draft rectangle coordinates must be numbers")
        if not room.id.strip() or not room.type.strip() or not room.source_entity_id.strip():
            issues.append("malformed_room: empty identity/type/source entity")
        if room.id in seen:
            issues.append(f"duplicate_id: {room.id}")
        seen.add(room.id)
        if not all(math.isfinite(value) for value in asdict(room.rect).values()) or room.rect.width <= 0 or room.rect.height <= 0:
            issues.append(f"invalid_rect: {room.id}")
        for other in rooms[:index]:
            if room.rect.intersection_area(other.rect) > 1e-6:
                issues.append(f"overlap: {other.id} and {room.id}")
    return issues


def _finish(draft, units, scale, rooms, issues, assumptions, storey=None) -> ImportDraft:
    rooms = tuple(sorted(rooms, key=lambda room: (room.id, room.source_entity_id)))
    issues = [*issues, *_room_issues(rooms)]
    if not rooms:
        issues.append("no_rooms: no supported rectangular room geometry imported")
    return ImportDraft(draft.source_path, draft.source_sha256, draft.source_format, units, scale,
                       rooms, tuple(issues), tuple(assumptions), storey)


def _file_hash(path: Path) -> str:
    try:
        digest = hashlib.sha256()
        with path.open("rb") as source:
            for chunk in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()
    except OSError as exc:
        raise ValueError(f"Original source/draft unavailable: {path}") from exc


def _canonical_json(value) -> bytes:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False), encoding="utf-8")

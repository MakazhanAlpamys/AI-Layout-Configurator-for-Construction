"""Bounded vector projection of actual orthogonal geometry, explicitly DRAFT.

Canonical source objects remain authoritative. This artifact index is not a
second geometry model, engineering approval or complete facility dispatch.
"""

from dataclasses import asdict, dataclass
import hashlib
import io
import json
from pathlib import Path

from pypdf.generic import TextStringObject
from reportlab.lib.pagesizes import A3, landscape
from reportlab.pdfgen import canvas
from shapely.geometry import box
from shapely.ops import unary_union

from .building import BuildingIR
from .drawing_sheets import _check_glyphs, _fonts, _wrap
from .equipment import EquipmentLayoutResult, clearance_rect
from .flows import FlowRoutingResult
from .models import LayoutResult, Rect
from .orthogonal import OrthogonalPolygon
from .orthogonal_equipment import validate_orthogonal_door_approaches
from .orthogonal_flows import OrthogonalFlowBudget, validate_orthogonal_flow_routes
from .orthogonal_projection import build_orthogonal_wall_plan
from .orthogonal_validation import OrthogonalDimensionPolicy, validate_orthogonal_equipment, validate_orthogonal_layout
from .result_geometry import parse_layout_result
from .schema import normalize_building_mapping


PAGE_WIDTH, PAGE_HEIGHT = landscape(A3)
INK = (.12, .19, .24)
STYLES = {
    "room": {"fill": (.94, .97, .99), "stroke": (.35, .44, .50), "paint": "B*"},
    "wall": {"fill": (.16, .22, .27), "stroke": INK, "paint": "f*"},
    "door": {"fill": INK, "stroke": (.69, .35, .15), "paint": "S"},
    "equipment": {"fill": (.80, .88, .95), "stroke": (.20, .40, .61), "paint": "B*"},
    "service": {"fill": INK, "stroke": (.20, .40, .61), "paint": "S"},
    "corridor": {"fill": (.84, .94, .86), "stroke": INK, "paint": "f*"},
    "route": {"fill": INK, "stroke": (.16, .45, .25), "paint": "S"},
}


@dataclass(frozen=True)
class OrthogonalPDFLimits:
    max_rooms: int = 32
    max_equipment: int = 32
    max_routes: int = 64
    max_vertices: int = 4096
    max_source_bytes: int = 1_048_576
    max_text_chars: int = 32_768
    max_pages: int = 8
    max_pdf_bytes: int = 8_388_608
    max_stream_bytes: int = 4_194_304
    max_operations: int = 50_000
    max_resources: int = 256

    def __post_init__(self):
        for name, value in vars(self).items():
            if not isinstance(value, int) or isinstance(value, bool) or not 1 <= value <= 16_777_216:
                raise ValueError(f"{name} requires an integer between 1 and 16777216")


def _json(raw):
    try:
        return json.dumps(raw, sort_keys=True, ensure_ascii=False, allow_nan=False,
                          separators=(",", ":")).encode("utf-8")
    except (ValueError, TypeError, OverflowError, UnicodeError) as exc:
        raise ValueError("Orthogonal PDF source requires finite UTF-8 canonical JSON") from exc


def _hash(raw):
    return hashlib.sha256(_json(raw)).hexdigest()


def _prepare(building, layout, equipment, routing, limits):
    """Validate canonical inputs and bounded local predicates before any output."""
    if not isinstance(limits, OrthogonalPDFLimits):
        raise ValueError("limits requires OrthogonalPDFLimits")
    if not isinstance(building, BuildingIR) or not isinstance(layout, LayoutResult):
        raise ValueError("Orthogonal PDF requires canonical BuildingIR and LayoutResult")
    try:
        if len(building.layout.rooms) > limits.max_rooms or len(building.equipment) > limits.max_equipment:
            raise ValueError("Orthogonal PDF source room/equipment budget exhausted")
        raw, result_data = building.to_dict(), layout.to_dict()
        if len(_json(raw)) + len(_json(result_data)) > limits.max_source_bytes:
            raise ValueError("Orthogonal PDF source byte budget exhausted")
        building, layout = normalize_building_mapping(raw), parse_layout_result(result_data)
    except (AttributeError, TypeError, OverflowError) as exc:
        raise ValueError("Orthogonal PDF source objects are malformed") from exc
    bound = building.layout.boundary
    if max(bound.width_mm, bound.height_mm, building.layout.wall_thickness_mm) > 1_000_000:
        raise ValueError("Orthogonal PDF source extent exceeds one million millimetres")
    vertices = sum(len(shape.vertices) if isinstance(shape, OrthogonalPolygon) else 4
                   for shape in layout.placements.values())
    if vertices > limits.max_vertices:
        raise ValueError("Orthogonal PDF room vertex budget exhausted")
    policy = OrthogonalDimensionPolicy(max_scan_steps=10_000, max_candidates=4096)
    room_report = validate_orthogonal_layout(building.layout, layout, dimension_policy=policy)
    if not room_report.ok:
        raise ValueError("Orthogonal PDF local room predicates are " + room_report.status)
    walls = build_orthogonal_wall_plan(building.layout, layout)
    local = {"rooms": "PASS", "walls_and_openings": "PASS", "equipment": "UNKNOWN",
             "door_approaches": "UNKNOWN", "routes": "UNKNOWN"}
    if equipment is not None:
        if (not isinstance(equipment, EquipmentLayoutResult) or
                not isinstance(equipment.placements, (tuple, list)) or len(equipment.placements) > limits.max_equipment):
            raise ValueError("Orthogonal PDF equipment placement container or budget is invalid")
        report = validate_orthogonal_equipment(building, layout, equipment)
        approaches = validate_orthogonal_door_approaches(building, layout, equipment)
        if not report.ok or not approaches.ok:
            raise ValueError("Orthogonal PDF supplied equipment or actual door approaches did not pass")
        local.update(equipment="PASS", door_approaches="PASS")
    if routing is not None:
        if (not isinstance(routing, FlowRoutingResult) or
                not isinstance(routing.routes, (tuple, list)) or len(routing.routes) > limits.max_routes):
            raise ValueError("Orthogonal PDF route container or budget is invalid")
        budget = OrthogonalFlowBudget(max_rooms=limits.max_rooms, max_equipment=limits.max_equipment,
            max_endpoint_pairs=limits.max_routes, max_points_per_route=min(limits.max_vertices, 1024), room_policy=policy)
        report = validate_orthogonal_flow_routes(building, layout, routing, equipment, budget=budget)
        if not report.ok:
            raise ValueError("Orthogonal PDF supplied full-width route predicates are " + report.status)
        local["routes"] = "PASS"
    supplied = equipment.to_dict(building) if equipment is not None else None
    routed = routing.to_dict() if routing is not None else None
    if len(_json(supplied)) + len(_json(routed)) > limits.max_source_bytes:
        raise ValueError("Orthogonal PDF optional component byte budget exhausted")
    source = {"building_sha256": _hash(raw), "layout_sha256": _hash(result_data),
              "equipment_sha256": _hash(supplied), "routing_sha256": _hash(routed)}
    return building, layout, walls, local, source


def _projection(building):
    boundary, half = building.layout.boundary, building.layout.wall_thickness_mm / 2
    scale = min(790 / (boundary.width_mm + 2 * half), 510 / (boundary.height_mm + 2 * half))
    return {"scale_pt_per_mm": scale, "origin_pt": [50 + half * scale, 185 + half * scale]}


def _route_id(route, routing):
    matches = [item for item in routing.routes if item.flow_id == route.flow_id]
    if len(matches) == 1:
        return route.flow_id
    return route.flow_id + ":" + _hash([route.from_id, route.to_id, route.from_room_id, route.to_room_id])


def _corridor(route):
    half = route.minimum_clear_width_mm / 2
    if len(route.points) == 1:
        x, y = route.points[0]
        return box(x - half, y - half, x + half, y + half)
    return unary_union([box(min(a[0], b[0]) - half, min(a[1], b[1]) - half,
                            max(a[0], b[0]) + half, max(a[1], b[1]) + half)
                        for a, b in zip(route.points, route.points[1:])])


def _rings(geometry):
    if geometry.geom_type == "Polygon":
        return [list(geometry.exterior.coords)[:-1], *[list(ring.coords)[:-1] for ring in geometry.interiors]]
    return [ring for child in geometry.geoms for ring in _rings(child)]


def _rows(building, layout, walls, equipment, routing):
    rows = ["Source inventory: room labels and embedded vector tags identify canonical sources."]
    for i, (identifier, shape) in enumerate(sorted(layout.placements.items()), 1):
        rows.append(f"R{i:02d} | room {identifier} | type {building.layout.room_by_id[identifier].type} | actual area {shape.area_m2:.3f} m2")
    for opening in walls.openings:
        rows.append(f"Door {opening.id} | {opening.room_a} to {opening.room_b} | actual contiguous width {opening.width:g} mm")
    specs = {item.id: item for item in building.equipment}
    for i, placement in enumerate(equipment.placements if equipment else (), 1):
        item = specs[placement.equipment_id]
        rows.append(f"E{i:02d} | equipment {item.id} | host {placement.room_id} | rotated {placement.rotated} | footprint {placement.rect.width:g} x {placement.rect.height:g} mm | source front/back/left/right clearances {item.clearance_front_mm:g}/{item.clearance_back_mm:g}/{item.clearance_left_mm:g}/{item.clearance_right_mm:g} mm")
    for i, route in enumerate(routing.routes if routing else (), 1):
        rows.append(f"F{i:02d} | flow {route.flow_id} | {route.from_id} to {route.to_id} | host chain {' > '.join(route.room_path)} | supplied checked full width {route.minimum_clear_width_mm:g} mm")
    return rows


def _pdf_string(value):
    output = io.BytesIO()
    TextStringObject(value).write_to_stream(output)
    return output.getvalue().decode("ascii")


def _paint(document, kind, identifier, rings, *, closed=True, scale):
    document.saveState()
    document._code.append(f"/FLC << /Kind /{kind} /Id {_pdf_string(identifier)} >> BDC")
    style = STYLES[kind]
    document.setFillColorRGB(*style["fill"])
    document.setStrokeColorRGB(*style["stroke"])
    document.setLineWidth((1.4 if kind == "door" else .7) / scale)
    if kind == "service":
        document.setDash(4 / scale, 2 / scale)
    for ring in rings:
        if not ring:
            raise ValueError("Orthogonal PDF cannot paint an empty contour")
        for index, (x, y) in enumerate(ring):
            document._code.append(f"{x:.17g} {y:.17g} {'m' if index == 0 else 'l'}")
        if closed:
            document._code.append("h")
    document._code.extend((style["paint"], "EMC"))
    document.restoreState()


def export_orthogonal_draft_pdf(path, building, layout, equipment=None, *, routing=None,
                                revision="P01", font_path=None, limits=OrthogonalPDFLimits()):
    """Export actual contours and only independently checked optional components."""
    building, layout, walls, local, source = _prepare(building, layout, equipment, routing, limits)
    if not isinstance(revision, str) or not revision.strip() or len(revision) > 64:
        raise ValueError("Orthogonal PDF revision requires 1..64 nonempty characters")
    rows = _rows(building, layout, walls, equipment, routing)
    text = [building.layout.project_name, revision, *rows]
    if sum(map(len, text)) > limits.max_text_chars or len(building.layout.project_name) > 256:
        raise ValueError("Orthogonal PDF text budget exhausted")
    if font_path is not None and Path(font_path).stat().st_size > limits.max_stream_bytes:
        raise ValueError("Orthogonal PDF custom font resource budget exhausted")
    regular, bold = _fonts(font_path)
    _check_glyphs(text, regular)
    lines = [line for row in rows for line in (*_wrap(row, regular, 9, PAGE_WIDTH - 110), "")]
    inventory_pages = [lines[i:i + 44] for i in range(0, len(lines), 44)]
    pages = 1 + len(inventory_pages)
    if pages > limits.max_pages:
        raise ValueError("Orthogonal PDF source inventory page budget exhausted")
    projection = _projection(building)
    scale, (x0, y0) = projection["scale_pt_per_mm"], projection["origin_pt"]
    memory = io.BytesIO()
    document = canvas.Canvas(memory, pagesize=(PAGE_WIDTH, PAGE_HEIGHT), pageCompression=0)
    document.setTitle(building.layout.project_name + " | DRAFT orthogonal projection")
    document.setAuthor("Layout Configurator software projection")

    def header(number):
        document.setFillColorRGB(*INK)
        document.setFont(bold, 13)
        for i, line in enumerate(_wrap(building.layout.project_name, bold, 13, PAGE_WIDTH - 100)):
            document.drawString(50, PAGE_HEIGHT - 38 - 16 * i, line)
        document.setFont(regular, 9)
        document.drawString(50, PAGE_HEIGHT - 87,
            f"DRAFT | Variant {layout.variant} | Revision {revision} | Page OP-{number:03d} ({number}/{pages})")
        document.setFont(bold, 10)
        document.drawString(50, PAGE_HEIGHT - 103, "FULL FACILITY: UNKNOWN | ENGINEERING ACCEPTANCE: PENDING")
        document.setFont(regular, 8)
        for i, (name, digest) in enumerate(source.items()):
            document.drawString(50, 76 - i * 13, f"{name}: {digest}")
        document.drawString(50, 18, "Source projection and local predicates only; complete profiles, windows and engineering review remain open.")

    header(1)
    document.saveState()
    document._code.append(f"{scale:.17g} 0 0 {scale:.17g} {x0:.17g} {y0:.17g} cm")
    for identifier, shape in sorted(layout.placements.items()):
        ring = shape.vertices if isinstance(shape, OrthogonalPolygon) else ((shape.x, shape.y), (shape.right, shape.y), (shape.right, shape.top), (shape.x, shape.top))
        _paint(document, "room", identifier, [ring], scale=scale)
    _paint(document, "wall", "walls", _rings(walls.geometry), scale=scale)
    for route in routing.routes if routing else ():
        identifier = _route_id(route, routing)
        _paint(document, "corridor", identifier, _rings(_corridor(route)), scale=scale)
        if len(route.points) > 1:
            _paint(document, "route", identifier, [route.points], closed=False, scale=scale)
    specifications = {item.id: item for item in building.equipment}
    for placement in equipment.placements if equipment else ():
        service = clearance_rect(specifications[placement.equipment_id], placement.rect, placement.rotated)
        _paint(document, "service", placement.equipment_id, _rings(box(service.x, service.y, service.right, service.top)), scale=scale)
        rect = placement.rect
        _paint(document, "equipment", placement.equipment_id, _rings(box(rect.x, rect.y, rect.right, rect.top)), scale=scale)
    for opening in walls.openings:
        _paint(document, "door", opening.id, [list(opening.centerline.coords)], closed=False, scale=scale)
    document.setFillColorRGB(*INK)
    document.setFont(bold, 8 / scale)
    for i, (_, shape) in enumerate(sorted(layout.placements.items()), 1):
        anchor = max((OrthogonalPolygon.from_rect(shape) if isinstance(shape, Rect) else shape).decompose(), key=lambda r: r.area_mm2)
        document.drawCentredString(*anchor.center, f"R{i:02d}")
    document.restoreState()
    document.setFont(regular, 10)
    legend = ["ACTUAL VECTOR GEOMETRY", "Room contour: pale blue", "Wall material: dark fill", "Door: actual opening line",
              "Equipment: blue footprint", "Service: directional dashed line", "Corridor: checked full-width fill", "Centerline: supplied route",
              "LOCAL PREDICATES", *[f"{name}: {status}" for name, status in local.items()],
              "FULL FACILITY: UNKNOWN"]
    for i, line in enumerate(legend):
        document.drawString(875, 670 - i * 22, line)
    document.showPage()
    for number, lines in enumerate(inventory_pages, 2):
        header(number)
        document.setFont(regular, 9)
        for i, line in enumerate(lines):
            document.drawString(50, 700 - i * 13, line)
        document.showPage()
    document.save()
    data = memory.getvalue()
    if len(data) > limits.max_pdf_bytes:
        raise ValueError("Orthogonal PDF output byte budget exhausted")
    # Inspect the in-memory artifact before creating a destination. These shared
    # bounded PDF parsers enforce resources/operators, not source-geometry truth.
    from pypdf import PdfReader
    from .orthogonal_pdf_qa import _path_stream, _resources
    reader = PdfReader(io.BytesIO(data), strict=True)
    for number, page in enumerate(reader.pages, 1):
        _resources(page, limits)
        _path_stream(page, reader, limits, projection, number == 1)
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    return {"format": "FLC-orthogonal-pdf-draft", "version": "0.1", "pdf": target.name,
        "sha256": hashlib.sha256(data).hexdigest(), "status": "DRAFT", "ok": False, "units": "mm",
        "source_variant": layout.variant, "project_name": building.layout.project_name, "revision": revision,
        "full_facility_validation": {"status": "UNKNOWN", "ok": False}, "engineering_acceptance": "PENDING",
        "source": source, "projection": projection, "local_checks": local,
        "components_supplied": {"equipment": equipment is not None, "routing": routing is not None},
        "pages": [{"page_id": f"OP-{i:03d}", "page_number": i, "kind": "plan" if i == 1 else "source_inventory",
                   "status": "DRAFT"} for i in range(1, pages + 1)], "limits": asdict(limits)}

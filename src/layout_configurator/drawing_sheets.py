"""Vector coordination sheets projected from the existing canonical results.

The returned metadata indexes printed pages; it never replaces BuildingIR or
solver results. Egress candidates use actual generated door openings, with no
claim of verified travel distance, clearance, fire safety or certification.
"""

from __future__ import annotations

from collections import deque
from dataclasses import asdict, dataclass
import hashlib
import math
from pathlib import Path
from typing import Callable

from reportlab.lib.pagesizes import A3, landscape
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas

from .building import BuildingIR
from .equipment import EquipmentLayoutResult, clearance_rect
from .facility import FacilityDrawingProfile
from .flows import FlowRoutingResult
from .models import LayoutResult, Rect
from .result_geometry import require_rectangular_layout
from .walls import WallPlan, build_wall_plan


@dataclass(frozen=True)
class DrawingSheetMetadata:
    """An artifact index entry, without a second geometry/result model."""

    kind: str
    sheet_id: str
    title: str
    revision: str
    page_number: int
    scale_denominator: int | None
    status: str
    source_variant: int
    candidate_route_count: int = 0

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


_THEMES = (("zones", "Z", "Functional zones"),
           ("equipment", "E", "Equipment and service envelopes"),
           ("flows", "F", "Process flow routes"),
           ("egress", "R", "Egress source and candidates"))
_PALETTE = ((0.24, 0.47, 0.65), (0.68, 0.44, 0.18), (0.34, 0.57, 0.42),
            (0.55, 0.37, 0.62), (0.65, 0.35, 0.38), (0.31, 0.54, 0.55))
_INK = (0.12, 0.19, 0.24)
_MUTED = (0.35, 0.40, 0.43)
_PAGE_W, _PAGE_H = landscape(A3)
_MARGIN = 28.0
_SIDE_X = _PAGE_W - 310.0
_LEGEND_WIDTH = 260.0
_PLAN_BOTTOM = 139.0
_PLAN_TOP = _PAGE_H - 115.0
_PLAN_LEFT = 62.0
_PLAN_RIGHT = _SIDE_X - 40.0


def export_drawing_sheet_set(
    path: str | Path,
    building: BuildingIR,
    result: LayoutResult,
    equipment: EquipmentLayoutResult,
    *,
    flows: FlowRoutingResult | None = None,
    drawing_profile: FacilityDrawingProfile | None = None,
    font_path: str | Path | None = None,
) -> tuple[DrawingSheetMetadata, ...]:
    """Write thematic A3 sheets and readable schedule continuations.

    Revision metadata is the current revision supplied by the drawing profile;
    earlier revision records and approval signatures are never manufactured.
    Bundled OFL Noto Sans fonts cover Cyrillic without relying on system fonts.
    An explicit TTF can extend coverage. Unsupported source characters raise
    before the output file is opened, rather than publishing black boxes.
    """

    require_rectangular_layout(result, "thematic drawing sheet export")
    drawing = drawing_profile or FacilityDrawingProfile()
    routes = flows or FlowRoutingResult(())
    _check_sources(building, result, equipment, routes)
    wall_plan = build_wall_plan(building.layout, result)
    candidates, egress_rows = _egress_candidates(building, result, wall_plan)
    legends = _legends(building, result, equipment, routes, egress_rows, drawing)
    regular, bold = _fonts(font_path)
    source_text = [building.layout.project_name, drawing.sheet_id, drawing.title,
                   drawing.discipline, drawing.revision]
    source_text.extend(text for entries in legends.values() for _, text in entries)
    if building.structural_grid and drawing.show_structural_grid:
        source_text.extend((*building.structural_grid.labels_x, *building.structural_grid.labels_y))
    _check_glyphs(source_text, regular)

    scale_denominator = _scale_denominator(building)
    scale = 72.0 / 25.4 / scale_denominator
    origin_x = _PLAN_LEFT + (_PLAN_RIGHT - _PLAN_LEFT - building.layout.boundary.width_mm * scale) / 2
    origin_y = _PLAN_BOTTOM + (_PLAN_TOP - _PLAN_BOTTOM - building.layout.boundary.height_mm * scale) / 2
    point = lambda x, y: (origin_x + x * scale, origin_y + y * scale)
    planned = []
    for kind, suffix, title in _THEMES:
        chunks = _paginate(legends[kind], regular)
        planned.append((kind, suffix, title, 0, [chunks[0]]))
        for continuation, start in enumerate(range(1, len(chunks), 3), 1):
            planned.append((kind, suffix, title, continuation, chunks[start:start + 3]))
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    pdf = canvas.Canvas(str(output), pagesize=(_PAGE_W, _PAGE_H), invariant=1, pageCompression=1)
    pdf.setTitle(f"{building.layout.project_name} | {drawing.title} | Rev {drawing.revision}")
    pdf.setAuthor("Facility Layout Compiler")
    metadata = []
    for page_number, (kind, suffix, title, continuation, columns) in enumerate(planned, 1):
        sheet_id = f"{drawing.sheet_id}-{suffix}" + (f"-{continuation + 1:02d}" if continuation else "")
        status = "UNKNOWN" if kind == "egress" else "SOURCE PROJECTION"
        if kind == "flows" and any(not any(route.flow_id == flow.id for route in routes.routes) for flow in building.flows):
            status = "UNKNOWN"
        if kind == "equipment" and len(equipment.placements) != len(building.equipment):
            status = "UNKNOWN"
        meta = DrawingSheetMetadata(kind, sheet_id, title, drawing.revision, page_number,
                                    None if continuation else scale_denominator, status, result.variant,
                                    len(candidates) if kind == "egress" else 0)
        _frame(pdf, meta, building, drawing, len(planned), regular, bold, continuation)
        if continuation:
            pdf.setFont(bold, 18)
            pdf.drawString(_PLAN_LEFT, _PAGE_H - 145, "Legend continuation")
            pdf.setFont(regular, 10)
            pdf.drawString(_PLAN_LEFT, _PAGE_H - 168,
                           f"Refer to {drawing.sheet_id}-{suffix} for the source plan and symbol locations.")
        else:
            _base_plan(pdf, building, result, wall_plan, point, scale, drawing, regular)
            _overlay(pdf, kind, building, result, equipment, routes, candidates, point, scale, drawing, regular)
            _room_annotations(pdf, building, result, point, scale, drawing, regular)
            _scale_bar(pdf, scale_denominator, scale, regular)
        if continuation:
            for index, rows in enumerate(columns):
                _legend(pdf, rows, regular, bold, x=_PLAN_LEFT + index * 355, top=_PAGE_H - 204)
        else:
            _legend(pdf, columns[0], regular, bold)
        pdf.showPage()
        metadata.append(meta)
    pdf.save()
    return tuple(metadata)


def _check_sources(building, result, equipment, flows):
    if set(result.placements) != {room.id for room in building.layout.rooms}:
        raise ValueError("Room placements must match the BuildingIR room program")
    known_equipment = {item.id for item in building.equipment}
    placed = [item.equipment_id for item in equipment.placements]
    if set(placed) - known_equipment or len(placed) != len(set(placed)):
        raise ValueError("Equipment placements contain unknown or duplicate equipment IDs")
    if any(item.room_id not in result.placements for item in equipment.placements):
        raise ValueError("Equipment placement references an unknown host room")
    known_flows = {flow.id for flow in building.flows}
    if any(route.flow_id not in known_flows for route in flows.routes):
        raise ValueError("Supplied route references an unknown flow")
    for rect in (*result.placements.values(), *(item.rect for item in equipment.placements)):
        if not all(math.isfinite(value) for value in (rect.x, rect.y, rect.width, rect.height)) or min(rect.width, rect.height) <= 0:
            raise ValueError("Drawing geometry must contain finite positive rectangles")
    if any(not all(math.isfinite(v) for point in route.points for v in point) for route in flows.routes):
        raise ValueError("Flow route coordinates must be finite")


def _fonts(font_path):
    if font_path is None:
        directory = Path(__file__).resolve().parent / "fonts"
        return _register_font(directory / "NotoSans-Regular.ttf"), _register_font(directory / "NotoSans-Bold.ttf")
    name = _register_font(Path(font_path))
    return name, name


def _register_font(source):
    name = "SheetTTF_" + hashlib.sha256(source.read_bytes()).hexdigest()[:16]
    if name not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(TTFont(name, str(source)))
    return name


def _check_glyphs(texts, font_name):
    for text in texts:
        unsupported = [char for char in text if ord(char) not in pdfmetrics.getFont(font_name).face.charToGlyph and char not in "\n\r\t"]
        if unsupported:
            raise ValueError("Drawing font lacks source glyphs; supply an explicit Unicode TTF font_path: "
                             + ", ".join(f"U+{ord(char):04X}" for char in sorted(set(unsupported))))


def _scale_denominator(building):
    boundary = building.layout.boundary
    required = max(boundary.width_mm * 72 / 25.4 / (_PLAN_RIGHT - _PLAN_LEFT),
                   boundary.height_mm * 72 / 25.4 / (_PLAN_TOP - _PLAN_BOTTOM))
    return next((value for value in (50, 100, 200, 250, 500, 1000, 2000, 5000) if value >= required), math.ceil(required / 1000) * 1000)


def _wrap(text, font, size, width):
    """Wrap measured words, including long identifiers, without clipping."""
    lines, current = [], ""
    for word in text.split():
        while pdfmetrics.stringWidth(word, font, size) > width:
            if current:
                lines.append(current)
                current = ""
            end = 1
            while end < len(word) and pdfmetrics.stringWidth(word[:end + 1], font, size) <= width:
                end += 1
            lines.append(word[:end])
            word = word[end:]
        proposed = f"{current} {word}".strip()
        if current and pdfmetrics.stringWidth(proposed, font, size) > width:
            lines.append(current)
            current = word
        else:
            current = proposed
    if current:
        lines.append(current)
    return lines or [""]


def _paginate(entries, font):
    chunks, current, height = [], [], 0
    available = _PAGE_H - 360
    for color, text in entries:
        lines = _wrap(text, font, 8, _LEGEND_WIDTH - 24)
        required = len(lines) * 11 + 12
        if current and height + required > available:
            chunks.append(current)
            current, height = [], 0
        # A single oversized source entry spans pages rather than reducing type.
        max_lines = int((available - 12) / 11)
        for start in range(0, len(lines), max_lines):
            part = lines[start:start + max_lines]
            part_height = len(part) * 11 + 12
            if current and height + part_height > available:
                chunks.append(current)
                current, height = [], 0
            current.append((color, part))
            height += part_height
    return chunks + ([current] if current else []) or [[]]


def _legends(building, result, equipment, flows, egress_rows, drawing):
    legends = {kind: [] for kind, _, _ in _THEMES}
    for index, zone in enumerate(sorted(building.zones, key=lambda item: item.id)):
        text = f"Z{index + 1:02d} {zone.id} | {zone.type} | direct: {', '.join(zone.room_ids) or 'none'}"
        if zone.parent_zone_id:
            text += f" | parent {zone.parent_zone_id}"
        if zone.cleanroom_class:
            text += f" | declared class {zone.cleanroom_class}"
        if zone.pressure_pa is not None:
            text += f" | declared pressure {zone.pressure_pa:g} Pa"
        legends["zones"].append((_PALETTE[index % len(_PALETTE)], text))
    legends["zones"].append((None, "Zone tint: direct membership. Ancestor membership is listed through the parent relation; no certification inferred."))
    legends["zones"].append((None, "Room schedule (tags shared across every plan):"))
    for index, room in enumerate(sorted(building.layout.rooms, key=lambda item: item.id)):
        rect = result.placements[room.id]
        legends["zones"].append((None, f"R{index + 1:02d} {room.id} | {room.type} | {rect.area_m2:.2f} m2 | {rect.width:g} x {rect.height:g} mm"))
    placements = {item.equipment_id: item for item in equipment.placements}
    for index, item in enumerate(sorted(building.equipment, key=lambda item: item.id)):
        placement = placements.get(item.id)
        text = f"E{index + 1:02d} {item.id} | {item.type}"
        if placement:
            text += f" | room {placement.room_id} | {placement.rect.width:g} x {placement.rect.height:g} mm"
            if drawing.show_equipment_clearance:
                clearance = clearance_rect(item, placement.rect, placement.rotated)
                text += f" | envelope {clearance.width:g} x {clearance.height:g} mm"
        else:
            text += " | UNPLACED - source position UNKNOWN"
        legends["equipment"].append((_PALETTE[0], text))
    legends["equipment"].append((None, "Solid footprint; dashed service envelope from declared clearance. Room tags refer to the zone-sheet room schedule."))
    for index, flow in enumerate(sorted(building.flows, key=lambda item: item.id)):
        supplied = [route for route in flows.routes if route.flow_id == flow.id]
        text = f"F{index + 1:02d} {flow.id} | {flow.type} | {', '.join(flow.from_ids)} to {', '.join(flow.to_ids)}"
        text += f" | declared minimum width {flow.minimum_clear_width_mm:g} mm"
        text += f" | {len(supplied)} supplied route(s)" if supplied else " | UNKNOWN - no supplied route"
        legends["flows"].append((_PALETTE[index % len(_PALETTE)], text))
    legends["flows"].append((None, "Arrows show supplied process-route direction. These drawings do not independently certify route clearance or regulatory compliance."))
    legends["egress"].append((_PALETTE[2], "UNKNOWN - egress compliance. Door-graph candidates only; clearance unverified. No approved evacuation plan or occupancy/fire assessment."))
    legends["egress"].extend((None, text) for text in egress_rows)
    return legends


def _egress_candidates(building, result, wall_plan):
    entry = building.layout.external_entry
    if entry is None:
        return [], ["No explicit external entry - exit location and candidate paths UNKNOWN."]
    external = next((opening for opening in wall_plan.openings if opening.external and opening.id == entry.id), None)
    if external is None:
        return [], [f"Entry {entry.id}: no generated external opening - path source UNKNOWN."]
    graph = {room_id: [] for room_id in result.placements}
    for opening in wall_plan.openings:
        if not opening.external:
            graph[opening.room_a].append((opening.room_b, opening.center))
            graph[opening.room_b].append((opening.room_a, opening.center))
    reached = {entry.room_id: (entry.room_id,)}
    portals = {entry.room_id: ()}
    queue = deque([entry.room_id])
    while queue:
        room_id = queue.popleft()
        for neighbor, portal in sorted(graph[room_id]):
            if neighbor not in reached:
                reached[neighbor] = (*reached[room_id], neighbor)
                portals[neighbor] = (*portals[room_id], portal)
                queue.append(neighbor)
    candidates, rows = [], [f"Declared external entry: {entry.id} | room {entry.room_id} | side {entry.side} | {entry.width_mm:g} mm."]
    for room_id in sorted(result.placements):
        if room_id not in reached:
            rows.append(f"{room_id}: UNKNOWN - no door path to the declared entry.")
            continue
        points = [result.placements[room_id].center]
        for index in range(len(reached[room_id]) - 1, 0, -1):
            points.extend((portals[room_id][index - 1], result.placements[reached[room_id][index - 1]].center))
        points.append(external.center)
        candidates.append(tuple(points))
        rows.append(f"{room_id}: derived candidate via {' / '.join(reversed(reached[room_id]))} to {entry.id}.")
    return candidates, rows


def _frame(pdf, meta, building, drawing, page_count, regular, bold, continuation):
    pdf.setStrokeColorRGB(*_INK)
    pdf.setLineWidth(0.7)
    pdf.rect(_MARGIN, _MARGIN, _PAGE_W - 2 * _MARGIN, _PAGE_H - 2 * _MARGIN)
    pdf.setFillColorRGB(*_INK)
    pdf.setFont(bold, 18)
    project_lines = _wrap(building.layout.project_name, bold, 18, _PAGE_W - 2 * _MARGIN - 250)
    for index, line in enumerate(project_lines[:2]):
        pdf.drawString(_MARGIN + 16, _PAGE_H - 57 - index * 20, line)
    if len(project_lines) > 2:
        raise ValueError("Project title is too long for a two-line sheet header")
    pdf.setFont(regular, 11)
    pdf.drawString(_MARGIN + 16, _PAGE_H - 101, meta.title + (" - legend continuation" if continuation else ""))
    pdf.setFont(bold, 10)
    pdf.drawRightString(_PAGE_W - _MARGIN - 16, _PAGE_H - 56, meta.sheet_id)
    pdf.setFont(regular, 9)
    pdf.drawRightString(_PAGE_W - _MARGIN - 16, _PAGE_H - 74, f"Revision {meta.revision} | Page {meta.page_number}/{page_count}")
    pdf.line(_MARGIN, 110, _PAGE_W - _MARGIN, 110)
    if not continuation:
        pdf.line(_SIDE_X - 16, 110, _SIDE_X - 16, _PAGE_H - 115)
    pdf.setFont(bold, 8)
    pdf.drawString(_MARGIN + 14, 91, f"{drawing.discipline} | Variant {meta.source_variant}")
    pdf.setFont(regular, 8)
    pdf.drawString(_MARGIN + 14, 75, f"LayoutIR {building.layout.version} / BuildingIR {building.version} | Geometry: millimetres")
    pdf.drawString(_MARGIN + 14, 58, "Source projection - independent technical and regulatory review required")
    pdf.setFont(bold, 8)
    pdf.drawString(_SIDE_X, 91, f"{meta.revision} | Current revision only")
    pdf.setFont(regular, 8)
    title_lines = _wrap(drawing.title, regular, 8, _LEGEND_WIDTH)
    for index, line in enumerate(title_lines[:2]):
        pdf.drawString(_SIDE_X, 75 - index * 11, line)
    if len(title_lines) > 2:
        raise ValueError("Drawing revision title is too long for its title block")
    pdf.setFont(bold, 8)
    pdf.drawString(_SIDE_X, 42, f"Status: {meta.status}")


def _base_plan(pdf, building, result, wall_plan, point, scale, drawing, font):
    pdf.setStrokeColorRGB(0.70, 0.73, 0.75)
    pdf.setLineWidth(0.5)
    pdf.setDash(3, 3)
    _rect(pdf, point, scale, Rect(0, 0, building.layout.boundary.width_mm, building.layout.boundary.height_mm))
    for cutout in building.layout.boundary.cutouts:
        _rect(pdf, point, scale, cutout)
    pdf.setDash()
    if drawing.show_structural_grid and building.structural_grid:
        grid = building.structural_grid
        pdf.setDash([5, 3, 1, 3])
        pdf.setFont(font, 7)
        pdf.setFillColorRGB(*_MUTED)
        for index, x in enumerate(grid.axes_x_mm):
            pdf.line(*point(x, 0), *point(x, building.layout.boundary.height_mm))
            px, py = point(x, building.layout.boundary.height_mm)
            pdf.drawCentredString(px, py + 12, grid.labels_x[index] if grid.labels_x else f"X{index + 1}")
        for index, y in enumerate(grid.axes_y_mm):
            pdf.line(*point(0, y), *point(building.layout.boundary.width_mm, y))
            px, py = point(0, y)
            pdf.drawRightString(px - 10, py - 2, grid.labels_y[index] if grid.labels_y else f"Y{index + 1}")
        pdf.setDash()
    pdf.setStrokeColorRGB(*_INK)
    pdf.setLineWidth(0.65)
    for ring in wall_plan.rings():
        path = pdf.beginPath()
        path.moveTo(*point(*ring[0]))
        for xy in ring[1:]:
            path.lineTo(*point(*xy))
        path.close()
        pdf.drawPath(path)


def _room_labels(building, result, point, scale, drawing, font):
    labels = []
    for index, room in enumerate(sorted(building.layout.rooms, key=lambda item: item.id)):
        rect = result.placements[room.id]
        x, top = point(rect.x + rect.width / 2, rect.top)
        clear_width = (rect.width - building.layout.wall_thickness_mm) * scale
        text = f"R{index + 1:02d}"
        size = 8 if pdfmetrics.stringWidth(text, font, 8) + 4 <= clear_width else 6
        y = top - building.layout.wall_thickness_mm * scale / 2 - size - 4
        labels.append((x, y, text, size))
        dimension = f"{rect.width:g} x {rect.height:g}"
        if drawing.show_room_dimensions and pdfmetrics.stringWidth(dimension, font, 6) + 4 <= clear_width and rect.height * scale >= 29:
            labels.append((x, y - 11, dimension, 6))
    return labels


def _room_annotations(pdf, building, result, point, scale, drawing, font):
    pdf.setFillColorRGB(*_INK)
    for x, y, text, size in _room_labels(building, result, point, scale, drawing, font):
        pdf.setFont(font, size)
        pdf.drawCentredString(x, y, text)


def _overlay(pdf, kind, building, result, equipment, flows, candidates, point, scale, drawing, font):
    pdf.setFont(font, 8)
    annotations = []
    if kind == "zones":
        zones = sorted(building.zones, key=lambda item: item.id)
        for room_id, rect in sorted(result.placements.items()):
            memberships = [i for i, zone in enumerate(zones) if room_id in zone.room_ids]
            if memberships:
                index = memberships[0]
                color = _PALETTE[index % len(_PALETTE)]
                pdf.setFillColorRGB(*(0.90 + value * 0.10 for value in color))
                inset = min(180, rect.width / 8, rect.height / 8)
                _rect(pdf, point, scale, Rect(rect.x + inset, rect.y + inset, rect.width - 2 * inset, rect.height - 2 * inset), stroke=0, fill=1)
                pdf.setFillColorRGB(*color)
                x, y = point(rect.x + rect.width / 2, rect.y + rect.height / 2)
                pdf.drawCentredString(x, y, f"Z{index + 1:02d}" + (f" +{len(memberships) - 1}" if len(memberships) > 1 else ""))
    elif kind == "equipment":
        specs = {item.id: item for item in building.equipment}
        tags = {item.id: f"E{index + 1:02d}" for index, item in enumerate(sorted(building.equipment, key=lambda item: item.id))}
        pdf.setStrokeColorRGB(*_PALETTE[0])
        pdf.setFillColorRGB(*_PALETTE[0])
        for placement in equipment.placements:
            if drawing.show_equipment_clearance:
                pdf.setDash(3, 2)
                _rect(pdf, point, scale, clearance_rect(specs[placement.equipment_id], placement.rect, placement.rotated))
                pdf.setDash()
            _rect(pdf, point, scale, placement.rect)
            x, y = point(placement.rect.x + placement.rect.width / 2, placement.rect.top)
            annotations.append((x, y + 7, tags[placement.equipment_id], _PALETTE[0]))
    elif kind == "flows":
        tags = {flow.id: (index, f"F{index + 1:02d}") for index, flow in enumerate(sorted(building.flows, key=lambda item: item.id))}
        for route in sorted(flows.routes, key=lambda route: (route.flow_id, route.from_id, route.to_id, route.points)):
            index, tag = tags[route.flow_id]
            _arrow(pdf, [point(*xy) for xy in route.points], _PALETTE[index % len(_PALETTE)])
            if drawing.show_flow_labels and route.points:
                x, y = point(*route.points[0])
                annotations.append((x + 6, y + 8, tag, _PALETTE[index % len(_PALETTE)]))
    elif kind == "egress":
        pdf.setDash(4, 3)
        for points in candidates:
            _arrow(pdf, [point(*xy) for xy in points], _PALETTE[2])
        pdf.setDash()
        if building.layout.external_entry:
            opening = next(item for item in build_wall_plan(building.layout, result).openings if item.external)
            x, y = point(*opening.center)
            pdf.setStrokeColorRGB(*_PALETTE[2])
            pdf.circle(x, y, 6)
            annotations.append((x + 9, y - 16, "ENTRY", _PALETTE[2]))
    if annotations:
        _draw_tags(pdf, annotations, _room_labels(building, result, point, scale, drawing, font), font)


def _draw_tags(pdf, annotations, room_labels, font):
    """Move compact tags clear of each other and room text, within the plot."""
    def bounds(x, y, text, size, centered=False):
        width = pdfmetrics.stringWidth(text, font, size)
        left = x - width / 2 if centered else x
        return left - 2, y - size * .25 - 2, left + width + 2, y + size + 2

    occupied = [bounds(x, y, text, size, centered=True) for x, y, text, size in room_labels]
    for anchor_x, anchor_y, text, color in annotations:
        chosen = None
        for radius in range(33):
            offsets = [(0, 0)] if radius == 0 else [(dx, dy) for dx in range(-radius, radius + 1)
                                                   for dy in range(-radius, radius + 1)
                                                   if max(abs(dx), abs(dy)) == radius]
            for dx, dy in offsets:
                x, y = anchor_x + dx * 12, anchor_y + dy * 12
                box = bounds(x, y, text, 8)
                if box[0] < _PLAN_LEFT or box[1] < _PLAN_BOTTOM or box[2] > _PLAN_RIGHT or box[3] > _PLAN_TOP:
                    continue
                if any(box[0] < other[2] and other[0] < box[2] and box[1] < other[3] and other[1] < box[3]
                       for other in occupied):
                    continue
                chosen = x, y, box
                break
            if chosen:
                break
        if chosen is None:
            raise ValueError("Sheet annotation density exceeds readable plot capacity")
        x, y, box = chosen
        occupied.append(box)
        pdf.setDash()
        pdf.setStrokeColorRGB(*color)
        if abs(x - anchor_x) + abs(y - anchor_y) > .1:
            pdf.setLineWidth(.4)
            pdf.line(anchor_x, anchor_y, x, y)
        pdf.setFillColorRGB(1, 1, 1)
        pdf.rect(box[0], box[1], box[2] - box[0], box[3] - box[1], stroke=0, fill=1)
        pdf.setFillColorRGB(*color)
        pdf.setFont(font, 8)
        pdf.drawString(x, y, text)


def _rect(pdf, point: Callable, scale: float, rect: Rect, *, stroke=1, fill=0):
    pdf.rect(*point(rect.x, rect.y), rect.width * scale, rect.height * scale, stroke=stroke, fill=fill)


def _arrow(pdf, points, color):
    pdf.setStrokeColorRGB(*color)
    pdf.setFillColorRGB(*color)
    pdf.setLineWidth(1.3)
    for a, b in zip(points, points[1:]):
        pdf.line(*a, *b)
    for a, b in reversed(list(zip(points, points[1:]))):
        dx, dy = b[0] - a[0], b[1] - a[1]
        length = math.hypot(dx, dy)
        if length > 1e-6:
            ux, uy = dx / length, dy / length
            pdf.line(*b, b[0] - ux * 8 + uy * 4, b[1] - uy * 8 - ux * 4)
            pdf.line(*b, b[0] - ux * 8 - uy * 4, b[1] - uy * 8 + ux * 4)
            break


def _legend(pdf, rows, regular, bold, *, x=_SIDE_X, top=_PAGE_H - 130):
    pdf.setFillColorRGB(*_INK)
    pdf.setFont(bold, 10)
    pdf.drawString(x, top, "LEGEND / SOURCE SCHEDULE")
    y = top - 24
    pdf.setFont(regular, 8)
    for color, lines in rows:
        if color:
            pdf.setFillColorRGB(*color)
            pdf.rect(x, y - 1, 8, 8, stroke=0, fill=1)
        pdf.setFillColorRGB(*_INK)
        for line in lines:
            pdf.drawString(x + 17, y, line)
            y -= 11
        y -= 12


def _scale_bar(pdf, denominator, scale, font):
    length_mm = max(1000, int(100 / scale / 1000) * 1000)
    length = length_mm * scale
    x, y = _PLAN_LEFT, 129
    pdf.setStrokeColorRGB(*_INK)
    pdf.setFillColorRGB(*_INK)
    pdf.setLineWidth(0.8)
    pdf.line(x, y, x + length, y)
    for tick in (0, length / 2, length):
        pdf.line(x + tick, y - 3, x + tick, y + 3)
    pdf.setFont(font, 7)
    pdf.drawCentredString(x, y - 13, "0")
    pdf.drawCentredString(x + length, y - 13, f"{length_mm / 1000:g} m")
    pdf.drawString(x + length + 14, y - 13, f"1:{denominator} at A3, 100% print")

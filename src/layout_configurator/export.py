"""DXF and vector-PDF exporters for a solver result."""

from __future__ import annotations

import math
import re
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path

from .building import BuildingIR, EquipmentSpec, StructuralGridSpec
from .equipment import EquipmentLayoutResult, clearance_rect
from .drawing_symbols import draw_equipment_symbol_pdf, equipment_symbol_paths, validate_symbol_assignments
from .dimension_chains import (DIMENSION_PADDING_MM, DIMENSION_MIN_PADDING_PT, build_dimension_chains,
                               draw_dimension_chains_dxf, draw_dimension_chains_pdf)
from .facility import FacilityDrawingProfile
from .flows import FlowRoutingResult, FlowValidationReport
from .models import LayoutIR, LayoutResult, Rect
from .result_geometry import require_rectangular_layout
from .validation import ValidationReport
from .walls import build_wall_plan


LAYERS = (
    "A-WALL",
    "A-ROOM",
    "A-DOOR",
    "A-WINDOW",
    "A-DIMS",
    "A-HATCH",
    "A-TEXT",
    "A-TITLE",
    "A-EQUIP",
    "A-CLEARANCE",
    "A-FLOW",
    "A-AXIS",
    "A-LEGEND",
)


def export_dxf(
    path: str | Path,
    spec: LayoutIR,
    result: LayoutResult,
    equipment: EquipmentLayoutResult | None = None,
    equipment_specs: Iterable[EquipmentSpec] | None = None,
    flows: FlowRoutingResult | None = None,
    structural_grid: StructuralGridSpec | None = None,
    flow_types: Mapping[str, str] | None = None,
    drawing_profile: FacilityDrawingProfile | None = None,
) -> None:
    require_rectangular_layout(result, "DXF export")
    import ezdxf
    from ezdxf.enums import TextEntityAlignment

    drawing = drawing_profile or FacilityDrawingProfile()
    if drawing.equipment_symbols:
        equipment_specs = tuple(equipment_specs or ())
    symbols = validate_symbol_assignments(drawing, equipment_specs or ()) if drawing.equipment_symbols else {}
    chains = build_dimension_chains(spec, result, structural_grid) if drawing.show_dimension_chains else ()
    document = ezdxf.new("R2018")
    document.header["$INSUNITS"] = 4
    for name in LAYERS:
        if name not in document.layers:
            document.layers.add(name)
    document.layers.get("A-ROOM").dxf.plot = 0
    document.appids.add("LAYOUT_IR")
    for name, color in (
        ("A-WALL", 7),
        ("A-ROOM", 8),
        ("A-DOOR", 3),
        ("A-DIMS", 2),
        ("A-TEXT", 7),
        ("A-TITLE", 4),
        ("A-EQUIP", 1),
        ("A-CLEARANCE", 6),
        ("A-FLOW", 5),
        ("A-AXIS", 9),
        ("A-LEGEND", 4),
    ):
        document.layers.get(name).dxf.color = color

    modelspace = document.modelspace()
    boundary = spec.boundary
    modelspace.add_lwpolyline(
        [(0, 0), (boundary.width_mm, 0), (boundary.width_mm, boundary.height_mm), (0, boundary.height_mm)],
        close=True,
        dxfattribs={"layer": "A-TITLE", "linetype": "DASHED"},
    )
    for cutout in boundary.cutouts:
        _add_rect_polyline(modelspace, cutout, "A-HATCH")

    wall_plan = build_wall_plan(spec, result)
    for ring in wall_plan.rings():
        modelspace.add_lwpolyline(ring, close=True, dxfattribs={"layer": "A-WALL"})

    for room in spec.rooms:
        rect = result.placements[room.id]
        outline = modelspace.add_lwpolyline(
            [(rect.x, rect.y), (rect.right, rect.y), (rect.right, rect.top), (rect.x, rect.top)],
            close=True,
            dxfattribs={"layer": "A-ROOM"},
        )
        outline.set_xdata("LAYOUT_IR", [(1000, f"room_id={room.id}"), (1000, f"room_type={room.type}")])
        label = f"{room.id} ({rect.area_m2:.2f} m2)"
        text = modelspace.add_text(
            label,
            dxfattribs={"height": _room_label_height(label, rect), "layer": "A-TEXT"},
        )
        text.set_placement(rect.center, align=TextEntityAlignment.MIDDLE_CENTER)
        if drawing.show_room_dimensions:
            dimension = modelspace.add_text(
                f"{rect.width:.0f} x {rect.height:.0f} mm",
                dxfattribs={"height": 75, "layer": "A-DIMS"},
            )
            dimension.set_placement((rect.center[0], rect.y + 180), align=TextEntityAlignment.MIDDLE_CENTER)

    if not chains:
        modelspace.add_line((0, -500), (boundary.width_mm, -500), dxfattribs={"layer": "A-DIMS"})
        modelspace.add_text(f"{boundary.width_mm:.0f} mm", dxfattribs={"height": 180, "layer": "A-DIMS"}).set_placement(
            (boundary.width_mm / 2, -800), align=TextEntityAlignment.MIDDLE_CENTER
        )
        modelspace.add_line((-500, 0), (-500, boundary.height_mm), dxfattribs={"layer": "A-DIMS"})
        modelspace.add_text(f"{boundary.height_mm:.0f} mm", dxfattribs={"height": 180, "layer": "A-DIMS"}).set_placement(
            (-800, boundary.height_mm / 2), align=TextEntityAlignment.MIDDLE_CENTER
        )
    if chains:
        draw_dimension_chains_dxf(document, modelspace, chains)

    for opening in wall_plan.openings:
        block_name = f"DOOR_{opening.width:.0f}"
        if block_name not in document.blocks:
            block = document.blocks.new(block_name)
            block.add_line((0, 0), (opening.width, 0))
            block.add_arc((0, 0), opening.width, 0, 90)
        x, y = (opening.fixed, opening.start) if opening.orientation == "vertical" else (opening.start, opening.fixed)
        insert = modelspace.add_blockref(block_name, (x, y), dxfattribs={"layer": "A-DOOR"})
        if opening.orientation == "vertical":
            insert.dxf.rotation = 90
    for opening in wall_plan.windows:
        block_name = f"WINDOW_{opening.width:.0f}"
        if block_name not in document.blocks:
            block = document.blocks.new(block_name)
            block.add_line((0, 0), (opening.width, 0))
            block.add_line((0, -100), (0, 100))
            block.add_line((opening.width, -100), (opening.width, 100))
        x, y = (opening.fixed, opening.start) if opening.orientation == "vertical" else (opening.start, opening.fixed)
        insert = modelspace.add_blockref(block_name, (x, y), dxfattribs={"layer": "A-WINDOW"})
        if opening.orientation == "vertical":
            insert.dxf.rotation = 90

    if equipment is not None:
        _draw_equipment_dxf(
            document,
            modelspace,
            equipment,
            equipment_specs or (),
            show_clearance=drawing.show_equipment_clearance,
            symbols=symbols,
        )
    if flows is not None:
        _draw_flows_dxf(
            modelspace,
            flows,
            flow_types or {},
            show_labels=drawing.show_flow_labels,
            boundary=(boundary.width_mm, boundary.height_mm),
            reserved=_room_label_boxes(spec, result),
        )
    if structural_grid is not None and drawing.show_structural_grid:
        _draw_structural_grid_dxf(modelspace, spec, structural_grid)
    if drawing.show_flow_legend and (equipment is not None or flows is not None):
        _draw_facility_legend_dxf(modelspace, spec, flows, flow_types or {}, drawing)

    title_x = boundary.width_mm - 3_000
    title_y = boundary.height_mm + 1_000
    modelspace.add_text(
        f"{spec.project_name} | {drawing.title}",
        dxfattribs={"height": 250, "layer": "A-TITLE"},
    ).set_placement((title_x, title_y))
    modelspace.add_text(
        f"{drawing.sheet_id} | {drawing.discipline} | Rev {drawing.revision} | Variant {result.variant} | LayoutIR {spec.version}",
        dxfattribs={"height": 150, "layer": "A-TITLE"},
    ).set_placement((title_x, title_y - 400))
    document.saveas(path)


def export_building_dxf(
    path: str | Path,
    building: BuildingIR,
    result: LayoutResult,
    equipment: EquipmentLayoutResult,
    flows: FlowRoutingResult | None = None,
    drawing_profile: FacilityDrawingProfile | None = None,
) -> None:
    """Export a building result with equipment blocks and service envelopes."""

    export_dxf(
        path,
        building.layout,
        result,
        equipment,
        building.equipment,
        flows,
        building.structural_grid,
        {item.id: item.type for item in building.flows},
        drawing_profile,
    )


def export_pdf(
    path: str | Path,
    spec: LayoutIR,
    result: LayoutResult,
    report: ValidationReport | None = None,
    equipment: EquipmentLayoutResult | None = None,
    equipment_specs: Iterable[EquipmentSpec] | None = None,
    flows: FlowRoutingResult | None = None,
    flow_report: FlowValidationReport | None = None,
    structural_grid: StructuralGridSpec | None = None,
    flow_types: Mapping[str, str] | None = None,
    drawing_profile: FacilityDrawingProfile | None = None,
) -> None:
    require_rectangular_layout(result, "PDF export")
    from reportlab.lib.pagesizes import A3, landscape
    from reportlab.pdfgen import canvas

    drawing = drawing_profile or FacilityDrawingProfile()
    if equipment is not None or drawing.equipment_symbols:
        equipment_specs = tuple(equipment_specs or ())
    symbols = validate_symbol_assignments(drawing, equipment_specs or ()) if drawing.equipment_symbols else {}
    chains = build_dimension_chains(spec, result, structural_grid) if drawing.show_dimension_chains else ()
    page_width, page_height = landscape(A3)
    margin = 45
    title_height = 45
    padding = DIMENSION_PADDING_MM if chains else 0
    scale = min(
        (page_width - 2 * margin) / (spec.boundary.width_mm + padding),
        (page_height - 2 * margin - title_height) / (spec.boundary.height_mm + padding),
    )
    if chains:
        scale = min(scale, (page_width - 2 * margin - DIMENSION_MIN_PADDING_PT) / spec.boundary.width_mm,
                    (page_height - 2 * margin - title_height - DIMENSION_MIN_PADDING_PT) / spec.boundary.height_mm)
        padding = max(padding, DIMENSION_MIN_PADDING_PT / scale)
    origin_x = margin + padding * scale
    origin_y = margin + title_height + padding * scale
    pdf = canvas.Canvas(str(path), pagesize=(page_width, page_height))
    pdf.setTitle(f"{spec.project_name} - variant {result.variant}")

    def point(x: float, y: float) -> tuple[float, float]:
        return origin_x + x * scale, origin_y + y * scale

    plot_bounds = (
        origin_x, origin_y, origin_x + spec.boundary.width_mm * scale,
        origin_y + spec.boundary.height_mm * scale,
    )
    room_anchors = _room_pdf_anchors(pdf, point, spec, result, scale, plot_bounds, drawing.show_room_dimensions)
    original_annotations = [row["box"] for row in room_anchors]
    dimension_boxes = [row["dimension"][3] for row in room_anchors if row["dimension"] is not None]
    original_annotations.extend(dimension_boxes)
    equipment_plan, equipment_annotations = ([], [])
    if equipment is not None:
        equipment_plan, equipment_annotations = _equipment_pdf_plan(
            pdf, point, equipment, equipment_specs or (), reserved=original_annotations, plot_bounds=plot_bounds,
        )
    room_plan = _room_pdf_labels(
        pdf, room_anchors, dimension_boxes + equipment_annotations,
        _equipment_pdf_ink_boxes(point, equipment, symbols), plot_bounds,
    )

    pdf.setLineWidth(0.5)
    pdf.setDash(3, 2)
    pdf.rect(origin_x, origin_y, spec.boundary.width_mm * scale, spec.boundary.height_mm * scale)
    pdf.setDash()
    pdf.setLineWidth(0.6)
    for cutout in spec.boundary.cutouts:
        x, y = point(cutout.x, cutout.y)
        pdf.setDash(3, 2)
        pdf.rect(x, y, cutout.width * scale, cutout.height * scale)
        pdf.setDash()

    wall_plan = build_wall_plan(spec, result)
    pdf.setLineWidth(0.7)
    for ring in wall_plan.rings():
        path = pdf.beginPath()
        first_x, first_y = point(*ring[0])
        path.moveTo(first_x, first_y)
        for ring_x, ring_y in ring[1:]:
            path.lineTo(*point(ring_x, ring_y))
        path.close()
        pdf.drawPath(path, stroke=1, fill=0)

    if chains:
        draw_dimension_chains_pdf(pdf, point, chains)

    reserved = []
    for row, (center_x, center_y, bounds) in zip(room_anchors, room_plan):
        pdf.setFont("Helvetica", row["size"])
        pdf.drawCentredString(center_x, center_y, row["text"])
        reserved.append(bounds)
        if row["dimension"] is not None:
            pdf.setFont("Helvetica", 4.5)
            pdf.setFillColorRGB(0.35, 0.35, 0.35)
            dimension, dimension_x, dimension_y, dimension_box = row["dimension"]
            pdf.drawCentredString(dimension_x, dimension_y, dimension)
            reserved.append(dimension_box)
            pdf.setFillColorRGB(0.0, 0.0, 0.0)

    pdf.setLineWidth(0.8)
    for opening in wall_plan.windows:
        if opening.orientation == "vertical":
            pdf.line(*point(opening.fixed, opening.start), *point(opening.fixed, opening.end))
        else:
            pdf.line(*point(opening.start, opening.fixed), *point(opening.end, opening.fixed))

    if equipment is not None:
        _draw_equipment_pdf(
            pdf,
            point,
            equipment,
            equipment_specs or (),
            show_clearance=drawing.show_equipment_clearance,
            symbols=symbols,
            reserved=original_annotations,
            plot_bounds=plot_bounds,
            annotation_plan=equipment_plan,
        )
        reserved.extend(equipment_annotations)
    if flows is not None:
        _draw_flows_pdf(
            pdf,
            point,
            flows,
            flow_types or {},
            show_labels=drawing.show_flow_labels,
            boundary=(spec.boundary.width_mm, spec.boundary.height_mm),
            reserved=reserved,
        )
    if structural_grid is not None and drawing.show_structural_grid:
        _draw_structural_grid_pdf(pdf, point, spec, structural_grid)

    pdf.setStrokeColorRGB(0.2, 0.2, 0.2)
    pdf.setFont("Helvetica-Bold", 12)
    pdf.drawString(margin, page_height - margin + 5, f"{spec.project_name} | {drawing.title}")
    pdf.setFont("Helvetica", 8)
    status = "VALID" if report is None or report.ok else f"INVALID ({len(report.issues)} issues)"
    flow_status = "" if flow_report is None else (" | FLOW OK" if flow_report.ok else f" | FLOW ({len(flow_report.issues)} issues)")
    pdf.drawRightString(
        page_width - margin,
        page_height - margin + 5,
        f"{drawing.sheet_id} Rev {drawing.revision} | Variant {result.variant} | {status}{flow_status}",
    )
    if drawing.show_flow_legend and flows is not None and flows.routes:
        types = sorted({flow_types.get(route.flow_id, "unspecified") for route in flows.routes})
        pdf.setFont("Helvetica", 6)
        pdf.drawString(margin, page_height - margin - 10, "Flow legend: " + ", ".join(types))
    pdf.setFont("Helvetica", 7)
    pdf.drawString(margin, 20, "Design aid - not a permit or code-compliance verdict")
    pdf.save()


def export_building_pdf(
    path: str | Path,
    building: BuildingIR,
    result: LayoutResult,
    equipment: EquipmentLayoutResult,
    report: ValidationReport | None = None,
    flows: FlowRoutingResult | None = None,
    flow_report: FlowValidationReport | None = None,
    drawing_profile: FacilityDrawingProfile | None = None,
) -> None:
    """Export a building result with vector equipment and clearance overlays."""

    export_pdf(
        path,
        building.layout,
        result,
        report,
        equipment,
        building.equipment,
        flows,
        flow_report,
        building.structural_grid,
        {item.id: item.type for item in building.flows},
        drawing_profile,
    )


def export_bundle(directory: str | Path, spec: LayoutIR, result: LayoutResult, report: ValidationReport | None = None) -> tuple[Path, Path]:
    output = Path(directory)
    output.mkdir(parents=True, exist_ok=True)
    stem = f"layout_{result.variant:02d}"
    dxf_path = output / f"{stem}.dxf"
    pdf_path = output / f"{stem}.pdf"
    export_dxf(dxf_path, spec, result)
    export_pdf(pdf_path, spec, result, report)
    return dxf_path, pdf_path


def export_building_bundle(
    directory: str | Path,
    building: BuildingIR,
    result: LayoutResult,
    equipment: EquipmentLayoutResult,
    report: ValidationReport | None = None,
    flows: FlowRoutingResult | None = None,
    flow_report: FlowValidationReport | None = None,
    drawing_profile: FacilityDrawingProfile | None = None,
) -> tuple[Path, Path]:
    """Export the complete drawing projection for one BuildingIR variant."""

    output = Path(directory)
    output.mkdir(parents=True, exist_ok=True)
    stem = f"building_{result.variant:02d}"
    dxf_path = output / f"{stem}.dxf"
    pdf_path = output / f"{stem}.pdf"
    export_building_dxf(dxf_path, building, result, equipment, flows, drawing_profile)
    export_building_pdf(pdf_path, building, result, equipment, report, flows, flow_report, drawing_profile)
    return dxf_path, pdf_path


def _draw_equipment_dxf(
    document,
    modelspace,
    equipment: EquipmentLayoutResult,
    equipment_specs: Iterable[EquipmentSpec],
    *,
    show_clearance: bool = True,
    symbols: Mapping[str, str] | None = None,
) -> None:
    specs = {item.id: item for item in equipment_specs}
    for placement in equipment.placements:
        spec = specs.get(placement.equipment_id)
        if spec is None:
            raise ValueError(f"Equipment placement references unknown equipment {placement.equipment_id}")
        if show_clearance:
            clearance = clearance_rect(spec, placement.rect, placement.rotated)
            _add_rect_polyline(modelspace, clearance, "A-CLEARANCE", lineweight=13, linetype="DASHED")

        block_name = _equipment_block_name(spec.id)
        if block_name not in document.blocks:
            block = document.blocks.new(block_name)
            block.add_lwpolyline(
                [(0, 0), (placement.rect.width, 0), (placement.rect.width, placement.rect.height), (0, placement.rect.height)],
                close=True,
                dxfattribs={"layer": "A-EQUIP", "lineweight": 25},
            )
            if symbols and spec.id in symbols:
                for points, closed in equipment_symbol_paths(symbols[spec.id], spec, placement):
                    block.add_lwpolyline([(x - placement.rect.x, y - placement.rect.y) for x, y in points],
                                        close=closed, dxfattribs={"layer": "A-EQUIP"})
            else:
                block.add_line((0, 0), (placement.rect.width, placement.rect.height), dxfattribs={"layer": "A-EQUIP"})
                block.add_line((0, placement.rect.height), (placement.rect.width, 0), dxfattribs={"layer": "A-EQUIP"})
            local_center = (placement.rect.width / 2, placement.rect.height / 2)
            block.add_text(
                spec.id,
                dxfattribs={"height": _equipment_label_height(placement.rect), "layer": "A-EQUIP"},
            ).set_placement(local_center)
            block.add_text(
                spec.type,
                dxfattribs={"height": max(45.0, _equipment_label_height(placement.rect) * 0.7), "layer": "A-EQUIP"},
            ).set_placement((local_center[0], local_center[1] - _equipment_label_height(placement.rect)))
        insert = modelspace.add_blockref(block_name, (placement.rect.x, placement.rect.y), dxfattribs={"layer": "A-EQUIP"})
        if symbols and spec.id in symbols:
            if "FLC_SYMBOL" not in document.appids:
                document.appids.new("FLC_SYMBOL")
            insert.set_xdata("FLC_SYMBOL", [(1000, f"equipment_id={spec.id}"), (1000, f"symbol={symbols[spec.id]}"),
                                            (1000, "scope=project_schematic_not_manufacturer_geometry")])


def _draw_flows_dxf(
    modelspace,
    flows: FlowRoutingResult,
    flow_types: Mapping[str, str],
    *,
    show_labels: bool = True,
    boundary: tuple[float, float] = (0.0, 0.0),
    reserved: Sequence[tuple[float, float, float, float]] = (),
) -> None:
    drawn = [route for route in flows.routes if len(route.points) >= 2]
    for route in drawn:
        modelspace.add_lwpolyline(
            route.points,
            dxfattribs={"layer": "A-FLOW", "linetype": "DASHED", "lineweight": 18},
        )
    if not show_labels or not drawn:
        return
    height = _flow_label_height(boundary)
    texts = [
        f"FLOW {route.flow_id} [{flow_types.get(route.flow_id, 'unspecified')}] / {route.minimum_clear_width_mm:.0f} mm"
        for route in drawn
    ]
    positions = place_annotation_labels(
        [flow_label_anchor(route.points, index, boundary) for index, route in enumerate(drawn)],
        [(_text_extent(text, height), height) for text in texts],
        step=height * 1.6,
        reserved=reserved,
    )
    for text, position in zip(texts, positions):
        modelspace.add_text(text, dxfattribs={"height": height, "layer": "A-FLOW"}).set_placement(position)


def _draw_facility_legend_dxf(
    modelspace,
    spec: LayoutIR,
    flows: FlowRoutingResult | None,
    flow_types: Mapping[str, str],
    drawing: FacilityDrawingProfile,
) -> None:
    """Add a compact, machine-readable layer legend outside the plan boundary."""

    x = spec.boundary.width_mm + drawing.legend_offset_x_mm
    y = spec.boundary.height_mm + drawing.legend_offset_y_mm
    entries = [
        ("A-WALL", "walls"),
        ("A-EQUIP", "equipment footprint"),
        ("A-CLEARANCE", "service clearance"),
        ("A-FLOW", "derived flow centerline"),
        ("A-DIMS", "dimensions"),
    ]
    if flows is not None and flows.routes:
        types = ", ".join(sorted({flow_types.get(route.flow_id, "unspecified") for route in flows.routes}))
        entries.append(("FLOW TYPES", types))
    modelspace.add_text(
        "FACILITY LAYOUT LEGEND",
        dxfattribs={"height": 180, "layer": "A-LEGEND"},
    ).set_placement((x, y))
    modelspace.add_text(
        f"{drawing.sheet_id} | {drawing.discipline} | Rev {drawing.revision}",
        dxfattribs={"height": 120, "layer": "A-LEGEND"},
    ).set_placement((x, y - 180))
    modelspace.add_text(
        drawing.title,
        dxfattribs={"height": 120, "layer": "A-LEGEND"},
    ).set_placement((x, y - 340))
    for index, (key, description) in enumerate(entries, start=1):
        modelspace.add_text(
            f"{key}: {description}",
            dxfattribs={"height": 100, "layer": "A-LEGEND"},
        ).set_placement((x, y - (index + 2) * 220))


def _draw_structural_grid_dxf(modelspace, spec: LayoutIR, grid: StructuralGridSpec) -> None:
    x_labels = grid.labels_x or tuple(str(index + 1) for index in range(len(grid.axes_x_mm)))
    y_labels = grid.labels_y or tuple(str(index + 1) for index in range(len(grid.axes_y_mm)))
    for axis, label in zip(grid.axes_x_mm, x_labels):
        modelspace.add_line(
            (axis, -600),
            (axis, spec.boundary.height_mm + 600),
            dxfattribs={"layer": "A-AXIS", "linetype": "DASHED"},
        )
        modelspace.add_text(label, dxfattribs={"height": 140, "layer": "A-AXIS"}).set_placement(
            (axis, spec.boundary.height_mm + 800)
        )
    for axis, label in zip(grid.axes_y_mm, y_labels):
        modelspace.add_line(
            (-600, axis),
            (spec.boundary.width_mm + 600, axis),
            dxfattribs={"layer": "A-AXIS", "linetype": "DASHED"},
        )
        modelspace.add_text(label, dxfattribs={"height": 140, "layer": "A-AXIS"}).set_placement(
            (-800, axis)
        )


def _pdf_centered_text_box(pdf, x, y, text, font, size):
    """Measure actual PDF-point centered text in its existing font."""
    from reportlab.pdfbase import pdfmetrics

    width = pdf.stringWidth(text, font, size)
    ascent, descent = pdfmetrics.getAscentDescent(font, size)
    return x - width / 2, y + descent, x + width / 2, y + ascent


def _room_pdf_anchors(pdf, point, spec, result, scale, plot_bounds, show_dimensions):
    """Measure original captions and reject known impossible widths early."""
    records = []
    for room in spec.rooms:
        rect = result.placements[room.id]
        left, bottom = point(rect.x, rect.y)
        right, top = point(rect.right, rect.top)
        center_x, center_y = (left + right) / 2, (bottom + top) / 2
        size = max(5, min(11, rect.width * scale / 16))
        text = f"{room.id} ({rect.area_m2:.1f} m2)"
        bounds = _pdf_centered_text_box(pdf, center_x, center_y, text, "Helvetica", size)
        host = (max(left, plot_bounds[0]), max(bottom, plot_bounds[1]),
                min(right, plot_bounds[2]), min(top, plot_bounds[3]))
        if (not all(math.isfinite(value) for value in (*bounds, *host)) or
                bounds[0] < host[0] or bounds[2] > host[2] or
                bounds[3] - bounds[1] > host[3] - host[1]):
            raise ValueError("Room annotation density exceeds readable plan capacity")
        dimension = None
        if show_dimensions:
            caption = f"{rect.width:.0f} x {rect.height:.0f} mm"
            dimension = (caption, center_x, bottom + 5,
                _pdf_centered_text_box(pdf, center_x, bottom + 5, caption, "Helvetica", 4.5))
        records.append({"text": text, "size": size, "x": center_x, "y": center_y,
            "host": host, "box": bounds, "dimension": dimension})
    return records


def _equipment_pdf_ink_boxes(point, equipment, symbols):
    """Reserve painted body half-widths and conservative symbol inventories."""
    occupied = []
    placements = () if equipment is None else equipment.placements
    for placement in placements:
        left, bottom = point(placement.rect.x, placement.rect.y)
        right, top = point(placement.rect.right, placement.rect.top)
        half_width = .4  # The actual unchanged body/symbol stroke is 0.8 pt.
        if placement.equipment_id in symbols:
            # Native symbol corners retain the PDF's default miter limit 10.
            # Acute joins can exceed half a stroke beyond the literal frame.
            symbol_padding = half_width * 10
            occupied.append((left - symbol_padding, bottom - symbol_padding,
                right + symbol_padding, top + symbol_padding))
        else:
            occupied.extend((
                (left - half_width, bottom - half_width, left + half_width, top + half_width),
                (right - half_width, bottom - half_width, right + half_width, top + half_width),
                (left - half_width, bottom - half_width, right + half_width, bottom + half_width),
                (left - half_width, top - half_width, right + half_width, top + half_width),
            ))
    return occupied


def _room_pdf_labels(pdf, anchors, reserved, ink, plot_bounds):
    """Keep clear original centers or choose finite in-host vertical offsets."""
    occupied, planned = list(reserved), []
    for index, row in enumerate(anchors):
        other_rooms = [other["box"] for other in anchors[index + 1:]]
        step = row["size"] * 1.6
        for attempt in range(13):
            shift = (0.0 if attempt == 0 else
                step * ((attempt + 1) // 2) * (1.0 if attempt % 2 else -1.0))
            x, y = row["x"], row["y"] + shift
            bounds = _pdf_centered_text_box(pdf, x, y, row["text"], "Helvetica", row["size"])
            if not all(math.isfinite(value) for value in bounds):
                continue
            if any(bounds[0] < frame[0] or bounds[1] < frame[1] or
                   bounds[2] > frame[2] or bounds[3] > frame[3] for frame in (row["host"], plot_bounds)):
                continue
            if any(_boxes_overlap(bounds, other) for other in (*occupied, *other_rooms, *ink)):
                continue
            planned.append((x, y, bounds))
            occupied.append(bounds)
            break
        else:
            raise ValueError("Room annotation density exceeds readable plan capacity")
    return planned


def _pdf_leader_box(leader):
    """Reserve the actual painted 0.4-point guide strip for later text."""
    return (
        min(leader[0], leader[2]) - .2, min(leader[1], leader[3]) - .2,
        max(leader[0], leader[2]) + .2, max(leader[1], leader[3]) + .2,
    )


def _equipment_pdf_label(pdf, text, size, anchor, body, reserved, plot_bounds, leaders):
    """Keep a clear anchor or choose a bounded, nonobscuring vertical offset."""
    anchor_x, anchor_y = anchor
    step = size * 1.6
    for attempt in range(13):
        shift = (
            0.0 if attempt == 0
            else step * ((attempt + 1) // 2) * (1.0 if attempt % 2 else -1.0)
        )
        x, y = anchor_x, anchor_y + shift
        bounds = _pdf_centered_text_box(pdf, x, y, text, "Helvetica-Bold", size)
        if plot_bounds is not None and (
            bounds[0] < plot_bounds[0] or bounds[1] < plot_bounds[1]
            or bounds[2] > plot_bounds[2] or bounds[3] > plot_bounds[3]
        ):
            continue
        if any(_boxes_overlap(bounds, other) for other in reserved) or any(
            _boxes_overlap(bounds, other) for other in leaders
        ):
            continue
        leader = None
        # Text that still meets its own literal body retains its association
        # and needs no line through the room's existing lettering. Separated
        # text connects the nearest body edge to the text edge without crossing
        # already reserved room, dimension or equipment glyph box.
        if bounds[1] > body[3]:
            leader = (x, body[3], x, bounds[1])
        elif bounds[3] < body[1]:
            leader = (x, body[1], x, bounds[3])
        if leader is not None:
            # Guide-guide coincidence remains allowed. Only glyph boxes block
            # a new guide; its painted strip blocks all later glyph placement.
            strip = _pdf_leader_box(leader)
            if any(_boxes_overlap(strip, other) for other in reserved):
                continue
        return x, y, bounds, leader
    raise ValueError("Equipment annotation density exceeds readable plan capacity")


def _equipment_pdf_plan(pdf, point, equipment, equipment_specs, *, reserved=(), plot_bounds=None):
    """Freeze the existing gear caption/guide choices before room placement."""
    specs = {item.id: item for item in equipment_specs}
    occupied, annotations, leaders, plans = list(reserved), [], [], []
    for placement in equipment.placements:
        spec = specs.get(placement.equipment_id)
        if spec is None:
            raise ValueError(f"Equipment placement references unknown equipment {placement.equipment_id}")
        clearance = clearance_rect(spec, placement.rect, placement.rotated)
        _, bottom = point(clearance.x, clearance.y)
        _, top = point(clearance.right, clearance.top)
        left, physical_bottom = point(placement.rect.x, placement.rect.y)
        right, physical_top = point(placement.rect.right, placement.rect.top)
        size = max(3.5, min(7.0,
            placement.rect.height * (top - bottom) / max(clearance.height, 1.0) / 3.0))
        x, y, bounds, leader = _equipment_pdf_label(pdf, spec.id, size,
            ((left + right) / 2, (physical_bottom + physical_top) / 2),
            (left, physical_bottom, right, physical_top), occupied, plot_bounds, leaders)
        plans.append((x, y, bounds, leader, size))
        if leader is not None:
            strip = _pdf_leader_box(leader)
            leaders.append(strip)
            annotations.append(strip)
        occupied.append(bounds)
        annotations.append(bounds)
    return plans, annotations


def _draw_equipment_pdf(
    pdf,
    point,
    equipment: EquipmentLayoutResult,
    equipment_specs: Iterable[EquipmentSpec],
    *,
    show_clearance: bool = True,
    symbols: Mapping[str, str] | None = None,
    reserved: Sequence[tuple[float, float, float, float]] = (),
    plot_bounds: tuple[float, float, float, float] | None = None,
    annotation_plan=None,
) -> list[tuple[float, float, float, float]]:
    specs = {item.id: item for item in equipment_specs}
    if annotation_plan is None:
        annotation_plan, _ = _equipment_pdf_plan(
            pdf, point, equipment, specs.values(), reserved=reserved, plot_bounds=plot_bounds,
        )
    if len(annotation_plan) != len(equipment.placements):
        raise ValueError("Equipment annotation inventory differs from supplied placements")
    annotations = []
    for placement, plan in zip(equipment.placements, annotation_plan):
        label_x, label_y, label_box, leader, font_size = plan
        spec = specs.get(placement.equipment_id)
        if spec is None:
            raise ValueError(f"Equipment placement references unknown equipment {placement.equipment_id}")
        clearance = clearance_rect(spec, placement.rect, placement.rotated)
        x, y = point(clearance.x, clearance.y)
        right_x, top_y = point(clearance.right, clearance.top)
        if show_clearance:
            pdf.setStrokeColorRGB(0.35, 0.35, 0.75)
            pdf.setLineWidth(0.45)
            pdf.setDash(3, 2)
            pdf.rect(x, y, right_x - x, top_y - y)
            pdf.setDash()

        pdf.setStrokeColorRGB(0.05, 0.25, 0.65)
        pdf.setLineWidth(0.8)
        physical_x, physical_y = point(placement.rect.x, placement.rect.y)
        physical_right, physical_top = point(placement.rect.right, placement.rect.top)
        pdf.rect(physical_x, physical_y, physical_right - physical_x, physical_top - physical_y)
        if symbols and spec.id in symbols:
            draw_equipment_symbol_pdf(pdf, point, symbols[spec.id], spec, placement)
        pdf.setFillColorRGB(0.05, 0.25, 0.65)
        if leader is not None:
            pdf.setLineWidth(.4)
            pdf.line(*leader)
            strip = _pdf_leader_box(leader)
            annotations.append(strip)
        pdf.setFont("Helvetica-Bold", font_size)
        pdf.drawCentredString(
            label_x,
            label_y,
            spec.id,
        )
        annotations.append(label_box)
        pdf.setFillColorRGB(0.0, 0.0, 0.0)
    return annotations


def _draw_flows_pdf(
    pdf,
    point,
    flows: FlowRoutingResult,
    flow_types: Mapping[str, str],
    *,
    show_labels: bool = True,
    boundary: tuple[float, float] = (0.0, 0.0),
    reserved: Sequence[tuple[float, float, float, float]] = (),
) -> None:
    pdf.setStrokeColorRGB(0.9, 0.45, 0.05)
    pdf.setLineWidth(1.0)
    pdf.setDash(5, 3)
    pdf.setFont("Helvetica", FLOW_LABEL_POINTS)
    drawn = [route for route in flows.routes if len(route.points) >= 2]
    for route in drawn:
        path = pdf.beginPath()
        first_x, first_y = point(*route.points[0])
        path.moveTo(first_x, first_y)
        for x, y in route.points[1:]:
            path.lineTo(*point(x, y))
        pdf.drawPath(path, stroke=1, fill=0)
    pdf.setDash()
    if not show_labels or not drawn:
        return
    texts = [
        f"FLOW {route.flow_id} [{flow_types.get(route.flow_id, 'unspecified')}] / {route.minimum_clear_width_mm:.0f} mm"
        for route in drawn
    ]
    # Sheet points, so the boxes are measured where the collision actually
    # happens rather than estimated in millimetres.
    anchors = []
    for index, route in enumerate(drawn):
        label_x, label_y = point(*flow_label_anchor(route.points, index, boundary))
        anchors.append((label_x + 2, label_y + 2))
    positions = place_annotation_labels(
        anchors,
        [(pdf.stringWidth(text, "Helvetica", FLOW_LABEL_POINTS), FLOW_LABEL_POINTS) for text in texts],
        step=FLOW_LABEL_POINTS * 1.6,
        reserved=reserved,
    )
    for text, (label_x, label_y) in zip(texts, positions):
        pdf.drawString(label_x, label_y, text)


FLOW_LABEL_POINTS = 5.0
"""Point size of the flow annotation on the generated sheet."""


def _flow_label_height(boundary: tuple[float, float]) -> float:
    """Model-space height of a flow annotation in the DXF."""

    return max(90.0, 0.008 * min(boundary)) if min(boundary) else 90.0


def _text_extent(text: str, height: float) -> float:
    """Conservative width estimate for a single-line label of that height."""

    return max(1, len(text)) * height * 0.62


def _room_label_boxes(
    spec: LayoutIR, result: LayoutResult
) -> list[tuple[float, float, float, float]]:
    """Boxes already taken by the centred room annotations, in model units."""

    boxes = []
    for room in spec.rooms:
        rect = result.placements[room.id]
        label = f"{room.id} ({rect.area_m2:.2f} m2)"
        height = _room_label_height(label, rect)
        width = _text_extent(label, height)
        center_x, center_y = rect.center
        boxes.append(
            (
                center_x - width / 2,
                center_y - height * 0.75,
                center_x + width / 2,
                center_y + height * 0.75,
            )
        )
        # The dimension line under each room is annotation too, and a flow label
        # landing on it is just as unreadable as one landing on the room name.
        dimension = f"{rect.width:.0f} x {rect.height:.0f} mm"
        dimension_width = _text_extent(dimension, 75.0)
        boxes.append(
            (
                center_x - dimension_width / 2,
                rect.y + 180 - 56.0,
                center_x + dimension_width / 2,
                rect.y + 180 + 56.0,
            )
        )
    return boxes


def flow_label_anchor(
    points: Iterable[tuple[float, float]],
    index: int,
    boundary: tuple[float, float],
) -> tuple[float, float]:
    """Offset a route label off its own polyline, proportionally to the drawing.

    The offset used to be a fixed 260 mm. On a facility-sized boundary fitted to
    a sheet that is a fraction of a point, so routes sharing a corridor printed
    their labels on top of each other. Scaling the offset to the drawn extent
    keeps the separation visible at whatever scale the sheet is produced.
    """

    points = tuple(points)
    if not points:
        return (0.0, 0.0)
    middle_index = len(points) // 2
    anchor = points[middle_index]
    previous = points[max(0, middle_index - 1)]
    following = points[min(len(points) - 1, middle_index + 1)]
    dx = float(following[0]) - float(previous[0])
    dy = float(following[1]) - float(previous[1])
    length = (dx * dx + dy * dy) ** 0.5 or 1.0
    side = 1.0 if index % 2 == 0 else -1.0
    separation = max(300.0, 0.025 * min(boundary))
    offset = separation * (1.0 + (index % 3) * 0.6)
    return (
        float(anchor[0]) - (dy / length) * offset * side,
        float(anchor[1]) + (dx / length) * offset * side,
    )


def _annotation_box(
    position: tuple[float, float], size: tuple[float, float]
) -> tuple[float, float, float, float]:
    """Box of a left-baseline label, with a little room under the baseline."""

    width, height = size
    return (position[0], position[1] - height * 0.25, position[0] + width, position[1] + height)


def _boxes_overlap(
    first: tuple[float, float, float, float], second: tuple[float, float, float, float]
) -> bool:
    return first[0] < second[2] and second[0] < first[2] and first[1] < second[3] and second[1] < first[3]


def place_annotation_labels(
    anchors: Sequence[tuple[float, float]],
    sizes: Sequence[tuple[float, float]],
    *,
    step: float,
    reserved: Sequence[tuple[float, float, float, float]] = (),
    max_shifts: int = 12,
) -> list[tuple[float, float]]:
    """Nudge labels off each other, deterministically and in drawing order.

    This is not a general label-placement solver. It only walks a label along
    the vertical axis until its box is clear, which is enough to separate the
    annotations of routes that share one corridor. A label that cannot be freed
    within ``max_shifts`` stays at its anchor so the drawing never silently
    loses an annotation.
    """

    placed = [tuple(box) for box in reserved]
    positions: list[tuple[float, float]] = []
    for anchor, size in zip(anchors, sizes):
        chosen = tuple(anchor)
        for attempt in range(max_shifts + 1):
            shift = 0.0 if attempt == 0 else step * ((attempt + 1) // 2) * (1.0 if attempt % 2 else -1.0)
            candidate = (float(anchor[0]), float(anchor[1]) + shift)
            if not any(_boxes_overlap(_annotation_box(candidate, size), other) for other in placed):
                chosen = candidate
                break
        placed.append(_annotation_box(chosen, size))
        positions.append(chosen)
    return positions


def _draw_structural_grid_pdf(pdf, point, spec: LayoutIR, grid: StructuralGridSpec) -> None:
    x_labels = grid.labels_x or tuple(str(index + 1) for index in range(len(grid.axes_x_mm)))
    y_labels = grid.labels_y or tuple(str(index + 1) for index in range(len(grid.axes_y_mm)))
    pdf.setStrokeColorRGB(0.65, 0.65, 0.65)
    pdf.setFillColorRGB(0.35, 0.35, 0.35)
    pdf.setLineWidth(0.35)
    pdf.setDash(2, 2)
    pdf.setFont("Helvetica", 6)
    for axis, label in zip(grid.axes_x_mm, x_labels):
        pdf.line(*point(axis, 0), *point(axis, spec.boundary.height_mm))
        label_x, label_y = point(axis, spec.boundary.height_mm)
        pdf.drawCentredString(label_x, label_y + 3, label)
    for axis, label in zip(grid.axes_y_mm, y_labels):
        pdf.line(*point(0, axis), *point(spec.boundary.width_mm, axis))
        label_x, label_y = point(0, axis)
        pdf.drawRightString(label_x - 3, label_y, label)
    pdf.setDash()
    pdf.setFillColorRGB(0.0, 0.0, 0.0)


def _equipment_block_name(equipment_id: str) -> str:
    safe_id = re.sub(r"[^A-Za-z0-9_]+", "_", equipment_id).strip("_") or "EQUIPMENT"
    return f"EQUIP_{safe_id}"


def _equipment_label_height(rect: Rect) -> float:
    return max(60.0, min(180.0, min(rect.width, rect.height) / 5.0))


def _add_rect_polyline(
    modelspace,
    rect: Rect,
    layer: str,
    lineweight: int | None = None,
    linetype: str | None = None,
) -> None:
    attribs = {"layer": layer}
    if lineweight is not None:
        attribs["lineweight"] = lineweight
    if linetype is not None:
        attribs["linetype"] = linetype
    modelspace.add_lwpolyline(
        [(rect.x, rect.y), (rect.right, rect.y), (rect.right, rect.top), (rect.x, rect.top)],
        close=True,
        dxfattribs=attribs,
    )


def _room_label_height(label: str, rect: Rect) -> float:
    """Keep a centered room label inside the clear room width.

    DXF text width is font-dependent, so use a conservative average glyph-width
    estimate.  This prevents long labels from colliding in narrow rooms while
    preserving readable text in larger rooms.
    """

    natural_height = min(rect.width, rect.height) / 10
    available_width = max(0.0, rect.width - 400)
    fitted_height = available_width / (max(1, len(label)) * 0.78)
    return max(80.0, min(natural_height, fitted_height))

"""DXF and vector-PDF exporters for a solver result."""

from __future__ import annotations

import re
from collections.abc import Iterable
from pathlib import Path

from .building import BuildingIR, EquipmentSpec
from .equipment import EquipmentLayoutResult, clearance_rect
from .models import LayoutIR, LayoutResult, Rect
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
)


def export_dxf(
    path: str | Path,
    spec: LayoutIR,
    result: LayoutResult,
    equipment: EquipmentLayoutResult | None = None,
    equipment_specs: Iterable[EquipmentSpec] | None = None,
) -> None:
    import ezdxf
    from ezdxf.enums import TextEntityAlignment

    document = ezdxf.new("R2018")
    document.header["$INSUNITS"] = 4
    for name in LAYERS:
        if name not in document.layers:
            document.layers.add(name)
    for name, color in (
        ("A-WALL", 7),
        ("A-ROOM", 8),
        ("A-DOOR", 3),
        ("A-DIMS", 2),
        ("A-TEXT", 7),
        ("A-TITLE", 4),
        ("A-EQUIP", 1),
        ("A-CLEARANCE", 6),
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
        label = f"{room.id} ({rect.area_m2:.2f} m2)"
        text = modelspace.add_text(
            label,
            dxfattribs={"height": _room_label_height(label, rect), "layer": "A-TEXT"},
        )
        text.set_placement(rect.center, align=TextEntityAlignment.MIDDLE_CENTER)

    modelspace.add_line((0, -500), (boundary.width_mm, -500), dxfattribs={"layer": "A-DIMS"})
    modelspace.add_text(f"{boundary.width_mm:.0f} mm", dxfattribs={"height": 180, "layer": "A-DIMS"}).set_placement(
        (boundary.width_mm / 2, -800), align=TextEntityAlignment.MIDDLE_CENTER
    )
    modelspace.add_line((-500, 0), (-500, boundary.height_mm), dxfattribs={"layer": "A-DIMS"})
    modelspace.add_text(f"{boundary.height_mm:.0f} mm", dxfattribs={"height": 180, "layer": "A-DIMS"}).set_placement(
        (-800, boundary.height_mm / 2), align=TextEntityAlignment.MIDDLE_CENTER
    )

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
        _draw_equipment_dxf(document, modelspace, equipment, equipment_specs or ())

    title_x = boundary.width_mm - 3_000
    title_y = boundary.height_mm + 1_000
    modelspace.add_text(spec.project_name, dxfattribs={"height": 250, "layer": "A-TITLE"}).set_placement((title_x, title_y))
    modelspace.add_text(
        f"Variant {result.variant} | LayoutIR {spec.version}",
        dxfattribs={"height": 150, "layer": "A-TITLE"},
    ).set_placement((title_x, title_y - 400))
    document.saveas(path)


def export_building_dxf(
    path: str | Path,
    building: BuildingIR,
    result: LayoutResult,
    equipment: EquipmentLayoutResult,
) -> None:
    """Export a building result with equipment blocks and service envelopes."""

    export_dxf(path, building.layout, result, equipment, building.equipment)


def export_pdf(
    path: str | Path,
    spec: LayoutIR,
    result: LayoutResult,
    report: ValidationReport | None = None,
    equipment: EquipmentLayoutResult | None = None,
    equipment_specs: Iterable[EquipmentSpec] | None = None,
) -> None:
    from reportlab.lib.pagesizes import A3, landscape
    from reportlab.pdfgen import canvas

    page_width, page_height = landscape(A3)
    margin = 45
    title_height = 45
    scale = min(
        (page_width - 2 * margin) / spec.boundary.width_mm,
        (page_height - 2 * margin - title_height) / spec.boundary.height_mm,
    )
    origin_x = margin
    origin_y = margin + title_height
    pdf = canvas.Canvas(str(path), pagesize=(page_width, page_height))
    pdf.setTitle(f"{spec.project_name} — variant {result.variant}")

    def point(x: float, y: float) -> tuple[float, float]:
        return origin_x + x * scale, origin_y + y * scale

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

    for room in spec.rooms:
        rect = result.placements[room.id]
        x, y = point(rect.x, rect.y)
        pdf.setFont("Helvetica", max(5, min(11, rect.width * scale / 16)))
        pdf.drawCentredString(x + rect.width * scale / 2, y + rect.height * scale / 2, f"{room.id} ({rect.area_m2:.1f} m2)")

    pdf.setLineWidth(0.8)
    for opening in wall_plan.windows:
        if opening.orientation == "vertical":
            pdf.line(*point(opening.fixed, opening.start), *point(opening.fixed, opening.end))
        else:
            pdf.line(*point(opening.start, opening.fixed), *point(opening.end, opening.fixed))

    if equipment is not None:
        _draw_equipment_pdf(pdf, point, equipment, equipment_specs or ())

    pdf.setStrokeColorRGB(0.2, 0.2, 0.2)
    pdf.setFont("Helvetica-Bold", 12)
    pdf.drawString(margin, page_height - margin + 5, spec.project_name)
    pdf.setFont("Helvetica", 8)
    status = "VALID" if report is None or report.ok else f"INVALID ({len(report.issues)} issues)"
    pdf.drawRightString(page_width - margin, page_height - margin + 5, f"Variant {result.variant} | {status}")
    pdf.setFont("Helvetica", 7)
    pdf.drawString(margin, 20, "Design aid — not a permit or code-compliance verdict")
    pdf.save()


def export_building_pdf(
    path: str | Path,
    building: BuildingIR,
    result: LayoutResult,
    equipment: EquipmentLayoutResult,
    report: ValidationReport | None = None,
) -> None:
    """Export a building result with vector equipment and clearance overlays."""

    export_pdf(path, building.layout, result, report, equipment, building.equipment)


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
) -> tuple[Path, Path]:
    """Export the complete drawing projection for one BuildingIR variant."""

    output = Path(directory)
    output.mkdir(parents=True, exist_ok=True)
    stem = f"building_{result.variant:02d}"
    dxf_path = output / f"{stem}.dxf"
    pdf_path = output / f"{stem}.pdf"
    export_building_dxf(dxf_path, building, result, equipment)
    export_building_pdf(pdf_path, building, result, equipment, report)
    return dxf_path, pdf_path


def _draw_equipment_dxf(document, modelspace, equipment: EquipmentLayoutResult, equipment_specs: Iterable[EquipmentSpec]) -> None:
    specs = {item.id: item for item in equipment_specs}
    for placement in equipment.placements:
        spec = specs.get(placement.equipment_id)
        if spec is None:
            raise ValueError(f"Equipment placement references unknown equipment {placement.equipment_id}")
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
            block.add_line(
                (0, 0),
                (placement.rect.width, placement.rect.height),
                dxfattribs={"layer": "A-EQUIP"},
            )
            block.add_line(
                (0, placement.rect.height),
                (placement.rect.width, 0),
                dxfattribs={"layer": "A-EQUIP"},
            )
            block.add_text(
                spec.id,
                dxfattribs={"height": _equipment_label_height(placement.rect), "layer": "A-EQUIP"},
            ).set_placement(placement.rect.center)
            block.add_text(
                spec.type,
                dxfattribs={"height": max(45.0, _equipment_label_height(placement.rect) * 0.7), "layer": "A-EQUIP"},
            ).set_placement((placement.rect.center[0], placement.rect.center[1] - _equipment_label_height(placement.rect)))
        modelspace.add_blockref(block_name, (placement.rect.x, placement.rect.y), dxfattribs={"layer": "A-EQUIP"})


def _draw_equipment_pdf(pdf, point, equipment: EquipmentLayoutResult, equipment_specs: Iterable[EquipmentSpec]) -> None:
    specs = {item.id: item for item in equipment_specs}
    for placement in equipment.placements:
        spec = specs.get(placement.equipment_id)
        if spec is None:
            raise ValueError(f"Equipment placement references unknown equipment {placement.equipment_id}")
        clearance = clearance_rect(spec, placement.rect, placement.rotated)
        pdf.setStrokeColorRGB(0.35, 0.35, 0.75)
        pdf.setLineWidth(0.45)
        pdf.setDash(3, 2)
        x, y = point(clearance.x, clearance.y)
        right_x, top_y = point(clearance.right, clearance.top)
        pdf.rect(x, y, right_x - x, top_y - y)
        pdf.setDash()

        pdf.setStrokeColorRGB(0.05, 0.25, 0.65)
        pdf.setLineWidth(0.8)
        physical_x, physical_y = point(placement.rect.x, placement.rect.y)
        physical_right, physical_top = point(placement.rect.right, placement.rect.top)
        pdf.rect(physical_x, physical_y, physical_right - physical_x, physical_top - physical_y)
        pdf.setFillColorRGB(0.05, 0.25, 0.65)
        font_size = max(3.5, min(7.0, placement.rect.height * (top_y - y) / max(clearance.height, 1.0) / 3.0))
        pdf.setFont("Helvetica-Bold", font_size)
        pdf.drawCentredString(
            (physical_x + physical_right) / 2,
            (physical_y + physical_top) / 2,
            spec.id,
        )
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

"""DXF and vector-PDF exporters for a solver result."""

from __future__ import annotations

from pathlib import Path

from .models import LayoutIR, LayoutResult, Rect
from .validation import ValidationReport


LAYERS = ("A-WALL", "A-ROOM", "A-DOOR", "A-WINDOW", "A-DIMS", "A-HATCH", "A-TEXT", "A-TITLE")


def export_dxf(path: str | Path, spec: LayoutIR, result: LayoutResult) -> None:
    import ezdxf
    from ezdxf.enums import TextEntityAlignment

    document = ezdxf.new("R2018")
    document.header["$INSUNITS"] = 4
    for name in LAYERS:
        if name not in document.layers:
            document.layers.add(name)
    for name, color in (("A-WALL", 7), ("A-ROOM", 8), ("A-DOOR", 3), ("A-DIMS", 2), ("A-TEXT", 7), ("A-TITLE", 4)):
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

    for room in spec.rooms:
        rect = result.placements[room.id]
        _add_rect_polyline(modelspace, rect, "A-WALL", lineweight=int(spec.wall_thickness_mm * 10))
        text = modelspace.add_text(
            f"{room.id} ({rect.area_m2:.2f} m2)",
            dxfattribs={"height": max(180, min(rect.width, rect.height) / 10), "layer": "A-TEXT"},
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

    if "DOOR_900" not in document.blocks:
        block = document.blocks.new("DOOR_900")
        block.add_line((0, 0), (spec.door_width_mm, 0))
        block.add_arc((0, 0), spec.door_width_mm, 0, 90)
    for room_a, room_b in spec.relation_pairs("required_adjacency"):
        shared = _shared_segment(result.placements[room_a], result.placements[room_b])
        if shared is None:
            continue
        (x, y), vertical_edge = shared
        insert = modelspace.add_blockref("DOOR_900", (x, y), dxfattribs={"layer": "A-DOOR"})
        if vertical_edge:
            insert.dxf.rotation = 90

    title_x = boundary.width_mm - 3_000
    title_y = boundary.height_mm + 1_000
    modelspace.add_text(spec.project_name, dxfattribs={"height": 250, "layer": "A-TITLE"}).set_placement((title_x, title_y))
    modelspace.add_text(
        f"Variant {result.variant} | LayoutIR {spec.version}",
        dxfattribs={"height": 150, "layer": "A-TITLE"},
    ).set_placement((title_x, title_y - 400))
    document.saveas(path)


def export_pdf(path: str | Path, spec: LayoutIR, result: LayoutResult, report: ValidationReport | None = None) -> None:
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

    pdf.setLineWidth(max(0.6, spec.wall_thickness_mm * scale / 5))
    pdf.rect(origin_x, origin_y, spec.boundary.width_mm * scale, spec.boundary.height_mm * scale)
    pdf.setLineWidth(0.6)
    for cutout in spec.boundary.cutouts:
        x, y = point(cutout.x, cutout.y)
        pdf.setDash(3, 2)
        pdf.rect(x, y, cutout.width * scale, cutout.height * scale)
        pdf.setDash()

    for room in spec.rooms:
        rect = result.placements[room.id]
        x, y = point(rect.x, rect.y)
        pdf.setLineWidth(max(0.7, spec.wall_thickness_mm * scale / 5))
        pdf.rect(x, y, rect.width * scale, rect.height * scale)
        pdf.setLineWidth(0.5)
        pdf.setFont("Helvetica", max(5, min(11, rect.width * scale / 16)))
        pdf.drawCentredString(x + rect.width * scale / 2, y + rect.height * scale / 2, f"{room.id} ({rect.area_m2:.1f} m2)")

    pdf.setStrokeColorRGB(0.2, 0.2, 0.2)
    pdf.setFont("Helvetica-Bold", 12)
    pdf.drawString(margin, page_height - margin + 5, spec.project_name)
    pdf.setFont("Helvetica", 8)
    status = "VALID" if report is None or report.ok else f"INVALID ({len(report.issues)} issues)"
    pdf.drawRightString(page_width - margin, page_height - margin + 5, f"Variant {result.variant} | {status}")
    pdf.setFont("Helvetica", 7)
    pdf.drawString(margin, 20, "Design aid — not a permit or code-compliance verdict")
    pdf.save()


def export_bundle(directory: str | Path, spec: LayoutIR, result: LayoutResult, report: ValidationReport | None = None) -> tuple[Path, Path]:
    output = Path(directory)
    output.mkdir(parents=True, exist_ok=True)
    stem = f"layout_{result.variant:02d}"
    dxf_path = output / f"{stem}.dxf"
    pdf_path = output / f"{stem}.pdf"
    export_dxf(dxf_path, spec, result)
    export_pdf(pdf_path, spec, result, report)
    return dxf_path, pdf_path


def _add_rect_polyline(modelspace, rect: Rect, layer: str, lineweight: int | None = None) -> None:
    attribs = {"layer": layer}
    if lineweight is not None:
        attribs["lineweight"] = lineweight
    modelspace.add_lwpolyline(
        [(rect.x, rect.y), (rect.right, rect.y), (rect.right, rect.top), (rect.x, rect.top)],
        close=True,
        dxfattribs=attribs,
    )


def _shared_segment(a: Rect, b: Rect):
    if abs(a.right - b.x) < 1e-6:
        low, high = max(a.y, b.y), min(a.top, b.top)
        return ((a.right, (low + high) / 2), True)
    if abs(b.right - a.x) < 1e-6:
        low, high = max(a.y, b.y), min(a.top, b.top)
        return ((a.x, (low + high) / 2), True)
    if abs(a.top - b.y) < 1e-6:
        low, high = max(a.x, b.x), min(a.right, b.right)
        return (((low + high) / 2, a.top), False)
    if abs(b.top - a.y) < 1e-6:
        low, high = max(a.x, b.x), min(a.right, b.right)
        return (((low + high) / 2, a.y), False)
    return None

"""Compare the variants of a generated facility bundle.

A bundle holds several accepted variants of one program. They all pass the same
gates, so the question a planner or a client asks is not "is it valid" but "which
one, and why". This module answers it from the saved results only; nothing is
re-solved. Every number traces back to a field of ``building_NN.json``:

* gates — the stored facility checks and the open coordination issues;
* area — how far each room landed from its programmed target area;
* flows — derived route lengths per flow type, and how many doors they cross;
* compactness — how much of the boundary the plan's footprint uses.

The ranking key is deliberately simple and printed with the result: failed
gates, then open issues, then total route length, then area deviation. It is a
design aid for choosing between valid options, not a quality verdict.
"""

from __future__ import annotations

import csv
import json
import math
from collections import defaultdict
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

DISCLAIMER = (
    "Design aid for comparing variants that passed the same deterministic gates. "
    "It is not a permit, a GMP or code-compliance verdict, or a substitute for the "
    "process, cleanroom/HVAC and architectural reviews."
)


@dataclass(frozen=True)
class VariantMetrics:
    variant: int
    source: str
    facility_ok: bool
    failed_checks: tuple[str, ...]
    open_issues: int
    rooms: int
    area_deviation_pct: float
    max_room_deviation_pct: float
    max_room_deviation_room: str
    route_length_m: float
    route_length_by_type_m: dict[str, float]
    longest_route: str
    longest_route_m: float
    door_crossings: int
    footprint_ratio: float
    door_approach_exceptions: tuple[str, ...]
    rank: int = 0
    reasons: tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        return {
            "variant": self.variant,
            "source": self.source,
            "rank": self.rank,
            "facility_ok": self.facility_ok,
            "failed_checks": list(self.failed_checks),
            "open_issues": self.open_issues,
            "rooms": self.rooms,
            "area_deviation_pct": round(self.area_deviation_pct, 2),
            "max_room_deviation_pct": round(self.max_room_deviation_pct, 2),
            "max_room_deviation_room": self.max_room_deviation_room,
            "route_length_m": round(self.route_length_m, 1),
            "route_length_by_type_m": {key: round(value, 1) for key, value in sorted(self.route_length_by_type_m.items())},
            "longest_route": self.longest_route,
            "longest_route_m": round(self.longest_route_m, 1),
            "door_crossings": self.door_crossings,
            "footprint_ratio": round(self.footprint_ratio, 3),
            "door_approach_exceptions": list(self.door_approach_exceptions),
            "reasons": list(self.reasons),
        }


def variant_metrics(path: str | Path) -> VariantMetrics:
    """Measure one saved variant (``building_NN.json``)."""

    path = Path(path)
    data = json.loads(path.read_text(encoding="utf-8"))
    spec = data["spec"]
    layout_spec = spec.get("layout", spec)
    placed = data["layout"]["rooms"]

    deviation_total = 0.0
    target_total = 0.0
    worst_room, worst_pct = "", 0.0
    for room in layout_spec["rooms"]:
        rect = placed.get(room["id"])
        if rect is None:
            continue
        target = float(room["target_area"])
        area = float(rect.get("area_m2", rect["width"] * rect["height"] / 1_000_000))
        deviation_total += abs(area - target)
        target_total += target
        pct = abs(area - target) / target * 100 if target else 0.0
        if pct > worst_pct:
            worst_room, worst_pct = room["id"], pct

    flow_types = {flow["id"]: flow.get("type", "flow") for flow in spec.get("flows", ())}
    by_type: dict[str, float] = defaultdict(float)
    total_length = 0.0
    longest, longest_length = "", 0.0
    crossings = 0
    for route in data.get("flow_routes", {}).get("routes", ()):
        points = [(float(point["x"]), float(point["y"])) for point in route["points"]]
        length = sum(math.dist(a, b) for a, b in zip(points, points[1:])) / 1000
        total_length += length
        by_type[flow_types.get(route["flow_id"], "flow")] += length
        crossings += max(0, len(route.get("room_path", ())) - 1)
        if length > longest_length:
            longest = f"{route['flow_id']} ({route['from_id']} → {route['to_id']})"
            longest_length = length

    boundary = layout_spec["boundary"]
    rects = list(placed.values())
    footprint = 0.0
    if rects:
        width = max(r["x"] + r["width"] for r in rects) - min(r["x"] for r in rects)
        height = max(r["y"] + r["height"] for r in rects) - min(r["y"] for r in rects)
        footprint = width * height / (float(boundary["width"]) * float(boundary["height"]))

    facility = data.get("facility_validation", {})
    failed = tuple(check["id"] for check in facility.get("checks", ()) if check.get("status") == "FAIL")
    open_issues = sum(
        1 for issue in facility.get("issues", ()) if str(issue.get("status", "OPEN")).upper() not in {"RESOLVED", "CLOSED"}
    )
    return VariantMetrics(
        variant=int(data["layout"].get("variant", 0)),
        source=path.name,
        facility_ok=bool(facility.get("ok", not failed)),
        failed_checks=failed,
        open_issues=open_issues,
        rooms=len(placed),
        area_deviation_pct=deviation_total / target_total * 100 if target_total else 0.0,
        max_room_deviation_pct=worst_pct,
        max_room_deviation_room=worst_room,
        route_length_m=total_length,
        route_length_by_type_m=dict(by_type),
        longest_route=longest,
        longest_route_m=longest_length,
        door_crossings=crossings,
        footprint_ratio=footprint,
        door_approach_exceptions=tuple(data.get("generation", {}).get("door_approach_exceptions", ())),
    )


RANKING_KEY = "failed gates, then open issues, then total route length, then area deviation"


def compare_bundle(directory: str | Path) -> list[VariantMetrics]:
    """Measure and rank every ``building_NN.json`` in a bundle directory."""

    paths = sorted(Path(directory).glob("building_[0-9][0-9].json"))
    if not paths:
        raise FileNotFoundError(f"No building_NN.json variants in {directory}")
    measured = [variant_metrics(path) for path in paths]
    ordered = sorted(
        measured,
        key=lambda m: (len(m.failed_checks), m.open_issues, round(m.route_length_m, 1), round(m.area_deviation_pct, 2), m.variant),
    )
    ranked: list[VariantMetrics] = []
    for rank, metrics in enumerate(ordered, start=1):
        ranked.append(_with_reasons(metrics, rank, measured))
    return ranked


def _with_reasons(m: VariantMetrics, rank: int, everyone: list[VariantMetrics]) -> VariantMetrics:
    others = [other for other in everyone if other.variant != m.variant]
    reasons: list[str] = []
    if m.failed_checks:
        reasons.append(f"fails {', '.join(m.failed_checks)}")
    else:
        reasons.append("passes every facility gate")
    if m.open_issues:
        reasons.append(f"{m.open_issues} open coordination issue(s)")
    if others:
        best_other = min(other.route_length_m for other in others)
        if m.route_length_m < best_other - 0.05:
            reasons.append(f"shortest total route length: {m.route_length_m:.1f} m, {best_other - m.route_length_m:.1f} m less than the next variant")
        elif m.route_length_m > best_other + 0.05:
            reasons.append(f"total route length {m.route_length_m:.1f} m, {m.route_length_m - best_other:.1f} m more than the shortest")
        best_area = min(other.area_deviation_pct for other in others)
        if m.area_deviation_pct < best_area - 0.005:
            reasons.append(f"closest to the programmed areas: {m.area_deviation_pct:.1f} % total deviation")
    if m.door_approach_exceptions:
        reasons.append(f"door approaches not kept clear in: {', '.join(m.door_approach_exceptions)}")
    return replace(m, rank=rank, reasons=tuple(reasons))


def write_comparison(directory: str | Path, output: str | Path | None = None, *, pdf: bool = True) -> dict[str, Path]:
    """Write ``comparison.json``, ``comparison.csv`` and optionally ``comparison.pdf``."""

    directory = Path(directory)
    output = Path(output) if output else directory
    output.mkdir(parents=True, exist_ok=True)
    ranked = compare_bundle(directory)
    written: dict[str, Path] = {}

    payload = {
        "bundle": str(directory),
        "ranking_key": RANKING_KEY,
        "disclaimer": DISCLAIMER,
        "variants": [metrics.to_dict() for metrics in ranked],
    }
    json_path = output / "comparison.json"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    written["json"] = json_path

    csv_path = output / "comparison.csv"
    flow_types = sorted({key for metrics in ranked for key in metrics.route_length_by_type_m})
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            ["rank", "variant", "facility_ok", "failed_checks", "open_issues", "area_deviation_pct",
             "max_room_deviation_pct", "route_length_m", *[f"route_{kind}_m" for kind in flow_types],
             "door_crossings", "footprint_ratio"]
        )
        for m in ranked:
            row = m.to_dict()
            writer.writerow(
                [m.rank, m.variant, m.facility_ok, " ".join(m.failed_checks), m.open_issues, row["area_deviation_pct"],
                 row["max_room_deviation_pct"], row["route_length_m"],
                 *[row["route_length_by_type_m"].get(kind, 0.0) for kind in flow_types],
                 m.door_crossings, row["footprint_ratio"]]
            )
    written["csv"] = csv_path

    if pdf:
        pdf_path = output / "comparison.pdf"
        _write_pdf(pdf_path, directory, ranked)
        written["pdf"] = pdf_path
    return written


def _write_pdf(path: Path, directory: Path, ranked: list[VariantMetrics]) -> None:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.pdfgen import canvas

    page_width, page_height = landscape(A4)
    sheet = canvas.Canvas(str(path), pagesize=(page_width, page_height))
    margin = 36
    first = json.loads((directory / ranked[0].source).read_text(encoding="utf-8"))
    project = first["spec"].get("project_name") or first["spec"].get("layout", {}).get("project_name", "Facility")

    sheet.setFont("Helvetica-Bold", 16)
    sheet.drawString(margin, page_height - margin - 6, f"Variant comparison — {project}")
    sheet.setFont("Helvetica", 9)
    sheet.drawString(margin, page_height - margin - 22, f"Ranking: {RANKING_KEY}.")

    best = ranked[0]
    sheet.setFont("Helvetica-Bold", 11)
    sheet.drawString(margin, page_height - margin - 44, f"Recommended: variant {best.variant}")
    sheet.setFont("Helvetica", 9)
    y = page_height - margin - 58
    for reason in best.reasons:
        sheet.drawString(margin + 10, y, f"• {reason}")
        y -= 12

    columns = [
        ("Rank", 34), ("Var.", 34), ("Gates", 60), ("Open issues", 64), ("Area dev. %", 64),
        ("Worst room dev. %", 150), ("Routes, m", 64), ("Longest route", 230), ("Doors", 40),
    ]
    y -= 10
    x = margin
    sheet.setFont("Helvetica-Bold", 8)
    for title, width in columns:
        sheet.drawString(x, y, title)
        x += width
    sheet.setFont("Helvetica", 8)
    for m in ranked:
        y -= 13
        values = [
            str(m.rank), str(m.variant), "PASS" if m.facility_ok else "FAIL", str(m.open_issues),
            f"{m.area_deviation_pct:.1f}", f"{m.max_room_deviation_pct:.1f} ({m.max_room_deviation_room})",
            f"{m.route_length_m:.1f}", f"{m.longest_route} {m.longest_route_m:.1f} m", str(m.door_crossings),
        ]
        x = margin
        for (title, width), value in zip(columns, values):
            sheet.drawString(x, y, value[: int(width / 4.2)])
            x += width

    # One thumbnail per variant: rooms and derived routes, same scale.
    thumbs_top = y - 24
    count = len(ranked)
    thumb_width = (page_width - 2 * margin - (count - 1) * 12) / max(count, 1)
    thumb_height = thumbs_top - margin - 30
    palette = {
        "people": colors.HexColor("#1f77b4"), "material": colors.HexColor("#d98c00"),
        "waste": colors.HexColor("#c0392b"), "dirty_material": colors.HexColor("#8e5a2b"),
        "finished_goods": colors.HexColor("#1e8449"), "service": colors.HexColor("#7d3c98"),
    }
    for index, m in enumerate(ranked):
        data = json.loads((directory / m.source).read_text(encoding="utf-8"))
        layout_spec = data["spec"].get("layout", data["spec"])
        bw, bh = float(layout_spec["boundary"]["width"]), float(layout_spec["boundary"]["height"])
        scale = min(thumb_width / bw, thumb_height / bh)
        ox = margin + index * (thumb_width + 12)
        # Hang the plans right under the table instead of at the page foot.
        oy = thumbs_top - bh * scale
        sheet.setStrokeColor(colors.grey)
        sheet.setDash(3, 2)
        sheet.rect(ox, oy, bw * scale, bh * scale)
        sheet.setDash()
        sheet.setStrokeColor(colors.black)
        sheet.setLineWidth(0.6)
        for room_id, rect in data["layout"]["rooms"].items():
            sheet.rect(ox + rect["x"] * scale, oy + rect["y"] * scale, rect["width"] * scale, rect["height"] * scale)
        types = {flow["id"]: flow.get("type", "flow") for flow in data["spec"].get("flows", ())}
        sheet.setLineWidth(1.2)
        for route in data.get("flow_routes", {}).get("routes", ()):
            sheet.setStrokeColor(palette.get(types.get(route["flow_id"], ""), colors.darkgrey))
            points = route["points"]
            for a, b in zip(points, points[1:]):
                sheet.line(ox + a["x"] * scale, oy + a["y"] * scale, ox + b["x"] * scale, oy + b["y"] * scale)
        sheet.setFillColor(colors.black)
        sheet.setFont("Helvetica-Bold", 9)
        sheet.drawString(ox, oy - 12, f"#{m.rank} · variant {m.variant} · {m.route_length_m:.1f} m of routes · {m.area_deviation_pct:.1f} % area deviation")

    legend_x = margin
    sheet.setFont("Helvetica", 8)
    for kind, colour in palette.items():
        sheet.setStrokeColor(colour)
        sheet.setLineWidth(2)
        sheet.line(legend_x, 32, legend_x + 16, 32)
        sheet.setFillColor(colors.black)
        sheet.drawString(legend_x + 20, 29, f"{kind.replace('_', ' ')} flow")
        legend_x += 110
    sheet.drawString(legend_x, 29, "Doors = door crossings along all routes")
    sheet.setFont("Helvetica-Oblique", 7)
    sheet.setFillColor(colors.grey)
    sheet.drawString(margin, 12, DISCLAIMER)
    sheet.showPage()
    sheet.save()

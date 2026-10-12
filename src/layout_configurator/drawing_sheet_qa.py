"""Independent artifact readback for optional thematic coordination sheets.

This checks the delivered PDF and its printed source identity. It does not
certify the depicted geometry, process clearance or evacuation compliance.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import math
from pathlib import Path
import re
from typing import Any

from pypdf import PdfReader
from pypdf.errors import PyPdfError

from .building import BuildingIR
from .drawing_revision_history import DrawingRevisionHistory
from .equipment import EquipmentLayoutResult
from .facility import FacilityDrawingProfile
from .flows import FlowRoutingResult
from .models import LayoutResult


# The delivered artifact contract, deliberately independent of the renderer.
_THEMES = {
    "zones": ("Z", "Functional zones"),
    "equipment": ("E", "Equipment and service envelopes"),
    "flows": ("F", "Process flow routes"),
    "egress": ("R", "Egress source and candidates"),
}
_FIELDS = {"kind", "sheet_id", "title", "revision", "page_number",
           "scale_denominator", "status", "source_variant", "candidate_route_count"}


@dataclass(frozen=True)
class DrawingSheetReadback:
    issues: tuple[dict[str, Any], ...]
    page_count: int = 0

    @property
    def ok(self) -> bool:
        return not self.issues

    def to_dict(self) -> dict[str, Any]:
        return {"scope": "PDF artifact identity and vector-content readback; no engineering certification",
                "page_count": self.page_count, "issues": list(self.issues)}


def validate_drawing_sheet_set(
    root: str | Path,
    sheet_set: Any,
    *,
    manifest_variant: Any,
    building: BuildingIR,
    result: LayoutResult,
    equipment: EquipmentLayoutResult,
    flows: FlowRoutingResult,
    drawing: FacilityDrawingProfile,
    revision_history: DrawingRevisionHistory | None = None,
) -> DrawingSheetReadback:
    """Read the optional manifest index and real PDF without modifying files."""

    issues: list[dict[str, Any]] = []

    def issue(code: str, message: str, **details: Any) -> None:
        issues.append({"code": code, "message": message, **details})

    if type(manifest_variant) is not int or manifest_variant != result.variant:
        issue("SHEET_VARIANT", "Manifest variant does not match the canonical result")
    if not isinstance(sheet_set, dict):
        issue("SHEET_INDEX", "Present sheet_set must be an object")
        return DrawingSheetReadback(tuple(issues))
    rows = sheet_set.get("pages")
    if not isinstance(rows, list) or len(rows) < 4:
        issue("SHEET_INDEX", "Page index must contain all four thematic plans")
        rows = []
    _validate_index(rows, building, result, equipment, flows, drawing, issue, history=revision_history)
    name = sheet_set.get("pdf")
    bundle_root = Path(root).resolve()
    if not isinstance(name, str) or not name or Path(name).is_absolute():
        issue("SHEET_FILE", "Sheet PDF must be a relative bundle filename")
        return DrawingSheetReadback(tuple(issues))
    try:
        path = (bundle_root / name).resolve()
    except (OSError, ValueError, RuntimeError) as exc:
        issue("SHEET_FILE", f"Sheet PDF reference cannot be resolved: {exc}")
        return DrawingSheetReadback(tuple(issues))
    if not path.is_relative_to(bundle_root) or path.suffix.lower() != ".pdf":
        issue("SHEET_FILE", "Sheet PDF reference is outside the bundle or has the wrong extension")
        return DrawingSheetReadback(tuple(issues))
    try:
        data = path.read_bytes()
    except OSError as exc:
        issue("SHEET_FILE", f"Sheet PDF cannot be read: {exc}")
        return DrawingSheetReadback(tuple(issues))
    expected_hash = sheet_set.get("sha256")
    if not isinstance(expected_hash, str) or not re.fullmatch(r"[0-9a-f]{64}", expected_hash) or hashlib.sha256(data).hexdigest() != expected_hash:
        issue("SHEET_HASH", "Delivered sheet PDF does not match its SHA256")
    page_count = 0
    history_body = []
    try:
        reader = PdfReader(path, strict=True)
        if reader.is_encrypted:
            issue("SHEET_PDF", "Encrypted sheets cannot be independently read back")
            return DrawingSheetReadback(tuple(issues))
        page_count = len(reader.pages)
        if page_count != len(rows):
            issue("SHEET_PAGE_COUNT", "PDF page count does not match its index", actual=page_count, indexed=len(rows))
        for number, page in enumerate(reader.pages, 1):
            if len(page.images):
                issue("SHEET_RASTER", "Vector sheet contains raster image content", page=number)
            stream = page.get_contents()
            operations = {operation for _, operation in stream.operations} if stream is not None else set()
            if not operations.intersection({b"l", b"re"}) or not operations.intersection({b"S", b"s", b"B", b"B*", b"b", b"b*"}):
                issue("SHEET_VECTOR", "Sheet has no readable stroked vector paths", page=number)
            width, height = float(page.mediabox.width), float(page.mediabox.height)
            if not math.isclose(width, 1190.55118, abs_tol=0.02) or not math.isclose(height, 841.88976, abs_tol=0.02) or page.rotation != 0 or page.user_unit != 1:
                issue("SHEET_PAGE_SIZE", "Sheet is not an unrotated A3 landscape page in points", page=number)
            if number <= len(rows) and isinstance(rows[number - 1], dict) and _FIELDS <= rows[number - 1].keys():
                fragments = _validate_printed_identity(page, rows[number - 1], page_count, building, drawing, issue, history=revision_history)
                if rows[number - 1]["kind"] == "revisions" and revision_history is not None:
                    _validate_history_page(fragments, height, revision_history, number, history_body, issue)
        if revision_history is not None:
            _validate_history_body(history_body, revision_history, issue)
    except (OSError, PyPdfError, ValueError, TypeError, KeyError, AttributeError, IndexError, OverflowError, RecursionError) as exc:
        issue("SHEET_PDF", f"Sheet PDF readback failed: {exc}")
    return DrawingSheetReadback(tuple(issues), page_count)


def _validate_index(rows, building, result, equipment, flows, drawing, issue, *, history=None):
    themes = {**_THEMES, **({"revisions": ("REV", "Supplied revision history")} if history is not None else {})}
    counts = {kind: 0 for kind in themes}
    ids = set()
    previous_theme = -1
    for number, row in enumerate(rows, 1):
        if not isinstance(row, dict) or not _FIELDS <= row.keys():
            issue("SHEET_INDEX", "Page index entry is missing required fields", page=number)
            continue
        kind = row["kind"]
        if not isinstance(kind, str) or kind not in themes:
            issue("SHEET_INDEX", "Unknown sheet theme", page=number)
            continue
        theme = tuple(themes).index(kind)
        if theme < previous_theme:
            issue("SHEET_INDEX", "Themes must be ordered zones, equipment, flows, egress", page=number)
        previous_theme = theme
        counts[kind] += 1
        suffix, title = themes[kind]
        expected_id = f"{drawing.sheet_id}-{suffix}" + (f"-{counts[kind]:02d}" if counts[kind] > 1 else "")
        sheet_id = row["sheet_id"]
        if not isinstance(sheet_id, str) or sheet_id != expected_id or sheet_id in ids:
            issue("SHEET_ID", "Sheet ID is duplicate or does not match its primary/continuation identity", page=number)
        if isinstance(sheet_id, str):
            ids.add(sheet_id)
        if row["title"] != title or row["revision"] != drawing.revision:
            issue("SHEET_IDENTITY", "Page title or revision differs from the source profile", page=number)
        if type(row["page_number"]) is not int or row["page_number"] != number or type(row["source_variant"]) is not int or row["source_variant"] != result.variant:
            issue("SHEET_IDENTITY", "Page number or variant differs from the canonical result", page=number)
        scale = row["scale_denominator"]
        if (kind != "revisions" and counts[kind] == 1 and (type(scale) is not int or scale <= 0)) or ((kind == "revisions" or counts[kind] > 1) and scale is not None):
            issue("SHEET_INDEX", "Primary plans need a positive scale; legend continuations have no plan scale", page=number)
        status = "UNKNOWN" if kind == "egress" else "SOURCE PROJECTION"
        if kind == "equipment" and len(equipment.placements) != len(building.equipment):
            status = "UNKNOWN"
        if kind == "flows" and any(not any(route.flow_id == flow.id for route in flows.routes) for flow in building.flows):
            status = "UNKNOWN"
        if row["status"] != status:
            issue("SHEET_STATUS", "Page status does not match available source evidence", page=number)
        candidates = row["candidate_route_count"]
        if type(candidates) is not int or not 0 <= candidates <= len(result.placements) or (kind != "egress" and candidates != 0) or (building.layout.external_entry is None and candidates != 0):
            issue("SHEET_INDEX", "Invalid candidate route count", page=number)
    if any(count == 0 for count in counts.values()):
        issue("SHEET_THEMES", "One or more required thematic plans are missing")


def _validate_printed_identity(page, row, page_count, building, drawing, issue, *, history=None):
    width, height = float(page.mediabox.width), float(page.mediabox.height)
    fragments = []

    def visit(text, cm, tm, font, size):
        if text.strip():
            x = tm[4] * cm[0] + tm[5] * cm[2] + cm[4]
            y = tm[4] * cm[1] + tm[5] * cm[3] + cm[5]
            fragments.append((x, y, text))

    page.extract_text(visitor_text=visit)

    def region(left, right, bottom, top):
        return " ".join(" ".join(text.split()) for x, y, text in fragments if left <= x <= right and bottom <= y <= top)

    expected = (
        ("project", region(0, width - 280, height - 81, height - 51), " ".join(building.layout.project_name.split())),
        ("sheet_id", region(width / 2, width, height - 61, height - 51), row["sheet_id"]),
        ("revision/page", region(width / 2, width, height - 79, height - 69), f"Revision {row['revision']} | Page {row['page_number']}/{page_count}"),
        ("theme title", region(0, width / 2, height - 106, height - 96), row["title"] + (" - legend continuation" if row["scale_denominator"] is None and row["sheet_id"] != f"{drawing.sheet_id}-REV" else "")),
        ("source variant", region(0, width / 2, 86, 96), f"{drawing.discipline} | Variant {row['source_variant']}"),
        ("source versions", region(0, width / 2, 70, 80), f"LayoutIR {building.layout.version} / BuildingIR {building.version} | Geometry: millimetres"),
        ("current revision", region(width / 2, width, 86, 96), f"{drawing.revision} | History: {drawing.sheet_id}-REV" if history is not None else f"{drawing.revision} | Current revision only"),
        ("drawing title", region(width / 2, width, 60, 80), " ".join(drawing.title.split())),
        ("status", region(width / 2, width, 37, 47), f"Status: {row['status']}"),
    )
    for field, actual, source in expected:
        if actual != source:
            issue("SHEET_PRINTED_IDENTITY", "Printed sheet identity differs from its canonical source", page=row["page_number"], field=field, expected=source, actual=actual)
    if row["scale_denominator"] is not None:
        scale_caption = f"1:{row['scale_denominator']} at A3, 100% print"
        if not any(text.strip() == scale_caption for x, y, text in fragments if 111 <= y <= 121):
            issue("SHEET_PRINTED_IDENTITY", "Printed plan scale differs from the index", page=row["page_number"], field="scale")
    return fragments


def _validate_history_page(fragments, height, history, number, body, issue):
    current = " ".join(text.strip() for x, y, text in fragments if height - 150 <= y <= height - 140)
    if current != f"Current {history.current_revision} | Previous {history.previous_revision}":
        issue("SHEET_PRINTED_HISTORY", "Printed current/predecessor relation differs from the supplied chain", page=number)
    attribution = " ".join(text.strip() for x, y, text in fragments if height - 170 <= y <= height - 160)
    if attribution != "Dates and authors are supplied attribution; no authenticated authorship or approval.":
        issue("SHEET_PRINTED_HISTORY", "Printed history does not identify supplied attribution", page=number)
    body.extend(text for x, y, text in fragments if 150 <= y <= height - 178)


def _validate_history_body(body, history, issue):
    expected = []
    for record in history.records:
        source = record.source_identity
        expected.extend((f"Revision {record.revision} | Previous {record.previous_revision or 'NONE'} | Date {record.date}",
                         f"Author (supplied): {record.author}", f"Description (supplied): {record.description}",
                         f"Source project: {source['project_name']}",
                         f"Source identity: Variant {source['variant']} | BuildingIR {source['building_version']} / LayoutIR {source['layout_version']}",
                         f"Source drawing revision: {source['revision']}", f"Source SHA256: {record.source_sha256}"))
    # Word wrapping changes whitespace only. Preserve every non-whitespace
    # character and record order, including exact full source hashes.
    if "".join("".join(body).split()) != "".join("".join(expected).split()):
        issue("SHEET_PRINTED_HISTORY", "Printed revision records differ from their verified source index")

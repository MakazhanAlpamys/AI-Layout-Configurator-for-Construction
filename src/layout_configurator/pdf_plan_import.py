"""Bounded rectangular vector candidates, calibrated and selected by a human."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
from itertools import islice
import json
import math
from pathlib import Path
from typing import Mapping

from jsonschema import Draft202012Validator
from pypdf import PdfReader
from pypdf.errors import PdfReadError
from shapely.geometry import Polygon

from .models import LayoutIR, Rect
from .plan_import import ImportedRoom, _canonical_json, _file_hash, _rectangle, _write_json, validate_fixed_rooms
from .resources import resource_path

_MAX_FILE_BYTES = 20 * 1024 * 1024
_MAX_CONTENT_BYTES = 8 * 1024 * 1024
_MAX_OPERATIONS = 20000
_MAX_CANDIDATES = 1000
_MAX_STACK = 64
_MAX_DIAGNOSTICS = 1000
_IDENTITY = (1, 0, 0, 1, 0, 0)
_STYLE_AND_TEXT = frozenset({
    b"w", b"J", b"j", b"M", b"d", b"ri", b"i", b"g", b"G", b"rg", b"RG", b"k", b"K",
    b"cs", b"CS", b"sc", b"SC", b"scn", b"SCN", b"BT", b"ET", b"Tc", b"Tw", b"Tz", b"TL",
    b"Tf", b"Tr", b"Ts", b"Td", b"TD", b"Tm", b"T*", b"Tj", b"TJ", b"'", b'"',
})


@dataclass(frozen=True)
class PdfRoomCandidate:
    id: str
    operator_index: int
    rect: Rect


@dataclass(frozen=True)
class PdfPlanDraft:
    source_path: str
    source_sha256: str
    page: int
    page_object: str
    content_sha256: str
    unit_scale_mm: float
    origin_pt: tuple[float, float]
    candidates: tuple[PdfRoomCandidate, ...]
    issues: tuple[str, ...] = ()
    ambiguities: tuple[str, ...] = ()
    assumptions: tuple[str, ...] = (
        "Explicit calibration: mm per unrotated default-user-space point; x right, y up; origin_pt maps to canonical (0,0).",
        "Candidates are not classified rooms; explicit selection and named review required. Text does not determine scale or identity.",
        "Only page-content closed rectangular paths; no OCR, forms, clipping, page rotation, bbox coercion or grid snapping.",
    )
    state: str = "DRAFT"

    def to_dict(self) -> dict:
        return {
            "format": "FLC-pdf-plan-draft", "version": "0.1", "state": self.state,
            "source": {"path": self.source_path, "sha256": self.source_sha256, "format": "PDF"},
            "page": self.page, "page_object": self.page_object, "content_sha256": self.content_sha256,
            "calibration": {"unit_scale_mm": self.unit_scale_mm, "origin_pt": list(self.origin_pt)},
            "candidates": [asdict(candidate) for candidate in self.candidates],
            "issues": list(self.issues), "ambiguities": list(self.ambiguities), "assumptions": list(self.assumptions),
        }


def import_pdf_plan(path: str | Path, *, page: int, unit_scale_mm: float, origin_pt: tuple[float, float]) -> PdfPlanDraft:
    """Extract one explicitly calibrated page without inferring room semantics."""
    if not isinstance(page, int) or isinstance(page, bool) or page < 1:
        raise ValueError("PDF page must be a positive one-based integer")
    if not _finite_number(unit_scale_mm) or unit_scale_mm <= 0:
        raise ValueError("PDF unit_scale_mm requires a finite positive number")
    if not isinstance(origin_pt, (tuple, list)) or len(origin_pt) != 2 or not all(_finite_number(value) for value in origin_pt):
        raise ValueError("PDF origin_pt requires two finite numeric page coordinates")
    source = Path(path).resolve()
    if source.suffix.lower() != ".pdf":
        raise ValueError("Vector PDF import requires a .pdf source")
    source_hash = _file_hash(source)
    draft = PdfPlanDraft(str(source), source_hash, page, "unavailable", hashlib.sha256(b"").hexdigest(),
                         unit_scale_mm, tuple(origin_pt), ())
    if source.stat().st_size > _MAX_FILE_BYTES:
        return _finish(draft, [], ["source_limit: PDF source exceeds 20 MiB"])
    try:
        reader = PdfReader(source, strict=True)
        if reader.is_encrypted:
            return _finish(draft, [], ["encrypted_source: decrypt before import"])
        if page > len(reader.pages):
            raise ValueError("PDF page is outside the source page range")
        selected = reader.pages[page - 1]
        reference = selected.indirect_reference
        page_object = f"{reference.idnum}:{reference.generation}" if reference else "direct"
        content = selected.get_contents()
        data = content.get_data() if content is not None else b""
        draft = PdfPlanDraft(str(source), source_hash, page, page_object, hashlib.sha256(data).hexdigest(),
                             unit_scale_mm, tuple(origin_pt), ())
        if selected.rotation != 0 or selected.user_unit != 1:
            return _finish(draft, [], ["unsupported_page: rotation or UserUnit differs from unrotated default point coordinates"])
        if selected.get("/Annots"):
            return _finish(draft, [], ["unsupported_annotations: page annotations require separate review"])
        if len(data) > _MAX_CONTENT_BYTES:
            return _finish(draft, [], ["content_limit: decoded page content exceeds 8 MiB"])
        operations = content.operations if content is not None else []
        if len(operations) > _MAX_OPERATIONS:
            return _finish(draft, [], ["operation_limit: page exceeds 20000 operators"])
        try:
            candidates, issues = _extract(operations, draft, selected.cropbox)
        except (ValueError, TypeError, OverflowError) as exc:
            return _finish(draft, [], [f"malformed_graphics: {exc}"])
        return _finish(draft, candidates, issues)
    except (PdfReadError, OSError, KeyError, IndexError, TypeError, OverflowError) as exc:
        return _finish(draft, [], [f"malformed_source: {exc}"])


def write_pdf_plan_draft(path: str | Path, draft: PdfPlanDraft) -> None:
    _validate(draft.to_dict())
    _write_json(Path(path), draft.to_dict())


def read_pdf_plan_draft(path: str | Path) -> PdfPlanDraft:
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"), object_pairs_hook=_unique_keys)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"Malformed or unavailable PDF draft: {exc}") from exc
    _validate(raw)
    source, calibration = raw["source"], raw["calibration"]
    candidates = tuple(PdfRoomCandidate(item["id"], item["operator_index"], Rect(**item["rect"])) for item in raw["candidates"])
    return PdfPlanDraft(source["path"], source["sha256"], raw["page"], raw["page_object"], raw["content_sha256"],
                        calibration["unit_scale_mm"], tuple(calibration["origin_pt"]), candidates,
                        tuple(raw["issues"]), tuple(raw["ambiguities"]), tuple(raw["assumptions"]))


def load_pdf_imported_constraints(path: str | Path, layout_spec: LayoutIR, *,
                                  candidate_mapping: Mapping[str, str] | None = None,
                                  confirmed: bool = False, reviewer: str | None = None) -> dict[str, Rect]:
    """Reextract original candidates and validate explicit selection as fixed rooms."""
    if confirmed is not True:
        raise ValueError("PDF geometry requires explicit human confirmation")
    if not isinstance(reviewer, str) or not reviewer.strip():
        raise ValueError("PDF geometry requires a named reviewer")
    if not isinstance(candidate_mapping, Mapping) or not candidate_mapping:
        raise ValueError("PDF candidate selection requires an explicit candidate_mapping")
    if not all(isinstance(key, str) and isinstance(value, str) and key.strip() and value.strip()
               for key, value in candidate_mapping.items()):
        raise ValueError("PDF candidate selection requires nonempty candidate and canonical room IDs")
    draft_path = Path(path)
    draft = read_pdf_plan_draft(draft_path)
    if _file_hash(Path(draft.source_path)) != draft.source_sha256:
        raise ValueError("Original PDF source changed: source hash differs from draft")
    original = import_pdf_plan(draft.source_path, page=draft.page, unit_scale_mm=draft.unit_scale_mm, origin_pt=draft.origin_pt)
    if original.to_dict() != draft.to_dict():
        raise ValueError("PDF draft candidate geometry or metadata differs from its original source")
    if draft.issues:
        raise ValueError("PDF draft has unresolved issues: " + "; ".join(draft.issues))
    by_id = {candidate.id: candidate for candidate in draft.candidates}
    if set(candidate_mapping) - by_id.keys():
        raise ValueError("PDF selection references an unknown candidate")
    rooms = tuple(ImportedRoom(candidate_id, "room", by_id[candidate_id].rect, candidate_id, True)
                  for candidate_id in sorted(candidate_mapping))
    fixed = validate_fixed_rooms(rooms, layout_spec, candidate_mapping)
    _write_json(draft_path.with_suffix(".confirmation.json"), {
        "format": "FLC-pdf-import-confirmation", "confirmed": True, "reviewer": reviewer.strip(),
        "source_sha256": draft.source_sha256, "draft_sha256": _file_hash(draft_path),
        "layout_sha256": hashlib.sha256(_canonical_json(layout_spec.to_dict())).hexdigest(),
        "selection_sha256": hashlib.sha256(_canonical_json(dict(candidate_mapping))).hexdigest(),
        "page": draft.page, "page_object": draft.page_object, "content_sha256": draft.content_sha256,
        "calibration": draft.to_dict()["calibration"], "candidate_mapping": dict(candidate_mapping),
        "scope": "PDF candidate selection and calibration acceptance; not engineering sign-off",
    })
    return fixed


def _extract(operations, draft: PdfPlanDraft, cropbox) -> tuple[list[PdfRoomCandidate], list[str]]:
    candidates, issues, stack, subpaths = [], [], [], []
    ctm, clipped, path_invalid, path_start = _IDENTITY, False, False, 0
    current = None
    for index, (args, operator) in enumerate(operations):
        if operator == b"q":
            if len(stack) >= _MAX_STACK:
                return [], ["graphics_limit: graphics-state nesting exceeds 64"]
            stack.append((ctm, clipped))
        elif operator == b"Q":
            if not stack:
                return [], ["malformed_graphics: unmatched restore"]
            ctm, clipped = stack.pop()
        elif operator == b"cm":
            ctm = _compose(tuple(float(value) for value in args), ctm)
        elif operator in {b"m", b"re"}:
            if not subpaths:
                path_start = index
            if operator == b"re":
                x, y, width, height = map(float, args)
                current = {"points": [_point(ctm, px, py) for px, py in ((x, y), (x + width, y), (x + width, y + height), (x, y + height))], "closed": True}
            else:
                current = {"points": [_point(ctm, *map(float, args))], "closed": False}
            subpaths.append(current)
        elif operator == b"l":
            if current is None:
                path_invalid = True
            else:
                current["points"].append(_point(ctm, *map(float, args)))
        elif operator == b"h":
            if current is None:
                path_invalid = True
            else:
                current["closed"] = True
        elif operator in {b"c", b"v", b"y"}:
            path_invalid = True
        elif operator in {b"W", b"W*"}:
            clipped = True
            path_invalid = True
            issues.append(f"op{index}: unsupported_clipping")
        elif operator in {b"S", b"s", b"f", b"F", b"f*", b"B", b"B*", b"b", b"b*", b"n"}:
            if operator in {b"s", b"b", b"b*"} and current:
                current["closed"] = True
            if operator != b"n" and (subpaths or path_invalid):
                try:
                    if clipped or path_invalid or len(subpaths) != 1:
                        raise ValueError("unsupported_path: clipped, curved, malformed or compound path")
                    if not subpaths[0]["closed"]:
                        raise ValueError("open_path: closure is not explicit; no inferred room boundary")
                    points = subpaths[0]["points"]
                    if len(points) > 5:
                        raise ValueError("nonrectangular: path has more than four corners")
                    ox, oy = draft.origin_pt
                    rect = _rectangle(Polygon([((x - ox) * draft.unit_scale_mm, (y - oy) * draft.unit_scale_mm) for x, y in points]))
                    if any(x < float(cropbox.left) - 1e-6 or x > float(cropbox.right) + 1e-6
                           or y < float(cropbox.bottom) - 1e-6 or y > float(cropbox.top) + 1e-6 for x, y in points):
                        raise ValueError("unsupported_crop: candidate crosses the visible page crop box")
                    candidates.append(PdfRoomCandidate(f"pdf:page{draft.page}:op{path_start}", path_start, rect))
                    if len(candidates) > _MAX_CANDIDATES:
                        return [], ["candidate_limit: page exceeds 1000 rectangular candidates"]
                except ValueError as exc:
                    issues.append(f"op{path_start}: {exc}")
            current, subpaths, path_invalid = None, [], False
        elif operator in {b"Do", b"INLINE IMAGE", b"sh", b"gs"}:
            issues.append(f"op{index}: unsupported_resource: image, form, shading or external graphics state")
        elif operator == b"Tr" and float(args[0]) >= 4:
            issues.append(f"op{index}: unsupported_text_clipping")
        elif operator not in _STYLE_AND_TEXT:
            issues.append(f"op{index}: unsupported_operator: {operator!r}")
    if stack or subpaths or path_invalid:
        issues.append("malformed_graphics: unbalanced save/restore or unfinished path")
    return candidates, issues


def _compose(first, second):
    a, b, c, d, e, f = first
    g, h, i, j, k, l = second
    result = (a * g + b * i, a * h + b * j, c * g + d * i, c * h + d * j, e * g + f * i + k, e * h + f * j + l)
    if not all(math.isfinite(value) for value in result):
        raise ValueError("nonfinite graphics transformation")
    return result


def _point(matrix, x, y):
    a, b, c, d, e, f = matrix
    return a * x + c * y + e, b * x + d * y + f


def _finish(draft, candidates, issues):
    ambiguities = _bounded_messages(f"candidate_overlap: {first.id} and {second.id} require explicit object selection"
                                   for index, first in enumerate(candidates) for second in candidates[:index]
                                   if first.rect.intersection_area(second.rect) > 1e-6)
    if not candidates:
        issues = [*issues, "no_candidates: no supported closed rectangular vector paths"]
    return PdfPlanDraft(draft.source_path, draft.source_sha256, draft.page, draft.page_object, draft.content_sha256,
                        draft.unit_scale_mm, draft.origin_pt, tuple(candidates), _bounded_messages(issues), ambiguities)


def _bounded_messages(messages):
    values = list(islice(messages, _MAX_DIAGNOSTICS + 1))
    if len(values) > _MAX_DIAGNOSTICS:
        values[-1] = "diagnostic_limit: additional source diagnostics omitted after 1000 entries"
    return tuple(values)


def _finite_number(value):
    try:
        return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
    except OverflowError:
        return False


def _validate(raw):
    try:
        _canonical_json(raw)  # Reject NaN and Infinity, which JSON Schema numbers otherwise accept.
    except (ValueError, TypeError, OverflowError) as exc:
        raise ValueError("Malformed PDF draft requires finite JSON values") from exc
    schema = json.loads(resource_path("schemas/pdf_plan_draft.schema.json").read_text(encoding="utf-8"))
    errors = sorted(Draft202012Validator(schema).iter_errors(raw), key=lambda error: str(error.path))
    if errors:
        raise ValueError("Malformed PDF draft: " + errors[0].message)
    ids = [candidate["id"] for candidate in raw["candidates"]]
    if len(ids) != len(set(ids)):
        raise ValueError("Malformed PDF draft has duplicate candidate identities")


def _unique_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Duplicate PDF draft JSON key: {key}")
        result[key] = value
    return result

"""Bounded raster source identity and unverified OCR words, never fixed rooms."""

from __future__ import annotations

import copy
from dataclasses import dataclass
import hashlib
from io import BytesIO
import json
import math
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import tempfile
import time
import warnings

from jsonschema import Draft202012Validator
from PIL import Image, ImageOps, UnidentifiedImageError

from .resources import resource_path

_MAX_SOURCE_BYTES = 20 * 1024 * 1024
_MAX_TSV_BYTES = 2 * 1024 * 1024
_MAX_DRAFT_BYTES = 8 * 1024 * 1024
_MAX_PIXELS = 16_000_000
_MAX_SIDE = 8192
_MAX_ROWS = 10000
_MAX_WORDS = 5000
_TSV_HEADER = "level page_num block_num par_num line_num word_num left top width height conf text".split()
_COORDINATES = {"units": "px", "origin": "top-left", "x": "right", "y": "down", "orientation": "upright-after-exif"}
_ASSUMPTIONS = (
    "Pixels only: no scale, millimetres, rooms or room semantics are inferred from image labels or resolution metadata.",
    "OCR words and engine confidence are UNVERIFIED even at 100; external TSV boxes must refer to upright-after-EXIF pixels.",
    "EXIF is applied once; transparency is composited on white and converted to RGB without ICC colour correction for decoded identity.",
    "Source identity and named supplied review attribution are not authenticated authorship or engineering approval.",
)


@dataclass(frozen=True)
class RasterPlanDraft:
    source: dict
    image: dict
    ocr: dict
    issues: tuple[str, ...]
    state: str = "DRAFT"

    def to_dict(self) -> dict:
        return {"format": "FLC-raster-plan-draft", "version": "0.1", "state": self.state,
                "source": copy.deepcopy(self.source), "image": copy.deepcopy(self.image),
                "coordinates": dict(_COORDINATES), "ocr": copy.deepcopy(self.ocr),
                "issues": list(self.issues), "assumptions": list(_ASSUMPTIONS)}


def import_raster_plan(path: str | Path, *, ocr_tsv: str | Path | None = None,
                       ocr_tesseract: bool = False, tesseract_path: str | Path | None = None,
                       language: str = "eng", timeout_seconds: float = 30) -> RasterPlanDraft:
    """Decode one bounded PNG/JPEG; optional OCR cannot create canonical geometry."""
    if type(ocr_tesseract) is not bool or (ocr_tsv is not None and ocr_tesseract):
        raise ValueError("Choose either external OCR TSV or opt-in local Tesseract")
    if not _finite(timeout_seconds) or not 0 < timeout_seconds <= 60:
        raise ValueError("OCR timeout_seconds must be finite and within (0, 60]")
    if not isinstance(language, str) or len(language) > 100 or re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*(\+[A-Za-z][A-Za-z0-9_]*)*", language) is None:
        raise ValueError("OCR language must be a bounded Tesseract language identifier")
    if tesseract_path is not None and not ocr_tesseract:
        raise ValueError("A local engine path requires explicit ocr_tesseract opt-in")
    source, metadata, upright = _decode_image(Path(path))
    ocr = _empty_ocr("none")
    issues = ["ocr_not_requested: OCR status UNKNOWN; no words inferred"]
    try:
        if ocr_tsv is not None:
            tsv_path = Path(ocr_tsv).resolve()
            tsv = _read_bounded(tsv_path, _MAX_TSV_BYTES, "OCR TSV")
            ocr = _tsv_ocr(tsv, metadata, method="external_tsv", source_path=str(tsv_path))
            issues = []
        elif ocr_tesseract:
            engine = str(Path(tesseract_path).resolve()) if tesseract_path is not None else shutil.which("tesseract")
            ocr, issues = _local_ocr(upright, metadata, engine, language, timeout_seconds)
    finally:
        upright.close()
    draft = RasterPlanDraft(source, metadata, ocr, tuple(issues))
    _validate(draft.to_dict())
    return draft


def write_raster_plan_draft(path: str | Path, draft: RasterPlanDraft) -> None:
    raw = draft.to_dict()
    _validate(raw)
    _verify_sources(raw)
    destination = Path(path).resolve()
    protected = {Path(raw["source"]["path"]).resolve()}
    if raw["ocr"]["source_path"] is not None:
        protected.add(Path(raw["ocr"]["source_path"]).resolve())
    if destination in protected:
        raise ValueError("Raster draft must not overwrite its image or OCR source")
    _write_json(destination, raw)


def read_raster_plan_draft(path: str | Path) -> RasterPlanDraft:
    try:
        raw = json.loads(_read_bounded(Path(path), _MAX_DRAFT_BYTES, "raster draft").decode("utf-8"), object_pairs_hook=_unique_keys)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"Malformed raster draft: {exc}") from exc
    _validate(raw)
    _verify_sources(raw)
    return RasterPlanDraft(raw["source"], raw["image"], raw["ocr"], tuple(raw["issues"]))


def record_raster_review(draft_path: str | Path, evidence_path: str | Path, *,
                         selected_word_ids: list[str], reviewer: str, source_sha256: str) -> None:
    """Record explicit source-word selection; it does not accept or map room geometry."""
    if not isinstance(reviewer, str) or not reviewer.strip() or len(reviewer) > 1024:
        raise ValueError("Raster review requires a named supplied reviewer")
    if not isinstance(selected_word_ids, (list, tuple)) or not selected_word_ids or not all(
            isinstance(value, str) for value in selected_word_ids) or len(set(selected_word_ids)) != len(selected_word_ids):
        raise ValueError("Raster review requires distinct explicit typed word IDs")
    draft = read_raster_plan_draft(draft_path)
    if source_sha256 != draft.source["sha256"]:
        raise ValueError("Raster review source hash differs from the actual source")
    by_id = {word["id"] for word in draft.ocr["words"]}
    if set(selected_word_ids) - by_id:
        raise ValueError("Raster review references unknown word IDs")
    destination = Path(evidence_path).resolve()
    if destination in {Path(draft_path).resolve(), Path(draft.source["path"]).resolve()} or (
            draft.ocr["source_path"] is not None and destination == Path(draft.ocr["source_path"]).resolve()):
        raise ValueError("Review evidence must be separate from the draft and original sources")
    _write_json(destination, {"format": "FLC-raster-review-evidence", "version": "0.1", "state": "RECORDED",
        "reviewer": reviewer.strip(), "selected_word_ids": list(selected_word_ids),
        "source_sha256": draft.source["sha256"], "decoded_image_sha256": draft.image["decoded_sha256"],
        "ocr_tsv_sha256": draft.ocr["tsv_sha256"],
        "draft_sha256": hashlib.sha256(_read_bounded(Path(draft_path), _MAX_DRAFT_BYTES, "raster draft")).hexdigest(),
        "scope": "Named supplied selection of unverified OCR word IDs; not engineering approval, canonical room mapping or scale acceptance"})


def _decode_image(path):
    path = path.resolve()
    expected = {".png": "PNG", ".jpg": "JPEG", ".jpeg": "JPEG"}.get(path.suffix.lower())
    if expected is None:
        raise ValueError("Raster source must be PNG or JPEG")
    data = _read_bounded(path, _MAX_SOURCE_BYTES, "image source")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(BytesIO(data), formats=["PNG", "JPEG"]) as image:
                width, height = image.size
                if image.format != expected or max(width, height) > _MAX_SIDE or width * height > _MAX_PIXELS:
                    raise ValueError("Raster image format or dimension/pixel limit exceeded")
                if getattr(image, "n_frames", 1) != 1:
                    raise ValueError("Raster source must contain exactly one image frame")
                with Image.open(BytesIO(data), formats=["PNG", "JPEG"]) as validator:
                    validator.verify()
                orientation = image.getexif().get(274, 1)
                if type(orientation) is not int or not 1 <= orientation <= 8:
                    raise ValueError("Raster EXIF orientation must be an integer from 1 through 8")
            with Image.open(BytesIO(data), formats=["PNG", "JPEG"]) as image:
                oriented = ImageOps.exif_transpose(image).convert("RGBA")
                upright = Image.new("RGB", oriented.size, "white")
                upright.paste(oriented, mask=oriented.getchannel("A"))
                oriented.close()
    except (OSError, UnidentifiedImageError, SyntaxError, TypeError, Image.DecompressionBombWarning, Image.DecompressionBombError) as exc:
        raise ValueError(f"Malformed or oversized raster image source: {exc}") from exc
    pixel_hash = hashlib.sha256(f"FLC-raster-RGB-v1\x00{upright.width}x{upright.height}\x00".encode("ascii") + upright.tobytes()).hexdigest()
    return ({"path": str(path), "sha256": hashlib.sha256(data).hexdigest(), "format": expected, "byte_size": len(data)},
            {"raw_width_px": width, "raw_height_px": height, "width_px": upright.width, "height_px": upright.height,
             "exif_orientation": orientation, "decoded_mode": "RGB", "decoded_sha256": pixel_hash}, upright)


def _read_bounded(path, limit, label):
    try:
        if not path.is_file():
            raise ValueError(f"{label} must be a regular file")
        flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NONBLOCK", 0)
        with os.fdopen(os.open(path, flags), "rb") as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                raise ValueError(f"{label} must remain a regular file")
            data = stream.read(limit + 1)
    except (OSError, ValueError) as exc:
        raise ValueError(f"Unavailable {label}: {exc}") from exc
    if len(data) > limit:
        raise ValueError(f"{label} exceeds its byte limit")
    return data


def _empty_ocr(method, *, engine=None, language=None):
    return {"method": method, "status": "UNKNOWN", "source_path": None, "tsv_utf8": None,
            "tsv_sha256": None, "engine": engine, "language": language, "words": []}


def _tsv_ocr(data, image, *, method, source_path=None, engine=None, language=None):
    try:
        text = data.decode("utf-8")
    except UnicodeError as exc:
        raise ValueError("OCR TSV must be exact UTF-8") from exc
    words = _parse_tsv(text, image["width_px"], image["height_px"])
    return {"method": method, "status": "UNVERIFIED", "source_path": source_path, "tsv_utf8": text,
            "tsv_sha256": hashlib.sha256(data).hexdigest(), "engine": engine, "language": language, "words": words}


def _local_ocr(upright, metadata, engine, language, timeout_seconds):
    unknown = _empty_ocr("tesseract", engine=engine, language=language)
    if not engine or not Path(engine).is_file():
        return unknown, ["ocr_unavailable: local Tesseract executable is unavailable"]
    with tempfile.TemporaryDirectory(prefix="flc-raster-ocr-") as directory:
        root = Path(directory)
        image_path, output = root / "upright.png", root / "word-output"
        stdout_path, stderr_path = root / "stdout.log", root / "stderr.log"
        tsv_path = output.with_suffix(".tsv")
        upright.save(image_path, format="PNG")
        monitored = ((tsv_path, _MAX_TSV_BYTES), (stdout_path, 65536), (stderr_path, 65536))
        def exceeded():
            return any(path.exists() and path.stat().st_size > limit for path, limit in monitored)
        with stdout_path.open("wb") as stdout, stderr_path.open("wb") as stderr:
            try:
                process = subprocess.Popen([engine, str(image_path), str(output), "--psm", "6", "-l", language, "tsv"],
                    shell=False, stdin=subprocess.DEVNULL, stdout=stdout, stderr=stderr,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            except OSError:
                return unknown, ["ocr_unavailable: local Tesseract could not be executed"]
            deadline = time.monotonic() + timeout_seconds
            try:
                while True:
                    if exceeded():
                        return unknown, ["ocr_output_limit: local output exceeds bounded TSV/log bytes"]
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        return unknown, ["ocr_timeout: local OCR deadline exceeded; no partial words accepted"]
                    try:
                        returncode = process.wait(timeout=min(0.05, remaining))
                        break
                    except subprocess.TimeoutExpired:
                        continue
            finally:
                if process.poll() is None:
                    process.kill()
                    process.wait(timeout=5)
        if exceeded():
            return unknown, ["ocr_output_limit: local output exceeds bounded TSV/log bytes"]
        if returncode != 0:
            return unknown, [f"ocr_failed: local engine exited with code {returncode}; no partial words accepted"]
        try:
            return _tsv_ocr(_read_bounded(tsv_path, _MAX_TSV_BYTES, "local OCR TSV"), metadata,
                            method="tesseract", engine=engine, language=language), []
        except ValueError:
            return unknown, ["ocr_malformed: local TSV output does not satisfy the bounded pixel-word contract"]


def _parse_tsv(text, width, height):
    lines = text.splitlines()
    if not lines or lines[0].split("\t") != _TSV_HEADER or len(lines) - 1 > _MAX_ROWS:
        raise ValueError("OCR TSV requires the exact bounded Tesseract header and row count")
    words, word_hierarchy = [], set()
    for row_number, line in enumerate(lines[1:], 2):
        if not line:
            continue
        values = line.split("\t")
        if len(values) != 12:
            raise ValueError("Malformed OCR TSV column count")
        if not all(re.fullmatch(r"[0-9]+", value) for value in values[:10]):
            raise ValueError("OCR TSV hierarchy and pixel boxes require unsigned integer values")
        numbers = list(map(int, values[:10]))
        level, page, block, paragraph, line_id, word_id, left, top, box_width, box_height = numbers
        if level not in range(1, 6) or page != 1 or max(numbers[2:6]) > 1000000:
            raise ValueError("OCR TSV requires bounded single-page hierarchy")
        if left + box_width > width or top + box_height > height:
            raise ValueError("OCR TSV pixel box is outside the upright image bounds")
        if re.fullmatch(r"-?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)", values[10]) is None:
            raise ValueError("OCR TSV confidence must be a finite numeric value")
        confidence = float(values[10])
        if level != 5:
            if confidence != -1 or values[11].strip():
                raise ValueError("OCR TSV nonword hierarchy rows require confidence -1 and no word text")
            continue
        text_value = values[11]
        if not text_value.strip():
            continue
        if not 0 <= confidence <= 100 or not all(numbers[2:6]) or not box_width or not box_height:
            raise ValueError("OCR TSV word confidence and hierarchy/box dimensions are invalid")
        if len(text_value) > 1024 or any(ord(character) < 32 for character in text_value):
            raise ValueError("OCR TSV word text exceeds its bound or contains control characters")
        identity = tuple(numbers[1:6])
        if identity in word_hierarchy:
            raise ValueError("OCR TSV has duplicate word hierarchy identities")
        word_hierarchy.add(identity)
        words.append({"id": f"raster:word:{row_number}", "source_row": row_number, "page_num": page,
            "block_num": block, "par_num": paragraph, "line_num": line_id, "word_num": word_id,
            "text": text_value, "pixel_box": {"left": left, "top": top, "width": box_width, "height": box_height},
            "confidence": confidence})
        if len(words) > _MAX_WORDS:
            raise ValueError("OCR TSV exceeds 5000 word candidates")
    return words


def _validate(raw):
    try:
        json.dumps(raw, allow_nan=False, ensure_ascii=False).encode("utf-8")
    except (ValueError, TypeError, OverflowError, UnicodeError) as exc:
        raise ValueError("Malformed raster draft requires finite UTF-8 JSON") from exc
    schema = json.loads(resource_path("schemas/raster_plan_draft.schema.json").read_text(encoding="utf-8"))
    error = next(Draft202012Validator(schema).iter_errors(raw), None)
    if error:
        raise ValueError("Malformed raster draft: " + error.message)
    if raw["assumptions"] != list(_ASSUMPTIONS):
        raise ValueError("Raster draft interpretation assumptions differ from this source-only contract")
    ocr = raw["ocr"]
    if ocr["method"] == "none":
        if ocr != _empty_ocr("none") or raw["issues"] != ["ocr_not_requested: OCR status UNKNOWN; no words inferred"]:
            raise ValueError("OCR not requested must remain UNKNOWN without word evidence")
    elif ocr["status"] == "UNKNOWN":
        if ocr["method"] != "tesseract" or ocr != _empty_ocr("tesseract", engine=ocr["engine"], language=ocr["language"]) or not ocr["language"] or not raw["issues"]:
            raise ValueError("Unknown local OCR requires explicit unavailable diagnostics without words")
    else:
        if not isinstance(ocr["tsv_utf8"], str) or not ocr["tsv_sha256"]:
            raise ValueError("Unverified OCR requires exact TSV evidence")
        data = ocr["tsv_utf8"].encode("utf-8")
        if len(data) > _MAX_TSV_BYTES or hashlib.sha256(data).hexdigest() != ocr["tsv_sha256"]:
            raise ValueError("OCR TSV hash or byte limit differs from its evidence")
        expected = _tsv_ocr(data, raw["image"], method=ocr["method"], source_path=ocr["source_path"], engine=ocr["engine"], language=ocr["language"])
        if ocr != expected or (ocr["method"] == "external_tsv" and (not ocr["source_path"] or ocr["engine"] is not None or ocr["language"] is not None)):
            raise ValueError("Raster OCR word geometry, confidence or identity differs from original TSV evidence")
        if ocr["method"] == "tesseract" and (ocr["source_path"] is not None or not ocr["engine"] or not ocr["language"]):
            raise ValueError("Local OCR must retain engine/language and no temporary source path")
        if raw["issues"]:
            raise ValueError("Unverified decoded OCR cannot also claim an unavailable-engine result")


def _verify_sources(raw):
    source, image, upright = _decode_image(Path(raw["source"]["path"]))
    upright.close()
    if source != raw["source"] or image != raw["image"]:
        raise ValueError("Original image source or decoded metadata changed")
    if raw["ocr"]["method"] == "external_tsv":
        data = _read_bounded(Path(raw["ocr"]["source_path"]), _MAX_TSV_BYTES, "OCR source")
        if hashlib.sha256(data).hexdigest() != raw["ocr"]["tsv_sha256"]:
            raise ValueError("Original OCR source changed")


def _write_json(path, raw):
    data = (json.dumps(raw, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode("utf-8")
    if len(data) > _MAX_DRAFT_BYTES:
        raise ValueError("Raster draft/evidence exceeds its JSON byte limit")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def _unique_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Duplicate raster draft JSON key: {key}")
        result[key] = value
    return result


def _finite(value):
    try:
        return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
    except OverflowError:
        return False

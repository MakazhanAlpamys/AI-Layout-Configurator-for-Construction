"""Source-bound DRAFT process documents with independent artifact readback.

The five-file package is separate from existing facility bundles. A delivery
PASS means its source, printed evidence and painted source contours agree;
engineering approval, complete installation paths and facility acceptance do
not follow from it. Rendering never solves or regenerates a layout.
"""

from dataclasses import asdict, dataclass, fields
import copy
import hashlib
import io
import json
import math
from pathlib import Path
import tempfile
from types import SimpleNamespace

from pypdf import PdfReader
from pypdf.generic import ArrayObject, ContentStream, DecodedStreamObject, DictionaryObject
from reportlab.lib.pagesizes import A3, landscape
from reportlab.pdfgen import canvas
from shapely.geometry import LineString, Polygon

from .drawing_sheets import _check_glyphs, _fonts, _wrap
from .engineering_library import _raw_json_preflight, _strict_json
from .orthogonal_bundle import _publish_directory
from .orthogonal_pdf import INK, _projection
from .orthogonal_pdf_qa import (_compose, _decode, _inventory_words_match,
    _number, _point, _resources, _same_color)
from .process_review import ProcessReviewLimits, load_process_review


_FORMAT = "FLC-process-documentation-draft"
_INVENTORY = {"source.json", "review.json", "documentation.pdf", "index.json", "qa.json"}
_FILES = {"source": "source.json", "review": "review.json", "pdf": "documentation.pdf"}
_COMPONENTS = ("building", "layout", "equipment", "flow_routes", "production_program",
               "transfer_program", "environment_program", "library", "scenario_requests")
_SCHEDULES = ("rooms", "operations", "equipment", "transfers", "clean_zones",
              "ventilation_zones", "pressure", "scenarios")
_REPORTS = ("production_program", "transfers", "environment", "library", "equipment_scenarios")
_WIDTH, _HEIGHT = landscape(A3)
_LEFT, _BODY_TOP, _BODY_BOTTOM = 50.0, _HEIGHT - 127.0, 143.0
_STYLES = {
    "room": {"stroke": (.35, .44, .50), "fill": (.94, .97, .99), "paint": "B*", "width": .7, "layer": 0},
    "clean_zone": {"stroke": (.12, .52, .30), "fill": INK, "paint": "S", "width": 1.1, "layer": 1},
    "ventilation_zone": {"stroke": (.17, .40, .70), "fill": INK, "paint": "S", "width": 1.1, "layer": 1},
    "pressure": {"stroke": (.69, .35, .15), "fill": INK, "paint": "S", "width": 1.4, "layer": 2},
}


@dataclass(frozen=True)
class ProcessDocumentationLimits(ProcessReviewLimits):
    max_pdf_bytes: int = 8_388_608
    max_total_bytes: int = 33_554_432
    max_metadata_bytes: int = 1_048_576
    max_text_chars: int = 1_000_000
    max_pages: int = 256
    max_stream_bytes: int = 4_194_304
    max_operations: int = 50_000
    max_total_pdf_operations: int = 262_144
    max_resources: int = 256
    max_delivery_nodes: int = 262_144
    max_delivery_depth: int = 64
    max_overlay_paths: int = 1024
    max_overlay_vertices: int = 32_768


def _valid_limits(limits):
    defaults = ProcessDocumentationLimits()
    if type(limits) is not ProcessDocumentationLimits or any(type(getattr(limits, row.name)) is not int or
            not 1 <= getattr(limits, row.name) <= getattr(defaults, row.name) for row in fields(defaults)) or limits.max_report_bytes < 4096:
        raise ValueError("Process document limits require bounded actual integers and a supported review delivery bound")


def _json(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _same(first, second):
    return _json(first) == _json(second)


def _read_bytes(path, maximum):
    path = Path(path)
    if not path.is_file() or path.stat().st_size > maximum:
        raise ValueError("Source or artifact is absent or exceeds its byte bound")
    with path.open("rb") as stream:
        data = stream.read(maximum + 1)
    if len(data) > maximum:
        raise ValueError("Source or artifact changed beyond its admitted byte bound")
    return data


def _read_json(data, maximum, limits):
    if len(data) > maximum:
        raise ValueError("Process document JSON byte budget exceeded")
    text = data.decode("utf-8")
    guard = SimpleNamespace(max_nodes=limits.max_delivery_nodes, max_depth=limits.max_delivery_depth)
    _raw_json_preflight(text, guard)
    value = _strict_json(text)
    queue, count = [(value, 0)], 0
    while queue:
        item, depth = queue.pop()
        count += 1
        if count > guard.max_nodes or depth > guard.max_depth:
            raise ValueError("Process document JSON structure budget exceeded")
        if type(item) is dict:
            queue.extend((child, depth + 1) for pair in item.items() for child in pair)
        elif type(item) is list:
            queue.extend((child, depth + 1) for child in item)
        elif item is not None and type(item) not in (str, bool, int, float):
            raise ValueError("Process documents require strict finite JSON values")
        elif type(item) in (int, float) and (abs(item) > 1e12 or not math.isfinite(item)):
            raise ValueError("Process document JSON contains unsupported numbers")
    _json(value)
    return value


def _current_review(source, limits, contract=None):
    original = _read_bytes(source, limits.max_input_bytes)
    parent = ProcessReviewLimits(**{row.name: getattr(limits, row.name) for row in fields(ProcessReviewLimits)})
    loader = _contract_value(contract, "review_loader", load_process_review)
    review = loader(source, limits=parent)
    if _read_bytes(source, limits.max_input_bytes) != original or review["review_source"]["file_sha256"] != _sha(original):
        raise ValueError("Original process source changed during fresh review")
    if review.get("inventory_status") in ("INCOMPLETE", "NOT_EVALUATED") or review.get("identity") is None or \
            set(review["source_frames"]) != set(_COMPONENTS) or set(review["schedules"]) != set(_SCHEDULES) or set(review["reports"]) != set(_REPORTS):
        raise ValueError("Complete process schedules require an admitted complete review inventory")
    if len(_json(review)) > limits.max_report_bytes:
        raise ValueError("Fresh process review exceeds the document delivery bound")
    admission = _contract_value(contract, "admit_review", None)
    if admission is not None:
        admission(review)
    return original, review


def _content_sections(review):
    identity = review["identity"]
    introductory = ["Supplied process concept; source declarations are not authenticated engineering approval.",
        "Document status: DRAFT", "Full facility validation: UNKNOWN", "Engineering acceptance: PENDING",
        "Full installation paths and three-dimensional feasibility: UNKNOWN",
        "ISO class, GMP grade, state, applicability, design pressures and measured pressures remain separate declarations.",
        *[f"Component {name}: {digest}" for name, digest in identity["component_sha256"].items()]]
    sections = [("identity", None, "Source identity and scope", introductory)]
    for name in _SCHEDULES:
        lines = []
        for number, row in enumerate(review["schedules"][name]["rows"], 1):
            lines.append(f"Record {number}: " + _json(row).decode("utf-8"))
            if type(row.get("label")) is str:
                lines.append("Label: " + row["label"])
        sections.append(("schedule", name, "Source schedule: " + name, lines or ["No supplied rows."]))
    for name in _REPORTS:
        record = review["reports"][name]
        sections.append(("report", name, "Fresh scoped report: " + name,
            ["Report status: " + record["status"], "Fresh evidence: " + _json(record["evidence"]).decode("utf-8")]))
    return sections


def _source_paths(review, limits):
    """Literal source corners and independently resolved portal segments only."""
    frame = review["source_frames"]
    records = []
    for identifier, shape in sorted(frame["layout"]["rooms"].items()):
        if shape.get("kind") == "orthogonal":
            points = shape["vertices"]
        else:
            x, y, w, h = (shape[key] for key in ("x", "y", "width", "height"))
            points = [[x, y], [x + w, y], [x + w, y + h], [x, y + h]]
        records.append(("room", identifier, points, True))
    environment = frame["environment_program"]
    if environment is not None:
        for namespace, kind in (("clean_zones", "clean_zone"), ("ventilation_zones", "ventilation_zone")):
            for row in environment[namespace]:
                records.append((kind, row["id"], row["boundary"]["vertices"], True))
        evidence = review["reports"]["environment"]["evidence"]
        for row in evidence.get("connections", []):
            if row["status"] == "PASS":
                records.append(("pressure", row["edge_id"], row["opening_segment"], False))
    return _admit_source_paths(records, limits)


def _admit_source_paths(records, limits):
    """Bound and admit literal vector paths for the selected document contract."""
    if len(records) > limits.max_overlay_paths or sum(len(row[2]) for row in records) > limits.max_overlay_vertices:
        raise ValueError("Process document source path budget exceeded")
    for _, _, points, closed in records:
        geometry = Polygon(points) if closed else LineString(points)
        if not geometry.is_valid or geometry.is_empty or (geometry.area <= 0 if closed else geometry.length <= 0):
            raise ValueError("Unsupported or unrepresentable literal document geometry")
    return records


def _source_projection(review):
    source = review["source_frames"]["building"]["layout"]
    return _projection(SimpleNamespace(layout=SimpleNamespace(
        boundary=SimpleNamespace(width_mm=source["boundary"]["width"], height_mm=source["boundary"]["height"]),
        wall_thickness_mm=source["wall_thickness_mm"])))


@dataclass(frozen=True)
class _DocumentContract:
    """Private per-call choices for an explicitly selected document version."""
    version: str = "0.1"
    scope: str = "Synchronized supplied process appendix and literal source overlays"
    review_loader: object = None
    admit_review: object = None
    sections: object = None
    source_paths: object = None
    projection: object = None
    styles: object = None
    extra_index: object = None
    check_appendix: object = None
    legend: object = None
    legend_size: float = 9.0
    legend_leading: float = 20.0
    legend_wrap: bool = False
    plan_bounds: object = None
    plan_title: str = "Literal room and environmental source overlays"
    delivery_validator: object = None


def _contract_value(contract, name, default):
    value = getattr(contract, name, None)
    return default if value is None else value


def _document_projection(review, limits, contract=None):
    callback = _contract_value(contract, "projection", None)
    return _source_projection(review) if callback is None else callback(review, limits)


def _page_plan(review, regular, limits, contract=None):
    sections = _contract_value(contract, "sections", _content_sections)(review)
    if sum(len(text) for _, _, title, lines in sections for text in (title, *lines)) > limits.max_text_chars:
        raise ValueError("Process document text budget exceeded")
    all_text = [text for _, _, title, lines in sections for text in (title, *lines)]
    all_text += [review["identity"]["drawing_revision"], review["review_source"]["file_sha256"]]
    _check_glyphs(all_text, regular)
    pages = []
    capacity = int((_BODY_TOP - _BODY_BOTTOM) / 13)
    for kind, identifier, title, entries in sections:
        lines = [line for entry in entries for line in _wrap(entry, regular, 9, _WIDTH - 2*_LEFT)]
        for offset in range(0, len(lines), capacity):
            pages.append((kind, identifier, title, lines[offset:offset + capacity]))
    pages.append(("plan", "source_overlays", _contract_value(contract, "plan_title",
        "Literal room and environmental source overlays"), []))
    if not pages or len(pages) > limits.max_pages:
        raise ValueError("Process document page budget exceeded")
    return pages


def _paint(document, kind, identifier, points, closed, scale, styles=None):
    # JSON-escaped PDF strings are not interchangeable with PDF literal strings.
    from pypdf.generic import TextStringObject
    text = io.BytesIO()
    TextStringObject(identifier).write_to_stream(text)
    style = (_STYLES if styles is None else styles)[kind]
    document.saveState()
    document._code.append(f"/FLC << /Kind /{kind} /Id {text.getvalue().decode('ascii')} >> BDC")
    document.setFillColorRGB(*style["fill"])
    document.setStrokeColorRGB(*style["stroke"])
    document.setLineWidth(style["width"] / scale)
    dash = style.get("dash", (4, 2) if kind == "ventilation_zone" else ())
    if dash:
        document.setDash([value/scale for value in dash])
    for number, (x, y) in enumerate(points):
        document._code.append(f"{x:.17g} {y:.17g} {'m' if number == 0 else 'l'}")
    if closed:
        document._code.append("h")
    document._code.extend((style["paint"], "EMC"))
    document.restoreState()


def _render(review, limits, contract=None):
    regular, bold = _fonts(None)
    plan = _page_plan(review, regular, limits, contract)
    paths = _contract_value(contract, "source_paths", _source_paths)(review, limits)
    projection = _document_projection(review, limits, contract)
    styles = _contract_value(contract, "styles", _STYLES)
    memory = io.BytesIO()
    document = canvas.Canvas(memory, pagesize=(_WIDTH, _HEIGHT), pageCompression=0)
    document.setTitle("Supplied process concept | DRAFT")
    document.setAuthor("Layout Configurator software projection")
    index = []
    for number, (kind, identifier, title, lines) in enumerate(plan, 1):
        row = {"page_id": f"PR-{number:04d}", "page_number": number, "kind": kind, "source_id": identifier}
        index.append(row)
        document.setFillColorRGB(*INK)
        document.setFont(bold, 14)
        document.drawString(_LEFT, _HEIGHT - 43, "Supplied process concept | DRAFT")
        document.setFont(regular, 9)
        document.drawString(_LEFT, _HEIGHT - 64,
            f"Variant {review['identity']['variant']} | Revision {review['identity']['drawing_revision']} | {row['page_id']}")
        document.drawString(_LEFT, _HEIGHT - 81, "Review status: " + review["status"])
        document.setFont(bold, 11)
        document.drawString(_LEFT, _HEIGHT - 103, title)
        document.setFont(regular, 9)
        for position, line in enumerate(lines):
            document.drawString(_LEFT, _BODY_TOP - 13*position, line)
        if kind == "plan":
            scale, (ox, oy) = projection["scale_pt_per_mm"], projection["origin_pt"]
            document.saveState()
            document._code.append(f"{scale:.17g} 0 0 {scale:.17g} {ox:.17g} {oy:.17g} cm")
            for role, object_id, points, closed in paths:
                _paint(document, role, object_id, points, closed, scale, styles)
            document.restoreState()
            document.setFillColorRGB(*INK)
            size = _contract_value(contract, "legend_size", 9)
            leading = _contract_value(contract, "legend_leading", 20)
            legend = _contract_value(contract, "legend", None)
            entries = (("Room: original contour", "Clean zone: literal boundary",
                    "Ventilation zone: literal dashed boundary", "Pressure: independently resolved portal segment",
                    "Design and measured pressure remain separate", "No airflow or installation-path approval")
                if legend is None else legend(review))
            _check_glyphs(entries, regular)
            lines = [line for entry in entries for line in (_wrap(entry, regular, size, _WIDTH - 925)
                if _contract_value(contract, "legend_wrap", False) else [entry])]
            if lines and 685 - leading*(len(lines) - 1) < _BODY_BOTTOM:
                raise ValueError("Process source legend exceeds the readable plan frame")
            document.setFont(regular, size)
            for position, text in enumerate(lines):
                document.drawString(875, 685 - leading*position, text)
        document.setFillColorRGB(*INK)
        document.setFont(regular, 8)
        document.drawString(_LEFT, 97, "Full facility validation: UNKNOWN | Engineering acceptance: PENDING")
        document.drawString(_LEFT, 80, "Original source SHA256: " + review["review_source"]["file_sha256"])
        document.drawString(_LEFT, 63, f"Page {number}/{len(plan)} | Full installation paths: UNKNOWN")
        document.showPage()
    document.save()
    data = memory.getvalue()
    if len(data) > limits.max_pdf_bytes:
        raise ValueError("Process document PDF byte budget exceeded")
    return data, index, projection


def _base_qa(contract=None):
    return {"format": "FLC-process-documentation-qa", "version": _contract_value(contract, "version", "0.1"), "scope": "Persisted source, printed evidence and painted source geometry only",
        "status": "FAIL", "ok": False, "document_status": "DRAFT", "review_status": "UNKNOWN", "issues": [],
        "full_facility_validation": {"status": "UNKNOWN", "ok": False}, "engineering_acceptance": "PENDING",
        "authenticated_approval": "UNKNOWN"}


def _read_vectors(page, reader, limits, projection, is_plan, *, styles=None, plan_bounds=None):
    """Read new process roles independently of the existing PDF role contract."""
    styles = _STYLES if styles is None else styles
    streams = page.get("/Contents")
    if streams is None:
        raise ValueError("PDF page has no content stream")
    if not isinstance(streams, ArrayObject):
        streams = [streams]
    if len(streams) > 16:
        raise ValueError("PDF content stream count budget exceeded")
    data = b"\n".join(_decode(item.get_object(), limits) for item in streams)
    if len(data) > limits.max_stream_bytes:
        raise ValueError("Combined PDF page stream budget exceeded")
    stream = DecodedStreamObject()
    stream.set_data(data)
    operations = ContentStream(stream, reader).operations
    if len(operations) > limits.max_operations:
        raise ValueError("PDF page operation budget exceeded")
    state = {"matrix": (1, 0, 0, 1, 0, 0), "fill": (0, 0, 0), "stroke": (0, 0, 0), "width": 1,
        "dash": (), "phase": 0, "font": 0, "text_matrix": (1, 0, 0, 1, 0, 0), "horizontal_scale": 100}
    stack, tag, paths, observed, stage = [], None, [], {}, -1
    ignored = {b"BT", b"ET", b"TL", b"T*", b"J", b"j", b"M"}
    paints = {b"S", b"s", b"f", b"f*", b"B", b"B*", b"b", b"b*"}
    for values, operator in operations:
        if operator == b"q":
            if len(stack) >= 64:
                raise ValueError("PDF state nesting exceeded")
            stack.append(copy.deepcopy(state))
        elif operator == b"Q":
            if not stack:
                raise ValueError("Unbalanced PDF state")
            state = stack.pop()
        elif operator == b"cm":
            if len(values) != 6:
                raise ValueError("Malformed PDF transform")
            state["matrix"] = _compose(state["matrix"], tuple(_number(value) for value in values))
        elif operator in (b"rg", b"RG"):
            color = tuple(_number(value) for value in values)
            if len(color) != 3 or any(not 0 <= value <= 1 for value in color):
                raise ValueError("Malformed PDF color")
            state["fill" if operator == b"rg" else "stroke"] = color
        elif operator == b"w":
            if len(values) != 1 or _number(values[0]) <= 0:
                raise ValueError("Malformed PDF stroke width")
            state["width"] = _number(values[0])
        elif operator == b"d":
            if len(values) != 2 or not isinstance(values[0], ArrayObject):
                raise ValueError("Malformed PDF dash pattern")
            state["dash"], state["phase"] = tuple(_number(value) for value in values[0]), _number(values[1])
        elif operator == b"Tf":
            if len(values) != 2 or values[0] not in page.get("/Resources", {}).get("/Font", {}) or _number(values[1]) <= 0:
                raise ValueError("Invalid PDF text font")
            state["font"] = _number(values[1])
        elif operator == b"Tm":
            if len(values) != 6:
                raise ValueError("Invalid PDF text transform")
            state["text_matrix"] = tuple(_number(value) for value in values)
        elif operator == b"Tz":
            if len(values) != 1 or _number(values[0]) <= 0:
                raise ValueError("Invalid PDF text scaling")
            state["horizontal_scale"] = _number(values[0])
        elif operator in (b"Tr", b"Ts", b"Tc", b"Tw"):
            if len(values) != 1 or _number(values[0]) != 0:
                raise ValueError("Hidden, raised or artificially spaced source text is unsupported")
        elif operator in (b"Tj", b"TJ"):
            if not _same_color(state["fill"], INK):
                raise ValueError("Source text is not visibly painted in the declared ink")
            a, b, c, d, _, _ = _compose(state["matrix"], state["text_matrix"])
            size = state["font"]
            # Two nonzero column lengths do not establish a glyph area: a
            # singular/sheared matrix can leave extracted text fully intact
            # while flattening or obscuring its actual painted characters.
            if a <= 0 or d <= 0 or abs(b) > 1e-7 or abs(c) > 1e-7 or (a*d - b*c)*size**2 < 1 or \
                    not 4 <= math.hypot(a, b)*size*state["horizontal_scale"]/100 <= 30 or not 4 <= math.hypot(c, d)*size <= 30:
                raise ValueError("Actual PDF source text is not readable")
        elif operator == b"BDC":
            if not is_plan or tag is not None or paths or len(values) != 2 or str(values[0]) != "/FLC":
                raise ValueError("Unexpected or nested source vector group")
            properties = values[1]
            if not isinstance(properties, DictionaryObject) or set(properties) != {"/Kind", "/Id"}:
                raise ValueError("Source vector requires closed kind/identity properties")
            role, identifier = str(properties["/Kind"])[1:], str(properties["/Id"])
            if role not in styles or not identifier or len(identifier) > 128 or styles[role]["layer"] < stage:
                raise ValueError("Unknown vector role or obscuring layer order")
            stage, tag = styles[role]["layer"], (role, identifier)
        elif operator == b"EMC":
            if tag is None or paths or tag not in observed:
                raise ValueError("Source vector group has no actual painted geometry")
            tag = None
        elif operator in (b"m", b"l"):
            if tag is None or len(values) != 2 or operator == b"l" and (not paths or paths[-1][1]):
                raise ValueError("Unmarked or malformed source contour")
            point = _point(*values, state["matrix"], projection)
            if operator == b"m":
                paths.append(([point], False))
            else:
                paths[-1][0].append(point)
        elif operator == b"h":
            if not paths or paths[-1][1]:
                raise ValueError("Malformed closed source contour")
            paths[-1] = (paths[-1][0], True)
        elif operator == b"re":
            if tag is None or len(values) != 4:
                raise ValueError("Unmarked source rectangle")
            x, y, w, h = (_number(value) for value in values)
            paths.append(([_point(px, py, state["matrix"], projection) for px, py in
                ((x, y), (x + w, y), (x + w, y + h), (x, y + h))], True))
        elif operator in paints:
            if tag is None or not paths or tag in observed:
                raise ValueError("Unmarked, duplicate or empty source painting")
            style = styles[tag[0]]
            if operator.decode("ascii") != style["paint"] or not _same_color(state["stroke"], style["stroke"]):
                raise ValueError("Source paint operation or stroke style changed")
            if style["paint"] == "B*" and not _same_color(state["fill"], style["fill"]):
                raise ValueError("Source room material was hidden or restyled")
            a, b, c, d, _, _ = state["matrix"]
            transform_scale = max(math.hypot(a, b), math.hypot(c, d))
            wanted_dash = style.get("dash", (4, 2) if tag[0] == "ventilation_zone" else ())
            if abs(state["width"]*transform_scale - style["width"]) > .002 or bool(state["dash"]) != bool(wanted_dash):
                raise ValueError("Source stroke is invisible, obscuring or incorrectly dashed")
            if state["dash"] and (len(state["dash"]) != len(wanted_dash) or abs(state["phase"]) > .001 or
                    any(abs(value*transform_scale - wanted) > .002 for value, wanted in zip(state["dash"], wanted_dash))):
                raise ValueError("Source boundary dash pattern changed")
            if plan_bounds is not None:
                scale, (ox, oy) = projection["scale_pt_per_mm"], projection["origin_pt"]
                left, bottom, right, top = plan_bounds
                for points, _ in paths:
                    for x, y in points:
                        px, py = x*scale + ox, y*scale + oy
                        if not math.isfinite(px) or not math.isfinite(py) or not left - .002 <= px <= right + .002 or \
                                not bottom - .002 <= py <= top + .002:
                            raise ValueError("Actual painted source point is outside the readable plan frame")
            observed[tag], paths = paths, []
        elif operator == b"n":
            if paths:
                raise ValueError("A source path was discarded without painting")
        elif operator not in ignored:
            raise ValueError("Unsupported PDF clipping, image, curve or operator")
    if stack or tag is not None or paths:
        raise ValueError("Incomplete PDF geometry or state")
    return observed, len(operations)


def _expected_groups(review, contract=None):
    # Artifact QA derives group inventories from the freshly recomputed review,
    # not saved page metadata or a renderer-generated text digest.
    if contract is not None and contract.sections is not None:
        return [(kind, identifier) for kind, identifier, _, _ in contract.sections(review)] + [("plan", "source_overlays")]
    return [("identity", None), *(("schedule", name) for name in _SCHEDULES),
            *(("report", name) for name in _REPORTS), ("plan", "source_overlays")]


def _check_pdf(data, index, review, limits, contract=None):
    sections = _contract_value(contract, "sections", _content_sections)(review)
    expected_groups = _expected_groups(review, contract)
    if sum(len(text) for _, _, title, lines in sections for text in (title, *lines)) > limits.max_text_chars:
        raise ValueError("Fresh process document text exceeds its declared bound")
    source_paths = _contract_value(contract, "source_paths", _source_paths)(review, limits)
    reader = PdfReader(io.BytesIO(data), strict=True)
    if reader.is_encrypted or not 1 <= len(reader.pages) <= limits.max_pages:
        raise ValueError("Unreadable or over-budget process PDF")
    rows = index["pages"]
    if type(rows) is not list or len(rows) != len(reader.pages):
        raise ValueError("PDF page count differs from the complete page inventory")
    groups, body, observed, total_operations = [], {}, {}, 0
    for number, (row, page) in enumerate(zip(rows, reader.pages), 1):
        if type(row) is not dict or set(row) != {"page_id", "page_number", "kind", "source_id"} or type(row["page_number"]) is not int or \
                row["page_number"] != number or row["page_id"] != f"PR-{number:04d}":
            raise ValueError("Malformed or duplicate process PDF page identity")
        group = (row["kind"], row["source_id"])
        if group not in expected_groups:
            raise ValueError("Page references an undeclared schedule, report or projection")
        if not groups or groups[-1] != group:
            groups.append(group)
        frame = (0.0, 0.0, _WIDTH, _HEIGHT)
        if page.rotation != 0 or page.user_unit != 1 or any(abs(_number(value) - expected) > .02
                for value, expected in zip(page.mediabox, frame)):
            raise ValueError("PDF page frame differs from its declared true-scale projection")
        # Viewer/print boxes and annotations can conceal an unchanged content
        # stream. Extracted text and path coordinates alone cannot detect it.
        for box in (page.cropbox, page.trimbox, page.bleedbox, page.artbox):
            if any(abs(_number(value) - expected) > .02 for value, expected in zip(box, frame)):
                raise ValueError("PDF display or print box clips the declared source document")
        if page.get("/Annots"):
            raise ValueError("Annotations can obscure source evidence and are outside this static document contract")
        _resources(page, limits)
        paths, operations = _read_vectors(page, reader, limits, index["projection"], row["kind"] == "plan",
            styles=_contract_value(contract, "styles", _STYLES), plan_bounds=_contract_value(contract, "plan_bounds", None))
        total_operations += operations
        if total_operations > limits.max_total_pdf_operations or set(observed).intersection(paths):
            raise ValueError("Aggregate PDF work exceeded or source contours were repeated")
        observed.update(paths)
        fragments = []

        def visitor(text, cm, tm, font, size):
            if text.strip():
                x = tm[4]*cm[0] + tm[5]*cm[2] + cm[4]
                y = tm[4]*cm[1] + tm[5]*cm[3] + cm[5]
                if not _LEFT - .02 <= x <= _WIDTH - _LEFT + .02 or not 40 <= y <= _HEIGHT - 30:
                    raise ValueError("Printed process source text is outside its readable page frame")
                fragments.append((x, y, text))

        page.extract_text(visitor_text=visitor)
        header = "\n".join(text for _, y, text in fragments if y >= _HEIGHT - 110)
        footer = "\n".join(text for _, y, text in fragments if y <= 110)
        required_header = ("Supplied process concept | DRAFT",
            f"Variant {review['identity']['variant']} | Revision {review['identity']['drawing_revision']} | {row['page_id']}",
            "Review status: " + review["status"])
        if any(text not in header for text in required_header) or any(text not in footer for text in (
                "Full facility validation: UNKNOWN | Engineering acceptance: PENDING",
                "Original source SHA256: " + review["review_source"]["file_sha256"],
                f"Page {number}/{len(rows)} | Full installation paths: UNKNOWN")):
            raise ValueError("Printed source SHA, revision, variant, verdict or acceptance scope changed")
        body.setdefault(group, []).extend(text for _, y, text in fragments if _BODY_BOTTOM <= y <= _BODY_TOP + .02)
    if groups != expected_groups:
        raise ValueError("Required source groups were omitted, repeated or reordered")
    # Check each full canonical record rather than only an ID/status substring.
    for name in _SCHEDULES:
        expected = []
        for number, record in enumerate(review["schedules"][name]["rows"], 1):
            expected.append(f"Record {number}: " + _json(record).decode("utf-8"))
            if type(record.get("label")) is str:
                expected.append("Label: " + record["label"])
        if not _inventory_words_match("\n".join(body[("schedule", name)]), expected or ["No supplied rows."]):
            raise ValueError("Printed schedule differs from complete fresh source records")
    for name in _REPORTS:
        evidence = review["reports"][name]
        expected = ["Report status: " + evidence["status"], "Fresh evidence: " + _json(evidence["evidence"]).decode("utf-8")]
        if not _inventory_words_match("\n".join(body[("report", name)]), expected):
            raise ValueError("Printed report omits or changes fresh verdict, source ID or witness")
    identity_text = "\n".join(body[("identity", None)])
    if any(f"Component {name}: {digest}" not in identity_text for name, digest in review["identity"]["component_sha256"].items()):
        raise ValueError("Printed source component identity is incomplete")
    appendix = _contract_value(contract, "check_appendix", None)
    if appendix is not None:
        appendix(review, body)
    expected_paths = {(role, identifier): (points, closed) for role, identifier, points, closed in source_paths}
    if set(observed) != set(expected_paths):
        raise ValueError("Actual painted source contours or resolved pressure IDs are incomplete")
    actual = {}
    for key, (points, closed) in expected_paths.items():
        paths = observed[key]
        if len(paths) != 1 or paths[0][1] != closed:
            raise ValueError("Actual source path count or closure differs")
        actual_points = paths[0][0]
        source = Polygon(points) if closed else LineString(points)
        geometry = Polygon(actual_points) if closed else LineString(actual_points)
        if not geometry.is_valid or geometry.is_empty or len(actual_points) != len(points) or \
                any(math.dist(a, b) > .001 for a, b in zip(actual_points, points)) or geometry.hausdorff_distance(source) > .001:
            raise ValueError("Actual painted source corners or portal segment differ")
        actual[key[0] + ":" + key[1]] = {"vertices": len(actual_points), "area_mm2": geometry.area if closed else None,
            "length_mm": geometry.length}
    return {"page_count": len(rows), "pdf_operations": total_operations, "source_paths": actual}


def _validate(source, destination, limits, *, verify_saved=True, contract=None):
    report = _base_qa(contract)
    try:
        _valid_limits(limits)
        original, review = _current_review(source, limits, contract)
        root = Path(destination)
        if not root.is_dir() or root.is_symlink():
            raise ValueError("Process documents require an actual directory")
        root = root.resolve()
        inventory = _INVENTORY if verify_saved else _INVENTORY - {"qa.json"}
        if {child.name for child in root.iterdir()} != inventory:
            raise ValueError("Process document inventory has missing or undeclared files")
        contents, total = {}, 0
        for name in sorted(inventory):
            path = root / name
            if path.is_symlink() or not path.resolve().is_relative_to(root):
                raise ValueError("Process document artifact is linked or escapes its directory")
            maximum = limits.max_pdf_bytes if name == "documentation.pdf" else limits.max_input_bytes if name == "source.json" else \
                limits.max_report_bytes if name == "review.json" else limits.max_metadata_bytes
            contents[name] = _read_bytes(path, maximum)
            total += len(contents[name])
        if total > limits.max_total_bytes or contents["source.json"] != original:
            raise ValueError("Package total exceeds its bound or original source bytes differ")
        saved = _read_json(contents["review.json"], limits.max_report_bytes, limits)
        if not _same(saved, review):
            raise ValueError("Saved review differs from freshly recomputed original source evidence")
        index = _read_json(contents["index.json"], limits.max_metadata_bytes, limits)
        keys = {"format", "version", "status", "ok", "review_status", "identity", "review_source", "files", "pages", "projection",
                "full_facility_validation", "engineering_acceptance", "authenticated_approval", "scope", "limits"}
        extras = _contract_value(contract, "extra_index", {})
        keys.update(extras)
        if type(index) is not dict or set(index) != keys:
            raise ValueError("Process document index has missing or unknown fields")
        declared = index["limits"]
        if type(declared) is not dict or set(declared) != set(asdict(limits)):
            raise ValueError("Process document declared limits are not closed")
        declared = ProcessDocumentationLimits(**declared)
        _valid_limits(declared)
        effective = ProcessDocumentationLimits(**{name: min(value, getattr(declared, name)) for name, value in asdict(limits).items()})
        _valid_limits(effective)
        expected = {"format": _FORMAT, "version": _contract_value(contract, "version", "0.1"), "status": "DRAFT", "ok": False,
            "review_status": review["status"], "identity": review["identity"], "review_source": review["review_source"],
            "full_facility_validation": {"status": "UNKNOWN", "ok": False}, "engineering_acceptance": "PENDING",
            "authenticated_approval": "UNKNOWN", "scope": _contract_value(contract, "scope",
                "Synchronized supplied process appendix and literal source overlays"),
            "projection": _document_projection(review, effective, contract), **extras}
        if not _same({key: index[key] for key in expected}, expected) or type(index["files"]) is not dict or set(index["files"]) != set(_FILES):
            raise ValueError("Process document index identity, source or acceptance scope changed")
        for kind, name in _FILES.items():
            if not _same(index["files"][kind], {"file": name, "sha256": _sha(contents[name])}):
                raise ValueError("Actual source, review or PDF bytes differ from the closed index")
        if total > effective.max_total_bytes:
            raise ValueError("Package exceeds its declared total byte bound")
        for name, data in contents.items():
            maximum = effective.max_pdf_bytes if name == "documentation.pdf" else effective.max_input_bytes if name == "source.json" else \
                effective.max_report_bytes if name == "review.json" else effective.max_metadata_bytes
            if len(data) > maximum:
                raise ValueError("Artifact exceeds its declared byte bound")
        # Declared tightening applies to fresh evidence as well as artifact bytes.
        if not _same(asdict(effective), asdict(limits)):
            current_bytes, current_review = _current_review(source, effective, contract)
            if current_bytes != original or not _same(current_review, review):
                raise ValueError("Process source or applicable admitted evidence changed during delivery QA")
        actual = _check_pdf(contents["documentation.pdf"], index, review, effective, contract)
        if _read_bytes(source, limits.max_input_bytes) != original or any(_read_bytes(root / name,
                len(data)) != data for name, data in contents.items()):
            raise ValueError("Source or persisted artifact changed during independent readback")
        report.update(status="PASS", ok=True, review_status=review["status"], identity=review["identity"],
            review_source=review["review_source"], index_sha256=_sha(contents["index.json"]), actual=actual)
        if verify_saved and not _same(_read_json(contents["qa.json"], limits.max_metadata_bytes, limits), report):
            raise ValueError("Saved process-document QA differs from fresh artifact readback")
    except Exception as exc:
        report.update(status="FAIL", ok=False)
        report["issues"].append({"code": "PROCESS_DOCUMENT_READBACK", "status": "FAIL", "message": str(exc)[:1024]})
    return report


def validate_process_documentation(source_path, destination, *, limits=ProcessDocumentationLimits()):
    """Recompute source evidence and inspect actual delivered text and vectors."""
    return _validate(source_path, destination, limits)


def _validate_delivery(source, destination, limits, *, verify_saved=True, contract=None):
    callback = _contract_value(contract, "delivery_validator", None)
    if callback is None:
        if verify_saved:
            return _validate(source, destination, limits, contract=contract)
        return _validate(source, destination, limits, verify_saved=False, contract=contract)
    return callback(source, destination, limits, verify_saved=verify_saved)


def _export(source_path, destination, limits, *, contract=None, publish_directory=None):
    """Publish a new DRAFT package only after fresh source and artifact readback.

    Existing destinations are preserved, including an alias of the source. The
    native final rename never replaces a directory that appeared during work.
    """
    _valid_limits(limits)
    source, target = Path(source_path), Path(destination).absolute()
    if target.exists() or target.is_symlink():
        raise FileExistsError("Process-document destination already exists")
    original, review = _current_review(source, limits, contract)
    pdf, pages, projection = _render(review, limits, contract)
    if _read_bytes(source, limits.max_input_bytes) != original:
        raise ValueError("Original process source changed while rendering")
    review_data = _json(review)
    index = {"format": _FORMAT, "version": _contract_value(contract, "version", "0.1"), "status": "DRAFT", "ok": False,
        "review_status": review["status"], "identity": review["identity"], "review_source": review["review_source"],
        "files": {kind: {"file": name, "sha256": _sha(data)} for (kind, name), data in zip(_FILES.items(), (original, review_data, pdf))},
        "pages": pages, "projection": projection, "full_facility_validation": {"status": "UNKNOWN", "ok": False},
        "engineering_acceptance": "PENDING", "authenticated_approval": "UNKNOWN",
        "scope": _contract_value(contract, "scope", "Synchronized supplied process appendix and literal source overlays"),
        "limits": asdict(limits), **_contract_value(contract, "extra_index", {})}
    index_data = _json(index)
    if len(index_data) > limits.max_metadata_bytes or sum(map(len, (original, review_data, pdf, index_data))) > limits.max_total_bytes:
        raise ValueError("Process document staged inventory exceeds its byte bound")
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".process-documents-", dir=target.parent) as temporary:
        stage = Path(temporary) / "documents"
        stage.mkdir()
        for name, data in (("source.json", original), ("review.json", review_data), ("documentation.pdf", pdf), ("index.json", index_data)):
            (stage / name).write_bytes(data)
        qa = _validate_delivery(source, stage, limits, verify_saved=False, contract=contract)
        if not qa["ok"]:
            raise ValueError("Staged process documents failed independent QA: " + qa["issues"][0]["message"])
        qa_data = _json(qa)
        if len(qa_data) > limits.max_metadata_bytes or sum(map(len, (original, review_data, pdf, index_data, qa_data))) > limits.max_total_bytes:
            raise ValueError("Process document final inventory exceeds its byte bound")
        (stage / "qa.json").write_bytes(qa_data)
        final = _validate_delivery(source, stage, limits, contract=contract)
        if not final["ok"] or _read_bytes(source, limits.max_input_bytes) != original:
            raise ValueError("Original process source or saved QA changed before publication")
        (_publish_directory if publish_directory is None else publish_directory)(stage, target)
    final = _validate_delivery(source, target, limits, contract=contract)
    if not final["ok"]:
        raise ValueError("Published DRAFT failed final current-source readback: " + final["issues"][0]["message"])
    return _strict_json(index_data.decode("utf-8"))


def export_process_documentation(source_path, destination, *, limits=ProcessDocumentationLimits()):
    """Publish the original version 0.1 five-file source-bound DRAFT contract."""
    return _export(source_path, destination, limits)

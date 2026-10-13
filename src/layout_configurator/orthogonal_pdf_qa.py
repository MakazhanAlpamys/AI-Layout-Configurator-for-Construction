"""Independent actual-path readback for the bounded orthogonal PDF draft.

The checker reconstructs painted vectors through effective PDF transforms and
compares them to canonical source geometry. It does not dispatch facility QA.
"""

import base64
import copy
import hashlib
import io
import math
from pathlib import Path
import zlib
from dataclasses import asdict

from pypdf import PdfReader
from pypdf.errors import PdfReadError
from pypdf.generic import ArrayObject, ContentStream, DecodedStreamObject, DictionaryObject, IndirectObject, StreamObject
from shapely.geometry import LineString, Polygon, box
from shapely.ops import unary_union

from .equipment import clearance_rect
from .orthogonal import OrthogonalPolygon
from .orthogonal_pdf import INK, PAGE_HEIGHT, PAGE_WIDTH, STYLES, OrthogonalPDFLimits, _footer, _json, _legend, _prepare, _projection, _route_id, _rows


# PDF number serialization can round an otherwise equivalent coordinate frame.
# One micrometre is the readback tolerance, not a geometric design allowance.
GEOMETRY_TOLERANCE_MM = .001


class _ReadbackError(ValueError):
    def __init__(self, code, message):
        self.code = code
        super().__init__(message)


def _reject(code, message):
    raise _ReadbackError(code, message)


def _number(value):
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        _reject("PDF_VECTOR_STRUCTURE", "PDF vector arguments require actual numeric operands")
    try:
        result = float(value)
    except (ValueError, TypeError, OverflowError):
        _reject("PDF_VECTOR_STRUCTURE", "PDF vector arguments require numbers")
    if not math.isfinite(result) or abs(result) > 1_000_000_000:
        _reject("PDF_VECTOR_STRUCTURE", "PDF vector numbers are nonfinite or exceed the resource extent")
    return result


def _decode(stream, limits):
    if not isinstance(stream, StreamObject):
        _reject("PDF_RESOURCE", "PDF content/font resources must be actual streams")
    data = stream._data
    if not isinstance(data, bytes) or len(data) > limits.max_stream_bytes:
        _reject("RESOURCE_LIMIT", "Encoded PDF stream budget exceeded")
    if stream.get("/DecodeParms") is not None:
        _reject("PDF_RESOURCE", "Predictor or external decode parameters are outside this draft contract")
    filters = stream.get("/Filter", [])
    if not isinstance(filters, (list, tuple, ArrayObject)):
        filters = [filters]
    for filter_name in filters:
        if str(filter_name) == "/ASCII85Decode":
            data = base64.a85decode(data, adobe=True)
        elif str(filter_name) == "/FlateDecode":
            inflater = zlib.decompressobj()
            data = inflater.decompress(data, limits.max_stream_bytes + 1)
            if len(data) > limits.max_stream_bytes or inflater.unconsumed_tail:
                _reject("RESOURCE_LIMIT", "Decoded PDF stream budget exceeded")
            data += inflater.flush(limits.max_stream_bytes + 1 - len(data))
            if not inflater.eof or inflater.unused_data:
                _reject("PDF_RESOURCE", "Malformed compressed PDF stream")
        else:
            _reject("PDF_RESOURCE", "Unsupported PDF stream filter")
        if len(data) > limits.max_stream_bytes:
            _reject("RESOURCE_LIMIT", "Decoded PDF stream budget exceeded")
    return data


def _resources(page, limits):
    resources = page.get("/Resources", {})
    if any(resources.get(name) for name in ("/XObject", "/ExtGState", "/Pattern", "/Shading", "/Properties")):
        _reject("PDF_RESOURCE", "Raster, form, opacity, shading or external property resources are outside this vector draft")
    count, seen = 0, set()

    def visit(item, depth=0):
        nonlocal count
        if depth > 16:
            _reject("RESOURCE_LIMIT", "PDF resource nesting budget exceeded")
        if isinstance(item, IndirectObject):
            key = (item.idnum, item.generation)
            if key in seen:
                return
            seen.add(key)
            item = item.get_object()
        if isinstance(item, (DictionaryObject, ArrayObject)):
            count += 1
            if count > limits.max_resources:
                _reject("RESOURCE_LIMIT", "PDF resource object budget exceeded")
        if isinstance(item, StreamObject):
            _decode(item, limits)
        if isinstance(item, DictionaryObject):
            for name, value in item.items():
                if name not in ("/Length", "/Parent"):
                    visit(value, depth + 1)
        elif isinstance(item, ArrayObject):
            if len(item) > 4096:
                _reject("RESOURCE_LIMIT", "PDF resource array budget exceeded")
            for child in item:
                visit(child, depth + 1)
    visit(resources)


def _compose(current, added):
    a, b, c, d, e, f = current
    g, h, i, j, k, l = added
    return (a * g + c * h, b * g + d * h, a * i + c * j, b * i + d * j,
            a * k + c * l + e, b * k + d * l + f)


def _point(x, y, matrix, projection):
    a, b, c, d, e, f = matrix
    scale, (ox, oy) = projection["scale_pt_per_mm"], projection["origin_pt"]
    return ((a * _number(x) + c * _number(y) + e - ox) / scale,
            (b * _number(x) + d * _number(y) + f - oy) / scale)


def _color(values):
    numbers = tuple(_number(value) for value in values)
    if len(numbers) != 3 or any(value < 0 or value > 1 for value in numbers):
        _reject("PDF_VECTOR_STRUCTURE", "Invalid RGB color")
    return numbers


def _same_color(first, second):
    return all(abs(a - b) < 1e-6 for a, b in zip(first, second))


def _path_stream(page, reader, limits, projection, plan):
    entries = page.get("/Contents")
    if entries is None:
        _reject("PDF_VECTOR_STRUCTURE", "PDF page has no content stream")
    if not isinstance(entries, ArrayObject):
        entries = [entries]
    if len(entries) > 16:
        _reject("RESOURCE_LIMIT", "PDF content stream count budget exceeded")
    decoded = b"\n".join(_decode(item.get_object(), limits) for item in entries)
    if len(decoded) > limits.max_stream_bytes:
        _reject("RESOURCE_LIMIT", "Combined decoded content budget exceeded")
    stream = DecodedStreamObject()
    stream.set_data(decoded)
    operations = ContentStream(stream, reader).operations
    if len(operations) > limits.max_operations:
        _reject("RESOURCE_LIMIT", "PDF operator budget exceeded")
    state = {"matrix": (1, 0, 0, 1, 0, 0), "fill": (0, 0, 0), "stroke": (0, 0, 0),
             "width": 1, "dash": (), "dash_phase": 0, "text_mode": 0, "font_size": 0,
             "text_matrix": (1, 0, 0, 1, 0, 0), "horizontal_scale": 100}
    stack, tag, paths, observed = [], None, [], {}
    stage = -1
    layers = {"room": 0, "wall": 1, "corridor": 2, "route": 2,
              "service": 3, "equipment": 3, "door": 4, "window": 4, "grid": 5}
    ignored = {b"BT", b"ET", b"TL", b"Td", b"TD", b"T*", b"Tc", b"Tw", b"J", b"j", b"M"}
    painting = {b"S", b"f", b"f*", b"B", b"B*", b"s", b"b", b"b*"}
    for values, op in operations:
        if op == b"q":
            if len(stack) >= 64:
                _reject("RESOURCE_LIMIT", "PDF graphics-state depth exceeded")
            stack.append(copy.deepcopy(state))
        elif op == b"Q":
            if not stack:
                _reject("PDF_VECTOR_STRUCTURE", "Unbalanced graphics-state restore")
            state = stack.pop()
        elif op == b"cm":
            if len(values) != 6:
                _reject("PDF_VECTOR_STRUCTURE", "Malformed PDF transform")
            state["matrix"] = _compose(state["matrix"], tuple(_number(value) for value in values))
        elif op in (b"rg", b"RG"):
            state["fill" if op == b"rg" else "stroke"] = _color(values)
        elif op in (b"g", b"G"):
            if len(values) != 1:
                _reject("PDF_VECTOR_STRUCTURE", "Malformed grayscale color")
            state["fill" if op == b"g" else "stroke"] = _color(values * 3)
        elif op == b"w":
            if len(values) != 1 or _number(values[0]) <= 0:
                _reject("PDF_VECTOR_STRUCTURE", "Invalid stroke width")
            state["width"] = _number(values[0])
        elif op == b"d":
            if len(values) != 2 or not isinstance(values[0], ArrayObject):
                _reject("PDF_VECTOR_STRUCTURE", "Malformed dash pattern")
            state["dash"] = tuple(_number(value) for value in values[0])
            state["dash_phase"] = _number(values[1])
        elif op == b"Tr":
            if len(values) != 1 or _number(values[0]) != 0:
                _reject("PDF_TEXT_IDENTITY", "Invisible or stroked identity text is outside the draft contract")
            state["text_mode"] = 0
        elif op == b"Tf":
            if (len(values) != 2 or values[0] not in page.get("/Resources", {}).get("/Font", {}) or
                    _number(values[1]) <= 0):
                _reject("PDF_TEXT_IDENTITY", "Actual source text requires a defined font with positive size")
            state["font_size"] = _number(values[1])
        elif op == b"Tm":
            if len(values) != 6:
                _reject("PDF_TEXT_IDENTITY", "Malformed actual text transform")
            state["text_matrix"] = tuple(_number(value) for value in values)
        elif op == b"Tz":
            if len(values) != 1 or _number(values[0]) <= 0:
                _reject("PDF_TEXT_IDENTITY", "Actual source text requires positive horizontal scaling")
            state["horizontal_scale"] = _number(values[0])
        elif op == b"Ts":
            if len(values) != 1 or _number(values[0]) != 0:
                _reject("PDF_TEXT_IDENTITY", "Raised source identity text is outside this draft contract")
        elif op in (b"Tj", b"TJ", b"'", b'"'):
            if not _same_color(state["fill"], INK):
                _reject("PDF_TEXT_IDENTITY", "Identity/source text must use the visible draft ink color")
            a, b, c, d, _, _ = _compose(state["matrix"], state["text_matrix"])
            font_size = state["font_size"]
            sizes = (math.hypot(a, b) * font_size * state["horizontal_scale"] / 100,
                     math.hypot(c, d) * font_size)
            if (any(not 4 <= size <= 30 for size in sizes) or
                    abs(a * d - b * c) * font_size ** 2 < 1):
                _reject("PDF_TEXT_IDENTITY", "Actual source identity text is invisible or outside readable size bounds")
        elif op == b"BDC":
            if not plan or tag is not None or paths or len(values) != 2 or str(values[0]) != "/FLC":
                _reject("PDF_VECTOR_STRUCTURE", "Invalid, nested or unexpected vector semantic group")
            props = values[1]
            if not isinstance(props, DictionaryObject) or set(props) != {"/Kind", "/Id"}:
                _reject("PDF_VECTOR_STRUCTURE", "Vector group requires explicit kind and source ID")
            kind, identifier = str(props["/Kind"])[1:], str(props["/Id"])
            if kind not in STYLES or not identifier or len(identifier) > 1024:
                _reject("PDF_VECTOR_STRUCTURE", "Unknown or malformed vector source role")
            if layers[kind] < stage:
                _reject("PDF_VECTOR_STRUCTURE", "Actual source vectors are hidden by a later background fill")
            stage = layers[kind]
            tag = (kind, identifier)
        elif op == b"EMC":
            if tag is None or paths or tag not in observed:
                _reject("PDF_VECTOR_STRUCTURE", "Vector group is missing actual painted geometry")
            tag = None
        elif op in (b"m", b"l"):
            if tag is None or len(values) != 2 or op == b"l" and (not paths or paths[-1][1]):
                _reject("PDF_VECTOR_STRUCTURE", "Vector coordinates are outside a valid source path")
            point = _point(*values, state["matrix"], projection)
            if op == b"m":
                paths.append(([point], False))
            else:
                paths[-1][0].append(point)
        elif op == b"h":
            if not paths or paths[-1][1]:
                _reject("PDF_VECTOR_STRUCTURE", "Malformed closed contour")
            paths[-1] = (paths[-1][0], True)
        elif op == b"re":
            if tag is None or len(values) != 4:
                _reject("PDF_VECTOR_STRUCTURE", "Rectangle is outside a tagged source path")
            x, y, w, h = (_number(value) for value in values)
            paths.append(([_point(px, py, state["matrix"], projection) for px, py in
                           ((x, y), (x + w, y), (x + w, y + h), (x, y + h))], True))
        elif op in painting:
            if tag is None or not paths or tag in observed:
                _reject("PDF_VECTOR_STRUCTURE", "Unmarked, duplicate or empty painted geometry")
            style = STYLES[tag[0]]
            if op.decode("ascii") != style["paint"]:
                _reject("PDF_VECTOR_STRUCTURE", "Paint operation does not preserve the intended filled corridor/contour role")
            if "f" in style["paint"] or "B" in style["paint"]:
                if not _same_color(state["fill"], style["fill"]):
                    _reject("PDF_VECTOR_STRUCTURE", "Actual filled geometry is hidden or has an unexpected style")
            if style["paint"] in ("S", "B*"):
                a, b, c, d, _, _ = state["matrix"]
                physical_width = state["width"] * max(math.hypot(a, b), math.hypot(c, d))
                expected_width = 1.4 if tag[0] in ("door", "window") else .8 if tag[0] == "grid" else .7
                if not _same_color(state["stroke"], style["stroke"]) or abs(physical_width - expected_width) > .002:
                    _reject("PDF_VECTOR_STRUCTURE", "Actual source stroke is invisible or obscures the projection")
            if bool(state["dash"]) != (tag[0] in ("service", "grid")):
                _reject("PDF_VECTOR_STRUCTURE", "Directional service contour must retain its distinct dash style")
            if tag[0] == "grid":
                a, b, c, d, _, _ = state["matrix"]
                first, second = math.hypot(a, b), math.hypot(c, d)
                dash_scale = max(first, second)
                if (min(first, second) <= 1e-12 or abs(first - second) > dash_scale * 1e-7
                        or abs(a * c + b * d) > dash_scale ** 2 * 1e-7):
                    _reject("PDF_VECTOR_STRUCTURE", "Reference-axis strokes require a nondegenerate uniform similarity transform")
                if (len(state["dash"]) != 2 or abs(state["dash_phase"]) > .001
                        or any(abs(value * dash_scale - wanted) > .002
                               for value, wanted in zip(state["dash"], (4, 2)))):
                    _reject("PDF_VECTOR_STRUCTURE", "Actual reference axes require the declared visible dash pattern")
            observed[tag] = paths
            paths = []
        elif op == b"n":
            if paths:
                _reject("PDF_VECTOR_STRUCTURE", "Source paths were discarded without painting")
        elif op not in ignored:
            _reject("PDF_VECTOR_STRUCTURE", f"Unsupported clipping, curve, image or operator {op!r}")
    if stack or tag is not None or paths:
        _reject("PDF_VECTOR_STRUCTURE", "PDF vector graphics state or contours are incomplete")
    return observed


def _polygon(paths):
    material = Polygon()
    for points, closed in paths:
        if not closed or len(points) < 4:
            _reject("PATH_GEOMETRY", "Actual polygon requires closed contours with at least four corners")
        ring = Polygon(points)
        if not ring.is_valid or ring.is_empty:
            _reject("PATH_GEOMETRY", "Actual painted contour is invalid")
        material = material.symmetric_difference(ring)
    return material


def _matches(actual, expected):
    return (actual.is_valid and actual.hausdorff_distance(expected) <= GEOMETRY_TOLERANCE_MM and
            actual.symmetric_difference(expected).area <=
            max(.001, expected.length * GEOMETRY_TOLERANCE_MM * 2))


def _check_vectors(observed, building, layout, walls, equipment, routing, *, include_windows=False, grid_capability=None):
    expected = {}
    for identifier, shape in layout.placements.items():
        expected[("room", identifier)] = shape.geometry if isinstance(shape, OrthogonalPolygon) else box(shape.x, shape.y, shape.right, shape.top)
    expected[("wall", "walls")] = walls.geometry
    for opening in walls.openings:
        expected[("door", opening.id)] = opening.centerline
    for window in walls.windows:
        expected[("window", window.id)] = window.centerline
    for axis in grid_capability["axes"] if grid_capability is not None else ():
        expected[("grid", axis["id"])] = LineString((axis["start"], axis["end"]))
    specifications = {item.id: item for item in building.equipment}
    for placement in equipment.placements if equipment else ():
        r = placement.rect
        expected[("equipment", placement.equipment_id)] = box(r.x, r.y, r.right, r.top)
        r = clearance_rect(specifications[placement.equipment_id], placement.rect, placement.rotated)
        expected[("service", placement.equipment_id)] = box(r.x, r.y, r.right, r.top)
    for route in routing.routes if routing else ():
        identifier, half = _route_id(route, routing), route.minimum_clear_width_mm / 2
        if len(route.points) == 1:
            x, y = route.points[0]
            swept = box(x - half, y - half, x + half, y + half)
        else:
            swept = unary_union([box(min(a[0], b[0]) - half, min(a[1], b[1]) - half,
                                   max(a[0], b[0]) + half, max(a[1], b[1]) + half)
                                for a, b in zip(route.points, route.points[1:])])
            expected[("route", identifier)] = LineString(route.points)
        expected[("corridor", identifier)] = swept
    if set(observed) != set(expected):
        _reject("PATH_IDENTITY", "Actual painted source geometry IDs are missing, duplicated or unexpected")
    actual = {"room_areas_mm2": {}, "room_vertex_counts": {}, "wall_area_mm2": 0,
              "door_widths_mm": {}, "service_areas_mm2": {}, "corridor_areas_mm2": {}}
    if include_windows:
        actual["window_widths_mm"] = {}
    if grid_capability is not None:
        actual["grid_axes_mm"] = {}
    for (kind, identifier), source in expected.items():
        paths = observed[(kind, identifier)]
        if kind in ("door", "window", "route", "grid"):
            if len(paths) != 1 or paths[0][1] or len(paths[0][0]) < 2:
                _reject("PATH_GEOMETRY", "Actual door/route requires one open source polyline")
            reconstructed = LineString(paths[0][0])
            if reconstructed.hausdorff_distance(source) > GEOMETRY_TOLERANCE_MM or abs(reconstructed.length - source.length) > GEOMETRY_TOLERANCE_MM:
                _reject("PATH_GEOMETRY", f"Actual {kind} geometry differs from canonical source {identifier}")
            if kind == "route" and (len(paths[0][0]) != len(source.coords) or
                    any(math.dist(a, b) > GEOMETRY_TOLERANCE_MM for a, b in zip(paths[0][0], source.coords))):
                _reject("PATH_GEOMETRY", "Actual route order/endpoints differ from the supplied checked route")
            if kind == "door":
                actual["door_widths_mm"][identifier] = round(reconstructed.length, 6)
            elif kind == "window":
                actual["window_widths_mm"][identifier] = round(reconstructed.length, 6)
            elif kind == "grid":
                actual["grid_axes_mm"][identifier] = [[round(value, 6) for value in point] for point in reconstructed.coords]
        else:
            reconstructed = _polygon(paths)
            if not _matches(reconstructed, source):
                _reject("PATH_GEOMETRY", f"Actual {kind} contour/material differs from canonical source {identifier}")
            if kind == "room":
                actual["room_areas_mm2"][identifier] = round(reconstructed.area, 6)
                actual["room_vertex_counts"][identifier] = sum(len(points) for points, _ in paths)
            elif kind == "wall":
                actual["wall_area_mm2"] = reconstructed.area
            elif kind == "service":
                actual["service_areas_mm2"][identifier] = reconstructed.area
            elif kind == "corridor":
                actual["corridor_areas_mm2"][identifier] = reconstructed.area
    return actual


def _inventory_words_match(actual, rows):
    """Retain label word boundaries while allowing long words to wrap lines."""
    position = 0
    for word in (word for row in rows for word in row.split()):
        while position < len(actual) and actual[position].isspace():
            position += 1
        for character in word:
            # The renderer may split an oversized word across rows or pages;
            # a space inside an identifier/word cannot be silently removed.
            while position < len(actual) and actual[position] in '\r\n':
                position += 1
            if position >= len(actual) or actual[position] != character:
                return False
            position += 1
        if position < len(actual) and not actual[position].isspace():
            return False
    return not actual[position:].strip()


def validate_orthogonal_pdf(path, building, layout, equipment=None, *, routing=None,
                            revision="P01", index=None, limits=OrthogonalPDFLimits(), include_windows=False, include_grid=False):
    """Read actual painted vectors; never accept a stored status/geometry report."""
    report = {"ok": False, "status": "FAIL", "scope": "Actual source identity and painted vector geometry only",
        "full_facility_validation": {"status": "UNKNOWN", "ok": False}, "engineering_acceptance": "PENDING",
        "issues": [], "actual": {}}
    try:
        building, layout, walls, local, source = _prepare(building, layout, equipment, routing, limits,
            include_windows=include_windows, include_grid=include_grid)
        target = Path(path)
        if target.stat().st_size > limits.max_pdf_bytes:
            _reject("RESOURCE_LIMIT", "PDF byte budget exceeded")
        data = target.read_bytes()
        expected = {"format": "FLC-orthogonal-pdf-draft", "version": "0.1", "pdf": target.name,
            "status": "DRAFT", "ok": False, "units": "mm", "source_variant": layout.variant,
            "project_name": building.layout.project_name, "revision": revision, "source": source,
            "sha256": hashlib.sha256(data).hexdigest(), "projection": _projection(building), "local_checks": local,
            "components_supplied": {"equipment": equipment is not None, "routing": routing is not None},
            "full_facility_validation": {"status": "UNKNOWN", "ok": False}, "engineering_acceptance": "PENDING"}
        if include_windows:
            expected["window_capability"] = local["window_capability"]
        if include_grid:
            expected["grid_capability"] = local["grid_capability"]
        if not isinstance(index, dict) or len(_json(index)) > limits.max_source_bytes:
            _reject("INDEX_IDENTITY", "Actual artifact index is malformed or exceeds the source budget")
        if (set(index) != {*expected, "pages", "limits"} or
                _json({key: index.get(key) for key in expected}) != _json(expected) or
                _json(index["limits"]) != _json(asdict(limits))):
            _reject("INDEX_IDENTITY", "Artifact hash, draft status or canonical source identity differs")
        pages = index["pages"]
        if not isinstance(pages, list) or not 2 <= len(pages) <= limits.max_pages:
            _reject("INDEX_IDENTITY", "Bounded plan and source-inventory page index is required")
        for number, entry in enumerate(pages, 1):
            if _json(entry) != _json({"page_id": f"OP-{number:03d}", "page_number": number,
                         "kind": "plan" if number == 1 else "source_inventory", "status": "DRAFT"}):
                _reject("INDEX_IDENTITY", "Page identity, order or draft status differs")
        reader = PdfReader(io.BytesIO(data), strict=True)
        if reader.is_encrypted or len(reader.pages) != len(pages):
            _reject("PDF_PAGE_IDENTITY", "PDF page count or encrypted content cannot satisfy the draft index")
        observed = None
        text_inventory = ""
        opted_inventory = ""
        opted_inventory_text = ""
        for number, page in enumerate(reader.pages, 1):
            if (page.get("/Rotate", 0) != 0 or page.get("/UserUnit", 1) != 1 or page.get("/Annots") or
                    any(abs(float(actual) - wanted) > .001 for actual, wanted in
                        zip(page.mediabox, (0, 0, PAGE_WIDTH, PAGE_HEIGHT))) or list(page.cropbox) != list(page.mediabox)):
                _reject("PDF_PAGE_IDENTITY", "Actual page transform, clipping box or annotations differ")
            _resources(page, limits)
            vectors = _path_stream(page, reader, limits, expected["projection"], number == 1)
            if number == 1:
                observed = vectors
            elif vectors:
                _reject("PDF_VECTOR_STRUCTURE", "Source inventory continuation contains unexpected geometry")
            def visible(text, cm, tm, font, size):
                if text.strip():
                    x, y = tm[4] * cm[0] + tm[5] * cm[2] + cm[4], tm[4] * cm[1] + tm[5] * cm[3] + cm[5]
                    if not math.isfinite(x) or not math.isfinite(y) or not 0 <= x <= PAGE_WIDTH or not 0 <= y <= PAGE_HEIGHT:
                        _reject("PDF_TEXT_IDENTITY", "Actual source identity text is outside the visible page")
            text = page.extract_text(visitor_text=visible)
            if len(text) > limits.max_text_chars:
                _reject("RESOURCE_LIMIT", "Extracted PDF text budget exceeded")
            compact = "".join(text.split())
            claims = [building.layout.project_name, f"DRAFT | Variant {layout.variant} | Revision {revision} | Page OP-{number:03d} ({number}/{len(pages)})",
                      "FULL FACILITY: UNKNOWN | ENGINEERING ACCEPTANCE: PENDING",
                      *[f"{name}: {digest}" for name, digest in source.items()]]
            if any("".join(claim.split()) not in compact for claim in claims) or "FULLFACILITY:PASS" in compact:
                _reject("PDF_TEXT_IDENTITY", "Actual printed draft status, source hashes or page identity differ")
            if include_windows or include_grid:
                footer = _footer(include_windows=include_windows, include_grid=include_grid)
                header = "".join("".join(claim.split()) for claim in (*claims, footer))
                if not compact.startswith(header):
                    _reject("PDF_TEXT_IDENTITY", "Opted source header order or scope differs")
                body = compact[len(header):]
                if number == 1:
                    plan = [*[f"R{i:02d}" for i in range(1, len(layout.placements) + 1)],
                            *_legend(local, include_windows=include_windows, include_grid=include_grid)]
                    expected_plan = "".join("".join(line.split()) for line in plan)
                    if body != expected_plan:
                        _reject("PDF_TEXT_IDENTITY", "Actual plan text has missing, contradictory or unexpected source/status claims")
                if ("ENGINEERINGACCEPTANCE:" in body or "STRUCTURALENGINEERING:PASS" in body
                        or number == 1 and ("|host" in body or "|boundaryreference" in body)):
                    _reject("PDF_TEXT_IDENTITY", "Unexpected source inventory or engineering acceptance claim")
                if number > 1:
                    opted_inventory += body
                    consumed = 0
                    for position, character in enumerate(text):
                        consumed += not character.isspace()
                        if consumed == len(header):
                            opted_inventory_text += text[position + 1:]
                            break
            text_inventory += compact
        if include_windows or include_grid:
            rows = _rows(building, layout, walls, equipment, routing, grid_capability=local.get("grid_capability"))
            expected_inventory = "".join("".join(row.split()) for row in rows)
            if opted_inventory != expected_inventory:
                _reject("PDF_TEXT_IDENTITY", "Actual source inventory has missing, duplicate, conflicting or unexpected entries")
            if not _inventory_words_match(opted_inventory_text, rows):
                _reject("PDF_TEXT_IDENTITY", "Actual source label/identifier word boundaries differ from the printed inventory")
        if any("".join(identifier.split()) not in text_inventory for identifier in
               [*layout.placements, *(item.equipment_id for item in (equipment.placements if equipment else ())),
                *(route.flow_id for route in (routing.routes if routing else ()))]):
            _reject("PDF_TEXT_IDENTITY", "Canonical source identifiers are missing from the printed inventory")
        for window in walls.windows:
            claim = (f"Window {window.id} | host {window.room_id} | orientation {window.orientation} | "
                     f"source width {window.width:g} mm | source sill {building.layout.window_sill_mm:g} mm | "
                     f"height {building.layout.window_height_mm:g} mm | closed facade barrier")
            if text_inventory.count("".join(claim.split())) != 1:
                _reject("PDF_TEXT_IDENTITY", "Actual printed source window identity, width, sill or height differs")
        report["actual"] = _check_vectors(observed, building, layout, walls, equipment, routing,
            include_windows=include_windows, grid_capability=local.get("grid_capability"))
        if include_windows:
            report["window_capability"] = local["window_capability"]
        if include_grid:
            report["grid_capability"] = local["grid_capability"]
        report.update(ok=True, status="PASS")
    except _ReadbackError as exc:
        report["issues"].append({"code": exc.code, "message": str(exc)})
    except (OSError, ValueError, TypeError, KeyError, AttributeError, OverflowError, zlib.error, PdfReadError) as exc:
        report["issues"].append({"code": "SOURCE_OR_PDF_INVALID", "message": str(exc)})
    return report

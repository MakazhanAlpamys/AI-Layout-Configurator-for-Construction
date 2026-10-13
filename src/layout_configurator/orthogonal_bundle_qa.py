"""Independent persisted-source and physical exchange checks for DRAFT bundles."""

from dataclasses import asdict
import io
import math
from pathlib import Path
import re
import tempfile

from shapely.geometry import LineString, Polygon, box
from shapely.ops import unary_union

from .equipment import clearance_rect
from .models import Rect
from .orthogonal_bundle import (ARTIFACT_FILES, FORMAT, FULL_UNKNOWN, INDEX_FILES, INVENTORY,
    SOURCE_FILES, OrthogonalBundleLimits, _hash, _json, _keys, _projection_scope, _read_json, _route_key, _sha, _source_frames,
    _grid_capability, _grid_options, _group_capability, _group_options, _window_capability, _window_options)
from .orthogonal_facility import validate_orthogonal_building
from .orthogonal_ifc_qa import validate_orthogonal_ifc
from .orthogonal_pdf_qa import validate_orthogonal_pdf
from .orthogonal_projection import build_orthogonal_wall_plan


def _identity(building, layout, profile, sources):
    return {"schema": FORMAT + "/0.1", "status": "DRAFT", "full_facility_validation": "UNKNOWN",
        "engineering_acceptance": "PENDING", "project_name": building.layout.project_name,
        "variant": str(layout.variant), "revision": profile.drawing.revision,
        **{name + "_sha256": _hash(value) for name, value in sources.items()}}


def _same(actual, expected):
    return _json(actual) == _json(expected)


def _digest(value):
    if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None:
        raise ValueError("SHA256 requires exactly 64 lowercase hexadecimal characters")
    return value


def _finish(report):
    statuses = {row["status"] for row in report["checks"]}
    report["status"] = "FAIL" if "FAIL" in statuses else "UNKNOWN" if "UNKNOWN" in statuses else "PASS"
    report["ok"] = report["status"] == "PASS"
    return report


def _check(report, identifier, status, evidence):
    report["checks"].append({"id": identifier, "status": status, "evidence": evidence})


def _tags(entity, app):
    try:
        raw = entity.get_xdata(app)
    except Exception as exc:
        raise ValueError("DXF physical geometry lacks required semantic XDATA") from exc
    values, numbers = {}, []
    for tag in raw:
        if tag.code == 1000:
            if not isinstance(tag.value, str) or "=" not in tag.value:
                raise ValueError("Malformed DXF semantic XDATA text")
            key, value = tag.value.split("=", 1)
            if key in values:
                raise ValueError("Duplicate DXF semantic XDATA key")
            values[key] = value
        elif tag.code == 1040:
            numbers.append(tag.value)
        else:
            raise ValueError("Unsupported DXF semantic XDATA value")
    return values, numbers


def _point(point):
    values = tuple(point)
    if len(values) != 3 or any(not math.isfinite(value) for value in values) or abs(values[2]) > 0.00001:
        raise ValueError("DXF geometry must have finite source-plane WCS coordinates")
    return values[:2]


def _points(entity, closed):
    if entity.dxftype() != "LWPOLYLINE" or bool(entity.closed) != closed:
        raise ValueError("DXF contour has an unsupported or incorrect closure")
    if entity.dxf.get("const_width", 0) != 0 or any(any(value != 0 for value in point[2:]) for point in entity.get_points("xyseb")):
        raise ValueError("DXF contours require straight zero-width segments without bulges")
    return [_point(point) for point in entity.vertices_in_wcs()]


def _polygon(entity):
    points = _points(entity, True)
    if not 3 <= len(points) <= 4096:
        raise ValueError("DXF ring vertex budget or minimum exceeded")
    result = Polygon(points)
    if not result.is_valid or result.is_empty or result.area <= 0:
        raise ValueError("DXF physical ring is invalid or empty")
    return result


def _parity(rings):
    if not rings:
        raise ValueError("DXF physical material has no rings")
    result = rings[0]
    for ring in rings[1:]:
        result = result.symmetric_difference(ring)
    if not result.is_valid or result.is_empty:
        raise ValueError("DXF physical material is invalid or empty")
    return result


def _geometry(actual, expected, title):
    if actual.symmetric_difference(expected).area > 0.0001 or actual.hausdorff_distance(expected) > 0.00001:
        raise ValueError(title + " actual physical geometry differs from canonical source")


def _rect(rect):
    return box(rect.x, rect.y, rect.right, rect.top)


def _route_material(route):
    # Independent full-width square-ended axis-aligned segment union, without
    # using the DXF writer's projection helper or bounding the complete path.
    half = route.minimum_clear_width_mm / 2
    pairs = list(zip(route.points, route.points[1:])) or [(route.points[0], route.points[0])]
    return unary_union([box(min(a[0], b[0]) - half, min(a[1], b[1]) - half,
        max(a[0], b[0]) + half, max(a[1], b[1]) + half) for a, b in pairs])


def _dxf_readback(data, building, layout, equipment, routing, identity, limits, *, include_windows=False, include_grid=False):
    report = {"status": "FAIL", "ok": False, "scope": "Independent actual WCS orthogonal DXF geometry",
              "issues": [], "counts": {}, "tolerance": {"coordinates_mm": 0.00001, "material_area_mm2": 0.0001}}
    try:
        import ezdxf
        # Match text-file universal newline decoding on Windows and Linux.
        document = ezdxf.read(io.StringIO(data.decode("utf-8"), newline=None))
        if document.header.get("$INSUNITS") != 4 or _point(document.header.get("$INSBASE", (0, 0, 0))) != (0, 0):
            raise ValueError("DXF requires millimetres and zero source insertion base")
        entities = list(document.modelspace())
        if len(entities) > limits.max_dxf_entities:
            raise ValueError("DXF model-space entity budget exceeded")
        room_shapes = {name: _rect(shape) if isinstance(shape, Rect) else shape.geometry for name, shape in layout.placements.items()}
        walls = build_orthogonal_wall_plan(building.layout, layout, **_window_options(include_windows))
        capability = _window_capability(building, layout, include_windows)
        grid_capability = _grid_capability(building, include_grid)
        grid_axes = {axis["id"]: axis for axis in grid_capability["axes"]} if include_grid else {}
        grid_labels = {(f"Grid {axis['id']} | label {axis['label']} | direction {axis['direction']} | "
            f"coordinate {axis['coordinate_mm']:g} mm | boundary reference"):
            (building.layout.boundary.width_mm + 500,
             building.layout.boundary.height_mm - 200 * ((len(walls.windows) if include_windows else 0) + position))
            for position, axis in enumerate(grid_axes.values())}
        openings = {item.id: item for item in walls.openings}
        windows = {item.id: item for item in walls.windows}
        window_labels = {(f"Window {window.id} | host {window.room_id} | width {window.width:g} mm | "
            f"sill {building.layout.window_sill_mm:g} mm | height {building.layout.window_height_mm:g} mm"):
            (building.layout.boundary.width_mm + 500, building.layout.boundary.height_mm - 200 * position)
            for position, window in enumerate(walls.windows)}
        specs = {item.id: item for item in building.equipment}
        eq = {item.equipment_id: item for item in equipment.placements} if equipment else {}
        routes = {_route_key(item): item for item in routing.routes} if routing else {}
        rooms, doors, physical, service, corridors, axes, wall_rings, markers, titles = {}, {}, {}, {}, {}, {}, [], [], []
        actual_windows = {}
        actual_window_labels = set()
        actual_grid_axes, actual_grid_labels = {}, set()
        allowed = {"A-ROOM": "LWPOLYLINE", "A-WALL": "LWPOLYLINE", "A-DOOR": "LINE", "A-TEXT": "TEXT",
            "A-TITLE": ("POINT", "TEXT"), "A-EQUIP": "LWPOLYLINE", "A-EQUIP-CLEAR": "LWPOLYLINE",
            "A-FLOW-WIDTH": "LWPOLYLINE", "A-FLOW": "LWPOLYLINE"}
        if include_windows:
            allowed["A-WINDOW"] = "LINE"
            allowed["A-WINDOW-TEXT"] = "TEXT"
        if include_grid:
            allowed["A-GRID"] = "LINE"
            allowed["A-GRID-TEXT"] = "TEXT"
        for entity in entities:
            layer = entity.dxf.layer
            types = allowed.get(layer, ())
            if entity.dxftype() not in ((types,) if isinstance(types, str) else types):
                raise ValueError("DXF contains unsupported or unindexed physical entities/layers")
            settings = document.layers.get(layer)
            if (settings.is_off() or settings.is_frozen() or layer != "A-ROOM" and not settings.dxf.get("plot", 1)
                    or entity.dxf.get("invisible", 0) or entity.dxf.get("thickness", 0) != 0
                    or entity.transparency != 0 or entity.dxf.get("linetype", "BYLAYER") != "BYLAYER"
                    or settings.dxf.linetype != "Continuous"):
                raise ValueError("DXF source geometry is hidden, extruded or has an unsupported display style")
            if layer == "A-WALL":
                wall_rings.append(_polygon(entity))
            elif layer == "A-ROOM":
                tags, numeric = _tags(entity, "LAYOUT_IR")
                name = tags.get("room_id")
                expected_tags = {"room_id": name, "room_type": building.layout.room_by_id[name].type} if name in room_shapes else None
                if tags != expected_tags or numeric or name in rooms:
                    raise ValueError("DXF unexpected/duplicate room identity or type")
                rooms[name] = _polygon(entity)
                _geometry(rooms[name], room_shapes[name], "DXF room " + name)
            elif layer == "A-DOOR":
                tags, numeric = _tags(entity, "LAYOUT_IR")
                name = tags.get("door_id")
                if name not in openings or name in doors:
                    raise ValueError("DXF unexpected/duplicate door identity")
                opening = openings[name]
                if tags != {"door_id": name, "room_a": opening.room_a, "room_b": opening.room_b} or len(numeric) != 1 or abs(numeric[0] - opening.width) > 0.00001:
                    raise ValueError("DXF door scalar source properties differ")
                line = LineString((_point(entity.dxf.start), _point(entity.dxf.end)))
                if line.hausdorff_distance(opening.centerline) > 0.00001 or abs(line.length - opening.width) > 0.00001:
                    raise ValueError("DXF actual door centerline differs from canonical opening")
                doors[name] = line
            elif layer == "A-WINDOW":
                tags, numeric = _tags(entity, "LAYOUT_IR")
                name = tags.get("id")
                if name not in windows or name in actual_windows:
                    raise ValueError("DXF unexpected/duplicate source window identity")
                window = windows[name]
                expected_tags = {"kind": "window", "id": name, "room_id": window.room_id,
                                 "orientation": window.orientation}
                expected_numbers = (window.width, building.layout.window_sill_mm, building.layout.window_height_mm)
                if (tags != expected_tags or len(numeric) != 3 or
                        any(not math.isfinite(actual) or abs(actual - expected) > 0.00001
                            for actual, expected in zip(numeric, expected_numbers))):
                    raise ValueError("DXF window host, orientation, width, source sill or height differs")
                line = LineString((_point(entity.dxf.start), _point(entity.dxf.end)))
                if line.hausdorff_distance(window.centerline) > 0.00001 or abs(line.length - window.width) > 0.00001:
                    raise ValueError("DXF actual window line differs from the canonical facade opening")
                actual_windows[name] = line
            elif layer == "A-WINDOW-TEXT":
                text = entity.dxf.text
                if (text not in window_labels or text in actual_window_labels or entity.dxf.height != 100 or
                        entity.dxf.get("rotation", 0) != 0 or entity.dxf.get("width", 1) != 1 or
                        entity.dxf.get("oblique", 0) != 0 or entity.dxf.get("text_generation_flag", 0) != 0 or
                        tuple(entity.dxf.get("extrusion", (0, 0, 1))) != (0, 0, 1) or
                        _point(entity.dxf.insert) != window_labels[text]):
                    raise ValueError("DXF actual visible window inventory, source sill/height or placement differs")
                actual_window_labels.add(text)
            elif layer == "A-GRID":
                tags, numeric = _tags(entity, "LAYOUT_IR")
                name = tags.get("id")
                if name not in grid_axes or name in actual_grid_axes:
                    raise ValueError("DXF unexpected/duplicate source reference axis")
                axis = grid_axes[name]
                if (tags != {"kind": "grid-axis", "id": name, "direction": axis["direction"], "label": axis["label"]}
                        or len(numeric) != 1 or not math.isfinite(numeric[0])
                        or abs(numeric[0] - axis["coordinate_mm"]) > 0.00001):
                    raise ValueError("DXF reference axis identity, label or source coordinate differs")
                points = (_point(entity.dxf.start), _point(entity.dxf.end))
                if any(abs(actual - expected) > 0.00001
                       for point, source in zip(points, (axis["start"], axis["end"]))
                       for actual, expected in zip(point, source)):
                    raise ValueError("DXF actual reference axis must span the complete source boundary at elevation zero")
                actual_grid_axes[name] = points
            elif layer == "A-GRID-TEXT":
                text = entity.dxf.text
                if (text not in grid_labels or text in actual_grid_labels or entity.dxf.height != 100
                        or entity.dxf.get("rotation", 0) != 0 or entity.dxf.get("width", 1) != 1
                        or entity.dxf.get("oblique", 0) != 0 or entity.dxf.get("text_generation_flag", 0) != 0
                        or tuple(entity.dxf.get("extrusion", (0, 0, 1))) != (0, 0, 1)
                        or _point(entity.dxf.insert) != grid_labels[text]):
                    raise ValueError("DXF visible source reference axis inventory or placement differs")
                actual_grid_labels.add(text)
            elif layer in ("A-EQUIP", "A-EQUIP-CLEAR"):
                tags, numeric = _tags(entity, "FLC_ORTH_BUNDLE")
                name = tags.get("equipment_id")
                inventory = physical if layer == "A-EQUIP" else service
                if name not in eq or name in inventory:
                    raise ValueError("DXF unexpected/duplicate equipment identity")
                item = eq[name]
                if tags != {"equipment_id": name, "room_id": item.room_id, "rotated": str(item.rotated).lower()} or numeric:
                    raise ValueError("DXF equipment source properties differ")
                shape = item.rect if layer == "A-EQUIP" else clearance_rect(specs[name], item.rect, item.rotated)
                inventory[name] = _polygon(entity)
                _geometry(inventory[name], _rect(shape), "DXF " + layer + " " + name)
            elif layer in ("A-FLOW-WIDTH", "A-FLOW"):
                tags, numeric = _tags(entity, "FLC_ORTH_BUNDLE")
                key = tags.get("route_key")
                if key not in routes or tags != {"route_key": key, "flow_id": routes[key].flow_id} or numeric:
                    raise ValueError("DXF unexpected route source identity")
                if layer == "A-FLOW-WIDTH":
                    corridors.setdefault(key, []).append(_polygon(entity))
                else:
                    if key in axes:
                        raise ValueError("DXF duplicate route centerline")
                    axes[key] = _points(entity, False)
                    points = routes[key].points
                    if len(axes[key]) != len(points) or any(abs(a - b) > 0.00001 for pair, expected in zip(axes[key], points) for a, b in zip(pair, expected)):
                        raise ValueError("DXF actual route centerline differs from supplied source")
            elif layer == "A-TITLE" and entity.dxftype() == "POINT":
                tags, numeric = _tags(entity, "FLC_ORTH_BUNDLE")
                original, old_numbers = _tags(entity, "LAYOUT_IR")
                expected = {"schema": "orthogonal-draft/0.1", "status": "DRAFT", "units": "mm",
                    "spec_sha256": _hash(building.layout.to_dict()), "layout_sha256": identity["layout_sha256"]}
                if include_windows:
                    expected.update(window_capability=capability["policy"]["name"], window_capability_sha256=_hash(capability))
                if include_grid:
                    expected.update(grid_capability=grid_capability["policy"]["name"], grid_capability_sha256=_hash(grid_capability))
                if tags != identity or numeric or original != expected or old_numbers or _point(entity.dxf.location) != (0, 0):
                    raise ValueError("DXF source/status/revision identity marker differs")
                markers.append(entity)
            else:
                if entity.dxf.height <= 0 or not math.isfinite(entity.dxf.height):
                    raise ValueError("DXF visible labels require finite positive heights")
                _point(entity.dxf.insert)
                if layer == "A-TITLE":
                    titles.append(entity.dxf.text)
        if set(rooms) != set(room_shapes) or set(doors) != set(openings) or set(physical) != set(eq) or set(service) != set(eq):
            raise ValueError("DXF is missing canonical room, door, equipment or service geometry")
        if set(actual_windows) != set(windows):
            raise ValueError("DXF is missing actual canonical source window lines")
        if actual_window_labels != set(window_labels):
            raise ValueError("DXF is missing visible canonical source window inventory")
        if set(actual_grid_axes) != set(grid_axes) or actual_grid_labels != set(grid_labels):
            raise ValueError("DXF is missing actual source reference axes or visible inventory")
        _geometry(_parity(wall_rings), walls.geometry, "DXF wall material")
        if set(corridors) != set(routes) or set(axes) != {key for key, route in routes.items() if len(route.points) > 1}:
            raise ValueError("DXF missing canonical route corridor or centerline")
        for key, route in routes.items():
            _geometry(_parity(corridors[key]), _route_material(route), "DXF full-width route corridor")
        expected_title = f"DRAFT | {building.layout.project_name} | Variant {layout.variant}"
        expected_revision = f"Revision {identity['revision']} | FULL FACILITY: UNKNOWN | ENGINEERING ACCEPTANCE: PENDING"
        if len(markers) != 1 or expected_title not in titles or expected_revision not in titles:
            raise ValueError("DXF visible source/revision/DRAFT identity is missing or false")
        report.update(status="PASS", ok=True, counts={"rooms": len(rooms), "wall_rings": len(wall_rings),
            "doors": len(doors), "equipment": len(physical), "services": len(service), "routes": len(corridors)})
        if include_windows:
            report["window_capability"] = capability
            report["counts"]["windows"] = len(actual_windows)
        if include_grid:
            report["grid_capability"] = grid_capability
            report["counts"]["grid_axes"] = len(actual_grid_axes)
    except Exception as exc:
        report["issues"].append("DXF independent readback failed: " + str(exc)[:2000])
    return report


def _express(raw):
    report = {"status": "FAIL", "ok": False, "statements": [], "issues": []}
    try:
        import ifcopenshell
        import ifcopenshell.validate
        model = ifcopenshell.file.from_string(raw.decode("utf-8"))
        if sum(1 for _ in model) > 100_000:
            raise ValueError("IFC EXPRESS entity budget of 100000 exceeded")
        logger = ifcopenshell.validate.json_logger()
        ifcopenshell.validate.validate(model, logger, express_rules=True)
        # Upstream statements contain native entity objects. Preserve their
        # readable identities rather than turning a native error into PASS.
        import json
        report["statements"] = json.loads(json.dumps(logger.statements, default=str))
        if not report["statements"]:
            report.update(status="PASS", ok=True)
    except Exception as exc:
        report["issues"].append("Actual IFC EXPRESS validation failed: " + str(exc)[:2000])
    return report


def _validate(output, *, limits=OrthogonalBundleLimits(), expected_profile=None, verify_saved=True, include_windows=False,
              include_grid=False, include_groups=False):
    report = {"format": "FLC-orthogonal-bundle-qa", "version": "0.1", "status": "FAIL", "ok": False,
        "scope": "Persisted exchange delivery and declared software predicates only",
        "full_facility_validation": dict(FULL_UNKNOWN), "engineering_acceptance": "PENDING",
        "checks": [], "readbacks": {}}
    try:
        window_options = _window_options(include_windows)
        projection_options = {**window_options, **_grid_options(include_grid)}
        bundle_options = {**projection_options, **_group_options(include_groups)}
        if not isinstance(limits, OrthogonalBundleLimits):
            raise ValueError("limits requires OrthogonalBundleLimits")
        root = Path(output)
        if not root.is_dir() or root.is_symlink():
            raise ValueError("Bundle must be an actual bounded directory")
        root = root.resolve()
        inventory = INVENTORY if verify_saved else INVENTORY - {"qa.json"}
        if {child.name for child in root.iterdir()} != inventory:
            raise ValueError("Bundle inventory has missing or undeclared files")
        data, total = {}, 0
        for filename in sorted(inventory):
            path = root / filename
            if path.is_symlink() or not path.is_file() or not path.resolve().is_relative_to(root):
                raise ValueError("Bundle inventory contains a link, escaped path or non-file")
            maximum = limits.max_json_bytes if filename.endswith(".json") else limits.max_artifact_bytes
            if path.stat().st_size > maximum:
                raise ValueError("Bundle artifact/JSON byte budget exceeded")
            data[filename] = path.read_bytes()
            total += len(data[filename])
            if len(data[filename]) > maximum or total > limits.max_total_bytes:
                raise ValueError("Bundle byte budget exceeded")
        _check(report, "INVENTORY", "PASS", ["Closed bounded file inventory; no links or escaped paths"])
    except Exception as exc:
        _check(report, "INVENTORY", "FAIL", [str(exc)[:2000]])
        return _finish(report)
    try:
        manifest = _read_json(data["manifest.json"], limits)
        _keys(manifest, ("format", "version", "status", "ok", "project_name", "variant", "revision",
            "full_facility_validation", "engineering_acceptance", "projection_scope", "sources", "artifacts", "facility_predicates", "limits",
            *(("window_capability",) if include_windows else ()), *(("grid_capability",) if include_grid else ()),
            *(("group_capability",) if include_groups else ())))
        raw = {name: _read_json(data[filename], limits) for name, filename in SOURCE_FILES.items()}
        building, layout, equipment, routing, profile = _source_frames(raw, **projection_options)
        capability = _window_capability(building, layout, include_windows)
        grid_capability = _grid_capability(building, include_grid)
        group_capability = _group_capability(building, layout, equipment, routing, include_groups)
        identity = _identity(building, layout, profile, raw)
        expected = {"format": FORMAT, "version": "0.1", "status": "DRAFT", "ok": False,
            "project_name": building.layout.project_name, "variant": layout.variant, "revision": profile.drawing.revision,
            "full_facility_validation": FULL_UNKNOWN, "engineering_acceptance": "PENDING",
            "projection_scope": _projection_scope(building, **bundle_options)}
        if include_windows:
            expected["window_capability"] = capability
        if include_grid:
            expected["grid_capability"] = grid_capability
        if include_groups:
            expected["group_capability"] = group_capability
        if not _same({key: manifest[key] for key in expected}, expected):
            raise ValueError("Manifest canonical source/revision/variant or DRAFT status is false")
        _keys(manifest["limits"], vars(limits))
        declared_limits = OrthogonalBundleLimits(**manifest["limits"])
        if not _same(manifest["limits"], asdict(declared_limits)):
            raise ValueError("Manifest bounds are not canonical")
        limits = OrthogonalBundleLimits(**{name: min(value, getattr(declared_limits, name)) for name, value in vars(limits).items()})
        if total > limits.max_total_bytes:
            raise ValueError("Actual saved bytes exceed the declared total bundle bound")
        for filename, content in data.items():
            maximum = limits.max_json_bytes if filename.endswith(".json") else limits.max_artifact_bytes
            if len(content) > maximum:
                raise ValueError("Actual saved bytes exceed the declared resource bound: " + filename)
            if filename.endswith(".json"):
                _read_json(content, limits)
        if expected_profile is not None and not _same(profile.to_dict(), expected_profile.to_dict()):
            raise ValueError("Persisted profile differs from independently supplied expected profile")
        _keys(manifest["sources"], SOURCE_FILES)
        for name, filename in SOURCE_FILES.items():
            record = manifest["sources"][name]
            _keys(record, ("file", "sha256", "canonical_sha256"))
            if record["file"] != filename or _digest(record["sha256"]) != _sha(data[filename]) or _digest(record["canonical_sha256"]) != _hash(raw[name]):
                raise ValueError("Source filename, actual bytes or canonical hash differs: " + name)
        report["identity"] = identity
        report["manifest_sha256"] = _sha(data["manifest.json"])
        report["projection_scope"] = _projection_scope(building, **bundle_options)
        if include_windows:
            report["window_capability"] = capability
        if include_grid:
            report["grid_capability"] = grid_capability
        if include_groups:
            report["group_capability"] = group_capability
        facility = validate_orthogonal_building(building, layout, equipment, routing, profile=profile, **window_options).to_dict()
        _check(report, "SOURCE_CONTRACT", "PASS", ["Five strict canonical source snapshots, source hashes and current profile/revision agree"])
    except Exception as exc:
        _check(report, "SOURCE_CONTRACT", "FAIL", [str(exc)[:2000]])
        return _finish(report)
    indices = {}
    try:
        _keys(manifest["artifacts"], ARTIFACT_FILES)
        for kind, filename in ARTIFACT_FILES.items():
            record = manifest["artifacts"][kind]
            _keys(record, ("file", "sha256", "index", "index_sha256"))
            if (record["file"] != filename or record["index"] != INDEX_FILES[kind] or
                    _digest(record["sha256"]) != _sha(data[filename]) or
                    _digest(record["index_sha256"]) != _sha(data[INDEX_FILES[kind]])):
                raise ValueError("Artifact filename or actual artifact/index hash differs: " + kind)
            indices[kind] = _read_json(data[INDEX_FILES[kind]], limits)
            if not isinstance(indices[kind], dict):
                raise ValueError("Artifact index requires an object: " + kind)
        expected_dxf = {"schema": "orthogonal-bundle-dxf/0.1", "status": "DRAFT", "ok": False, "units": "mm",
            "identity": identity, "artifact_sha256": _sha(data[ARTIFACT_FILES["dxf"]]),
            "full_facility_validation": FULL_UNKNOWN, "engineering_acceptance": "PENDING"}
        if include_windows:
            expected_dxf["window_capability"] = capability
        if include_grid:
            expected_dxf["grid_capability"] = grid_capability
        if not _same(indices["dxf"], expected_dxf):
            raise ValueError("DXF index identity or DRAFT scope differs")
        ifc = indices["ifc"]
        _keys(ifc, ("schema", "status", "ok", "units", "source", "variant", "artifact_sha256",
            "full_facility_validation", "engineering_acceptance", "scope", "local_validation", "counts", "bundle_identity",
            *(("window_capability",) if include_windows else ()), *(("grid_capability",) if include_grid else ()),
            *(("group_capability",) if include_groups else ())))
        expected_ifc = {"schema": "orthogonal-ifc-draft/0.1", "status": "DRAFT", "ok": False, "units": "mm",
            "source": {key: value for key, value in identity.items() if key.endswith("_sha256") and key != "profile_sha256"},
            "variant": layout.variant, "artifact_sha256": _sha(data[ARTIFACT_FILES["ifc"]]),
            "full_facility_validation": FULL_UNKNOWN, "engineering_acceptance": "PENDING", "bundle_identity": identity,
            "scope": "Exact IFC coordination geometry and local supplied predicates; not full facility or engineering acceptance",
            "local_validation": {"rooms": facility["local_validation"]["rooms"],
                "equipment": facility["local_validation"]["equipment"] if equipment is not None else {"status": "NOT_SUPPLIED"},
                "door_approaches": facility["local_validation"]["door_approaches"] if equipment is not None else {"status": "NOT_SUPPLIED"},
                "routes": facility["local_validation"]["routes"] if routing is not None else {"status": "NOT_SUPPLIED"}}}
        if include_windows:
            expected_ifc["window_capability"] = capability
            expected_ifc["local_validation"]["window_capability"] = capability
        if include_grid:
            expected_ifc["grid_capability"] = grid_capability
            expected_ifc["local_validation"]["grid_capability"] = grid_capability
        if include_groups:
            expected_ifc["group_capability"] = group_capability
            expected_ifc["local_validation"]["group_capability"] = group_capability
        if not _same({key: ifc[key] for key in expected_ifc}, expected_ifc):
            raise ValueError("IFC index source, revision or DRAFT scope differs")
        _check(report, "ARTIFACT_INDEX", "PASS", ["Three actual artifact and index byte hashes agree with the closed manifest"])
    except Exception as exc:
        _check(report, "ARTIFACT_INDEX", "FAIL", [str(exc)[:2000]])
        # Recompute actual source predicates even when saved artifact indices
        # are malformed; a cached positive verdict never suppresses a failure.
    report["readbacks"]["facility"] = facility
    _check(report, "FACILITY_PREDICATES", facility["status"], ["Recomputed every declared current-profile predicate from persisted canonical sources"])
    try:
        record = manifest["facility_predicates"]
        _keys(record, ("file", "sha256"))
        if record["file"] != "facility-predicates.json" or _digest(record["sha256"]) != _sha(data["facility-predicates.json"]):
            raise ValueError("Facility report actual bytes or fixed filename differs")
        saved_facility = _read_json(data["facility-predicates.json"], limits)
        if not _same(saved_facility, facility):
            raise ValueError("Saved facility report differs from fresh source-backed predicates/provenance")
        _check(report, "FACILITY_REPORT_IDENTITY", "PASS", ["Saved predicate/provenance report exactly matches current independent recomputation"])
    except Exception as exc:
        _check(report, "FACILITY_REPORT_IDENTITY", "FAIL", [str(exc)[:2000]])
    # Native readers operate on immutable copies of exactly the bytes hashed
    # above, preventing a second mutable-path read from changing their input.
    with tempfile.TemporaryDirectory(prefix="flc-orthogonal-readback-") as temporary:
        snapshot = Path(temporary)
        for filename in ARTIFACT_FILES.values():
            (snapshot / filename).write_bytes(data[filename])
        dxf = _dxf_readback(data["geometry.dxf"], building, layout, equipment, routing, identity, limits, **projection_options)
        pdf = validate_orthogonal_pdf(snapshot / "geometry.pdf", building, layout, equipment,
            routing=routing, revision=profile.drawing.revision, index=indices.get("pdf"), **projection_options)
        ifc = validate_orthogonal_ifc(snapshot / "geometry.ifc", building, layout, equipment,
            routing=routing, expected_sha256=_sha(data["geometry.ifc"]), max_file_bytes=min(limits.max_artifact_bytes, 16_777_216), **bundle_options)
        try:
            import ifcopenshell
            from .orthogonal_ifc_qa import _properties, _same_properties
            model = ifcopenshell.file.from_string(data["geometry.ifc"].decode("utf-8"))
            projects = model.by_type("IfcProject")
            if len(projects) != 1 or not _same_properties(_properties(projects[0], "Pset_OrthogonalBundle"), identity):
                raise ValueError("Actual IFC source/profile/revision bundle properties differ")
            if "ifc" in indices and not _same(indices["ifc"]["counts"], ifc["counts"]):
                raise ValueError("IFC index counts differ from actual independent geometry readback")
        except Exception as exc:
            ifc.update(status="FAIL", ok=False)
            ifc["issues"].append(str(exc)[:2000])
        express = _express(data["geometry.ifc"])
    for kind, identifier, readback in (("dxf", "DXF_READBACK", dxf), ("pdf", "PDF_READBACK", pdf),
            ("ifc", "IFC_READBACK", ifc), ("ifc_express", "IFC_EXPRESS", express)):
        report["readbacks"][kind] = readback
        _check(report, identifier, readback["status"], readback.get("issues", []) or ["Actual persisted physical exchange predicate passed"])
    _finish(report)
    if verify_saved:
        try:
            if not _same(_read_json(data["qa.json"], limits), report):
                raise ValueError("Saved QA differs from freshly recomputed persisted source/artifact verdict")
            _check(report, "SAVED_QA", "PASS", ["Saved QA equals the independent current report before this self-check"])
        except Exception as exc:
            _check(report, "SAVED_QA", "FAIL", [str(exc)[:2000]])
    return _finish(report)


def validate_orthogonal_bundle(output, *, expected_profile=None, limits=OrthogonalBundleLimits(), include_windows=False,
                               include_grid=False, include_groups=False):
    """Reload bounded persisted sources and independently check all three files.

    PASS is limited to exchange delivery and declared software predicates.
    UNKNOWN optional input remains UNKNOWN; engineering acceptance is PENDING.
    A caller-supplied expected profile is an additional independent anchor.
    """
    try:
        return _validate(output, limits=limits, expected_profile=expected_profile,
            **_window_options(include_windows), **_grid_options(include_grid), **_group_options(include_groups))
    except Exception as exc:
        return {"format": "FLC-orthogonal-bundle-qa", "version": "0.1", "status": "FAIL", "ok": False,
            "scope": "Persisted exchange delivery and declared software predicates only",
            "full_facility_validation": dict(FULL_UNKNOWN), "engineering_acceptance": "PENDING",
            "checks": [{"id": "READBACK", "status": "FAIL", "evidence": [str(exc)[:2000]]}], "readbacks": {}}

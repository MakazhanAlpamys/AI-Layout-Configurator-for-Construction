"""Atomic persisted drafts from existing canonical orthogonal source models."""

from dataclasses import asdict, dataclass
import hashlib
import json
import math
import os
from pathlib import Path
import sys
import tempfile

from shapely.geometry import box
from shapely.ops import unary_union

from .building import BuildingIR
from .equipment import EquipmentLayoutResult, EquipmentPlacement, clearance_rect
from .facility import DEFAULT_FACILITY_PROFILE_SCHEMA_PATH, FacilityProfile
from .flows import FlowRoute, FlowRoutingResult
from .models import LayoutResult, Rect
from .orthogonal_facility import validate_orthogonal_building
from .orthogonal_ifc import export_orthogonal_draft_ifc
from .orthogonal_pdf import OrthogonalPDFLimits, export_orthogonal_draft_pdf
from .orthogonal_projection import build_orthogonal_wall_plan, export_orthogonal_draft_dxf
from .result_geometry import parse_layout_result
from .schema import normalize_building_mapping, normalize_mapping, validate_mapping


SOURCE_FILES = {name: name + ".json" for name in ("building", "layout", "equipment", "routing", "profile")}
ARTIFACT_FILES = {name: "geometry." + name for name in ("dxf", "pdf", "ifc")}
INDEX_FILES = {name: name + "-index.json" for name in ARTIFACT_FILES}
INVENTORY = {*SOURCE_FILES.values(), *ARTIFACT_FILES.values(), *INDEX_FILES.values(),
             "facility-predicates.json", "manifest.json", "qa.json"}
FORMAT = "FLC-orthogonal-bundle-draft"
FULL_UNKNOWN = {"status": "UNKNOWN", "ok": False}


@dataclass(frozen=True)
class OrthogonalBundleLimits:
    max_json_bytes: int = 1_048_576
    max_artifact_bytes: int = 16_777_216
    max_total_bytes: int = 33_554_432
    max_dxf_entities: int = 5000
    max_json_nodes: int = 50000
    max_json_depth: int = 32

    def __post_init__(self):
        for name, value in vars(self).items():
            if type(value) is not int or not 1 <= value <= 33_554_432:
                raise ValueError(f"{name} requires a positive bounded integer")


def _json(value):
    try:
        return json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False,
                          separators=(",", ":")).encode("utf-8")
    except (ValueError, TypeError, UnicodeError, OverflowError, RecursionError) as exc:
        raise ValueError("Draft bundle requires finite UTF-8 canonical JSON") from exc


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _hash(value):
    return _sha(_json(value))


def _read_json(data, limits):
    if len(data) > limits.max_json_bytes:
        raise ValueError("Draft JSON byte budget exceeded")
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("Duplicate draft JSON key: " + key)
            result[key] = value
        return result
    try:
        raw = json.loads(data.decode("utf-8"), object_pairs_hook=unique,
                         parse_constant=lambda value: (_ for _ in ()).throw(ValueError("Nonfinite JSON number")))
    except (UnicodeError, json.JSONDecodeError, RecursionError) as exc:
        raise ValueError("Malformed bounded UTF-8 draft JSON") from exc
    count, queue = 0, [(raw, 0)]
    while queue:
        value, depth = queue.pop()
        count += 1
        if count > limits.max_json_nodes or depth > limits.max_json_depth:
            raise ValueError("Draft JSON structure budget exceeded")
        if isinstance(value, dict):
            queue.extend((child, depth + 1) for child in value.values())
        elif isinstance(value, list):
            queue.extend((child, depth + 1) for child in value)
    _json(raw)
    return raw


def _number(value, positive=False):
    if type(value) not in (int, float) or not math.isfinite(value) or positive and value <= 0:
        raise ValueError("Source frame requires actual finite numbers and positive extents")
    return value


def _keys(raw, required):
    if not isinstance(raw, dict) or set(raw) != set(required):
        raise ValueError("Source frame has missing or unknown fields")


def _source_frames(raw):
    """Materialize strict canonical serializations using existing source classes."""
    _keys(raw, SOURCE_FILES)
    normalize_mapping(raw["building"]["layout"])
    building = normalize_building_mapping(raw["building"])
    if building.structural_grid is not None:
        raise ValueError("Orthogonal draft export does not support structural_grid projection")
    if building.layout.windows or any(room.needs_daylight for room in building.layout.rooms):
        raise ValueError("Orthogonal draft export does not support explicit windows or daylight windows")
    layout = parse_layout_result(raw["layout"])
    if validate_mapping(raw["profile"], DEFAULT_FACILITY_PROFILE_SCHEMA_PATH):
        raise ValueError("Source profile fails its strict canonical schema")
    profile = FacilityProfile.from_mapping(raw["profile"])
    equipment = None
    if raw["equipment"] is not None:
        _keys(raw["equipment"], ("equipment",))
        items = raw["equipment"]["equipment"]
        if not isinstance(items, list) or len(items) > 32:
            raise ValueError("Equipment source requires at most 32 placement records")
        placements = []
        known = {item.id for item in building.equipment}
        for item in items:
            _keys(item, ("equipment_id", "room_id", "x", "y", "width", "depth", "rotated", "clearance"))
            if (not isinstance(item["equipment_id"], str) or item["equipment_id"] not in known or
                    not isinstance(item["room_id"], str) or type(item["rotated"]) is not bool):
                raise ValueError("Equipment source identity/rotation is not canonical")
            rect = Rect(*(_number(item[key], key in ("width", "depth")) for key in ("x", "y", "width", "depth")))
            placements.append(EquipmentPlacement(item["equipment_id"], item["room_id"], rect, item["rotated"]))
        equipment = EquipmentLayoutResult(tuple(placements))
    routing = None
    if raw["routing"] is not None:
        _keys(raw["routing"], ("routes",))
        items = raw["routing"]["routes"]
        if not isinstance(items, list) or len(items) > 64:
            raise ValueError("Routing source requires at most 64 routes")
        routes = []
        for item in items:
            fields = ("flow_id", "from_id", "to_id", "from_room_id", "to_room_id", "room_path", "points", "minimum_clear_width_mm")
            _keys(item, fields)
            if any(not isinstance(item[key], str) or not item[key] for key in fields[:5]):
                raise ValueError("Route source identities require nonempty text")
            if (not isinstance(item["room_path"], list) or not item["room_path"] or
                    any(not isinstance(room, str) or not room for room in item["room_path"]) or
                    not isinstance(item["points"], list) or not 1 <= len(item["points"]) <= 1024):
                raise ValueError("Route source has malformed/bounded room path or points")
            points = []
            for point in item["points"]:
                _keys(point, ("x", "y"))
                points.append((_number(point["x"]), _number(point["y"])))
            routes.append(FlowRoute(*(item[key] for key in fields[:5]), tuple(item["room_path"]),
                tuple(points), _number(item["minimum_clear_width_mm"], True)))
        routing = FlowRoutingResult(tuple(routes))
    canonical = {"building": building.to_dict(), "layout": layout.to_dict(), "profile": profile.to_dict(),
                 "equipment": equipment.to_dict(building) if equipment is not None else None,
                 "routing": routing.to_dict() if routing is not None else None}
    if _json(canonical) != _json(raw):
        raise ValueError("Source frames require canonical values and actual derived equipment clearance")
    return building, layout, equipment, routing, profile


def _projection_scope(building):
    return {"geometry_components": ["room_contours", "wall_material", "door_openings", "equipment_footprints",
        "directional_service_envelopes", "full_width_route_corridors", "route_centerlines"],
        "zones": {"declared": len(building.zones), "source_preserved": True,
            "software_predicates": "RECOMPUTED", "native_ifc_projection": "UNKNOWN"},
        "process_systems": {"declared": len(building.flows), "source_preserved": True,
            "software_predicates": "RECOMPUTED", "native_ifc_projection": "UNKNOWN"},
        "unsupported_components": ["structural_grid", "explicit_windows", "daylight_windows"],
        "height_policy": "coordination_proxy_v1; rooms/walls=2800; doors/openings=2100; equipment=1000; service/routes=20 mm"}


def _route_key(route):
    return _hash([route.flow_id, route.from_id, route.to_id, route.from_room_id, route.to_room_id])


def _rings(shape):
    if shape.geom_type == "Polygon":
        return [list(shape.exterior.coords)[:-1], *[list(ring.coords)[:-1] for ring in shape.interiors]]
    return [ring for part in shape.geoms for ring in _rings(part)]


def _corridor(route):
    half = route.minimum_clear_width_mm / 2
    if len(route.points) == 1:
        x, y = route.points[0]
        return box(x - half, y - half, x + half, y + half)
    return unary_union([box(min(a[0], b[0]) - half, min(a[1], b[1]) - half,
                            max(a[0], b[0]) + half, max(a[1], b[1]) + half)
                        for a, b in zip(route.points, route.points[1:])])


def _dxf(path, building, layout, equipment, routing, identity):
    export_orthogonal_draft_dxf(path, building.layout, layout)
    import ezdxf
    document = ezdxf.readfile(path)
    document.appids.add("FLC_ORTH_BUNDLE")
    model = document.modelspace()
    for name, color in (("A-EQUIP", 5), ("A-EQUIP-CLEAR", 4), ("A-FLOW-WIDTH", 3), ("A-FLOW", 3)):
        document.layers.add(name, dxfattribs={"color": color})
    marker = list(model.query('POINT[layer=="A-TITLE"]'))[0]
    marker.set_xdata("FLC_ORTH_BUNDLE", [(1000, key + "=" + str(value)) for key, value in identity.items()])
    specs = {item.id: item for item in building.equipment}
    for placement in equipment.placements if equipment else ():
        service = clearance_rect(specs[placement.equipment_id], placement.rect, placement.rotated)
        for layer, rect in (("A-EQUIP", placement.rect), ("A-EQUIP-CLEAR", service)):
            entity = model.add_lwpolyline(((rect.x, rect.y), (rect.right, rect.y), (rect.right, rect.top), (rect.x, rect.top)),
                                         close=True, dxfattribs={"layer": layer})
            entity.set_xdata("FLC_ORTH_BUNDLE", [(1000, "equipment_id=" + placement.equipment_id),
                (1000, "room_id=" + placement.room_id), (1000, "rotated=" + str(placement.rotated).lower())])
    for route in routing.routes if routing else ():
        tags = [(1000, "route_key=" + _route_key(route)), (1000, "flow_id=" + route.flow_id)]
        for ring in _rings(_corridor(route)):
            model.add_lwpolyline(ring, close=True, dxfattribs={"layer": "A-FLOW-WIDTH"}).set_xdata("FLC_ORTH_BUNDLE", tags)
        if len(route.points) > 1:
            model.add_lwpolyline(route.points, dxfattribs={"layer": "A-FLOW"}).set_xdata("FLC_ORTH_BUNDLE", tags)
    model.add_text(f"Revision {identity['revision']} | FULL FACILITY: UNKNOWN | ENGINEERING ACCEPTANCE: PENDING",
        dxfattribs={"height": 100, "layer": "A-TITLE"}).set_placement((0, building.layout.boundary.height_mm + 750))
    document.saveas(path)
    return {"schema": "orthogonal-bundle-dxf/0.1", "status": "DRAFT", "ok": False, "units": "mm",
            "identity": identity, "artifact_sha256": _sha(path.read_bytes()),
            "full_facility_validation": dict(FULL_UNKNOWN), "engineering_acceptance": "PENDING"}


def _ifc(path, building, layout, equipment, routing, identity):
    index = export_orthogonal_draft_ifc(path, building, layout, equipment, routing=routing)
    import ifcopenshell
    from .ifc import _add_properties
    document = ifcopenshell.open(str(path))
    project = document.by_type("IfcProject")[0]
    _add_properties(document, project.OwnerHistory, [project], "Pset_OrthogonalBundle", identity)
    document.write(str(path))
    index.update(artifact_sha256=_sha(path.read_bytes()), bundle_identity=identity)
    return index


def _publish_directory(stage, target):
    """Atomically rename a directory without replacing any existing target.

    Both platforms enforce absence inside the native filesystem operation;
    a preceding Python existence check cannot provide that guarantee alone.
    Unsupported native publication fails explicitly without an overwrite path.
    """
    stage, target = Path(stage), Path(target)
    if os.name == "nt":
        stage.rename(target)
        return
    if sys.platform.startswith("linux"):
        import ctypes
        library = ctypes.CDLL(None, use_errno=True)
        rename = getattr(library, "renameat2", None)
        if rename is None:
            raise ValueError("Atomic no-replace directory publication requires native Linux renameat2")
        rename.argtypes = (ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint)
        rename.restype = ctypes.c_int
        # AT_FDCWD=-100; RENAME_NOREPLACE=1. Keep the complete directory on
        # the same filesystem as its staging sibling for atomic publication.
        if rename(-100, os.fsencode(stage), -100, os.fsencode(target), 1) != 0:
            code = ctypes.get_errno()
            raise OSError(code, os.strerror(code), str(target))
        return
    raise ValueError("Atomic no-replace directory publication is unsupported on " + sys.platform)


def write_orthogonal_bundle(output, building, layout, equipment=None, *, routing=None,
                            profile, revision=None, font_path=None, limits=OrthogonalBundleLimits()):
    """Publish a new source-bound DRAFT directory after independent readback.

    Local FAIL or an unsupported render component rejects publication. Missing
    declared optional evidence can persist UNKNOWN; full acceptance is UNKNOWN.
    """
    if not isinstance(limits, OrthogonalBundleLimits):
        raise ValueError("limits requires OrthogonalBundleLimits")
    target = Path(output).absolute()
    if target.exists() or target.is_symlink():
        raise FileExistsError("Draft bundle destination already exists")
    try:
        if not isinstance(building, BuildingIR) or not isinstance(layout, LayoutResult) or not isinstance(profile, FacilityProfile):
            raise ValueError("Draft bundle requires canonical building/layout/profile")
        if equipment is not None and not isinstance(equipment, EquipmentLayoutResult):
            raise ValueError("Draft bundle requires canonical equipment or None")
        if routing is not None and not isinstance(routing, FlowRoutingResult):
            raise ValueError("Draft bundle requires canonical routing or None")
        raw = {"building": building.to_dict(), "layout": layout.to_dict(), "profile": profile.to_dict(),
               "equipment": equipment.to_dict(building) if equipment is not None else None,
               "routing": routing.to_dict() if routing is not None else None}
        raw = {name: _read_json(_json(value), limits) for name, value in raw.items()}
        building, layout, equipment, routing, profile = _source_frames(raw)
        revision = profile.drawing.revision if revision is None else revision
        if not isinstance(revision, str) or not revision.strip() or revision != profile.drawing.revision:
            raise ValueError("Bundle revision must match its actual source drawing profile")
        facility = validate_orthogonal_building(building, layout, equipment, routing, profile=profile).to_dict()
        if facility["status"] == "FAIL":
            raise ValueError("Declared actual facility predicates failed")
        build_orthogonal_wall_plan(building.layout, layout)
        if facility["local_validation"]["rooms"]["status"] != "PASS":
            raise ValueError("Draft source room/opening predicates must PASS before publication")
        source = {name + "_sha256": _hash(value) for name, value in raw.items()}
        identity = {"schema": FORMAT + "/0.1", "status": "DRAFT", "full_facility_validation": "UNKNOWN",
                    "engineering_acceptance": "PENDING", "project_name": building.layout.project_name,
                    "variant": str(layout.variant), "revision": revision, **source}
        # Preflight the PDF's stricter canonical/local budgets before staging.
        from .orthogonal_pdf import _prepare
        _prepare(building, layout, equipment, routing, OrthogonalPDFLimits())
    except (TypeError, KeyError, AttributeError, OverflowError, UnicodeError) as exc:
        raise ValueError("Malformed canonical draft source") from exc
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".orthogonal-draft-", dir=target.parent) as temporary:
        stage = Path(temporary) / "bundle"
        stage.mkdir()
        def save(name, value):
            data = _json(value)
            _read_json(data, limits)
            (stage / name).write_bytes(data)
            return _sha(data)
        sources = {name: {"file": SOURCE_FILES[name], "sha256": save(SOURCE_FILES[name], value),
                          "canonical_sha256": _hash(value)} for name, value in raw.items()}
        indices = {
            "dxf": _dxf(stage / ARTIFACT_FILES["dxf"], building, layout, equipment, routing, identity),
            "pdf": export_orthogonal_draft_pdf(stage / ARTIFACT_FILES["pdf"], building, layout, equipment,
                routing=routing, revision=revision, font_path=font_path),
            "ifc": _ifc(stage / ARTIFACT_FILES["ifc"], building, layout, equipment, routing, identity),
        }
        artifacts = {name: {"file": ARTIFACT_FILES[name], "sha256": _sha((stage / ARTIFACT_FILES[name]).read_bytes()),
            "index": INDEX_FILES[name], "index_sha256": save(INDEX_FILES[name], index)} for name, index in indices.items()}
        manifest = {"format": FORMAT, "version": "0.1", "status": "DRAFT", "ok": False,
            "project_name": building.layout.project_name, "variant": layout.variant, "revision": revision,
            "full_facility_validation": dict(FULL_UNKNOWN), "engineering_acceptance": "PENDING",
            "projection_scope": _projection_scope(building),
            "sources": sources, "artifacts": artifacts, "facility_predicates": {
                "file": "facility-predicates.json", "sha256": save("facility-predicates.json", facility)},
            "limits": asdict(limits)}
        save("manifest.json", manifest)
        from .orthogonal_bundle_qa import _validate
        qa = _validate(stage, limits=limits, verify_saved=False)
        if qa["status"] == "FAIL":
            failures = [row["id"] + ": " + "; ".join(row["evidence"]) for row in qa["checks"] if row["status"] == "FAIL"]
            raise ValueError("Persisted orthogonal draft failed independent delivery QA: " + "; ".join(failures))
        save("qa.json", qa)
        final = _validate(stage, limits=limits, verify_saved=True)
        if final["status"] == "FAIL":
            raise ValueError("Persisted orthogonal draft saved QA is inconsistent")
        if target.exists() or target.is_symlink():
            raise FileExistsError("Draft destination appeared before publication")
        _publish_directory(stage, target)
    return json.loads(_json(manifest))

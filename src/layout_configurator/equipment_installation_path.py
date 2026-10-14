"""Bounded checks of a supplied fixed-orientation whole-equipment 2D path.

No path, clearance, removable obstacle or lifting operation is inferred. A
local PASS concerns the literal supplied 2D motion and its declared premises;
complete installation, 3D feasibility and engineering acceptance remain open.
"""

from dataclasses import dataclass, fields, replace
import hashlib
import json

from shapely.geometry import Polygon, box
from shapely.ops import unary_union

from .engineering_library import (EngineeringLibraryContract, EngineeringLibraryLimits,
    _digest, _number, _pointer, _raw_json_preflight, _same_json, _strict_json,
    _utf8_size, validate_engineering_library)
from .equipment_scenario_geometry import (EquipmentScenarioLimits, _aggregate_preflight,
    _BoundedFindings, _InputError, _canonical_equipment, _id, _prepare, _rect, _ring, _trusted, _world)
from .models import Rect
from .orthogonal import OrthogonalPolygon
from .orthogonal_projection import build_orthogonal_wall_plan


@dataclass(frozen=True)
class EquipmentInstallationPathLimits:
    max_equipment: int = 32
    max_rooms: int = 64
    max_poses: int = 128
    max_source_objects: int = 1024
    max_sources: int = 64
    max_source_bytes: int = 262144
    max_total_source_bytes: int = 1048576
    max_total_utf8_bytes: int = 4194304
    max_nodes: int = 131072
    max_depth: int = 24
    max_vertices: int = 4096
    max_staging_obstacles: int = 256
    max_geometry_checks: int = 131072
    max_findings: int = 256


@dataclass(frozen=True)
class EquipmentInstallationPathReport:
    _json: str
    status: str

    @property
    def ok(self):
        return False

    def to_dict(self):
        return _strict_json(self._json)


def _issue(code, message, *, status="FAIL", **evidence):
    return {"code": code, "status": status, "message": message, **evidence}


def _status(rows):
    return "FAIL" if any(row["status"] == "FAIL" for row in rows) else "UNKNOWN" if rows else "PASS"


def _base():
    return {"schema_version": 1, "scope": "supplied_fixed_orientation_installation_path_2d",
        "status": "UNKNOWN", "ok": False, "local_2d_path_status": "UNKNOWN",
        "declared_premises_status": "UNKNOWN", "source": {}, "source_binding_status": "UNKNOWN",
        "moving_body": None, "entry_validation": {"status": "UNKNOWN"},
        "segments": [], "opening_crossings": [], "actions": [], "issues": [],
        "three_dimensional_feasibility": "UNKNOWN", "full_scenario_validation": "UNKNOWN",
        "engineering_acceptance": "PENDING", "source_authentication": "UNKNOWN",
        "proven_building_infeasible": False,
        "policy": {"path_generation": False, "orientation": "One actual installed 0/90 degree orientation",
            "collision": "Positive-area intersection over each complete axis segment",
            "windows": "Closed wall material; never installation portals",
            "automatic_adjacency_openings": "Closed; only explicit source doors are cut",
            "temporary_action_text": "No geometric effect",
            "stop_effect": "Only its own bound non-target operating reservation; body remains",
            "stationary_installation_reservation": "Not a moving transport envelope",
            "exterior_inventory_scope": "Supplied source-bound 2D floor and obstacles only"}}


def _freeze(raw):
    encoder = json.JSONEncoder(sort_keys=True, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
    size, chunks = 0, []
    for chunk in encoder.iterencode(raw):
        size += len(chunk.encode("utf-8"))
        if size > EquipmentInstallationPathLimits().max_total_utf8_bytes:
            small = _base()
            small.update({key: raw[key] for key in ("source", "status", "declared_premises_status", "source_binding_status")})
            small["local_2d_path_status"] = "FAIL" if raw["local_2d_path_status"] == "FAIL" else "UNKNOWN"
            failure = next((row for row in raw["issues"] if row["status"] == "FAIL"), None)
            small["issues"] = ([failure] if failure else []) + [_issue("output_budget_exceeded",
                "Detailed path inventory exceeds its delivery bound", status="UNKNOWN")]
            small["finding_inventory"] = {"status": "INCOMPLETE"}
            return _freeze(small)
        chunks.append(chunk)
    return EquipmentInstallationPathReport("".join(chunks), raw["status"])


def _scenario_limits(limits):
    return EquipmentScenarioLimits(max_equipment=limits.max_equipment, max_rooms=limits.max_rooms,
        max_source_objects=limits.max_source_objects, max_total_utf8_bytes=limits.max_total_utf8_bytes,
        max_nodes=limits.max_nodes, max_depth=limits.max_depth, max_vertices=limits.max_vertices,
        max_pair_checks=limits.max_geometry_checks, max_findings=limits.max_findings)


def _lexical_nodes(content, limits):
    """Charge every nested source before any source JSON is decoded."""
    _raw_json_preflight(content, limits)
    quoted, escaped, atom, count = False, False, False, 0
    for char in content:
        if quoted:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                quoted = False
        elif char == '"':
            quoted, atom = True, False
            count += 1
        elif char in "[{":
            atom = False
            count += 1
        elif char in "},]:" or char.isspace():
            atom = False
        elif not atom:
            count += 1
            atom = True
    return count


def _shape(value, *, unit_optional=False):
    """Pure validation, including representability, before GEOS construction."""
    if type(value) is not dict or value.get("kind") not in ("rectangle", "orthogonal_polygon"):
        raise _InputError("unsupported_supplied_geometry", "Only literal rectangles and orthogonal rings are supported", "UNKNOWN")
    if value.get("unit", "mm" if unit_optional else None) != "mm":
        raise _InputError("invalid_supplied_geometry", "Supplied path geometry requires millimetres")
    if value["kind"] == "rectangle":
        keys = {"kind", "unit", "x", "y", "width", "depth"}
        if set(value) - keys or not {"x", "y", "width", "depth"} <= value.keys():
            raise _InputError("invalid_supplied_geometry", "Rectangle fields must be explicit and closed")
        rect = Rect(value["x"], value["y"], value["width"], value["depth"])
        _rect(rect)
        return rect
    if set(value) - {"kind", "unit", "points"} or type(value.get("points")) not in (list, tuple):
        raise _InputError("invalid_supplied_geometry", "Orthogonal ring fields must be explicit and closed")
    points = value["points"]
    if not 5 <= len(points) <= 257 or points[0] != points[-1] or any(type(point) not in (list, tuple) or len(point) != 2 or
        any(not _number(number, lower=-1000000, upper=1000000) for number in point) for point in points):
        raise _InputError("invalid_supplied_geometry", "A closed bounded actual numeric ring is required")
    if any((a[0] == b[0]) == (a[1] == b[1]) for a, b in zip(points, points[1:])):
        raise _InputError("invalid_supplied_geometry", "Ring edges must be nonzero and axis-aligned")
    return points


def _geometry(shape):
    geometry = box(shape.x, shape.y, shape.right, shape.top) if type(shape) is Rect else Polygon(shape)
    if not geometry.is_valid or geometry.area <= 0:
        raise _InputError("unrepresentable_supplied_geometry", "Literal geometry is not valid and positive at supported precision", "UNKNOWN")
    return geometry


def _evidence_shape(row, name, limits):
    if row is None:
        return
    if type(row) is not dict or set(row) != {"source_id", "source_sha256", "source_pointer", "value"} or not _id(row["source_id"]):
        raise _InputError("invalid_path_evidence", "Path evidence needs closed explicit source identity, bytes and pointer")
    if type(row["source_sha256"]) is not str or len(row["source_sha256"]) != 64 or any(c not in "0123456789abcdef" for c in row["source_sha256"]):
        raise _InputError("invalid_path_evidence", "Exact SHA-256 source identity is required")
    if type(row["source_pointer"]) is not str or len(row["source_pointer"]) > 1024 or not row["source_pointer"].startswith("/"):
        raise _InputError("invalid_path_evidence", "An explicit bounded structured source pointer is required")
    value = row["value"]
    if name == "transport_clearance":
        if type(_shape(value)) is not Rect:
            raise _InputError("unsupported_transport_clearance", "Moving clearance currently requires a literal source rectangle", "UNKNOWN")
    else:
        if type(value) is not dict or set(value) != {"kind", "unit", "floor", "obstacles", "inventory_scope"} or value["kind"] != "exterior_staging_2d" or value["unit"] != "mm" or value["inventory_scope"] != "supplied_2d_only":
            raise _InputError("invalid_staging", "Staging needs explicit millimetre floor and supplied 2D obstacle inventory")
        _shape(value["floor"], unit_optional=True)
        if type(value["obstacles"]) is not list:
            raise _InputError("invalid_staging", "Supplied staging obstacles require an actual list")
        if len(value["obstacles"]) > limits.max_staging_obstacles:
            raise _InputError("input_budget_exceeded", "Staging obstacle count exceeded before geometry", "UNKNOWN")
        for obstacle in value["obstacles"]:
            _shape(obstacle, unit_optional=True)


def _admit(request, building, layout, equipment, library, mapping, actions, limits):
    defaults = EquipmentInstallationPathLimits()
    if type(limits) is not EquipmentInstallationPathLimits or any(type(getattr(limits, field.name)) is not int or
        not 1 <= getattr(limits, field.name) <= getattr(defaults, field.name) for field in fields(defaults)):
        raise _InputError("invalid_limits", "Limits require actual positive integers and may only tighten defaults")
    scenario_limits = _scenario_limits(limits)
    counts = _aggregate_preflight((request, building, layout, equipment, library, mapping, actions), scenario_limits)
    if type(request) is not dict or set(request) != {"version", "id", "equipment_id", "card_id", "external_entry_id", "source", "poses", "transport_clearance", "exterior_staging"} or request["version"] != "0.1":
        raise _InputError("invalid_path_request", "A closed version 0.1 supplied path request is required")
    if any(not _id(request[key]) for key in ("id", "equipment_id", "card_id", "external_entry_id")):
        raise _InputError("invalid_path_request", "Path selectors require explicit bounded identities")
    source = request["source"]
    keys = {"building_sha256", "layout_sha256", "equipment_sha256", "library_sha256"}
    if type(source) is not dict or set(source) != keys or any(type(value) is not str or len(value) != 64 or any(c not in "0123456789abcdef" for c in value) for value in source.values()):
        raise _InputError("invalid_source_binding", "All four exact original source SHA-256 identities are required")
    poses = request["poses"]
    if type(poses) is not list or len(poses) < 2:
        raise _InputError("invalid_path_request", "At least two ordered explicit poses are required")
    if len(poses) > limits.max_poses:
        raise _InputError("input_budget_exceeded", "Pose count exceeds its bound before geometry", "UNKNOWN")
    for pose in poses:
        if type(pose) is not dict or set(pose) != {"x_mm", "y_mm", "rotated"} or type(pose["rotated"]) is not bool or any(not _number(pose[key], lower=-1000000, upper=1000000) for key in ("x_mm", "y_mm")):
            raise _InputError("invalid_path_pose", "Poses require bounded finite actual coordinates and actual rotation flags")
    for name in ("transport_clearance", "exterior_staging"):
        _evidence_shape(request[name], name, limits)
    if type(library) is EngineeringLibraryContract:
        library = library.to_dict()
    if library is not None and type(library) is not dict:
        raise _InputError("invalid_library", "Library must retain its original supplied contract")
    raw_sources = library.get("sources", []) if library is not None else []
    if type(raw_sources) is not list:
        raise _InputError("invalid_library", "Library source inventory needs an actual list")
    if len(raw_sources) > limits.max_sources:
        raise _InputError("input_budget_exceeded", "Source count exceeds its bound", "UNKNOWN")
    lexical_limits = replace(EngineeringLibraryLimits(), max_depth=limits.max_depth,
        max_nodes=min(limits.max_nodes, EngineeringLibraryLimits().max_nodes))
    nested_nodes, source_bytes = 0, 0
    for source in raw_sources:
        if type(source) is dict and type(source.get("content")) is str:
            try:
                size = _utf8_size(source["content"], limits.max_source_bytes, "$.library.sources.content")
                source_bytes += size
                if source_bytes > limits.max_total_source_bytes:
                    raise _InputError("input_budget_exceeded", "Combined source byte budget exceeded", "UNKNOWN")
                if source["content"].lstrip().startswith(("[", "{")):
                    nested_nodes += _lexical_nodes(source["content"], lexical_limits)
                if counts["nodes"] + nested_nodes > limits.max_nodes or counts["utf8_byte_upper_bound"] + source_bytes > limits.max_total_utf8_bytes:
                    raise _InputError("input_budget_exceeded", "Aggregate decoded source node/byte budget exceeded before parsing", "UNKNOWN")
            except ValueError as exc:
                raise _InputError("input_budget_exceeded", str(exc)[:512], "UNKNOWN") from exc
    # Common original frame, identity, placement and world-precision admission
    # is pure. Its nested decoding is reached only after the whole lexical pass.
    (layout, equipment, library, mapping, _, _, actions, frames, _) = _prepare(building, layout, equipment,
        library, mapping, (), (request["equipment_id"],), actions, scenario_limits)
    specs = {item.id: item for item in building.equipment}
    placements = {item.equipment_id: item for item in equipment.placements}
    placement = placements.get(request["equipment_id"])
    if placement is None:
        raise _InputError("missing_path_equipment", "The requested original equipment needs its actual installed placement", "UNKNOWN")
    target_spec = specs[placement.equipment_id]
    for pose in poses:
        width, depth = (target_spec.depth_mm, target_spec.width_mm) if pose["rotated"] else (target_spec.width_mm, target_spec.depth_mm)
        _rect(Rect(pose["x_mm"], pose["y_mm"], width, depth))
    clearance = request["transport_clearance"]
    if clearance is not None:
        local = _shape(clearance["value"])
        for pose in poses:
            oriented = Rect(target_spec.depth_mm - local.top, local.x, local.height, local.width) if pose["rotated"] else local
            _rect(Rect(pose["x_mm"] + oriented.x, pose["y_mm"] + oriented.y, oriented.width, oriented.height))
    raw_cards = library.get("cards", []) if type(library) is dict else []
    envelope_count = sum(len(card.get("envelopes", [])) for card in raw_cards if type(card) is dict and type(card.get("envelopes")) is list) if type(raw_cards) is list else 0
    obstacles = len(request["exterior_staging"]["value"]["obstacles"]) if request["exterior_staging"] is not None else 0
    openings = len(building.layout.doors) + (building.layout.external_entry is not None)
    room_count = len(layout.placements)
    predicate_upper = 2 * (len(poses) - 1) * (room_count + len(placements) + envelope_count + obstacles + 4 * openings + 4)
    predicate_upper += 2 * len(poses) * (len(placements) + envelope_count + obstacles + 4)
    predicate_upper += room_count * room_count + 8 * envelope_count + 8 * openings + 8 * room_count + len(poses)
    predicate_upper += len(placements) * max(0, len(placements) - 1) // 2 + 4 * len(placements) + room_count
    if predicate_upper > limits.max_geometry_checks:
        raise _InputError("input_budget_exceeded", "Whole path geometry predicate budget exceeded before GEOS", "UNKNOWN")
    counts.update({"nested_source_nodes": nested_nodes, "source_utf8_bytes": source_bytes,
        "predicate_upper_bound": predicate_upper, "poses": len(poses)})
    return layout, equipment, library, mapping, actions, specs, placements, frames, counts


def _bound_value(row, name, sources, documents, issues):
    if row is None:
        if name == "staging":
            issues.append(_issue("missing_exterior_staging", "Exterior approach coverage and obstacles were not supplied", status="UNKNOWN"))
        return None
    source = sources.get(row["source_id"])
    try:
        if source is None or source["sha256"] != row["source_sha256"] or hashlib.sha256(source["content"].encode("utf-8")).hexdigest() != row["source_sha256"]:
            raise ValueError("Exact supplied source bytes differ")
        if source["id"] not in documents:
            issues.append(_issue("unverified_" + name + "_source", "No admitted structured numeric source is available", status="UNKNOWN", source_id=source["id"]))
            return None
        if not _same_json(_pointer(documents[source["id"]], row["source_pointer"]), row["value"]):
            raise ValueError("Structured source pointer/value or scalar types differ")
    except (ValueError, KeyError, IndexError) as exc:
        issues.append(_issue(name + "_source_mismatch", str(exc), source_id=row["source_id"], source_pointer=row["source_pointer"]))
        return None
    if source["usage_rights"]["state"] != "PERMITTED" or source["kind"] == "visual_reference":
        issues.append(_issue("unverified_" + name + "_source", "Reusable numeric source evidence is unavailable", status="UNKNOWN", source_id=source["id"]))
        return None
    return row["value"]


def _sweep(first, second, local):
    rect = Rect(min(first["x_mm"], second["x_mm"]) + local.x,
        min(first["y_mm"], second["y_mm"]) + local.y,
        abs(second["x_mm"] - first["x_mm"]) + local.width,
        abs(second["y_mm"] - first["y_mm"]) + local.height)
    _rect(rect)
    return rect


def _opening_record(opening, sweep, first, second, local, thickness, segment):
    vertical = opening.orientation == "vertical"
    if vertical and first["x_mm"] == second["x_mm"] or not vertical and first["y_mm"] == second["y_mm"]:
        return None
    low, high = (sweep.x, sweep.right) if vertical else (sweep.y, sweep.top)
    transverse_low, transverse_high = (sweep.y, sweep.top) if vertical else (sweep.x, sweep.right)
    if low >= opening.fixed + thickness / 2 or high <= opening.fixed - thickness / 2 or transverse_high <= opening.start or transverse_low >= opening.end:
        return None
    first_low = first["x_mm"] + local.x if vertical else first["y_mm"] + local.y
    last_low = second["x_mm"] + local.x if vertical else second["y_mm"] + local.y
    extent = local.width if vertical else local.height
    complete = (first_low + extent <= opening.fixed - thickness / 2 and last_low >= opening.fixed + thickness / 2 or
        last_low + extent <= opening.fixed - thickness / 2 and first_low >= opening.fixed + thickness / 2)
    return {"opening_id": opening.id, "segment_index": segment, "external": opening.external,
        "orientation": opening.orientation, "fixed_mm": opening.fixed, "start_mm": opening.start,
        "end_mm": opening.end, "opening_width_mm": opening.width,
        "required_transverse_width_mm": transverse_high - transverse_low,
        "transverse_start_mm": transverse_low, "transverse_end_mm": transverse_high,
        "full_wall_thickness_crossing": complete,
        "fits": transverse_low >= opening.start and transverse_high <= opening.end}


def validate_equipment_installation_path(request, *, building, layout, equipment, library,
    card_by_equipment, actions_by_scenario=None, limits=EquipmentInstallationPathLimits()):
    """Check a supplied piecewise axis path, retaining independently known FAIL.

    Poses identify the oriented body's lower-left world coordinate. Transport
    clearance, when supplied, is a separate literal source-bound local
    rectangle transformed by the same fixed installed orientation. A supplied
    staging floor and complete *supplied* obstacle inventory cover exterior
    portions; their real-world completeness and all 3D operations stay UNKNOWN.
    """
    raw = _base()
    try:
        (layout, equipment, library, mapping, actions, specs, placements, frames, counts) = _admit(request,
            building, layout, equipment, library, card_by_equipment, actions_by_scenario, limits)
    except (ValueError, TypeError, AttributeError, KeyError, RecursionError) as exc:
        status = exc.status if isinstance(exc, _InputError) else "FAIL"
        raw["issues"] = [_issue(exc.code if isinstance(exc, _InputError) else "invalid_input", str(exc)[:512], status=status)]
        raw["status"] = raw["local_2d_path_status"] = status
        return _freeze(raw)
    issues = _BoundedFindings(limits.max_findings)
    premise_issues = _BoundedFindings(limits.max_findings)
    path_issues = _BoundedFindings(limits.max_findings)
    raw["issues"], raw["source"], raw["workload"] = issues, dict(request["source"]), counts
    raw["limits"] = {field.name: getattr(limits, field.name) for field in fields(limits)}
    actual = {"building_sha256": _digest(building.to_dict()), "layout_sha256": _digest(layout.to_dict()),
        "equipment_sha256": _digest(equipment.to_dict(building)), "library_sha256": _digest(library)}
    for key, value in actual.items():
        if request["source"][key] != value:
            path_issues.append(_issue("source_binding_mismatch", "Original canonical source identity differs", component=key,
                expected_sha256=request["source"][key], actual_sha256=value))
    raw["actual_source"] = actual
    raw["canonical_equipment"] = _canonical_equipment(building, layout, equipment)
    if raw["canonical_equipment"]["status"] != "PASS":
        path_issues.append(_issue("canonical_equipment", "Unchanged canonical body, host, anchor and directional clearance predicates remain required",
            status=raw["canonical_equipment"]["status"]))
    admission = validate_engineering_library(library)
    raw["library_validation"] = admission.to_dict()
    if admission.library is None or admission.binding_status != "PASS":
        path_issues.append(_issue("library_binding", "Unbound library geometry is excluded", status="FAIL" if admission.status == "FAIL" else "UNKNOWN"))
        cards, sources, documents = {}, {}, {}
    else:
        current = admission.library.to_dict()
        cards = {row["id"]: row for row in current["cards"]}
        sources = {row["id"]: row for row in current["sources"]}
        documents = {key: _strict_json(source["content"]) for key, source in sources.items() if source["content"].lstrip().startswith(("[", "{"))}
    bound_cards = {}
    for equipment_id, frame in frames.items():
        card = cards.get(mapping.get(equipment_id))
        if card is None:
            path_issues.append(_issue("missing_bound_card", "Relevant source-bound card is unavailable", status="UNKNOWN", equipment_id=equipment_id))
            continue
        if card["equipment_binding"]["id"] != equipment_id or card["equipment_binding"]["sha256"] != _digest(frame) or card["equipment_type"] != specs[equipment_id].type:
            path_issues.append(_issue("card_binding_mismatch", "Card must bind exact original equipment", equipment_id=equipment_id))
            continue
        binding_source = sources[card["equipment_binding"]["source_id"]]
        if binding_source["usage_rights"]["state"] != "PERMITTED" or binding_source["kind"] == "visual_reference":
            path_issues.append(_issue("unverified_card_provenance", "Reusable numeric equipment source is unavailable", status="UNKNOWN", equipment_id=equipment_id))
            continue
        if card["applicability"]["conditions"] or "installation_replacement" not in card["applicability"]["scenarios"]:
            path_issues.append(_issue("unsupported_applicability", "Source installation applicability is unresolved", status="UNKNOWN", equipment_id=equipment_id))
            continue
        bound_cards[equipment_id] = card
    target_id = request["equipment_id"]
    target = placements[target_id]
    spec = specs[target_id]
    if mapping.get(target_id) != request["card_id"]:
        path_issues.append(_issue("card_selection_mismatch", "Path card must match the explicit original equipment mapping"))
    raw["moving_body"] = {"equipment_id": target_id, "card_id": request["card_id"],
        "width_mm": target.rect.width, "depth_mm": target.rect.height, "rotated": target.rotated,
        "authority": "Exact original EquipmentSpec and installed EquipmentPlacement"}
    active, stopped = {}, set()
    for equipment_id, ids in actions.get("installation_replacement", {}).items():
        card = bound_cards.get(equipment_id)
        for action_id in ids:
            row = next((item for item in card["scenario_actions"] if item["id"] == action_id), None) if card else None
            if row is None:
                premise_issues.append(_issue("unbound_action", "Selected action needs its fresh source-bound card", status="UNKNOWN", equipment_id=equipment_id, action_id=action_id))
            elif row["scenario"] != "installation_replacement":
                premise_issues.append(_issue("invalid_action_reference", "Active action belongs to another scenario", equipment_id=equipment_id, action_id=action_id))
            elif not _trusted(row["evidence"], card, sources):
                premise_issues.append(_issue("unverified_action", "Action premise is missing, assumed or unavailable", status="UNKNOWN", equipment_id=equipment_id, action_id=action_id))
            else:
                active.setdefault(equipment_id, set()).add(action_id)
                raw["actions"].append({"equipment_id": equipment_id, "id": action_id, "value": row["evidence"]["value"], "execution_verification": "UNKNOWN"})
                if row["evidence"]["value"]["kind"] == "production_stopped":
                    stopped.add(equipment_id)
    target_card = bound_cards.get(target_id)
    if target_card is None:
        premise_issues.append(_issue("missing_bound_target_card", "Installation premises cannot be read from an admitted card", status="UNKNOWN"))
    else:
        for row in target_card["envelopes"]:
            if row["scenario"] != "installation_replacement":
                continue
            if not _trusted(row["evidence"], target_card, sources):
                premise_issues.append(_issue("unverified_installation_premise", "Installation evidence remains unavailable", status="UNKNOWN", envelope_id=row["id"]))
            missing = set(row["required_action_ids"]) - active.get(target_id, set())
            if missing:
                premise_issues.append(_issue("missing_installation_action", "Supplied installation premise is not declared active", status="UNKNOWN", required_action_ids=sorted(missing)))
        for name, dimension in target_card["dimensions"].items():
            if name != "height_mm" and not _trusted(dimension["measurement"], target_card, sources):
                path_issues.append(_issue("unverified_card_dimension", "Moving 2D source dimension is unavailable", status="UNKNOWN", dimension=name))
    staging_value = _bound_value(request["exterior_staging"], "staging", sources, documents, path_issues)
    clearance_value = _bound_value(request["transport_clearance"], "transport_clearance", sources, documents, path_issues)
    body_local = Rect(0.0, 0.0, target.rect.width, target.rect.height)
    moving = [("body", body_local)]
    if clearance_value is not None:
        local = _shape(clearance_value)
        if target.rotated:
            local = Rect(spec.depth_mm - local.top, local.x, local.height, local.width)
        moving.append(("transport_clearance", local))
    poses = request["poses"]
    if poses[-1]["x_mm"] != target.rect.x or poses[-1]["y_mm"] != target.rect.y:
        path_issues.append(_issue("final_pose_mismatch", "Path must end at the exact actual installed lower-left pose"))
    if any(pose["rotated"] != target.rotated for pose in poses):
        path_issues.append(_issue("unsupported_moving_rotation", "Orientation changes and differing installed orientation need a separate motion model", status="UNKNOWN"))
    entry = building.layout.external_entry
    entry_issues = _BoundedFindings(limits.max_findings)
    if entry is None or entry.id != request["external_entry_id"]:
        entry_issues.append(_issue("external_entry_mismatch", "Installation must use the actual explicitly declared external entry"))
    # Detached material-only projection: window metadata and adjacency-generated
    # cuts cannot open a passage. Every actual contour, explicit door, entry and
    # original wall thickness is retained; provenance always hashes the original.
    explicit_pairs = {tuple(sorted((door.room_a, door.room_b))) for door in building.layout.doors}
    material_spec = replace(building.layout, windows=(), rooms=tuple(replace(room,
        needs_daylight=False, required_adjacency=tuple(other for other in room.required_adjacency
            if tuple(sorted((room.id, other))) in explicit_pairs)) for room in building.layout.rooms))
    raw["wall_material_projection"] = {"windows_closed": True, "automatic_openings_closed": True,
        "original_spec_sha256": _digest(building.layout.to_dict()), "material_spec_sha256": _digest(material_spec.to_dict())}
    wall_plan = None
    try:
        wall_plan = build_orthogonal_wall_plan(material_spec, layout, include_windows=False)
    except (ValueError, KeyError) as exc:
        path_issues.append(_issue("unverified_wall_geometry", "Actual wall material could not be projected: " + str(exc)[:256], status="UNKNOWN"))
        if entry is not None and entry.id == request["external_entry_id"]:
            entry_issues.append(_issue("unverified_external_entry", "Complete entry material geometry is unavailable", status="UNKNOWN"))
    room_geometries = []
    for room_id, shape in layout.placements.items():
        geometry = Polygon(shape.vertices) if type(shape) is OrthogonalPolygon else box(shape.x, shape.y, shape.right, shape.top)
        if not geometry.is_valid or geometry.area <= 0:
            path_issues.append(_issue("invalid_room_geometry", "Actual room contour is invalid", room_id=room_id))
        else:
            room_geometries.append(geometry)
    if set(layout.placements) != {room.id for room in building.layout.rooms} or set(placements) != set(specs):
        path_issues.append(_issue("incomplete_source_inventory", "All actual rooms and installed equipment are required", status="UNKNOWN"))
    floor = unary_union(room_geometries)
    staging_obstacles = []
    if staging_value is not None:
        try:
            floor = unary_union((floor, _geometry(_shape(staging_value["floor"], unit_optional=True))))
            staging_obstacles = [_geometry(_shape(value, unit_optional=True)) for value in staging_value["obstacles"]]
        except _InputError as exc:
            path_issues.append(_issue(exc.code, str(exc), status=exc.status))
    bodies = {key: box(item.rect.x, item.rect.y, item.rect.right, item.rect.top)
        for key, item in placements.items() if key != target_id}
    operating = []
    for equipment_id, card in bound_cards.items():
        if equipment_id == target_id or equipment_id in stopped:
            continue
        placed = placements.get(equipment_id)
        if placed is None:
            continue
        for row in card["envelopes"]:
            if row["role"] != "operating" or row["scenario"] != "production":
                continue
            if not _trusted(row["evidence"], card, sources) or _ring(row["evidence"]["value"]) is None:
                path_issues.append(_issue("unverified_neighbor_operating_geometry", "Neighbor production reservation remains unavailable", status="UNKNOWN", equipment_id=equipment_id))
                continue
            points = [_world(point, specs[equipment_id], placed) for point in _ring(row["evidence"]["value"])]
            if any(not _number(value, lower=-1000000, upper=1000000) for point in points for value in point):
                path_issues.append(_issue("operating_world_coordinate_bound", "Neighbor reservation exceeds its world bounds", equipment_id=equipment_id))
                continue
            geometry = Polygon(points)
            if not geometry.is_valid or geometry.area <= 0:
                path_issues.append(_issue("unrepresentable_neighbor_operating_geometry", "Neighbor reservation is unavailable at current precision", status="UNKNOWN", equipment_id=equipment_id))
            else:
                operating.append((equipment_id, row["id"], geometry))
    if wall_plan is not None and entry is not None and entry.id == request["external_entry_id"]:
        opening = next((item for item in wall_plan.openings if item.external and item.id == entry.id), None)
        if opening is None:
            entry_issues.append(_issue("external_entry_unresolved", "Actual source entry has no resolved complete opening"))
        else:
            start = Rect(poses[0]["x_mm"], poses[0]["y_mm"], body_local.width, body_local.height)
            half = building.layout.wall_thickness_mm / 2
            outside = {"left": start.right <= opening.fixed - half, "right": start.x >= opening.fixed + half,
                "bottom": start.top <= opening.fixed - half, "top": start.y >= opening.fixed + half}[entry.side]
            if not outside:
                entry_issues.append(_issue("partial_external_start", "Initial whole body must lie fully outside the actual entry wall band"))
    # Unsupported motion between waypoints never obscures a known conflict at
    # a literal supplied pose. Each source-permitted 0/90 degree pose has an
    # independent full body; no diagonal bounding rectangle is invented.
    raw["pose_validation"] = []
    for index, pose in enumerate(poses):
        pose_issues = _BoundedFindings(limits.max_findings)
        record = {"index": index, "pose": dict(pose), "geometry": []}
        raw["pose_validation"].append(record)
        if pose["rotated"] and not spec.rotation_allowed:
            pose_issues.append(_issue("unsupported_pose_rotation", "Supplied pose rotation is not source-permitted", status="UNKNOWN", pose_index=index))
            record["status"] = "UNKNOWN"
            path_issues.extend(pose_issues)
            continue
        width, depth = (spec.depth_mm, spec.width_mm) if pose["rotated"] else (spec.width_mm, spec.depth_mm)
        pose_roles = [("body", Rect(0.0, 0.0, width, depth))]
        if clearance_value is not None:
            local = _shape(clearance_value)
            if pose["rotated"]:
                local = Rect(spec.depth_mm - local.top, local.x, local.height, local.width)
            pose_roles.append(("transport_clearance", local))
        for role, local in pose_roles:
            rect = Rect(pose["x_mm"] + local.x, pose["y_mm"] + local.y, local.width, local.height)
            geometry = box(rect.x, rect.y, rect.right, rect.top)
            record["geometry"].append({"role": role, "x_mm": rect.x, "y_mm": rect.y,
                "width_mm": rect.width, "depth_mm": rect.height})
            if staging_value is not None and not floor.covers(geometry):
                pose_issues.append(_issue("pose_floor_coverage", "Whole supplied pose leaves actual room/staging floor coverage", pose_index=index, role=role,
                    outside_area_mm2=geometry.difference(floor).area))
            for other_id, body in bodies.items():
                area = geometry.intersection(body).area
                if area > 0:
                    pose_issues.append(_issue("pose_body_collision", "Literal whole-body pose intersects another physical body", pose_index=index,
                        role=role, related_equipment_id=other_id, overlap_area_mm2=area))
            for other_id, envelope_id, reservation in operating:
                area = geometry.intersection(reservation).area
                if area > 0:
                    pose_issues.append(_issue("pose_operating_collision", "Literal pose intersects a neighbor production reservation", pose_index=index,
                        role=role, related_equipment_id=other_id, envelope_id=envelope_id, overlap_area_mm2=area))
            for obstacle_index, obstacle in enumerate(staging_obstacles):
                area = geometry.intersection(obstacle).area
                if area > 0:
                    pose_issues.append(_issue("pose_staging_obstacle_collision", "Literal pose intersects a supplied exterior obstacle", pose_index=index,
                        role=role, obstacle_index=obstacle_index, overlap_area_mm2=area))
            if wall_plan is not None:
                area = geometry.intersection(wall_plan.geometry).area
                if area > 0:
                    pose_issues.append(_issue("pose_wall_collision", "Literal pose intersects actual closed wall material", pose_index=index,
                        role=role, overlap_area_mm2=area))
        record["status"] = _status(pose_issues)
        path_issues.extend(pose_issues)
    crossing_seen = set()
    for index, (first, second) in enumerate(zip(poses, poses[1:])):
        segment = {"index": index, "from_pose": dict(first), "to_pose": dict(second), "sweeps": []}
        raw["segments"].append(segment)
        if first["rotated"] != target.rotated or second["rotated"] != target.rotated or first["x_mm"] != second["x_mm"] and first["y_mm"] != second["y_mm"]:
            segment["status"] = "UNKNOWN"
            path_issues.append(_issue("unsupported_path_segment", "Only fixed-orientation axis translations are checked", status="UNKNOWN", segment_index=index))
            continue
        segment_issues = _BoundedFindings(limits.max_findings)
        for role, local in moving:
            sweep = _sweep(first, second, local)
            swept = box(sweep.x, sweep.y, sweep.right, sweep.top)
            segment["sweeps"].append({"role": role, "x_mm": sweep.x, "y_mm": sweep.y,
                "width_mm": sweep.width, "depth_mm": sweep.height})
            if staging_value is not None and not floor.covers(swept):
                segment_issues.append(_issue("swept_floor_coverage", "Whole swept body or clearance leaves the supplied actual floor/staging coverage", segment_index=index, role=role, outside_area_mm2=swept.difference(floor).area))
            for other_id, body in bodies.items():
                area = swept.intersection(body).area
                if area > 0:
                    segment_issues.append(_issue("swept_body_collision", "Whole supplied segment intersects an installed physical body", segment_index=index, role=role, related_equipment_id=other_id, overlap_area_mm2=area))
            for other_id, envelope_id, geometry in operating:
                area = swept.intersection(geometry).area
                if area > 0:
                    segment_issues.append(_issue("swept_operating_collision", "Neighbor production reservation intersects the whole supplied motion", segment_index=index, role=role, related_equipment_id=other_id, envelope_id=envelope_id, overlap_area_mm2=area))
            for obstacle_index, obstacle in enumerate(staging_obstacles):
                area = swept.intersection(obstacle).area
                if area > 0:
                    segment_issues.append(_issue("swept_staging_obstacle_collision", "Supplied source-bound exterior obstacle intersects the whole segment", segment_index=index, role=role, obstacle_index=obstacle_index, overlap_area_mm2=area))
            if wall_plan is not None:
                area = swept.intersection(wall_plan.geometry).area
                if area > 0:
                    segment_issues.append(_issue("swept_wall_collision", "Whole supplied segment intersects actual closed wall material", segment_index=index, role=role, overlap_area_mm2=area))
                for opening in wall_plan.openings:
                    record = _opening_record(opening, sweep, first, second, local, building.layout.wall_thickness_mm, index)
                    if record is None:
                        continue
                    record["role"] = role
                    if not record["fits"]:
                        finding = _issue("opening_too_narrow", "Actual complete opening cannot contain the whole transverse moving envelope", **record)
                        segment_issues.append(finding)
                        if opening.external and opening.id == request["external_entry_id"]:
                            entry_issues.append(finding)
                    if role == "body" and opening.id not in crossing_seen:
                        raw["opening_crossings"].append(record)
                        crossing_seen.add(opening.id)
        segment["status"] = _status(segment_issues)
        path_issues.extend(segment_issues)
    if entry is not None and entry.id == request["external_entry_id"]:
        records = [record for record in raw["opening_crossings"] if record["opening_id"] == entry.id]
        unsupported = any(row["code"] in {"unsupported_path_segment", "unsupported_moving_rotation"} for row in path_issues)
        # A waypoint may split a legitimate translation inside the wall band.
        # Exact checked continuity plus exterior/installed whole-body endpoints
        # establishes complete traversal without requiring a single long step.
        if records and not unsupported and not entry_issues:
            opening = next((item for item in wall_plan.openings if item.id == entry.id), None) if wall_plan else None
            if opening is not None:
                end = target.rect
                half = building.layout.wall_thickness_mm / 2
                inside = {"left": end.x >= opening.fixed + half, "right": end.right <= opening.fixed - half,
                    "bottom": end.y >= opening.fixed + half, "top": end.top <= opening.fixed - half}[entry.side]
                if inside:
                    records[0]["full_wall_thickness_crossing"] = True
        if wall_plan is not None and (not records or not any(record["full_wall_thickness_crossing"] for record in records)):
            entry_issues.append(_issue("incomplete_external_crossing", "Complete crossing of the actual external entry is not established",
                status="UNKNOWN" if unsupported else "FAIL"))
    raw["entry_validation"] = {"status": _status(entry_issues), "external_entry_id": request["external_entry_id"], "issues": entry_issues}
    path_issues.extend(row for row in entry_issues if row["code"] != "opening_too_narrow")
    raw["source_binding_status"] = _status([row for row in path_issues if row["code"] in {"source_binding_mismatch", "library_binding", "card_binding_mismatch", "card_selection_mismatch", "missing_bound_card", "unverified_card_provenance"}])
    raw["declared_premises_status"] = _status(premise_issues)
    raw["local_2d_path_status"] = _status(path_issues + premise_issues)
    issues.extend(path_issues)
    issues.extend(premise_issues)
    if issues.overflow and raw["local_2d_path_status"] == "PASS":
        raw["local_2d_path_status"] = "UNKNOWN"
    raw["status"] = "FAIL" if any(row["status"] == "FAIL" for row in issues) else "UNKNOWN"
    raw["conditionally_inactive_operating_ids"] = sorted(stopped - {target_id})
    raw["finding_inventory"] = {"status": "INCOMPLETE" if issues.overflow or path_issues.overflow or premise_issues.overflow else "COMPLETE",
        "evaluated_findings": path_issues.total + premise_issues.total, "returned_findings": len(issues)}
    return _freeze(raw)

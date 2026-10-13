"""Independent bounded checks of supplied local equipment scenario geometry.

PASS is available only on named local predicates. Full scenarios, action
execution, routes from external access and 3D replacement feasibility remain
UNKNOWN. A source-bound literal collision is FAIL even if another input is
unknown; failed library binding never makes library contours authoritative.
"""

from dataclasses import dataclass, fields, is_dataclass, replace
import json

from shapely.geometry import Point, Polygon, box

from .building import BuildingIR
from .equipment import EquipmentLayoutResult, EquipmentPlacement, clearance_rect, validate_equipment_layout
from .equipment_card_projection import _original_equipment, _placement_frame
from .engineering_library import (EngineeringLibraryContract, EngineeringLibraryLimits, _digest, _number,
                                  _raw_json_preflight, _strict_json, _utf8_size, validate_engineering_library)
from .models import LayoutIR, LayoutResult, Rect, RoomSpec
from .orthogonal import OrthogonalPolygon
from .orthogonal_projection import build_orthogonal_wall_plan
from .orthogonal_validation import validate_orthogonal_equipment
from .walls import build_wall_plan


_SCENARIOS = ("production", "maintenance", "installation_replacement")


@dataclass(frozen=True)
class EquipmentScenarioLimits:
    max_equipment: int = 32
    max_rooms: int = 64
    max_source_objects: int = 1024
    max_total_utf8_bytes: int = 4194304
    max_nodes: int = 131072
    max_depth: int = 32
    max_vertices: int = 16384
    max_pair_checks: int = 131072
    max_findings: int = 256


@dataclass(frozen=True)
class EquipmentScenarioReport:
    _json: str
    status: str

    @property
    def ok(self):
        return False

    def to_dict(self):
        _utf8_size(self._json, EquipmentScenarioLimits().max_total_utf8_bytes, "$")
        _raw_json_preflight(self._json, EquipmentScenarioLimits())
        return _strict_json(self._json)


class _InputError(ValueError):
    def __init__(self, code, message, status="FAIL"):
        self.code, self.status = code, status
        super().__init__(message)


class _BoundedFindings(list):
    """Limit delivery memory while retaining a known failure and honest gaps."""

    def __init__(self, limit):
        super().__init__()
        self.limit, self.total, self.first_failure = limit, 0, None

    @property
    def overflow(self):
        return self.total > self.limit

    def append(self, value):
        self.total += 1
        if value["status"] == "FAIL" and self.first_failure is None:
            self.first_failure = value
        if len(self) < self.limit:
            super().append(value)
        else:
            self[-1] = _issue("finding_budget_exceeded", "Finding inventory is incomplete; all geometry predicates were still checked",
                status="FAIL" if self.first_failure is not None else "UNKNOWN", omitted_findings=self.total - self.limit + 1,
                first_known_failure=self.first_failure)

    def extend(self, values):
        for value in values:
            self.append(value)


def _issue(code, message, *, status="FAIL", **evidence):
    return {"code": code, "status": status, "message": message, **evidence}


def _status(issues, *, complete=True):
    return "FAIL" if any(row["status"] == "FAIL" for row in issues) else "UNKNOWN" if not complete or issues else "PASS"


def _freeze(raw):
    encoder = json.JSONEncoder(sort_keys=True, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
    size, chunks = 0, []
    for chunk in encoder.iterencode(raw):
        size += len(chunk.encode("utf-8"))
        if size > EquipmentScenarioLimits().max_total_utf8_bytes:
            return _freeze(_compact_report(raw))
        chunks.append(chunk)
    return EquipmentScenarioReport("".join(chunks), raw["status"])


def _compact_report(raw):
    """Bound delivery, retaining exact source IDs and known failures, not PASS."""
    def selected(rows, limit):
        values = list(rows[:limit])
        failure = next((row for row in rows if row["status"] == "FAIL"), None)
        if failure is not None and failure not in values:
            values.append(failure)
        return values

    output = _base_report()
    output.update({key: raw[key] for key in ("source", "limits", "workload", "policy") if key in raw})
    output["status"] = "FAIL" if raw["status"] == "FAIL" else "UNKNOWN"
    output["source_binding"] = {"status": raw["source_binding"]["status"]}
    canonical = raw["canonical_equipment"]["report"]
    output["canonical_equipment"] = {"status": raw["canonical_equipment"]["status"], "inventory_status": "INCOMPLETE",
                                      "report": {"issues": canonical["issues"][:32]} if canonical is not None else None}
    output["issues"] = selected(raw["issues"], 32) + [_issue("output_budget_exceeded", "Detailed delivery inventory exceeds its byte bound", status="UNKNOWN")]
    output["output_inventory"] = {"status": "INCOMPLETE", "utf8_byte_limit": EquipmentScenarioLimits().max_total_utf8_bytes}
    for name, scenario in raw["scenarios"].items():
        current = output["scenarios"][name]
        for key in ("status", "local_geometry_status", "declared_premises_status"):
            current[key] = "FAIL" if scenario[key] == "FAIL" else "UNKNOWN"
        current["issues"] = selected(scenario["issues"], 16) + [_issue("output_budget_exceeded", "Detailed scenario inventory was omitted", status="UNKNOWN")]
        current["omitted_geometry_rows"] = len(scenario["geometry"])
        current["omitted_interaction_points"] = len(scenario["interaction_points"])
    return output


def _base_report(issues=()):
    return {"schema_version": 1, "scope": "Supplied local 2D equipment reservations only",
            "status": "FAIL" if any(row["status"] == "FAIL" for row in issues) else "UNKNOWN", "ok": False,
            "policy": {"name": "source_local_scenarios_2d_v1", "host_coverage": "Exact declared room contour",
                       "collision": "Positive-area intersection between different equipment owners",
                       "point_blockage": "Strict point interior of another canonical body; no invented radius",
                       "neighbor_occupancy": "Production continues unless its own source-bound stop is explicitly declared active",
                       "stop_effect": "Conditionally suppress own operating reservation only; physical body remains",
                       "temporary_actions": "No geometric effect inferred from description text",
                       "wall_obstruction": "Fresh original B3 material geometry; unsupported projection remains UNKNOWN",
                       "same_owner_envelopes": "May overlap; no collision between roles of the same equipment"},
            "source": {}, "source_binding": {"status": "UNKNOWN"},
            "canonical_equipment": {"status": "UNKNOWN", "report": None},
            "scenarios": {name: {"status": "UNKNOWN", "local_geometry_status": "UNKNOWN",
                "declared_premises_status": "UNKNOWN", "full_scenario_validation": "UNKNOWN",
                "full_path_validation": "UNKNOWN", "three_dimensional_feasibility": "UNKNOWN",
                "engineering_acceptance": "PENDING", "geometry": [], "interaction_points": [], "actions": [],
                "conditionally_inactive_operating_ids": [], "issues": []} for name in _SCENARIOS},
            "issues": list(issues), "full_facility_validation": "UNKNOWN", "full_scenario_validation": "UNKNOWN",
            "engineering_acceptance": "PENDING", "source_authentication": "UNKNOWN",
            "proven_building_infeasible": False}


def _aggregate_preflight(values, limits):
    """Walk original objects, not asdict/to_dict, before serializers or GEOS."""
    defaults = EquipmentScenarioLimits()
    if type(limits) is not EquipmentScenarioLimits or any(type(getattr(limits, field.name)) is not int or
        not 1 <= getattr(limits, field.name) <= getattr(defaults, field.name) for field in fields(defaults)):
        raise _InputError("invalid_limits", "Limits require positive bounded actual integers")
    active, nodes, size, vertices = set(), 0, 0, 0
    modules = {"layout_configurator.building", "layout_configurator.models", "layout_configurator.equipment",
               "layout_configurator.orthogonal", "layout_configurator.production_program", "layout_configurator.engineering_library"}
    bool_fields = {"rotatable", "needs_daylight", "is_heated", "airlock", "required", "fixed", "rotation_allowed", "rotated"}

    def walk(value, depth):
        nonlocal nodes, size, vertices
        nodes += 1
        if nodes > limits.max_nodes or depth > limits.max_depth:
            raise _InputError("input_budget_exceeded", "Aggregate node/depth budget exceeded", "UNKNOWN")
        if is_dataclass(value) and not isinstance(value, type):
            if type(value).__module__ not in modules:
                raise _InputError("invalid_input_type", "Only original canonical dataclass sources are supported")
            if id(value) in active:
                raise _InputError("invalid_input_type", "Cyclic source is unsupported")
            active.add(id(value))
            for field in fields(value):
                child = getattr(value, field.name)
                if field.name in bool_fields and type(child) is not bool:
                    raise _InputError("invalid_source_scalar", "Original source flags require actual booleans")
                if child is not None and (field.name.endswith(("_mm", "_m2")) or field.name in ("pressure_pa", "objective_value") or
                    type(value) is Rect and field.name in ("x", "y", "width", "height")) and not _number(child):
                    raise _InputError("invalid_source_scalar", "Original source dimensions require bounded finite actual numbers")
                if field.name == "vertices" and type(child) in (tuple, list):
                    vertices += len(child)
                walk(field.name, depth + 1)
                walk(child, depth + 1)
            active.remove(id(value))
        elif type(value) in (dict, list, tuple):
            if id(value) in active:
                raise _InputError("invalid_input_type", "Cyclic source is unsupported")
            active.add(id(value))
            if type(value) is dict:
                if type(value.get("points")) in (tuple, list):
                    vertices += len(value["points"])
                for key, child in value.items():
                    if type(key) is not str:
                        raise _InputError("invalid_input_type", "Object keys must be actual strings")
                    walk(key, depth + 1)
                    walk(child, depth + 1)
            else:
                for child in value:
                    walk(child, depth + 1)
            active.remove(id(value))
            size += 2 + max(0, len(value) - 1)
        elif type(value) is str:
            try:
                size += _utf8_size(value, limits.max_total_utf8_bytes, "$") + 2
            except ValueError as exc:
                raise _InputError("input_budget_exceeded" if "budget" in str(exc) else "invalid_source_scalar", str(exc),
                                  "UNKNOWN" if "budget" in str(exc) else "FAIL") from exc
            size += sum(1 if char in '\"\\\b\f\n\r\t' else 5 if ord(char) < 32 else 0 for char in value)
        elif value is None or type(value) is bool:
            size += 5
        elif _number(value):
            size += len(str(value))
        else:
            raise _InputError("invalid_source_scalar", "Original inputs require bounded finite JSON-compatible values")
        if size > limits.max_total_utf8_bytes or vertices > limits.max_vertices:
            raise _InputError("input_budget_exceeded", "Aggregate byte/vertex budget exceeded", "UNKNOWN")
    walk(values, 0)
    return {"nodes": nodes, "utf8_byte_upper_bound": size, "vertices": vertices}


def _id(value):
    return type(value) is str and 1 <= len(value) <= 128 and value == value.strip() and not any(c in value for c in "\r\n")


def _ids(values, known, name):
    if type(values) not in (tuple, list) or len(values) > len(known) or any(not _id(value) for value in values):
        raise _InputError("invalid_identity", name + " needs bounded explicit identities")
    if len(set(values)) != len(values) or set(values) - set(known):
        raise _InputError("invalid_identity", name + " contains duplicate or unknown identities")
    return tuple(values)


def _rect(rect):
    if type(rect) is not Rect or any(not _number(getattr(rect, key), lower=-1000000, upper=1000000) for key in ("x", "y", "width", "height")) or rect.width <= 0 or rect.height <= 0:
        raise _InputError("invalid_geometry", "Canonical rectangles require bounded actual dimensions")
    if abs(rect.right) > 1000000 or abs(rect.top) > 1000000:
        raise _InputError("invalid_geometry", "Rectangle endpoints exceed the coordinate bound")
    if rect.right <= rect.x or rect.top <= rect.y:
        raise _InputError("unrepresentable_canonical_geometry", "Positive source extent is lost at current world-coordinate precision", "UNKNOWN")


def _prepare(building, layout, equipment, library, mapping, maintenance, installation, actions, limits):
    if type(building) is not BuildingIR or type(building.layout) is not LayoutIR or type(layout) is not LayoutResult or type(equipment) is not EquipmentLayoutResult:
        raise _InputError("invalid_input_type", "Original BuildingIR, LayoutResult and EquipmentLayoutResult are required")
    if type(building.layout.rooms) is not tuple or len(building.layout.rooms) > limits.max_rooms or type(building.equipment) is not tuple or len(building.equipment) > limits.max_equipment:
        raise _InputError("input_budget_exceeded", "Canonical room/equipment count budget exceeded", "UNKNOWN")
    if type(layout.placements) is not dict or len(layout.placements) > limits.max_rooms or type(equipment.placements) not in (tuple, list) or len(equipment.placements) > limits.max_equipment:
        raise _InputError("input_budget_exceeded", "Supplied room/equipment count budget exceeded", "UNKNOWN")
    raw_objects = sum(len(getattr(building.layout, key)) for key in ("rooms", "doors", "windows")) + len(building.layout.boundary.cutouts)
    raw_objects += sum(len(getattr(building, key)) for key in ("equipment", "zones", "flows"))
    if raw_objects > limits.max_source_objects:
        raise _InputError("input_budget_exceeded", "Source object count budget exceeded", "UNKNOWN")
    # The aggregate scan also bounds detached contract strings before parsing.
    _aggregate_preflight((building, layout, equipment, library, mapping, maintenance, installation, actions), limits)
    if type(library) is EngineeringLibraryContract:
        try:
            library = library.to_dict()
        except (ValueError, RecursionError) as exc:
            raise _InputError("invalid_library", str(exc)[:512]) from exc
    decoded_sources = []
    raw_limits = replace(EngineeringLibraryLimits(), max_depth=min(limits.max_depth, EngineeringLibraryLimits().max_depth),
                         max_nodes=min(limits.max_nodes, EngineeringLibraryLimits().max_nodes))
    if type(library) is dict and type(library.get("sources")) is list:
        for source in library["sources"]:
            if type(source) is dict and type(source.get("content")) is str and source["content"].lstrip().startswith(("{", "[")):
                try:
                    _raw_json_preflight(source["content"], raw_limits)
                except ValueError as exc:
                    raise _InputError("input_budget_exceeded", str(exc)[:512], "UNKNOWN") from exc
                try:
                    decoded_sources.append(_strict_json(source["content"]))
                except (ValueError, RecursionError):
                    # Fresh library QA reports the failed binding; no contours from it.
                    continue
    counts = _aggregate_preflight((building, layout, equipment, library, decoded_sources, mapping, maintenance, installation, actions), limits)
    if type(building.layout.grid_mm) is not int or building.layout.grid_mm <= 0 or not _number(building.layout.wall_thickness_mm, lower=0, upper=1000000):
        raise _InputError("invalid_source_scalar", "Source grid/wall dimensions require actual valid numbers")
    if not _number(building.layout.boundary.width_mm, lower=1, upper=1000000) or not _number(building.layout.boundary.height_mm, lower=1, upper=1000000):
        raise _InputError("invalid_source_scalar", "Source boundary dimensions exceed coordinate bounds")
    if any(type(room) is not RoomSpec or not _id(room.id) for room in building.layout.rooms):
        raise _InputError("invalid_identity", "Canonical room declarations need valid identities")
    room_ids = {room.id for room in building.layout.rooms}
    if len(room_ids) != len(building.layout.rooms) or any(not _id(key) for key in layout.placements) or set(layout.placements) - room_ids:
        raise _InputError("invalid_identity", "Room result has duplicate or unknown source identities")
    if type(layout.variant) is not int or not 1 <= layout.variant <= 1000000 or layout.geometry_version not in (None, "0.2"):
        raise _InputError("invalid_identity", "Layout variant/geometry version is unsupported")
    for shape in layout.placements.values():
        if type(shape) is Rect:
            _rect(shape)
        elif type(shape) is OrthogonalPolygon:
            if type(shape.vertices) not in (tuple, list) or not 4 <= len(shape.vertices) <= 256 or any(type(point) not in (tuple, list) or len(point) != 2 or
                any(not _number(value, lower=-1000000, upper=1000000) for value in point) for point in shape.vertices):
                raise _InputError("invalid_geometry", "Actual orthogonal room ring exceeds bounds")
        else:
            raise _InputError("unsupported_geometry", "Only exact canonical rectangular/orthogonal rooms are supported", "UNKNOWN")
    frames = {item.id: _original_equipment(item) for item in building.equipment}
    if len(frames) != len(building.equipment) or any(not _id(key) for key in frames):
        raise _InputError("invalid_identity", "Source equipment identities must be unique")
    building._validate_references()
    specs = {item.id: item for item in building.equipment}
    seen = set()
    for placement in equipment.placements:
        if type(placement) is not EquipmentPlacement or not _id(placement.equipment_id) or placement.equipment_id not in specs or placement.equipment_id in seen:
            raise _InputError("invalid_identity", "Supplied equipment has unknown or duplicate identities")
        _placement_frame(placement, specs[placement.equipment_id])
        _rect(placement.rect)
        _rect(clearance_rect(specs[placement.equipment_id], placement.rect, placement.rotated))
        seen.add(placement.equipment_id)
    if type(mapping) is not dict or len(mapping) > limits.max_equipment or any(not _id(key) or not _id(value) for key, value in mapping.items()) or set(mapping) - set(frames):
        raise _InputError("invalid_card_mapping", "Card mapping must use only declared equipment identities")
    maintenance = _ids(maintenance, frames, "Maintenance targets")
    installation = _ids(installation, frames, "Installation/replacement targets")
    if actions is None:
        actions = {}
    if type(actions) is not dict or set(actions) - set(_SCENARIOS):
        raise _InputError("invalid_actions", "Action declarations must use exactly supported scenario names")
    for scenario, by_equipment in actions.items():
        if type(by_equipment) is not dict or set(by_equipment) - set(frames):
            raise _InputError("invalid_actions", "Action declarations must bind existing equipment")
        for equipment_id, ids in by_equipment.items():
            if not _id(equipment_id) or type(ids) not in (list, tuple) or len(ids) > 32 or any(not _id(value) for value in ids) or len(set(ids)) != len(ids):
                raise _InputError("invalid_actions", "Explicit actions need unique bounded identities")
    raw_cards = library.get("cards", []) if type(library) is dict else []
    cards = {row["id"]: row for row in raw_cards if type(row) is dict and _id(row.get("id"))} if type(raw_cards) is list else {}
    port_count = sum(len(cards[value].get("interaction_points", [])) for value in mapping.values()
                     if value in cards and type(cards[value].get("interaction_points")) is list)
    count = len(frames)
    pair_upper = 3 * (4 * count * max(0, 4 * count - 1) // 2 + port_count * count + 8 * count + 2 * port_count)
    pair_upper += count * max(0, count - 1) // 2 + 4 * count + len(building.layout.rooms)
    if pair_upper > limits.max_pair_checks:
        raise _InputError("input_budget_exceeded", "Aggregate predicate pair budget exceeded before geometry", "UNKNOWN")
    counts["predicate_pair_upper_bound"] = pair_upper
    return (replace(layout, placements=dict(layout.placements)), EquipmentLayoutResult(tuple(equipment.placements)),
            library, dict(mapping), maintenance, installation, {name: {key: tuple(ids) for key, ids in rows.items()} for name, rows in actions.items()}, frames, counts)


def _canonical_equipment(building, layout, equipment):
    if any(type(shape) is OrthogonalPolygon for shape in layout.placements.values()):
        report = validate_orthogonal_equipment(building, layout, equipment)
        raw = report.to_dict()
        status = report.status
    else:
        report = validate_equipment_layout(building, layout, equipment)
        raw = report.to_dict()
        status = "PASS" if report.ok else "FAIL"
    missing_codes = {"missing_equipment", "missing_host_room", "wall_geometry"}
    if set(layout.placements) != {room.id for room in building.layout.rooms} or len(equipment.placements) != len(building.equipment):
        if all(row["code"] in missing_codes for row in raw["issues"]):
            status = "UNKNOWN"
    return {"status": status, "scope": "Unchanged canonical body, directional clearance, host, anchor and wall predicates", "report": raw}


def _ring(value):
    if value["kind"] == "orthogonal_polygon":
        return value["points"]
    if value["kind"] == "rectangle":
        x, y, width, depth = (value[key] for key in ("x", "y", "width", "depth"))
        return [[x, y], [x + width, y], [x + width, y + depth], [x, y + depth], [x, y]]
    return None


def _world(point, spec, placement):
    x, y = point
    return [placement.rect.x + spec.depth_mm - y, placement.rect.y + x] if placement.rotated else [placement.rect.x + x, placement.rect.y + y]


def _evidence(evidence, sources):
    source = sources.get(evidence["source_id"])
    return {"source_id": evidence["source_id"], "source_sha256": source["sha256"] if source else None,
            "source_pointer": evidence["source_pointer"], "basis": evidence["basis"], "status": evidence["status"],
            "value_sha256": _digest(evidence["value"])}


def _trusted(evidence, card, sources):
    source = sources.get(evidence["source_id"])
    return evidence["status"] == "KNOWN" and evidence["basis"] != "assumption" and source is not None and source["usage_rights"]["state"] == "PERMITTED" and source["kind"] != "visual_reference" and (
        evidence["basis"] != "supplied_confirmation" or card["engineer_confirmation"] is not None)


def validate_equipment_scenarios(building, layout, equipment, *, library, card_by_equipment,
                                 maintenance_targets, installation_targets, actions_by_scenario=None,
                                 limits=EquipmentScenarioLimits()):
    """Recompute three explicit local scenarios without solver or route claims.

    Missing target/placement/card/premise evidence is incomplete, not an original
    building infeasibility proof. Source-bound collisions dominate UNKNOWN.
    Library failure permits only independently known canonical body geometry.
    """
    try:
        # Validate limits even before using them as count bounds.
        _aggregate_preflight((), limits)
        (layout, equipment, library, mapping, maintenance, installation, actions, frames, counts) = _prepare(
            building, layout, equipment, library, card_by_equipment, maintenance_targets, installation_targets, actions_by_scenario, limits)
    except (ValueError, RecursionError, TypeError, AttributeError) as exc:
        code = exc.code if isinstance(exc, _InputError) else "invalid_input"
        status = exc.status if isinstance(exc, _InputError) else "FAIL"
        return _freeze(_base_report((_issue(code, str(exc)[:512], status=status),)))
    raw = _base_report()
    raw["issues"] = _BoundedFindings(limits.max_findings)
    for scenario in raw["scenarios"].values():
        scenario["issues"] = _BoundedFindings(limits.max_findings)
    raw["limits"] = {field.name: getattr(limits, field.name) for field in fields(limits)}
    raw["workload"] = counts
    raw["source"] = {"building_sha256": _digest(building.to_dict()), "layout_sha256": _digest(layout.to_dict()),
        "equipment_sha256": _digest(equipment.to_dict(building)), "card_mapping_sha256": _digest(mapping),
        "scenario_requests_sha256": _digest({"maintenance_targets": maintenance, "installation_targets": installation, "actions": actions})}
    admission = validate_engineering_library(library)
    raw["source"]["library_sha256"] = admission.library_sha256
    raw["source_binding"]["library_validation"] = admission.to_dict()
    global_issues = raw["issues"]
    if admission.library is None or admission.binding_status != "PASS":
        global_issues.append(_issue("library_binding", "Library binding is not independently established; library contours are excluded",
                                    status="FAIL" if admission.status == "FAIL" else "UNKNOWN"))
        cards, sources = {}, {}
    else:
        current = admission.library.to_dict()
        cards = {row["id"]: row for row in current["cards"]}
        sources = {row["id"]: row for row in current["sources"]}
    by_equipment = {}
    for equipment_id in frames:
        if equipment_id not in mapping:
            global_issues.append(_issue("missing_card_mapping", "Every declared equipment item needs a supplied card mapping", status="UNKNOWN", equipment_id=equipment_id))
        elif cards:
            card = cards.get(mapping[equipment_id])
            if card is None or card["equipment_binding"]["id"] != equipment_id or card["equipment_binding"]["sha256"] != _digest(frames[equipment_id]):
                global_issues.append(_issue("card_binding_mismatch", "Mapped card does not bind exact original equipment", equipment_id=equipment_id))
            else:
                by_equipment[equipment_id] = card
                source = sources[card["equipment_binding"]["source_id"]]
                if source["usage_rights"]["state"] != "PERMITTED" or source["kind"] == "visual_reference":
                    global_issues.append(_issue("unverified_binding_provenance", "Reusable numeric equipment binding provenance is unavailable",
                                                status="UNKNOWN", equipment_id=equipment_id))
    if any(row["status"] == "FAIL" for row in global_issues):
        by_equipment = {}  # No source-contour authority after failed binding.
    raw["source_binding"]["status"] = _status(global_issues)
    raw["canonical_equipment"] = _canonical_equipment(building, layout, equipment)
    if raw["canonical_equipment"]["status"] != "PASS":
        global_issues.append(_issue("canonical_equipment", "Original canonical equipment predicates are retained",
                                   status=raw["canonical_equipment"]["status"]))
    specs = {item.id: item for item in building.equipment}
    placements = {item.equipment_id: item for item in equipment.placements}
    rooms = {}
    for room_id, shape in layout.placements.items():
        geometry = Polygon(shape.vertices) if type(shape) is OrthogonalPolygon else box(shape.x, shape.y, shape.right, shape.top)
        if not geometry.is_valid or geometry.area <= 0:
            global_issues.append(_issue("invalid_room_geometry", "Original room ring must remain simple and nonempty", room_id=room_id))
        else:
            rooms[room_id] = geometry
    wall_geometry, wall_issue = None, None
    try:
        wall_geometry = (build_orthogonal_wall_plan if any(type(shape) is OrthogonalPolygon for shape in layout.placements.values()) else
                         build_wall_plan)(building.layout, layout).geometry
    except (ValueError, KeyError) as exc:
        wall_issue = _issue("unverified_wall_geometry", "Original wall material projection is unavailable: " + str(exc)[:256], status="UNKNOWN")
    body_geometries = {}
    body_rows = {}
    for equipment_id, placement in placements.items():
        rect = placement.rect
        points = [[rect.x, rect.y], [rect.right, rect.y], [rect.right, rect.top], [rect.x, rect.top], [rect.x, rect.y]]
        body_geometries[equipment_id] = Polygon(points)
        body_rows[equipment_id] = {"equipment_id": equipment_id, "card_id": mapping.get(equipment_id), "id": "canonical-body",
            "role": "body", "room_id": placement.room_id, "world_points_mm": points,
            "authority": "Original canonical EquipmentSpec/EquipmentPlacement",
            "equipment_sha256": _digest(frames[equipment_id])}
    for name in _SCENARIOS:
        scenario = raw["scenarios"][name]
        issues = scenario["issues"]
        targets = set(specs) if name == "production" else set(maintenance if name == "maintenance" else installation)
        complete = len(placements) == len(specs) and set(rooms) == {room.id for room in building.layout.rooms} and len(by_equipment) == len(specs) and raw["source_binding"]["status"] == "PASS"
        if name != "production" and not targets:
            issues.append(_issue("scenario_targets_not_requested", "No explicit scenario targets supplied", status="UNKNOWN"))
            complete = False
        if not complete:
            issues.append(_issue("incomplete_scenario_inputs", "All relevant cards, room geometry, placements and explicit targets are required", status="UNKNOWN"))
        if raw["canonical_equipment"]["status"] != "PASS":
            issues.append(_issue("canonical_equipment", "Original canonical equipment predicates remain required in every local scenario",
                                 status=raw["canonical_equipment"]["status"]))
        if wall_issue is not None:
            issues.append(dict(wall_issue))
            complete = False
        shapes = {key: [(body_rows[key], geometry)] for key, geometry in body_geometries.items()}
        scenario["geometry"] = [body_rows[key] for key in sorted(body_rows)]
        active = {}
        for equipment_id, ids in actions.get(name, {}).items():
            card = by_equipment.get(equipment_id)
            if card is None:
                issues.append(_issue("unbound_action", "An active action needs a freshly bound equipment card", status="UNKNOWN", equipment_id=equipment_id))
                continue
            declared = {row["id"]: row for row in card["scenario_actions"]}
            for action_id in ids:
                row = declared.get(action_id)
                if row is None or row["scenario"] != name:
                    issues.append(_issue("invalid_action_reference", "Active action must be explicitly declared in this scenario", equipment_id=equipment_id, action_id=action_id))
                    continue
                if not _trusted(row["evidence"], card, sources):
                    issues.append(_issue("unverified_action", "Action evidence is unavailable or assumed", status="UNKNOWN", equipment_id=equipment_id, action_id=action_id))
                    continue
                active.setdefault(equipment_id, set()).add(action_id)
                scenario["actions"].append({"equipment_id": equipment_id, "id": action_id, "value": row["evidence"]["value"],
                    "source": _evidence(row["evidence"], sources), "execution_verification": "UNKNOWN"})
        stopped = {row["equipment_id"] for row in scenario["actions"] if row["value"]["kind"] == "production_stopped"}
        scenario["conditionally_inactive_operating_ids"] = sorted(stopped - targets) if name != "production" else []
        for equipment_id, card in sorted(by_equipment.items()):
            placement = placements.get(equipment_id)
            if placement is None:
                continue
            selected = name if equipment_id in targets else "production"
            if selected not in card["applicability"]["scenarios"] or card["applicability"]["conditions"]:
                issues.append(_issue("unsupported_applicability", "Selected equipment/scenario applicability is unresolved", status="UNKNOWN", equipment_id=equipment_id))
                complete = False
                continue
            for field, quantity in card["dimensions"].items():
                if field != "height_mm" and not _trusted(quantity["measurement"], card, sources):
                    issues.append(_issue("unverified_card_dimension", "Card dimension remains unavailable; known independent geometry is still checked", status="UNKNOWN", equipment_id=equipment_id, dimension=field))
                    complete = False
            body = next(row for row in card["envelopes"] if row["role"] == "body")
            value = body["evidence"]["value"]
            spec = specs[equipment_id]
            if not _trusted(body["evidence"], card, sources) or value["kind"] != "rectangle" or any(value[key] != expected for key, expected in
                (("x", 0), ("y", 0), ("width", spec.width_mm), ("depth", spec.depth_mm))):
                issues.append(_issue("unverified_card_body", "Library body capability is not admitted; original canonical body remains authoritative", status="UNKNOWN", equipment_id=equipment_id))
                complete = False
            for row in card["envelopes"]:
                if row["role"] == "body" or row["scenario"] != selected:
                    continue
                if selected == "production" and equipment_id in stopped and name != "production":
                    continue
                evidence = row["evidence"]
                if not _trusted(evidence, card, sources) or _ring(evidence["value"]) is None:
                    issues.append(_issue("unverified_scenario_geometry", "Selected envelope geometry is missing, assumed, restricted or unsupported", status="UNKNOWN", equipment_id=equipment_id, envelope_id=row["id"]))
                    complete = False
                    continue
                if not set(row["required_action_ids"]) <= active.get(equipment_id, set()):
                    issues.append(_issue("missing_scenario_action", "Source envelope premise was not explicitly declared active", status="UNKNOWN", equipment_id=equipment_id,
                                         envelope_id=row["id"], required_action_ids=row["required_action_ids"]))
                points = [_world(point, spec, placement) for point in _ring(evidence["value"])]
                if any(not _number(value, lower=-1000000, upper=1000000) for point in points for value in point):
                    issues.append(_issue("world_coordinate_bound", "Projected contour exceeds capability bounds", equipment_id=equipment_id))
                    complete = False
                    continue
                record = {"equipment_id": equipment_id, "card_id": card["id"], "id": row["id"], "role": row["role"],
                          "room_id": placement.room_id, "world_points_mm": points, "source": _evidence(evidence, sources),
                          "required_action_ids": row["required_action_ids"]}
                polygon = Polygon(points)
                if not polygon.is_valid or polygon.area <= 0:
                    issues.append(_issue("unrepresentable_scenario_geometry", "Literal source reservation is not a valid positive-area world contour at supported precision",
                                         status="UNKNOWN", equipment_id=equipment_id, envelope_id=row["id"]))
                    complete = False
                    continue
                shapes[equipment_id].append((record, polygon))
                scenario["geometry"].append(record)
            for row in card["interaction_points"]:
                if row["scenario"] != selected:
                    continue
                evidence = row["evidence"]
                if not _trusted(evidence, card, sources):
                    issues.append(_issue("unverified_interaction_point", "Named interaction point is unavailable", status="UNKNOWN", equipment_id=equipment_id, point_id=row["id"]))
                    complete = False
                    continue
                value = evidence["value"]
                point = _world([value["x"], value["y"]], spec, placement)
                if any(not _number(coordinate, lower=-1000000, upper=1000000) for coordinate in point):
                    issues.append(_issue("world_coordinate_bound", "Projected point exceeds capability bounds", equipment_id=equipment_id, point_id=row["id"]))
                    complete = False
                    continue
                directions = {"front": "left", "back": "right", "left": "bottom", "right": "top"} if placement.rotated else {
                    "front": "top", "back": "bottom", "left": "left", "right": "right"}
                scenario["interaction_points"].append({"equipment_id": equipment_id, "card_id": card["id"], "id": row["id"], "role": row["role"],
                    "room_id": placement.room_id, "world_point_mm": point, "source_direction": value["direction"],
                    "world_direction": directions[value["direction"]], "source": _evidence(evidence, sources)})
        for equipment_id, rows in shapes.items():
            host = rooms.get(placements[equipment_id].room_id)
            for record, polygon in rows:
                if host is None:
                    complete = False
                elif not host.covers(polygon):
                    issues.append(_issue("envelope_outside_host", "Literal reserved geometry leaves actual declared host contour", equipment_id=equipment_id,
                        envelope_id=record["id"], role=record["role"], outside_area_mm2=polygon.difference(host).area))
                if wall_geometry is not None:
                    wall_area = wall_geometry.intersection(polygon).area
                    if wall_area > 0:
                        issues.append(_issue(record["role"] + "_wall_blocked", "Literal reservation intersects original wall material",
                            equipment_id=equipment_id, envelope_id=record["id"], overlap_area_mm2=wall_area))
            for other_id in sorted(shapes):
                if other_id <= equipment_id:
                    continue
                for left, left_geometry in rows:
                    for right, right_geometry in shapes[other_id]:
                        area = left_geometry.intersection(right_geometry).area
                        if area <= 0:
                            continue
                        bodies = left["role"] == right["role"] == "body"
                        issues.append(_issue("canonical_body_overlap" if bodies else "scenario_envelope_overlap", "Positive-area occupied geometry overlap between equipment owners",
                            equipment_id=equipment_id, related_equipment_id=other_id, envelope_id=left["id"], related_envelope_id=right["id"],
                            roles=[left["role"], right["role"]], overlap_area_mm2=area))
                        for owner, own, other in ((equipment_id, left, other_id), (other_id, right, equipment_id)):
                            if own["role"] in ("service", "panel", "tool_removal"):
                                issues.append(_issue(own["role"] + "_blocked", "Source-bound maintenance reservation intersects another occupied owner",
                                    equipment_id=owner, related_equipment_id=other, envelope_id=own["id"], overlap_area_mm2=area))
        for row in scenario["interaction_points"]:
            point = Point(row["world_point_mm"])
            host = rooms.get(row["room_id"])
            if host is not None and not host.covers(point):
                issues.append(_issue("interaction_point_outside_host", "Named source point leaves actual host geometry", equipment_id=row["equipment_id"], point_id=row["id"]))
            if wall_geometry is not None and wall_geometry.contains(point):
                issues.append(_issue("interaction_point_wall_blocked", "Named source point lies inside original wall material", equipment_id=row["equipment_id"], point_id=row["id"]))
            for other_id, body in body_geometries.items():
                if other_id != row["equipment_id"] and body.contains(point):
                    issues.append(_issue("interaction_point_blocked", "Exact named interaction point lies inside another physical body",
                        equipment_id=row["equipment_id"], related_equipment_id=other_id, point_id=row["id"], world_point_mm=row["world_point_mm"]))
        premise_codes = {"missing_scenario_action", "unbound_action", "unverified_action", "invalid_action_reference"}
        if issues.overflow:
            complete = False
        scenario["finding_inventory"] = {"status": "INCOMPLETE" if issues.overflow else "COMPLETE",
                                         "evaluated_findings": issues.total, "returned_findings": len(issues)}
        geometry_issues = [row for row in issues if row["code"] not in premise_codes]
        premise_issues = [row for row in issues if row["code"] in premise_codes]
        scenario["local_geometry_status"] = _status(geometry_issues, complete=complete)
        scenario["declared_premises_status"] = _status(premise_issues, complete=complete)
        scenario["status"] = "FAIL" if any(row["status"] == "FAIL" for row in issues) else "UNKNOWN"
        global_issues.extend({**row, "scenario": name} for row in issues)
    raw["status"] = "FAIL" if any(row["status"] == "FAIL" for row in global_issues) else "UNKNOWN"
    raw["finding_inventory"] = {"status": "INCOMPLETE" if global_issues.overflow else "COMPLETE",
                                "returned_findings": len(global_issues)}
    return _freeze(raw)

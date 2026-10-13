"""Bounded explicit JSON review of supplied source, geometry and actual routes."""

from dataclasses import dataclass, replace
import hashlib
import json
import math
from pathlib import Path

from .building import BuildingIR
from .equipment import EquipmentLayoutResult, EquipmentPlacement
from .flows import FlowRoute, FlowRoutingResult
from .models import LayoutResult, Rect
from .production_program import _json_bytes, _preflight, _schema_issues
from .result_geometry import parse_layout_result
from .schema import DEFAULT_SCHEMA_PATH, load_building_schema, load_json_schema
from .transfer_program import (TransferProgramContract, TransferProgramLimits, _contract_raw,
                               _limits_valid, _model_preflight, _raw_limits)
from .transfer_validation import validate_transfer_routes


def _object(raw, required, optional=()):
    if type(raw) is not dict or not set(required) <= set(raw) or set(raw) - set(required) - set(optional):
        raise ValueError("Review data requires supported fields and every mandatory field")


def _number(value, *, positive=False):
    if type(value) not in (int, float) or not math.isfinite(value) or positive and value <= 0:
        raise ValueError("Review geometry requires finite nonboolean numbers and positive dimensions")
    return value


def _budget_report(original, message):
    return {"status": "UNKNOWN", "ok": False, "scope": "supplied_transfer_review_input",
        "geometry_status": "NOT_EVALUATED", "permission_status": "UNKNOWN", "mechanism_status": "NOT_EVALUATED",
        "engineering_acceptance": "PENDING", "authenticated_approval": "UNKNOWN",
        "full_facility_validation": {"status": "UNKNOWN", "ok": False}, "proven_building_infeasible": False,
        "review_source": {"file_sha256": hashlib.sha256(original).hexdigest(), "bytes": len(original),
                          "scope": "exact_supplied_review_json"},
        "issues": [{"status": "UNKNOWN", "code": "input_budget_exceeded", "path": "$", "message": message}]}


@dataclass(frozen=True)
class TransferReviewInput:
    building: BuildingIR
    layout: LayoutResult
    routing: FlowRoutingResult
    equipment: EquipmentLayoutResult | None
    production_program: dict
    transfer_program: dict


def read_review_source(path, *, limits=TransferProgramLimits()):
    """Read a bounded original JSON snapshot without interpreting its semantics."""
    if not _limits_valid(limits):
        raise ValueError("Review requires bounded limits")
    source = Path(path)
    if source.stat().st_size > limits.max_input_bytes:
        raise ValueError("Transfer review exceeds its input byte budget")
    with source.open("rb") as stream:
        original = stream.read(limits.max_input_bytes + 1)
    if len(original) > limits.max_input_bytes:
        raise ValueError("Transfer review exceeds its input byte budget")
    # The shared contract parser bounds UTF-8, nesting and nodes before JSON.
    raw = _contract_raw(TransferProgramContract(original.decode("utf-8")), TransferProgramContract,
                        replace(limits, max_program_bytes=limits.max_input_bytes))
    return original, raw


def admit_transfer_review(raw, original, *, limits=TransferProgramLimits(), geometry_work_extra=0):
    """Admit exact source objects once after complete raw resource checks.

    A downstream independent reviewer can supply its bounded additional
    geometry admission score before any layout geometry is constructed.
    """
    if not _limits_valid(limits) or type(geometry_work_extra) is not int or geometry_work_extra < 0:
        raise ValueError("Review requires bounded limits and a nonnegative actual work score")
    _preflight(raw, _raw_limits(limits, byte_limit=limits.max_input_bytes))
    _object(raw, ("building", "production_program", "transfer_program", "layout", "flow_routes"), ("equipment",))
    _model_preflight(raw, limits)
    building_raw = raw["building"]
    if type(building_raw) is not dict or type(building_raw.get("layout")) is not dict:
        raise ValueError("Review requires complete canonical BuildingIR source")
    for field, maximum in (("zones", limits.max_zones), ("equipment", limits.max_equipment), ("flows", limits.max_flows)):
        if type(building_raw.get(field)) is list and len(building_raw[field]) > maximum:
            raise ValueError("Review canonical source entity budget exceeded")
    rooms = building_raw["layout"].get("rooms")
    if type(rooms) is list and len(rooms) > limits.max_rooms:
        raise ValueError("Review canonical room budget exceeded")
    embedded = "production_program" in building_raw
    source_issues = _schema_issues(building_raw, load_building_schema(include_production_program=embedded))
    source_issues += _schema_issues(building_raw["layout"], load_json_schema(DEFAULT_SCHEMA_PATH))
    if source_issues:
        raise ValueError("Review source must satisfy original canonical schemas: " + source_issues[0].message)
    building = BuildingIR.from_mapping(building_raw, include_production_program=embedded)
    if _json_bytes(building.to_dict()) != _json_bytes(building_raw):
        raise ValueError("Review admission cannot change canonical source scalar types or fields")
    if type(raw["layout"]) is not dict or type(raw["layout"].get("rooms")) is not dict or len(raw["layout"]["rooms"]) > limits.max_rooms:
        raise ValueError("Review result room budget must hold before geometry construction")
    routing_raw = raw["flow_routes"]
    _object(routing_raw, ("routes",))
    if type(routing_raw["routes"]) is not list or len(routing_raw["routes"]) > limits.max_routes:
        raise ValueError("Review requires a bounded actual routes array")
    routes, total_points = [], 0
    route_fields = ("flow_id", "from_id", "to_id", "from_room_id", "to_room_id", "room_path", "points", "minimum_clear_width_mm")
    for route in routing_raw["routes"]:
        _object(route, route_fields)
        points = route["points"]
        if type(points) is not list or len(points) > limits.max_route_points:
            raise ValueError("Review route exceeds its per-route point budget")
        total_points += len(points)
        if total_points > limits.max_total_points:
            return _budget_report(original, "Total actual point budget exceeded before geometry construction")
        values = []
        for point in points:
            _object(point, ("x", "y"))
            values.append((_number(point["x"]), _number(point["y"])))
        if type(route["room_path"]) is not list:
            raise ValueError("Review room_path requires literal source IDs")
        routes.append(FlowRoute(*(route[field] for field in route_fields[:5]), tuple(route["room_path"]),
            tuple(values), _number(route["minimum_clear_width_mm"], positive=True)))
    equipment_raw = raw.get("equipment", [])
    if type(equipment_raw) is not list or len(equipment_raw) > limits.max_equipment:
        raise ValueError("Review requires bounded canonical equipment placements")
    # Apply the same conservative admission score to raw shape counts before
    # parse_layout_result constructs and checks any GEOS geometry.
    edges = []
    for footprint in raw["layout"]["rooms"].values():
        if type(footprint) is not dict:
            raise ValueError("Review requires actual canonical room geometry objects")
        vertices = footprint.get("vertices")
        if footprint.get("kind") == "orthogonal":
            if type(vertices) is not list or not 4 <= len(vertices) <= 257:
                raise ValueError("Review orthogonal vertex budget exceeded")
            edges.append(len(vertices))
        else:
            edges.append(4)
    room_edges, maximum_edges = sum(edges), max(edges, default=0)
    openings = len(building.layout.doors) + len(building.layout.windows) + int(building.layout.external_entry is not None)
    if openings > limits.max_openings:
        return _budget_report(original, "Source opening budget exceeded before geometry construction")
    equipment_count = len(equipment_raw)
    work = geometry_work_extra + sum(max(0, len(route.points) - 1) for route in routes) * (room_edges + len(building.layout.rooms) + openings + equipment_count)
    work += room_edges**2 + 4*equipment_count*room_edges + 16*equipment_count**2
    work += (openings + len(building.layout.rooms)**2)*maximum_edges**2
    work += (4 + 4*len(building.layout.boundary.cutouts))*room_edges
    if work > limits.max_geometry_checks:
        return _budget_report(original, "Geometry work score exceeded before geometry construction")
    layout = parse_layout_result(raw["layout"])
    placements = []
    for item in equipment_raw:
        _object(item, ("equipment_id", "room_id", "x", "y", "width", "depth", "rotated"), ("clearance",))
        if type(item["rotated"]) is not bool or any(type(item[key]) is not str for key in ("equipment_id", "room_id")):
            raise ValueError("Review equipment requires literal source IDs and boolean rotation")
        placements.append(EquipmentPlacement(item["equipment_id"], item["room_id"],
            Rect(_number(item["x"]), _number(item["y"]), _number(item["width"], positive=True),
                 _number(item["depth"], positive=True)), item["rotated"]))
    equipment = EquipmentLayoutResult(tuple(placements)) if "equipment" in raw else None
    if equipment is not None:
        rebuilt = equipment.to_dict(building)["equipment"]
        for supplied, current in zip(equipment_raw, rebuilt):
            if "clearance" in supplied and _json_bytes(supplied["clearance"]) != _json_bytes(current["clearance"]):
                raise ValueError("Saved equipment clearance differs from original source-derived geometry")
    return TransferReviewInput(building, layout, FlowRoutingResult(tuple(routes)), equipment,
                              raw["production_program"], raw["transfer_program"])


def load_transfer_review(path, *, include_windows=False, limits=TransferProgramLimits()):
    """Check literal supplied routes without generating, normalizing or approving.

    JSON requires building, production_program, transfer_program, layout and
    flow_routes, plus optional canonical equipment placements. This explicit
    source-only boundary does not admit unsupported production generation.
    """
    if type(include_windows) is not bool:
        raise ValueError("Review requires a boolean window control")
    original, raw = read_review_source(path, limits=limits)
    admitted = admit_transfer_review(raw, original, limits=limits)
    if type(admitted) is dict:
        return admitted
    report = validate_transfer_routes(admitted.building, admitted.layout, admitted.routing, admitted.transfer_program,
        production_program=admitted.production_program, equipment=admitted.equipment,
        include_windows=include_windows, limits=limits).to_dict()
    report["review_source"] = {"file_sha256": hashlib.sha256(original).hexdigest(),
                               "bytes": len(original), "scope": "exact_supplied_review_json"}
    return report

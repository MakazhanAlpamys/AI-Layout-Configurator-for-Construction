"""Bounded explicit review of environment declarations and actual geometry."""

import hashlib

from .environment_program import (EnvironmentProgramLimits, _environment_preflight, _limits_valid,
                                  _parent_limits)
from .environment_validation import validate_environment_layout
from .production_program import _InputFailure, _schema_issues
from .resources import resource_path
from .schema import load_json_schema
from .transfer_program import _model_preflight
from .transfer_review import _budget_report, _object, admit_transfer_review, read_review_source


def load_environment_review(path, *, include_windows=False, limits=EnvironmentProgramLimits()):
    """Check supplied routes, regions and pressure claims without generating.

    Requires every transfer-review field plus environment_program. An example
    without actual routes cannot silently authorize geometry.
    """
    if not _limits_valid(limits) or type(include_windows) is not bool:
        raise ValueError("Environment review requires bounded limits and a boolean window control")
    parent_limits = _parent_limits(limits)
    original, raw = read_review_source(path, limits=parent_limits)

    def stopped(message):
        result = _budget_report(original, message)
        result.update(scope="supplied_environment_review_input", pressure_declared_status="UNKNOWN",
                      pressure_resolved_status="UNKNOWN", pressure_measured_status="UNKNOWN")
        return result

    _object(raw, ("building", "production_program", "transfer_program", "environment_program", "layout", "flow_routes"),
            ("equipment",))
    try:
        environment, counts = _environment_preflight(raw["environment_program"], limits)
        _model_preflight(raw, limits)
    except _InputFailure as exc:
        if exc.issue.code == "input_budget_exceeded":
            return stopped(exc.issue.message)
        raise
    errors = _schema_issues(environment, load_json_schema(resource_path("schemas/environment_program.schema.json")))
    if errors:
        raise ValueError("Environment declarations must satisfy the original closed schema: " + errors[0].message)
    # Include extra region/network work before parse_layout_result constructs
    # any GEOS geometry; do not validate only the cheaper transfer boundary.
    layout_raw, building_raw = raw["layout"], raw["building"]
    if type(layout_raw) is not dict or type(layout_raw.get("rooms")) is not dict or type(building_raw) is not dict or type(building_raw.get("layout")) is not dict:
        raise ValueError("Environment review requires original canonical source and room objects")
    room_edges = 0
    for row in layout_raw["rooms"].values():
        if type(row) is not dict:
            raise ValueError("Review requires literal room footprints")
        if row.get("kind") == "orthogonal":
            if type(row.get("vertices")) is not list:
                raise ValueError("Review requires literal orthogonal vertices")
            room_edges += len(row["vertices"])
        else:
            room_edges += 4
    source_layout = building_raw["layout"]
    equipment_raw = raw.get("equipment", [])
    if type(equipment_raw) is not list or any(type(source_layout.get(name, [])) is not list for name in ("doors", "windows")):
        raise ValueError("Review requires canonical equipment and opening arrays")
    openings = len(source_layout.get("doors", [])) + len(source_layout.get("windows", []))
    openings += int(source_layout.get("external_entry") is not None)
    vertices = counts["zone_vertices"]
    extra = vertices ** 2 + vertices * (room_edges + 4 * len(equipment_raw))
    extra += counts["pressure_edges"] * (vertices + room_edges + openings)
    core = {name: value for name, value in raw.items() if name != "environment_program"}
    admitted = admit_transfer_review(core, original, limits=parent_limits, geometry_work_extra=extra)
    if type(admitted) is dict:
        return stopped(admitted["issues"][0]["message"])
    result = validate_environment_layout(admitted.building, admitted.layout, admitted.routing, environment,
        production_program=admitted.production_program, transfer_program=admitted.transfer_program,
        equipment=admitted.equipment, include_windows=include_windows, limits=limits).to_dict()
    result["review_source"] = {"file_sha256": hashlib.sha256(original).hexdigest(), "bytes": len(original),
                              "scope": "exact_supplied_environment_review_json"}
    return result

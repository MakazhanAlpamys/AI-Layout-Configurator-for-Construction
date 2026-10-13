"""One bounded original revision supplies schedules and fresh process checks.

This offline reader never generates geometry, follows source URIs or publishes
files. Local verdicts remain scoped; synchronized documentation is always a
DRAFT with engineering acceptance pending.
"""

from dataclasses import dataclass, fields, replace
from functools import lru_cache
import hashlib
import json
import math

from jsonschema import Draft202012Validator
from referencing import Registry, Resource

from .engineering_library import (EngineeringLibraryLimits, _Failure as _LibraryFailure,
    _preflight as _library_preflight, _raw_json_preflight, validate_engineering_library)
from .environment_program import EnvironmentProgramLimits, _environment_preflight
from .environment_validation import validate_environment_layout
from .equipment_scenario_geometry import (EquipmentScenarioLimits, _canonical_equipment,
    validate_equipment_scenarios)
from .production_program import (ProductionProgramLimits, _InputFailure, _json_bytes,
    _preflight, _schema_issues, _utf8_size, validate_production_program)
from .resources import resource_path
from .schema import DEFAULT_SCHEMA_PATH, load_building_schema, load_json_schema
from .transfer_program import TransferProgramLimits, _raw_limits
from .transfer_review import admit_transfer_review, read_review_source
from .transfer_validation import validate_transfer_routes


_COMPONENTS = ("building", "layout", "equipment", "flow_routes", "production_program",
               "transfer_program", "environment_program", "library", "scenario_requests")
_SCHEDULES = ("rooms", "operations", "equipment", "transfers", "clean_zones",
              "ventilation_zones", "pressure", "scenarios")
_REPORTS = ("production_program", "transfers", "environment", "library", "equipment_scenarios")
_SCENARIOS = ("production", "maintenance", "installation_replacement")


@dataclass(frozen=True)
class ProcessReviewLimits(EnvironmentProgramLimits):
    max_input_bytes: int = 4_194_304
    max_nodes: int = 131_072
    max_depth: int = 16
    max_equipment: int = 32
    max_library_sources: int = 64
    max_library_cards: int = 128
    max_library_templates: int = 64
    max_source_bytes: int = 262_144
    max_total_source_bytes: int = 1_048_576
    max_library_geometry_points: int = 4096
    max_scenario_vertices: int = 16_384
    max_scenario_pair_checks: int = 131_072
    max_source_objects: int = 1024
    max_findings: int = 256
    max_report_bytes: int = 8_388_608


def _limits_valid(limits):
    defaults = ProcessReviewLimits()
    return type(limits) is ProcessReviewLimits and all(type(getattr(limits, row.name)) is int and
        1 <= getattr(limits, row.name) <= getattr(defaults, row.name) for row in fields(defaults)) and limits.max_report_bytes >= 4096


def _parent_limits(limits):
    return TransferProgramLimits(**{row.name: getattr(limits, row.name) for row in fields(TransferProgramLimits)})


def _environment_limits(limits):
    defaults = EnvironmentProgramLimits()
    return EnvironmentProgramLimits(**{row.name: min(getattr(limits, row.name), getattr(defaults, row.name))
        for row in fields(defaults)})


def _library_limits(limits):
    defaults = EngineeringLibraryLimits()
    return EngineeringLibraryLimits(max_sources=limits.max_library_sources, max_cards=limits.max_library_cards,
        max_templates=limits.max_library_templates, max_source_bytes=limits.max_source_bytes,
        max_total_source_bytes=limits.max_total_source_bytes,
        max_total_utf8_bytes=min(limits.max_input_bytes, defaults.max_total_utf8_bytes),
        max_nodes=min(limits.max_nodes, defaults.max_nodes), max_depth=min(limits.max_depth, defaults.max_depth),
        max_geometry_points=limits.max_library_geometry_points)


def _scenario_limits(limits):
    return EquipmentScenarioLimits(max_equipment=limits.max_equipment, max_rooms=limits.max_rooms,
        max_source_objects=limits.max_source_objects, max_total_utf8_bytes=limits.max_input_bytes,
        max_nodes=limits.max_nodes, max_depth=limits.max_depth, max_vertices=limits.max_scenario_vertices,
        max_pair_checks=limits.max_scenario_pair_checks, max_findings=limits.max_findings)


@lru_cache(maxsize=1)
def _validator():
    # Every reference is registered from bundled bytes. Missing resources fail
    # locally; no network retrieval or user source URI resolution is installed.
    names = ("production_program", "transfer_program", "environment_program", "engineering_library", "layout_result")
    registry = Registry()
    for name in names:
        schema = load_json_schema(resource_path("schemas/" + name + ".schema.json"))
        registry = registry.with_resource(schema["$id"], Resource.from_contents(schema))
    schema = load_json_schema(resource_path("schemas/process_review.schema.json"))
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema, registry=registry)


def _sha(raw):
    return hashlib.sha256(_json_bytes(raw)).hexdigest()


def _identity(raw):
    return {"drawing_revision": raw["drawing_revision"], "variant": raw["variant"],
        "component_sha256": {name: _sha(raw[name]) for name in _COMPONENTS}}


def _base(original, identity=None):
    return {"schema_version": 1, "scope": "synchronized_supplied_process_review", "document_status": "DRAFT",
        "status": "UNKNOWN", "ok": False, "engineering_acceptance": "PENDING", "authenticated_approval": "UNKNOWN",
        "full_facility_validation": {"status": "UNKNOWN", "ok": False}, "proven_building_infeasible": False,
        "identity": identity, "review_source": {"file_sha256": hashlib.sha256(original).hexdigest(),
            "bytes": len(original), "scope": "exact_supplied_process_review_json"},
        "source_frames": {}, "schedules": {}, "reports": {}, "issues": [],
        "policy": {"source_uri_access": False, "movement_classification": "explicit_source_bound_ids_only",
            "source_facts": "supplied_declarations_only", "iso_to_gmp_inference": False,
            "pressure_performance_authentication": "UNKNOWN", "hvac_validation": "UNKNOWN"}}


def _stopped(original, message):
    report = _base(original)
    report["inventory_status"] = "NOT_EVALUATED"
    report["issues"] = [{"status": "UNKNOWN", "code": "input_budget_exceeded", "path": "$", "message": message}]
    return report


def _fail_budget(message):
    raise _InputFailure("input_budget_exceeded", "$", message)


def _nested_nodes(content, limits):
    """Charge every structured source before decoding any of the sources.

    The existing lexical guard checks each source first. Counting tokens is a
    conservative bound on allocated decoded JSON nodes, including object keys.
    """
    _raw_json_preflight(content, limits)
    quoted, escaped, atom, nodes = False, False, False, 0
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
            nodes += 1
        elif char in "[{":
            atom = False
            nodes += 1
        elif char in "},]:" or char.isspace():
            atom = False
        elif not atom:
            nodes += 1
            atom = True
    return nodes


def _raw_preflight(raw, limits):
    counts = _preflight(raw, _raw_limits(_parent_limits(limits), byte_limit=limits.max_input_bytes))
    if type(raw) is not dict:
        raise ValueError("Process review requires a closed original JSON object")
    building, layout = raw.get("building"), raw.get("layout")
    if type(building) is not dict or type(building.get("layout")) is not dict or type(layout) is not dict:
        raise ValueError("Process review requires original canonical source and layout objects")
    source_layout = building["layout"]
    for field, maximum in (("equipment", limits.max_equipment), ("zones", limits.max_zones), ("flows", limits.max_flows)):
        if type(building.get(field)) is list and len(building[field]) > maximum:
            _fail_budget("Canonical " + field + " entity budget exceeded before admission")
    for values, maximum in ((source_layout.get("rooms"), limits.max_rooms), (raw.get("equipment"), limits.max_equipment)):
        if type(values) is list and len(values) > maximum:
            _fail_budget("Room/equipment entity budget exceeded before admission")
    if type(layout.get("rooms")) is dict and len(layout["rooms"]) > limits.max_rooms:
        _fail_budget("Actual room entity budget exceeded before admission")
    source_bytes = 0
    for key in ("production_program", "transfer_program"):
        component = raw.get(key)
        if type(component) is not dict:
            raise ValueError("Process review requires nonnull original " + key)
        _preflight(component, _raw_limits(_parent_limits(limits)))
    production_occurrences = [raw["production_program"]]
    if type(building.get("production_program")) is dict:
        production_occurrences.append(building["production_program"])
    production_limits = replace(_raw_limits(_parent_limits(limits)), max_source_bytes=limits.max_source_bytes,
        max_total_source_bytes=limits.max_total_source_bytes)
    for occurrence in production_occurrences:
        _preflight(occurrence, production_limits)
        sources = occurrence.get("sources")
        for source in sources if type(sources) is list else ():
            if type(source) is dict and type(source.get("content")) is str:
                source_bytes += _utf8_size(source["content"], limits.max_source_bytes, "$.production_program.sources.content")
                if source_bytes > limits.max_total_source_bytes:
                    _fail_budget("Aggregate supplied source occurrences exceed their UTF-8 budget")
    environment = raw.get("environment_program")
    environment_counts = {"zone_vertices": 0, "pressure_edges": 0}
    if environment is not None:
        _, environment_counts = _environment_preflight(environment, _environment_limits(limits))
    library = raw.get("library")
    points, nested_nodes, nested_bytes, envelope_count, port_count = 0, 0, 0, 0, 0
    if library is not None:
        library_limits = _library_limits(limits)
        _library_preflight(library, library_limits)
        if type(library) is dict:
            for source in library.get("sources", []) if type(library.get("sources")) is list else ():
                content = source.get("content") if type(source) is dict else None
                if type(content) is str:
                    size = _utf8_size(content, limits.max_source_bytes, "$.library.sources.content")
                    source_bytes += size
                    if source_bytes > limits.max_total_source_bytes:
                        _fail_budget("Aggregate supplied source occurrences exceed their UTF-8 budget")
                    if content.lstrip().startswith(("[", "{")):
                        nested_nodes += _nested_nodes(content, library_limits)
                        nested_bytes += size
                        if counts["json_nodes"] + nested_nodes > limits.max_nodes or counts["json_utf8_bytes"] + nested_bytes > limits.max_input_bytes:
                            _fail_budget("Aggregate nested structured source node/byte budget exceeded before decoding")
            for card in library.get("cards", []) if type(library.get("cards")) is list else ():
                if type(card) is not dict:
                    continue
                rows = card.get("envelopes", [])
                envelope_count += len(rows) if type(rows) is list else 0
                ports = card.get("interaction_points", [])
                port_count += len(ports) if type(ports) is list else 0
                for row in rows if type(rows) is list else ():
                    value = row.get("evidence", {}).get("value") if type(row) is dict and type(row.get("evidence")) is dict else None
                    if type(value) is dict:
                        points += len(value.get("points", [])) if type(value.get("points")) is list else 4 if value.get("kind") == "rectangle" else 0
    if source_bytes > limits.max_total_source_bytes:
        _fail_budget("Aggregate supplied source UTF-8 budget exceeded")
    if points + environment_counts["zone_vertices"] > limits.max_scenario_vertices:
        _fail_budget("Aggregate source geometry vertex budget exceeded before GEOS")
    equipment_count = len(raw.get("equipment", [])) if type(raw.get("equipment")) is list else 0
    pairs = 3 * ((equipment_count + envelope_count) ** 2 + port_count * equipment_count + 8 * equipment_count + 2 * port_count)
    if pairs > limits.max_scenario_pair_checks:
        _fail_budget("Aggregate scenario predicate pair budget exceeded before GEOS")
    return environment_counts, points, pairs


def _check_schema(raw):
    error = next(_validator().iter_errors(raw), None)
    if error is not None:
        raise ValueError("Process review must satisfy its closed source schema: " + error.message[:512])
    building = raw["building"]
    source_issues = _schema_issues(building, load_building_schema(include_production_program="production_program" in building))
    source_issues += _schema_issues(building["layout"], load_json_schema(DEFAULT_SCHEMA_PATH))
    if source_issues:
        raise ValueError("Process review source must satisfy original canonical schemas: " + source_issues[0].message)
    if type(raw["variant"]) is not int or type(raw["layout"].get("variant")) is not int or raw["variant"] != raw["layout"]["variant"]:
        raise ValueError("Process review variant must exactly match the actual original layout variant")


def _unrepresentable_rectangle(raw):
    """Check source extents with the same arithmetic used by canonical Rect.

    A positive declared extent lost at its world origin is a precision gap,
    not an inconsistent source or a building infeasibility proof. No geometry
    object is constructed here, including on the optional-library path.
    """
    def lost(x, y, width, height):
        try:
            return not all(math.isfinite(value) for value in (x, y, width, height, x + width, y + height)) or x + width <= x or y + height <= y
        except OverflowError:
            return True

    def issue(path):
        return {"status": "UNKNOWN", "code": "unrepresentable_canonical_geometry", "path": path,
            "message": "Positive source extent or directional clearance is lost at current world-coordinate precision"}

    for room_id, shape in raw["layout"]["rooms"].items():
        if shape.get("kind") != "orthogonal" and lost(shape["x"], shape["y"], shape["width"], shape["height"]):
            return issue("$.layout.rooms." + room_id)
    specs = {row["id"]: row for row in raw["building"]["equipment"]}
    for index, placement in enumerate(raw["equipment"]):
        x, y, width, depth = (placement[key] for key in ("x", "y", "width", "depth"))
        path = f"$.equipment[{index}]"
        if lost(x, y, width, depth):
            return issue(path)
        spec = specs.get(placement["equipment_id"])
        if spec is None:
            continue  # Unknown source identities retain ordinary admission failures.
        source_width, source_depth = spec["width_mm"], spec["depth_mm"]
        if placement["rotated"]:
            source_width, source_depth = source_depth, source_width
            left, right, bottom, top = (spec[key] for key in ("clearance_front_mm", "clearance_back_mm", "clearance_left_mm", "clearance_right_mm"))
        else:
            left, right, bottom, top = (spec[key] for key in ("clearance_left_mm", "clearance_right_mm", "clearance_back_mm", "clearance_front_mm"))
        if lost(x, y, source_width, source_depth):
            return issue(path + ".source_dimensions")
        try:
            envelope_x, envelope_y = x - left, y - bottom
            envelope_width, envelope_depth = width + left + right, depth + bottom + top
            if lost(envelope_x, envelope_y, envelope_width, envelope_depth):
                return issue(path + ".source_clearance")
            if left > 0 and envelope_x >= x or bottom > 0 and envelope_y >= y or right > 0 and envelope_x + envelope_width <= x + width or top > 0 and envelope_y + envelope_depth <= y + depth:
                return issue(path + ".source_clearance")
        except OverflowError:
            return issue(path + ".source_clearance")
    return None


def _exact_frames_and_requests(raw):
    """Apply original I4 identity/dimension guards before geometry admission.

    The optional library controls contour evidence, never canonical source
    identity or placement precision. Undeclared card/action evidence cannot be
    established when the library is absent; equipment IDs remain mandatory.
    """
    def number(value, *, positive=False):
        return type(value) in (int, float) and -1_000_000 <= value <= 1_000_000 and math.isfinite(value) and (not positive or value > 0)

    def identifier(value):
        return type(value) is str and 1 <= len(value) <= 128 and value == value.strip() and not any(ch in value for ch in "\r\n")

    building = raw["building"]
    rooms = {row["id"] for row in building["layout"]["rooms"]}
    specs = {row["id"]: row for row in building["equipment"]}
    if len(specs) != len(building["equipment"]) or any(not identifier(key) for key in specs):
        raise ValueError("Canonical equipment requires unique bounded original identities")
    for spec in specs.values():
        for key in ("width_mm", "depth_mm", "clearance_front_mm", "clearance_back_mm", "clearance_left_mm", "clearance_right_mm", "anchor_offset_mm"):
            if not number(spec[key], positive=key in ("width_mm", "depth_mm")) or spec[key] < 0:
                raise ValueError("Original equipment dimensions require finite bounded actual source numbers")
    seen = set()
    zones = {row["id"]: row for row in building["zones"]}
    for placement in raw["equipment"]:
        equipment_id, host = placement["equipment_id"], placement["room_id"]
        if equipment_id not in specs or equipment_id in seen or not identifier(host) or host not in rooms:
            raise ValueError("Canonical placement must bind unique original equipment and a declared room")
        seen.add(equipment_id)
        spec = specs[equipment_id]
        if "room_id" in spec:
            allowed = {spec["room_id"]}
        else:
            if spec["zone_id"] not in zones:
                raise ValueError("Original equipment zone is undeclared")
            descendants = {spec["zone_id"]}
            changed = True
            while changed:
                before = len(descendants)
                descendants.update(row["id"] for row in building["zones"] if row.get("parent_zone_id") in descendants)
                changed = before != len(descendants)
            allowed = {room for key in descendants for room in zones[key]["room_ids"]}
        if host not in allowed or placement["rotated"] and not spec["rotation_allowed"]:
            raise ValueError("Placement host or rotation differs from original equipment source")
        width, depth = (spec["depth_mm"], spec["width_mm"]) if placement["rotated"] else (spec["width_mm"], spec["depth_mm"])
        if placement["width"] != width or placement["depth"] != depth:
            raise ValueError("Placement footprint must equal exact oriented source dimensions")
        x, y = placement["x"], placement["y"]
        if not all(number(value) for value in (x, y, x + width, y + depth)):
            raise ValueError("Canonical equipment endpoints exceed supported world-coordinate bounds")
        if placement["rotated"]:
            left, right, bottom, top = (spec[key] for key in ("clearance_front_mm", "clearance_back_mm", "clearance_left_mm", "clearance_right_mm"))
        else:
            left, right, bottom, top = (spec[key] for key in ("clearance_left_mm", "clearance_right_mm", "clearance_back_mm", "clearance_front_mm"))
        if not all(number(value) for value in (x - left, y - bottom, (x - left) + (width + left + right), (y - bottom) + (depth + bottom + top))):
            raise ValueError("Source-derived directional clearance endpoints exceed supported world bounds")
    for shape in raw["layout"]["rooms"].values():
        if shape.get("kind") == "orthogonal":
            if any(not number(value) for point in shape["vertices"] for value in point):
                raise ValueError("Actual room coordinates exceed local review bounds")
        elif not all(number(value) for value in (shape["x"], shape["y"], shape["width"], shape["height"], shape["x"] + shape["width"], shape["y"] + shape["height"])):
            raise ValueError("Actual room rectangle endpoints exceed local review bounds")
    requests = raw["scenario_requests"]
    mapping = requests["card_by_equipment"]
    if set(mapping) - set(specs):
        raise ValueError("Card mapping must retain original equipment identities")
    for field in ("maintenance_targets", "installation_targets"):
        if set(requests[field]) - set(specs):
            raise ValueError("Scenario targets must retain original equipment identities")
    library = raw["library"]
    cards = {} if library is None else {row["id"]: row for row in library["cards"]}
    if library is not None and set(mapping.values()) - set(cards):
        raise ValueError("Mapped card identity is absent from the supplied library")
    for name, actions in requests["actions_by_scenario"].items():
        if set(actions) - set(specs):
            raise ValueError("Scenario actions must bind original equipment identities")
        for equipment_id, action_ids in actions.items():
            if library is not None and equipment_id in mapping:
                declarations = {row["id"]: row for row in cards[mapping[equipment_id]]["scenario_actions"]}
                if any(action_id not in declarations or declarations[action_id]["scenario"] != name for action_id in action_ids):
                    raise ValueError("Explicit action IDs must bind their selected card and declared scenario")


def _geometry_work(raw, counts, points, pairs, limits):
    """Count the whole review before common admission constructs any GEOS."""
    source = raw["building"]["layout"]
    shapes = raw["layout"]["rooms"].values()
    edges = [len(row["vertices"]) if row.get("kind") == "orthogonal" else 4 for row in shapes]
    total, maximum = sum(edges), max(edges, default=0)
    n = len(raw["equipment"])
    openings = len(source.get("doors", [])) + len(source.get("windows", [])) + int(source.get("external_entry") is not None)
    if openings > limits.max_openings:
        _fail_budget("Source opening budget exceeded before GEOS")
    route_rows = raw["flow_routes"]["routes"]
    if len(route_rows) > limits.max_routes or sum(len(row["points"]) for row in route_rows) > limits.max_total_points:
        _fail_budget("Aggregate actual route point budget exceeded before GEOS")
    common = sum(max(0, len(row["points"]) - 1) for row in route_rows) * (total + len(source["rooms"]) + openings + n)
    common += total ** 2 + 4*n*total + 16*n*n
    common += (openings + len(source["rooms"]) ** 2)*maximum ** 2
    common += (4 + 4*len(source["boundary"].get("cutouts", [])))*total
    v = counts["zone_vertices"]
    environment = v*v + v*(total + 4*n) + counts.get("pressure_edges", 0)*(v + total + openings)
    # Independent transfer/environment predicates repeat the original checks.
    # Local scenario checks add source contours and owner pair reservations.
    extra = 2*common + environment + points*points + pairs + points*(total + 4*n)
    if common + extra > limits.max_geometry_checks:
        _fail_budget("Aggregate process geometry work budget exceeded before canonical admission")
    objects = len(source["rooms"]) + openings + len(source["boundary"].get("cutouts", []))
    objects += sum(len(raw["building"][key]) for key in ("equipment", "zones", "flows"))
    if objects > limits.max_source_objects:
        _fail_budget("Aggregate canonical source object budget exceeded before admission")
    return extra


def _missing(component):
    return {"status": "UNKNOWN", "engineering_acceptance": "PENDING", "authenticated_approval": "UNKNOWN",
        "issues": [{"status": "UNKNOWN", "code": "missing_" + component, "message": "No supplied " + component + " evidence"}]}


def _schedule_rows(raw):
    environment = raw["environment_program"]
    environment = {} if environment is None else environment
    placements = {row["equipment_id"]: row for row in raw["equipment"]}
    rooms = [{**row, "actual_footprint": raw["layout"]["rooms"].get(row["id"])} for row in raw["building"]["layout"]["rooms"]]
    equipment = [{**row, "actual_placement": placements.get(row["id"]),
        "requested_card_id": raw["scenario_requests"]["card_by_equipment"].get(row["id"])} for row in raw["building"]["equipment"]]
    pressure = [{"record_kind": kind, **row} for kind in ("pressure_datums", "pressure_nodes", "pressure_edges")
                for row in environment.get(kind, [])]
    scenarios = [{"record_kind": "environment_declaration", **row} for row in environment.get("scenarios", [])]
    requests = raw["scenario_requests"]
    for name in _SCENARIOS:
        scenarios.append({"record_kind": "equipment_scenario_request", "id": name,
            "target_equipment_ids": requests["maintenance_targets"] if name == "maintenance" else
                requests["installation_targets"] if name == "installation_replacement" else [row["id"] for row in raw["building"]["equipment"]],
            "declared_actions": requests["actions_by_scenario"][name], "action_execution": "UNKNOWN"})
    return {"rooms": rooms, "operations": raw["production_program"]["operations"], "equipment": equipment,
        "transfers": raw["transfer_program"]["transfers"], "clean_zones": environment.get("clean_zones", []),
        "ventilation_zones": environment.get("ventilation_zones", []), "pressure": pressure, "scenarios": scenarios}


def _originating_failures(report, limit):
    """Retain original issue codes and source IDs, never just aggregate FAIL.

    Canonical legacy issues omit a status field; their immediate independently
    failed parent supplies it. An explicit UNKNOWN issue is never promoted.
    """
    retained, seen, total = [], set(), 0

    def walk(value, inherited):
        if type(value) is dict:
            state = value.get("status", inherited)
            if "code" in value and value.get("status", inherited) == "FAIL":
                yield value
            for key, child in value.items():
                if key != "source_frames" and type(child) in (dict, list):
                    yield from walk(child, state)
        elif type(value) is list:
            for child in value:
                yield from walk(child, inherited)

    for component, wrapper in report["reports"].items():
        if wrapper["status"] != "FAIL":
            continue
        for original in walk(wrapper["evidence"], "FAIL"):
            selected = {"code": original["code"], "status": "FAIL"}
            for key in ("source_id", "edge_id", "opening_id", "equipment_id", "related_equipment_id", "operation_id",
                        "requirement_id", "scenario_id", "zone_id", "node_id", "transfer_id", "action_id"):
                if key in original and original[key] is not None:
                    selected[key] = original[key]
            for key, maximum in (("path", 128), ("message", 256)):
                if type(original.get(key)) is str:
                    selected[key] = original[key][:maximum]
                    if len(original[key]) > maximum:
                        selected.setdefault("truncated_fields", []).append(key)
            digest = (component, _sha(selected))
            if digest in seen:
                continue
            seen.add(digest)
            total += 1
            if len(retained) < limit:
                retained.append({"component": component, "identity": wrapper["identity"], "failure": selected})
    return retained, total


def _bounded_output(report, limits):
    # Streaming size accounting avoids an unbounded second serialization. The
    # returned object is already detached from file bytes and canonical models.
    size = 0
    for chunk in json.JSONEncoder(sort_keys=True, ensure_ascii=False, allow_nan=False).iterencode(report):
        size += len(chunk.encode("utf-8"))
        if size > limits.max_report_bytes:
            compact = {key: report[key] for key in ("schema_version", "scope", "document_status", "status", "ok",
                "engineering_acceptance", "authenticated_approval", "full_facility_validation",
                "proven_building_infeasible", "identity", "review_source", "policy")}
            compact.update(inventory_status="INCOMPLETE", source_frames={}, schedules={}, reports={}, issues=[
                {"status": "UNKNOWN", "code": "output_budget_exceeded", "message": "Source frames and schedules exceed the bounded delivery inventory"}])
            retained, total = _originating_failures(report, limits.max_findings)
            compact["retained_failures"] = retained
            compact["failure_inventory"] = {"status": "INCOMPLETE", "originating_failures": total,
                "retained": len(retained), "omitted": total - len(retained)}
            while len(_json_bytes(compact)) > limits.max_report_bytes and len(retained) > 1:
                retained.pop()
                compact["failure_inventory"].update(retained=len(retained), omitted=total - len(retained))
            if len(_json_bytes(compact)) > limits.max_report_bytes:
                # Policy details are already omitted from the bounded source
                # inventory; preserve originating failure and exact identity.
                compact.pop("policy", None)
            if len(_json_bytes(compact)) > limits.max_report_bytes and retained:
                failure = retained[0]["failure"]
                omitted = [key for key in failure if key not in ("code", "status", "source_id", "edge_id")]
                for key in omitted:
                    failure.pop(key)
                compact["failure_inventory"]["omitted_first_failure_fields"] = len(omitted)
            if len(_json_bytes(compact)) > limits.max_report_bytes:
                raise ValueError("Process review delivery budget cannot hold its mandatory failure/identity record")
            return compact
    return report


def load_process_review(path, *, include_windows=False, limits=ProcessReviewLimits()):
    """Read one original source snapshot and recompute all supported local gates.

    Environment and library may be explicitly null. Mandatory declarations,
    saved geometry and scenario requests cannot be replaced with null or a
    supplied report. All source tables retain their original scalar types.
    """
    if not _limits_valid(limits) or type(include_windows) is not bool:
        raise ValueError("Process review requires bounded actual integer limits and a boolean window control")
    parent = _parent_limits(limits)
    try:
        original, raw = read_review_source(path, limits=parent)
    except _InputFailure as exc:
        raise ValueError(exc.issue.message) from exc
    try:
        counts, points, pairs = _raw_preflight(raw, limits)
        _check_schema(raw)
        _exact_frames_and_requests(raw)
        precision_issue = _unrepresentable_rectangle(raw)
        if precision_issue is not None:
            report = _base(original)
            report.update(inventory_status="NOT_EVALUATED", issues=[precision_issue])
            return report
        extra = _geometry_work(raw, counts, points, pairs, limits)
    except (_InputFailure, _LibraryFailure) as exc:
        if exc.issue.code == "input_budget_exceeded":
            return _stopped(original, exc.issue.message)
        raise ValueError(exc.issue.message) from exc
    identity = _identity(raw)
    core = {name: raw[name] for name in ("building", "layout", "equipment", "flow_routes", "production_program", "transfer_program")}
    admitted = admit_transfer_review(core, original, limits=parent, geometry_work_extra=extra)
    if type(admitted) is dict:
        return _stopped(original, admitted["issues"][0]["message"])
    reports = {}
    reports["production_program"] = validate_production_program(raw["production_program"], building=admitted.building).to_dict()
    reports["transfers"] = validate_transfer_routes(admitted.building, admitted.layout, admitted.routing,
        raw["transfer_program"], production_program=raw["production_program"], equipment=admitted.equipment,
        include_windows=include_windows, limits=parent).to_dict()
    reports["environment"] = _missing("environment_program") if raw["environment_program"] is None else validate_environment_layout(
        admitted.building, admitted.layout, admitted.routing, raw["environment_program"], production_program=raw["production_program"],
        transfer_program=raw["transfer_program"], equipment=admitted.equipment, include_windows=include_windows,
        limits=_environment_limits(limits)).to_dict()
    reports["library"] = _missing("library") if raw["library"] is None else validate_engineering_library(
        raw["library"], limits=_library_limits(limits)).to_dict()
    if raw["library"] is None:
        scenarios = _missing("library_for_equipment_scenarios")
        # A missing card library cannot hide independently known canonical
        # body/clearance collisions or turn missing reservations into PASS.
        canonical = _canonical_equipment(admitted.building, admitted.layout, admitted.equipment)
        scenarios["canonical_equipment"] = canonical
        if canonical["status"] == "FAIL":
            scenarios["status"] = "FAIL"
        scenarios["scenarios"] = {name: {"status": "UNKNOWN", "full_path_validation": "UNKNOWN",
            "engineering_acceptance": "PENDING", "issues": []} for name in _SCENARIOS}
        reports["equipment_scenarios"] = scenarios
    else:
        requests = raw["scenario_requests"]
        reports["equipment_scenarios"] = validate_equipment_scenarios(admitted.building, admitted.layout, admitted.equipment,
            library=raw["library"], card_by_equipment=requests["card_by_equipment"], maintenance_targets=requests["maintenance_targets"],
            installation_targets=requests["installation_targets"], actions_by_scenario=requests["actions_by_scenario"],
            limits=_scenario_limits(limits)).to_dict()
    output = _base(original, identity)
    output["source_frames"] = {name: raw[name] for name in _COMPONENTS}
    output["schedules"] = {name: {"identity": identity, "scope": "literal_supplied_source_and_saved_geometry", "rows": rows}
        for name, rows in _schedule_rows(raw).items()}
    output["reports"] = {name: {"identity": identity, "status": evidence["status"], "evidence": evidence}
        for name, evidence in reports.items()}
    output["status"] = "FAIL" if any(evidence["status"] == "FAIL" for evidence in reports.values()) else "UNKNOWN"
    output["issues"] = [{"status": evidence["status"], "code": "scoped_review_" + evidence["status"].lower(),
        "component": name, "message": "Fresh local evidence is attached; no full engineering approval is inferred"}
        for name, evidence in reports.items() if evidence["status"] != "PASS"]
    return _bounded_output(output, limits)

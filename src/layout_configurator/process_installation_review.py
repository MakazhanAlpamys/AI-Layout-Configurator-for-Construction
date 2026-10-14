"""Explicit process revision 0.2 with fresh supplied installation-path checks.

The nine raw process component identities stay separate from the standalone
path checker's canonical model signatures. This reader writes no files,
rebinds no request and infers no route, action execution or 3D approval.
"""

from dataclasses import dataclass, fields, replace
from functools import lru_cache
import json
import math

from jsonschema import Draft202012Validator
from referencing import Registry, Resource

from . import process_review as _common
from .process_review import ProcessReviewLimits
from .equipment_installation_path import (EquipmentInstallationPathLimits,
    _InputError as _PathInputError, _evidence_shape, validate_equipment_installation_path)
from .production_program import _InputFailure, _json_bytes, _preflight, _utf8_size
from .resources import resource_path
from .schema import load_json_schema
from .transfer_program import _raw_limits
from .transfer_review import read_review_source


@dataclass(frozen=True)
class ProcessInstallationPathLimits:
    """Separate opt-in limits; historical review/document limits never change.

    Geometry controls the sum of standalone path admission scores. The parent
    process geometry cap additionally includes the unchanged common score.
    Node/byte work reserves repeated structured-source decoding in the common
    review and every path, including detached library JSON allocations.
    """

    max_paths: int = 16
    max_poses: int = 128
    max_total_poses: int = 512
    max_total_staging_obstacles: int = 512
    max_geometry_checks: int = 131072
    max_node_work: int = 131072
    max_source_byte_work: int = 16777216


class _PrecisionGap(ValueError):
    pass


def _valid_path_limits(limits):
    defaults = ProcessInstallationPathLimits()
    return type(limits) is ProcessInstallationPathLimits and all(type(getattr(limits, row.name)) is int and
        1 <= getattr(limits, row.name) <= getattr(defaults, row.name) for row in fields(defaults))


@lru_cache(maxsize=1)
def _validator():
    registry = Registry()
    for name in ("production_program", "transfer_program", "environment_program", "engineering_library",
                 "layout_result", "process_review"):
        schema = load_json_schema(resource_path("schemas/" + name + ".schema.json"))
        registry = registry.with_resource(schema["$id"], Resource.from_contents(schema))
    schema = load_json_schema(resource_path("schemas/process_review_paths.schema.json"))
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema, registry=registry)


def _budget(message):
    raise _InputFailure("input_budget_exceeded", "$.installation_paths", message)


def _rectangle(x, y, width, depth):
    values = (x, y, width, depth, x + width, y + depth)
    if any(type(value) not in (int, float) or not math.isfinite(value) or abs(value) > 1000000 for value in values) or width <= 0 or depth <= 0:
        raise ValueError("Supplied path rectangle needs bounded finite actual coordinates and positive dimensions")
    if x + width <= x or y + depth <= y:
        raise _PrecisionGap("Positive supplied path extent is lost at world-coordinate precision")


def _pose_precision(request, spec):
    clearance = request["transport_clearance"]
    for pose in request["poses"]:
        width, depth = (spec["depth_mm"], spec["width_mm"]) if pose["rotated"] else (spec["width_mm"], spec["depth_mm"])
        _rectangle(pose["x_mm"], pose["y_mm"], width, depth)
        if clearance is not None:
            value = clearance["value"]
            x, y, w, h = (value[key] for key in ("x", "y", "width", "depth"))
            if pose["rotated"]:
                x, y, w, h = spec["depth_mm"] - (y + h), x, h, w
            _rectangle(pose["x_mm"] + x, pose["y_mm"] + y, w, h)
    # Complete axis sweeps have the same endpoint arithmetic as the standalone
    # checker. Unsupported motion remains UNKNOWN; no diagonal sweep is added.
    for first, second in zip(request["poses"], request["poses"][1:]):
        if first["rotated"] != second["rotated"] or first["x_mm"] != second["x_mm"] and first["y_mm"] != second["y_mm"]:
            continue
        width, depth = (spec["depth_mm"], spec["width_mm"]) if first["rotated"] else (spec["width_mm"], spec["depth_mm"])
        locals_ = [(0, 0, width, depth)]
        if clearance is not None:
            value = clearance["value"]
            x, y, w, h = (value[key] for key in ("x", "y", "width", "depth"))
            if first["rotated"]:
                x, y, w, h = spec["depth_mm"] - (y + h), x, h, w
            locals_.append((x, y, w, h))
        for x, y, w, h in locals_:
            _rectangle(min(first["x_mm"], second["x_mm"]) + x,
                min(first["y_mm"], second["y_mm"]) + y,
                abs(second["x_mm"] - first["x_mm"]) + w,
                abs(second["y_mm"] - first["y_mm"]) + h)


def _path_score(raw, request, envelope_count):
    poses = len(request["poses"])
    rooms, equipment = len(raw["layout"]["rooms"]), len(raw["equipment"])
    layout = raw["building"]["layout"]
    openings = len(layout.get("doors", [])) + int(layout.get("external_entry") is not None)
    staging = request["exterior_staging"]
    obstacles = len(staging["value"]["obstacles"]) if staging is not None else 0
    # Same conservative predicate score as the standalone path's _admit.
    score = 2 * (poses - 1) * (rooms + equipment + envelope_count + obstacles + 4 * openings + 4)
    score += 2 * poses * (equipment + envelope_count + obstacles + 4)
    score += rooms * rooms + 8 * envelope_count + 8 * openings + 8 * rooms + poses
    score += equipment * max(0, equipment - 1) // 2 + 4 * equipment + rooms
    return score


def _literal_path_vertices(request):
    """Reserve all supplied shapes, including unbound staging inventories."""
    shapes = []
    if request["transport_clearance"] is not None:
        shapes.append(request["transport_clearance"]["value"])
    staging = request["exterior_staging"]
    if staging is not None:
        shapes.append(staging["value"]["floor"])
        shapes.extend(staging["value"]["obstacles"])
    return sum(4 if shape["kind"] == "rectangle" else len(shape["points"]) for shape in shapes)


def _raw_point_vertices(value):
    """Count existing bounded object arrays without copying or source decoding."""
    if type(value) is dict:
        count = len(value["points"]) if type(value.get("points")) is list else 0
        return count + sum(_raw_point_vertices(child) for child in value.values())
    if type(value) is list:
        return sum(_raw_point_vertices(child) for child in value)
    return 0


def _string_points_key(content, start):
    """Skip a quoted token; retain only whether its decoded key is points."""
    index, length, matches = start + 1, 0, True
    escapes = {'"': '"', "\\": "\\", "/": "/", "b": "\b", "f": "\f", "n": "\n", "r": "\r", "t": "\t"}
    while index < len(content):
        char = content[index]
        if char == '"':
            return index + 1, matches and length == 6
        if char == "\\":
            index += 1
            if index >= len(content):
                return index, False
            char = content[index]
            if char == "u":
                digits = content[index + 1:index + 5]
                if len(digits) == 4 and all(value in "0123456789abcdefABCDEF" for value in digits):
                    char = chr(int(digits, 16))
                    index += 4
                else:
                    matches = False
            else:
                char = escapes.get(char, "\0")
        matches = matches and length < 6 and char == "points"[length]
        length += 1
        index += 1
    # Syntax authentication belongs to the unchanged strict source parser.
    return index, False


def _lexical_point_vertices(content, capacity, max_depth):
    """O(bytes)/O(depth) metadata only; never allocate a decoded document."""
    index, count, stack = 0, 0, []

    def start_value():
        nonlocal count
        if not stack:
            return False
        frame = stack[-1]
        named_array = frame[0] == "{" and frame[1] == "value" and frame[3]
        if frame[0] == "[" and frame[1] == "value" and frame[2]:
            count += 1
            if count > capacity:
                _budget("Nested structured source point arrays exceed their remaining vertex budget before decoding")
        frame[1], frame[3] = "comma", False
        return named_array

    while index < len(content):
        char = content[index]
        if char.isspace():
            index += 1
            continue
        if char == '"':
            index, points_key = _string_points_key(content, index)
            if stack and stack[-1][0] == "{" and stack[-1][1] == "key":
                stack[-1][1], stack[-1][3] = "colon", points_key
            else:
                start_value()
            continue
        if char in "[{":
            named_array = start_value()
            if len(stack) >= max_depth:
                _budget("Structured source metadata stack exceeds its existing depth bound")
            stack.append([char, "value" if char == "[" else "key", char == "[" and named_array, False])
        elif char in "]}":
            if stack:
                stack.pop()
        elif char == ":":
            if stack and stack[-1][0] == "{" and stack[-1][1] == "colon":
                stack[-1][1] = "value"
        elif char == ",":
            if stack:
                stack[-1][1], stack[-1][3] = ("key" if stack[-1][0] == "{" else "value"), False
        else:
            start_value()
            index += 1
            while index < len(content) and not content[index].isspace() and content[index] not in '[]{}:,"':
                index += 1
            continue
        index += 1
    return count


def _reserve_vertices(common_vertices, request_vertices, limits):
    combined = common_vertices + sum(request_vertices)
    if combined > limits.max_scenario_vertices:
        _budget("Combined original source and literal installation path vertex budget exceeded before geometry")
    leaf_limit = min(limits.max_scenario_vertices, EquipmentInstallationPathLimits().max_vertices)
    if any(common_vertices + count > leaf_limit for count in request_vertices):
        _budget("Supplied source and literal path vertices exceed the standalone vertex ceiling before common geometry")
    return combined, leaf_limit


def _path_preflight(raw, limits, path_limits):
    paths = raw.get("installation_paths") if type(raw) is dict else None
    if type(paths) is not list:
        raise ValueError("Version 0.2 requires an explicit installation_paths array")
    if len(paths) > path_limits.max_paths:
        _budget("Supplied installation request count exceeds its bound")
    pose_count, obstacle_count = 0, 0
    for request in paths:
        if type(request) is not dict or type(request.get("poses")) is not list:
            raise ValueError("Each supplied installation path needs an actual ordered pose array")
        count = len(request["poses"])
        pose_count += count
        if count > path_limits.max_poses or pose_count > path_limits.max_total_poses:
            _budget("Combined installation pose budget exceeded before geometry")
        staging = request.get("exterior_staging")
        if type(staging) is dict and type(staging.get("value")) is dict and type(staging["value"].get("obstacles")) is list:
            count = len(staging["value"]["obstacles"])
            obstacle_count += count
            if count > EquipmentInstallationPathLimits().max_staging_obstacles or obstacle_count > path_limits.max_total_staging_obstacles:
                _budget("Combined supplied staging obstacle budget exceeded before geometry")
    error = next(_validator().iter_errors(raw), None)
    if error is not None:
        raise ValueError("Installation review requires its closed version 0.2 schema: " + error.message[:512])
    _common._check_common_schema(raw)
    # Common admission uses declared envelope rectangles/rings; the leaf also
    # inventories raw and nested source point arrays and actual polygon rooms.
    # Reserve the larger library inventory, not their double-counted sum.
    # Literal path rectangles reserve four vertices even without points arrays.
    common_counts, envelope_vertices, _ = _common._raw_preflight(raw, limits)
    room_vertices = sum(len(room["vertices"]) for room in raw["layout"]["rooms"].values() if room.get("kind") == "orthogonal")
    raw_library_vertices = _raw_point_vertices(raw["library"])
    common_vertices = common_counts["zone_vertices"] + room_vertices + max(envelope_vertices, raw_library_vertices)
    request_vertices = [_literal_path_vertices(request) for request in paths]
    combined_vertices, leaf_vertex_limit = _reserve_vertices(common_vertices, request_vertices, limits)
    common_capacity = limits.max_scenario_vertices - sum(request_vertices)
    if request_vertices:
        common_capacity = min(common_capacity, leaf_vertex_limit - max(request_vertices))
    nested_vertex_capacity = common_capacity - common_counts["zone_vertices"] - room_vertices - raw_library_vertices
    ids = [request["id"] for request in paths]
    if len(set(ids)) != len(ids):
        raise ValueError("Installation request identities must be unique")
    specs = {row["id"]: row for row in raw["building"]["equipment"]}
    targets = set(raw["scenario_requests"]["installation_targets"])
    mapping = raw["scenario_requests"]["card_by_equipment"]
    for request in paths:
        equipment_id = request["equipment_id"]
        if equipment_id not in specs or equipment_id not in targets or mapping.get(equipment_id) != request["card_id"]:
            raise ValueError("Each path must retain its explicit installation target and mapped original card")
        try:
            for name in ("transport_clearance", "exterior_staging"):
                _evidence_shape(request[name], name, EquipmentInstallationPathLimits())
        except _PathInputError as exc:
            if exc.status == "UNKNOWN":
                raise _PrecisionGap(str(exc)) from exc
            raise ValueError(str(exc)) from exc
        _pose_precision(request, specs[equipment_id])
    raw_limits = _raw_limits(_common._parent_limits(limits), byte_limit=limits.max_input_bytes)
    raw_counts = _preflight(raw, raw_limits)
    library = raw["library"]
    library_counts = {"json_nodes": 0}
    nested_nodes, content_bytes, envelope_count, nested_vertices = 0, 0, 0, 0
    if library is not None:
        library_counts = _preflight(library, raw_limits)
        lexical_limits = _common._library_limits(limits)
        for source in library["sources"]:
            content = source["content"]
            content_bytes += _utf8_size(content, limits.max_source_bytes, "$.library.sources.content")
            if content.lstrip().startswith(("[", "{")):
                nested_nodes += _common._nested_nodes(content, lexical_limits)
                nested_vertices += _lexical_point_vertices(content, nested_vertex_capacity - nested_vertices, lexical_limits.max_depth)
        envelope_count = sum(len(card["envelopes"]) for card in library["cards"])
    common_vertices = common_counts["zone_vertices"] + room_vertices + max(envelope_vertices, raw_library_vertices + nested_vertices)
    combined_vertices, leaf_vertex_limit = _reserve_vertices(common_vertices, request_vertices, limits)
    production_bytes = 0
    programs = [raw["production_program"]]
    if "production_program" in raw["building"]:
        programs.append(raw["building"]["production_program"])
    for program in programs:
        production_bytes += sum(_utf8_size(source["content"], limits.max_source_bytes, "$.production_program.sources.content") for source in program["sources"])
    # Actual current call sites: common library QA1 + scenario preparation1 +
    # scenario library QA1; each leaf preparation1 + library QA1 + documents1.
    # Each scenario/leaf also decodes one detached outer library JSON snapshot.
    nested_passes, outer_passes = 3 + 3 * len(paths), 1 + len(paths)
    node_work = raw_counts["json_nodes"] + nested_passes * nested_nodes + outer_passes * library_counts["json_nodes"]
    source_work = production_bytes + (1 + nested_passes) * content_bytes
    if node_work > path_limits.max_node_work:
        _budget("Combined original, repeated nested-source and detached-library node work exceeded before any nested decoding")
    if source_work > path_limits.max_source_byte_work:
        _budget("Combined supplied production and repeated library source byte work exceeded before any nested decoding")
    scores = [_path_score(raw, request, envelope_count) for request in paths]
    if sum(scores) > path_limits.max_geometry_checks:
        _budget("Combined standalone path predicate budget exceeded before common geometry admission")
    return {"path_count": len(paths), "poses": pose_count, "staging_obstacles": obstacle_count,
        "common_source_vertices": common_vertices, "literal_path_vertices": sum(request_vertices),
        "per_request_literal_vertices": request_vertices, "combined_source_geometry_vertices": combined_vertices,
        "actual_orthogonal_room_vertices": room_vertices, "declared_library_envelope_vertices": envelope_vertices,
        "raw_library_point_vertices": raw_library_vertices, "nested_library_point_vertices": nested_vertices,
        "source_vertex_reservation_policy": "ENV plus actual orthogonal rooms plus max(declared envelopes, raw and nested library point arrays)",
        "shared_source_geometry_vertex_limit": limits.max_scenario_vertices, "per_request_source_geometry_vertex_limit": leaf_vertex_limit,
        "path_geometry_upper_bound": sum(scores), "per_request_geometry_upper_bounds": scores,
        "shared_process_geometry_limit": limits.max_geometry_checks, "original_json_nodes": raw_counts["json_nodes"],
        "nested_library_nodes": nested_nodes, "outer_library_nodes": library_counts["json_nodes"],
        "nested_library_decode_passes": nested_passes, "outer_library_decode_passes": outer_passes,
        "node_work_upper_bound": node_work, "production_source_occurrence_bytes": production_bytes,
        "library_source_bytes": content_bytes, "source_byte_work_upper_bound": source_work}


def _leaf_limits(limits, path_limits):
    defaults = EquipmentInstallationPathLimits()
    return replace(defaults, max_equipment=min(limits.max_equipment, defaults.max_equipment),
        max_rooms=min(limits.max_rooms, defaults.max_rooms), max_poses=path_limits.max_poses,
        max_source_objects=min(limits.max_source_objects, defaults.max_source_objects),
        max_sources=min(limits.max_library_sources, defaults.max_sources),
        max_source_bytes=min(limits.max_source_bytes, defaults.max_source_bytes),
        max_total_source_bytes=min(limits.max_total_source_bytes, defaults.max_total_source_bytes),
        max_total_utf8_bytes=min(limits.max_input_bytes, defaults.max_total_utf8_bytes),
        max_nodes=min(limits.max_nodes, defaults.max_nodes), max_depth=min(limits.max_depth, defaults.max_depth),
        max_vertices=min(limits.max_scenario_vertices, defaults.max_vertices),
        max_geometry_checks=min(path_limits.max_geometry_checks, defaults.max_geometry_checks),
        max_findings=min(limits.max_findings, defaults.max_findings))


def _actual_failures(value, inherited="FAIL"):
    """Prefer originating evidence over a bounded-finding delivery wrapper."""
    if type(value) is dict:
        state = value.get("status", inherited)
        first = value.get("first_known_failure")
        if type(first) is dict:
            yield from _actual_failures(first, "FAIL")
        if "code" in value and state == "FAIL" and value["code"] not in {"finding_budget_exceeded", "output_budget_exceeded"}:
            yield value
        for key, child in value.items():
            if key not in {"first_known_failure", "source_frames"} and type(child) in (dict, list):
                yield from _actual_failures(child, state)
    elif type(value) is list:
        for child in value:
            yield from _actual_failures(child, inherited)


def _retained_path_failures(section, limit):
    retained, seen, total = [], set(), 0
    for wrapper in section["reports"]:
        if wrapper["status"] != "FAIL":
            continue
        for original in _actual_failures(wrapper["evidence"]):
            selected = {"code": original["code"], "status": "FAIL"}
            for key in ("source_id", "source_pointer", "equipment_id", "related_equipment_id", "opening_id", "segment_index",
                        "pose_index", "obstacle_index", "role", "envelope_id", "overlap_area_mm2"):
                if key in original:
                    selected[key] = original[key]
            for key, maximum in (("message", 160), ("path", 128)):
                if type(original.get(key)) is str:
                    selected[key] = original[key][:maximum]
            signature = (wrapper["request_id"], _common._sha(selected))
            if signature in seen:
                continue
            seen.add(signature)
            total += 1
            if len(retained) < limit:
                retained.append({"component": "installation_paths", "identity": wrapper["identity"],
                    "request_id": wrapper["request_id"], "request_sha256": wrapper["request_sha256"], "failure": selected})
    return retained, total


def _bounded_output(report, limits, *, additional_failures=(), additional_failure_total=0,
                    additional_summary=None, additional_delivery_incomplete=False):
    size = 0
    for chunk in json.JSONEncoder(sort_keys=True, ensure_ascii=False, allow_nan=False).iterencode(report):
        size += len(chunk.encode("utf-8"))
        if size <= limits.max_report_bytes:
            continue
        compact = {key: report[key] for key in ("schema_version", "scope", "document_status", "status", "ok",
            "engineering_acceptance", "authenticated_approval", "full_facility_validation", "proven_building_infeasible",
            "identity", "review_source")}
        compact.update(inventory_status="INCOMPLETE", source_frames={}, schedules={}, reports={}, issues=[{
            "code": "output_budget_exceeded", "status": "UNKNOWN", "message": "Detailed process and path delivery inventory exceeds its byte bound"}])
        section = report.get("installation_paths")
        path_rows, path_total = ([], 0) if section is None else _retained_path_failures(section, limits.max_findings)
        common_rows, common_total = _common._originating_failures(report, limits.max_findings)
        # Reserve an originating path conflict even if an earlier common report
        # also failed. The inventory truthfully accounts for omitted conflicts.
        retained = (list(additional_failures) + path_rows + common_rows)[:limits.max_findings]
        total = additional_failure_total + path_total + common_total
        compact["retained_failures"] = retained
        compact["failure_inventory"] = {"status": "INCOMPLETE", "originating_failures": total,
            "retained": len(retained), "omitted": total - len(retained),
            "count_scope": "Available distinct originating witnesses after bounded leaf inventories",
            "leaf_delivery_incomplete": additional_delivery_incomplete or (section is not None and any(
                row["evidence"].get("finding_inventory", {}).get("status") == "INCOMPLETE"
                for row in section["reports"]))}
        if section is not None:
            compact["installation_paths"] = {"identity": section["identity"], "status": section["status"],
                "request_set_sha256": section["request_set_sha256"], "inventory_status": "INCOMPLETE",
                "full_scenario_validation": "UNKNOWN", "three_dimensional_feasibility": "UNKNOWN", "engineering_acceptance": "PENDING"}
        if additional_summary is not None:
            compact["additional_reports"] = additional_summary
        while len(json.dumps(compact, ensure_ascii=False, allow_nan=False).encode("utf-8")) > limits.max_report_bytes and len(retained) > 1:
            retained.pop()
            compact["failure_inventory"].update(retained=len(retained), omitted=total - len(retained))
        if section is not None and len(json.dumps(compact, ensure_ascii=False, allow_nan=False).encode("utf-8")) > limits.max_report_bytes:
            compact["installation_paths"].pop("identity")
        if len(json.dumps(compact, ensure_ascii=False, allow_nan=False).encode("utf-8")) > limits.max_report_bytes:
            raise ValueError("Process installation review delivery cannot hold its mandatory originating failure and identity")
        return compact
    return report


def _attach_paths(report, admitted, raw, *, limits, path_limits, work):
    """Attach fresh path evidence without applying a final delivery cap."""
    identity = report["identity"]
    requests = raw["scenario_requests"]
    rows = []
    for request in raw["installation_paths"]:
        evidence = validate_equipment_installation_path(request, building=admitted.building, layout=admitted.layout,
            equipment=admitted.equipment, library=raw["library"], card_by_equipment=requests["card_by_equipment"],
            actions_by_scenario=requests["actions_by_scenario"], limits=_leaf_limits(limits, path_limits)).to_dict()
        rows.append({"request_id": request["id"], "request_sha256": _common._sha(request), "identity": identity,
            "status": evidence["status"], "evidence": evidence})
    covered = {request["equipment_id"] for request in raw["installation_paths"]}
    targets = requests["installation_targets"]
    section = {"identity": identity, "source_requests": raw["installation_paths"],
        "request_set_sha256": _common._sha(raw["installation_paths"]), "requested_targets": list(targets),
        "covered_targets": [target for target in targets if target in covered],
        "missing_targets": [target for target in targets if target not in covered], "reports": rows,
        "status": "FAIL" if any(row["status"] == "FAIL" for row in rows) else "UNKNOWN",
        "scope": "Supplied independent local 2D paths; no complete installation approval", "ok": False,
        "full_scenario_validation": "UNKNOWN", "three_dimensional_feasibility": "UNKNOWN", "engineering_acceptance": "PENDING",
        "workload": work, "source_signature_policy": "Standalone canonical model signatures remain separate from the nine original raw process component hashes"}
    report["installation_paths"] = section
    if section["status"] == "FAIL":
        report["status"] = "FAIL"
    report["issues"].append({"code": "scoped_installation_paths_" + section["status"].lower(),
        "status": section["status"], "component": "installation_paths", "message": "Fresh supplied local path evidence is attached; full scenario and 3D feasibility remain UNKNOWN"})
    return report


def load_process_review_with_paths(path, *, include_windows=False, limits=ProcessReviewLimits(),
                                   path_limits=ProcessInstallationPathLimits()):
    """Read explicit version 0.2 without rewriting source or model signatures."""
    if not _common._limits_valid(limits) or not _valid_path_limits(path_limits) or type(include_windows) is not bool:
        raise ValueError("Process installation review requires bounded actual limits and a boolean window control")
    try:
        original, raw = read_review_source(path, limits=_common._parent_limits(limits))
    except _InputFailure as exc:
        raise ValueError(exc.issue.message) from exc
    work = {}

    def preflight(source):
        work.update(_path_preflight(source, limits, path_limits))
        return work["path_geometry_upper_bound"]

    try:
        report, admitted = _common._evaluate_process_source(original, raw, include_windows=include_windows,
            limits=limits, schema_check=_common._check_common_schema, additional_preflight=preflight)
    except _PrecisionGap as exc:
        report = _common._base(original)
        report.update(inventory_status="NOT_EVALUATED", issues=[{"code": "unrepresentable_path_geometry",
            "status": "UNKNOWN", "message": str(exc)}])
        return report
    if admitted is None:
        return report
    _attach_paths(report, admitted, raw, limits=limits, path_limits=path_limits, work=work)
    return _bounded_output(report, limits)

"""Bounded opt-in selection of literal source-bound production room choices.

Only fresh room/door evidence and original production adjacency predicates are
accepted. The finite catalog is not a whole-building feasibility proof, and no
equipment, route, facility or engineering capability is enabled here.
"""

from dataclasses import dataclass, field, fields, replace
import hashlib
import json
import math
import time

from .building import BuildingIR
from .models import Rect
from .orthogonal import OrthogonalPolygon
from .orthogonal_validation import OrthogonalDimensionPolicy, _intersect_intervals
from .production_program import _InputFailure, _json_bytes, _preflight, validate_production_program
from .production_layout_review import ProductionLayoutLimits
from . import production_layout_review as _production
from . import orthogonal_solver as _kernel


@dataclass(frozen=True)
class ProductionLayoutGenerationLimits(ProductionLayoutLimits):
    """Closed tighten-only admission, finite search and delivery controls."""

    max_candidates_per_room: int = 32
    max_candidates: int = 256
    max_attempts: int = 4
    max_cp_variables: int = 4096
    max_cp_constraints: int = 65536
    max_cp_objective_terms: int = 4096
    dimension_policy: OrthogonalDimensionPolicy = field(default_factory=OrthogonalDimensionPolicy)


class _Stop(ValueError):
    def __init__(self, code, message, *, status="UNKNOWN"):
        super().__init__(message)
        self.code, self.status = code, status


def _valid_limits(limits):
    defaults = ProductionLayoutGenerationLimits()
    if type(limits) is not ProductionLayoutGenerationLimits:
        return False
    for row in fields(defaults):
        if row.name == "dimension_policy":
            continue
        value = getattr(limits, row.name)
        if type(value) is not int or not 1 <= value <= getattr(defaults, row.name):
            return False
    policy, ceiling = limits.dimension_policy, OrthogonalDimensionPolicy()
    return (limits.max_report_bytes >= 4096 and type(policy) is OrthogonalDimensionPolicy and
        all(type(getattr(policy, row.name)) is int and
            1 <= getattr(policy, row.name) <= getattr(ceiling, row.name) for row in fields(ceiling)))


def _leaf_limits(limits):
    return ProductionLayoutLimits(**{row.name: getattr(limits, row.name)
        for row in fields(ProductionLayoutLimits())})


def _sha(value):
    return hashlib.sha256(_json_bytes(value)).hexdigest()


def _limits_record(limits):
    return {row.name: ({part.name: getattr(limits.dimension_policy, part.name)
        for part in fields(limits.dimension_policy)} if row.name == "dimension_policy"
        else getattr(limits, row.name)) for row in fields(limits)}


def _base(variants, limits):
    return {"schema_version": 1, "scope": "Finite supplied production room catalog and fresh local adjacency only",
        "backend": "production_room_catalog_v1", "document_status": "DRAFT", "status": "UNKNOWN",
        "generation_status": "UNKNOWN", "ok": False, "engineering_acceptance": "PENDING",
        "authenticated_approval": "UNKNOWN", "full_facility_validation": {"status": "UNKNOWN", "ok": False},
        "full_scenario_validation": "UNKNOWN", "three_dimensional_feasibility": "UNKNOWN",
        "proven_building_infeasible": False, "inventory_status": "NOT_EVALUATED",
        "requested_variants": variants, "attempts": 0, "source": {}, "source_frames": {},
        "candidates": [], "rejections": [], "issues": [], "workload": {}, "limits": _limits_record(limits),
        "policy": {"family": "Only the complete supplied finite room catalog",
            "acceptance": "Fresh room and actual door QA plus strict original production geometry",
            "required_contact": "One contiguous span at least original layout.door_width_mm; no epsilon acceptance",
            "forbidden_contact": "No positive shared span or positive room overlap",
            "approval": "No authenticated engineering, capacity, process-route or facility approval"}}


def _limit(value, maximum, message):
    if value > maximum:
        raise _Stop("input_budget_exceeded", message)


def _shape_schema(row):
    if type(row) is not dict:
        raise ValueError("Catalog choices require closed raw JSON footprint objects")
    if row.get("kind") == "orthogonal":
        if set(row) != {"kind", "units", "vertices"} or row["units"] != "mm":
            raise ValueError("Orthogonal catalog choices require only kind, mm units and vertices")
        points = row["vertices"]
        if type(points) is not list or len(points) < 4 or any(type(point) is not list or len(point) != 2
                or any(not _production._finite(value) for value in point) for point in points):
            raise ValueError("Orthogonal catalog vertices require bounded finite JSON coordinate pairs")
    elif set(row) != {"x", "y", "width", "height"} or any(
            not _production._finite(row[key]) for key in ("x", "y", "width", "height")):
        raise ValueError("Rectangular catalog choices require only four finite numeric dimensions")


def _raw_admission(program, building, catalog, limits):
    parent = _production._program_limits(limits)
    whole = _preflight([program, building, catalog], parent)
    if type(program) is not dict or type(building) is not dict or type(catalog) is not dict:
        raise ValueError("Production generation requires original JSON objects and a literal room catalog")
    program_counts = _preflight(program, parent)
    embedded = building.get("production_program")
    embedded_counts = _preflight(embedded, parent) if embedded is not None else {
        "json_nodes": 0, "json_utf8_bytes": 0, "source_utf8_bytes": 0}
    source_bytes = program_counts["source_utf8_bytes"] + embedded_counts["source_utf8_bytes"]
    _limit(source_bytes, limits.max_total_source_bytes, "Top and embedded source-content occurrences exceed their shared bound")
    program_schema, building_schema, embedded_schema, layout_schema, _ = _production._schemas()
    errors = _production._schema_issues(program, program_schema)
    errors += _production._schema_issues(building, embedded_schema if embedded is not None else building_schema)
    if type(building.get("layout")) is dict:
        errors += _production._schema_issues(building["layout"], layout_schema)
    if errors:
        raise ValueError("Original production source must satisfy its closed schemas: " + errors[0].message)
    source = building["layout"]
    room_ids = {row["id"] for row in source["rooms"]}
    if set(catalog) != room_ids:
        raise ValueError("The literal catalog must cover every original room ID and no other IDs")
    _limit(len(room_ids), min(limits.max_rooms, _kernel.OrthogonalSolverBudget().max_rooms),
        "Original room inventory exceeds the bounded producer kernel")
    total = 0
    for choices in catalog.values():
        if type(choices) is not list or not choices:
            raise ValueError("Every original room requires a nonempty raw JSON list of choices")
        _limit(len(choices), limits.max_candidates_per_room, "A complete literal room domain exceeds its candidate bound")
        total += len(choices)
        for row in choices:
            _shape_schema(row)
    _limit(total, limits.max_candidates, "Combined literal catalog candidates exceed their bound")
    objects = 1 + len(room_ids) + sum(len(building[key]) for key in ("zones", "equipment", "flows"))
    objects += len(source.get("doors", [])) + len(source.get("windows", []))
    objects += len(source["boundary"].get("cutouts", [])) + int(source.get("external_entry") is not None)
    grid = building.get("structural_grid")
    if grid is not None:
        objects += 1 + sum(len(grid.get(key, [])) for key in ("axes_x_mm", "axes_y_mm", "labels_x", "labels_y"))
    references = whole["typed_references"] + sum(len(row.get(key, [])) for row in source["rooms"]
        for key in ("required_adjacency", "preferred_adjacency", "forbidden_adjacency"))
    references += sum(len(row[key]) for row in building["zones"]
        for key in ("room_ids", "required_adjacency", "preferred_adjacency", "forbidden_adjacency"))
    references += sum(len(row[key]) for row in building["flows"] for key in ("from_ids", "to_ids"))
    references += 2 * len(source.get("doors", [])) + 1
    _limit(objects, limits.max_building_objects, "Combined source object inventory exceeds its bound")
    _limit(references, limits.max_references, "Combined source reference inventory exceeds its bound")
    return whole, program_counts, embedded_counts, source_bytes


def _identity(program, building, catalog):
    base = {key: value for key, value in building.items() if key != "production_program"}
    return {"building_sha256": _sha(building), "production_program_sha256": _sha(program),
        "program_base_building_sha256": _sha(base), "room_catalog_sha256": _sha(catalog)}


def _binding(program, building, identity):
    if "production_program" in building and _json_bytes(building["production_program"]) != _json_bytes(program):
        raise _Stop("embedded_production_program_mismatch",
            "Whole embedded and top production programs differ in original fields or scalar types", status="FAIL")
    if program["building_sha256"] != identity["program_base_building_sha256"]:
        raise _Stop("stale_building_binding", "Production program differs from the unchanged original base building", status="FAIL")


def _dimension_work(row, room, limits):
    """Pure raw-slab upper bounds; rectangle QA performs no polygon scan."""
    if row.get("kind") != "orthogonal":
        return 0, 0
    points, policy = row["vertices"], limits.dimension_policy
    levels = sorted({point[1] for point in points})
    slab_count, edge_count = len(levels) - 1, len(points)
    coarse = slab_count * (slab_count + 1) // 2 * (2 * edge_count + 1)
    _limit(coarse, limits.max_pair_checks, "Raw polygon dimension-planning work exceeds the combined geometry bound")
    slabs = []
    for bottom, top in zip(levels, levels[1:]):
        middle = bottom + (top - bottom) / 2
        crossings = sorted(first[0] for first, second in zip(points, (*points[1:], points[0]))
            if first[0] == second[0] and min(first[1], second[1]) < middle < max(first[1], second[1]))
        if len(crossings) % 2:
            raise ValueError("Literal orthogonal catalog has invalid scanline parity")
        slabs.append(tuple(zip(crossings[::2], crossings[1::2])))
    width, depth = room.get("min_width", 2000.0), room.get("min_depth", 2000.0)
    orientations = [(width, depth)]
    if room.get("rotatable", False) and width != depth:
        orientations.append((depth, width))
    work, vertices = slab_count * edge_count, 0
    for width, depth in orientations:
        retained, steps, exhausted = set(), 0, False
        for start in range(len(slabs)):
            active = slabs[start]
            for end in range(start, len(slabs)):
                cost = len(active) + len(slabs[end]) + 1
                if steps + cost > policy.max_scan_steps:
                    exhausted = True
                    break
                steps += cost
                if end > start:
                    active = _intersect_intervals(active, slabs[end])
                if not active:
                    break
                if levels[end + 1] - levels[start] + 1e-6 >= depth:
                    for left, right in active:
                        if right - left + 1e-6 >= width:
                            key = (left, right, levels[start])
                            if key not in retained and len(retained) >= policy.max_candidates:
                                exhausted = True
                                break
                            retained.add(key)
                if exhausted:
                    break
            if exhausted:
                break
        coverage_edges = 4 * len(retained)
        vertices += coverage_edges
        work += steps + 16 * len(retained) + coverage_edges * coverage_edges + coverage_edges * edge_count
    return work, vertices


def _layout_envelope(catalog, parent, attempts):
    # A finite binary64 JSON scalar uses at most this many ASCII characters.
    numeric_envelope = -1.2345678901234568e-100
    envelope = _preflight({"variant": attempts, "objective_value": -(2**62 - 1),
        "geometry_version": "0.2", "units": "mm", "rooms": {}}, parent)
    nodes, utf = envelope["json_nodes"], envelope["json_utf8_bytes"]
    for room_id, choices in catalog.items():
        row_counts = []
        for row in choices:
            if row.get("kind") == "orthogonal":
                shape = {"kind": "orthogonal", "units": "mm", "vertices": [
                    [float(value) for value in point] for point in row["vertices"]], "area_m2": numeric_envelope}
            else:
                shape = {**row, "area_m2": numeric_envelope}
            row_counts.append(_preflight(shape, parent))
        nodes += 1 + max(row["json_nodes"] for row in row_counts)
        utf += len(_json_bytes(room_id)) + 1 + max(row["json_utf8_bytes"] for row in row_counts) + 1
    return nodes, utf


def _reserve(program, building, catalog, limits, raw_counts, program_counts, embedded_counts, source_bytes, variants):
    """Admit every catalog consumer and bounded attempt before constructors."""
    parent = _production._program_limits(limits)
    source, room_ids = building["layout"], sorted(catalog)
    rooms = {row["id"]: row for row in source["rooms"]}
    sizes = {room_id: len(catalog[room_id]) for room_id in room_ids}
    pair_count = sum(sizes[first] * sizes[second] for index, first in enumerate(room_ids) for second in room_ids[index + 1:])
    preferred = {tuple(sorted((row["id"], other))) for row in source["rooms"]
        for other in row.get("preferred_adjacency", []) if row["id"] != other}
    preferred_count = sum(sizes[first] * sizes[second] for first, second in preferred)
    candidates = sum(sizes.values())
    cp_variables = candidates + preferred_count
    cp_constraints = len(room_ids) + pair_count + 3 * preferred_count + limits.max_attempts
    cp_terms = candidates + preferred_count
    _limit(cp_variables, limits.max_cp_variables, "All candidate and preferred-support CP variables exceed their bound")
    _limit(cp_constraints, limits.max_cp_constraints, "All compatibility, support and bounded no-good CP constraints exceed their bound")
    _limit(cp_terms, limits.max_cp_objective_terms, "All literal and preferred-support objective terms exceed their bound")
    _limit(pair_count, _kernel.OrthogonalSolverBudget().max_pair_checks, "The complete literal compatibility table exceeds the kernel bound")
    boundary = source["boundary"]
    _production._rectangle_precision({"x": 0, "y": 0, "width": boundary["width"], "height": boundary["height"]})
    for cutout in boundary.get("cutouts", []):
        _production._rectangle_precision(cutout)
    boundary_edges = 4 * (1 + len(boundary.get("cutouts", [])))
    edges, dimension, dimension_vertices, positions, area_errors = {}, {}, {}, {}, {}
    for room_id in room_ids:
        edges[room_id], dimension[room_id], dimension_vertices[room_id] = [], [], []
        positions[room_id], area_errors[room_id] = [], []
        target = rooms[room_id]["target_area"] * 1000000
        if not _production._finite(target) or target <= 0:
            raise _Stop("objective_numeric_limit", "Source target area exceeds finite objective arithmetic")
        for row in catalog[room_id]:
            edge_count = _production._shape_precision(row)
            edges[room_id].append(edge_count)
            _limit(sum(sum(values) for values in edges.values()) + boundary_edges, limits.max_vertices,
                "All literal catalog and boundary vertices exceed their shared bound")
            scan_work, scan_vertices = _dimension_work(row, rooms[room_id], limits)
            dimension[room_id].append(scan_work)
            dimension_vertices[room_id].append(scan_vertices)
            if row.get("kind") == "orthogonal":
                xs, ys = [point[0] for point in row["vertices"]], [point[1] for point in row["vertices"]]
                left, bottom, right, top = min(xs), min(ys), max(xs), max(ys)
            else:
                left, bottom, right, top = row["x"], row["y"], row["x"] + row["width"], row["y"] + row["height"]
            bbox_area = (right - left) * (top - bottom)
            if not _production._finite(bbox_area):
                raise _Stop("objective_numeric_limit", "Literal bounding-frame area exceeds finite objective arithmetic")
            area_errors[room_id].append(math.ceil(max(target, bbox_area)) + 1)
            positions[room_id].append(abs(round((left + bottom) / source["grid_mm"])) *
                limits.max_candidates_per_room + sizes[room_id] - 1)
    position_bound = sum(max(values) for values in positions.values())
    preference_weight = position_bound + 1
    area_weight = len(preferred) * preference_weight + position_bound + 1
    objective_bound = sum(max(area_errors[room_id]) * area_weight + max(positions[room_id]) for room_id in room_ids)
    objective_bound += len(preferred) * preference_weight
    if objective_bound >= 2**62:
        raise _Stop("objective_numeric_limit", "The complete CP objective coefficient ceiling exceeds its integer domain")
    maxima = {room_id: max(edges[room_id]) for room_id in room_ids}
    all_edges = sum(sum(values) for values in edges.values())
    selected_edges = sum(maxima.values())
    selected_pairs = sum(maxima[first] * maxima[second] for index, first in enumerate(room_ids) for second in room_ids[index + 1:])
    selected_base = sum(value * value for value in maxima.values()) + selected_edges * boundary_edges + boundary_edges**2 + selected_pairs
    requirement_pairs = {tuple(sorted((row["room_a"], row["room_b"]))) for row in program["requirements"]}
    production_work = selected_base + sum(maxima.get(first, 0) * maxima.get(second, 0) for first, second in requirement_pairs)
    catalog_work = 12 * sum(value * value for values in edges.values() for value in values)
    catalog_work += boundary_edges**2 + 6 * all_edges * boundary_edges
    catalog_work += sum(sum(values) for values in dimension.values())
    catalog_work += 2 * sum(sum(edges[first]) * sum(edges[second]) for index, first in enumerate(room_ids) for second in room_ids[index + 1:])
    opening_count = len(source.get("doors", [])) + int(source.get("external_entry") is not None)
    opening_count += len({tuple(sorted((row["id"], other))) for row in source["rooms"]
        for other in row.get("required_adjacency", []) if row["id"] != other})
    opening_count += len(source.get("windows", []))
    # Repeated canonical formation, result predicates, mitre inset/wall bands,
    # actual opening cuts/approaches and source window consumers are reserved.
    room_work = 8 * selected_base + sum(max(values) for values in dimension.values())
    room_work += 4 * (selected_edges + 2 * opening_count)**2
    geometry_work = catalog_work + (limits.max_attempts + 1) * production_work + limits.max_attempts * room_work
    _limit(geometry_work, limits.max_pair_checks, "Catalog and all bounded fresh room/production geometry work exceed their shared bound")
    catalog_aux = sum(sum(values) for values in dimension_vertices.values())
    selected_aux = sum(max(values) for values in dimension_vertices.values())
    vertex_work = boundary_edges + all_edges + catalog_aux + (limits.max_attempts + 1) * selected_edges + limits.max_attempts * selected_aux
    _limit(vertex_work, limits.max_vertices, "Catalog and repeated selected-layout/coverage vertex inventories exceed their shared bound")
    layout_nodes, layout_utf = _layout_envelope(catalog, parent, limits.max_attempts)
    building_counts, catalog_counts = _preflight(building, parent), _preflight(catalog, parent)
    layout_source_counts = _preflight(source, parent)
    raw3_nodes = 1 + program_counts["json_nodes"] + building_counts["json_nodes"] + layout_nodes
    raw3_utf = 4 + program_counts["json_utf8_bytes"] + building_counts["json_utf8_bytes"] + layout_utf
    program_decode_nodes = program_counts["json_nodes"] + embedded_counts["json_nodes"]
    program_decode_utf = program_counts["json_utf8_bytes"] + embedded_counts["json_utf8_bytes"]
    probe_count = limits.max_attempts + 1
    finding_bound = min(limits.max_findings, len(program["requirements"]) + len(room_ids)**2 + len(room_ids) + 32)
    leaf_report_utf = 2 * (raw3_utf + program_counts["json_utf8_bytes"]) + finding_bound * 1024 + 4096
    leaf_report_nodes = 2 * raw3_nodes + program_counts["json_nodes"] + finding_bound * 32 + 512
    rejection_bound = min(limits.max_findings, probe_count * (len(program["requirements"]) + len(room_ids)**2 + len(room_ids)))
    delivery_utf = raw_counts["json_utf8_bytes"] + variants * (leaf_report_utf + layout_utf + 4096) + rejection_bound * 1024 + 8192
    delivery_nodes = raw_counts["json_nodes"] + variants * (leaf_report_nodes + layout_nodes + 256) + rejection_bound * 32 + 512
    # Full original snapshot plus every raw3 traversal/snapshot, introduced
    # programme JSON decode and complete retained report frames are charged.
    node_work = 2 * raw_counts["json_nodes"] + 4 * catalog_counts["json_nodes"]
    node_work += probe_count * (3 * raw3_nodes + 2 * program_decode_nodes + leaf_report_nodes)
    node_work += limits.max_attempts * (2 * layout_source_counts["json_nodes"] + 2 * layout_nodes)
    node_work += delivery_nodes
    byte_work = 2 * raw_counts["json_utf8_bytes"] + 4 * catalog_counts["json_utf8_bytes"]
    byte_work += probe_count * (3 * raw3_utf + 2 * program_decode_utf + leaf_report_utf)
    byte_work += limits.max_attempts * (2 * layout_source_counts["json_utf8_bytes"] + 2 * layout_utf)
    byte_work += delivery_utf
    source_content_work = source_bytes * (2 + 5 * probe_count)
    _limit(node_work, limits.max_nodes, "Full raw snapshots, possible layouts, nested decodes and reports exceed the shared node bound")
    _limit(byte_work, limits.max_total_utf8_bytes, "Full raw snapshots, possible layouts, nested decodes and reports exceed the shared JSON UTF bound")
    _limit(source_content_work, limits.max_total_source_bytes, "Repeated top/embedded source-content work exceeds its shared byte bound")
    _limit(delivery_utf, limits.max_report_bytes, "Complete source-derived candidate and failure delivery cannot fit its reserved report bound")
    return {"input_json_nodes": raw_counts["json_nodes"], "input_json_utf8_bytes": raw_counts["json_utf8_bytes"],
        "literal_candidates": candidates, "literal_catalog_vertices": all_edges, "source_boundary_vertices": boundary_edges,
        "combined_vertex_work_upper_bound": vertex_work, "catalog_geometry_work_upper_bound": catalog_work,
        "per_selected_room_geometry_work_upper_bound": room_work, "per_production_geometry_work_upper_bound": production_work,
        "production_review_invocation_limit": probe_count, "geometry_work_upper_bound": geometry_work,
        "generated_layout_json_nodes_upper_bound": layout_nodes, "generated_layout_json_utf8_bytes_upper_bound": layout_utf,
        "node_work_upper_bound": node_work, "source_byte_work_upper_bound": byte_work,
        "source_content_work_upper_bound": source_content_work, "report_bytes_upper_bound": delivery_utf,
        "cp_variables_upper_bound": cp_variables, "cp_constraints_upper_bound": cp_constraints,
        "cp_objective_terms_upper_bound": cp_terms, "cp_objective_coefficient_upper_bound": objective_bound,
        "candidate_pair_checks_upper_bound": pair_count, "preferred_supports_upper_bound": preferred_count}


def _retain(report, evidence, limits, stage, attempt=0):
    records = evidence.get("retained_failures", []) or [row for row in evidence.get("issues", []) if row.get("status") == "FAIL"]
    for row in records:
        report["failure_inventory"]["originating_failures"] += 1
        if len(report["rejections"]) < limits.max_findings:
            report["rejections"].append({"stage": stage, "attempt": attempt, **_production._failure_row(row)})
    report["failure_inventory"]["retained"] = len(report["rejections"])
    report["failure_inventory"]["omitted"] = report["failure_inventory"]["originating_failures"] - len(report["rejections"])


def _delivery(report, limits):
    if len(_json_bytes(report)) <= limits.max_report_bytes:
        return report
    omitted_candidates = len(report["candidates"])
    original_status = report["status"]
    report.update(source_frames={}, candidates=[], inventory_status="INCOMPLETE", generation_status="UNKNOWN",
        status="FAIL" if original_status == "FAIL" else "UNKNOWN",
        omission_inventory={"source_frames": 3, "candidates": omitted_candidates,
            "reason": "Bounded complete delivery exceeded its byte cap"})
    report["issues"] = [{"code": "output_budget_exceeded", "status": "UNKNOWN",
        "message": "Detailed source/candidate delivery omitted; no complete generated set is claimed"}]
    while len(_json_bytes(report)) > limits.max_report_bytes and len(report["rejections"]) > 1:
        report["rejections"].pop()
    report["failure_inventory"]["retained"] = len(report["rejections"])
    report["failure_inventory"]["omitted"] = report["failure_inventory"]["originating_failures"] - len(report["rejections"])
    if len(_json_bytes(report)) > limits.max_report_bytes:
        report["workload"] = {"omitted": True}
        report["policy"] = {"scope": "Finite catalog only; incomplete bounded delivery"}
    if len(_json_bytes(report)) > limits.max_report_bytes:
        raise ValueError("Delivery bound cannot retain original identity and an originating failure")
    return report


def generate_production_layout_candidates(program_raw, *, building_raw, room_catalog,
        variants=1, seed=42, time_limit_seconds=10, deterministic_units=None,
        limits=ProductionLayoutGenerationLimits(), include_windows=False):
    """Select a bounded literal room set without rewriting original authority."""
    if not _valid_limits(limits):
        raise ValueError("Production generation requires exact closed tighten-only limit and dimension-policy types")
    if type(variants) is not int or not 1 <= variants <= limits.max_attempts:
        raise ValueError("variants requires a positive actual integer within the attempt bound")
    if type(seed) is not int or not 0 <= seed <= 2**31 - 1 or type(include_windows) is not bool:
        raise ValueError("seed and window controls require actual bounded integer/boolean values")
    for name, value in (("time_limit_seconds", time_limit_seconds), ("deterministic_units", deterministic_units)):
        if value is None and name == "deterministic_units":
            continue
        if not _production._finite(value) or not 0 < value <= 86400:
            raise ValueError(name + " requires a bounded finite positive actual number")
    started = time.monotonic()
    report = _base(variants, limits)
    report["failure_inventory"] = {"originating_failures": 0, "retained": 0, "omitted": 0}
    try:
        raw, program_counts, embedded_counts, source_bytes = _raw_admission(program_raw, building_raw, room_catalog, limits)
        report["source"] = _identity(program_raw, building_raw, room_catalog)
        _binding(program_raw, building_raw, report["source"])
        report["workload"] = _reserve(program_raw, building_raw, room_catalog, limits,
            raw, program_counts, embedded_counts, source_bytes, variants)
        captured = _json_bytes([program_raw, building_raw, room_catalog])
        program, building_source, catalog_source = json.loads(captured)
        if _identity(program_raw, building_raw, room_catalog) != report["source"]:
            raise _Stop("original_source_changed", "Original inputs changed during admission", status="FAIL")
        report["source_frames"] = {"building": building_source, "production_program": program, "room_catalog": catalog_source}
        if not program["requirements"]:
            raise _Stop("no_production_requirements", "Empty original requirements cannot establish production geometry acceptance")
        base_source = {key: value for key, value in building_source.items() if key != "production_program"}
        building = BuildingIR.from_mapping(base_source)
        if _json_bytes(building.to_dict()) != _json_bytes(base_source):
            raise _Stop("unsupported_original_source_identity", "Original scalar types or fields lie outside exact canonical roundtrip")
        contract = validate_production_program(program, building=building, limits=_production._program_limits(limits))
        report["program_validation"] = contract.to_dict()
        if contract.schema_status != "PASS" or contract.binding_status != "PASS" or contract.evidence_status == "FAIL":
            report["issues"] = [row.to_dict() for row in contract.issues]
            report["status"] = "FAIL"
            return _delivery(report, limits)
        if "production_program" in building_source:
            if _json_bytes(replace(building, production_program=contract.program).to_dict()) != _json_bytes(building_source):
                raise _Stop("unsupported_original_source_identity", "Whole original embedded frame changes during canonical roundtrip")
        leaf_limits = _leaf_limits(limits)
        first_layout = {"variant": 1, "rooms": {room_id: choices[0] for room_id, choices in catalog_source.items()}}
        if any(row.get("kind") == "orthogonal" for row in first_layout["rooms"].values()):
            first_layout.update(geometry_version="0.2", units="mm")
        initial = _production.validate_production_program_layout(program,
            building_raw=building_source, layout_raw=first_layout, limits=leaf_limits)
        _retain(report, initial, limits, "initial_literal_production")
        if initial.get("inventory_status") != "COMPLETE":
            raise _Stop("incomplete_initial_production_review", "Bounded original production review is incomplete")
        if initial["binding_status"] != "PASS":
            raise _Stop("original_production_binding_failed", "Fresh original program binding does not pass", status="FAIL")
        catalog, origins = {}, {}
        for room_id, descriptors in catalog_source.items():
            catalog[room_id], origins[room_id] = [], {}
            for descriptor in descriptors:
                shape = OrthogonalPolygon.from_mapping(descriptor) if descriptor.get("kind") == "orthogonal" else Rect(**descriptor)
                catalog[room_id].append(shape)
                effective_id = _kernel._hash_json(_kernel._shape_data(shape))
                origins[room_id].setdefault(effective_id, _sha(descriptor))
        accepted = []
        deadline = math.inf if deterministic_units is not None else started + time_limit_seconds

        def postselection(result, room_report, effective_ids, attempt):
            layout = result.to_dict()
            evidence = _production.validate_production_program_layout(program,
                building_raw=building_source, layout_raw=layout, limits=leaf_limits)
            _retain(report, evidence, limits, "production_qa", attempt)
            complete = evidence.get("inventory_status") == "COMPLETE"
            valid = complete and evidence["binding_status"] == "PASS" and evidence["geometry_status"] == "PASS"
            valid = valid and evidence["provenance_status"] != "FAIL" and time.monotonic() < deadline
            if valid:
                accepted.append({"layout": layout, "room_report": room_report.to_dict(),
                    "production_review": evidence,
                    "candidate_ids": {room_id: origins[room_id][identifier] for room_id, identifier in effective_ids.items()}})
            return {"accepted": valid, "reason": "Fresh complete strict production geometry does not pass",
                "evidence": {"geometry_status": evidence["geometry_status"], "binding_status": evidence["binding_status"]}}

        remaining = time_limit_seconds if deterministic_units is not None else deadline - time.monotonic()
        if remaining <= 0:
            raise _Stop("search_budget_exhausted", "Admission and initial fresh review used the wall-clock allowance")
        kernel_budget = _kernel.OrthogonalSolverBudget(max_rooms=min(limits.max_rooms, 64),
            max_candidates_per_room=limits.max_candidates_per_room,
            max_catalog_steps_per_room=limits.max_candidates_per_room,
            max_pair_checks=min(limits.max_pair_checks, 100000), max_attempts=limits.max_attempts,
            max_rejections=min(limits.max_findings, 128), dimension_policy=limits.dimension_policy)
        solved = _kernel._solve_orthogonal_candidates(building.layout, catalog=catalog,
            variants=variants, seed=seed, time_limit_seconds=remaining, deterministic_units=deterministic_units,
            budget=kernel_budget, include_windows=include_windows, _postselection=postselection)
        report.update(attempts=solved.evidence["attempts"], candidates=accepted,
            inventory_status="COMPLETE", stop_reason=solved.evidence["stop_reason"],
            effective_catalog_sha256=solved.evidence["catalog_sha256"],
            kernel_rejection_inventory={"total": solved.evidence["rejection_count"],
                "counts": solved.evidence["rejection_counts"], "last": solved.evidence["last_rejection"]})
        if len(accepted) == variants:
            report["generation_status"] = "PASS"
            report["status"] = "PASS" if contract.evidence_status == "PASS" and all(
                row["production_review"]["status"] == "PASS" for row in accepted) else "UNKNOWN"
        return _delivery(report, limits)
    except _InputFailure as exc:
        stop = _Stop(exc.issue.code, exc.issue.message,
            status="UNKNOWN" if exc.issue.code == "input_budget_exceeded" else "FAIL")
    except _production._PrecisionGap as exc:
        stop = _Stop("unsupported_source_geometry_precision", str(exc))
    except _Stop as exc:
        stop = exc
    except (ValueError, TypeError, KeyError, OverflowError, AttributeError, RecursionError) as exc:
        stop = _Stop("invalid_original_source", str(exc)[:512], status="FAIL")
    report["status"] = stop.status
    report["issues"].append({"code": stop.code, "status": stop.status, "message": str(stop)[:512]})
    if stop.status == "FAIL":
        _retain(report, {"issues": [report["issues"][-1]]}, limits, "input")
    return _delivery(report, limits)

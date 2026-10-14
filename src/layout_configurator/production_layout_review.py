"""Fresh actual contacts for supplied production-program room requirements.

This standalone reader preserves raw source identity and leaves every existing
review, producer and facility capability boundary unchanged. A local PASS is
not a transfer, throughput, process, scenario or engineering acceptance.
"""

import copy
from dataclasses import dataclass, fields, replace
from fractions import Fraction
from functools import lru_cache
import hashlib
import json
import math

from .building import BuildingIR
from .orthogonal import OrthogonalPolygon
from . import result_geometry
from .production_program import (ProductionProgramLimits, _InputFailure,
    _json_bytes, _preflight, _schema_issues, validate_production_program)
from .resources import resource_path
from .schema import DEFAULT_SCHEMA_PATH, load_building_schema, load_json_schema


@dataclass(frozen=True)
class ProductionLayoutLimits(ProductionProgramLimits):
    """Separate tighten-only input, actual geometry and delivery bounds."""

    max_rooms: int = 128
    max_vertices: int = 16384
    max_pair_checks: int = 131072
    max_findings: int = 256
    max_report_bytes: int = 1048576


class _PrecisionGap(ValueError):
    pass


def _valid_limits(limits):
    defaults = ProductionLayoutLimits()
    return type(limits) is ProductionLayoutLimits and all(type(getattr(limits, row.name)) is int and
        1 <= getattr(limits, row.name) <= getattr(defaults, row.name) for row in fields(defaults)) and limits.max_report_bytes >= 4096


def _program_limits(limits):
    return ProductionProgramLimits(**{row.name: getattr(limits, row.name) for row in fields(ProductionProgramLimits())})


def _sha(value):
    return hashlib.sha256(_json_bytes(value)).hexdigest()


def _issue(code, message, status="FAIL", **evidence):
    return {"code": code, "status": status, "message": message, **evidence}


def _base():
    return {"schema_version": 1, "scope": "Supplied actual production room adjacency requirements only",
        "document_status": "DRAFT", "status": "UNKNOWN", "ok": False, "geometry_status": "NOT_EVALUATED",
        "binding_status": "NOT_EVALUATED", "provenance_status": "NOT_EVALUATED",
        "engineering_acceptance": "PENDING", "authenticated_approval": "UNKNOWN",
        "full_facility_validation": {"status": "UNKNOWN", "ok": False}, "full_scenario_validation": "UNKNOWN",
        "three_dimensional_feasibility": "UNKNOWN", "proven_building_infeasible": False,
        "inventory_status": "NOT_EVALUATED", "source": {}, "source_frames": {}, "requirements": [],
        "program_validation": None, "source_layout_status": "NOT_EVALUATED",
        "source_layout_scope": "Original room identities, declared area, boundary coverage and interior overlaps only",
        "counts": {}, "issues": [],
        "policy": {"contact_geometry": "Actual maximal collinear shared spans; corners excluded",
            "required_contact": "One contiguous span at least canonical layout.door_width_mm; no disjoint summation",
            "forbidden_contact": "Any positive shared span; positive room overlap independently fails",
            "wall_geometry": "Gross room boundaries are wall centerlines; wall thickness does not excuse gaps",
            "transfer_permission": "NOT_EVALUATED", "flow_corridor_reservation": "NOT_EVALUATED",
            "capacity_simulation": "NOT_EVALUATED", "minimum_room_dimensions": "NOT_EVALUATED",
            "opening_geometry": "NOT_EVALUATED", "source_uri_access": False}}


@lru_cache(maxsize=1)
def _schemas():
    return (load_json_schema(resource_path("schemas/production_program.schema.json")),
        load_building_schema(), load_building_schema(include_production_program=True),
        load_json_schema(DEFAULT_SCHEMA_PATH), load_json_schema(resource_path("schemas/layout_result.schema.json")))


def _budget(message):
    raise _InputFailure("input_budget_exceeded", "$", message)


def _finite(value):
    return type(value) in (int, float) and (type(value) is not int or value.bit_length() <= 1024) and math.isfinite(value)


def _rectangle_precision(row, *, height="height"):
    x, y, width, depth = row["x"], row["y"], row["width"], row[height]
    if any(not _finite(value) for value in (x, y, width, depth, x + width, y + depth)):
        raise _PrecisionGap("Actual rectangle endpoints exceed supported finite precision")
    if width <= 0 or depth <= 0:
        raise ValueError("Actual rectangles require positive dimensions")
    if x + width <= x or y + depth <= y or width * depth <= 0 or not math.isfinite(width * depth):
        raise _PrecisionGap("Positive actual rectangle extent or area is lost at world-coordinate precision")
    if any(abs(value) > 1000000 for value in (x, y, x + width, y + depth)):
        raise _PrecisionGap("Actual rectangle coordinates exceed the supported precision domain")


def _shape_precision(row):
    if row.get("kind") != "orthogonal":
        _rectangle_precision(row)
        return 4
    points = row["vertices"]
    if any(not _finite(value) or abs(value) > 1000000 for point in points for value in point):
        raise _PrecisionGap("Actual polygon coordinates exceed supported finite precision")
    pairs = zip(points, (*points[1:], points[0]))
    if any(first[0] != second[0] and first[1] != second[1] for first, second in pairs):
        raise ValueError("Actual orthogonal room edges must be axis aligned")
    ox, oy = points[0]
    area = math.fsum((first[0] - ox) * (second[1] - oy) - (second[0] - ox) * (first[1] - oy)
        for first, second in zip(points, (*points[1:], points[0])))
    if not math.isfinite(area):
        raise _PrecisionGap("Actual polygon area exceeds supported finite precision")
    if area == 0:
        # Only the exceptional zero result needs exact represented-coordinate
        # arithmetic. Bounded finite int/float coordinates have bounded binary
        # ratios; this allocates no polygon or decoded source document.
        origin_x, origin_y = Fraction(ox), Fraction(oy)
        exact_area = sum((Fraction(first[0]) - origin_x) * (Fraction(second[1]) - origin_y) -
            (Fraction(second[0]) - origin_x) * (Fraction(first[1]) - origin_y)
            for first, second in zip(points, (*points[1:], points[0])))
        if exact_area != 0:
            raise _PrecisionGap("Nonzero actual polygon area is lost at world-coordinate precision")
        # Truly zero signed area remains invalid geometry for the existing
        # canonical constructor; it is not mislabeled as a precision gap.
    return len(points)


def _admission(program, building, layout, limits):
    """Reserve all original work before canonical constructors or any GEOS."""
    parent = _program_limits(limits)
    program_counts = _preflight(program, parent)
    whole_counts = _preflight([program, building, layout], parent)
    if type(program) is not dict or type(building) is not dict or type(layout) is not dict:
        raise ValueError("Production layout review requires original JSON objects")
    occurrences = [program]
    if "production_program" in building:
        occurrences.append(building["production_program"])
    source_bytes = 0
    for occurrence in occurrences:
        counts = _preflight(occurrence, parent)
        source_bytes += counts["source_utf8_bytes"]
        if source_bytes > limits.max_total_source_bytes:
            _budget("Combined top and embedded production source occurrences exceed their byte bound")
    program_schema, building_schema, embedded_schema, layout_schema, result_schema = _schemas()
    errors = _schema_issues(program, program_schema)
    errors += _schema_issues(building, embedded_schema if "production_program" in building else building_schema)
    if type(building.get("layout")) is dict:
        errors += _schema_issues(building["layout"], layout_schema)
    errors += _schema_issues(layout, result_schema)
    if errors:
        raise ValueError("Original source must satisfy its canonical schemas: " + errors[0].message)
    source = building["layout"]
    if len(source["rooms"]) > limits.max_rooms or len(layout["rooms"]) > limits.max_rooms:
        _budget("Combined actual/source room inventories exceed their room bound")
    objects = 1 + sum(len(building[key]) for key in ("zones", "equipment", "flows")) + len(source["rooms"])
    objects += len(source.get("doors", [])) + len(source.get("windows", [])) + len(source["boundary"].get("cutouts", []))
    objects += int(source.get("external_entry") is not None)
    grid = building.get("structural_grid")
    if grid is not None:
        objects += 1 + sum(len(grid.get(key, [])) for key in ("axes_x_mm", "axes_y_mm", "labels_x", "labels_y"))
    references = whole_counts["typed_references"]
    references += sum(len(room.get(key, [])) for room in source["rooms"]
        for key in ("required_adjacency", "preferred_adjacency", "forbidden_adjacency"))
    references += sum(len(zone[key]) for zone in building["zones"]
        for key in ("room_ids", "required_adjacency", "preferred_adjacency", "forbidden_adjacency"))
    references += sum(len(flow[key]) for flow in building["flows"] for key in ("from_ids", "to_ids"))
    references += 2 * len(source.get("doors", [])) + 1
    if objects > limits.max_building_objects or references > limits.max_references:
        _budget("Original building object/reference work exceeds its bound before normalization")
    edges = {room_id: _shape_precision(shape) for room_id, shape in layout["rooms"].items()}
    boundary = source["boundary"]
    _rectangle_precision({"x": 0, "y": 0, "width": boundary["width"], "height": boundary["height"]})
    for cutout in boundary.get("cutouts", []):
        _rectangle_precision(cutout)
    boundary_edges = 4 * (1 + len(boundary.get("cutouts", [])))
    vertices = sum(edges.values()) + boundary_edges
    if vertices > limits.max_vertices:
        _budget("Combined actual room and source boundary vertex budget exceeded before geometry")
    # Formation, boundary/cutout coverage, every actual overlap pair and every
    # distinct originating requirement contact are reserved together. Repeated
    # requirements reuse only fresh geometric facts from this invocation.
    counts = list(edges.values())
    work = sum(value * value for value in counts) + sum(counts) * boundary_edges + boundary_edges * boundary_edges
    work += sum(first * second for index, first in enumerate(counts) for second in counts[index + 1:])
    requested_pairs = {tuple(sorted((row["room_a"], row["room_b"]))) for row in program["requirements"]}
    work += sum(edges.get(first, 0) * edges.get(second, 0) for first, second in requested_pairs)
    if work > limits.max_pair_checks:
        _budget("Combined formation, coverage, overlap and requirement edge-pair work exceeds its bound before geometry")
    return {"json_nodes": whole_counts["json_nodes"], "json_utf8_bytes": whole_counts["json_utf8_bytes"],
        "source_occurrence_bytes": source_bytes, "building_objects": objects, "typed_references": references,
        "declared_rooms": len(source["rooms"]), "actual_rooms": len(layout["rooms"]),
        "actual_room_vertices": sum(edges.values()), "source_boundary_vertices": boundary_edges,
        "combined_geometry_vertices": vertices, "geometry_work_upper_bound": work,
        "distinct_requirement_pairs": len(requested_pairs), "declared_requirements": program_counts.get("requirements", 0),
        "evaluated_requirements": 0}


class _Findings:
    def __init__(self, maximum):
        self.maximum, self.total, self.failures = maximum, 0, 0
        self.rows, self.retained = [], []

    def append(self, row):
        self.total += 1
        if len(self.rows) < self.maximum:
            self.rows.append(row)
        if row["status"] == "FAIL":
            self.failures += 1
            if len(self.retained) < self.maximum:
                self.retained.append(row)


def _failure_row(row):
    selected = {key: row[key] for key in ("code", "status", "requirement_id", "requirement_sha256", "operation_id",
        "source_id", "source_sha256", "room_a", "room_b", "maximum_contiguous_mm", "required_contiguous_mm",
        "overlap_area_mm2") if key in row}
    selected["message"] = row.get("message", "")[:192]
    return selected


def _delivery(report, findings, limits):
    report["issues"] = findings.rows
    report["retained_failures"] = [_failure_row(row) for row in findings.retained]
    report["failure_inventory"] = {"originating_failures": findings.failures,
        "retained": len(findings.retained), "omitted": findings.failures - len(findings.retained)}
    if findings.total > limits.max_findings:
        report["inventory_status"] = "INCOMPLETE"
        report["finding_inventory"] = {"status": "INCOMPLETE", "findings": findings.total,
            "retained": len(findings.rows), "omitted": findings.total - len(findings.rows)}
    size = 0
    for chunk in json.JSONEncoder(sort_keys=True, ensure_ascii=False, allow_nan=False).iterencode(report):
        size += len(chunk.encode("utf-8"))
        if size <= limits.max_report_bytes:
            continue
        compact = {key: value for key, value in report.items() if key not in {
            "source_frames", "requirements", "program_validation", "issues", "retained_failures"}}
        compact.update(inventory_status="INCOMPLETE", source_frames={}, requirements=[], program_validation=None,
            issues=[_issue("output_budget_exceeded", "Detailed source and requirement delivery exceeds its byte bound", "UNKNOWN")],
            retained_failures=[_failure_row(row) for row in findings.retained])
        while len(json.dumps(compact, ensure_ascii=False, allow_nan=False).encode("utf-8")) > limits.max_report_bytes and len(compact["retained_failures"]) > 1:
            compact["retained_failures"].pop()
        count = len(compact["retained_failures"])
        compact["failure_inventory"].update(retained=count, omitted=findings.failures - count)
        if len(json.dumps(compact, ensure_ascii=False, allow_nan=False).encode("utf-8")) > limits.max_report_bytes:
            raise ValueError("Delivery bound cannot hold original source identity and the originating failure")
        return compact
    return report


def _stop(report, findings, limits, code, message, *, status="FAIL"):
    findings.append(_issue(code, message, status))
    report.update(status=status, binding_status="NOT_EVALUATED" if status == "UNKNOWN" else "FAIL")
    return _delivery(report, findings, limits)


def _actual_geometry(report, building, layout, program, findings):
    footprints = {room_id: shape if isinstance(shape, OrthogonalPolygon) else OrthogonalPolygon.from_rect(shape)
        for room_id, shape in layout.placements.items()}
    geometries = {room_id: shape.geometry for room_id, shape in footprints.items()}
    source_rooms = building.layout.room_by_id
    complete = set(source_rooms) == set(footprints)
    layout_failed = False
    for room_id in sorted(set(footprints) - set(source_rooms)):
        findings.append(_issue("unknown_layout_room", "Actual room identity is absent from the original building", room_a=room_id))
        layout_failed = True
    for room_id in sorted(set(source_rooms) - set(footprints)):
        findings.append(_issue("missing_room_geometry", "Original room lacks supplied actual geometry", "UNKNOWN", room_a=room_id))
    boundary = building.layout.boundary
    allowed = result_geometry.box(0, 0, boundary.width_mm, boundary.height_mm)
    for cutout in boundary.cutouts:
        allowed = allowed.difference(result_geometry.box(cutout.x, cutout.y, cutout.right, cutout.top))
    for room_id, geometry in geometries.items():
        if not allowed.covers(geometry):
            findings.append(_issue("room_outside_source_boundary", "Actual room extends outside the original allowed boundary", room_a=room_id))
            layout_failed = True
        if room_id in source_rooms:
            room = source_rooms[room_id]
            area = geometry.area / 1000000
            if not room.min_area_m2 - 1e-6 <= area <= room.max_area_m2 + 1e-6:
                findings.append(_issue("room_area_outside_source", "Actual room area differs from the original source range",
                    room_a=room_id, actual_area_m2=area))
                layout_failed = True
    overlap = {}
    ids = sorted(footprints)
    for index, first in enumerate(ids):
        for second in ids[index + 1:]:
            area = geometries[first].intersection(geometries[second]).area
            overlap[(first, second)] = area
            if area > 0:
                findings.append(_issue("room_overlap", "Actual room interiors overlap", room_a=first, room_b=second, overlap_area_mm2=area))
                layout_failed = True
    report["source_layout_status"] = "FAIL" if layout_failed else "PASS" if complete else "UNKNOWN"
    contacts, states = {}, []
    sources = {source["id"]: source for source in program["sources"]}
    for requirement in program["requirements"]:
        first, second = requirement["room_a"], requirement["room_b"]
        source = sources[requirement["source_id"]]
        row = {key: requirement[key] for key in ("operation_id", "source_id", "kind", "room_a", "room_b", "reason", "excerpt", "basis")}
        row.update(requirement_id=requirement["id"], requirement_sha256=_sha(requirement),
            source_sha256=source["sha256"], usage_rights_state=source["usage_rights"]["state"],
            status="UNKNOWN", shared_segments_mm=[], maximum_contiguous_mm=None, overlap_area_mm2=None,
            required_contiguous_mm=building.layout.door_width_mm if requirement["kind"] == "required_room_adjacency" else None)
        if first not in footprints or second not in footprints:
            findings.append(_issue("missing_requirement_room_geometry", "A named requirement room has no supplied actual footprint", "UNKNOWN",
                requirement_id=requirement["id"], operation_id=requirement["operation_id"], source_id=source["id"],
                source_sha256=source["sha256"], room_a=first, room_b=second))
        else:
            pair = tuple(sorted((first, second)))
            if pair not in contacts:
                contacts[pair] = footprints[pair[0]].shared_edges(footprints[pair[1]])
            spans = contacts[pair]
            maximum = max((abs(end[0] - start[0]) + abs(end[1] - start[1]) for start, end in spans), default=0)
            area = overlap[pair]
            row.update(shared_segments_mm=[[list(start), list(end)] for start, end in spans],
                maximum_contiguous_mm=maximum, overlap_area_mm2=area, status="PASS")
            code = "room_overlap" if area > 0 else "required_room_adjacency_failed" if requirement["kind"] == "required_room_adjacency" and maximum < building.layout.door_width_mm else \
                "forbidden_room_adjacency_failed" if requirement["kind"] == "forbidden_room_adjacency" and maximum > 0 else None
            if code is not None:
                row["status"] = "FAIL"
                findings.append(_issue(code, "Actual room geometry conflicts with the originating production requirement",
                    **{key: row[key] for key in ("requirement_id", "requirement_sha256", "operation_id", "source_id", "source_sha256",
                        "room_a", "room_b", "maximum_contiguous_mm", "required_contiguous_mm", "overlap_area_mm2")}))
            report["counts"]["evaluated_requirements"] += 1
        states.append(row["status"])
        report["requirements"].append(row)
    if not states:
        findings.append(_issue("no_executable_requirements", "No actual production adjacency requirement was supplied", "UNKNOWN"))
    report["geometry_status"] = "FAIL" if layout_failed or "FAIL" in states else "PASS" if states and all(state == "PASS" for state in states) and complete else "UNKNOWN"


def validate_production_program_layout(program_raw, *, building_raw, layout_raw, limits=ProductionLayoutLimits()):
    """Return detached local evidence from three original supplied JSON frames."""
    report = _base()
    if not _valid_limits(limits):
        report.update(status="FAIL", binding_status="FAIL",
            issues=[_issue("invalid_limits", "Limits require bounded actual integers, tighten-only defaults and a report cap of at least 4096 bytes")])
        return report
    findings = _Findings(limits.max_findings)
    try:
        report["counts"] = _admission(program_raw, building_raw, layout_raw, limits)
    except _InputFailure as exc:
        return _stop(report, findings, limits, exc.issue.code, exc.issue.message,
            status="UNKNOWN" if exc.issue.code == "input_budget_exceeded" else "FAIL")
    except _PrecisionGap as exc:
        return _stop(report, findings, limits, "unrepresentable_actual_geometry", str(exc), status="UNKNOWN")
    except (ValueError, TypeError, KeyError, OverflowError, RecursionError) as exc:
        return _stop(report, findings, limits, "invalid_original_source", str(exc)[:512])
    # The complete bounded raw frames, including an embedded program, remain
    # separate from constructor-normalized models and the program base binding.
    program, building_source, layout_source = copy.deepcopy((program_raw, building_raw, layout_raw))
    base_source = {key: value for key, value in building_source.items() if key != "production_program"}
    report["source"] = {"building_sha256": _sha(building_source), "layout_sha256": _sha(layout_source),
        "production_program_sha256": _sha(program), "program_base_building_sha256": _sha(base_source)}
    report["source_frames"] = {"building": building_source, "layout": layout_source, "production_program": program}
    if "production_program" in building_source and _json_bytes(building_source["production_program"]) != _json_bytes(program):
        return _stop(report, findings, limits, "embedded_production_program_mismatch",
            "Whole original embedded and top production programs differ in exact fields or scalar types")
    if program["building_sha256"] != report["source"]["program_base_building_sha256"]:
        return _stop(report, findings, limits, "stale_building_binding", "Program differs from the original canonical base building hash")
    try:
        building = BuildingIR.from_mapping(base_source)
        if _json_bytes(building.to_dict()) != _json_bytes(base_source):
            return _stop(report, findings, limits, "original_source_identity_changed",
                "Canonical source scalar types or fields change during constructor normalization")
        validation = validate_production_program(program, building=building, limits=_program_limits(limits))
        report["program_validation"] = validation.to_dict()
        report["binding_status"] = validation.binding_status
        report["provenance_status"] = validation.evidence_status
        for issue in validation.issues:
            findings.append(issue.to_dict())
        if validation.schema_status != "PASS" or validation.binding_status != "PASS":
            report["status"] = "FAIL"
            return _delivery(report, findings, limits)
        if "production_program" in building_source:
            building = replace(building, production_program=validation.program)
            if _json_bytes(building.to_dict()) != _json_bytes(building_source):
                return _stop(report, findings, limits, "original_source_identity_changed",
                    "Whole original embedded source identity changes during admitted model construction")
        layout = result_geometry.parse_layout_result(layout_source)
        _actual_geometry(report, building, layout, program, findings)
    except (ValueError, TypeError, KeyError, OverflowError, AttributeError, RecursionError) as exc:
        findings.append(_issue("invalid_actual_geometry", str(exc)[:512]))
        report["geometry_status"] = "FAIL"
    states = (report["binding_status"], report["provenance_status"], report["geometry_status"])
    report["status"] = "FAIL" if "FAIL" in states or findings.failures else "PASS" if all(state == "PASS" for state in states) else "UNKNOWN"
    report["inventory_status"] = "COMPLETE"
    return _delivery(report, findings, limits)

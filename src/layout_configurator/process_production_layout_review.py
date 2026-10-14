"""Opted fresh production adjacency evidence on one original process revision.

The closed version 0.2 input and its nine original component identities stay
unchanged. All consumers reserve their combined work before constructing any
canonical model or geometry, and share one final process delivery bound.
"""

import copy

from . import process_installation_review as _paths
from . import process_review as _common
from . import production_layout_review as _production
from .process_installation_review import ProcessInstallationPathLimits
from .process_review import ProcessReviewLimits
from .production_layout_review import ProductionLayoutLimits
from .production_program import _InputFailure, _json_bytes, _preflight
from .transfer_review import read_review_source


class _EvaluationStop(Exception):
    def __init__(self, code, message, *, status="UNKNOWN", evidence=None):
        self.code, self.message, self.status, self.evidence = code, message, status, evidence


def _core_geometry_work(raw):
    """The unchanged transfer core score, counted without its constructor."""
    source = raw["building"]["layout"]
    edges = [len(row["vertices"]) if row.get("kind") == "orthogonal" else 4
        for row in raw["layout"]["rooms"].values()]
    total, maximum = sum(edges), max(edges, default=0)
    equipment = len(raw["equipment"])
    openings = (len(source.get("doors", [])) + len(source.get("windows", [])) +
        int(source.get("external_entry") is not None))
    work = sum(max(0, len(row["points"]) - 1) for row in raw["flow_routes"]["routes"])
    work *= total + len(source["rooms"]) + openings + equipment
    work += total ** 2 + 4 * equipment * total + 16 * equipment ** 2
    work += (openings + len(source["rooms"]) ** 2) * maximum ** 2
    work += (4 + 4 * len(source["boundary"].get("cutouts", []))) * total
    return work


def _source_identity(raw):
    base = {key: value for key, value in raw["building"].items() if key != "production_program"}
    return {"building_sha256": _common._sha(raw["building"]),
        "layout_sha256": _common._sha(raw["layout"]),
        "production_program_sha256": _common._sha(raw["production_program"]),
        "program_base_building_sha256": _common._sha(base)}


def _binding_guard(raw, source):
    building, program = raw["building"], raw["production_program"]
    if "production_program" in building and _json_bytes(building["production_program"]) != _json_bytes(program):
        raise _EvaluationStop("embedded_production_program_mismatch",
            "Whole original embedded and top production programs differ in exact fields or scalar types", status="FAIL")
    if program["building_sha256"] != source["program_base_building_sha256"]:
        raise _EvaluationStop("stale_building_binding",
            "Program differs from the original canonical base building hash", status="FAIL")


def _reserve_consumers(raw, limits, path_limits, production_limits, work, production_counts):
    """Extend the existing ledger before snapshots, nested decoding or GEOS."""
    counts, points, pairs = _common._raw_preflight(raw, limits)
    core = _core_geometry_work(raw)
    common_extra = _common._geometry_work(raw, counts, points, pairs, limits)
    production_work = production_counts["geometry_work_upper_bound"]
    combined_work = core + common_extra + work["path_geometry_upper_bound"] + production_work
    if combined_work > limits.max_geometry_checks:
        _common._fail_budget("Combined core, common, installation path and production geometry work exceeds its bound before constructors")
    combined_vertices = work["combined_source_geometry_vertices"] + production_counts["combined_geometry_vertices"]
    if combined_vertices > limits.max_scenario_vertices:
        _common._fail_budget("Combined path and production source vertices exceed their shared bound before constructors")
    # The standalone consumer detaches its complete original three-frame
    # tuple. An embedded program also introduces one contract.to_dict decode
    # when its whole admitted BuildingIR identity is checked. Repeated common
    # and path library decodes retain their existing ledger charges.
    embedded_nodes, embedded_bytes, embedded_json_bytes = 0, 0, 0
    if "production_program" in raw["building"]:
        embedded = _preflight(raw["production_program"], _production._program_limits(production_limits))
        embedded_nodes, embedded_bytes = embedded["json_nodes"], embedded["source_utf8_bytes"]
        embedded_json_bytes = embedded["json_utf8_bytes"]
    snapshot_nodes = production_counts["json_nodes"]
    snapshot_bytes = production_counts["source_occurrence_bytes"]
    node_work = work["node_work_upper_bound"] + snapshot_nodes + embedded_nodes
    source_work = (work["source_byte_work_upper_bound"] + production_counts["json_utf8_bytes"] +
        embedded_json_bytes)
    if node_work > path_limits.max_node_work:
        _common._fail_budget("Combined existing consumers, original production snapshot and embedded decode node work exceeds its bound before constructors")
    if source_work > path_limits.max_source_byte_work:
        _common._fail_budget("Combined existing consumers, original production snapshot and embedded decode source-byte work exceeds its bound before constructors")
    work.update(common_core_geometry_upper_bound=core, common_additional_geometry_upper_bound=common_extra,
        production_layout_geometry_upper_bound=production_work, combined_geometry_upper_bound=combined_work,
        production_layout_source_vertices=production_counts["combined_geometry_vertices"],
        combined_consumer_source_vertices=combined_vertices,
        production_layout_snapshot_nodes=snapshot_nodes,
        production_layout_snapshot_json_bytes=production_counts["json_utf8_bytes"],
        production_layout_snapshot_source_bytes=snapshot_bytes,
        production_layout_embedded_decode_nodes=embedded_nodes,
        production_layout_embedded_decode_json_bytes=embedded_json_bytes,
        production_layout_embedded_decode_source_bytes=embedded_bytes,
        production_layout_embedded_decode_passes=int("production_program" in raw["building"]),
        node_work_upper_bound=node_work, source_byte_work_upper_bound=source_work)


def _verify_source(evidence, source, identity):
    if evidence["source"] != source:
        raise ValueError("Fresh production evidence must retain the exact original three-frame and base identities")
    components = identity["component_sha256"]
    if any(evidence["source"][name + "_sha256"] != components[name]
           for name in ("building", "layout", "production_program")):
        raise ValueError("Fresh production source hashes must match the original process component identities")


def _unsupported_evidence(evidence):
    """Refuse an unsupported canonical roundtrip without rebinding its source."""
    if not any(row["code"] == "original_source_identity_changed" for row in evidence["issues"]):
        return False
    evidence.update(status="UNKNOWN", binding_status="NOT_EVALUATED", geometry_status="NOT_EVALUATED",
        inventory_status="NOT_EVALUATED", retained_failures=[],
        failure_inventory={"originating_failures": 0, "retained": 0, "omitted": 0})
    for row in evidence["issues"]:
        if row["code"] == "original_source_identity_changed":
            row.update(code="unsupported_original_source_identity", status="UNKNOWN")
    return True


def _stopped_evidence(raw, source, counts, stop, *, snapshot_admitted=False):
    if stop.evidence is not None:
        return stop.evidence
    evidence = _production._base()
    frames = {}
    if snapshot_admitted:
        frames = copy.deepcopy({"building": raw["building"], "layout": raw["layout"],
            "production_program": raw["production_program"]})
    evidence.update(source=source, counts=counts, source_frames=frames,
        status=stop.status, binding_status="FAIL" if stop.status == "FAIL" else "NOT_EVALUATED",
        issues=[_production._issue(stop.code, stop.message, stop.status)])
    if stop.status == "FAIL":
        evidence["retained_failures"] = [_production._failure_row(evidence["issues"][0])]
        evidence["failure_inventory"] = {"originating_failures": 1, "retained": 1, "omitted": 0}
    return evidence


def _production_failures(evidence, identity, maximum):
    unique = {}
    for row in _paths._actual_failures(evidence):
        selected = _production._failure_row(row)
        unique.setdefault(_common._sha(selected), selected)
    # An actual originating requirement remains reviewable beside earlier
    # generic source, pressure or path findings even under a one-finding cap.
    ordered = sorted(unique.values(), key=lambda row: "requirement_sha256" not in row)
    retained = [{"component": "production_layout", "identity": identity, "failure": row}
        for row in ordered[:maximum]]
    total = max(len(unique), evidence.get("failure_inventory", {}).get("originating_failures", 0))
    return retained, total


def _delivery(report, evidence, limits):
    rows, total = _production_failures(evidence, report["identity"], limits.max_findings)
    summary = {"production_layout": {"status": evidence["status"], "inventory_status": "INCOMPLETE",
        "evidence": {key: evidence[key] for key in ("status", "geometry_status", "binding_status", "provenance_status")}}}
    summary["production_layout"]["evidence"]["inventory_status"] = "INCOMPLETE"
    return _paths._bounded_output(report, limits, additional_failures=rows, additional_failure_total=total,
        additional_summary=summary, additional_delivery_incomplete=evidence["inventory_status"] == "INCOMPLETE")


def load_process_review_with_production_layout(path, *, include_windows=False, limits=ProcessReviewLimits(),
        path_limits=ProcessInstallationPathLimits(), production_limits=ProductionLayoutLimits()):
    """Read one unchanged version 0.2 source and attach scoped actual contacts."""
    if (not _common._limits_valid(limits) or not _paths._valid_path_limits(path_limits) or
            not _production._valid_limits(production_limits) or type(include_windows) is not bool):
        raise ValueError("Production process review requires bounded actual limits and a boolean window control")
    try:
        original, raw = read_review_source(path, limits=_common._parent_limits(limits))
    except _InputFailure as exc:
        raise ValueError(exc.issue.message) from exc
    error = next(_paths._validator().iter_errors(raw), None)
    if error is not None:
        raise ValueError("Production process review requires the unchanged closed version 0.2 schema: " + error.message[:512])
    _common._check_common_schema(raw)
    identity, source = _common._identity(raw), _source_identity(raw)
    work, production_counts, evidence = {}, {}, None
    snapshot_admitted = False

    def preflight(frame):
        nonlocal evidence, snapshot_admitted
        _binding_guard(frame, source)
        work.update(_paths._path_preflight(frame, limits, path_limits))
        production_counts.update(_production._admission(frame["production_program"], frame["building"],
            frame["layout"], production_limits))
        _reserve_consumers(frame, limits, path_limits, production_limits, work, production_counts)
        snapshot_admitted = True
        _common._exact_frames_and_requests(frame)
        precision = _common._unrepresentable_rectangle(frame)
        if precision is not None:
            raise _EvaluationStop(precision["code"], precision["message"])
        evidence = _production.validate_production_program_layout(frame["production_program"],
            building_raw=frame["building"], layout_raw=frame["layout"], limits=production_limits)
        _verify_source(evidence, source, identity)
        if _unsupported_evidence(evidence):
            raise _EvaluationStop("unsupported_original_source_identity",
                "Original source fields or scalar types are outside the supported exact canonical roundtrip", evidence=evidence)
        return work["path_geometry_upper_bound"] + production_counts["geometry_work_upper_bound"]

    try:
        report, admitted = _common._evaluate_process_source(original, raw, include_windows=include_windows,
            limits=limits, schema_check=_common._check_common_schema, additional_preflight=preflight)
    except (_production._PrecisionGap, _paths._PrecisionGap) as exc:
        stop = _EvaluationStop("unsupported_source_geometry_precision", str(exc))
        evidence = _stopped_evidence(raw, source, production_counts, stop, snapshot_admitted=snapshot_admitted)
        report, admitted = _common._base(original, identity), None
        report.update(inventory_status="NOT_EVALUATED", issues=[{
            "code": stop.code, "status": stop.status, "message": stop.message}])
    except _EvaluationStop as stop:
        evidence = _stopped_evidence(raw, source, production_counts, stop, snapshot_admitted=snapshot_admitted)
        report, admitted = _common._base(original, identity), None
        report.update(status=stop.status, inventory_status="NOT_EVALUATED", issues=[{
            "code": stop.code, "status": stop.status, "message": stop.message}])
    if admitted is not None:
        _paths._attach_paths(report, admitted, raw, limits=limits, path_limits=path_limits, work=work)
    elif evidence is None:
        issue = report["issues"][0]
        evidence = _stopped_evidence(raw, source, production_counts,
            _EvaluationStop(issue["code"], issue["message"], status=issue["status"]),
            snapshot_admitted=snapshot_admitted)
    report["identity"] = identity
    _verify_source(evidence, source, identity)
    report["additional_reports"] = {"production_layout": {
        "identity": identity, "status": evidence["status"], "evidence": evidence}}
    if evidence["status"] == "FAIL":
        report["status"] = "FAIL"
    report["issues"].append({"code": "scoped_production_layout_" + evidence["status"].lower(),
        "status": evidence["status"], "component": "production_layout",
        "message": "Fresh local actual room contacts are attached; full process and engineering acceptance remain unapproved"})
    return _delivery(report, evidence, limits)

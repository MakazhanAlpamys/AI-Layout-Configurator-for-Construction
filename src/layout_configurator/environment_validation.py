"""Source-bound region and pressure checks on current exact planar geometry.

Resolved declarations are distinct from supplied observations. No pressure
network is inferred from a functional parent hierarchy. HVAC operation,
physical action execution and authenticated measurements remain UNKNOWN.
"""

from dataclasses import dataclass
from fractions import Fraction
import json

from shapely.ops import unary_union

from .environment_program import (EnvironmentIssue, EnvironmentProgramLimits, _environment_preflight,
    _limits_valid, _network, _parent_limits, validate_environment_program)
from .orthogonal import OrthogonalPolygon
from .orthogonal_projection import build_orthogonal_wall_plan
from .production_program import ProductionProgramContract, _InputFailure, _preflight
from .transfer_program import TransferProgramContract, _contract_raw, _hash, _model_preflight, _raw_limits
from .transfer_validation import _request_counts, validate_transfer_routes


@dataclass(frozen=True)
class EnvironmentLayoutValidation:
    status: str
    geometry_status: str
    boundary_status: str
    connectivity_status: str
    pressure_declared_status: str
    pressure_resolved_status: str
    pressure_design_status: str
    pressure_measured_status: str
    issues: tuple[EnvironmentIssue, ...]
    _evidence_json: str

    def to_dict(self):
        return {"schema_version": 1, "scope": "supplied_environment_on_current_planar_geometry", "status": self.status,
                "geometry_status": self.geometry_status, "boundary_status": self.boundary_status,
                "connectivity_status": self.connectivity_status, "pressure_declared_status": self.pressure_declared_status,
                "pressure_resolved_status": self.pressure_resolved_status, "pressure_design_status": self.pressure_design_status,
                "pressure_measured_status": self.pressure_measured_status, "action_execution": "UNKNOWN",
                "hvac_validation": "UNKNOWN", "measurement_authentication": "UNKNOWN", "authenticated_approval": "UNKNOWN",
                "engineering_acceptance": "PENDING", "full_facility_validation": {"status": "UNKNOWN", "ok": False},
                "proven_building_infeasible": False, "issues": [issue.to_dict() for issue in self.issues],
                **json.loads(self._evidence_json)}


def validate_environment_layout(building, layout, routing, raw, *, production_program, transfer_program,
                                equipment=None, include_windows=False, limits=EnvironmentProgramLimits()):
    """Rebuild regions and all portals, preserving source and geometry failures.

    Contract consistency uses the complete exact supplied graph. Resolved
    consistency uses only independently established current boundary transfers;
    an incomplete resolved graph can never establish complete connectivity.
    """
    issues = []
    evidence = {"source": {}, "counts": {}, "contract_validation": None, "source_predicates": None,
                "declared_network": None, "resolved_network": None, "regions": [], "connections": [],
                "policy": {"iso_to_gmp_inference": False, "functional_parent_pressure_inference": False,
                    "pressure_value_scope": "Numerical supplied claims only; not authenticated or built performance",
                    "applicability_scope": "Supplied conditions retained; no legal threshold is inferred"}}

    def issue(code, message, status="UNKNOWN", path="$", **ids):
        issues.append(EnvironmentIssue(code, path, message, status, **ids))

    def result(geometry="NOT_EVALUATED", boundary="NOT_EVALUATED", connectivity="NOT_EVALUATED",
               declared="NOT_EVALUATED", resolved="NOT_EVALUATED", design="NOT_EVALUATED", measured="NOT_EVALUATED"):
        states = (geometry, boundary, connectivity, declared, resolved, design, measured)
        status = "FAIL" if "FAIL" in states or any(row.status == "FAIL" for row in issues) else "UNKNOWN"
        return EnvironmentLayoutValidation(status, geometry, boundary, connectivity, declared, resolved, design, measured,
            tuple(issues), json.dumps(evidence, sort_keys=True, ensure_ascii=False, allow_nan=False, separators=(",", ":")))

    if not _limits_valid(limits) or type(include_windows) is not bool:
        issue("invalid_options", "Limits require positive actual integers and include_windows an actual boolean", "FAIL")
        return result()
    try:
        raw, counts = _environment_preflight(raw, limits)
        evidence["counts"] = counts
        parent_limits = _parent_limits(limits)
        request_counts = _request_counts(building, layout, routing, equipment, parent_limits)
        room_edges = sum(len(row.vertices) if isinstance(row, OrthogonalPolygon) else 4 for row in layout.placements.values())
        work = request_counts["geometry_work_score"] + counts.get("zone_vertices", 0) ** 2
        work += counts.get("zone_vertices", 0) * (room_edges + 4 * request_counts["equipment"])
        work += counts.get("pressure_edges", 0) * (counts.get("zone_vertices", 0) + room_edges + request_counts["openings"])
        if work > limits.max_geometry_checks:
            raise _InputFailure("input_budget_exceeded", "$.geometry", "Aggregate environment/source geometry admission score exceeded")
        evidence["counts"].update(request_counts, aggregate_geometry_work_score=work)
        production = _contract_raw(production_program, ProductionProgramContract, parent_limits)
        transfer = _contract_raw(transfer_program, TransferProgramContract, parent_limits)
        _preflight(production, _raw_limits(limits))
        _preflight(transfer, _raw_limits(limits))
        _model_preflight((raw, building, layout, routing, equipment, production, transfer), limits)
        frames = {"environment": raw, "building": building.to_dict(), "layout": layout.to_dict(),
                  "routing": routing.to_dict(), "equipment": equipment.to_dict(building) if equipment else {"equipment": []},
                  "production_program": production, "transfer_program": transfer}
        combined = _preflight(frames, _raw_limits(limits, byte_limit=limits.max_input_bytes))
        evidence["counts"].update(combined_json_nodes=combined["json_nodes"], combined_json_utf8_bytes=combined["json_utf8_bytes"])
        evidence["source"] = {name + "_sha256": _hash(value) for name, value in frames.items()}
        contract = validate_environment_program(raw, building=building, production_program=production,
                                                transfer_program=transfer, limits=limits)
        evidence["contract_validation"] = contract.to_dict()
        issues.extend(contract.issues)
        evidence["declared_network"] = contract.to_dict()["declared_network"]
        if contract.schema_status != "PASS" or contract.binding_status != "PASS":
            return result(declared=contract.pressure_declared_status)
        if raw["layout_sha256"] != evidence["source"]["layout_sha256"]:
            issue("stale_layout_binding", "Environment sidecar differs from the exact current canonical layout", "FAIL", "$.layout_sha256")
            return result(declared=contract.pressure_declared_status)
    except _InputFailure as exc:
        issue(exc.issue.code, exc.issue.message, "UNKNOWN" if exc.issue.code == "input_budget_exceeded" else "FAIL", exc.issue.path)
        return result()
    except (ValueError, TypeError, AttributeError, OverflowError, RecursionError, KeyError) as exc:
        issue("invalid_request", str(exc)[:256], "FAIL")
        return result()

    raw = contract.program.to_dict()
    window_options = {"include_windows": True} if include_windows else {}
    fresh = validate_transfer_routes(building, layout, routing, transfer, production_program=production,
                                     equipment=equipment, limits=parent_limits, **window_options)
    evidence["source_predicates"] = fresh.to_dict()
    geometry = fresh.geometry_status
    if fresh.status == "FAIL":
        issue("source_predicates_failed", "Fresh original room, equipment, route or transfer predicates failed", "FAIL")
    elif fresh.status != "PASS":
        issue("source_predicates_unknown", "Fresh original predicates have unsupported or incomplete evidence")
    try:
        footprints = {identifier: (row if isinstance(row, OrthogonalPolygon) else OrthogonalPolygon.from_rect(row))
                      for identifier, row in layout.placements.items()}
        rooms = {identifier: row.geometry for identifier, row in footprints.items()}
    except (ValueError, TypeError, AttributeError, OverflowError) as exc:
        issue("invalid_actual_footprint", str(exc)[:256], "FAIL")
        return result("FAIL", declared=contract.pressure_declared_status)
    regions, boundary = {}, "PASS"
    placed_equipment = {row.equipment_id: row for row in (equipment.placements if equipment else ())}
    operation_rooms = {row["id"]: row["room_id"] for row in production["operations"]}
    for name, namespace in (("clean_zones", "clean_zone"), ("ventilation_zones", "ventilation_zone")):
        for row in raw[name]:
            ids = {"zone_id": row["id"], "source_id": row["source_id"]}
            try:
                region = OrthogonalPolygon.from_mapping(row["boundary"]).geometry
                hosts = unary_union([rooms[identifier] for identifier in row["host_room_ids"]])
                valid = hosts.covers(region)
                if not valid:
                    boundary = "FAIL"
                    issue("environment_boundary_outside_hosts", "Complete declared region is outside its actual host-room union", "FAIL", **ids)
                regions[(namespace, row["id"])] = region
                record = {"namespace": namespace, "id": row["id"], "host_room_ids": row["host_room_ids"],
                          "status": "PASS" if valid else "FAIL", "area_mm2": region.area,
                          "equipment_memberships": [], "operation_memberships": []}
                for equipment_id in row["equipment_ids"]:
                    placement = placed_equipment.get(equipment_id)
                    contained = placement is not None and region.covers(OrthogonalPolygon.from_rect(placement.rect).geometry)
                    record["equipment_memberships"].append({"equipment_id": equipment_id, "status": "PASS" if contained else "UNKNOWN"})
                    if not contained:
                        issue("environment_equipment_interface_unknown", "Equipment has no complete contained actual body or independently modeled clean-boundary interface", zone_id=row["id"], source_id=row["source_id"])
                for operation_id in row["operation_ids"]:
                    contained = region.covers(rooms[operation_rooms[operation_id]])
                    record["operation_memberships"].append({"operation_id": operation_id, "status": "PASS" if contained else "UNKNOWN"})
                    if not contained:
                        issue("environment_operation_location_unknown", "Local operation assignment has no source-bound interaction point proving its position", operation_id=operation_id, **ids)
                evidence["regions"].append(record)
            except (ValueError, TypeError, AttributeError, OverflowError, KeyError) as exc:
                boundary = "FAIL"
                issue("invalid_environment_region", str(exc)[:256], "FAIL", **ids)
    openings = None
    try:
        openings = build_orthogonal_wall_plan(building.layout, layout, **window_options).openings
    except (ValueError, TypeError, AttributeError, OverflowError, KeyError) as exc:
        issue("current_portals_unavailable", str(exc)[:256])
    transfers = {row["id"]: row for row in transfer["transfers"]}
    nodes = {row["id"]: row for row in raw["pressure_nodes"]}
    spaces = {("room", identifier): geometry for identifier, geometry in rooms.items()}
    spaces.update(regions)
    connectivity, resolved_ids = "PASS" if raw["pressure_edges"] else "UNKNOWN", []
    for edge in sorted(raw["pressure_edges"], key=lambda row: row["id"]):
        declaration = transfers[edge["transfer_id"]]
        ids = {"edge_id": edge["id"], "transfer_id": declaration["id"], "opening_id": declaration["opening_id"],
               "scenario_id": edge["scenario_id"], "source_id": edge["source_id"]}
        record = {**ids, "status": "UNKNOWN"}
        evidence["connections"].append(record)
        if openings is None:
            if connectivity != "FAIL": connectivity = "UNKNOWN"
            continue
        matches = [opening for opening in openings if not opening.external and opening.id == declaration["opening_id"]]
        if len(matches) != 1:
            if connectivity != "FAIL": connectivity = "UNKNOWN"
            issue("ambiguous_current_transfer", "Typed transfer has no unique fresh current source opening", **ids)
            continue
        opening = matches[0]
        high = nodes[edge["high_node_id"]]["space"]
        low = nodes[edge["low_node_id"]]["space"]
        first, second = spaces.get((high["namespace"], high["id"])), spaces.get((low["namespace"], low["id"]))
        if first is None or second is None:
            if connectivity != "FAIL": connectivity = "UNKNOWN"
            issue("pressure_region_unavailable", "One pressure node has no valid actual region", **ids)
            continue
        if not first.boundary.intersection(second.boundary).covers(opening.centerline):
            connectivity = "FAIL"
            record["status"] = "FAIL"
            issue("transfer_not_on_environment_boundary", "Complete actual source opening is not on both declared pressure-space boundaries", "FAIL", **ids)
            continue
        if first.intersection(second).area > 0:
            if connectivity != "FAIL": connectivity = "UNKNOWN"
            issue("overlapping_pressure_spaces", "Overlapping declared pressure regions do not establish distinct connected air spaces", **ids)
            continue
        record["status"] = "PASS"
        record["opening_segment"] = [list(point) for point in opening.centerline.coords]
        resolved_ids.append(edge["id"])
    resolved = _network(raw, set(resolved_ids))
    resolved.update(scope="Exact supplied inequalities on independently resolved current planar boundaries only; not airflow",
                    resolved_edge_ids=resolved_ids, incomplete=len(resolved_ids) != len(raw["pressure_edges"]))
    if resolved["status"] != "FAIL" and resolved["incomplete"]:
        resolved["status"] = "UNKNOWN"
    evidence["resolved_network"] = resolved
    for witness in resolved["cycle_witness"]:
        issue("contradictory_resolved_pressure_cycle", "Resolved current planar declaration constraints contain a contradictory cycle", "FAIL",
              edge_id=witness["edge_id"], scenario_id=witness["scenario_id"], source_id=witness["source_id"])

    def values_status(kind):
        status = "PASS" if raw["pressure_edges"] else "UNKNOWN"
        for edge in raw["pressure_edges"]:
            high, low = nodes[edge["high_node_id"]], nodes[edge["low_node_id"]]
            values = [next((row[kind] for row in node["values"] if row["scenario_id"] == edge["scenario_id"]), None)
                      for node in (high, low)]
            ids = {"edge_id": edge["id"], "scenario_id": edge["scenario_id"], "source_id": edge["source_id"]}
            if high["datum_id"] != low["datum_id"] or any(row is None or row["status"] != "KNOWN" for row in values):
                if status != "FAIL": status = "UNKNOWN"
                issue("unknown_" + kind + "_pressure", "Comparable supplied values on a common declared datum are missing", **ids)
                continue
            delta = Fraction(str(values[0]["value"])) - Fraction(str(values[1]["value"]))
            if delta < Fraction(str(edge["minimum_delta_pa"])) or edge["maximum_delta_pa"] is not None and delta > Fraction(str(edge["maximum_delta_pa"])):
                status = "FAIL"
                issue("declared_" + kind + "_pressure_violation", "Supplied numerical pressure values violate the specified differential; built performance is not authenticated", "FAIL", **ids)
        return status

    design, measured = values_status("design"), values_status("measured")
    return result(geometry, boundary, connectivity, contract.pressure_declared_status, resolved["status"], design, measured)

"""Independent source-bound permission checks on actual route crossings.

Fresh room, equipment, corridor and opening checks remain mandatory. Window
facades stay closed; source permissions do not certify airlock or pass-box
mechanisms, engineering approval, pressure control or complete facility QA.
"""

from dataclasses import dataclass
from types import SimpleNamespace
import json
import math

from .equipment import EquipmentLayoutResult
from .flows import FlowRoute, FlowRoutingResult
from .models import LayoutResult, Rect
from .orthogonal import OrthogonalPolygon
from .orthogonal_flows import OrthogonalFlowBudget, _trace_rooms, validate_orthogonal_flow_routes
from .orthogonal_projection import _offset_opening, build_orthogonal_wall_plan
from .production_program import ProductionProgramContract, _InputFailure, _preflight
from .transfer_program import (TransferIssue, TransferProgramContract, TransferProgramLimits,
                               _building_counts, _contract_raw, _hash, _limits_valid,
                               _model_preflight, _raw_limits, validate_transfer_program)


@dataclass(frozen=True)
class TransferRouteValidation:
    status: str
    geometry_status: str
    permission_status: str
    mechanism_status: str
    issues: tuple[TransferIssue, ...]
    _evidence_json: str

    def to_dict(self):
        return {"schema_version": 1, "scope": "source_bound_actual_route_transfer_permissions",
                "status": self.status, "ok": self.status == "PASS", "geometry_status": self.geometry_status,
                "permission_status": self.permission_status, "mechanism_status": self.mechanism_status,
                "engineering_acceptance": "PENDING", "authenticated_approval": "UNKNOWN",
                "full_facility_validation": {"status": "UNKNOWN", "ok": False},
                "proven_building_infeasible": False,
                "issues": [issue.to_dict() for issue in self.issues], **json.loads(self._evidence_json)}


def _finite(value):
    return type(value) in (int, float) and (type(value) is not int or value.bit_length() <= 1024) and math.isfinite(value)


def _request_counts(building, layout, routing, equipment, limits):
    """Count bounded geometry work before serialization or any GEOS call."""
    counts = _building_counts(building, limits)
    if not isinstance(layout, LayoutResult) or type(layout.placements) is not dict or len(layout.placements) > limits.max_rooms:
        raise ValueError("Layout requires bounded canonical LayoutResult rooms")
    if not isinstance(routing, FlowRoutingResult) or type(routing.routes) not in (list, tuple):
        raise ValueError("Routing requires typed FlowRoutingResult")
    if len(routing.routes) > limits.max_routes:
        raise _InputFailure("input_budget_exceeded", "$.routing", "Actual route count budget exceeded")
    total_points, total_segments, room_edges, maximum_room_edges = 0, 0, 0, 0
    for route in routing.routes:
        if not isinstance(route, FlowRoute) or type(route.points) not in (tuple, list):
            raise ValueError("Actual routes require typed FlowRoute coordinate lists")
        if len(route.points) > limits.max_route_points:
            raise _InputFailure("input_budget_exceeded", "$.routing", "Per-route point budget exceeded")
        total_points += len(route.points)
        total_segments += max(0, len(route.points) - 1)
        if total_points > limits.max_total_points:
            raise _InputFailure("input_budget_exceeded", "$.routing", "Total actual point budget exceeded")
        for point in route.points:
            if type(point) not in (tuple, list) or len(point) != 2 or not all(_finite(value) for value in point):
                raise ValueError("Actual coordinates require finite nonboolean numeric pairs")
        if not _finite(route.minimum_clear_width_mm) or route.minimum_clear_width_mm <= 0:
            raise ValueError("Actual route width requires a positive finite nonboolean number")
        if not all(type(value) is str and value.strip() for value in
                   (route.flow_id, route.from_id, route.to_id, route.from_room_id, route.to_room_id)):
            raise ValueError("Actual routes require explicit source endpoint and host IDs")
        if type(route.room_path) not in (tuple, list) or len(route.room_path) > limits.max_total_points:
            raise ValueError("Saved room path is a bounded list of source IDs, never permission authority")
    for footprint in layout.placements.values():
        if isinstance(footprint, Rect):
            if not all(_finite(value) for value in (footprint.x, footprint.y, footprint.width, footprint.height)) or footprint.width <= 0 or footprint.height <= 0:
                raise ValueError("Actual rectangular geometry requires finite nonboolean coordinates and positive dimensions")
            edge_count = 4
        elif isinstance(footprint, OrthogonalPolygon):
            edge_count = len(footprint.vertices)
        else:
            raise ValueError("Actual room footprints require Rect or OrthogonalPolygon")
        room_edges += edge_count
        maximum_room_edges = max(maximum_room_edges, edge_count)
    placements = () if equipment is None else equipment.placements if isinstance(equipment, EquipmentLayoutResult) else None
    if type(placements) not in (list, tuple):
        raise ValueError("Equipment requires canonical EquipmentLayoutResult")
    if len(placements) > limits.max_equipment:
        raise _InputFailure("input_budget_exceeded", "$.equipment", "Actual equipment count budget exceeded")
    # An admission score includes source pair checks even for an empty route
    # list. It bounds allocated input interactions, not GEOS execution time.
    work = total_segments * (room_edges + counts["rooms"] + counts["openings"] + len(placements))
    work += room_edges ** 2 + 4 * len(placements) * room_edges + 16 * len(placements) ** 2
    work += (counts["openings"] + counts["rooms"] ** 2) * maximum_room_edges ** 2
    work += (4 + 4 * len(building.layout.boundary.cutouts)) * room_edges
    if work > limits.max_geometry_checks:
        raise _InputFailure("input_budget_exceeded", "$.routing", "Conservative route geometry work budget exceeded")
    counts.update(routes=len(routing.routes), actual_points=total_points, geometry_work_score=work)
    _model_preflight((building, layout, routing, equipment), limits)
    return counts


def validate_transfer_routes(building, layout, routing, transfer_program, *, production_program,
                             equipment=None, include_windows=False, limits=TransferProgramLimits()):
    """Evaluate supplied permissions at coordinate-derived physical crossings.

    The caller's room_path and free-text FlowSpec.type never classify movement
    or identify a portal. Every crossing is resolved against all rebuilt doors,
    not the lexical door chosen by a saved or freshly generated route report.
    """
    evidence = {"source": {}, "counts": {}, "crossings": [], "routes": [],
                "contract_validation": None, "geometry_validation": None,
                "policy": {"movement_classification": "explicit_source_bound_flow_id_only",
                           "opening_binding": "all_actual_doors_then_unique_explicit_source_id",
                           "airlock_and_pass_box_mechanisms": "UNKNOWN",
                           "mechanism_scope": "encountered_uniquely_bound_transfers_only"}}
    issues = []

    def result(geometry, permission, mechanism):
        states = (geometry, permission, mechanism)
        status = "FAIL" if "FAIL" in states else "UNKNOWN" if "UNKNOWN" in states else "PASS"
        return TransferRouteValidation(status, geometry, permission, mechanism, tuple(issues),
            json.dumps(evidence, sort_keys=True, ensure_ascii=False, allow_nan=False, separators=(",", ":")))

    def issue(code, message, status="UNKNOWN", path="$.routing", **ids):
        issues.append(TransferIssue(code, path, message, status, **ids))

    if not _limits_valid(limits):
        issue("invalid_limits", "Limits require actual bounded positive integers", "FAIL", "$.limits")
        return result("NOT_EVALUATED", "FAIL", "NOT_EVALUATED")
    if type(include_windows) is not bool:
        issue("invalid_include_windows", "include_windows requires an actual boolean", "FAIL", "$.include_windows")
        return result("NOT_EVALUATED", "FAIL", "NOT_EVALUATED")
    try:
        evidence["counts"] = _request_counts(building, layout, routing, equipment, limits)
        transfer_raw = _contract_raw(transfer_program, TransferProgramContract, limits)
        production_raw = _contract_raw(production_program, ProductionProgramContract, limits)
        raw_count = _preflight(transfer_raw, _raw_limits(limits))
        production_count = _preflight(production_raw, _raw_limits(limits))
        _model_preflight((building, layout, routing, equipment, transfer_raw, production_raw), limits)
        source_frames = {"building": building.to_dict(), "layout": layout.to_dict(), "routing": routing.to_dict(),
                         "equipment": equipment.to_dict(building) if equipment is not None else {"equipment": []}}
        geometry_count = _preflight(source_frames, _raw_limits(limits, byte_limit=limits.max_input_bytes))
        combined_bytes = raw_count["json_utf8_bytes"] + production_count["json_utf8_bytes"] + geometry_count["json_utf8_bytes"]
        combined_nodes = raw_count["json_nodes"] + production_count["json_nodes"] + geometry_count["json_nodes"]
        if combined_bytes > limits.max_input_bytes or combined_nodes > limits.max_nodes:
            raise _InputFailure("input_budget_exceeded", "$", "Combined actual source/contract request budget exceeded")
        evidence["counts"].update(combined_json_utf8_bytes=combined_bytes, combined_json_nodes=combined_nodes)
        evidence["source"] = {name + "_sha256": _hash(value) for name, value in source_frames.items()}
        evidence["source"].update(transfer_program_sha256=_hash(transfer_raw),
                                  production_program_sha256=_hash(production_raw) if production_raw is not None else None)
    except _InputFailure as exc:
        issue(exc.issue.code, exc.issue.message, "UNKNOWN" if exc.issue.code == "input_budget_exceeded" else "FAIL", exc.issue.path)
        return result("NOT_EVALUATED", "UNKNOWN" if exc.issue.code == "input_budget_exceeded" else "FAIL", "NOT_EVALUATED")
    except (ValueError, TypeError, AttributeError, OverflowError, RecursionError, KeyError) as exc:
        issue("invalid_request", str(exc)[:256], "FAIL", "$")
        return result("FAIL", "NOT_EVALUATED", "NOT_EVALUATED")

    contract = validate_transfer_program(transfer_raw, building=building, production_program=production_raw, limits=limits)
    evidence["contract_validation"] = contract.to_dict()
    issues.extend(contract.issues)
    if contract.schema_status != "PASS" or contract.binding_status != "PASS":
        return result("NOT_EVALUATED", "FAIL", "NOT_EVALUATED")
    raw = contract.program.to_dict()
    permission = contract.provenance_status
    window_options = {"include_windows": True} if include_windows else {}
    unresolved_pairs = set()
    try:
        budget = OrthogonalFlowBudget(max_rooms=min(1_000_000, limits.max_rooms),
            max_zones=min(1_000_000, limits.max_zones), max_equipment=min(1_000_000, limits.max_equipment),
            max_flows=min(1_000_000, limits.max_flows), max_endpoint_pairs=min(1_000_000, limits.max_routes),
            max_points_per_route=min(1_000_000, limits.max_route_points))
        fresh = validate_orthogonal_flow_routes(building, layout, routing, equipment, budget=budget, **window_options)
        evidence["geometry_validation"] = fresh.to_dict()
        geometry = fresh.status
    except (ValueError, TypeError, AttributeError, OverflowError, KeyError) as exc:
        geometry = "UNKNOWN"
        permission = "UNKNOWN" if permission != "FAIL" else permission
        issue("actual_geometry_unavailable", str(exc)[:256])
    try:
        rooms = {identifier: (footprint if isinstance(footprint, OrthogonalPolygon) else
                              OrthogonalPolygon.from_rect(footprint)).geometry for identifier, footprint in layout.placements.items()}
        openings = build_orthogonal_wall_plan(building.layout, layout, **window_options).openings
    except (ValueError, TypeError, AttributeError, OverflowError, KeyError) as exc:
        # A rejected source capability cannot gain geometry acceptance here.
        # Individually resolve explicit doors only to retain ambiguity/denial
        # evidence; this diagnostic path cannot authorize a scoped PASS.
        if geometry != "FAIL":
            geometry = "UNKNOWN"
        permission = "UNKNOWN" if permission != "FAIL" else permission
        issue("unsupported_actual_opening", str(exc)[:256])
        try:
            footprints = {identifier: (footprint if isinstance(footprint, OrthogonalPolygon) else
                                       OrthogonalPolygon.from_rect(footprint)) for identifier, footprint in layout.placements.items()}
            rooms = {identifier: footprint.geometry for identifier, footprint in footprints.items()}
            openings = []
            for door in building.layout.doors:
                try:
                    edges = footprints[door.room_a].shared_edges(footprints[door.room_b])
                    if len(edges) != 1:
                        raise ValueError("Explicit opening has no unique actual shared edge")
                    openings.append(_offset_opening(door.room_a, door.room_b, edges[0], door.offset_mm, door.width_mm, door.id))
                except (ValueError, TypeError, AttributeError, OverflowError, KeyError):
                    unresolved_pairs.add(frozenset((door.room_a, door.room_b)))
                    issue("ambiguous_explicit_opening_geometry", "Explicit opening has no unique actual shared edge",
                          opening_id=door.id)
        except (ValueError, TypeError, AttributeError, OverflowError, KeyError) as fallback_error:
            issue("actual_opening_resolution_unavailable", str(fallback_error)[:256])
            return result(geometry, "UNKNOWN", "UNKNOWN")

    if geometry != "PASS":
        issue("actual_geometry_failed" if geometry == "FAIL" else "actual_geometry_unknown",
              "Fresh source room, equipment, endpoint and corridor predicates did not pass", geometry)
    transfers = {row["opening_id"]: row for row in raw["transfers"]}
    classifications = {row["flow_id"]: row for row in raw["flow_movements"]}
    flows = {flow.id: flow for flow in building.flows}
    mechanism = "PASS"

    def permission_issue(code, message, status="UNKNOWN", **ids):
        nonlocal permission
        issue(code, message, status, **ids)
        if status == "FAIL" or permission != "FAIL":
            permission = status

    for route in routing.routes:
        ids = {"flow_id": route.flow_id, "from_id": route.from_id, "to_id": route.to_id}
        classification = classifications.get(route.flow_id)
        if classification is None:
            permission_issue("missing_movement_classification", "No explicit supplied classification for this flow ID; FlowSpec.type is not authority", **ids)
        if route.flow_id not in flows:
            permission_issue("unknown_actual_flow", "Actual route does not bind a canonical source flow", "FAIL", **ids)
            continue
        points = tuple(tuple(point) for point in route.points)
        if len(set(points)) < 2 or any(a[0] != b[0] and a[1] != b[1] for a, b in zip(points, points[1:])):
            permission_issue("actual_crossings_unavailable", "Degenerate or nonorthogonal actual route cannot establish portal traversal", **ids)
            continue
        actual_path, crossings, trace_issues = _trace_rooms(SimpleNamespace(rooms=rooms), points)
        evidence["routes"].append({**ids, "from_room_id": route.from_room_id, "to_room_id": route.to_room_id,
                                   "actual_room_path": list(actual_path), "trace_issues": list(trace_issues)})
        if trace_issues:
            permission_issue("ambiguous_actual_trace", "Shared-boundary run or missing interior hosts cannot establish complete permission coverage", **ids)
        width = max(route.minimum_clear_width_mm, flows[route.flow_id].minimum_clear_width_mm)
        for first, second, point in crossings:
            matched = [door for door in openings if not door.external and {door.room_a, door.room_b} == {first, second}
                       and abs((point[0] if door.orientation == "vertical" else point[1]) - door.fixed) <= 1e-6
                       and door.start - 1e-6 <= (point[1] if door.orientation == "vertical" else point[0]) <= door.end + 1e-6]
            crossing = {**ids, "from_room": first, "to_room": second, "point": list(point),
                        "matched_opening_ids": sorted(door.id for door in matched), "opening_id": None,
                        "permission_status": "UNKNOWN"}
            evidence["crossings"].append(crossing)
            if frozenset((first, second)) in unresolved_pairs:
                permission_issue("ambiguous_opening_binding", "An unresolved source opening could cover this room crossing; other matches cannot establish permission", **ids)
                continue
            if not matched:
                permission_issue("actual_crossing_without_door", "Actual room side-change is not inside a rebuilt source door", "FAIL", **ids)
                crossing["permission_status"] = "FAIL"
                continue
            if len(matched) != 1:
                permission_issue("ambiguous_opening_binding", "Multiple actual source openings cover this crossing; no lexical ID can establish permission", **ids)
                continue
            opening = matched[0]
            crossing["opening_id"] = opening.id
            transfer = transfers.get(opening.id)
            if transfer is None:
                permission_issue("unbound_actual_opening", "Actual crossed door has no supplied explicit transfer declaration", opening_id=opening.id, **ids)
                continue
            transfer_ids = {**ids, "transfer_id": transfer["id"], "opening_id": opening.id, "source_id": transfer["source_id"]}
            crossing.update(transfer_id=transfer["id"], source_id=transfer["source_id"], kind=transfer["kind"])
            if transfer["kind"] != "door":
                mechanism = "UNKNOWN"
                issue("unsupported_transfer_mechanism", "Two-dimensional crossing does not verify chamber, interlock, HVAC or three-dimensional loading operation", **transfer_ids)
            minimum = max(width, transfer["minimum_clear_width_mm"])
            coordinate = point[1] if opening.orientation == "vertical" else point[0]
            width_failed = coordinate - minimum / 2 < opening.start - 1e-6 or coordinate + minimum / 2 > opening.end + 1e-6
            if width_failed:
                permission_issue("transfer_crossing_too_narrow", "Complete actual corridor does not fit the declared minimum source transfer span", "FAIL", **transfer_ids)
                crossing["permission_status"] = "FAIL"
            if classification is None:
                continue
            movement = classification["movement_kind"]
            direction = "forward" if [first, second] == transfer["room_chain"] else "reverse"
            crossing.update(movement_kind=movement, direction=direction,
                            classification_source_id=classification["source_id"], classification_basis=classification["basis"])
            permission_row = next((row for row in transfer["permissions"] if row["direction"] == direction), None)
            if transfer["kind"] in ("pass_box", "material_airlock") and movement == "personnel":
                permission_issue("transfer_movement_forbidden", "Personnel may not traverse a pass-box or material airlock", "FAIL", **transfer_ids)
                crossing["permission_status"] = "FAIL"
            elif permission_row is None:
                permission_issue("transfer_direction_forbidden", "Actual direction has no supplied transfer permission", "FAIL", **transfer_ids)
                crossing["permission_status"] = "FAIL"
            elif movement not in permission_row["movement_kinds"]:
                permission_issue("transfer_movement_forbidden", "Explicit classified movement is absent from supplied permissions", "FAIL", **transfer_ids)
                crossing["permission_status"] = "FAIL"
            elif not width_failed:
                crossing["permission_status"] = "PASS" if contract.provenance_status == "PASS" else "UNKNOWN"
    return result(geometry, permission, mechanism)

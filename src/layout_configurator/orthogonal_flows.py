"""Bounded Manhattan routing and independent actual-polygon corridor checks.

The source-backed output is DRAFT. Local route geometry does not establish
flow segregation, profile compliance, engineering approval or export support.
No rectangular complete-pipeline capability guard is changed by this module.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
import hashlib
import heapq
import json
import math

from shapely.geometry import LineString, Point, box
from shapely.ops import unary_union

from .building import BuildingIR
from .equipment import EquipmentLayoutResult, EquipmentPlacement, clearance_rect
from .flows import FlowRoute, FlowRoutingResult, FlowValidationIssue
from .models import LayoutResult, Rect
from .orthogonal import OrthogonalPolygon
from .orthogonal_projection import ProjectionCapabilityError, build_orthogonal_wall_plan
from .orthogonal_validation import OrthogonalDimensionPolicy, _window_options, validate_orthogonal_equipment, validate_orthogonal_layout
from .result_geometry import parse_layout_result
from .schema import normalize_building_mapping


@dataclass(frozen=True)
class OrthogonalFlowBudget:
    max_rooms: int = 64
    max_zones: int = 64
    max_equipment: int = 64
    max_flows: int = 64
    max_endpoint_pairs: int = 256
    max_endpoint_candidates: int = 16
    max_nodes: int = 4096
    max_grid_steps: int = 65_536
    max_edge_checks: int = 100_000
    max_search_steps: int = 100_000
    max_points_per_route: int = 1024
    max_rejections: int = 128
    room_policy: OrthogonalDimensionPolicy = field(default_factory=OrthogonalDimensionPolicy)

    def __post_init__(self):
        for name, value in vars(self).items():
            if name == "room_policy":
                if not isinstance(value, OrthogonalDimensionPolicy):
                    raise ValueError("room_policy requires OrthogonalDimensionPolicy")
            elif not isinstance(value, int) or isinstance(value, bool) or not 1 <= value <= 1_000_000:
                raise ValueError(f"{name} requires an integer between 1 and 1000000")

    def to_dict(self):
        return {**{name: value for name, value in vars(self).items() if name != "room_policy"},
                "room_policy": self.room_policy.to_dict(), "work_scope": "Grid, edge and search limits apply per endpoint pair"}


@dataclass(frozen=True)
class OrthogonalFlowReport:
    issues: tuple[FlowValidationIssue, ...] = ()
    unknown: tuple[str, ...] = ()
    proofs: tuple[dict, ...] = ()
    room_report: object | None = None
    equipment_report: object | None = None

    @property
    def status(self):
        reports = (self.room_report, self.equipment_report)
        if self.issues or any(report is not None and report.status == "FAIL" for report in reports):
            return "FAIL"
        return "UNKNOWN" if self.unknown or any(report is not None and report.status == "UNKNOWN" for report in reports) else "PASS"

    @property
    def ok(self):
        return self.status == "PASS"

    def to_dict(self):
        return {"status": self.status, "ok": self.ok,
                "scope": "Source room/equipment and actual route width, crossings and endpoint access only; not flow segregation or engineering acceptance",
                "policy": _policy(), "issues": [issue.to_dict() for issue in self.issues],
                "unknown": list(self.unknown), "proofs": list(self.proofs),
                "room_report": self.room_report.to_dict() if self.room_report else None,
                "equipment_report": self.equipment_report.to_dict() if self.equipment_report else None}


@dataclass(frozen=True)
class OrthogonalFlowResult:
    routing: FlowRoutingResult
    report: OrthogonalFlowReport
    evidence: dict

    @property
    def status(self):
        return "DRAFT" if self.report.ok and self.evidence["completed_pairs"] == self.evidence["expected_pairs"] and not self.evidence["search_truncated"] else "UNKNOWN"

    @property
    def ok(self):
        return False

    def to_dict(self):
        return {"status": self.status, "ok": self.ok, "backend": "orthogonal_visibility_v1",
                "scope": "Finite orthogonal path search; exhaustion is not a whole-program infeasibility proof",
                "full_facility_validation": {"status": "UNKNOWN", "evidence": ["Flow segregation, profiles, windows and complete polygon exporters are not dispatched"]},
                "routing": self.routing.to_dict(), "report": self.report.to_dict(), **self.evidence}


@dataclass
class _Context:
    building: BuildingIR
    layout: LayoutResult
    equipment: EquipmentLayoutResult
    rooms: dict
    services: dict
    usable: dict
    circulation: object
    wall_plan: object | None
    room_report: object
    equipment_report: object
    unknown: tuple[str, ...]


def route_orthogonal_flows(building: BuildingIR, layout: LayoutResult,
                           equipment: EquipmentLayoutResult | None = None, *,
                           budget: OrthogonalFlowBudget = OrthogonalFlowBudget(),
                           include_windows: bool = False) -> OrthogonalFlowResult:
    """Search finite actual free-space visibility graphs, then recheck routes.

    Configuration-space polygon erosion can omit zero-area exact-width
    passages. Such missed paths remain UNKNOWN; a supplied route can still
    pass the independent swept-corridor checker at exactly the source width.
    """
    window_options = _window_options(include_windows)
    layout, equipment = _inputs(building, layout, equipment, budget)
    state = {"source_sha256": _hash(building.to_dict()), "layout_sha256": _hash(layout.to_dict()),
             "equipment_sha256": _hash(_equipment_data(equipment)), "search_space_sha256": None,
             "budget": budget.to_dict(), "policy": _policy(), "expected_pairs": 0, "allocated_endpoint_pairs": 0, "completed_pairs": 0,
             "search_truncated": False, "pairs": [], "rejections": [], "rejection_counts": {},
             "last_rejection": None, "rejections_truncated": False,
             "search_limitations": ["Finite critical-coordinate grid and bounded component representatives",
                                    "Polygon erosion may omit zero-area exact-width connections"]}
    if include_windows:
        state.update(window_policy="source_facade_v1")
    limit = _source_limit(building, equipment, budget)
    if limit:
        state["search_truncated"] = True
        _reject(state, budget, "source_budget_exhausted", message=limit)
        return OrthogonalFlowResult(FlowRoutingResult(()), OrthogonalFlowReport(unknown=(limit,)), state)
    context = _context(building, layout, equipment, budget, **window_options)
    expected, total_pairs = _expected_pairs(context, budget.max_endpoint_pairs)
    state["expected_pairs"] = total_pairs
    state["allocated_endpoint_pairs"] = len(expected)
    if total_pairs > budget.max_endpoint_pairs:
        state["search_truncated"] = True
        _reject(state, budget, "endpoint_pair_budget", message="Only the bounded endpoint-pair prefix was allocated")
        return OrthogonalFlowResult(FlowRoutingResult(()), _validate(context, FlowRoutingResult(()), expected, budget), state)
    if not context.room_report.ok or not context.equipment_report.ok or context.wall_plan is None:
        _reject(state, budget, "source_validation", message="Source room/equipment/opening predicates did not pass")
        report = _validate(context, FlowRoutingResult(()), expected, budget)
        return OrthogonalFlowResult(FlowRoutingResult(()), report, state)
    routes, fingerprints = [], []
    for index, (key, flow) in enumerate(sorted(expected.items())):
        if index >= budget.max_endpoint_pairs:
            state["search_truncated"] = True
            _reject(state, budget, "endpoint_pair_budget", key)
            break
        free = context.circulation.difference(context.wall_plan.geometry)
        free = free.difference(unary_union(list(context.services.values())))
        center = free.buffer(-flow.minimum_clear_width_mm / 2, join_style=2)
        starts, start_cut = _anchors(context, key[1], key[3], center, flow.minimum_clear_width_mm, budget)
        ends, end_cut = _anchors(context, key[2], key[4], center, flow.minimum_clear_width_mm, budget)
        pair = {"key": list(key), "grid_steps": 0, "edge_checks": 0, "search_steps": 0,
                "nodes": 0, "edges": 0, "truncated": start_cut or end_cut, "status": "UNKNOWN"}
        points, fingerprint, reason = _search(context, free, center, starts, ends, flow.minimum_clear_width_mm, budget, pair)
        fingerprints.append({"key": list(key), "fingerprint": fingerprint})
        if points:
            room_path, _, _ = _trace_rooms(context, points)
            candidate = FlowRoute(key[0], key[1], key[2], key[3], key[4], room_path, points, flow.minimum_clear_width_mm)
            candidate_issues, _ = _check_route(context, candidate, flow)
            if candidate_issues:
                reason = "candidate_failed_independent_geometry"
                pair["candidate_issues"] = [issue.to_dict() for issue in candidate_issues]
            else:
                routes.append(candidate)
                state["completed_pairs"] += 1
                pair["status"] = "PASS"
        if pair["status"] != "PASS":
            _reject(state, budget, reason or "no_finite_path", key)
        state["search_truncated"] |= pair["truncated"]
        state["pairs"].append(pair)
    state["search_space_sha256"] = _hash(fingerprints)
    routing = FlowRoutingResult(tuple(routes))
    report = _validate(context, routing, expected, budget)
    return OrthogonalFlowResult(routing, report, state)


def validate_orthogonal_flow_routes(building: BuildingIR, layout: LayoutResult, routing: FlowRoutingResult,
                                    equipment: EquipmentLayoutResult | None = None, *,
                                    budget: OrthogonalFlowBudget = OrthogonalFlowBudget(),
                                    include_windows: bool = False) -> OrthogonalFlowReport:
    """Rebuild source geometry; never accept a supplied report or room chain."""
    window_options = _window_options(include_windows)
    layout, equipment = _inputs(building, layout, equipment, budget)
    if not isinstance(routing, FlowRoutingResult) or not isinstance(routing.routes, (list, tuple)):
        raise ValueError("Flow check requires FlowRoutingResult with a route list/tuple")
    limit = _source_limit(building, equipment, budget)
    if limit:
        return OrthogonalFlowReport(unknown=(limit,))
    context = _context(building, layout, equipment, budget, **window_options)
    expected, _ = _expected_pairs(context, budget.max_endpoint_pairs)
    return _validate(context, routing, expected, budget)


def _validate(context, routing, expected, budget):
    issues, proofs, seen = [], [], set()
    unknown = list(context.unknown)
    if len(expected) > budget.max_endpoint_pairs or len(routing.routes) > budget.max_endpoint_pairs:
        return OrthogonalFlowReport(unknown=("Independent endpoint-pair checking budget exhausted",),
                                    room_report=context.room_report, equipment_report=context.equipment_report)
    flows = {flow.id: flow for flow in context.building.flows}
    for route in routing.routes:
        _route_data(route, budget)
        flow = flows.get(route.flow_id)
        key = (route.flow_id, route.from_id, route.to_id, route.from_room_id, route.to_room_id)
        if key in seen:
            issues.append(FlowValidationIssue("duplicate_route", "Duplicate source endpoint pair", route.flow_id))
        seen.add(key)
        if flow is None:
            issues.append(FlowValidationIssue("unknown_flow", "Route has no canonical source flow", route.flow_id))
            continue
        if key not in expected:
            issues.append(FlowValidationIssue("unexpected_endpoint_pair", "Route endpoints/hosts do not match the source program", route.flow_id))
        if context.wall_plan is not None:
            route_issues, proof = _check_route(context, route, flow)
            issues.extend(route_issues)
            proofs.append(proof)
    for key, flow in sorted(expected.items()):
        if flow.required and key not in seen:
            issues.append(FlowValidationIssue("no_route", f"Required endpoint pair {key[1:]} has no supplied route", flow.id))
    for flow in context.building.flows:
        if not _resolve(context, flow.from_ids) or not _resolve(context, flow.to_ids):
            issues.append(FlowValidationIssue("endpoint_missing", "Source endpoint cannot be resolved to a supplied host", flow.id))
    return OrthogonalFlowReport(tuple(issues), tuple(unknown), tuple(proofs), context.room_report, context.equipment_report)


def _check_route(context, route, flow):
    issues = []
    def fail(code, message):
        issues.append(FlowValidationIssue(code, message, route.flow_id))
    width = max(route.minimum_clear_width_mm, flow.minimum_clear_width_mm)
    if route.minimum_clear_width_mm + 1e-6 < flow.minimum_clear_width_mm:
        fail("route_width_below_source", "Claimed corridor width is below the canonical source requirement")
    points = tuple(tuple(point) for point in route.points)
    if len(points) < 2 or len(set(points)) < 2:
        fail("degenerate_route", "A route requires at least two distinct actual points")
    if any(a[0] != b[0] and a[1] != b[1] for a, b in zip(points, points[1:])):
        fail("nonorthogonal_route", "All route segments must be axis aligned")
    if not points or any(issue.code in {"degenerate_route", "nonorthogonal_route"} for issue in issues):
        return issues, {"flow_id": flow.id, "status": "FAIL", "predicates": [issue.to_dict() for issue in issues]}
    corridor = _corridor(points, width)
    actual_path, crossings, trace_issues = _trace_rooms(context, points)
    for code in trace_issues:
        fail(code, "Actual centreline has an unsupported shared-boundary run or lies outside its room footprints")
    if tuple(route.room_path) != actual_path:
        fail("room_path_mismatch", f"Coordinates traverse {actual_path}, not the reported room path")
    if not actual_path or actual_path[0] != route.from_room_id or actual_path[-1] != route.to_room_id:
        fail("endpoint_host_mismatch", "Actual path terminals do not belong to the declared source hosts")
    doors = []
    for first, second, point in crossings:
        adequate = [door for door in context.wall_plan.openings if not door.external
                    and {door.room_a, door.room_b} == {first, second}
                    and abs((point[0] if door.orientation == "vertical" else point[1]) - door.fixed) <= 1e-6
                    and door.start - 1e-6 <= (point[1] if door.orientation == "vertical" else point[0]) <= door.end + 1e-6]
        if not adequate:
            fail("route_missing_door", "Actual room crossing has no generated source door")
            continue
        door = min(adequate, key=lambda item: item.id)
        coordinate = point[1] if door.orientation == "vertical" else point[0]
        if door.width + 1e-6 < width:
            fail("opening_too_narrow", "Actual generated opening is below the required corridor width")
        elif coordinate - width / 2 < door.start - 1e-6 or coordinate + width / 2 > door.end + 1e-6:
            fail("door_crossing_clearance", "The actual corridor crossing does not fit the complete opening span")
        doors.append({"door_id": door.id, "from_room": first, "to_room": second, "point": list(point), "width_mm": door.width})
    outside = corridor.difference(unary_union(list(context.rooms.values()))).area
    outside_usable = corridor.difference(context.circulation).area
    wall_overlap = corridor.intersection(context.wall_plan.geometry).area
    if outside > 1e-6:
        fail("corridor_outside_rooms", "Actual full-width corridor leaves the source room union")
    if outside_usable > 1e-6:
        fail("corridor_outside_usable_components", "Full-width corridor leaves actual wall-half inset components and generated door throats")
    if wall_overlap > 1e-6:
        fail("wall_collision", "Actual full-width corridor intersects the rebuilt wall bands")
    collisions = []
    for identifier, service in sorted(context.services.items()):
        overlap = corridor.intersection(service).area
        if overlap > 1e-6:
            fail("equipment_clearance_collision", f"Actual corridor intersects service envelope {identifier}")
            collisions.append({"equipment_id": identifier, "overlap_area_mm2": overlap})
    accesses = []
    for identifier, host, point in ((route.from_id, route.from_room_id, points[0]), (route.to_id, route.to_room_id, points[-1])):
        endpoint_issues, access = _check_endpoint(context, identifier, host, point, width)
        for code in endpoint_issues:
            fail(code, f"Endpoint {identifier} does not have source-backed full-width actual access")
        accesses.append(access)
    failed = {issue.code for issue in issues}
    predicate_groups = {"source_width": {"route_width_below_source"}, "actual_room_path": {"room_path_mismatch", "endpoint_host_mismatch", "route_outside_rooms", "ambiguous_boundary_run"},
                        "actual_doors": {"route_missing_door", "opening_too_narrow", "door_crossing_clearance"},
                        "swept_corridor": {"corridor_outside_rooms", "corridor_outside_usable_components", "wall_collision", "equipment_clearance_collision"},
                        "source_endpoints": {"equipment_endpoint_mismatch", "unknown_equipment_endpoint", "endpoint_outside_host", "endpoint_access_wall_collision", "endpoint_access_collision", "endpoint_access_outside_host"}}
    return issues, {"flow_id": flow.id, "from_id": route.from_id, "to_id": route.to_id,
                    "status": "FAIL" if issues else "PASS", "required_width_mm": width,
                    "actual_room_path": list(actual_path), "actual_crossings": doors, "endpoint_access": accesses,
                    "corridor_area_mm2": corridor.area, "outside_area_mm2": outside, "outside_usable_area_mm2": outside_usable,
                    "wall_overlap_area_mm2": wall_overlap, "service_collisions": collisions,
                    "predicates": [{"id": name, "status": "FAIL" if failed & codes else "PASS"} for name, codes in predicate_groups.items()]}


def _check_endpoint(context, identifier, host, point, width):
    issues, evidence = [], {"source_id": identifier, "room_id": host, "point": list(point)}
    room = context.rooms.get(host)
    if room is None or not room.buffer(-context.building.layout.wall_thickness_mm / 2, join_style=2).covers(_corridor((point,), width)):
        issues.append("endpoint_outside_host")
    placement = next((item for item in context.equipment.placements if item.equipment_id == identifier), None)
    if placement is None:
        return issues, evidence
    if identifier not in context.services:
        issues.append("unknown_equipment_endpoint")
        return issues, evidence
    anchor, access = _front_access(context, placement, width)
    evidence.update({"policy": "front_service_standoff_v1", "expected_point": list(anchor), "access_area_mm2": access.area})
    if math.dist(point, anchor) > 1e-6:
        issues.append("equipment_endpoint_mismatch")
    if host not in context.usable or access.difference(context.usable[host]).area > 1e-6:
        issues.append("endpoint_access_outside_host")
    if access.intersection(context.wall_plan.geometry).area > 1e-6:
        issues.append("endpoint_access_wall_collision")
    if any(access.intersection(service).area > 1e-6 for other, service in context.services.items() if other != identifier):
        issues.append("endpoint_access_collision")
    return issues, evidence


def _front_access(context, placement, width):
    rect, half = placement.rect, width / 2
    service = context.services[placement.equipment_id]
    left, _, _, top = service.bounds
    if placement.rotated:
        anchor = (left - half - 10, rect.y + rect.height / 2)
        access = box(anchor[0], anchor[1] - half, rect.x, anchor[1] + half)
    else:
        anchor = (rect.x + rect.width / 2, top + half + 10)
        access = box(anchor[0] - half, rect.top, anchor[0] + half, anchor[1])
    return anchor, access


def _trace_rooms(context, points):
    pieces, issues = [], set()
    for first, second in zip(points, points[1:]):
        if first == second:
            continue
        line = LineString((first, second))
        levels = {0.0, 1.0}
        for room in context.rooms.values():
            intersection = line.intersection(room.boundary)
            if intersection.length > 1e-6:
                issues.add("ambiguous_boundary_run")
            for coordinate in _coordinates(intersection):
                levels.add(min(1.0, max(0.0, line.project(Point(coordinate)) / line.length)))
        levels = sorted(levels)
        for low, high in zip(levels, levels[1:]):
            if (high - low) * line.length <= 1e-6:
                continue
            point = line.interpolate((low + high) * line.length / 2)
            hosts = [identifier for identifier, room in sorted(context.rooms.items()) if room.contains(point)]
            if len(hosts) != 1:
                issues.add("route_outside_rooms")
            else:
                start = line.interpolate(low * line.length)
                pieces.append((hosts[0], (start.x, start.y)))
    rooms, crossings = [], []
    for host, point in pieces:
        if not rooms or rooms[-1] != host:
            if rooms:
                crossings.append((rooms[-1], host, point))
            rooms.append(host)
    return tuple(rooms), tuple(crossings), tuple(sorted(issues))


def _anchors(context, identifier, host, center, width, budget):
    placement = next((item for item in context.equipment.placements if item.equipment_id == identifier), None)
    if placement:
        anchor, _ = _front_access(context, placement, width)
        issues, _ = _check_endpoint(context, identifier, host, anchor, width)
        return ((anchor,) if not issues and center.covers(Point(anchor)) else ()), False
    room_center = context.rooms[host].buffer(-(context.building.layout.wall_thickness_mm / 2 + width / 2), join_style=2)
    parts = sorted(_polygons(center.intersection(room_center)), key=lambda part: (-part.area, part.bounds))
    points = tuple((part.representative_point().x, part.representative_point().y) for part in parts[:budget.max_endpoint_candidates])
    return points, len(parts) > budget.max_endpoint_candidates


def _search(context, free, center, starts, ends, width, budget, pair):
    if not starts or not ends:
        return (), {"starts": starts, "ends": ends}, "endpoint_has_no_full_width_access"
    if not any(part.covers(Point(start)) and part.covers(Point(end)) for part in _polygons(center) for start in starts for end in ends):
        return (), {"starts": starts, "ends": ends}, "no_free_component_connection"
    coordinates = [*_coordinates(center), *starts, *ends,
                   *(door.center for door in context.wall_plan.openings if not door.external and door.width + 1e-6 >= width)]
    xs, ys = sorted({x for x, _ in coordinates}), sorted({y for _, y in coordinates})
    nodes = []
    for x in xs:
        for y in ys:
            if pair["grid_steps"] >= budget.max_grid_steps or len(nodes) >= budget.max_nodes:
                pair["truncated"] = True
                break
            pair["grid_steps"] += 1
            if center.covers(Point(x, y)):
                nodes.append((x, y))
        if pair["truncated"]:
            break
    pair["nodes"] = len(nodes)
    indexes = {point: index for index, point in enumerate(nodes)}
    fingerprint = {"xs": xs, "ys": ys, "nodes": nodes, "edges": []}
    if pair["truncated"]:
        return (), fingerprint, "visibility_grid_budget"
    edges = defaultdict(list)
    axes = (defaultdict(list), defaultdict(list))
    for index, (x, y) in enumerate(nodes):
        axes[0][x].append(index)
        axes[1][y].append(index)
    for axis in axes:
        for indices in axis.values():
            for left, right in zip(indices, indices[1:]):
                if pair["edge_checks"] >= budget.max_edge_checks:
                    pair["truncated"] = True
                    return (), fingerprint, "visibility_edge_budget"
                pair["edge_checks"] += 1
                line = LineString((nodes[left], nodes[right]))
                if center.covers(line) and _corridor((nodes[left], nodes[right]), width).difference(free).area <= 1e-6:
                    distance = line.length
                    edges[left].append((right, distance))
                    edges[right].append((left, distance))
                    fingerprint["edges"].append([left, right])
    pair["edges"] = len(fingerprint["edges"])
    goals = {indexes[point] for point in ends if point in indexes}
    best, queue = {}, []
    for point in starts:
        if point in indexes:
            index = indexes[point]
            best[index] = (0.0, (index,))
            heapq.heappush(queue, (0.0, (index,), index))
    while queue:
        if pair["search_steps"] >= budget.max_search_steps:
            pair["truncated"] = True
            return (), fingerprint, "path_search_budget"
        pair["search_steps"] += 1
        distance, path, current = heapq.heappop(queue)
        if best.get(current) != (distance, path):
            continue
        if current in goals and len(path) > 1:
            points = _simplify(tuple(nodes[index] for index in path))
            if len(points) > budget.max_points_per_route:
                pair["truncated"] = True
                return (), fingerprint, "route_point_budget"
            return points, fingerprint, None
        for neighbor, cost in sorted(edges[current]):
            candidate = (distance + cost, path + (neighbor,))
            if neighbor not in best or candidate < best[neighbor]:
                best[neighbor] = candidate
                heapq.heappush(queue, (*candidate, neighbor))
    return (), fingerprint, "no_finite_path"


def _context(building, layout, equipment, budget, *, include_windows=False):
    rooms = {identifier: (OrthogonalPolygon.from_rect(shape) if isinstance(shape, Rect) else shape).geometry
             for identifier, shape in sorted(layout.placements.items())}
    window_options = _window_options(include_windows)
    room_report = validate_orthogonal_layout(building.layout, layout, dimension_policy=budget.room_policy, **window_options)
    equipment_report = validate_orthogonal_equipment(building, layout, equipment, **window_options)
    specs = {item.id: item for item in building.equipment}
    services = {}
    for item in equipment.placements:
        if item.equipment_id in specs:
            rect = clearance_rect(specs[item.equipment_id], item.rect, item.rotated)
            services[item.equipment_id] = box(rect.x, rect.y, rect.right, rect.top)
    unknown, wall_plan = (), None
    try:
        wall_plan = build_orthogonal_wall_plan(building.layout, layout, **window_options)
    except (ProjectionCapabilityError, ValueError) as exc:
        unknown = (f"Actual opening projection unavailable: {exc}",)
    half_wall = building.layout.wall_thickness_mm / 2
    usable = {identifier: room.buffer(-half_wall, join_style=2) for identifier, room in rooms.items()}
    throats = []
    for door in wall_plan.openings if wall_plan else ():
        if not door.external:
            throats.append(box(door.fixed - half_wall - 1, door.start, door.fixed + half_wall + 1, door.end)
                           if door.orientation == "vertical" else
                           box(door.start, door.fixed - half_wall - 1, door.end, door.fixed + half_wall + 1))
    circulation = unary_union([*usable.values(), *throats])
    return _Context(building, layout, equipment, rooms, services, usable, circulation, wall_plan, room_report, equipment_report, unknown)


def _inputs(building, layout, equipment, budget):
    if not isinstance(building, BuildingIR) or not isinstance(layout, LayoutResult) or not isinstance(budget, OrthogonalFlowBudget):
        raise ValueError("Orthogonal flows require canonical BuildingIR, LayoutResult and OrthogonalFlowBudget")
    _hash(building.to_dict())
    normalize_building_mapping(building.to_dict())
    layout = parse_layout_result(layout.to_dict())
    equipment = EquipmentLayoutResult(()) if equipment is None else equipment
    if not isinstance(equipment, EquipmentLayoutResult) or not isinstance(equipment.placements, (list, tuple)):
        raise ValueError("Equipment requires EquipmentLayoutResult with a placement list/tuple")
    for item in equipment.placements:
        if not isinstance(item, EquipmentPlacement) or not isinstance(item.rotated, bool) or not all(isinstance(value, str) and value.strip() for value in (item.equipment_id, item.room_id)):
            raise ValueError("Equipment requires typed placement, scalar identifiers and boolean rotation")
        OrthogonalPolygon.from_rect(item.rect)
    return layout, equipment


def _resolve(context, identifiers):
    room_ids = {room.id for room in context.building.layout.rooms}
    equipment = {item.equipment_id: item for item in context.equipment.placements}
    zones = {zone.id for zone in context.building.zones}
    result = set()
    for identifier in identifiers:
        if identifier in room_ids:
            result.add((identifier, identifier))
        elif identifier in equipment:
            result.add((identifier, equipment[identifier].room_id))
        elif identifier in zones:
            result.update((identifier, room) for room in context.building.room_ids_for_zone(identifier))
    return tuple(sorted(result))


def _expected_pairs(context, maximum):
    expected, count = {}, 0
    for flow in sorted(context.building.flows, key=lambda item: item.id):
        sources, targets = _resolve(context, flow.from_ids), _resolve(context, flow.to_ids)
        count += len(sources) * len(targets)
        if len(expected) <= maximum:
            for first, first_host in sources:
                for second, second_host in targets:
                    expected[(flow.id, first, second, first_host, second_host)] = flow
                    if len(expected) > maximum:
                        break
                if len(expected) > maximum:
                    break
    return expected, count


def _source_limit(building, equipment, budget):
    for count, maximum, name in ((len(building.layout.rooms), budget.max_rooms, "rooms"),
                                 (len(building.zones), budget.max_zones, "zones"),
                                 (max(len(building.equipment), len(equipment.placements)), budget.max_equipment, "equipment"),
                                 (len(building.flows), budget.max_flows, "flows")):
        if count > maximum:
            return f"Source {name} count exceeds this component's checking budget"
    return None


def _route_data(route, budget):
    if not isinstance(route, FlowRoute) or not all(isinstance(value, str) and value.strip() for value in (route.flow_id, route.from_id, route.to_id, route.from_room_id, route.to_room_id)):
        raise ValueError("FlowRoute requires nonempty scalar source identifiers")
    if not isinstance(route.room_path, (tuple, list)) or not all(isinstance(value, str) and value.strip() for value in route.room_path):
        raise ValueError("Room path requires a list/tuple of scalar identifiers")
    if not isinstance(route.points, (tuple, list)) or len(route.points) > budget.max_points_per_route:
        raise ValueError("Route point container is invalid or exceeds the independent checking budget")
    for point in route.points:
        if not isinstance(point, (tuple, list)) or len(point) != 2 or not all(_finite(value) for value in point):
            raise ValueError("Route points require finite numeric coordinate pairs")
    if not _finite(route.minimum_clear_width_mm) or route.minimum_clear_width_mm <= 0:
        raise ValueError("Route width requires a positive finite number")


def _corridor(points, width):
    half = width / 2
    if len(points) == 1:
        x, y = points[0]
        return box(x - half, y - half, x + half, y + half)
    return unary_union([box(min(a[0], b[0]) - half, min(a[1], b[1]) - half,
                            max(a[0], b[0]) + half, max(a[1], b[1]) + half)
                        for a, b in zip(points, points[1:])])


def _coordinates(geometry):
    if geometry.is_empty:
        return []
    if geometry.geom_type == "Polygon":
        return [*geometry.exterior.coords, *(point for ring in geometry.interiors for point in ring.coords)]
    if hasattr(geometry, "geoms"):
        return [point for part in geometry.geoms for point in _coordinates(part)]
    return list(geometry.coords)


def _polygons(geometry):
    return [geometry] if geometry.geom_type == "Polygon" and not geometry.is_empty else [part for part in getattr(geometry, "geoms", ()) if part.geom_type == "Polygon" and not part.is_empty]


def _simplify(points):
    result = []
    for point in points:
        if len(result) >= 2 and ((result[-2][0] == result[-1][0] == point[0]) or (result[-2][1] == result[-1][1] == point[1])):
            result.pop()
        result.append(point)
    return tuple(result)


def _reject(state, budget, code, key=None, message=None):
    rejection = {"code": code}
    if key is not None:
        rejection["endpoint_pair"] = list(key)
    if message is not None:
        rejection["message"] = message
    state["rejection_counts"][code] = state["rejection_counts"].get(code, 0) + 1
    state["last_rejection"] = rejection
    if len(state["rejections"]) < budget.max_rejections:
        state["rejections"].append(rejection)
    else:
        state["rejections_truncated"] = True


def _equipment_data(equipment):
    return [{"id": item.equipment_id, "host": item.room_id, "rotated": item.rotated,
             "rect": [item.rect.x, item.rect.y, item.rect.width, item.rect.height]}
            for item in sorted(equipment.placements, key=lambda item: (item.equipment_id, item.room_id, item.rect.x, item.rect.y))]


def _finite(value):
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:
        return False


def _hash(value):
    try:
        data = json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError("Orthogonal flow inputs require finite canonical JSON") from exc
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


def _policy():
    return {"name": "square_swept_corridor_v1", "source": "Project orthogonal routing contract; not a regulatory width standard",
            "width": "At least the canonical source flow width, including complete square turns and terminal caps",
            "crossings": "Actual ordered room changes at complete-width generated door spans",
            "usable_geometry": "Union of actual wall-half room inset components and generated internal door throats; source wall bands remain an independent obstacle",
            "equipment_endpoints": "front_service_standoff_v1: half-width plus 10 mm outside declared front service edge; independently checked normal access strip",
            "obstacles": "All directional service envelopes; no entire endpoint-equipment exemption"}

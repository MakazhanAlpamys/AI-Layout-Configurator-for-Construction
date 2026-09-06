"""Deterministic routing and validation for people/material/service flows."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
import math
from typing import Iterable

from shapely.geometry import LineString, box
from .building import BuildingIR, FlowSpec
from .equipment import EquipmentLayoutResult, EquipmentPlacement, clearance_rect
from .models import LayoutResult, Rect
from .walls import DoorOpening, WallPlan, build_wall_plan


@dataclass(frozen=True)
class FlowRoute:
    """A derived centreline route through solver-produced rooms and openings."""

    flow_id: str
    from_id: str
    to_id: str
    from_room_id: str
    to_room_id: str
    room_path: tuple[str, ...]
    points: tuple[tuple[float, float], ...]
    minimum_clear_width_mm: float

    def to_dict(self) -> dict[str, object]:
        return {
            "flow_id": self.flow_id,
            "from_id": self.from_id,
            "to_id": self.to_id,
            "from_room_id": self.from_room_id,
            "to_room_id": self.to_room_id,
            "room_path": list(self.room_path),
            "points": [{"x": x, "y": y} for x, y in self.points],
            "minimum_clear_width_mm": self.minimum_clear_width_mm,
        }


@dataclass(frozen=True)
class FlowRoutingResult:
    routes: tuple[FlowRoute, ...]

    def to_dict(self) -> dict[str, object]:
        return {"routes": [route.to_dict() for route in self.routes]}


@dataclass(frozen=True)
class FlowValidationIssue:
    code: str
    message: str
    flow_id: str | None = None

    def to_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {"code": self.code, "message": self.message}
        if self.flow_id is not None:
            payload["flow_id"] = self.flow_id
        return payload


@dataclass(frozen=True)
class FlowValidationReport:
    issues: tuple[FlowValidationIssue, ...]

    @property
    def ok(self) -> bool:
        return not self.issues

    def to_dict(self) -> dict[str, object]:
        return {"ok": self.ok, "issues": [issue.to_dict() for issue in self.issues]}


def _route_label(route: FlowRoute) -> str:
    """Name a route by its own endpoints, not just by its flow.

    A flow can declare several sources or targets, so ``flow_id`` alone does not
    identify which route a finding belongs to.
    """

    return f"{route.flow_id} ({route.from_id} → {route.to_id})"


@dataclass(frozen=True)
class _Endpoint:
    source_id: str
    room_id: str
    point: tuple[float, float] | None = None


def route_flows(
    building: BuildingIR,
    layout: LayoutResult,
    equipment: EquipmentLayoutResult | None = None,
) -> FlowRoutingResult:
    """Build stable room-graph routes from flow endpoints.

    Coordinates are derived only from room placements and generated door
    openings. A room or zone endpoint is represented at its nearest portal;
    equipment endpoints use the placed equipment centre.
    """

    wall_plan = build_wall_plan(building.layout, layout)
    graph = _door_graph(building, layout, wall_plan)
    equipment_by_id = {item.equipment_id: item for item in (equipment.placements if equipment else ())}
    routes: list[FlowRoute] = []

    for flow in building.flows:
        sources = _resolve_endpoints(building, flow.from_ids, layout, equipment_by_id)
        targets = _resolve_endpoints(building, flow.to_ids, layout, equipment_by_id)
        for source in sources:
            for target in targets:
                room_path, openings = _shortest_room_path(source.room_id, target.room_id, graph)
                if room_path is None or openings is None:
                    continue
                points = _route_points(
                    room_path,
                    openings,
                    source,
                    target,
                    layout,
                    flow.minimum_clear_width_mm,
                    building,
                    equipment,
                )
                routes.append(
                    FlowRoute(
                        flow_id=flow.id,
                        from_id=source.source_id,
                        to_id=target.source_id,
                        from_room_id=source.room_id,
                        to_room_id=target.room_id,
                        room_path=room_path,
                        points=points,
                        minimum_clear_width_mm=flow.minimum_clear_width_mm,
                    )
                )
    return FlowRoutingResult(tuple(routes))


def validate_flow_routes(
    building: BuildingIR,
    layout: LayoutResult,
    routes: FlowRoutingResult,
    equipment: EquipmentLayoutResult | None = None,
) -> FlowValidationReport:
    """Independently check route completeness, doors, walls and equipment."""

    issues: list[FlowValidationIssue] = []
    wall_plan = build_wall_plan(building.layout, layout)
    graph = _door_graph(building, layout, wall_plan)
    equipment_by_id = {item.equipment_id: item for item in (equipment.placements if equipment else ())}
    equipment_specs = {item.id: item for item in building.equipment}
    flow_by_id = {flow.id: flow for flow in building.flows}
    expected: dict[tuple[str, str, str, str, str], FlowSpec] = {}

    for flow in building.flows:
        sources = _resolve_endpoints(building, flow.from_ids, layout, equipment_by_id)
        targets = _resolve_endpoints(building, flow.to_ids, layout, equipment_by_id)
        if not sources:
            issues.append(FlowValidationIssue("endpoint_unplaced", f"Flow {flow.id} has no placed source endpoint", flow.id))
        if not targets:
            issues.append(FlowValidationIssue("endpoint_unplaced", f"Flow {flow.id} has no placed target endpoint", flow.id))
        for source in sources:
            for target in targets:
                expected[(flow.id, source.source_id, target.source_id, source.room_id, target.room_id)] = flow

    seen: set[tuple[str, str, str, str, str]] = set()
    for route in routes.routes:
        key = (route.flow_id, route.from_id, route.to_id, route.from_room_id, route.to_room_id)
        label = _route_label(route)
        flow = flow_by_id.get(route.flow_id)
        if flow is None:
            issues.append(FlowValidationIssue("unknown_flow", f"Route references unknown flow {route.flow_id}", route.flow_id))
            continue
        if key in seen:
            issues.append(FlowValidationIssue("duplicate_route", f"Flow route {label} is duplicated", route.flow_id))
        seen.add(key)
        if key not in expected:
            issues.append(FlowValidationIssue("unexpected_route", f"Route {label} does not match declared endpoints", route.flow_id))
        if len(route.points) < 2:
            issues.append(FlowValidationIssue("degenerate_route", f"Flow {label} has fewer than two points", route.flow_id))
            continue
        if route.minimum_clear_width_mm + 1e-6 < flow.minimum_clear_width_mm:
            issues.append(FlowValidationIssue("route_width_mismatch", f"Flow {label} route width is below its declared minimum", route.flow_id))

        route_line = LineString(route.points)
        corridor = route_line.buffer(route.minimum_clear_width_mm / 2, cap_style=2, join_style=3)
        for room_a, room_b in zip(route.room_path, route.room_path[1:]):
            opening = _graph_opening(graph, room_a, room_b)
            if opening is None:
                issues.append(FlowValidationIssue("route_missing_door", f"Flow {label} crosses {room_a}–{room_b} without a generated door", route.flow_id))
            elif opening.width + 1e-6 < flow.minimum_clear_width_mm:
                issues.append(
                    FlowValidationIssue(
                        "opening_too_narrow",
                        f"Flow {label} needs {flow.minimum_clear_width_mm:.0f} mm but {room_a}–{room_b} opening is {opening.width:.0f} mm",
                        route.flow_id,
                    )
                )
        if wall_plan.geometry.intersection(corridor).area > 1e-6:
            issues.append(FlowValidationIssue("wall_collision", f"Flow {label} corridor intersects wall geometry", route.flow_id))

        for placement in equipment.placements if equipment else ():
            if placement.equipment_id in {route.from_id, route.to_id}:
                continue
            if placement.room_id not in route.room_path:
                continue
            spec = equipment_specs.get(placement.equipment_id)
            if spec is None:
                continue
            clearance = clearance_rect(spec, placement.rect, placement.rotated)
            if corridor.intersection(box(clearance.x, clearance.y, clearance.right, clearance.top)).area > 1e-6:
                issues.append(
                    FlowValidationIssue(
                        "equipment_clearance_collision",
                        f"Flow {label} corridor intersects clearance of {placement.equipment_id}",
                        route.flow_id,
                    )
                )

    for key, flow in expected.items():
        if flow.required and key not in seen:
            issues.append(
                FlowValidationIssue(
                    "no_route",
                    f"Required flow {flow.id} has no route from {key[1]} to {key[2]} ({key[3]} → {key[4]})",
                    flow.id,
                )
            )
    return FlowValidationReport(tuple(issues))


def _resolve_endpoints(
    building: BuildingIR,
    endpoint_ids: Iterable[str],
    layout: LayoutResult,
    equipment_by_id: dict[str, EquipmentPlacement],
) -> tuple[_Endpoint, ...]:
    room_ids = {room.id for room in building.layout.rooms}
    zone_ids = {zone.id for zone in building.zones}
    result: list[_Endpoint] = []
    for endpoint_id in endpoint_ids:
        if endpoint_id in room_ids:
            result.append(_Endpoint(endpoint_id, endpoint_id))
        elif endpoint_id in equipment_by_id:
            placement = equipment_by_id[endpoint_id]
            spec = next(
                (item for item in building.equipment if item.id == endpoint_id),
                None,
            )
            point = _equipment_access_point(spec, placement) if spec is not None else placement.rect.center
            result.append(_Endpoint(endpoint_id, placement.room_id, point))
        elif endpoint_id in zone_ids:
            zone_set = _descendant_zones(building, endpoint_id)
            result.extend(
                _Endpoint(endpoint_id, room.id)
                for room in building.layout.rooms
                if room.id in layout.placements and any(room.id in zone.room_ids for zone in building.zones if zone.id in zone_set)
            )
    return tuple(result)


def _equipment_access_point(spec, placement: EquipmentPlacement) -> tuple[float, float]:
    """Use the declared front edge as the process-flow connection point."""

    rect = placement.rect
    if placement.rotated:
        # A 90-degree turn maps local front to the global left edge.
        return (rect.x, rect.y + rect.height / 2)
    return (rect.x + rect.width / 2, rect.top)


def _descendant_zones(building: BuildingIR, root_id: str) -> set[str]:
    zone_ids = {root_id}
    changed = True
    while changed:
        changed = False
        for zone in building.zones:
            if zone.parent_zone_id in zone_ids and zone.id not in zone_ids:
                zone_ids.add(zone.id)
                changed = True
    return zone_ids


def _door_graph(building: BuildingIR, layout: LayoutResult, wall_plan: WallPlan):
    room_ids = {room.id for room in building.layout.rooms}
    graph: dict[str, list[tuple[str, DoorOpening]]] = {room_id: [] for room_id in room_ids if room_id in layout.placements}
    for opening in wall_plan.openings:
        if opening.external or opening.room_a not in graph or opening.room_b not in graph:
            continue
        graph[opening.room_a].append((opening.room_b, opening))
        graph[opening.room_b].append((opening.room_a, opening))
    for room_id in graph:
        graph[room_id].sort(key=lambda item: (item[0], item[1].id))
    return graph


def _shortest_room_path(start: str, target: str, graph):
    if start not in graph or target not in graph:
        return None, None
    if start == target:
        return (start,), ()
    queue: deque[str] = deque([start])
    previous: dict[str, tuple[str, DoorOpening]] = {}
    visited = {start}
    while queue:
        current = queue.popleft()
        for neighbor, opening in graph.get(current, ()):
            if neighbor in visited:
                continue
            visited.add(neighbor)
            previous[neighbor] = (current, opening)
            if neighbor == target:
                queue.clear()
                break
            queue.append(neighbor)
    if target not in previous:
        return None, None
    rooms = [target]
    openings: list[DoorOpening] = []
    current = target
    while current != start:
        parent, opening = previous[current]
        rooms.append(parent)
        openings.append(opening)
        current = parent
    rooms.reverse()
    openings.reverse()
    return tuple(rooms), tuple(openings)


def _route_points(
    room_path: tuple[str, ...],
    openings: tuple[DoorOpening, ...],
    source: _Endpoint,
    target: _Endpoint,
    layout: LayoutResult,
    minimum_clear_width_mm: float,
    building: BuildingIR,
    equipment: EquipmentLayoutResult | None,
) -> tuple[tuple[float, float], ...]:
    if len(room_path) == 1:
        start = source.point or layout.placements[room_path[0]].center
        end = target.point or layout.placements[room_path[0]].center
        return _deduplicate_points((start, end))

    portal_offset = max(
        500.0,
        minimum_clear_width_mm / 2 + building.layout.wall_thickness_mm / 2 + 50.0,
    )
    points: list[tuple[float, float]] = []
    for index, room_id in enumerate(room_path):
        room_points: list[tuple[float, float]] = []
        if index == 0:
            room_points.append(source.point or _inward_point(layout.placements[room_id], openings[0], portal_offset))
        else:
            # Start inside the room rather than at the wall centreline. A
            # swept flow corridor is wider than the centreline opening; using
            # the portal itself as a diagonal routing node makes its buffer
            # leak into the wall beside the door.
            room_points.append(_inward_point(layout.placements[room_id], openings[index - 1], portal_offset))
        if index < len(room_path) - 1:
            room_points.append(_inward_point(layout.placements[room_id], openings[index], portal_offset))
        else:
            room_points.append(target.point or _inward_point(layout.placements[room_id], openings[index - 1], portal_offset))
        room_obstacles = _room_clearance_obstacles(
            room_id,
            building,
            equipment,
            excluded={source.source_id, target.source_id},
        )
        room_route = _route_around_obstacles(
            room_points[0],
            room_points[1],
            layout.placements[room_id],
            room_obstacles,
            minimum_clear_width_mm,
        )
        points.extend(room_route)
        if index < len(room_path) - 1:
            points.append(openings[index].center)
    return _deduplicate_points(points)


def _inward_point(room: Rect, opening: DoorOpening, offset: float) -> tuple[float, float]:
    x, y = opening.center
    if opening.orientation == "vertical":
        distance = min(offset, room.width / 2)
        x += distance if abs(opening.fixed - room.x) <= 1e-6 else -distance
    else:
        distance = min(offset, room.height / 2)
        y += distance if abs(opening.fixed - room.y) <= 1e-6 else -distance
    return (x, y)


def _graph_opening(graph, room_a: str, room_b: str) -> DoorOpening | None:
    for neighbor, opening in graph.get(room_a, ()):
        if neighbor == room_b:
            return opening
    return None


def _room_clearance_obstacles(
    room_id: str,
    building: BuildingIR,
    equipment: EquipmentLayoutResult | None,
    *,
    excluded: set[str],
) -> tuple[Rect, ...]:
    if equipment is None:
        return ()
    specs = {item.id: item for item in building.equipment}
    return tuple(
        clearance_rect(specs[placement.equipment_id], placement.rect, placement.rotated)
        for placement in equipment.placements
        if placement.room_id == room_id
        and placement.equipment_id not in excluded
        and placement.equipment_id in specs
    )


def _route_around_obstacles(
    start: tuple[float, float],
    end: tuple[float, float],
    room: Rect,
    obstacles: tuple[Rect, ...],
    width_mm: float,
) -> tuple[tuple[float, float], ...]:
    """Find a shortest visible polyline whose swept corridor avoids obstacles."""

    if not obstacles or _segment_is_clear(start, end, obstacles, width_mm):
        return (start, end)

    half_width = width_mm / 2
    nodes: list[tuple[float, float]] = [start, end]
    for obstacle in obstacles:
        # Keep a small numerical/design buffer beyond the exact swept width;
        # a tangent route otherwise still intersects the obstacle after GEOS
        # computes joins and floating-point coordinates.
        margin = half_width + 10.0
        expanded = Rect(
            obstacle.x - margin,
            obstacle.y - margin,
            obstacle.width + 2 * margin,
            obstacle.height + 2 * margin,
        )
        for candidate in (
            (expanded.x, expanded.y),
            (expanded.right, expanded.y),
            (expanded.right, expanded.top),
            (expanded.x, expanded.top),
        ):
            if (
                room.x + half_width <= candidate[0] <= room.right - half_width
                and room.y + half_width <= candidate[1] <= room.top - half_width
            ):
                nodes.append(candidate)
    nodes = list(dict.fromkeys(nodes))
    if len(nodes) <= 2:
        return (start, end)

    edges: dict[int, list[tuple[int, float]]] = {index: [] for index in range(len(nodes))}
    for left in range(len(nodes)):
        for right in range(left + 1, len(nodes)):
            if not _segment_is_clear(nodes[left], nodes[right], obstacles, width_mm):
                continue
            distance = math.dist(nodes[left], nodes[right])
            edges[left].append((right, distance))
            edges[right].append((left, distance))

    best: dict[int, tuple[float, tuple[int, ...]]] = {0: (0.0, (0,))}
    pending = {0}
    while pending:
        current = min(pending, key=lambda index: (best[index][0], best[index][1]))
        pending.remove(current)
        if current == 1:
            break
        distance, path = best[current]
        for neighbor, edge_distance in sorted(edges[current]):
            candidate = (distance + edge_distance, path + (neighbor,))
            if neighbor not in best or candidate < best[neighbor]:
                best[neighbor] = candidate
                pending.add(neighbor)
    if 1 not in best:
        return (start, end)
    return tuple(nodes[index] for index in best[1][1])


def _segment_is_clear(
    start: tuple[float, float],
    end: tuple[float, float],
    obstacles: tuple[Rect, ...],
    width_mm: float,
) -> bool:
    if start == end:
        return True
    corridor = LineString([start, end]).buffer(width_mm / 2, cap_style=2, join_style=3)
    return all(
        corridor.intersection(box(obstacle.x, obstacle.y, obstacle.right, obstacle.top)).area <= 1e-6
        for obstacle in obstacles
    )


def _deduplicate_points(points: Iterable[tuple[float, float]]) -> tuple[tuple[float, float], ...]:
    result: list[tuple[float, float]] = []
    for point in points:
        if not result or point != result[-1]:
            result.append((float(point[0]), float(point[1])))
    return tuple(result)

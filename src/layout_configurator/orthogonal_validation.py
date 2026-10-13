"""Independent predicates for canonical orthogonal rooms and equipment.

axis_aligned_coverage_v1 is a project geometry contract derived from source
RoomSpec.min_width_mm/min_depth_mm/rotatable, not a regulatory room-width rule.
Every footprint point must belong to a contained rectangle of the selected
minimum dimensions. A rotatable room selects one orientation for the whole
footprint. Wall-half mitre insets and actual generated doors are checked
separately; equipment service envelopes retain their source directional sizes.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import math

from shapely.geometry import LineString, box
from shapely.ops import unary_union

from .building import BuildingIR
from .equipment import EquipmentLayoutResult, EquipmentValidationIssue, clearance_rect
from .models import LayoutIR, LayoutResult, Rect, RoomSpec
from .orthogonal import OrthogonalPolygon
from .orthogonal_projection import ProjectionCapabilityError, build_orthogonal_wall_plan
from .orthogonal_window_capability import validate_orthogonal_window_capability
from .result_geometry import GeometryCheck, parse_layout_result, validate_result_geometry
from .schema import normalize_building_mapping, normalize_mapping


_POLICY = "axis_aligned_coverage_v1"


@dataclass(frozen=True)
class OrthogonalDimensionPolicy:
    max_scan_steps: int = 100_000
    max_candidates: int = 10_000

    def __post_init__(self):
        for name, value in (("max_scan_steps", self.max_scan_steps), ("max_candidates", self.max_candidates)):
            if not isinstance(value, int) or isinstance(value, bool) or not 1 <= value <= 1_000_000:
                raise ValueError(f"{name} requires an integer between 1 and 1000000")

    def to_dict(self):
        return {"name": _POLICY,
                "source": "Project geometry contract from RoomSpec min_width_mm/min_depth_mm/rotatable; not a regulatory standard",
                "orientation": "one global orientation, swapped only when source rotatable=true",
                "coverage": "every footprint point covered by a contained minimum-size axis-aligned rectangle",
                "usable_connectivity": "one nonempty component after wall-half mitre inset",
                "door_approaches": "full opening span reaches the inset one millimetre past the wall face",
                "max_scan_steps_per_orientation": self.max_scan_steps,
                "max_candidates_per_orientation": self.max_candidates}


@dataclass(frozen=True)
class MinimumDimensionEvidence:
    room_id: str
    status: str
    selected_dimensions_mm: tuple[float, float] | None
    uncovered_area_mm2: float | None
    scan_steps: int
    candidate_count: int
    message: str
    policy: str = _POLICY

    def to_dict(self):
        return {"room_id": self.room_id, "status": self.status, "policy": self.policy,
                "selected_dimensions_mm": list(self.selected_dimensions_mm) if self.selected_dimensions_mm else None,
                "uncovered_area_mm2": self.uncovered_area_mm2, "scan_steps": self.scan_steps,
                "candidate_count": self.candidate_count, "message": self.message}


@dataclass(frozen=True)
class OrthogonalValidationReport:
    checks: tuple[GeometryCheck, ...]
    room_dimensions: tuple[MinimumDimensionEvidence, ...]
    policy: OrthogonalDimensionPolicy
    window_capability: dict | None = None

    @property
    def status(self):
        return _status(check.status for check in self.checks)

    @property
    def ok(self):
        return self.status == "PASS"

    def to_dict(self):
        return {"status": self.status, "ok": self.ok, "policy": self.policy.to_dict(),
                "scope": "Source room geometry, declared minima and actual door connectivity only; not engineering sign-off, equipment or process-route acceptance",
                "checks": [_check_dict(check) for check in self.checks],
                "room_dimensions": [room.to_dict() for room in self.room_dimensions],
                **({"window_capability": self.window_capability} if self.window_capability is not None else {})}


@dataclass(frozen=True)
class OrthogonalEquipmentReport:
    checks: tuple[GeometryCheck, ...]
    issues: tuple[EquipmentValidationIssue, ...]
    window_capability: dict | None = None

    @property
    def status(self):
        return "FAIL" if self.issues else _status(check.status for check in self.checks)

    @property
    def ok(self):
        return self.status == "PASS"

    def to_dict(self):
        return {"status": self.status, "ok": self.ok,
                "scope": "Supplied equipment footprint, directional service envelope, host and wall geometry only; not room topology, process-route or engineering sign-off",
                "checks": [_check_dict(check) for check in self.checks],
                "issues": [issue.to_dict() for issue in self.issues],
                **({"window_capability": self.window_capability} if self.window_capability is not None else {})}


def check_room_minimum_dimensions(room: RoomSpec, footprint: Rect | OrthogonalPolygon, *,
                                  policy: OrthogonalDimensionPolicy = OrthogonalDimensionPolicy()) -> MinimumDimensionEvidence:
    """Certify complete actual-footprint coverage, without bbox substitution.

    A maximal contained rectangle can be extended to vertex y-levels. For
    each level pair, intersect the exact slab x-intervals; qualifying rectangles
    therefore cover every possible placement of a minimum-size rectangle.
    Work and retained rectangles are bounded. Exhaustion is UNKNOWN, never an
    infeasibility conclusion. Rect uses the same source .fits tolerance.
    """
    for value in (room.min_width_mm, room.min_depth_mm):
        _positive(value, "source minimum room dimension")
    if not isinstance(room.rotatable, bool):
        raise ValueError("Source room rotatable must be boolean")
    shape = _footprint(footprint)
    orientations = [(room.min_width_mm, room.min_depth_mm)]
    if room.rotatable and room.min_width_mm != room.min_depth_mm:
        orientations.append((room.min_depth_mm, room.min_width_mm))
    if isinstance(footprint, Rect):
        selected = next((pair for pair in orientations if footprint.width + 1e-6 >= pair[0] and footprint.height + 1e-6 >= pair[1]), None)
        return MinimumDimensionEvidence(room.id, "PASS" if selected else "FAIL", selected,
                                        0 if selected else shape.area_mm2, 0, int(selected is not None),
                                        "Legacy rectangle dimension predicate passed" if selected else "Rectangle does not meet source minimum dimensions")
    partitions = shape.decompose()
    levels = sorted({y for _, y in shape.vertices})
    slabs = [tuple((rect.x, rect.right) for rect in sorted(partitions, key=lambda item: item.x)
                   if rect.y <= bottom and rect.top >= top)
             for bottom, top in zip(levels, levels[1:])]
    results, total_steps, total_count = [], 0, 0
    for width, depth in orientations:
        rectangles, steps, exhausted = _covering_rectangles(levels, slabs, width, depth, policy)
        total_steps += steps
        total_count += len(rectangles)
        if exhausted:
            results.append(None)
            continue
        coverage = unary_union([box(rect.x, rect.y, rect.right, rect.top) for rect in rectangles])
        uncovered = shape.geometry.difference(coverage).area
        if uncovered <= max(1e-6, shape.area_mm2 * 1e-12):
            return MinimumDimensionEvidence(room.id, "PASS", (width, depth), 0, total_steps, total_count,
                                            "Entire actual footprint covered by contained minimum-size rectangles")
        results.append(uncovered)
    if None in results:
        return MinimumDimensionEvidence(room.id, "UNKNOWN", None, None, total_steps, total_count,
                                        "Minimum-dimension search limit exhausted before every allowed orientation was evaluated")
    return MinimumDimensionEvidence(room.id, "FAIL", None, min(results), total_steps, total_count,
                                    "Actual footprint has area that no contained source-minimum rectangle can cover")


def validate_orthogonal_layout(spec: LayoutIR, result: LayoutResult, *,
                               dimension_policy: OrthogonalDimensionPolicy = OrthogonalDimensionPolicy(),
                               include_windows: bool = False) -> OrthogonalValidationReport:
    """Evaluate exact room geometry, project minima, and actual door graph."""
    window_options = _window_options(include_windows)
    result = _canonical_layout(spec, result)
    geometry = validate_result_geometry(spec, result)
    checks = list(geometry.checks[:4])
    capability = _window_check(spec, result, checks) if include_windows else None
    rooms = {room_id: _footprint(shape) for room_id, shape in result.placements.items()}
    minimums = tuple(check_room_minimum_dimensions(room, result.placements[room.id], policy=dimension_policy)
                     for room in sorted(spec.rooms, key=lambda item: item.id) if room.id in rooms)
    missing = set(spec.room_by_id) - set(rooms)
    minimum_status = _status([room.status for room in minimums] + (["UNKNOWN"] if missing else []))
    checks.append(GeometryCheck("MIN_DIMENSION", minimum_status, tuple(f"{item.room_id}: {item.message}" for item in minimums)))
    components, disconnected = {}, []
    for room_id in sorted(set(rooms) & set(spec.room_by_id)):
        components[room_id] = rooms[room_id].inset_components(spec.wall_thickness_mm / 2)
        if len(components[room_id]) != 1:
            disconnected.append(f"{room_id}: {len(components[room_id])} usable components after wall-half inset")
    checks.append(GeometryCheck("ROOM_USABLE_CONNECTIVITY", "FAIL" if disconnected else "UNKNOWN" if missing else "PASS",
                                tuple(disconnected) or ("Each declared room has one nonempty usable inset component",)))
    try:
        wall_plan = build_orthogonal_wall_plan(spec, result, **window_options)
    except ProjectionCapabilityError as exc:
        checks.extend(GeometryCheck(key, "UNKNOWN", (str(exc),)) for key in ("OPENINGS", "DOOR_APPROACHES", "TOPOLOGY"))
    except ValueError as exc:
        checks.append(GeometryCheck("OPENINGS", "FAIL", (str(exc),)))
        checks.extend(GeometryCheck(key, "UNKNOWN", ("Valid actual opening geometry unavailable",)) for key in ("DOOR_APPROACHES", "TOPOLOGY"))
    else:
        checks.append(GeometryCheck("OPENINGS", "PASS", ("Source doors fit actual single contiguous shared/exterior segments",)))
        graph = {room.id: set() for room in spec.rooms}
        approach_failures = []
        for opening in wall_plan.openings:
            hosts = (opening.room_a,) if opening.external else (opening.room_a, opening.room_b)
            valid = True
            for host in hosts:
                if not _door_reaches_usable_room(opening, components.get(host, ()), spec.wall_thickness_mm / 2):
                    approach_failures.append(f"{opening.id}/{host}: full door span cannot reach usable room area beyond wall face")
                    valid = False
            if valid and not opening.external:
                graph[opening.room_a].add(opening.room_b)
                graph[opening.room_b].add(opening.room_a)
        checks.append(GeometryCheck("DOOR_APPROACHES", "FAIL" if approach_failures else "PASS",
                                    tuple(approach_failures) or ("Full source opening widths reach actual usable inset components",)))
        reached, pending = {spec.entry_room}, [spec.entry_room]
        while pending:
            current = pending.pop()
            for neighbor in sorted(graph[current] - reached):
                reached.add(neighbor)
                pending.append(neighbor)
        unreachable = sorted(set(graph) - reached)
        checks.append(GeometryCheck("TOPOLOGY", "FAIL" if unreachable else "PASS",
                                    tuple(f"{room_id}: unreachable from declared entry through actual usable doors" for room_id in unreachable)
                                    or ("All rooms reached through actual usable doors from declared entry room",)))
    return OrthogonalValidationReport(tuple(checks), minimums, dimension_policy, capability)


def validate_orthogonal_equipment(building: BuildingIR, layout: LayoutResult,
                                  equipment: EquipmentLayoutResult, *,
                                  include_windows: bool = False) -> OrthogonalEquipmentReport:
    """Check supplied equipment against actual mitre insets and wall bands.

    No equipment placement is performed. An anchor uses its unique actual
    outward-facing inset edge, preserving Rect offset semantics; multiple
    candidate edges require a future explicit selector and stay UNKNOWN.
    """
    window_options = _window_options(include_windows)
    _finite_json(building.to_dict())
    normalize_building_mapping(building.to_dict())
    layout = _canonical_layout(building.layout, layout)
    source_geometry = validate_result_geometry(building.layout, layout)
    checks = [GeometryCheck("SOURCE_ROOM_GEOMETRY", _status(check.status for check in source_geometry.checks[:4]),
                            tuple(message for check in source_geometry.checks[:4] if check.status != "PASS" for message in check.evidence)
                            or ("Exact source room identity/area/boundary/overlap evaluated",))]
    capability = _window_check(building.layout, layout, checks) if include_windows else None
    specs, seen, valid, issues = {item.id: item for item in building.equipment}, set(), [], []
    components = {room_id: _footprint(shape).inset_components(building.layout.wall_thickness_mm / 2)
                  for room_id, shape in layout.placements.items()}
    unknown_anchors = []
    try:
        walls = build_orthogonal_wall_plan(building.layout, layout, **window_options).geometry
    except ProjectionCapabilityError as exc:
        walls = None
        checks.append(GeometryCheck("WALL_COLLISIONS", "UNKNOWN", (str(exc),)))
    except ValueError as exc:
        walls = None
        checks.append(GeometryCheck("WALL_COLLISIONS", "FAIL", (str(exc),)))
    for placement in equipment.placements:
        if not all(isinstance(value, str) and value.strip() for value in (placement.equipment_id, placement.room_id)):
            issues.append(EquipmentValidationIssue("invalid_equipment_geometry", "Equipment and host IDs require nonempty strings"))
            continue
        item = specs.get(placement.equipment_id)
        if item is None:
            issues.append(EquipmentValidationIssue("unknown_equipment", "Placement references unknown source equipment", placement.equipment_id))
            continue
        if item.id in seen:
            issues.append(EquipmentValidationIssue("duplicate_equipment", "Equipment has more than one placement", item.id))
        seen.add(item.id)
        if not isinstance(placement.rotated, bool):
            issues.append(EquipmentValidationIssue("invalid_equipment_geometry", "Equipment rotated flag must be boolean", item.id))
            continue
        try:
            physical = OrthogonalPolygon.from_rect(placement.rect).geometry
            service_rect = clearance_rect(item, placement.rect, placement.rotated)
            service = OrthogonalPolygon.from_rect(service_rect).geometry
        except ValueError as exc:
            issues.append(EquipmentValidationIssue("invalid_equipment_geometry", str(exc), item.id))
            continue
        allowed = (item.room_id,) if item.room_id is not None else building.room_ids_for_zone(item.zone_id)
        if placement.room_id not in allowed:
            issues.append(EquipmentValidationIssue("invalid_host_room", f"Equipment host not allowed by source: {placement.room_id}", item.id))
        if placement.room_id not in layout.placements:
            issues.append(EquipmentValidationIssue("missing_host_room", "Host has no canonical geometry", item.id))
        if placement.rotated and not item.rotation_allowed:
            issues.append(EquipmentValidationIssue("rotation_not_allowed", "Source forbids equipment rotation", item.id))
        width, depth = (item.depth_mm, item.width_mm) if placement.rotated else (item.width_mm, item.depth_mm)
        if not math.isclose(placement.rect.width, width, abs_tol=1e-6, rel_tol=0) or not math.isclose(placement.rect.height, depth, abs_tol=1e-6, rel_tol=0):
            issues.append(EquipmentValidationIssue("footprint_mismatch", "Footprint differs from source oriented dimensions", item.id))
        usable = components.get(placement.room_id, ())
        if not any(part.geometry.covers(physical) for part in usable):
            issues.append(EquipmentValidationIssue("equipment_outside_room", "Physical equipment leaves actual usable inset component", item.id))
        if not any(part.geometry.covers(service) for part in usable):
            issues.append(EquipmentValidationIssue("clearance_outside_room", "Service envelope leaves actual usable inset component", item.id))
        if walls is not None:
            overlap = walls.intersection(service).area
            if overlap > 1e-6:
                issues.append(EquipmentValidationIssue("wall_collision", "Service envelope intersects actual wall bands", item.id, overlap_area_mm2=overlap))
        if item.anchor_side is not None and usable:
            edges = tuple(edge for part in usable for edge in part.edges if _outward_side(edge) == item.anchor_side)
            if len(edges) != 1:
                unknown_anchors.append(f"{item.id}: anchor requires an actual-edge selector for {len(edges)} candidate {item.anchor_side} edges")
            elif not _anchor_matches(item, placement.rect, service_rect, edges[0]):
                issues.append(EquipmentValidationIssue("anchor_mismatch", "Equipment violates source offset from actual inset anchor edge", item.id))
        valid.append((placement, service))
    for equipment_id in sorted(set(specs) - seen):
        issues.append(EquipmentValidationIssue("missing_equipment", "Source equipment has no placement", equipment_id))
    for index, (first, left) in enumerate(valid):
        for second, right in valid[index + 1:]:
            overlap = left.intersection(right).area
            if overlap > 1e-6:
                issues.append(EquipmentValidationIssue("clearance_collision", "Service envelopes overlap", first.equipment_id, second.equipment_id, overlap))
    if walls is not None:
        checks.append(GeometryCheck("WALL_COLLISIONS", "FAIL" if any(issue.code == "wall_collision" for issue in issues) else "PASS",
                                    ("Actual wall/service envelope intersections evaluated",)))
    checks.append(GeometryCheck("ANCHORS", "FAIL" if any(issue.code == "anchor_mismatch" for issue in issues) else "UNKNOWN" if unknown_anchors else "PASS",
                                tuple(unknown_anchors) or ("Source anchors evaluated against actual unique inset edges",)))
    checks.append(GeometryCheck("EQUIPMENT", "FAIL" if issues else "PASS", ("Source identity/host/orientation/footprint/service/pair collision predicates evaluated",)))
    return OrthogonalEquipmentReport(tuple(checks), tuple(issues), capability)


def _window_options(include_windows):
    """Validate opt-in without changing omitted/default downstream calls."""
    if not isinstance(include_windows, bool):
        raise ValueError("include_windows requires a boolean")
    return {"include_windows": True} if include_windows else {}


def _window_check(spec, layout, checks):
    capability = validate_orthogonal_window_capability(spec, layout)
    checks.append(GeometryCheck("WINDOW_CAPABILITY", capability.status,
        tuple(message for check in capability.checks for message in check.evidence)))
    return capability.to_dict()


def _covering_rectangles(levels, slabs, width, depth, policy):
    retained, steps = {}, 0
    for start in range(len(slabs)):
        active = slabs[start]
        for end in range(start, len(slabs)):
            cost = len(active) + len(slabs[end]) + 1
            if steps + cost > policy.max_scan_steps:
                return tuple(retained.values()), steps, True
            steps += cost
            if end > start:
                active = _intersect_intervals(active, slabs[end])
            if not active:
                break
            height = levels[end + 1] - levels[start]
            if height + 1e-6 >= depth:
                for left, right in active:
                    if right - left + 1e-6 >= width:
                        key = (left, right, levels[start])
                        if key not in retained and len(retained) >= policy.max_candidates:
                            return tuple(retained.values()), steps, True
                        retained[key] = Rect(left, levels[start], right - left, height)
    return tuple(retained.values()), steps, False


def _intersect_intervals(first, second):
    result, left_index, right_index = [], 0, 0
    while left_index < len(first) and right_index < len(second):
        left, right = max(first[left_index][0], second[right_index][0]), min(first[left_index][1], second[right_index][1])
        if right > left:
            result.append((left, right))
        if first[left_index][1] <= second[right_index][1]:
            left_index += 1
        else:
            right_index += 1
    return tuple(result)


def _door_reaches_usable_room(opening, components, half_wall):
    for direction in (-1, 1):
        position = opening.fixed + direction * (half_wall + 1)
        points = ((position, opening.start), (position, opening.end)) if opening.orientation == "vertical" else ((opening.start, position), (opening.end, position))
        if any(part.geometry.covers(LineString(points)) for part in components):
            return True
    return False


def _anchor_matches(item, physical, clearance, edge):
    left, right = physical.x - clearance.x, clearance.right - physical.right
    bottom, top = physical.y - clearance.y, clearance.top - physical.top
    if item.anchor_side in ("left", "right"):
        fixed, low = edge[0][0], min(edge[0][1], edge[1][1])
        expected = (fixed + left if item.anchor_side == "left" else fixed - right - physical.width,
                    low + item.anchor_offset_mm + bottom)
    else:
        fixed, low = edge[0][1], min(edge[0][0], edge[1][0])
        expected = (low + item.anchor_offset_mm + left,
                    fixed + bottom if item.anchor_side == "bottom" else fixed - top - physical.height)
    return math.isclose(physical.x, expected[0], abs_tol=1e-6, rel_tol=0) and math.isclose(physical.y, expected[1], abs_tol=1e-6, rel_tol=0)


def _outward_side(edge):
    first, second = edge
    if first[0] == second[0]:
        return "right" if second[1] > first[1] else "left"
    return "bottom" if second[0] > first[0] else "top"


def _canonical_layout(spec, result):
    _finite_json(spec.to_dict())
    normalize_mapping(spec.to_dict())
    return parse_layout_result(result.to_dict())


def _finite_json(payload):
    try:
        json.dumps(payload, allow_nan=False)
    except (ValueError, TypeError, OverflowError) as exc:
        raise ValueError("Orthogonal check source requires finite canonical JSON") from exc


def _footprint(shape):
    if isinstance(shape, Rect):
        return OrthogonalPolygon.from_rect(shape)
    if not isinstance(shape, OrthogonalPolygon):
        raise ValueError("Room footprint must be Rect or OrthogonalPolygon")
    return shape


def _positive(value, name):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
        raise ValueError(f"{name} requires a positive finite millimetre number")


def _status(statuses):
    values = tuple(statuses)
    return "FAIL" if "FAIL" in values else "UNKNOWN" if not values or "UNKNOWN" in values else "PASS"


def _check_dict(check):
    return {"id": check.id, "status": check.status, "evidence": list(check.evidence)}

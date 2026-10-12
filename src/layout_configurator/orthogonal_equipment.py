"""Finite source-backed equipment packing inside actual orthogonal rooms.

Free positions use the source grid; anchors use unique actual inset edges and
exact source offsets. Every accepted placement passes independent B4 equipment
checks and recomputed full-width door reservations. The output remains DRAFT:
packing and local approach clearance do not establish process-route acceptance.
The complete rectangular facility pipeline and its capability guards are intact.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
import hashlib
import json
import math
import time

from shapely.geometry import box

from .building import BuildingIR
from .equipment import EquipmentLayoutResult, EquipmentPlacement, clearance_rect, flow_portal_offset_mm
from .models import LayoutResult, Rect
from .orthogonal import OrthogonalPolygon
from .orthogonal_projection import ProjectionCapabilityError, build_orthogonal_wall_plan
from .orthogonal_validation import OrthogonalDimensionPolicy, OrthogonalEquipmentReport, OrthogonalValidationReport, validate_orthogonal_equipment, validate_orthogonal_layout
from .result_geometry import parse_layout_result, validate_result_geometry
from .schema import normalize_building_mapping
from .search import configure_solver


def _integer(value, name, minimum=1, maximum=1_000_000):
    if not isinstance(value, int) or isinstance(value, bool) or not minimum <= value <= maximum:
        raise ValueError(f"{name} requires integer {minimum}..{maximum}")


@dataclass(frozen=True)
class OrthogonalPackingBudget:
    max_rooms: int = 64
    max_equipment: int = 64
    max_candidates_per_equipment: int = 128
    max_catalog_steps_per_equipment: int = 16_384
    max_pair_checks: int = 100_000
    max_attempts: int = 16
    max_rejections: int = 128
    room_policy: OrthogonalDimensionPolicy = field(default_factory=OrthogonalDimensionPolicy)

    def __post_init__(self):
        for name in ("max_rooms", "max_equipment", "max_candidates_per_equipment", "max_catalog_steps_per_equipment", "max_pair_checks", "max_attempts", "max_rejections"):
            _integer(getattr(self, name), name)
        if not isinstance(self.room_policy, OrthogonalDimensionPolicy):
            raise ValueError("room_policy requires OrthogonalDimensionPolicy")

    def to_dict(self):
        return {**{name: getattr(self, name) for name in ("max_rooms", "max_equipment", "max_candidates_per_equipment", "max_catalog_steps_per_equipment", "max_pair_checks", "max_attempts", "max_rejections")},
                "room_policy": self.room_policy.to_dict()}


@dataclass(frozen=True)
class DoorReservation:
    door_id: str
    room_id: str
    rect: Rect

    def to_dict(self):
        return {"door_id": self.door_id, "room_id": self.room_id,
                "rect": {"x": self.rect.x, "y": self.rect.y, "width": self.rect.width, "height": self.rect.height}}


@dataclass(frozen=True)
class OrthogonalDoorReport:
    reservations: tuple[DoorReservation, ...]
    issues: tuple[dict, ...] = ()
    unknown: tuple[str, ...] = ()

    @property
    def status(self):
        return "FAIL" if self.issues else "UNKNOWN" if self.unknown else "PASS"

    @property
    def ok(self):
        return self.status == "PASS"

    def to_dict(self):
        return {"status": self.status, "ok": self.ok,
                "scope": "Local actual-door approach geometry and service-envelope occupancy only; not process-route or engineering acceptance",
                "policy": {"name": "full_opening_span_v1", "source": "Project equipment.door_approach_zones and flow_portal_offset_mm; not a regulatory clearance",
                           "all_actual_openings": True, "routing_margin_mm": 10,
                           "span": "max(actual opening width, widest required flow width) plus two routing margins",
                           "depth_from_wall_line": "flow_portal_offset_mm plus required-flow half-width plus routing margin",
                           "start": "actual interior wall face; entire reservation must fit one usable component"},
                "reservations": [zone.to_dict() for zone in self.reservations], "issues": list(self.issues), "unknown": list(self.unknown)}


@dataclass(frozen=True)
class OrthogonalPackingResult:
    equipment: EquipmentLayoutResult | None
    equipment_data: dict | None
    room_report: OrthogonalValidationReport | None
    equipment_report: OrthogonalEquipmentReport | None
    door_report: OrthogonalDoorReport | None
    evidence: dict

    @property
    def status(self):
        reports = (self.room_report, self.equipment_report, self.door_report)
        return "DRAFT" if self.equipment is not None and all(report is not None and report.ok for report in reports) else "UNKNOWN"

    @property
    def ok(self):
        return False

    def to_dict(self):
        return {"status": self.status, "ok": self.ok, "backend": "orthogonal_equipment_catalog_v1",
                "scope": "Finite equipment/service packing and local reserved approaches; not a general infeasibility proof or full facility acceptance",
                "full_facility_validation": {"status": "UNKNOWN", "evidence": ["Polygon process routes, profiles, windows and complete exporters are not dispatched"]},
                "equipment_layout": self.equipment_data,
                "room_report": self.room_report.to_dict() if self.room_report else None,
                "equipment_report": self.equipment_report.to_dict() if self.equipment_report else None,
                "door_report": self.door_report.to_dict() if self.door_report else None, **self.evidence}


def validate_orthogonal_door_approaches(building: BuildingIR, layout: LayoutResult,
                                       equipment: EquipmentLayoutResult) -> OrthogonalDoorReport:
    """Recompute actual full-width approaches without clipping or bbox sides.

    Required flow width is conservatively reserved at every actual door. A
    narrow source opening fails this local predicate; no routing is inferred.
    Unlike the legacy optional keep-outs, anchors also must leave these clear.
    """
    layout = _canonical_inputs(building, layout)
    if not isinstance(equipment, EquipmentLayoutResult) or not isinstance(equipment.placements, (list, tuple)):
        raise ValueError("Door approach check requires EquipmentLayoutResult with a placement list/tuple")
    geometry = validate_result_geometry(building.layout, layout)
    if any(check.status != "PASS" for check in geometry.checks[:4]):
        return OrthogonalDoorReport((), ({"code": "source_room_geometry", "message": "Actual source room identity/area/bounds/overlap failed"},))
    try:
        openings = build_orthogonal_wall_plan(building.layout, layout).openings
    except ProjectionCapabilityError as exc:
        return OrthogonalDoorReport((), unknown=(str(exc),))
    except ValueError as exc:
        return OrthogonalDoorReport((), ({"code": "source_openings", "message": str(exc)},))
    half_wall = building.layout.wall_thickness_mm / 2
    clear_width = max((flow.minimum_clear_width_mm for flow in building.flows if flow.required), default=0)
    depth = flow_portal_offset_mm(clear_width, building.layout.wall_thickness_mm) + clear_width / 2 + 10
    components = {room_id: _polygon(shape).inset_components(half_wall) for room_id, shape in layout.placements.items()}
    reservations, issues = [], []
    for opening in openings:
        if opening.width + 1e-6 < clear_width:
            issues.append({"code": "opening_below_required_flow_width", "door_id": opening.id,
                           "message": "Actual opening is narrower than the source required flow width"})
            continue
        center, span = (opening.start + opening.end) / 2, max(opening.width, clear_width) + 20
        hosts = (opening.room_a,) if opening.external else (opening.room_a, opening.room_b)
        for host in hosts:
            candidates = []
            for direction in (-1, 1):
                low = opening.fixed - depth if direction < 0 else opening.fixed + half_wall
                rect = Rect(low, center - span / 2, depth - half_wall, span) if opening.orientation == "vertical" else Rect(center - span / 2, low, span, depth - half_wall)
                if any(part.covers_rect(rect) for part in components.get(host, ())):
                    candidates.append(rect)
            if len(candidates) != 1:
                issues.append({"code": "door_approach_geometry", "door_id": opening.id, "room_id": host,
                               "message": f"Full approach has {len(candidates)} actual usable inward directions; no clipped reservation accepted"})
            else:
                reservations.append(DoorReservation(opening.id, host, candidates[0]))
    specs = {item.id: item for item in building.equipment}
    for placement in equipment.placements:
        try:
            _placement_data(placement)
        except ValueError as exc:
            issues.append({"code": "invalid_equipment_geometry", "message": str(exc)})
            continue
        item = specs.get(placement.equipment_id)
        if item is None or placement.room_id not in layout.placements:
            issues.append({"code": "unknown_equipment_host", "equipment_id": placement.equipment_id})
            continue
        service = clearance_rect(item, placement.rect, placement.rotated)
        for zone in reservations:
            if zone.room_id == placement.room_id:
                overlap = service.intersection_area(zone.rect)
                if overlap > 1e-6:
                    issues.append({"code": "door_approach_collision", "equipment_id": item.id,
                                   "door_id": zone.door_id, "room_id": zone.room_id, "overlap_area_mm2": overlap})
    return OrthogonalDoorReport(tuple(reservations), tuple(issues))


def place_orthogonal_equipment(building: BuildingIR, layout: LayoutResult, *,
                               catalog: Mapping[str, Sequence[EquipmentPlacement]] | None = None,
                               excluded_layouts: Sequence[EquipmentLayoutResult] | None = None,
                               seed: int = 42, time_limit_seconds: float = 10,
                               deterministic_units: float | None = None,
                               budget: OrthogonalPackingBudget = OrthogonalPackingBudget()) -> OrthogonalPackingResult:
    """Select one finite real-footprint/service candidate per source item.

    Optional exclusions contain at most 128 independently valid complete
    equipment combinations for this exact room layout. They remove only the
    selected combination, preserving other packings that reuse any item.
    Empty/omitted exclusions preserve the existing output contract exactly.
    """
    from ortools.sat.python import cp_model

    layout = _canonical_inputs(building, layout)
    if not isinstance(budget, OrthogonalPackingBudget):
        raise ValueError("budget requires OrthogonalPackingBudget")
    _integer(seed, "seed", minimum=0, maximum=2**31 - 1)
    _positive(time_limit_seconds, "time_limit_seconds")
    if deterministic_units is not None:
        _positive(deterministic_units, "deterministic_units")
    source_ids = {item.id for item in building.equipment}
    if catalog is not None and (not isinstance(catalog, Mapping) or set(catalog) != source_ids):
        raise ValueError("Catalog must map every source equipment ID and no unknown IDs")
    state = {"source_sha256": _hash(building.to_dict()), "layout_sha256": _hash(layout.to_dict()),
             "catalog_sha256": None, "catalog_fingerprint_complete": False, "seed": seed,
             "catalog_fingerprint_scope": "Observed finite input patterns and retained domains; enumeration truncation is reported separately",
             "time_limit_seconds": time_limit_seconds, "deterministic_units": deterministic_units,
             "repeatable": deterministic_units is not None, "budget": budget.to_dict(),
             "grid_policy": "Free positions align to source grid; unique anchors retain exact source offsets",
             "catalog_mode": "explicit" if catalog is not None else "generated_grid_and_source_anchors",
             "catalog_truncated": False, "catalog_stats": {}, "pair_checks": 0, "attempts": 0,
             "rejection_count": 0, "rejection_counts": {}, "rejections": [], "last_rejection": None, "stop_reason": None}
    accepted, room_report, equipment_report, door_report = None, None, None, None
    deadline = time.monotonic() + time_limit_seconds if deterministic_units is None else math.inf

    def reject(stage, reason, **details):
        state["rejection_count"] += 1
        state["rejection_counts"][stage] = state["rejection_counts"].get(stage, 0) + 1
        state["last_rejection"] = {"stage": stage, "reason": reason, **details}
        if len(state["rejections"]) < budget.max_rejections:
            state["rejections"].append(state["last_rejection"])

    def finish(reason):
        state["stop_reason"] = reason
        return OrthogonalPackingResult(accepted, accepted.to_dict(building) if accepted else None,
                                       room_report, equipment_report, door_report, state)

    if len(building.layout.rooms) > budget.max_rooms or len(building.equipment) > budget.max_equipment:
        return finish("source_size_limit")
    excluded = _exclusion_data(building, layout, excluded_layouts)
    if excluded:
        state.update(excluded_layouts_count=len(excluded), excluded_layouts_sha256=_hash(excluded), matched_excluded_layouts=0)
    room_report = validate_orthogonal_layout(building.layout, layout, dimension_policy=budget.room_policy)
    if not room_report.ok:
        reject("room_qa", "; ".join(check.id for check in room_report.checks if check.status != "PASS"), status=room_report.status)
        return finish("source_room_qa_failed")
    door_report = validate_orthogonal_door_approaches(building, layout, EquipmentLayoutResult(()))
    if not door_report.ok:
        reject("door_qa", "Source full-width door reservations failed", status=door_report.status, report=door_report.to_dict())
        return finish("source_door_reservations_failed")
    components = {room_id: _polygon(shape).inset_components(building.layout.wall_thickness_mm / 2) for room_id, shape in layout.placements.items()}
    walls = build_orthogonal_wall_plan(building.layout, layout).geometry
    domains, raw = {}, {}
    for item in sorted(building.equipment, key=lambda value: (not value.fixed, value.id)):
        counter = {"steps": 0, "truncated": False}
        if catalog is not None:
            values = catalog[item.id]
            if not isinstance(values, (list, tuple)) or len(values) > budget.max_catalog_steps_per_equipment:
                raise ValueError("Explicit catalog requires bounded lists/tuples of EquipmentPlacement")
            raw[item.id] = sorted(_hash(_placement_data(placement)) for placement in values)
            stream = iter(sorted(values, key=_placement_key))
        else:
            raw[item.id] = []
            stream = _generate_placements(building, item, components, counter, budget, deadline, reject)
        retained, examined = {}, 0
        for placement in stream:
            if not _step(counter, budget, deadline):
                break
            examined += 1
            candidate_id = _hash(_placement_data(placement))
            if catalog is None:
                raw[item.id].append(candidate_id)
            failure = _candidate_failure(building, item, placement, components, walls, door_report.reservations)
            if failure:
                reject("catalog", failure, equipment_id=item.id, candidate_id=candidate_id)
                continue
            if candidate_id not in retained:
                if len(retained) >= budget.max_candidates_per_equipment:
                    counter["truncated"] = True
                    break
                retained[candidate_id] = placement
        domains[item.id] = tuple(sorted(retained.items(), key=lambda entry: _placement_key(entry[1])))
        state["catalog_stats"][item.id] = {"steps": counter["steps"], "examined": examined, "retained": len(retained), "truncated": counter["truncated"]}
        state["catalog_truncated"] |= counter["truncated"]
    state["catalog_sha256"] = _hash({"inputs": raw, "effective": {item_id: [identifier for identifier, _ in values] for item_id, values in domains.items()}})
    state["catalog_fingerprint_complete"] = True
    if time.monotonic() >= deadline:
        return finish("time_limit_during_catalog")
    if any(not values for values in domains.values()):
        return finish("no_candidate_in_finite_catalog")
    model = cp_model.CpModel()
    choices = {item_id: tuple(model.NewBoolVar(f"{item_id}_{index}") for index in range(len(values))) for item_id, values in domains.items()}
    for item_id in domains:
        model.AddExactlyOne(choices[item_id])
    for combination in excluded:
        selected = []
        for item_id, placement in combination.items():
            matches = [index for index, (_, candidate) in enumerate(domains[item_id]) if _placement_data(candidate) == placement]
            if not matches:
                break
            selected.append(choices[item_id][matches[0]])
        else:
            # Match actual canonical placements; hashes identify evidence only.
            model.Add(sum(selected) <= len(selected) - 1)
            state["matched_excluded_layouts"] += 1
    specs = {item.id: item for item in building.equipment}
    services = {item_id: tuple(clearance_rect(specs[item_id], placement.rect, placement.rotated) for _, placement in values) for item_id, values in domains.items()}
    item_ids = list(domains)
    for index, first in enumerate(item_ids):
        for second in item_ids[index + 1:]:
            for left_index, left in enumerate(services[first]):
                for right_index, right in enumerate(services[second]):
                    if state["pair_checks"] >= budget.max_pair_checks or time.monotonic() >= deadline:
                        return finish("pair_check_limit" if state["pair_checks"] >= budget.max_pair_checks else "time_limit_during_pairs")
                    state["pair_checks"] += 1
                    if left.intersection_area(right) > 1e-6:
                        model.Add(choices[first][left_index] + choices[second][right_index] <= 1)
                        reject("pair", "service_overlap", equipment_ids=[first, second])
    terms, coefficient_bound = [], 0
    for priority, (item_id, values) in enumerate(domains.items()):
        coefficients = [(len(item_ids) - priority) * (round((placement.rect.x + placement.rect.y) / building.layout.grid_mm) * (budget.max_candidates_per_equipment + 1) + index)
                        for index, (_, placement) in enumerate(values)]
        coefficient_bound += max(coefficients)
        terms.extend(choice * coefficient for choice, coefficient in zip(choices[item_id], coefficients))
    if coefficient_bound >= 2**62:
        return finish("objective_numeric_limit")
    model.Minimize(sum(terms))
    remaining_units = deterministic_units
    while state["attempts"] < budget.max_attempts:
        remaining_seconds = deadline - time.monotonic()
        if remaining_seconds <= 0 or remaining_units is not None and remaining_units <= 0:
            return finish("search_budget_exhausted")
        solver = cp_model.CpSolver()
        configure_solver(solver, seed=seed, time_limit_seconds=min(time_limit_seconds, remaining_seconds), deterministic_units=remaining_units)
        state["attempts"] += 1
        status = solver.Solve(model)
        if remaining_units is not None:
            remaining_units -= solver.ResponseProto().deterministic_time
        if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            return finish("finite_catalog_exhausted" if status == cp_model.INFEASIBLE else "solver_model_invalid" if status == cp_model.MODEL_INVALID else "search_budget_exhausted")
        selected = {item_id: next(index for index, choice in enumerate(choices[item_id]) if solver.Value(choice)) for item_id in item_ids}
        candidate = EquipmentLayoutResult(tuple(domains[item_id][index][1] for item_id, index in selected.items()))
        equipment_report = validate_orthogonal_equipment(building, layout, candidate)
        door_report = validate_orthogonal_door_approaches(building, layout, candidate)
        if equipment_report.ok and door_report.ok:
            accepted = candidate
            return finish("independent_equipment_and_approaches_verified")
        reject("equipment_qa", "Independent equipment or recomputed door approach check failed",
               equipment_report=equipment_report.to_dict(), door_report=door_report.to_dict())
        model.Add(sum(choices[item_id][index] for item_id, index in selected.items()) <= len(item_ids) - 1)
    return finish("attempt_limit")


def _generate_placements(building, item, components, counter, budget, deadline, reject):
    streams = []
    for host in _hosts(building, item):
        for rotated in ((False, True) if item.rotation_allowed else (False,)):
            if not _step(counter, budget, deadline):
                return
            width, height = (item.depth_mm, item.width_mm) if rotated else (item.width_mm, item.depth_mm)
            service = clearance_rect(item, Rect(0, 0, width, height), rotated)
            if item.anchor_side is not None:
                position = _anchor_position(item, components[host], width, height, service)
                if position is None:
                    reject("catalog", "anchor_selector_required", equipment_id=item.id, room_id=host)
                    continue
                streams.append(iter((EquipmentPlacement(item.id, host, Rect(*position, width, height), rotated),)))
            else:
                for component in components[host]:
                    streams.append(_grid_positions(item.id, host, rotated, width, height, service, component,
                                                   building.layout.grid_mm, counter, budget, deadline))
    # Alternate eligible host/orientation streams so one orientation's many
    # translations cannot consume the whole finite retained domain first.
    yield from _round_robin(streams)


def _round_robin(streams):
    while streams:
        pending = []
        for stream in streams:
            placement = next(stream, None)
            if placement is not None:
                yield placement
                pending.append(stream)
        streams = pending


def _grid_positions(item_id, host, rotated, width, height, service, component, grid, counter, budget, deadline):
    # Exact slab windows expose different arms before empty bbox grid cells
    # can crowd out an orientation. Full bounds remain a separate stream:
    # valid service rectangles may span several adjacent decomposition slabs.
    streams = []
    for window in (*component.decompose(), component.bounds_rect):
        if not _step(counter, budget, deadline):
            return
        streams.append(_window_positions(service, window, grid, counter, budget, deadline))
    seen = set()
    for x, y in _round_robin(streams):
        if (x, y) not in seen:
            seen.add((x, y))
            yield EquipmentPlacement(item_id, host, Rect(x, y, width, height), rotated)


def _window_positions(service, bounds, grid, counter, budget, deadline):
    left, right = math.ceil((bounds.x - service.x) / grid - 1e-9), math.floor((bounds.right - service.right) / grid + 1e-9)
    bottom, top = math.ceil((bounds.y - service.y) / grid - 1e-9), math.floor((bounds.top - service.top) / grid + 1e-9)
    for y in range(bottom, top + 1):
        for x in range(left, right + 1):
            if not _step(counter, budget, deadline):
                return
            yield x * grid, y * grid


def _candidate_failure(building, item, placement, components, walls, reservations):
    if placement.equipment_id != item.id:
        return "equipment_identity_mismatch"
    if placement.room_id not in _hosts(building, item):
        return "invalid_host_room"
    if placement.rotated and not item.rotation_allowed:
        return "rotation_not_allowed"
    width, height = (item.depth_mm, item.width_mm) if placement.rotated else (item.width_mm, item.depth_mm)
    physical = placement.rect
    if not math.isclose(physical.width, width, abs_tol=1e-6, rel_tol=0) or not math.isclose(physical.height, height, abs_tol=1e-6, rel_tol=0):
        return "footprint_mismatch"
    if item.anchor_side is None and any(abs(value / building.layout.grid_mm - round(value / building.layout.grid_mm)) > 1e-7 for value in (physical.x, physical.y)):
        return "grid_mismatch"
    usable = components[placement.room_id]
    service = clearance_rect(item, physical, placement.rotated)
    if not any(part.covers_rect(physical) for part in usable):
        return "equipment_outside_room"
    if not any(part.covers_rect(service) for part in usable):
        return "clearance_outside_room"
    if walls.intersection(box(service.x, service.y, service.right, service.top)).area > 1e-6:
        return "wall_collision"
    if item.anchor_side is not None:
        expected = _anchor_position(item, usable, width, height, clearance_rect(item, Rect(0, 0, width, height), placement.rotated))
        if expected is None:
            return "anchor_selector_required"
        if any(not math.isclose(actual, wanted, abs_tol=1e-6, rel_tol=0) for actual, wanted in zip((physical.x, physical.y), expected)):
            return "anchor_mismatch"
    if any(zone.room_id == placement.room_id and service.intersection_area(zone.rect) > 1e-6 for zone in reservations):
        return "door_approach_collision"
    return None


def _anchor_position(item, components, width, height, service):
    edges = [edge for part in components for edge in part.edges if _outward_side(edge) == item.anchor_side]
    if len(edges) != 1:
        return None
    edge = edges[0]
    if item.anchor_side in ("left", "right"):
        fixed, low = edge[0][0], min(point[1] for point in edge)
        return (fixed - service.x if item.anchor_side == "left" else fixed - service.right,
                low + item.anchor_offset_mm - service.y)
    fixed, low = edge[0][1], min(point[0] for point in edge)
    return (low + item.anchor_offset_mm - service.x,
            fixed - service.y if item.anchor_side == "bottom" else fixed - service.top)


def _outward_side(edge):
    first, second = edge
    if first[0] == second[0]:
        return "right" if second[1] > first[1] else "left"
    return "bottom" if second[0] > first[0] else "top"


def _hosts(building, item):
    return (item.room_id,) if item.room_id is not None else tuple(sorted(building.room_ids_for_zone(item.zone_id)))


def _canonical_inputs(building, layout):
    if not isinstance(building, BuildingIR) or not isinstance(layout, LayoutResult):
        raise ValueError("Polygon equipment packing requires canonical BuildingIR and LayoutResult")
    _hash(building.to_dict())
    normalize_building_mapping(building.to_dict())
    return parse_layout_result(layout.to_dict())


def _polygon(shape):
    return OrthogonalPolygon.from_rect(shape) if isinstance(shape, Rect) else shape


def _placement_data(placement):
    if not isinstance(placement, EquipmentPlacement) or not all(isinstance(value, str) and value.strip() for value in (placement.equipment_id, placement.room_id)) or not isinstance(placement.rotated, bool):
        raise ValueError("Catalog requires EquipmentPlacement with nonempty scalar IDs and boolean rotation")
    OrthogonalPolygon.from_rect(placement.rect)
    return {"equipment_id": placement.equipment_id, "room_id": placement.room_id, "rotated": placement.rotated,
            "units": "mm", "rect": {"x": placement.rect.x, "y": placement.rect.y, "width": placement.rect.width, "height": placement.rect.height}}


def _exclusion_data(building, layout, excluded_layouts):
    if excluded_layouts is None:
        return ()
    if not isinstance(excluded_layouts, (list, tuple)) or len(excluded_layouts) > 128:
        raise ValueError("excluded_layouts requires a list/tuple of at most 128 complete EquipmentLayoutResult combinations")
    identifiers = {item.id for item in building.equipment}
    canonical = {}
    for result in excluded_layouts:
        if not isinstance(result, EquipmentLayoutResult) or not isinstance(result.placements, (list, tuple)) or len(result.placements) != len(identifiers):
            raise ValueError("Every excluded layout must contain each source equipment ID exactly once")
        data = [_placement_data(placement) for placement in result.placements]
        if {placement["equipment_id"] for placement in data} != identifiers:
            raise ValueError("Every excluded layout must contain each source equipment ID exactly once")
        if not validate_orthogonal_equipment(building, layout, result).ok or not validate_orthogonal_door_approaches(building, layout, result).ok:
            raise ValueError("Excluded equipment combinations require independently valid actual equipment and door approaches")
        combination = {placement["equipment_id"]: placement for placement in sorted(data, key=lambda item: item["equipment_id"])}
        serialized = json.dumps(combination, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":"))
        canonical[serialized] = combination
    return tuple(canonical[key] for key in sorted(canonical))


def _placement_key(placement):
    return (placement.room_id, placement.rotated, placement.rect.y, placement.rect.x, placement.rect.width, placement.rect.height)


def _step(counter, budget, deadline):
    if counter["steps"] >= budget.max_catalog_steps_per_equipment or time.monotonic() >= deadline:
        counter["truncated"] = True
        return False
    counter["steps"] += 1
    return True


def _hash(payload):
    try:
        serialized = json.dumps(payload, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":"))
    except (ValueError, TypeError, OverflowError) as exc:
        raise ValueError("Polygon equipment source requires finite canonical JSON") from exc
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _positive(value, name):
    if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value) or value <= 0:
        raise ValueError(f"{name} requires a positive finite number")

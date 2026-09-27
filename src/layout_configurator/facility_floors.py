"""Multi-storey facilities: floors, vertical cores and cross-floor flows.

Each floor is an ordinary facility program (``BuildingIR``) with its own
profile, so everything that works on one floor — the hierarchical solver,
equipment packing, routing, the gates, the viewer, QA and variant comparison —
works on every floor unchanged. This module adds only what connects floors:

* **Vertical cores** (stair, lifts, service shafts) are rooms declared on every
  floor they serve. The lowest floor places them; every other floor is solved
  with those rooms pinned to the same rectangle.
* **Cross-floor flows** become an ordinary flow on each end: on the departure
  floor from the source to the core, on the arrival floor from the core to the
  target. The normal routing and flow gates judge both legs, including door
  approaches and equipment clearances.
* **Vertical checks** confirm, independently of the solve, that every core has
  the same rectangle on each floor, that the core kind may carry the flow type
  and that the core is wide enough for the flow.

Which flow types a core kind may carry is a project policy (``CORE_FLOW_TYPES``),
not a regulatory statement.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

import yaml

from .building import BuildingIR, FlowSpec
from .facility import FacilityProfile, default_facility_profile, load_facility_profile
from .facility_generation import (
    FeasibleFacilityCandidate,
    InfeasibleFacilityGeneration,
    solve_feasible_facility_variants,
)
from .models import Rect

# Project policy: what each kind of vertical core may carry.
CORE_FLOW_TYPES: dict[str, frozenset[str]] = {
    "stair": frozenset({"people"}),
    "passenger_lift": frozenset({"people"}),
    "goods_lift": frozenset({"material", "finished_goods", "dirty_material", "waste"}),
    "service_shaft": frozenset({"service"}),
}


class FacilityFloorsError(ValueError):
    """The multi-floor program is inconsistent or could not be solved."""

    def __init__(self, message: str, evidence: dict[str, Any] | None = None):
        super().__init__(message)
        self.evidence = evidence or {}


@dataclass(frozen=True)
class FloorEntry:
    level: int
    elevation_mm: float
    building: BuildingIR
    program_path: str
    profile_path: str | None = None


@dataclass(frozen=True)
class VerticalCore:
    id: str
    kind: str
    rooms: dict[int, str]


@dataclass(frozen=True)
class CrossFloorFlow:
    id: str
    type: str
    from_level: int
    from_id: str
    to_level: int
    to_id: str
    via: tuple[str, ...]
    minimum_clear_width_mm: float
    required: bool = True

    def leg_id(self, level: int) -> str:
        return f"{self.id}@L{level}"


@dataclass(frozen=True)
class FacilityFloorsSpec:
    project_name: str
    floor_height_mm: float
    floors: tuple[FloorEntry, ...]
    cores: tuple[VerticalCore, ...]
    cross_flows: tuple[CrossFloorFlow, ...]

    def floor(self, level: int) -> FloorEntry:
        for entry in self.floors:
            if entry.level == level:
                return entry
        raise KeyError(level)

    def core_for(self, flow: CrossFloorFlow) -> VerticalCore:
        """The first listed core that serves both ends and may carry the flow."""

        cores = {core.id: core for core in self.cores}
        for core_id in flow.via or tuple(cores):
            core = cores[core_id]
            if flow.from_level in core.rooms and flow.to_level in core.rooms and flow.type in CORE_FLOW_TYPES[core.kind]:
                return core
        raise FacilityFloorsError(
            f"Cross-floor flow {flow.id} ({flow.type}) has no core that serves levels "
            f"{flow.from_level} and {flow.to_level} and may carry {flow.type}"
        )

    def floor_programs(self) -> dict[int, BuildingIR]:
        """Each floor's program with the cross-floor legs added as ordinary flows."""

        extra: dict[int, list[FlowSpec]] = {entry.level: [] for entry in self.floors}
        for flow in self.cross_flows:
            core = self.core_for(flow)
            extra[flow.from_level].append(
                FlowSpec(
                    id=flow.leg_id(flow.from_level),
                    type=flow.type,
                    from_ids=(flow.from_id,),
                    to_ids=(core.rooms[flow.from_level],),
                    minimum_clear_width_mm=flow.minimum_clear_width_mm,
                    required=flow.required,
                )
            )
            extra[flow.to_level].append(
                FlowSpec(
                    id=flow.leg_id(flow.to_level),
                    type=flow.type,
                    from_ids=(core.rooms[flow.to_level],),
                    to_ids=(flow.to_id,),
                    minimum_clear_width_mm=flow.minimum_clear_width_mm,
                    required=flow.required,
                )
            )
        return {
            entry.level: replace(entry.building, flows=tuple(entry.building.flows) + tuple(extra[entry.level]))
            for entry in self.floors
        }


def load_facility_floors(path: str | Path) -> FacilityFloorsSpec:
    path = Path(path)
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    return facility_floors_from_mapping(raw, base_dir=path.parent)


def facility_floors_from_mapping(raw: Mapping[str, Any], *, base_dir: Path) -> FacilityFloorsSpec:
    from .io import load_building

    if not isinstance(raw, Mapping):
        raise FacilityFloorsError("Multi-floor facility root must be an object")
    payload = raw.get("facility_multi_floor", raw)
    floors_raw = payload.get("floors") or ()
    if len(floors_raw) < 2:
        raise FacilityFloorsError("A multi-floor facility needs at least two floors")
    floors: list[FloorEntry] = []
    for index, item in enumerate(floors_raw):
        level = int(item.get("level", index))
        if any(entry.level == level for entry in floors):
            raise FacilityFloorsError(f"Duplicate floor level {level}")
        program = item.get("program")
        if not program:
            raise FacilityFloorsError(f"Floor {level} needs a program")
        program_path = (base_dir / str(program)).resolve()
        profile = item.get("profile")
        floors.append(
            FloorEntry(
                level=level,
                elevation_mm=float(item.get("elevation_mm", level * float(payload.get("floor_height_mm", 4000)))),
                building=load_building(program_path),
                program_path=str(program_path),
                profile_path=str((base_dir / str(profile)).resolve()) if profile else None,
            )
        )
    floors.sort(key=lambda entry: entry.level)
    by_level = {entry.level: entry for entry in floors}

    cores: list[VerticalCore] = []
    for item in payload.get("vertical_cores") or ():
        core_id = str(item.get("id", "")).strip()
        kind = str(item.get("kind", "")).strip()
        if not core_id:
            raise FacilityFloorsError("Every vertical core needs an id")
        if kind not in CORE_FLOW_TYPES:
            raise FacilityFloorsError(f"Core {core_id} has unknown kind {kind!r}; use one of {', '.join(sorted(CORE_FLOW_TYPES))}")
        rooms = {int(level): str(room_id) for level, room_id in (item.get("rooms") or {}).items()}
        if len(rooms) < 2:
            raise FacilityFloorsError(f"Core {core_id} must serve at least two floors")
        for level, room_id in rooms.items():
            if level not in by_level:
                raise FacilityFloorsError(f"Core {core_id} references unknown floor {level}")
            if room_id not in {room.id for room in by_level[level].building.layout.rooms}:
                raise FacilityFloorsError(f"Core {core_id}: room {room_id} is not declared on floor {level}")
        cores.append(VerticalCore(core_id, kind, rooms))

    flows: list[CrossFloorFlow] = []
    core_ids = {core.id for core in cores}
    for item in payload.get("cross_floor_flows") or ():
        flow_id = str(item.get("id", "")).strip()
        source, target = item.get("from") or {}, item.get("to") or {}
        try:
            flow = CrossFloorFlow(
                id=flow_id,
                type=str(item.get("type", "people")),
                from_level=int(source["level"]),
                from_id=str(source["id"]),
                to_level=int(target["level"]),
                to_id=str(target["id"]),
                via=tuple(str(core_id) for core_id in item.get("via") or ()),
                minimum_clear_width_mm=float(item.get("minimum_clear_width_mm", 1200)),
                required=bool(item.get("required", True)),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise FacilityFloorsError(f"Cross-floor flow {flow_id or '<unnamed>'} needs from/to with level and id") from exc
        if not flow_id:
            raise FacilityFloorsError("Every cross-floor flow needs an id")
        if flow.from_level == flow.to_level:
            raise FacilityFloorsError(f"Cross-floor flow {flow_id} starts and ends on floor {flow.from_level}")
        for level, endpoint in ((flow.from_level, flow.from_id), (flow.to_level, flow.to_id)):
            if level not in by_level:
                raise FacilityFloorsError(f"Cross-floor flow {flow_id} references unknown floor {level}")
            building = by_level[level].building
            known = {room.id for room in building.layout.rooms} | {item.id for item in building.equipment} | {zone.id for zone in building.zones}
            if endpoint not in known:
                raise FacilityFloorsError(f"Cross-floor flow {flow_id}: {endpoint} is not a room, zone or equipment on floor {level}")
        unknown_via = set(flow.via) - core_ids
        if unknown_via:
            raise FacilityFloorsError(f"Cross-floor flow {flow_id} references unknown cores: {', '.join(sorted(unknown_via))}")
        flows.append(flow)

    spec = FacilityFloorsSpec(
        project_name=str(payload.get("project_name", "Multi-floor facility")),
        floor_height_mm=float(payload.get("floor_height_mm", 4000)),
        floors=tuple(floors),
        cores=tuple(cores),
        cross_flows=tuple(flows),
    )
    for flow in spec.cross_flows:
        spec.core_for(flow)  # fail early when no core can carry a flow
    return spec


@dataclass(frozen=True)
class VerticalCheck:
    id: str
    status: str
    evidence: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "status": self.status, "evidence": list(self.evidence)}


@dataclass(frozen=True)
class FloorStack:
    """One complete multi-floor variant: an accepted candidate per floor."""

    number: int
    floors: dict[int, FeasibleFacilityCandidate]
    checks: tuple[VerticalCheck, ...]

    @property
    def ok(self) -> bool:
        return all(check.status == "PASS" for check in self.checks) and all(
            candidate.facility_report.ok for candidate in self.floors.values()
        )


@dataclass
class FacilityFloorsResult:
    spec: FacilityFloorsSpec
    stacks: list[FloorStack]
    programs: dict[int, BuildingIR]
    profiles: dict[int, FacilityProfile]
    rejected: list[dict[str, Any]] = field(default_factory=list)


def solve_facility_floors(
    spec: FacilityFloorsSpec,
    *,
    variants: int,
    time_limit_seconds: float,
    seed: int,
    max_attempts: int | None = None,
    room_solver: str = "auto",
    default_profile: FacilityProfile | None = None,
) -> FacilityFloorsResult:
    """Solve every floor, lowest first, with cores pinned where already placed."""

    programs = {level: building.with_required_flow_opening_width() for level, building in spec.floor_programs().items()}
    profiles = {
        entry.level: load_facility_profile(entry.profile_path)
        if entry.profile_path
        else (default_profile or default_facility_profile())
        for entry in spec.floors
    }
    levels = [entry.level for entry in spec.floors]
    base = levels[0]
    try:
        base_generation = solve_feasible_facility_variants(
            programs[base], profiles[base], variants=variants, time_limit_seconds=time_limit_seconds,
            seed=seed, max_attempts=max_attempts, room_solver=room_solver,
        )
    except InfeasibleFacilityGeneration as exc:
        raise FacilityFloorsError(f"Floor {base}: {exc}", {"level": base, "generation": exc.generation.to_dict()}) from exc

    result = FacilityFloorsResult(spec=spec, stacks=[], programs=programs, profiles=profiles)
    for number, base_candidate in enumerate(base_generation.accepted, start=1):
        floors = {base: base_candidate}
        placed = {
            core.id: base_candidate.result.placements[core.rooms[base]] for core in spec.cores if base in core.rooms
        }
        failure = None
        for level in levels[1:]:
            fixed = {core.rooms[level]: placed[core.id] for core in spec.cores if level in core.rooms and core.id in placed}
            try:
                generation = solve_feasible_facility_variants(
                    programs[level], profiles[level], variants=1, time_limit_seconds=time_limit_seconds,
                    seed=seed + number - 1, max_attempts=max_attempts or 3, room_solver=room_solver, fixed_rects=fixed,
                )
            except InfeasibleFacilityGeneration as exc:
                failure = {"stack": number, "level": level, "reason": str(exc), "generation": exc.generation.to_dict()}
                break
            candidate = generation.accepted[0]
            candidate = replace(candidate, result=replace(candidate.result, variant=number))
            floors[level] = candidate
            for core in spec.cores:
                if level in core.rooms and core.id not in placed:
                    placed[core.id] = candidate.result.placements[core.rooms[level]]
        if failure:
            result.rejected.append(failure)
            continue
        result.stacks.append(FloorStack(number=len(result.stacks) + 1, floors=floors, checks=vertical_checks(spec, floors)))
    if not result.stacks:
        raise FacilityFloorsError(
            "No floor stack could be completed: " + "; ".join(f"floor {item['level']}: {item['reason']}" for item in result.rejected),
            {"rejected": result.rejected},
        )
    if len(result.stacks) < variants:
        raise FacilityFloorsError(
            f"Only {len(result.stacks)} of {variants} floor stack(s) could be completed",
            {"rejected": result.rejected},
        )
    return result


def vertical_checks(spec: FacilityFloorsSpec, floors: Mapping[int, FeasibleFacilityCandidate]) -> tuple[VerticalCheck, ...]:
    """Check the cores and cross-floor flows of one stack from its results only."""

    checks: list[VerticalCheck] = []

    alignment: list[str] = []
    aligned = True
    for core in spec.cores:
        rects = {level: floors[level].result.placements.get(room_id) for level, room_id in core.rooms.items() if level in floors}
        distinct = {(r.x, r.y, r.width, r.height) for r in rects.values() if r is not None}
        if len(distinct) != 1 or None in rects.values():
            aligned = False
            alignment.append(f"{core.id}: rectangles differ between floors {sorted(rects)}")
        else:
            (x, y, w, h), = distinct
            alignment.append(f"{core.id} ({core.kind}): {w:.0f} x {h:.0f} mm at ({x:.0f}, {y:.0f}) on floors {sorted(rects)}")
    checks.append(VerticalCheck("CORE_ALIGNMENT", "PASS" if aligned else "FAIL", tuple(alignment)))

    capacity: list[str] = []
    carriage: list[str] = []
    routes: list[str] = []
    capacity_ok = carriage_ok = routes_ok = True
    for flow in spec.cross_flows:
        core = spec.core_for(flow)
        rect: Rect | None = floors[flow.from_level].result.placements.get(core.rooms[flow.from_level])
        allowed = flow.type in CORE_FLOW_TYPES[core.kind]
        carriage_ok &= allowed
        carriage.append(f"{flow.id}: {flow.type} via {core.id} ({core.kind}) — {'allowed' if allowed else 'not allowed'} by project policy")
        if rect is None:
            capacity_ok = False
            capacity.append(f"{flow.id}: core {core.id} is not placed")
        else:
            # Rooms are measured to wall centrelines; the clear opening loses one wall.
            clear = min(rect.width, rect.height) - spec.floor(flow.from_level).building.layout.wall_thickness_mm
            fits = clear + 1e-6 >= flow.minimum_clear_width_mm
            capacity_ok &= fits
            capacity.append(f"{flow.id}: core {core.id} clear {clear:.0f} mm, needs {flow.minimum_clear_width_mm:.0f} mm")
        for level in (flow.from_level, flow.to_level):
            candidate = floors[level]
            leg = flow.leg_id(level)
            leg_routes = [route for route in candidate.flow_routes.routes if route.flow_id == leg]
            leg_issues = [issue for issue in candidate.flow_report.issues if issue.flow_id == leg]
            if not leg_routes or leg_issues:
                routes_ok = False
                routes.append(f"{leg}: {len(leg_routes)} route(s), {len(leg_issues)} issue(s)")
            else:
                length = sum(
                    math.dist(a, b) for route in leg_routes for a, b in zip(route.points, route.points[1:])
                ) / 1000
                routes.append(f"{leg}: routed, {length:.1f} m, through {' → '.join(leg_routes[0].room_path)}")
    if spec.cross_flows:
        checks.append(VerticalCheck("CORE_FLOW_TYPES", "PASS" if carriage_ok else "FAIL", tuple(carriage)))
        checks.append(VerticalCheck("CORE_CLEAR_WIDTH", "PASS" if capacity_ok else "FAIL", tuple(capacity)))
        checks.append(VerticalCheck("CROSS_FLOOR_ROUTES", "PASS" if routes_ok else "FAIL", tuple(routes)))
    return tuple(checks)

"""Hierarchical room solver: solve clusters first, then place them.

The monolithic model in :mod:`solver` places every room at once. It finds the
13-room pilot quickly but stops scaling around 20 rooms: the required adjacency
graph of a facility is close to a tree hanging off a few corridors, and one
``NoOverlap2D`` with a contact constraint per edge gives CP-SAT nothing to hold
on to. The scaling study recorded one success in three at 20 rooms, after
64 minutes.

This module splits the problem along that tree, as ``docs/PRODUCT_PLAN.md``
section 5 prescribes:

1. **Clusters.** A hub (a room with three or more required neighbours, usually
   a corridor) is grouped with its leaf neighbours. Every other room is its own
   cluster. Rooms with boundary obligations (daylight, the external entry) and
   rooms in zone-level relations stay single so the top level can place them.
2. **Cluster shapes.** Each cluster is solved as a small layout of its own with
   the ordinary room solver, in a few box proportions, so every room keeps its
   area, minimum dimensions and equipment fit. A hub must touch as many sides of
   its box as it has neighbours outside the cluster, so it stays reachable.
3. **Top level.** Only the cluster offsets and the choice of shape, mirror and
   quarter turn are decided. Room coordinates are linear in those decisions, so
   every cross-cluster contact, daylight, external-entry and zone relation is
   posted on the rooms exactly, and rooms (not cluster boxes) may not overlap,
   so concave clusters can interlock.

The result is an ordinary :class:`LayoutResult`. Nothing downstream trusts it:
the independent layout, equipment, flow and profile validators judge it like
any monolithic candidate. The hierarchy restricts the search (a room's shape is
one of a few per cluster), so it can miss layouts the monolithic model would
find; :func:`solve_layouts_auto` therefore falls back to the monolithic solver.
"""

from __future__ import annotations

import math
import os
from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, replace

from .models import BoundarySpec, LayoutIR, LayoutResult, Rect
from .search import configure_solver
from .solver import (
    InfeasibleLayout,
    _RoomVars,
    _add_adjacency_indicator,
    _add_forbidden_adjacency,
    _add_required_adjacency,
    _area_to_grid2,
    _ceil_grid,
    _constrain_daylight_contact,
    _constrain_external_entry,
    _normalise_adjacency_groups,
    _room_size_vars,
    solve_layouts,
)

SOLVER_STRATEGIES = ("auto", "monolithic", "hierarchical")

# Box proportions (width / height) and fill ratios tried for each cluster. The
# first few feasible boxes become the cluster's alternative shapes.
_ASPECTS = (1.0, 2.0, 0.5, 3.5, 0.29, 1.4, 0.7)
_FILLS = (0.8, 0.7, 0.6, 0.5)
_MAX_SHAPES = 4
# Single rooms keep a free size at the top level instead of a few fixed shapes.
_FLEXIBLE_SINGLETONS = True


class HierarchyNotApplicable(InfeasibleLayout):
    """The program uses a feature the hierarchical solver does not model."""


@dataclass(frozen=True)
class _Placement:
    """One cluster shape after an optional quarter turn and mirroring."""

    width: float
    height: float
    rooms: tuple[tuple[str, float, float, float, float], ...]

    @property
    def room_ids(self) -> tuple[str, ...]:
        # Members are sorted in every shape, so index i is the same room in all.
        return tuple(item[0] for item in self.rooms)


def cluster_rooms(
    spec: LayoutIR,
    *,
    pinned: Iterable[str] = (),
) -> dict[str, tuple[str, ...]]:
    """Group rooms into hub-and-leaf clusters, keyed by the hub id.

    ``pinned`` rooms always stay single. The grouping is deterministic: hubs are
    taken by descending degree, then id.
    """

    neighbours: dict[str, set[str]] = defaultdict(set)
    for room_a, room_b in spec.relation_pairs("required_adjacency"):
        neighbours[room_a].add(room_b)
        neighbours[room_b].add(room_a)
    single = set(pinned)
    for room in spec.rooms:
        if room.needs_daylight:
            single.add(room.id)
    if spec.external_entry is not None:
        single.add(spec.external_entry.room_id)

    owner: dict[str, str] = {}
    hubs = sorted(
        (room.id for room in spec.rooms if len(neighbours[room.id]) >= 3 and room.id not in single),
        key=lambda room_id: (-len(neighbours[room_id]), room_id),
    )
    for hub in hubs:
        if hub in owner:
            continue
        owner[hub] = hub
        for leaf in sorted(neighbours[hub]):
            if leaf not in owner and leaf not in single and len(neighbours[leaf]) == 1:
                owner[leaf] = hub
    groups: dict[str, list[str]] = defaultdict(list)
    for room in spec.rooms:
        groups[owner.get(room.id, room.id)].append(room.id)
    return {cluster_id: tuple(members) for cluster_id, members in sorted(groups.items())}


def solve_layouts_hierarchical(
    spec: LayoutIR,
    variants: int = 1,
    time_limit_seconds: float = 30,
    seed: int = 42,
    *,
    required_adjacency_groups: Iterable[tuple[str, Iterable[str], Iterable[str]]] | None = None,
    forbidden_adjacency_groups: Iterable[tuple[str, Iterable[str], Iterable[str]]] | None = None,
    minimum_adjacency_mm: float | None = None,
    equipment_fit_options: Mapping[str, Iterable[tuple[str, Iterable[tuple[float, float]]]]] | None = None,
    deterministic_units: float | None = None,
    fixed_rects: Mapping[str, Rect] | None = None,
) -> list[LayoutResult]:
    """Return up to ``variants`` distinct layouts from the cluster hierarchy.

    ``fixed_rects`` pins rooms (for example a vertical core repeated on every
    floor); a pinned room is always its own cluster.
    """

    if variants < 1:
        raise ValueError("variants must be at least 1")
    if spec.boundary.cutouts:
        raise HierarchyNotApplicable("The hierarchical solver does not model boundary cutouts")
    try:
        from ortools.sat.python import cp_model
    except ImportError as exc:  # pragma: no cover - dependency is declared in pyproject.toml
        raise RuntimeError("Install project dependencies: pip install -e .") from exc

    known = {room.id for room in spec.rooms}
    fixed_rects = dict(fixed_rects or {})
    unknown_fixed = set(fixed_rects) - known
    if unknown_fixed:
        raise InfeasibleLayout(f"Cannot fix unknown rooms: {', '.join(sorted(unknown_fixed))}")
    required_groups = _normalise_adjacency_groups(required_adjacency_groups, known, "required")
    forbidden_groups = _normalise_adjacency_groups(forbidden_adjacency_groups, known, "forbidden")
    fit_options = {
        str(room_id): tuple(
            (str(equipment_id), tuple((float(w), float(h)) for w, h in options))
            for equipment_id, options in requirements
        )
        for room_id, requirements in (equipment_fit_options or {}).items()
    }
    adjacency_mm = spec.door_width_mm if minimum_adjacency_mm is None else float(minimum_adjacency_mm)
    in_groups = {room_id for _, left, right in (*required_groups, *forbidden_groups) for room_id in (*left, *right)}
    clusters = cluster_rooms(spec, pinned=in_groups | set(fixed_rects))
    cluster_of = {room_id: cluster_id for cluster_id, members in clusters.items() for room_id in members}
    rooms_by_id = {room.id: room for room in spec.rooms}

    external: dict[str, int] = defaultdict(int)
    for room_a, room_b in spec.relation_pairs("required_adjacency"):
        if cluster_of[room_a] != cluster_of[room_b]:
            external[room_a] += 1
            external[room_b] += 1

    shapes: dict[str, list[_Placement]] = {}
    for cluster_id, members in clusters.items():
        if len(members) == 1 and (_FLEXIBLE_SINGLETONS or members[0] in fixed_rects):
            # A single room keeps a free size at the top level, exactly as in
            # the monolithic model; only multi-room clusters become rigid.
            continue
        found = _cluster_shapes(
            spec,
            members,
            {room_id: external[room_id] for room_id in members if len(members) > 1 and external[room_id]},
            fit_options,
            adjacency_mm,
            time_limit_seconds,
            seed,
            deterministic_units,
        )
        placements = [
            placement
            for shape in found
            for placement in _orientations(shape, rooms_by_id, fit_options)
        ]
        if not placements:
            raise InfeasibleLayout(f"Cluster {cluster_id} has no shape that fits the boundary")
        shapes[cluster_id] = list(dict.fromkeys(placements))

    grid = spec.grid_mm
    boundary_width = _ceil_grid(spec.boundary.width_mm, grid)
    boundary_height = _ceil_grid(spec.boundary.height_mm, grid)
    model = cp_model.CpModel()
    room_vars: dict[str, _RoomVars] = {}
    cluster_vars: list[object] = []
    # (x, y, shape choices) per placed unit, for the diversity cut between variants.
    anchors: list[tuple[object, object, tuple[object, ...]]] = []
    x_intervals = []
    y_intervals = []
    area_deviations = []
    for cluster_id, members in clusters.items():
        if len(members) != 1 or not (_FLEXIBLE_SINGLETONS or members[0] in fixed_rects):
            continue
        room = rooms_by_id[members[0]]
        x = model.NewIntVar(0, boundary_width, f"{room.id}_x")
        y = model.NewIntVar(0, boundary_height, f"{room.id}_y")
        width, height = _room_size_vars(model, room, grid, boundary_width, boundary_height)
        if room.id in fixed_rects:
            pinned = fixed_rects[room.id]
            model.Add(x == _units(pinned.x, grid))
            model.Add(y == _units(pinned.y, grid))
            model.Add(width == _units(pinned.width, grid))
            model.Add(height == _units(pinned.height, grid))
        area = model.NewIntVar(0, boundary_width * boundary_height, f"{room.id}_area")
        model.AddMultiplicationEquality(area, [width, height])
        minimum_area = _area_to_grid2(room.min_area_m2, grid, ceil=True)
        maximum_area = _area_to_grid2(room.max_area_m2, grid, ceil=False)
        if maximum_area < minimum_area:
            raise InfeasibleLayout(f"Room {room.id} area range is smaller than one grid step")
        model.Add(area >= minimum_area)
        model.Add(area <= maximum_area)
        deviation = model.NewIntVar(0, boundary_width * boundary_height, f"{room.id}_area_deviation")
        model.AddAbsEquality(deviation, area - _area_to_grid2(room.target_area_m2, grid, ceil=False))
        area_deviations.append(deviation)
        for equipment_id, options in fit_options.get(room.id, ()):
            fits = []
            for option_index, (fit_width, fit_height) in enumerate(options):
                fit = model.NewBoolVar(f"equipment_fit_{room.id}_{equipment_id}_{option_index}")
                model.Add(width >= _ceil_grid(fit_width, grid)).OnlyEnforceIf(fit)
                model.Add(height >= _ceil_grid(fit_height, grid)).OnlyEnforceIf(fit)
                fits.append(fit)
            model.AddBoolOr(fits)
        x_end = model.NewIntVar(0, boundary_width, f"{room.id}_x_end")
        y_end = model.NewIntVar(0, boundary_height, f"{room.id}_y_end")
        model.Add(x_end == x + width)
        model.Add(y_end == y + height)
        x_intervals.append(model.NewIntervalVar(x, width, x_end, f"{room.id}_x_interval"))
        y_intervals.append(model.NewIntervalVar(y, height, y_end, f"{room.id}_y_interval"))
        room_vars[room.id] = _RoomVars(x=x, y=y, width=width, height=height, area=area)
        cluster_vars.extend((x, y, width, height))
        anchors.append((x, y, ()))
    for cluster_id, options in shapes.items():
        cx = model.NewIntVar(0, boundary_width, f"cluster_{cluster_id}_x")
        cy = model.NewIntVar(0, boundary_height, f"cluster_{cluster_id}_y")
        choices = [model.NewBoolVar(f"cluster_{cluster_id}_shape_{index}") for index in range(len(options))]
        model.AddExactlyOne(choices)
        cluster_vars.extend((cx, cy, *choices))
        anchors.append((cx, cy, tuple(choices)))

        def pick(values: list[int]):
            return sum(choice * value for choice, value in zip(choices, values))

        model.Add(cx + pick([_units(option.width, grid) for option in options]) <= boundary_width)
        model.Add(cy + pick([_units(option.height, grid) for option in options]) <= boundary_height)
        for member_index, room_id in enumerate(options[0].room_ids):
            geometry = [option.rooms[member_index] for option in options]
            x = model.NewIntVar(0, boundary_width, f"{room_id}_x")
            y = model.NewIntVar(0, boundary_height, f"{room_id}_y")
            width = model.NewIntVar(0, boundary_width, f"{room_id}_width")
            height = model.NewIntVar(0, boundary_height, f"{room_id}_height")
            model.Add(x == cx + pick([_units(item[1], grid) for item in geometry]))
            model.Add(y == cy + pick([_units(item[2], grid) for item in geometry]))
            model.Add(width == pick([_units(item[3], grid) for item in geometry]))
            model.Add(height == pick([_units(item[4], grid) for item in geometry]))
            x_end = model.NewIntVar(0, boundary_width, f"{room_id}_x_end")
            y_end = model.NewIntVar(0, boundary_height, f"{room_id}_y_end")
            model.Add(x_end == x + width)
            model.Add(y_end == y + height)
            x_intervals.append(model.NewIntervalVar(x, width, x_end, f"{room_id}_x_interval"))
            y_intervals.append(model.NewIntervalVar(y, height, y_end, f"{room_id}_y_interval"))
            room_vars[room_id] = _RoomVars(x=x, y=y, width=width, height=height, area=None)
    # Rooms, not cluster boxes, must not overlap: concave clusters interlock.
    model.AddNoOverlap2D(x_intervals, y_intervals)

    minimum_shared = max(1, _ceil_grid(adjacency_mm, grid))
    for room_a, room_b in spec.relation_pairs("required_adjacency"):
        if cluster_of[room_a] != cluster_of[room_b]:
            _add_required_adjacency(model, room_vars[room_a], room_vars[room_b], minimum_shared, f"adj_{room_a}_{room_b}")
    for room_a, room_b in spec.relation_pairs("forbidden_adjacency"):
        if cluster_of[room_a] != cluster_of[room_b]:
            _add_forbidden_adjacency(model, room_vars[room_a], room_vars[room_b], f"forbid_{room_a}_{room_b}")
    for group_name, left, right in required_groups:
        indicators = [
            _add_adjacency_indicator(model, room_vars[a], room_vars[b], minimum_shared, f"zone_required_{group_name}_{a}_{b}")
            for a in left
            for b in right
            if a != b
        ]
        if not indicators:
            raise InfeasibleLayout(f"Group adjacency {group_name} contains no valid rooms")
        model.AddBoolOr(indicators)
    for group_name, left, right in forbidden_groups:
        for a in left:
            for b in right:
                if a != b:
                    _add_forbidden_adjacency(model, room_vars[a], room_vars[b], f"zone_forbidden_{group_name}_{a}_{b}")
    for room in spec.rooms:
        if room.needs_daylight:
            _constrain_daylight_contact(model, room_vars[room.id], spec, boundary_width, boundary_height, room.id)
    if spec.external_entry is not None:
        _constrain_external_entry(model, room_vars[spec.external_entry.room_id], spec, boundary_width, boundary_height)
    # Cluster shapes already carry their area accuracy; single rooms are sized
    # here, so their area deviation leads and position only breaks ties.
    model.Minimize(
        sum(area_deviations) * 10_000
        + sum(v for item in room_vars.values() for v in (item.x, item.y))
    )

    results: list[LayoutResult] = []
    for variant in range(1, variants + 1):
        solver = cp_model.CpSolver()
        configure_solver(
            solver,
            seed=seed + variant - 1,
            time_limit_seconds=time_limit_seconds,
            deterministic_units=deterministic_units,
            # The top level is where the search is hard; with a wall-clock
            # budget it may use the parallel portfolio.
            workers=_top_level_workers(),
        )
        # The top level is a feasibility search; the ranking that matters is
        # done afterwards by the independent gates.
        solver.parameters.stop_after_first_solution = True
        status = solver.Solve(model)
        if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE) and model.Proto().assumptions:
            # No clearly different layout within the budget: accept one that
            # differs only in detail rather than returning fewer variants.
            model.ClearAssumptions()
            solver = cp_model.CpSolver()
            configure_solver(
                solver,
                seed=seed + variant - 1,
                time_limit_seconds=time_limit_seconds,
                deterministic_units=deterministic_units,
                workers=_top_level_workers(),
            )
            solver.parameters.stop_after_first_solution = True
            status = solver.Solve(model)
        if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            if not results:
                raise InfeasibleLayout("The hierarchical solver found no placement of the room clusters")
            break
        if area_deviations and solver.Value(sum(area_deviations)) > 0:
            # The first placement settles the hard part; a short second pass
            # starting from it brings the free single rooms back towards
            # their programmed areas without searching the placement again.
            solver = _polish_areas(model, solver, cluster_vars, area_deviations, seed + variant - 1,
                                   time_limit_seconds, deterministic_units)
        placements = {
            room.id: Rect(
                solver.Value(room_vars[room.id].x) * grid,
                solver.Value(room_vars[room.id].y) * grid,
                solver.Value(room_vars[room.id].width) * grid,
                solver.Value(room_vars[room.id].height) * grid,
            )
            for room in spec.rooms
        }
        results.append(LayoutResult(variant=variant, placements=placements, objective_value=_area_objective(spec, placements)))
        model.AddForbiddenAssignments(cluster_vars, [[solver.Value(value) for value in cluster_vars]])
        _require_distinct_variant(model, solver, anchors, grid, variant)
    return results


def solve_layouts_auto(
    spec: LayoutIR,
    variants: int = 1,
    time_limit_seconds: float = 30,
    seed: int = 42,
    *,
    strategy: str = "auto",
    evidence: dict | None = None,
    **options,
) -> list[LayoutResult]:
    """Dispatch to the requested room solver.

    ``auto`` tries the hierarchy first and falls back to the monolithic model
    when the hierarchy does not apply or returns fewer layouts than requested;
    the monolithic model alone is authoritative for its own failure message.
    ``evidence``, when given, receives ``room_solver_used`` and, after a
    fallback, ``hierarchy_fallback_reason``.
    """

    evidence = evidence if evidence is not None else {}

    def monolithic(reason: str | None = None) -> list[LayoutResult]:
        evidence["room_solver_used"] = "monolithic"
        if reason:
            evidence["hierarchy_fallback_reason"] = reason
        return solve_layouts(spec, variants, time_limit_seconds, seed, **options)

    if strategy not in SOLVER_STRATEGIES:
        raise ValueError(f"Unknown solver strategy {strategy!r}; use one of {', '.join(SOLVER_STRATEGIES)}")
    if strategy == "monolithic":
        return monolithic()
    hierarchical_options = {
        key: value
        for key, value in options.items()
        if key
        in {
            "required_adjacency_groups",
            "forbidden_adjacency_groups",
            "minimum_adjacency_mm",
            "equipment_fit_options",
            "deterministic_units",
            "fixed_rects",
        }
    }
    unsupported = sorted(key for key, value in options.items() if key not in hierarchical_options and value)
    if unsupported:
        message = f"The hierarchical solver does not support: {', '.join(unsupported)}"
        if strategy == "hierarchical":
            raise HierarchyNotApplicable(message)
        return monolithic(message)
    try:
        results = solve_layouts_hierarchical(spec, variants, time_limit_seconds, seed, **hierarchical_options)
    except InfeasibleLayout as exc:
        if strategy == "hierarchical":
            raise
        return monolithic(str(exc))
    if strategy == "auto" and len(results) < variants:
        return monolithic(f"The hierarchy returned {len(results)} of {variants} candidates")
    evidence["room_solver_used"] = "hierarchical"
    return results


def _cluster_shapes(
    spec: LayoutIR,
    members: tuple[str, ...],
    side_contacts: Mapping[str, int],
    fit_options,
    adjacency_mm: float,
    time_limit_seconds: float,
    seed: int,
    deterministic_units: float | None,
) -> list[dict[str, Rect]]:
    member_set = set(members)
    rooms = tuple(
        replace(
            room,
            required_adjacency=tuple(other for other in room.required_adjacency if other in member_set),
            forbidden_adjacency=tuple(other for other in room.forbidden_adjacency if other in member_set),
            preferred_adjacency=(),
            # Boundary obligations are the top level's job.
            needs_daylight=False,
        )
        for room in spec.rooms
        if room.id in member_set
    )
    area = sum(room.target_area_m2 for room in rooms) * 1_000_000
    grid = spec.grid_mm
    found: list[dict[str, Rect]] = []
    for aspect in _ASPECTS:
        for fill in _FILLS:
            width = math.ceil(math.sqrt(area / fill * aspect) / grid) * grid
            height = math.ceil(area / fill / width / grid) * grid
            if width > spec.boundary.width_mm or height > spec.boundary.height_mm:
                continue
            sub_spec = replace(
                spec,
                rooms=rooms,
                boundary=BoundarySpec(width, height, ()),
                entry_room=rooms[0].id,
                external_entry=None,
                doors=(),
                windows=(),
            )
            try:
                solved = solve_layouts(
                    sub_spec,
                    1,
                    time_limit_seconds,
                    seed,
                    minimum_adjacency_mm=adjacency_mm,
                    equipment_fit_options={room_id: fit_options[room_id] for room_id in members if room_id in fit_options},
                    deterministic_units=deterministic_units,
                    boundary_side_contacts=side_contacts,
                )
            except InfeasibleLayout:
                continue
            placements = solved[0].placements
            x0 = min(rect.x for rect in placements.values())
            y0 = min(rect.y for rect in placements.values())
            found.append(
                {room_id: Rect(rect.x - x0, rect.y - y0, rect.width, rect.height) for room_id, rect in placements.items()}
            )
            break
        if len(found) >= _MAX_SHAPES:
            break
    return found


def _orientations(shape: Mapping[str, Rect], rooms_by_id, fit_options) -> list[_Placement]:
    """Mirror and quarter-turn a cluster shape, keeping only legal results.

    Minimum width and depth are per axis unless a room is rotatable, and
    equipment fit depends on
    orientation, so a turned shape is kept only if every room still satisfies
    both.
    """

    room_ids = tuple(sorted(shape))
    width = max(rect.right for rect in shape.values())
    height = max(rect.top for rect in shape.values())
    result: list[_Placement] = []
    for turned in (False, True):
        for mirror_x in (False, True):
            for mirror_y in (False, True):
                box_width, box_height = (height, width) if turned else (width, height)
                rooms = []
                legal = True
                for room_id in room_ids:
                    rect = shape[room_id]
                    x, y, w, h = (rect.y, rect.x, rect.height, rect.width) if turned else (rect.x, rect.y, rect.width, rect.height)
                    if mirror_x:
                        x = box_width - x - w
                    if mirror_y:
                        y = box_height - y - h
                    room = rooms_by_id[room_id]
                    if not room.fits(w, h):
                        legal = False
                    for _, options in fit_options.get(room_id, ()):
                        if not any(w + 1e-6 >= fit_w and h + 1e-6 >= fit_h for fit_w, fit_h in options):
                            legal = False
                    rooms.append((room_id, x, y, w, h))
                if legal:
                    result.append(_Placement(box_width, box_height, tuple(rooms)))
    return result


# A later variant should be a different option, not the same plan shifted by one
# grid step: a quarter of the placed units must move this far or change shape.
_DIVERSITY_MOVE_MM = 3000
_DIVERSITY_SHARE = 0.25


def _require_distinct_variant(model, solver, anchors, grid: int, variant: int) -> None:
    distance = max(1, round(_DIVERSITY_MOVE_MM / grid))
    moved = []
    for index, (x, y, choices) in enumerate(anchors):
        name = f"variant_{variant}_unit_{index}"
        vx, vy = solver.Value(x), solver.Value(y)
        reasons = []
        for var, value, axis in ((x, vx, "x"), (y, vy, "y")):
            below = model.NewBoolVar(f"{name}_{axis}_below")
            above = model.NewBoolVar(f"{name}_{axis}_above")
            model.Add(var <= value - distance).OnlyEnforceIf(below)
            model.Add(var >= value + distance).OnlyEnforceIf(above)
            reasons.extend((below, above))
        chosen = [choice for choice in choices if solver.Value(choice)]
        if chosen and len(choices) > 1:
            reasons.append(chosen[0].Not())
        unit_moved = model.NewBoolVar(f"{name}_moved")
        model.AddBoolOr(reasons).OnlyEnforceIf(unit_moved)
        moved.append(unit_moved)
    enforce = model.NewBoolVar(f"variant_{variant}_distinct")
    model.Add(sum(moved) >= max(1, math.ceil(len(moved) * _DIVERSITY_SHARE))).OnlyEnforceIf(enforce)
    # Enforced through assumptions, one per earlier variant, so a later solve
    # can drop them all if no clearly different layout exists.
    model.AddAssumptions([enforce])


def _polish_areas(model, first, decision_vars, area_deviations, seed, time_limit_seconds, deterministic_units):
    from ortools.sat.python import cp_model

    model.ClearHints()
    for var in decision_vars:
        model.AddHint(var, first.Value(var))
    model.Minimize(sum(area_deviations))
    polish = cp_model.CpSolver()
    configure_solver(
        polish,
        seed=seed,
        time_limit_seconds=min(time_limit_seconds, max(2.0, time_limit_seconds / 6)),
        deterministic_units=None if deterministic_units is None else max(1.0, deterministic_units / 6),
        workers=_top_level_workers(),
    )
    status = polish.Solve(model)
    model.ClearHints()
    if status in (cp_model.OPTIMAL, cp_model.FEASIBLE) and polish.Value(sum(area_deviations)) <= first.Value(sum(area_deviations)):
        return polish
    return first


def _top_level_workers() -> int:
    configured = os.environ.get("LAYOUT_SOLVER_WORKERS")
    if configured:
        return max(1, int(configured))
    return max(1, min(4, os.cpu_count() or 1))


def _units(value: float, grid: int) -> int:
    return int(round(value / grid))


def _area_objective(spec: LayoutIR, placements: Mapping[str, Rect]) -> int:
    """Report the same area-deviation measure the monolithic objective uses."""

    grid = spec.grid_mm
    total = 0
    for room in spec.rooms:
        rect = placements[room.id]
        area = _units(rect.width, grid) * _units(rect.height, grid)
        total += abs(area - _area_to_grid2(room.target_area_m2, grid, ceil=False))
    return total * 10_000

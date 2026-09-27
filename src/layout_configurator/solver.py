"""CP-SAT rectangular room solver."""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from .models import LayoutIR, LayoutResult, Rect
from .search import configure_solver


class InfeasibleLayout(RuntimeError):
    """Raised when no layout satisfies the hard constraints."""


@dataclass
class _RoomVars:
    x: object
    y: object
    width: object
    height: object
    area: object


def solve_layouts(
    spec: LayoutIR,
    variants: int = 1,
    time_limit_seconds: float = 30,
    seed: int = 42,
    fixed_rects: Mapping[str, Rect] | None = None,
    axis_aligned_room_ids: Iterable[str] | None = None,
    structural_axes_x_mm: Iterable[float] | None = None,
    structural_axes_y_mm: Iterable[float] | None = None,
    required_adjacency_groups: Iterable[tuple[str, Iterable[str], Iterable[str]]] | None = None,
    forbidden_adjacency_groups: Iterable[tuple[str, Iterable[str], Iterable[str]]] | None = None,
    minimum_adjacency_mm: float | None = None,
    equipment_fit_options: Mapping[str, Iterable[tuple[str, Iterable[tuple[float, float]]]]] | None = None,
    deterministic_units: float | None = None,
) -> list[LayoutResult]:
    """Solve grid-snapped layouts with optional fixed rectangles and axes.

    ``required_adjacency_groups`` expands higher-level constraints such as
    "logistics must touch production". Each group is ``(name, left_rooms,
    right_rooms)`` and requires at least one room pair to share a boundary.
    ``forbidden_adjacency_groups`` uses the same shape and prevents every pair
    from touching. Room-level LayoutIR relations remain supported unchanged.
    ``minimum_adjacency_mm`` can reserve extra room beside a generated opening
    for a wider facility flow corridor.
    ``equipment_fit_options`` adds necessary room width/height constraints for
    fixed room equipment; the second-stage equipment solver still packs all
    clearances exactly.
    """

    if variants < 1:
        raise ValueError("variants must be at least 1")
    try:
        from ortools.sat.python import cp_model
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("Install the project dependencies: pip install -e .") from exc

    fixed_rects = fixed_rects or {}
    unknown_fixed = set(fixed_rects) - {room.id for room in spec.rooms}
    if unknown_fixed:
        raise InfeasibleLayout(f"Cannot fix unknown rooms: {', '.join(sorted(unknown_fixed))}")
    axis_room_ids = set(axis_aligned_room_ids or ())
    unknown_axis_rooms = axis_room_ids - {room.id for room in spec.rooms}
    if unknown_axis_rooms:
        raise InfeasibleLayout(f"Cannot bind unknown rooms to axes: {', '.join(sorted(unknown_axis_rooms))}")

    known_room_ids = {room.id for room in spec.rooms}
    required_groups = _normalise_adjacency_groups(required_adjacency_groups, known_room_ids, "required")
    forbidden_groups = _normalise_adjacency_groups(forbidden_adjacency_groups, known_room_ids, "forbidden")
    room_fit_options = _normalise_equipment_fit_options(equipment_fit_options, known_room_ids)
    adjacency_width_mm = spec.door_width_mm if minimum_adjacency_mm is None else float(minimum_adjacency_mm)
    if adjacency_width_mm <= 0:
        raise ValueError("minimum_adjacency_mm must be positive")

    model = cp_model.CpModel()
    grid = spec.grid_mm
    boundary_width = _ceil_grid(spec.boundary.width_mm, grid)
    boundary_height = _ceil_grid(spec.boundary.height_mm, grid)
    room_vars: dict[str, _RoomVars] = {}
    x_intervals = []
    y_intervals = []
    area_deviations = []

    for room in spec.rooms:
        x = model.NewIntVar(0, boundary_width, f"{room.id}_x")
        y = model.NewIntVar(0, boundary_height, f"{room.id}_y")
        width = model.NewIntVar(_ceil_grid(room.min_width_mm, grid), boundary_width, f"{room.id}_width")
        height = model.NewIntVar(_ceil_grid(room.min_depth_mm, grid), boundary_height, f"{room.id}_height")
        area = model.NewIntVar(0, boundary_width * boundary_height, f"{room.id}_area")
        model.Add(x + width <= boundary_width)
        model.Add(y + height <= boundary_height)
        if room.id in axis_room_ids:
            if structural_axes_x_mm is not None:
                model.AddAllowedAssignments(
                    [x, width],
                    _axis_pairs(structural_axes_x_mm, boundary_width, grid, room.id, "x"),
                )
            if structural_axes_y_mm is not None:
                model.AddAllowedAssignments(
                    [y, height],
                    _axis_pairs(structural_axes_y_mm, boundary_height, grid, room.id, "y"),
                )
        if room.id in fixed_rects:
            fixed = fixed_rects[room.id]
            model.Add(x == _fixed_grid_value(fixed.x, grid, room.id, "x"))
            model.Add(y == _fixed_grid_value(fixed.y, grid, room.id, "y"))
            model.Add(width == _fixed_grid_value(fixed.width, grid, room.id, "width"))
            model.Add(height == _fixed_grid_value(fixed.height, grid, room.id, "height"))
        for equipment_id, fit_options in room_fit_options.get(room.id, ()):
            fit_choices = []
            for option_index, (minimum_width, minimum_height) in enumerate(fit_options):
                fit_choice = model.NewBoolVar(f"equipment_fit_{room.id}_{equipment_id}_{option_index}")
                model.Add(width >= _ceil_grid(minimum_width, grid)).OnlyEnforceIf(fit_choice)
                model.Add(height >= _ceil_grid(minimum_height, grid)).OnlyEnforceIf(fit_choice)
                fit_choices.append(fit_choice)
            if not fit_choices:
                raise InfeasibleLayout(f"Equipment fit requirements for {equipment_id} contain no orientations")
            model.AddBoolOr(fit_choices)
        if room.needs_daylight:
            _constrain_daylight_contact(
                model,
                _RoomVars(x=x, y=y, width=width, height=height, area=area),
                spec,
                boundary_width,
                boundary_height,
                room.id,
            )
        model.AddMultiplicationEquality(area, [width, height])

        min_area = _area_to_grid2(room.min_area_m2, grid, ceil=True)
        max_area = _area_to_grid2(room.max_area_m2, grid, ceil=False)
        target_area = _area_to_grid2(room.target_area_m2, grid, ceil=False)
        if max_area < min_area:
            raise InfeasibleLayout(f"Area range of room {room.id} is smaller than the grid step")
        model.Add(area >= min_area)
        model.Add(area <= max_area)
        deviation = model.NewIntVar(0, boundary_width * boundary_height, f"{room.id}_area_deviation")
        model.AddAbsEquality(deviation, area - target_area)
        area_deviations.append(deviation)

        x_end = model.NewIntVar(0, boundary_width, f"{room.id}_x_end")
        y_end = model.NewIntVar(0, boundary_height, f"{room.id}_y_end")
        model.Add(x_end == x + width)
        model.Add(y_end == y + height)
        x_intervals.append(model.NewIntervalVar(x, width, x_end, f"{room.id}_x_interval"))
        y_intervals.append(model.NewIntervalVar(y, height, y_end, f"{room.id}_y_interval"))
        room_vars[room.id] = _RoomVars(x=x, y=y, width=width, height=height, area=area)
        for index, cutout in enumerate(spec.boundary.cutouts):
            _avoid_cutout(model, x, y, width, height, cutout, grid, f"{room.id}_cutout_{index}")

    if spec.external_entry is not None:
        _constrain_external_entry(model, room_vars[spec.external_entry.room_id], spec, boundary_width, boundary_height)

    model.AddNoOverlap2D(x_intervals, y_intervals)
    minimum_shared = max(1, _ceil_grid(adjacency_width_mm, grid))
    for room_a, room_b in spec.relation_pairs("required_adjacency"):
        _add_required_adjacency(model, room_vars[room_a], room_vars[room_b], minimum_shared, f"adj_{room_a}_{room_b}")
    for room_a, room_b in spec.relation_pairs("forbidden_adjacency"):
        _add_forbidden_adjacency(model, room_vars[room_a], room_vars[room_b], f"forbid_{room_a}_{room_b}")

    for group_name, left_rooms, right_rooms in required_groups:
        indicators = [
            _add_adjacency_indicator(
                model,
                room_vars[room_a],
                room_vars[room_b],
                minimum_shared,
                f"zone_required_{group_name}_{room_a}_{room_b}",
            )
            for room_a in left_rooms
            for room_b in right_rooms
            if room_a != room_b
        ]
        if not indicators:
            raise InfeasibleLayout(f"Group adjacency {group_name} contains no valid rooms")
        model.AddBoolOr(indicators)

    for group_name, left_rooms, right_rooms in forbidden_groups:
        for room_a in left_rooms:
            for room_b in right_rooms:
                if room_a == room_b:
                    continue
                _add_forbidden_adjacency(
                    model,
                    room_vars[room_a],
                    room_vars[room_b],
                    f"zone_forbidden_{group_name}_{room_a}_{room_b}",
                )

    # Area accuracy dominates; the position tie-breaker makes one run stable.
    model.Minimize(sum(area_deviations) * 10_000 + sum(v for room in spec.rooms for v in (room_vars[room.id].x, room_vars[room.id].y)))

    results: list[LayoutResult] = []
    for variant in range(1, variants + 1):
        solver = cp_model.CpSolver()
        configure_solver(
            solver,
            seed=seed + variant - 1,
            time_limit_seconds=time_limit_seconds,
            deterministic_units=deterministic_units,
        )
        status = solver.Solve(model)
        if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            if not results:
                raise InfeasibleLayout("The solver found no feasible layout. Check areas, boundaries and adjacencies.")
            break

        placements = {room.id: _rect_from_solver(solver, room_vars[room.id], grid) for room in spec.rooms}
        results.append(LayoutResult(variant=variant, placements=placements, objective_value=int(solver.ObjectiveValue())))
        model.AddForbiddenAssignments(
            [value for room in spec.rooms for value in (room_vars[room.id].x, room_vars[room.id].y, room_vars[room.id].width, room_vars[room.id].height)],
            [[
                value
                for room in spec.rooms
                for value in (
                    solver.Value(room_vars[room.id].x),
                    solver.Value(room_vars[room.id].y),
                    solver.Value(room_vars[room.id].width),
                    solver.Value(room_vars[room.id].height),
                )
            ]],
        )
    return results


def _add_required_adjacency(model, a: _RoomVars, b: _RoomVars, minimum_shared: int, name: str) -> None:
    indicator = _add_adjacency_indicator(model, a, b, minimum_shared, name)
    model.Add(indicator == 1)


def _add_adjacency_indicator(model, a: _RoomVars, b: _RoomVars, minimum_shared: int, name: str):
    """Create a Boolean that is true exactly when a pair shares a usable edge."""

    indicator = model.NewBoolVar(f"{name}_contact")
    orientations = [
        model.NewBoolVar(f"{name}_right"),
        model.NewBoolVar(f"{name}_left"),
        model.NewBoolVar(f"{name}_above"),
        model.NewBoolVar(f"{name}_below"),
    ]
    model.AddExactlyOne(orientations).OnlyEnforceIf(indicator)
    model.AddBoolOr([indicator.Not(), *orientations])
    for orientation in orientations:
        model.AddImplication(orientation, indicator)
    right, left, above, below = orientations
    model.Add(a.x + a.width == b.x).OnlyEnforceIf(right)
    model.Add(b.x + b.width == a.x).OnlyEnforceIf(left)
    model.Add(a.y + a.height == b.y).OnlyEnforceIf(above)
    model.Add(b.y + b.height == a.y).OnlyEnforceIf(below)
    _add_overlap(model, right, a.y, a.height, b.y, b.height, minimum_shared, f"{name}_right_overlap")
    _add_overlap(model, left, a.y, a.height, b.y, b.height, minimum_shared, f"{name}_left_overlap")
    _add_overlap(model, above, a.x, a.width, b.x, b.width, minimum_shared, f"{name}_above_overlap")
    _add_overlap(model, below, a.x, a.width, b.x, b.width, minimum_shared, f"{name}_below_overlap")
    return indicator


def _add_overlap(model, orientation, a_start, a_size, b_start, b_size, minimum: int, name: str) -> None:
    # Both edges must be long enough, including when one contains the other.
    model.Add(a_size >= minimum).OnlyEnforceIf(orientation)
    model.Add(b_size >= minimum).OnlyEnforceIf(orientation)
    a_first = model.NewBoolVar(f"{name}_a_first")
    b_first = model.NewBoolVar(f"{name}_b_first")
    model.AddAtMostOne(a_first, b_first)
    model.AddImplication(a_first, orientation)
    model.AddImplication(b_first, orientation)
    model.AddBoolOr([orientation.Not(), a_first, b_first])
    model.Add(a_start <= b_start).OnlyEnforceIf([orientation, a_first])
    model.Add(a_start + a_size >= b_start + minimum).OnlyEnforceIf([orientation, a_first])
    model.Add(b_start <= a_start).OnlyEnforceIf([orientation, b_first])
    model.Add(b_start + b_size >= a_start + minimum).OnlyEnforceIf([orientation, b_first])


def _add_forbidden_adjacency(model, a: _RoomVars, b: _RoomVars, name: str) -> None:
    choices = [model.NewBoolVar(f"{name}_{suffix}") for suffix in ("left", "right", "below", "above")]
    left, right, below, above = choices
    model.AddBoolOr(choices)
    model.Add(a.x + a.width + 1 <= b.x).OnlyEnforceIf(left)
    model.Add(b.x + b.width + 1 <= a.x).OnlyEnforceIf(right)
    model.Add(a.y + a.height + 1 <= b.y).OnlyEnforceIf(below)
    model.Add(b.y + b.height + 1 <= a.y).OnlyEnforceIf(above)


def _normalise_equipment_fit_options(options, known_room_ids: set[str]):
    normalised: dict[str, tuple[tuple[str, tuple[tuple[float, float], ...]], ...]] = {}
    for room_id, requirements in (options or {}).items():
        room_id = str(room_id)
        if room_id not in known_room_ids:
            raise InfeasibleLayout(f"Equipment fit requirements reference unknown room {room_id}")
        room_requirements = []
        for equipment_id, fit_options in requirements:
            normalised_options = tuple(
                (float(minimum_width), float(minimum_height))
                for minimum_width, minimum_height in fit_options
            )
            if any(width <= 0 or height <= 0 for width, height in normalised_options):
                raise InfeasibleLayout(f"Equipment fit requirements for {equipment_id} must be positive")
            room_requirements.append((str(equipment_id), normalised_options))
        normalised[room_id] = tuple(room_requirements)
    return normalised


def _normalise_adjacency_groups(groups, known_room_ids: set[str], relation: str):
    normalised = []
    for index, raw_group in enumerate(groups or ()):
        try:
            name, left_raw, right_raw = raw_group
        except (TypeError, ValueError) as exc:
            raise InfeasibleLayout(f"Invalid {relation} group adjacency #{index}") from exc
        left = tuple(str(room_id) for room_id in left_raw)
        right = tuple(str(room_id) for room_id in right_raw)
        unknown = (set(left) | set(right)) - known_room_ids
        if unknown:
            raise InfeasibleLayout(
                f"{relation} group adjacency {name} references unknown rooms: {', '.join(sorted(unknown))}"
            )
        normalised.append((str(name), tuple(dict.fromkeys(left)), tuple(dict.fromkeys(right))))
    return tuple(normalised)


def _constrain_external_entry(model, room: _RoomVars, spec: LayoutIR, boundary_width: int, boundary_height: int) -> None:
    entry = spec.external_entry
    if entry is None:  # pragma: no cover - guarded by the caller
        return
    grid = spec.grid_mm
    required_span = entry.offset_mm + entry.width_mm / 2
    if entry.offset_mm < entry.width_mm / 2 - 1e-6:
        raise InfeasibleLayout(f"External entry {entry.id} offset is smaller than half its width")
    minimum_span = _ceil_grid(required_span, grid)
    if entry.side in {"left", "right"}:
        model.Add(room.x == 0 if entry.side == "left" else room.x + room.width == boundary_width)
        model.Add(room.height >= minimum_span)
    else:
        model.Add(room.y == 0 if entry.side == "bottom" else room.y + room.height == boundary_height)
        model.Add(room.width >= minimum_span)


def _constrain_daylight_contact(
    model,
    room: _RoomVars,
    spec: LayoutIR,
    boundary_width: int,
    boundary_height: int,
    room_id: str,
) -> None:
    """Require daylight rooms to touch the outer boundary or a cutout edge."""

    contacts = [model.NewBoolVar(f"daylight_{room_id}_{side}") for side in ("left", "right", "bottom", "top")]
    left, right, bottom, top = contacts
    model.Add(room.x == 0).OnlyEnforceIf(left)
    model.Add(room.x + room.width == boundary_width).OnlyEnforceIf(right)
    model.Add(room.y == 0).OnlyEnforceIf(bottom)
    model.Add(room.y + room.height == boundary_height).OnlyEnforceIf(top)

    for index, cutout in enumerate(spec.boundary.cutouts):
        cutout_x = _ceil_grid(cutout.x, spec.grid_mm)
        cutout_y = _ceil_grid(cutout.y, spec.grid_mm)
        cutout_right = _ceil_grid(cutout.right, spec.grid_mm)
        cutout_top = _ceil_grid(cutout.top, spec.grid_mm)
        cutout_width = cutout_right - cutout_x
        cutout_height = cutout_top - cutout_y
        cutout_contacts = {
            "cutout_left": (room.x + room.width == cutout_x, room.y, room.height, cutout_y, cutout_height),
            "cutout_right": (room.x == cutout_right, room.y, room.height, cutout_y, cutout_height),
            "cutout_bottom": (room.y + room.height == cutout_y, room.x, room.width, cutout_x, cutout_width),
            "cutout_top": (room.y == cutout_top, room.x, room.width, cutout_x, cutout_width),
        }
        for side, (edge_constraint, room_start, room_size, cutout_start, cutout_size) in cutout_contacts.items():
            contact = model.NewBoolVar(f"daylight_{room_id}_{index}_{side}")
            contacts.append(contact)
            model.Add(edge_constraint).OnlyEnforceIf(contact)
            _add_overlap(
                model,
                contact,
                room_start,
                room_size,
                cutout_start,
                cutout_size,
                1,
                f"daylight_{room_id}_{index}_{side}_overlap",
            )
    model.AddBoolOr(contacts)


def _avoid_cutout(model, x, y, width, height, cutout, grid: int, name: str) -> None:
    cutout_x = _ceil_grid(cutout.x, grid)
    cutout_y = _ceil_grid(cutout.y, grid)
    cutout_right = _ceil_grid(cutout.right, grid)
    cutout_top = _ceil_grid(cutout.top, grid)
    choices = [model.NewBoolVar(f"{name}_{suffix}") for suffix in ("left", "right", "below", "above")]
    left, right, below, above = choices
    model.AddBoolOr(choices)
    model.Add(x + width <= cutout_x).OnlyEnforceIf(left)
    model.Add(x >= cutout_right).OnlyEnforceIf(right)
    model.Add(y + height <= cutout_y).OnlyEnforceIf(below)
    model.Add(y >= cutout_top).OnlyEnforceIf(above)


def _rect_from_solver(solver, values: _RoomVars, grid: int):
    from .models import Rect

    return Rect(
        x=solver.Value(values.x) * grid,
        y=solver.Value(values.y) * grid,
        width=solver.Value(values.width) * grid,
        height=solver.Value(values.height) * grid,
    )


def _ceil_grid(value: float, grid: int) -> int:
    return max(1, math.ceil(value / grid - 1e-9))


def _area_to_grid2(area_m2: float, grid: int, ceil: bool) -> int:
    value = area_m2 * 1_000_000 / (grid * grid)
    return math.ceil(value - 1e-9) if ceil else math.floor(value + 1e-9)


def _fixed_grid_value(value: float, grid: int, room_id: str, field_name: str) -> int:
    grid_value = round(value / grid)
    if abs(value - grid_value * grid) > 1e-6:
        raise InfeasibleLayout(f"Room {room_id}: {field_name}={value} is not aligned to the {grid} mm grid")
    return int(grid_value)


def _axis_pairs(axes: Iterable[float], boundary: int, grid: int, room_id: str, axis_name: str) -> list[list[int]]:
    values = []
    for index, axis in enumerate(axes):
        values.append(_fixed_grid_value(float(axis), grid, room_id, f"{axis_name}-axis[{index}]"))
    pairs = sorted(
        {
            (left, right - left)
            for left in values
            for right in values
            if 0 <= left < right <= boundary
        }
    )
    if not pairs:
        raise InfeasibleLayout(f"Room {room_id} has no valid pair of {axis_name} axes on the boundary")
    return [list(pair) for pair in pairs]

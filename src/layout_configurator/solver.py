"""CP-SAT rectangular room solver."""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass

from .models import LayoutIR, LayoutResult, Rect


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
) -> list[LayoutResult]:
    """Solve one or more grid-snapped layouts, optionally locking room rectangles."""

    if variants < 1:
        raise ValueError("variants должен быть не меньше 1")
    try:
        from ortools.sat.python import cp_model
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("Установите зависимости проекта: pip install -e .") from exc

    fixed_rects = fixed_rects or {}
    unknown_fixed = set(fixed_rects) - {room.id for room in spec.rooms}
    if unknown_fixed:
        raise InfeasibleLayout(f"Нельзя зафиксировать неизвестные комнаты: {', '.join(sorted(unknown_fixed))}")

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
        if room.id in fixed_rects:
            fixed = fixed_rects[room.id]
            model.Add(x == _fixed_grid_value(fixed.x, grid, room.id, "x"))
            model.Add(y == _fixed_grid_value(fixed.y, grid, room.id, "y"))
            model.Add(width == _fixed_grid_value(fixed.width, grid, room.id, "width"))
            model.Add(height == _fixed_grid_value(fixed.height, grid, room.id, "height"))
        model.AddMultiplicationEquality(area, [width, height])

        min_area = _area_to_grid2(room.min_area_m2, grid, ceil=True)
        max_area = _area_to_grid2(room.max_area_m2, grid, ceil=False)
        target_area = _area_to_grid2(room.target_area_m2, grid, ceil=False)
        if max_area < min_area:
            raise InfeasibleLayout(f"Диапазон площади комнаты {room.id} меньше шага сетки")
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
    minimum_shared = max(1, _ceil_grid(spec.door_width_mm, grid))
    for room_a, room_b in spec.relation_pairs("required_adjacency"):
        _add_required_adjacency(model, room_vars[room_a], room_vars[room_b], minimum_shared, f"adj_{room_a}_{room_b}")
    for room_a, room_b in spec.relation_pairs("forbidden_adjacency"):
        _add_forbidden_adjacency(model, room_vars[room_a], room_vars[room_b], f"forbid_{room_a}_{room_b}")

    # Area accuracy dominates; the position tie-breaker makes one run stable.
    model.Minimize(sum(area_deviations) * 10_000 + sum(v for room in spec.rooms for v in (room_vars[room.id].x, room_vars[room.id].y)))

    results: list[LayoutResult] = []
    for variant in range(1, variants + 1):
        solver = cp_model.CpSolver()
        solver.parameters.max_time_in_seconds = float(time_limit_seconds)
        solver.parameters.random_seed = seed + variant - 1
        solver.parameters.num_search_workers = 1
        solver.parameters.log_search_progress = False
        status = solver.Solve(model)
        if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            if not results:
                raise InfeasibleLayout("Солвер не нашёл допустимую планировку. Проверьте площади, границы и смежности.")
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
    orientations = [model.NewBoolVar(f"{name}_right"), model.NewBoolVar(f"{name}_left"), model.NewBoolVar(f"{name}_above"), model.NewBoolVar(f"{name}_below")]
    model.AddExactlyOne(orientations)
    right, left, above, below = orientations
    model.Add(a.x + a.width == b.x).OnlyEnforceIf(right)
    model.Add(b.x + b.width == a.x).OnlyEnforceIf(left)
    model.Add(a.y + a.height == b.y).OnlyEnforceIf(above)
    model.Add(b.y + b.height == a.y).OnlyEnforceIf(below)
    _add_overlap(model, right, a.y, a.height, b.y, b.height, minimum_shared, f"{name}_right_overlap")
    _add_overlap(model, left, a.y, a.height, b.y, b.height, minimum_shared, f"{name}_left_overlap")
    _add_overlap(model, above, a.x, a.width, b.x, b.width, minimum_shared, f"{name}_above_overlap")
    _add_overlap(model, below, a.x, a.width, b.x, b.width, minimum_shared, f"{name}_below_overlap")


def _add_overlap(model, orientation, a_start, a_size, b_start, b_size, minimum: int, name: str) -> None:
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
        raise InfeasibleLayout(f"Комната {room_id}: {field_name}={value} не попадает на сетку {grid} мм")
    return int(grid_value)

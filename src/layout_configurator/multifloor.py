"""Deterministic multi-floor coordination over the single-floor solver.

Each floor still owns a normal ``LayoutIR``. This module adds only the
cross-floor constraints: repeated vertical-core rooms are locked to the same
rectangle, optional structural axes are checked, and stair clear width is
validated. It never invents an independent structural calculation.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from .models import LayoutIR, LayoutResult, Rect
from .solver import InfeasibleLayout, solve_layouts
from .validation import validate_layout


@dataclass(frozen=True)
class FloorSpec:
    level: int
    layout: LayoutIR
    elevation_mm: float


@dataclass(frozen=True)
class VerticalCoreSpec:
    id: str
    room_ids: tuple[str, ...]
    stair_width_mm: float = 1_000
    stair_run_length_mm: float = 2_800


@dataclass(frozen=True)
class MultiFloorSpec:
    project_name: str
    floors: tuple[FloorSpec, ...]
    vertical_cores: tuple[VerticalCoreSpec, ...]
    structural_axes_x_mm: tuple[float, ...] = ()
    structural_axes_y_mm: tuple[float, ...] = ()
    floor_height_mm: float = 2_800

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> "MultiFloorSpec":
        if not isinstance(raw, Mapping):
            raise ValueError("Multi-floor root must be an object")
        payload = raw.get("multi_floor", raw)
        if not isinstance(payload, Mapping):
            raise ValueError("multi_floor must be an object")
        floors_raw = payload.get("floors", ())
        if not isinstance(floors_raw, (list, tuple)) or len(floors_raw) < 2:
            raise ValueError("Multi-floor specification needs at least two floors")
        floors: list[FloorSpec] = []
        levels: set[int] = set()
        for index, item in enumerate(floors_raw):
            if not isinstance(item, Mapping):
                raise ValueError(f"floors[{index}] must be an object")
            level = int(item.get("level", index))
            if level in levels:
                raise ValueError(f"Duplicate floor level: {level}")
            levels.add(level)
            layout_raw = item.get("layout", item.get("spec"))
            if not isinstance(layout_raw, Mapping):
                raise ValueError(f"floors[{index}].layout must be an object")
            layout = LayoutIR.from_mapping(layout_raw)
            elevation = float(item.get("elevation_mm", level * float(payload.get("floor_height_mm", 2_800))))
            floors.append(FloorSpec(level, layout, elevation))
        floors.sort(key=lambda item: item.level)
        if any(left.elevation_mm >= right.elevation_mm for left, right in zip(floors, floors[1:])):
            raise ValueError("Floor elevations must increase with level")

        first = floors[0].layout
        for floor in floors[1:]:
            if (
                floor.layout.boundary.width_mm != first.boundary.width_mm
                or floor.layout.boundary.height_mm != first.boundary.height_mm
                or floor.layout.grid_mm != first.grid_mm
            ):
                raise ValueError("All floors must use the same boundary and grid")

        cores_raw = payload.get("vertical_cores", ())
        if not isinstance(cores_raw, (list, tuple)):
            raise ValueError("vertical_cores must be a list")
        cores: list[VerticalCoreSpec] = []
        core_rooms: set[tuple[int, str]] = set()
        for index, item in enumerate(cores_raw):
            if not isinstance(item, Mapping):
                raise ValueError(f"vertical_cores[{index}] must be an object")
            core_id = str(item.get("id", "")).strip()
            room_ids_raw = item.get("room_ids", item.get("rooms", ()))
            if not core_id or not isinstance(room_ids_raw, (list, tuple)):
                raise ValueError(f"vertical_cores[{index}] needs id and room_ids")
            room_ids = tuple(str(room_id).strip() for room_id in room_ids_raw)
            if len(room_ids) != len(floors) or not all(room_ids):
                raise ValueError(f"Vertical core {core_id} must name one room per floor")
            if any(room_id not in floor.layout.room_by_id for floor, room_id in zip(floors, room_ids)):
                raise ValueError(f"Vertical core {core_id} references a missing room")
            if any((floor_index, room_id) in core_rooms for floor_index, room_id in enumerate(room_ids)):
                raise ValueError(f"A room cannot belong to more than one vertical core: {core_id}")
            core_rooms.update((floor_index, room_id) for floor_index, room_id in enumerate(room_ids))
            if any(existing.id == core_id for existing in cores):
                raise ValueError(f"Duplicate vertical core id: {core_id}")
            stair_width = float(item.get("stair_width_mm", 1_000))
            stair_run_length = float(item.get("stair_run_length_mm", 2_800))
            if stair_width <= 0 or stair_run_length <= 0:
                raise ValueError(f"Vertical core {core_id} stair dimensions must be positive")
            cores.append(
                VerticalCoreSpec(
                    core_id,
                    room_ids,
                    stair_width,
                    stair_run_length,
                )
            )
        if not cores:
            raise ValueError("Multi-floor specification needs at least one vertical core")
        floor_height = float(payload.get("floor_height_mm", 2_800))
        if floor_height <= 0:
            raise ValueError("floor_height_mm must be positive")
        return cls(
            project_name=str(payload.get("project_name", first.project_name)),
            floors=tuple(floors),
            vertical_cores=tuple(cores),
            structural_axes_x_mm=_number_tuple(payload.get("structural_axes_x_mm", ()), "structural_axes_x_mm"),
            structural_axes_y_mm=_number_tuple(payload.get("structural_axes_y_mm", ()), "structural_axes_y_mm"),
            floor_height_mm=floor_height,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "project_name": self.project_name,
            "floor_height_mm": self.floor_height_mm,
            "structural_axes_x_mm": list(self.structural_axes_x_mm),
            "structural_axes_y_mm": list(self.structural_axes_y_mm),
            "floors": [
                {"level": floor.level, "elevation_mm": floor.elevation_mm, "layout": floor.layout.to_dict()}
                for floor in self.floors
            ],
            "vertical_cores": [
                {
                    "id": core.id,
                    "room_ids": list(core.room_ids),
                    "stair_width_mm": core.stair_width_mm,
                    "stair_run_length_mm": core.stair_run_length_mm,
                }
                for core in self.vertical_cores
            ],
        }


@dataclass(frozen=True)
class MultiFloorIssue:
    code: str
    message: str


@dataclass(frozen=True)
class MultiFloorValidationReport:
    issues: tuple[MultiFloorIssue, ...]

    @property
    def ok(self) -> bool:
        return not self.issues

    def to_dict(self) -> dict[str, Any]:
        return {"ok": self.ok, "issues": [{"code": item.code, "message": item.message} for item in self.issues]}


@dataclass(frozen=True)
class MultiFloorResult:
    spec: MultiFloorSpec
    floors: tuple[LayoutResult, ...]
    report: MultiFloorValidationReport

    def to_dict(self) -> dict[str, Any]:
        return {
            "project_name": self.spec.project_name,
            "spec": self.spec.to_dict(),
            "floors": [result.to_dict() for result in self.floors],
            "validation": self.report.to_dict(),
        }


def solve_multifloor(
    spec: MultiFloorSpec,
    time_limit_seconds: float = 30,
    seed: int = 42,
) -> MultiFloorResult:
    """Solve floors in order while locking every repeated vertical core."""

    results: list[LayoutResult] = []
    first_result: LayoutResult | None = None
    for index, floor in enumerate(spec.floors):
        fixed: dict[str, Rect] = {}
        if first_result is not None:
            for core in spec.vertical_cores:
                first_room_id = core.room_ids[0]
                room_id = core.room_ids[index]
                if first_room_id not in first_result.placements:
                    raise InfeasibleLayout(f"Vertical core {core.id} is missing on the first floor")
                fixed[room_id] = first_result.placements[first_room_id]
        core_room_ids = {core.room_ids[index] for core in spec.vertical_cores}
        result = solve_layouts(
            floor.layout,
            variants=1,
            time_limit_seconds=time_limit_seconds,
            seed=seed + index,
            fixed_rects=fixed,
            axis_aligned_room_ids=core_room_ids,
            structural_axes_x_mm=spec.structural_axes_x_mm or None,
            structural_axes_y_mm=spec.structural_axes_y_mm or None,
        )[0]
        if first_result is None:
            first_result = result
        results.append(result)
    report = validate_multifloor(spec, tuple(results))
    if not report.ok:
        details = "; ".join(f"{issue.code}: {issue.message}" for issue in report.issues)
        raise InfeasibleLayout(f"Multi-floor coordination failed: {details}")
    return MultiFloorResult(spec, tuple(results), report)


def validate_multifloor(spec: MultiFloorSpec, results: tuple[LayoutResult, ...]) -> MultiFloorValidationReport:
    issues: list[MultiFloorIssue] = []
    if len(results) != len(spec.floors):
        issues.append(MultiFloorIssue("FLOOR_COUNT", "Number of floor results does not match the specification"))
        return MultiFloorValidationReport(tuple(issues))
    for floor, result in zip(spec.floors, results):
        report = validate_layout(floor.layout, result)
        issues.extend(MultiFloorIssue(f"FLOOR_{floor.level}_{item.code}", item.message) for item in report.issues)

    for core in spec.vertical_cores:
        rectangles: list[Rect] = []
        for index, room_id in enumerate(core.room_ids):
            rect = results[index].placements.get(room_id)
            if rect is None:
                issues.append(MultiFloorIssue("VERTICAL_CORE_MISSING", f"Core {core.id} room {room_id} is missing on floor {spec.floors[index].level}"))
            else:
                rectangles.append(rect)
                if min(rect.width, rect.height) + 1e-6 < core.stair_width_mm:
                    issues.append(MultiFloorIssue("STAIR_CLEAR_WIDTH", f"Core {core.id} on floor {spec.floors[index].level} is narrower than stair width {core.stair_width_mm:.0f} mm"))
        if rectangles:
            base = rectangles[0]
            for index, rect in enumerate(rectangles[1:], start=1):
                if not _same_rect(base, rect):
                    issues.append(MultiFloorIssue("VERTICAL_CORE_ALIGNMENT", f"Core {core.id} is not coaxial between levels {spec.floors[0].level} and {spec.floors[index].level}"))

    for axis_name, axes, coordinate in (
        ("x", spec.structural_axes_x_mm, lambda rect: (rect.x, rect.right)),
        ("y", spec.structural_axes_y_mm, lambda rect: (rect.y, rect.top)),
    ):
        for core in spec.vertical_cores:
            for index, room_id in enumerate(core.room_ids):
                rect = results[index].placements.get(room_id)
                if rect is None or not axes:
                    continue
                if not all(any(abs(value - axis) <= 1e-6 for axis in axes) for value in coordinate(rect)):
                    issues.append(MultiFloorIssue("STRUCTURAL_AXIS_ALIGNMENT", f"Core {core.id} room {room_id} edges are not on declared {axis_name}-axes"))
    return MultiFloorValidationReport(tuple(issues))


def _same_rect(left: Rect, right: Rect) -> bool:
    return all(abs(a - b) <= 1e-6 for a, b in zip((left.x, left.y, left.width, left.height), (right.x, right.y, right.width, right.height)))


def _number_tuple(value: Any, field_name: str) -> tuple[float, ...]:
    if not isinstance(value, (list, tuple)):
        raise ValueError(f"{field_name} must be a list")
    result = tuple(float(item) for item in value)
    if any(item < 0 for item in result):
        raise ValueError(f"{field_name} must contain non-negative coordinates")
    return result

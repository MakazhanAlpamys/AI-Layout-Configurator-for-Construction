"""Deterministic equipment placement and clearance validation."""

from __future__ import annotations

from dataclasses import dataclass
import math

from shapely.geometry import box

from .building import BuildingIR, EquipmentSpec
from .models import LayoutResult, Rect
from .walls import build_wall_plan


class EquipmentPlacementError(RuntimeError):
    """Raised when an equipment program cannot be placed without collisions."""


@dataclass(frozen=True)
class EquipmentPlacement:
    """Solver-produced equipment footprint inside one host room."""

    equipment_id: str
    room_id: str
    rect: Rect
    rotated: bool = False

    def to_dict(self, spec: EquipmentSpec) -> dict[str, object]:
        clearance = clearance_rect(spec, self.rect, self.rotated)
        return {
            "equipment_id": self.equipment_id,
            "room_id": self.room_id,
            "x": self.rect.x,
            "y": self.rect.y,
            "width": self.rect.width,
            "depth": self.rect.height,
            "rotated": self.rotated,
            "clearance": {
                "x": clearance.x,
                "y": clearance.y,
                "width": clearance.width,
                "depth": clearance.height,
            },
        }


@dataclass(frozen=True)
class EquipmentLayoutResult:
    """All equipment placements for one already-solved room layout."""

    placements: tuple[EquipmentPlacement, ...]

    def to_dict(self, building: BuildingIR) -> dict[str, object]:
        specs = {item.id: item for item in building.equipment}
        return {
            "equipment": [placement.to_dict(specs[placement.equipment_id]) for placement in self.placements]
        }


@dataclass(frozen=True)
class EquipmentValidationIssue:
    code: str
    message: str
    equipment_id: str | None = None


@dataclass(frozen=True)
class EquipmentValidationReport:
    issues: tuple[EquipmentValidationIssue, ...]

    @property
    def ok(self) -> bool:
        return not self.issues


def place_equipment(building: BuildingIR, layout: LayoutResult) -> EquipmentLayoutResult:
    """Place equipment on the construction grid using deterministic first-fit packing.

    The room solver owns room coordinates.  This second-stage packer only chooses
    equipment coordinates inside those rooms, respecting wall thickness,
    service clearances, anchors and already placed equipment.
    """

    placements: list[EquipmentPlacement] = []
    occupied: list[tuple[EquipmentPlacement, Rect]] = []
    ordered = sorted(building.equipment, key=lambda item: (not item.fixed, item.id))

    for item in ordered:
        candidate_rooms = _candidate_rooms(building, item)
        placed: EquipmentPlacement | None = None
        for room_id in candidate_rooms:
            room_rect = layout.placements.get(room_id)
            if room_rect is None:
                continue
            usable = _usable_room_rect(room_rect, building.layout.wall_thickness_mm)
            for rotated in _orientations(item):
                for physical, clearance in _candidate_rects(item, usable, rotated, building.layout.grid_mm):
                    if any(clearance.intersection_area(other_clearance) > 1e-6 for _, other_clearance in occupied):
                        continue
                    candidate = EquipmentPlacement(item.id, room_id, physical, rotated)
                    placed = candidate
                    occupied.append((candidate, clearance))
                    placements.append(candidate)
                    break
                if placed is not None:
                    break
            if placed is not None:
                break
        if placed is None:
            room_text = ", ".join(candidate_rooms) or "no eligible room"
            raise EquipmentPlacementError(
                f"Equipment {item.id} cannot be placed with its footprint and clearance; candidates: {room_text}"
            )

    result = EquipmentLayoutResult(tuple(placements))
    report = validate_equipment_layout(building, layout, result)
    if not report.ok:
        details = "; ".join(issue.message for issue in report.issues)
        raise EquipmentPlacementError(f"Equipment placement failed independent validation: {details}")
    return result


def validate_equipment_layout(
    building: BuildingIR,
    layout: LayoutResult,
    equipment_layout: EquipmentLayoutResult,
    *,
    check_walls: bool = True,
) -> EquipmentValidationReport:
    """Independently validate footprint, clearance, host and wall collisions."""

    issues: list[EquipmentValidationIssue] = []
    specs = {item.id: item for item in building.equipment}
    placements = equipment_layout.placements
    seen: set[str] = set()
    wall_geometry = None
    if check_walls:
        try:
            wall_geometry = build_wall_plan(building.layout, layout).geometry
        except (KeyError, ValueError):
            issues.append(EquipmentValidationIssue("wall_geometry", "Cannot build wall geometry for the supplied room layout"))

    for placement in placements:
        item = specs.get(placement.equipment_id)
        if item is None:
            issues.append(
                EquipmentValidationIssue(
                    "unknown_equipment", f"Placement references unknown equipment {placement.equipment_id}", placement.equipment_id
                )
            )
            continue
        if placement.equipment_id in seen:
            issues.append(
                EquipmentValidationIssue("duplicate_equipment", f"Equipment {placement.equipment_id} has multiple placements", placement.equipment_id)
            )
        seen.add(placement.equipment_id)
        allowed_rooms = _candidate_rooms(building, item)
        if placement.room_id not in allowed_rooms:
            issues.append(
                EquipmentValidationIssue(
                    "invalid_host_room",
                    f"Equipment {item.id} is placed in {placement.room_id}; allowed rooms: {', '.join(allowed_rooms)}",
                    item.id,
                )
            )
            continue
        if not item.rotation_allowed and placement.rotated:
            issues.append(
                EquipmentValidationIssue(
                    "rotation_not_allowed",
                    f"Equipment {item.id} is rotated although rotation_allowed=false",
                    item.id,
                )
            )
        room_rect = layout.placements.get(placement.room_id)
        if room_rect is None:
            issues.append(EquipmentValidationIssue("missing_host_room", f"Host room {placement.room_id} has no placement", item.id))
            continue
        usable = _usable_room_rect(room_rect, building.layout.wall_thickness_mm)
        expected_width, expected_depth, _, _, _, _ = _oriented_dimensions(item, placement.rotated)
        if abs(placement.rect.width - expected_width) > 1e-6 or abs(placement.rect.height - expected_depth) > 1e-6:
            issues.append(
                EquipmentValidationIssue(
                    "footprint_mismatch",
                    f"Equipment {item.id} footprint does not match its specification",
                    item.id,
                )
            )
        clearance = clearance_rect(item, placement.rect, placement.rotated)
        _validate_anchor(item, placement, usable, issues)
        if not _contains(usable, placement.rect):
            issues.append(
                EquipmentValidationIssue("equipment_outside_room", f"Equipment {item.id} footprint leaves usable room area", item.id)
            )
        if not _contains(usable, clearance):
            issues.append(
                EquipmentValidationIssue("clearance_outside_room", f"Equipment {item.id} clearance leaves usable room area", item.id)
            )
        if wall_geometry is not None and wall_geometry.intersection(box(clearance.x, clearance.y, clearance.right, clearance.top)).area > 1e-6:
            issues.append(
                EquipmentValidationIssue("wall_collision", f"Equipment {item.id} clearance intersects wall geometry", item.id)
            )

    missing = sorted(set(specs) - seen)
    for equipment_id in missing:
        issues.append(EquipmentValidationIssue("missing_equipment", f"Equipment {equipment_id} has no placement", equipment_id))

    for index, left in enumerate(placements):
        left_spec = specs.get(left.equipment_id)
        if left_spec is None:
            continue
        left_clearance = clearance_rect(left_spec, left.rect, left.rotated)
        for right in placements[index + 1 :]:
            right_spec = specs.get(right.equipment_id)
            if right_spec is None:
                continue
            right_clearance = clearance_rect(right_spec, right.rect, right.rotated)
            if left_clearance.intersection_area(right_clearance) > 1e-6:
                issues.append(
                    EquipmentValidationIssue(
                        "clearance_collision",
                        f"Clearance zones of {left.equipment_id} and {right.equipment_id} overlap",
                        left.equipment_id,
                    )
                )
    return EquipmentValidationReport(tuple(issues))


def _validate_anchor(
    item: EquipmentSpec,
    placement: EquipmentPlacement,
    usable: Rect,
    issues: list[EquipmentValidationIssue],
) -> None:
    if item.anchor_side is None:
        return
    width, depth, left, right, bottom, top = _oriented_dimensions(item, placement.rotated)
    expected = {
        "left": (usable.x + left, usable.y + bottom + item.anchor_offset_mm),
        "right": (usable.right - right - width, usable.y + bottom + item.anchor_offset_mm),
        "bottom": (usable.x + left + item.anchor_offset_mm, usable.y + bottom),
        "top": (usable.x + left + item.anchor_offset_mm, usable.top - top - depth),
    }[item.anchor_side]
    if abs(placement.rect.x - expected[0]) > 1e-6 or abs(placement.rect.y - expected[1]) > 1e-6:
        issues.append(
            EquipmentValidationIssue(
                "anchor_mismatch",
                f"Equipment {item.id} does not satisfy its {item.anchor_side} wall anchor",
                item.id,
            )
        )


def clearance_rect(spec: EquipmentSpec, physical: Rect, rotated: bool = False) -> Rect:
    """Return the axis-aligned service envelope around an equipment footprint."""

    _, _, left, right, bottom, top = _oriented_dimensions(spec, rotated)
    return Rect(
        physical.x - left,
        physical.y - bottom,
        physical.width + left + right,
        physical.height + bottom + top,
    )


def _candidate_rooms(building: BuildingIR, item: EquipmentSpec) -> tuple[str, ...]:
    if item.room_id is not None:
        return (item.room_id,)
    assert item.zone_id is not None
    zone_ids = {item.zone_id}
    changed = True
    while changed:
        changed = False
        for zone in building.zones:
            if zone.parent_zone_id in zone_ids and zone.id not in zone_ids:
                zone_ids.add(zone.id)
                changed = True
    room_ids = [room.id for room in building.layout.rooms if any(room.id in zone.room_ids for zone in building.zones if zone.id in zone_ids)]
    return tuple(room_ids)


def _orientations(item: EquipmentSpec) -> tuple[bool, ...]:
    return (False, True) if item.rotation_allowed and item.width_mm != item.depth_mm else (False,)


def _candidate_rects(item: EquipmentSpec, usable: Rect, rotated: bool, grid: int):
    width, depth, left, right, bottom, top = _oriented_dimensions(item, rotated)
    x_min = usable.x + left
    x_max = usable.right - right - width
    y_min = usable.y + bottom
    y_max = usable.top - top - depth
    if x_min > x_max + 1e-6 or y_min > y_max + 1e-6:
        return

    if item.anchor_side is not None:
        if item.anchor_side == "left":
            x_values = (x_min,)
            y_values = (y_min + item.anchor_offset_mm,)
        elif item.anchor_side == "right":
            x_values = (x_max,)
            y_values = (y_min + item.anchor_offset_mm,)
        elif item.anchor_side == "bottom":
            x_values = (x_min + item.anchor_offset_mm,)
            y_values = (y_min,)
        else:
            x_values = (x_min + item.anchor_offset_mm,)
            y_values = (y_max,)
    else:
        x_values = _grid_values(x_min, x_max, grid)
        y_values = _grid_values(y_min, y_max, grid)

    for y in y_values:
        for x in x_values:
            physical = Rect(float(x), float(y), width, depth)
            clearance = clearance_rect(item, physical, rotated)
            if _contains(usable, clearance):
                yield physical, clearance


def _oriented_dimensions(item: EquipmentSpec, rotated: bool):
    if not rotated:
        return (
            item.width_mm,
            item.depth_mm,
            item.clearance_left_mm,
            item.clearance_right_mm,
            item.clearance_back_mm,
            item.clearance_front_mm,
        )
    # A 90-degree counter-clockwise turn maps local front→global left and
    # local left→global bottom.  Keeping the mapping explicit makes the
    # clearance evidence reproducible instead of silently using a scalar.
    return (
        item.depth_mm,
        item.width_mm,
        item.clearance_front_mm,
        item.clearance_back_mm,
        item.clearance_left_mm,
        item.clearance_right_mm,
    )


def _usable_room_rect(room: Rect, wall_thickness_mm: float) -> Rect:
    inset = wall_thickness_mm / 2
    return Rect(room.x + inset, room.y + inset, room.width - 2 * inset, room.height - 2 * inset)


def _grid_values(low: float, high: float, grid: int) -> tuple[float, ...]:
    first = math.ceil((low - 1e-9) / grid) * grid
    last = math.floor((high + 1e-9) / grid) * grid
    if first > last:
        return ()
    return tuple(float(value) for value in range(int(first), int(last) + 1, grid))


def _contains(container: Rect, candidate: Rect) -> bool:
    return (
        candidate.x >= container.x - 1e-6
        and candidate.y >= container.y - 1e-6
        and candidate.right <= container.right + 1e-6
        and candidate.top <= container.top + 1e-6
    )

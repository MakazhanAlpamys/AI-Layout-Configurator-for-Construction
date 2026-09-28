"""Independent geometry and topology checks for solver results."""

from __future__ import annotations

from dataclasses import dataclass

from .models import LayoutIR, LayoutResult, Rect


@dataclass(frozen=True)
class ValidationIssue:
    code: str
    message: str


@dataclass(frozen=True)
class ValidationReport:
    issues: tuple[ValidationIssue, ...]

    @property
    def ok(self) -> bool:
        return not self.issues


def validate_layout(spec: LayoutIR, result: LayoutResult) -> ValidationReport:
    from shapely.geometry import box

    issues: list[ValidationIssue] = []
    boundary = Rect(0, 0, spec.boundary.width_mm, spec.boundary.height_mm)
    allowed_geometry = box(0, 0, boundary.right, boundary.top)
    for cutout in spec.boundary.cutouts:
        allowed_geometry = allowed_geometry.difference(box(cutout.x, cutout.y, cutout.right, cutout.top))
    placements = result.placements
    room_by_id = spec.room_by_id

    for room_id in sorted(set(room_by_id) - set(placements)):
        issues.append(ValidationIssue("missing_room", f"Room {room_id} is missing from the result"))
    for room_id in sorted(set(placements) - set(room_by_id)):
        issues.append(ValidationIssue("unknown_room", f"Result contains unknown room {room_id}"))

    for room_id, room in room_by_id.items():
        rect = placements.get(room_id)
        if rect is None:
            continue
        if rect.width <= 0 or rect.height <= 0:
            issues.append(ValidationIssue("invalid_rect", f"Room {room_id} has a non-positive size"))
        room_geometry = box(rect.x, rect.y, rect.right, rect.top)
        if not allowed_geometry.covers(room_geometry):
            if rect.x < 0 or rect.y < 0 or rect.right > boundary.right or rect.top > boundary.top:
                issues.append(ValidationIssue("outside_boundary", f"Room {room_id} extends beyond the bounding box"))
            else:
                issues.append(ValidationIssue("cutout_overlap", f"Room {room_id} overlaps a cutout zone"))
        if not room.fits(rect.width, rect.height):
            issues.append(ValidationIssue("min_dimension", f"Room {room_id} is smaller than the minimum dimension"))
        if not room.min_area_m2 - 1e-6 <= rect.area_m2 <= room.max_area_m2 + 1e-6:
            issues.append(
                ValidationIssue(
                    "area_range",
                    f"Area of {room_id}={rect.area_m2:.2f} m² is outside the range {room.min_area_m2:.2f}–{room.max_area_m2:.2f} m²",
                )
            )

    for index, room_a in enumerate(spec.rooms):
        rect_a = placements.get(room_a.id)
        if rect_a is None:
            continue
        for room_b in spec.rooms[index + 1 :]:
            rect_b = placements.get(room_b.id)
            if rect_b is not None and box(rect_a.x, rect_a.y, rect_a.right, rect_a.top).intersection(
                box(rect_b.x, rect_b.y, rect_b.right, rect_b.top)
            ).area > 0:
                issues.append(ValidationIssue("overlap", f"Rooms {room_a.id} and {room_b.id} overlap"))

    minimum_shared = max(1, spec.door_width_mm)
    for room_a, room_b in spec.relation_pairs("required_adjacency"):
        shared = placements[room_a].shared_boundary(placements[room_b]) if room_a in placements and room_b in placements else 0
        if shared + 1e-6 < minimum_shared:
            issues.append(ValidationIssue("required_adjacency", f"Required adjacency {room_a}–{room_b} not satisfied: shared boundary {shared:.0f} mm"))

    for room_a, room_b in spec.relation_pairs("forbidden_adjacency"):
        if room_a in placements and room_b in placements and placements[room_a].shared_boundary(placements[room_b]) > 0:
            issues.append(ValidationIssue("forbidden_adjacency", f"Forbidden adjacency {room_a}–{room_b}"))

    graph = {room.id: set() for room in spec.rooms}
    for index, room_a in enumerate(spec.rooms):
        for room_b in spec.rooms[index + 1 :]:
            if room_a.id not in placements or room_b.id not in placements:
                continue
            if placements[room_a.id].shared_boundary(placements[room_b.id]) + 1e-6 >= minimum_shared:
                graph[room_a.id].add(room_b.id)
                graph[room_b.id].add(room_a.id)

    reachable = {spec.entry_room}
    frontier = [spec.entry_room]
    while frontier:
        current = frontier.pop()
        for neighbour in graph[current]:
            if neighbour not in reachable:
                reachable.add(neighbour)
                frontier.append(neighbour)
    for room in spec.rooms:
        if room.id not in reachable:
            issues.append(ValidationIssue("unreachable_room", f"Room {room.id} is unreachable from {spec.entry_room}"))
    if not (set(room_by_id) - set(placements)):
        from .walls import build_wall_plan

        try:
            wall_plan = build_wall_plan(spec, result)
        except (KeyError, ValueError) as exc:
            issues.append(ValidationIssue("window_opening", f"Invalid window opening placement: {exc}"))
        else:
            window_rooms = {window.room_id for window in wall_plan.windows}
            for room in spec.rooms:
                if room.needs_daylight and room.id not in window_rooms:
                    issues.append(
                        ValidationIssue(
                            "daylight_opening",
                            f"Room {room.id} requires daylight, but no exterior window opening was generated",
                        )
                    )
    return ValidationReport(tuple(issues))

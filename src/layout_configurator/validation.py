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
        issues.append(ValidationIssue("missing_room", f"В результате нет комнаты {room_id}"))
    for room_id in sorted(set(placements) - set(room_by_id)):
        issues.append(ValidationIssue("unknown_room", f"В результате неизвестная комната {room_id}"))

    for room_id, room in room_by_id.items():
        rect = placements.get(room_id)
        if rect is None:
            continue
        if rect.width <= 0 or rect.height <= 0:
            issues.append(ValidationIssue("invalid_rect", f"Комната {room_id} имеет неположительный размер"))
        room_geometry = box(rect.x, rect.y, rect.right, rect.top)
        if not allowed_geometry.covers(room_geometry):
            if rect.x < 0 or rect.y < 0 or rect.right > boundary.right or rect.top > boundary.top:
                issues.append(ValidationIssue("outside_boundary", f"Комната {room_id} выходит за bounding box"))
            else:
                issues.append(ValidationIssue("cutout_overlap", f"Комната {room_id} пересекает вычитаемую зону"))
        if rect.width + 1e-6 < room.min_width_mm or rect.height + 1e-6 < room.min_depth_mm:
            issues.append(ValidationIssue("min_dimension", f"Комната {room_id} меньше минимального габарита"))
        if not room.min_area_m2 - 1e-6 <= rect.area_m2 <= room.max_area_m2 + 1e-6:
            issues.append(
                ValidationIssue(
                    "area_range",
                    f"Площадь {room_id}={rect.area_m2:.2f} м² вне диапазона {room.min_area_m2:.2f}–{room.max_area_m2:.2f} м²",
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
                issues.append(ValidationIssue("overlap", f"Комнаты {room_a.id} и {room_b.id} пересекаются"))

    minimum_shared = max(1, spec.door_width_mm)
    for room_a, room_b in spec.relation_pairs("required_adjacency"):
        shared = placements[room_a].shared_boundary(placements[room_b]) if room_a in placements and room_b in placements else 0
        if shared + 1e-6 < minimum_shared:
            issues.append(ValidationIssue("required_adjacency", f"Обязательная смежность {room_a}–{room_b} не выполнена: общая граница {shared:.0f} мм"))

    for room_a, room_b in spec.relation_pairs("forbidden_adjacency"):
        if room_a in placements and room_b in placements and placements[room_a].shared_boundary(placements[room_b]) > 0:
            issues.append(ValidationIssue("forbidden_adjacency", f"Запрещённая смежность {room_a}–{room_b}"))

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
            issues.append(ValidationIssue("unreachable_room", f"Комната {room.id} недостижима от {spec.entry_room}"))
    return ValidationReport(tuple(issues))

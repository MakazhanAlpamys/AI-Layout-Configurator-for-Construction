"""Typed, deterministic edits applied to an existing layout."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Literal

from .models import DoorSpec, LayoutIR, LayoutResult, Rect, RoomSpec, WindowSpec


class EditError(ValueError):
    """Raised when a typed edit cannot be applied to a layout state."""


@dataclass(frozen=True)
class MoveRoom:
    room_id: str
    dx_mm: float
    dy_mm: float

    def apply(self, spec: LayoutIR, result: LayoutResult) -> tuple[LayoutIR, LayoutResult]:
        rect = _room_rect(result, self.room_id)
        placements = dict(result.placements)
        placements[self.room_id] = Rect(rect.x + self.dx_mm, rect.y + self.dy_mm, rect.width, rect.height)
        return spec, replace(result, placements=placements)

    def locked_room_ids(self, spec: LayoutIR, result: LayoutResult) -> tuple[str, ...]:
        return (self.room_id,)


@dataclass(frozen=True)
class ResizeRoom:
    room_id: str
    width_mm: float
    height_mm: float
    anchor: Literal["center", "bottom_left"] = "center"

    def apply(self, spec: LayoutIR, result: LayoutResult) -> tuple[LayoutIR, LayoutResult]:
        if self.width_mm <= 0 or self.height_mm <= 0:
            raise EditError("Размеры комнаты должны быть положительными")
        if self.anchor not in {"center", "bottom_left"}:
            raise EditError(f"Неизвестная anchor-точка: {self.anchor}")
        old = _room_rect(result, self.room_id)
        if self.anchor == "center":
            x = old.x + (old.width - self.width_mm) / 2
            y = old.y + (old.height - self.height_mm) / 2
        else:
            x, y = old.x, old.y
        placements = dict(result.placements)
        placements[self.room_id] = Rect(x, y, self.width_mm, self.height_mm)
        return spec, replace(result, placements=placements)

    def locked_room_ids(self, spec: LayoutIR, result: LayoutResult) -> tuple[str, ...]:
        return (self.room_id,)


@dataclass(frozen=True)
class AddDoor:
    room_a: str
    room_b: str
    offset_mm: float | None = None
    width_mm: float | None = None
    door_id: str = ""

    def apply(self, spec: LayoutIR, result: LayoutResult) -> tuple[LayoutIR, LayoutResult]:
        if self.room_a == self.room_b:
            raise EditError("Дверь должна соединять две разные комнаты")
        _room_rect(result, self.room_a)
        _room_rect(result, self.room_b)
        room_ids = {room.id for room in spec.rooms}
        if self.room_a not in room_ids or self.room_b not in room_ids:
            raise EditError("Для двери указана неизвестная комната")

        rooms = []
        for room in spec.rooms:
            if room.id == self.room_a and self.room_b not in room.required_adjacency:
                room = replace(room, required_adjacency=room.required_adjacency + (self.room_b,))
            elif room.id == self.room_b and self.room_a not in room.required_adjacency:
                room = replace(room, required_adjacency=room.required_adjacency + (self.room_a,))
            rooms.append(room)
        explicit_door = None
        if self.offset_mm is not None or self.width_mm is not None:
            if self.offset_mm is None or self.width_mm is None:
                raise EditError("Explicit door requires both offset and width")
            if self.offset_mm < 0 or self.width_mm <= 0:
                raise EditError("Door offset must be non-negative and width must be positive")
            pair = tuple(sorted((self.room_a, self.room_b)))
            if any(tuple(sorted((door.room_a, door.room_b))) == pair for door in spec.doors):
                raise EditError(f"An explicit door already exists for {pair[0]}-{pair[1]}")
            door_id = self.door_id.strip() or f"door_{self.room_a}_{self.room_b}_{len(spec.doors) + 1}"
            if any(door.id == door_id for door in spec.doors):
                raise EditError(f"Door id already exists: {door_id}")
            explicit_door = DoorSpec(door_id, self.room_a, self.room_b, self.offset_mm, self.width_mm)
        return replace(spec, rooms=tuple(rooms), doors=spec.doors + ((explicit_door,) if explicit_door else ())), result

    def locked_room_ids(self, spec: LayoutIR, result: LayoutResult) -> tuple[str, ...]:
        # Adding a door must not silently rearrange an otherwise unchanged plan.
        return tuple(result.placements)


@dataclass(frozen=True)
class RemoveDoor:
    door_id: str

    def apply(self, spec: LayoutIR, result: LayoutResult) -> tuple[LayoutIR, LayoutResult]:
        if not any(door.id == self.door_id for door in spec.doors):
            raise EditError(f"Explicit door id not found: {self.door_id}")
        doors = tuple(door for door in spec.doors if door.id != self.door_id)
        return replace(spec, doors=doors), result

    def locked_room_ids(self, spec: LayoutIR, result: LayoutResult) -> tuple[str, ...]:
        return tuple(result.placements)


@dataclass(frozen=True)
class AddWindow:
    room_id: str
    side: Literal["left", "right", "bottom", "top"]
    offset_mm: float
    width_mm: float
    window_id: str = ""

    def apply(self, spec: LayoutIR, result: LayoutResult) -> tuple[LayoutIR, LayoutResult]:
        _room_rect(result, self.room_id)
        if self.side not in {"left", "right", "bottom", "top"}:
            raise EditError(f"Unknown window side: {self.side}")
        if self.offset_mm < 0 or self.width_mm <= 0:
            raise EditError("Window offset must be non-negative and width must be positive")
        window_id = self.window_id.strip() or f"window_{self.room_id}_{len(spec.windows) + 1}"
        if any(window.id == window_id for window in spec.windows):
            raise EditError(f"Window id already exists: {window_id}")
        window = WindowSpec(window_id, self.room_id, self.side, self.offset_mm, self.width_mm)
        return replace(spec, windows=spec.windows + (window,)), result

    def locked_room_ids(self, spec: LayoutIR, result: LayoutResult) -> tuple[str, ...]:
        return tuple(result.placements)


@dataclass(frozen=True)
class RemoveWindow:
    window_id: str

    def apply(self, spec: LayoutIR, result: LayoutResult) -> tuple[LayoutIR, LayoutResult]:
        if not any(window.id == self.window_id for window in spec.windows):
            raise EditError(f"Window id not found: {self.window_id}")
        windows = tuple(window for window in spec.windows if window.id != self.window_id)
        return replace(spec, windows=windows), result

    def locked_room_ids(self, spec: LayoutIR, result: LayoutResult) -> tuple[str, ...]:
        return tuple(result.placements)


Command = MoveRoom | ResizeRoom | AddDoor | RemoveDoor | AddWindow | RemoveWindow


def _room_rect(result: LayoutResult, room_id: str) -> Rect:
    try:
        return result.placements[room_id]
    except KeyError as exc:
        raise EditError(f"В результате нет комнаты {room_id}") from exc

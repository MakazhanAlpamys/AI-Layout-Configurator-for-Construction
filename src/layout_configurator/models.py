"""Typed input and output model for the layout engine."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping


class SpecError(ValueError):
    """Raised when an input specification is incomplete or contradictory."""


@dataclass(frozen=True)
class Rect:
    """Axis-aligned rectangle in millimetres."""

    x: float
    y: float
    width: float
    height: float

    @property
    def right(self) -> float:
        return self.x + self.width

    @property
    def top(self) -> float:
        return self.y + self.height

    @property
    def area_mm2(self) -> float:
        return self.width * self.height

    @property
    def area_m2(self) -> float:
        return self.area_mm2 / 1_000_000

    @property
    def center(self) -> tuple[float, float]:
        return (self.x + self.width / 2, self.y + self.height / 2)

    def intersection_area(self, other: "Rect") -> float:
        width = max(0.0, min(self.right, other.right) - max(self.x, other.x))
        height = max(0.0, min(self.top, other.top) - max(self.y, other.y))
        return width * height

    def shared_boundary(self, other: "Rect", tolerance: float = 1e-6) -> float:
        """Length of a shared axis-aligned side, excluding corner contact."""

        if abs(self.right - other.x) <= tolerance or abs(other.right - self.x) <= tolerance:
            return max(0.0, min(self.top, other.top) - max(self.y, other.y))
        if abs(self.top - other.y) <= tolerance or abs(other.top - self.y) <= tolerance:
            return max(0.0, min(self.right, other.right) - max(self.x, other.x))
        return 0.0


@dataclass(frozen=True)
class RoomSpec:
    id: str
    type: str
    target_area_m2: float
    min_area_m2: float
    max_area_m2: float
    min_width_mm: float = 1_800
    min_depth_mm: float = 1_800
    required_adjacency: tuple[str, ...] = ()
    preferred_adjacency: tuple[str, ...] = ()
    forbidden_adjacency: tuple[str, ...] = ()
    needs_daylight: bool = False

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any], tolerance: float) -> "RoomSpec":
        room_id = str(raw.get("id", "")).strip()
        if not room_id:
            raise SpecError("Каждая комната должна иметь непустой id")
        target = _positive_float(raw.get("target_area", raw.get("area")), f"rooms[{room_id}].target_area")
        minimum = float(raw.get("min_area", target * (1 - tolerance)))
        maximum = float(raw.get("max_area", target * (1 + tolerance)))
        min_width = float(raw.get("min_width", 1_800))
        min_depth = float(raw.get("min_depth", 1_800))
        if minimum <= 0 or maximum < minimum or min_width <= 0 or min_depth <= 0:
            raise SpecError(f"Некорректный диапазон площади комнаты {room_id}")

        return cls(
            id=room_id,
            type=str(raw.get("type", "room")),
            target_area_m2=target,
            min_area_m2=minimum,
            max_area_m2=maximum,
            min_width_mm=min_width,
            min_depth_mm=min_depth,
            required_adjacency=_string_tuple(raw.get("required_adjacency", raw.get("adjacency", ()))),
            preferred_adjacency=_string_tuple(raw.get("preferred_adjacency", ())),
            forbidden_adjacency=_string_tuple(raw.get("forbidden_adjacency", ())),
            needs_daylight=bool(raw.get("needs_daylight", False)),
        )


@dataclass(frozen=True)
class BoundarySpec:
    width_mm: float
    height_mm: float
    cutouts: tuple[Rect, ...] = ()

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> "BoundarySpec":
        width = _positive_float(raw.get("width", raw.get("width_mm")), "boundary.width")
        height = _positive_float(raw.get("height", raw.get("height_mm")), "boundary.height")
        cutouts: list[Rect] = []
        for index, item in enumerate(raw.get("cutouts", ())):
            cutout = Rect(
                x=float(item.get("x", 0)),
                y=float(item.get("y", 0)),
                width=_positive_float(item.get("width"), f"boundary.cutouts[{index}].width"),
                height=_positive_float(item.get("height"), f"boundary.cutouts[{index}].height"),
            )
            if cutout.x < 0 or cutout.y < 0 or cutout.right > width or cutout.top > height:
                raise SpecError(f"Вырез boundary.cutouts[{index}] выходит за границы bounding box")
            cutouts.append(cutout)
        return cls(width_mm=width, height_mm=height, cutouts=tuple(cutouts))


@dataclass(frozen=True)
class LayoutIR:
    """Versioned, serialisable input contract for one single-storey layout."""

    project_name: str
    boundary: BoundarySpec
    rooms: tuple[RoomSpec, ...]
    entry_room: str
    tolerance: float = 0.03
    grid_mm: int = 100
    wall_thickness_mm: float = 200
    door_width_mm: float = 900
    version: str = "0.1"

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> "LayoutIR":
        payload = raw.get("layout", raw)
        if not isinstance(payload, Mapping):
            raise SpecError("Корень спецификации должен быть объектом")

        tolerance = float(payload.get("tolerance", 0.03))
        if not 0 < tolerance < 1:
            raise SpecError("tolerance должен быть между 0 и 1")
        rooms_raw = payload.get("rooms", ())
        if not rooms_raw:
            raise SpecError("В спецификации должна быть хотя бы одна комната")
        rooms = tuple(RoomSpec.from_mapping(item, tolerance) for item in rooms_raw)
        ids = {room.id for room in rooms}
        if len(ids) != len(rooms):
            raise SpecError("id комнат должны быть уникальными")

        entry_room = str(payload.get("entry_room", rooms[0].id))
        if entry_room not in ids:
            raise SpecError(f"entry_room {entry_room!r} не найден среди комнат")
        for room in rooms:
            for relation in (*room.required_adjacency, *room.preferred_adjacency, *room.forbidden_adjacency):
                if relation not in ids:
                    raise SpecError(f"Комната {room.id} ссылается на неизвестную комнату {relation}")

        grid = int(payload.get("grid_mm", 100))
        if grid <= 0:
            raise SpecError("grid_mm должен быть положительным целым числом")
        return cls(
            project_name=str(payload.get("project_name", payload.get("name", "Layout"))),
            boundary=BoundarySpec.from_mapping(payload.get("boundary", {})),
            rooms=rooms,
            entry_room=entry_room,
            tolerance=tolerance,
            grid_mm=grid,
            wall_thickness_mm=float(payload.get("wall_thickness_mm", 200)),
            door_width_mm=float(payload.get("door_width_mm", 900)),
            version=str(payload.get("version", "0.1")),
        )

    @property
    def room_by_id(self) -> dict[str, RoomSpec]:
        return {room.id: room for room in self.rooms}

    def relation_pairs(self, relation: str) -> set[tuple[str, str]]:
        pairs: set[tuple[str, str]] = set()
        for room in self.rooms:
            for other in getattr(room, relation):
                if room.id != other:
                    pairs.add(tuple(sorted((room.id, other))))
        return pairs

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "project_name": self.project_name,
            "tolerance": self.tolerance,
            "grid_mm": self.grid_mm,
            "wall_thickness_mm": self.wall_thickness_mm,
            "door_width_mm": self.door_width_mm,
            "entry_room": self.entry_room,
            "boundary": {
                "width": self.boundary.width_mm,
                "height": self.boundary.height_mm,
                "cutouts": [
                    {"x": c.x, "y": c.y, "width": c.width, "height": c.height}
                    for c in self.boundary.cutouts
                ],
            },
            "rooms": [
                {
                    "id": r.id,
                    "type": r.type,
                    "target_area": r.target_area_m2,
                    "min_area": r.min_area_m2,
                    "max_area": r.max_area_m2,
                    "min_width": r.min_width_mm,
                    "min_depth": r.min_depth_mm,
                    "required_adjacency": list(r.required_adjacency),
                    "preferred_adjacency": list(r.preferred_adjacency),
                    "forbidden_adjacency": list(r.forbidden_adjacency),
                    "needs_daylight": r.needs_daylight,
                }
                for r in self.rooms
            ],
        }


@dataclass(frozen=True)
class LayoutResult:
    """One solver candidate, with dimensions returned in millimetres."""

    variant: int
    placements: dict[str, Rect]
    objective_value: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "variant": self.variant,
            "objective_value": self.objective_value,
            "rooms": {
                room_id: {
                    "x": rect.x,
                    "y": rect.y,
                    "width": rect.width,
                    "height": rect.height,
                    "area_m2": rect.area_m2,
                }
                for room_id, rect in self.placements.items()
            },
        }


def _positive_float(value: Any, field_name: str) -> float:
    if value is None:
        raise SpecError(f"Не задано обязательное поле {field_name}")
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise SpecError(f"Поле {field_name} должно быть числом") from exc
    if parsed <= 0:
        raise SpecError(f"Поле {field_name} должно быть положительным")
    return parsed


def _string_tuple(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,)
    return tuple(str(item) for item in value)

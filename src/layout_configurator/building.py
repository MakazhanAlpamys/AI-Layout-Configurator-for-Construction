"""Extended domain model for regulated and industrial facility layouts.

``LayoutIR`` remains the stable single-floor room-program contract.  This
module adds the non-geometric program objects needed by the next solver layer:
zones, equipment, process flows and structural axes.  It intentionally does
not accept generated coordinates; those remain solver output.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from collections.abc import Mapping
import math
from typing import Any, Literal

from .models import LayoutIR


class BuildingSpecError(ValueError):
    """Raised when an extended building program is incomplete or contradictory."""


@dataclass(frozen=True)
class ZoneSpec:
    """Functional zone containing a group of rooms."""

    id: str
    type: str
    room_ids: tuple[str, ...] = ()
    parent_zone_id: str | None = None
    cleanroom_class: str | None = None
    pressure_pa: float | None = None
    airlock: bool = False
    required_adjacency: tuple[str, ...] = ()
    preferred_adjacency: tuple[str, ...] = ()
    forbidden_adjacency: tuple[str, ...] = ()

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any], index: int) -> "ZoneSpec":
        zone_id = _required_id(raw.get("id", f"zone_{index + 1}"), f"zones[{index}].id")
        room_ids = _ids(raw.get("room_ids", raw.get("rooms", ())), f"zones[{zone_id}].room_ids")
        parent = raw.get("parent_zone_id", raw.get("parent"))
        parent_id = None if parent in (None, "") else _required_id(parent, f"zones[{zone_id}].parent_zone_id")
        cleanroom_class_value = raw.get("cleanroom_class", raw.get("classification"))
        cleanroom_class = (
            None
            if cleanroom_class_value in (None, "")
            else str(cleanroom_class_value).strip()
        )
        pressure_value = raw.get("pressure_pa")
        pressure_pa = (
            None
            if pressure_value in (None, "")
            else _number(pressure_value, f"zones[{zone_id}].pressure_pa")
        )
        return cls(
            id=zone_id,
            type=str(raw.get("type", "zone")).strip() or "zone",
            room_ids=room_ids,
            parent_zone_id=parent_id,
            cleanroom_class=cleanroom_class,
            pressure_pa=pressure_pa,
            airlock=_bool(raw.get("airlock", False)),
            required_adjacency=_ids(raw.get("required_adjacency", ()), f"zones[{zone_id}].required_adjacency"),
            preferred_adjacency=_ids(raw.get("preferred_adjacency", ()), f"zones[{zone_id}].preferred_adjacency"),
            forbidden_adjacency=_ids(raw.get("forbidden_adjacency", ()), f"zones[{zone_id}].forbidden_adjacency"),
        )

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "id": self.id,
            "type": self.type,
            "room_ids": list(self.room_ids),
            "required_adjacency": list(self.required_adjacency),
            "preferred_adjacency": list(self.preferred_adjacency),
            "forbidden_adjacency": list(self.forbidden_adjacency),
        }
        if self.parent_zone_id is not None:
            payload["parent_zone_id"] = self.parent_zone_id
        if self.cleanroom_class is not None:
            payload["cleanroom_class"] = self.cleanroom_class
        if self.pressure_pa is not None:
            payload["pressure_pa"] = self.pressure_pa
        if self.airlock:
            payload["airlock"] = True
        return payload


@dataclass(frozen=True)
class EquipmentSpec:
    """Equipment footprint and its non-negotiable service clearances."""

    id: str
    type: str
    room_id: str | None
    zone_id: str | None
    width_mm: float
    depth_mm: float
    clearance_front_mm: float = 0.0
    clearance_back_mm: float = 0.0
    clearance_left_mm: float = 0.0
    clearance_right_mm: float = 0.0
    rotation_allowed: bool = True
    fixed: bool = False
    anchor_side: Literal["left", "right", "bottom", "top"] | None = None
    anchor_offset_mm: float = 0.0

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any], index: int) -> "EquipmentSpec":
        equipment_id = _required_id(raw.get("id", f"equipment_{index + 1}"), f"equipment[{index}].id")
        room_value = raw.get("room_id", raw.get("room"))
        zone_value = raw.get("zone_id", raw.get("zone"))
        room_id = None if room_value in (None, "") else _required_id(room_value, f"equipment[{equipment_id}].room_id")
        zone_id = None if zone_value in (None, "") else _required_id(zone_value, f"equipment[{equipment_id}].zone_id")
        if room_id is None and zone_id is None:
            raise BuildingSpecError(f"Equipment {equipment_id} must reference room_id or zone_id")
        anchor_value = raw.get("anchor_side", raw.get("anchor"))
        anchor_side = None if anchor_value in (None, "") else str(anchor_value).strip().lower()
        if anchor_side not in {None, "left", "right", "bottom", "top"}:
            raise BuildingSpecError(f"Equipment {equipment_id} has unsupported anchor_side {anchor_side!r}")
        fixed = _bool(raw.get("fixed", False))
        if fixed and (room_id is None or anchor_side is None):
            raise BuildingSpecError(
                f"Fixed equipment {equipment_id} must reference room_id and anchor_side"
            )

        shared_clearance = raw.get("clearance_mm")
        if shared_clearance is not None:
            shared = _non_negative(shared_clearance, f"equipment[{equipment_id}].clearance_mm")
        else:
            shared = 0.0
        return cls(
            id=equipment_id,
            type=str(raw.get("type", "equipment")).strip() or "equipment",
            room_id=room_id,
            zone_id=zone_id,
            width_mm=_positive(raw.get("width_mm", raw.get("width")), f"equipment[{equipment_id}].width_mm"),
            depth_mm=_positive(raw.get("depth_mm", raw.get("depth")), f"equipment[{equipment_id}].depth_mm"),
            clearance_front_mm=_non_negative(raw.get("clearance_front_mm", shared), f"equipment[{equipment_id}].clearance_front_mm"),
            clearance_back_mm=_non_negative(raw.get("clearance_back_mm", shared), f"equipment[{equipment_id}].clearance_back_mm"),
            clearance_left_mm=_non_negative(raw.get("clearance_left_mm", shared), f"equipment[{equipment_id}].clearance_left_mm"),
            clearance_right_mm=_non_negative(raw.get("clearance_right_mm", shared), f"equipment[{equipment_id}].clearance_right_mm"),
            rotation_allowed=_bool(raw.get("rotation_allowed", True)),
            fixed=fixed,
            anchor_side=anchor_side,
            anchor_offset_mm=_non_negative(
                raw.get("anchor_offset_mm", raw.get("anchor_offset", 0)),
                f"equipment[{equipment_id}].anchor_offset_mm",
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "id": self.id,
            "type": self.type,
            "width_mm": self.width_mm,
            "depth_mm": self.depth_mm,
            "clearance_front_mm": self.clearance_front_mm,
            "clearance_back_mm": self.clearance_back_mm,
            "clearance_left_mm": self.clearance_left_mm,
            "clearance_right_mm": self.clearance_right_mm,
            "rotation_allowed": self.rotation_allowed,
            "fixed": self.fixed,
            "anchor_offset_mm": self.anchor_offset_mm,
        }
        if self.room_id is not None:
            payload["room_id"] = self.room_id
        if self.zone_id is not None:
            payload["zone_id"] = self.zone_id
        if self.anchor_side is not None:
            payload["anchor_side"] = self.anchor_side
        return payload

    def room_fit_dimensions(self, wall_thickness_mm: float) -> tuple[tuple[float, float], ...]:
        """Return room dimensions that can contain this item and its envelope.

        These are necessary per-equipment room constraints for the room solver;
        the equipment solver still performs the final multi-item packing.
        """

        orientations = ((self.width_mm, self.depth_mm, self.clearance_left_mm, self.clearance_right_mm, self.clearance_back_mm, self.clearance_front_mm),)
        if self.rotation_allowed and self.width_mm != self.depth_mm:
            orientations += ((self.depth_mm, self.width_mm, self.clearance_front_mm, self.clearance_back_mm, self.clearance_left_mm, self.clearance_right_mm),)
        return tuple(
            (
                wall_thickness_mm + width + left + right,
                wall_thickness_mm + depth + bottom + top,
            )
            for width, depth, left, right, bottom, top in orientations
        )


@dataclass(frozen=True)
class FlowSpec:
    """Directed movement requirement between rooms, zones or equipment."""

    id: str
    type: str
    from_ids: tuple[str, ...]
    to_ids: tuple[str, ...]
    minimum_clear_width_mm: float = 1_200.0
    required: bool = True
    stage: str | None = None

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any], index: int) -> "FlowSpec":
        flow_id = _required_id(raw.get("id", f"flow_{index + 1}"), f"flows[{index}].id")
        from_ids = _ids(raw.get("from_ids", raw.get("from", ())), f"flows[{flow_id}].from_ids")
        to_ids = _ids(raw.get("to_ids", raw.get("to", ())), f"flows[{flow_id}].to_ids")
        if not from_ids or not to_ids:
            raise BuildingSpecError(f"Flow {flow_id} must have non-empty from and to endpoints")
        if set(from_ids) & set(to_ids):
            raise BuildingSpecError(f"Flow {flow_id} cannot have the same endpoint on both sides")
        stage_value = raw.get("stage", raw.get("process_stage"))
        stage = None if stage_value in (None, "") else str(stage_value).strip() or None
        return cls(
            id=flow_id,
            type=str(raw.get("type", raw.get("kind", "people"))).strip() or "people",
            from_ids=from_ids,
            to_ids=to_ids,
            minimum_clear_width_mm=_positive(
                raw.get("minimum_clear_width_mm", raw.get("min_clear_width_mm", 1_200)),
                f"flows[{flow_id}].minimum_clear_width_mm",
            ),
            required=_bool(raw.get("required", True)),
            stage=stage,
        )

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "id": self.id,
            "type": self.type,
            "from_ids": list(self.from_ids),
            "to_ids": list(self.to_ids),
            "minimum_clear_width_mm": self.minimum_clear_width_mm,
            "required": self.required,
        }
        if self.stage is not None:
            payload["stage"] = self.stage
        return payload


@dataclass(frozen=True)
class StructuralGridSpec:
    """Candidate structural axes shared by the future placement solver."""

    axes_x_mm: tuple[float, ...]
    axes_y_mm: tuple[float, ...]
    labels_x: tuple[str, ...] = ()
    labels_y: tuple[str, ...] = ()

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> "StructuralGridSpec":
        axes_x = _numbers(raw.get("axes_x_mm", raw.get("x", ())), "structural_grid.axes_x_mm")
        axes_y = _numbers(raw.get("axes_y_mm", raw.get("y", ())), "structural_grid.axes_y_mm")
        if not axes_x or not axes_y:
            raise BuildingSpecError("structural_grid must contain non-empty x and y axes")
        labels_x = _labels(raw.get("labels_x", ()), len(axes_x), "structural_grid.labels_x")
        labels_y = _labels(raw.get("labels_y", ()), len(axes_y), "structural_grid.labels_y")
        return cls(axes_x, axes_y, labels_x, labels_y)

    def validate_against(self, layout: LayoutIR) -> None:
        for axis_name, axes, limit in (
            ("x", self.axes_x_mm, layout.boundary.width_mm),
            ("y", self.axes_y_mm, layout.boundary.height_mm),
        ):
            if any(axis < 0 or axis > limit for axis in axes):
                raise BuildingSpecError(
                    f"structural_grid {axis_name} axes must lie inside the layout boundary 0..{limit:g}"
                )
            if tuple(sorted(set(axes))) != axes:
                raise BuildingSpecError(f"structural_grid {axis_name} axes must be strictly increasing")

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "axes_x_mm": list(self.axes_x_mm),
            "axes_y_mm": list(self.axes_y_mm),
        }
        if self.labels_x:
            payload["labels_x"] = list(self.labels_x)
        if self.labels_y:
            payload["labels_y"] = list(self.labels_y)
        return payload


@dataclass(frozen=True)
class BuildingIR:
    """Extended canonical program for a dense single-floor facility pilot."""

    layout: LayoutIR
    zones: tuple[ZoneSpec, ...] = ()
    equipment: tuple[EquipmentSpec, ...] = ()
    flows: tuple[FlowSpec, ...] = ()
    structural_grid: StructuralGridSpec | None = None
    version: str = "0.2"

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> "BuildingIR":
        if not isinstance(raw, Mapping):
            raise BuildingSpecError("BuildingIR root must be an object")
        payload = raw.get("building", raw)
        if not isinstance(payload, Mapping):
            raise BuildingSpecError("building must be an object")
        layout_raw = payload.get("layout", payload)
        if not isinstance(layout_raw, Mapping):
            raise BuildingSpecError("building.layout must be an object")
        if "project_name" not in layout_raw and payload.get("project_name"):
            layout_raw = {**layout_raw, "project_name": payload["project_name"]}
        layout = LayoutIR.from_mapping(layout_raw)

        zones = _parse_list(payload.get("zones", ()), ZoneSpec.from_mapping, "zones")
        equipment = _parse_list(payload.get("equipment", ()), EquipmentSpec.from_mapping, "equipment")
        flows = _parse_list(payload.get("flows", ()), FlowSpec.from_mapping, "flows")
        structural_raw = payload.get("structural_grid")
        structural_grid = None
        if structural_raw is not None:
            if not isinstance(structural_raw, Mapping):
                raise BuildingSpecError("structural_grid must be an object")
            structural_grid = StructuralGridSpec.from_mapping(structural_raw)
            structural_grid.validate_against(layout)

        result = cls(
            layout=layout,
            zones=zones,
            equipment=equipment,
            flows=flows,
            structural_grid=structural_grid,
            version=str(payload.get("version", "0.2")),
        )
        result._validate_references()
        return result

    def _validate_references(self) -> None:
        room_ids = {room.id for room in self.layout.rooms}
        zone_ids = _unique_ids(self.zones, "zones")
        equipment_ids = _unique_ids(self.equipment, "equipment")
        _unique_ids(self.flows, "flows")

        room_membership: dict[str, str] = {}
        for zone in self.zones:
            if zone.parent_zone_id is not None and zone.parent_zone_id not in zone_ids:
                raise BuildingSpecError(f"Zone {zone.id} references unknown parent zone {zone.parent_zone_id}")
            if zone.parent_zone_id == zone.id:
                raise BuildingSpecError(f"Zone {zone.id} cannot be its own parent")
            if zone.id in zone.required_adjacency or zone.id in zone.preferred_adjacency or zone.id in zone.forbidden_adjacency:
                raise BuildingSpecError(f"Zone {zone.id} cannot reference itself in an adjacency relation")
            for room_id in zone.room_ids:
                if room_id not in room_ids:
                    raise BuildingSpecError(f"Zone {zone.id} references unknown room {room_id}")
                previous = room_membership.get(room_id)
                if previous is not None and previous != zone.id:
                    raise BuildingSpecError(f"Room {room_id} belongs to multiple zones: {previous}, {zone.id}")
                room_membership[room_id] = zone.id
            self._validate_relation_ids(zone.id, zone.required_adjacency, zone_ids, "required_adjacency")
            self._validate_relation_ids(zone.id, zone.preferred_adjacency, zone_ids, "preferred_adjacency")
            self._validate_relation_ids(zone.id, zone.forbidden_adjacency, zone_ids, "forbidden_adjacency")

        for zone in self.zones:
            visited: set[str] = set()
            current = zone
            while current.parent_zone_id is not None:
                if current.id in visited:
                    raise BuildingSpecError(f"Zone hierarchy contains a cycle at {current.id}")
                visited.add(current.id)
                current = next(item for item in self.zones if item.id == current.parent_zone_id)

        for item in self.equipment:
            if item.room_id is not None and item.room_id not in room_ids:
                raise BuildingSpecError(f"Equipment {item.id} references unknown room {item.room_id}")
            if item.zone_id is not None and item.zone_id not in zone_ids:
                raise BuildingSpecError(f"Equipment {item.id} references unknown zone {item.zone_id}")
            if item.id in room_ids or item.id in zone_ids:
                raise BuildingSpecError(f"Equipment id {item.id} collides with a room or zone id")

        endpoint_ids = room_ids | zone_ids | equipment_ids
        for flow in self.flows:
            unknown = (set(flow.from_ids) | set(flow.to_ids)) - endpoint_ids
            if unknown:
                raise BuildingSpecError(
                    f"Flow {flow.id} references unknown endpoints: {', '.join(sorted(unknown))}"
                )

    def room_ids_for_zone(self, zone_id: str, *, include_descendants: bool = True) -> tuple[str, ...]:
        """Return stable room membership for a zone and its child zones."""

        zones_by_id = {zone.id: zone for zone in self.zones}
        if zone_id not in zones_by_id:
            raise BuildingSpecError(f"Unknown zone: {zone_id}")
        selected = {zone_id}
        if include_descendants:
            changed = True
            while changed:
                changed = False
                for zone in self.zones:
                    if zone.parent_zone_id in selected and zone.id not in selected:
                        selected.add(zone.id)
                        changed = True
        room_ids = {
            room_id
            for zone in self.zones
            if zone.id in selected
            for room_id in zone.room_ids
        }
        return tuple(room.id for room in self.layout.rooms if room.id in room_ids)

    def with_required_flow_opening_width(self) -> "BuildingIR":
        """Return a copy whose generated doors can carry required flows.

        ``LayoutIR.door_width_mm`` is the minimum generated opening width. A
        process flow can demand a wider passage, so the facility compiler
        promotes the effective width before solving instead of producing a
        geometrically plausible but unusable route. A small, explicit routing
        margin makes the swept 2D corridor unambiguous at the generated door
        jambs; it is a geometry-model margin, not a regulatory clearance. The
        canonical result keeps the promoted value visible in its evidence
        bundle.
        """

        routing_margin_mm = 200.0
        required_width = max(
            (
                flow.minimum_clear_width_mm + routing_margin_mm
                for flow in self.flows
                if flow.required
            ),
            default=self.layout.door_width_mm,
        )
        if required_width <= self.layout.door_width_mm + 1e-6:
            return self
        return replace(self, layout=replace(self.layout, door_width_mm=required_width))

    def zone_relation_groups(
        self,
        relation: Literal["required_adjacency", "preferred_adjacency", "forbidden_adjacency"],
    ) -> tuple[tuple[str, tuple[str, ...], tuple[str, ...]], ...]:
        """Expand zone relations into named groups of candidate room pairs.

        A required zone relation means that at least one room pair from the two
        zones must share a usable boundary. A forbidden relation means that no
        pair may touch. Duplicate reverse declarations are collapsed here so
        the solver and evidence report stay deterministic.
        """

        known = {zone.id for zone in self.zones}
        groups: list[tuple[str, tuple[str, ...], tuple[str, ...]]] = []
        seen: set[tuple[str, str]] = set()
        for zone in self.zones:
            for other_id in getattr(zone, relation):
                if other_id not in known:
                    continue
                pair = tuple(sorted((zone.id, other_id)))
                if pair in seen:
                    continue
                seen.add(pair)
                left = self.room_ids_for_zone(pair[0])
                right = self.room_ids_for_zone(pair[1])
                groups.append((f"{pair[0]}-{pair[1]}", left, right))
        return tuple(groups)

    @staticmethod
    def _validate_relation_ids(owner: str, relation_ids: tuple[str, ...], known: set[str], field: str) -> None:
        unknown = set(relation_ids) - known
        if unknown:
            raise BuildingSpecError(
                f"Zone {owner} {field} references unknown zones: {', '.join(sorted(unknown))}"
            )

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "version": self.version,
            "project_name": self.layout.project_name,
            "layout": self.layout.to_dict(),
            "zones": [zone.to_dict() for zone in self.zones],
            "equipment": [item.to_dict() for item in self.equipment],
            "flows": [flow.to_dict() for flow in self.flows],
        }
        if self.structural_grid is not None:
            payload["structural_grid"] = self.structural_grid.to_dict()
        return payload


def _parse_list(value: Any, factory, field_name: str):
    if value is None:
        return ()
    if not isinstance(value, (list, tuple)):
        raise BuildingSpecError(f"{field_name} must be a list")
    if not all(isinstance(item, Mapping) for item in value):
        raise BuildingSpecError(f"{field_name} items must be objects")
    return tuple(factory(item, index) for index, item in enumerate(value))


def _unique_ids(items, field_name: str) -> set[str]:
    ids = [item.id for item in items]
    if len(set(ids)) != len(ids):
        raise BuildingSpecError(f"{field_name} ids must be unique")
    return set(ids)


def _required_id(value: Any, field_name: str) -> str:
    result = str(value).strip()
    if not result:
        raise BuildingSpecError(f"{field_name} must be a non-empty id")
    return result


def _ids(value: Any, field_name: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        value = (value,)
    if not isinstance(value, (list, tuple, set, frozenset)):
        raise BuildingSpecError(f"{field_name} must be a list of ids")
    result = tuple(_required_id(item, field_name) for item in value)
    if len(set(result)) != len(result):
        raise BuildingSpecError(f"{field_name} must not contain duplicate ids")
    return result


def _numbers(value: Any, field_name: str) -> tuple[float, ...]:
    if not isinstance(value, (list, tuple)):
        raise BuildingSpecError(f"{field_name} must be a list of numbers")
    try:
        result = tuple(float(item) for item in value)
    except (TypeError, ValueError) as exc:
        raise BuildingSpecError(f"{field_name} must contain numbers") from exc
    if any(number < 0 for number in result):
        raise BuildingSpecError(f"{field_name} must contain non-negative numbers")
    if len(set(result)) != len(result):
        raise BuildingSpecError(f"{field_name} must not contain duplicate axes")
    return result


def _labels(value: Any, expected: int, field_name: str) -> tuple[str, ...]:
    if value is None:
        return ()
    labels = _ids(value, field_name)
    if labels and len(labels) != expected:
        raise BuildingSpecError(f"{field_name} must contain exactly {expected} labels")
    return labels


def _positive(value: Any, field_name: str) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise BuildingSpecError(f"{field_name} must be a number") from exc
    if result <= 0:
        raise BuildingSpecError(f"{field_name} must be positive")
    return result


def _non_negative(value: Any, field_name: str) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise BuildingSpecError(f"{field_name} must be a number") from exc
    if result < 0:
        raise BuildingSpecError(f"{field_name} must be non-negative")
    return result


def _number(value: Any, field_name: str) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise BuildingSpecError(f"{field_name} must be a number") from exc
    if not math.isfinite(result):
        raise BuildingSpecError(f"{field_name} must be finite")
    return result


def _bool(value: Any) -> bool:
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "yes", "1"}:
            return True
        if normalized in {"false", "no", "0"}:
            return False
    return bool(value)

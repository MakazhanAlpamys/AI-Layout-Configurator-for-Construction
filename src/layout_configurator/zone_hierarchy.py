"""Opt-in rectangular root-zone containment, returning canonical LayoutResult.

Each nonempty root zone includes its canonical descendant membership. Local
room/cluster CP-SAT solves provide a bounded set of shapes inside rectangular
containers; a second CP-SAT model places those containers and chooses shapes.
Unassigned rooms have explicit singleton units. The optional recursive mode
solves physical descendant containers from leaves to roots, preserving canonical
ancestry. Neither zone mode is in the auto dispatcher.

Budgets apply per CP-SAT solve; the local hierarchy can perform several solves
per option. Rectangular
containers and bounded shapes restrict the search: failure never proves that
the original building program is infeasible. Equipment packing, flow routing
and facility profile acceptance remain separate stages.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field, replace
from numbers import Real

from .building import BuildingIR
from .hierarchy import solve_layouts_hierarchical
from .models import BoundarySpec, LayoutResult, Rect
from .search import configure_solver
from .solver import (
    InfeasibleLayout,
    _RoomVars,
    _add_adjacency_indicator,
    _add_forbidden_adjacency,
    _add_required_adjacency,
    _area_to_grid2,
    _axis_pairs,
    _avoid_cutout,
    _ceil_grid,
    _constrain_automatic_window_facade,
    _constrain_external_entry,
    _fixed_grid_value,
    _layout_objective,
)
from .validation import ValidationIssue, ValidationReport, validate_layout


class ZoneHierarchyFailure(InfeasibleLayout):
    """Stage evidence for a failure of this restricted search, not BuildingIR."""

    def __init__(self, message, *, stage, code, unit_ids=(), room_ids=(),
                 model_status=None, proof_scope="bounded_candidates", issues=()):
        super().__init__(message)
        self.stage = stage
        self.code = code
        self.unit_ids = tuple(unit_ids)
        self.room_ids = tuple(room_ids)
        self.model_status = model_status
        self.proof_scope = proof_scope
        self.issues = tuple(issues)

    def to_dict(self):
        return {
            "stage": self.stage, "code": self.code, "message": str(self),
            "unit_ids": list(self.unit_ids), "room_ids": list(self.room_ids),
            "model_status": self.model_status, "proof_scope": self.proof_scope,
            "proven_building_infeasible": False, "issues": list(self.issues),
        }


class ZoneHierarchyNotApplicable(ZoneHierarchyFailure):
    """Unsupported or ambiguous input that an explicit caller may fall back on."""


@dataclass(frozen=True)
class _ZoneShape:
    width: float
    height: float
    placements: Mapping[str, Rect]
    containers: Mapping[str, Rect] = field(default_factory=dict)


def _fail(evidence, message, *, not_applicable=False, **details):
    kind = ZoneHierarchyNotApplicable if not_applicable else ZoneHierarchyFailure
    failure = kind(message, **details)
    evidence["failure"] = failure.to_dict()
    raise failure


def _normalise_room_constraints(spec, fixed_rects, axis_room_ids, axes_x, axes_y, evidence):
    """Validate before any local solve, and materialize one-shot axis iterables."""
    known = set(spec.room_by_id)
    if fixed_rects is not None and not isinstance(fixed_rects, Mapping):
        _fail(evidence, "fixed_rects must map room IDs to Rect values", stage="input",
              code="invalid_fixed_rect", model_status="NOT_BUILT", not_applicable=True)
    fixed = dict(fixed_rects or {})
    unknown = set(fixed) - known
    if unknown:
        _fail(evidence, "Cannot fix unknown rooms", stage="input", code="unknown_fixed_room",
              room_ids=sorted(unknown, key=str), model_status="NOT_BUILT", not_applicable=True)
    for room_id, rect in fixed.items():
        try:
            if not isinstance(rect, Rect):
                raise ValueError("the geometry must be a Rect")
            values = (rect.x, rect.y, rect.width, rect.height)
            if any(not isinstance(value, Real) or isinstance(value, bool) or not math.isfinite(value) for value in values):
                raise ValueError("coordinates and dimensions must be finite numeric millimetres")
            for name, value in zip(("x", "y", "width", "height"), values):
                grid_value = _fixed_grid_value(value, spec.grid_mm, room_id, name)
                if abs(grid_value) >= 1 << 60:
                    raise ValueError("coordinates exceed safe integer bounds")
            if rect.width < spec.grid_mm or rect.height < spec.grid_mm:
                raise ValueError("dimensions must be positive grid lengths")
            room = spec.room_by_id[room_id]
            if not room.fits(rect.width, rect.height):
                raise ValueError("dimensions violate the canonical room minimums")
            if not room.min_area_m2 - 1e-6 <= rect.area_m2 <= room.max_area_m2 + 1e-6:
                raise ValueError("area violates the canonical room range")
        except (InfeasibleLayout, ValueError, TypeError, OverflowError) as exc:
            _fail(evidence, f"Invalid fixed room {room_id}: {exc}", stage="input", code="invalid_fixed_rect",
                  room_ids=(room_id,), model_status="NOT_BUILT", not_applicable=True)
    try:
        if isinstance(axis_room_ids, (str, bytes)):
            raise ValueError("selected room IDs must be an iterable of IDs")
        selected = tuple(dict.fromkeys(axis_room_ids if axis_room_ids is not None else ()))
        unknown = set(selected) - known
    except (ValueError, TypeError) as exc:
        _fail(evidence, f"Invalid selected axis rooms: {exc}", stage="input", code="invalid_axis_rooms",
              model_status="NOT_BUILT", not_applicable=True)
    if unknown:
        _fail(evidence, "Cannot bind unknown rooms to axes", stage="input", code="unknown_axis_room",
              room_ids=sorted(unknown, key=str), model_status="NOT_BUILT", not_applicable=True)
    axes = {}
    # As in the monolithic backend, unused axis arguments impose no constraints.
    for name, supplied, boundary in (("x", axes_x, spec.boundary.width_mm), ("y", axes_y, spec.boundary.height_mm)):
        if not selected or supplied is None:
            continue
        try:
            values = tuple(supplied)
            if any(not isinstance(value, Real) or isinstance(value, bool) or not math.isfinite(value) for value in values):
                raise ValueError("axes must be finite numeric millimetres")
            _axis_pairs(values, _ceil_grid(boundary, spec.grid_mm), spec.grid_mm, selected[0], name)
            axes[name] = values
        except (InfeasibleLayout, ValueError, TypeError, OverflowError) as exc:
            _fail(evidence, f"Invalid {name} structural axes: {exc}", stage="input", code="invalid_axes",
                  room_ids=selected, model_status="NOT_BUILT", not_applicable=True)
    return fixed, selected, axes


def _root_units(building, evidence):
    zones = {zone.id: zone for zone in building.zones}
    known_rooms = {room.id for room in building.layout.rooms}
    if len(zones) != len(building.zones):
        _fail(evidence, "Zone IDs must be unique", stage="membership", code="duplicate_zone_id", not_applicable=True)
    ancestors = {}
    for zone in building.zones:
        lineage = set()
        current = zone
        while current.parent_zone_id is not None:
            parent = current.parent_zone_id
            if parent not in zones or parent == zone.id or parent in lineage:
                _fail(evidence, f"Invalid zone ancestry for {zone.id}", stage="membership", code="invalid_ancestry",
                      unit_ids=(zone.id,), not_applicable=True)
            lineage.add(parent)
            current = zones[parent]
        ancestors[zone.id] = lineage
        unknown = set(zone.room_ids) - known_rooms
        if unknown:
            _fail(evidence, f"Zone {zone.id} has unknown rooms", stage="membership", code="unknown_room",
                  unit_ids=(zone.id,), room_ids=sorted(unknown), not_applicable=True)
        for relation in (zone.required_adjacency, zone.forbidden_adjacency, zone.preferred_adjacency):
            if set(relation) - set(zones) or zone.id in relation:
                _fail(evidence, f"Invalid relation of zone {zone.id}", stage="membership", code="invalid_relation",
                      unit_ids=(zone.id,), not_applicable=True)
    owners = {}
    for zone in building.zones:
        for room_id in zone.room_ids:
            for other in owners.get(room_id, ()):
                if other not in ancestors[zone.id] and zone.id not in ancestors[other]:
                    _fail(evidence, f"Room {room_id} belongs to unrelated zones {other} and {zone.id}",
                          stage="membership", code="ambiguous_membership", unit_ids=(other, zone.id),
                          room_ids=(room_id,), not_applicable=True)
            owners.setdefault(room_id, []).append(zone.id)
    units = {}
    empty = []
    for zone in building.zones:
        if zone.parent_zone_id is not None:
            continue
        members = building.room_ids_for_zone(zone.id)
        if members:
            units[zone.id] = members
        else:
            empty.append(zone.id)
    assigned = {room_id for members in units.values() for room_id in members}
    descriptions = [{"id": unit_id, "kind": "root_zone", "room_ids": list(members)} for unit_id, members in units.items()]
    for room in building.layout.rooms:
        if room.id in assigned:
            continue
        unit_id = f"room:{room.id}"
        while unit_id in zones or unit_id in units:
            unit_id = f"room:{unit_id}"
        units[unit_id] = (room.id,)
        descriptions.append({"id": unit_id, "kind": "unassigned_room", "room_ids": [room.id]})
    evidence["units"] = descriptions
    evidence["empty_zone_ids"] = empty
    return units


def _recursive_units(building, root_units, evidence):
    """Project canonical ancestry into solve units, without changing membership."""
    zones = {zone.id: zone for zone in building.zones}
    units = dict(root_units)
    ancestors = {}
    children = {}
    descriptions = list(evidence.get("units", ()))
    described = {description["id"] for description in descriptions}
    empty = []
    for zone in building.zones:
        members = building.room_ids_for_zone(zone.id)
        if not members:
            empty.append(zone.id)
            continue
        units[zone.id] = members
        lineage = []
        current = zone
        while current.parent_zone_id is not None:
            lineage.append(current.parent_zone_id)
            current = zones[current.parent_zone_id]
        ancestors[zone.id] = tuple(lineage)
        if zone.id not in described:
            descriptions.append({"id": zone.id, "kind": "child_zone", "room_ids": list(members),
                                 "parent_zone_id": zone.parent_zone_id})
    for zone in building.zones:
        if zone.id not in units:
            continue
        physical_children = [child.id for child in building.zones
                             if child.parent_zone_id == zone.id and child.id in units]
        if not physical_children:
            continue
        assigned = {room_id for child_id in physical_children for room_id in units[child_id]}
        direct = []
        for room_id in units[zone.id]:
            if room_id in assigned:
                continue
            unit_id = f"room:{room_id}"
            while unit_id in zones or unit_id in units:
                unit_id = f"room:{unit_id}"
            units[unit_id] = (room_id,)
            ancestors[unit_id] = (zone.id, *ancestors[zone.id])
            direct.append(unit_id)
            descriptions.append({"id": unit_id, "kind": "direct_room", "room_ids": [room_id],
                                 "parent_zone_id": zone.id})
        children[zone.id] = tuple((*physical_children, *direct))
    evidence["units"] = descriptions
    evidence["empty_zone_ids"] = empty
    return units, children, ancestors


def _size_options(building, units, supplied, maximum, evidence, *, allow_boundary_strips=False, fixed_rects=None):
    spec = building.layout
    unknown = set(supplied or {}) - set(units)
    if unknown:
        _fail(evidence, f"Unknown zone size option units: {', '.join(sorted(unknown))}",
              stage="input", code="unknown_size_unit", unit_ids=sorted(unknown), not_applicable=True)
    result = {}
    for unit_id, members in units.items():
        if supplied is not None and unit_id in supplied:
            try:
                raw = tuple(supplied[unit_id])
                if any(not isinstance(value, Real) or isinstance(value, bool)
                       for pair in raw for value in pair):
                    raise ValueError("dimensions must be numeric millimetres, excluding booleans")
                options = tuple((float(w), float(h)) for w, h in raw)
            except (TypeError, ValueError, OverflowError) as exc:
                _fail(evidence, f"Invalid zone size options for {unit_id}: {exc}", stage="input",
                      code="invalid_zone_size", unit_ids=(unit_id,), not_applicable=True)
            if not options or len(options) > maximum:
                _fail(evidence, f"Zone {unit_id} needs 1..{maximum} size options", stage="input",
                      code="invalid_zone_size", unit_ids=(unit_id,), not_applicable=True)
            for w, h in options:
                if any(not math.isfinite(v) or v <= 0 or round(v / spec.grid_mm) < 1 or
                       abs(v - round(v / spec.grid_mm) * spec.grid_mm) > 1e-6 for v in (w, h)):
                    _fail(evidence, f"Zone {unit_id} dimensions must be finite positive grid-aligned millimetres",
                          stage="input", code="invalid_zone_size", unit_ids=(unit_id,), not_applicable=True)
            result[unit_id] = tuple(dict.fromkeys((round(w / spec.grid_mm) * spec.grid_mm,
                                                  round(h / spec.grid_mm) * spec.grid_mm) for w, h in options))
            continue
        fixed = fixed_rects or {}
        area = sum(fixed[room_id].area_m2 if room_id in fixed else spec.room_by_id[room_id].target_area_m2
                   for room_id in members) * 1_000_000
        candidates = []
        if len(members) == 1 and members[0] in fixed:
            rect = fixed[members[0]]
            candidates.append((rect.width, rect.height))
        for fill, aspect in ((1, 1), (1, 2), (1, 0.5), (0.8, 1), (0.8, 2), (0.8, 0.5), (0.7, 3.5), (0.7, 0.29)):
            w = math.ceil(math.sqrt(area / fill * aspect) / spec.grid_mm) * spec.grid_mm
            h = math.ceil(area / fill / w / spec.grid_mm) * spec.grid_mm
            if w <= spec.boundary.width_mm and h <= spec.boundary.height_mm:
                candidates.append((w, h))
        if allow_boundary_strips and not candidates:
            usable_width = math.floor(spec.boundary.width_mm / spec.grid_mm) * spec.grid_mm
            usable_height = math.floor(spec.boundary.height_mm / spec.grid_mm) * spec.grid_mm
            if usable_width > 0 and usable_height > 0:
                for w, h in ((math.ceil(area / usable_height / spec.grid_mm) * spec.grid_mm, usable_height),
                             (usable_width, math.ceil(area / usable_width / spec.grid_mm) * spec.grid_mm)):
                    if w <= usable_width and h <= usable_height:
                        candidates.append((w, h))
        result[unit_id] = tuple(dict.fromkeys(candidates))[:maximum]
    return result


def _internal_groups(groups, members):
    return tuple((name, left, right) for name, left, right in groups
                 if left and right and set(left) | set(right) <= members)


def _group_issues(placements, required, forbidden, minimum):
    issues = []
    for name, left, right in required:
        if not any(a != b and placements[a].shared_boundary(placements[b]) + 1e-6 >= minimum for a in left for b in right):
            issues.append(ValidationIssue("zone_required_adjacency", f"Required zone relation {name} has no usable room contact"))
    for name, left, right in forbidden:
        if any(a != b and placements[a].shared_boundary(placements[b]) > 1e-6 for a in left for b in right):
            issues.append(ValidationIssue("zone_forbidden_adjacency", f"Forbidden zone relation {name} has a room contact"))
    return issues


def _mirrored_shapes(width, height, placements, containers=None):
    seen = set()
    shapes = []
    for mirror_x, mirror_y in ((False, False), (True, False), (False, True), (True, True)):
        def mirror(geometry):
            return {item_id: Rect(width - rect.right if mirror_x else rect.x,
                                  height - rect.top if mirror_y else rect.y, rect.width, rect.height)
                    for item_id, rect in geometry.items()}

        mirrored = mirror(placements)
        child_rects = mirror(containers or {})
        key = tuple((room_id, rect.x, rect.y, rect.width, rect.height) for room_id, rect in mirrored.items())
        key += tuple((unit_id, rect.x, rect.y, rect.width, rect.height) for unit_id, rect in child_rects.items())
        if key not in seen:
            seen.add(key)
            shapes.append(_ZoneShape(width, height, mirrored, child_rects))
    return shapes


def _local_shapes(building, units, options, groups, minimum, time_limit, seed, deterministic_units, evidence,
                  fixed_rects=None):
    spec = building.layout
    fit_options = {}
    for item in building.equipment:
        if item.room_id is not None:
            fit_options.setdefault(item.room_id, []).append((item.id, item.room_fit_dimensions(spec.wall_thickness_mm)))
    found = {}
    for unit_id, members in units.items():
        member_set = set(members)
        rooms = tuple(replace(
            room, needs_daylight=False,
            required_adjacency=tuple(other for other in room.required_adjacency if other in member_set),
            forbidden_adjacency=tuple(other for other in room.forbidden_adjacency if other in member_set),
            preferred_adjacency=tuple(other for other in room.preferred_adjacency if other in member_set),
        ) for room in spec.rooms if room.id in member_set)
        # Global positions are not local origins. Only dimensions are pinned in
        # the local CP models; global coordinates are constrained after packing.
        rooms = tuple(replace(room, min_width_mm=fixed_rects[room.id].width,
                              min_depth_mm=fixed_rects[room.id].height,
                              min_area_m2=fixed_rects[room.id].area_m2,
                              max_area_m2=fixed_rects[room.id].area_m2, rotatable=False)
                      if fixed_rects and room.id in fixed_rects else room for room in rooms)
        local_groups = {relation: _internal_groups(value, member_set) for relation, value in groups.items()}
        unit_shapes = []
        for index, (width, height) in enumerate(options[unit_id]):
            attempt = {"stage": "local", "unit_id": unit_id, "size_mm": [width, height], "accepted": False}
            evidence["stage_attempts"].append(attempt)
            if width > spec.boundary.width_mm or height > spec.boundary.height_mm:
                attempt["reason"] = "Container exceeds the global bounding dimensions"
                continue
            local = replace(spec, rooms=rooms, boundary=BoundarySpec(width, height, ()),
                            entry_room=spec.entry_room if spec.entry_room in member_set else rooms[0].id,
                            external_entry=None, doors=(), windows=())
            try:
                candidate = solve_layouts_hierarchical(
                    local, 1, time_limit, seed + index,
                    required_adjacency_groups=local_groups["required"],
                    forbidden_adjacency_groups=local_groups["forbidden"],
                    preferred_adjacency_groups=local_groups["preferred"],
                    equipment_fit_options={key: value for key, value in fit_options.items() if key in member_set},
                    minimum_adjacency_mm=minimum, deterministic_units=deterministic_units,
                )[0]
            except (InfeasibleLayout, ValueError) as exc:
                # The reused backend does not expose its CP status: do not
                # infer a proof or call a budget exhaustion INFEASIBLE.
                attempt["reason"] = str(exc)
                attempt["model_status"] = "NOT_AVAILABLE"
                continue
            issues = (*validate_layout(local, candidate).issues,
                      *_group_issues(candidate.placements, local_groups["required"], local_groups["forbidden"], minimum))
            if issues:
                attempt["reason"] = "; ".join(issue.message for issue in issues)
                continue
            attempt["accepted"] = True
            unit_shapes.extend(_mirrored_shapes(width, height, candidate.placements))
        if not unit_shapes:
            _fail(evidence, f"No independently valid local shape for zone unit {unit_id} in the bounded options",
                  stage="local", code="no_valid_local_shape", unit_ids=(unit_id,), room_ids=members)
        found[unit_id] = unit_shapes
    return found


def _recursive_shapes(building, roots, units, children, ancestors, options, groups,
                      minimum, time_limit, seed, deterministic_units, evidence, fixed_rects=None):
    """Solve leaves once, then pack cached child shapes inside canonical parents."""
    spec = building.layout
    zone_ids = {zone.id for zone in building.zones}
    found = {}

    def visit(unit_id):
        if unit_id in found:
            return found[unit_id]
        members = units[unit_id]
        if unit_id not in children:
            found.update(_local_shapes(building, {unit_id: members}, options, groups,
                                       minimum, time_limit, seed, deterministic_units, evidence, fixed_rects))
            return found[unit_id]
        child_units = {child_id: units[child_id] for child_id in children[unit_id]}
        child_shapes = {child_id: visit(child_id) for child_id in child_units}
        member_set = set(members)
        rooms = tuple(replace(
            room, needs_daylight=False,
            required_adjacency=tuple(other for other in room.required_adjacency if other in member_set),
            forbidden_adjacency=tuple(other for other in room.forbidden_adjacency if other in member_set),
            preferred_adjacency=tuple(other for other in room.preferred_adjacency if other in member_set),
        ) for room in spec.rooms if room.id in member_set)
        local_groups = {relation: _internal_groups(value, member_set) for relation, value in groups.items()}
        all_containers = dict.fromkeys(child_units)
        for shapes in child_shapes.values():
            all_containers.update(dict.fromkeys(shapes[0].containers))
        local_members = {container_id: units[container_id] for container_id in all_containers}
        local_ancestors = {container_id: tuple(parent for parent in ancestors.get(container_id, ())
                                              if parent in all_containers) for container_id in all_containers}
        parent_shapes = []
        for index, (width, height) in enumerate(options[unit_id]):
            attempt = {"stage": "subtree", "unit_id": unit_id, "size_mm": [width, height], "accepted": False}
            evidence["stage_attempts"].append(attempt)
            if width > spec.boundary.width_mm or height > spec.boundary.height_mm:
                attempt["reason"] = "Container exceeds the global bounding dimensions"
                continue
            local = replace(spec, rooms=rooms, boundary=BoundarySpec(width, height, ()),
                            entry_room=spec.entry_room if spec.entry_room in member_set else rooms[0].id,
                            external_entry=None, doors=(), windows=())
            # This is a stage projection of canonical rooms, not another public IR.
            projected = replace(building, layout=local)
            local_evidence = {"stage_attempts": [], "variants": []}
            attempt["search_attempts"] = local_evidence["stage_attempts"]
            try:
                candidate = _solve_shape_layouts(
                    projected, child_units, child_shapes, local_groups, minimum, 1,
                    time_limit, seed + index, 3, deterministic_units, local_evidence,
                    container_members=local_members, ancestors=local_ancestors,
                    recursive_zones=True, stage="subtree", stage_unit=unit_id,
                )[0]
            except ZoneHierarchyFailure as failure:
                attempt["failure"] = failure.to_dict()
                attempt["reason"] = str(failure)
                continue
            attempt["accepted"] = True
            attempt["model_status"] = local_evidence["stage_attempts"][-1]["model_status"]
            rectangles = {key: Rect(**raw) for key, raw in local_evidence["variants"][0]["zone_rects"].items()
                          if key in zone_ids}
            parent_shapes.extend(_mirrored_shapes(width, height, candidate.placements, rectangles))
        if not parent_shapes:
            _fail(evidence, f"No valid subtree shape for zone {unit_id} in the bounded options",
                  stage="subtree", code="no_valid_subtree_shape", unit_ids=(unit_id,), room_ids=members,
                  proof_scope="bounded_subtree_candidates")
        found[unit_id] = parent_shapes
        return parent_shapes

    return {unit_id: visit(unit_id) for unit_id in roots}


def _constrain_explicit_window(model, room, spec, window):
    """Constrain the selected side to a real facade usable by the wall builder."""
    span = window.offset_mm + window.width_mm / 2
    if not math.isfinite(span) or window.offset_mm < window.width_mm / 2 - 1e-6:
        raise InfeasibleLayout(f"Window {window.id} has an unusable offset")
    grid = spec.grid_mm
    vertical = window.side in {"left", "right"}
    start, size = (room.y, room.height) if vertical else (room.x, room.width)
    edge = {
        "left": room.x, "right": room.x + room.width,
        "bottom": room.y, "top": room.y + room.height,
    }[window.side]
    facade = {
        "left": (0, 0, spec.boundary.height_mm),
        "right": (spec.boundary.width_mm, 0, spec.boundary.height_mm),
        "bottom": (0, 0, spec.boundary.width_mm),
        "top": (spec.boundary.height_mm, 0, spec.boundary.width_mm),
    }
    facades = [facade[window.side]]
    for cutout in spec.boundary.cutouts:
        facades.append({
            "left": (cutout.right, cutout.y, cutout.top),
            "right": (cutout.x, cutout.y, cutout.top),
            "bottom": (cutout.top, cutout.x, cutout.right),
            "top": (cutout.y, cutout.x, cutout.right),
        }[window.side])
    contacts = []
    for index, (fixed, low, high) in enumerate(facades):
        if abs(fixed - round(fixed / grid) * grid) > 1e-6 or high - low < span - 1e-6:
            continue
        contact = model.NewBoolVar(f"window_{window.id}_facade_{index}")
        model.Add(edge == round(fixed / grid)).OnlyEnforceIf(contact)
        # The current wall builder measures offset from the exposed span's
        # start. Require enough contiguous exposure before it can be selected.
        model.Add(size >= math.ceil(span / grid - 1e-9)).OnlyEnforceIf(contact)
        model.Add(start + size >= math.ceil((low + span) / grid - 1e-9)).OnlyEnforceIf(contact)
        model.Add(start <= math.floor((high - span) / grid + 1e-9)).OnlyEnforceIf(contact)
        contacts.append(contact)
    model.AddBoolOr(contacts)


def _validate_container_layout(spec, result, zone_rects, units, ancestors, *, check_unrelated_rooms=False):
    from shapely.geometry import box

    geometry = (*result.placements.values(), *zone_rects.values())
    if any(not isinstance(rect, Rect) for rect in geometry):
        return ValidationReport((ValidationIssue("unsupported_geometry", "Root-zone containment supports rectangular rooms and containers"),))
    if any(not math.isfinite(value) for rect in geometry for value in (rect.x, rect.y, rect.width, rect.height)):
        return ValidationReport((ValidationIssue("invalid_rect", "Root-zone containment requires finite coordinates"),))
    issues = list(validate_layout(spec, result).issues)
    allowed = box(0, 0, spec.boundary.width_mm, spec.boundary.height_mm)
    for cutout in spec.boundary.cutouts:
        allowed = allowed.difference(box(cutout.x, cutout.y, cutout.right, cutout.top))
    for unknown in set(zone_rects) - set(units):
        issues.append(ValidationIssue("unknown_zone_container", f"Unknown container {unknown}"))
    for unit_id, members in units.items():
        container = zone_rects.get(unit_id)
        if container is None:
            issues.append(ValidationIssue("missing_zone_container", f"Missing container {unit_id}"))
            continue
        if container.width <= 0 or container.height <= 0 or not allowed.covers(box(container.x, container.y, container.right, container.top)):
            issues.append(ValidationIssue("zone_boundary", f"Container {unit_id} is outside the allowed building footprint"))
        for room_id in members:
            room = result.placements.get(room_id)
            if room is not None and (room.x < container.x - 1e-6 or room.y < container.y - 1e-6 or
                                     room.right > container.right + 1e-6 or room.top > container.top + 1e-6):
                issues.append(ValidationIssue("zone_containment", f"Room {room_id} is outside container {unit_id}"))
        for ancestor_id in ancestors.get(unit_id, ()):
            ancestor = zone_rects.get(ancestor_id)
            if ancestor is not None and (container.x < ancestor.x - 1e-6 or container.y < ancestor.y - 1e-6 or
                                         container.right > ancestor.right + 1e-6 or container.top > ancestor.top + 1e-6):
                issues.append(ValidationIssue("child_zone_containment", f"Container {unit_id} is outside ancestor {ancestor_id}"))
        if check_unrelated_rooms:
            for room_id, room in result.placements.items():
                if room_id not in members and room.intersection_area(container) > 1e-6:
                    issues.append(ValidationIssue("room_zone_overlap", f"Room {room_id} overlaps unrelated container {unit_id}"))
    containers = list(zone_rects.items())
    for index, (unit_id, rect) in enumerate(containers):
        for other_id, other in containers[index + 1:]:
            related = unit_id in ancestors.get(other_id, ()) or other_id in ancestors.get(unit_id, ())
            if not related and rect.intersection_area(other) > 1e-6:
                issues.append(ValidationIssue("zone_overlap", f"Containers {unit_id} and {other_id} overlap"))
    return ValidationReport(tuple(issues))


def validate_zone_layout(building: BuildingIR, result: LayoutResult, zone_rects: Mapping[str, Rect], *,
                         minimum_adjacency_mm=None, recursive_zones=False, fixed_rects=None,
                         axis_aligned_room_ids=None, structural_axes_x_mm=None, structural_axes_y_mm=None):
    """Independently check rooms, physical containers, ancestry and hard relations."""
    units = _root_units(building, {})
    ancestors = {}
    if recursive_zones:
        all_units, _, ancestors = _recursive_units(building, units, {})
        zone_ids = {zone.id for zone in building.zones}
        units = {unit_id: members for unit_id, members in all_units.items() if unit_id in units or unit_id in zone_ids}
    spec = building.layout
    report = _validate_container_layout(spec, result, zone_rects, units, ancestors,
                                        check_unrelated_rooms=recursive_zones)
    if any(issue.code in {"unsupported_geometry", "invalid_rect"} for issue in report.issues):
        return report
    issues = list(report.issues)
    if set(result.placements) == set(spec.room_by_id):
        minimum = spec.door_width_mm if minimum_adjacency_mm is None else minimum_adjacency_mm
        issues.extend(_group_issues(result.placements, building.zone_relation_groups("required_adjacency"),
                                    building.zone_relation_groups("forbidden_adjacency"), minimum))
        fixed, selected, axes = _normalise_room_constraints(
            spec, fixed_rects, axis_aligned_room_ids, structural_axes_x_mm, structural_axes_y_mm, {})
        for room_id, expected in fixed.items():
            actual = result.placements[room_id]
            if any(abs(getattr(actual, name) - getattr(expected, name)) > 1e-6
                   for name in ("x", "y", "width", "height")):
                issues.append(ValidationIssue("fixed_room_mismatch", f"Room {room_id} differs from its fixed rectangle"))
        for room_id in selected:
            room = result.placements[room_id]
            for name, values in axes.items():
                endpoints = (room.x, room.right) if name == "x" else (room.y, room.top)
                if any(not any(abs(endpoint - value) <= 1e-6 for value in values) for endpoint in endpoints):
                    issues.append(ValidationIssue("structural_axis_mismatch", f"Room {room_id} endpoints miss {name} axes"))
    return ValidationReport(tuple(issues))


def solve_zone_layouts(
    building: BuildingIR,
    variants: int = 1,
    time_limit_seconds: float = 30,
    seed: int = 42,
    *,
    zone_size_options_mm: Mapping[str, Iterable[tuple[float, float]]] | None = None,
    max_local_options: int = 4,
    max_validation_attempts: int | None = None,
    minimum_adjacency_mm: float | None = None,
    deterministic_units: float | None = None,
    evidence: dict | None = None,
    recursive_zones: bool = False,
    allow_partial: bool = False,
    fixed_rects: Mapping[str, Rect] | None = None,
    axis_aligned_room_ids: Iterable[str] | None = None,
    structural_axes_x_mm: Iterable[float] | None = None,
    structural_axes_y_mm: Iterable[float] | None = None,
) -> list[LayoutResult]:
    """Solve rooms inside disjoint root-zone containers with bounded retries.

    Explicit size options specify width/height in millimetres on the room grid;
    they are not silently rotated or rescaled. Budgets are per CP-SAT solve, rather
    than a total orchestration deadline. Inspect ``evidence`` for local option
    failures, global CP status and accepted container coordinates.
    ``recursive_zones=False`` preserves the root-only contract. When enabled,
    each nonempty descendant zone has a physically solved container; parent
    containers pack child alternatives and direct-room units with CP-SAT.
    ``allow_partial=True`` is for candidate pools: a nonempty independently
    validated pool may be returned when enumeration stops before its requested
    size. The default still requires the exact number of requested variants.
    Fixed rectangles and selected structural axes constrain final global room
    coordinates. Local candidates retain fixed dimensions, but their relative
    positions and bounded size options still restrict this backend's search.
    """
    evidence = evidence if evidence is not None else {}
    evidence.update(room_solver_used="zone_hierarchical", scope="root_zone_containers",
                    descendant_container_geometry=False, stage_attempts=[], variants=[])
    evidence.pop("failure", None)
    evidence.pop("candidate_pool", None)
    if not isinstance(allow_partial, bool):
        _fail(evidence, "allow_partial must be a boolean", stage="input",
              code="invalid_pool_mode", not_applicable=True)
    if not isinstance(recursive_zones, bool):
        _fail(evidence, "recursive_zones must be a boolean", stage="input",
              code="invalid_recursive_mode", not_applicable=True)
    if recursive_zones:
        evidence.update(room_solver_used="zone_recursive", scope="recursive_zone_containers",
                        descendant_container_geometry=True)
    counts = (variants, max_local_options)
    if max_validation_attempts is not None:
        counts += (max_validation_attempts,)
    if any(not isinstance(value, int) or isinstance(value, bool) for value in counts):
        _fail(evidence, "Search counts must be integers, excluding booleans", stage="input",
              code="invalid_budget", not_applicable=True)
    if variants < 1 or max_local_options < 1 or not math.isfinite(time_limit_seconds) or time_limit_seconds <= 0:
        _fail(evidence, "Variant count, local option count and time limit must be positive", stage="input",
              code="invalid_budget", not_applicable=True)
    if deterministic_units is not None and (not math.isfinite(deterministic_units) or deterministic_units <= 0):
        _fail(evidence, "deterministic_units must be finite and positive", stage="input", code="invalid_budget", not_applicable=True)
    attempts_limit = max(3, variants * 3) if max_validation_attempts is None else max_validation_attempts
    if attempts_limit < variants:
        _fail(evidence, "max_validation_attempts must cover requested variants", stage="input", code="invalid_budget", not_applicable=True)
    units = _root_units(building, evidence)
    spec = building.layout
    if any(not math.isfinite(value) or value <= 0 for value in (spec.boundary.width_mm, spec.boundary.height_mm)):
        _fail(evidence, "Boundary dimensions must be finite and positive", stage="input", code="invalid_boundary", not_applicable=True)
    boundary_width = _ceil_grid(spec.boundary.width_mm, spec.grid_mm)
    boundary_height = _ceil_grid(spec.boundary.height_mm, spec.grid_mm)
    if max(boundary_width, boundary_height) >= 1 << 60:
        _fail(evidence, "Boundary coordinates exceed safe integer bounds", stage="input", code="integer_bounds", not_applicable=True)
    fixed, selected, axes = _normalise_room_constraints(
        spec, fixed_rects, axis_aligned_room_ids, structural_axes_x_mm, structural_axes_y_mm, evidence)
    shape_units, children, ancestors = _recursive_units(building, units, evidence) if recursive_zones else (units, {}, {})
    options = _size_options(building, shape_units, zone_size_options_mm, max_local_options, evidence,
                            allow_boundary_strips=recursive_zones, fixed_rects=fixed)
    minimum = spec.door_width_mm if minimum_adjacency_mm is None else float(minimum_adjacency_mm)
    if not math.isfinite(minimum) or minimum <= 0:
        _fail(evidence, "minimum_adjacency_mm must be finite and positive", stage="input", code="invalid_adjacency_width", not_applicable=True)
    groups = {relation: building.zone_relation_groups(f"{relation}_adjacency") for relation in ("required", "forbidden", "preferred")}
    if recursive_zones:
        shapes = _recursive_shapes(building, units, shape_units, children, ancestors, options, groups,
                                   minimum, time_limit_seconds, seed, deterministic_units, evidence, fixed)
    else:
        shapes = _local_shapes(building, units, options, groups, minimum, time_limit_seconds, seed, deterministic_units, evidence, fixed)
    return _solve_shape_layouts(
        building, units, shapes, groups, minimum, variants, time_limit_seconds, seed,
        attempts_limit, deterministic_units, evidence,
        container_members={unit_id: members for unit_id, members in shape_units.items()
                           if unit_id in units or unit_id in {zone.id for zone in building.zones}},
        ancestors=ancestors, recursive_zones=recursive_zones, allow_partial=allow_partial,
        fixed_rects=fixed, axis_aligned_room_ids=selected, structural_axes=axes,
    )


def _solve_shape_layouts(building, units, shapes, groups, minimum, variants, time_limit_seconds,
                         seed, attempts_limit, deterministic_units, evidence, *,
                         container_members, ancestors, recursive_zones=False,
                         stage="global", stage_unit=None, allow_partial=False,
                         fixed_rects=None, axis_aligned_room_ids=(), structural_axes=None):
    from ortools.sat.python import cp_model

    spec = building.layout
    boundary_width = _ceil_grid(spec.boundary.width_mm, spec.grid_mm)
    boundary_height = _ceil_grid(spec.boundary.height_mm, spec.grid_mm)
    model = cp_model.CpModel()
    grid = spec.grid_mm
    room_vars = {}
    unit_vars = {}
    x_intervals, y_intervals = [], []
    area_deviations = []
    area_bound = 0
    for unit_id, unit_shapes in shapes.items():
        x = model.NewIntVar(0, boundary_width, f"zone_{unit_id}_x")
        y = model.NewIntVar(0, boundary_height, f"zone_{unit_id}_y")
        width = model.NewIntVar(1, boundary_width, f"zone_{unit_id}_width")
        height = model.NewIntVar(1, boundary_height, f"zone_{unit_id}_height")
        choices = [model.NewBoolVar(f"zone_{unit_id}_shape_{index}") for index in range(len(unit_shapes))]
        model.AddExactlyOne(choices)

        def pick(values):
            return sum(choice * value for choice, value in zip(choices, values))

        model.Add(width == pick([round(shape.width / grid) for shape in unit_shapes]))
        model.Add(height == pick([round(shape.height / grid) for shape in unit_shapes]))
        x_end = model.NewIntVar(0, boundary_width, f"zone_{unit_id}_right")
        y_end = model.NewIntVar(0, boundary_height, f"zone_{unit_id}_top")
        model.Add(x_end == x + width)
        model.Add(y_end == y + height)
        x_intervals.append(model.NewIntervalVar(x, width, x_end, f"zone_{unit_id}_x_interval"))
        y_intervals.append(model.NewIntervalVar(y, height, y_end, f"zone_{unit_id}_y_interval"))
        unit_vars[unit_id] = _RoomVars(x, y, width, height, None)
        deviations = []
        for shape in unit_shapes:
            deviations.append(sum(abs(round(rect.width / grid) * round(rect.height / grid) -
                                      _area_to_grid2(spec.room_by_id[room_id].target_area_m2, grid, ceil=False))
                                  for room_id, rect in shape.placements.items()))
        area_deviations.append(pick(deviations))
        area_bound += sum(deviations)
        for room_id in units[unit_id]:
            geometry = [shape.placements[room_id] for shape in unit_shapes]
            rx = model.NewIntVar(0, boundary_width, f"{room_id}_x")
            ry = model.NewIntVar(0, boundary_height, f"{room_id}_y")
            rw = model.NewIntVar(1, boundary_width, f"{room_id}_width")
            rh = model.NewIntVar(1, boundary_height, f"{room_id}_height")
            model.Add(rx == x + pick([round(rect.x / grid) for rect in geometry]))
            model.Add(ry == y + pick([round(rect.y / grid) for rect in geometry]))
            model.Add(rw == pick([round(rect.width / grid) for rect in geometry]))
            model.Add(rh == pick([round(rect.height / grid) for rect in geometry]))
            # These are actual model constraints, rather than inferred boxes
            # attached to an independently solved monolithic room plan.
            model.Add(rx >= x)
            model.Add(ry >= y)
            model.Add(rx + rw <= x + width)
            model.Add(ry + rh <= y + height)
            room_vars[room_id] = _RoomVars(rx, ry, rw, rh, None)
        for child_id in unit_shapes[0].containers:
            geometry = [shape.containers[child_id] for shape in unit_shapes]
            cx = model.NewIntVar(0, boundary_width, f"zone_{child_id}_x")
            cy = model.NewIntVar(0, boundary_height, f"zone_{child_id}_y")
            cw = model.NewIntVar(1, boundary_width, f"zone_{child_id}_width")
            ch = model.NewIntVar(1, boundary_height, f"zone_{child_id}_height")
            model.Add(cx == x + pick([round(rect.x / grid) for rect in geometry]))
            model.Add(cy == y + pick([round(rect.y / grid) for rect in geometry]))
            model.Add(cw == pick([round(rect.width / grid) for rect in geometry]))
            model.Add(ch == pick([round(rect.height / grid) for rect in geometry]))
            model.Add(cx >= x)
            model.Add(cy >= y)
            model.Add(cx + cw <= x + width)
            model.Add(cy + ch <= y + height)
            unit_vars[child_id] = _RoomVars(cx, cy, cw, ch, None)
        for index, cutout in enumerate(spec.boundary.cutouts):
            _avoid_cutout(model, x, y, width, height, cutout, grid, f"zone_{unit_id}_cutout_{index}")
    model.AddNoOverlap2D(x_intervals, y_intervals)
    for room_id, rect in (fixed_rects or {}).items():
        room = room_vars[room_id]
        for name in ("x", "y", "width", "height"):
            model.Add(getattr(room, name) == _fixed_grid_value(getattr(rect, name), grid, room_id, name))
    for room_id in axis_aligned_room_ids:
        room = room_vars[room_id]
        for name, values in (structural_axes or {}).items():
            coordinate, dimension, boundary = (room.x, room.width, boundary_width) if name == "x" else (room.y, room.height, boundary_height)
            model.AddAllowedAssignments([coordinate, dimension], _axis_pairs(values, boundary, grid, room_id, name))
    if recursive_zones:
        for unit_id, values in unit_vars.items():
            for room_id in container_members[unit_id]:
                room = room_vars[room_id]
                model.Add(room.x >= values.x)
                model.Add(room.y >= values.y)
                model.Add(room.x + room.width <= values.x + values.width)
                model.Add(room.y + room.height <= values.y + values.height)
            for ancestor_id in ancestors.get(unit_id, ()):
                if ancestor_id in unit_vars:
                    parent = unit_vars[ancestor_id]
                    model.Add(values.x >= parent.x)
                    model.Add(values.y >= parent.y)
                    model.Add(values.x + values.width <= parent.x + parent.width)
                    model.Add(values.y + values.height <= parent.y + parent.height)
    minimum_shared = max(1, _ceil_grid(minimum, grid))
    for a, b in spec.relation_pairs("required_adjacency"):
        _add_required_adjacency(model, room_vars[a], room_vars[b], minimum_shared, f"required_{a}_{b}")
    for a, b in spec.relation_pairs("forbidden_adjacency"):
        _add_forbidden_adjacency(model, room_vars[a], room_vars[b], f"forbidden_{a}_{b}")
    for name, left, right in groups["required"]:
        indicators = [_add_adjacency_indicator(model, room_vars[a], room_vars[b], minimum_shared, f"zone_required_{name}_{a}_{b}")
                      for a in left for b in right if a != b]
        if not indicators:
            _fail(evidence, f"Required zone relation {name} has no distinct room pair", stage=stage, code="empty_required_group")
        model.AddBoolOr(indicators)
    for name, left, right in groups["forbidden"]:
        for a in left:
            for b in right:
                if a != b:
                    _add_forbidden_adjacency(model, room_vars[a], room_vars[b], f"zone_forbidden_{name}_{a}_{b}")
    try:
        explicit_window_rooms = {window.room_id for window in spec.windows}
        for room in spec.rooms:
            if room.needs_daylight and room.id not in explicit_window_rooms:
                _constrain_automatic_window_facade(model, room_vars[room.id], spec, room.id)
        for window in spec.windows:
            _constrain_explicit_window(model, room_vars[window.room_id], spec, window)
        if spec.external_entry is not None:
            _constrain_external_entry(model, room_vars[spec.external_entry.room_id], spec, boundary_width, boundary_height)
        if not (groups["preferred"] or spec.relation_pairs("preferred_adjacency")):
            if area_bound * 10_000 + len(spec.rooms) * (boundary_width + boundary_height) > (1 << 62) - 1:
                raise ValueError("Zone objective exceeds safe integer bounds")
        objective = _layout_objective(
            model, spec, room_vars, area_deviations, minimum_shared,
            [value for room in spec.rooms for value in (room_vars[room.id].x, room_vars[room.id].y)],
            area_domain_bound=area_bound, preferred_groups=groups["preferred"],
        )
    except (InfeasibleLayout, ValueError) as exc:
        _fail(evidence, f"Cannot construct {stage} zone constraints: {exc}", stage=stage,
              code="invalid_global_constraint", model_status="NOT_BUILT")
    model.Minimize(objective)
    results = []
    last_issues = []

    def partial_pool(reason):
        evidence["candidate_pool"] = {
            "requested": variants, "returned": len(results), "partial": True,
            "stop_reason": reason, "proof_scope": "selected_zone_shapes",
            "proven_building_infeasible": False,
        }
        return results

    variables = [value for room in spec.rooms for value in
                 (room_vars[room.id].x, room_vars[room.id].y, room_vars[room.id].width, room_vars[room.id].height)]
    for attempt in range(attempts_limit):
        solver = cp_model.CpSolver()
        configure_solver(solver, seed=seed + attempt, time_limit_seconds=time_limit_seconds, deterministic_units=deterministic_units)
        status = solver.Solve(model)
        record = {"stage": stage, "attempt": attempt + 1, "model_status": solver.StatusName(status), "accepted": False}
        if stage_unit is not None:
            record["unit_id"] = stage_unit
        evidence["stage_attempts"].append(record)
        if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            if allow_partial and results:
                return partial_pool(solver.StatusName(status))
            if last_issues:
                break
            _fail(evidence, f"{stage.title()} zone placement returned {solver.StatusName(status)} for the selected shapes",
                  stage=stage, code=f"no_{stage}_placement", unit_ids=units,
                  model_status=solver.StatusName(status),
                  proof_scope="selected_zone_shapes" if stage == "global" else "selected_subtree_shapes")

        def rectangle(values):
            return Rect(*(solver.Value(value) * grid for value in (values.x, values.y, values.width, values.height)))

        candidate = LayoutResult(len(results) + 1, {room.id: rectangle(room_vars[room.id]) for room in spec.rooms}, solver.Value(objective))
        zone_rects = {unit_id: rectangle(values) for unit_id, values in unit_vars.items()}
        if stage == "global":
            report = validate_zone_layout(building, candidate, zone_rects, minimum_adjacency_mm=minimum,
                                          recursive_zones=recursive_zones, fixed_rects=fixed_rects,
                                          axis_aligned_room_ids=axis_aligned_room_ids,
                                          structural_axes_x_mm=(structural_axes or {}).get("x"),
                                          structural_axes_y_mm=(structural_axes or {}).get("y"))
        else:
            report = _validate_container_layout(spec, candidate, zone_rects, container_members, ancestors,
                                                check_unrelated_rooms=True)
            report = ValidationReport((*report.issues,
                                       *_group_issues(candidate.placements, groups["required"], groups["forbidden"], minimum)))
        model.AddForbiddenAssignments(variables, [[solver.Value(value) for value in variables]])
        if not report.ok:
            last_issues = [f"{issue.code}: {issue.message}" for issue in report.issues]
            record["issues"] = last_issues
            continue
        record["accepted"] = True
        results.append(candidate)
        evidence["variants"].append({"variant": candidate.variant, "zone_rects": {
            unit_id: {"x": rect.x, "y": rect.y, "width": rect.width, "height": rect.height}
            for unit_id, rect in zone_rects.items()
        }})
        if len(results) == variants:
            return results
    if allow_partial and results:
        return partial_pool("VALIDATION_ATTEMPT_LIMIT")
    _fail(evidence, f"Only {len(results)} of {variants} independently valid zone layouts within the bounded search",
          stage="validation" if stage == "global" else stage, code="insufficient_valid_candidates",
          proof_scope="bounded_candidates" if stage == "global" else "bounded_subtree_candidates", issues=last_issues)

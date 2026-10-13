"""Two source-derived stacks for narrowly eligible automatic root leaves.

This explicit candidate policy extends the automatic aspect pool only for a
three-room interface star. It retains all source predicates and search bounds;
local validity never establishes whole-program or engineering acceptance.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import replace
import math

from .models import BoundarySpec, Rect
from .shape_cache import _valid_shape, cluster_shape_request
from .solver import _area_to_grid2, _ceil_grid
from .zone_leaf_turns import _counts, _key
from .zone_contact_pruning import (
    MAX_CANDIDATE_PAIRS, MAX_ORIENTATION_CONTACT_CHECKS,
    MAX_PARTICIPATING_RECTANGLE_RECORDS, MAX_SOURCE_ROOMS, MAX_SUPPORT_ROWS, MAX_UNIT_PAIRS,
)
from .zone_diagnostics import MAX_CANDIDATE_SHAPES, MAX_RECTANGLE_RECORDS


MAX_REPACKINGS_PER_UNIT = 2


def _positive(value):
    return type(value) in (int, float) and math.isfinite(value) and value > 0


def _stack(building, members, interface, minimum):
    """Derive a vertical stack in original source axes, using integer grid units."""
    grid = building.layout.grid_mm
    rooms = building.layout.room_by_id
    if type(grid) is not int or grid <= 0:
        raise ValueError("The source grid must be a positive integer")
    boundary = building.layout.boundary
    if not all(_positive(v) for v in (boundary.width_mm, boundary.height_mm)):
        raise ValueError("The original boundary must be finite and positive")
    for room_id in members:
        room = rooms[room_id]
        if not all(_positive(v) for v in (room.min_width_mm, room.min_depth_mm,
                room.target_area_m2, room.min_area_m2, room.max_area_m2)):
            raise ValueError("Room dimensions and areas must be finite and positive")
        if room.min_area_m2 > room.max_area_m2 or any(type(v) is not bool
                for v in (room.needs_daylight, room.rotatable)):
            raise ValueError("Invalid original room range or boolean predicate")
    ends = [k for k in members if k != interface]
    daylight = [k for k in members if rooms[k].needs_daylight]
    if rooms[interface].needs_daylight or len(daylight) > 1:
        raise ValueError("A stack may expose at most one daylight end")
    if daylight:
        ends.sort(key=lambda k: k != daylight[0])
    first, last = ends
    width = _ceil_grid(max(minimum, *(rooms[k].min_width_mm for k in members)), grid)
    limits, targets = {}, {}
    for room_id in members:
        room = rooms[room_id]
        low_area = _area_to_grid2(room.min_area_m2, grid, True)
        high_area = _area_to_grid2(room.max_area_m2, grid, False)
        low = max(_ceil_grid(room.min_depth_mm, grid), (low_area + width - 1) // width)
        high = high_area // width
        if high < low:
            raise ValueError("Source area cannot fit the derived minimum width")
        limits[room_id] = (low, high)
        target = round(room.target_area_m2 * 1e6 / (grid * grid * width))
        targets[room_id] = max(low, min(high, target))
    # A short exposed end and a long interface are intentional source-bounded
    # alternatives. The other end keeps its ordinary target-area depth.
    depths = {first: limits[first][0], interface: limits[interface][1], last: targets[last]}
    height = sum(depths.values())
    if width * grid > boundary.width_mm or height * grid > boundary.height_mm:
        raise ValueError("The derived envelope exceeds the original boundary")
    geometry, y = {}, 0
    for room_id in (first, interface, last):
        geometry[room_id] = Rect(0, y * grid, width * grid, depths[room_id] * grid)
        y += depths[room_id]
    derivation = {"width_mm": width * grid, "height_mm": height * grid,
        "room_depth_intervals_mm": {k: [lo * grid, hi * grid] for k, (lo, hi) in limits.items()},
        "room_depths_mm": {k: v * grid for k, v in depths.items()},
        "first_end_room_id": first, "interface_room_id": interface, "last_end_room_id": last}
    return width * grid, height * grid, geometry, derivation


def plan_selective_leaf_repacking(building, units, shapes, *, minimum_adjacency_mm,
                                 explicit_unit_ids=(), fixed_rects=None, axis_aligned_room_ids=()):
    """Append two freshly checked source-derived stacks, or reject all on overflow.

    A canonical three-room root leaf must have one room serving two distinct
    external units and both required internal end-room adjacencies. Explicit
    pools, parents, equipment, explicit openings, fixed rooms and selected axes
    remain excluded. All old shape objects, order and source values are retained.
    """
    try:
        if not _positive(minimum_adjacency_mm):
            raise ValueError("minimum_adjacency_mm must be finite and positive")
    except OverflowError as exc:
        raise ValueError("minimum_adjacency_mm must be finite and positive") from exc
    limits = {"source_rooms": MAX_SOURCE_ROOMS, "unit_pairs": MAX_UNIT_PAIRS,
        "candidate_pairs": MAX_CANDIDATE_PAIRS, "orientation_contact_checks": MAX_ORIENTATION_CONTACT_CHECKS,
        "participating_rectangle_records": MAX_PARTICIPATING_RECTANGLE_RECORDS,
        "support_row_upper_bound": MAX_SUPPORT_ROWS, "candidate_shapes": MAX_CANDIDATE_SHAPES,
        "rectangle_records": MAX_RECTANGLE_RECORDS}
    report = {"schema_version": 1, "status": "APPLIED",
        "scope": "selected_final_automatic_root_leaf_candidate_diversity",
        "policy": "source_bounded_three_room_interface_stack_v1",
        "size_policy": "explicit_detached_extension_of_automatic_aspect_pool",
        "maximum_repackings_per_unit": MAX_REPACKINGS_PER_UNIT,
        "source_geometry_rotated": False, "positions_are_constraints": False,
        "original_program_infeasibility_proven": False, "limits": limits,
        "counts": {"added_shapes": 0}, "units": []}
    owner, order = {}, {k: i for i, k in enumerate(units)}
    for unit_id, members in units.items():
        if unit_id not in shapes:
            report.update(status="SKIPPED_UNSAFE", skip_reason="missing_unit_choices")
            return shapes, report
        for room_id in members:
            if room_id in owner or room_id not in building.layout.room_by_id:
                report.update(status="SKIPPED_UNSAFE", skip_reason="ambiguous_unit_membership")
                return shapes, report
            owner[room_id] = unit_id
    contacts, neighbors = defaultdict(list), defaultdict(set)
    pairs = building.layout.relation_pairs("required_adjacency")
    internal_pairs = {frozenset(pair) for pair in pairs}
    for left, right in pairs:
        a, b = owner.get(left), owner.get(right)
        if a is None or b is None or a == b:
            continue
        neighbors[left].add(b)
        neighbors[right].add(a)
        if order[a] > order[b]:
            a, b, left, right = b, a, right, left
        contacts[a, b].append((left, right))
    before = _counts(building, units, shapes, contacts)
    report["before"] = before
    for name, limit in limits.items():
        if before[name] > limit:
            report.update(status="SKIPPED_LIMIT", skip_reason=name + "_limit")
            return shapes, report
    explicit, fixed, axes = set(explicit_unit_ids), set(fixed_rects or {}), set(axis_aligned_room_ids)
    zones = {z.id: z for z in building.zones}
    parents = {z.parent_zone_id for z in building.zones
        if z.parent_zone_id and building.room_ids_for_zone(z.id)}
    opening_rooms = {w.room_id for w in building.layout.windows}
    opening_rooms.update(k for d in building.layout.doors for k in (d.room_a, d.room_b))
    if building.layout.external_entry is not None:
        opening_rooms.add(building.layout.external_entry.room_id)
    equipment_rooms = set()
    for item in building.equipment:
        if item.room_id is not None:
            equipment_rooms.add(item.room_id)
        elif item.zone_id is not None:
            equipment_rooms.update(building.room_ids_for_zone(item.zone_id))
        else:
            equipment_rooms.update(owner)
    from .zone_hierarchy import _group_issues, _internal_groups, _ZoneShape

    groups = {name: building.zone_relation_groups(name + "_adjacency")
        for name in ("required", "forbidden")}
    result = {k: list(v) for k, v in shapes.items()}
    for unit_id, members in units.items():
        interfaces = [k for k in members if len(neighbors[k]) >= 2]
        if not interfaces:
            continue
        event = {"unit_id": unit_id, "interface_room_ids": interfaces, "added_shapes": 0}
        report["units"].append(event)
        member_set = set(members)
        zone = zones.get(unit_id)
        if unit_id in explicit:
            event["reason"] = "explicit_size_options_preserved"
        elif zone is None or zone.parent_zone_id is not None or unit_id in parents or any(s.containers for s in shapes[unit_id]):
            event["reason"] = "not_canonical_root_leaf"
        elif member_set != set(zone.room_ids) or len(members) != 3 or len(interfaces) != 1:
            event["reason"] = "not_three_room_interface_star"
        elif member_set & fixed:
            event["reason"] = "fixed_room_unit"
        elif member_set & axes:
            event["reason"] = "axis_bound_unit"
        elif member_set & opening_rooms:
            event["reason"] = "explicit_opening_unit"
        elif member_set & equipment_rooms:
            event["reason"] = "equipment_unit"
        elif not all(frozenset((interfaces[0], k)) in internal_pairs for k in members if k != interfaces[0]):
            event["reason"] = "missing_required_internal_star"
        if "reason" in event:
            continue
        internal = {name: _internal_groups(values, member_set) for name, values in groups.items()}
        seen = {_key(s) for s in shapes[unit_id]}
        event["reason"] = "no_new_valid_stack"
        try:
            width, height, geometry, derivation = _stack(building, members, interfaces[0], minimum_adjacency_mm)
            local = replace(building.layout, boundary=BoundarySpec(width, height))
            request = cluster_shape_request(local, members, side_contacts={}, fit_options={},
                adjacency_mm=minimum_adjacency_mm, time_limit_seconds=1, seed=0,
                deterministic_units=None, aspects=(), fills=(), max_shapes=1)
            event["derivation"] = derivation
            for reflected in (False, True):
                proposed = {k: Rect(r.x, height - r.top if reflected else r.y, r.width, r.height)
                    for k, r in geometry.items()}
                shape = _ZoneShape(width, height, proposed)
                if _key(shape) in seen or not _valid_shape(request, proposed) or _group_issues(proposed,
                        internal["required"], internal["forbidden"], minimum_adjacency_mm):
                    continue
                seen.add(_key(shape))
                result[unit_id].append(shape)
                event["added_shapes"] += 1
                event["reason"] = "validated_source_derived_stacks"
        except (ValueError, TypeError, OverflowError):
            event["reason"] = "source_stack_not_supported"
    after = _counts(building, units, result, contacts)
    report["proposed"] = after
    for name, limit in limits.items():
        if after[name] > limit:
            report.update(status="SKIPPED_LIMIT", skip_reason=name + "_limit")
            for event in report["units"]:
                event["proposed_shapes"] = event["added_shapes"]
                event["added_shapes"] = 0
            return shapes, report
    report["counts"] = {**after, "added_shapes": sum(e["added_shapes"] for e in report["units"])}
    return result, report

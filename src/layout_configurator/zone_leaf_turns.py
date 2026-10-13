"""Bounded extra leaf choices, preserving the canonical source and old shapes.

Only independently valid transposed/reflected leaf geometry is admitted. These
choices do not establish global packing, equipment, routes or engineering QA.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import replace
import math

from .models import BoundarySpec, Rect
from .shape_cache import _valid_shape, cluster_shape_request
from .zone_contact_pruning import (
    MAX_CANDIDATE_PAIRS, MAX_ORIENTATION_CONTACT_CHECKS,
    MAX_PARTICIPATING_RECTANGLE_RECORDS, MAX_SOURCE_ROOMS, MAX_SUPPORT_ROWS, MAX_UNIT_PAIRS,
)
from .zone_diagnostics import MAX_CANDIDATE_SHAPES, MAX_RECTANGLE_RECORDS


MAX_TURNS_PER_UNIT = 4


def _key(shape):
    return (shape.width, shape.height, tuple((k, r.x, r.y, r.width, r.height)
        for k, r in sorted(shape.placements.items())))


def _counts(building, units, shapes, contacts):
    participating = {unit_id for pair in contacts for unit_id in pair}
    pairs = sum(len(shapes[a]) * len(shapes[b]) for a, b in contacts)
    return {
        "source_rooms": len(building.layout.rooms), "unit_pairs": len(contacts),
        "candidate_pairs": pairs,
        "orientation_contact_checks": sum(4 * len(shapes[a]) * len(shapes[b]) * len(values)
            for (a, b), values in contacts.items()),
        "participating_rectangle_records": sum(len(s.placements) + len(s.containers)
            for k in participating for s in shapes[k]),
        "support_row_upper_bound": 4 * pairs,
        "candidate_shapes": sum(len(values) for values in shapes.values()),
        "rectangle_records": sum(len(s.placements) + len(s.containers)
            for values in shapes.values() for s in values),
    }


def plan_selective_leaf_turns(building, units, shapes, *, minimum_adjacency_mm,
                             explicit_unit_ids=(), fixed_rects=None, axis_aligned_room_ids=()):
    """Append at most four checked choices per eligible automatic root leaf.

    A single canonical room must serve two distinct selected neighboring units.
    Parents, explicit pools and source-bound equipment/opening/axis/fixed rooms
    are excluded. All existing choices and their order remain intact. A complete
    resource preflight rejects all additions rather than disabling support QA.
    """
    if type(minimum_adjacency_mm) not in (int, float) or not math.isfinite(minimum_adjacency_mm) or minimum_adjacency_mm <= 0:
        raise ValueError("minimum_adjacency_mm must be finite and positive")
    limits = {
        "source_rooms": MAX_SOURCE_ROOMS, "unit_pairs": MAX_UNIT_PAIRS,
        "candidate_pairs": MAX_CANDIDATE_PAIRS,
        "orientation_contact_checks": MAX_ORIENTATION_CONTACT_CHECKS,
        "participating_rectangle_records": MAX_PARTICIPATING_RECTANGLE_RECORDS,
        "support_row_upper_bound": MAX_SUPPORT_ROWS, "candidate_shapes": MAX_CANDIDATE_SHAPES,
        "rectangle_records": MAX_RECTANGLE_RECORDS,
    }
    report = {"schema_version": 1, "status": "APPLIED",
        "scope": "selected_final_automatic_root_leaf_candidate_diversity",
        "policy": "checked_swapped_leaf_envelopes_v1", "maximum_turns_per_unit": MAX_TURNS_PER_UNIT,
        "source_geometry_rotated": False, "positions_are_constraints": False,
        "original_program_infeasibility_proven": False, "limits": limits,
        "counts": {"added_shapes": 0}, "units": []}
    owner, order = {}, {k: i for i, k in enumerate(units)}
    for unit_id, members in units.items():
        for room_id in members:
            if room_id in owner or room_id not in building.layout.room_by_id:
                report.update(status="SKIPPED_UNSAFE", skip_reason="ambiguous_unit_membership")
                return shapes, report
            owner[room_id] = unit_id
    contacts, neighbors = defaultdict(list), defaultdict(set)
    for left, right in building.layout.relation_pairs("required_adjacency"):
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
    parents = {z.parent_zone_id for z in building.zones
        if z.parent_zone_id and building.room_ids_for_zone(z.id)}
    opening_rooms = {w.room_id for w in building.layout.windows}
    opening_rooms.update(room_id for d in building.layout.doors for room_id in (d.room_a, d.room_b))
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
    # Lazy use of the existing group checker avoids a second interpretation of
    # existential source zone relations and an import cycle with ZoneHints.
    from .zone_hierarchy import _group_issues, _internal_groups

    groups = {name: building.zone_relation_groups(name + "_adjacency")
        for name in ("required", "forbidden")}
    result = {k: list(v) for k, v in shapes.items()}
    for unit_id, members in units.items():
        interfaces = [room_id for room_id in members if len(neighbors[room_id]) >= 2]
        if not interfaces:
            continue
        event = {"unit_id": unit_id, "interface_room_ids": interfaces, "added_shapes": 0}
        report["units"].append(event)
        member_set = set(members)
        if unit_id in explicit:
            event["reason"] = "explicit_size_options_preserved"
        elif unit_id in parents or any(s.containers for s in shapes[unit_id]):
            event["reason"] = "canonical_parent_unit"
        elif member_set & fixed:
            event["reason"] = "fixed_room_unit"
        elif member_set & axes:
            event["reason"] = "axis_bound_unit"
        elif member_set & opening_rooms:
            event["reason"] = "explicit_opening_unit"
        elif member_set & equipment_rooms:
            event["reason"] = "equipment_unit"
        if "reason" in event:
            continue
        internal = {name: _internal_groups(values, member_set) for name, values in groups.items()}
        seen = {_key(s) for s in shapes[unit_id]}
        event["reason"] = "no_new_valid_turn"
        for shape in shapes[unit_id]:
            if event["added_shapes"] >= MAX_TURNS_PER_UNIT:
                break
            try:
                local = replace(building.layout, boundary=BoundarySpec(shape.width, shape.height))
                request = cluster_shape_request(local, members, side_contacts={}, fit_options={},
                    adjacency_mm=minimum_adjacency_mm, time_limit_seconds=1, seed=0,
                    deterministic_units=None, aspects=(), fills=(), max_shapes=1)
                if not _valid_shape(request, shape.placements) or _group_issues(shape.placements,
                        internal["required"], internal["forbidden"], minimum_adjacency_mm):
                    continue
                width, height = shape.height, shape.width
                if width > building.layout.boundary.width_mm or height > building.layout.boundary.height_mm:
                    continue
                swapped = replace(local, boundary=BoundarySpec(width, height))
                turned_request = replace(request, spec=swapped)
                valid_turn_found = False
                for mirror_x, mirror_y in ((False, False), (False, True), (True, False), (True, True)):
                    geometry = {k: Rect(width - r.top if mirror_x else r.y,
                        height - r.right if mirror_y else r.x, r.height, r.width)
                        for k, r in shape.placements.items()}
                    turned = replace(shape, width=width, height=height, placements=geometry)
                    key = _key(turned)
                    if not _valid_shape(turned_request, geometry) or _group_issues(geometry,
                            internal["required"], internal["forbidden"], minimum_adjacency_mm):
                        continue
                    valid_turn_found = True
                    if key in seen:
                        continue
                    seen.add(key)
                    result[unit_id].append(turned)
                    event["added_shapes"] += 1
                    event["reason"] = "validated_swapped_envelope_turns"
                    if event["added_shapes"] == MAX_TURNS_PER_UNIT:
                        break
                # One stable first valid representative bounds the diversity
                # and prevents later runs from turning additions back again.
                if valid_turn_found:
                    break
            except (ValueError, TypeError, OverflowError):
                continue
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

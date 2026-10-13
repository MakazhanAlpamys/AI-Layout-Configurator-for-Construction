"""Exact necessary contact compatibility for selected final root candidates.

This computes alternatives for two fixed integer shapes. Empty compatibility
permits a redundant CP no-good; it never proves the source program infeasible.
All original constraints and candidate geometry remain in the final model.
"""

from __future__ import annotations


MAX_SOURCE_ROOMS = 80
MAX_UNIT_PAIRS = 64
MAX_CANDIDATE_PAIRS = 65536
MAX_ORIENTATION_CONTACT_CHECKS = 1048576
MAX_PARTICIPATING_RECTANGLE_RECORDS = 65536
MAX_SUPPORT_ROWS = 16384


def _integer_shape(shape, grid):
    """Use the same round-grid values as the global weighted shape picks."""
    try:
        width, height = round(shape.width / grid), round(shape.height / grid)
        rooms = {room_id: tuple(round(getattr(rect, name) / grid) for name in ("x", "y", "width", "height"))
                 for room_id, rect in shape.placements.items()}
    except (TypeError, ValueError, OverflowError, ZeroDivisionError):
        return None
    if width < 1 or height < 1 or any(
            x < 0 or y < 0 or w < 1 or h < 1 or x + w > width or y + h > height
            for x, y, w, h in rooms.values()):
        return None
    return width, height, rooms


def _translation_segments(a, b, contacts, minimum, boundary):
    if a is None or b is None:
        return None
    wa, ha, rooms_a = a
    wb, hb, rooms_b = b
    width, height = boundary
    x_low, x_high = -(width - wa), width - wb
    y_low, y_high = -(height - ha), height - hb
    if wa > width or wb > width or ha > height or hb > height:
        return ()
    if not contacts or any(left not in rooms_a or right not in rooms_b for left, right in contacts):
        return None
    segments = []
    # Containment and positive usable overlap imply that nonoverlapping root
    # envelopes meet on a common external side. Every required contact must
    # therefore use that same side; four interval intersections are exact.
    for side in ("right", "left", "above", "below"):
        vertical = side in ("right", "left")
        fixed = (wa if side == "right" else -wb) if vertical else (ha if side == "above" else -hb)
        low, high = (y_low, y_high) if vertical else (x_low, x_high)
        if not ((x_low <= fixed <= x_high) if vertical else (y_low <= fixed <= y_high)):
            continue
        valid = True
        for left, right in contacts:
            ax, ay, aw, ah = rooms_a[left]
            bx, by, bw, bh = rooms_b[right]
            if vertical:
                port = (ax + aw == wa and bx == 0) if side == "right" else (ax == 0 and bx + bw == wb)
                if not port or ah < minimum or bh < minimum:
                    valid = False
                    break
                low, high = max(low, ay + minimum - by - bh), min(high, ay + ah - minimum - by)
            else:
                port = (ay + ah == ha and by == 0) if side == "above" else (ay == 0 and by + bh == hb)
                if not port or aw < minimum or bw < minimum:
                    valid = False
                    break
                low, high = max(low, ax + minimum - bx - bw), min(high, ax + aw - minimum - bx)
            if low > high:
                valid = False
                break
        if valid:
            segments.append((fixed, fixed, low, high) if vertical else (low, high, fixed, fixed))
    return tuple(sorted(set(segments)))


def translation_segments(shape_a, shape_b, contacts, *, grid_mm, minimum_grid, boundary_grid):
    """Return exact closed integer delta segments, or None to skip unsafe input.

Coordinates describe B's origin minus A's. A nonempty result is necessary for
global feasibility, not sufficient for any other hard constraint or validator.
"""
    if type(minimum_grid) is not int or minimum_grid < 1:
        raise ValueError("minimum_grid must be a positive integer")
    return _translation_segments(_integer_shape(shape_a, grid_mm), _integer_shape(shape_b, grid_mm),
                                 tuple(contacts), minimum_grid, boundary_grid)


def plan_pruning(building, units, shapes, *, minimum_grid, boundary_grid):
    """Preflight the whole pass before returning any redundant candidate no-goods."""
    limits = {"source_rooms": MAX_SOURCE_ROOMS, "unit_pairs": MAX_UNIT_PAIRS,
              "candidate_pairs": MAX_CANDIDATE_PAIRS,
              "orientation_contact_checks": MAX_ORIENTATION_CONTACT_CHECKS}
    order = {unit_id: index for index, unit_id in enumerate(units)}
    owner = {room_id: unit_id for unit_id, members in units.items() for room_id in members}
    contacts_by_units = {}
    for left, right in building.layout.relation_pairs("required_adjacency"):
        a, b = owner.get(left), owner.get(right)
        if a is None or b is None or a == b:
            continue
        if order[a] > order[b]:
            a, b, left, right = b, a, right, left
        contacts_by_units.setdefault((a, b), []).append((left, right))
    counts = {
        "source_rooms": len(building.layout.rooms), "unit_pairs": len(contacts_by_units),
        "candidate_pairs": sum(len(shapes[a]) * len(shapes[b]) for a, b in contacts_by_units),
        "orientation_contact_checks": sum(4 * len(shapes[a]) * len(shapes[b]) * len(contacts)
                                           for (a, b), contacts in contacts_by_units.items()),
        "examined_pairs": 0, "skipped_invalid_containment_pairs": 0,
        "rejected_pairs": 0, "added_clauses": 0,
    }
    report = {"schema_version": 1, "scope": "selected_final_global_candidate_pairs",
              "required_relations_used": "explicit_cross_root_room_pairs_only",
              "status": "APPLIED", "limits": limits, "counts": counts,
              "original_program_infeasibility_proven": False}
    for name, limit in limits.items():
        if counts[name] > limit:
            report.update(status="SKIPPED_LIMIT", skip_reason=name + "_limit")
            return [], report
    integer_shapes = {}
    rejected = []
    for (a, b), contacts in contacts_by_units.items():
        for index_a, shape_a in enumerate(shapes[a]):
            for index_b, shape_b in enumerate(shapes[b]):
                for unit_id, index, shape in ((a, index_a, shape_a), (b, index_b, shape_b)):
                    if (unit_id, index) not in integer_shapes:
                        integer_shapes[unit_id, index] = _integer_shape(shape, building.layout.grid_mm)
                compatible = _translation_segments(integer_shapes[a, index_a], integer_shapes[b, index_b],
                                                    contacts, minimum_grid, boundary_grid)
                counts["examined_pairs"] += 1
                if compatible is None:
                    counts["skipped_invalid_containment_pairs"] += 1
                elif not compatible:
                    rejected.append((a, index_a, b, index_b))
    counts["rejected_pairs"] = len(rejected)
    return rejected, report


def plan_contact_support(building, units, shapes, *, minimum_grid, boundary_grid):
    """Plan finite shape/translation supports before posting any model constraints.

    Each row is (shape_A, shape_B, dx_low, dx_high, dy_low, dy_high).
    The last four values describe one closed integer segment for B minus A.
    At least one row extends every original feasible assignment: positive room
    contact inside nonoverlapping root envelopes forces one common external
    side. All required contacts use its intersected translation interval.

    Unknown effective containment skips the entire unit pair, including its
    known candidates. Bounds skip the whole pass, never a subset of rows.
    """
    if type(minimum_grid) is not int or minimum_grid < 1:
        raise ValueError("minimum_grid must be a positive integer")
    order = {unit_id: index for index, unit_id in enumerate(units)}
    owner = {room_id: unit_id for unit_id, members in units.items() for room_id in members}
    contacts_by_units = {}
    for left, right in building.layout.relation_pairs("required_adjacency"):
        a, b = owner.get(left), owner.get(right)
        if a is None or b is None or a == b:
            continue
        if order[a] > order[b]:
            a, b, left, right = b, a, right, left
        contacts_by_units.setdefault((a, b), []).append((left, right))
    participating = {unit_id for pair in contacts_by_units for unit_id in pair}
    candidate_pairs = sum(len(shapes[a]) * len(shapes[b]) for a, b in contacts_by_units)
    limits = {
        "source_rooms": MAX_SOURCE_ROOMS, "unit_pairs": MAX_UNIT_PAIRS,
        "candidate_pairs": MAX_CANDIDATE_PAIRS,
        "orientation_contact_checks": MAX_ORIENTATION_CONTACT_CHECKS,
        "participating_rectangle_records": MAX_PARTICIPATING_RECTANGLE_RECORDS,
        "support_rows": MAX_SUPPORT_ROWS,
    }
    counts = {
        "source_rooms": len(building.layout.rooms), "unit_pairs": len(contacts_by_units),
        "candidate_pairs": candidate_pairs,
        "orientation_contact_checks": sum(4 * len(shapes[a]) * len(shapes[b]) * len(contacts)
                                           for (a, b), contacts in contacts_by_units.items()),
        "participating_rectangle_records": sum(len(shape.placements) + len(shape.containers)
                                               for unit_id in participating for shape in shapes[unit_id]),
        "support_row_upper_bound": 4 * candidate_pairs,
        "examined_unit_pairs": 0, "skipped_invalid_containment_unit_pairs": 0,
        "eligible_unit_pairs": 0, "participating_units": 0, "support_rows": 0, "table_cells": 0,
        "added_index_variables": 0, "added_endpoint_variables": 0,
        "added_linear_constraints": 0, "added_table_constraints": 0,
    }
    report = {
        "schema_version": 1, "scope": "selected_final_global_shape_translation_supports",
        "required_relations_used": "explicit_cross_root_room_pairs_only",
        "status": "APPLIED", "limits": limits, "counts": counts,
        "original_program_infeasibility_proven": False,
    }
    for name, limit in limits.items():
        count_name = "support_row_upper_bound" if name == "support_rows" else name
        if counts[count_name] > limit:
            report.update(status="SKIPPED_LIMIT", skip_reason=count_name + "_limit")
            return [], report
    integer_shapes = {unit_id: [_integer_shape(shape, building.layout.grid_mm) for shape in shapes[unit_id]]
                      for unit_id in participating}
    plans = []
    for (a, b), contacts in contacts_by_units.items():
        counts["examined_unit_pairs"] += 1
        if any(shape is None for shape in (*integer_shapes[a], *integer_shapes[b])):
            counts["skipped_invalid_containment_unit_pairs"] += 1
            continue
        rows, unsafe = [], False
        for index_a, shape_a in enumerate(integer_shapes[a]):
            for index_b, shape_b in enumerate(integer_shapes[b]):
                segments = _translation_segments(shape_a, shape_b, contacts, minimum_grid, boundary_grid)
                if segments is None:
                    unsafe = True
                    break
                rows.extend((index_a, index_b, *segment) for segment in segments)
            if unsafe:
                break
        if unsafe:
            counts["skipped_invalid_containment_unit_pairs"] += 1
            continue
        plans.append((a, b, tuple(rows)))
    counts["eligible_unit_pairs"] = len(plans)
    counts["participating_units"] = len({unit_id for a, b, _ in plans for unit_id in (a, b)})
    counts["support_rows"] = sum(len(rows) for _, _, rows in plans)
    counts["table_cells"] = 6 * counts["support_rows"]
    return plans, report

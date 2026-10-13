"""Scoped soft guidance for physical zone models, never inferred containers."""

from __future__ import annotations

import math

from .hierarchy import _orientations
from .hierarchy_hints import HierarchyHints
from .models import Rect
from .shape_cache import _valid_shape, cluster_shape_request


class ZoneHints:
    """Project source room coordinates into independently checked stage hints.

    Source extents only propose one extra automatic container size. A source
    leaf shape is admitted after current room/equipment/contact checks; parent
    and global container geometry is still solved and independently validated.
    No old coordinate is added to the final model as a constraint.
    """

    def __init__(self, building, rects):
        self.building = building
        self.rects = HierarchyHints(building.layout, rects).rects
        if any(abs(round(value / building.layout.grid_mm)) >= 1 << 60
               for rect in self.rects.values() for value in (rect.x, rect.y, rect.width, rect.height)):
            raise ValueError("Zone hints exceed safe integer coordinate bounds")
        self.evidence = {
            "scope": "single_zone_hierarchy_invocation",
            "supplied_room_ids": sorted(self.rects), "positions_are_constraints": False,
            "maximum_extra_automatic_sizes_per_unit": 1,
            "maximum_source_leaf_orientations": 8,
            "maximum_solved_leaf_orientations": 8, "maximum_corner_translations_per_orientation": 4,
            "size_candidates": [], "leaf_shapes": [], "stages": [],
        }

    def project(self, members, *, translate):
        rects = {room_id: self.rects[room_id] for room_id in members if room_id in self.rects}
        if translate and rects:
            x, y = min(rect.x for rect in rects.values()), min(rect.y for rect in rects.values())
            return {room_id: Rect(rect.x - x, rect.y - y, rect.width, rect.height)
                    for room_id, rect in rects.items()}
        return rects

    def extra_sizes(self, units, options, supplied):
        """Preserve explicit dimensions and every ordinary automatic candidate."""
        spec = self.building.layout
        fit_options = {}
        for item in self.building.equipment:
            if item.room_id is not None:
                fit_options.setdefault(item.room_id, []).append(item.room_fit_dimensions(spec.wall_thickness_mm))
        for unit_id, members in units.items():
            event = {"unit_id": unit_id, "accepted": False, "geometry_is_solved": False}
            self.evidence["size_candidates"].append(event)
            if supplied is not None and unit_id in supplied:
                event["outcome"] = "explicit_size_options_preserved"
                continue
            rects = self.project(members, translate=True)
            if len(rects) != len(members):
                event["outcome"] = "incomplete_unit_hint"
                continue
            growth = 1.0
            for room_id, rect in rects.items():
                room = spec.room_by_id[room_id]
                ratios = [max(room.min_width_mm / rect.width, room.min_depth_mm / rect.height)]
                if room.rotatable:
                    ratios.append(max(room.min_depth_mm / rect.width, room.min_width_mm / rect.height))
                growth = max(growth, min(ratios), math.sqrt(room.min_area_m2 / rect.area_m2))
                for dimensions in fit_options.get(room_id, ()):
                    growth = max(growth, min(max(width / rect.width, height / rect.height)
                                             for width, height in dimensions))
            width = math.ceil(max(rect.right for rect in rects.values()) * growth / spec.grid_mm) * spec.grid_mm
            height = math.ceil(max(rect.top for rect in rects.values()) * growth / spec.grid_mm) * spec.grid_mm
            event.update(size_mm=[width, height], growth_factor=growth)
            if width > spec.boundary.width_mm or height > spec.boundary.height_mm:
                event["outcome"] = "outside_global_dimensions"
            elif (width, height) in options[unit_id]:
                event["outcome"] = "existing_size_option"
            else:
                options[unit_id] = (*options[unit_id], (width, height))
                event.update(accepted=True, outcome="bounded_size_candidate")

    def leaf_orientations(self, local, members, fit_options, minimum, group_validator, unit_id, *, solved=None):
        shape = self.project(members, translate=True)
        if solved is not None:
            x, y = min(rect.x for rect in solved.values()), min(rect.y for rect in solved.values())
            shape = {room_id: Rect(rect.x - x, rect.y - y, rect.width, rect.height)
                     for room_id, rect in solved.items()}
        event = {"unit_id": unit_id, "room_ids": list(members), "orientations": 0,
                 "origin": "validated_local_cp_result" if solved is not None else "supplied_room_hints"}
        self.evidence["leaf_shapes"].append(event)
        if len(shape) != len(members):
            event["outcome"] = "incomplete_leaf_hint"
            return []
        request = cluster_shape_request(
            local, members, side_contacts={}, fit_options=fit_options, adjacency_mm=minimum,
            time_limit_seconds=1, seed=0, deterministic_units=None, aspects=(), fills=(), max_shapes=1,
        )
        if not _valid_shape(request, shape) or group_validator(shape):
            event["outcome"] = "invalid_current_leaf_geometry"
            return []
        found = []
        for orientation in _orientations(shape, local.room_by_id, fit_options):
            geometry = {room_id: Rect(x, y, width, height)
                        for room_id, x, y, width, height in orientation.rooms}
            if (_valid_shape(request, geometry) and not group_validator(geometry)
                    and orientation.width <= local.boundary.width_mm
                    and orientation.height <= local.boundary.height_mm):
                found.append(geometry)
        event.update(outcome="independently_validated_leaf_orientations", orientations=len(found))
        return found

    def apply(self, model, spec, units, shapes, unit_vars, choices_by_unit, room_vars, *, stage, unit_id):
        members = tuple(room_vars)
        rects = self.project(members, translate=stage != "global")
        hints = HierarchyHints(spec, rects)
        hints.evidence["scope"] = "single_physical_zone_stage"
        hints.evidence["independent_validation_scope"] = "room_geometry_physical_containers_ancestry_and_hard_relations"
        event = {"stage": stage, "unit_id": unit_id, "hint_evidence": hints.evidence,
                 "matched_unit_ids": [], "positions_are_constraints": False}
        self.evidence["stages"].append(event)
        for room_id, variables in room_vars.items():
            hints.apply_free_room(model, room_id, variables)
        for current_id, unit_shapes in shapes.items():
            source = {room_id: rects[room_id] for room_id in units[current_id] if room_id in rects}
            if len(source) != len(units[current_id]):
                continue
            for index, shape in enumerate(unit_shapes):
                first = next(iter(source))
                anchor_x, anchor_y = source[first].x - shape.placements[first].x, source[first].y - shape.placements[first].y
                if not all(rect.width == shape.placements[room_id].width and rect.height == shape.placements[room_id].height
                           and rect.x - shape.placements[room_id].x == anchor_x
                           and rect.y - shape.placements[room_id].y == anchor_y
                           for room_id, rect in source.items()):
                    continue
                event["matched_unit_ids"].append(current_id)
                values = unit_vars[current_id]
                for field, value in (("x", anchor_x), ("y", anchor_y), ("width", shape.width), ("height", shape.height)):
                    hints._add_hint(model, getattr(values, field), round(value / spec.grid_mm), current_id, field)
                for choice_index, choice in enumerate(choices_by_unit[current_id]):
                    hints._add_hint(model, choice, int(index == choice_index), current_id, f"choice_{choice_index}")
                for child_id, rect in shape.containers.items():
                    values = unit_vars[child_id]
                    for field, value in (("x", anchor_x + rect.x), ("y", anchor_y + rect.y),
                                         ("width", rect.width), ("height", rect.height)):
                        hints._add_hint(model, getattr(values, field), round(value / spec.grid_mm), child_id, field)
                break
        return hints

    def complete_leaf_turns(self, units, shapes, *, minimum, supplied=None, fixed_rects=None,
                            axis_aligned_room_ids=()):
        """Plan explicit checked diversity without modifying source hints."""
        from .zone_leaf_turns import plan_selective_leaf_turns

        return plan_selective_leaf_turns(self.building, units, shapes,
            minimum_adjacency_mm=minimum, explicit_unit_ids=tuple(supplied or {}),
            fixed_rects=fixed_rects, axis_aligned_room_ids=axis_aligned_room_ids)

"""Validate and map soft room hints to a bounded cluster pattern model."""

from __future__ import annotations

import math
import time
from collections.abc import Mapping

from ortools.sat.python import cp_model

from .models import LayoutIR, LayoutResult, Rect
from .search import configure_solver
from .shape_cache import ClusterShapeRequest, _valid_shape
from .validation import validate_layout


class HierarchyHints:
    """Hints add search guidance and at most one source shape per cluster.

    A supplemental shape passes independent current room, adjacency, grid and
    equipment-fit checks. It supplements the ordinary CP-generated patterns;
    no original option is removed and no hinted position becomes a constraint.
    Final positions and choices are still returned by the global CP-SAT model.
    """

    def __init__(self, spec: LayoutIR, rects: Mapping[str, Rect]) -> None:
        if not isinstance(rects, Mapping):
            raise ValueError("hint_rects must be a room-to-rectangle mapping")
        unknown = set(rects) - set(spec.room_by_id)
        if unknown:
            raise ValueError(f"Cannot hint unknown rooms: {', '.join(sorted(unknown))}")
        for room_id, rect in rects.items():
            if not isinstance(rect, Rect):
                raise ValueError(f"Hint for {room_id} must be a Rect")
            for value in (rect.x, rect.y, rect.width, rect.height):
                if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                    raise ValueError(f"Hint for {room_id} must contain finite numeric coordinates")
                if abs(value / spec.grid_mm - round(value / spec.grid_mm)) > 1e-7:
                    raise ValueError(f"Hint for {room_id} must follow the room grid")
            if rect.width <= 0 or rect.height <= 0:
                raise ValueError(f"Hint for {room_id} must have positive dimensions")
        self.spec = spec
        self.rects = dict(rects)
        self._clusters: dict[str, tuple] = {}
        self.evidence = {
            "scope": "single_hierarchical_invocation",
            "supplied_room_ids": sorted(rects),
            "positions_are_constraints": False,
            "maximum_source_shapes_per_cluster": 1,
            "maximum_added_orientations_per_cluster": 8,
            "repair_hint": False,
            "repair_worker_policy": "single_worker",
            "applied_values": 0,
            "free_room_ids": [],
            "skipped_values": [],
            "clusters": [],
            "global_search": [],
        }

    def augment_cluster(self, cluster_id: str, request: ClusterShapeRequest, options: list, orientations) -> list:
        supplied = set(request.room_ids) & self.rects.keys()
        event = {"cluster_id": cluster_id, "room_ids": list(request.room_ids), "added_patterns": 0}
        self.evidence["clusters"].append(event)
        if not supplied:
            event["outcome"] = "no_cluster_hint"
            return options
        if len(supplied) != len(request.room_ids):
            event["outcome"] = "partial_cluster_hint"
            return options
        x0 = min(self.rects[room_id].x for room_id in request.room_ids)
        y0 = min(self.rects[room_id].y for room_id in request.room_ids)
        shape = {room_id: Rect(rect.x - x0, rect.y - y0, rect.width, rect.height)
                 for room_id in request.room_ids for rect in (self.rects[room_id],)}
        if not _valid_shape(request, shape):
            event["outcome"] = "invalid_local_geometry"
            return options
        if _matching_option(options, shape) is None:
            added = [option for option in orientations(shape) if option not in options]
            options = [*options, *dict.fromkeys(added)]
            event["added_patterns"] = len(dict.fromkeys(added))
            event["outcome"] = "validated_supplement"
        else:
            event["outcome"] = "matched_existing_pattern"
        if _matching_option(options, shape) is not None:
            self._clusters[cluster_id] = (x0, y0, shape, event)
        return options

    def apply_free_room(self, model, room_id: str, variables) -> None:
        rect = self.rects.get(room_id)
        if rect is None:
            return
        self.evidence["free_room_ids"].append(room_id)
        for name, value in (("x", rect.x), ("y", rect.y), ("width", rect.width), ("height", rect.height)):
            self._add_hint(model, getattr(variables, name), round(value / self.spec.grid_mm), room_id, name)

    def apply_cluster(self, model, cluster_id: str, x, y, choices, options, room_vars) -> None:
        source = self._clusters.get(cluster_id)
        if source is None:
            return
        x0, y0, shape, event = source
        index = _matching_option(options, shape)
        if index is None:  # Defensive: no shape identity is inferred from its index.
            event["outcome"] = "pattern_not_available"
            return
        event["choice_index"] = index
        grid = self.spec.grid_mm
        self._add_hint(model, x, round(x0 / grid), cluster_id, "anchor_x")
        self._add_hint(model, y, round(y0 / grid), cluster_id, "anchor_y")
        for choice_index, choice in enumerate(choices):
            self._add_hint(model, choice, int(choice_index == index), cluster_id, f"choice_{choice_index}")
        for room_id in shape:
            self.apply_free_room(model, room_id, room_vars[room_id])
        # These rooms are rigid members, not free singleton rooms.
        self.evidence["free_room_ids"] = [room_id for room_id in self.evidence["free_room_ids"] if room_id not in shape]

    def _add_hint(self, model, variable, value: int, owner: str, field: str) -> None:
        domain = tuple(model.Proto().variables[variable.Index()].domain)
        if not any(lower <= value <= upper for lower, upper in zip(domain[::2], domain[1::2])):
            self.evidence["skipped_values"].append({"owner": owner, "field": field, "reason": "outside_current_domain"})
            return
        model.AddHint(variable, int(value))
        self.evidence["applied_values"] += 1
        self.evidence["repair_hint"] = True

    def complete(self, model, room_vars, *, seed: int, time_limit_seconds: float,
                 deterministic_units: float | None) -> None:
        """Complete auxiliary hints in a separate, explicitly restricted model.

        Only the clone fixes hinted values. On successful independent room
        validation its complete solution becomes soft guidance in the original
        model. The original constraints, objective and search remain intact.
        """
        if not self.evidence["repair_hint"]:
            return
        started = time.perf_counter()
        stage = {
            "proof_scope": "cloned_model_with_supplied_hints_fixed",
            "original_program_infeasibility_proven": False,
            "final_model_positions_are_constraints": False,
            "time_limit_seconds": min(time_limit_seconds, 1.0),
            "deterministic_units": min(deterministic_units, 0.05) if deterministic_units is not None else None,
            "supplied_values": len(model.Proto().solution_hint.vars),
            "outcome": "bounded_completion_failed",
        }
        self.evidence["completion"] = stage
        candidate = model.Clone()
        solver = cp_model.CpSolver()
        configure_solver(solver, seed=seed, time_limit_seconds=stage["time_limit_seconds"],
                         deterministic_units=stage["deterministic_units"], workers=1)
        solver.parameters.fix_variables_to_their_hinted_value = True
        status = solver.Solve(candidate)
        stage["model_status"] = solver.StatusName(status)
        stage["solver_wall_seconds"] = solver.WallTime()
        if status in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            values = [solver.Value(candidate.GetIntVarFromProtoIndex(index))
                      for index in range(len(candidate.Proto().variables))]
            grid = self.spec.grid_mm
            placements = {room_id: Rect(*(values[getattr(variables, field).Index()] * grid
                                         for field in ("x", "y", "width", "height")))
                          for room_id, variables in room_vars.items()}
            report = validate_layout(self.spec, LayoutResult(1, placements))
            stage["independent_layout_ok"] = report.ok
            if report.ok:
                model.ClearHints()
                for index, value in enumerate(values):
                    model.AddHint(model.GetIntVarFromProtoIndex(index), value)
                stage["outcome"] = "complete_soft_hint"
                stage["transferred_values"] = len(values)
            else:
                stage["outcome"] = "independent_geometry_rejected"
                stage["independent_layout_issues"] = [issue.message for issue in report.issues]
        stage["total_seconds"] = time.perf_counter() - started

    def record_global_search(self, variant: int, status: str, *, assumptions_dropped: bool = False) -> None:
        self.evidence["global_search"].append({
            "variant": variant, "model_status": status,
            "assumptions_dropped": assumptions_dropped,
            "proof_scope": "original_and_validated_hint_cluster_shapes",
            "original_program_infeasibility_proven": False,
        })


def _matching_option(options, shape) -> int | None:
    expected = tuple((room_id, rect.x, rect.y, rect.width, rect.height) for room_id, rect in sorted(shape.items()))
    return next((index for index, option in enumerate(options) if option.rooms == expected), None)

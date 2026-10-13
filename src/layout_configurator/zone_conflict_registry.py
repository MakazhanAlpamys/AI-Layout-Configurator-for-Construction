"""Actual zone-builder terminals and bounded invocation-wide diagnostic capture.

The source envelope records the original program and the stage projection.
Candidate generation may already have used source constraints. Deletion proofs
keep that generated family and all unnamed base clauses fixed; they cannot
establish equivalence to, or infeasibility of, the original BuildingIR.
"""

from __future__ import annotations

import hashlib
import json
import math
import time

from .finite_conflicts import (
    ConflictBudget, NamedConstraint, SNAPSHOT_PHASE, _json_bytes, _kind,
    _model_bytes, _registry, explain_finite_conflict,
)
from .zone_diagnostics import capture_candidate_family

MAX_CAPTURES = 32
MAX_CAPTURE_BYTES = 32 * 1024 * 1024
MAX_SOURCE_OBJECTS = 1024


def validate_conflict_budget(budget):
    if type(budget) is not ConflictBudget:
        raise ValueError("conflict_budget must be a ConflictBudget")
    def positive(value, maximum):
        return type(value) in (int, float) and 0 < value <= maximum and math.isfinite(value)
    if not positive(budget.wall_seconds, 10) or type(budget.max_solves) is not int or not 1 <= budget.max_solves <= 512:
        raise ValueError("conflict_budget requires at most 10 seconds and 1..512 solves")
    if budget.deterministic_units is not None and not positive(budget.deterministic_units, 10):
        raise ValueError("conflict_budget deterministic_units must be in (0,10]")


def _digest(raw):
    return hashlib.sha256(raw).hexdigest()


def _pointer_key(value):
    return value.replace("~", "~0").replace("/", "~1")


def _identity(kind, values):
    return kind + ":" + json.dumps(list(values), ensure_ascii=False, separators=(",", ":"))


def _building_frame(building):
    layout = building.layout
    objects = sum(len(getattr(building, key)) for key in ("zones", "equipment", "flows"))
    objects += sum(len(getattr(layout, key)) for key in ("rooms", "doors", "windows"))
    objects += len(layout.boundary.cutouts)
    if objects > MAX_SOURCE_OBJECTS:
        raise ValueError("source_object_limit")
    return json.loads(_json_bytes(building.to_dict()))


class ZoneTerminalRegistry:
    """Bind returned terminal indices without parsing human-readable names."""

    def __init__(self, session, building, groups):
        self.session, self.building = session, building
        self.requirements, self.incomplete_reason = [], None
        self.room_paths, self.zone_descriptors = {}, {}
        original = session.original_source
        if original is None:
            self.incomplete_reason = session.source_error
            return
        for relation in ("required", "forbidden"):
            field = relation + "_adjacency"
            paths = {}
            for index, room in enumerate(original["layout"]["rooms"]):
                for position, other in enumerate(room.get(field, [])):
                    pair = tuple(sorted((room["id"], other)))
                    paths.setdefault(pair, []).append(f"/original_building/layout/rooms/{index}/{field}/{position}")
            self.room_paths[relation] = paths
            zone_paths, ordered = {}, []
            known = {zone["id"] for zone in original["zones"]}
            for index, zone in enumerate(original["zones"]):
                for position, other in enumerate(zone.get(field, [])):
                    if other not in known:
                        continue
                    pair = tuple(sorted((zone["id"], other)))
                    if pair not in zone_paths:
                        ordered.append(pair)
                    zone_paths.setdefault(pair, []).append(f"/original_building/zones/{index}/{field}/{position}")
            descriptors = []
            for pair in ordered:
                left = session.original_building.room_ids_for_zone(pair[0])
                right = session.original_building.room_ids_for_zone(pair[1])
                descriptors.append(((f"{pair[0]}-{pair[1]}", left, right), pair, tuple(zone_paths[pair])))
            selected = []
            for group in groups[relation]:
                # Identical display names can denote different semantic pairs.
                # Match the full original group tuple and consume in source order.
                match = next((index for index, item in enumerate(descriptors) if item[0] == group), None)
                if match is None:
                    self.incomplete_reason = "unbound_zone_terminal"
                    selected.append(None)
                else:
                    selected.append(descriptors.pop(match)[1:])
            self.zone_descriptors[relation] = selected

    def _add(self, identity, terminals, paths, source_ids):
        if not paths:
            self.incomplete_reason = "unbound_source_terminal"
            return
        self.requirements.append(NamedConstraint(identity, tuple(item.Index() for item in terminals),
                                                tuple(paths), tuple(source_ids)))

    def room(self, relation, pair, terminal):
        self._add(_identity("room_" + relation, pair), (terminal,),
                  self.room_paths.get(relation, {}).get(pair, ()), pair)

    def zone(self, relation, position, terminals):
        descriptor = self.zone_descriptors.get(relation, [])[position] if self.zone_descriptors.get(relation) else None
        if descriptor is None or not terminals:
            self.incomplete_reason = "unbound_zone_terminal"
            return
        pair, paths = descriptor
        self._add(_identity("zone_" + relation, pair), terminals, paths, pair)

    def fixed(self, room_id, terminals):
        self._add(_identity("room_fixed", (room_id,)), terminals,
                  ("/effective_constraints/fixed_rects/" + _pointer_key(room_id),), (room_id,))

    def axis(self, room_id, direction, terminal):
        self._add(_identity("room_axis", (room_id, direction)), (terminal,),
                  ("/effective_constraints/axis_aligned_room_ids",
                   "/effective_constraints/structural_axes_mm/" + direction), (room_id,))


def _unknown(reason):
    return {"schema_version": 1, "status": "UNKNOWN", "complete": False, "reason": reason,
            "scope": "fixed_base_selected_candidate_family", "searches": [], "source": {}, "requirements": [],
            "family_conditioning": "Source-dependent generated candidates stay fixed during requirement deletion",
            "core": {"requirement_ids": [], "independently_verified": False, "subset_minimal": False},
            "proven_building_infeasible": False, "original_program_infeasibility_proven": False,
            "full_facility_validation": {"status": "UNKNOWN"}, "engineering_acceptance": "PENDING"}


class ZoneConflictSession:
    """One diagnostic deadline and solve allowance across subtree retries.

The deadline starts at the first snapshot, includes capture and intervening
production search, and is never reset. Ordinary solve budgets stay unchanged.
"""

    def __init__(self, building, *, evidence, budget=ConflictBudget()):
        validate_conflict_budget(budget)
        started = time.monotonic()
        self.original_building, self.budget = building, budget
        self.deadline, self.capture_bytes = None, 0
        self.remaining_units = budget.deterministic_units
        self.original_source, self.source_error = None, None
        try:
            self.original_source = _building_frame(building)
        except (ValueError, TypeError, OverflowError, UnicodeError, KeyError, AttributeError) as exc:
            self.source_error = "unsafe_original_source:" + type(exc).__name__
        self.preparation_seconds = time.monotonic() - started
        self.evidence = {
            "schema_version": 1, "scope": "actual_zone_builder_fixed_base_and_conditioned_family",
            "budget": {"wall_seconds": budget.wall_seconds, "max_solves": budget.max_solves,
                       "deterministic_units": budget.deterministic_units, "workers": 1,
                       "deadline_origin": "first_pre_pruning_snapshot"},
            "captures": [], "explanations": [], "solves_used": 0,
            "original_program_infeasibility_proven": False, "proven_building_infeasible": False,
            "full_facility_validation": {"status": "UNKNOWN"}, "engineering_acceptance": "PENDING",
        }
        evidence["conflict_explanations"] = self.evidence

    def registry(self, building, groups):
        started = time.monotonic()
        registry = ZoneTerminalRegistry(self, building, groups)
        if self.deadline is None:
            self.preparation_seconds += time.monotonic() - started
        return registry

    def capture(self, model, registry, building, units, shapes, *, container_members, ancestors,
                recursive_zones, minimum, fixed_rects, axis_room_ids, structural_axes, stage, stage_unit):
        if self.deadline is None:
            self.deadline = time.monotonic() + self.budget.wall_seconds - self.preparation_seconds
        if len(self.evidence["captures"]) >= MAX_CAPTURES:
            self.evidence["capture_limit_reached"] = True
            return {"error": "capture_resource_limit", "capture_index": None}
        record = {"stage": stage, "unit_id": stage_unit, "capture": {"capture_status": "INCOMPLETE"},
                  "binding": {}, "model_sha256": None, "snapshot_state": None}
        index = len(self.evidence["captures"])
        self.evidence["captures"].append(record)
        snapshot = {"error": None, "capture_index": index}
        try:
            if time.monotonic() >= self.deadline:
                raise ValueError("verification_budget_exhausted")
            if registry.incomplete_reason:
                raise ValueError(registry.incomplete_reason)
            stage_source = _building_frame(building)
            effective = {
                "fixed_rects": {key: {name: getattr(rect, name) for name in ("x", "y", "width", "height")}
                                for key, rect in (fixed_rects or {}).items()},
                "axis_aligned_room_ids": list(axis_room_ids),
                "structural_axes_mm": {key: list(values) for key, values in (structural_axes or {}).items()},
                "minimum_adjacency_mm": minimum, "recursive_zones": recursive_zones,
                "stage": stage, "unit_id": stage_unit,
            }
            source = {"original_building": self.original_source, "stage_building": stage_source,
                      "effective_constraints": effective}
            raw_source = _json_bytes(source, deadline=self.deadline)
            capture = capture_candidate_family(building, units, shapes, container_members=container_members,
                ancestors=ancestors, recursive_zones=recursive_zones,
                minimum_grid=math.ceil(minimum / building.layout.grid_mm), fixed_rects=fixed_rects,
                axis_room_ids=axis_room_ids, structural_axes=structural_axes)
            capture.update(snapshot_phase=SNAPSHOT_PHASE, source_sha256=_digest(raw_source),
                           scope="final_global_cp_selected_zone_shapes" if stage == "global" else "subtree_cp_selected_zone_shapes")
            if capture["capture_status"] != "COMPLETE":
                raise ValueError(capture.get("incomplete_reason", "incomplete_capture"))
            raw_capture = _json_bytes(capture, deadline=self.deadline)
            raw_model = _model_bytes(model, deadline=self.deadline)
            checked_requirements = _registry(model, registry.requirements, source, deadline=self.deadline)
            if model.Validate():
                raise ValueError("model_invalid")
            if self.capture_bytes + len(raw_source) + len(raw_capture) + len(raw_model) > MAX_CAPTURE_BYTES:
                raise ValueError("capture_resource_limit")
            self.capture_bytes += len(raw_source) + len(raw_capture) + len(raw_model)
            if time.monotonic() >= self.deadline:
                raise ValueError("verification_budget_exhausted")
            proto = model.Proto()
            has_hint = proto.has_solution_hint() if hasattr(proto, "has_solution_hint") else proto.HasField("solution_hint")
            has_objective = proto.has_objective() if hasattr(proto, "has_objective") else proto.HasField("objective")
            record.update(capture=json.loads(raw_capture), model_sha256=_digest(raw_model),
                binding={"original_building_sha256": _digest(_json_bytes(self.original_source)),
                         "stage_building_sha256": _digest(_json_bytes(stage_source)),
                         "diagnostic_envelope_sha256": _digest(raw_source),
                         "projection": "original_global_frame" if stage == "global" else "bounded_subtree_stage_projection",
                         "model_source_equivalence": "NOT_PROVEN"},
                snapshot_state={"objective": has_objective, "hints": len(proto.solution_hint.vars) if has_hint else 0,
                                "assumptions": len(proto.assumptions),
                                "enumeration_exclusions": sum(_kind(c) == "table" and c.table.negated for c in proto.constraints)},
                requirements=checked_requirements)
            if time.monotonic() >= self.deadline:
                raise ValueError("verification_budget_exhausted")
            snapshot.update(model=model.clone(), source=json.loads(raw_source), capture=json.loads(raw_capture),
                            requirements=tuple(registry.requirements), model_sha256=record["model_sha256"])
            snapshot["record_sha256"] = _digest(_json_bytes(record, deadline=self.deadline))
        except (ValueError, TypeError, OverflowError, UnicodeError, KeyError, AttributeError) as exc:
            snapshot["error"] = str(exc) or type(exc).__name__
            record["capture"]["capture_status"] = "INCOMPLETE"
            record["capture"]["incomplete_reason"] = snapshot["error"]
        return snapshot

    def explain(self, snapshot):
        if len(self.evidence["explanations"]) >= MAX_CAPTURES:
            self.evidence["explanation_limit_reached"] = True
            return
        remaining = self.deadline - time.monotonic()
        solves = self.budget.max_solves - self.evidence["solves_used"]
        if snapshot["error"]:
            report = _unknown(snapshot["error"])
        elif remaining <= 0 or solves <= 0 or (self.remaining_units is not None and self.remaining_units <= 0):
            report = _unknown("verification_budget_exhausted")
        else:
            try:
                record = self.evidence["captures"][snapshot["capture_index"]]
                if _digest(_json_bytes(record, deadline=self.deadline)) != snapshot["record_sha256"]:
                    raise ValueError("mutated_capture_evidence")
                remaining = self.deadline - time.monotonic()
                if remaining <= 0:
                    raise ValueError("verification_budget_exhausted")
                report = explain_finite_conflict(snapshot["model"], snapshot["requirements"],
                    source=snapshot["source"], capture=snapshot["capture"], expected_model_sha256=snapshot["model_sha256"],
                    budget=ConflictBudget(wall_seconds=min(remaining, 10), max_solves=solves,
                                          deterministic_units=self.remaining_units))
            except (ValueError, TypeError, OverflowError, UnicodeError, KeyError, IndexError) as exc:
                report = _unknown(str(exc) or type(exc).__name__)
            self.evidence["solves_used"] += len(report["searches"])
            if self.remaining_units is not None:
                self.remaining_units -= sum(item["deterministic_units"] for item in report["searches"])
            if time.monotonic() > self.deadline and report["complete"]:
                report.update(status="UNKNOWN", complete=False, reason="verification_budget_exhausted")
                report["core"]["subset_minimal"] = False
        self.evidence["explanations"].append({"capture_index": snapshot["capture_index"],
            "production_model_status": "INFEASIBLE", "production_attempt": 1, "report": report})

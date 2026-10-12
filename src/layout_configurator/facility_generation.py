"""Feasibility-aware candidate selection for regulated facility layouts.

The room solver receives zone relations, opening allowances and necessary
equipment envelopes. A facility candidate is not accepted until independent
room, equipment, route and profile checks have all passed. This module keeps
that orchestration deterministic and
records rejected candidates instead of silently publishing a lucky seed.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, replace

from .building import BuildingIR
from .equipment import (
    EquipmentLayoutResult,
    EquipmentPlacementError,
    EquipmentValidationReport,
    place_equipment_clear_of_doors,
    validate_equipment_layout,
)
from .facility import FacilityProfile, FacilityValidationReport, validate_building
from .flows import FlowRoutingResult, FlowValidationReport, route_flows, validate_flow_routes
from .models import LayoutResult, Rect
from .hierarchy import SOLVER_STRATEGIES, solve_layouts_auto
from .hierarchy_hints import HierarchyHints
from .solver import InfeasibleLayout
from .validation import ValidationReport, validate_layout
from .zone_hierarchy import solve_zone_layouts


FACILITY_SOLVER_STRATEGIES = (*SOLVER_STRATEGIES, "zone_hierarchical", "zone_recursive")


@dataclass(frozen=True)
class FacilityCandidateRejection:
    """A room-solver candidate rejected before publication."""

    attempt: int
    layout_seed: int
    reasons: tuple[str, ...]
    stage_evidence: tuple[dict[str, object], ...] = ()

    def to_dict(self) -> dict[str, object]:
        result = {
            "attempt": self.attempt,
            "layout_seed": self.layout_seed,
            "reasons": list(self.reasons),
        }
        if self.stage_evidence:
            result["stage_evidence"] = list(self.stage_evidence)
        return result


@dataclass(frozen=True)
class FeasibleFacilityCandidate:
    """One accepted candidate and the evidence required to export it."""

    attempt: int
    layout_seed: int
    equipment_seed: int
    equipment_attempts: int
    result: LayoutResult
    layout_report: ValidationReport
    equipment: EquipmentLayoutResult
    equipment_report: EquipmentValidationReport
    flow_routes: FlowRoutingResult
    flow_report: FlowValidationReport
    facility_report: FacilityValidationReport
    # Rooms too small to host their equipment with every door approach clear;
    # there the packer fell back to the unconstrained placement.
    door_approach_exceptions: tuple[str, ...] = ()

    def generation_evidence(self) -> dict[str, object]:
        return {
            "candidate_attempt": self.attempt,
            "layout_seed": self.layout_seed,
            "equipment_seed": self.equipment_seed,
            "equipment_attempts": self.equipment_attempts,
            "door_approach_exceptions": list(self.door_approach_exceptions),
        }


@dataclass(frozen=True)
class FacilityGenerationResult:
    """Accepted variants plus transparent evidence for discarded candidates."""

    requested_variants: int
    max_attempts: int
    attempted_candidates: int
    accepted: tuple[FeasibleFacilityCandidate, ...]
    rejected: tuple[FacilityCandidateRejection, ...]
    seed: int
    time_limit_seconds: float
    equipment_retries: int
    deterministic_units: float | None = None
    room_solver: str = "auto"
    room_solver_used: str | None = None
    hierarchy_fallback_reason: str | None = None
    room_solver_evidence: dict[str, object] | None = None
    failure_evidence: tuple[dict[str, object], ...] = ()
    incremental_evidence: dict[str, object] | None = None

    def to_dict(self) -> dict[str, object]:
        result = {
            "requested_variants": self.requested_variants,
            "max_attempts": self.max_attempts,
            "attempted_candidates": self.attempted_candidates,
            "evaluated_candidates": len(self.accepted) + len(self.rejected),
            "accepted_candidates": len(self.accepted),
            "rejected_candidates": [item.to_dict() for item in self.rejected],
            "seed": self.seed,
            "time_limit_seconds": self.time_limit_seconds,
            # Without the budget mode a manifest cannot say whether the run it
            # describes can be reproduced at all.
            "deterministic_units": self.deterministic_units,
            "repeatable": self.deterministic_units is not None,
            "equipment_retries": self.equipment_retries,
            "room_solver": self.room_solver,
            "room_solver_used": self.room_solver_used,
            "hierarchy_fallback_reason": self.hierarchy_fallback_reason,
        }
        if self.room_solver_evidence is not None:
            result["room_solver_evidence"] = self.room_solver_evidence
        if self.failure_evidence:
            result["failure_evidence"] = list(self.failure_evidence)
        if self.incremental_evidence is not None:
            result["incremental_evidence"] = self.incremental_evidence
        return result


class InfeasibleFacilityGeneration(InfeasibleLayout):
    """Raised when the requested number of fully checked variants is unavailable."""

    def __init__(self, message: str, generation: FacilityGenerationResult):
        super().__init__(message)
        self.generation = generation


def _stage_evidence(stage, status, source_ids, *, proof_scope="candidate_predicates", **details):
    """Describe a checked candidate or bounded search, never the entire program.

    Generic solver exceptions do not expose a CP proof/status. Their search
    outcome therefore stays UNKNOWN; actual independent predicates retain FAIL
    and profile checks retain their original UNKNOWN and source provenance.
    """
    return {"stage": stage, "status": status, "proof_scope": proof_scope,
            "source_ids": sorted(source_ids), "proven_building_infeasible": False, **details}


def solve_feasible_facility_variants(
    building: BuildingIR,
    profile: FacilityProfile,
    *,
    variants: int,
    time_limit_seconds: float,
    seed: int,
    max_attempts: int | None = None,
    equipment_retries: int = 2,
    deterministic_units: float | None = None,
    room_solver: str = "auto",
    fixed_rects: Mapping[str, Rect] | None = None,
    axis_aligned_room_ids: Iterable[str] | None = None,
    structural_axes_x_mm: Iterable[float] | None = None,
    structural_axes_y_mm: Iterable[float] | None = None,
    hint_rects: Mapping[str, Rect] | None = None,
    cache_cluster_shapes: bool = False,
) -> FacilityGenerationResult:
    """Return only candidates that pass every deterministic facility gate.

    The solver is asked for a larger, geometrically distinct candidate pool.
    Each candidate is independently rechecked after equipment packing and flow
    routing.  A failed candidate is retained as evidence; it is never exported
    as one of the requested options.  If the pool cannot supply all requested
    variants, generation fails with a concise, reproducible explanation.

    ``room_solver`` picks the room stage: ``auto`` (hierarchical, falling back
    to monolithic), ``hierarchical``, ``monolithic`` or the opt-in BuildingIR
    root-container backend ``zone_hierarchical`` or ``zone_recursive``, which
    also solves physical descendant containers. Both support checked fixed-room
    and structural-axis constraints. ``fixed_rects`` pins
    rooms, for example a vertical core already placed on another floor.
    ``hint_rects`` provides soft guidance to ordinary or zone-container room
    backends; ``cache_cluster_shapes`` enables invocation-local reuse of checked
    room cluster shapes. Neither option creates fixed-room constraints. Zone
    modes still solve and independently check physical canonical containers.
    """

    if not isinstance(cache_cluster_shapes, bool):
        raise ValueError("cache_cluster_shapes must be a boolean")
    if hint_rects is not None and not isinstance(hint_rects, Mapping):
        raise ValueError("hint_rects must be a room-to-rectangle mapping")
    if variants < 1:
        raise ValueError("variants must be at least 1")
    if time_limit_seconds <= 0:
        raise ValueError("time_limit_seconds must be positive")
    if equipment_retries < 1:
        raise ValueError("equipment_retries must be at least 1")
    if max_attempts is None:
        max_attempts = max(variants * 3, 3)
    if max_attempts < variants:
        raise ValueError("max_attempts must be greater than or equal to variants")

    prepared = building.with_required_flow_opening_width()
    warm_options = {}
    if hint_rects:
        # Validate identities, finite dimensions and the grid even when an
        # explicitly selected monolithic backend would ignore an unknown hint.
        warm_options["hint_rects"] = HierarchyHints(prepared.layout, hint_rects).rects
    if cache_cluster_shapes:
        warm_options["cache_cluster_shapes"] = True
    keep_solver_evidence = bool(warm_options) or room_solver in {"zone_hierarchical", "zone_recursive"}
    equipment_fit_options = {}
    for item in prepared.equipment:
        if item.room_id is not None:
            equipment_fit_options.setdefault(item.room_id, []).append(
                (item.id, item.room_fit_dimensions(prepared.layout.wall_thickness_mm))
            )
    solver_evidence: dict[str, object] = {}
    try:
        minimum = prepared.layout.door_width_mm + 2 * prepared.layout.wall_thickness_mm if prepared.flows else None
        if room_solver in {"zone_hierarchical", "zone_recursive"}:
            room_candidates = solve_zone_layouts(
                prepared, max_attempts, time_limit_seconds, seed,
                minimum_adjacency_mm=minimum, deterministic_units=deterministic_units,
                evidence=solver_evidence,
                recursive_zones=room_solver == "zone_recursive",
                allow_partial=True,
                fixed_rects=fixed_rects,
                axis_aligned_room_ids=axis_aligned_room_ids,
                structural_axes_x_mm=structural_axes_x_mm,
                structural_axes_y_mm=structural_axes_y_mm,
                **warm_options,
            )
        else:
            room_candidates = solve_layouts_auto(
                prepared.layout,
                max_attempts,
                time_limit_seconds,
                seed,
                required_adjacency_groups=prepared.zone_relation_groups("required_adjacency"),
                forbidden_adjacency_groups=prepared.zone_relation_groups("forbidden_adjacency"),
                preferred_adjacency_groups=prepared.zone_relation_groups("preferred_adjacency"),
                minimum_adjacency_mm=minimum,
                equipment_fit_options=equipment_fit_options,
                deterministic_units=deterministic_units,
                strategy=room_solver,
                evidence=solver_evidence,
                fixed_rects=dict(fixed_rects) if fixed_rects else None,
                axis_aligned_room_ids=axis_aligned_room_ids,
                structural_axes_x_mm=structural_axes_x_mm,
                structural_axes_y_mm=structural_axes_y_mm,
                **warm_options,
            )
    except InfeasibleLayout as exc:
        failure = _stage_evidence(
            "rooms", "UNKNOWN", (room.id for room in prepared.layout.rooms),
            proof_scope="configured_room_search", message=str(exc),
            search={"time_limit_seconds": time_limit_seconds, "deterministic_units": deterministic_units,
                    "seed": seed, "max_attempts": max_attempts, "room_solver": room_solver},
        )
        if solver_evidence.get("failure") is not None:
            failure["solver_failure"] = solver_evidence["failure"]
        generation = FacilityGenerationResult(
            requested_variants=variants, max_attempts=max_attempts,
            attempted_candidates=0, accepted=(), rejected=(), seed=seed,
            time_limit_seconds=time_limit_seconds, equipment_retries=equipment_retries,
            deterministic_units=deterministic_units,
            room_solver=room_solver,
            room_solver_used=solver_evidence.get("room_solver_used"),
            hierarchy_fallback_reason=solver_evidence.get("hierarchy_fallback_reason"),
            room_solver_evidence=solver_evidence if keep_solver_evidence else None,
            failure_evidence=(failure,),
        )
        raise InfeasibleFacilityGeneration(
            f"The room solver returned no candidate within the configured search budget: {exc}",
            generation,
        ) from exc

    accepted: list[FeasibleFacilityCandidate] = []
    rejected: list[FacilityCandidateRejection] = []
    for room_candidate in room_candidates:
        attempt = room_candidate.variant
        layout_seed = seed + attempt - 1
        layout_report = validate_layout(prepared.layout, room_candidate)
        if not layout_report.ok:
            rejected.append(
                FacilityCandidateRejection(
                    attempt,
                    layout_seed,
                    tuple(f"layout.{issue.code}: {issue.message}" for issue in layout_report.issues),
                    (_stage_evidence("rooms", "FAIL", (room.id for room in prepared.layout.rooms),
                                     issues=[{"code": issue.code, "message": issue.message}
                                             for issue in layout_report.issues]),),
                )
            )
            continue

        packed: EquipmentLayoutResult | None = None
        equipment_report: EquipmentValidationReport | None = None
        equipment_seed = layout_seed
        equipment_errors: list[str] = []
        equipment_stages: list[dict[str, object]] = []
        door_exceptions: tuple[str, ...] = ()
        for retry in range(equipment_retries):
            equipment_seed = layout_seed + retry
            try:
                # The packer knows where the doors are: a service clearance over
                # a door approach leaves the router no way into the room, which
                # used to reject most pilot candidates after packing succeeded.
                packed, door_exceptions = place_equipment_clear_of_doors(
                    prepared,
                    room_candidate,
                    time_limit_seconds=time_limit_seconds,
                    seed=equipment_seed,
                    deterministic_units=deterministic_units,
                )
                equipment_report = validate_equipment_layout(prepared, room_candidate, packed)
                if equipment_report.ok:
                    break
                equipment_errors = [
                    f"equipment.{issue.code}: {issue.message}" for issue in equipment_report.issues
                ]
                equipment_stages.append(_stage_evidence(
                    "equipment", "FAIL", (item.id for item in prepared.equipment),
                    retry=retry + 1, equipment_seed=equipment_seed,
                    issues=[issue.to_dict() for issue in equipment_report.issues],
                ))
            except EquipmentPlacementError as exc:
                equipment_errors = [f"equipment.infeasible: {exc}"]
                equipment_stages.append(_stage_evidence(
                    "equipment", "UNKNOWN", (item.id for item in prepared.equipment),
                    proof_scope="configured_equipment_search", retry=retry + 1,
                    equipment_seed=equipment_seed, message=str(exc),
                    search={"time_limit_seconds": time_limit_seconds, "deterministic_units": deterministic_units},
                ))
        if packed is None or equipment_report is None or not equipment_report.ok:
            rejected.append(
                FacilityCandidateRejection(
                    attempt,
                    layout_seed,
                    tuple(equipment_errors or ("equipment.infeasible: no placement was returned",)),
                    tuple(equipment_stages),
                )
            )
            continue

        flow_routes = route_flows(prepared, room_candidate, packed)
        flow_report = validate_flow_routes(prepared, room_candidate, flow_routes, packed)
        facility_report = validate_building(
            prepared,
            room_candidate,
            packed,
            flow_routes,
            flow_report,
            profile=profile,
            equipment_report=equipment_report,
        )
        if not facility_report.ok:
            reasons = tuple(
                f"facility.{check.id}.{check.status}: " + "; ".join(check.evidence)
                for check in facility_report.checks
                if not check.ok
            )
            stages = list(equipment_stages)
            if not flow_report.ok:
                stages.append(_stage_evidence(
                    "flows", "FAIL", (flow.id for flow in prepared.flows),
                    issues=[issue.to_dict() for issue in flow_report.issues],
                ))
            checks = [check.to_dict() for check in facility_report.checks if not check.ok]
            stages.append(_stage_evidence(
                "facility", "FAIL" if any(check["status"] == "FAIL" for check in checks) else "UNKNOWN",
                (zone.id for zone in prepared.zones), profile_name=profile.name,
                profile_version=profile.version, checks=checks,
                issues=[issue.to_dict() for issue in facility_report.issues],
            ))
            rejected.append(FacilityCandidateRejection(attempt, layout_seed, reasons, tuple(stages)))
            continue

        published_result = replace(room_candidate, variant=len(accepted) + 1)
        accepted.append(
            FeasibleFacilityCandidate(
                attempt=attempt,
                layout_seed=layout_seed,
                equipment_seed=equipment_seed,
                equipment_attempts=retry + 1,
                result=published_result,
                layout_report=layout_report,
                equipment=packed,
                equipment_report=equipment_report,
                flow_routes=flow_routes,
                flow_report=flow_report,
                facility_report=facility_report,
                door_approach_exceptions=door_exceptions,
            )
        )
        if len(accepted) == variants:
            break

    generation = FacilityGenerationResult(
        requested_variants=variants,
        max_attempts=max_attempts,
        attempted_candidates=len(room_candidates),
        accepted=tuple(accepted),
        rejected=tuple(rejected),
        seed=seed,
        time_limit_seconds=time_limit_seconds,
        equipment_retries=equipment_retries,
        deterministic_units=deterministic_units,
        room_solver=room_solver,
        room_solver_used=solver_evidence.get("room_solver_used"),
        hierarchy_fallback_reason=solver_evidence.get("hierarchy_fallback_reason"),
        room_solver_evidence=solver_evidence if keep_solver_evidence else None,
    )
    if len(accepted) != variants:
        detail = "; ".join(
            f"attempt {item.attempt}: {item.reasons[0]}" for item in rejected[:3] if item.reasons
        )
        if not detail:
            detail = "the room solver produced fewer distinct candidates than requested"
        raise InfeasibleFacilityGeneration(
            f"Only {len(accepted)} of {variants} fully feasible facility variant(s) were found "
            f"within {max_attempts} candidate attempt(s): {detail}",
            generation,
        )
    return generation

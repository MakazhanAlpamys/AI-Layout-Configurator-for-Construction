"""Feasibility-aware candidate selection for regulated facility layouts.

The room solver receives zone relations, opening allowances and necessary
equipment envelopes. A facility candidate is not accepted until independent
room, equipment, route and profile checks have all passed. This module keeps
that orchestration deterministic and
records rejected candidates instead of silently publishing a lucky seed.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from .building import BuildingIR
from .equipment import (
    EquipmentLayoutResult,
    EquipmentPlacementError,
    EquipmentValidationReport,
    place_equipment,
    validate_equipment_layout,
)
from .facility import FacilityProfile, FacilityValidationReport, validate_building
from .flows import FlowRoutingResult, FlowValidationReport, route_flows, validate_flow_routes
from .models import LayoutResult
from .solver import InfeasibleLayout, solve_layouts
from .validation import ValidationReport, validate_layout


@dataclass(frozen=True)
class FacilityCandidateRejection:
    """A room-solver candidate rejected before publication."""

    attempt: int
    layout_seed: int
    reasons: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "attempt": self.attempt,
            "layout_seed": self.layout_seed,
            "reasons": list(self.reasons),
        }


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

    def generation_evidence(self) -> dict[str, int]:
        return {
            "candidate_attempt": self.attempt,
            "layout_seed": self.layout_seed,
            "equipment_seed": self.equipment_seed,
            "equipment_attempts": self.equipment_attempts,
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

    def to_dict(self) -> dict[str, object]:
        return {
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
        }


class InfeasibleFacilityGeneration(InfeasibleLayout):
    """Raised when the requested number of fully checked variants is unavailable."""

    def __init__(self, message: str, generation: FacilityGenerationResult):
        super().__init__(message)
        self.generation = generation


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
) -> FacilityGenerationResult:
    """Return only candidates that pass every deterministic facility gate.

    The solver is asked for a larger, geometrically distinct candidate pool.
    Each candidate is independently rechecked after equipment packing and flow
    routing.  A failed candidate is retained as evidence; it is never exported
    as one of the requested options.  If the pool cannot supply all requested
    variants, generation fails with a concise, reproducible explanation.
    """

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
    equipment_fit_options = {}
    for item in prepared.equipment:
        if item.room_id is not None:
            equipment_fit_options.setdefault(item.room_id, []).append(
                (item.id, item.room_fit_dimensions(prepared.layout.wall_thickness_mm))
            )
    try:
        room_candidates = solve_layouts(
            prepared.layout,
            max_attempts,
            time_limit_seconds,
            seed,
            required_adjacency_groups=prepared.zone_relation_groups("required_adjacency"),
            forbidden_adjacency_groups=prepared.zone_relation_groups("forbidden_adjacency"),
            minimum_adjacency_mm=(
                prepared.layout.door_width_mm + 2 * prepared.layout.wall_thickness_mm
                if prepared.flows
                else None
            ),
            equipment_fit_options=equipment_fit_options,
            deterministic_units=deterministic_units,
        )
    except InfeasibleLayout as exc:
        generation = FacilityGenerationResult(
            requested_variants=variants, max_attempts=max_attempts,
            attempted_candidates=0, accepted=(), rejected=(), seed=seed,
            time_limit_seconds=time_limit_seconds, equipment_retries=equipment_retries,
            deterministic_units=deterministic_units,
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
                )
            )
            continue

        packed: EquipmentLayoutResult | None = None
        equipment_report: EquipmentValidationReport | None = None
        equipment_seed = layout_seed
        equipment_errors: list[str] = []
        for retry in range(equipment_retries):
            equipment_seed = layout_seed + retry
            try:
                packed = place_equipment(
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
            except EquipmentPlacementError as exc:
                equipment_errors = [f"equipment.infeasible: {exc}"]
        if packed is None or equipment_report is None or not equipment_report.ok:
            rejected.append(
                FacilityCandidateRejection(
                    attempt,
                    layout_seed,
                    tuple(equipment_errors or ("equipment.infeasible: no placement was returned",)),
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
            rejected.append(FacilityCandidateRejection(attempt, layout_seed, reasons))
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

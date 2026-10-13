"""Re-solve an edited canonical facility using validated old geometry softly."""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace

from .building import BuildingIR
from .facility import FacilityProfile, _zone_check
from .facility_generation import (
    FacilityGenerationResult,
    InfeasibleFacilityGeneration,
    solve_feasible_facility_variants,
)
from .hierarchy_hints import HierarchyHints
from .models import LayoutResult
from .validation import validate_layout


class IncrementalSourceError(ValueError):
    """The supplied prior source cannot support this incremental request."""

    def __init__(self, message: str, evidence: dict[str, object]):
        super().__init__(message)
        self.evidence = evidence


def _sha256(payload) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"),
                                     ensure_ascii=False, allow_nan=False).encode("utf-8")).hexdigest()


def _source_rows(building: BuildingIR) -> dict[str, dict[str, dict]]:
    raw = building.to_dict()
    return {name: {item["id"]: item for item in items} for name, items in (
        ("rooms", raw["layout"]["rooms"]), ("zones", raw["zones"]),
        ("equipment", raw["equipment"]), ("flows", raw["flows"]))}


def _reject(evidence, code, message, **details):
    failure = {**evidence, "stage": "input", "status": "FAIL", "code": code,
               "message": message, "proof_scope": "supplied_incremental_source",
               "proven_building_infeasible": False, **details}
    raise IncrementalSourceError(message, failure)


def re_solve_facility_after_edit(
    prior_building: BuildingIR,
    prior_result: LayoutResult,
    edited_building: BuildingIR,
    profile: FacilityProfile,
    *,
    variants: int = 1,
    time_limit_seconds: float = 20,
    seed: int = 42,
    max_attempts: int | None = None,
    equipment_retries: int = 2,
    deterministic_units: float | None = None,
    room_solver: str = "hierarchical",
    cache_cluster_shapes: bool = True,
) -> FacilityGenerationResult:
    """Repair a program edit without fixing any room to its old geometry.

    This bounded slice retains all room, zone, equipment and flow source IDs.
    It independently checks prior room geometry, coordinates and hard zone
    relations; it does not claim prior equipment or profile acceptance from a
    geometry-only input. Every returned current candidate passes the complete
    facility-generation gates. Old geometry is soft search guidance, so changed
    room dimensions or equipment fits can invalidate a hinted pattern without
    ruling out the edited program. The final CP-SAT search is unrestricted by
    old positions. Cache state lasts only for this invocation.

    Input objects are never mutated. Both accepted generation and bounded
    failure retain source/edit hashes and unchanged source IDs. Search budgets
    keep their ordinary per-model semantics, rather than a total deadline.
    """
    evidence: dict[str, object] = {
        "scope": "canonical_program_edit_with_unchanged_source_ids",
        "hint_policy": "soft", "synthesized_fixed_room_ids": [],
        "prior_validation_scope": "room_geometry_grid_and_hard_zone_relations",
        "prior_facility_acceptance_claimed": False,
    }
    if not isinstance(prior_building, BuildingIR) or not isinstance(edited_building, BuildingIR):
        _reject(evidence, "invalid_source_type", "Prior and edited programs must be canonical BuildingIR objects")
    unsupported = [name for name, source in (("prior", prior_building), ("edited", edited_building))
                   if source.production_program is not None]
    if unsupported:
        _reject(evidence, "unsupported_production_program",
                "Embedded production-program declarations are not enforced by incremental facility solving",
                unsupported_sources=unsupported)
    if not isinstance(prior_result, LayoutResult) or not isinstance(profile, FacilityProfile):
        _reject(evidence, "invalid_source_type", "Prior geometry and current profile must be canonical objects")
    try:
        evidence.update(source_building_sha256=_sha256(prior_building.to_dict()),
                        source_layout_sha256=_sha256(prior_result.to_dict()),
                        edited_building_sha256=_sha256(edited_building.to_dict()),
                        profile_sha256=_sha256(profile.to_dict()))
    except (AttributeError, TypeError, ValueError) as exc:
        _reject(evidence, "invalid_source_values", f"Canonical source values cannot be hashed: {exc}")
    before = _source_rows(prior_building)
    after = _source_rows(edited_building)
    evidence["source_ids"] = {name: sorted(rows) for name, rows in before.items()}
    differences = {name: {"removed": sorted(set(before[name]) - set(after[name])),
                          "added": sorted(set(after[name]) - set(before[name]))}
                   for name in before if set(before[name]) != set(after[name])}
    if differences:
        _reject(evidence, "source_ids_changed", "Incremental re-solve requires unchanged canonical source IDs",
                source_id_changes=differences)
    evidence["changed_source_ids"] = {
        name: sorted(item_id for item_id in before[name] if before[name][item_id] != after[name][item_id])
        for name in before}
    evidence["unchanged_source_ids"] = {
        name: sorted(item_id for item_id in before[name] if before[name][item_id] == after[name][item_id])
        for name in before}
    prior_prepared = prior_building.with_required_flow_opening_width()
    try:
        prior_hints = HierarchyHints(prior_prepared.layout, prior_result.placements)
        prior_validation = validate_layout(prior_prepared.layout, prior_result)
    except (TypeError, ValueError) as exc:
        _reject(evidence, "prior_geometry_invalid", f"Prior geometry is not a valid soft-hint source: {exc}")
    evidence["prior_layout_validation"] = {
        "ok": prior_validation.ok,
        "issues": [{"code": issue.code, "message": issue.message} for issue in prior_validation.issues]}
    if not prior_validation.ok:
        _reject(evidence, "prior_geometry_invalid", "Independent validation rejects the prior room geometry")
    prior_zones = _zone_check(prior_prepared, prior_result)
    evidence["prior_zone_validation"] = prior_zones.to_dict()
    if not prior_zones.ok:
        _reject(evidence, "prior_geometry_invalid", "Independent validation rejects the prior hard zone relations")
    current_hints = {}
    skipped = []
    current_layout = edited_building.with_required_flow_opening_width().layout
    for room_id, rect in prior_hints.rects.items():
        try:
            current_hints.update(HierarchyHints(current_layout, {room_id: rect}).rects)
        except ValueError as exc:
            # A source can be valid on its former grid and unusable on the
            # edited grid. Discard that soft hint without changing geometry.
            skipped.append({"room_id": room_id, "reason": str(exc)})
    evidence["hint_sources"] = {"retained_room_ids": sorted(current_hints),
                                "skipped": sorted(skipped, key=lambda item: item["room_id"])}
    try:
        generation = solve_feasible_facility_variants(
            edited_building, profile, variants=variants, time_limit_seconds=time_limit_seconds, seed=seed,
            max_attempts=max_attempts, equipment_retries=equipment_retries,
            deterministic_units=deterministic_units, room_solver=room_solver,
            hint_rects=current_hints, cache_cluster_shapes=cache_cluster_shapes)
    except InfeasibleFacilityGeneration as exc:
        failed = replace(exc.generation, incremental_evidence=evidence)
        raise InfeasibleFacilityGeneration(str(exc), failed) from exc
    return replace(generation, incremental_evidence=evidence)

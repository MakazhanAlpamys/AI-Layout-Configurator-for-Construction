"""Bounded canonical orthogonal candidates through fresh local facility gates.

This opt-in orchestrator composes the existing B5/B6/B7/B10 components. It does
not create a second IR or weaken rectangular dispatch. A locally accepted
candidate is DRAFT until complete persisted exchange gates are integrated.
Finite catalog exhaustion never proves whole-program infeasibility.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass, field, replace
import hashlib
import json
import math
import time

from .building import BuildingIR
from .equipment import EquipmentLayoutResult, EquipmentPlacement
from .facility import DEFAULT_FACILITY_PROFILE_SCHEMA_PATH, FacilityProfile
from .flows import FlowRoutingResult
from .models import LayoutResult, Rect
from .orthogonal import OrthogonalPolygon
from .orthogonal_equipment import OrthogonalPackingBudget, place_orthogonal_equipment
from .orthogonal_facility import OrthogonalFacilityReport, validate_orthogonal_building
from .orthogonal_flows import OrthogonalFlowBudget, route_orthogonal_flows
from .orthogonal_solver import OrthogonalSolverBudget, solve_orthogonal_candidates
from .schema import normalize_building_mapping, validate_mapping


def _integer(value, name, minimum=1, maximum=128):
    if not isinstance(value, int) or isinstance(value, bool) or not minimum <= value <= maximum:
        raise ValueError(f"{name} requires integer {minimum}..{maximum}")


@dataclass(frozen=True)
class OrthogonalGenerationBudget:
    max_room_candidates: int = 8
    max_equipment_retries: int = 8
    max_rejections: int = 128
    room_budget: OrthogonalSolverBudget = field(default_factory=OrthogonalSolverBudget)
    equipment_budget: OrthogonalPackingBudget = field(default_factory=OrthogonalPackingBudget)
    flow_budget: OrthogonalFlowBudget = field(default_factory=OrthogonalFlowBudget)

    def __post_init__(self):
        for name, maximum in (("max_room_candidates", 128), ("max_equipment_retries", 128), ("max_rejections", 1024)):
            _integer(getattr(self, name), name, maximum=maximum)
        for name, expected in (("room_budget", OrthogonalSolverBudget), ("equipment_budget", OrthogonalPackingBudget), ("flow_budget", OrthogonalFlowBudget)):
            if not isinstance(getattr(self, name), expected):
                raise ValueError(f"{name} requires {expected.__name__}")

    def to_dict(self):
        return {"max_room_candidates": self.max_room_candidates, "max_equipment_retries": self.max_equipment_retries,
                "max_rejections": self.max_rejections, "room_budget": self.room_budget.to_dict(),
                "equipment_budget": self.equipment_budget.to_dict(), "flow_budget": self.flow_budget.to_dict()}


@dataclass(frozen=True)
class OrthogonalFacilityCandidate:
    result: LayoutResult
    equipment: EquipmentLayoutResult
    flow_routes: FlowRoutingResult
    facility_report: OrthogonalFacilityReport
    evidence: dict

    def to_dict(self, building: BuildingIR):
        _require_source(building, self.facility_report.source["building_sha256"])
        return {"layout": self.result.to_dict(), "equipment": self.equipment.to_dict(building),
                "routing": self.flow_routes.to_dict(), "facility_report": self.facility_report.to_dict(),
                "generation": deepcopy(self.evidence)}


@dataclass(frozen=True)
class OrthogonalGenerationResult:
    accepted: tuple[OrthogonalFacilityCandidate, ...]
    rejected: tuple[dict, ...]
    requested_variants: int
    evidence: dict

    @property
    def status(self):
        return "DRAFT" if len(self.accepted) == self.requested_variants else "UNKNOWN"

    @property
    def ok(self):
        return False

    def to_dict(self, building: BuildingIR):
        _require_source(building, self.evidence["source"]["building_sha256"])
        return {"backend": "orthogonal_facility_catalog_v1", "status": self.status, "ok": False,
                "scope": "Bounded canonical room/equipment/route search and declared local profile predicates only",
                "requested_variants": self.requested_variants, "accepted_candidates": len(self.accepted),
                "accepted": [candidate.to_dict(building) for candidate in self.accepted], "rejected": deepcopy(list(self.rejected)),
                "declared_local_predicates": {"status": "PASS" if self.status == "DRAFT" else "UNKNOWN"},
                "full_facility_validation": {"status": "UNKNOWN", "ok": False}, "engineering_acceptance": "PENDING",
                "proven_building_infeasible": False, **deepcopy(self.evidence)}


def generate_orthogonal_facility_variants(building: BuildingIR, *, profile: FacilityProfile,
        room_catalog: Mapping[str, Sequence[Rect | OrthogonalPolygon]] | None = None,
        equipment_catalog: Mapping[str, Sequence[EquipmentPlacement]] | None = None,
        fixed_footprints: Mapping[str, Rect | OrthogonalPolygon] | None = None,
        variants: int = 1, seed: int = 42, time_limit_seconds: float = 10,
        deterministic_units: float | None = None,
        budget: OrthogonalGenerationBudget = OrthogonalGenerationBudget()) -> OrthogonalGenerationResult:
    """Compose actual geometry components without trusting their prior reports.

    The room backend supplies a finite independently checked candidate pool;
    valid partial pools are retained. Complete equipment combinations rejected
    downstream are excluded by exact no-good constraints on the next retry.
    Every accepted candidate passes a fresh B10 source/profile check.

    Wall-clock time limits stop launching stages and cap each CP invocation
    with the remaining time. Existing route/profile predicates are bounded but
    cannot be preempted mid-stage; a late candidate is rejected. In deterministic
    mode the work allowance is PER CP invocation; catalog/route/profile bounds
    remain active and no aggregate consumed-work or global wall-clock guarantee
    is implied. Repeatability requires the same runtime/platform.
    """
    # perf_counter uses a high-resolution monotonic clock on Windows, where
    # Python 3.11 time.monotonic may have a 15.625 ms GetTickCount64 resolution.
    started = time.perf_counter()
    building, profile = _inputs(building, profile, budget, variants, seed, time_limit_seconds,
                                deterministic_units, room_catalog, equipment_catalog, fixed_footprints)
    source = {"building_sha256": _hash(building.to_dict()), "profile_sha256": _hash(profile.to_dict())}
    state = {"source": source, "seed": seed, "time_limit_seconds": time_limit_seconds,
             "deterministic_units": deterministic_units, "repeatable_within_same_runtime": deterministic_units is not None,
             "budget": budget.to_dict(), "budget_scope": "Wall-clock between bounded stages; deterministic allowance per CP invocation",
             "evaluated_room_candidates": 0, "equipment_attempts": 0, "route_attempts": 0, "profile_attempts": 0,
             "rejection_count": 0, "rejection_counts": {}, "rejections_truncated": False,
             "last_rejection": None, "stop_reason": None, "room_search": None,
             "search_limitations": ["Finite B5 footprint family and B6 placement domains",
                                    "B7 bounded critical-coordinate visibility search",
                                    "No complete persisted polygon bundle acceptance in this component"]}
    accepted, rejections = [], []
    deadline = started + time_limit_seconds if deterministic_units is None else math.inf

    def reject(stage, status, evidence, *, room_candidate=None, equipment_attempt=None):
        scope = "candidate_predicates" if status == "FAIL" else "profile_source_requirements" if stage == "profile" else "configured_catalog_search"
        row = {"stage": stage, "status": status, "scope": scope,
               "source": source, "room_candidate": room_candidate, "equipment_attempt": equipment_attempt,
               "source_ids": {"rooms": [room.id for room in building.layout.rooms], "zones": [zone.id for zone in building.zones],
                              "equipment": [item.id for item in building.equipment], "flows": [flow.id for flow in building.flows]},
               "proven_building_infeasible": False, "evidence": deepcopy(evidence)}
        state["rejection_count"] += 1
        state["rejection_counts"][stage] = state["rejection_counts"].get(stage, 0) + 1
        state["last_rejection"] = row
        if len(rejections) < budget.max_rejections:
            rejections.append(row)
        else:
            state["rejections_truncated"] = True

    def finish(reason):
        state["stop_reason"] = reason
        return OrthogonalGenerationResult(tuple(accepted), tuple(rejections), variants, state)

    if any(len(items) > 64 for items in (building.layout.rooms, building.zones, building.equipment, building.flows)):
        reject("source", "UNKNOWN", {"reason": "Component source-size limit of 64 exceeded"})
        return finish("source_size_limit")
    if time.perf_counter() >= deadline:
        reject("budget", "UNKNOWN", {"reason": "Time budget exhausted during canonical preparation"})
        return finish("time_limit_during_preparation")
    solved = solve_orthogonal_candidates(building.layout, catalog=room_catalog, fixed_footprints=fixed_footprints,
        variants=budget.max_room_candidates, seed=seed, time_limit_seconds=min(time_limit_seconds, deadline - time.perf_counter()),
        deterministic_units=deterministic_units, budget=budget.room_budget)
    state["room_search"] = solved.to_dict()
    if time.perf_counter() >= deadline:
        reject("budget", "UNKNOWN", {"reason": "Room stage completed after wall-clock deadline", "room_search": solved.to_dict()})
        return finish("time_limit_after_rooms")
    if not solved.candidates:
        reject("rooms", "UNKNOWN", {"room_search": solved.to_dict()})
        return finish("no_verified_room_candidate")
    for room_index, room_candidate in enumerate(solved.candidates, start=1):
        if time.perf_counter() >= deadline:
            reject("budget", "UNKNOWN", {"reason": "Stage-boundary wall-clock limit reached"}, room_candidate=room_index)
            return finish("time_limit_between_stages")
        state["evaluated_room_candidates"] += 1
        layout = replace(room_candidate.result, variant=len(accepted) + 1)
        excluded = []
        for equipment_attempt in range(1, budget.max_equipment_retries + 1):
            if time.perf_counter() >= deadline:
                reject("budget", "UNKNOWN", {"reason": "Stage-boundary wall-clock limit reached"}, room_candidate=room_index)
                return finish("time_limit_between_stages")
            state["equipment_attempts"] += 1
            packing = place_orthogonal_equipment(building, layout, catalog=equipment_catalog, excluded_layouts=tuple(excluded),
                seed=seed, time_limit_seconds=min(time_limit_seconds, deadline - time.perf_counter()),
                deterministic_units=deterministic_units, budget=budget.equipment_budget)
            if packing.equipment is None or packing.status != "DRAFT":
                reject("equipment", "UNKNOWN", {"packing": packing.to_dict()}, room_candidate=room_index, equipment_attempt=equipment_attempt)
                break
            if time.perf_counter() >= deadline:
                reject("budget", "UNKNOWN", {"reason": "Equipment stage completed after wall-clock deadline", "packing": packing.to_dict()},
                       room_candidate=room_index, equipment_attempt=equipment_attempt)
                return finish("time_limit_after_equipment")
            state["route_attempts"] += 1
            flow = route_orthogonal_flows(building, layout, packing.equipment, budget=budget.flow_budget)
            if flow.status != "DRAFT":
                reject("routes", "UNKNOWN", {"packing": packing.to_dict(), "routing": flow.to_dict()},
                       room_candidate=room_index, equipment_attempt=equipment_attempt)
                excluded.append(packing.equipment)
                continue
            if time.perf_counter() >= deadline:
                reject("budget", "UNKNOWN", {"reason": "Route stage completed after wall-clock deadline", "routing": flow.to_dict()},
                       room_candidate=room_index, equipment_attempt=equipment_attempt)
                return finish("time_limit_after_routes")
            state["profile_attempts"] += 1
            report = validate_orthogonal_building(building, layout, packing.equipment, flow.routing, profile=profile)
            if not report.ok:
                reject("profile", report.status, {"packing": packing.to_dict(), "routing": flow.to_dict(), "report": report.to_dict()},
                       room_candidate=room_index, equipment_attempt=equipment_attempt)
                excluded.append(packing.equipment)
                continue
            if time.perf_counter() >= deadline:
                reject("budget", "UNKNOWN", {"reason": "Fresh profile stage completed after wall-clock deadline", "report": report.to_dict()},
                       room_candidate=room_index, equipment_attempt=equipment_attempt)
                return finish("time_limit_after_profile")
            candidate = OrthogonalFacilityCandidate(layout, packing.equipment, flow.routing, report,
                {"room_candidate": room_index, "equipment_attempt": equipment_attempt, "seed": seed,
                 "room_candidate_ids": dict(room_candidate.candidate_ids), "packing": packing.to_dict(), "routing": flow.to_dict()})
            accepted.append(candidate)
            if len(accepted) == variants:
                return finish("requested_local_candidates_verified")
            break
    reject("candidate_pool", "UNKNOWN", {"reason": "Configured candidate pool did not supply the complete requested accepted set",
        "requested_variants": variants, "accepted_candidates": len(accepted),
        "room_search_stop_reason": solved.evidence["stop_reason"]})
    return finish("bounded_candidate_pool_evaluated")


def _inputs(building, profile, budget, variants, seed, time_limit_seconds, deterministic_units,
            room_catalog, equipment_catalog, fixed_footprints):
    if not isinstance(building, BuildingIR) or not isinstance(profile, FacilityProfile):
        raise ValueError("Orthogonal generation requires canonical BuildingIR and FacilityProfile")
    if not isinstance(budget, OrthogonalGenerationBudget):
        raise ValueError("budget requires OrthogonalGenerationBudget")
    _integer(variants, "variants", maximum=budget.max_room_candidates)
    _integer(seed, "seed", minimum=0, maximum=2**31 - 1)
    for name, value in (("time_limit_seconds", time_limit_seconds), ("deterministic_units", deterministic_units)):
        if value is None and name == "deterministic_units":
            continue
        if not isinstance(value, (float, int)) or isinstance(value, bool) or not math.isfinite(value) or value <= 0:
            raise ValueError(f"{name} requires a positive finite number")
    raw_building = deepcopy(building.to_dict())
    if validate_mapping(raw_building["layout"]):
        raise ValueError("Canonical nested LayoutIR does not match schema before normalization")
    building = normalize_building_mapping(raw_building)
    raw_profile = deepcopy(profile.to_dict())
    errors = validate_mapping(raw_profile, DEFAULT_FACILITY_PROFILE_SCHEMA_PATH)
    if errors or not 1 <= len(profile.rules) <= 1000:
        raise ValueError("Orthogonal generation requires a schema-valid profile with 1..1000 declared rules")
    profile = FacilityProfile.from_mapping(raw_profile)
    if profile.to_dict() != raw_profile:
        raise ValueError("Facility profile serialization must already be canonical")
    for name, catalog, identifiers, expected in (("room_catalog", room_catalog, set(building.layout.room_by_id), (Rect, OrthogonalPolygon)),
        ("equipment_catalog", equipment_catalog, {item.id for item in building.equipment}, (EquipmentPlacement,))):
        if catalog is not None:
            if not isinstance(catalog, Mapping) or set(catalog) != identifiers:
                raise ValueError(f"{name} must map every canonical source ID and no unknown IDs")
            maximum = budget.room_budget.max_catalog_steps_per_room if name == "room_catalog" else budget.equipment_budget.max_catalog_steps_per_equipment
            if any(not isinstance(values, (list, tuple)) or len(values) > maximum or any(not isinstance(value, expected) for value in values) for values in catalog.values()):
                raise ValueError(f"{name} requires bounded canonical candidate lists/tuples")
    if fixed_footprints is not None and (not isinstance(fixed_footprints, Mapping) or set(fixed_footprints) - set(building.layout.room_by_id)):
        raise ValueError("fixed_footprints must map known canonical room IDs")
    _hash(building.to_dict())
    _hash(profile.to_dict())
    return building, profile


def _hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
        allow_nan=False, separators=(",", ":")).encode()).hexdigest()


def _require_source(building, expected):
    if not isinstance(building, BuildingIR) or _hash(building.to_dict()) != expected:
        raise ValueError("Generation evidence must be serialized against its exact canonical building source")

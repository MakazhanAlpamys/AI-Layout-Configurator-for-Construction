"""Bounded opt-in catalog search over actual orthogonal room footprints.

The generated catalog contains grid rectangles and single-notch L footprints
in the floor bounding window, clipped against actual source cutouts. It is a
finite pattern family, not a general orthogonal feasibility decision procedure.
Every published room candidate passes independent project geometry QA. Overall
output stays DRAFT: complete equipment/route/profile/export consumers are not
dispatched here, and the rectangular facility pipeline remains unchanged.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
import hashlib
import json
import math
import time

from shapely.geometry import box

from .models import LayoutIR, LayoutResult, Rect
from .orthogonal import OrthogonalPolygon
from .orthogonal_validation import OrthogonalDimensionPolicy, OrthogonalValidationReport, check_room_minimum_dimensions, validate_orthogonal_layout
from .schema import normalize_mapping
from .search import configure_solver


@dataclass(frozen=True)
class OrthogonalSolverBudget:
    max_rooms: int = 64
    max_candidates_per_room: int = 128
    max_catalog_steps_per_room: int = 8192
    max_pair_checks: int = 100_000
    max_attempts: int = 32
    max_rejections: int = 128
    dimension_policy: OrthogonalDimensionPolicy = field(default_factory=OrthogonalDimensionPolicy)

    def __post_init__(self):
        for name in ("max_rooms", "max_candidates_per_room", "max_catalog_steps_per_room", "max_pair_checks", "max_attempts", "max_rejections"):
            value = getattr(self, name)
            if not isinstance(value, int) or isinstance(value, bool) or not 1 <= value <= 1_000_000:
                raise ValueError(f"{name} requires an integer between 1 and 1000000")
        if not isinstance(self.dimension_policy, OrthogonalDimensionPolicy):
            raise ValueError("dimension_policy requires OrthogonalDimensionPolicy")

    def to_dict(self):
        return {**{name: getattr(self, name) for name in ("max_rooms", "max_candidates_per_room", "max_catalog_steps_per_room", "max_pair_checks", "max_attempts", "max_rejections")},
                "dimension_policy": self.dimension_policy.to_dict()}


@dataclass(frozen=True)
class OrthogonalRoomCandidate:
    result: LayoutResult
    room_report: OrthogonalValidationReport
    candidate_ids: dict[str, str]

    def to_dict(self):
        return {"layout": self.result.to_dict(), "room_report": self.room_report.to_dict(), "candidate_ids": self.candidate_ids}


@dataclass(frozen=True)
class OrthogonalSolveResult:
    candidates: tuple[OrthogonalRoomCandidate, ...]
    requested_variants: int
    evidence: dict

    @property
    def status(self):
        return "DRAFT" if len(self.candidates) == self.requested_variants else "UNKNOWN"

    @property
    def ok(self):
        return False

    def to_dict(self):
        return {"status": self.status, "ok": self.ok, "backend": "orthogonal_catalog_v1",
                "scope": "Finite catalog room geometry search; not a general infeasibility proof or full facility acceptance",
                "full_facility_validation": {"status": "UNKNOWN", "evidence": ["Polygon equipment packing, routes, profiles and complete exporters are not dispatched"]},
                "requested_variants": self.requested_variants, "candidates": [candidate.to_dict() for candidate in self.candidates], **self.evidence}


def solve_orthogonal_candidates(spec: LayoutIR, *, catalog: Mapping[str, Sequence[Rect | OrthogonalPolygon]] | None = None,
                                fixed_footprints: Mapping[str, Rect | OrthogonalPolygon] | None = None,
                                variants: int = 1, seed: int = 42, time_limit_seconds: float = 10,
                                deterministic_units: float | None = None,
                                budget: OrthogonalSolverBudget = OrthogonalSolverBudget()) -> OrthogonalSolveResult:
    """Select real source-compliant footprints and independently check doors.

    An explicit catalog supplies a finite solver domain, outside canonical IR.
    Fixed geometry is a separately hashed constraint. Any budget stop or finite
    domain exhaustion is UNKNOWN; a selected combination is only a DRAFT after
    complete scoped room QA passes. No bbox is used for collision/containment.
    """
    from ortools.sat.python import cp_model

    if not isinstance(spec, LayoutIR):
        raise ValueError("Orthogonal solver source requires canonical LayoutIR")
    source_hash = _hash_json(spec.to_dict())
    normalize_mapping(spec.to_dict())
    if not isinstance(budget, OrthogonalSolverBudget):
        raise ValueError("budget requires OrthogonalSolverBudget")
    _integer(variants, "variants")
    _integer(seed, "seed", minimum=0, maximum=2**31 - 1)
    _positive(time_limit_seconds, "time_limit_seconds")
    if deterministic_units is not None:
        _positive(deterministic_units, "deterministic_units")
    fixed = {} if fixed_footprints is None else fixed_footprints
    if not isinstance(fixed, Mapping) or set(fixed) - set(spec.room_by_id):
        raise ValueError("Fixed footprints require a mapping of known source room IDs")
    if catalog is not None and (not isinstance(catalog, Mapping) or set(catalog) != set(spec.room_by_id)):
        raise ValueError("Explicit catalog must map every source room ID and no unknown IDs")
    allowed = box(0, 0, spec.boundary.width_mm, spec.boundary.height_mm)
    for cutout in spec.boundary.cutouts:
        allowed = allowed.difference(box(cutout.x, cutout.y, cutout.right, cutout.top))
    for room_id, shape in fixed.items():
        rejection = _candidate_failure(spec, spec.room_by_id[room_id], shape, allowed, budget)
        if rejection:
            raise ValueError(f"Fixed footprint {room_id} rejected: {rejection}")
    fixed_hash = _hash_json({room_id: _shape_data(shape) for room_id, shape in sorted(fixed.items())})
    state = {"source_sha256": source_hash, "fixed_constraints_sha256": fixed_hash, "catalog_sha256": None,
             "catalog_fingerprint_complete": False,
             "seed": seed, "time_limit_seconds": time_limit_seconds, "deterministic_units": deterministic_units,
             "repeatable": deterministic_units is not None, "budget": budget.to_dict(),
             "catalog_mode": "explicit" if catalog is not None else "generated_rect_and_floor_window_L",
             "catalog_truncated": False, "catalog_stats": {}, "pair_checks": 0, "attempts": 0,
             "rejection_count": 0, "rejection_counts": {}, "rejections": [], "last_rejection": None, "stop_reason": None}
    accepted = []
    deadline = time.monotonic() + time_limit_seconds if deterministic_units is None else math.inf

    def reject(stage, reason, **details):
        state["rejection_count"] += 1
        state["rejection_counts"][stage] = state["rejection_counts"].get(stage, 0) + 1
        state["last_rejection"] = {"stage": stage, "reason": reason, **details}
        if len(state["rejections"]) < budget.max_rejections:
            state["rejections"].append(state["last_rejection"])

    def finish(reason):
        state["stop_reason"] = reason
        return OrthogonalSolveResult(tuple(accepted), variants, state)

    if len(spec.rooms) > budget.max_rooms:
        return finish("room_limit")
    domains, raw_fingerprints = {}, {}
    for room in sorted(spec.rooms, key=lambda item: item.id):
        if catalog is not None:
            values = catalog[room.id]
            if not isinstance(values, (list, tuple)) or len(values) > budget.max_catalog_steps_per_room:
                raise ValueError("Explicit catalog requires bounded lists/tuples of canonical footprints")
            raw_fingerprints[room.id] = sorted(_hash_json(_shape_data(shape)) for shape in values)
            shapes = iter(sorted(values, key=_shape_key))
            counter = {"steps": 0, "truncated": False}
        elif room.id in fixed:
            shapes, counter = iter((fixed[room.id],)), {"steps": 0, "truncated": False}
            raw_fingerprints[room.id] = []
        else:
            counter = {"steps": 0, "truncated": False}
            shapes = _generate_shapes(spec, room, allowed, budget, counter,
                lambda shape, error: reject("catalog_geometry", "unrepresentable_clipping",
                    room_id=room.id, candidate_id=_hash_json(_shape_data(shape)), detail=error), deadline=deadline)
            raw_fingerprints[room.id] = []
        retained, examined = {}, 0
        for shape in shapes:
            if time.monotonic() >= deadline:
                counter["truncated"] = True
                break
            examined += 1
            candidate_id = _hash_json(_shape_data(shape))
            if catalog is None:
                raw_fingerprints[room.id].append(candidate_id)
            failure = _candidate_failure(spec, room, shape, allowed, budget)
            if room.id in fixed and candidate_id != _hash_json(_shape_data(fixed[room.id])):
                failure = "fixed_geometry: candidate does not match the exact fixed footprint"
            if failure:
                reject("catalog", failure, room_id=room.id, candidate_id=candidate_id)
                continue
            if candidate_id not in retained:
                if len(retained) >= budget.max_candidates_per_room:
                    counter["truncated"] = True
                    break
                retained[candidate_id] = shape
        domains[room.id] = tuple(sorted(retained.items(), key=lambda entry: _shape_key(entry[1])))
        state["catalog_stats"][room.id] = {"steps": counter["steps"], "examined": examined, "retained": len(retained),
                                         "truncated": counter["truncated"], "unrepresentable_clips": counter.get("unrepresentable_clips", 0)}
        state["catalog_truncated"] |= counter["truncated"]
    state["catalog_sha256"] = _hash_json({"inputs": raw_fingerprints, "effective": {room_id: [identifier for identifier, _ in values] for room_id, values in domains.items()}})
    state["catalog_fingerprint_complete"] = True
    if time.monotonic() >= deadline:
        return finish("time_limit_during_catalog")
    if any(not values for values in domains.values()):
        return finish("no_candidate_in_finite_catalog")
    model = cp_model.CpModel()
    choices = {room_id: tuple(model.NewBoolVar(f"{room_id}_{index}") for index in range(len(values))) for room_id, values in domains.items()}
    for room_id in domains:
        model.AddExactlyOne(choices[room_id])
    required, forbidden = set(spec.relation_pairs("required_adjacency")), set(spec.relation_pairs("forbidden_adjacency"))
    preferred = set(spec.relation_pairs("preferred_adjacency"))
    preference_terms = []
    room_ids = sorted(domains)
    polygons = {room_id: tuple(_polygon(shape) for _, shape in values) for room_id, values in domains.items()}
    for first_index, first in enumerate(room_ids):
        for second in room_ids[first_index + 1:]:
            pair = (first, second)
            for left_index, left in enumerate(polygons[first]):
                for right_index, right in enumerate(polygons[second]):
                    if state["pair_checks"] >= budget.max_pair_checks or time.monotonic() >= deadline:
                        return finish("pair_check_limit" if state["pair_checks"] >= budget.max_pair_checks else "time_limit_during_pairs")
                    state["pair_checks"] += 1
                    edges = left.shared_edges(right)
                    contiguous = max((abs(b[0] - a[0]) + abs(b[1] - a[1]) for a, b in edges), default=0)
                    reason = "overlap" if left.intersection_area(right) > 0 else "forbidden_contact" if pair in forbidden and edges else "required_contiguous_door" if pair in required and contiguous + 1e-6 < spec.door_width_mm else None
                    a, b = choices[first][left_index], choices[second][right_index]
                    if reason:
                        model.Add(a + b <= 1)
                        reject("pair", reason, rooms=list(pair), candidate_ids=[domains[first][left_index][0], domains[second][right_index][0]])
                    elif pair in preferred and contiguous + 1e-6 >= spec.door_width_mm:
                        together = model.NewBoolVar(f"preferred_{first}_{left_index}_{second}_{right_index}")
                        model.Add(together <= a)
                        model.Add(together <= b)
                        model.Add(together >= a + b - 1)
                        preference_terms.append(together)
    position_scores = {room_id: tuple(round((shape.bounds_rect.x + shape.bounds_rect.y) / spec.grid_mm) * budget.max_candidates_per_room + index
                                     for index, shape in enumerate(footprints)) for room_id, footprints in polygons.items()}
    position_limit = sum(max(scores) for scores in position_scores.values())
    preference_weight = position_limit + 1
    area_weight = len(preferred) * preference_weight + position_limit + 1
    terms, coefficient_bound = [], 0
    for room_id, values in domains.items():
        coefficients = []
        for index, (_, shape) in enumerate(values):
            footprint = _polygon(shape)
            area_error = round(abs(footprint.area_mm2 - spec.room_by_id[room_id].target_area_m2 * 1e6))
            coefficient = area_error * area_weight + position_scores[room_id][index]
            coefficients.append(abs(coefficient))
            terms.append(choices[room_id][index] * coefficient)
        coefficient_bound += max(coefficients)
    if coefficient_bound + len(preferred) * preference_weight >= 2**62:
        return finish("objective_numeric_limit")
    model.Minimize(sum(terms) - sum(preference_terms) * preference_weight)
    remaining_units = deterministic_units
    while state["attempts"] < budget.max_attempts and len(accepted) < variants:
        remaining_seconds = deadline - time.monotonic()
        if remaining_seconds <= 0 or remaining_units is not None and remaining_units <= 0:
            return finish("search_budget_exhausted")
        solver = cp_model.CpSolver()
        configure_solver(solver, seed=seed, time_limit_seconds=min(time_limit_seconds, remaining_seconds), deterministic_units=remaining_units)
        state["attempts"] += 1
        status = solver.Solve(model)
        if remaining_units is not None:
            remaining_units -= solver.ResponseProto().deterministic_time
        if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            return finish("finite_catalog_exhausted" if status == cp_model.INFEASIBLE else "solver_model_invalid" if status == cp_model.MODEL_INVALID else "search_budget_exhausted")
        selected = {room_id: next(index for index, choice in enumerate(choices[room_id]) if solver.Value(choice)) for room_id in room_ids}
        result = LayoutResult(len(accepted) + 1, {room_id: domains[room_id][index][1] for room_id, index in selected.items()}, solver.ObjectiveValue())
        report = validate_orthogonal_layout(spec, result, dimension_policy=budget.dimension_policy)
        if report.ok:
            accepted.append(OrthogonalRoomCandidate(result, report, {room_id: domains[room_id][index][0] for room_id, index in selected.items()}))
        else:
            reject("room_qa", "; ".join(f"{check.id}: {'; '.join(check.evidence)}" for check in report.checks if check.status != "PASS"), attempt=state["attempts"], status=report.status)
        model.Add(sum(choices[room_id][index] for room_id, index in selected.items()) <= len(room_ids) - 1)
    return finish("requested_room_candidates_verified" if len(accepted) == variants else "attempt_limit")


def _candidate_failure(spec, room, shape, allowed, budget):
    footprint = _polygon(shape)
    if any(abs(value / spec.grid_mm - round(value / spec.grid_mm)) > 1e-7 for point in footprint.vertices for value in point):
        return "grid: footprint vertices do not align with source grid_mm"
    if not allowed.covers(footprint.geometry):
        return "boundary: actual footprint leaves boundary or intersects a cutout"
    if not room.min_area_m2 - 1e-6 <= footprint.area_m2 <= room.max_area_m2 + 1e-6:
        return "area: actual footprint area outside source range"
    minimum = check_room_minimum_dimensions(room, shape, policy=budget.dimension_policy)
    if minimum.status != "PASS":
        return f"min_dimension/{minimum.status}: {minimum.message}"
    parts = footprint.inset_components(spec.wall_thickness_mm / 2)
    if len(parts) != 1:
        return f"usable_components: {len(parts)} components after wall-half inset"
    return None


def _generate_shapes(spec, room, allowed, budget, counter, on_unsupported=None, *, deadline=math.inf):
    """Finite grid pattern family. Every enumeration step consumes a bound."""
    grid = spec.grid_mm
    width, height = math.floor(spec.boundary.width_mm / grid), math.floor(spec.boundary.height_mm / grid)
    minimum = max(1, math.ceil(room.min_area_m2 * 1e6 / grid**2 - 1e-9))
    maximum = min(width * height, math.floor(room.max_area_m2 * 1e6 / grid**2 + 1e-9))
    target = room.target_area_m2 * 1e6 / grid**2

    def step():
        if counter["steps"] >= budget.max_catalog_steps_per_room or time.monotonic() >= deadline:
            counter["truncated"] = True
            return False
        counter["steps"] += 1
        return True

    def clipped(shape):
        geometry = _polygon(shape).geometry.intersection(allowed)
        try:
            exact = OrthogonalPolygon.from_geometry(geometry)
        except ValueError as exc:
            counter["unrepresentable_clips"] = counter.get("unrepresentable_clips", 0) + 1
            if on_unsupported is not None:
                on_unsupported(shape, str(exc))
            return None
        if isinstance(shape, Rect) and geometry.equals(_polygon(shape).geometry):
            return shape
        return exact

    if width <= 0 or height <= 0 or maximum < minimum:
        return
    if step():
        full = clipped(Rect(0, 0, width * grid, height * grid))
        if full is not None:
            yield full
    else:
        return
    # Full floor-window L patterns first, so important concave alternatives
    # are not crowded out by hundreds of translated rectangle candidates.
    for notch_width in range(1, width):
        if not step():
            return
        low = max(1, math.ceil((width * height - maximum) / notch_width))
        high = min(height - 1, math.floor((width * height - minimum) / notch_width))
        for notch_height in _prioritized_integers(low, high, (width * height - target) / notch_width):
            for flip_x, flip_y in ((False, False), (True, False), (False, True), (True, True)):
                if not step():
                    return
                points = ((0, 0), (width * grid, 0), (width * grid, (height - notch_height) * grid),
                          ((width - notch_width) * grid, (height - notch_height) * grid),
                          ((width - notch_width) * grid, height * grid), (0, height * grid))
                shape = OrthogonalPolygon(tuple((width * grid - x if flip_x else x, height * grid - y if flip_y else y) for x, y in points))
                shape = clipped(shape)
                if shape is not None:
                    yield shape
    dimensions = []
    for room_width in range(1, width + 1):
        if not step():
            return
        low, high = max(1, math.ceil(minimum / room_width)), min(height, math.floor(maximum / room_width))
        for room_depth in _prioritized_integers(low, high, target / room_width):
            dimensions.append((room_width, room_depth))
            for x, y in _corners(width - room_width, height - room_depth):
                if not step():
                    return
                shape = clipped(Rect(x * grid, y * grid, room_width * grid, room_depth * grid))
                if shape is not None:
                    yield shape
    # Cover distinct sizes at boundary corners before enumerating translated
    # interiors of any one size. The retained-candidate bound must not erase
    # all other size choices merely because the first size has many positions.
    for room_width, room_depth in dimensions:
        corners = _corners(width - room_width, height - room_depth)
        for y in range(height - room_depth + 1):
            for x in range(width - room_width + 1):
                if (x, y) in corners:
                    continue
                if not step():
                    return
                shape = clipped(Rect(x * grid, y * grid, room_width * grid, room_depth * grid))
                if shape is not None:
                    yield shape


def _corners(right, top):
    return tuple(dict.fromkeys(((0, 0), (right, 0), (0, top), (right, top))))


def _prioritized_integers(low, high, target):
    if low > high:
        return
    first = tuple(dict.fromkeys((max(low, min(high, round(target))), low, high)))
    yield from first
    for value in range(low, high + 1):
        if value not in first:
            yield value


def _shape_data(shape):
    _polygon(shape)  # Validate finite canonical geometry before provenance hashing.
    return shape.to_dict() if isinstance(shape, OrthogonalPolygon) else {"kind": "rect", "units": "mm", "x": shape.x, "y": shape.y, "width": shape.width, "height": shape.height}


def _shape_key(shape):
    return (_polygon(shape).vertices, isinstance(shape, OrthogonalPolygon))


def _polygon(shape):
    if isinstance(shape, Rect):
        return OrthogonalPolygon.from_rect(shape)
    if not isinstance(shape, OrthogonalPolygon):
        raise ValueError("Catalog/fixed geometry requires canonical Rect or OrthogonalPolygon")
    return shape


def _hash_json(payload):
    try:
        data = json.dumps(payload, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    except (ValueError, TypeError, OverflowError) as exc:
        raise ValueError("Orthogonal solver input requires finite canonical JSON") from exc
    return hashlib.sha256(data).hexdigest()


def _integer(value, name, minimum=1, maximum=1_000_000):
    if not isinstance(value, int) or isinstance(value, bool) or not minimum <= value <= maximum:
        raise ValueError(f"{name} requires integer {minimum}..{maximum}")


def _positive(value, name):
    if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value) or value <= 0:
        raise ValueError(f"{name} requires a positive finite number")

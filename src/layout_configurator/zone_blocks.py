"""Explicit root-zone block unions, with canonical membership and CP geometry.

A root zone may have several spatially separated connected blocks. The caller
names and partitions those blocks explicitly; required-relation graphs are not
used to invent partitions. Local room/cluster CP models solve each rectangular
block, and a global CP model places the blocks with all canonical hard relations.
Zone geometry is their physical union, which may be disconnected. It is never a
room bounding box. Descendant memberships remain canonical, but this first slice
does not solve separate descendant containers or nested block unions.

The bounded local shapes constrain the search. A failed selected-block model
does not prove the original BuildingIR infeasible. Facility acceptance remains a
separate gate, and this API is not selected by the auto dispatcher.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from numbers import Real

from .building import BuildingIR
from .models import LayoutResult, Rect
from .solver import _ceil_grid
from .validation import ValidationIssue, ValidationReport
from .zone_hierarchy import (
    _fail, _group_issues, _local_shapes, _normalise_room_constraints,
    _root_units, _size_options, _solve_shape_layouts, _validate_container_layout,
)


def _block_units(building, partition, evidence):
    roots = _root_units(building, evidence)
    zones = {zone.id: zone for zone in building.zones}
    if not isinstance(partition, Mapping):
        _fail(evidence, "zone_blocks must map root zone IDs to named blocks", stage="input",
              code="invalid_block_partition", model_status="NOT_BUILT", not_applicable=True)
    unknown = set(partition) - set(zones)
    if unknown:
        _fail(evidence, "Block partitions reference unknown zones", stage="input", code="unknown_block_zone",
              unit_ids=sorted(unknown, key=str), model_status="NOT_BUILT", not_applicable=True)
    nested = [zone_id for zone_id in partition if zones[zone_id].parent_zone_id is not None]
    if nested:
        _fail(evidence, "Explicit block partitions currently support root zones only", stage="input",
              code="nested_block_zone", unit_ids=nested, model_status="NOT_BUILT",
              proof_scope="unsupported_input", not_applicable=True)
    units, zone_units, descriptions = {}, {}, []
    reserved = set(zones) | set(roots)
    for unit_id, canonical in roots.items():
        if unit_id not in partition:
            units[unit_id] = canonical
            descriptions.append({"id": unit_id, "kind": "root_zone" if unit_id in zones else "unassigned_room",
                                 "room_ids": list(canonical)})
            if unit_id in zones:
                zone_units[unit_id] = (unit_id,)
            continue
        blocks = partition[unit_id]
        if not isinstance(blocks, Mapping) or not blocks:
            _fail(evidence, f"Zone {unit_id} needs at least one named block", stage="input",
                  code="invalid_block_partition", unit_ids=(unit_id,), model_status="NOT_BUILT", not_applicable=True)
        assigned, named = set(), []
        for block_id, members in blocks.items():
            if not isinstance(block_id, str) or not block_id.strip() or isinstance(members, (str, bytes)):
                _fail(evidence, f"Invalid block of zone {unit_id}", stage="input", code="invalid_block_partition",
                      unit_ids=(unit_id,), model_status="NOT_BUILT", not_applicable=True)
            if block_id in reserved or block_id in units:
                _fail(evidence, f"Block ID {block_id} is not globally unique", stage="input", code="duplicate_block_id",
                      unit_ids=(unit_id, block_id), model_status="NOT_BUILT", not_applicable=True)
            try:
                members = tuple(members)
                members_set = set(members)
            except TypeError as exc:
                _fail(evidence, f"Invalid members of block {block_id}: {exc}", stage="input", code="invalid_block_partition",
                      unit_ids=(block_id,), model_status="NOT_BUILT", not_applicable=True)
            if not members or len(members_set) != len(members):
                _fail(evidence, f"Block {block_id} needs nonempty unique room IDs", stage="input", code="invalid_block_partition",
                      unit_ids=(block_id,), model_status="NOT_BUILT", not_applicable=True)
            foreign = members_set - set(canonical)
            if foreign:
                _fail(evidence, f"Block {block_id} has rooms outside canonical zone {unit_id}", stage="input",
                      code="foreign_block_room", unit_ids=(unit_id, block_id), room_ids=sorted(foreign, key=str),
                      model_status="NOT_BUILT", not_applicable=True)
            repeated = assigned & members_set
            if repeated:
                _fail(evidence, f"Zone {unit_id} assigns rooms to several blocks", stage="input",
                      code="duplicate_block_membership", unit_ids=(unit_id,), room_ids=sorted(repeated),
                      model_status="NOT_BUILT", not_applicable=True)
            # Preserve canonical room order, even when the caller supplies sets.
            units[block_id] = tuple(room_id for room_id in canonical if room_id in members_set)
            named.append(block_id)
            assigned.update(members_set)
            descriptions.append({"id": block_id, "kind": "explicit_zone_block", "zone_id": unit_id,
                                 "room_ids": list(units[block_id])})
        missing = set(canonical) - assigned
        if missing:
            _fail(evidence, f"Blocks do not cover canonical zone {unit_id}", stage="input",
                  code="incomplete_block_partition", unit_ids=(unit_id,), room_ids=sorted(missing),
                  model_status="NOT_BUILT", not_applicable=True)
        zone_units[unit_id] = tuple(named)
    # Empty root zones have no fabricated block or physical union.
    empty_supplied = set(partition) - set(roots)
    if empty_supplied:
        _fail(evidence, "Cannot partition an empty root zone into physical blocks", stage="input",
              code="invalid_block_partition", unit_ids=sorted(empty_supplied), model_status="NOT_BUILT", not_applicable=True)
    evidence["units"] = descriptions
    evidence["zone_blocks"] = {zone_id: list(block_ids) for zone_id, block_ids in zone_units.items()}
    return units, zone_units


def _block_connectivity(placements, members, minimum):
    present = [room_id for room_id in members if room_id in placements]
    if len(present) != len(members):
        return True  # Missing rooms are reported by the general layout validator.
    seen, stack = set(), list(present[:1])
    while stack:
        room_id = stack.pop()
        if room_id in seen:
            continue
        seen.add(room_id)
        stack.extend(other for other in present if other not in seen and other != room_id
                     and placements[room_id].shared_boundary(placements[other]) + 1e-6 >= minimum)
    return len(seen) == len(present)


def validate_block_zone_layout(
    building: BuildingIR, result: LayoutResult, block_rects: Mapping[str, Rect], *,
    zone_blocks: Mapping[str, Mapping[str, Iterable[str]]],
    zone_geometry: Mapping[str, Iterable[Rect]] | None = None,
    minimum_adjacency_mm: float | None = None,
    fixed_rects: Mapping[str, Rect] | None = None,
    axis_aligned_room_ids: Iterable[str] | None = None,
    structural_axes_x_mm: Iterable[float] | None = None,
    structural_axes_y_mm: Iterable[float] | None = None,
) -> ValidationReport:
    """Check actual blocks and exact canonical zone unions independently."""
    from shapely.geometry import box
    from shapely.ops import unary_union

    units, zone_units = _block_units(building, zone_blocks, {})
    spec = building.layout
    report = _validate_container_layout(spec, result, block_rects, units, {}, check_unrelated_rooms=True)
    if any(issue.code in {"unsupported_geometry", "invalid_rect"} for issue in report.issues):
        return report
    issues = list(report.issues)
    minimum = spec.door_width_mm if minimum_adjacency_mm is None else minimum_adjacency_mm
    if set(result.placements) == set(spec.room_by_id):
        issues.extend(_group_issues(result.placements, building.zone_relation_groups("required_adjacency"),
                                    building.zone_relation_groups("forbidden_adjacency"), minimum))
    for block_id, members in units.items():
        if not _block_connectivity(result.placements, members, minimum):
            issues.append(ValidationIssue("disconnected_zone_block", f"Block {block_id} has disconnected rooms"))

    def shape(rect):
        return box(rect.x, rect.y, rect.right, rect.top)

    if zone_geometry is not None:
        for unknown in set(zone_geometry) - set(zone_units):
            issues.append(ValidationIssue("unknown_zone_union", f"Unexpected physical zone union {unknown}"))
    unions = {}
    for zone_id, block_ids in zone_units.items():
        if any(block_id not in block_rects for block_id in block_ids):
            continue
        expected = unary_union([shape(block_rects[block_id]) for block_id in block_ids])
        actual = expected
        if zone_geometry is not None:
            if zone_id not in zone_geometry:
                issues.append(ValidationIssue("missing_zone_union", f"Missing physical union of zone {zone_id}"))
                continue
            try:
                pieces = tuple(zone_geometry[zone_id])
                if any(not isinstance(rect, Rect) or rect.width <= 0 or rect.height <= 0 or
                       not all(isinstance(value, Real) and not isinstance(value, bool) and math.isfinite(value)
                               for value in (rect.x, rect.y, rect.width, rect.height)) for rect in pieces):
                    raise ValueError("union pieces must be finite positive Rect values")
                actual = unary_union([shape(rect) for rect in pieces])
            except (ValueError, TypeError, OverflowError) as exc:
                issues.append(ValidationIssue("invalid_zone_union", f"Invalid physical union of zone {zone_id}: {exc}"))
                continue
            if actual.symmetric_difference(expected).area > 1e-6:
                issues.append(ValidationIssue("zone_union_mismatch", f"Zone {zone_id} union differs from its solved blocks"))
        unions[zone_id] = actual
        members = set(building.room_ids_for_zone(zone_id))
        for room_id, room in result.placements.items():
            if room_id in members and not actual.covers(shape(room)):
                issues.append(ValidationIssue("zone_union_containment", f"Room {room_id} is outside canonical zone {zone_id} union"))
            if room_id not in members and actual.intersection(shape(room)).area > 1e-6:
                issues.append(ValidationIssue("zone_union_unrelated_room", f"Zone {zone_id} union includes unrelated room {room_id}"))
    for index, (zone_id, geometry) in enumerate(unions.items()):
        for other_id, other in tuple(unions.items())[index + 1:]:
            if geometry.intersection(other).area > 1e-6:
                issues.append(ValidationIssue("zone_union_overlap", f"Unrelated root zone unions {zone_id} and {other_id} overlap"))
    fixed, selected, axes = _normalise_room_constraints(
        spec, fixed_rects, axis_aligned_room_ids, structural_axes_x_mm, structural_axes_y_mm, {})
    for room_id, expected in fixed.items():
        actual = result.placements.get(room_id)
        if actual is not None and any(abs(getattr(actual, name) - getattr(expected, name)) > 1e-6
                                      for name in ("x", "y", "width", "height")):
            issues.append(ValidationIssue("fixed_room_mismatch", f"Room {room_id} differs from its fixed rectangle"))
    for room_id in selected:
        room = result.placements.get(room_id)
        if room is not None:
            for name, values in axes.items():
                endpoints = (room.x, room.right) if name == "x" else (room.y, room.top)
                if any(not any(abs(endpoint - value) <= 1e-6 for value in values) for endpoint in endpoints):
                    issues.append(ValidationIssue("structural_axis_mismatch", f"Room {room_id} endpoints miss {name} axes"))
    return ValidationReport(tuple(issues))


def solve_block_zone_layouts(
    building: BuildingIR, variants: int = 1, time_limit_seconds: float = 30, seed: int = 42, *,
    zone_blocks: Mapping[str, Mapping[str, Iterable[str]]],
    zone_size_options_mm: Mapping[str, Iterable[tuple[float, float]]] | None = None,
    max_local_options: int = 4, max_validation_attempts: int | None = None,
    minimum_adjacency_mm: float | None = None, deterministic_units: float | None = None,
    evidence: dict | None = None, allow_partial: bool = False,
    fixed_rects: Mapping[str, Rect] | None = None,
    axis_aligned_room_ids: Iterable[str] | None = None,
    structural_axes_x_mm: Iterable[float] | None = None,
    structural_axes_y_mm: Iterable[float] | None = None,
) -> list[LayoutResult]:
    """Solve an explicit partition of each supplied root zone into named blocks.

    Block IDs are globally unique and must differ from all canonical zone IDs.
    Every supplied partition covers that root's canonical descendant membership
    exactly once. Unsupplied roots retain one physical block; unassigned rooms
    remain explicit singleton units. Size options are keyed by physical block
    IDs (or an unsplit root ID), and budgets apply separately to each CP solve.
    """
    evidence = evidence if evidence is not None else {}
    evidence.update(room_solver_used="zone_blocks", scope="root_zone_block_unions",
                    descendant_container_geometry=False, stage_attempts=[], variants=[])
    evidence.pop("failure", None)
    evidence.pop("candidate_pool", None)
    counts = (variants, max_local_options) + (() if max_validation_attempts is None else (max_validation_attempts,))
    numeric_budgets = (time_limit_seconds,) + (() if deterministic_units is None else (deterministic_units,))
    if any(not isinstance(value, int) or isinstance(value, bool) or value < 1 for value in counts) or any(
        not isinstance(value, Real) or isinstance(value, bool) or not math.isfinite(value) or value <= 0 for value in numeric_budgets
    ):
        _fail(evidence, "Search counts and budgets must be finite positive values of the correct type", stage="input",
              code="invalid_budget", model_status="NOT_BUILT", not_applicable=True)
    attempts = max(3, variants * 3) if max_validation_attempts is None else max_validation_attempts
    if attempts < variants or not isinstance(allow_partial, bool):
        _fail(evidence, "Validation attempts must cover variants and allow_partial must be boolean", stage="input",
              code="invalid_budget", model_status="NOT_BUILT", not_applicable=True)
    units, zone_units = _block_units(building, zone_blocks, evidence)
    # Replay partitions safely when room membership arrived as one-shot iterables.
    partition = {zone_id: {block_id: units[block_id] for block_id in block_ids}
                 for zone_id, block_ids in zone_units.items() if zone_id in zone_blocks}
    spec = building.layout
    if any(not math.isfinite(value) or value <= 0 for value in (spec.boundary.width_mm, spec.boundary.height_mm)):
        _fail(evidence, "Boundary dimensions must be finite and positive", stage="input",
              code="invalid_boundary", model_status="NOT_BUILT", not_applicable=True)
    if max(_ceil_grid(spec.boundary.width_mm, spec.grid_mm), _ceil_grid(spec.boundary.height_mm, spec.grid_mm)) >= 1 << 60:
        _fail(evidence, "Boundary coordinates exceed safe integer bounds", stage="input",
              code="integer_bounds", model_status="NOT_BUILT", not_applicable=True)
    fixed, selected, axes = _normalise_room_constraints(
        spec, fixed_rects, axis_aligned_room_ids, structural_axes_x_mm, structural_axes_y_mm, evidence)
    minimum = spec.door_width_mm if minimum_adjacency_mm is None else minimum_adjacency_mm
    if not isinstance(minimum, Real) or isinstance(minimum, bool) or not math.isfinite(minimum) or minimum <= 0:
        _fail(evidence, "minimum_adjacency_mm must be finite and positive", stage="input",
              code="invalid_adjacency_width", model_status="NOT_BUILT", not_applicable=True)
    options = _size_options(building, units, zone_size_options_mm, max_local_options, evidence, fixed_rects=fixed)
    groups = {relation: building.zone_relation_groups(f"{relation}_adjacency")
              for relation in ("required", "forbidden", "preferred")}
    shapes = _local_shapes(building, units, options, groups, minimum, time_limit_seconds,
                           seed, deterministic_units, evidence, fixed)

    def validate_global(source, result, rectangles, *, recursive_zones=False, **constraints):
        return validate_block_zone_layout(source, result, rectangles, zone_blocks=partition, **constraints)

    try:
        return _solve_shape_layouts(
            building, units, shapes, groups, minimum, variants, time_limit_seconds, seed,
            attempts, deterministic_units, evidence, container_members=units, ancestors={},
            allow_partial=allow_partial, fixed_rects=fixed, axis_aligned_room_ids=selected,
            structural_axes=axes, global_validator=validate_global,
        )
    finally:
        # A direct exact-count failure may still have accepted stage candidates.
        # Preserve their actual block geometry with the same vocabulary as a
        # successful result, without pretending the requested count was met.
        for variant in evidence["variants"]:
            rectangles = variant.pop("zone_rects")
            variant["block_rects"] = rectangles
            variant["zone_geometry"] = {zone_id: [rectangles[block_id] for block_id in block_ids]
                                        for zone_id, block_ids in zone_units.items()}

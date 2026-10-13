"""Bounded metadata about a selected final zone model; never a second IR.

Captured candidates describe the effective integer geometry supplied to CP-SAT.
They are alternatives, not accepted layouts or a proof about the source program.
"""

from __future__ import annotations

from collections import Counter
import hashlib
import json
import math


MAX_SOURCE_ROOMS = 80
MAX_CANDIDATE_SHAPES = 4096
MAX_RECTANGLE_RECORDS = 65536
MAX_FAMILY_BYTES = 8 * 1024 * 1024


def _canonical_hash(value, *, byte_limit=None):
    encoder = json.JSONEncoder(sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    digest, size = hashlib.sha256(), 0
    for chunk in encoder.iterencode(value):
        raw = chunk.encode("utf-8")
        size += len(raw)
        if byte_limit is not None and size > byte_limit:
            return None, size
        digest.update(raw)
    return digest.hexdigest(), size


def capture_candidate_family(building, units, shapes, *, container_members, ancestors,
                             recursive_zones, minimum_grid, fixed_rects, axis_room_ids,
                             structural_axes):
    """Capture all effective candidates or explicit incomplete metadata only."""
    limits = {"source_rooms": MAX_SOURCE_ROOMS, "candidate_shapes": MAX_CANDIDATE_SHAPES,
              "rectangle_records": MAX_RECTANGLE_RECORDS, "family_bytes": MAX_FAMILY_BYTES}
    counts = {"source_rooms": len(building.layout.rooms), "units": len(shapes),
              "candidate_shapes": sum(len(options) for options in shapes.values()),
              "rectangle_records": sum(len(shape.placements) + len(shape.containers)
                                        for options in shapes.values() for shape in options)}
    diagnostic = {
        "schema_version": 1, "scope": "final_global_cp_selected_zone_shapes",
        "capture_status": "INCOMPLETE", "limits": limits, "counts": counts,
        "original_program_infeasibility_proven": False, "searches": [],
    }
    for name in ("source_rooms", "candidate_shapes", "rectangle_records"):
        if counts[name] > limits[name]:
            diagnostic["incomplete_reason"] = name + "_limit"
            return diagnostic
    try:
        source_hash, _ = _canonical_hash(building.to_dict())
    except (ValueError, OverflowError, TypeError):
        diagnostic["incomplete_reason"] = "source_not_canonical_finite_json"
        return diagnostic
    diagnostic["source_sha256"] = source_hash
    grid = building.layout.grid_mm

    def rectangle(rect):
        return [round(getattr(rect, name) / grid) for name in ("x", "y", "width", "height")]

    family = {
        "grid_mm": grid,
        "units": [{"unit_id": unit_id, "room_ids": list(units[unit_id]),
                   "shape_count": len(options), "shapes": [
                       {"index": index, "width": round(shape.width / grid), "height": round(shape.height / grid),
                        "rooms": {room_id: rectangle(rect) for room_id, rect in shape.placements.items()},
                        "containers": {container_id: rectangle(rect) for container_id, rect in shape.containers.items()}}
                       for index, shape in enumerate(options)]}
                  for unit_id, options in shapes.items()],
        "container_members": {unit_id: list(members) for unit_id, members in container_members.items()},
        "ancestors": {unit_id: sorted(parents) for unit_id, parents in ancestors.items()},
        "effective_constraints": {
            "recursive_zones": recursive_zones, "minimum_adjacency_grid": minimum_grid,
            "fixed_rooms": {room_id: rectangle(rect) for room_id, rect in (fixed_rects or {}).items()},
            "axis_aligned_room_ids": list(axis_room_ids),
            "structural_axes_grid": {name: [round(value / grid) for value in values]
                                     for name, values in (structural_axes or {}).items()},
        },
    }
    # Counts bound record allocation. Byte checks include identifiers and hashes;
    # an oversized family is never published as a misleading partial snapshot.
    family_hash, size = _canonical_hash(family, byte_limit=MAX_FAMILY_BYTES)
    if family_hash is not None:
        for unit in family["units"]:
            for shape in unit["shapes"]:
                payload = {key: value for key, value in shape.items() if key != "index"}
                shape["sha256"] = _canonical_hash(payload)[0]
        family_hash, size = _canonical_hash(family, byte_limit=MAX_FAMILY_BYTES)
    if family_hash is None:
        diagnostic["incomplete_reason"] = "family_bytes_limit"
        diagnostic["counts"]["family_bytes_exceeded"] = size
        return diagnostic
    diagnostic.update(capture_status="COMPLETE", candidate_family=family,
                      candidate_family_sha256=family_hash)
    diagnostic["counts"]["family_bytes"] = size
    return diagnostic


def model_counts(model):
    """Count the actual proto, before this attempt adds further no-goods."""
    proto = model.Proto()
    kinds = Counter()
    for constraint in proto.constraints:
        if hasattr(constraint, "WhichOneof"):
            kind = constraint.WhichOneof("constraint") or "unset"
        else:
            kind = next((name[4:] for name in dir(constraint)
                         if name.startswith("has_") and callable(getattr(constraint, name))
                         and getattr(constraint, name)()), "unset")
        kinds[kind] += 1
    return {"variables": len(proto.variables), "constraints": len(proto.constraints),
            "constraint_types": dict(sorted(kinds.items()))}


def record_search(diagnostic, *, attempt, solver, status, counts):
    """Record ResponseProto values with strict finite JSON numeric types."""
    response = solver.ResponseProto()
    unavailable, values = [], {}
    fields = ("wall_time", "user_time", "deterministic_time", "num_branches", "num_conflicts",
              "num_binary_propagations", "num_integer_propagations", "num_booleans", "num_integers",
              "objective_value", "best_objective_bound", "gap_integral")
    for name in fields:
        value = getattr(response, name, None)
        if type(value) in (int, float) and (type(value) is int or math.isfinite(value)):
            values[name] = value
        else:
            values[name] = None
            unavailable.append(name)
    values.update(solution_present=bool(len(response.solution)), unavailable_fields=unavailable)
    parameters = solver.parameters
    diagnostic["searches"].append({
        "attempt": attempt, "model_status": solver.StatusName(status), "model": counts, "response": values,
        "settings": {"seed": parameters.random_seed, "workers": parameters.num_search_workers,
                     "time_limit_seconds": parameters.max_time_in_seconds,
                     "deterministic_units": parameters.max_deterministic_time if math.isfinite(parameters.max_deterministic_time) else None,
                     "repair_hint": parameters.repair_hint},
        "original_program_infeasibility_proven": False,
    })

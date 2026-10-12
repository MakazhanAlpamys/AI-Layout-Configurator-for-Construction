"""Invocation-local reuse of independently checked CP-SAT cluster shapes.

Roles preserve the source room order. This deliberately recognises ordered
isomorphisms only: a different order is a miss, rather than a change to the
local solver's variable order or budget semantics. IDs and equipment labels
are interchangeable; every effective constraint and search setting is not.
The hierarchy owns the cache lifetime. No geometry is persisted between calls.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping
from dataclasses import asdict, dataclass, replace

from .models import BoundarySpec, LayoutIR, LayoutResult, Rect
from .validation import validate_layout


_POLICY = {
    "version": 1,
    "solver": "rectangular_cp_sat",
    "local_workers": 1,
    "variants": 1,
    "first_solution_only": False,
    "room_order": "source_order",
    "entry": "first_local_room",
    "daylight": False,
    "external_entry": None,
    "doors": (),
    "windows": (),
    "cutouts": (),
    "normalization": "translate_to_minimum_x_y",
}


@dataclass(frozen=True)
class ClusterShapeRequest:
    """Cache metadata referring to the canonical input, not a separate IR."""

    key: str
    spec: LayoutIR
    room_ids: tuple[str, ...]
    side_contacts: tuple[tuple[str, int], ...]
    fit_options: tuple[tuple[str, tuple], ...]
    adjacency_mm: float


def cluster_shape_request(
    spec: LayoutIR,
    members: tuple[str, ...],
    *,
    side_contacts: Mapping[str, int],
    fit_options: Mapping,
    adjacency_mm: float,
    time_limit_seconds: float,
    seed: int,
    deterministic_units: float | None,
    aspects: tuple[float, ...],
    fills: tuple[float, ...],
    max_shapes: int,
) -> ClusterShapeRequest:
    """Fingerprint all inputs used by the hierarchy's local motif search."""

    member_set = set(members)
    rooms = tuple(room for room in spec.rooms if room.id in member_set)
    room_ids = tuple(room.id for room in rooms)
    if len(room_ids) != len(member_set) or not rooms:
        raise ValueError("Shape cache members must be known, non-empty rooms")
    roles = {room_id: index for index, room_id in enumerate(room_ids)}
    canonical_rooms = []
    for room in rooms:
        attributes = asdict(room)
        attributes["id"] = roles[room.id]
        for relation in ("required_adjacency", "preferred_adjacency", "forbidden_adjacency"):
            attributes[relation] = tuple(sorted(roles[other] for other in getattr(room, relation) if other in roles))
        canonical_rooms.append(attributes)
    # These are exactly the scalar settings inherited by replace(spec) in the
    # local solver. Boundary dimensions also bound which motifs may be tried.
    settings = asdict(spec)
    for name in ("project_name", "rooms", "entry_room", "external_entry", "doors", "windows", "boundary"):
        settings.pop(name)
    fit = tuple(
        (room_id, tuple((str(label), tuple((float(w), float(h)) for w, h in options))
                        for label, options in fit_options.get(room_id, ())))
        for room_id in room_ids
    )
    contacts = tuple((room_id, side_contacts.get(room_id, 0)) for room_id in room_ids)
    canonical = {
        "policy": _POLICY,
        "settings": settings,
        "boundary_size_limit": (spec.boundary.width_mm, spec.boundary.height_mm),
        "rooms": canonical_rooms,
        # Pair canonicalisation matches LayoutIR.relation_pairs, including
        # relations declared on either or both endpoints.
        "relations": {
            relation: sorted(tuple(sorted((roles[a], roles[b])))
                             for a, b in spec.relation_pairs(relation) if a in roles and b in roles)
            for relation in ("required_adjacency", "preferred_adjacency", "forbidden_adjacency")
        },
        "equipment_fit_groups": [[options for _, options in requirements] for _, requirements in fit],
        "boundary_side_contacts": [count for _, count in contacts],
        "minimum_adjacency_mm": float(adjacency_mm),
        "time_limit_seconds": float(time_limit_seconds),
        "deterministic_units": None if deterministic_units is None else float(deterministic_units),
        "seed": int(seed),
        "aspects": aspects,
        "fills": fills,
        "max_shapes": max_shapes,
    }
    return ClusterShapeRequest(_payload_digest(canonical), spec, room_ids, contacts, fit, float(adjacency_mm))


def _payload_digest(value) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True)
class _Entry:
    payload: tuple
    checksum: str
    source_room_ids: tuple[str, ...]


class ClusterShapeCache:
    """Immutable stored coordinates, fresh mappings on every validated hit."""

    def __init__(self) -> None:
        self._entries: dict[str, _Entry] = {}
        self.evidence = {
            "scope": "single_hierarchical_invocation",
            "key_policy": "ordered_local_problem_v1",
            "hits": 0,
            "misses": 0,
            "rejected_hits": 0,
            "stored_entries": 0,
            "events": [],
            "global_search": [],
        }

    def record_global_search(self, variant: int, model_status: str, *, assumptions_dropped: bool = False) -> None:
        self.evidence["global_search"].append({
            "variant": variant, "model_status": model_status,
            "assumptions_dropped": assumptions_dropped,
            "proof_scope": "selected_cluster_shapes",
            "original_program_infeasibility_proven": False,
        })

    def get(self, request: ClusterShapeRequest) -> list[dict[str, Rect]] | None:
        entry = self._entries.get(request.key)
        event = {"key": request.key, "room_ids": list(request.room_ids)}
        if entry is None:
            self.evidence["misses"] += 1
            self.evidence["events"].append({**event, "outcome": "miss"})
            return None
        reason = None
        try:
            if _payload_digest(entry.payload) != entry.checksum:
                reason = "payload_checksum"
            else:
                if not isinstance(entry.payload, tuple) or not entry.payload:
                    raise ValueError("Empty or mutable cached payload")
                shapes = []
                for payload in entry.payload:
                    if not isinstance(payload, tuple) or len(payload) != len(request.room_ids):
                        raise ValueError("Cached room count differs")
                    if any(not isinstance(coordinates, tuple) or len(coordinates) != 4 for coordinates in payload):
                        raise ValueError("Cached coordinates must be immutable rectangle tuples")
                    shapes.append({room_id: Rect(*coordinates) for room_id, coordinates in zip(request.room_ids, payload)})
                if any(not _valid_shape(request, shape) for shape in shapes):
                    reason = "invalid_geometry"
        except (TypeError, ValueError, OverflowError, AttributeError):
            reason = "invalid_geometry"
        if reason:
            self._entries.pop(request.key, None)
            self.evidence["stored_entries"] = len(self._entries)
            self.evidence["rejected_hits"] += 1
            self.evidence["misses"] += 1
            self.evidence["events"].append({**event, "outcome": "rejected", "reason": reason})
            return None
        self.evidence["hits"] += 1
        self.evidence["events"].append({
            **event, "outcome": "hit", "source_room_ids": list(entry.source_room_ids),
            "shapes": len(shapes), "independently_validated": True,
        })
        return shapes

    def store(self, request: ClusterShapeRequest, shapes: list[dict[str, Rect]]) -> None:
        # A bounded unsuccessful search is not an infeasibility proof and is
        # never stored as a negative result.
        if not shapes:
            return
        if any(not _valid_shape(request, shape) for shape in shapes):
            raise ValueError("Cannot cache an invalid local cluster shape")
        payload = tuple(tuple((shape[room_id].x, shape[room_id].y, shape[room_id].width, shape[room_id].height)
                              for room_id in request.room_ids) for shape in shapes)
        self._entries[request.key] = _Entry(payload, _payload_digest(payload), request.room_ids)
        self.evidence["stored_entries"] = len(self._entries)


def _valid_shape(request: ClusterShapeRequest, shape: Mapping[str, Rect]) -> bool:
    """Check actual geometry without consulting a cache hash or CP response."""

    if set(shape) != set(request.room_ids):
        return False
    grid = request.spec.grid_mm
    for rect in shape.values():
        if not isinstance(rect, Rect):
            return False
        values = (rect.x, rect.y, rect.width, rect.height)
        if any(isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value)
               or abs(value / grid - round(value / grid)) > 1e-7 for value in values):
            return False
        if rect.x < 0 or rect.y < 0 or rect.width <= 0 or rect.height <= 0:
            return False
    if min(rect.x for rect in shape.values()) != 0 or min(rect.y for rect in shape.values()) != 0:
        return False
    width = max(rect.right for rect in shape.values())
    height = max(rect.top for rect in shape.values())
    if width > request.spec.boundary.width_mm or height > request.spec.boundary.height_mm:
        return False
    members = set(request.room_ids)
    local = replace(
        request.spec,
        rooms=tuple(replace(
            room, needs_daylight=False,
            required_adjacency=tuple(other for other in room.required_adjacency if other in members),
            preferred_adjacency=tuple(other for other in room.preferred_adjacency if other in members),
            forbidden_adjacency=tuple(other for other in room.forbidden_adjacency if other in members),
        ) for room in request.spec.rooms if room.id in members),
        boundary=BoundarySpec(width, height), entry_room=request.room_ids[0],
        external_entry=None, doors=(), windows=(),
    )
    if not validate_layout(local, LayoutResult(variant=1, placements=shape)).ok:
        return False
    minimum_shared = max(1, math.ceil(request.adjacency_mm / grid)) * grid
    if any(shape[a].shared_boundary(shape[b]) + 1e-6 < minimum_shared
           for a, b in local.relation_pairs("required_adjacency")):
        return False
    for room_id, count in request.side_contacts:
        rect = shape[room_id]
        contacts = sum((rect.x == 0, rect.right == width, rect.y == 0, rect.top == height))
        if contacts < min(4, max(0, int(count))):
            return False
    for room_id, requirements in request.fit_options:
        rect = shape[room_id]
        for _, options in requirements:
            if not any(rect.width + 1e-6 >= w and rect.height + 1e-6 >= h for w, h in options):
                return False
    return True

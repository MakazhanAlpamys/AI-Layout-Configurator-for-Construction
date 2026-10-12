"""Supplied revision records linked to exact prior canonical result bytes.

Dates and authors are supplied attribution, not authenticated signatures or
approval. Previous source geometry is read for its contract and identity only.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
import hashlib
import json
import math
from pathlib import Path
import tempfile
from typing import Any

from .building import BuildingIR
from .facility import FacilityDrawingProfile, FacilityProfile, DEFAULT_FACILITY_PROFILE_SCHEMA_PATH
from .io import load_building_result
from .resources import resource_path
from .schema import DEFAULT_BUILDING_SCHEMA_PATH, DEFAULT_SCHEMA_PATH, validate_mapping


@dataclass(frozen=True)
class SuppliedDrawingRevision:
    revision: str
    previous_revision: str | None
    date: str
    author: str
    description: str
    source_json: str
    source_sha256: str
    source_identity: dict[str, Any]

    @property
    def source_bytes(self) -> bytes:
        return self.source_json.encode("utf-8")

    def to_input(self) -> dict[str, Any]:
        return {key: getattr(self, key) for key in ("revision", "previous_revision", "date", "author",
                                                   "description", "source_json", "source_sha256")}

    def to_index(self) -> dict[str, Any]:
        return {**{key: value for key, value in self.to_input().items() if key != "source_json"},
                "source_identity": dict(self.source_identity),
                "source_file": f"revision-sources/{self.source_sha256}.json"}


@dataclass(frozen=True)
class DrawingRevisionHistory:
    current_revision: str
    previous_revision: str
    records: tuple[SuppliedDrawingRevision, ...]

    def to_input(self) -> dict[str, Any]:
        return {"current_revision": self.current_revision, "previous_revision": self.previous_revision,
                "records": [record.to_input() for record in self.records]}

    def to_index(self) -> dict[str, Any]:
        return {"current_revision": self.current_revision, "previous_revision": self.previous_revision,
                "records": [record.to_index() for record in self.records]}


def parse_revision_history(raw: Any, *, building: BuildingIR, drawing: FacilityDrawingProfile) -> DrawingRevisionHistory:
    """Validate every record before any output artifacts are created."""
    errors = validate_mapping(raw, resource_path("schemas/drawing_revision_history.schema.json"))
    if errors:
        raise ValueError("Invalid supplied revision history: " + errors[0].message)
    if not raw["current_revision"].strip() or raw["current_revision"] != drawing.revision:
        raise ValueError("History current_revision must exactly match the drawing profile revision")
    records, revisions, previous_date = [], set(), None
    predecessor = None
    for row in raw["records"]:
        if any(not row[key].strip() for key in ("revision", "author", "description")):
            raise ValueError("Supplied revision, author and description must be nonempty text")
        revision = row["revision"]
        if revision in revisions or revision == drawing.revision or row["previous_revision"] != predecessor:
            raise ValueError("Supplied revision history must form one unique, contiguous predecessor chain")
        supplied_date = date.fromisoformat(row["date"])
        if previous_date is not None and supplied_date < previous_date:
            raise ValueError("Revision dates must be chronological")
        try:
            source_bytes = row["source_json"].encode("utf-8")
        except UnicodeError as exc:
            raise ValueError("Revision source must contain valid UTF-8 text") from exc
        if hashlib.sha256(source_bytes).hexdigest() != row["source_sha256"]:
            raise ValueError("Revision source_sha256 differs from the exact supplied source bytes")
        identity, source_history, history_present = _read_source(source_bytes)
        if identity["revision"] != revision or identity["project_name"] != building.layout.project_name:
            raise ValueError("Supplied revision/project differs from the actual canonical prior source")
        if history_present:
            expected = {"current_revision": revision, "previous_revision": predecessor,
                        "records": [record.to_index() for record in records]}
            if source_history != expected:
                raise ValueError("Supplied predecessor/attribution chain differs from the history already recorded in the prior source")
        records.append(SuppliedDrawingRevision(**row, source_identity=identity))
        revisions.add(revision)
        predecessor, previous_date = revision, supplied_date
    if raw["previous_revision"] != predecessor:
        raise ValueError("Current previous_revision must reference the last supplied prior revision")
    return DrawingRevisionHistory(raw["current_revision"], raw["previous_revision"], tuple(records))


def _read_source(source_bytes):
    # Reuse the canonical result reader, including duplicate-key detection and
    # versioned exact geometry, rather than introducing another result parser.
    try:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "prior.json"
            path.write_bytes(source_bytes)
            building, result, equipment = load_building_result(path)
        payload = json.loads(source_bytes)
        json.dumps(payload, allow_nan=False)
        for mapping, schema in ((payload["spec"], DEFAULT_BUILDING_SCHEMA_PATH),
                                (payload["spec"]["layout"], DEFAULT_SCHEMA_PATH),
                                (payload["facility_validation"]["profile"], DEFAULT_FACILITY_PROFILE_SCHEMA_PATH)):
            errors = validate_mapping(mapping, schema)
            if errors:
                raise ValueError("Invalid prior canonical contract: " + errors[0].message)
        profile_raw = payload["facility_validation"]["profile"]
        revision = profile_raw["drawing"]["revision"]
        if not isinstance(revision, str) or not revision.strip():
            raise ValueError("Prior drawing revision must be explicitly supplied as text")
        source_profile = FacilityProfile.from_mapping(profile_raw)
        if payload["spec"] != building.to_dict() or payload["layout"] != result.to_dict() or profile_raw != source_profile.to_dict():
            raise ValueError("Prior source must contain canonical program/result/profile values without implicit normalization")
        if set(result.placements) != set(building.layout.room_by_id):
            raise ValueError("Prior room identities do not match the canonical room program")
        raw_equipment = payload["equipment"]
        if not isinstance(raw_equipment, list):
            raise ValueError("Prior canonical equipment must be an array")
        known, seen = {item.id for item in building.equipment}, set()
        for item in raw_equipment:
            if not isinstance(item, dict) or not isinstance(item.get("equipment_id"), str) or item["equipment_id"] not in known or item["equipment_id"] in seen:
                raise ValueError("Unknown or duplicate prior equipment identity")
            if not isinstance(item.get("room_id"), str) or item["room_id"] not in result.placements or type(item.get("rotated", False)) is not bool:
                raise ValueError("Invalid prior equipment host or rotation type")
            for key in ("x", "y", "width", "depth"):
                value = item[key]
                if type(value) not in (int, float) or not math.isfinite(value) or (key in ("width", "depth") and value <= 0):
                    raise ValueError("Prior equipment dimensions require finite numbers and positive extents")
            if "clearance" in item:
                envelope = item["clearance"]
                if not isinstance(envelope, dict) or any(not _number(envelope.get(key), positive=key in ("width", "depth")) for key in ("x", "y", "width", "depth")):
                    raise ValueError("Invalid prior equipment clearance envelope contract")
            seen.add(item["equipment_id"])
        if "flow_routes" in payload:
            _check_flow_contract(payload["flow_routes"], building, result)
        identity = {"project_name": building.layout.project_name, "variant": result.variant,
                "building_version": building.version, "layout_version": building.layout.version,
                "revision": revision}
        return identity, payload.get("drawing_revision_history"), "drawing_revision_history" in payload
    except (OSError, UnicodeError, ValueError, TypeError, KeyError, AttributeError, OverflowError, RecursionError) as exc:
        raise ValueError(f"Invalid prior canonical BuildingResult source: {exc}") from exc


def _number(value, *, positive=False):
    return type(value) in (int, float) and math.isfinite(value) and (not positive or value > 0)


def _check_flow_contract(raw, building, result):
    if not isinstance(raw, dict) or not isinstance(raw.get("routes"), list):
        raise ValueError("Prior flow_routes must contain a routes array")
    known = {flow.id: flow for flow in building.flows}
    for route in raw["routes"]:
        if not isinstance(route, dict) or not isinstance(route.get("flow_id"), str) or route["flow_id"] not in known:
            raise ValueError("Unknown prior route flow identity")
        flow = known[route["flow_id"]]
        if not isinstance(route.get("from_id"), str) or route["from_id"] not in flow.from_ids or not isinstance(route.get("to_id"), str) or route["to_id"] not in flow.to_ids:
            raise ValueError("Prior route endpoints differ from the declared flow")
        rooms = route.get("room_path")
        if not isinstance(rooms, list) or not rooms or any(not isinstance(room, str) or room not in result.placements for room in rooms):
            raise ValueError("Invalid prior route room identities")
        if route.get("from_room_id") != rooms[0] or route.get("to_room_id") != rooms[-1] or not _number(route.get("minimum_clear_width_mm"), positive=True):
            raise ValueError("Invalid prior route host/width contract")
        points = route.get("points")
        if not isinstance(points, list) or not points or any(not isinstance(point, dict) or not _number(point.get("x")) or not _number(point.get("y")) for point in points):
            raise ValueError("Prior route coordinates must be finite numeric points")


def write_revision_sources(root: str | Path, history: DrawingRevisionHistory) -> None:
    """Copy verified source bytes using only their content digest as a name."""
    directory = Path(root) / "revision-sources"
    directory.mkdir(parents=True, exist_ok=True)
    for record in history.records:
        path = directory / f"{record.source_sha256}.json"
        if path.exists() and path.read_bytes() != record.source_bytes:
            raise ValueError("Existing revision source differs from its immutable content digest")
        path.write_bytes(record.source_bytes)


def read_revision_sources(root: str | Path, index: Any, *, building: BuildingIR, drawing: FacilityDrawingProfile) -> DrawingRevisionHistory:
    """Validate an artifact index against the delivered exact source files."""
    try:
        if not isinstance(index, dict) or set(index) != {"current_revision", "previous_revision", "records"} or not isinstance(index["records"], list):
            raise ValueError("Malformed revision source index")
        raw = {"current_revision": index["current_revision"], "previous_revision": index["previous_revision"], "records": []}
        root = Path(root).resolve()
        for row in index["records"]:
            if not isinstance(row, dict) or set(row) != {"revision", "previous_revision", "date", "author", "description", "source_sha256", "source_identity", "source_file"}:
                raise ValueError("Malformed indexed revision record")
            digest = row["source_sha256"]
            expected = f"revision-sources/{digest}.json"
            if row["source_file"] != expected:
                raise ValueError("Revision source path must be its content-digest filename")
            path = (root / expected).resolve()
            if not path.is_relative_to(root):
                raise ValueError("Revision source is outside its bundle")
            source = path.read_bytes().decode("utf-8")
            raw["records"].append({**{key: row[key] for key in ("revision", "previous_revision", "date", "author", "description", "source_sha256")},
                                   "source_json": source})
        history = parse_revision_history(raw, building=building, drawing=drawing)
        if history.to_index() != index:
            raise ValueError("Indexed revision identity differs from the actual canonical source")
        return history
    except (OSError, UnicodeError, ValueError, TypeError, KeyError, AttributeError, RuntimeError) as exc:
        raise ValueError(f"Revision source readback failed: {exc}") from exc

"""Constrained, reviewable program edits; no provider calls or solved geometry."""

from __future__ import annotations

import copy
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

from .building import BuildingIR
from .schema import normalize_building_mapping, normalize_mapping, validate_mapping
from .resources import resource_path

PROPOSED_EDITS_SCHEMA_PATH = resource_path("schemas/proposed_edits.schema.json")


@dataclass(frozen=True)
class ProposedEdit:
    operation: str
    target_id: str
    changes: Mapping[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {"operation": self.operation, "target_id": self.target_id, "changes": copy.deepcopy(dict(self.changes))}


@dataclass(frozen=True)
class ProposedEditsDraft:
    base_sha256: str
    source_text: str
    source_sha256: str
    producer: str
    source_kind: str
    edits: tuple[ProposedEdit, ...]
    state: str = "DRAFT"

    def to_dict(self) -> dict[str, Any]:
        return {
            "format": "FLC-proposed-edits", "version": "0.1", "state": self.state,
            "base_sha256": self.base_sha256,
            "source": {"kind": self.source_kind, "producer": self.producer, "text": self.source_text, "sha256": self.source_sha256},
            "edits": [edit.to_dict() for edit in self.edits],
        }


@dataclass(frozen=True)
class AppliedEdits:
    building: BuildingIR
    evidence: Mapping[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {"program": self.building.to_dict(), "evidence": dict(self.evidence)}


def propose_edits(
    building: BuildingIR,
    mapping: Mapping[str, Any],
    *,
    source_text: str,
    producer: str = "manual",
    source_kind: str = "human_text",
) -> ProposedEditsDraft:
    """Validate structured proposals against a canonical program and provenance.

    Text is recorded as source evidence, never interpreted or executed here.
    Numeric edits require explicit mm or m2 quantities. The operation catalog
    cannot write coordinates, rules, exports, classification or verdicts.
    """

    if not isinstance(mapping, Mapping) or set(mapping) != {"edits"}:
        raise ValueError("Proposal input must contain only the edits field")
    if not isinstance(source_text, str):
        raise ValueError("Proposal source_text must be a string")
    base = _canonical_program(building.to_dict())
    draft = _draft_from_mapping({
        "format": "FLC-proposed-edits", "version": "0.1", "state": "DRAFT",
        "base_sha256": _hash(base.to_dict()),
        "source": {"kind": source_kind, "producer": producer, "text": source_text,
                   "sha256": hashlib.sha256(source_text.encode("utf-8")).hexdigest()},
        "edits": copy.deepcopy(mapping["edits"]),
    })
    _edited_program(base, draft.edits)  # Reject invalid targets/references; do not apply.
    return draft


def apply_proposed_edits(
    building: BuildingIR,
    draft: ProposedEditsDraft,
    *,
    confirmed: bool = False,
    reviewer: str | None = None,
) -> AppliedEdits:
    """Atomically apply a confirmed proposal to its exact canonical base.

    The returned program still requires solver and independent checks. Existing
    geometry and export files are invalidated; confirmation is program review,
    never engineering sign-off or a regulatory compliance verdict.
    """

    if confirmed is not True:
        raise ValueError("Applying proposed edits requires explicit confirmation")
    if not isinstance(reviewer, str) or not reviewer.strip():
        raise ValueError("Applying proposed edits requires a named reviewer")
    checked = _draft_from_mapping(draft.to_dict())
    base = _canonical_program(building.to_dict())
    if _hash(base.to_dict()) != checked.base_sha256:
        raise ValueError("Stale proposal: canonical base hash does not match")
    result = _edited_program(base, checked.edits)
    return AppliedEdits(result, {
        "format": "FLC-program-edit-application", "reviewer": reviewer.strip(),
        "base_sha256": checked.base_sha256, "result_sha256": _hash(result.to_dict()),
        "proposal_sha256": _hash(checked.to_dict()), "source_sha256": checked.source_sha256,
        "producer": checked.producer, "source_kind": checked.source_kind,
        "validation_status": "PENDING_SOLVER_AND_CHECKS", "derived_artifacts_invalidated": True,
        "scope": "Confirmed canonical program edit; not engineering sign-off",
    })


def write_proposed_edits(path: str | Path, draft: ProposedEditsDraft) -> None:
    checked = _draft_from_mapping(draft.to_dict())
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(checked.to_dict(), ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")


def read_proposed_edits(path: str | Path) -> ProposedEditsDraft:
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"), object_pairs_hook=_unique_object)
    except OSError as exc:
        raise ValueError(f"Proposal draft unavailable: {path}") from exc
    return _draft_from_mapping(raw)


def _draft_from_mapping(raw) -> ProposedEditsDraft:
    _json_bytes(raw)  # JSON Schema numbers must also be finite JSON numbers.
    issues = validate_mapping(raw, PROPOSED_EDITS_SCHEMA_PATH)
    if issues:
        raise ValueError("Proposed edits do not match schema: " + "; ".join(f"{issue.path}: {issue.message}" for issue in issues))
    source = raw["source"]
    if hashlib.sha256(source["text"].encode("utf-8")).hexdigest() != source["sha256"]:
        raise ValueError("Proposal source hash does not match source text")
    edits, seen = [], set()
    for edit in raw["edits"]:
        for field in edit["changes"]:
            key = (edit["operation"], edit["target_id"], field)
            if key in seen:
                raise ValueError(f"duplicate field edit: {edit['target_id']}.{field}")
            seen.add(key)
        edits.append(ProposedEdit(edit["operation"], edit["target_id"], copy.deepcopy(edit["changes"])))
    return ProposedEditsDraft(raw["base_sha256"], source["text"], source["sha256"], source["producer"],
                              source["kind"], tuple(edits))


def _edited_program(base: BuildingIR, edits: tuple[ProposedEdit, ...]) -> BuildingIR:
    payload = copy.deepcopy(base.to_dict())
    targets = {
        "set_room": {item["id"]: item for item in payload["layout"]["rooms"]},
        "set_equipment": {item["id"]: item for item in payload["equipment"]},
        "set_flow": {item["id"]: item for item in payload["flows"]},
    }
    for edit in edits:
        target = targets[edit.operation].get(edit.target_id)
        if target is None:
            raise ValueError(f"Unknown {edit.operation} target: {edit.target_id}")
        for field, value in edit.changes.items():
            if edit.operation == "set_room" and field.endswith("_adjacency") and edit.target_id in value:
                raise ValueError(f"Room {edit.target_id} cannot be adjacent to itself")
            target[field] = value["value"] if isinstance(value, dict) else copy.deepcopy(value)
    return _canonical_program(payload)


def _canonical_program(mapping: Mapping[str, Any]) -> BuildingIR:
    # BuildingIR's schema deliberately delegates its layout object; validate
    # LayoutIR separately before normalization can discard unknown fields.
    _json_bytes(mapping)
    normalize_mapping(mapping["layout"])
    return normalize_building_mapping(mapping)


def _hash(value) -> str:
    return hashlib.sha256(_json_bytes(value)).hexdigest()


def _json_bytes(value) -> bytes:
    try:
        return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Proposal contains malformed or nonfinite JSON: {exc}") from exc


def _unique_object(pairs) -> dict[str, Any]:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Proposal JSON contains duplicate field: {key}")
        result[key] = value
    return result

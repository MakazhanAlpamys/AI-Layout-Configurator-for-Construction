"""Specification loading and result serialisation."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .building import BuildingIR
from .equipment import EquipmentLayoutResult, EquipmentPlacement, EquipmentValidationReport
from .facility import FacilityValidationReport
from .flows import FlowRoutingResult, FlowValidationReport
from .models import LayoutIR, LayoutResult, Rect
from .schema import load_mapping
from .result_geometry import parse_layout_result


def load_spec(path: str | Path) -> LayoutIR:
    source = Path(path)
    raw_text = source.read_text(encoding="utf-8")
    if source.suffix.lower() == ".json":
        raw: Any = json.loads(raw_text)
    elif source.suffix.lower() in {".yaml", ".yml"}:
        import yaml

        raw = yaml.safe_load(raw_text)
    else:
        raise ValueError("Specification file must have a .json, .yaml or .yml extension")
    return LayoutIR.from_mapping(raw)


def load_building(path: str | Path) -> BuildingIR:
    """Load an extended dense-building program without normalizing shorthand."""

    return BuildingIR.from_mapping(load_mapping(path))


def write_result(path: str | Path, spec: LayoutIR, result: LayoutResult) -> None:
    payload = {"spec": spec.to_dict(), "layout": result.to_dict()}
    parse_layout_result(payload["layout"])
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def load_result(path: str | Path) -> tuple[LayoutIR, LayoutResult]:
    payload = _read_result_payload(path)
    spec = LayoutIR.from_mapping(payload["spec"])
    layout = payload["layout"]
    return spec, parse_layout_result(layout)


def load_building_result(path: str | Path) -> tuple[BuildingIR, LayoutResult, EquipmentLayoutResult]:
    """Read a generated facility result for an independent re-check."""

    payload = _read_result_payload(path)
    building = BuildingIR.from_mapping(payload["spec"])
    layout = payload["layout"]
    result = parse_layout_result(layout)
    equipment_placements = tuple(
        EquipmentPlacement(
            equipment_id=str(item["equipment_id"]),
            room_id=str(item["room_id"]),
            rect=Rect(
                x=float(item["x"]),
                y=float(item["y"]),
                width=float(item["width"]),
                height=float(item["depth"]),
            ),
            rotated=bool(item.get("rotated", False)),
        )
        for item in payload.get("equipment", ())
    )
    return (
        building,
        result,
        EquipmentLayoutResult(equipment_placements),
    )


def _read_result_payload(path: str | Path) -> dict:
    def unique_keys(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"Duplicate result JSON field: {key}")
            result[key] = value
        return result
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"), object_pairs_hook=unique_keys)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"Malformed or unavailable result JSON: {exc}") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("spec"), dict) or not isinstance(payload.get("layout"), dict):
        raise ValueError("Result JSON requires spec and layout objects")
    return payload


def write_building_result(
    path: str | Path,
    building: BuildingIR,
    result: LayoutResult,
    equipment: EquipmentLayoutResult,
    flow_routes: FlowRoutingResult | None = None,
    flow_report: FlowValidationReport | None = None,
    *,
    equipment_report: EquipmentValidationReport | None = None,
    facility_report: FacilityValidationReport | None = None,
    generation_evidence: dict[str, object] | None = None,
) -> None:
    """Write room and equipment solver outputs without converting to drawing data."""

    payload = {
        "spec": building.to_dict(),
        "layout": result.to_dict(),
        **equipment.to_dict(building),
    }
    parse_layout_result(payload["layout"])
    if flow_routes is not None:
        payload["flow_routes"] = flow_routes.to_dict()
    if flow_report is not None:
        payload["flow_validation"] = flow_report.to_dict()
    if equipment_report is not None:
        payload["equipment_validation"] = equipment_report.to_dict()
    if facility_report is not None:
        payload["facility_validation"] = facility_report.to_dict()
    if generation_evidence is not None:
        payload["generation"] = dict(generation_evidence)
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def write_coordination_issues(
    path: str | Path,
    report: FacilityValidationReport,
    *,
    project_name: str | None = None,
    variant: int | None = None,
    model_references: dict[str, str] | None = None,
) -> None:
    """Write a portable issue sidecar without pretending it is a BCF ZIP."""

    payload = {
        "format": "FLC-BCF-like-json",
        "version": "0.1",
        "profile": report.profile.to_dict(),
        "open_count": sum(issue.status == "OPEN" for issue in report.issues),
        "resolved_count": sum(issue.status == "RESOLVED" for issue in report.issues),
        "issues": [issue.to_dict() for issue in report.issues],
    }
    if project_name is not None:
        payload["project_name"] = project_name
    if variant is not None:
        payload["variant"] = variant
    if model_references:
        payload["model_references"] = dict(model_references)
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

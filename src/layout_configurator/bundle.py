"""Write one facility variant as a complete, self-checked bundle entry.

``generate-building`` and the facility editor's "save revision" share this
path, so a revision saved from the browser carries exactly the same evidence as
a generated variant: IFC with read-back, DXF, PDF, the canonical JSON, the
coordination issues and a BCF package that can continue an earlier issue
history.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from .bcf import write_bcf_package
from .building import BuildingIR
from .drawing_sheets import export_drawing_sheet_set
from .drawing_revision_history import parse_revision_history, write_revision_sources
from .equipment import EquipmentLayoutResult, EquipmentValidationReport
from .export import export_building_bundle
from .facility import FacilityProfile, FacilityValidationReport
from .flows import FlowRoutingResult, FlowValidationReport
from .ifc import export_building_ifc, validate_ifc_roundtrip
from .io import write_building_result, write_coordination_issues
from .models import LayoutResult
from .validation import ValidationReport


class VariantExportError(RuntimeError):
    """An artifact of the variant could not be produced or failed its read-back."""


def write_facility_variant(
    output: str | Path,
    building: BuildingIR,
    profile: FacilityProfile,
    result: LayoutResult,
    *,
    layout_report: ValidationReport,
    equipment: EquipmentLayoutResult,
    equipment_report: EquipmentValidationReport,
    flow_routes: FlowRoutingResult,
    flow_report: FlowValidationReport,
    facility_report: FacilityValidationReport,
    generation_evidence: dict[str, Any] | None = None,
    bcf_input: str | Path | None = None,
    sheet_font_path: str | Path | None = None,
    revision_history: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Export every artifact of one variant and return its manifest entry."""

    history = parse_revision_history(revision_history, building=building, drawing=profile.drawing) if revision_history is not None else None
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    if history is not None:
        write_revision_sources(output, history)
    stem = f"building_{result.variant:02d}"
    try:
        ifc_path = output / f"{stem}.ifc"
        ifc_summary = export_building_ifc(ifc_path, building, result, equipment, flow_routes, flow_report)
        ifc_readback = validate_ifc_roundtrip(
            ifc_path,
            ifc_summary,
            expected_flow_ids=(route.flow_id for route in flow_routes.routes),
        )
        if not ifc_readback.ok:
            raise RuntimeError("; ".join(ifc_readback.issues))
    except (ValueError, RuntimeError) as exc:
        raise VariantExportError(f"export/IFC QA failed for variant {result.variant}: {exc}") from exc
    dxf_path, pdf_path = export_building_bundle(
        output,
        building,
        result,
        equipment,
        layout_report,
        flow_routes,
        flow_report,
        profile.drawing,
    )
    sheets_path = output / f"{stem}_sheets.pdf"
    try:
        sheets = export_drawing_sheet_set(
            sheets_path, building, result, equipment, flows=flow_routes,
            drawing_profile=profile.drawing, font_path=sheet_font_path,
            revision_history=history.to_input() if history is not None else None,
        )
    except (OSError, ValueError, RuntimeError) as exc:
        raise VariantExportError(f"Drawing sheets for variant {result.variant} could not be generated: {exc}") from exc
    json_path = output / f"{stem}.json"
    write_building_result(
        json_path,
        building,
        result,
        equipment,
        flow_routes,
        flow_report,
        equipment_report=equipment_report,
        facility_report=facility_report,
        generation_evidence=generation_evidence,
    )
    if history is not None:
        canonical = json.loads(json_path.read_text(encoding="utf-8"))
        canonical["drawing_revision_history"] = history.to_index()
        json_path.write_text(json.dumps(canonical, ensure_ascii=False, indent=2), encoding="utf-8")
    references = {"program": json_path.name, "dxf": dxf_path.name, "pdf": pdf_path.name, "ifc": ifc_path.name}
    issues_path = output / f"{stem}.coordination.json"
    write_coordination_issues(
        issues_path,
        facility_report,
        project_name=building.layout.project_name,
        variant=result.variant,
        model_references=references,
    )
    bcf_path = output / f"{stem}.bcf"
    try:
        bcf_summary = write_bcf_package(
            bcf_path,
            facility_report,
            project_name=building.layout.project_name,
            variant=result.variant,
            model_references=references,
            ifc_path=ifc_path,
            previous=bcf_input,
        )
    except (OSError, ValueError, RuntimeError) as exc:
        raise VariantExportError(f"BCF package for variant {result.variant} could not be generated: {exc}") from exc
    return {
        "variant": result.variant,
        "generation": generation_evidence,
        "rooms": len(result.placements),
        "equipment": len(equipment.placements),
        "equipment_issues": len(equipment_report.issues),
        "flows": len(flow_routes.routes),
        "flow_issues": len(flow_report.issues),
        "facility_checks": len(facility_report.checks),
        "facility_issues": sum(check.status == "FAIL" for check in facility_report.checks),
        "coordination_issues": issues_path.name,
        "coordination_issue_count": len(facility_report.issues),
        "bcf": bcf_path.name,
        "bcf_version": bcf_summary.version,
        "bcf_topic_count": len(bcf_summary.topics),
        "bcf_open_topics": bcf_summary.open_topics,
        "bcf_resolved_topics": bcf_summary.resolved_topics,
        "facility_ok": facility_report.ok,
        "dxf": dxf_path.name,
        "pdf": pdf_path.name,
        "sheet_set": {
            "pdf": sheets_path.name,
            "sha256": hashlib.sha256(sheets_path.read_bytes()).hexdigest(),
            "pages": [sheet.to_dict() for sheet in sheets],
            **({"revision_history": history.to_index()} if history is not None else {}),
        },
        "ifc": ifc_path.name,
        "ifc_entities": {
            "spaces": ifc_summary.spaces,
            "walls": ifc_summary.walls,
            "doors": ifc_summary.doors,
            "windows": ifc_summary.windows,
            "equipment": ifc_summary.equipment,
            "equipment_types": ifc_summary.equipment_types,
            "flow_routes": ifc_summary.flow_routes,
            "openings": ifc_summary.openings,
        },
        "ifc_readback": ifc_readback.to_dict(),
        "json": json_path.name,
    }

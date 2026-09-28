"""Write one facility variant as a complete, self-checked bundle entry.

``generate-building`` and the facility editor's "save revision" share this
path, so a revision saved from the browser carries exactly the same evidence as
a generated variant: IFC with read-back, DXF, PDF, the canonical JSON, the
coordination issues and a BCF package that can continue an earlier issue
history.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .bcf import write_bcf_package
from .building import BuildingIR
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
) -> dict[str, Any]:
    """Export every artifact of one variant and return its manifest entry."""

    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
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

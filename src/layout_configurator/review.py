"""Reviewer dossiers for the external acceptance gates.

`PILOT_ACCEPTANCE_SPEC.md` keeps three gates that no test can close: a process
and quality review, a cleanroom/HVAC review and an architectural/BIM review.
Those reviewers should be deciding, not mining JSON, so this module projects an
accepted bundle into one document per role: the declarations that role is asked
to confirm, the deterministic evidence already produced, and the explicit list of
what the tool did not evaluate.

A dossier is a projection like DXF, PDF and IFC are. It states nothing the
canonical result does not already contain and it never turns a project policy
`PASS` into a regulatory verdict.
"""

from __future__ import annotations

import hashlib
import json
import zipfile
from collections import Counter
from collections.abc import Mapping, Sequence
from pathlib import Path

import ezdxf
import ifcopenshell

from .building import BuildingIR
from .equipment import EquipmentLayoutResult, clearance_rect
from .facility import FacilityProfile, FacilityValidationReport
from .flows import FlowRoutingResult
from .models import LayoutResult

BOUNDARY_NOTE = (
    "This package is an auditable early-design coordination package. It is not "
    "a GMP or ISO certification, a cleanroom qualification, an HVAC design, a "
    "contamination control strategy, construction documentation or a building "
    "permit. `PASS` only means that the declared project policy predicate "
    "passed."
)

RETURN_NOTE = (
    "Return comments as a BCF-topic in `{stem}.bcf` or as a list that names the "
    "room, equipment or flow id. The next iteration carries topics that "
    "disappeared from the report into the new package as `Closed`, so the "
    "comment history is preserved. Edits made in DXF/PDF do not flow back into "
    "the model: the facility program remains the source of truth."
)

IFC_ENTITIES = (
    "IfcProject",
    "IfcSite",
    "IfcBuilding",
    "IfcBuildingStorey",
    "IfcSpace",
    "IfcWall",
    "IfcDoor",
    "IfcWindow",
    "IfcOpeningElement",
    "IfcRelVoidsElement",
    "IfcRelFillsElement",
    "IfcRelSpaceBoundary",
    "IfcBuildingElementProxy",
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _table(header: Sequence[str], rows: Sequence[Sequence[str]]) -> str:
    if not rows:
        return "_No entries._\n"
    lines = ["| " + " | ".join(header) + " |", "| " + " | ".join("---" for _ in header) + " |"]
    lines.extend("| " + " | ".join(str(cell) for cell in row) + " |" for row in rows)
    return "\n".join(lines) + "\n"


def _status_rows(report: FacilityValidationReport) -> list[list[str]]:
    return [[check.id, check.status, "; ".join(check.evidence)] for check in report.checks]


def _header(title: str, building: BuildingIR, result: LayoutResult, profile: FacilityProfile) -> str:
    return (
        f"# {title}\n\n"
        f"**Project:** {building.layout.project_name}  \n"
        f"**Variant:** {result.variant}  \n"
        f"**Profile:** {profile.name} (`{profile.domain}`, version {profile.version})  \n"
        f"**Profile jurisdiction:** {profile.jurisdiction}\n\n"
        f"{BOUNDARY_NOTE}\n\n"
    )


def _decision_block(items: Sequence[str], stem: str) -> str:
    lines = ["## What is required from the reviewer\n"]
    lines.extend(f"{index}. {text}" for index, text in enumerate(items, start=1))
    lines.append("")
    lines.append("For each item, state one of: **confirmed**, "
                 "**program change required**, **exception recorded**.")
    lines.append("")
    lines.append(RETURN_NOTE.format(stem=stem))
    lines.append("")
    return "\n".join(lines)


def _out_of_scope(items: Sequence[str]) -> str:
    lines = ["## Outside the scope of the tool\n"]
    lines.extend(f"- {text}" for text in items)
    lines.append("")
    return "\n".join(lines)


def technologist_dossier(
    building: BuildingIR,
    result: LayoutResult,
    routes: FlowRoutingResult,
    report: FacilityValidationReport,
    profile: FacilityProfile,
    stem: str,
) -> str:
    routed = {route.flow_id for route in routes.routes}
    flow_rows = [
        [
            flow.id,
            flow.type,
            flow.stage or "—",
            "yes" if flow.required else "no",
            f"{flow.minimum_clear_width_mm:.0f}",
            ", ".join(flow.from_ids),
            ", ".join(flow.to_ids),
            "yes" if flow.id in routed else "no",
        ]
        for flow in building.flows
    ]
    route_rows = [
        [route.flow_id, route.from_id, route.to_id, " → ".join(route.room_path), f"{route.minimum_clear_width_mm:.0f}"]
        for route in routes.routes
    ]
    room_rows = [
        [
            room.id,
            room.type,
            f"{result.placements[room.id].area_m2:.1f}",
            f"{room.min_area_m2:.0f}–{room.max_area_m2:.0f}",
            f"{result.placements[room.id].width:.0f} × {result.placements[room.id].height:.0f}",
        ]
        for room in building.layout.rooms
    ]
    pair_rows = [[first, second] for first, second in profile.incompatible_flow_type_pairs]

    return (
        _header("Dossier: process technologist / pharmaceutical QA", building, result, profile)
        + "## Declared flows\n\n"
        + _table(
            ["Flow", "Type", "Stage", "Required", "Width, mm", "From", "To", "Route built"],
            flow_rows,
        )
        + "\n## Derived routes\n\n"
        + _table(["Flow", "Start", "End", "Through rooms", "Width, mm"], route_rows)
        + "\n## Flow separation policy in the profile\n\n"
        + "Type pairs that the profile treats as incompatible in shared intermediate rooms:\n\n"
        + _table(["Type A", "Type B"], pair_rows)
        + "\n## Room program\n\n"
        + _table(["Room", "Type", "Area, m²", "Allowed range, m²", "Size, mm"], room_rows)
        + "\n## Deterministic checks\n\n"
        + _table(["Check", "Status", "Evidence"], _status_rows(report))
        + "\n"
        + _decision_block(
            [
                "Confirm the stage sequence and the waste branch as the process contract.",
                "Confirm that the list of flow types covers the real process.",
                "Confirm or change the list of incompatible flow type pairs.",
                "Confirm room purposes and areas against the real equipment and headcount.",
                "Record exceptions that the model does not express.",
            ],
            stem,
        )
        + _out_of_scope(
            [
                "temporal flow separation and organisational procedures;",
                "transfer, disinfection and cleaning procedures;",
                "process, sterilisation and cleaning validation;",
                "the contamination control strategy and its approval.",
            ]
        )
    )


def cleanroom_dossier(
    building: BuildingIR,
    result: LayoutResult,
    report: FacilityValidationReport,
    profile: FacilityProfile,
    stem: str,
) -> str:
    zone_rows = [
        [
            zone.id,
            zone.type,
            zone.cleanroom_class or "—",
            "—" if zone.pressure_pa is None else f"{zone.pressure_pa:.0f}",
            zone.parent_zone_id or "—",
            "yes" if zone.airlock else "no",
            ", ".join(zone.room_ids),
        ]
        for zone in building.zones
    ]
    cascade_rows = []
    zones_by_id = {zone.id: zone for zone in building.zones}
    for zone in building.zones:
        parent = zones_by_id.get(zone.parent_zone_id or "")
        if parent is None or zone.pressure_pa is None or parent.pressure_pa is None:
            continue
        cascade_rows.append(
            [
                f"{zone.id} → {parent.id}",
                f"{zone.pressure_pa:.0f} → {parent.pressure_pa:.0f}",
                f"{parent.pressure_pa - zone.pressure_pa:.0f}",
            ]
        )
    airlock_rows = [
        [zone.id, zone.type, zone.parent_zone_id or "—", ", ".join(zone.room_ids)]
        for zone in building.zones
        if zone.airlock
    ]
    unknown = [check for check in report.checks if check.status == "UNKNOWN"]

    return (
        _header("Dossier: cleanroom / HVAC engineer", building, result, profile)
        + "## Zones and declared parameters\n\n"
        + _table(
            ["Zone", "Type", "Class", "Pressure, Pa", "Parent", "Airlock", "Rooms"],
            zone_rows,
        )
        + "\n## Declared pressure cascade\n\n"
        + "The values are a project declaration. The tool only checks their order and "
        + "presence and does not calculate air change.\n\n"
        + _table(["Transition", "Pa", "Differential, Pa"], cascade_rows)
        + "\n## Airlocks\n\n"
        + _table(["Zone", "Role", "Parent clean zone", "Rooms"], airlock_rows)
        + "\n## Deterministic checks\n\n"
        + _table(["Check", "Status", "Evidence"], _status_rows(report))
        + (
            "\n**Attention:** some checks have status `UNKNOWN` — "
            + ", ".join(check.id for check in unknown)
            + ". `UNKNOWN` means missing input data and is not a confirmation.\n"
            if unknown
            else ""
        )
        + "\n"
        + _decision_block(
            [
                "Confirm the zone classes and the parent-child cleanliness order.",
                "Confirm the declared pressure differentials and that the profile guidance value applies to this strategy.",
                "Confirm the separate personnel/material airlock roles and their assignment to a clean zone.",
                "Confirm that the absence of a door interlocking, air change, particle calculation and containment model is acceptable at this stage.",
                "Record HVAC zone requirements that the current model does not express.",
            ],
            stem,
        )
        + _out_of_scope(
            [
                "calculation of air change, air change rates and pressure differentials;",
                "airflow and particle count modelling;",
                "airlock door interlock logic;",
                "HVAC and containment equipment selection;",
                "cleanroom qualification to ISO 14644.",
            ]
        )
    )


def architect_dossier(
    building: BuildingIR,
    result: LayoutResult,
    equipment: EquipmentLayoutResult,
    routes: FlowRoutingResult,
    report: FacilityValidationReport,
    profile: FacilityProfile,
    stem: str,
    artifacts: Mapping[str, Path],
    inventory: Mapping[str, object],
) -> str:
    specs = {item.id: item for item in building.equipment}
    equipment_rows = []
    for placement in equipment.placements:
        spec = specs.get(placement.equipment_id)
        clearance = clearance_rect(spec, placement.rect, placement.rotated) if spec else placement.rect
        equipment_rows.append(
            [
                placement.equipment_id,
                spec.type if spec else "—",
                placement.room_id,
                f"{placement.rect.width:.0f} × {placement.rect.height:.0f}",
                f"{clearance.width:.0f} × {clearance.height:.0f}",
                "yes" if placement.rotated else "no",
            ]
        )
    grid = building.structural_grid
    grid_text = (
        "X axes: " + ", ".join(f"{label} ({axis:.0f})" for label, axis in zip(grid.labels_x, grid.axes_x_mm))
        + "  \nY axes: " + ", ".join(f"{label} ({axis:.0f})" for label, axis in zip(grid.labels_y, grid.axes_y_mm))
        if grid
        else "no structural grid defined"
    )
    artifact_rows = [
        [path.name, f"{path.stat().st_size / 1024:.1f} KB", _sha256(path)[:16] + "…"]
        for _, path in sorted(artifacts.items())
        if path.is_file()
    ]
    ifc_rows = [[entity, str(count)] for entity, count in (inventory.get("ifc_entities") or {}).items()]
    dxf_rows = [[layer, str(count)] for layer, count in (inventory.get("dxf_layers") or {}).items()]
    drawing = profile.drawing

    return (
        _header("Dossier: architect / BIM coordinator", building, result, profile)
        + "## Sheet\n\n"
        + f"Title block `{drawing.sheet_id} Rev {drawing.revision}`, discipline {drawing.discipline}, "
        + f"title \"{drawing.title}\".\n\n"
        + "## Structural axes\n\n"
        + grid_text
        + "\n\n## Equipment and service clearances\n\n"
        + _table(
            ["Equipment", "Type", "Room", "Size, mm", "Including clearance, mm", "Rotated"],
            equipment_rows,
        )
        + "\n## IFC contents\n\n"
        + _table(["Entity", "Count"], ifc_rows)
        + "\n## DXF layers\n\n"
        + _table(["Layer", "Entities"], dxf_rows)
        + "\n## Package files\n\n"
        + _table(["File", "Size", "SHA-256"], artifact_rows)
        + "\n## Deterministic checks\n\n"
        + _table(["Check", "Status", "Evidence"], _status_rows(report))
        + "\n"
        + _decision_block(
            [
                "Open the DXF in the receiving CAD tool and the IFC in the receiving BIM tool; record the tool, version and any losses on import.",
                "Check the layers, equipment blocks, dashed clearances and the sheet title block.",
                "Check the IFC spatial structure, walls, openings, types, `IfcRelSpaceBoundary` and the equipment and route property sets.",
                "Compare the package variants: room, equipment and flow identifiers must match while the geometry differs.",
                "Record drafting requirements that the current projection does not meet.",
            ],
            stem,
        )
        + _out_of_scope(
            [
                "construction documentation and details;",
                "structural analysis and MEP routing;",
                "fire safety, egress and accessibility requirements;",
                "approvals and the building permit.",
            ]
        )
    )


def artifact_inventory(artifacts: Mapping[str, Path]) -> dict[str, object]:
    """Read back the exchange artifacts of one variant for the architect dossier."""

    inventory: dict[str, object] = {}
    ifc_path = artifacts.get("ifc")
    if ifc_path is not None and ifc_path.is_file():
        model = ifcopenshell.open(str(ifc_path))
        inventory["ifc_schema"] = model.schema
        inventory["ifc_entities"] = {entity: len(model.by_type(entity)) for entity in IFC_ENTITIES}
    dxf_path = artifacts.get("dxf")
    if dxf_path is not None and dxf_path.is_file():
        document = ezdxf.readfile(str(dxf_path))
        layers = Counter(entity.dxf.layer for entity in document.modelspace())
        inventory["dxf_version"] = document.dxfversion
        inventory["dxf_layers"] = dict(sorted(layers.items()))
        inventory["dxf_blocks"] = sorted(
            block.name for block in document.blocks if not block.name.startswith("*")
        )
    bcf_path = artifacts.get("bcf")
    if bcf_path is not None and bcf_path.is_file():
        with zipfile.ZipFile(bcf_path) as archive:
            names = archive.namelist()
        inventory["bcf_topics"] = sum(name.endswith("markup.bcf") for name in names)
        inventory["bcf_viewpoints"] = sum(name.endswith(".bcfv") for name in names)
    inventory["sha256"] = {
        path.name: _sha256(path) for _, path in sorted(artifacts.items()) if path.is_file()
    }
    return inventory


def write_review_dossier(
    directory: str | Path,
    building: BuildingIR,
    result: LayoutResult,
    equipment: EquipmentLayoutResult,
    routes: FlowRoutingResult,
    report: FacilityValidationReport,
    profile: FacilityProfile,
    *,
    source: Path,
) -> dict[str, Path]:
    """Write one dossier per external gate plus the artifact inventory."""

    target = Path(directory)
    target.mkdir(parents=True, exist_ok=True)
    stem = source.stem
    artifacts = {
        suffix.lstrip("."): source.with_suffix(suffix)
        for suffix in (".json", ".dxf", ".pdf", ".ifc", ".bcf")
    }
    inventory = artifact_inventory(artifacts)

    written: dict[str, Path] = {}
    documents = {
        "technologist.md": technologist_dossier(building, result, routes, report, profile, stem),
        "cleanroom-hvac.md": cleanroom_dossier(building, result, report, profile, stem),
        "architect-bim.md": architect_dossier(
            building, result, equipment, routes, report, profile, stem, artifacts, inventory
        ),
    }
    for name, text in documents.items():
        path = target / f"{stem}.{name}"
        path.write_text(text, encoding="utf-8")
        written[name] = path

    inventory_path = target / f"{stem}.artifact-inventory.json"
    inventory_path.write_text(json.dumps(inventory, ensure_ascii=False, indent=2), encoding="utf-8")
    written["artifact-inventory.json"] = inventory_path
    return written

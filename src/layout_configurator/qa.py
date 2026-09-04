"""Deterministic QA for a generated facility coordination bundle."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import re
from typing import Any
from zipfile import BadZipFile
import xml.etree.ElementTree as ET

from .bcf import read_bcf_package
from .facility import FacilityProfile, default_facility_profile, validate_building
from .flows import route_flows, validate_flow_routes
from .ifc import read_ifc_summary, validate_ifc_roundtrip
from .issue_management import issue_state_map, load_issue_management
from .io import load_building_result
from .equipment import validate_equipment_layout


@dataclass(frozen=True)
class BundleQACheck:
    id: str
    ok: bool
    message: str
    details: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"id": self.id, "status": "PASS" if self.ok else "FAIL", "message": self.message}
        if self.details:
            payload["details"] = self.details
        return payload


@dataclass(frozen=True)
class BundleQAReport:
    input_path: Path
    checks: tuple[BundleQACheck, ...]

    @property
    def ok(self) -> bool:
        return all(check.ok for check in self.checks)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "input": str(self.input_path),
            "checks": [check.to_dict() for check in self.checks],
        }


@dataclass(frozen=True)
class BuildingSetQAReport:
    """QA result for a generated multi-variant coordination bundle."""

    output_dir: Path
    variant_reports: tuple[BundleQAReport, ...]
    consistency: BundleQACheck
    expected_variants: int | None = None

    @property
    def ok(self) -> bool:
        return self.consistency.ok and all(report.ok for report in self.variant_reports)

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "ok": self.ok,
            "output": str(self.output_dir),
            "variant_count": len(self.variant_reports),
            "variants": [report.to_dict() for report in self.variant_reports],
            "consistency": self.consistency.to_dict(),
        }
        if self.expected_variants is not None:
            payload["expected_variants"] = self.expected_variants
        return payload


def validate_building_bundle(
    input_path: str | Path,
    *,
    profile: FacilityProfile | None = None,
    dxf_path: str | Path | None = None,
    pdf_path: str | Path | None = None,
    ifc_path: str | Path | None = None,
    bcf_path: str | Path | None = None,
    coordination_path: str | Path | None = None,
    manifest_path: str | Path | None = None,
    schema_path: str | Path = "schemas/coordination_issues.schema.json",
) -> BundleQAReport:
    """Check the generated artifacts without opening or modifying them."""

    source = Path(input_path)
    payload = json.loads(source.read_text(encoding="utf-8"))
    building, result, equipment = load_building_result(source)
    selected_profile = profile or _profile_from_payload(payload)
    equipment_report = validate_equipment_layout(building, result, equipment)
    routes = route_flows(building, result, equipment)
    flow_report = validate_flow_routes(building, result, routes, equipment)
    facility_report = validate_building(
        building,
        result,
        equipment,
        routes,
        flow_report,
        profile=selected_profile,
        equipment_report=equipment_report,
    )
    management_path = source.with_name(f"{source.stem}.issue-management.json")
    management = load_issue_management(management_path)
    managed_records = issue_state_map(management)
    expected_open = sum(
        managed_records.get(issue.issue_id, {}).get("status", issue.status) == "OPEN"
        for issue in facility_report.issues
    )
    checks: list[BundleQACheck] = [
        BundleQACheck(
            "FACILITY_VALIDATION",
            facility_report.ok,
            "Facility checks pass" if facility_report.ok else "Facility checks contain failures",
            {"issues": len(facility_report.issues)},
        )
    ]

    dxf = Path(dxf_path) if dxf_path is not None else source.with_suffix(".dxf")
    pdf = Path(pdf_path) if pdf_path is not None else source.with_suffix(".pdf")
    ifc = Path(ifc_path) if ifc_path is not None else source.with_suffix(".ifc")
    bcf = Path(bcf_path) if bcf_path is not None else source.with_suffix(".bcf")
    coordination = (
        Path(coordination_path)
        if coordination_path is not None
        else source.with_suffix(".coordination.json")
    )
    manifest = Path(manifest_path) if manifest_path is not None else source.parent / "manifest.json"

    checks.append(_qa_manifest(manifest, source, (dxf, pdf, ifc, bcf, coordination)))
    checks.append(_qa_dxf(dxf, len(equipment.placements), len(routes.routes)))
    checks.append(_qa_pdf(pdf))
    checks.append(
        _qa_ifc(
            ifc,
            room_count=len(result.placements),
            equipment_count=len(equipment.placements),
            flow_ids={route.flow_id for route in routes.routes},
        )
    )
    checks.append(_qa_bcf(bcf, {issue.issue_id for issue in facility_report.issues}, expected_open=expected_open))
    checks.append(_qa_coordination(coordination, facility_report, schema_path, expected_open=expected_open))
    return BundleQAReport(source, tuple(checks))


def validate_building_set(
    output_dir: str | Path,
    *,
    expected_variants: int | None = None,
    profile: FacilityProfile | None = None,
) -> BuildingSetQAReport:
    """Run full bundle QA and cross-variant identity checks in read-only mode."""

    root = Path(output_dir)
    if not root.is_dir():
        raise OSError(f"Building set directory does not exist: {root}")
    if expected_variants is not None and expected_variants < 1:
        raise ValueError("expected_variants must be positive")

    variant_paths = sorted(
        (path for path in root.iterdir() if _VARIANT_JSON_RE.fullmatch(path.name)),
        key=_variant_sort_key,
    )
    reports: list[BundleQAReport] = []
    signatures: list[dict[str, Any] | None] = []
    for path in variant_paths:
        try:
            report = validate_building_bundle(path, profile=profile)
        except (BadZipFile, ET.ParseError, OSError, KeyError, TypeError, ValueError, RuntimeError) as exc:
            report = BundleQAReport(
                path,
                (BundleQACheck("BUNDLE_READ", False, f"Bundle QA failed: {exc}"),),
            )
        reports.append(report)
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            signatures.append(_variant_exchange_signature(root, path, payload))
        except (BadZipFile, ET.ParseError, OSError, TypeError, ValueError, RuntimeError) as exc:
            signatures.append(None)

    mismatches: list[dict[str, Any]] = []
    if not variant_paths:
        mismatches.append({"code": "NO_VARIANTS", "message": "No building_XX.json variants found"})
    if expected_variants is not None and len(variant_paths) != expected_variants:
        mismatches.append(
            {
                "code": "VARIANT_COUNT",
                "expected": expected_variants,
                "actual": len(variant_paths),
            }
        )
    if any(signature is None for signature in signatures):
        mismatches.append({"code": "SIGNATURE_READ", "message": "One or more variant signatures could not be read"})
    elif signatures:
        baseline = signatures[0]
        for path, signature in zip(variant_paths[1:], signatures[1:]):
            if baseline is None or signature is None:
                continue
            for key in ("semantic_ids", "route_topology", "ifc", "bcf"):
                if signature[key] != baseline[key]:
                    mismatches.append(
                        {
                            "code": "IDENTITY_DRIFT",
                            "variant": path.name,
                            "field": key,
                            "expected": baseline[key],
                            "actual": signature[key],
                        }
                    )

    details: dict[str, Any] = {
        "variant_count": len(variant_paths),
        "variants": [path.name for path in variant_paths],
        "mismatches": mismatches,
    }
    if signatures and signatures[0] is not None:
        details["baseline"] = signatures[0]
    consistency = BundleQACheck(
        "CROSS_VARIANT_CONSISTENCY",
        not mismatches,
        "Semantic IDs and BCF/IFC exchange identities match across variants"
        if not mismatches
        else "Cross-variant identity checks failed",
        details,
    )
    return BuildingSetQAReport(root, tuple(reports), consistency, expected_variants)


_VARIANT_JSON_RE = re.compile(r"building_(\d+)\.json$")


def _variant_sort_key(path: Path) -> tuple[int, str]:
    match = _VARIANT_JSON_RE.fullmatch(path.name)
    return (int(match.group(1)), path.name) if match else (10**9, path.name)


def _variant_exchange_signature(root: Path, path: Path, payload: dict[str, Any]) -> dict[str, Any]:
    spec = payload.get("spec", {})
    if not isinstance(spec, dict):
        raise ValueError("Variant is missing the spec object")
    layout = spec.get("layout", {})
    if not isinstance(layout, dict):
        raise ValueError("Variant is missing spec.layout")

    semantic_ids = {
        "rooms": _id_tuple(layout.get("rooms", ())),
        "equipment": _id_tuple(spec.get("equipment", ())),
        "flows": _id_tuple(spec.get("flows", ())),
    }
    flow_routes = payload.get("flow_routes", {})
    if not isinstance(flow_routes, dict):
        raise ValueError("Variant is missing flow_routes")
    route_topology = tuple(
        sorted(
            (
                str(route.get("flow_id")),
                str(route.get("from_id")),
                str(route.get("to_id")),
                str(route.get("from_room_id")),
                str(route.get("to_room_id")),
                float(route.get("minimum_clear_width_mm", 0)),
                tuple(str(room_id) for room_id in route.get("room_path", ())),
            )
            for route in flow_routes.get("routes", ())
            if isinstance(route, dict)
        )
    )

    bcf_summary = read_bcf_package(root / f"{path.stem}.bcf")
    bcf_topics = tuple(
        sorted(
            (
                topic.issue_id or "",
                topic.topic_id,
                topic.normalized_status,
            )
            for topic in bcf_summary.topics
        )
    )
    ifc_summary = read_ifc_summary(root / f"{path.stem}.ifc")
    return {
        "semantic_ids": semantic_ids,
        "route_topology": route_topology,
        "ifc": {
            "project_global_id": ifc_summary.project_global_id,
            "equipment_ids": tuple(ifc_summary.equipment_ids),
            "flow_ids": tuple(ifc_summary.flow_ids),
        },
        "bcf": {
            "version": bcf_summary.version,
            "topics": bcf_topics,
        },
    }


def _id_tuple(items: Any) -> tuple[str, ...]:
    if not isinstance(items, (list, tuple)):
        raise ValueError("Expected a list of objects with IDs")
    return tuple(sorted(str(item["id"]) for item in items if isinstance(item, dict) and "id" in item))


def _profile_from_payload(payload: dict[str, Any]) -> FacilityProfile:
    raw = payload.get("facility_validation", {}).get("profile")
    if isinstance(raw, dict):
        return FacilityProfile.from_mapping(raw)
    return default_facility_profile()


def _qa_manifest(path: Path, input_path: Path, artifacts: tuple[Path, ...]) -> BundleQACheck:
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
        variants = manifest.get("variants")
        if not isinstance(variants, list):
            return BundleQACheck("MANIFEST", False, "Manifest has no variants list")
        variant = next((item for item in variants if isinstance(item, dict) and item.get("json") == input_path.name), None)
        if variant is None:
            return BundleQACheck("MANIFEST", False, "Manifest does not reference the input result")
        missing = [str(item) for item in artifacts if not item.is_file()]
        linked_names = {
            str(variant.get(key))
            for key in ("dxf", "pdf", "ifc", "bcf", "coordination_issues")
            if variant.get(key)
        }
        expected_names = {item.name for item in artifacts}
        if missing:
            return BundleQACheck("MANIFEST", False, "Manifest bundle references missing artifacts", {"missing": missing})
        if not expected_names.issubset(linked_names):
            return BundleQACheck(
                "MANIFEST",
                False,
                "Manifest does not link every expected artifact",
                {"missing_links": sorted(expected_names - linked_names)},
            )
        readback = variant.get("ifc_readback", {})
        if not isinstance(readback, dict) or readback.get("ok") is not True:
            return BundleQACheck("MANIFEST", False, "Manifest IFC read-back is not PASS")
        return BundleQACheck("MANIFEST", True, "Manifest links the complete coordination bundle", {"variant": variant.get("variant")})
    except (OSError, TypeError, ValueError) as exc:
        return BundleQACheck("MANIFEST", False, f"Manifest QA failed: {exc}")


def _qa_dxf(path: Path, equipment_count: int, flow_count: int) -> BundleQACheck:
    try:
        import ezdxf

        document = ezdxf.readfile(path)
        entities = list(document.modelspace())
        layers = {str(layer.dxf.name) for layer in document.layers}
        required_layers = {"A-WALL", "A-ROOM", "A-DIMS", "A-TITLE"}
        missing_layers = sorted(required_layers - layers)
        equipment_entities = sum(entity.dxf.layer == "A-EQUIP" for entity in entities)
        flow_entities = sum(
            entity.dxf.layer == "A-FLOW" and entity.dxftype() == "LWPOLYLINE"
            for entity in entities
        )
        if missing_layers:
            return BundleQACheck("DXF_READBACK", False, "DXF is missing required layers", {"missing_layers": missing_layers})
        if equipment_count and equipment_entities != equipment_count:
            return BundleQACheck(
                "DXF_READBACK",
                False,
                "DXF equipment entity count does not match the program",
                {"expected_equipment": equipment_count, "actual_equipment": equipment_entities},
            )
        if flow_entities != flow_count:
            return BundleQACheck(
                "DXF_READBACK",
                False,
                "DXF flow centerline count does not match derived routes",
                {"expected_flows": flow_count, "actual_flows": flow_entities},
            )
        return BundleQACheck(
            "DXF_READBACK",
            True,
            "DXF opens and contains the expected coordination geometry",
            {"layers": len(layers), "equipment_entities": equipment_entities, "flow_centerlines": flow_entities},
        )
    except (ImportError, OSError, RuntimeError, ValueError) as exc:
        return BundleQACheck("DXF_READBACK", False, f"DXF read-back failed: {exc}")


def _qa_pdf(path: Path) -> BundleQACheck:
    try:
        data = path.read_bytes()
        valid = data.startswith(b"%PDF-") and b"%%EOF" in data[-128:]
        return BundleQACheck(
            "PDF_READBACK",
            valid,
            "PDF signature and trailer are present" if valid else "PDF signature or trailer is missing",
            {"bytes": len(data)},
        )
    except OSError as exc:
        return BundleQACheck("PDF_READBACK", False, f"PDF read-back failed: {exc}")


def _qa_ifc(path: Path, *, room_count: int, equipment_count: int, flow_ids: set[str]) -> BundleQACheck:
    report = validate_ifc_roundtrip(path, expected_flow_ids=flow_ids)
    if not report.ok or report.summary is None:
        return BundleQACheck("IFC_READBACK", False, "; ".join(report.issues) or "IFC read-back failed")
    summary = report.summary
    count_issues = []
    if summary.spaces != room_count:
        count_issues.append(f"spaces expected {room_count}, got {summary.spaces}")
    if summary.equipment != equipment_count:
        count_issues.append(f"equipment expected {equipment_count}, got {summary.equipment}")
    if count_issues:
        return BundleQACheck("IFC_READBACK", False, "IFC counts do not match the program", {"issues": count_issues})
    return BundleQACheck(
        "IFC_READBACK",
        True,
        "IFC opens and preserves room/equipment/flow metadata",
        {"spaces": summary.spaces, "equipment": summary.equipment, "flow_routes": summary.flow_routes},
    )


def _qa_bcf(path: Path, issue_ids: set[str], *, expected_open: int) -> BundleQACheck:
    try:
        summary = read_bcf_package(path)
        actual_ids = {topic.issue_id for topic in summary.topics if topic.issue_id}
        missing = sorted(issue_ids - actual_ids)
        if missing or summary.open_topics != expected_open:
            return BundleQACheck(
                "BCF_READBACK",
                False,
                "BCF topics do not match current coordination issues",
                {"missing_issue_ids": missing, "expected_open": expected_open, "actual_open": summary.open_topics},
            )
        return BundleQACheck(
            "BCF_READBACK",
            True,
            "BCF 2.1 package opens and contains current issues",
            {"version": summary.version, "topics": len(summary.topics), "open_topics": summary.open_topics, "resolved_topics": summary.resolved_topics},
        )
    except (OSError, ValueError, RuntimeError) as exc:
        return BundleQACheck("BCF_READBACK", False, f"BCF read-back failed: {exc}")


def _qa_coordination(path: Path, facility_report, schema_path: str | Path, *, expected_open: int) -> BundleQACheck:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        from jsonschema import Draft202012Validator

        schema = json.loads(Path(schema_path).read_text(encoding="utf-8"))
        errors = list(Draft202012Validator(schema).iter_errors(data))
        if errors:
            return BundleQACheck("COORDINATION_JSON", False, "Coordination sidecar violates its JSON Schema", {"errors": [error.message for error in errors]})
        current_ids = {issue.issue_id for issue in facility_report.issues}
        actual_ids = {issue.get("issue_id") for issue in data.get("issues", []) if isinstance(issue, dict)}
        missing = sorted(current_ids - actual_ids)
        if missing:
            return BundleQACheck("COORDINATION_JSON", False, "Coordination sidecar is missing current issue IDs", {"missing_issue_ids": missing})
        if data.get("open_count") != expected_open:
            return BundleQACheck("COORDINATION_JSON", False, "Coordination sidecar issue count is stale")
        return BundleQACheck("COORDINATION_JSON", True, "Coordination sidecar matches schema and current issue workflow", {"open_count": data.get("open_count", 0), "resolved_count": data.get("resolved_count", 0)})
    except (ImportError, OSError, TypeError, ValueError) as exc:
        return BundleQACheck("COORDINATION_JSON", False, f"Coordination sidecar QA failed: {exc}")

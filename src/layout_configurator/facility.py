"""Facility-level validation for regulated and process-heavy layouts.

``LayoutIR`` checks are intentionally small and generic.  This module adds
the next contract layer for ``BuildingIR``: zone relations, equipment evidence,
flow completeness and profile-specific flow separation.  It does not claim to
be a building-code engine; profiles are versioned project/domain policies.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
from pathlib import Path
from typing import Any, Literal, Mapping

from .building import BuildingIR
from .equipment import EquipmentLayoutResult, EquipmentValidationReport
from .flows import FlowRoutingResult, FlowValidationReport
from .models import LayoutResult
from .validation import ValidationReport, validate_layout


FacilityCheckStatus = Literal["PASS", "FAIL", "NOT_APPLICABLE", "UNKNOWN"]
CoordinationSeverity = Literal["ERROR", "WARNING", "INFO"]


@dataclass(frozen=True)
class FacilityDrawingProfile:
    """Deterministic presentation settings for facility drawing exports."""

    sheet_id: str = "A-101"
    discipline: str = "Facility layout"
    title: str = "Generated coordination plan"
    revision: str = "P01"
    show_room_dimensions: bool = True
    show_flow_legend: bool = True
    show_flow_labels: bool = True
    show_equipment_clearance: bool = True
    show_structural_grid: bool = True
    legend_offset_x_mm: float = 900.0
    legend_offset_y_mm: float = 1_200.0

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> "FacilityDrawingProfile":
        if not isinstance(raw, Mapping):
            raise ValueError("Facility profile drawing must be an object")
        return cls(
            sheet_id=str(raw.get("sheet_id", "A-101")).strip() or "A-101",
            discipline=str(raw.get("discipline", "Facility layout")).strip() or "Facility layout",
            title=str(raw.get("title", "Generated coordination plan")).strip() or "Generated coordination plan",
            revision=str(raw.get("revision", "P01")).strip() or "P01",
            show_room_dimensions=_profile_bool(raw.get("show_room_dimensions", True)),
            show_flow_legend=_profile_bool(raw.get("show_flow_legend", True)),
            show_flow_labels=_profile_bool(raw.get("show_flow_labels", True)),
            show_equipment_clearance=_profile_bool(raw.get("show_equipment_clearance", True)),
            show_structural_grid=_profile_bool(raw.get("show_structural_grid", True)),
            legend_offset_x_mm=_profile_non_negative(raw.get("legend_offset_x_mm", 900), "legend_offset_x_mm"),
            legend_offset_y_mm=_profile_non_negative(raw.get("legend_offset_y_mm", 1_200), "legend_offset_y_mm"),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "sheet_id": self.sheet_id,
            "discipline": self.discipline,
            "title": self.title,
            "revision": self.revision,
            "show_room_dimensions": self.show_room_dimensions,
            "show_flow_legend": self.show_flow_legend,
            "show_flow_labels": self.show_flow_labels,
            "show_equipment_clearance": self.show_equipment_clearance,
            "show_structural_grid": self.show_structural_grid,
            "legend_offset_x_mm": self.legend_offset_x_mm,
            "legend_offset_y_mm": self.legend_offset_y_mm,
        }


@dataclass(frozen=True)
class FacilityProfile:
    """Versioned domain policy used by facility-level checks."""

    name: str
    version: str
    domain: str
    jurisdiction: str
    incompatible_flow_type_pairs: tuple[tuple[str, str], ...] = ()
    provenance: Mapping[str, str] = field(default_factory=dict)
    drawing: FacilityDrawingProfile = field(default_factory=FacilityDrawingProfile)

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> "FacilityProfile":
        if not isinstance(raw, Mapping):
            raise ValueError("Facility profile root must be an object")
        separation = raw.get("flow_separation", {})
        if separation is None:
            separation = {}
        if not isinstance(separation, Mapping):
            raise ValueError("Facility profile flow_separation must be an object")
        pairs_raw = separation.get(
            "incompatible_flow_type_pairs",
            raw.get("incompatible_flow_type_pairs", ()),
        )
        if not isinstance(pairs_raw, (list, tuple)):
            raise ValueError("Facility profile incompatible flow type pairs must be a list")
        pairs: list[tuple[str, str]] = []
        for index, pair in enumerate(pairs_raw):
            if not isinstance(pair, (list, tuple)) or len(pair) != 2:
                raise ValueError(f"incompatible_flow_type_pairs[{index}] must contain two flow types")
            left, right = (str(value).strip().lower() for value in pair)
            if not left or not right or left == right:
                raise ValueError(f"incompatible_flow_type_pairs[{index}] must contain two distinct types")
            normalised = tuple(sorted((left, right)))
            if normalised not in pairs:
                pairs.append(normalised)
        provenance = raw.get("provenance", {})
        if provenance is None:
            provenance = {}
        if not isinstance(provenance, Mapping):
            raise ValueError("Facility profile provenance must be an object")
        drawing_raw = raw.get("drawing", {})
        drawing = FacilityDrawingProfile.from_mapping(drawing_raw)
        return cls(
            name=str(raw.get("name", "Unnamed facility profile")),
            version=str(raw.get("version", "0.1")),
            domain=str(raw.get("domain", "generic-facility")),
            jurisdiction=str(raw.get("jurisdiction", "project-profile-not-a-code-verdict")),
            incompatible_flow_type_pairs=tuple(pairs),
            provenance={str(key): str(value) for key, value in provenance.items()},
            drawing=drawing,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "version": self.version,
            "domain": self.domain,
            "jurisdiction": self.jurisdiction,
            "flow_separation": {
                "incompatible_flow_type_pairs": [list(pair) for pair in self.incompatible_flow_type_pairs]
            },
            "provenance": dict(self.provenance),
            "drawing": self.drawing.to_dict(),
        }


def default_facility_profile() -> FacilityProfile:
    """Return the safe default profile used by ``generate-building``."""

    return FacilityProfile(
        name="Pharma-like clean production pilot",
        version="0.1",
        domain="pharma-clean-production",
        jurisdiction="project-profile-not-a-regulatory-verdict",
        incompatible_flow_type_pairs=(
            ("material", "waste"),
            ("people", "waste"),
            ("finished_goods", "waste"),
        ),
        provenance={"source": "Facility Layout Compiler domain profile"},
        drawing=FacilityDrawingProfile(
            sheet_id="A-101",
            discipline="Pharma clean production",
            title="Clean production coordination plan",
        ),
    )


@dataclass(frozen=True)
class FacilityCheckResult:
    id: str
    title: str
    status: FacilityCheckStatus
    evidence: tuple[str, ...]

    @property
    def ok(self) -> bool:
        return self.status in {"PASS", "NOT_APPLICABLE"}

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "status": self.status,
            "evidence": list(self.evidence),
        }


@dataclass(frozen=True)
class CoordinationIssue:
    """Portable BCF-like issue record derived from deterministic checks."""

    issue_id: str
    code: str
    title: str
    message: str
    severity: CoordinationSeverity
    source: str
    status: Literal["OPEN", "RESOLVED"] = "OPEN"
    flow_id: str | None = None
    equipment_id: str | None = None
    location: tuple[float, float] | None = None

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "issue_id": self.issue_id,
            "code": self.code,
            "title": self.title,
            "message": self.message,
            "severity": self.severity,
            "source": self.source,
            "status": self.status,
        }
        if self.flow_id is not None:
            payload["flow_id"] = self.flow_id
        if self.equipment_id is not None:
            payload["equipment_id"] = self.equipment_id
        if self.location is not None:
            payload["location"] = {"x": self.location[0], "y": self.location[1]}
        return payload


@dataclass(frozen=True)
class FacilityValidationReport:
    profile: FacilityProfile
    checks: tuple[FacilityCheckResult, ...]
    issues: tuple[CoordinationIssue, ...] = ()

    @property
    def ok(self) -> bool:
        return all(check.ok for check in self.checks)

    def to_dict(self) -> dict[str, Any]:
        return {
            "profile": self.profile.to_dict(),
            "ok": self.ok,
            "checks": [check.to_dict() for check in self.checks],
            "issues": [issue.to_dict() for issue in self.issues],
        }


def load_facility_profile(path: str | Path) -> FacilityProfile:
    """Load a YAML domain profile without executing arbitrary rule code."""

    import yaml

    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    return FacilityProfile.from_mapping(raw)


def validate_building(
    building: BuildingIR,
    layout: LayoutResult,
    equipment: EquipmentLayoutResult | None,
    flow_routes: FlowRoutingResult | None,
    flow_report: FlowValidationReport | None,
    *,
    profile: FacilityProfile,
    equipment_report: EquipmentValidationReport | None = None,
) -> FacilityValidationReport:
    """Run deterministic facility checks and preserve evidence for each one."""

    geometry_report = validate_layout(building.layout, layout)
    checks = [
        _geometry_check(geometry_report),
        _zone_check(building, layout),
        _equipment_check(building, equipment, equipment_report),
        _flow_check(building, flow_routes, flow_report),
        _flow_separation_check(building, flow_routes, profile),
    ]
    issues = _coordination_issues(
        checks,
        geometry_report,
        equipment_report,
        flow_routes,
        flow_report,
    )
    return FacilityValidationReport(profile=profile, checks=tuple(checks), issues=issues)


def _coordination_issues(
    checks: list[FacilityCheckResult],
    geometry_report: ValidationReport,
    equipment_report: EquipmentValidationReport | None,
    flow_routes: FlowRoutingResult | None,
    flow_report: FlowValidationReport | None,
) -> tuple[CoordinationIssue, ...]:
    """Convert failed/unknown evidence into deterministic issue records."""

    issues: list[CoordinationIssue] = []
    check_by_id = {check.id: check for check in checks}
    for item in geometry_report.issues:
        issues.append(
            _issue(
                code=item.code.upper(),
                title="Facility geometry validation failed",
                message=item.message,
                source="FACILITY_GEOMETRY",
            )
        )
    if equipment_report is not None:
        for item in equipment_report.issues:
            issues.append(
                _issue(
                    code=item.code.upper(),
                    title="Equipment coordination issue",
                    message=item.message,
                    source="EQUIPMENT_CLEARANCE",
                    equipment_id=item.equipment_id,
                )
            )
    if flow_report is not None:
        for item in flow_report.issues:
            route = next(
                (
                    route
                    for route in (flow_routes.routes if flow_routes else ())
                    if item.flow_id is not None and route.flow_id == item.flow_id
                ),
                None,
            )
            location = None if route is None else _route_midpoint(route.points)
            issues.append(
                _issue(
                    code=item.code.upper(),
                    title="Flow coordination issue",
                    message=item.message,
                    source="FLOW_COMPLETENESS",
                    flow_id=item.flow_id,
                    location=location,
                )
            )

    zone_check = check_by_id.get("ZONE_RELATIONS")
    if zone_check is not None and zone_check.status == "FAIL":
        for message in zone_check.evidence:
            if "satisfied by" in message or "has no touching" in message:
                continue
            issues.append(
                _issue(
                    code="ZONE_RELATION",
                    title="Functional zone relation is not satisfied",
                    message=message,
                    source="ZONE_RELATIONS",
                )
            )

    separation_check = check_by_id.get("FLOW_TYPE_SEPARATION")
    if separation_check is not None and separation_check.status == "FAIL":
        for message in separation_check.evidence:
            if "share intermediate" not in message:
                continue
            issues.append(
                _issue(
                    code="FLOW_TYPE_SEPARATION",
                    title="Incompatible flow types share an intermediate room",
                    message=message,
                    source="FLOW_TYPE_SEPARATION",
                )
            )

    for check in checks:
        if check.status != "UNKNOWN":
            continue
        issues.append(
            _issue(
                code=f"{check.id}_UNKNOWN",
                title="Facility check could not be evaluated",
                message="; ".join(check.evidence),
                source=check.id,
                severity="WARNING",
            )
        )
    return tuple(issues)


def _issue(
    *,
    code: str,
    title: str,
    message: str,
    source: str,
    severity: CoordinationSeverity = "ERROR",
    flow_id: str | None = None,
    equipment_id: str | None = None,
    location: tuple[float, float] | None = None,
) -> CoordinationIssue:
    seed = "|".join(
        str(value)
        for value in (code, title, message, source, flow_id, equipment_id, location)
    )
    issue_id = f"FLC-{hashlib.sha1(seed.encode('utf-8')).hexdigest()[:12].upper()}"
    return CoordinationIssue(
        issue_id=issue_id,
        code=code,
        title=title,
        message=message,
        severity=severity,
        source=source,
        flow_id=flow_id,
        equipment_id=equipment_id,
        location=location,
    )


def _route_midpoint(points: tuple[tuple[float, float], ...]) -> tuple[float, float] | None:
    if not points:
        return None
    index = (len(points) - 1) // 2
    return points[index]


def _profile_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and value in {0, 1}:
        return bool(value)
    if isinstance(value, str) and value.strip().lower() in {"true", "yes", "on", "1"}:
        return True
    if isinstance(value, str) and value.strip().lower() in {"false", "no", "off", "0"}:
        return False
    raise ValueError(f"Expected a boolean drawing setting, got {value!r}")


def _profile_non_negative(value: Any, field_name: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Facility drawing {field_name} must be a number") from exc
    if number < 0:
        raise ValueError(f"Facility drawing {field_name} must be non-negative")
    return number


def _geometry_check(report: ValidationReport) -> FacilityCheckResult:
    if report.ok:
        return FacilityCheckResult(
            "FACILITY_GEOMETRY",
            "Room geometry and topology are valid",
            "PASS",
            ("Independent LayoutIR geometry validation passed",),
        )
    return FacilityCheckResult(
        "FACILITY_GEOMETRY",
        "Room geometry and topology are valid",
        "FAIL",
        tuple(f"{issue.code}: {issue.message}" for issue in report.issues),
    )


def _zone_check(building: BuildingIR, layout: LayoutResult) -> FacilityCheckResult:
    required = building.zone_relation_groups("required_adjacency")
    forbidden = building.zone_relation_groups("forbidden_adjacency")
    if not required and not forbidden:
        return FacilityCheckResult(
            "ZONE_RELATIONS",
            "Required and forbidden functional-zone relations",
            "NOT_APPLICABLE",
            ("No zone adjacency constraints are declared",),
        )

    failures: list[str] = []
    evidence: list[str] = []
    minimum_shared = building.layout.door_width_mm
    placements = layout.placements
    for name, left_rooms, right_rooms in required:
        matches = [
            (left_id, right_id, placements[left_id].shared_boundary(placements[right_id]))
            for left_id in left_rooms
            for right_id in right_rooms
            if left_id in placements and right_id in placements
            and placements[left_id].shared_boundary(placements[right_id]) + 1e-6 >= minimum_shared
        ]
        if matches:
            best = max(matches, key=lambda item: item[2])
            evidence.append(
                f"Required zone relation {name} satisfied by {best[0]}–{best[1]} "
                f"with {best[2]:.0f} mm shared boundary"
            )
        else:
            failures.append(
                f"Required zone relation {name} has no room pair sharing {minimum_shared:.0f} mm"
            )
    for name, left_rooms, right_rooms in forbidden:
        collisions = [
            (left_id, right_id, placements[left_id].shared_boundary(placements[right_id]))
            for left_id in left_rooms
            for right_id in right_rooms
            if left_id in placements and right_id in placements
            and placements[left_id].shared_boundary(placements[right_id]) > 1e-6
        ]
        if collisions:
            failures.extend(
                f"Forbidden zone relation {name} is violated by {left_id}–{right_id} "
                f"({shared:.0f} mm shared boundary)"
                for left_id, right_id, shared in collisions
            )
        else:
            evidence.append(f"Forbidden zone relation {name} has no touching room pairs")

    return FacilityCheckResult(
        "ZONE_RELATIONS",
        "Required and forbidden functional-zone relations",
        "FAIL" if failures else "PASS",
        tuple(failures + evidence),
    )


def _equipment_check(
    building: BuildingIR,
    equipment: EquipmentLayoutResult | None,
    report: EquipmentValidationReport | None,
) -> FacilityCheckResult:
    if not building.equipment:
        return FacilityCheckResult(
            "EQUIPMENT_CLEARANCE",
            "Equipment footprints and service clearances are valid",
            "NOT_APPLICABLE",
            ("No equipment is declared",),
        )
    if equipment is None or report is None:
        return FacilityCheckResult(
            "EQUIPMENT_CLEARANCE",
            "Equipment footprints and service clearances are valid",
            "UNKNOWN",
            ("Equipment evidence was not supplied",),
        )
    if report.ok:
        return FacilityCheckResult(
            "EQUIPMENT_CLEARANCE",
            "Equipment footprints and service clearances are valid",
            "PASS",
            (f"Validated {len(equipment.placements)} equipment placement(s), including wall and clearance checks",),
        )
    return FacilityCheckResult(
        "EQUIPMENT_CLEARANCE",
        "Equipment footprints and service clearances are valid",
        "FAIL",
        tuple(f"{issue.code}: {issue.message}" for issue in report.issues),
    )


def _flow_check(
    building: BuildingIR,
    routes: FlowRoutingResult | None,
    report: FlowValidationReport | None,
) -> FacilityCheckResult:
    if not building.flows:
        return FacilityCheckResult(
            "FLOW_COMPLETENESS",
            "Declared people/material/service flows have valid routes",
            "NOT_APPLICABLE",
            ("No flows are declared",),
        )
    if routes is None or report is None:
        return FacilityCheckResult(
            "FLOW_COMPLETENESS",
            "Declared people/material/service flows have valid routes",
            "UNKNOWN",
            ("Flow routing evidence was not supplied",),
        )
    if report.ok:
        return FacilityCheckResult(
            "FLOW_COMPLETENESS",
            "Declared people/material/service flows have valid routes",
            "PASS",
            (f"Validated {len(routes.routes)} derived route(s) for {len(building.flows)} flow declaration(s)",),
        )
    return FacilityCheckResult(
        "FLOW_COMPLETENESS",
        "Declared people/material/service flows have valid routes",
        "FAIL",
        tuple(f"{issue.code}: {issue.message}" for issue in report.issues),
    )


def _flow_separation_check(
    building: BuildingIR,
    routes: FlowRoutingResult | None,
    profile: FacilityProfile,
) -> FacilityCheckResult:
    if not building.flows:
        return FacilityCheckResult(
            "FLOW_TYPE_SEPARATION",
            "Incompatible flow types do not share intermediate rooms",
            "NOT_APPLICABLE",
            ("No flows are declared",),
        )
    if not profile.incompatible_flow_type_pairs:
        return FacilityCheckResult(
            "FLOW_TYPE_SEPARATION",
            "Incompatible flow types do not share intermediate rooms",
            "NOT_APPLICABLE",
            ("The selected facility profile declares no incompatible flow types",),
        )
    if routes is None or not routes.routes:
        return FacilityCheckResult(
            "FLOW_TYPE_SEPARATION",
            "Incompatible flow types do not share intermediate rooms",
            "NOT_APPLICABLE",
            ("No derived route pair is available; route completeness is reported separately",),
        )

    flow_types = {flow.id: flow.type.strip().lower() for flow in building.flows}
    failures: list[str] = []
    evidence: list[str] = []
    for left_type, right_type in profile.incompatible_flow_type_pairs:
        left_routes = [route for route in routes.routes if flow_types.get(route.flow_id) == left_type]
        right_routes = [route for route in routes.routes if flow_types.get(route.flow_id) == right_type]
        if not left_routes or not right_routes:
            evidence.append(f"No route pair is applicable for incompatible types {left_type}/{right_type}")
            continue
        pair_failures = 0
        for left in left_routes:
            for right in right_routes:
                shared_intermediate = (set(left.room_path[1:-1]) & set(right.room_path[1:-1]))
                if shared_intermediate:
                    pair_failures += 1
                    failures.append(
                        f"Incompatible flows {left.flow_id} and {right.flow_id} share intermediate room(s): "
                        + ", ".join(sorted(shared_intermediate))
                    )
        if not pair_failures:
            evidence.append(f"Incompatible flow types {left_type}/{right_type} use separated intermediate rooms")
    return FacilityCheckResult(
        "FLOW_TYPE_SEPARATION",
        "Incompatible flow types do not share intermediate rooms",
        "FAIL" if failures else "PASS",
        tuple(failures + evidence),
    )

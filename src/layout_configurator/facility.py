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
from typing import Any, Iterable, Literal, Mapping

from .building import BuildingIR
from .equipment import EquipmentLayoutResult, EquipmentValidationReport
from .flows import FlowRoutingResult, FlowValidationReport
from .models import LayoutResult
from .validation import ValidationReport, validate_layout


FacilityCheckStatus = Literal["PASS", "FAIL", "NOT_APPLICABLE", "UNKNOWN"]
CoordinationSeverity = Literal["ERROR", "WARNING", "INFO"]
FacilityRuleKind = Literal[
    "flow_separation",
    "required_flow_types",
    "process_sequence",
    "required_zone_fields",
    "airlock_presence",
    "pressure_cascade",
]
_FACILITY_RULE_KINDS = frozenset(
    {
        "flow_separation",
        "required_flow_types",
        "process_sequence",
        "required_zone_fields",
        "airlock_presence",
        "pressure_cascade",
    }
)
DEFAULT_FACILITY_PROFILE_PATH = Path(__file__).resolve().parents[2] / "rules" / "default_facility.yaml"
DEFAULT_FACILITY_PROFILE_SCHEMA_PATH = Path(__file__).resolve().parents[2] / "schemas" / "facility_profile.schema.json"


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
class FacilityRule:
    """One auditable project/domain rule declared by a YAML rule pack."""

    id: str
    kind: FacilityRuleKind
    title: str
    source: str
    edition: str
    effective_date: str
    evidence: tuple[str, ...]
    parameters: Mapping[str, Any] = field(default_factory=dict)
    severity: CoordinationSeverity = "ERROR"

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any], index: int) -> "FacilityRule":
        if not isinstance(raw, Mapping):
            raise ValueError(f"Facility profile rules[{index}] must be an object")
        rule_id = _required_profile_text(raw.get("id"), f"rules[{index}].id")
        kind = _required_profile_text(raw.get("kind"), f"rules[{rule_id}].kind").lower()
        if kind not in _FACILITY_RULE_KINDS:
            raise ValueError(f"rules[{rule_id}].kind has unsupported value {kind!r}")
        title = _required_profile_text(raw.get("title"), f"rules[{rule_id}].title")
        source = _required_profile_text(raw.get("source"), f"rules[{rule_id}].source")
        edition = _required_profile_text(raw.get("edition"), f"rules[{rule_id}].edition")
        effective_date = _required_profile_text(
            raw.get("effective_date"), f"rules[{rule_id}].effective_date"
        )
        evidence_raw = raw.get("evidence")
        if isinstance(evidence_raw, str):
            evidence = (evidence_raw.strip(),)
        elif isinstance(evidence_raw, (list, tuple)):
            evidence = tuple(str(item).strip() for item in evidence_raw if str(item).strip())
        else:
            raise ValueError(f"rules[{rule_id}].evidence must be a string or list")
        if not evidence:
            raise ValueError(f"rules[{rule_id}].evidence must not be empty")
        parameters = raw.get("parameters", {})
        if parameters is None:
            parameters = {}
        if not isinstance(parameters, Mapping):
            raise ValueError(f"rules[{rule_id}].parameters must be an object")
        severity = str(raw.get("severity", "ERROR")).strip().upper()
        if severity not in {"ERROR", "WARNING", "INFO"}:
            raise ValueError(f"rules[{rule_id}].severity has unsupported value {severity!r}")
        return cls(
            id=rule_id,
            kind=kind,  # type: ignore[arg-type]
            title=title,
            source=source,
            edition=edition,
            effective_date=effective_date,
            evidence=evidence,
            parameters={str(key): value for key, value in parameters.items()},
            severity=severity,  # type: ignore[arg-type]
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "title": self.title,
            "source": self.source,
            "edition": self.edition,
            "effective_date": self.effective_date,
            "evidence": list(self.evidence),
            "parameters": dict(self.parameters),
            "severity": self.severity,
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
    rules: tuple[FacilityRule, ...] = ()

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
        pairs = _parse_flow_pairs(pairs_raw, "incompatible_flow_type_pairs")
        provenance = raw.get("provenance", {})
        if provenance is None:
            provenance = {}
        if not isinstance(provenance, Mapping):
            raise ValueError("Facility profile provenance must be an object")
        rules_raw = raw.get("rules", ())
        if rules_raw is None:
            rules_raw = ()
        if not isinstance(rules_raw, (list, tuple)):
            raise ValueError("Facility profile rules must be a list")
        rules: list[FacilityRule] = []
        for index, rule_raw in enumerate(rules_raw):
            rule = FacilityRule.from_mapping(rule_raw, index)
            if any(existing.id == rule.id for existing in rules):
                raise ValueError(f"Facility profile rule id {rule.id!r} is duplicated")
            rules.append(rule)
        flow_rule = next((rule for rule in rules if rule.kind == "flow_separation"), None)
        if flow_rule is not None and "incompatible_flow_type_pairs" in flow_rule.parameters:
            pairs = _parse_flow_pairs(
                flow_rule.parameters["incompatible_flow_type_pairs"],
                f"rules[{flow_rule.id}].parameters.incompatible_flow_type_pairs",
            )
        if not rules and pairs:
            rules.append(
                FacilityRule(
                    id="FLOW_TYPE_SEPARATION",
                    kind="flow_separation",
                    title="Incompatible flow types do not share intermediate rooms",
                    source=str(provenance.get("source", "project-profile")),
                    edition=str(provenance.get("edition", raw.get("version", "not-stated"))),
                    effective_date=str(provenance.get("effective_date", "not-stated")),
                    evidence=(
                        "Legacy flow_separation declaration is retained as a project coordination policy; "
                        "it is not a regulatory verdict.",
                    ),
                    parameters={
                        "incompatible_flow_type_pairs": [list(pair) for pair in pairs]
                    },
                )
            )
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
            rules=tuple(rules),
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
            "rules": [rule.to_dict() for rule in self.rules],
            "drawing": self.drawing.to_dict(),
        }


def default_facility_profile() -> FacilityProfile:
    """Return the YAML-backed compatibility profile used by ``generate-building``."""

    if DEFAULT_FACILITY_PROFILE_PATH.exists():
        return load_facility_profile(DEFAULT_FACILITY_PROFILE_PATH)
    raise FileNotFoundError(
        f"YAML-backed default facility profile is missing: {DEFAULT_FACILITY_PROFILE_PATH}"
    )


@dataclass(frozen=True)
class FacilityCheckResult:
    id: str
    title: str
    status: FacilityCheckStatus
    evidence: tuple[str, ...]
    rule_id: str | None = None
    source: str | None = None
    edition: str | None = None
    effective_date: str | None = None
    severity: CoordinationSeverity | None = None
    rule_evidence: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return self.status in {"PASS", "NOT_APPLICABLE"}

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "id": self.id,
            "title": self.title,
            "status": self.status,
            "evidence": list(self.evidence),
        }
        if self.rule_id is not None:
            payload["rule"] = {
                "id": self.rule_id,
                "source": self.source,
                "edition": self.edition,
                "effective_date": self.effective_date,
                "severity": self.severity,
            }
        return payload


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
    if isinstance(raw, Mapping) and "rules" in raw:
        from .schema import validate_mapping

        schema_issues = validate_mapping(raw, DEFAULT_FACILITY_PROFILE_SCHEMA_PATH)
        if schema_issues:
            details = "; ".join(f"{issue.path}: {issue.message}" for issue in schema_issues)
            raise ValueError(f"Facility profile does not match schema: {details}")
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
    flow_separation_rule = next(
        (rule for rule in profile.rules if rule.kind == "flow_separation"),
        None,
    )
    profile_checks = list(_profile_rule_checks(building, profile))
    checks = [
        _geometry_check(geometry_report),
        _zone_check(building, layout),
        _equipment_check(building, equipment, equipment_report),
        _flow_check(building, flow_routes, flow_report),
        _flow_separation_check(building, flow_routes, profile, flow_separation_rule),
        *profile_checks,
    ]
    issues = _coordination_issues(
        checks,
        geometry_report,
        equipment_report,
        flow_routes,
        flow_report,
        profile_checks=profile_checks,
    )
    return FacilityValidationReport(profile=profile, checks=tuple(checks), issues=issues)


def _coordination_issues(
    checks: list[FacilityCheckResult],
    geometry_report: ValidationReport,
    equipment_report: EquipmentValidationReport | None,
    flow_routes: FlowRoutingResult | None,
    flow_report: FlowValidationReport | None,
    *,
    profile_checks: Iterable[FacilityCheckResult] = (),
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
                    source=separation_check.source or "FLOW_TYPE_SEPARATION",
                )
            )

    for check in profile_checks:
        if check.status != "FAIL":
            continue
        for message in check.evidence:
            if message in check.rule_evidence:
                continue
            issues.append(
                _issue(
                    code=check.id,
                    title=check.title,
                    message=message,
                    source=check.source or check.id,
                    severity=check.severity or "ERROR",
                )
            )

    for check in checks:
        if check.status != "UNKNOWN":
            continue
        issues.append(
            _issue(
                code=f"{check.id}_UNKNOWN",
                title=check.title if check.rule_id is not None else "Facility check could not be evaluated",
                message="; ".join(check.evidence),
                source=check.source or check.id,
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


def _required_profile_text(value: Any, field_name: str) -> str:
    if value in (None, ""):
        raise ValueError(f"Facility profile {field_name} must be a non-empty string")
    text = str(value).strip()
    if not text:
        raise ValueError(f"Facility profile {field_name} must be a non-empty string")
    return text


def _parse_flow_pairs(value: Any, field_name: str) -> list[tuple[str, str]]:
    if value is None:
        value = ()
    if not isinstance(value, (list, tuple)):
        raise ValueError(f"Facility profile {field_name} must be a list")
    pairs: list[tuple[str, str]] = []
    for index, pair in enumerate(value):
        if not isinstance(pair, (list, tuple)) or len(pair) != 2:
            raise ValueError(f"{field_name}[{index}] must contain two flow types")
        left, right = (str(item).strip().lower() for item in pair)
        if not left or not right or left == right:
            raise ValueError(f"{field_name}[{index}] must contain two distinct types")
        normalised = tuple(sorted((left, right)))
        if normalised not in pairs:
            pairs.append(normalised)
    return pairs


def _parse_directed_connections(value: Any, field_name: str) -> tuple[tuple[str, str], ...]:
    if value is None:
        value = ()
    if not isinstance(value, (list, tuple)):
        raise ValueError(f"{field_name} must be a list")
    connections: list[tuple[str, str]] = []
    for index, pair in enumerate(value):
        if not isinstance(pair, (list, tuple)) or len(pair) != 2:
            raise ValueError(f"{field_name}[{index}] must contain two stage ids")
        left, right = (str(item).strip().lower() for item in pair)
        if not left or not right or left == right:
            raise ValueError(f"{field_name}[{index}] must contain two distinct stage ids")
        connection = (left, right)
        if connection not in connections:
            connections.append(connection)
    return tuple(connections)


def _rule_check(
    rule: FacilityRule,
    status: FacilityCheckStatus,
    details: Iterable[str],
    *,
    check_id: str | None = None,
    title: str | None = None,
) -> FacilityCheckResult:
    return FacilityCheckResult(
        id=check_id or rule.id,
        title=title or rule.title,
        status=status,
        evidence=tuple(rule.evidence) + tuple(details),
        rule_id=rule.id,
        source=rule.source,
        edition=rule.edition,
        effective_date=rule.effective_date,
        severity=rule.severity,
        rule_evidence=rule.evidence,
    )


def _rule_string_list(rule: FacilityRule, key: str) -> tuple[tuple[str, ...], str | None]:
    raw = rule.parameters.get(key)
    if not isinstance(raw, (list, tuple)):
        return (), f"Rule parameter {key!r} must be a list"
    values = tuple(str(item).strip().lower() for item in raw if str(item).strip())
    if not values:
        return (), f"Rule parameter {key!r} must not be empty"
    return values, None


def _optional_rule_string_list(rule: FacilityRule, key: str) -> tuple[tuple[str, ...] | None, str | None]:
    if key not in rule.parameters:
        return None, None
    values, error = _rule_string_list(rule, key)
    return values, error


def _applicable_zones(building: BuildingIR, zone_types: tuple[str, ...] | None) -> tuple[Any, ...]:
    if zone_types is None:
        return tuple(building.zones)
    allowed = set(zone_types)
    return tuple(zone for zone in building.zones if zone.type.strip().lower() in allowed)


def _profile_rule_checks(building: BuildingIR, profile: FacilityProfile) -> tuple[FacilityCheckResult, ...]:
    checks: list[FacilityCheckResult] = []
    for rule in profile.rules:
        if rule.kind == "flow_separation":
            continue
        if rule.kind == "required_flow_types":
            checks.append(_required_flow_types_check(building, rule))
        elif rule.kind == "process_sequence":
            checks.append(_process_sequence_check(building, rule))
        elif rule.kind == "required_zone_fields":
            checks.append(_required_zone_fields_check(building, rule))
        elif rule.kind == "airlock_presence":
            checks.append(_airlock_presence_check(building, rule))
        elif rule.kind == "pressure_cascade":
            checks.append(_pressure_cascade_check(building, rule))
    return tuple(checks)


def _required_flow_types_check(building: BuildingIR, rule: FacilityRule) -> FacilityCheckResult:
    expected, error = _rule_string_list(rule, "flow_types")
    if error:
        return _rule_check(rule, "UNKNOWN", (error,))
    present = {flow.type.strip().lower() for flow in building.flows}
    missing = tuple(flow_type for flow_type in expected if flow_type not in present)
    if missing:
        return _rule_check(
            rule,
            "FAIL",
            (
                "Missing declared flow type(s): " + ", ".join(missing),
                "Present declared flow type(s): " + (", ".join(sorted(present)) or "none"),
            ),
        )
    return _rule_check(
        rule,
        "PASS",
        ("All required flow types are declared: " + ", ".join(expected),),
    )


def _process_sequence_check(building: BuildingIR, rule: FacilityRule) -> FacilityCheckResult:
    stages, stage_error = _rule_string_list(rule, "sequence")
    if stage_error:
        return _rule_check(rule, "UNKNOWN", (stage_error,))
    if len(stages) < 2:
        return _rule_check(
            rule,
            "UNKNOWN",
            ("Rule parameter 'sequence' must contain at least two stages",),
        )
    try:
        branches = _parse_directed_connections(
            rule.parameters.get("branches", ()),
            f"rules[{rule.id}].parameters.branches",
        )
    except ValueError as exc:
        return _rule_check(rule, "UNKNOWN", (str(exc),))

    flows_by_stage: dict[str, list[Any]] = {}
    for flow in building.flows:
        if flow.stage:
            flows_by_stage.setdefault(flow.stage.strip().lower(), []).append(flow)
    connections = tuple(zip(stages, stages[1:])) + branches
    referenced_stages = tuple(
        dict.fromkeys(stage for connection in connections for stage in connection)
    )
    missing = tuple(stage for stage in referenced_stages if stage not in flows_by_stage)
    if missing:
        return _rule_check(
            rule,
            "UNKNOWN",
            (
                "Flow stage evidence is missing for: " + ", ".join(missing),
                "Declare FlowSpec.stage before evaluating the process sequence",
            ),
        )

    failures: list[str] = []
    for left_stage, right_stage in connections:
        connected = any(
            set(left.to_ids) & set(right.from_ids)
            for left in flows_by_stage[left_stage]
            for right in flows_by_stage[right_stage]
        )
        if not connected:
            failures.append(
                f"Process stages {left_stage} and {right_stage} have no declared endpoint transition"
            )
    if failures:
        return _rule_check(rule, "FAIL", tuple(failures))
    return _rule_check(
        rule,
        "PASS",
        (f"Validated {len(connections)} declared process-stage transition(s)",),
    )


def _required_zone_fields_check(building: BuildingIR, rule: FacilityRule) -> FacilityCheckResult:
    zone_types, type_error = _optional_rule_string_list(rule, "zone_types")
    fields, field_error = _rule_string_list(rule, "fields")
    if type_error or field_error:
        return _rule_check(rule, "UNKNOWN", tuple(error for error in (type_error, field_error) if error))
    supported_fields = {"cleanroom_class", "pressure_pa", "parent_zone_id", "airlock"}
    unsupported = tuple(field_name for field_name in fields if field_name not in supported_fields)
    if unsupported:
        return _rule_check(
            rule,
            "UNKNOWN",
            ("Unsupported required zone field(s): " + ", ".join(unsupported),),
        )
    zones = _applicable_zones(building, zone_types)
    if not zones:
        return _rule_check(
            rule,
            "UNKNOWN",
            ("No zones match the rule's declared zone_types",),
        )
    missing: list[str] = []
    for zone in zones:
        for field_name in fields:
            value = getattr(zone, field_name)
            if field_name == "airlock":
                absent = value is not True
            else:
                absent = value in (None, "")
            if absent:
                missing.append(f"{zone.id}.{field_name}")
    if missing:
        return _rule_check(
            rule,
            "FAIL",
            ("Missing required zone field(s): " + ", ".join(missing),),
        )
    return _rule_check(
        rule,
        "PASS",
        (f"Validated required fields on {len(zones)} applicable zone(s)",),
    )


def _airlock_presence_check(building: BuildingIR, rule: FacilityRule) -> FacilityCheckResult:
    zone_types, type_error = _optional_rule_string_list(rule, "zone_types")
    if type_error:
        return _rule_check(rule, "UNKNOWN", (type_error,))
    raw_minimum = rule.parameters.get("minimum_count")
    if raw_minimum is None:
        required = rule.parameters.get("required", True)
        if not isinstance(required, bool):
            return _rule_check(rule, "UNKNOWN", ("Rule parameter 'required' must be a boolean",))
        minimum_count = 1 if required else 0
    else:
        try:
            minimum_count = int(raw_minimum)
        except (TypeError, ValueError):
            return _rule_check(rule, "UNKNOWN", ("Rule parameter 'minimum_count' must be an integer",))
    if minimum_count < 0:
        return _rule_check(rule, "UNKNOWN", ("Rule parameter 'minimum_count' must be non-negative",))
    zones = _applicable_zones(building, zone_types)
    airlocks = tuple(zone.id for zone in zones if zone.airlock)
    if len(airlocks) < minimum_count:
        return _rule_check(
            rule,
            "FAIL",
            (
                f"Declared airlock zone count {len(airlocks)} is below project requirement {minimum_count}",
                "Airlock zones found: " + (", ".join(airlocks) or "none"),
            ),
        )
    return _rule_check(
        rule,
        "PASS",
        (f"Declared airlock zone count {len(airlocks)} meets project requirement {minimum_count}",),
    )


def _pressure_cascade_check(building: BuildingIR, rule: FacilityRule) -> FacilityCheckResult:
    zone_types, type_error = _optional_rule_string_list(rule, "zone_types")
    if type_error:
        return _rule_check(rule, "UNKNOWN", (type_error,))
    direction = str(rule.parameters.get("direction", "parent_gt_child")).strip().lower()
    if direction not in {"parent_gt_child", "child_gt_parent"}:
        return _rule_check(rule, "UNKNOWN", ("Rule parameter 'direction' must be parent_gt_child or child_gt_parent",))
    minimum_delta = rule.parameters.get("minimum_delta_pa")
    if minimum_delta is not None:
        try:
            minimum_delta = float(minimum_delta)
        except (TypeError, ValueError):
            return _rule_check(rule, "UNKNOWN", ("Rule parameter 'minimum_delta_pa' must be a number",))
        if minimum_delta < 0:
            return _rule_check(rule, "UNKNOWN", ("Rule parameter 'minimum_delta_pa' must be non-negative",))
    zones = _applicable_zones(building, zone_types)
    zone_by_id = {zone.id: zone for zone in zones}
    edges = tuple(
        (zone_by_id[zone.parent_zone_id], zone)
        for zone in zones
        if zone.parent_zone_id in zone_by_id
    )
    if not edges:
        return _rule_check(
            rule,
            "UNKNOWN",
            ("No parent-child pressure edges are declared among applicable zones",),
        )
    failures: list[str] = []
    unknown: list[str] = []
    for parent, child in edges:
        if parent.pressure_pa is None or child.pressure_pa is None:
            unknown.append(f"Pressure evidence is missing for zone edge {parent.id}->{child.id}")
            continue
        delta = parent.pressure_pa - child.pressure_pa
        signed_delta = delta if direction == "parent_gt_child" else -delta
        if signed_delta <= 0 or (minimum_delta is not None and signed_delta + 1e-9 < minimum_delta):
            threshold = "strict ordering" if minimum_delta is None else f"delta >= {minimum_delta:g} Pa"
            failures.append(
                f"Pressure cascade {direction} is violated on {parent.id}->{child.id}: "
                f"parent={parent.pressure_pa:g} Pa, child={child.pressure_pa:g} Pa, expected {threshold}"
            )
    if failures:
        return _rule_check(rule, "FAIL", tuple(failures + unknown))
    if unknown:
        return _rule_check(rule, "UNKNOWN", tuple(unknown))
    return _rule_check(
        rule,
        "PASS",
        (f"Validated pressure ordering on {len(edges)} parent-child zone edge(s)",),
    )


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
    rule: FacilityRule | None = None,
) -> FacilityCheckResult:
    check_id = "FLOW_TYPE_SEPARATION"
    title = "Incompatible flow types do not share intermediate rooms"
    pairs = profile.incompatible_flow_type_pairs
    if rule is not None:
        rule_pairs = rule.parameters.get("incompatible_flow_type_pairs")
        if rule_pairs is not None:
            try:
                pairs = tuple(_parse_flow_pairs(rule_pairs, f"rules[{rule.id}].parameters.incompatible_flow_type_pairs"))
            except ValueError as exc:
                return _rule_check(rule, "UNKNOWN", (str(exc),), check_id=check_id, title=title)
    if not building.flows:
        if rule is not None:
            return _rule_check(rule, "NOT_APPLICABLE", ("No flows are declared",), check_id=check_id, title=title)
        return FacilityCheckResult(check_id, title, "NOT_APPLICABLE", ("No flows are declared",))
    if not pairs:
        if rule is not None:
            return _rule_check(
                rule,
                "NOT_APPLICABLE",
                ("The selected facility profile declares no incompatible flow types",),
                check_id=check_id,
                title=title,
            )
        return FacilityCheckResult(
            check_id,
            title,
            "NOT_APPLICABLE",
            ("The selected facility profile declares no incompatible flow types",),
        )
    if routes is None or not routes.routes:
        if rule is not None:
            return _rule_check(
                rule,
                "NOT_APPLICABLE",
                ("No derived route pair is available; route completeness is reported separately",),
                check_id=check_id,
                title=title,
            )
        return FacilityCheckResult(
            check_id,
            title,
            "NOT_APPLICABLE",
            ("No derived route pair is available; route completeness is reported separately",),
        )

    flow_types = {flow.id: flow.type.strip().lower() for flow in building.flows}
    failures: list[str] = []
    evidence: list[str] = []
    for left_type, right_type in pairs:
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
    status: FacilityCheckStatus = "FAIL" if failures else "PASS"
    details = tuple(failures + evidence)
    if rule is not None:
        return _rule_check(rule, status, details, check_id=check_id, title=title)
    return FacilityCheckResult(check_id, title, status, details)

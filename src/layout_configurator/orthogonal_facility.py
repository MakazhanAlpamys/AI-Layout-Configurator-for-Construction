"""Independent declared facility policies over actual canonical footprints.

PASS covers the supplied room/equipment/route and declared profile predicates.
It is separate from complete artifact exchange and engineering acceptance.
The existing rectangular validator and dispatcher remain unchanged.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, replace
import hashlib
import json

from .building import BuildingIR
from .equipment import EquipmentLayoutResult
from .facility import (
    DEFAULT_FACILITY_PROFILE_SCHEMA_PATH, CoordinationIssue, FacilityCheckResult,
    FacilityProfile, _flow_separation_check, _profile_rule_checks,
)
from .flows import FlowRoutingResult
from .models import LayoutResult, Rect
from .orthogonal import OrthogonalPolygon
from .orthogonal_equipment import validate_orthogonal_door_approaches
from .orthogonal_flows import validate_orthogonal_flow_routes
from .orthogonal_validation import validate_orthogonal_equipment, validate_orthogonal_layout
from .result_geometry import parse_layout_result
from .schema import normalize_building_mapping, validate_mapping


@dataclass(frozen=True)
class OrthogonalFacilityReport:
    profile: FacilityProfile
    checks: tuple[FacilityCheckResult, ...]
    source: dict
    local_validation: dict
    issues: tuple[CoordinationIssue, ...] = ()

    @property
    def status(self):
        statuses = {check.status for check in self.checks}
        return "FAIL" if "FAIL" in statuses else "UNKNOWN" if "UNKNOWN" in statuses else "PASS"

    @property
    def ok(self):
        return self.status == "PASS"

    def to_dict(self):
        return {"status": self.status, "ok": self.ok,
                "scope": "Independent actual geometry and all declared facility/profile predicates only",
                "profile": self.profile.to_dict(), "checks": [check.to_dict() for check in self.checks],
                "source": dict(self.source), "local_validation": deepcopy(self.local_validation),
                "issues": [issue.to_dict() for issue in self.issues],
                "full_facility_validation": {"status": "UNKNOWN", "ok": False,
                    "evidence": ["Complete polygon bundle dispatch, artifact exchange and unsupported source components require separate gates"]},
                "engineering_acceptance": "PENDING"}


def validate_orthogonal_building(building: BuildingIR, layout: LayoutResult,
                                  equipment: EquipmentLayoutResult | None,
                                  routing: FlowRoutingResult | None, *,
                                  profile: FacilityProfile) -> OrthogonalFacilityReport:
    """Recompute local predicates; never accept a caller-supplied QA verdict.

    Missing declared placements/routes yield UNKNOWN. Supplied empty or forged
    results are independently checked and can fail. Required zone contacts use
    one contiguous actual shared edge, rather than sums or bounding rectangles.
    Rules retain their unchanged source provenance, including UNKNOWN evidence.
    """
    if not isinstance(building, BuildingIR) or not isinstance(layout, LayoutResult) or not isinstance(profile, FacilityProfile):
        raise ValueError("Canonical BuildingIR, LayoutResult and FacilityProfile required")
    if any(len(items) > 64 for items in (building.layout.rooms, building.zones, building.equipment, building.flows)):
        raise ValueError("Orthogonal facility source limit is 64 rooms/zones/equipment/flows")
    if not 1 <= len(profile.rules) <= 1000:
        raise ValueError("Orthogonal facility profile requires 1..1000 declared rules")
    if equipment is not None and (not isinstance(equipment, EquipmentLayoutResult) or len(equipment.placements) > 64):
        raise ValueError("Orthogonal facility equipment requires at most 64 canonical placements")
    if routing is not None and (not isinstance(routing, FlowRoutingResult) or len(routing.routes) > 256):
        raise ValueError("Orthogonal facility routing requires at most 256 canonical routes")
    raw_profile = profile.to_dict()
    errors = validate_mapping(raw_profile, DEFAULT_FACILITY_PROFILE_SCHEMA_PATH)
    if errors:
        raise ValueError("Facility profile does not match schema: " + "; ".join(f"{error.path}: {error.message}" for error in errors))
    # Validate before normalization can convert malformed values to strings.
    building = normalize_building_mapping(building.to_dict())
    layout = parse_layout_result(layout.to_dict())
    normalized_profile = FacilityProfile.from_mapping(raw_profile)
    if normalized_profile.to_dict() != raw_profile:
        raise ValueError("Facility profile serialization must already be canonical")
    source = {name + "_sha256": _hash(value) for name, value in (
        ("building", building.to_dict()), ("layout", layout.to_dict()),
        ("equipment", _equipment_source(building, equipment)),
        ("routing", routing.to_dict() if routing is not None else None), ("profile", raw_profile))}
    rooms = validate_orthogonal_layout(building.layout, layout)
    local = {"rooms": rooms.to_dict()}
    checks = [_report_check("FACILITY_GEOMETRY", "Actual room geometry and openings", local["rooms"]),
              _zone_check(building, layout)]
    if equipment is None and building.equipment:
        for name, title in (("EQUIPMENT_CLEARANCE", "Equipment footprints and directional service"),
                            ("DOOR_EQUIPMENT_APPROACHES", "Actual full-width doors remain accessible")):
            checks.append(FacilityCheckResult(name, title, "UNKNOWN", ("Declared equipment evidence was not supplied",)))
        local.update(equipment={"status": "UNKNOWN"}, door_approaches={"status": "UNKNOWN"})
    else:
        items = equipment if equipment is not None else EquipmentLayoutResult(())
        item_report = validate_orthogonal_equipment(building, layout, items)
        door_report = validate_orthogonal_door_approaches(building, layout, items)
        local.update(equipment=item_report.to_dict(), door_approaches=door_report.to_dict())
        checks.extend((_report_check("EQUIPMENT_CLEARANCE", "Equipment footprints and directional service", local["equipment"]),
                       _report_check("DOOR_EQUIPMENT_APPROACHES", "Actual full-width doors remain accessible", local["door_approaches"])))
    verified_routes = None
    if routing is None:
        status = "UNKNOWN" if building.flows else "NOT_APPLICABLE"
        message = "Declared routing evidence was not supplied" if building.flows else "No flows are declared"
        checks.append(FacilityCheckResult("FLOW_COMPLETENESS", "Actual declared flow routes", status, (message,)))
        local["routes"] = {"status": status}
    else:
        route_report = validate_orthogonal_flow_routes(building, layout, routing, equipment)
        local["routes"] = route_report.to_dict()
        checks.append(_report_check("FLOW_COMPLETENESS", "Actual declared flow routes", local["routes"]))
        if route_report.ok:
            verified_routes = routing
    separation_rules = [rule for rule in profile.rules if rule.kind == "flow_separation"]
    for index, rule in enumerate(separation_rules or [None]):
        check = _flow_separation_check(building, verified_routes, profile, rule)
        if check.status == "NOT_APPLICABLE" and building.flows and verified_routes is None:
            # Missing/unverified routes cannot establish an applicable separation.
            check = replace(check, status="UNKNOWN", evidence=("Actual route geometry has not passed independent checking; separation is unresolved",))
        if index:
            check = replace(check, id=f"FLOW_TYPE_SEPARATION:{rule.id}")
        checks.append(check)
    checks.extend(_profile_rule_checks(building, profile, verified_routes))
    issues = tuple(CoordinationIssue(
        issue_id="orthogonal-" + hashlib.sha256(check.id.encode()).hexdigest()[:20],
        code=check.id, title=check.title, message="; ".join(check.evidence),
        severity="ERROR" if check.status == "FAIL" else "WARNING", source=check.source or "orthogonal-facility-predicates")
        for check in checks if check.status in {"FAIL", "UNKNOWN"})
    return OrthogonalFacilityReport(profile, tuple(checks), source, local, issues)


def _report_check(identifier, title, report):
    evidence = [str(message) for message in report.get("unknown", ())]
    for issue in report.get("issues", ()):
        evidence.append(f"{issue.get('code', 'issue')}: {issue.get('message', json.dumps(issue, sort_keys=True))}")
    for check in report.get("checks", ()):
        if check["status"] not in {"PASS", "NOT_APPLICABLE"}:
            evidence.extend(f"{check['id']}: {message}" for message in check["evidence"])
    return FacilityCheckResult(identifier, title, report["status"], tuple(evidence[:128]) or
                               ("Independent actual-source predicates passed",))


def _zone_check(building, layout):
    required = building.zone_relation_groups("required_adjacency")
    forbidden = building.zone_relation_groups("forbidden_adjacency")
    title = "Required and forbidden functional-zone relations on actual footprints"
    if not required and not forbidden:
        return FacilityCheckResult("ZONE_RELATIONS", title, "NOT_APPLICABLE", ("No zone adjacency constraints are declared",))
    shapes = {identifier: OrthogonalPolygon.from_rect(shape) if isinstance(shape, Rect) else shape
              for identifier, shape in layout.placements.items()}
    failures, evidence = [], []
    width = building.layout.door_width_mm
    for name, left_rooms, right_rooms in required:
        matches = []
        for left in left_rooms:
            for right in right_rooms:
                if left == right or left not in shapes or right not in shapes:
                    continue
                for first, second in shapes[left].shared_edges(shapes[right]):
                    span = abs(second[0] - first[0]) + abs(second[1] - first[1])
                    if span + 1e-6 >= width:
                        matches.append((span, left, right))
        if matches:
            span, left, right = max(matches)
            evidence.append(f"Required zone relation {name}: {left}/{right} have one contiguous {span:g} mm shared edge")
        else:
            failures.append(f"Required zone relation {name} has no actual room pair with one contiguous {width:g} mm shared edge")
    for name, left_rooms, right_rooms in forbidden:
        collisions = [(left, right) for left in left_rooms for right in right_rooms
                      if left != right and left in shapes and right in shapes and shapes[left].shared_edges(shapes[right])]
        if collisions:
            failures.extend(f"Forbidden zone relation {name}: actual footprints {left}/{right} share an edge" for left, right in collisions)
        else:
            evidence.append(f"Forbidden zone relation {name}: no actual touching room pairs")
    return FacilityCheckResult("ZONE_RELATIONS", title, "FAIL" if failures else "PASS", tuple(failures + evidence))


def _hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
        allow_nan=False, separators=(",", ":")).encode()).hexdigest()


def _equipment_source(building, equipment):
    if equipment is None:
        return None
    specs = {item.id: item for item in building.equipment}
    values = []
    for item in equipment.placements:
        if item.equipment_id in specs:
            values.append(item.to_dict(specs[item.equipment_id]))
        else:
            # Preserve rejected geometry without inventing an undeclared service
            # envelope. Independent identity checks will report the failure.
            values.append({"equipment_id": item.equipment_id, "room_id": item.room_id,
                           "x": item.rect.x, "y": item.rect.y, "width": item.rect.width,
                           "depth": item.rect.height, "rotated": item.rotated, "clearance": None})
    return {"equipment": values}

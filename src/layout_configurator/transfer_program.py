"""Bounded supplied transfer permissions tied to original source identities.

This contract references canonical rooms, flows and explicit source doors. It
does not add openings, classify free-text flow types, or certify equipment or
airlock operation. PASS is limited to supplied declarations and references.
"""

from dataclasses import dataclass, fields, is_dataclass, replace
import hashlib
import json
import math

from .building import BuildingIR
from .production_program import (ProductionProgramContract, ProductionProgramLimits,
                                 _InputFailure, _json_bytes, _preflight, _schema_issues,
                                 _utf8_size, validate_production_program)
from .resources import resource_path
from .schema import DEFAULT_SCHEMA_PATH, load_building_schema, load_json_schema


@dataclass(frozen=True)
class TransferProgramLimits:
    max_program_bytes: int = 2_097_152
    max_input_bytes: int = 8_388_608
    max_nodes: int = 262_144
    max_depth: int = 16
    max_rooms: int = 64
    max_zones: int = 64
    max_equipment: int = 64
    max_flows: int = 64
    max_transfers: int = 128
    max_openings: int = 256
    max_routes: int = 256
    max_route_points: int = 1024
    max_total_points: int = 16_384
    max_geometry_checks: int = 1_000_000


@dataclass(frozen=True)
class TransferIssue:
    code: str
    path: str
    message: str
    status: str = "FAIL"
    flow_id: str | None = None
    transfer_id: str | None = None
    opening_id: str | None = None
    source_id: str | None = None
    from_id: str | None = None
    to_id: str | None = None

    def to_dict(self):
        return {field.name: getattr(self, field.name) for field in fields(self)
                if getattr(self, field.name) is not None}


@dataclass(frozen=True)
class TransferProgramContract:
    """Detached finite JSON preserves exact caller scalar and source identity."""

    _json: str

    def to_dict(self):
        return json.loads(self._json)


@dataclass(frozen=True)
class TransferProgramValidation:
    status: str
    schema_status: str
    binding_status: str
    provenance_status: str
    program: TransferProgramContract | None
    issues: tuple[TransferIssue, ...]
    counts: tuple[tuple[str, int], ...]
    building_sha256: str | None = None
    production_program_sha256: str | None = None

    def to_dict(self):
        return {"schema_version": 1, "scope": "supplied_transfer_program_contract", "status": self.status,
                "schema_status": self.schema_status, "binding_status": self.binding_status,
                "provenance_status": self.provenance_status, "engineering_acceptance": "PENDING",
                "authenticated_approval": "UNKNOWN", "usage_rights_scope": "supplied_declaration_only",
                "full_facility_validation": {"status": "UNKNOWN", "ok": False},
                "proven_building_infeasible": False, "counts": dict(self.counts),
                "source": {"building_sha256": self.building_sha256,
                           "production_program_sha256": self.production_program_sha256},
                "issues": [issue.to_dict() for issue in self.issues]}


def _hash(value):
    return hashlib.sha256(_json_bytes(value)).hexdigest()


def _limits_valid(limits):
    return type(limits) is TransferProgramLimits and all(type(getattr(limits, field.name)) is int and
                                                        1 <= getattr(limits, field.name) <= 1_000_000_000
                                                        for field in fields(limits))


def _raw_limits(limits, *, byte_limit=None):
    return replace(ProductionProgramLimits(), max_total_utf8_bytes=byte_limit or limits.max_program_bytes,
                   max_nodes=limits.max_nodes, max_depth=limits.max_depth)


def _unique_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Supplied contract JSON contains duplicate mapping keys")
        result[key] = value
    return result


def _contract_raw(value, contract_type, limits):
    if type(value) is contract_type:
        if type(value._json) is not str:
            raise ValueError("Contract requires a bounded raw JSON string")
        _utf8_size(value._json, limits.max_program_bytes, "$.contract")
        # Bound nesting before parsing a manually constructed detached contract.
        depth, quoted, escaped, atom, nodes = 0, False, False, False, 0
        for char in value._json:
            if quoted:
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == '"':
                    quoted = False
            elif char == '"':
                quoted = True
                atom = False
                nodes += 1
            elif char in "[{":
                depth += 1
                atom = False
                nodes += 1
                if depth > limits.max_depth:
                    raise _InputFailure("input_budget_exceeded", "$.contract", "Raw JSON depth budget exceeded")
            elif char in "]}":
                depth -= 1
                atom = False
            elif char in ",:" or char.isspace():
                atom = False
            elif not atom:
                atom = True
                nodes += 1
            if nodes > limits.max_nodes:
                raise _InputFailure("input_budget_exceeded", "$.contract", "Raw JSON node budget exceeded before parsing")
        value = json.loads(value._json, object_pairs_hook=_unique_pairs)
    return value


def _model_preflight(value, limits):
    """Bound typed requests before their potentially expanding to_dict calls."""
    active, nodes, size = set(), 0, 0

    def walk(item, depth):
        nonlocal nodes, size
        nodes += 1
        if nodes > limits.max_nodes or depth > limits.max_depth:
            raise _InputFailure("input_budget_exceeded", "$.request", "Typed request node/depth budget exceeded")
        if is_dataclass(item) and not isinstance(item, type):
            children = ((field.name, getattr(item, field.name)) for field in fields(item))
        elif type(item) is dict:
            children = item.items()
        elif type(item) in (tuple, list):
            children = ((None, child) for child in item)
        else:
            children = None
        if children is not None:
            if id(item) in active:
                raise _InputFailure("invalid_json_value", "$.request", "Cyclic request is not canonical data")
            active.add(id(item))
            size += 2
            for key, child in children:
                if key is not None:
                    walk(key, depth + 1)
                walk(child, depth + 1)
                size += 2
            active.remove(id(item))
        elif type(item) is str:
            size += _utf8_size(item, limits.max_input_bytes, "$.request") + 2
        elif item is None or type(item) is bool:
            size += 5
        elif type(item) in (int, float):
            if (type(item) is float and not math.isfinite(item)) or (type(item) is int and item.bit_length() > 1024):
                raise _InputFailure("invalid_json_value", "$.request", "Request numbers must be finite and bounded")
            size += len(str(item))
        else:
            raise _InputFailure("invalid_request", "$.request", "Request contains an unsupported typed value")
        if size > limits.max_input_bytes:
            raise _InputFailure("input_budget_exceeded", "$.request", "Typed request UTF-8 budget exceeded")

    walk(value, 0)


def _building_counts(building, limits):
    if not isinstance(building, BuildingIR):
        raise ValueError("Binding requires original canonical BuildingIR")
    counts = {"rooms": len(building.layout.rooms), "zones": len(building.zones),
              "equipment": len(building.equipment), "flows": len(building.flows),
              "openings": len(building.layout.doors) + len(building.layout.windows) +
                          int(building.layout.external_entry is not None)}
    if any(count > getattr(limits, "max_" + name) for name, count in counts.items()):
        raise _InputFailure("input_budget_exceeded", "$.building", "Canonical source entity budget exceeded")
    return counts


def _provenance(row, production):
    """Unknown rights and supplied attribution are not authenticated approval."""
    source = next(source for source in production["sources"] if source["id"] == row["source_id"])
    reasons = []
    if source["usage_rights"]["state"] != "PERMITTED":
        reasons.append("unknown_usage_rights" if source["usage_rights"]["state"] == "UNKNOWN" else "restricted_usage_rights")
    if row["basis"] == "assumption":
        reasons.append("assumed_transfer_declaration")
    elif row["basis"] == "supplied_confirmation":
        confirmation = production["engineer_confirmation"]
        if confirmation is None:
            reasons.append("missing_confirmation")
        elif confirmation["source_id"] != row["source_id"]:
            reasons.append("mismatched_confirmation")
    return reasons


def validate_transfer_program(raw, *, building, production_program, limits=TransferProgramLimits()):
    """Validate strict supplied declarations against exact whole-source hashes.

    Original production-program base binding is checked independently. The
    transfer binding includes any embedded program in the whole BuildingIR
    identity. No declaration is turned into geometric or engineering approval.
    """
    counts, digest, production_digest = {}, None, None

    def result(schema, binding, provenance, issues, program=None):
        states = (schema, binding, provenance)
        status = "FAIL" if "FAIL" in states else "UNKNOWN" if "UNKNOWN" in states else "PASS"
        return TransferProgramValidation(status, schema, binding, provenance, program, tuple(issues),
                                         tuple(sorted(counts.items())), digest, production_digest)

    if not _limits_valid(limits):
        return result("NOT_EVALUATED", "FAIL", "NOT_EVALUATED", [TransferIssue(
            "invalid_limits", "$", "Limits require actual bounded positive integers")])
    try:
        raw = _contract_raw(raw, TransferProgramContract, limits)
        counts = _preflight(raw, _raw_limits(limits))
        if type(raw) is dict:
            for name, maximum in (("flow_movements", limits.max_flows), ("transfers", limits.max_transfers)):
                if type(raw.get(name)) is list and len(raw[name]) > maximum:
                    raise _InputFailure("input_budget_exceeded", "$." + name, "Transfer entity budget exceeded")
        schema_issues = _schema_issues(raw, load_json_schema(resource_path("schemas/transfer_program.schema.json")))
        if schema_issues:
            return result("FAIL", "NOT_EVALUATED", "NOT_EVALUATED", [TransferIssue(
                issue.code, issue.path, issue.message) for issue in schema_issues])
        counts.update(_building_counts(building, limits))
        production = _contract_raw(production_program, ProductionProgramContract, limits)
        production_counts = _preflight(production, _raw_limits(limits))
        # Traverse all caller objects before serializing the source or contracts.
        _model_preflight((building, raw, production), limits)
        original = building.to_dict()
        original_counts = _preflight(original, _raw_limits(limits, byte_limit=limits.max_input_bytes))
        combined_bytes = counts["json_utf8_bytes"] + production_counts["json_utf8_bytes"] + original_counts["json_utf8_bytes"]
        combined_nodes = counts["json_nodes"] + production_counts["json_nodes"] + original_counts["json_nodes"]
        if combined_bytes > limits.max_input_bytes or combined_nodes > limits.max_nodes:
            raise _InputFailure("input_budget_exceeded", "$", "Combined canonical request budget exceeded")
        counts.update(combined_json_utf8_bytes=combined_bytes, combined_json_nodes=combined_nodes,
                      transfers=len(raw["transfers"]), flow_movements=len(raw["flow_movements"]))
        original_issues = _schema_issues(original, load_building_schema(include_production_program=building.production_program is not None))
        original_issues += _schema_issues(original["layout"], load_json_schema(DEFAULT_SCHEMA_PATH))
        if original_issues:
            return result("PASS", "FAIL", "NOT_EVALUATED", [TransferIssue(
                "invalid_building", "$.building", "Original source must satisfy canonical BuildingIR and LayoutIR schemas")])
        building._validate_references()
        source_ids = [room.id for room in building.layout.rooms]
        opening_ids = [opening.id for opening in (*building.layout.doors, *building.layout.windows)]
        if building.layout.external_entry is not None:
            opening_ids.append(building.layout.external_entry.id)
        if len(set(source_ids)) != len(source_ids) or len(set(opening_ids)) != len(opening_ids):
            return result("PASS", "FAIL", "NOT_EVALUATED", [TransferIssue(
                "duplicate_source_id", "$.building", "Source room and opening namespaces require unique stable IDs")])
        digest = _hash(original)
        if raw["building_sha256"] != digest:
            return result("PASS", "FAIL", "NOT_EVALUATED", [TransferIssue(
                "stale_building_binding", "$.building_sha256", "Whole-source BuildingIR hash differs from supplied binding")])
    except _InputFailure as exc:
        return result("FAIL", "FAIL", "NOT_EVALUATED", [TransferIssue(exc.issue.code, exc.issue.path, exc.issue.message)])
    except (ValueError, TypeError, AttributeError, OverflowError, RecursionError) as exc:
        return result("FAIL", "FAIL", "NOT_EVALUATED", [TransferIssue("invalid_request", "$", str(exc)[:256])])

    issues = []

    def issue(code, path, message, status="FAIL", **ids):
        issues.append(TransferIssue(code, path, message, status, **ids))

    doors = {door.id: door for door in building.layout.doors}
    windows = {window.id for window in building.layout.windows}
    room_ids = {room.id for room in building.layout.rooms}
    flow_ids = {flow.id for flow in building.flows}
    seen_flows, seen_transfers, seen_openings = set(), set(), set()
    for index, row in enumerate(raw["flow_movements"]):
        path, ids = f"$.flow_movements[{index}]", {"flow_id": row["flow_id"], "source_id": row["source_id"]}
        if row["flow_id"] not in flow_ids:
            issue("unknown_flow", path, "Classification must bind an existing source flow ID", **ids)
        if row["flow_id"] in seen_flows:
            issue("duplicate_flow_classification", path, "Each flow has at most one explicit classification", **ids)
        seen_flows.add(row["flow_id"])
    for index, row in enumerate(raw["transfers"]):
        path = f"$.transfers[{index}]"
        ids = {"transfer_id": row["id"], "opening_id": row["opening_id"], "source_id": row["source_id"]}
        if row["id"] in seen_transfers or row["opening_id"] in seen_openings:
            issue("duplicate_transfer_binding", path, "Transfer and opening bindings must be unique", **ids)
        seen_transfers.add(row["id"])
        seen_openings.add(row["opening_id"])
        for room in row["room_chain"]:
            if room not in room_ids:
                issue("unknown_room", path + ".room_chain", "Room chain must bind existing source rooms", **ids)
        door = doors.get(row["opening_id"])
        if row["opening_id"] in windows:
            issue("window_is_not_transfer", path + ".opening_id", "Facade windows are closed walls, never transfer portals", **ids)
        elif door is None:
            issue("unknown_explicit_opening", path + ".opening_id", "Transfer requires an explicit source DoorSpec ID; automatic doors are not authority", **ids)
        elif set(row["room_chain"]) != {door.room_a, door.room_b}:
            issue("opening_room_mismatch", path + ".room_chain", "Ordered room chain must identify this source door's two rooms", **ids)
        elif row["minimum_clear_width_mm"] > door.width_mm:
            issue("source_opening_too_narrow", path + ".minimum_clear_width_mm", "Declared minimum exceeds the explicit source opening width", **ids)
        directions = set()
        for permission in row["permissions"]:
            if permission["direction"] in directions:
                issue("duplicate_permission_direction", path + ".permissions", "Each direction has one permission declaration", **ids)
            directions.add(permission["direction"])
            if row["kind"] in ("pass_box", "material_airlock") and "personnel" in permission["movement_kinds"]:
                issue("forbidden_transfer_movement", path + ".permissions", "Pass-box and material-airlock declarations cannot permit personnel", **ids)
            if row["kind"] == "personnel_airlock" and any(kind != "personnel" for kind in permission["movement_kinds"]):
                issue("forbidden_transfer_movement", path + ".permissions", "Personnel-airlock permission is scoped to personnel", **ids)

    binding = "FAIL" if issues else "PASS"
    if production is None:
        issue("missing_production_evidence", "$.production_program", "No supplied source-bound production program is present", "UNKNOWN")
        return result("PASS", binding, "UNKNOWN", issues, TransferProgramContract(_json_bytes(raw).decode("utf-8")) if binding == "PASS" else None)
    production_digest = _hash(production)
    if raw["production_program_sha256"] != production_digest:
        issue("stale_production_program_binding", "$.production_program_sha256", "Exact supplied production-program hash differs")
        return result("PASS", "FAIL", "NOT_EVALUATED", issues)
    production_report = validate_production_program(production, building=building,
        limits=replace(_raw_limits(limits), max_total_utf8_bytes=limits.max_input_bytes))
    if production_report.binding_status != "PASS" or production_report.schema_status != "PASS":
        for original_issue in production_report.issues:
            issue(original_issue.code, "$.production_program" + original_issue.path[1:], original_issue.message,
                  original_issue.status, source_id=original_issue.source_id)
        return result("PASS", "FAIL", "NOT_EVALUATED", issues)
    if building.production_program is not None and _hash(building.production_program.to_dict()) != production_digest:
        issue("embedded_production_program_mismatch", "$.production_program", "Supplied program differs from the embedded whole-source program")
    sources = {source["id"]: source for source in production["sources"]}
    for name in ("flow_movements", "transfers"):
        for index, row in enumerate(raw[name]):
            path = f"$.{name}[{index}]"
            ids = {"source_id": row["source_id"], **({"flow_id": row["flow_id"]} if name == "flow_movements" else
                   {"transfer_id": row["id"], "opening_id": row["opening_id"]})}
            source = sources.get(row["source_id"])
            if source is None:
                issue("unknown_source", path + ".source_id", "Declaration must reference the supplied production-program source namespace", **ids)
            elif row["excerpt"] not in source["content"]:
                issue("source_excerpt_mismatch", path + ".excerpt", "Excerpt is absent from exact verified supplied content", **ids)
    if any(item.status == "FAIL" for item in issues):
        return result("PASS", "FAIL", "NOT_EVALUATED", issues)
    if production_report.evidence_status != "PASS":
        issue("production_evidence_incomplete" if production_report.evidence_status == "UNKNOWN" else "production_evidence_conflict",
              "$.production_program", "Fresh supplied production evidence did not pass its declared scope", production_report.evidence_status)
    for name in ("flow_movements", "transfers"):
        for index, row in enumerate(raw[name]):
            ids = {"source_id": row["source_id"], **({"flow_id": row["flow_id"]} if name == "flow_movements" else
                   {"transfer_id": row["id"], "opening_id": row["opening_id"]})}
            for reason in _provenance(row, production):
                issue(reason, f"$.{name}[{index}]", "Supplied claim has incomplete rights, assumption or source attribution evidence", "UNKNOWN", **ids)
    provenance = "FAIL" if any(item.status == "FAIL" for item in issues) else "UNKNOWN" if issues else "PASS"
    return result("PASS", "PASS", provenance, issues, TransferProgramContract(_json_bytes(raw).decode("utf-8")))

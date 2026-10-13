"""Bounded production-program declarations tied to original canonical source.

PASS covers supplied identity, references and declared room relations only.
There is no generated geometry, capacity simulation, or authenticated approval.
"""

from dataclasses import dataclass, fields, replace
from datetime import date
import hashlib
import json
import math

from .building import BuildingIR
from .resources import resource_path
from .schema import DEFAULT_BUILDING_SCHEMA_PATH, DEFAULT_SCHEMA_PATH, load_json_schema


@dataclass(frozen=True)
class ProductionProgramLimits:
    max_sources: int = 128
    max_materials: int = 256
    max_states: int = 1024
    max_operations: int = 256
    max_requirements: int = 1024
    max_source_bytes: int = 262144
    max_total_source_bytes: int = 1048576
    max_total_utf8_bytes: int = 2097152
    max_nodes: int = 65536
    max_depth: int = 16
    max_building_objects: int = 1024
    max_references: int = 16384


@dataclass(frozen=True)
class ProductionProgramIssue:
    code: str
    path: str
    message: str
    status: str = "FAIL"
    operation_id: str | None = None
    requirement_id: str | None = None
    source_id: str | None = None

    def to_dict(self):
        return {field.name: getattr(self, field.name) for field in fields(self)
                if getattr(self, field.name) is not None}


@dataclass(frozen=True)
class ProductionProgramContract:
    """Detached JSON preserves caller scalar types and exact source strings."""

    _json: str

    def to_dict(self):
        return json.loads(self._json)


@dataclass(frozen=True)
class ProductionProgramValidation:
    status: str
    schema_status: str
    binding_status: str
    evidence_status: str
    program: ProductionProgramContract | None
    issues: tuple[ProductionProgramIssue, ...]
    counts: tuple[tuple[str, int], ...]
    building_sha256: str | None = None

    def to_dict(self):
        return {"schema_version": 1, "scope": "supplied_production_program_contract",
                "status": self.status, "schema_status": self.schema_status,
                "binding_status": self.binding_status, "evidence_status": self.evidence_status,
                "engineering_acceptance": "PENDING", "authenticated_approval": "UNKNOWN",
                "geometry_validation": "NOT_EVALUATED", "usage_rights_scope": "supplied_declaration_only",
                "building_sha256": self.building_sha256, "counts": dict(self.counts),
                "issues": [issue.to_dict() for issue in self.issues]}


class _InputFailure(Exception):
    def __init__(self, code, path, message):
        self.issue = ProductionProgramIssue(code, path, message)


def _json_bytes(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False,
                      separators=(",", ":")).encode("utf-8")


def _utf8_size(text, limit, path):
    # Character count is a lower bound: reject before allocating a large encoding.
    if len(text) > limit:
        raise _InputFailure("input_budget_exceeded", path, "UTF-8 byte budget exceeded")
    total = 0
    for offset in range(0, len(text), 4096):
        try:
            total += len(text[offset:offset + 4096].encode("utf-8"))
        except UnicodeError as exc:
            raise _InputFailure("invalid_json_value", path, "Text must be valid UTF-8") from exc
        if total > limit:
            raise _InputFailure("input_budget_exceeded", path, "UTF-8 byte budget exceeded")
    return total


def _preflight(value, limits):
    """Count compact JSON bytes without serializing or normalizing the document."""
    counts = {"json_nodes": 0, "json_utf8_bytes": 0, "source_utf8_bytes": 0, "typed_references": 0}
    if type(value) is dict:
        for name in ("sources", "materials", "states", "operations", "requirements"):
            rows = value.get(name)
            if type(rows) is list:
                counts[name] = len(rows)
                if len(rows) > getattr(limits, "max_" + name):
                    raise _InputFailure("input_budget_exceeded", "$." + name, "Entity count budget exceeded")
        for index, source in enumerate(value.get("sources", []) if type(value.get("sources")) is list else []):
            if type(source) is dict and type(source.get("content")) is str:
                counts["source_utf8_bytes"] += _utf8_size(source["content"], limits.max_source_bytes,
                                                         f"$.sources[{index}].content")
                if counts["source_utf8_bytes"] > limits.max_total_source_bytes:
                    raise _InputFailure("input_budget_exceeded", "$.sources", "Total source byte budget exceeded")

    active = set()

    def add(amount, path):
        counts["json_utf8_bytes"] += amount
        if counts["json_utf8_bytes"] > limits.max_total_utf8_bytes:
            raise _InputFailure("input_budget_exceeded", path, "Total JSON UTF-8 byte budget exceeded")

    def walk(item, path, depth):
        counts["json_nodes"] += 1
        if depth > limits.max_depth or counts["json_nodes"] > limits.max_nodes:
            raise _InputFailure("input_budget_exceeded", path, "JSON depth or node budget exceeded")
        if type(item) in (dict, list):
            if id(item) in active:
                raise _InputFailure("invalid_json_value", path, "Cyclic input is not JSON")
            active.add(id(item))
            add(2 + max(0, len(item) - 1), path)
            if type(item) is dict:
                for key, child in item.items():
                    if type(key) is not str:
                        raise _InputFailure("invalid_json_value", path, "Object keys must be strings")
                    if key.endswith("_id") and child is not None:
                        counts["typed_references"] += 1
                    elif key in ("equipment_ids", "flow_ids", "inputs", "outputs") and type(child) is list:
                        counts["typed_references"] += len(child)
                    if counts["typed_references"] > limits.max_references:
                        raise _InputFailure("input_budget_exceeded", path, "Typed reference budget exceeded")
                    walk(key, path, depth + 1)
                    add(1, path)
                    walk(child, path + "." + key[:128], depth + 1)
            else:
                for index, child in enumerate(item):
                    walk(child, f"{path}[{index}]", depth + 1)
            active.remove(id(item))
        elif type(item) is str:
            size = _utf8_size(item, limits.max_total_utf8_bytes, path)
            # ensure_ascii=False still escapes quotes, backslashes and controls.
            escapes = sum(1 if char in '\"\\\b\f\n\r\t' else 5 if ord(char) < 32 else 0 for char in item)
            add(size + escapes + 2, path)
        elif item is None or type(item) is bool:
            add(4 if item is None or item is True else 5, path)
        elif type(item) in (int, float):
            if (type(item) is float and not math.isfinite(item)) or (type(item) is int and item.bit_length() > 1024):
                raise _InputFailure("invalid_json_value", path, "Numbers must be finite and bounded")
            add(len(str(item)), path)
        else:
            raise _InputFailure("invalid_json_value", path, "Only raw JSON scalar, list and object types are accepted")

    walk(value, "$", 0)
    return counts


def _schema_issues(value, schema):
    from jsonschema import Draft202012Validator

    issues = []
    for error in Draft202012Validator(schema).iter_errors(value):
        path = "$" + "".join(f"[{part}]" if type(part) is int else "." + part for part in error.absolute_path)
        issues.append(ProductionProgramIssue("schema_invalid", path, error.message[:512]))
        if len(issues) == 32:
            break
    return issues


def validate_production_program(raw, *, building, limits=ProductionProgramLimits()):
    """Reconcile supplied declarations with original source, never placed geometry.

    Building identity hashes its unchanged canonical ``to_dict()`` bytes using
    sorted keys, compact separators, UTF-8 and finite JSON numbers. Numeric
    strings and booleans are never normalized into measurements. PASS does not
    establish the truth of source claims or approval of the manufacturing design.
    """
    counts = {}

    def result(schema, binding, evidence, issues, program=None, digest=None):
        status = "FAIL" if "FAIL" in (schema, binding, evidence) else "UNKNOWN" if evidence == "UNKNOWN" else "PASS"
        return ProductionProgramValidation(status, schema, binding, evidence, program, tuple(issues),
                                           tuple(sorted(counts.items())), digest)

    if type(limits) is not ProductionProgramLimits or any(type(getattr(limits, field.name)) is not int or
                                                        getattr(limits, field.name) <= 0 for field in fields(limits)):
        return result("NOT_EVALUATED", "FAIL", "NOT_EVALUATED", [ProductionProgramIssue(
            "invalid_limits", "$", "Limits must contain actual positive integers")])
    try:
        counts = _preflight(raw, limits)
    except _InputFailure as exc:
        schema = "NOT_EVALUATED" if exc.issue.code == "input_budget_exceeded" else "FAIL"
        return result(schema, "FAIL", "NOT_EVALUATED", [exc.issue])
    issues = _schema_issues(raw, load_json_schema(resource_path("schemas/production_program.schema.json")))
    if issues:
        return result("FAIL", "NOT_EVALUATED", "NOT_EVALUATED", issues)
    if not isinstance(building, BuildingIR):
        return result("PASS", "FAIL", "NOT_EVALUATED", [ProductionProgramIssue(
            "invalid_building", "$", "Binding requires an original canonical BuildingIR")])
    try:
        objects = len(building.layout.rooms) + len(building.zones) + len(building.equipment) + len(building.flows)
        objects += len(building.layout.doors) + len(building.layout.windows) + len(building.layout.boundary.cutouts)
        objects += int(building.layout.external_entry is not None)
        if building.structural_grid is not None:
            objects += 1 + len(building.structural_grid.axes_x_mm) + len(building.structural_grid.axes_y_mm)
            objects += len(building.structural_grid.labels_x) + len(building.structural_grid.labels_y)
        references = sum(len(room.required_adjacency) + len(room.preferred_adjacency) + len(room.forbidden_adjacency)
                         for room in building.layout.rooms)
        references += sum(len(zone.room_ids) + len(zone.required_adjacency) + len(zone.preferred_adjacency) +
                          len(zone.forbidden_adjacency) for zone in building.zones)
        references += sum(len(flow.from_ids) + len(flow.to_ids) for flow in building.flows)
        references += sum(int(zone.parent_zone_id is not None) for zone in building.zones)
        references += sum(int(item.room_id is not None) + int(item.zone_id is not None) for item in building.equipment)
        references += 2 * len(building.layout.doors) + len(building.layout.windows)
        references += 1 + int(building.layout.external_entry is not None)
        if objects > limits.max_building_objects or references + counts["typed_references"] > limits.max_references:
            raise _InputFailure("input_budget_exceeded", "$.building", "Original building object/reference budget exceeded")
        original = building.to_dict()
        remaining_bytes = limits.max_total_utf8_bytes - counts["json_utf8_bytes"]
        remaining_nodes = limits.max_nodes - counts["json_nodes"]
        if remaining_bytes <= 0 or remaining_nodes <= 0:
            raise _InputFailure("input_budget_exceeded", "$.building", "Combined canonical input budget exceeded")
        original_counts = _preflight(original, replace(limits, max_total_utf8_bytes=remaining_bytes,
                                                       max_nodes=remaining_nodes))
        counts.update(building_objects=objects,
                      combined_json_utf8_bytes=counts["json_utf8_bytes"] + original_counts["json_utf8_bytes"],
                      combined_json_nodes=counts["json_nodes"] + original_counts["json_nodes"])
        building_issues = _schema_issues(original, load_json_schema(DEFAULT_BUILDING_SCHEMA_PATH))
        building_issues += _schema_issues(original["layout"], load_json_schema(DEFAULT_SCHEMA_PATH))
        if building_issues:
            return result("PASS", "FAIL", "NOT_EVALUATED", [ProductionProgramIssue(
                "invalid_building", "$.building", "Original building must satisfy both canonical schemas")])
        building._validate_references()
        digest = hashlib.sha256(_json_bytes(original)).hexdigest()
    except _InputFailure as exc:
        return result("PASS", "FAIL", "NOT_EVALUATED", [exc.issue])
    except (AttributeError, ValueError, TypeError, OverflowError) as exc:
        return result("PASS", "FAIL", "NOT_EVALUATED", [ProductionProgramIssue(
            "invalid_building", "$.building", f"Invalid original canonical building: {str(exc)[:256]}")])
    if raw["building_sha256"] != digest:
        return result("PASS", "FAIL", "NOT_EVALUATED", [ProductionProgramIssue(
            "stale_building_binding", "$.building_sha256", "Program differs from the original canonical building hash")], digest=digest)

    issues = []

    def fail(code, path, message, **ids):
        issues.append(ProductionProgramIssue(code, path, message, **ids))

    namespaces = {}
    for name in ("sources", "materials", "states", "operations", "requirements"):
        rows = {}
        for index, row in enumerate(raw[name]):
            if row["id"] in rows:
                fail("duplicate_id", f"$.{name}[{index}].id", f"Duplicate ID in {name} namespace")
            rows[row["id"]] = row
        namespaces[name] = rows
    sources = namespaces["sources"]
    room_ids = {room.id for room in building.layout.rooms}
    equipment = {item.id: item for item in building.equipment}
    flows = {flow.id: flow for flow in building.flows}

    def endpoint_rooms(endpoint):
        if endpoint in room_ids:
            return (endpoint,)
        if endpoint in equipment:
            item = equipment[endpoint]
            return (item.room_id,) if item.room_id else building.room_ids_for_zone(item.zone_id)
        return building.room_ids_for_zone(endpoint)

    def reference(value, allowed, path, **ids):
        if value not in allowed:
            fail("unknown_reference", path, "Reference is absent from its declared namespace", **ids)
            return False
        return True

    for index, source in enumerate(raw["sources"]):
        if hashlib.sha256(source["content"].encode("utf-8")).hexdigest() != source["sha256"]:
            fail("source_hash_mismatch", f"$.sources[{index}].sha256", "Hash differs from exact supplied UTF-8 content", source_id=source["id"])
    for index, state in enumerate(raw["states"]):
        reference(state["material_id"], namespaces["materials"], f"$.states[{index}].material_id")
    for index, operation in enumerate(raw["operations"]):
        path, operation_id = f"$.operations[{index}]", operation["id"]
        reference(operation["room_id"], room_ids, path + ".room_id", operation_id=operation_id)
        for name, allowed in (("equipment_ids", equipment), ("flow_ids", flows),
                              ("inputs", namespaces["states"]), ("outputs", namespaces["states"])):
            for value in operation[name]:
                if reference(value, allowed, path + "." + name, operation_id=operation_id) and name == "equipment_ids":
                    item = equipment[value]
                    allowed_rooms = (item.room_id,) if item.room_id else building.room_ids_for_zone(item.zone_id)
                    if operation["room_id"] not in allowed_rooms:
                        fail("equipment_room_mismatch", path + ".equipment_ids", "Equipment is not assigned to the operation room", operation_id=operation_id)
                elif name == "flow_ids" and value in flows:
                    flow = flows[value]
                    if not any(operation["room_id"] in endpoint_rooms(endpoint) for endpoint in (*flow.from_ids, *flow.to_ids)):
                        fail("flow_room_mismatch", path + ".flow_ids", "No actual flow endpoint belongs to the operation room", operation_id=operation_id)
        for name, measurement in operation["measurements"].items():
            if measurement["source_id"] is not None:
                reference(measurement["source_id"], sources, path + ".measurements." + name + ".source_id",
                          operation_id=operation_id, source_id=measurement["source_id"])
    for index, requirement in enumerate(raw["requirements"]):
        path = f"$.requirements[{index}]"
        ids = {"requirement_id": requirement["id"], "source_id": requirement["source_id"], "operation_id": requirement["operation_id"]}
        reference(requirement["operation_id"], namespaces["operations"], path + ".operation_id", **ids)
        for name in ("room_a", "room_b"):
            reference(requirement[name], room_ids, path + "." + name, **ids)
        if requirement["room_a"] == requirement["room_b"]:
            fail("invalid_room_pair", path, "Adjacency requires two distinct source rooms", **ids)
        if reference(requirement["source_id"], sources, path + ".source_id", **ids):
            if requirement["excerpt"] not in sources[requirement["source_id"]]["content"]:
                fail("source_excerpt_mismatch", path + ".excerpt", "Excerpt is absent from exact supplied source content", **ids)
    confirmation = raw["engineer_confirmation"]
    if confirmation is not None:
        if reference(confirmation["source_id"], sources, "$.engineer_confirmation.source_id", source_id=confirmation["source_id"]):
            if confirmation["source_sha256"] != sources[confirmation["source_id"]]["sha256"] or confirmation["revision"] != raw["revision"]:
                fail("stale_confirmation", "$.engineer_confirmation", "Supplied attribution differs from source identity or program revision", source_id=confirmation["source_id"])
        try:
            date.fromisoformat(confirmation["date"])
        except ValueError:
            fail("invalid_confirmation_date", "$.engineer_confirmation.date", "Supplied date must be an actual calendar date")
    if issues:
        return result("PASS", "FAIL", "NOT_EVALUATED", issues, digest=digest)

    def evidence(code, path, message, status="UNKNOWN", **ids):
        issues.append(ProductionProgramIssue(code, path, message, status, **ids))

    def supplied_confirmation(path, **ids):
        if confirmation is None:
            evidence("missing_confirmation", path, "No supplied attribution record is present", **ids)
        elif confirmation["source_id"] != ids["source_id"]:
            evidence("mismatched_confirmation", path,
                     "Supplied attribution names a different source from this claim", **ids)

    for index, source in enumerate(raw["sources"]):
        state = source["usage_rights"]["state"]
        if state != "PERMITTED":
            evidence("unknown_usage_rights" if state == "UNKNOWN" else "restricted_usage_rights", f"$.sources[{index}].usage_rights",
                     "Usage rights do not establish permitted project use", source_id=source["id"])
    for index, operation in enumerate(raw["operations"]):
        for name, measurement in operation["measurements"].items():
            path = f"$.operations[{index}].measurements.{name}"
            ids = {"operation_id": operation["id"], "source_id": measurement["source_id"]}
            if measurement["status"] == "UNKNOWN":
                evidence("unknown_measurement", path, "Required measurement is explicitly unknown", **ids)
            if measurement["basis"] == "assumption":
                evidence("assumed_measurement", path, "Measurement remains a supplied assumption", **ids)
            elif measurement["basis"] == "supplied_confirmation":
                supplied_confirmation(path, **ids)
    required, forbidden = set(), set()
    for room in building.layout.rooms:
        required.update(frozenset((room.id, other)) for other in room.required_adjacency)
        forbidden.update(frozenset((room.id, other)) for other in room.forbidden_adjacency)
    for index, requirement in enumerate(raw["requirements"]):
        path = f"$.requirements[{index}]"
        ids = {"requirement_id": requirement["id"], "source_id": requirement["source_id"], "operation_id": requirement["operation_id"]}
        pair = frozenset((requirement["room_a"], requirement["room_b"]))
        declared, opposite = (required, forbidden) if requirement["kind"] == "required_room_adjacency" else (forbidden, required)
        if pair in opposite:
            evidence("contradictory_adjacency_declaration", path, "Original source declares the opposite hard relation", "FAIL", **ids)
        elif pair not in declared:
            evidence("missing_adjacency_declaration", path, "Original source has no matching hard relation", "FAIL", **ids)
        if requirement["basis"] == "assumption":
            evidence("assumed_requirement", path, "Requirement remains a supplied assumption", **ids)
        elif requirement["basis"] == "supplied_confirmation":
            supplied_confirmation(path, **ids)
    status = "FAIL" if any(issue.status == "FAIL" for issue in issues) else "UNKNOWN" if issues else "PASS"
    program = ProductionProgramContract(_json_bytes(raw).decode("utf-8"))
    return result("PASS", "PASS", status, issues, program, digest)

"""Detached finite equipment cards backed by exact supplied structured sources.

Evidence PASS checks declared source identity and structured values only. It does
not authenticate a manufacturer, rights holder or engineer, or prove a scenario
safe. Templates select explicitly supplied variants; they never evaluate code,
interpolate dimensions, or infer dimensions from text, drawings or photographs.
"""

from dataclasses import dataclass, fields
from datetime import date
from functools import lru_cache
import hashlib
from itertools import islice
import json
import math
import re

from jsonschema import Draft202012Validator
from referencing import Registry, Resource

from .resources import resource_path


@dataclass(frozen=True)
class EngineeringLibraryLimits:
    max_sources: int = 64
    max_cards: int = 128
    max_templates: int = 64
    max_source_bytes: int = 262144
    max_total_source_bytes: int = 1048576
    max_total_utf8_bytes: int = 2097152
    max_nodes: int = 65536
    max_depth: int = 24
    max_geometry_points: int = 4096


@dataclass(frozen=True)
class EngineeringLibraryIssue:
    code: str
    path: str
    message: str
    status: str = "FAIL"

    def to_dict(self):
        return {field.name: getattr(self, field.name) for field in fields(self)}


@dataclass(frozen=True)
class EngineeringLibraryContract:
    """A detached snapshot, not a cached validation verdict."""

    _json: str

    def to_dict(self):
        if type(self._json) is not str or len(self._json) > EngineeringLibraryLimits().max_total_utf8_bytes:
            raise ValueError("Detached library JSON exceeds its bound")
        _utf8_size(self._json, EngineeringLibraryLimits().max_total_utf8_bytes, "$")
        _raw_json_preflight(self._json, EngineeringLibraryLimits())
        raw = _strict_json(self._json)
        _preflight(raw, EngineeringLibraryLimits())
        return raw


@dataclass(frozen=True)
class EngineeringLibraryValidation:
    status: str
    schema_status: str
    binding_status: str
    evidence_status: str
    library: EngineeringLibraryContract | None
    issues: tuple[EngineeringLibraryIssue, ...]
    library_sha256: str | None = None

    def to_dict(self):
        return {"schema_version": 1, "scope": "supplied_finite_equipment_library",
                "status": self.status, "schema_status": self.schema_status,
                "binding_status": self.binding_status, "evidence_status": self.evidence_status,
                "library_sha256": self.library_sha256, "issues": [issue.to_dict() for issue in self.issues],
                "geometry_validation": "LOCAL_SHAPES_ONLY", "full_scenario_validation": "UNKNOWN",
                "engineering_acceptance": "PENDING", "source_authentication": "UNKNOWN",
                "usage_rights_scope": "supplied_declaration_only"}


@dataclass(frozen=True)
class EquipmentCardInstance:
    _json: str

    def to_dict(self):
        return EngineeringLibraryContract(self._json).to_dict()


class EngineeringTemplateError(ValueError):
    def __init__(self, code, message, *, status="FAIL"):
        self.code, self.status = code, status
        super().__init__(message)

    def to_dict(self):
        return {"status": self.status, "code": self.code, "message": str(self),
                "engineering_acceptance": "PENDING", "full_scenario_validation": "UNKNOWN"}


class _Failure(ValueError):
    def __init__(self, code, path, message):
        self.issue = EngineeringLibraryIssue(code, path, message)
        super().__init__(message)


def _json_bytes(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False,
                      separators=(",", ":")).encode("utf-8")


def _digest(value):
    return hashlib.sha256(_json_bytes(value)).hexdigest()


def _number(value, *, lower=-1000000000000, upper=1000000000000):
    return type(value) in (int, float) and lower <= value <= upper and math.isfinite(value)


def _utf8_size(value, maximum, path):
    if len(value) > maximum:
        raise _Failure("input_budget_exceeded", path, "UTF-8 byte budget exceeded")
    total = 0
    for offset in range(0, len(value), 4096):
        try:
            total += len(value[offset:offset + 4096].encode("utf-8"))
        except UnicodeError as exc:
            raise _Failure("invalid_json_value", path, "Text must be valid UTF-8") from exc
        if total > maximum:
            raise _Failure("input_budget_exceeded", path, "UTF-8 byte budget exceeded")
    return total


def _preflight(raw, limits):
    """Bound types/bytes/nodes before serialization, constructors or geometry."""
    defaults = EngineeringLibraryLimits()
    if type(limits) is not EngineeringLibraryLimits or any(
            type(getattr(limits, field.name)) is not int or not 1 <= getattr(limits, field.name) <= getattr(defaults, field.name)
            for field in fields(defaults)):
        raise _Failure("invalid_limits", "$", "Limits must be positive bounded integers")
    if type(raw) is dict:
        for key in ("sources", "cards", "templates"):
            if type(raw.get(key)) is list and len(raw[key]) > getattr(limits, "max_" + key):
                raise _Failure("input_budget_exceeded", "$." + key, "Entity count budget exceeded")
        total_sources = 0
        for index, source in enumerate(raw.get("sources", []) if type(raw.get("sources")) is list else []):
            if type(source) is dict and type(source.get("content")) is str:
                total_sources += _utf8_size(source["content"], limits.max_source_bytes, f"$.sources[{index}].content")
                if total_sources > limits.max_total_source_bytes:
                    raise _Failure("input_budget_exceeded", "$.sources", "Total source byte budget exceeded")
    active, count, utf8, points = set(), 0, 0, 0

    def walk(value, path, depth):
        nonlocal count, utf8, points
        count += 1
        if count > limits.max_nodes or depth > limits.max_depth:
            raise _Failure("input_budget_exceeded", path, "JSON depth or node budget exceeded")
        if type(value) in (dict, list):
            if id(value) in active:
                raise _Failure("invalid_json_value", path, "Cyclic objects are not JSON")
            active.add(id(value))
            utf8 += 2 + max(0, len(value) - 1)
            if type(value) is dict:
                if type(value.get("points")) is list:
                    points += len(value["points"])
                    if points > limits.max_geometry_points:
                        raise _Failure("input_budget_exceeded", path, "Geometry point budget exceeded")
                for key, child in value.items():
                    if type(key) is not str:
                        raise _Failure("invalid_json_value", path, "JSON keys must be strings")
                    walk(key, path, depth + 1)
                    utf8 += 1
                    walk(child, path + "." + key[:128], depth + 1)
            else:
                for index, child in enumerate(value):
                    walk(child, f"{path}[{index}]", depth + 1)
            active.remove(id(value))
        elif type(value) is str:
            utf8 += _utf8_size(value, limits.max_total_utf8_bytes, path) + 2
            utf8 += sum(1 if ch in '\"\\\b\f\n\r\t' else 5 if ord(ch) < 32 else 0 for ch in value)
        elif value is None or type(value) is bool:
            utf8 += 5
        elif _number(value):
            utf8 += len(str(value))
        else:
            raise _Failure("invalid_json_value", path, "Only bounded finite JSON scalar values are supported")
        if utf8 > limits.max_total_utf8_bytes:
            raise _Failure("input_budget_exceeded", path, "Total JSON UTF-8 budget exceeded")
    walk(raw, "$", 0)


def _raw_json_preflight(content, limits):
    """Bound lexical nesting and node work before JSON allocates containers."""
    depth, quoted, escaped, atom, nodes = 0, False, False, False, 0
    for char in content:
        if quoted:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                quoted = False
        elif char == '"':
            quoted, atom = True, False
            nodes += 1
        elif char in "[{":
            depth += 1
            atom = False
            nodes += 1
            if depth > limits.max_depth:
                raise _Failure("input_budget_exceeded", "$", "Raw JSON depth budget exceeded before parsing")
        elif char in "]}":
            depth -= 1
            atom = False
        elif char in ",:" or char.isspace():
            atom = False
        elif not atom:
            atom = True
            nodes += 1
        if nodes > limits.max_nodes:
            raise _Failure("input_budget_exceeded", "$", "Raw JSON node budget exceeded before parsing")


def _strict_json(content):
    def pairs(rows):
        value = {}
        for key, item in rows:
            if key in value:
                raise ValueError("Duplicate structured source key")
            value[key] = item
        return value

    def constant(value):
        raise ValueError("Nonfinite JSON constant: " + value)
    return json.loads(content, object_pairs_hook=pairs, parse_constant=constant)


@lru_cache(maxsize=1)
def _validator():
    shared = json.loads(resource_path("schemas/production_program.schema.json").read_text(encoding="utf-8"))
    schema = json.loads(resource_path("schemas/engineering_library.schema.json").read_text(encoding="utf-8"))
    registry = Registry().with_resource(shared["$id"], Resource.from_contents(shared))
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema, registry=registry)


def _pointer(document, pointer):
    if type(pointer) is not str or len(pointer) > 1024 or (pointer and not pointer.startswith("/")):
        raise ValueError("Invalid structured source pointer")
    current = document
    for token in pointer.split("/")[1:] if pointer else []:
        if re.search(r"~(?![01])", token):
            raise ValueError("Invalid JSON pointer escape")
        token = token.replace("~1", "/").replace("~0", "~")
        if type(current) is dict:
            current = current[token]
        elif type(current) is list and re.fullmatch(r"0|[1-9][0-9]*", token) and len(token) <= 6:
            current = current[int(token)]
        else:
            raise ValueError("Pointer does not name a structured value")
    return current


def _source_documents(raw, issues, limits):
    documents = {}
    for index, source in enumerate(raw["sources"]):
        path = f"$.sources[{index}]"
        if source["sha256"] != hashlib.sha256(source["content"].encode("utf-8")).hexdigest():
            issues.append(EngineeringLibraryIssue("source_hash_mismatch", path, "Exact supplied UTF-8 source digest differs"))
            continue
        if source["usage_rights"]["state"] != "PERMITTED":
            issues.append(EngineeringLibraryIssue("unavailable_usage_rights", path, "Reusable permission was not supplied", "UNKNOWN"))
        if source["kind"] == "visual_reference":
            issues.append(EngineeringLibraryIssue("visual_reference_not_numeric", path, "Visual references do not establish equipment dimensions or physical geometry", "UNKNOWN"))
        content = source["content"].lstrip()
        if not content.startswith(("{", "[")):
            issues.append(EngineeringLibraryIssue("unstructured_source", path, "Text/hash/excerpts do not establish structured numeric truth", "UNKNOWN"))
            continue
        try:
            _raw_json_preflight(source["content"], limits)
            document = _strict_json(source["content"])
            _preflight(document, limits)
        except (ValueError, RecursionError) as exc:
            issues.append(EngineeringLibraryIssue("invalid_structured_source", path, str(exc)))
            continue
        documents[source["id"]] = document
    return documents


def _source_value(documents, source_id, pointer, expected, path, issues):
    if source_id not in documents:
        issues.append(EngineeringLibraryIssue("unverified_structured_value", path, "No independently parsed structured source supplied", "UNKNOWN"))
        return False
    try:
        actual = _pointer(documents[source_id], pointer)
        if not _same_json(actual, expected):
            raise ValueError("Structured value/type/unit differs from source")
    except (KeyError, IndexError, ValueError) as exc:
        issues.append(EngineeringLibraryIssue("source_value_mismatch", path, str(exc)))
        return False
    return True


def _same_json(actual, expected):
    """Exact JSON scalar types without repeatedly serializing large source roots."""
    if type(actual) is not type(expected):
        return False
    if type(actual) is dict:
        return actual.keys() == expected.keys() and all(_same_json(actual[key], expected[key]) for key in expected)
    if type(actual) is list:
        return len(actual) == len(expected) and all(_same_json(a, b) for a, b in zip(actual, expected))
    return actual == expected


def _quantity(row, path, documents, source_ids, issues):
    metric = row["measurement"]
    if metric["source_id"] is not None and metric["source_id"] not in source_ids:
        issues.append(EngineeringLibraryIssue("unknown_source_id", path, "Quantity refers to undeclared source"))
    if metric["status"] == "UNKNOWN":
        issues.append(EngineeringLibraryIssue("unknown_quantity", path, "Missing number remains explicitly UNKNOWN", "UNKNOWN"))
        return
    _source_value(documents, metric["source_id"], row["source_pointer"],
                  {"value": metric["value"], "unit": metric["unit"]}, path, issues)
    if metric["basis"] == "assumption":
        issues.append(EngineeringLibraryIssue("assumed_evidence", path, "Assumed dimension is not a sourced fact", "UNKNOWN"))


def _geometry(value, path, issues):
    kind = value["kind"]
    if kind in ("curve", "geometry_3d"):
        issues.append(EngineeringLibraryIssue("unsupported_geometry", path, "Curves and 3D routes are outside source_local_2d_v1", "UNKNOWN"))
    elif kind == "orthogonal_polygon":
        points = value["points"]
        if points[0] != points[-1] or any((a[0] == b[0]) == (a[1] == b[1]) for a, b in zip(points, points[1:])):
            issues.append(EngineeringLibraryIssue("invalid_local_geometry", path, "Ring must be closed with nonzero orthogonal edges"))
            return
        # Only bounded literal rings reach geometry construction.
        from shapely.geometry import Polygon
        polygon = Polygon(points)
        if not polygon.is_valid or polygon.area <= 0:
            issues.append(EngineeringLibraryIssue("invalid_local_geometry", path, "Ring must be simple with positive area"))
    elif kind == "rectangle" and (abs(value["x"] + value["width"]) > 1000000 or abs(value["y"] + value["depth"]) > 1000000):
        issues.append(EngineeringLibraryIssue("invalid_local_geometry", path, "Rectangle endpoint exceeds coordinate bound"))


def _equipment_frame(frame):
    """Check original raw fields before any coercion or EquipmentSpec serializer."""
    numeric = ("width_mm", "depth_mm", "clearance_front_mm", "clearance_back_mm",
               "clearance_left_mm", "clearance_right_mm", "anchor_offset_mm")
    required = {"id", "type", *numeric, "rotation_allowed", "fixed"}
    if type(frame) is not dict or not required <= frame.keys() or set(frame) - required - {"room_id", "zone_id", "anchor_side"}:
        raise ValueError("Source equipment must be an exact canonical EquipmentSpec frame")
    for name in numeric:
        if not _number(frame[name], lower=0, upper=1000000) or (name in ("width_mm", "depth_mm") and frame[name] == 0):
            raise ValueError("Equipment dimensions require bounded finite actual numbers")
    for name in ("rotation_allowed", "fixed"):
        if type(frame[name]) is not bool:
            raise ValueError("Equipment flags require actual booleans")
    for name in ("id", "type", "room_id", "zone_id"):
        if name in frame and (type(frame[name]) is not str or not 1 <= len(frame[name]) <= 128 or frame[name] != frame[name].strip() or any(ch in frame[name] for ch in "\r\n")):
            raise ValueError("Equipment identities require bounded strings")
    if "room_id" not in frame and "zone_id" not in frame:
        raise ValueError("Equipment must retain an explicit room or zone identity")
    if frame.get("anchor_side") not in (None, "left", "right", "bottom", "top"):
        raise ValueError("Unsupported equipment anchor")
    if frame["fixed"] and ("room_id" not in frame or "anchor_side" not in frame):
        raise ValueError("Fixed equipment needs a room and explicit anchor")
    return frame


def _unique(rows, path, issues):
    ids = [row["id"] for row in rows]
    if len(set(ids)) != len(ids):
        issues.append(EngineeringLibraryIssue("duplicate_identity", path, "Semantic identities must be unique"))


def validate_engineering_library(raw, *, limits=EngineeringLibraryLimits()):
    """Fresh offline schema, hashes, pointers, shapes, references and rights QA."""
    try:
        if type(raw) is EngineeringLibraryContract:
            raw = raw.to_dict()
        _preflight(raw, limits)
        errors = list(islice(_validator().iter_errors(raw), 64))
        if errors:
            issues = tuple(EngineeringLibraryIssue("schema_error", "$" + "".join(f"[{part!r}]" for part in error.absolute_path),
                                                   error.message[:512]) for error in errors[:64])
            return EngineeringLibraryValidation("FAIL", "FAIL", "NOT_EVALUATED", "NOT_EVALUATED", None, issues)
    except (ValueError, RecursionError) as exc:
        issue = exc.issue if isinstance(exc, _Failure) else EngineeringLibraryIssue("invalid_json_value", "$", str(exc)[:512])
        return EngineeringLibraryValidation("FAIL", "FAIL", "NOT_EVALUATED", "NOT_EVALUATED", None, (issue,))

    issues = []
    for key in ("sources", "cards", "templates"):
        _unique(raw[key], "$." + key, issues)
    sources = {row["id"]: row for row in raw["sources"]}
    documents = _source_documents(raw, issues, limits)
    cards = {row["id"]: row for row in raw["cards"]}
    role_scenarios = {"body": "all", "operating": "production", "service": "maintenance",
                      "panel": "maintenance", "tool_removal": "maintenance", "installation": "installation_replacement"}
    for index, card in enumerate(raw["cards"]):
        path = f"$.cards[{index}]"
        binding = card["equipment_binding"]
        if binding["source_id"] not in sources:
            issues.append(EngineeringLibraryIssue("unknown_source_id", path, "Card equipment binding has undeclared source"))
        frame = None
        if binding["source_id"] in documents:
            try:
                frame = _equipment_frame(_pointer(documents[binding["source_id"]], binding["source_pointer"]))
                if frame["id"] != binding["id"] or frame["type"] != card["equipment_type"] or _digest(frame) != binding["sha256"]:
                    raise ValueError("Source equipment id/type/digest differs from declared card binding")
            except (KeyError, IndexError, ValueError) as exc:
                issues.append(EngineeringLibraryIssue("equipment_binding_mismatch", path, str(exc)))
                frame = None
        else:
            issues.append(EngineeringLibraryIssue("unverified_structured_value", path, "Equipment binding cannot be read from structured source", "UNKNOWN"))
        confirmation = card["engineer_confirmation"]
        confirmation_valid = False
        if confirmation is not None:
            try:
                date.fromisoformat(confirmation["date"])
                if confirmation["revision"] != card["revision"] or confirmation["source_id"] not in sources or confirmation["source_sha256"] != sources[confirmation["source_id"]]["sha256"]:
                    raise ValueError("Confirmation revision/source digest differs")
                confirmation_valid = True
            except ValueError as exc:
                issues.append(EngineeringLibraryIssue("invalid_confirmation", path, str(exc)))
        evidence_rows = []
        for name, row in card["dimensions"].items():
            _quantity(row, path + ".dimensions." + name, documents, sources, issues)
            metric = row["measurement"]
            evidence_rows.append(metric)
            if metric["status"] == "KNOWN" and (name in ("width_mm", "depth_mm", "height_mm") and metric["value"] == 0):
                issues.append(EngineeringLibraryIssue("invalid_dimension", path, "Known physical dimensions must be positive"))
            if frame is not None and name != "height_mm" and metric["status"] == "KNOWN" and metric["value"] != frame[name]:
                issues.append(EngineeringLibraryIssue("equipment_binding_mismatch", path + ".dimensions." + name, "Card dimension differs from exact bound equipment"))
        roles = [row["role"] for row in card["envelopes"]]
        if set(roles) != set(role_scenarios):
            issues.append(EngineeringLibraryIssue("invalid_envelope_roles", path, "Every envelope role must be explicit exactly once"))
        action_ids = {row["id"]: row for row in card["scenario_actions"]}
        for collection in ("envelopes", "interaction_points", "scenario_actions"):
            _unique(card[collection], path + "." + collection, issues)
            for row in card[collection]:
                row_path = path + "." + collection + "." + row["id"]
                evidence = row["evidence"]
                evidence_rows.append(evidence)
                if evidence["source_id"] is not None and evidence["source_id"] not in sources:
                    issues.append(EngineeringLibraryIssue("unknown_source_id", row_path, "Evidence refers to undeclared source"))
                if evidence["status"] == "UNKNOWN":
                    issues.append(EngineeringLibraryIssue("unknown_geometry_or_action", row_path, "Missing geometry/action remains UNKNOWN", "UNKNOWN"))
                else:
                    _source_value(documents, evidence["source_id"], evidence["source_pointer"], evidence["value"], row_path, issues)
                    value = evidence["value"]
                    allowed = {"envelopes": {"rectangle", "orthogonal_polygon", "curve", "geometry_3d"},
                               "interaction_points": {"point"}, "scenario_actions": {"production_stopped", "temporary_action"}}
                    if value["kind"] not in allowed[collection]:
                        issues.append(EngineeringLibraryIssue("invalid_evidence_kind", row_path, "Value is not appropriate for this declared role"))
                    semantic_keys = {"envelopes": ("id", "role", "scenario", "required_action_ids"),
                                     "interaction_points": ("id", "role", "scenario"), "scenario_actions": ("id", "scenario")}
                    if any(not _same_json(value.get(key), row[key]) for key in semantic_keys[collection]):
                        issues.append(EngineeringLibraryIssue("source_semantic_mismatch", row_path, "Card identity/role/scenario/action premise differs from structured source record"))
                    _geometry(value, row_path, issues)
                if evidence["basis"] == "assumption":
                    issues.append(EngineeringLibraryIssue("assumed_evidence", row_path, "Assumed geometry/action is not independently sourced", "UNKNOWN"))
                if collection == "envelopes":
                    if row["scenario"] != role_scenarios[row["role"]]:
                        issues.append(EngineeringLibraryIssue("invalid_envelope_scenario", row_path, "Envelope role needs its explicit capability scenario"))
                    for action_id in row["required_action_ids"]:
                        if action_id not in action_ids or action_ids[action_id]["scenario"] != row["scenario"]:
                            issues.append(EngineeringLibraryIssue("invalid_action_reference", row_path, "Required action must exist in the same scenario"))
        if any(row["basis"] == "supplied_confirmation" for row in evidence_rows) and not confirmation_valid:
            issues.append(EngineeringLibraryIssue("missing_confirmation", path, "Supplied confirmation basis needs source-bound revision confirmation", "UNKNOWN"))
        if confirmation_valid and any(row["basis"] == "supplied_confirmation" and
                                      row["source_id"] != confirmation["source_id"] for row in evidence_rows):
            issues.append(EngineeringLibraryIssue("confirmation_source_mismatch", path,
                                                  "Confirmation must name the exact source identity of each confirmed claim"))
        if card["applicability"]["conditions"]:
            issues.append(EngineeringLibraryIssue("unsupported_applicability", path, "Other applicability predicates are not evaluated", "UNKNOWN"))
        if binding["id"] not in card["applicability"]["equipment_ids"] or card["equipment_type"] not in card["applicability"]["equipment_types"]:
            issues.append(EngineeringLibraryIssue("invalid_applicability", path, "Bound equipment must be in explicit applicability inventory"))

    for index, template in enumerate(raw["templates"]):
        path = f"$.templates[{index}]"
        _unique(template["parameters"], path + ".parameters", issues)
        _unique(template["variants"], path + ".variants", issues)
        parameters = {row["id"]: row for row in template["parameters"]}
        expected = {"capability": template["capability"], "parameters": {}, "variants": template["variants"]}
        for parameter in template["parameters"]:
            expected["parameters"][parameter["id"]] = {"dimension": parameter["dimension"]}
            for bound in ("minimum", "maximum"):
                row = parameter[bound]
                _quantity(row, path + "." + parameter["id"] + "." + bound, documents, sources, issues)
                metric = row["measurement"]
                if metric["basis"] == "supplied_confirmation":
                    issues.append(EngineeringLibraryIssue("unsupported_bound_confirmation", path, "Template bound confirmation is not authenticated or independently supplied in this capability", "UNKNOWN"))
                expected["parameters"][parameter["id"]][bound] = {"value": metric["value"], "unit": metric["unit"]}
            low, high = (parameter[key]["measurement"]["value"] for key in ("minimum", "maximum"))
            if low is not None and high is not None and low > high:
                issues.append(EngineeringLibraryIssue("invalid_parameter_bounds", path, "Parameter minimum exceeds maximum"))
        if template["source_id"] not in sources:
            issues.append(EngineeringLibraryIssue("unknown_source_id", path, "Template source is undeclared"))
        _source_value(documents, template["source_id"], template["source_pointer"], expected, path, issues)
        vectors = set()
        for variant in template["variants"]:
            # Actual numbers 1000 and 1000.0 identify the same finite vector.
            vector = tuple(sorted(variant["parameters"].items()))
            if vector in vectors:
                issues.append(EngineeringLibraryIssue("ambiguous_variant", path, "A parameter vector must identify one supplied variant"))
            vectors.add(vector)
            if set(variant["parameters"]) != set(parameters) or variant["card_id"] not in cards:
                issues.append(EngineeringLibraryIssue("invalid_variant_reference", path, "Variant must identify every parameter and an existing card"))
            for key, value in variant["parameters"].items():
                if key in parameters:
                    low, high = (parameters[key][bound]["measurement"]["value"] for bound in ("minimum", "maximum"))
                    if low is not None and high is not None and not low <= value <= high:
                        issues.append(EngineeringLibraryIssue("parameter_out_of_bounds", path, "Declared variant lies outside supplied bounds"))
                    if variant["card_id"] in cards:
                        dimension = cards[variant["card_id"]]["dimensions"][parameters[key]["dimension"]]["measurement"]
                        if dimension["status"] != "KNOWN":
                            issues.append(EngineeringLibraryIssue("unknown_variant_dimension", path, "Selected parameter needs an actual sourced card dimension", "UNKNOWN"))
                        elif dimension["value"] != value:
                            issues.append(EngineeringLibraryIssue("invalid_variant_dimension", path, "Finite parameter value differs from selected sourced card dimension"))

    failed = any(issue.status == "FAIL" for issue in issues)
    binding_unknown = any(issue.code in ("unstructured_source", "unverified_structured_value") for issue in issues)
    status = "FAIL" if failed else "UNKNOWN" if issues else "PASS"
    contract = None if failed else EngineeringLibraryContract(_json_bytes(raw).decode("utf-8"))
    return EngineeringLibraryValidation(status, "PASS", "FAIL" if failed else "UNKNOWN" if binding_unknown else "PASS",
                                         status, contract, tuple(issues), _digest(raw))


def instantiate_equipment_template(library, template_id, parameters, *, variant_id=None):
    """Select one exact supplied finite vector; unsupported interpolation is UNKNOWN."""
    report = validate_engineering_library(library)
    if report.library is None or report.binding_status != "PASS":
        raise EngineeringTemplateError("invalid_library", "A freshly source-bound library is required", status="FAIL" if report.status == "FAIL" else "UNKNOWN")
    raw = report.library.to_dict()
    if type(template_id) is not str or type(parameters) is not dict or len(parameters) > 16 or (variant_id is not None and type(variant_id) is not str):
        raise EngineeringTemplateError("invalid_parameters", "Template identity and parameters require strict bounded types")
    template = next((row for row in raw["templates"] if row["id"] == template_id), None)
    if template is None:
        raise EngineeringTemplateError("unknown_template", "Unknown finite template")
    declared = {row["id"]: row for row in template["parameters"]}
    if set(parameters) != set(declared) or any(not _number(value, lower=0) for value in parameters.values()):
        raise EngineeringTemplateError("invalid_parameters", "Every declared parameter needs one actual bounded number")
    source_map = {row["id"]: row for row in raw["sources"]}
    if source_map[template["source_id"]]["usage_rights"]["state"] != "PERMITTED":
        raise EngineeringTemplateError("unavailable_usage_rights", "Reusable template rights were not supplied", status="UNKNOWN")
    for key, parameter in declared.items():
        for bound in ("minimum", "maximum"):
            metric = parameter[bound]["measurement"]
            if metric["status"] != "KNOWN" or metric["basis"] != "declared_fact" or source_map[metric["source_id"]]["usage_rights"]["state"] != "PERMITTED":
                raise EngineeringTemplateError("unverified_parameter_bounds", "Known source-backed non-assumed bounds are required", status="UNKNOWN")
        low, high = (parameter[bound]["measurement"]["value"] for bound in ("minimum", "maximum"))
        if not low <= parameters[key] <= high:
            raise EngineeringTemplateError("parameter_out_of_bounds", "Parameter is outside supplied finite bounds")
    selected = next((row for row in template["variants"] if row["parameters"] == parameters and (variant_id is None or row["id"] == variant_id)), None)
    if selected is None:
        raise EngineeringTemplateError("unsupported_parameter_vector", "No supplied variant matches; interpolation is not supported", status="UNKNOWN")
    card = next(row for row in raw["cards"] if row["id"] == selected["card_id"])
    used_sources = {card["equipment_binding"]["source_id"]}
    used_sources.update(row["measurement"]["source_id"] for row in card["dimensions"].values() if row["measurement"]["source_id"] is not None)
    used_sources.update(row["evidence"]["source_id"] for key in ("envelopes", "interaction_points", "scenario_actions")
                        for row in card[key] if row["evidence"]["source_id"] is not None)
    if any(source_map[source_id]["usage_rights"]["state"] != "PERMITTED" or source_map[source_id]["kind"] == "visual_reference" for source_id in used_sources):
        raise EngineeringTemplateError("unavailable_usage_rights", "Reusable selected card rights were not supplied", status="UNKNOWN")
    for key, parameter in declared.items():
        dimension = card["dimensions"][parameter["dimension"]]["measurement"]
        if dimension["status"] != "KNOWN" or dimension["basis"] == "assumption":
            raise EngineeringTemplateError("unverified_variant_dimension", "Selected parameter needs a non-assumed sourced card dimension", status="UNKNOWN")
    return EquipmentCardInstance(_json_bytes({"status": "DRAFT", "selection_status": "PASS", "library_sha256": report.library_sha256,
        "template_id": template_id, "template_sha256": _digest(template), "template_capability": template["capability"],
        "variant_id": selected["id"], "parameters": parameters, "card_id": card["id"], "card_sha256": _digest(card),
        "equipment_sha256": card["equipment_binding"]["sha256"], "card_evidence_status": report.evidence_status,
        "selection_scope": "Exact supplied finite vector and bound card only; no interpolation or geometry acceptance",
        "engineering_acceptance": "PENDING",
        "full_scenario_validation": "UNKNOWN", "source_authentication": "UNKNOWN"}).decode("utf-8"))

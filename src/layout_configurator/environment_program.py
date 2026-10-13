"""Offline environment declarations bound to original process and layout IDs.

Clean and ventilation regions are independent annotations. Classification,
actions and observations remain supplied declarations; they are not legal
applicability decisions, authenticated measurements or HVAC approval.
"""

from dataclasses import dataclass, fields
from fractions import Fraction
import json

from .production_program import ProductionProgramContract, _InputFailure, _preflight, _schema_issues
from .resources import resource_path
from .schema import load_json_schema
from .transfer_program import (TransferProgramContract, TransferProgramLimits, _building_counts,
    _contract_raw, _hash, _model_preflight, _provenance, _raw_limits, validate_transfer_program)


@dataclass(frozen=True)
class EnvironmentProgramLimits(TransferProgramLimits):
    max_clean_zones: int = 64
    max_ventilation_zones: int = 64
    max_scenarios: int = 3
    max_actions: int = 128
    max_pressure_datums: int = 32
    max_pressure_nodes: int = 128
    max_pressure_edges: int = 256
    max_zone_vertices: int = 256
    max_total_zone_vertices: int = 8192
    max_pressure_relaxations: int = 1_000_000


@dataclass(frozen=True)
class EnvironmentIssue:
    code: str
    path: str
    message: str
    status: str = "FAIL"
    source_id: str | None = None
    zone_id: str | None = None
    operation_id: str | None = None
    scenario_id: str | None = None
    action_id: str | None = None
    edge_id: str | None = None
    node_id: str | None = None
    transfer_id: str | None = None
    opening_id: str | None = None

    def to_dict(self):
        return {field.name: getattr(self, field.name) for field in fields(self) if getattr(self, field.name) is not None}


@dataclass(frozen=True)
class EnvironmentProgramContract:
    _json: str

    def to_dict(self):
        limits = EnvironmentProgramLimits()
        raw = _contract_raw(self, EnvironmentProgramContract, limits)
        return _environment_preflight(raw, limits)[0]


@dataclass(frozen=True)
class EnvironmentProgramValidation:
    status: str
    schema_status: str
    binding_status: str
    provenance_status: str
    classification_status: str
    scenario_status: str
    pressure_declared_status: str
    program: EnvironmentProgramContract | None
    issues: tuple[EnvironmentIssue, ...]
    _evidence_json: str

    def to_dict(self):
        return {"schema_version": 1, "scope": "supplied_environment_program_contract", "status": self.status,
                "schema_status": self.schema_status, "binding_status": self.binding_status,
                "provenance_status": self.provenance_status, "classification_status": self.classification_status,
                "scenario_status": self.scenario_status, "pressure_declared_status": self.pressure_declared_status,
                "engineering_acceptance": "PENDING", "authenticated_approval": "UNKNOWN",
                "full_facility_validation": {"status": "UNKNOWN", "ok": False}, "proven_building_infeasible": False,
                "layout_binding": "NOT_EVALUATED", "action_execution": "UNKNOWN", "hvac_validation": "UNKNOWN",
                "issues": [issue.to_dict() for issue in self.issues], **json.loads(self._evidence_json)}


def _limits_valid(limits):
    ceilings = EnvironmentProgramLimits()
    return type(limits) is EnvironmentProgramLimits and all(type(getattr(limits, field.name)) is int and
        1 <= getattr(limits, field.name) <= getattr(ceilings, field.name) for field in fields(limits))


def _parent_limits(limits):
    return TransferProgramLimits(**{field.name: getattr(limits, field.name) for field in fields(TransferProgramLimits)})


def _environment_preflight(raw, limits):
    """Count all region and graph work before parsing polygons or constructors."""
    raw = _contract_raw(raw, EnvironmentProgramContract, limits)
    counts = _preflight(raw, _raw_limits(limits))
    if type(raw) is not dict:
        return raw, counts
    for name in ("clean_zones", "ventilation_zones", "scenarios", "pressure_datums", "pressure_nodes", "pressure_edges"):
        rows = raw.get(name)
        if type(rows) is list:
            counts[name] = len(rows)
            if len(rows) > getattr(limits, "max_" + name):
                raise _InputFailure("input_budget_exceeded", "$." + name, "Environment entity budget exceeded")
    vertices, actions = 0, 0
    for name in ("clean_zones", "ventilation_zones"):
        for row in raw.get(name, []) if type(raw.get(name)) is list else []:
            ring = row.get("boundary", {}).get("vertices") if type(row) is dict and type(row.get("boundary")) is dict else None
            if type(ring) is list:
                vertices += len(ring)
                if len(ring) > limits.max_zone_vertices or vertices > limits.max_total_zone_vertices:
                    raise _InputFailure("input_budget_exceeded", "$.", "Environment vertex budget exceeded")
    for row in raw.get("scenarios", []) if type(raw.get("scenarios")) is list else []:
        if type(row) is dict and type(row.get("actions")) is list:
            actions += len(row["actions"])
            if actions > limits.max_actions:
                raise _InputFailure("input_budget_exceeded", "$.scenarios", "Aggregate scenario action budget exceeded")
    relaxations = counts.get("pressure_nodes", 0) * counts.get("pressure_edges", 0) * 2 * max(1, counts.get("scenarios", 0))
    if relaxations > limits.max_pressure_relaxations:
        raise _InputFailure("input_budget_exceeded", "$.pressure_edges", "Exact declared pressure graph budget exceeded")
    counts.update(zone_vertices=vertices, actions=actions, pressure_relaxation_upper_bound=relaxations)
    return raw, counts


def _network(raw, allowed_edge_ids=None):
    """Exact rational difference constraints on supplied decimal pressure claims.

    Fraction(str(value)) matches finite canonical JSON numeric tokens. A cycle
    witness proves inconsistency of these declarations only, not infeasibility
    of the original building or actual HVAC performance.
    """
    summaries, first_witness = [], []
    node_ids = tuple(sorted(row["id"] for row in raw["pressure_nodes"]))
    for scenario in sorted(raw["scenarios"], key=lambda row: row["id"]):
        edges = []
        for row in sorted(raw["pressure_edges"], key=lambda row: row["id"]):
            if row["scenario_id"] != scenario["id"] or allowed_edge_ids is not None and row["id"] not in allowed_edge_ids:
                continue
            edges.append((row["high_node_id"], row["low_node_id"], -Fraction(str(row["minimum_delta_pa"])), row, "minimum"))
            if row["maximum_delta_pa"] is not None:
                edges.append((row["low_node_id"], row["high_node_id"], Fraction(str(row["maximum_delta_pa"])), row, "maximum"))
        distance, previous, changed = {node: Fraction(0) for node in node_ids}, {}, None
        for _ in node_ids:
            changed = None
            for first, second, weight, row, bound in edges:
                if distance[second] > distance[first] + weight:
                    distance[second] = distance[first] + weight
                    previous[second] = (first, second, weight, row, bound)
                    changed = second
            if changed is None:
                break
        witness = []
        if changed is not None:
            current = changed
            for _ in node_ids:
                current = previous[current][0]
            start = current
            while not witness or current != start:
                first, second, weight, row, bound = previous[current]
                witness.append({"edge_id": row["id"], "scenario_id": scenario["id"], "source_id": row["source_id"],
                                "bound": bound, "from_node_id": first, "to_node_id": second, "weight_pa_rational": str(weight)})
                current = first
            if not first_witness:
                first_witness = witness
        summaries.append({"scenario_id": scenario["id"], "status": "FAIL" if witness else "PASS", "cycle_witness": witness})
    return {"status": "FAIL" if first_witness else "UNKNOWN" if not raw["pressure_edges"] else "PASS",
            "logical_consistency_status": "FAIL" if first_witness else "PASS",
            "scope": "Exact supplied inequalities only; no physical connectivity or HVAC inference",
            "cycle_witness": first_witness, "scenarios": summaries}


def _evidence_rows(raw):
    for name in ("clean_zones", "ventilation_zones", "scenarios", "pressure_datums", "pressure_nodes", "pressure_edges"):
        for index, row in enumerate(raw[name]):
            yield f"$.{name}[{index}]", row
            if name == "scenarios":
                for action_index, action in enumerate(row["actions"]):
                    yield f"$.{name}[{index}].actions[{action_index}]", action
            elif name == "pressure_nodes":
                for value_index, value in enumerate(row["values"]):
                    for kind in ("design", "measured"):
                        yield f"$.{name}[{index}].values[{value_index}].{kind}", value[kind]


def validate_environment_program(raw, *, building, production_program, transfer_program,
                                 limits=EnvironmentProgramLimits()):
    """Check supplied identities, memberships, scenarios and declared graph."""
    issues, declared_issues, evidence = [], [], {"source": {}, "counts": {}, "declared_network": None,
        "policy": {"iso_to_gmp_inference": False, "functional_zone_pressure_inference": False,
                   "classification_and_applicability": "supplied_declarations_only"}}

    def result(schema, binding, provenance="NOT_EVALUATED", classification="NOT_EVALUATED", scenario="NOT_EVALUATED",
               pressure="NOT_EVALUATED", program=None):
        if pressure == "NOT_EVALUATED" and evidence["declared_network"] is not None:
            pressure = evidence["declared_network"]["status"]
        if evidence["declared_network"] is not None:
            evidence["declared_network"]["source_binding"] = binding
        states = (schema, binding, provenance, classification, scenario, pressure)
        status = "FAIL" if "FAIL" in states else "UNKNOWN" if "UNKNOWN" in states else "PASS"
        return EnvironmentProgramValidation(status, schema, binding, provenance, classification, scenario, pressure,
            program, tuple(issues + declared_issues), json.dumps(evidence, sort_keys=True, ensure_ascii=False, allow_nan=False, separators=(",", ":")))

    def issue(code, path, message, status="FAIL", **ids):
        issues.append(EnvironmentIssue(code, path, message, status, **ids))

    if not _limits_valid(limits):
        issue("invalid_limits", "$.limits", "Limits require actual bounded positive integers")
        return result("NOT_EVALUATED", "FAIL")
    try:
        raw, counts = _environment_preflight(raw, limits)
        evidence["counts"] = counts
        schema_issues = _schema_issues(raw, load_json_schema(resource_path("schemas/environment_program.schema.json")))
        if schema_issues:
            issues.extend(EnvironmentIssue(row.code, row.path, row.message) for row in schema_issues)
            return result("FAIL", "NOT_EVALUATED")
        parent_limits = _parent_limits(limits)
        _building_counts(building, parent_limits)
        production = _contract_raw(production_program, ProductionProgramContract, parent_limits)
        transfer = _contract_raw(transfer_program, TransferProgramContract, parent_limits)
        _preflight(production, _raw_limits(limits))
        _preflight(transfer, _raw_limits(limits))
        _model_preflight((raw, building, production, transfer), limits)
        frames = {"environment": raw, "building": building.to_dict(), "production_program": production, "transfer_program": transfer}
        combined = _preflight(frames, _raw_limits(limits, byte_limit=limits.max_input_bytes))
        evidence["counts"].update(combined_json_nodes=combined["json_nodes"], combined_json_utf8_bytes=combined["json_utf8_bytes"])
        evidence["source"] = {name + "_sha256": _hash(value) for name, value in frames.items()}
        # The exact supplied graph can contradict itself even when a parent
        # source is unavailable. Its internal IDs must first be unambiguous;
        # this proves nothing about physical portals or original geometry.
        node_ids = {row["id"] for row in raw["pressure_nodes"]}
        scenario_ids = {row["id"] for row in raw["scenarios"]}
        if len(node_ids) == len(raw["pressure_nodes"]) and len(scenario_ids) == len(raw["scenarios"]) and all(
                row["high_node_id"] in node_ids and row["low_node_id"] in node_ids and row["scenario_id"] in scenario_ids
                for row in raw["pressure_edges"]):
            evidence["declared_network"] = _network(raw)
            for witness in evidence["declared_network"]["cycle_witness"]:
                declared_issues.append(EnvironmentIssue("contradictory_pressure_cycle", "$.pressure_edges",
                    "Exact supplied pressure constraints contain a negative difference-constraint cycle",
                    edge_id=witness["edge_id"], scenario_id=witness["scenario_id"], source_id=witness["source_id"]))
        parent = validate_transfer_program(transfer, building=building, production_program=production, limits=parent_limits)
        evidence["transfer_contract_validation"] = parent.to_dict()
        if parent.schema_status != "PASS" or parent.binding_status != "PASS":
            for item in parent.issues:
                issue(item.code, "$.transfer_program" + item.path[1:], item.message, item.status,
                      source_id=item.source_id, transfer_id=item.transfer_id, opening_id=item.opening_id)
            return result("PASS", "FAIL")
        for name in ("building", "production_program", "transfer_program"):
            if frames[name] is not None and raw[name + "_sha256"] != evidence["source"][name + "_sha256"]:
                issue("stale_" + name + "_binding", "$." + name + "_sha256", "Exact original supplied-source identity differs")
        if issues:
            return result("PASS", "FAIL")
        if production is None:
            issue("missing_production_evidence", "$.production_program", "No supplied production source is available", "UNKNOWN")
            return result("PASS", "UNKNOWN", "UNKNOWN")
    except _InputFailure as exc:
        issue(exc.issue.code, exc.issue.path, exc.issue.message)
        return result("FAIL", "FAIL")
    except (ValueError, TypeError, AttributeError, OverflowError, RecursionError, KeyError) as exc:
        issue("invalid_request", "$", str(exc)[:256])
        return result("FAIL", "FAIL")

    namespaces = {}
    for name in ("clean_zones", "ventilation_zones", "scenarios", "pressure_datums", "pressure_nodes", "pressure_edges"):
        namespace = {}
        for index, row in enumerate(raw[name]):
            if row["id"] in namespace:
                issue("duplicate_id", f"$.{name}[{index}]", "Stable IDs must be unique in their typed namespace")
            namespace[row["id"]] = row
        namespaces[name] = namespace
    rooms = {row.id for row in building.layout.rooms}
    equipment = {row.id: row for row in building.equipment}
    operations = {row["id"]: row for row in production["operations"]}
    transfers = {row["id"]: row for row in transfer["transfers"]}
    sources = {row["id"]: row for row in production["sources"]}

    def refs(values, allowed, path, **ids):
        for value in values:
            if value not in allowed:
                issue("unknown_reference", path, "Reference is absent from its explicit typed source namespace", **ids)

    for name in ("clean_zones", "ventilation_zones"):
        for row in raw[name]:
            ids = {"zone_id": row["id"], "source_id": row["source_id"]}
            refs(row["host_room_ids"], rooms, "$." + name + ".host_room_ids", **ids)
            refs(row["equipment_ids"], equipment, "$." + name + ".equipment_ids", **ids)
            refs(row["operation_ids"], operations, "$." + name + ".operation_ids", **ids)
            for operation_id in row["operation_ids"]:
                if operation_id in operations and operations[operation_id]["room_id"] not in row["host_room_ids"]:
                    issue("operation_host_mismatch", "$." + name, "Operation source room is absent from declared region hosts", operation_id=operation_id, **ids)
    scenario_kinds = set()
    for scenario in raw["scenarios"]:
        ids = {"scenario_id": scenario["id"], "source_id": scenario["source_id"]}
        if scenario["kind"] in scenario_kinds:
            issue("duplicate_scenario_kind", "$.scenarios", "Each scenario kind has one explicit declaration", **ids)
        scenario_kinds.add(scenario["kind"])
        refs(scenario["active_operation_ids"] + scenario["stopped_operation_ids"], operations, "$.scenarios", **ids)
        for operation_id in sorted(set(scenario["active_operation_ids"]) & set(scenario["stopped_operation_ids"])):
            issue("scenario_operation_conflict", "$.scenarios", "An operation cannot be declared both active and stopped", operation_id=operation_id, **ids)
        action_ids = set()
        for action in scenario["actions"]:
            action_ids_tuple = {**ids, "action_id": action["id"], "source_id": action["source_id"]}
            if action["id"] in action_ids:
                issue("duplicate_action_id", "$.scenarios.actions", "Action IDs must be unique within a scenario", **action_ids_tuple)
            action_ids.add(action["id"])
            refs(action["operation_ids"] + action["required_stopped_operation_ids"], operations, "$.scenarios.actions", **action_ids_tuple)
            refs(action["equipment_ids"], equipment, "$.scenarios.actions", **action_ids_tuple)
            refs(action["transfer_ids"], transfers, "$.scenarios.actions", **action_ids_tuple)
            required_stopped = set(action["required_stopped_operation_ids"])
            if action["kind"] == "production_stopped":
                required_stopped.update(action["operation_ids"])
            for operation_id in sorted(required_stopped - set(scenario["stopped_operation_ids"])):
                issue("unstopped_action_requirement", "$.scenarios.actions", "Action requires an explicitly stopped operation", operation_id=operation_id, **action_ids_tuple)
    pressure_spaces = set()
    for node in raw["pressure_nodes"]:
        space = node["space"]
        allowed = rooms if space["namespace"] == "room" else namespaces["clean_zones" if space["namespace"] == "clean_zone" else "ventilation_zones"]
        refs([space["id"]], allowed, "$.pressure_nodes.space", node_id=node["id"])
        refs([node["datum_id"]], namespaces["pressure_datums"], "$.pressure_nodes.datum_id", node_id=node["id"])
        space_key = (space["namespace"], space["id"])
        if space_key in pressure_spaces:
            issue("duplicate_pressure_space", "$.pressure_nodes", "One physical typed space must have one pressure node", node_id=node["id"])
        pressure_spaces.add(space_key)
        scenarios = set()
        for value in node["values"]:
            refs([value["scenario_id"]], namespaces["scenarios"], "$.pressure_nodes.values", node_id=node["id"])
            if value["scenario_id"] in scenarios:
                issue("duplicate_pressure_scenario", "$.pressure_nodes.values", "Pressure values must be unique per node/scenario", node_id=node["id"])
            scenarios.add(value["scenario_id"])
    for edge in raw["pressure_edges"]:
        ids = {"edge_id": edge["id"], "scenario_id": edge["scenario_id"], "source_id": edge["source_id"], "transfer_id": edge["transfer_id"]}
        refs([edge["scenario_id"]], namespaces["scenarios"], "$.pressure_edges.scenario_id", **ids)
        refs([edge["high_node_id"], edge["low_node_id"]], namespaces["pressure_nodes"], "$.pressure_edges", **ids)
        refs([edge["transfer_id"]], transfers, "$.pressure_edges.transfer_id", **ids)
        if edge["maximum_delta_pa"] is not None and edge["maximum_delta_pa"] < edge["minimum_delta_pa"]:
            issue("inverted_pressure_bounds", "$.pressure_edges", "Maximum differential is below its declared minimum", **ids)
    for path, row in _evidence_rows(raw):
        if row.get("status") == "UNKNOWN" and row["source_id"] is None:
            continue
        source = sources.get(row["source_id"])
        if source is None:
            issue("unknown_source", path + ".source_id", "Claim must reference an original supplied production source", source_id=row["source_id"])
        elif row["excerpt"] is not None and row["excerpt"] not in source["content"]:
            issue("source_excerpt_mismatch", path + ".excerpt", "Excerpt differs from exact supplied source content", source_id=row["source_id"])
    if issues:
        return result("PASS", "FAIL")
    declared = evidence["declared_network"] or _network(raw)
    evidence["declared_network"] = declared
    if declared["status"] == "UNKNOWN":
        issue("missing_pressure_declarations", "$.pressure_edges", "No supplied pressure requirements establish a network scope", "UNKNOWN")
    scenario_status, classification_status = "PASS", "PASS"
    if scenario_kinds != {"production", "maintenance", "installation_replacement"}:
        scenario_status = "UNKNOWN"
        issue("missing_scenario", "$.scenarios", "All three explicit operating scenarios are required for this declared scope", "UNKNOWN")
    for scenario in raw["scenarios"]:
        for operation_id in sorted(set(operations) - set(scenario["active_operation_ids"]) - set(scenario["stopped_operation_ids"])):
            scenario_status = "UNKNOWN"
            issue("missing_operation_state", "$.scenarios", "Operation state is not declared for this scenario", "UNKNOWN",
                  operation_id=operation_id, scenario_id=scenario["id"])
    for row in raw["clean_zones"]:
        for name in ("iso_class", "gmp_grade", "state"):
            if row[name]["status"] == "UNKNOWN":
                classification_status = "UNKNOWN"
                issue("unknown_environment_classification", "$.clean_zones." + name, "Classification/state remains explicitly unknown; no mapping is inferred", "UNKNOWN", zone_id=row["id"], source_id=row["source_id"])
    provenance = parent.provenance_status
    for path, row in _evidence_rows(raw):
        if row.get("status") == "UNKNOWN" and (row["source_id"] is None or row["excerpt"] is None):
            if provenance != "FAIL":
                provenance = "UNKNOWN"
            issue("missing_measurement_evidence", path,
                  "Unknown pressure value has no complete supplied observation evidence", "UNKNOWN", source_id=row["source_id"])
            continue
        for reason in _provenance(row, production):
            if provenance != "FAIL":
                provenance = "UNKNOWN"
            issue(reason, path, "Supplied claim has incomplete source rights, assumption or attribution evidence", "UNKNOWN", source_id=row["source_id"])
    if parent.provenance_status != "PASS":
        issue("parent_evidence_incomplete", "$.transfer_program", "Fresh source-bound process/transfer provenance did not pass", parent.provenance_status)
    program = EnvironmentProgramContract(json.dumps(raw, sort_keys=True, ensure_ascii=False, allow_nan=False, separators=(",", ":")))
    return result("PASS", "PASS", provenance, classification_status, scenario_status, declared["status"], program)

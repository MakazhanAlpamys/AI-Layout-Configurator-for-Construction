"""Checked conflicts relative to one fixed CP base and conditioned finite family.

The caller registers terminal clauses in the actual builder before derived
pruning, hints or enumeration exclusions. This module never reconstructs a
layout algorithm or proves the original BuildingIR infeasible.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
import re
import time

from ortools.sat.python import cp_model


MAX_REQUIREMENTS = 128
MAX_VARIABLES = 20000
MAX_CONSTRAINTS = 30000
MAX_MODEL_BYTES = 16 * 1024 * 1024
MAX_JSON_BYTES = 8 * 1024 * 1024
MAX_RECORDS = 250000
SNAPSHOT_PHASE = "pre_pruning_support_hints_enumeration"


@dataclass(frozen=True)
class NamedConstraint:
    id: str
    constraint_indices: tuple[int, ...]
    source_paths: tuple[str, ...]
    source_ids: tuple[str, ...]


@dataclass(frozen=True)
class ConflictBudget:
    wall_seconds: float = 2.0
    max_solves: int = 64
    deterministic_units: float | None = .1


class _Incomplete(ValueError):
    pass


def _text(value, limit):
    if type(value) is not str or not value or len(value) > limit:
        raise _Incomplete("unsafe_metadata")
    if len(value.encode("utf-8")) > limit:
        raise _Incomplete("unsafe_metadata")
    return value


def _deadline(deadline):
    if deadline is not None and time.monotonic() >= deadline:
        raise _Incomplete("verification_budget_exhausted")


def _json_bytes(value, *, deadline=None):
    records = 0

    def visit(item, depth=0):
        nonlocal records
        records += 1
        if records % 1024 == 0:
            _deadline(deadline)
        if depth > 32 or records > MAX_RECORDS:
            raise _Incomplete("source_resource_limit")
        if type(item) is dict:
            if len(item) > MAX_RECORDS:
                raise _Incomplete("source_resource_limit")
            for key, entry in item.items():
                _text(key, 4096)
                visit(entry, depth + 1)
        elif type(item) is list:
            if len(item) > MAX_RECORDS:
                raise _Incomplete("source_resource_limit")
            for entry in item:
                visit(entry, depth + 1)
        elif type(item) is str:
            if len(item) > MAX_JSON_BYTES or len(item.encode("utf-8")) > MAX_JSON_BYTES:
                raise _Incomplete("source_resource_limit")
        elif type(item) is float:
            if not math.isfinite(item):
                raise _Incomplete("unsafe_metadata")
        elif type(item) is int:
            if item.bit_length() > 512:
                raise _Incomplete("unsafe_metadata")
        elif item is not None and type(item) is not bool:
            raise _Incomplete("unsafe_metadata")

    visit(value)
    chunks, size = [], 0
    encoder = json.JSONEncoder(sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    for chunk in encoder.iterencode(value):
        _deadline(deadline)
        raw = chunk.encode("utf-8")
        size += len(raw)
        if size > MAX_JSON_BYTES:
            raise _Incomplete("source_resource_limit")
        chunks.append(raw)
    return b"".join(chunks)


def _hash(raw):
    return hashlib.sha256(raw).hexdigest()


def _kind(constraint):
    if hasattr(constraint, "WhichOneof"):
        return constraint.WhichOneof("constraint")
    return next((name[4:] for name in dir(constraint) if name.startswith("has_")
        and callable(getattr(constraint, name)) and getattr(constraint, name)()), None)


def _model_bytes(model, *, deadline=None):
    if type(model) is not cp_model.CpModel:
        raise _Incomplete("unsafe_model")
    proto = model.Proto()
    if len(proto.variables) > MAX_VARIABLES or len(proto.constraints) > MAX_CONSTRAINTS:
        raise _Incomplete("model_resource_limit")
    # Check nested repeated fields and scalar text before allocating a clone or
    # the model's complete text representation. Supports protobuf and pybind.
    records, estimated = 0, 0

    def visit(value, depth=0):
        nonlocal records, estimated
        records += 1
        if records % 1024 == 0:
            _deadline(deadline)
        estimated += 64
        if depth > 16 or records > MAX_RECORDS or estimated > MAX_MODEL_BYTES:
            raise _Incomplete("model_resource_limit")
        if isinstance(value, (str, bytes)):
            size = len(value.encode("utf-8")) if isinstance(value, str) else len(value)
            estimated += size
            if size > MAX_MODEL_BYTES or estimated > MAX_MODEL_BYTES:
                raise _Incomplete("model_resource_limit")
        elif isinstance(value, (bool, int, float)):
            if isinstance(value, float) and not math.isfinite(value):
                raise _Incomplete("unsafe_model")
        elif hasattr(value, "ListFields"):
            for _, entry in value.ListFields():
                visit(entry, depth + 1)
        elif hasattr(value, "copy_from"):
            for name in dir(value):
                if name.startswith("_") or name.startswith(("has_", "clear_")):
                    continue
                has = getattr(value, "has_" + name, None)
                if callable(has) and not has():
                    continue
                entry = getattr(value, name)
                if callable(entry):
                    continue
                visit(entry, depth + 1)
        elif hasattr(value, "__len__") and (hasattr(value, "__iter__") or hasattr(value, "__getitem__")):
            if len(value) > MAX_RECORDS:
                raise _Incomplete("model_resource_limit")
            for entry in value:
                visit(entry, depth + 1)
        else:
            raise _Incomplete("unsafe_model")

    visit(proto)
    _deadline(deadline)
    raw = str(proto).encode("utf-8")
    if len(raw) > MAX_MODEL_BYTES:
        raise _Incomplete("model_resource_limit")
    return raw


def model_sha256(model):
    """Hash the actual bounded CP proto text in this installed OR-Tools runtime."""
    return _hash(_model_bytes(model))


def _pointer(source, path):
    _text(path, 1024)
    if not path.startswith("/") or re.search(r"~(?![01])", path):
        raise _Incomplete("invalid_source_path")
    value = source
    for segment in path[1:].split("/"):
        segment = segment.replace("~1", "/").replace("~0", "~")
        if type(value) is dict and segment in value:
            value = value[segment]
        elif type(value) is list and re.fullmatch(r"0|[1-9][0-9]*", segment) and int(segment) < len(value):
            value = value[int(segment)]
        else:
            raise _Incomplete("invalid_source_path")
    return value


def _registry(model, requirements, source, *, deadline=None):
    if type(requirements) not in (tuple, list) or len(requirements) > MAX_REQUIREMENTS:
        raise _Incomplete("registry_resource_limit")
    seen_ids, seen_indices, records = set(), set(), []
    for item in requirements:
        _deadline(deadline)
        if type(item) is not NamedConstraint or _text(item.id, 256) in seen_ids:
            raise _Incomplete("invalid_registry")
        seen_ids.add(item.id)
        for values in (item.constraint_indices, item.source_paths, item.source_ids):
            if type(values) is not tuple or not 1 <= len(values) <= 32:
                raise _Incomplete("invalid_registry")
        if len(set(item.source_paths)) != len(item.source_paths) or len(set(item.source_ids)) != len(item.source_ids):
            raise _Incomplete("invalid_registry")
        for index in item.constraint_indices:
            if type(index) is not int or index < 0 or index >= len(model.Proto().constraints) or index in seen_indices:
                raise _Incomplete("invalid_registry")
            seen_indices.add(index)
            constraint = model.Proto().constraints[index]
            if _kind(constraint) not in ("bool_or", "linear", "table"):
                raise _Incomplete("unsupported_terminal_constraint")
        for source_id in item.source_ids:
            _text(source_id, 256)
        values = [_pointer(source, path) for path in item.source_paths]
        records.append({"id": item.id, "constraint_indices": list(item.constraint_indices),
            "source_paths": list(item.source_paths), "source_ids": list(item.source_ids),
            "source_values": values, "source_values_sha256": _hash(_json_bytes(values, deadline=deadline))})
    return sorted(records, key=lambda record: record["id"])


def explain_finite_conflict(model, requirements, *, source, capture, expected_model_sha256,
                            budget=ConflictBudget()):
    """Independently solve a guarded clone; certify only completed core checks.

    Source paths bind the builder's declared registry; this generic CP helper
    cannot establish that arbitrary caller-provided clauses implement BuildingIR.
    The integration must register the actual original terminal clauses.
    """
    started = time.monotonic()
    report = {"schema_version": 1, "status": "UNKNOWN", "complete": False,
        "reason": None, "scope": "fixed_base_selected_candidate_family",
        "family_conditioning": "Source-dependent generated candidates stay fixed during requirement deletion",
        "proven_building_infeasible": False, "original_program_infeasibility_proven": False,
        "full_facility_validation": {"status": "UNKNOWN"}, "engineering_acceptance": "PENDING",
        "source": {}, "requirements": [], "core": {"requirement_ids": [],
            "independently_verified": False, "subset_minimal": False}, "searches": []}

    def stop(reason):
        report["reason"] = reason
        report["elapsed_seconds"] = time.monotonic() - started
        return report

    try:
        if type(budget) is not ConflictBudget or type(budget.wall_seconds) not in (int, float) or \
                not math.isfinite(budget.wall_seconds) or not 0 < budget.wall_seconds <= 10 or \
                type(budget.max_solves) is not int or not 1 <= budget.max_solves <= 512:
            raise _Incomplete("invalid_budget")
        if budget.deterministic_units is not None and (type(budget.deterministic_units) not in (int, float)
                or not math.isfinite(budget.deterministic_units) or not 0 < budget.deterministic_units <= 10):
            raise _Incomplete("invalid_budget")
        deadline = started + budget.wall_seconds
        _deadline(deadline)
        if type(source) is not dict or type(capture) is not dict:
            raise _Incomplete("unsafe_metadata")
        source_raw, capture_raw = _json_bytes(source, deadline=deadline), _json_bytes(capture, deadline=deadline)
        source = json.loads(source_raw)
        capture = json.loads(capture_raw)
        if capture.get("capture_status") != "COMPLETE":
            raise _Incomplete("incomplete_capture")
        if capture.get("snapshot_phase") != SNAPSHOT_PHASE or capture.get("scope") not in (
                "final_global_cp_selected_zone_shapes", "subtree_cp_selected_zone_shapes"):
            raise _Incomplete("unsafe_snapshot_phase")
        if type(capture.get("candidate_family")) is not dict:
            raise _Incomplete("unsafe_metadata")
        source_hash, family_hash = _hash(source_raw), _hash(_json_bytes(capture["candidate_family"], deadline=deadline))
        if source_hash != capture.get("source_sha256") or family_hash != capture.get("candidate_family_sha256"):
            raise _Incomplete("stale_source_or_family")
        raw_model = _model_bytes(model, deadline=deadline)
        model_hash = _hash(raw_model)
        if type(expected_model_sha256) is not str or not re.fullmatch(r"[0-9a-f]{64}", expected_model_sha256) or \
                model_hash != expected_model_sha256:
            raise _Incomplete("stale_model")
        records = _registry(model, requirements, source, deadline=deadline)
        if model.Validate():
            raise _Incomplete("model_invalid")
        proto = model.Proto()
        has_hint = proto.has_solution_hint() if hasattr(proto, "has_solution_hint") else proto.HasField("solution_hint")
        if proto.assumptions or (has_hint and proto.solution_hint.vars) or proto.search_strategy or \
                any(_kind(c) == "table" and c.table.negated for c in proto.constraints):
            raise _Incomplete("unsafe_snapshot_phase")
        report["source"] = {"source_sha256": source_hash, "candidate_family_sha256": family_hash,
            "model_sha256": model_hash, "capture_sha256": _hash(capture_raw)}
        report["requirements"] = records
        report["base"] = {"variables": len(proto.variables), "constraints": len(proto.constraints),
            "named_terminal_constraints": sum(len(record["constraint_indices"]) for record in records),
            "scope": "All other original model constraints, domains and fixed candidate choices"}
        report["source"]["registry_sha256"] = _hash(_json_bytes(records, deadline=deadline))
        _deadline(deadline)
        guarded = model.clone()
        guarded.ClearObjective()
        guarded.ClearHints()
        guards = {}
        for record in records:
            guard = guarded.NewBoolVar("conflict_requirement_" + record["id"])
            guards[record["id"]] = guard.Index()
            for index in record["constraint_indices"]:
                guarded.Proto().constraints[index].enforcement_literal.append(guard.Index())
        if guarded.Validate():
            raise _Incomplete("guarded_model_invalid")
        report["source"]["guarded_model_sha256"] = _hash(_model_bytes(guarded, deadline=deadline))
        remaining_units = budget.deterministic_units

        def solve(enabled, phase, removed=None):
            nonlocal remaining_units
            remaining = budget.wall_seconds - (time.monotonic() - started)
            if remaining <= 0 or len(report["searches"]) >= budget.max_solves or \
                    (remaining_units is not None and remaining_units <= 0):
                raise _Incomplete("verification_budget_exhausted")
            _deadline(deadline)
            trial = guarded.clone()
            trial.ClearAssumptions()
            trial.AddAssumptions([trial.get_bool_var_from_proto_index(index) if name in enabled
                else trial.get_bool_var_from_proto_index(index).Not() for name, index in guards.items()])
            if trial.Validate():
                raise _Incomplete("trial_model_invalid")
            remaining = deadline - time.monotonic()
            _deadline(deadline)
            solver = cp_model.CpSolver()
            solver.parameters.num_search_workers = 1
            solver.parameters.random_seed = 1
            solver.parameters.max_time_in_seconds = remaining
            if remaining_units is not None:
                solver.parameters.max_deterministic_time = remaining_units
            status = solver.Solve(trial)
            response = solver.ResponseProto()
            if any(type(value) not in (int, float) or not math.isfinite(value) or value < 0
                    for value in (response.wall_time, response.deterministic_time)):
                raise _Incomplete("unsafe_solver_response")
            if remaining_units is not None:
                remaining_units -= response.deterministic_time
            record = {"phase": phase, "enabled_requirement_ids": sorted(enabled),
                "removed_requirement_id": removed, "model_status": solver.StatusName(status),
                "wall_seconds": response.wall_time, "deterministic_units": response.deterministic_time,
                "workers": 1, "solution": list(response.solution) if status in (cp_model.OPTIMAL, cp_model.FEASIBLE) else []}
            report["searches"].append(record)
            if status in (cp_model.OPTIMAL, cp_model.FEASIBLE) and (
                    len(record["solution"]) != len(trial.Proto().variables) or
                    any(type(value) is not int for value in record["solution"])):
                raise _Incomplete("missing_sat_witness")
            if time.monotonic() - started > budget.wall_seconds:
                raise _Incomplete("verification_budget_exhausted")
            if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE, cp_model.INFEASIBLE):
                raise _Incomplete("solver_" + solver.StatusName(status).lower())
            return status, solver

        base_status, _ = solve(set(), "base_check")
        if base_status == cp_model.INFEASIBLE:
            return stop("base_model_infeasible")
        all_ids = set(guards)
        status, solver = solve(all_ids, "all_requirements")
        if status != cp_model.INFEASIBLE:
            report.update(status="NO_CONFLICT", complete=True)
            return stop("fixed_model_feasible")
        raw_core = list(solver.SufficientAssumptionsForInfeasibility())
        inverse = {index: name for name, index in guards.items()}
        if not raw_core or any(index not in inverse for index in raw_core) or len(set(raw_core)) != len(raw_core):
            raise _Incomplete("missing_or_unsafe_core")
        core = {inverse[index] for index in raw_core}
        report["core"]["requirement_ids"] = sorted(core)
        status, _ = solve(core, "core_recheck")
        if status != cp_model.INFEASIBLE:
            raise _Incomplete("core_recheck_not_infeasible")
        report["core"]["independently_verified"] = True
        for name in sorted(core):
            status, _ = solve(core - {name}, "deletion", name)
            if status == cp_model.INFEASIBLE:
                core.remove(name)
                report["core"]["requirement_ids"] = sorted(core)
        if not core:
            raise _Incomplete("empty_core")
        status, _ = solve(core, "final_core_recheck")
        if status != cp_model.INFEASIBLE:
            raise _Incomplete("final_core_not_infeasible")
        for name in sorted(core):
            status, _ = solve(core - {name}, "final_deletion", name)
            if status == cp_model.INFEASIBLE:
                raise _Incomplete("final_core_not_subset_minimal")
        report["core"]["subset_minimal"] = True
        report.update(status="EXPLAINED", complete=True)
        return stop("checked_subset_minimal_core")
    except _Incomplete as exc:
        return stop(str(exc))
    except (ValueError, TypeError, OverflowError, UnicodeError, KeyError) as exc:
        return stop("unsafe_input:" + type(exc).__name__)

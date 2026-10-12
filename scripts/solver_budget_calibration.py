"""Measure actual deterministic work on the rectangular monolithic room model."""

import argparse
import csv
import hashlib
from importlib.metadata import version
import json
import math
import os
from pathlib import Path
import platform
import statistics
import time

from layout_configurator import models, schema, search, solver, validation
from layout_configurator.io import load_building, load_spec
from layout_configurator.schema import load_mapping
from layout_configurator.validation import validate_layout


def _hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()


def _file_hash(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _fingerprint():
    return _hash({"modules": {module.__name__: _file_hash(module.__file__)
                              for module in (models, schema, search, solver, validation)},
                  "runner": _file_hash(__file__),
                  "dependencies": {name: version(name) for name in ("ortools", "shapely", "jsonschema", "PyYAML")},
                  "python": platform.python_version()})


def _write(root, report):
    root.mkdir(parents=True, exist_ok=True)
    (root / "calibration.json").write_text(json.dumps(report, ensure_ascii=False, indent=2,
                                                     allow_nan=False) + "\n", encoding="utf-8")
    fields = ("program", "room_count", "seed", "repetition", "budget_deterministic_units",
              "model_status", "deterministic_time", "wall_time_seconds", "units_per_solver_second",
              "orchestration_seconds", "geometry_ok", "result_sha256")
    with (root / "calibration.csv").open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(report["samples"])


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("program", type=Path, nargs="+")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--budgets", type=float, nargs="+", default=[.05, .1, .25])
    parser.add_argument("--seeds", type=int, nargs="+", default=[1, 7])
    parser.add_argument("--repetitions", type=int, default=2)
    args = parser.parse_args(argv)
    report = {"status": "RUNNING", "backend": "monolithic_room_model", "samples": [],
              "scope": "Measured consumed CP-SAT work and room geometry; not facility acceptance, equipment, flows, or an end-to-end seconds estimate",
              "decision": "retain_wall_clock_facility_scale_matrix",
              "decision_reason": "These measurements cover the room model only. Hierarchical per-stage budgets and end-to-end acceptance must be calibrated before changing the scale matrix."}
    try:
        if (not 1 <= args.repetitions <= 10 or len(set(args.seeds)) != len(args.seeds)
                or len(set(args.budgets)) != len(args.budgets)
                or any(not math.isfinite(value) or not 0 < value <= 100 for value in args.budgets)
                or len(set(path.resolve() for path in args.program)) != len(args.program)):
            raise ValueError("Use distinct sources/seeds/budgets, 1–10 repetitions and finite budgets in (0,100]")
        programs = {}
        for path in args.program:
            raw = load_mapping(path)
            programs[path] = load_building(path).layout if isinstance(raw, dict) and "layout" in raw else load_spec(path)
        fingerprint = _fingerprint()
        sources = {str(path): {"sha256": _file_hash(path), "canonical_sha256": _hash(spec.to_dict()),
                               "room_count": len(spec.rooms)} for path, spec in programs.items()}
    except (OSError, ValueError, TypeError, OverflowError) as exc:
        report.update(status="INPUT_REJECTED", message=str(exc))
        _write(args.output, report)
        return 2
    report.update(runtime_fingerprint=fingerprint, sources=sources,
                  ci_commit=os.environ.get("GITHUB_SHA"), python=platform.python_version(),
                  platform=platform.platform(), processor=platform.processor(), logical_cpus=os.cpu_count(),
                  ortools_version=version("ortools"), settings={"seeds": args.seeds,
                      "budgets": args.budgets, "repetitions": args.repetitions, "workers": 1})
    _write(args.output, report)
    try:
        signatures = {}
        for path, spec in programs.items():
            for budget in args.budgets:
                for seed in args.seeds:
                    for repetition in range(1, args.repetitions + 1):
                        measurements, results = [], []
                        started = time.monotonic()
                        try:
                            results = solver.solve_layouts(spec, 1, 1, seed,
                                deterministic_units=budget, statistics=measurements)
                        except solver.InfeasibleLayout:
                            pass  # The response records UNKNOWN/INFEASIBLE; no accepted layout is invented.
                        elapsed = time.monotonic() - started
                        if len(measurements) != 1:
                            raise ValueError("Expected one actual CP-SAT response per calibration sample")
                        row = {"program": str(path), "room_count": len(spec.rooms), "repetition": repetition,
                               **measurements[0], "orchestration_seconds": elapsed,
                               "result_sha256": _hash(results[0].to_dict()) if results else None,
                               "geometry_ok": validate_layout(spec, results[0]).ok if results else None}
                        if (row["model_status"] not in {"OPTIMAL", "FEASIBLE", "UNKNOWN", "INFEASIBLE"}
                                or row["geometry_ok"] is False or row["workers"] != 1
                                or row["wall_time_seconds"] <= 0 or row["deterministic_time"] < 0
                                or not all(math.isfinite(row[key]) for key in ("wall_time_seconds", "deterministic_time"))):
                            raise ValueError("Invalid solver response or independently rejected geometry")
                        row["units_per_solver_second"] = row["deterministic_time"] / row["wall_time_seconds"]
                        report["samples"].append(row)
                        if _file_hash(path) != sources[str(path)]["sha256"] or _fingerprint() != fingerprint:
                            raise ValueError("Source or relevant implementation changed during calibration")
                        key = (str(path), budget, seed)
                        signature = (row["model_status"], row["result_sha256"], row["deterministic_time"])
                        if key in signatures and signatures[key] != signature:
                            raise ValueError("Repeated single-worker deterministic samples changed status, geometry or consumed work")
                        signatures[key] = signature
                        _write(args.output, report)
        report.update(status="CALIBRATED", median_units_per_solver_second=statistics.median(
            row["units_per_solver_second"] for row in report["samples"]))
    except (OSError, ValueError, TypeError, RuntimeError, OverflowError) as exc:
        report.update(status="MEASUREMENT_FAILED", message=str(exc))
        _write(args.output, report)
        return 4
    _write(args.output, report)
    print(f"CALIBRATED: {len(report['samples'])} samples; {args.output / 'calibration.csv'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

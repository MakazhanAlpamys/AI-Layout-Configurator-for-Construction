"""Generate and independently verify a pinned multi-floor scale series."""

import argparse
import hashlib
from importlib.metadata import version
import json
import math
import os
from pathlib import Path
import platform
import time

from layout_configurator.cli import main as layout_main
from layout_configurator.facility_floors import load_facility_floors
from layout_configurator.facility import default_facility_profile, load_facility_profile
from layout_configurator.api import _runtime_fingerprint


def _write(path, report):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def _source_hashes(path, spec):
    paths = {path.resolve(), *(Path(floor.program_path) for floor in spec.floors)}
    paths.update(Path(floor.profile_path) for floor in spec.floors if floor.profile_path)
    return {str(item): hashlib.sha256(item.read_bytes()).hexdigest() for item in sorted(paths)}


def _profile_hashes(spec):
    return {str(floor.level): hashlib.sha256(json.dumps(
        (load_facility_profile(floor.profile_path) if floor.profile_path else default_facility_profile()).to_dict(),
        sort_keys=True, allow_nan=False, ensure_ascii=False, separators=(",", ":")).encode("utf-8")).hexdigest()
        for floor in spec.floors}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("spec", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--seeds", type=int, nargs="+", default=[1, 7, 42])
    parser.add_argument("--variants", type=int, default=1)
    parser.add_argument("--max-attempts", type=int, default=3)
    parser.add_argument("--time-limit", type=float, default=40)
    parser.add_argument("--minimum-rooms", type=int, default=40)
    parser.add_argument("--room-solver", choices=("auto", "hierarchical", "monolithic"), default="auto")
    parser.add_argument("--check-only", action="store_true", help="independently recheck existing seed_N bundles")
    args = parser.parse_args(argv)
    report_path = args.output / "acceptance-report.json"
    report = {"ok": False, "status": "RUNNING", "spec": str(args.spec), "seeds": []}
    try:
        if (args.minimum_rooms < 1 or args.variants < 1 or args.max_attempts < args.variants or
                not math.isfinite(args.time_limit) or args.time_limit <= 0 or
                len(set(args.seeds)) != len(args.seeds)):
            raise ValueError("Supply positive counts/time, distinct seeds and enough attempts")
        spec = load_facility_floors(args.spec)
        room_count = sum(len(floor.building.layout.rooms) for floor in spec.floors)
        if room_count < args.minimum_rooms:
            raise ValueError(f"The acceptance program needs at least {args.minimum_rooms} rooms; found {room_count}")
        if spec.structural_grid is None:
            raise ValueError("Multi-floor scale acceptance requires a declared shared structural grid")
        source_hashes = _source_hashes(args.spec, spec)
        profile_hashes = _profile_hashes(spec)
        fingerprint = _runtime_fingerprint()
        runner_hash = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    except (OSError, ValueError, TypeError) as exc:
        report.update(status="INPUT_REJECTED", message=str(exc))
        _write(report_path, report)
        return 2
    report.update(room_count=room_count, floor_count=len(spec.floors), source_sha256=source_hashes,
                  profile_sha256=profile_hashes, runtime_fingerprint=fingerprint, runner_sha256=runner_hash,
                  ci_commit=os.environ.get("GITHUB_SHA"), mode="read-only" if args.check_only else "generate-and-check",
                  python=platform.python_version(), platform=platform.platform(),
                  dependencies={name: version(name) for name in ("ortools", "shapely", "ezdxf", "reportlab", "pypdf", "ifcopenshell")},
                  settings={"variants": args.variants, "max_attempts": args.max_attempts,
                            "time_limit_seconds_per_solve": args.time_limit, "room_solver": args.room_solver})
    _write(report_path, report)
    for seed in args.seeds:
        seed_dir = args.output / f"seed_{seed}"
        started = time.monotonic()
        entry = {"seed": seed, "ok": False, "output": str(seed_dir)}
        try:
            code = 0 if args.check_only else layout_main([
                "generate-facility-floors", str(args.spec), "--output", str(seed_dir),
                "--variants", str(args.variants), "--max-attempts", str(args.max_attempts),
                "--time-limit", str(args.time_limit), "--seed", str(seed),
                "--room-solver", args.room_solver, "--unified-ifc",
            ])
            if code:
                entry.update(status="GENERATION_FAILED", exit_code=code)
            else:
                from layout_configurator.facility_floors_qa import validate_facility_floors_bundle

                qa = validate_facility_floors_bundle(spec, seed_dir, expected_stacks=args.variants)
                entry.update(status="PASS" if qa.ok else "CHECK_FAILED", ok=qa.ok, qa=qa.to_dict())
            if _source_hashes(args.spec, spec) != source_hashes:
                entry.update(status="SOURCE_CHANGED", ok=False, message="Source program/profile changed during acceptance")
            if (_runtime_fingerprint() != fingerprint or _profile_hashes(spec) != profile_hashes
                    or hashlib.sha256(Path(__file__).read_bytes()).hexdigest() != runner_hash):
                entry.update(status="IMPLEMENTATION_CHANGED", ok=False,
                             message="Executable source, assets, versions or effective profile changed during acceptance")
        except (ImportError, OSError, ValueError, RuntimeError, TypeError) as exc:
            entry.update(status="CHECK_FAILED", message=str(exc))
        entry["elapsed_seconds"] = round(time.monotonic() - started, 3)
        report["seeds"].append(entry)
        _write(report_path, report)
        print(f"seed {seed}: {entry['status']}; {entry['elapsed_seconds']:.3f} s")
    report.update(ok=all(entry["ok"] for entry in report["seeds"]),
                  status="PASS" if all(entry["ok"] for entry in report["seeds"]) else "FAIL")
    _write(report_path, report)
    return 0 if report["ok"] else 4


if __name__ == "__main__":
    raise SystemExit(main())

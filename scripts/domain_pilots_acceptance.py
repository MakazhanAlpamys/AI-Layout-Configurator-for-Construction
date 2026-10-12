"""Generate coordinate-free domain pilots and independently verify saved bundles.

The existing rule packs are project coordination policies. PASS covers declared
software predicates and exchange artifacts; engineering acceptance stays PENDING.
"""

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import time

import ifcopenshell
import ifcopenshell.validate
from jsonschema import Draft202012Validator
import yaml

from layout_configurator.api import _runtime_fingerprint
from layout_configurator.cli import main as layout_main
from layout_configurator.facility import load_facility_profile
from layout_configurator.io import load_building
from layout_configurator.qa import validate_building_set
from layout_configurator.schema import load_json_schema


ROOT = Path(__file__).resolve().parents[1]
DOMAINS = ("laboratory", "hospital", "industrial")


def _sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _digest(raw):
    return hashlib.sha256(json.dumps(raw, sort_keys=True, ensure_ascii=False,
        allow_nan=False, separators=(",", ":")).encode("utf-8")).hexdigest()


def _write(path, raw):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(raw, ensure_ascii=False, allow_nan=False, indent=2) + "\n", encoding="utf-8")


def _inputs(domain):
    source = ROOT / "examples" / f"{domain}_pilot.yaml"
    profile_source = ROOT / "rules" / f"{domain}_pilot.yaml"
    raw = yaml.safe_load(source.read_text(encoding="utf-8"))
    for schema_name, payload in (("building_ir.schema.json", raw),
                                 ("layout_ir.schema.json", raw.get("layout") if isinstance(raw, dict) else None)):
        error = next(Draft202012Validator(load_json_schema(ROOT / "schemas" / schema_name)).iter_errors(payload), None)
        if error is not None:
            raise ValueError(f"{domain} canonical source {error.json_path}: {error.message}")
    building = load_building(source)
    if building.to_dict() != raw:
        raise ValueError(f"The {domain} pilot must already be canonical, without implicit source normalization")
    profile = load_facility_profile(profile_source)
    if profile.domain != domain:
        raise ValueError(f"The {domain} rule pack declares a different typology")
    return source, profile_source, building.with_required_flow_opening_width(), profile


def main(argv=None):
    started = time.monotonic()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--domains", choices=DOMAINS, nargs="+", default=list(DOMAINS))
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--time-limit", type=float, default=5)
    parser.add_argument("--deterministic-budget", type=float, default=.25)
    parser.add_argument("--max-attempts", type=int, default=3)
    parser.add_argument("--check-only", action="store_true", help="independently recheck existing domain bundles")
    args = parser.parse_args(argv)
    report_path = args.output / "acceptance-report.json"
    report = {"ok": False, "status": "RUNNING", "engineering_acceptance": "PENDING", "pilots": [],
        "scope": "Existing project flow policies, persisted artifacts and strict IFC EXPRESS; no domain certification",
        "open_profile_scope": ["No zone-field/type, pressure, classification, airlock or process-sequence rule is declared"],
        "mode": "read-only" if args.check_only else "generate-and-check",
        "timing_scope": "In-process CLI, persisted QA and strict IFC EXPRESS; excludes Python process startup"}
    try:
        if (len(set(args.domains)) != len(args.domains) or args.max_attempts < 1 or
                not math.isfinite(args.time_limit) or args.time_limit <= 0 or
                not math.isfinite(args.deterministic_budget) or args.deterministic_budget <= 0):
            raise ValueError("Use distinct domains, positive attempts and positive finite solver limits")
        inputs = {domain: _inputs(domain) for domain in args.domains}
        hashes = {str(path): _sha(path) for values in inputs.values() for path in values[:2]}
        effective = {domain: _digest(values[3].to_dict()) for domain, values in inputs.items()}
        fingerprint = _runtime_fingerprint()
        runner_hash = _sha(Path(__file__))
    except (OSError, ValueError, TypeError, yaml.YAMLError) as exc:
        report.update(status="INPUT_REJECTED", message=str(exc), elapsed_seconds=round(time.monotonic() - started, 3))
        _write(report_path, report)
        return 2
    report.update(runtime_fingerprint=fingerprint, runner_sha256=runner_hash, ci_commit=os.environ.get("GITHUB_SHA"),
        python=platform.python_version(), platform=platform.platform(),
        settings={"seed": args.seed, "variants": 1, "max_attempts": args.max_attempts,
            "time_limit_seconds_per_solve": args.time_limit, "deterministic_units": args.deterministic_budget,
            "room_solver": "monolithic", "strict_ifc_express": True})
    _write(report_path, report)
    for domain, (source, profile_source, building, profile) in inputs.items():
        begin = time.monotonic()
        output = args.output / domain
        entry = {"domain": domain, "ok": False, "status": "RUNNING", "source": str(source),
            "source_sha256": hashes[str(source)], "profile_source": str(profile_source),
            "profile_source_sha256": hashes[str(profile_source)], "effective_profile_sha256": effective[domain],
            "profile_provenance": profile.to_dict(), "room_count": len(building.layout.rooms),
            "flow_types": sorted({flow.type for flow in building.flows}), "output": str(output)}
        try:
            generation_started = time.monotonic()
            code = 0 if args.check_only else layout_main(["generate-building", str(source),
                "--profile", str(profile_source), "--output", str(output), "--variants", "1",
                "--seed", str(args.seed), "--max-attempts", str(args.max_attempts),
                "--time-limit", str(args.time_limit), "--deterministic-budget", str(args.deterministic_budget),
                "--room-solver", "monolithic"])
            entry["generation_seconds"] = round(time.monotonic() - generation_started, 3)
            if code:
                entry.update(status="GENERATION_FAILED", exit_code=code)
            else:
                check_started = time.monotonic()
                saved = json.loads((output / "building_01.json").read_text(encoding="utf-8"))
                if (saved.get("spec") != building.to_dict() or
                        saved.get("facility_validation", {}).get("profile") != profile.to_dict()):
                    raise ValueError("Saved program/profile differs from the exact canonical source semantics")
                qa = validate_building_set(output, expected_variants=1, profile=profile)
                _write(output / "qa-report.json", qa.to_dict())
                entry.update(qa=qa.to_dict(), persisted_qa_seconds=round(time.monotonic() - check_started, 3))
                express_started = time.monotonic()
                model = ifcopenshell.open(output / "building_01.ifc")
                logger = ifcopenshell.validate.json_logger()
                ifcopenshell.validate.validate(model, logger, express_rules=True)
                statements = [{"level": item.get("level"), "message": item.get("message")} for item in logger.statements]
                entry["ifc_express"] = {"ok": not statements, "express_rules": True, "statements": statements,
                    "ifc_sha256": _sha(output / "building_01.ifc")}
                entry["ifc_express_seconds"] = round(time.monotonic() - express_started, 3)
                entry["artifact_sha256"] = [{"file": path.name, "sha256": _sha(path), "bytes": path.stat().st_size}
                    for path in sorted(output.iterdir()) if path.is_file()]
                ok = qa.ok and not statements
                entry.update(ok=ok, status="PASS" if ok else "CHECK_FAILED")
            if any(_sha(Path(path)) != digest for path, digest in hashes.items()):
                entry.update(ok=False, status="SOURCE_CHANGED")
            if (_runtime_fingerprint() != fingerprint or _sha(Path(__file__)) != runner_hash or
                    _digest(load_facility_profile(profile_source).to_dict()) != effective[domain]):
                entry.update(ok=False, status="IMPLEMENTATION_CHANGED")
        except (ImportError, OSError, ValueError, RuntimeError, TypeError, KeyError, ifcopenshell.Error) as exc:
            entry.update(ok=False, status="CHECK_FAILED", message=str(exc))
        entry["elapsed_seconds"] = round(time.monotonic() - begin, 3)
        report["pilots"].append(entry)
        _write(report_path, report)
        print(f"{domain}: {entry['status']}; {entry['elapsed_seconds']:.3f} s")
    ok = all(entry["ok"] for entry in report["pilots"])
    report.update(ok=ok, status="PASS" if ok else "FAIL", elapsed_seconds=round(time.monotonic() - started, 3))
    _write(report_path, report)
    return 0 if ok else 4


if __name__ == "__main__":
    raise SystemExit(main())

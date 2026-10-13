"""Measure one source-pinned synthetic80 warm-start and its complete bundle QA.

This is an explicit A/B clone of a supplied accepted40 layout, not arbitrary
80-room generation from scratch or completion of recursive zone decomposition.
The parent bounds the entire worker, including imports, preparation and exports.
"""

import argparse
import copy
import hashlib
from importlib.metadata import distribution, distributions
import json
import math
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import time


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_FIXTURE = REPO_ROOT / "tests" / "fixtures" / "warmstart80"
SOURCE_COMMIT = "35eff27f334a0b77741cabf841f8c5000046b316"


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()


def file_digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def new_report(max_seconds, *, room_solver="hierarchical", zone_global_seconds=None, zone_global_workers=1,
               zone_global_diagnostics=False, zone_contact_pruning=False, zone_contact_support=False):
    if type(zone_global_diagnostics) is not bool:
        raise ValueError("zone-global-diagnostics must be a boolean")
    if type(zone_contact_pruning) is not bool:
        raise ValueError("zone-contact-pruning must be a boolean")
    if type(zone_contact_support) is not bool:
        raise ValueError("zone-contact-support must be a boolean")
    report = {
        "status": "RUNNING", "accepted": False, "source_commit": SOURCE_COMMIT,
        "scope": "Source-pinned synthetic A/B clone of supplied accepted40 with complete persisted bundle acceptance",
        "general_scratch80_acceptance": False, "recursive_zone_decomposition_complete": False,
        "generating_supplied40_from_scratch_included": False,
        "timing_scope": "Whole runner request: worker startup/imports, source preparation/validation/derivation, rooms, equipment, routes, exports and independent persisted bundle QA",
        "settings": {"max_total_seconds": max_seconds, "seed": 7, "variants": 1,
                     "room_solver": room_solver, "room_seconds_per_model": 2,
                     "equipment_seconds_per_model": 5, "cache_cluster_shapes": True,
                     "hint_completion_seconds_per_model": 1, "hint_repair_workers": 1,
                     "deterministic_units": None, "strict_ifc_express": True},
        "stages": {}, "stage_hashes": {}, "source_gates": {}, "gates": {},
        "final_hint_positions_fixed": False,
    }
    _record_zone_search(report, zone_global_seconds, zone_global_workers, room_seconds=2,
                        diagnostics=zone_global_diagnostics, contact_pruning=zone_contact_pruning,
                        **({"contact_support": True} if zone_contact_support else {}))
    return report


def _record_zone_search(report, seconds, workers, *, room_seconds, diagnostics=False, contact_pruning=False,
                        contact_support=False):
    if seconds is not None or workers != 1:
        value = room_seconds if seconds is None else seconds
        if type(value) in (int, float) and not math.isfinite(value):
            value = str(value)
        report["settings"].update(zone_global_seconds_per_model=value, zone_global_workers=workers)
    if diagnostics:
        report["settings"]["zone_global_diagnostics"] = True
    if contact_pruning:
        report["settings"]["zone_contact_pruning"] = True
    if contact_support:
        report["settings"]["zone_contact_support"] = True


def _zone_search_options(room_solver, seconds, workers, *, diagnostics=False, contact_pruning=False,
                         contact_support=False):
    if type(diagnostics) is not bool:
        raise ValueError("zone-global-diagnostics must be a boolean")
    if type(contact_pruning) is not bool:
        raise ValueError("zone-contact-pruning must be a boolean")
    if type(contact_support) is not bool:
        raise ValueError("zone-contact-support must be a boolean")
    if type(workers) is not int or not 1 <= workers <= 8:
        raise ValueError("zone-global-workers must be an integer in 1..8")
    if seconds is not None and (type(seconds) not in (int, float) or not 0 < seconds <= 120):
        raise ValueError("zone-global-seconds must be finite and in (0,120]")
    if room_solver != "zone_recursive" and (seconds is not None or workers != 1 or diagnostics or contact_pruning or contact_support):
        raise ValueError("Nondefault zone-global controls require room-solver zone_recursive")
    options = {}
    if seconds is not None:
        options["global_time_limit_seconds"] = seconds
    if workers != 1:
        options["global_workers"] = workers
    if diagnostics:
        options["global_diagnostics"] = True
    if contact_pruning:
        options["prune_cross_root_contacts"] = True
    if contact_support:
        options["cross_root_contact_support"] = True
    return options


def _requested_zone_controls(args):
    controls = {}
    if args.zone_global_seconds is not None:
        controls["zone_global_seconds"] = args.zone_global_seconds
    if args.zone_global_workers != 1:
        controls["zone_global_workers"] = args.zone_global_workers
    if args.zone_global_diagnostics:
        controls["zone_global_diagnostics"] = True
    if args.zone_contact_pruning:
        controls["zone_contact_pruning"] = True
    if args.zone_contact_support:
        controls["zone_contact_support"] = True
    return controls


def write_report(output, report):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    temporary = output / "acceptance-report.tmp"
    temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    temporary.replace(output / "acceptance-report.json")


def runtime_state():
    import layout_configurator
    from layout_configurator import resources

    package = Path(layout_configurator.__file__).resolve().parent
    installed = distribution("layout-configurator")
    distribution_root = Path(installed.locate_file("")).resolve()
    direct_url = json.loads(installed.read_text("direct_url.json") or "{}")
    editable = bool(direct_url.get("dir_info", {}).get("editable", False))
    suffixes = {".py", ".json", ".ttf", ".txt", ".html", ".css", ".js", ".yaml", ".yml", ".ids", ".xml"}
    files = {f"package/{path.relative_to(package).as_posix()}": file_digest(path)
             for path in sorted(package.rglob("*")) if path.is_file() and path.suffix in suffixes}
    files.update({f"schemas/{path.relative_to(REPO_ROOT / 'schemas').as_posix()}": file_digest(path)
                  for path in sorted((REPO_ROOT / "schemas").rglob("*.json"))})
    resource_roots = [package / "_data"]
    source_root = Path(resources.__file__).resolve().parents[2]
    if (source_root / "pyproject.toml").is_file():
        resource_roots.append(source_root)
    resource_names = {path.relative_to(root).as_posix()
                      for root in resource_roots for category in ("schemas", "rules", "ids")
                      for path in (root / category).rglob("*") if path.is_file()}
    resolved_resources = {}
    for name in sorted(resource_names):
        actual = resources.resource_path(name).resolve()
        sha256 = file_digest(actual)
        resolved_resources[name] = {"path": str(actual), "sha256": sha256}
        files["resource/" + name] = sha256
    files["runner"] = file_digest(__file__)
    dependencies = {item.metadata["Name"]: item.version for item in distributions() if item.metadata.get("Name")}
    state = {"files": files, "resolved_resources": resolved_resources,
             "dependencies": dict(sorted(dependencies.items())),
             "python": platform.python_version(), "platform": platform.platform(),
             "package_root": str(package), "distribution_root": str(distribution_root),
             "editable_package": editable,
             "installed_package": package.is_relative_to(distribution_root) and not editable}
    return {**state, "sha256": digest(state)}


def fixture_state(root):
    root = Path(root)
    return {path.name: file_digest(path) for path in sorted(root.iterdir()) if path.is_file()}


def derive_program(source):
    """Clone every source attribute, qualifying references and adding one bridge."""
    from layout_configurator.building import BuildingIR

    if source.layout.boundary.cutouts or source.layout.external_entry or source.layout.windows or source.layout.doors:
        raise ValueError("This pinned derivation requires no source cutouts or explicit openings/entry")
    original = source.to_dict()
    clone = copy.deepcopy(original)
    title = "Explicit synthetic 80-room duplication of scale_40_rooms"
    clone["project_name"] = clone["layout"]["project_name"] = title
    width = source.layout.boundary.width_mm
    clone["layout"]["boundary"]["width"] = 2 * width
    clone["layout"]["entry_room"] = "A/" + source.layout.entry_room
    for category in ("rooms", "zones", "equipment", "flows"):
        rows = original["layout"][category] if category == "rooms" else original[category]
        result = []
        for prefix in ("A/", "B/"):
            for original_row in rows:
                row = copy.deepcopy(original_row)
                row["id"] = prefix + row["id"]
                for field in ("room_id", "parent_zone_id"):
                    if row.get(field):
                        row[field] = prefix + row[field]
                for field in ("room_ids", "required_adjacency", "preferred_adjacency", "forbidden_adjacency", "from_ids", "to_ids"):
                    if field in row:
                        row[field] = [prefix + member for member in row[field]]
                if category == "rooms" and row["id"] == "A/receiving":
                    row["required_adjacency"].append("B/receiving")
                result.append(row)
        if category == "rooms":
            clone["layout"][category] = result
        else:
            clone[category] = result
    if source.structural_grid is not None:
        grid = clone["structural_grid"]
        grid["axes_x_mm"] = [*grid["axes_x_mm"], *(width + value for value in grid["axes_x_mm"])]
        grid["labels_x"] = [prefix + label for prefix in ("A/", "B/") for label in grid["labels_x"]]
    return BuildingIR.from_mapping(clone)


def prepare_fixture(root, report):
    from layout_configurator.equipment import validate_equipment_layout
    from layout_configurator.facility import FacilityProfile, load_facility_profile, validate_building
    from layout_configurator.flows import route_flows, validate_flow_routes
    from layout_configurator.io import load_building, load_building_result
    from layout_configurator.models import LayoutResult, Rect
    from layout_configurator.validation import validate_layout

    started = time.monotonic()
    root = Path(root).resolve()
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    if manifest.get("version") != 1 or manifest.get("source_commit") != SOURCE_COMMIT:
        raise ValueError("Fixture source commit/version differs from the pinned pilot")
    if set(manifest.get("files", {})) != {"raw40", "accepted40", "profile", "program80"}:
        raise ValueError("Fixture must pin raw40, accepted40, profile and program80")
    if digest(manifest["derivation"]) != manifest.get("derivation_sha256"):
        raise ValueError("Fixture derivation hash differs from its declared policy")
    paths = {}
    for key, entry in manifest["files"].items():
        path = (root / entry["path"]).resolve()
        if path.parent != root or file_digest(path) != entry["sha256"]:
            raise ValueError(f"Fixture hash/path mismatch: {key}")
        paths[key] = path
    report.update(source_ci=manifest["source_ci"], source_files=manifest["files"],
                  fixture_sha256=digest(fixture_state(root)))
    raw40 = load_building(paths["raw40"])
    source, layout, equipment = load_building_result(paths["accepted40"])
    prepared40 = raw40.with_required_flow_opening_width()
    if prepared40.to_dict() != source.to_dict() or len(source.layout.rooms) != 40:
        raise ValueError("Accepted40 identity differs from the prepared pinned raw40 program")
    profile = load_facility_profile(paths["profile"])
    accepted_payload = json.loads(paths["accepted40"].read_text(encoding="utf-8"))
    if FacilityProfile.from_mapping(accepted_payload["facility_validation"]["profile"]).to_dict() != profile.to_dict():
        raise ValueError("The accepted40 profile differs from the full pinned original profile")
    layout_report = validate_layout(source.layout, layout)
    equipment_report = validate_equipment_layout(source, layout, equipment)
    routes = route_flows(source, layout, equipment)
    flow_report = validate_flow_routes(source, layout, routes, equipment)
    facility = validate_building(source, layout, equipment, routes, flow_report,
                                profile=profile, equipment_report=equipment_report)
    report["source_gates"] = {"layout": {"ok": layout_report.ok, "issues": [item.message for item in layout_report.issues]},
                              "equipment": equipment_report.to_dict(), "flows": flow_report.to_dict(),
                              "facility": facility.to_dict()}
    if not all(gate["ok"] for gate in report["source_gates"].values()):
        raise ValueError("Independent validation rejects the supplied accepted40 fixture")
    clone = derive_program(raw40)
    if clone.to_dict() != load_building(paths["program80"]).to_dict():
        raise ValueError("Pinned program80 is not the exact unweakened A/B source clone plus receiving bridge")
    building = clone.with_required_flow_opening_width()
    width = max(rect.right for rect in layout.placements.values())
    hints = {}
    for room_id, rect in layout.placements.items():
        hints["A/" + room_id] = Rect(width - rect.right, rect.y, rect.width, rect.height)
        hints["B/" + room_id] = Rect(width + rect.x, rect.y, rect.width, rect.height)
    if not validate_layout(building.layout, LayoutResult(1, hints)).ok:
        raise ValueError("Derived warm-start geometry fails independent original room constraints")
    report["derivation"] = {**manifest["derivation"], "exact_unweakened_clone_except_bridge": True,
                            "occupied_width_mm": width,
                            "bridge_shared_mm": hints["A/receiving"].shared_boundary(hints["B/receiving"])}
    hashes = {"source40_program": raw40.to_dict(), "source40_prepared": source.to_dict(),
              "source40_layout": layout.to_dict(), "source40_equipment": equipment.to_dict(source),
              "source40_flows": routes.to_dict(), "source40_facility": facility.to_dict(), "profile": profile.to_dict(),
              "program80_raw": clone.to_dict(), "program80_prepared": building.to_dict(),
              "hints80": LayoutResult(1, hints).to_dict(), "derivation": report["derivation"]}
    report["stage_hashes"].update({key: digest(value) for key, value in hashes.items()})
    report["stages"]["source_preparation_seconds"] = time.monotonic() - started
    return building, hints, profile


def accept_program(building, hints, profile, report, output, *, room_seconds=2, equipment_seconds=5,
                   seed=7, checkpoint=lambda: None, room_solver="hierarchical",
                   zone_global_seconds=None, zone_global_workers=1, zone_global_diagnostics=False,
                   zone_contact_pruning=False, zone_contact_support=False):
    if type(zone_contact_support) is not bool:
        raise ValueError("zone-contact-support must be a boolean")
    zone_options = _zone_search_options(room_solver, zone_global_seconds, zone_global_workers,
                                       diagnostics=zone_global_diagnostics, contact_pruning=zone_contact_pruning,
                                       **({"contact_support": True} if zone_contact_support else {}))
    _record_zone_search(report, zone_global_seconds, zone_global_workers, room_seconds=room_seconds,
                        diagnostics=zone_global_diagnostics, contact_pruning=zone_contact_pruning,
                        **({"contact_support": True} if zone_contact_support else {}))
    from layout_configurator.bundle import write_facility_variant
    from layout_configurator.equipment import place_equipment_clear_of_doors, validate_equipment_layout
    from layout_configurator.facility import validate_building
    from layout_configurator.flows import route_flows, validate_flow_routes
    from layout_configurator.hierarchy import solve_layouts_auto
    from layout_configurator.qa import validate_building_set
    from layout_configurator.solver import InfeasibleLayout
    from layout_configurator.validation import validate_layout
    import ifcopenshell
    import ifcopenshell.validate

    output = Path(output)
    report["room_solver"] = {}
    report["active_stage"] = "rooms80"
    checkpoint()
    fits = {}
    for item in building.equipment:
        if item.room_id is not None:
            fits.setdefault(item.room_id, []).append((item.id, item.room_fit_dimensions(building.layout.wall_thickness_mm)))
    started = time.monotonic()
    try:
        if room_solver == "zone_recursive":
            from layout_configurator.zone_hierarchy import solve_zone_layouts

            result = solve_zone_layouts(
                building, 1, room_seconds, seed, recursive_zones=True,
                cache_cluster_shapes=True, hint_rects=hints, evidence=report["room_solver"],
                minimum_adjacency_mm=building.layout.door_width_mm + 2 * building.layout.wall_thickness_mm,
                **zone_options,
            )[0]
        elif room_solver == "hierarchical":
            result = solve_layouts_auto(building.layout, 1, room_seconds, seed, strategy="hierarchical",
                                       cache_cluster_shapes=True, hint_rects=hints, evidence=report["room_solver"],
                                       equipment_fit_options=fits,
                                       minimum_adjacency_mm=building.layout.door_width_mm + 2 * building.layout.wall_thickness_mm,
                                       required_adjacency_groups=building.zone_relation_groups("required_adjacency"),
                                       forbidden_adjacency_groups=building.zone_relation_groups("forbidden_adjacency"),
                                       preferred_adjacency_groups=building.zone_relation_groups("preferred_adjacency"))[0]
        else:
            raise ValueError("Unsupported supplied warm-start room solver")
    except InfeasibleLayout as exc:
        error = {"type": type(exc).__name__, "message": str(exc)}
        if room_solver == "zone_recursive":
            from layout_configurator.zone_hierarchy import ZoneHierarchyFailure

            if isinstance(exc, ZoneHierarchyFailure):
                error["stage_evidence"] = exc.to_dict()
        report.update(status="ROOM_SEARCH_FAILED", error=error)
        return False
    finally:
        report["stages"]["room_solve_seconds"] = time.monotonic() - started
        checkpoint()
    report["cp_sat_final_result"] = True
    report["stage_hashes"]["rooms80"] = digest(result.to_dict())
    if room_solver == "zone_recursive":
        from layout_configurator.models import Rect
        from layout_configurator.zone_hierarchy import validate_zone_layout

        report["active_stage"] = "independent_physical_zones80"
        checkpoint()
        started = time.monotonic()
        raw_containers = report["room_solver"]["variants"][0]["zone_rects"]
        containers = {unit_id: Rect(**raw) for unit_id, raw in raw_containers.items()}
        zone_report = validate_zone_layout(
            building, result, containers, recursive_zones=True,
            minimum_adjacency_mm=building.layout.door_width_mm + 2 * building.layout.wall_thickness_mm,
        )
        report["gates"]["physical_zones"] = {
            "ok": zone_report.ok,
            "issues": [{"code": issue.code, "message": issue.message} for issue in zone_report.issues],
        }
        report["stage_hashes"]["zones80"] = digest(raw_containers)
        report["stages"]["physical_zone_validation_seconds"] = time.monotonic() - started
        output.mkdir(parents=True, exist_ok=True)
        (output / "zones80.json").write_text(json.dumps(raw_containers, indent=2, allow_nan=False) + "\n", encoding="utf-8")
        checkpoint()
        if not zone_report.ok:
            report["status"] = "ZONE_GEOMETRY_REJECTED"
            return False
    report["active_stage"] = "equipment_routes_and_facility80"
    checkpoint()
    started = time.monotonic()
    layout_report = validate_layout(building.layout, result)
    report["gates"]["layout"] = {"ok": layout_report.ok, "issues": [item.message for item in layout_report.issues]}
    if not layout_report.ok:
        report["status"] = "LAYOUT_REJECTED"
        return False
    packed, _ = place_equipment_clear_of_doors(building, result, time_limit_seconds=equipment_seconds, seed=seed)
    equipment_report = validate_equipment_layout(building, result, packed)
    routes = route_flows(building, result, packed)
    flow_report = validate_flow_routes(building, result, routes, packed)
    facility = validate_building(building, result, packed, routes, flow_report, profile=profile,
                                equipment_report=equipment_report)
    report["gates"].update(equipment=equipment_report.to_dict(), flows=flow_report.to_dict(), facility=facility.to_dict())
    report["stage_hashes"].update(equipment80=digest(packed.to_dict(building)), flows80=digest(routes.to_dict()),
                                  facility80=digest(facility.to_dict()))
    report["stages"]["equipment_routes_facility_seconds"] = time.monotonic() - started
    checkpoint()
    if not all(gate["ok"] for gate in report["gates"].values()):
        report["status"] = "FACILITY_REJECTED"
        return False
    report["active_stage"] = "export_bundle80"
    checkpoint()
    started = time.monotonic()
    bundle = output / "bundle"
    entry = write_facility_variant(bundle, building, profile, result, layout_report=layout_report,
                                  equipment=packed, equipment_report=equipment_report, flow_routes=routes,
                                  flow_report=flow_report, facility_report=facility,
                                  generation_evidence={"seed": seed, "room_solver_used": room_solver,
                                                       "room_solver_evidence": report["room_solver"],
                                                       "final_hint_positions_fixed": False})
    manifest = {"project": building.layout.project_name, "facility_profile": profile.to_dict(),
                "drawing_profile": profile.drawing.to_dict(), "variants": [entry]}
    (bundle / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    report["stages"]["bundle_export_seconds"] = time.monotonic() - started
    report["active_stage"] = "independent_persisted_bundle80_qa"
    checkpoint()
    started = time.monotonic()
    qa = validate_building_set(bundle, expected_variants=1, profile=profile)
    report["bundle_qa"] = qa.to_dict()
    report["stages"]["bundle_readback_seconds"] = time.monotonic() - started
    checkpoint()
    report["active_stage"] = "strict_ifc80_express"
    checkpoint()
    started = time.monotonic()
    logger = ifcopenshell.validate.json_logger()
    ifcopenshell.validate.validate(ifcopenshell.open(bundle / entry["ifc"]), logger, express_rules=True)
    report["ifc_express"] = {"ok": not logger.statements, "express_rules": True,
                             "issues": [str(item.get("message", "EXPRESS violation")) for item in logger.statements]}
    report["stages"]["ifc_express_seconds"] = time.monotonic() - started
    report["artifact_sha256"] = {path.name: file_digest(path) for path in sorted(bundle.iterdir()) if path.is_file()}
    report["stage_hashes"].update(bundle80=digest(report["artifact_sha256"]), bundle80_qa=digest(report["bundle_qa"]),
                                  ifc80_express=digest(report["ifc_express"]))
    report["gates"].update(bundle={"ok": qa.ok}, ifc_express={"ok": report["ifc_express"]["ok"]})
    report["status"] = "ALL_GATES_PASS" if all(gate["ok"] for gate in report["gates"].values()) else "BUNDLE_REJECTED"
    checkpoint()
    return report["status"] == "ALL_GATES_PASS"


def worker(args):
    from layout_configurator.models import LayoutResult

    controls = _requested_zone_controls(args)
    report = new_report(args.max_seconds, room_solver=args.room_solver, **controls)
    report.update(ci_commit=os.environ.get("GITHUB_SHA"), python=platform.python_version(),
                  platform=platform.platform(), logical_cpus=os.cpu_count(), active_stage="preparation")
    def checkpoint():
        report["total_seconds"] = time.monotonic() - args.started
        write_report(args.output, report)
        if report["total_seconds"] >= args.max_seconds:
            raise TimeoutError("The complete measured request exceeded its total budget")
    checkpoint()
    try:
        report["implementation_commit"] = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=REPO_ROOT, text=True, timeout=2).strip()
        report["working_source_changes"] = subprocess.check_output(
            ["git", "diff", "--name-only"], cwd=REPO_ROOT, text=True, timeout=2,
            stderr=subprocess.DEVNULL).splitlines()
        fingerprint = runtime_state()
        if os.environ.get("GITHUB_ACTIONS") == "true" and not fingerprint["installed_package"]:
            raise ValueError("Manual CI acceptance requires the executing module from a noneditable installed package")
        sources = fixture_state(args.fixture)
        report["runtime"] = fingerprint
        checkpoint()
        shutil.copytree(args.fixture, args.output / "supplied-source", dirs_exist_ok=True)
        building, hints, profile = prepare_fixture(args.fixture, report)
        (args.output / "program80_prepared.json").write_text(json.dumps(building.to_dict(), indent=2) + "\n", encoding="utf-8")
        (args.output / "hints80.json").write_text(json.dumps(LayoutResult(1, hints).to_dict(), indent=2) + "\n", encoding="utf-8")
        report["stages"]["preparation_including_imports_seconds"] = time.monotonic() - args.started
        checkpoint()
        ok = accept_program(building, hints, profile, report, args.output, checkpoint=checkpoint,
                            room_solver=args.room_solver, **controls)
        report["active_stage"] = "source_and_implementation_recheck"
        checkpoint()
        if fixture_state(args.fixture) != sources or runtime_state() != fingerprint:
            raise ValueError("Fixture or executable source/dependencies changed during acceptance")
        report["implementation_commit_after"] = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=REPO_ROOT, text=True, timeout=2).strip()
        report.update(accepted=ok, status="PASS" if ok else report["status"], active_stage="finished")
    except TimeoutError as exc:
        report.update(accepted=False, status="BUDGET_EXCEEDED", error={"type": type(exc).__name__, "message": str(exc)})
    except Exception as exc:
        report.update(accepted=False, status="INPUT_REJECTED" if report["active_stage"] == "preparation" else "CHECK_FAILED",
                      error={"type": type(exc).__name__, "message": str(exc)})
    report["total_seconds"] = time.monotonic() - args.started
    if report["total_seconds"] >= args.max_seconds:
        report.update(accepted=False, status="BUDGET_EXCEEDED")
    write_report(args.output, report)
    return 0 if report["accepted"] else (124 if report["status"] == "BUDGET_EXCEEDED" else 4)


def main(argv=None):
    started = time.monotonic()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture", type=Path, default=DEFAULT_FIXTURE)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--max-seconds", type=float, default=120)
    parser.add_argument("--room-solver", choices=("hierarchical", "zone_recursive"), default="hierarchical",
                        help="Explicit supplied warm-start backend; recursive mode checks physical canonical containers")
    parser.add_argument("--zone-global-seconds", type=float, default=None,
                        help="Recursive final global CP seconds per model in (0,120]; local/subtree budgets remain unchanged")
    parser.add_argument("--zone-global-workers", type=int, default=1,
                        help="Recursive final global CP workers in 1..8; the whole request remains bounded by max-seconds")
    parser.add_argument("--zone-global-diagnostics", action="store_true",
                        help="Capture bounded final recursive candidate families and actual CP model/search counters")
    parser.add_argument("--zone-contact-pruning", action="store_true",
                        help="Post bounded redundant no-goods for incompatible final recursive root contacts")
    parser.add_argument("--zone-contact-support", action="store_true",
                        help="Couple final recursive shape choices to exact shared translation intervals")
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--started", type=float, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    report = new_report(args.max_seconds if math.isfinite(args.max_seconds) else str(args.max_seconds),
                        room_solver=args.room_solver, **_requested_zone_controls(args))
    if not math.isfinite(args.max_seconds) or not 0 < args.max_seconds <= 120:
        report.update(status="INPUT_REJECTED", message="max-seconds must be finite and in (0,120]")
        write_report(args.output, report)
        return 2
    try:
        _zone_search_options(args.room_solver, args.zone_global_seconds, args.zone_global_workers,
                             diagnostics=args.zone_global_diagnostics, contact_pruning=args.zone_contact_pruning,
                             **({"contact_support": True} if args.zone_contact_support else {}))
    except ValueError as exc:
        report.update(status="INPUT_REJECTED", message=str(exc))
        write_report(args.output, report)
        return 2
    if args.worker:
        return worker(args)
    report.update(active_stage="worker_startup", total_seconds=time.monotonic() - started)
    write_report(args.output, report)
    command = [sys.executable, str(Path(__file__).resolve()), "--worker", "--started", str(started),
               "--fixture", str(args.fixture.resolve()), "--output", str(args.output.resolve()),
               "--max-seconds", str(args.max_seconds)]
    if args.room_solver != "hierarchical":
        command.extend(["--room-solver", args.room_solver])
    if args.zone_global_seconds is not None:
        command.extend(["--zone-global-seconds", str(args.zone_global_seconds)])
    if args.zone_global_workers != 1:
        command.extend(["--zone-global-workers", str(args.zone_global_workers)])
    if args.zone_global_diagnostics:
        command.append("--zone-global-diagnostics")
    if args.zone_contact_pruning:
        command.append("--zone-contact-pruning")
    if args.zone_contact_support:
        command.append("--zone-contact-support")
    process = subprocess.Popen(command, cwd=REPO_ROOT, shell=False,
                               creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    try:
        code = process.wait(timeout=max(.001, args.max_seconds - (time.monotonic() - started)))
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()
        code = 124
    path = args.output / "acceptance-report.json"
    report = json.loads(path.read_text(encoding="utf-8"))
    elapsed = time.monotonic() - started
    report["total_seconds"] = elapsed
    if code == 124 or elapsed >= args.max_seconds:
        report.update(accepted=False, status="BUDGET_EXCEEDED", worker_exit_code=code)
        code = 124
    elif code and report["status"] == "RUNNING":
        report.update(accepted=False, status="WORKER_FAILED", worker_exit_code=code)
    write_report(args.output, report)
    print(f"{report['status']}: {elapsed:.3f} s; {path}")
    return code


if __name__ == "__main__":
    raise SystemExit(main())

"""Command-line entry point."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from .brief import parse_text_brief
from .bcf import write_bcf_package
from .building import BuildingSpecError
from .compliance import validate_ids
from .commands import AddDoor, AddWindow, EditError, MoveRoom, RemoveDoor, RemoveExternalEntry, RemoveWindow, ResizeRoom, SetExternalEntry
from .editor import EditorState
from .equipment import EquipmentPlacementError, place_equipment, validate_equipment_layout
from .export import export_building_bundle, export_bundle
from .facility import default_facility_profile, load_facility_profile, validate_building
from .flows import route_flows, validate_flow_routes
from .ifc import export_building_ifc, export_ifc, export_multifloor_ifc, validate_ifc_roundtrip
from .llm import llm_settings_from_environment, parse_with_openai_compatible
from .io import (
    load_building,
    load_building_result,
    load_result,
    load_spec,
    write_building_result,
    write_coordination_issues,
    write_result,
)
from .norms import check_layout, load_ruleset, retrieve_rule_citations
from .multifloor import MultiFloorSpec, solve_multifloor
from .qa import validate_building_bundle, validate_building_set
from .schema import load_canonical_spec, normalize_mapping, validate_mapping, validate_spec, load_mapping
from .solver import InfeasibleLayout, solve_layouts
from .validation import validate_layout


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="layout-configurator", description="Solver-first facility layout compiler")
    subparsers = parser.add_subparsers(dest="command", required=True)
    generate = subparsers.add_parser("generate", help="solve a JSON/YAML specification and export DXF/PDF")
    generate.add_argument("spec", type=Path)
    generate.add_argument("--output", "-o", type=Path, default=Path("out"))
    generate.add_argument("--variants", type=int, default=1)
    generate.add_argument("--time-limit", type=float, default=30)
    generate.add_argument("--seed", type=int, default=42)
    generate.add_argument("--strict-input", action="store_true", help="require canonical LayoutIR input before solving")
    generate.add_argument("--schema", type=Path, default=Path("schemas/layout_ir.schema.json"))
    generate_building = subparsers.add_parser(
        "generate-building", help="solve a dense-building program and place equipment"
    )
    generate_building.add_argument("spec", type=Path)
    generate_building.add_argument("--output", "-o", type=Path, default=Path("out/building"))
    generate_building.add_argument("--variants", type=int, default=1)
    generate_building.add_argument(
        "--time-limit",
        type=float,
        default=60,
        help="CP-SAT time limit in seconds; dense facility defaults to 60",
    )
    generate_building.add_argument("--seed", type=int, default=42)
    generate_building.add_argument(
        "--profile",
        type=Path,
        default=None,
        help="facility profile YAML; defaults to the YAML-backed compatibility profile",
    )
    generate_building.add_argument(
        "--bcf-input",
        type=Path,
        default=None,
        help="previous BCF package; disappeared issues are carried forward as resolved history",
    )
    check_building = subparsers.add_parser(
        "check-building",
        help="recompute and validate a generated BuildingIR result",
    )
    check_building.add_argument("input", type=Path)
    check_building.add_argument("--profile", type=Path, default=None)
    check_building.add_argument("--json", action="store_true", dest="json_output")
    check_building.add_argument("--issues-output", type=Path, default=None)
    check_building.add_argument("--bcf-output", type=Path, default=None)
    check_building.add_argument("--bcf-input", type=Path, default=None)
    check_building.add_argument("--ifc", type=Path, default=None, help="optional IFC projection to read back")
    qa_building = subparsers.add_parser(
        "qa-building",
        help="read back and cross-check a complete BuildingIR coordination bundle",
    )
    qa_building.add_argument("input", type=Path)
    qa_building.add_argument("--profile", type=Path, default=None)
    qa_building.add_argument("--dxf", type=Path, default=None)
    qa_building.add_argument("--pdf", type=Path, default=None)
    qa_building.add_argument("--ifc", type=Path, default=None)
    qa_building.add_argument("--bcf", type=Path, default=None)
    qa_building.add_argument("--coordination", type=Path, default=None)
    qa_building.add_argument("--manifest", type=Path, default=None)
    qa_building.add_argument("--json", action="store_true", dest="json_output")
    qa_building_set = subparsers.add_parser(
        "qa-building-set",
        help="run full bundle QA and cross-variant identity checks",
    )
    qa_building_set.add_argument("output", type=Path)
    qa_building_set.add_argument("--variants", type=int, default=None)
    qa_building_set.add_argument("--profile", type=Path, default=None)
    qa_building_set.add_argument("--json", action="store_true", dest="json_output")
    qa_building_set.add_argument(
        "--report",
        type=Path,
        default=None,
        help="write the machine-readable QA evidence report to this JSON file",
    )
    edit = subparsers.add_parser("edit", help="apply typed edits to an existing layout JSON and re-export it")
    edit.add_argument("input", type=Path)
    edit.add_argument("--output", "-o", type=Path, default=Path("edited"))
    edit.add_argument("--move-room", nargs=3, action="append", metavar=("ROOM", "DX_MM", "DY_MM"))
    edit.add_argument("--resize-room", nargs=3, action="append", metavar=("ROOM", "WIDTH_MM", "HEIGHT_MM"))
    edit.add_argument("--add-door", nargs=2, action="append", metavar=("ROOM_A", "ROOM_B"))
    edit.add_argument("--add-door-at", nargs=4, action="append", metavar=("ROOM_A", "ROOM_B", "OFFSET_MM", "WIDTH_MM"))
    edit.add_argument("--remove-door", action="append", metavar="DOOR_ID")
    edit.add_argument("--add-window", nargs=4, action="append", metavar=("ROOM", "SIDE", "OFFSET_MM", "WIDTH_MM"))
    edit.add_argument("--remove-window", action="append", metavar="WINDOW_ID")
    entry_group = edit.add_mutually_exclusive_group()
    entry_group.add_argument("--set-external-entry", nargs=4, action="append", metavar=("ROOM", "SIDE", "OFFSET_MM", "WIDTH_MM"))
    entry_group.add_argument("--remove-external-entry", action="store_true")
    edit.add_argument("--rules", type=Path, help="run deterministic rules after editing")
    edit.add_argument("--require-provenance", action="store_true", help="require auditable source metadata for --rules")
    validate = subparsers.add_parser("validate", help="validate an IFC file against an IDS requirements file")
    validate.add_argument("ifc", type=Path)
    validate.add_argument("--ids", type=Path, default=Path("ids/layout_baseline.ids"))
    check = subparsers.add_parser("check", help="run deterministic design rules against a layout JSON")
    check.add_argument("input", type=Path)
    check.add_argument("--rules", type=Path, default=Path("rules/baseline.yaml"))
    check.add_argument("--require-provenance", action="store_true", help="require auditable source metadata in the ruleset")
    check.add_argument("--json", action="store_true", dest="json_output", help="print a machine-readable report")
    schema = subparsers.add_parser("schema", help="validate a LayoutIR document against JSON Schema")
    schema.add_argument("input", type=Path)
    schema.add_argument("--schema", type=Path, default=Path("schemas/layout_ir.schema.json"))
    schema.add_argument("--raw", action="store_true", help="validate the file as-is, without LayoutIR normalization")
    schema.add_argument("--json", action="store_true", dest="json_output", help="print a machine-readable report")
    normalize = subparsers.add_parser("normalize", help="strictly normalize canonical JSON/YAML into LayoutIR JSON")
    normalize.add_argument("input", type=Path)
    normalize.add_argument("--output", "-o", type=Path, help="write canonical JSON to this file; otherwise print it")
    normalize.add_argument("--schema", type=Path, default=Path("schemas/layout_ir.schema.json"))
    parse_brief = subparsers.add_parser("parse-brief", help="parse a constrained text brief into canonical LayoutIR JSON")
    parse_brief.add_argument("input", type=Path)
    parse_brief.add_argument("--output", "-o", type=Path)
    parse_brief.add_argument("--schema", type=Path, default=Path("schemas/layout_ir.schema.json"))
    cite = subparsers.add_parser("cite", help="retrieve rule references without evaluating a layout")
    cite.add_argument("query", nargs="+")
    cite.add_argument("--rules", type=Path, default=Path("rules/baseline.yaml"))
    cite.add_argument("--json", action="store_true", dest="json_output")
    parse_llm = subparsers.add_parser("parse-llm", help="parse a text brief through an opt-in JSON-only LLM provider")
    parse_llm.add_argument("input", type=Path)
    parse_llm.add_argument("--output", "-o", type=Path)
    parse_llm.add_argument("--schema", type=Path, default=Path("schemas/layout_ir.schema.json"))
    parse_llm.add_argument("--endpoint", default=None, help="OpenAI-compatible chat completions endpoint; or LAYOUT_LLM_ENDPOINT")
    parse_llm.add_argument("--model", default=None, help="provider model; or LAYOUT_LLM_MODEL")
    parse_llm.add_argument("--api-key-env", default="LAYOUT_LLM_API_KEY", help="environment variable containing the provider key")
    multifloor = subparsers.add_parser("generate-multifloor", help="solve coordinated multi-floor layouts")
    multifloor.add_argument("spec", type=Path)
    multifloor.add_argument("--output", "-o", type=Path, default=Path("out/multifloor"))
    multifloor.add_argument("--time-limit", type=float, default=30)
    multifloor.add_argument("--seed", type=int, default=42)
    ui = subparsers.add_parser("ui", help="serve a local browser editor or facility review")
    ui.add_argument("input", type=Path, help="layout/building result JSON, YAML/JSON specification, or generated variant directory")
    ui.add_argument("--output", "-o", type=Path, default=Path("out/ui"))
    ui.add_argument("--host", default="127.0.0.1")
    ui.add_argument("--port", type=int, default=8765)
    ui.add_argument("--rules", type=Path, help="optional deterministic ruleset shown after each edit")
    ui.add_argument("--profile", type=Path, help="optional facility profile for BuildingIR read-only review")
    ui.add_argument("--variant", type=int, default=1, help="variant number when input is a generated bundle directory")
    ui.add_argument("--require-provenance", action="store_true", help="require complete ruleset provenance")
    args = parser.parse_args(argv)

    if args.command == "generate":
        try:
            spec = load_canonical_spec(args.spec, args.schema) if args.strict_input else load_spec(args.spec)
            results = solve_layouts(spec, args.variants, args.time_limit, args.seed)
        except (OSError, ValueError, InfeasibleLayout, RuntimeError) as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 2

        args.output.mkdir(parents=True, exist_ok=True)
        manifest = {"project": spec.project_name, "variants": []}
        for result in results:
            report = validate_layout(spec, result)
            if not report.ok:
                print(f"ERROR: вариант {result.variant} не прошёл валидацию", file=sys.stderr)
                for issue in report.issues:
                    print(f"  - {issue.code}: {issue.message}", file=sys.stderr)
                return 3
            dxf_path, pdf_path = export_bundle(args.output, spec, result, report)
            json_path = args.output / f"layout_{result.variant:02d}.json"
            ifc_path = args.output / f"layout_{result.variant:02d}.ifc"
            ifc_summary = export_ifc(ifc_path, spec, result)
            write_result(json_path, spec, result)
            manifest["variants"].append(
                {
                    "variant": result.variant,
                    "dxf": dxf_path.name,
                    "pdf": pdf_path.name,
                    "ifc": ifc_path.name,
                    "ifc_entities": {
                        "spaces": ifc_summary.spaces,
                        "walls": ifc_summary.walls,
                        "doors": ifc_summary.doors,
                        "windows": ifc_summary.windows,
                        "openings": ifc_summary.openings,
                        "space_boundaries": ifc_summary.space_boundaries,
                        "voids": ifc_summary.voids,
                        "fills": ifc_summary.fills,
                        "wall_types": ifc_summary.wall_types,
                        "door_types": ifc_summary.door_types,
                        "window_types": ifc_summary.window_types,
                        "external_entries": ifc_summary.external_entries,
                    },
                    "json": json_path.name,
                }
            )
            print(f"variant {result.variant}: {dxf_path} | {pdf_path} | {ifc_path}")
        (args.output / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        return 0
    if args.command == "generate-building":
        try:
            building = load_building(args.spec).with_required_flow_opening_width()
            profile = load_facility_profile(args.profile) if args.profile else default_facility_profile()
            equipment_fit_options = {}
            for item in building.equipment:
                if item.room_id is not None:
                    equipment_fit_options.setdefault(item.room_id, []).append(
                        (item.id, item.room_fit_dimensions(building.layout.wall_thickness_mm))
                    )
            results = solve_layouts(
                building.layout,
                args.variants,
                args.time_limit,
                args.seed,
                required_adjacency_groups=building.zone_relation_groups("required_adjacency"),
                forbidden_adjacency_groups=building.zone_relation_groups("forbidden_adjacency"),
                minimum_adjacency_mm=(
                    building.layout.door_width_mm + 2 * building.layout.wall_thickness_mm
                    if building.flows
                    else None
                ),
                equipment_fit_options=equipment_fit_options,
            )
        except (OSError, ValueError, BuildingSpecError, InfeasibleLayout, RuntimeError) as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 2

        args.output.mkdir(parents=True, exist_ok=True)
        manifest = {
            "project": building.layout.project_name,
            "facility_profile": profile.to_dict(),
            "drawing_profile": profile.drawing.to_dict(),
            "variants": [],
        }
        facility_reports = []
        for result in results:
            report = validate_layout(building.layout, result)
            if not report.ok:
                print(f"ERROR: вариант {result.variant} не прошёл валидацию", file=sys.stderr)
                for issue in report.issues:
                    print(f"  - {issue.code}: {issue.message}", file=sys.stderr)
                return 3
            try:
                equipment = place_equipment(
                    building,
                    result,
                    time_limit_seconds=args.time_limit,
                    seed=args.seed,
                )
                equipment_report = validate_equipment_layout(building, result, equipment)
                if not equipment_report.ok:
                    raise EquipmentPlacementError(
                        "; ".join(issue.message for issue in equipment_report.issues)
                    )
                flow_routes = route_flows(building, result, equipment)
                flow_report = validate_flow_routes(building, result, flow_routes, equipment)
                facility_report = validate_building(
                    building,
                    result,
                    equipment,
                    flow_routes,
                    flow_report,
                    profile=profile,
                    equipment_report=equipment_report,
                )
                ifc_path = args.output / f"building_{result.variant:02d}.ifc"
                ifc_summary = export_building_ifc(
                    ifc_path,
                    building,
                    result,
                    equipment,
                    flow_routes,
                    flow_report,
                )
                ifc_readback = validate_ifc_roundtrip(
                    ifc_path,
                    ifc_summary,
                    expected_flow_ids=(route.flow_id for route in flow_routes.routes),
                )
                if not ifc_readback.ok:
                    raise RuntimeError("; ".join(ifc_readback.issues))
            except (EquipmentPlacementError, ValueError, RuntimeError) as exc:
                print(f"ERROR: оборудование в варианте {result.variant} не размещено: {exc}", file=sys.stderr)
                return 3
            dxf_path, pdf_path = export_building_bundle(
                args.output,
                building,
                result,
                equipment,
                report,
                flow_routes,
                flow_report,
                profile.drawing,
            )
            json_path = args.output / f"building_{result.variant:02d}.json"
            write_building_result(
                json_path,
                building,
                result,
                equipment,
                flow_routes,
                flow_report,
                equipment_report=equipment_report,
                facility_report=facility_report,
            )
            issues_path = args.output / f"building_{result.variant:02d}.coordination.json"
            write_coordination_issues(
                issues_path,
                facility_report,
                project_name=building.layout.project_name,
                variant=result.variant,
                model_references={
                    "program": json_path.name,
                    "dxf": dxf_path.name,
                    "pdf": pdf_path.name,
                    "ifc": ifc_path.name,
                },
            )
            bcf_path = args.output / f"building_{result.variant:02d}.bcf"
            try:
                bcf_summary = write_bcf_package(
                    bcf_path,
                    facility_report,
                    project_name=building.layout.project_name,
                    variant=result.variant,
                    model_references={
                        "program": json_path.name,
                        "dxf": dxf_path.name,
                        "pdf": pdf_path.name,
                        "ifc": ifc_path.name,
                    },
                    ifc_path=ifc_path,
                    previous=args.bcf_input,
                )
            except (OSError, ValueError, RuntimeError) as exc:
                print(f"ERROR: BCF-пакет варианта {result.variant} не сформирован: {exc}", file=sys.stderr)
                return 3
            facility_reports.append(facility_report)
            manifest["variants"].append(
                {
                    "variant": result.variant,
                    "rooms": len(result.placements),
                    "equipment": len(equipment.placements),
                    "equipment_issues": len(equipment_report.issues),
                    "flows": len(flow_routes.routes),
                    "flow_issues": len(flow_report.issues),
                    "facility_checks": len(facility_report.checks),
                    "facility_issues": sum(check.status == "FAIL" for check in facility_report.checks),
                    "coordination_issues": issues_path.name,
                    "coordination_issue_count": len(facility_report.issues),
                    "bcf": bcf_path.name,
                    "bcf_version": bcf_summary.version,
                    "bcf_topic_count": len(bcf_summary.topics),
                    "bcf_open_topics": bcf_summary.open_topics,
                    "bcf_resolved_topics": bcf_summary.resolved_topics,
                    "facility_ok": facility_report.ok,
                    "dxf": dxf_path.name,
                    "pdf": pdf_path.name,
                    "ifc": ifc_path.name,
                    "ifc_entities": {
                        "spaces": ifc_summary.spaces,
                        "walls": ifc_summary.walls,
                        "doors": ifc_summary.doors,
                        "windows": ifc_summary.windows,
                        "equipment": ifc_summary.equipment,
                        "equipment_types": ifc_summary.equipment_types,
                        "flow_routes": ifc_summary.flow_routes,
                        "openings": ifc_summary.openings,
                    },
                    "ifc_readback": ifc_readback.to_dict(),
                    "json": json_path.name,
                }
            )
            print(
                f"variant {result.variant}: {dxf_path} | {pdf_path} | {ifc_path} | "
                f"{json_path} | {issues_path} | {bcf_path}"
            )
            if flow_report.issues:
                print(
                    f"  flow validation: {len(flow_report.issues)} issue(s); see {json_path.name} -> flow_validation",
                    file=sys.stderr,
                )
        (args.output / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return 0 if all(report.ok for report in facility_reports) else 4
    if args.command == "qa-building":
        try:
            profile = load_facility_profile(args.profile) if args.profile else None
            qa_report = validate_building_bundle(
                args.input,
                profile=profile,
                dxf_path=args.dxf,
                pdf_path=args.pdf,
                ifc_path=args.ifc,
                bcf_path=args.bcf,
                coordination_path=args.coordination,
                manifest_path=args.manifest,
            )
        except (OSError, KeyError, TypeError, ValueError, RuntimeError) as exc:
            print(f"ERROR: bundle QA failed: {exc}", file=sys.stderr)
            return 2
        if args.json_output:
            print(json.dumps(qa_report.to_dict(), ensure_ascii=False, indent=2))
        else:
            print(f"Bundle QA: {'PASS' if qa_report.ok else 'FAIL'}")
            for check in qa_report.checks:
                print(f"{'PASS' if check.ok else 'FAIL'}: {check.id}: {check.message}")
        return 0 if qa_report.ok else 4
    if args.command == "qa-building-set":
        try:
            profile = load_facility_profile(args.profile) if args.profile else None
            qa_report = validate_building_set(
                args.output,
                expected_variants=args.variants,
                profile=profile,
            )
            if args.report:
                args.report.parent.mkdir(parents=True, exist_ok=True)
                args.report.write_text(
                    json.dumps(qa_report.to_dict(), ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8",
                )
        except (OSError, KeyError, TypeError, ValueError, RuntimeError) as exc:
            print(f"ERROR: building set QA failed: {exc}", file=sys.stderr)
            return 2
        if args.json_output:
            print(json.dumps(qa_report.to_dict(), ensure_ascii=False, indent=2))
        else:
            print(f"Building set QA: {'PASS' if qa_report.ok else 'FAIL'} ({len(qa_report.variant_reports)} variants)")
            for report in qa_report.variant_reports:
                print(f"{'PASS' if report.ok else 'FAIL'}: {report.input_path.name}")
            check = qa_report.consistency
            print(f"{'PASS' if check.ok else 'FAIL'}: {check.id}: {check.message}")
        return 0 if qa_report.ok else 4
    if args.command == "check-building":
        try:
            building, result, equipment = load_building_result(args.input)
            profile = load_facility_profile(args.profile) if args.profile else default_facility_profile()
            equipment_report = validate_equipment_layout(building, result, equipment)
            flow_routes = route_flows(building, result, equipment)
            flow_report = validate_flow_routes(building, result, flow_routes, equipment)
            ifc_path = args.ifc
            ifc_readback = None
            if ifc_path is None:
                candidate = args.input.with_suffix(".ifc")
                if candidate.is_file():
                    ifc_path = candidate
            if ifc_path is not None:
                ifc_readback = validate_ifc_roundtrip(
                    ifc_path,
                    expected_flow_ids=(route.flow_id for route in flow_routes.routes),
                )
            facility_report = validate_building(
                building,
                result,
                equipment,
                flow_routes,
                flow_report,
                profile=profile,
                equipment_report=equipment_report,
            )
            if args.issues_output is not None:
                write_coordination_issues(
                    args.issues_output,
                    facility_report,
                    project_name=building.layout.project_name,
                    variant=result.variant,
                )
            if args.bcf_output is not None:
                write_bcf_package(
                    args.bcf_output,
                    facility_report,
                    project_name=building.layout.project_name,
                    variant=result.variant,
                    model_references={"program": args.input.name},
                    previous=args.bcf_input,
                )
        except (OSError, KeyError, TypeError, ValueError, RuntimeError) as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 2
        if args.json_output:
            payload = facility_report.to_dict()
            if ifc_readback is not None:
                payload["ifc_readback"] = ifc_readback.to_dict()
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        else:
            print(
                f"Facility profile: {profile.name} {profile.version} "
                f"[{profile.domain}]"
            )
            for check_result in facility_report.checks:
                print(f"{check_result.status}: {check_result.id}: {check_result.title}")
                for evidence in check_result.evidence:
                    print(f"  - {evidence}")
        return 0 if facility_report.ok and (ifc_readback is None or ifc_readback.ok) else 4
    if args.command == "edit":
        if not any((args.move_room, args.resize_room, args.add_door, args.add_door_at, args.remove_door, args.add_window, args.remove_window, args.set_external_entry, args.remove_external_entry)):
            print("ERROR: укажите хотя бы одну typed-команду правки", file=sys.stderr)
            return 2
        try:
            spec, result = load_result(args.input)
            state = EditorState.from_layout(spec, result)
            for room_id, dx_mm, dy_mm in args.move_room or ():
                state = state.apply(MoveRoom(room_id, float(dx_mm), float(dy_mm)))
            for room_id, width_mm, height_mm in args.resize_room or ():
                state = state.apply(ResizeRoom(room_id, float(width_mm), float(height_mm)))
            for room_a, room_b in args.add_door or ():
                state = state.apply(AddDoor(room_a, room_b))
            for room_a, room_b, offset_mm, width_mm in args.add_door_at or ():
                state = state.apply(AddDoor(room_a, room_b, float(offset_mm), float(width_mm)))
            for door_id in args.remove_door or ():
                state = state.apply(RemoveDoor(door_id))
            for room_id, side, offset_mm, width_mm in args.add_window or ():
                state = state.apply(AddWindow(room_id, side, float(offset_mm), float(width_mm)))
            for window_id in args.remove_window or ():
                state = state.apply(RemoveWindow(window_id))
            for room_id, side, offset_mm, width_mm in args.set_external_entry or ():
                state = state.apply(SetExternalEntry(room_id, side, float(offset_mm), float(width_mm)))
            if args.remove_external_entry:
                state = state.apply(RemoveExternalEntry())
            if args.require_provenance and args.rules is None:
                raise EditError("--require-provenance requires --rules")
            ruleset = load_ruleset(args.rules) if args.rules else None
            if ruleset and args.require_provenance:
                provenance_issues = ruleset.provenance_issues()
                if provenance_issues:
                    details = "; ".join(provenance_issues)
                    raise EditError(f"ruleset provenance is incomplete: {details}")
            dxf_path, pdf_path = export_bundle(args.output, state.spec, state.result, state.report)
            ifc_path = args.output / f"layout_{state.result.variant:02d}.ifc"
            export_ifc(ifc_path, state.spec, state.result)
            json_path = args.output / f"layout_{state.result.variant:02d}.json"
            write_result(json_path, state.spec, state.result)
            print(f"edited ({', '.join(state.history)}): {dxf_path} | {pdf_path} | {ifc_path} | {json_path}")
            if ruleset:
                norms_report = check_layout(state.spec, state.result, ruleset)
                print(f"rules: {norms_report.ruleset_name} {norms_report.ruleset_version} [{norms_report.jurisdiction}]")
                for rule in norms_report.results:
                    print(f"  {rule.status}: {rule.id} ({rule.clause})")
                return 0 if norms_report.ok else 4
            return 0
        except (OSError, ValueError, EditError, RuntimeError) as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 2
    if args.command == "validate":
        try:
            report = validate_ids(args.ifc, args.ids)
        except (OSError, ValueError, RuntimeError) as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 2
        for specification in report.specifications:
            status = "PASS" if specification.ok else "FAIL"
            print(f"{status}: {specification.name} ({specification.passed}/{specification.applicable})")
        return 0 if report.ok else 4
    if args.command == "check":
        try:
            spec, result = load_result(args.input)
            ruleset = load_ruleset(args.rules)
            if args.require_provenance:
                provenance_issues = ruleset.provenance_issues()
                if provenance_issues:
                    print("ERROR: ruleset provenance is incomplete", file=sys.stderr)
                    for issue in provenance_issues:
                        print(f"  - {issue}", file=sys.stderr)
                    return 2
            report = check_layout(spec, result, ruleset)
        except (OSError, ValueError, KeyError, RuntimeError) as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 2
        if args.json_output:
            print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))
        else:
            print(f"Ruleset: {report.ruleset_name} {report.ruleset_version} [{report.jurisdiction}]")
            for rule in report.results:
                print(f"{rule.status}: {rule.id}: {rule.title} [{rule.source}]")
                print(f"  clause: {rule.clause}")
                for evidence in rule.evidence:
                    print(f"  - {evidence}")
        return 0 if report.ok else 4
    if args.command == "schema":
        try:
            if args.raw:
                issues = validate_mapping(load_mapping(args.input), args.schema)
            else:
                issues = validate_spec(load_spec(args.input), args.schema)
        except (OSError, ValueError, KeyError, RuntimeError) as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 2
        payload = {
            "ok": not issues,
            "schema": str(args.schema),
            "input": str(args.input),
            "raw": args.raw,
            "issues": [issue.to_dict() for issue in issues],
        }
        if args.json_output:
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        elif issues:
            print(f"FAIL: {args.input} does not match {args.schema}")
            for issue in issues:
                print(f"  - {issue.path}: {issue.message}")
        else:
            mode = "raw" if args.raw else "normalized"
            print(f"PASS: {args.input} matches LayoutIR schema ({mode})")
        return 0 if not issues else 4
    if args.command == "normalize":
        try:
            spec = normalize_mapping(load_mapping(args.input), args.schema)
            payload = json.dumps(spec.to_dict(), ensure_ascii=False, indent=2)
            if args.output:
                args.output.parent.mkdir(parents=True, exist_ok=True)
                args.output.write_text(payload + "\n", encoding="utf-8")
                print(f"normalized: {args.input} -> {args.output}")
            else:
                print(payload)
            return 0
        except (OSError, ValueError, KeyError, RuntimeError) as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 2
    if args.command == "parse-brief":
        try:
            spec = parse_text_brief(args.input.read_text(encoding="utf-8"), str(args.schema))
            payload = json.dumps(spec.to_dict(), ensure_ascii=False, indent=2)
            if args.output:
                args.output.parent.mkdir(parents=True, exist_ok=True)
                args.output.write_text(payload + "\n", encoding="utf-8")
                print(f"parsed brief: {args.input} -> {args.output}")
            else:
                print(payload)
            return 0
        except (OSError, ValueError, KeyError, RuntimeError) as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 2
    if args.command == "cite":
        try:
            ruleset = load_ruleset(args.rules)
            citations = retrieve_rule_citations(ruleset, " ".join(args.query))
        except (OSError, ValueError, KeyError, RuntimeError) as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 2
        if args.json_output:
            print(json.dumps({"query": " ".join(args.query), "citations": [item.to_dict() for item in citations]}, ensure_ascii=False, indent=2))
        elif citations:
            for citation in citations:
                print(f"{citation.id}: {citation.title}")
                print(f"  clause: {citation.clause}")
                print(f"  source: {citation.source}")
                if citation.source_url:
                    print(f"  source_url: {citation.source_url}")
        else:
            print("No matching rule references")
        return 0
    if args.command == "parse-llm":
        try:
            settings = llm_settings_from_environment()
            endpoint = args.endpoint or settings["endpoint"]
            model = args.model or settings["model"]
            api_key = os.environ.get(args.api_key_env, "")
            spec = parse_with_openai_compatible(
                args.input.read_text(encoding="utf-8"),
                endpoint=endpoint,
                model=model,
                api_key=api_key,
                schema_path=args.schema,
            )
            payload = json.dumps(spec.to_dict(), ensure_ascii=False, indent=2)
            if args.output:
                args.output.parent.mkdir(parents=True, exist_ok=True)
                args.output.write_text(payload + "\n", encoding="utf-8")
                print(f"parsed by LLM: {args.input} -> {args.output}")
            else:
                print(payload)
            return 0
        except (OSError, ValueError, KeyError, RuntimeError) as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 2
    if args.command == "generate-multifloor":
        try:
            import yaml

            raw = yaml.safe_load(args.spec.read_text(encoding="utf-8"))
            spec = MultiFloorSpec.from_mapping(raw)
            result = solve_multifloor(spec, args.time_limit, args.seed)
            args.output.mkdir(parents=True, exist_ok=True)
            floors = []
            for floor, layout in zip(spec.floors, result.floors):
                floor_output = args.output / f"floor_{floor.level:02d}"
                report = validate_layout(floor.layout, layout)
                dxf_path, pdf_path = export_bundle(floor_output, floor.layout, layout, report)
                ifc_path = floor_output / f"layout_{layout.variant:02d}.ifc"
                export_ifc(ifc_path, floor.layout, layout)
                json_path = floor_output / f"layout_{layout.variant:02d}.json"
                write_result(json_path, floor.layout, layout)
                floors.append({"level": floor.level, "elevation_mm": floor.elevation_mm, "output": str(floor_output), "json": str(json_path), "dxf": str(dxf_path), "pdf": str(pdf_path), "ifc": str(ifc_path)})
            manifest = result.to_dict()
            combined_ifc = args.output / "multifloor.ifc"
            combined_summary = export_multifloor_ifc(combined_ifc, spec, result)
            manifest["ifc"] = str(combined_ifc)
            manifest["ifc_entities"] = {
                "storeys": combined_summary.storeys,
                "spaces": combined_summary.spaces,
                "walls": combined_summary.walls,
                "doors": combined_summary.doors,
                "windows": combined_summary.windows,
                "openings": combined_summary.openings,
                "stairs": combined_summary.stairs,
            }
            manifest["floors"] = floors
            (args.output / "multifloor.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"multi-floor: {len(floors)} floors -> {args.output}")
            return 0
        except (OSError, ValueError, InfeasibleLayout, RuntimeError) as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 2
    if args.command == "ui":
        try:
            from .ui import serve_ui

            return serve_ui(
                args.input,
                args.output,
                host=args.host,
                port=args.port,
                rules_path=args.rules,
                profile_path=args.profile,
                variant=args.variant,
                require_provenance=args.require_provenance,
            )
        except (OSError, ValueError, KeyError, RuntimeError, InfeasibleLayout) as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 2
    return 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())

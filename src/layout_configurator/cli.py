"""Command-line entry point."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .compliance import validate_ids
from .commands import AddDoor, AddWindow, EditError, MoveRoom, RemoveDoor, RemoveWindow, ResizeRoom
from .editor import EditorState
from .export import export_bundle
from .ifc import export_ifc
from .io import load_result, load_spec, write_result
from .norms import check_layout, load_ruleset
from .schema import load_canonical_spec, normalize_mapping, validate_mapping, validate_spec, load_mapping
from .solver import InfeasibleLayout, solve_layouts
from .validation import validate_layout


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="layout-configurator", description="Deterministic residential layout generator")
    subparsers = parser.add_subparsers(dest="command", required=True)
    generate = subparsers.add_parser("generate", help="solve a JSON/YAML specification and export DXF/PDF")
    generate.add_argument("spec", type=Path)
    generate.add_argument("--output", "-o", type=Path, default=Path("out"))
    generate.add_argument("--variants", type=int, default=1)
    generate.add_argument("--time-limit", type=float, default=30)
    generate.add_argument("--seed", type=int, default=42)
    generate.add_argument("--strict-input", action="store_true", help="require canonical LayoutIR input before solving")
    generate.add_argument("--schema", type=Path, default=Path("schemas/layout_ir.schema.json"))
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
                    },
                    "json": json_path.name,
                }
            )
            print(f"variant {result.variant}: {dxf_path} | {pdf_path} | {ifc_path}")
        (args.output / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        return 0
    if args.command == "edit":
        if not any((args.move_room, args.resize_room, args.add_door, args.add_door_at, args.remove_door, args.add_window, args.remove_window)):
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
            dxf_path, pdf_path = export_bundle(args.output, state.spec, state.result, state.report)
            ifc_path = args.output / f"layout_{state.result.variant:02d}.ifc"
            export_ifc(ifc_path, state.spec, state.result)
            json_path = args.output / f"layout_{state.result.variant:02d}.json"
            write_result(json_path, state.spec, state.result)
            print(f"edited ({', '.join(state.history)}): {dxf_path} | {pdf_path} | {ifc_path} | {json_path}")
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
    return 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())

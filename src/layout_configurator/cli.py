"""Command-line entry point."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .export import export_bundle
from .io import load_spec, write_result
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
    args = parser.parse_args(argv)

    if args.command == "generate":
        try:
            spec = load_spec(args.spec)
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
            write_result(json_path, spec, result)
            manifest["variants"].append({"variant": result.variant, "dxf": dxf_path.name, "pdf": pdf_path.name, "json": json_path.name})
            print(f"variant {result.variant}: {dxf_path} | {pdf_path}")
        (args.output / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        return 0
    return 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())

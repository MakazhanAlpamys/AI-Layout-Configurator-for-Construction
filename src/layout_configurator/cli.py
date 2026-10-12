"""Command-line entry point."""

from __future__ import annotations

import argparse
from dataclasses import asdict, replace
import hashlib
import json
import os
import sys
from pathlib import Path

from .brief import parse_text_brief
from .bundle import VariantExportError, write_facility_variant
from .bcf import write_bcf_package
from .building import BuildingSpecError
from .compliance import validate_ids
from .commands import AddDoor, AddWindow, EditError, MoveRoom, RemoveDoor, RemoveExternalEntry, RemoveWindow, ResizeRoom, SetExternalEntry
from .editor import EditorState
from .export import export_building_bundle, export_bundle
from .equipment import validate_equipment_layout
from .facility import default_facility_profile, load_facility_profile, validate_building
from .facility_generation import FACILITY_SOLVER_STRATEGIES, InfeasibleFacilityGeneration, solve_feasible_facility_variants
from .facility_incremental import IncrementalSourceError, re_solve_facility_after_edit
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
from .review import write_review_dossier
from .schema import load_canonical_spec, normalize_mapping, validate_mapping, validate_spec, load_mapping
from .solver import InfeasibleLayout, solve_layouts
from .validation import validate_layout


def _dossier_sources(input_path: Path, variant: int | None) -> list[Path]:
    """Resolve one building result, or every variant of a generated bundle."""

    if input_path.is_dir():
        if variant is not None:
            # ui.py owns the bundle-variant convention; importing it lazily keeps
            # the HTTP server out of the import path of every other command.
            from .ui import resolve_ui_input

            return [resolve_ui_input(input_path, variant=variant)]
        found = sorted(input_path.glob("building_*.json"))
        results = [
            path
            for path in found
            if not path.name.endswith((".coordination.json", ".issue-management.json"))
        ]
        if not results:
            raise ValueError(f"No building result was found in {input_path}")
        return results
    if not input_path.is_file():
        raise ValueError(f"Building result was not found: {input_path}")
    return [input_path]


def _add_import_options(parser):
    parser.add_argument("--imported-plan", type=Path, help="source-backed DXF/IFC/PDF DRAFT JSON to validate and pin")
    parser.add_argument("--confirm-import", action="store_true", help="explicitly confirm reviewed import geometry")
    parser.add_argument("--import-reviewer", help="name of the person confirming imported geometry")
    parser.add_argument("--room-map", nargs=2, action="append", default=[], metavar=("SOURCE", "ROOM"), help="map an imported ID to a canonical room ID; repeat as needed")


def _solve_incremental_cli(args, building, profile):
    """Retain exact prior file identity alongside canonical soft-hint evidence."""
    source_hash = None
    try:
        if args.incremental_from.stat().st_size > 8 * 1024 * 1024:
            raise ValueError("Incremental source result exceeds 8 MiB")
        with args.incremental_from.open("rb") as stream:
            source_bytes = stream.read(8 * 1024 * 1024 + 1)
        if len(source_bytes) > 8 * 1024 * 1024:
            raise ValueError("Incremental source result exceeds 8 MiB")
        source_hash = hashlib.sha256(source_bytes).hexdigest()
        prior_building, prior_result, _ = load_building_result(args.incremental_from)
        if args.incremental_from.read_bytes() != source_bytes:
            raise ValueError("Incremental source changed while it was being loaded")
    except (ValueError, OSError, TypeError, KeyError) as exc:
        raise IncrementalSourceError(str(exc), {
            "stage": "input", "status": "FAIL", "code": "invalid_source_result",
            "message": str(exc), "source_file_sha256": source_hash,
            "proof_scope": "supplied_incremental_source", "proven_building_infeasible": False,
        }) from exc
    try:
        generation = re_solve_facility_after_edit(
            prior_building, prior_result, building, profile, variants=args.variants,
            time_limit_seconds=args.time_limit, seed=args.seed, max_attempts=args.max_attempts,
            equipment_retries=args.equipment_retries, deterministic_units=args.deterministic_units,
            room_solver=args.room_solver, cache_cluster_shapes=True)
    except IncrementalSourceError as exc:
        exc.evidence["source_file_sha256"] = source_hash
        raise
    except InfeasibleFacilityGeneration as exc:
        evidence = {**exc.generation.incremental_evidence, "source_file_sha256": source_hash}
        raise InfeasibleFacilityGeneration(str(exc), replace(exc.generation, incremental_evidence=evidence)) from exc
    if args.incremental_from.read_bytes() != source_bytes:
        raise IncrementalSourceError("Incremental source changed during re-solve", {
            "stage": "input", "status": "FAIL", "code": "source_changed",
            "source_file_sha256": source_hash, "proof_scope": "supplied_incremental_source",
            "proven_building_infeasible": False,
        })
    return replace(generation, incremental_evidence={
        **generation.incremental_evidence, "source_file_sha256": source_hash})


def _confirmed_import(args, spec):
    if args.imported_plan is None:
        if args.confirm_import or args.import_reviewer or args.room_map:
            raise ValueError("Import confirmation/mapping requires --imported-plan")
        return None, None
    from .plan_import import load_imported_constraints

    mapping = {}
    for source_id, room_id in args.room_map:
        if source_id in mapping:
            raise ValueError(f"Duplicate room mapping for {source_id}")
        mapping[source_id] = room_id
    draft_header = json.loads(args.imported_plan.read_text(encoding="utf-8"))
    if not isinstance(draft_header, dict):
        raise ValueError("Import draft root must be an object")
    draft_format = draft_header.get("format")
    if draft_format == "FLC-pdf-plan-draft":
        from .pdf_plan_import import load_pdf_imported_constraints

        fixed = load_pdf_imported_constraints(args.imported_plan, spec, confirmed=args.confirm_import,
                                              reviewer=args.import_reviewer, candidate_mapping=mapping)
    else:
        fixed = load_imported_constraints(args.imported_plan, spec, confirmed=args.confirm_import,
                                          reviewer=args.import_reviewer, room_mapping=mapping)
    evidence = json.loads(args.imported_plan.with_suffix(".confirmation.json").read_text(encoding="utf-8"))
    return fixed, evidence


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="layout-configurator", description="Solver-first facility layout compiler")
    subparsers = parser.add_subparsers(dest="command", required=True)
    generate = subparsers.add_parser("generate", help="solve a JSON/YAML specification and export DXF/PDF")
    generate.add_argument("spec", type=Path)
    generate.add_argument("--output", "-o", type=Path, default=Path("out"))
    generate.add_argument("--variants", type=int, default=1)
    generate.add_argument("--time-limit", type=float, default=30)
    generate.add_argument("--deterministic-budget", type=float, default=None, dest="deterministic_units", help="spend a CP-SAT work budget instead of the wall clock for repeatability within the same runtime; wall time varies by machine and cross-platform output may differ")
    generate.add_argument("--seed", type=int, default=42)
    generate.add_argument("--strict-input", action="store_true", help="require canonical LayoutIR input before solving")
    generate.add_argument("--schema", type=Path, default=Path("schemas/layout_ir.schema.json"))
    _add_import_options(generate)
    generate_building = subparsers.add_parser(
        "generate-building", help="solve a dense-building program and place equipment"
    )
    generate_building.add_argument("spec", type=Path)
    generate_building.add_argument("--incremental-from", type=Path, help="prior canonical building result for soft geometry guidance and local shape reuse; source IDs must remain unchanged")
    _add_import_options(generate_building)
    generate_building.add_argument("--output", "-o", type=Path, default=Path("out/building"))
    generate_building.add_argument("--variants", type=int, default=1)
    generate_building.add_argument(
        "--time-limit",
        type=float,
        default=60,
        help="CP-SAT wall-clock limit in seconds; dense facility defaults to 60",
    )
    generate_building.add_argument(
        "--deterministic-budget",
        type=float,
        default=None,
        dest="deterministic_units",
        help="spend a CP-SAT work budget instead of the wall clock for repeatability within the same runtime; wall time varies by machine and cross-platform output may differ",
    )
    generate_building.add_argument("--room-solver", choices=FACILITY_SOLVER_STRATEGIES, default="auto", help="room stage: auto, room clusters, monolithic, root zone containers (zone_hierarchical), or recursive zone containers (zone_recursive)")
    generate_building.add_argument("--seed", type=int, default=42)
    generate_building.add_argument(
        "--max-attempts",
        type=int,
        default=None,
        help="maximum room-layout candidates evaluated before reporting an incomplete accepted set; default is max(3, variants * 3)",
    )
    generate_building.add_argument(
        "--equipment-retries",
        type=int,
        default=2,
        help="deterministic equipment-packing retries per room-layout candidate",
    )
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
    review_dossier = subparsers.add_parser(
        "review-dossier",
        help="project an accepted bundle into one dossier per external review gate",
    )
    review_dossier.add_argument("input", type=Path, help="building result JSON or a generated bundle directory")
    review_dossier.add_argument("--profile", type=Path, default=None)
    review_dossier.add_argument("--output", "-o", type=Path, default=Path("review"))
    review_dossier.add_argument("--variant", type=int, default=None, help="single variant when input is a bundle directory")
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
    acceptance_matrix = subparsers.add_parser(
        "acceptance-building-matrix",
        help="generate and QA a full facility acceptance set for several deterministic seeds",
    )
    acceptance_matrix.add_argument("spec", type=Path)
    acceptance_matrix.add_argument("--profile", type=Path, required=True)
    acceptance_matrix.add_argument("--output", "-o", type=Path, default=Path("out/facility_acceptance_matrix"))
    acceptance_matrix.add_argument("--variants", type=int, default=3)
    acceptance_matrix.add_argument("--time-limit", type=float, default=30)
    acceptance_matrix.add_argument("--deterministic-budget", type=float, default=None, dest="deterministic_units", help="spend a CP-SAT work budget instead of the wall clock for repeatability within the same runtime; wall time varies by machine and cross-platform output may differ")
    acceptance_matrix.add_argument("--room-solver", choices=FACILITY_SOLVER_STRATEGIES, default="auto", help="room stage: auto, room clusters, monolithic, root zone containers (zone_hierarchical), or recursive zone containers (zone_recursive)")
    acceptance_matrix.add_argument("--seeds", type=int, nargs="+", default=[1, 7, 42])
    acceptance_matrix.add_argument("--max-attempts", type=int, default=None)
    acceptance_matrix.add_argument("--equipment-retries", type=int, default=2)
    acceptance_matrix.add_argument(
        "--report",
        type=Path,
        default=None,
        help="write matrix evidence JSON; defaults to OUTPUT/acceptance-matrix-report.json",
    )
    facility_floors = subparsers.add_parser(
        "generate-facility-floors",
        help="solve a multi-storey facility: floors, vertical cores and cross-floor flows",
    )
    facility_floors.add_argument("spec", type=Path, help="facility_multi_floor YAML/JSON")
    facility_floors.add_argument("--output", "-o", type=Path, default=Path("out/facility_floors"))
    facility_floors.add_argument("--profile", type=Path, default=None, help="profile for floors that do not name one")
    facility_floors.add_argument("--variants", type=int, default=1)
    facility_floors.add_argument("--max-attempts", type=int, default=None)
    facility_floors.add_argument("--time-limit", type=float, default=60)
    facility_floors.add_argument("--seed", type=int, default=42)
    facility_floors.add_argument("--room-solver", choices=FACILITY_SOLVER_STRATEGIES, default="auto", help="room stage: auto, room clusters, monolithic, root zone containers (zone_hierarchical), or recursive zone containers (zone_recursive)")
    facility_floors.add_argument("--unified-ifc", action="store_true", help="require a unified IFC and an explicitly declared shared grid; shared-grid programs export it automatically")
    compare_variants = subparsers.add_parser(
        "compare-variants",
        help="rank the variants of a generated facility bundle and write JSON, CSV and a client PDF",
    )
    compare_variants.add_argument("bundle", type=Path, help="directory with building_NN.json variants")
    compare_variants.add_argument("--output", "-o", type=Path, default=None, help="defaults to the bundle directory")
    compare_variants.add_argument("--no-pdf", action="store_true", help="skip comparison.pdf")
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
    ui.add_argument("--edit", action="store_true", help="facility bundles: allow room moves/resizes with live re-check and save revisions")
    api = subparsers.add_parser("api", help="serve persistent facility generation, batch, check and review jobs over HTTP")
    api.add_argument("--output", "-o", type=Path, default=Path("out/api-jobs"))
    api.add_argument("--host", default="127.0.0.1")
    api.add_argument("--port", type=int, default=8766)
    import_source = subparsers.add_parser("import-plan", help="extract DXF/IFC rooms or calibrated PDF candidates into an unconfirmed draft")
    import_source.add_argument("source", type=Path)
    import_source.add_argument("--output", "-o", type=Path, required=True)
    import_source.add_argument("--unit-scale-mm", type=float, help="millimetres per DXF unit or PDF page point")
    import_source.add_argument("--storey", help="IFC storey GlobalId or name")
    import_source.add_argument("--page", type=int, help="one-based PDF page; required for PDF")
    import_source.add_argument("--origin-pt", type=float, nargs=2, metavar=("X", "Y"), help="PDF point coordinates corresponding to canonical (0,0); required for PDF")
    import_raster = subparsers.add_parser("import-raster", help="read PNG/JPEG source identity and optional unverified OCR words in pixels")
    import_raster.add_argument("source", type=Path)
    import_raster.add_argument("--output", "-o", type=Path, required=True)
    raster_ocr = import_raster.add_mutually_exclusive_group()
    raster_ocr.add_argument("--ocr-tsv", type=Path, help="exact UTF-8 Tesseract TSV with boxes in upright-after-EXIF pixels")
    raster_ocr.add_argument("--ocr-tesseract", action="store_true", help="explicitly run a local Tesseract executable")
    import_raster.add_argument("--tesseract-path", type=Path, help="optional local executable path; requires --ocr-tesseract")
    import_raster.add_argument("--ocr-language", default="eng", help="local Tesseract language identifiers, for example eng+rus")
    import_raster.add_argument("--ocr-timeout", type=float, default=30, help="local OCR deadline in seconds, at most 60")
    propose = subparsers.add_parser("propose-edits", help="validate typed program edits into a reviewable draft")
    propose.add_argument("spec", type=Path)
    propose.add_argument("edits", type=Path, help="JSON object containing the typed edits array")
    propose.add_argument("--source-text", type=Path, required=True, help="UTF-8 source request text")
    propose.add_argument("--producer", default="manual")
    propose.add_argument("--source-kind", choices=("human_text", "ai_proposal"), default="human_text")
    propose.add_argument("--output", "-o", type=Path, required=True)
    apply_edits = subparsers.add_parser("apply-edits", help="apply a confirmed typed draft to its exact canonical base")
    apply_edits.add_argument("spec", type=Path)
    apply_edits.add_argument("draft", type=Path)
    apply_edits.add_argument("--output", "-o", type=Path, required=True)
    apply_edits.add_argument("--confirm", action="store_true")
    apply_edits.add_argument("--reviewer")
    propose_llm = subparsers.add_parser("propose-llm-edits", help="request a strict typed DRAFT from an explicitly configured compatible provider")
    propose_llm.add_argument("spec", type=Path)
    propose_llm.add_argument("request", type=Path, help="UTF-8 request text")
    propose_llm.add_argument("--output", "-o", type=Path, required=True)
    propose_llm.add_argument("--timeout", type=float, default=60)
    args = parser.parse_args(argv)

    if args.command == "import-raster":
        try:
            from .raster_plan_import import import_raster_plan, write_raster_plan_draft

            draft = import_raster_plan(args.source, ocr_tsv=args.ocr_tsv, ocr_tesseract=args.ocr_tesseract,
                tesseract_path=args.tesseract_path, language=args.ocr_language, timeout_seconds=args.ocr_timeout)
            write_raster_plan_draft(args.output, draft)
            print(f"DRAFT: {args.output}; {len(draft.ocr['words'])} unverified OCR words in pixels; OCR {draft.ocr['status']}")
            return 0
        except (OSError, ValueError, RuntimeError) as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 2

    if args.command == "propose-llm-edits":
        try:
            from .llm_edits import propose_with_compatible_provider
            from .proposed_edits import write_proposed_edits

            draft = propose_with_compatible_provider(load_building(args.spec),
                       args.request.read_text(encoding="utf-8"),
                       **llm_settings_from_environment(), timeout_seconds=args.timeout)
            write_proposed_edits(args.output, draft)
            print(f"DRAFT: {args.output}; {len(draft.edits)} typed proposals; confirmation and regeneration required")
            return 0
        except (OSError, ValueError, RuntimeError) as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 2

    if args.command == "propose-edits":
        try:
            from .proposed_edits import propose_edits, write_proposed_edits

            draft = propose_edits(load_building(args.spec), json.loads(args.edits.read_text(encoding="utf-8")),
                                  source_text=args.source_text.read_text(encoding="utf-8"),
                                  producer=args.producer, source_kind=args.source_kind)
            write_proposed_edits(args.output, draft)
            print(f"DRAFT: {args.output}; {len(draft.edits)} typed edits; confirmation required")
            return 0
        except (OSError, ValueError, RuntimeError) as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 2

    if args.command == "apply-edits":
        try:
            from .proposed_edits import apply_proposed_edits, read_proposed_edits

            applied = apply_proposed_edits(load_building(args.spec), read_proposed_edits(args.draft),
                                            confirmed=args.confirm, reviewer=args.reviewer)
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(json.dumps(applied.building.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8")
            args.output.with_suffix(".application.json").write_text(
                json.dumps(dict(applied.evidence), indent=2, ensure_ascii=False), encoding="utf-8")
            print(f"Canonical input: {args.output}; PENDING_SOLVER_AND_CHECKS; run generate-building on this input")
            return 0
        except (OSError, ValueError, RuntimeError) as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 2

    if args.command == "import-plan":
        try:
            from .plan_import import import_plan, write_import_draft

            if args.source.suffix.lower() == ".pdf":
                from .pdf_plan_import import import_pdf_plan, write_pdf_plan_draft

                if args.page is None or args.unit_scale_mm is None or args.origin_pt is None or args.storey is not None:
                    raise ValueError("PDF import requires --page, --unit-scale-mm and --origin-pt")
                draft = import_pdf_plan(args.source, page=args.page, unit_scale_mm=args.unit_scale_mm,
                                        origin_pt=tuple(args.origin_pt))
                write_pdf_plan_draft(args.output, draft)
                count = len(draft.candidates)
            else:
                if args.page is not None or args.origin_pt is not None:
                    raise ValueError("--page and --origin-pt apply to PDF sources only")
                draft = import_plan(args.source, unit_scale_mm=args.unit_scale_mm, storey=args.storey)
                write_import_draft(args.output, draft)
                count = len(draft.rooms)
            print(f"DRAFT: {args.output}; {count} objects; {len(draft.issues)} unresolved issues")
            return 0
        except (OSError, ValueError, RuntimeError) as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 2

    if args.command == "api":
        try:
            from .api import serve_api

            return serve_api(args.output, host=args.host, port=args.port, token=os.environ.get("LAYOUT_API_TOKEN"))
        except (OSError, ValueError, RuntimeError) as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 2

    if args.command == "generate":
        try:
            spec = load_canonical_spec(args.spec, args.schema) if args.strict_input else load_spec(args.spec)
            fixed_rects, import_confirmation = _confirmed_import(args, spec)
            results = solve_layouts(
                spec,
                args.variants,
                args.time_limit,
                args.seed,
                deterministic_units=args.deterministic_units,
                fixed_rects=fixed_rects,
            )
        except (OSError, ValueError, InfeasibleLayout, RuntimeError) as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 2

        args.output.mkdir(parents=True, exist_ok=True)
        manifest = {"project": spec.project_name, "variants": []}
        if import_confirmation is not None:
            manifest["import_confirmation"] = import_confirmation
        for result in results:
            report = validate_layout(spec, result)
            if not report.ok:
                print(f"ERROR: variant {result.variant} failed validation", file=sys.stderr)
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
    if args.command == "acceptance-building-matrix":
        if args.variants < 1:
            print("ERROR: variants must be at least 1", file=sys.stderr)
            return 2
        if not args.seeds:
            print("ERROR: at least one seed is required", file=sys.stderr)
            return 2
        try:
            profile = load_facility_profile(args.profile)
        except (OSError, ValueError) as exc:
            print(f"ERROR: facility profile cannot be loaded: {exc}", file=sys.stderr)
            return 2

        args.output.mkdir(parents=True, exist_ok=True)
        runs = []
        semantic_baseline = None
        semantic_mismatches = []
        for seed in args.seeds:
            seed_output = args.output / f"seed_{seed}"
            generate_argv = [
                "generate-building",
                str(args.spec),
                "--profile",
                str(args.profile),
                "--output",
                str(seed_output),
                "--variants",
                str(args.variants),
                "--time-limit",
                str(args.time_limit),
                *(
                    ["--deterministic-budget", str(args.deterministic_units)]
                    if args.deterministic_units is not None
                    else []
                ),
                "--seed",
                str(seed),
                "--equipment-retries",
                str(args.equipment_retries),
                "--room-solver",
                args.room_solver,
            ]
            if args.max_attempts is not None:
                generate_argv.extend(["--max-attempts", str(args.max_attempts)])
            generation_code = main(generate_argv)
            run = {
                "seed": seed,
                "output": str(seed_output),
                "generation_exit_code": generation_code,
            }
            if generation_code != 0:
                failure_path = seed_output / "generation-failure.json"
                if failure_path.exists():
                    run["generation_failure"] = json.loads(failure_path.read_text(encoding="utf-8"))
            if generation_code == 0:
                try:
                    qa_report = validate_building_set(
                        seed_output,
                        expected_variants=args.variants,
                        profile=profile,
                    )
                except (OSError, KeyError, TypeError, ValueError, RuntimeError) as exc:
                    run["qa_error"] = str(exc)
                else:
                    run["qa"] = qa_report.to_dict()
                    semantic_ids = qa_report.consistency.details.get("baseline", {}).get("semantic_ids")
                    if semantic_baseline is None:
                        semantic_baseline = semantic_ids
                    elif semantic_ids != semantic_baseline:
                        semantic_mismatches.append({"seed": seed, "semantic_ids": semantic_ids})
            runs.append(run)

        ok = (
            not semantic_mismatches
            and all(
                run["generation_exit_code"] == 0
                and isinstance(run.get("qa"), dict)
                and bool(run["qa"].get("ok"))
                for run in runs
            )
        )
        matrix_report = {
            "ok": ok,
            "spec": str(args.spec),
            "profile": profile.to_dict(),
            "requested_variants": args.variants,
            "seeds": list(args.seeds),
            "search": {
                "time_limit_seconds": args.time_limit,
                "deterministic_units": args.deterministic_units,
                "repeatable": args.deterministic_units is not None,
                "max_attempts": args.max_attempts if args.max_attempts is not None else max(3, args.variants * 3),
                "equipment_retries": args.equipment_retries,
                "room_solver": args.room_solver,
            },
            "runs": runs,
            "cross_seed_semantic_ids": {
                "status": (
                    "FAIL" if semantic_mismatches else
                    "PASS" if all(run.get("qa", {}).get("ok") for run in runs) else
                    "UNKNOWN"
                ),
                "baseline": semantic_baseline,
                "mismatches": semantic_mismatches,
            },
        }
        report_path = args.report or args.output / "acceptance-matrix-report.json"
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(matrix_report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"Acceptance matrix: {'PASS' if ok else 'FAIL'}; report: {report_path}")
        return 0 if ok else 4
    if args.command == "generate-facility-floors":
        from .facility_floors import FacilityFloorsError, load_facility_floors, solve_facility_floors

        try:
            floors_spec = load_facility_floors(args.spec)
            if args.unified_ifc and floors_spec.structural_grid is None:
                raise FacilityFloorsError("Unified facility IFC requires an explicit shared structural grid")
            solved = solve_facility_floors(
                floors_spec,
                variants=args.variants,
                time_limit_seconds=args.time_limit,
                seed=args.seed,
                max_attempts=args.max_attempts,
                room_solver=args.room_solver,
                default_profile=load_facility_profile(args.profile) if args.profile else None,
            )
        except (FacilityFloorsError, BuildingSpecError, OSError) as exc:
            args.output.mkdir(parents=True, exist_ok=True)
            (args.output / "multi-floor-failure.json").write_text(
                json.dumps({"ok": False, "message": str(exc), "evidence": getattr(exc, "evidence", {})}, ensure_ascii=False, indent=2, default=str),
                encoding="utf-8",
            )
            print(f"ERROR: {exc}", file=sys.stderr)
            return 2
        args.output.mkdir(parents=True, exist_ok=True)
        floor_manifests: dict[int, dict] = {}
        report = {
            "project": floors_spec.project_name,
            "floor_height_mm": floors_spec.floor_height_mm,
            "structural_grid": None if floors_spec.structural_grid is None else floors_spec.structural_grid.to_dict(),
            "floors": [
                {"level": entry.level, "elevation_mm": entry.elevation_mm, "program": entry.program_path,
                 "profile": solved.profiles[entry.level].name, "bundle": f"floor_{entry.level:02d}"}
                for entry in floors_spec.floors
            ],
            "vertical_cores": [{"id": core.id, "kind": core.kind, "rooms": {str(k): v for k, v in core.rooms.items()}} for core in floors_spec.cores],
            "cross_floor_flows": [
                {"id": flow.id, "type": flow.type, "from": {"level": flow.from_level, "id": flow.from_id},
                 "to": {"level": flow.to_level, "id": flow.to_id}, "via": floors_spec.core_for(flow).id,
                 "legs": [flow.leg_id(flow.from_level), flow.leg_id(flow.to_level)]}
                for flow in floors_spec.cross_flows
            ],
            "stacks": [],
            "rejected_stacks": solved.rejected,
        }
        if floors_spec.blocks:
            report["facility_blocks"] = [block.to_dict() for block in floors_spec.blocks]
        if floors_spec.service_links:
            report["service_zone_links"] = [link.to_dict() for link in floors_spec.service_links]
        for stack in solved.stacks:
            files = {}
            for level, candidate in sorted(stack.floors.items()):
                floor_dir = args.output / f"floor_{level:02d}"
                result = candidate.result if candidate.result.variant == stack.number else replace(candidate.result, variant=stack.number)
                try:
                    entry = write_facility_variant(
                        floor_dir, solved.programs[level], solved.profiles[level], result,
                        layout_report=candidate.layout_report, equipment=candidate.equipment,
                        equipment_report=candidate.equipment_report, flow_routes=candidate.flow_routes,
                        flow_report=candidate.flow_report, facility_report=candidate.facility_report,
                        generation_evidence={**candidate.generation_evidence(), "stack": stack.number, "level": level},
                    )
                except VariantExportError as exc:
                    print(f"ERROR: floor {level}: {exc}", file=sys.stderr)
                    return 3
                floor_manifests.setdefault(level, {
                    "project": solved.programs[level].layout.project_name,
                    "level": level,
                    "facility_profile": solved.profiles[level].to_dict(),
                    "drawing_profile": solved.profiles[level].drawing.to_dict(),
                    "variants": [],
                })["variants"].append(entry)
                files[str(level)] = f"floor_{level:02d}/{entry['json']}"
            unified_ifc = None
            if floors_spec.structural_grid is not None:
                from .ifc import export_facility_floors_ifc

                ifc_path = args.output / f"facility_stack_{stack.number:02d}.ifc"
                try:
                    summary = export_facility_floors_ifc(ifc_path, floors_spec, stack, solved.programs)
                    readback = validate_ifc_roundtrip(ifc_path, summary)
                    if not readback.ok:
                        raise ValueError("Unified IFC read-back rejected: " + "; ".join(readback.issues))
                    summary_fields = asdict(summary)
                    summary_fields.pop("path")
                    unified_ifc = {"path": ifc_path.name,
                                   "sha256": hashlib.sha256(ifc_path.read_bytes()).hexdigest(),
                                   "summary": summary_fields, "readback": readback.to_dict(),
                                   "identity_contract": "L{level}/{kind}/{local_id}"}
                except (ImportError, OSError, ValueError, RuntimeError) as exc:
                    print(f"ERROR: unified IFC: {exc}", file=sys.stderr)
                    return 3
            report["stacks"].append({
                "stack": stack.number,
                "ok": stack.ok,
                "floors": files,
                "floor_checks_ok": {str(level): candidate.facility_report.ok for level, candidate in stack.floors.items()},
                "vertical_checks": [check.to_dict() for check in stack.checks],
                "unified_ifc": unified_ifc,
            })
            print(f"stack {stack.number}: {'PASS' if stack.ok else 'FAIL'} — " + ", ".join(f"floor {k}: {v}" for k, v in files.items()))
        for level, manifest in floor_manifests.items():
            (args.output / f"floor_{level:02d}" / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        report["ok"] = all(stack["ok"] for stack in report["stacks"])
        (args.output / "multi_floor_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        print(f"Multi-floor facility: {'PASS' if report['ok'] else 'FAIL'}; report: {args.output / 'multi_floor_report.json'}")
        return 0 if report["ok"] else 4
    if args.command == "compare-variants":
        from .compare import compare_bundle, write_comparison

        try:
            written = write_comparison(args.bundle, args.output, pdf=not args.no_pdf)
        except FileNotFoundError as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 2
        for metrics in compare_bundle(args.bundle):
            print(
                f"#{metrics.rank} variant {metrics.variant}: "
                f"{'PASS' if metrics.facility_ok else 'FAIL'}, {metrics.open_issues} open issue(s), "
                f"routes {metrics.route_length_m:.1f} m, area deviation {metrics.area_deviation_pct:.1f} %"
            )
        print(" | ".join(str(path) for path in written.values()))
        return 0
    if args.command == "generate-building":
        try:
            if args.incremental_from is not None:
                if args.imported_plan or args.confirm_import or args.import_reviewer or args.room_map:
                    raise ValueError("Incremental soft geometry cannot be combined with imported fixed-room constraints")
                if any(path.resolve().is_relative_to(args.output.resolve())
                       for path in (args.incremental_from, args.spec)):
                    raise ValueError("Incremental source and edited input must be outside the output directory")
            (args.output / "generation-failure.json").unlink(missing_ok=True)
            building = load_building(args.spec).with_required_flow_opening_width()
            fixed_rects, import_confirmation = _confirmed_import(args, building.layout)
            profile = load_facility_profile(args.profile) if args.profile else default_facility_profile()
            generation = _solve_incremental_cli(args, building, profile) if args.incremental_from is not None else solve_feasible_facility_variants(
                building,
                profile,
                variants=args.variants,
                time_limit_seconds=args.time_limit,
                seed=args.seed,
                max_attempts=args.max_attempts,
                equipment_retries=args.equipment_retries,
                deterministic_units=args.deterministic_units,
                room_solver=args.room_solver,
                fixed_rects=fixed_rects,
            )
        except IncrementalSourceError as exc:
            args.output.mkdir(parents=True, exist_ok=True)
            failure = {"ok": False, "status": "INCREMENTAL_SOURCE_REJECTED", "message": str(exc),
                       "spec": building.to_dict(), "profile": profile.to_dict(), "input_evidence": exc.evidence}
            (args.output / "generation-failure.json").write_text(
                json.dumps(failure, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            print(f"ERROR: {exc}", file=sys.stderr)
            return 2
        except InfeasibleFacilityGeneration as exc:
            args.output.mkdir(parents=True, exist_ok=True)
            failure = {
                "ok": False,
                "status": "NO_ACCEPTED_SET_WITHIN_BUDGET",
                "message": str(exc),
                "spec": building.to_dict(),
                "profile": profile.to_dict(),
                "generation": exc.generation.to_dict(),
            }
            (args.output / "generation-failure.json").write_text(
                json.dumps(failure, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
            )
            print(f"ERROR: {exc}", file=sys.stderr)
            return 2
        except (
            OSError,
            ValueError,
            BuildingSpecError,
            InfeasibleLayout,
            RuntimeError,
        ) as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 2

        args.output.mkdir(parents=True, exist_ok=True)
        manifest = {
            "project": building.layout.project_name,
            "facility_profile": profile.to_dict(),
            "drawing_profile": profile.drawing.to_dict(),
            "generation": generation.to_dict(),
            "variants": [],
        }
        if import_confirmation is not None:
            manifest["import_confirmation"] = import_confirmation
        for candidate in generation.accepted:
            try:
                variant_evidence = candidate.generation_evidence()
                if generation.incremental_evidence is not None:
                    variant_evidence.update(incremental_evidence=generation.incremental_evidence,
                                            room_solver_evidence=generation.room_solver_evidence)
                entry = write_facility_variant(
                    args.output,
                    building,
                    profile,
                    candidate.result,
                    layout_report=candidate.layout_report,
                    equipment=candidate.equipment,
                    equipment_report=candidate.equipment_report,
                    flow_routes=candidate.flow_routes,
                    flow_report=candidate.flow_report,
                    facility_report=candidate.facility_report,
                    generation_evidence=variant_evidence,
                    bcf_input=args.bcf_input,
                )
            except VariantExportError as exc:
                print(f"ERROR: {exc}", file=sys.stderr)
                return 3
            manifest["variants"].append(entry)
            print(
                f"variant {entry['variant']}: {args.output / entry['dxf']} | {args.output / entry['pdf']} | "
                f"{args.output / entry['ifc']} | {args.output / entry['json']} | "
                f"{args.output / entry['coordination_issues']} | {args.output / entry['bcf']}"
            )
            if entry["flow_issues"]:
                print(
                    f"  flow validation: {entry['flow_issues']} issue(s); see {entry['json']} -> flow_validation",
                    file=sys.stderr,
                )
        (args.output / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return 0
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
    if args.command == "review-dossier":
        try:
            sources = _dossier_sources(args.input, args.variant)
        except ValueError as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 2
        profile_path = args.profile
        for source in sources:
            try:
                building, result, equipment = load_building_result(source)
                profile = load_facility_profile(profile_path) if profile_path else default_facility_profile()
                equipment_report = validate_equipment_layout(building, result, equipment)
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
                written = write_review_dossier(
                    args.output,
                    building,
                    result,
                    equipment,
                    flow_routes,
                    facility_report,
                    profile,
                    source=source,
                )
            except (OSError, ValueError, KeyError, RuntimeError) as exc:
                print(f"ERROR: {exc}", file=sys.stderr)
                return 2
            print(f"{source.name}: " + " | ".join(str(path) for path in written.values()))
            if not facility_report.ok:
                print(
                    f"WARNING: {source.name} is not a fully accepted variant; "
                    "the dossier records its open coordination issues",
                    file=sys.stderr,
                )
        return 0

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
            print("ERROR: specify at least one typed edit command", file=sys.stderr)
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
                edit=args.edit,
            )
        except (OSError, ValueError, KeyError, RuntimeError, InfeasibleLayout) as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 2
    return 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())

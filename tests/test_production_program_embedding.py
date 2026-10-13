"""Explicit source-only embedding never grants solver or engineering acceptance."""

import copy
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import replace
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from layout_configurator.building import BuildingIR
from layout_configurator.cli import main
from layout_configurator.facility import default_facility_profile, validate_building
from layout_configurator.facility_generation import InfeasibleFacilityGeneration, solve_feasible_facility_variants
from layout_configurator.facility_incremental import IncrementalSourceError, re_solve_facility_after_edit
from layout_configurator.models import LayoutIR, LayoutResult, Rect
from layout_configurator.production_program import validate_production_program
from layout_configurator.schema import normalize_building_mapping
from test_production_program import _building, _digest, _program


def _tiny_building():
    return BuildingIR.from_mapping({"layout": {
        "project_name": "Embedding guard", "boundary": {"width": 4000, "height": 4000},
        "entry_room": "molding", "rooms": [
            {"id": "molding", "target_area": 16, "min_width": 4000, "min_depth": 4000}]}})


def _tiny_program(building):
    raw = _program(building)
    raw["operations"] = raw["operations"][:1]
    raw["operations"][0].update(equipment_ids=[], flow_ids=[])
    raw["requirements"] = []
    return raw


class ProductionProgramEmbeddingTests(unittest.TestCase):
    def setUp(self):
        self.base = _building()
        self.raw = self.base.to_dict()
        self.raw["production_program"] = _program(self.base)

    def admit(self, raw=None):
        return BuildingIR.from_mapping(self.raw if raw is None else raw, include_production_program=True)

    def test_ordinary_constructor_cannot_silently_drop_embedded_source(self):
        with self.assertRaisesRegex(ValueError, "production_program"):
            BuildingIR.from_mapping(self.raw)
        with self.assertRaises(ValueError):
            normalize_building_mapping(self.raw)
        for value in (None, {}, False):
            raw = {**self.raw, "production_program": value}
            with self.subTest(value=value), self.assertRaises(ValueError):
                BuildingIR.from_mapping(raw)

    def test_opt_in_roundtrip_retains_whole_source_and_excludes_program_from_binding(self):
        before = copy.deepcopy(self.raw)
        embedded = self.admit()
        self.assertEqual(embedded.to_dict(), before)
        self.assertEqual(self.raw, before)
        self.assertNotEqual(_digest(embedded.to_dict()), self.raw["production_program"]["building_sha256"])
        self.assertEqual(_digest(replace(embedded, production_program=None).to_dict()),
                         self.raw["production_program"]["building_sha256"])
        report = validate_production_program(embedded.production_program.to_dict(), building=embedded)
        self.assertEqual((report.status, report.building_sha256),
                         ("PASS", self.raw["production_program"]["building_sha256"]))
        detached = embedded.to_dict()
        detached["production_program"]["sources"][0]["content"] = "changed"
        self.assertEqual(embedded.to_dict(), before)
        self.assertEqual(normalize_building_mapping(before, include_production_program=True).to_dict(), before)

    def test_raw_types_are_rejected_before_coercing_constructor(self):
        for target, field, value in (("layout", "grid_mm", True),
                                     ("equipment", "width_mm", True),
                                     ("equipment", "width_mm", "1000")):
            raw = copy.deepcopy(self.raw)
            row = raw[target][0] if target == "equipment" else raw[target]
            row[field] = value
            with self.subTest(target=target, field=field, value=value):
                with patch.object(LayoutIR, "from_mapping", side_effect=AssertionError("Raw source was coerced")):
                    with self.assertRaises(ValueError):
                        self.admit(raw)
        raw = copy.deepcopy(self.raw)
        raw["production_program"]["operations"][0]["measurements"]["operators"]["value"] = True
        with self.assertRaises(ValueError):
            self.admit(raw)

    def test_stale_self_inclusive_and_scalar_identity_drift_are_rejected(self):
        changes = (
            lambda raw: raw["equipment"][0].update(width_mm=1100.0),
            lambda raw: raw["production_program"].update(building_sha256=_digest(raw)),
            lambda raw: raw["equipment"][0].update(width_mm=1000),
            lambda raw: raw["production_program"]["sources"][0].update(content="tampered"),
        )
        for index, change in enumerate(changes):
            raw = copy.deepcopy(self.raw)
            change(raw)
            with self.subTest(index=index), self.assertRaises(ValueError):
                self.admit(raw)

    def test_generation_rejects_embedded_program_before_any_cp_call(self):
        embedded = self.admit()
        with patch("layout_configurator.facility_generation.solve_layouts_auto") as ordinary, \
                patch("layout_configurator.facility_generation.solve_zone_layouts") as zones:
            with self.assertRaises(InfeasibleFacilityGeneration) as caught:
                solve_feasible_facility_variants(embedded, default_facility_profile(),
                                                variants=1, max_attempts=1, time_limit_seconds=1, seed=1)
        ordinary.assert_not_called()
        zones.assert_not_called()
        report = caught.exception.generation.to_dict()
        self.assertEqual(report["accepted_candidates"], 0)
        self.assertEqual(report["attempted_candidates"], 0)
        failure = report["failure_evidence"][0]
        self.assertEqual((failure["stage"], failure["status"], failure["code"]),
                         ("input", "UNKNOWN", "unsupported_production_program"))
        self.assertEqual(failure["source_ids"], ["parts_program"])
        self.assertFalse(failure["proven_building_infeasible"])

    def test_incremental_rejects_either_source_even_if_the_program_was_removed(self):
        embedded = self.admit()
        previous = LayoutResult(1, {})
        for prior, edited in ((embedded, self.base), (self.base, embedded), (embedded, embedded)):
            with self.subTest(prior=prior.production_program is not None, edited=edited.production_program is not None):
                with patch("layout_configurator.facility_incremental.solve_feasible_facility_variants") as solve:
                    with self.assertRaises(IncrementalSourceError) as caught:
                        re_solve_facility_after_edit(prior, previous, edited, default_facility_profile())
                solve.assert_not_called()
                evidence = caught.exception.evidence
                self.assertEqual(evidence["code"], "unsupported_production_program")
                self.assertFalse(evidence["proven_building_infeasible"])

    def test_cli_valid_incomplete_and_stale_reports_have_truthful_exit_codes(self):
        with tempfile.TemporaryDirectory() as directory:
            source, output = Path(directory) / "source.json", Path(directory) / "report.json"
            for status, exit_code in (("PASS", 0), ("UNKNOWN", 4), ("FAIL", 2)):
                raw = copy.deepcopy(self.raw)
                if status == "UNKNOWN":
                    raw["production_program"]["operations"][0]["measurements"]["cycle_time"].update(
                        status="UNKNOWN", value=None, source_id=None)
                elif status == "FAIL":
                    raw["production_program"]["building_sha256"] = "0" * 64
                source.write_text(json.dumps(raw), encoding="utf-8")
                with self.subTest(status=status), redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                    self.assertEqual(main(["validate-production-program", str(source), "--output", str(output)]), exit_code)
                report = json.loads(output.read_text(encoding="utf-8"))
                self.assertEqual(report["status"], status)
                self.assertEqual(report["engineering_acceptance"], "PENDING")
                self.assertEqual(report["geometry_validation"], "NOT_EVALUATED")

    def test_cli_rejects_duplicate_json_yaml_keys(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source.json"
            source.write_text('{"version":"0.2","version":"0.2"}', encoding="utf-8")
            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                self.assertEqual(main(["validate-production-program", str(source)]), 2)
            source = Path(directory) / "source.yaml"
            source.write_text("version: '0.2'\nversion: '0.2'\n", encoding="utf-8")
            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                self.assertEqual(main(["validate-production-program", str(source)]), 2)

    def test_raw_building_record_budget_is_checked_before_constructor(self):
        from layout_configurator.production_program import ProductionProgramLimits, admit_embedded_production_program

        with patch.object(LayoutIR, "from_mapping", side_effect=AssertionError("Object budget was checked too late")):
            with self.assertRaisesRegex(ValueError, "input_budget_exceeded"):
                admit_embedded_production_program(self.raw, limits=ProductionProgramLimits(max_building_objects=1))

    def test_raw_building_relation_budget_is_checked_before_constructor(self):
        from layout_configurator.production_program import ProductionProgramLimits, admit_embedded_production_program

        # The contract references fit; adding the original room relations and
        # source flow endpoints exceeds this combined request budget.
        with patch.object(LayoutIR, "from_mapping", side_effect=AssertionError("Reference budget was checked too late")):
            with self.assertRaisesRegex(ValueError, "input_budget_exceeded"):
                admit_embedded_production_program(self.raw, limits=ProductionProgramLimits(max_references=26))

    def test_file_byte_budget_rejects_before_json_parsing(self):
        from layout_configurator.production_program import ProductionProgramLimits, load_embedded_production_program

        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source.json"
            source.write_bytes(b" " * 17)
            with patch("layout_configurator.production_program.json.loads", side_effect=AssertionError("File limit was checked too late")):
                with self.assertRaisesRegex(ValueError, "input_budget_exceeded"):
                    load_embedded_production_program(source, limits=ProductionProgramLimits(max_total_utf8_bytes=16))

    def test_independent_facility_qa_cannot_pass_embedded_process_requirements(self):
        base = _tiny_building()
        previous = LayoutResult(1, {"molding": Rect(0, 0, 4000, 4000)})
        profile = default_facility_profile()
        self.assertTrue(validate_building(base, previous, None, None, None, profile=profile).ok)
        embedded = self.admit({**base.to_dict(), "production_program": _tiny_program(base)})
        report = validate_building(embedded, previous, None, None, None, profile=profile)
        self.assertFalse(report.ok)
        capability = next(check for check in report.checks if check.id == "PRODUCTION_PROGRAM_CAPABILITY")
        self.assertEqual(capability.status, "UNKNOWN")
        self.assertEqual(next(check for check in report.checks if check.id == "FACILITY_GEOMETRY").status, "PASS")
        self.assertIn("PRODUCTION_PROGRAM_CAPABILITY_UNKNOWN", {issue.code for issue in report.issues})

    def test_default_source_form_schema_and_qa_bytes_match_pre_embedding_snapshots(self):
        from layout_configurator.program_form import program_form_config
        from layout_configurator.schema import DEFAULT_BUILDING_SCHEMA_PATH, load_building_schema, load_json_schema

        base = _tiny_building()
        self.assertIsNone(base.production_program)
        self.assertNotIn("production_program", base.to_dict())
        self.assertEqual(_digest(base.to_dict()), "a788cd159510184e6ed0993e6b7c6fa6570b98f9f8c6f81114adea2f54e38bf7")
        self.assertEqual(_digest(program_form_config()), "e73755489bd306e3b4cd79c728ee356dff5ba68dda654e0a3c287b43ee46a887")
        report = validate_building(base, LayoutResult(1, {"molding": Rect(0, 0, 4000, 4000)}),
                                   None, None, None, profile=default_facility_profile())
        self.assertEqual(_digest(report.to_dict()), "ead7bdb748138b8c7978309775581ee49c4aa586da1d30d50bfc8a1ac2ef7516")
        self.assertEqual(load_building_schema(), load_json_schema(DEFAULT_BUILDING_SCHEMA_PATH))
        self.assertNotIn("production_program", load_building_schema()["properties"])

    def test_opt_in_schema_uses_closed_packaged_resources_without_network_or_cwd(self):
        from jsonschema import Draft202012Validator
        from layout_configurator.resources import resource_path
        from layout_configurator.schema import load_building_schema, validate_building as validate_building_schema

        original_cwd = Path.cwd()
        with tempfile.TemporaryDirectory() as directory:
            try:
                os.chdir(directory)
                with patch("urllib.request.urlopen", side_effect=AssertionError("Schema attempted a network request")):
                    schema = load_building_schema(include_production_program=True)
                    Draft202012Validator.check_schema(schema)
                    self.assertEqual(list(Draft202012Validator(schema).iter_errors(self.raw)), [])
                    self.assertEqual(schema["properties"]["production_program"], json.loads(
                        resource_path("schemas/production_program.schema.json").read_text(encoding="utf-8")))
                    embedded = self.admit()
                    self.assertEqual(validate_building_schema(embedded, include_production_program=True), ())
                    self.assertTrue(validate_building_schema(embedded))
                    invalid = copy.deepcopy(self.raw)
                    invalid["production_program"]["operations"][0]["geometry"] = {}
                    self.assertTrue(list(Draft202012Validator(schema).iter_errors(invalid)))
            finally:
                os.chdir(original_cwd)

    def test_default_api_form_orthogonal_predicates_and_source_frames_reject_program(self):
        from layout_configurator.api import _validated_request
        from layout_configurator.equipment import EquipmentLayoutResult
        from layout_configurator.orthogonal_bundle import _source_frames
        from layout_configurator.orthogonal_generation import generate_orthogonal_facility_variants
        from layout_configurator.orthogonal_validation import validate_orthogonal_equipment
        from layout_configurator.program_form import validate_program_form

        with self.assertRaises(ValueError):
            _validated_request({"program": self.raw})
        with self.assertRaises(ValueError):
            validate_program_form({"program": self.raw})
        embedded = self.admit()
        previous = LayoutResult(1, {})
        with patch("layout_configurator.orthogonal_generation.solve_orthogonal_candidates") as solve:
            with self.assertRaises(ValueError):
                generate_orthogonal_facility_variants(embedded, profile=default_facility_profile(), time_limit_seconds=1)
        solve.assert_not_called()
        with self.assertRaises(ValueError):
            validate_orthogonal_equipment(embedded, previous, EquipmentLayoutResult(()))
        source_frames = {"building": embedded.to_dict(), "layout": previous.to_dict(),
                         "profile": default_facility_profile().to_dict(), "equipment": None, "routing": None}
        with self.assertRaises(ValueError):
            _source_frames(source_frames)

    def test_ordinary_actual_generation_has_no_new_options_or_evidence(self):
        from layout_configurator.facility_generation import solve_layouts_auto

        base = _tiny_building()
        with patch("layout_configurator.facility_generation.solve_layouts_auto", wraps=solve_layouts_auto) as solve:
            result = solve_feasible_facility_variants(base, default_facility_profile(),
                variants=1, max_attempts=1, time_limit_seconds=1, seed=1, room_solver="monolithic")
        self.assertEqual(len(result.accepted), 1)
        self.assertTrue(result.accepted[0].facility_report.ok)
        self.assertEqual(result.accepted[0].result.placements, {"molding": Rect(0, 0, 4000, 4000)})
        self.assertEqual(set(solve.call_args.kwargs), {
            "required_adjacency_groups", "forbidden_adjacency_groups", "preferred_adjacency_groups",
            "minimum_adjacency_mm", "equipment_fit_options", "deterministic_units", "strategy", "evidence",
            "fixed_rects", "axis_aligned_room_ids", "structural_axes_x_mm", "structural_axes_y_mm"})
        self.assertNotIn("failure_evidence", result.to_dict())
        self.assertNotIn("incremental_evidence", result.to_dict())
        self.assertNotIn("room_solver_evidence", result.to_dict())

    def test_strict_opt_in_and_unknown_evidence_never_promote_approval(self):
        from layout_configurator.schema import load_building_schema

        for flag in (None, 0, 1, "true"):
            with self.subTest(flag=flag):
                with self.assertRaises(ValueError):
                    BuildingIR.from_mapping(self.base.to_dict(), include_production_program=flag)
                with self.assertRaises(ValueError):
                    normalize_building_mapping(self.base.to_dict(), include_production_program=flag)
                with self.assertRaises(ValueError):
                    load_building_schema(include_production_program=flag)
        raw = copy.deepcopy(self.raw)
        raw["production_program"]["operations"][0]["measurements"]["cycle_time"].update(
            status="UNKNOWN", value=None, source_id=None)
        embedded = self.admit(raw)
        report = validate_production_program(embedded.production_program.to_dict(), building=embedded)
        self.assertEqual(report.status, "UNKNOWN")
        self.assertEqual(report.to_dict()["engineering_acceptance"], "PENDING")
        self.assertEqual(report.to_dict()["authenticated_approval"], "UNKNOWN")

    def test_actual_yaml_cli_preserves_source_and_reports_bound_declaration_failures(self):
        import yaml

        with tempfile.TemporaryDirectory() as directory:
            source, output = Path(directory) / "source.yaml", Path(directory) / "report.json"
            source.write_text(yaml.safe_dump(self.raw, sort_keys=False), encoding="utf-8")
            before = source.read_bytes()
            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                self.assertEqual(main(["validate-production-program", str(source), "--output", str(output)]), 0)
            self.assertEqual(source.read_bytes(), before)
            self.assertEqual(json.loads(output.read_text(encoding="utf-8"))["building_sha256"],
                             self.raw["production_program"]["building_sha256"])
            raw = copy.deepcopy(self.raw)
            raw["production_program"]["requirements"][0]["kind"] = "forbidden_room_adjacency"
            source.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")
            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                self.assertEqual(main(["validate-production-program", str(source), "--output", str(output)]), 2)
            report = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual((report["schema_status"], report["binding_status"], report["evidence_status"]),
                             ("PASS", "PASS", "FAIL"))
            issue = next(issue for issue in report["issues"] if issue["code"] == "contradictory_adjacency_declaration")
            self.assertEqual((issue["requirement_id"], issue["source_id"]), ("near_assembly", "brief"))

    def test_cli_rejects_actual_raw_boolean_grid_and_invalid_utf8_with_a_report(self):
        with tempfile.TemporaryDirectory() as directory:
            source, output = Path(directory) / "source.json", Path(directory) / "report.json"
            raw = copy.deepcopy(self.raw)
            raw["layout"]["grid_mm"] = True
            for data in (json.dumps(raw).encode("utf-8"), b"\xff"):
                source.write_bytes(data)
                with self.subTest(data=data[:12]), redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                    self.assertEqual(main(["validate-production-program", str(source), "--output", str(output)]), 2)
                report = json.loads(output.read_text(encoding="utf-8"))
                self.assertEqual(report["status"], "FAIL")
                self.assertEqual(report["geometry_validation"], "NOT_EVALUATED")

    def test_embedded_budget_and_closed_source_gates_reject_without_partial_admission(self):
        from layout_configurator.production_program import ProductionProgramLimits, admit_embedded_production_program

        variants = []
        for field, value in (("operators", True), ("cycle_time", "30"), ("cycle_time", float("inf"))):
            raw = copy.deepcopy(self.raw)
            raw["production_program"]["operations"][0]["measurements"][field]["value"] = value
            variants.append(raw)
        unknown = copy.deepcopy(self.raw)
        unknown["production_program"]["operations"][0]["room_id"] = "unknown_room"
        variants.append(unknown)
        extra = copy.deepcopy(self.raw)
        extra["production_program"]["operations"][0]["placement"] = {"x": 0}
        variants.append(extra)
        for index, raw in enumerate(variants):
            with self.subTest(index=index), self.assertRaises(ValueError):
                self.admit(raw)
        for limits in (ProductionProgramLimits(max_source_bytes=10), ProductionProgramLimits(max_nodes=4),
                       ProductionProgramLimits(max_total_utf8_bytes=100), ProductionProgramLimits(max_depth=2)):
            with self.subTest(limits=limits):
                with patch.object(LayoutIR, "from_mapping", side_effect=AssertionError("Input normalized before budget rejection")):
                    with self.assertRaisesRegex(ValueError, "input_budget_exceeded"):
                        admit_embedded_production_program(self.raw, limits=limits)

    def test_real_reconstruction_embeds_as_unknown_without_geometry_or_approval(self):
        from layout_configurator.io import load_building

        root = Path(__file__).resolve().parents[1]
        base = load_building(root / "examples/pharma_cleanroom_pilot.yaml")
        program = json.loads((root / "examples/production_program_reconstruction.json").read_text(encoding="utf-8"))
        embedded = self.admit({**base.to_dict(), "production_program": program})
        report = validate_production_program(program, building=embedded)
        self.assertEqual((report.schema_status, report.binding_status, report.evidence_status),
                         ("PASS", "PASS", "UNKNOWN"))
        self.assertEqual(report.building_sha256, "aedca95d5c3288724790ade6ce2ab5b5fbda58a23cce99108c380cac9c60d459")
        self.assertEqual(report.to_dict()["geometry_validation"], "NOT_EVALUATED")
        self.assertEqual(report.to_dict()["engineering_acceptance"], "PENDING")
        self.assertEqual(embedded.to_dict()["production_program"], program)


if __name__ == "__main__":
    unittest.main()

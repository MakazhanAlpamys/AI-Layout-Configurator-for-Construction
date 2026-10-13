"""Actual command-line transfer review keeps source bytes and scoped verdicts."""

from contextlib import redirect_stderr, redirect_stdout
import copy
import importlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from layout_configurator.cli import main
from layout_configurator.flows import route_flows
from test_transfer_program import case
from test_transfer_validation import layout


def review_input(kind="door", movement="personnel"):
    building, production, transfer = case()
    if kind == "door":
        transfer["transfers"][0].update(kind="door", excerpt="paired door exercise permits personnel")
        transfer["transfers"][0]["permissions"][0]["movement_kinds"] = ["personnel"]
    if movement == "material":
        transfer["flow_movements"][0].update(movement_kind=movement, excerpt="paired material exercise")
    result = layout()
    return {"building": building.to_dict(), "production_program": production, "transfer_program": transfer,
            "layout": result.to_dict(), "flow_routes": route_flows(building, result).to_dict()}


class TransferReviewTests(unittest.TestCase):
    def invoke(self, raw, *, include_windows=False):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "review.json"
            target = Path(directory) / "report.json"
            source.write_text(json.dumps(raw), encoding="utf-8")
            before = source.read_bytes()
            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                try:
                    code = main(["check-transfers", str(source), "--output", str(target),
                                 *(["--include-windows"] if include_windows else [])])
                except SystemExit as exc:
                    self.fail("Actual transfer-review command is missing: " + str(exc))
            self.assertEqual(source.read_bytes(), before)
            self.assertTrue(target.is_file(), "Command did not persist scoped transfer review")
            return code, json.loads(target.read_text())

    def test_scoped_door_pass_is_persisted_without_solver_or_full_facility_claim(self):
        with patch("layout_configurator.cli.solve_feasible_facility_variants",
                   side_effect=AssertionError("Source review must not generate a facility")):
            code, report = self.invoke(review_input())
        self.assertEqual((code, report["status"], report["geometry_status"], report["permission_status"]),
                         (0, "PASS", "PASS", "PASS"))
        self.assertEqual(report["full_facility_validation"]["status"], "UNKNOWN")
        self.assertEqual(report["engineering_acceptance"], "PENDING")
        self.assertEqual(report["crossings"][0]["point"], [3000., 1500.])
        self.assertIn("file_sha256", report["review_source"])

    def test_actual_personnel_denial_and_allowed_material_unknown_keep_distinct_exits(self):
        code, report = self.invoke(review_input("pass_box"))
        self.assertEqual((code, report["permission_status"]), (2, "FAIL"))
        self.assertIn("transfer_movement_forbidden", {item["code"] for item in report["issues"]})
        code, report = self.invoke(review_input("pass_box", "material"))
        self.assertEqual((code, report["permission_status"], report["mechanism_status"]), (4, "PASS", "UNKNOWN"))

    def test_saved_room_path_and_missing_routes_never_authorize_passage(self):
        raw = review_input("pass_box")
        raw["flow_routes"]["routes"][0]["room_path"] = ["source"]
        code, report = self.invoke(raw)
        self.assertEqual((code, report["geometry_status"], report["permission_status"]), (2, "FAIL", "FAIL"))
        raw = review_input()
        del raw["flow_routes"]
        code, report = self.invoke(raw)
        self.assertEqual((code, report["status"]), (2, "FAIL"))

    def test_raw_booleans_unknown_fields_and_stale_bindings_fail_before_coercion(self):
        for change in ("coordinate", "variant", "unknown", "route", "source"):
            raw = review_input()
            if change == "coordinate":
                raw["flow_routes"]["routes"][0]["points"][0]["x"] = True
            elif change == "variant":
                raw["layout"]["variant"] = True
            elif change == "unknown":
                raw["flow_routes"]["saved_ok"] = True
            elif change == "route":
                raw["flow_routes"]["routes"][0]["minimum_clear_width_mm"] = "800"
            else:
                raw["transfer_program"]["building_sha256"] = "0"*64
            with self.subTest(change=change):
                code, report = self.invoke(raw)
                self.assertEqual((code, report["status"]), (2, "FAIL"))

    def test_duplicate_keys_and_output_alias_cannot_mutate_review_source(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "review.json"
            target = Path(directory) / "report.json"
            source.write_text('{"building":{},"building":{}}')
            before = source.read_bytes()
            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                try:
                    code = main(["check-transfers", str(source), "--output", str(target)])
                except SystemExit as exc:
                    self.fail("Bounded transfer-review intake is missing: " + str(exc))
            self.assertEqual(code, 2)
            self.assertEqual(json.loads(target.read_text())["status"], "FAIL")
            self.assertEqual(source.read_bytes(), before)
            source.write_text(json.dumps(review_input()))
            before = source.read_bytes()
            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                self.assertEqual(main(["check-transfers", str(source), "--output", str(source)]), 2)
            self.assertEqual(source.read_bytes(), before)

    def test_file_and_route_budgets_precede_parsing_and_geometry(self):
        try:
            api = importlib.import_module("layout_configurator.transfer_review")
        except ModuleNotFoundError as exc:
            self.fail("Bounded actual transfer-review loader is missing: " + str(exc))
        from layout_configurator.transfer_program import TransferProgramLimits
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "review.json"
            source.write_text(json.dumps(review_input()))
            with patch.object(api.json, "loads", side_effect=AssertionError("Parsed after byte overflow")), self.assertRaises(ValueError):
                api.load_transfer_review(source, limits=TransferProgramLimits(max_input_bytes=16))
            with patch("layout_configurator.transfer_validation.validate_orthogonal_flow_routes",
                       side_effect=AssertionError("Geometry ran after route count overflow")), \
                    patch.object(api, "parse_layout_result", side_effect=AssertionError("Constructed geometry after point overflow")):
                report = api.load_transfer_review(source, limits=TransferProgramLimits(max_total_points=2))
            self.assertEqual(report["status"], "UNKNOWN")
            self.assertFalse(report["ok"])

    def test_result_room_budget_precedes_any_geometry_construction(self):
        api = importlib.import_module("layout_configurator.transfer_review")
        from layout_configurator.transfer_program import TransferProgramLimits
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "review.json"
            raw = review_input()
            raw["layout"]["rooms"]["unexpected"] = copy.deepcopy(raw["layout"]["rooms"]["source"])
            source.write_text(json.dumps(raw))
            with patch.object(api, "parse_layout_result", side_effect=AssertionError("Constructed geometry after room overflow")), self.assertRaises(ValueError):
                api.load_transfer_review(source, limits=TransferProgramLimits(max_rooms=2))

    def test_explicit_embedded_source_review_preserves_whole_hash_and_producer_guard(self):
        from layout_configurator.building import BuildingIR
        from layout_configurator.facility import default_facility_profile
        from layout_configurator.facility_generation import InfeasibleFacilityGeneration, solve_feasible_facility_variants
        from test_transfer_program import _sha
        raw = review_input()
        raw["building"]["production_program"] = copy.deepcopy(raw["production_program"])
        embedded = BuildingIR.from_mapping(raw["building"], include_production_program=True)
        raw["transfer_program"]["building_sha256"] = _sha(embedded.to_dict())
        code, report = self.invoke(raw)
        self.assertEqual((code, report["status"], report["geometry_status"]), (4, "UNKNOWN", "UNKNOWN"))
        self.assertEqual(report["source"]["building_sha256"], _sha(embedded.to_dict()))
        with self.assertRaises(InfeasibleFacilityGeneration) as rejected:
            solve_feasible_facility_variants(embedded, default_facility_profile(), variants=1,
                time_limit_seconds=.1, seed=1)
        self.assertEqual(rejected.exception.generation.attempted_candidates, 0)


if __name__ == "__main__":
    unittest.main()

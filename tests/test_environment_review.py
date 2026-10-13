"""Explicit environment CLI reviews actual coordinates without generation."""

from contextlib import redirect_stderr, redirect_stdout
import copy
import hashlib
import importlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from layout_configurator.cli import main
from layout_configurator.flows import route_flows
from test_environment_program import case


def review_input(*, cycle=False):
    building, layout, production, transfer, environment = case(cycle=cycle)
    return {"building": building.to_dict(), "layout": layout.to_dict(), "production_program": production,
            "transfer_program": transfer, "environment_program": environment,
            "flow_routes": route_flows(building, layout).to_dict()}


class EnvironmentReviewTests(unittest.TestCase):
    def invoke(self, raw):
        with tempfile.TemporaryDirectory() as directory:
            source, output = Path(directory) / "source.json", Path(directory) / "report.json"
            source.write_text(json.dumps(raw), encoding="utf-8")
            before = source.read_bytes()
            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                try:
                    code = main(["check-environment", str(source), "--output", str(output)])
                except SystemExit as exc:
                    self.fail("Actual environment review command is missing: " + str(exc))
            self.assertEqual(source.read_bytes(), before)
            self.assertTrue(output.is_file())
            report = json.loads(output.read_text())
            if "review_source" in report:
                self.assertEqual(report["review_source"]["file_sha256"], hashlib.sha256(before).hexdigest())
            return code, report

    def test_consistent_actual_network_is_scoped_unknown_without_generating(self):
        with patch("layout_configurator.cli.solve_feasible_facility_variants", side_effect=AssertionError("Review generated geometry")):
            code, report = self.invoke(review_input())
        self.assertEqual((code, report["status"], report["geometry_status"]), (4, "UNKNOWN", "PASS"))
        self.assertEqual(report["pressure_resolved_status"], "PASS")
        self.assertEqual(report["pressure_measured_status"], "UNKNOWN")
        self.assertEqual(report["engineering_acceptance"], "PENDING")

    def test_actual_pressure_cycle_is_fail_with_source_bound_edge_evidence(self):
        code, report = self.invoke(review_input(cycle=True))
        self.assertEqual((code, report["pressure_declared_status"], report["pressure_resolved_status"]), (2, "FAIL", "FAIL"))
        self.assertTrue(any(row["code"] == "contradictory_resolved_pressure_cycle" and row.get("edge_id")
                            for row in report["issues"]))

    def test_strict_boundary_does_not_invent_routes_or_coerce_pressure_numbers(self):
        for edit in ("routes", "unknown", "boolean"):
            raw = review_input()
            if edit == "routes": del raw["flow_routes"]
            elif edit == "unknown": raw["inferred_measurements"] = {}
            else: raw["environment_program"]["pressure_edges"][0]["minimum_delta_pa"] = True
            with self.subTest(edit=edit):
                code, report = self.invoke(raw)
                self.assertEqual((code, report["status"]), (2, "FAIL"))

    def test_hardlinked_report_cannot_modify_environment_source(self):
        with tempfile.TemporaryDirectory() as directory:
            source, output = Path(directory) / "source.json", Path(directory) / "alias.json"
            source.write_text(json.dumps(review_input()))
            before = source.read_bytes()
            output.hardlink_to(source)
            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                try:
                    code = main(["check-environment", str(source), "--output", str(output)])
                except SystemExit as exc:
                    self.fail("Environment review source guard is missing: " + str(exc))
            self.assertEqual(code, 2)
            self.assertEqual(source.read_bytes(), before)
            self.assertEqual(output.read_bytes(), before)

    def test_combined_environment_work_is_admitted_before_any_result_geometry(self):
        api = importlib.import_module("layout_configurator.environment_review")
        from layout_configurator.environment_program import EnvironmentProgramLimits
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source.json"
            source.write_text(json.dumps(review_input()))
            # Transfer geometry costs420; environment adds229. Checking only
            # transfer work would wrongly construct geometry below this bound.
            with patch("layout_configurator.transfer_review.parse_layout_result",
                       side_effect=AssertionError("Constructed geometry after combined work overflow")):
                report = api.load_environment_review(source, limits=EnvironmentProgramLimits(max_geometry_checks=600))
            self.assertEqual(report["status"], "UNKNOWN")
            self.assertEqual(report["scope"], "supplied_environment_review_input")
            self.assertEqual(report["issues"][0]["code"], "input_budget_exceeded")

    def test_environment_schema_rejects_pressure_boolean_before_result_geometry(self):
        api = importlib.import_module("layout_configurator.environment_review")
        raw = review_input()
        raw["environment_program"]["pressure_edges"][0]["minimum_delta_pa"] = True
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source.json"
            source.write_text(json.dumps(raw))
            with patch("layout_configurator.transfer_review.parse_layout_result",
                       side_effect=AssertionError("Constructed geometry before invalid environment schema")), self.assertRaises(ValueError):
                api.load_environment_review(source)

    def test_embedded_program_stays_present_and_unsupported_geometry_stays_unknown(self):
        from layout_configurator.building import BuildingIR
        from test_transfer_program import _sha
        raw = review_input()
        raw["building"]["production_program"] = copy.deepcopy(raw["production_program"])
        building = BuildingIR.from_mapping(raw["building"], include_production_program=True)
        raw["transfer_program"]["building_sha256"] = _sha(building.to_dict())
        raw["environment_program"].update(building_sha256=_sha(building.to_dict()),
                                          transfer_program_sha256=_sha(raw["transfer_program"]))
        with patch("layout_configurator.cli.solve_feasible_facility_variants", side_effect=AssertionError("Embedded review started generation")):
            code, report = self.invoke(raw)
        self.assertEqual((code, report["status"], report["geometry_status"]), (4, "UNKNOWN", "UNKNOWN"))
        self.assertEqual(report["source"]["building_sha256"], _sha(building.to_dict()))
        self.assertEqual(report["engineering_acceptance"], "PENDING")


if __name__ == "__main__":
    unittest.main()

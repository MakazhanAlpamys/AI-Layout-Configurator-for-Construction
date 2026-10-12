import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from test_solver_statistics import program


class SolverBudgetCalibrationTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.source = self.root / "program.json"
        self.source.write_text(json.dumps(program().to_dict()), encoding="utf-8")

    def runner(self):
        path = Path(__file__).resolve().parents[1] / "scripts" / "solver_budget_calibration.py"
        self.assertTrue(path.is_file(), "CI calibration runner is missing")
        spec = importlib.util.spec_from_file_location("calibration_runner", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def run_calibration(self, *extra):
        code = self.runner().main([str(self.source), "--output", str(self.root / "report"),
                                   "--budgets", ".01", "--seeds", "7", "--repetitions", "2", *extra])
        report = json.loads((self.root / "report" / "calibration.json").read_text(encoding="utf-8"))
        return code, report

    def test_real_repeated_solves_measure_consumed_work_and_preserve_geometry(self):
        code, report = self.run_calibration()
        self.assertEqual(code, 0, report)
        self.assertEqual(report["status"], "CALIBRATED")
        self.assertEqual(report["decision"], "retain_wall_clock_facility_scale_matrix")
        self.assertEqual(report["backend"], "monolithic_room_model")
        self.assertEqual(len(report["samples"]), 2)
        first, second = report["samples"]
        self.assertEqual(first["result_sha256"], second["result_sha256"])
        self.assertTrue(first["geometry_ok"])
        self.assertGreater(first["wall_time_seconds"], 0)
        self.assertLess(first["deterministic_time"], first["budget_deterministic_units"])
        self.assertGreaterEqual(first["units_per_solver_second"], 0)
        self.assertTrue((self.root / "report" / "calibration.csv").is_file())
        self.assertEqual(len(report["runtime_fingerprint"]), 64)
        self.assertEqual(len(report["sources"][str(self.source)]["sha256"]), 64)

    def test_source_infeasibility_is_measurement_without_facility_acceptance(self):
        self.source.write_text(json.dumps(program(True).to_dict()), encoding="utf-8")
        code, report = self.run_calibration()
        self.assertEqual(code, 0, report)
        self.assertEqual(report["samples"][0]["model_status"], "INFEASIBLE")
        self.assertIsNone(report["samples"][0]["result_sha256"])
        self.assertIsNone(report["samples"][0]["geometry_ok"])
        self.assertIn("not facility acceptance", report["scope"])

    def test_invalid_budget_and_duplicate_seeds_reject_before_search(self):
        for extra in (("--budgets", "nan"), ("--seeds", "7", "7"), ("--repetitions", "0")):
            with self.subTest(extra=extra):
                code, report = self.run_calibration(*extra)
                self.assertEqual(code, 2)
                self.assertEqual(report["status"], "INPUT_REJECTED")
                self.assertEqual(report["samples"], [])

    def test_changed_actual_source_cannot_publish_a_calibrated_report(self):
        runner = self.runner()
        original = runner.solver.solve_layouts

        def change_after_real_solve(*args, **kwargs):
            result = original(*args, **kwargs)
            self.source.write_text(json.dumps(program(True).to_dict()), encoding="utf-8")
            return result

        with patch.object(runner.solver, "solve_layouts", change_after_real_solve):
            code = runner.main([str(self.source), "--output", str(self.root / "report"),
                                "--budgets", ".01", "--seeds", "7", "--repetitions", "2"])
        report = json.loads((self.root / "report" / "calibration.json").read_text(encoding="utf-8"))
        self.assertEqual(code, 4)
        self.assertEqual(report["status"], "MEASUREMENT_FAILED")
        self.assertIn("changed", report["message"])
        self.assertEqual(len(report["samples"]), 1)


if __name__ == "__main__":
    unittest.main()

import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


class FacilityFloorsAcceptanceTests(unittest.TestCase):
    def runner(self):
        path = Path(__file__).resolve().parents[1] / "scripts" / "facility_floors_acceptance.py"
        self.assertTrue(path.is_file(), "Multi-floor scale acceptance runner is missing")
        spec = importlib.util.spec_from_file_location("facility_floors_acceptance", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def fixture(self, root):
        program = {"layout": {"boundary": {"width": 3000, "height": 3000}, "grid_mm": 100,
                              "entry_room": "room", "rooms": [{"id": "room", "area": 9,
                              "min_area": 9, "max_area": 9, "min_width": 3000, "min_depth": 3000}]}}
        (root / "floor.json").write_text(json.dumps(program))
        payload = {"facility_multi_floor": {"project_name": "Runner input", "floor_height_mm": 4500,
                    "structural_grid": {"axes_x_mm": [0, 3000], "axes_y_mm": [0, 3000]},
                    "floors": [{"level": 0, "program": "floor.json"}, {"level": 1, "program": "floor.json"}]}}
        spec = root / "stack.json"
        spec.write_text(json.dumps(payload))
        return spec, program

    def test_insufficient_rooms_cannot_be_reported_as_the_40_room_gate(self):
        runner = self.runner()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            spec, _ = self.fixture(root)
            output = root / "evidence"
            code = runner.main([str(spec), "--output", str(output), "--seeds", "1"])
            self.assertEqual(code, 2)
            report = json.loads((output / "acceptance-report.json").read_text())
            self.assertFalse(report["ok"])
            self.assertEqual(report["status"], "INPUT_REJECTED")
            self.assertIn("40", report["message"])
            self.assertFalse((output / "seed_1").exists())

    def test_a_failed_generation_retains_evidence_without_passing_unified_exchange(self):
        runner = self.runner()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            spec, program = self.fixture(root)
            program["layout"]["rooms"].append({**program["layout"]["rooms"][0], "id": "extra"})
            (root / "floor.json").write_text(json.dumps(program))
            output = root / "failure"
            code = runner.main([str(spec), "--output", str(output), "--seeds", "1", "--minimum-rooms", "4",
                                "--max-attempts", "1", "--time-limit", "1"])
            self.assertEqual(code, 4)
            report = json.loads((output / "acceptance-report.json").read_text())
            self.assertFalse(report["ok"])
            self.assertEqual(report["seeds"][0]["status"], "GENERATION_FAILED")
            self.assertEqual(report["room_count"], 4)
            self.assertTrue((output / "seed_1" / "multi-floor-failure.json").is_file())
            self.assertNotIn("qa", report["seeds"][0])

    def test_a_real_stack_passes_and_check_only_rejects_tampered_ifc_despite_pass_flags(self):
        runner = self.runner()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            spec, _ = self.fixture(root)
            output = root / "accepted"
            args = [str(spec), "--output", str(output), "--seeds", "1", "--minimum-rooms", "2",
                    "--max-attempts", "1", "--time-limit", "2"]
            self.assertEqual(runner.main(args), 0)
            report = json.loads((output / "acceptance-report.json").read_text())
            self.assertTrue(report["ok"])
            self.assertEqual(report["room_count"], 2)
            self.assertTrue(report["seeds"][0]["qa"]["ok"])
            self.assertEqual(len(report["source_sha256"]), 2)
            self.assertRegex(report["runtime_fingerprint"], r"^[0-9a-f]{64}$")
            self.assertEqual(set(report["profile_sha256"]), {"0", "1"})
            self.assertRegex(report["profile_sha256"]["0"], r"^[0-9a-f]{64}$")
            ifc_path = output / "seed_1" / "facility_stack_01.ifc"
            ifc_path.write_text("invalid IFC exchange data")
            self.assertEqual(runner.main([*args, "--check-only"]), 4)
            report = json.loads((output / "acceptance-report.json").read_text())
            self.assertFalse(report["ok"])
            self.assertEqual(report["seeds"][0]["status"], "CHECK_FAILED")


if __name__ == "__main__":
    unittest.main()

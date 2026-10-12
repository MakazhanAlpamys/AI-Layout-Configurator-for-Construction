import contextlib
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest

import ifcopenshell

from layout_configurator.cli import main
from layout_configurator.qa import validate_building_bundle


class UnifiedFacilityCliTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        program = {"layout": {
            "project_name": "Repeated CLI floor", "boundary": {"width": 9000, "height": 6000},
            "grid_mm": 100, "entry_room": "core",
            "rooms": [{"id": room, "area": 9, "min_area": 9, "max_area": 9,
                       "min_width": 3000, "min_depth": 3000,
                       "required_adjacency": ["core"] if room != "core" else []}
                      for room in ("core", "process", "support")],
        }}
        for filename in ("ground.json", "upper.json"):
            (self.root / filename).write_text(json.dumps(program), encoding="utf-8")
        self.payload = {"facility_multi_floor": {
            "project_name": "CLI unified facility", "floor_height_mm": 4500,
            "structural_grid": {"axes_x_mm": [0, 3000, 6000, 9000], "axes_y_mm": [0, 3000, 6000]},
            "floors": [{"level": 0, "elevation_mm": 0, "program": "ground.json"},
                       {"level": 1, "elevation_mm": 4500, "program": "upper.json"}],
            "vertical_cores": [{"id": "staff_core", "kind": "stair", "rooms": {"0": "core", "1": "core"}}],
        }}
        self.spec = self.root / "stack.json"
        self.output = self.root / "bundle"

    def run_stack(self, *options):
        self.spec.write_text(json.dumps(self.payload), encoding="utf-8")
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()) as error:
            code = main(["generate-facility-floors", str(self.spec), "--output", str(self.output),
                         "--variants", "1", "--max-attempts", "1", "--time-limit", "3",
                         "--seed", "1", "--room-solver", "monolithic", *options])
        return code, error.getvalue()

    def test_shared_grid_automatically_exports_one_ifc_and_preserves_floor_bundle_qa(self):
        code, error = self.run_stack()
        self.assertEqual(code, 0, error)
        report = json.loads((self.output / "multi_floor_report.json").read_text())
        entry = report["stacks"][0].get("unified_ifc")
        self.assertIsNotNone(entry, "A shared-grid stack must include its unified IFC")
        path = self.output / entry["path"]
        self.assertEqual(entry["sha256"], hashlib.sha256(path.read_bytes()).hexdigest())
        self.assertEqual((entry["summary"]["storeys"], entry["summary"]["spaces"]), (2, 6))
        model = ifcopenshell.open(path)
        self.assertEqual(len(model.by_type("IfcProject")), 1)
        self.assertEqual(len(model.by_type("IfcBuilding")), 1)
        self.assertEqual(len(model.by_type("IfcGrid")), 1)
        self.assertEqual(sorted(storey.Elevation for storey in model.by_type("IfcBuildingStorey")), [0, 4500])
        for level in (0, 1):
            self.assertTrue(validate_building_bundle(self.output / f"floor_{level:02d}" / "building_01.json").ok)

    def test_explicit_unified_request_without_a_declared_grid_fails_before_generation(self):
        self.payload["facility_multi_floor"].pop("structural_grid")
        code, error = self.run_stack("--unified-ifc")
        self.assertEqual(code, 2, error)
        self.assertIn("shared structural grid", error)
        failure = json.loads((self.output / "multi-floor-failure.json").read_text())
        self.assertFalse(failure["ok"])
        self.assertFalse((self.output / "floor_00").exists())


if __name__ == "__main__":
    unittest.main()

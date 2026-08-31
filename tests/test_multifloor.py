import unittest
from pathlib import Path

import yaml
import ifcopenshell

from layout_configurator.compliance import validate_ids
from layout_configurator.ifc import export_multifloor_ifc
from layout_configurator.multifloor import MultiFloorSpec, solve_multifloor


class MultiFloorTests(unittest.TestCase):
    def test_solver_locks_vertical_core_and_writes_coordinated_result(self):
        raw = yaml.safe_load(Path("examples/multifloor.yaml").read_text(encoding="utf-8"))
        spec = MultiFloorSpec.from_mapping(raw)
        result = solve_multifloor(spec, time_limit_seconds=10)

        self.assertTrue(result.report.ok, result.report.to_dict())
        self.assertEqual(len(result.floors), 2)
        first = result.floors[0].placements["stair_0"]
        second = result.floors[1].placements["stair_1"]
        self.assertEqual(first, second)
        self.assertTrue(result.to_dict()["validation"]["ok"])

        with self.subTest("combined IFC"):
            output = Path("out") / "test_multifloor.ifc"
            try:
                summary = export_multifloor_ifc(output, spec, result)
                model = ifcopenshell.open(output)
                self.assertEqual(summary.storeys, 2)
                self.assertEqual(summary.stairs, 2)
                self.assertEqual(len(model.by_type("IfcBuildingStorey")), 2)
                self.assertEqual(len(model.by_type("IfcSpace")), 6)
                self.assertEqual(len(model.by_type("IfcStair")), 2)
                ids_report = validate_ids(output, "ids/kz_layout_exchange.ids")
                self.assertTrue(ids_report.ok, ids_report)
            finally:
                if output.exists():
                    output.unlink()

    def test_multifloor_rejects_mismatched_boundary(self):
        raw = yaml.safe_load(Path("examples/multifloor.yaml").read_text(encoding="utf-8"))
        raw["floors"][1]["layout"]["boundary"]["width"] = 11000

        with self.assertRaises(ValueError):
            MultiFloorSpec.from_mapping(raw)


if __name__ == "__main__":
    unittest.main()

import contextlib
import io
import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

import yaml

from layout_configurator.cli import main
from layout_configurator.facility_floors import (
    FacilityFloorsError,
    facility_floors_from_mapping,
    load_facility_floors,
    solve_facility_floors,
    vertical_checks,
)
from layout_configurator.models import Rect
from layout_configurator.qa import validate_building_bundle

EXAMPLES = Path("examples")


def _raw():
    return yaml.safe_load((EXAMPLES / "pharma_two_floor.yaml").read_text(encoding="utf-8"))


class FacilityFloorsSpecTests(unittest.TestCase):
    def test_cross_floor_flows_become_ordinary_legs_on_each_floor(self):
        spec = load_facility_floors(EXAMPLES / "pharma_two_floor.yaml")
        programs = spec.floor_programs()
        ground = {flow.id: flow for flow in programs[0].flows}
        upper = {flow.id: flow for flow in programs[1].flows}
        self.assertEqual(ground["qc_retained_samples@L0"].to_ids, ("goods_lift",))
        self.assertEqual(upper["qc_retained_samples@L1"].from_ids, ("goods_lift",))
        self.assertEqual(ground["staff_to_upper_floor@L0"].to_ids, ("stair_core",))
        self.assertEqual(spec.core_for(spec.cross_flows[1]).kind, "stair")

    def _expect_error(self, mutate, message):
        raw = _raw()
        mutate(raw["facility_multi_floor"])
        with self.assertRaisesRegex(FacilityFloorsError, message):
            facility_floors_from_mapping(raw, base_dir=EXAMPLES)

    def test_inconsistent_programs_are_rejected_early(self):
        self._expect_error(lambda p: p["vertical_cores"][0].update(kind="escalator"), "unknown kind")
        self._expect_error(lambda p: p["vertical_cores"][0]["rooms"].update({1: "missing_room"}), "not declared on floor 1")
        # A stair may not carry finished goods, and the goods lift may not carry people.
        self._expect_error(lambda p: p["cross_floor_flows"][0].update(via=["stair_core"]), "no core")
        self._expect_error(lambda p: p["cross_floor_flows"][1].update(via=["goods_lift"]), "no core")
        self._expect_error(lambda p: p["cross_floor_flows"][0]["to"].update(level=0), "starts and ends")
        self._expect_error(lambda p: p["cross_floor_flows"][0]["from"].update(id="nowhere"), "not a room, zone or equipment")
        self._expect_error(lambda p: p.update(floors=p["floors"][:1]), "at least two floors")


class FacilityFloorsSolveTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.spec = load_facility_floors(EXAMPLES / "pharma_two_floor.yaml")
        cls.result = solve_facility_floors(cls.spec, variants=1, time_limit_seconds=30, seed=1)

    def test_every_floor_passes_and_cores_line_up(self):
        stack = self.result.stacks[0]
        self.assertTrue(stack.ok, [check.to_dict() for check in stack.checks])
        for core in self.spec.cores:
            rects = {stack.floors[level].result.placements[room] for level, room in core.rooms.items()}
            self.assertEqual(len(rects), 1, core.id)
        self.assertEqual({check.id for check in stack.checks},
                         {"CORE_ALIGNMENT", "CORE_FLOW_TYPES", "CORE_CLEAR_WIDTH", "CROSS_FLOOR_ROUTES"})

    def test_vertical_checks_catch_a_misaligned_core(self):
        stack = self.result.stacks[0]
        upper = stack.floors[1]
        placements = dict(upper.result.placements)
        rect = placements["stair_core"]
        placements["stair_core"] = Rect(rect.x + 100, rect.y, rect.width, rect.height)
        shifted = replace(upper, result=replace(upper.result, placements=placements))
        checks = {check.id: check.status for check in vertical_checks(self.spec, {0: stack.floors[0], 1: shifted})}
        self.assertEqual(checks["CORE_ALIGNMENT"], "FAIL")

    def test_cli_writes_a_bundle_per_floor_and_the_report(self):
        with tempfile.TemporaryDirectory() as directory:
            out = Path(directory)
            with contextlib.redirect_stdout(io.StringIO()):
                code = main(["generate-facility-floors", str(EXAMPLES / "pharma_two_floor.yaml"), "--output", str(out),
                             "--variants", "1", "--time-limit", "30", "--seed", "1"])
            self.assertEqual(code, 0)
            report = json.loads((out / "multi_floor_report.json").read_text(encoding="utf-8"))
            self.assertTrue(report["ok"])
            self.assertEqual(report["stacks"][0]["floors"], {"0": "floor_00/building_01.json", "1": "floor_01/building_01.json"})
            for level in ("00", "01"):
                qa = validate_building_bundle(out / f"floor_{level}" / "building_01.json")
                self.assertTrue(qa.ok, qa.to_dict())


if __name__ == "__main__":
    unittest.main()

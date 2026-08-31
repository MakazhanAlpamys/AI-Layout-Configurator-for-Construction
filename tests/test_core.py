import unittest

from layout_configurator.io import load_spec
from layout_configurator.models import LayoutIR, LayoutResult, Rect, SpecError
from layout_configurator.solver import solve_layouts
from layout_configurator.validation import validate_layout
from layout_configurator.walls import build_wall_plan


class CoreTests(unittest.TestCase):
    def setUp(self):
        self.spec = LayoutIR.from_mapping(
            {
                "project_name": "test",
                "boundary": {"width": 12000, "height": 9000},
                "entry_room": "hall",
                "rooms": [
                    {"id": "hall", "target_area": 12, "min_width": 1800, "min_depth": 1800, "required_adjacency": ["living", "bedroom"]},
                    {"id": "living", "target_area": 24, "min_width": 3000, "min_depth": 3000, "required_adjacency": ["hall"]},
                    {"id": "bedroom", "target_area": 16, "min_width": 2800, "min_depth": 2800, "required_adjacency": ["hall"]},
                ],
            }
        )

    def test_solver_produces_valid_layout(self):
        result = solve_layouts(self.spec, variants=2, time_limit_seconds=10)
        self.assertEqual(len(result), 2)
        for layout in result:
            report = validate_layout(self.spec, layout)
            self.assertTrue(report.ok, report.issues)

    def test_duplicate_room_ids_rejected(self):
        with self.assertRaises(SpecError):
            LayoutIR.from_mapping(
                {
                    "boundary": {"width": 5000, "height": 5000},
                    "rooms": [{"id": "x", "target_area": 5}, {"id": "x", "target_area": 5}],
                }
            )

    def test_cutout_and_forbidden_adjacency(self):
        spec = LayoutIR.from_mapping(
            {
                "project_name": "L shape",
                "boundary": {
                    "width": 12000,
                    "height": 8000,
                    "cutouts": [{"x": 8000, "y": 4000, "width": 4000, "height": 4000}],
                },
                "entry_room": "hall",
                "rooms": [
                    {"id": "hall", "target_area": 12, "min_width": 1800, "min_depth": 1800, "required_adjacency": ["living", "bed"]},
                    {"id": "living", "target_area": 20, "min_width": 3000, "min_depth": 3000, "required_adjacency": ["hall"], "forbidden_adjacency": ["bed"]},
                    {"id": "bed", "target_area": 12, "min_width": 2400, "min_depth": 2500, "required_adjacency": ["hall"], "forbidden_adjacency": ["living"]},
                ],
            }
        )
        result = solve_layouts(spec, variants=1, time_limit_seconds=10)[0]
        self.assertTrue(validate_layout(spec, result).ok)

    def test_wall_plan_has_real_band_and_door_gaps(self):
        result = solve_layouts(self.spec, variants=1, time_limit_seconds=10)[0]
        wall_plan = build_wall_plan(self.spec, result)
        self.assertTrue(wall_plan.geometry.is_valid)
        self.assertEqual(len(wall_plan.openings), 2)
        self.assertGreater(wall_plan.geometry.area, 0)
        for opening in wall_plan.openings:
            self.assertLess(wall_plan.geometry.intersection(opening.centerline).length, 1e-6)

    def test_fixed_room_keeps_position_while_solver_repacks_others(self):
        spec = LayoutIR.from_mapping(
            {
                "boundary": {"width": 8000, "height": 4000},
                "entry_room": "a",
                "rooms": [
                    {"id": "a", "target_area": 8, "required_adjacency": ["b"]},
                    {"id": "b", "target_area": 8, "required_adjacency": ["a"]},
                ],
            }
        )
        fixed = Rect(0, 0, 2000, 4000)
        result = solve_layouts(spec, variants=1, time_limit_seconds=10, fixed_rects={"a": fixed})[0]
        self.assertEqual(result.placements["a"], fixed)
        self.assertTrue(validate_layout(spec, result).ok)
        self.assertGreaterEqual(result.placements["a"].shared_boundary(result.placements["b"]), spec.door_width_mm)

    def test_explicit_openings_are_part_of_layout_ir_and_round_trip(self):
        spec = LayoutIR.from_mapping(
            {
                "boundary": {"width": 6000, "height": 4000},
                "entry_room": "a",
                "rooms": [
                    {"id": "a", "target_area": 12, "required_adjacency": ["b"]},
                    {"id": "b", "target_area": 12, "required_adjacency": ["a"]},
                ],
                "doors": [{"id": "door-main", "room_a": "a", "room_b": "b", "offset_mm": 1000, "width_mm": 800}],
                "windows": [{"id": "window-main", "room_id": "a", "side": "left", "offset_mm": 2000, "width_mm": 1000}],
            }
        )
        result = LayoutResult(
            variant=1,
            placements={"a": Rect(0, 0, 3000, 4000), "b": Rect(3000, 0, 3000, 4000)},
        )

        report = validate_layout(spec, result)
        wall_plan = build_wall_plan(spec, result)
        round_tripped = LayoutIR.from_mapping(spec.to_dict())
        self.assertTrue(report.ok, report.issues)
        self.assertEqual(wall_plan.openings[0].id, "door-main")
        self.assertEqual(wall_plan.windows[0].id, "window-main")
        self.assertEqual(round_tripped.doors, spec.doors)
        self.assertEqual(round_tripped.windows, spec.windows)

    def test_external_entry_is_solved_on_requested_edge_and_round_trips(self):
        spec = load_spec("examples/kz_entry_pass.yaml")
        result = solve_layouts(spec, variants=1, time_limit_seconds=15)[0]
        report = validate_layout(spec, result)
        wall_plan = build_wall_plan(spec, result)

        self.assertTrue(report.ok, report.issues)
        self.assertEqual(result.placements["tambour"].y, 0)
        self.assertEqual(wall_plan.openings[0].id, "main_entry")
        self.assertTrue(wall_plan.openings[0].external)
        self.assertEqual(wall_plan.openings[0].center, (1000, 0))
        self.assertLess(wall_plan.geometry.intersection(wall_plan.openings[0].centerline).length, 1e-6)

        round_tripped = LayoutIR.from_mapping(spec.to_dict())
        self.assertEqual(round_tripped.external_entry, spec.external_entry)
        self.assertTrue(round_tripped.room_by_id["hall"].is_heated)


if __name__ == "__main__":
    unittest.main()

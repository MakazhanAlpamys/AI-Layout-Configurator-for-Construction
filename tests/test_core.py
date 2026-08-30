import unittest

from layout_configurator.models import LayoutIR, SpecError
from layout_configurator.solver import solve_layouts
from layout_configurator.validation import validate_layout


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


if __name__ == "__main__":
    unittest.main()

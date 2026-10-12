import unittest

from layout_configurator.models import LayoutIR
from layout_configurator.solver import InfeasibleLayout, solve_layouts


def program(impossible=False):
    return LayoutIR.from_mapping({
        "boundary": {"width": 3000 if impossible else 6000, "height": 3000},
        "entry_room": "a", "rooms": [
            {"id": room, "target_area": 9, "min_area": 9, "max_area": 9,
             "min_width": 3000, "min_depth": 3000,
             "required_adjacency": ["b"] if room == "a" else []}
            for room in ("a", "b")],
    })


class SolverStatisticsTests(unittest.TestCase):
    def test_statistics_preserve_results_and_record_actual_solver_response(self):
        baseline = solve_layouts(program(), 1, 1, 7, deterministic_units=.01)
        statistics = []
        actual = solve_layouts(program(), 1, 1, 7, deterministic_units=.01,
                               statistics=statistics)
        self.assertEqual(actual, baseline)
        self.assertEqual(len(statistics), 1)
        row = statistics[0]
        self.assertEqual(row["seed"], 7)
        self.assertEqual(row["workers"], 1)
        self.assertEqual(row["budget_deterministic_units"], .01)
        self.assertIn(row["model_status"], {"FEASIBLE", "OPTIMAL"})
        self.assertGreater(row["wall_time_seconds"], 0)
        self.assertGreaterEqual(row["deterministic_time"], 0)
        self.assertLess(row["deterministic_time"], .01)
        self.assertIsInstance(row["branches"], int)
        self.assertIsInstance(row["conflicts"], int)

    def test_infeasible_response_is_retained_without_a_false_solution(self):
        statistics = []
        with self.assertRaises(InfeasibleLayout):
            solve_layouts(program(True), 1, 1, 7, deterministic_units=.01,
                          statistics=statistics)
        self.assertEqual(len(statistics), 1)
        self.assertEqual(statistics[0]["model_status"], "INFEASIBLE")


if __name__ == "__main__":
    unittest.main()

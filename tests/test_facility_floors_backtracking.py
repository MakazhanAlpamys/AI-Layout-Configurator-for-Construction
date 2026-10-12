import json
from pathlib import Path
import tempfile
import unittest

from layout_configurator.facility_floors import (
    FacilityFloorsError, load_facility_floors, solve_facility_floors,
)


class FacilityFloorsBacktrackingTests(unittest.TestCase):
    def fixture(self, root):
        rooms = [{"id": name, "area": 9, "min_area": 9, "max_area": 9,
                  "min_width": 3000, "min_depth": 3000} for name in ("a_entry", "z_core")]
        rooms[0]["required_adjacency"] = ["z_core"]
        ground = {"layout": {"boundary": {"width": 6000, "height": 3000}, "grid_mm": 1000,
                             "entry_room": "a_entry", "rooms": rooms}}
        upper = {"layout": {**ground["layout"], "boundary": {"width": 6000, "height": 6000,
                            "cutouts": [{"x": 0, "y": 0, "width": 3000, "height": 6000}]}}}
        for name, payload in (("ground", ground), ("upper", upper)):
            (root / f"{name}.json").write_text(json.dumps(payload))
        stack = {"facility_multi_floor": {"project_name": "Bounded backtracking",
                 "structural_grid": {"axes_x_mm": [0, 3000, 6000], "axes_y_mm": [0, 3000]},
                 "floors": [{"level": 0, "program": "ground.json"},
                            {"level": 1, "program": "upper.json"}],
                 "vertical_cores": [{"id": "staff", "kind": "stair", "rooms": {0: "z_core", 1: "z_core"}}]}}
        path = root / "stack.json"
        path.write_text(json.dumps(stack))
        return load_facility_floors(path)

    def test_alternative_ground_placement_completes_stack_after_first_core_fails(self):
        with tempfile.TemporaryDirectory() as directory:
            spec = self.fixture(Path(directory))
            solved = solve_facility_floors(spec, variants=1, max_attempts=3,
                                          time_limit_seconds=2, seed=1, room_solver="monolithic")
            self.assertEqual(len(solved.stacks), 1)
            self.assertTrue(solved.stacks[0].ok)
            self.assertEqual(solved.stacks[0].floors[0].result.placements["z_core"].x, 3000)
            self.assertEqual({floor.result.variant for floor in solved.stacks[0].floors.values()}, {1})
            self.assertTrue(solved.rejected, "The failed first core placement must retain evidence")
            self.assertEqual(solved.rejected[0]["level"], 1)

    def test_exhausted_candidate_pool_retains_scoped_failure_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            spec = self.fixture(Path(directory))
            with self.assertRaises(FacilityFloorsError) as caught:
                solve_facility_floors(spec, variants=1, max_attempts=1,
                                     time_limit_seconds=2, seed=1, room_solver="monolithic")
            evidence = caught.exception.evidence
            self.assertEqual(evidence["proof_scope"], "selected_base_floor_candidates")
            self.assertFalse(evidence["proven_building_infeasible"])
            self.assertEqual(evidence["base_candidate_limit"], 1)
            self.assertTrue(evidence["rejected"])


if __name__ == "__main__":
    unittest.main()

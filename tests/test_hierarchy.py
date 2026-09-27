import unittest

from layout_configurator.building import BuildingIR
from layout_configurator.facility import load_facility_profile
from layout_configurator.facility_generation import solve_feasible_facility_variants
from layout_configurator.hierarchy import (
    HierarchyNotApplicable,
    cluster_rooms,
    solve_layouts_auto,
    solve_layouts_hierarchical,
)
from layout_configurator.io import load_building
from layout_configurator.models import LayoutIR, Rect
from layout_configurator.validation import validate_layout


def _room(room_id, area, neighbours=(), **extra):
    return {
        "id": room_id,
        "type": room_id,
        "target_area": area,
        "min_area": area * 0.9,
        "max_area": area * 1.1,
        "min_width": 2400,
        "min_depth": 2400,
        "required_adjacency": list(neighbours),
        **extra,
    }


def _corridor_program(**layout_extra):
    """Two corridors in a chain, each with leaf rooms: the shape that scales badly."""

    rooms = [
        _room("corridor_a", 36, ["corridor_b"], min_depth=9000),
        _room("corridor_b", 36, min_depth=9000),
        *[_room(f"a_{index}", 16, ["corridor_a"]) for index in range(4)],
        *[_room(f"b_{index}", 16, ["corridor_b"]) for index in range(4)],
        _room("office", 20, ["corridor_a"], needs_daylight=True),
    ]
    return LayoutIR.from_mapping(
        {
            "project_name": "hierarchy test",
            "boundary": {"width": 30000, "height": 20000},
            "entry_room": "office",
            "rooms": rooms,
            **layout_extra,
        }
    )


class ClusterTests(unittest.TestCase):
    def test_hubs_take_their_leaves_and_boundary_rooms_stay_single(self):
        clusters = cluster_rooms(_corridor_program())
        self.assertEqual(set(clusters["corridor_a"]), {"corridor_a", "a_0", "a_1", "a_2", "a_3"})
        self.assertEqual(set(clusters["corridor_b"]), {"corridor_b", "b_0", "b_1", "b_2", "b_3"})
        # A daylight room must reach the outer boundary, which only the top
        # level can see, so it is never folded into a cluster.
        self.assertEqual(clusters["office"], ("office",))

    def test_pinned_rooms_stay_single(self):
        clusters = cluster_rooms(_corridor_program(), pinned=("a_0",))
        self.assertEqual(clusters["a_0"], ("a_0",))
        self.assertNotIn("a_0", clusters["corridor_a"])


class HierarchicalSolverTests(unittest.TestCase):
    def test_layouts_pass_independent_validation_and_differ(self):
        spec = _corridor_program()
        results = solve_layouts_hierarchical(spec, 3, time_limit_seconds=10, seed=1, minimum_adjacency_mm=1300)
        self.assertEqual(len(results), 3)
        for result in results:
            report = validate_layout(spec, result)
            self.assertTrue(report.ok, [issue.message for issue in report.issues])
        self.assertEqual(len({tuple(sorted(r.placements.items())) for r in results}), 3)

    def test_cutouts_are_not_modelled_and_auto_falls_back(self):
        spec = _corridor_program(boundary={"width": 30000, "height": 20000, "cutouts": [{"x": 0, "y": 0, "width": 2000, "height": 2000}]})
        with self.assertRaises(HierarchyNotApplicable):
            solve_layouts_hierarchical(spec, 1, time_limit_seconds=5, seed=1)
        evidence = {}
        results = solve_layouts_auto(spec, 1, 20, 1, strategy="auto", evidence=evidence, minimum_adjacency_mm=1300)
        self.assertEqual(evidence["room_solver_used"], "monolithic")
        self.assertIn("cutouts", evidence["hierarchy_fallback_reason"])
        self.assertTrue(validate_layout(spec, results[0]).ok)

    def test_unsupported_options_are_refused_when_hierarchy_is_forced(self):
        spec = _corridor_program()
        with self.assertRaises(HierarchyNotApplicable):
            solve_layouts_auto(spec, 1, 5, 1, strategy="hierarchical", fixed_rects={"office": Rect(0, 0, 5000, 4000)})
        with self.assertRaises(ValueError):
            solve_layouts_auto(spec, 1, 5, 1, strategy="fastest")


class FacilityHierarchyTests(unittest.TestCase):
    def test_pilot_candidates_pass_every_gate_and_record_the_solver(self):
        building = load_building("examples/pharma_cleanroom_pilot.yaml")
        profile = load_facility_profile("rules/pharma_cleanroom_pilot.yaml")
        generation = solve_feasible_facility_variants(
            building, profile, variants=3, time_limit_seconds=20, seed=1, max_attempts=3, room_solver="hierarchical"
        )
        self.assertEqual(len(generation.accepted), 3)
        self.assertEqual(generation.rejected, ())
        evidence = generation.to_dict()
        self.assertEqual(evidence["room_solver"], "hierarchical")
        self.assertEqual(evidence["room_solver_used"], "hierarchical")
        for candidate in generation.accepted:
            self.assertTrue(candidate.facility_report.ok)

    def test_monolithic_choice_is_recorded(self):
        building = BuildingIR.from_mapping(
            {
                "layout": {
                    "boundary": {"width": 10000, "height": 6000},
                    "entry_room": "production",
                    "rooms": [{"id": "production", "target_area": 20, "min_area": 15, "max_area": 30}],
                },
            }
        )
        generation = solve_feasible_facility_variants(
            building, load_facility_profile("rules/default_facility.yaml"), variants=1,
            time_limit_seconds=5, seed=1, max_attempts=1, room_solver="monolithic",
        )
        self.assertEqual(generation.to_dict()["room_solver_used"], "monolithic")


if __name__ == "__main__":
    unittest.main()


class RotatableRoomTests(unittest.TestCase):
    def _spec(self, rotatable):
        return LayoutIR.from_mapping(
            {
                "project_name": "rotatable",
                "boundary": {"width": 5000, "height": 15000},
                "entry_room": "corridor",
                "rooms": [
                    {
                        "id": "corridor",
                        "type": "corridor",
                        "target_area": 36,
                        "min_area": 30,
                        "max_area": 45,
                        # "At least 12 m long": only reachable along y here.
                        "min_width": 12000,
                        "min_depth": 2400,
                        "rotatable": rotatable,
                    }
                ],
            }
        )

    def test_minimums_may_be_met_in_either_orientation_only_when_declared(self):
        spec = self._spec(True)
        room = spec.rooms[0]
        self.assertTrue(room.fits(2400, 12000))
        self.assertTrue(room.fits(12000, 2400))
        self.assertFalse(room.fits(2400, 11000))
        self.assertFalse(self._spec(False).rooms[0].fits(2400, 12000))

    def test_solver_turns_a_rotatable_room_and_the_validator_accepts_it(self):
        from layout_configurator.solver import InfeasibleLayout, solve_layouts

        spec = self._spec(True)
        result = solve_layouts(spec, 1, 10, 1)[0]
        rect = result.placements["corridor"]
        self.assertGreaterEqual(rect.height, 12000)
        self.assertTrue(validate_layout(spec, result).ok)
        with self.assertRaises(InfeasibleLayout):
            solve_layouts(self._spec(False), 1, 10, 1)

    def test_rotatable_survives_the_canonical_round_trip(self):
        spec = self._spec(True)
        self.assertTrue(spec.to_dict()["rooms"][0]["rotatable"])
        self.assertTrue(LayoutIR.from_mapping(spec.to_dict()).rooms[0].rotatable)
        self.assertNotIn("rotatable", self._spec(False).to_dict()["rooms"][0])


class WorkerPolicyTests(unittest.TestCase):
    def test_parallel_search_only_with_a_wall_clock_budget(self):
        from ortools.sat.python import cp_model

        from layout_configurator.search import configure_solver

        wall = cp_model.CpSolver()
        configure_solver(wall, seed=1, time_limit_seconds=5, workers=4)
        self.assertEqual(wall.parameters.num_search_workers, 4)
        repeatable = cp_model.CpSolver()
        configure_solver(repeatable, seed=1, time_limit_seconds=5, deterministic_units=3, workers=4)
        self.assertEqual(repeatable.parameters.num_search_workers, 1)

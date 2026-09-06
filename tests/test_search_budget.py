import unittest

from layout_configurator.equipment import place_equipment
from layout_configurator.io import load_building, load_spec
from layout_configurator.solver import solve_layouts

PROGRAM = "examples/pharma_cleanroom_pilot.yaml"


class DeterministicBudgetTests(unittest.TestCase):
    """A repeatable run needs a work budget, not a stopwatch.

    ``--time-limit`` is wall clock, so the search stops wherever the machine
    happened to be when the clock ran out and two runs of the same seed can
    return different layouts. The deterministic budget spends a machine
    independent amount of work instead.
    """

    def test_wall_clock_runs_are_not_guaranteed_to_repeat(self):
        # Documents the limitation rather than asserting a difference: a small
        # model can finish and prove optimality well inside the budget, in which
        # case the wall clock never truncates the search.
        spec = load_spec("examples/basic.yaml")
        first = solve_layouts(spec, variants=1, time_limit_seconds=10, seed=1)[0]
        self.assertIsNotNone(first.objective_value)

    def test_room_solver_repeats_under_a_deterministic_budget(self):
        spec = load_spec("examples/basic.yaml")

        runs = [
            solve_layouts(spec, variants=2, time_limit_seconds=10, seed=1, deterministic_units=6.0)
            for _ in range(2)
        ]

        self.assertEqual(
            [result.to_dict() for result in runs[0]],
            [result.to_dict() for result in runs[1]],
        )

    def test_equipment_packing_repeats_under_a_deterministic_budget(self):
        building = load_building(PROGRAM)
        layout = solve_layouts(
            building.layout, variants=1, time_limit_seconds=60, seed=1, deterministic_units=40.0
        )[0]

        runs = [
            place_equipment(building, layout, time_limit_seconds=30, seed=42, deterministic_units=12.0)
            for _ in range(2)
        ]

        self.assertEqual(runs[0].placements, runs[1].placements)

    def test_a_deterministic_budget_replaces_the_wall_clock_cap(self):
        from ortools.sat.python import cp_model

        from layout_configurator.search import configure_solver

        wall = cp_model.CpSolver()
        configure_solver(wall, seed=1, time_limit_seconds=7.5)
        self.assertAlmostEqual(wall.parameters.max_time_in_seconds, 7.5)
        self.assertEqual(wall.parameters.num_search_workers, 1)

        deterministic = cp_model.CpSolver()
        configure_solver(deterministic, seed=1, time_limit_seconds=7.5, deterministic_units=3.0)
        self.assertAlmostEqual(deterministic.parameters.max_deterministic_time, 3.0)
        self.assertEqual(deterministic.parameters.num_search_workers, 1)
        # A wall-clock cap left in place would still be able to truncate the
        # search at a machine-dependent point.
        self.assertGreater(deterministic.parameters.max_time_in_seconds, 1e9)


if __name__ == "__main__":
    unittest.main()


class ModelOrderStabilityTests(unittest.TestCase):
    """Model construction must not depend on Python's per-process hash seed.

    Adjacency pairs used to be collected in a set, so the constraints reached
    CP-SAT in a different order in every process. A deterministic budget cannot
    repeat a run whose model is not itself identical.
    """

    def test_relation_pairs_are_emitted_in_a_stable_order(self):
        building = load_building(PROGRAM)
        pairs = building.layout.relation_pairs("required_adjacency")

        self.assertEqual(list(pairs), sorted(pairs))
        self.assertEqual(len(set(pairs)), len(list(pairs)), "pairs must stay deduplicated")


class BudgetEvidenceTests(unittest.TestCase):
    """A manifest has to say whether the run it describes can be repeated."""

    def test_generation_evidence_records_the_budget_mode(self):
        from layout_configurator.facility_generation import FacilityGenerationResult

        wall = FacilityGenerationResult(
            requested_variants=1,
            max_attempts=3,
            attempted_candidates=1,
            accepted=(),
            rejected=(),
            seed=1,
            time_limit_seconds=30.0,
            equipment_retries=2,
        ).to_dict()
        self.assertIsNone(wall["deterministic_units"])
        self.assertFalse(wall["repeatable"])

        repeatable = FacilityGenerationResult(
            requested_variants=1,
            max_attempts=3,
            attempted_candidates=1,
            accepted=(),
            rejected=(),
            seed=1,
            time_limit_seconds=30.0,
            equipment_retries=2,
            deterministic_units=10.0,
        ).to_dict()
        self.assertEqual(repeatable["deterministic_units"], 10.0)
        self.assertTrue(repeatable["repeatable"])

"""Final zone search controls preserve independent geometry and local budgets."""

import unittest
from unittest.mock import patch

from layout_configurator.search import configure_solver
from layout_configurator.zone_hierarchy import ZoneHierarchyFailure, solve_zone_layouts, validate_zone_layout
from test_zone_warm_start import program, source_rects, zone_rects


class ZoneGlobalSearchTests(unittest.TestCase):
    def solve(self, evidence, **options):
        return solve_zone_layouts(program(), 1, 1, 1, recursive_zones=True,
            zone_size_options_mm={"parent": [(8000, 6000)], "a_zone": [(4000, 6000)], "b_zone": [(4000, 6000)]},
            hint_rects=source_rects(), cache_cluster_shapes=True, evidence=evidence, **options)[0]

    def test_real_parallel_final_search_keeps_local_models_and_physical_gates(self):
        evidence, configured = {}, []

        def observe(solver, **settings):
            configure_solver(solver, **settings)
            configured.append((solver.parameters.max_time_in_seconds, solver.parameters.num_search_workers))

        with patch("layout_configurator.zone_hierarchy.configure_solver", side_effect=observe):
            result = self.solve(evidence, global_time_limit_seconds=.75, global_workers=2)
        self.assertTrue(validate_zone_layout(program(), result, zone_rects(evidence), recursive_zones=True).ok)
        self.assertEqual(configured[-1], (.75, 2))
        self.assertTrue(all(workers == 1 for _, workers in configured[:-1]))
        self.assertEqual(evidence["global_search_settings"]["time_limit_seconds"], .75)
        self.assertEqual(evidence["global_search_settings"]["workers"], 2)
        self.assertFalse(evidence["zone_hierarchy_hints"]["positions_are_constraints"])

    def test_deterministic_final_search_always_uses_one_worker_and_repeats(self):
        first, second = {}, {}
        baseline = self.solve(first, deterministic_units=.05)
        opted = self.solve(second, deterministic_units=.05, global_time_limit_seconds=.75, global_workers=4)
        self.assertEqual(baseline.to_dict(), opted.to_dict())
        self.assertNotIn("global_search_settings", first)
        self.assertEqual(second["global_search_settings"]["requested_workers"], 4)
        self.assertEqual(second["global_search_settings"]["workers"], 1)
        self.assertTrue(validate_zone_layout(program(), opted, zone_rects(second), recursive_zones=True).ok)

    def test_invalid_final_search_controls_fail_before_any_cp_stage(self):
        for options in ({"global_workers": True}, {"global_workers": 0}, {"global_workers": 9},
                        {"global_workers": 1.5}, {"global_time_limit_seconds": True},
                        {"global_time_limit_seconds": 0}, {"global_time_limit_seconds": 121},
                        {"global_time_limit_seconds": float("nan")}, {"global_time_limit_seconds": 10**400}):
            evidence = {}
            with self.subTest(options=options):
                with self.assertRaises(ZoneHierarchyFailure) as failure:
                    self.solve(evidence, **options)
                self.assertEqual(failure.exception.to_dict()["stage"], "input")
                self.assertEqual(failure.exception.to_dict()["model_status"], "NOT_BUILT")
                self.assertEqual(evidence["stage_attempts"], [])


if __name__ == "__main__":
    unittest.main()

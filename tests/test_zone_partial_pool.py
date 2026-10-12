import unittest

from layout_configurator.building import BuildingIR
from layout_configurator.facility import default_facility_profile
from layout_configurator.facility_generation import InfeasibleFacilityGeneration, solve_feasible_facility_variants
from layout_configurator.validation import validate_layout
from layout_configurator.zone_hierarchy import ZoneHierarchyFailure, ZoneHierarchyNotApplicable, solve_zone_layouts


def _program(width=8000):
    return BuildingIR.from_mapping({
        "layout": {
            "boundary": {"width": width, "height": 2000}, "entry_room": "a",
            "rooms": [{"id": room_id, "target_area": 4, "min_area": 4, "max_area": 4,
                       "min_width": 2000, "min_depth": 2000,
                       "required_adjacency": [other] if other else []}
                      for room_id, other in (("a", "b"), ("b", "c"), ("c", "d"), ("d", None))],
        },
        "zones": [{"id": "left", "room_ids": ["a"]},
                  {"id": "right", "room_ids": ["c", "d"]},
                  {"id": "child", "parent_zone_id": "left", "room_ids": []},
                  {"id": "grandchild", "parent_zone_id": "child", "room_ids": ["b"]}],
        "equipment": [{"id": "machine", "room_id": "a", "width_mm": 400,
                       "depth_mm": 400, "clearance_mm": 100}],
        "flows": [{"id": "material", "type": "material", "from_ids": ["a"], "to_ids": ["d"],
                   "minimum_clear_width_mm": 800, "required": True}],
    })


class ZonePartialPoolTests(unittest.TestCase):
    def test_default_three_candidate_budget_accepts_one_variant_when_only_two_geometries_exist(self):
        for strategy in ("zone_hierarchical", "zone_recursive"):
            with self.subTest(strategy=strategy):
                generation = solve_feasible_facility_variants(
                    _program(), default_facility_profile(), variants=1, time_limit_seconds=0.5,
                    seed=1, room_solver=strategy,
                )
                self.assertEqual(generation.max_attempts, 3)
                self.assertEqual(generation.attempted_candidates, 2)
                self.assertEqual(len(generation.accepted), 1)
                candidate = generation.accepted[0]
                for report in (candidate.layout_report, candidate.equipment_report,
                               candidate.flow_report, candidate.facility_report):
                    self.assertTrue(report.ok, str(report))
                pool = generation.to_dict()["room_solver_evidence"]["candidate_pool"]
                self.assertEqual((pool["requested"], pool["returned"]), (3, 2))
                self.assertEqual(pool["stop_reason"], "INFEASIBLE")
                self.assertFalse(pool["proven_building_infeasible"])

    def test_direct_exact_count_contract_is_unchanged(self):
        for recursive in (False, True):
            with self.subTest(recursive=recursive):
                evidence = {}
                with self.assertRaises(ZoneHierarchyFailure):
                    solve_zone_layouts(_program(), 3, 0.5, 1, recursive_zones=recursive, evidence=evidence)
                self.assertEqual(len(evidence["variants"]), 2)
                self.assertNotIn("candidate_pool", evidence)
                self.assertFalse(evidence["failure"]["proven_building_infeasible"])

    def test_explicit_partial_pool_contains_only_independently_valid_candidates(self):
        for recursive in (False, True):
            with self.subTest(recursive=recursive):
                evidence = {}
                results = solve_zone_layouts(_program(), 3, 0.5, 1, recursive_zones=recursive,
                                             allow_partial=True, evidence=evidence)
                self.assertEqual(len(results), 2)
                self.assertEqual([result.variant for result in results], [1, 2])
                for result in results:
                    self.assertTrue(validate_layout(_program().layout, result).ok)
                self.assertNotIn("failure", evidence)

    def test_no_valid_candidates_still_fails_instead_of_returning_an_empty_pool(self):
        for strategy in ("zone_hierarchical", "zone_recursive"):
            with self.subTest(strategy=strategy):
                with self.assertRaises(InfeasibleFacilityGeneration) as caught:
                    solve_feasible_facility_variants(
                        _program(width=2000), default_facility_profile(), variants=1, time_limit_seconds=0.5,
                        seed=1, room_solver=strategy,
                    )
                self.assertEqual(caught.exception.generation.accepted, ())
                self.assertEqual(caught.exception.generation.attempted_candidates, 0)
                self.assertFalse(caught.exception.generation.to_dict()["room_solver_evidence"]["failure"]["proven_building_infeasible"])

    def test_partial_pool_mode_requires_a_boolean_before_search(self):
        for value in (1, "true", None):
            with self.subTest(value=value):
                evidence = {}
                with self.assertRaises(ZoneHierarchyNotApplicable) as caught:
                    solve_zone_layouts(_program(), 1, 0.5, 1, allow_partial=value, evidence=evidence)
                self.assertEqual(caught.exception.stage, "input")
                self.assertEqual(caught.exception.code, "invalid_pool_mode")
                self.assertEqual(evidence["stage_attempts"], [])

    def test_partial_mode_never_returns_a_pool_of_independently_invalid_disconnected_candidates(self):
        building = BuildingIR.from_mapping({
            "layout": {"boundary": {"width": 6000, "height": 2000}, "entry_room": "a",
                       "rooms": [{"id": room_id, "target_area": 4, "min_area": 4, "max_area": 4,
                                  "min_width": 2000, "min_depth": 2000,
                                  "forbidden_adjacency": ["b"] if room_id == "a" else []}
                                 for room_id in ("a", "b")]},
            "zones": [{"id": "left", "room_ids": ["a"]}, {"id": "right", "room_ids": ["b"]}],
        })
        evidence = {}
        with self.assertRaises(ZoneHierarchyFailure) as caught:
            solve_zone_layouts(building, 1, 0.5, 1, allow_partial=True, max_validation_attempts=2,
                               evidence=evidence)
        self.assertEqual(caught.exception.stage, "validation")
        self.assertTrue(any("unreachable_room" in issue for issue in caught.exception.issues))
        self.assertEqual(evidence["variants"], [])
        self.assertNotIn("candidate_pool", evidence)

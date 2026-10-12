import unittest
from dataclasses import replace

from layout_configurator.building import BuildingIR
from layout_configurator.models import Rect
from layout_configurator.solver import solve_layouts
from layout_configurator.validation import validate_layout
from layout_configurator.zone_hierarchy import (
    ZoneHierarchyFailure, ZoneHierarchyNotApplicable, solve_zone_layouts, validate_zone_layout,
)


def _single(*, flexible=False, cutout=False):
    return BuildingIR.from_mapping({
        "layout": {
            "boundary": {"width": 6000, "height": 6000,
                         "cutouts": [{"x": 3000, "y": 2000, "width": 2000, "height": 2000}] if cutout else []},
            "entry_room": "a",
            "rooms": [{"id": "a", "target_area": 4, "min_area": 4,
                       "max_area": 6 if flexible else 4, "min_width": 2000, "min_depth": 2000}],
        },
        "zones": [{"id": "parent", "room_ids": []},
                  {"id": "child", "parent_zone_id": "parent", "room_ids": ["a"]}],
    })


def _chain():
    return BuildingIR.from_mapping({
        "layout": {
            "boundary": {"width": 8000, "height": 2000}, "entry_room": "a",
            "rooms": [{"id": room_id, "target_area": 4, "min_area": 4, "max_area": 4,
                       "min_width": 2000, "min_depth": 2000,
                       "required_adjacency": [other] if other else []}
                      for room_id, other in (("a", "b"), ("b", "c"), ("c", "d"), ("d", None))],
        },
        "zones": [{"id": "parent", "room_ids": []},
                  {"id": "left", "parent_zone_id": "parent", "room_ids": ["a", "b"]},
                  {"id": "right", "parent_zone_id": "parent", "room_ids": ["c", "d"]}],
    })


def _containers(evidence):
    return {key: Rect(**raw) for key, raw in evidence["variants"][0]["zone_rects"].items()}


class ZoneConstraintTests(unittest.TestCase):
    def _solve(self, building, recursive, **kwargs):
        evidence = {}
        result = solve_zone_layouts(building, 1, 0.5, 1, recursive_zones=recursive,
                                   evidence=evidence, **kwargs)[0]
        self.assertTrue(validate_layout(building.layout, result).ok)
        constraints = {key: value for key, value in kwargs.items() if key != "zone_size_options_mm"}
        report = validate_zone_layout(building, result, _containers(evidence),
                                      recursive_zones=recursive, **constraints)
        self.assertTrue(report.ok, report.issues)
        return result, evidence

    def test_fixed_coordinates_choose_global_mirrored_shape_in_both_modes(self):
        building = _chain()
        for recursive in (False, True):
            with self.subTest(recursive=recursive):
                sizes = {"parent": [(8000, 2000)]}
                if recursive:
                    sizes.update(left=[(4000, 2000)], right=[(4000, 2000)])
                fixed = {"a": Rect(6000, 0, 2000, 2000), "d": Rect(0, 0, 2000, 2000)}
                result, _ = self._solve(building, recursive, fixed_rects=fixed, zone_size_options_mm=sizes)
                for room_id, rect in fixed.items():
                    self.assertEqual(result.placements[room_id], rect)

    def test_fixed_size_different_from_target_is_solved_locally_then_placed_globally(self):
        fixed = {"a": Rect(3000, 2000, 3000, 2000)}
        for recursive in (False, True):
            with self.subTest(recursive=recursive):
                result, evidence = self._solve(_single(flexible=True), recursive, fixed_rects=fixed)
                self.assertEqual(result.placements["a"], fixed["a"])
                self.assertEqual(set(_containers(evidence)), {"parent", "child"} if recursive else {"parent"})

    def test_selected_axes_constrain_both_room_endpoints_in_global_coordinates(self):
        for recursive in (False, True):
            with self.subTest(recursive=recursive):
                result, _ = self._solve(_single(), recursive, axis_aligned_room_ids=["a"],
                                        structural_axes_x_mm=[3000, 5000], structural_axes_y_mm=[2000, 4000])
                self.assertEqual(result.placements["a"], Rect(3000, 2000, 2000, 2000))

    def test_axis_iterators_are_replayed_for_multiple_selected_rooms(self):
        for recursive in (False, True):
            with self.subTest(recursive=recursive):
                sizes = {"parent": [(8000, 2000)]}
                if recursive:
                    sizes.update(left=[(4000, 2000)], right=[(4000, 2000)])
                result, _ = self._solve(_chain(), recursive, zone_size_options_mm=sizes,
                                        axis_aligned_room_ids=iter(("a", "b", "c", "d")),
                                        structural_axes_x_mm=iter(range(0, 8001, 2000)),
                                        structural_axes_y_mm=iter((0, 2000)))
                for room in result.placements.values():
                    self.assertIn(room.x, range(0, 8001, 2000))
                    self.assertIn(room.right, range(0, 8001, 2000))

    def test_unknown_selected_rooms_and_unusable_axes_fail_before_local_models(self):
        invalid = (
            ({"axis_aligned_room_ids": ["missing"]}, "unknown_axis_room"),
            ({"axis_aligned_room_ids": ["a"], "structural_axes_x_mm": []}, "invalid_axes"),
            ({"axis_aligned_room_ids": ["a"], "structural_axes_y_mm": [0]}, "invalid_axes"),
            ({"axis_aligned_room_ids": ["a"], "structural_axes_x_mm": [0, 2001]}, "invalid_axes"),
            ({"axis_aligned_room_ids": ["a"], "structural_axes_x_mm": [0, float("nan")]}, "invalid_axes"),
            ({"axis_aligned_room_ids": ["a"], "structural_axes_x_mm": [0, "2000"]}, "invalid_axes"),
            ({"axis_aligned_room_ids": ["a"], "structural_axes_x_mm": [False, 2000]}, "invalid_axes"),
        )
        for recursive in (False, True):
            for kwargs, code in invalid:
                with self.subTest(recursive=recursive, kwargs=kwargs):
                    evidence = {}
                    with self.assertRaises(ZoneHierarchyNotApplicable) as caught:
                        solve_zone_layouts(_single(), 1, 0.5, 1, evidence=evidence,
                                           recursive_zones=recursive, **kwargs)
                    self.assertEqual(caught.exception.stage, "input")
                    self.assertEqual(caught.exception.code, code)
                    self.assertEqual(caught.exception.model_status, "NOT_BUILT")
                    self.assertEqual(evidence["stage_attempts"], [])

    def test_fixed_rects_reject_unknown_nonfinite_offgrid_invalid_size_and_area(self):
        invalid = (
            ({"missing": Rect(0, 0, 2000, 2000)}, "unknown_fixed_room"),
            ({"a": Rect("0", 0, 2000, 2000)}, "invalid_fixed_rect"),
            ({"a": Rect(False, 0, 2000, 2000)}, "invalid_fixed_rect"),
            ({"a": Rect(float("inf"), 0, 2000, 2000)}, "invalid_fixed_rect"),
            ({"a": Rect(0, 0, float("nan"), 2000)}, "invalid_fixed_rect"),
            ({"a": Rect(1, 0, 2000, 2000)}, "invalid_fixed_rect"),
            ({"a": Rect(0, 0, 0, 2000)}, "invalid_fixed_rect"),
            ({"a": Rect(0, 0, 2000, -2000)}, "invalid_fixed_rect"),
            ({"a": Rect(0, 0, 1000, 4000)}, "invalid_fixed_rect"),
            ({"a": Rect(0, 0, 3000, 2000)}, "invalid_fixed_rect"),
            ({"a": "rectangle"}, "invalid_fixed_rect"),
        )
        for recursive in (False, True):
            for fixed, code in invalid:
                with self.subTest(recursive=recursive, fixed=fixed):
                    evidence = {}
                    with self.assertRaises(ZoneHierarchyNotApplicable) as caught:
                        solve_zone_layouts(_single(), 1, 0.5, 1, evidence=evidence,
                                           recursive_zones=recursive, fixed_rects=fixed)
                    self.assertEqual(caught.exception.code, code)
                    self.assertEqual(caught.exception.stage, "input")
                    self.assertEqual(caught.exception.model_status, "NOT_BUILT")
                    self.assertEqual(evidence["stage_attempts"], [])

    def test_fixed_cutout_and_pin_axis_conflicts_are_actual_global_model_failures(self):
        for recursive in (False, True):
            for building, kwargs in (
                (_single(cutout=True), {"fixed_rects": {"a": Rect(3000, 2000, 2000, 2000)}}),
                (_single(), {"fixed_rects": {"a": Rect(0, 0, 2000, 2000)},
                             "axis_aligned_room_ids": ["a"], "structural_axes_x_mm": [3000, 5000]}),
            ):
                with self.subTest(recursive=recursive, kwargs=kwargs):
                    evidence = {}
                    with self.assertRaises(ZoneHierarchyFailure) as caught:
                        solve_zone_layouts(building, 1, 0.5, 1, evidence=evidence,
                                           recursive_zones=recursive, **kwargs)
                    self.assertNotIsInstance(caught.exception, ZoneHierarchyNotApplicable)
                    self.assertEqual(caught.exception.stage, "global")
                    self.assertEqual(caught.exception.model_status, "INFEASIBLE")
                    self.assertEqual(caught.exception.proof_scope, "selected_zone_shapes")
                    self.assertFalse(caught.exception.to_dict()["proven_building_infeasible"])

    def test_independent_validator_detects_pin_and_axis_errors_on_otherwise_valid_geometry(self):
        building = _single()
        for recursive in (False, True):
            with self.subTest(recursive=recursive):
                result, evidence = self._solve(building, recursive)
                moved = replace(result, placements={"a": Rect(1000, 1000, 2000, 2000)})
                moved_containers = {key: Rect(1000, 1000, 2000, 2000) for key in _containers(evidence)}
                self.assertTrue(validate_layout(building.layout, moved).ok)
                report = validate_zone_layout(building, moved, moved_containers, recursive_zones=recursive,
                                              fixed_rects={"a": result.placements["a"]},
                                              axis_aligned_room_ids=["a"], structural_axes_x_mm=[0, 2000])
                self.assertIn("fixed_room_mismatch", {issue.code for issue in report.issues})
                self.assertIn("structural_axis_mismatch", {issue.code for issue in report.issues})

    def test_axes_without_selected_rooms_keep_monolithic_ignore_semantics(self):
        for recursive in (False, True):
            with self.subTest(recursive=recursive):
                baseline, _ = self._solve(_single(), recursive)
                ignored, _ = self._solve(_single(), recursive, structural_axes_x_mm=[],
                                         axis_aligned_room_ids=[])
                self.assertEqual(ignored, baseline)

    def test_missing_local_axis_size_is_not_a_proof_against_the_unrestricted_program(self):
        building = _single(flexible=True)
        constraints = {"axis_aligned_room_ids": ["a"], "structural_axes_x_mm": [3000, 6000],
                       "structural_axes_y_mm": [2000, 4000]}
        baseline = solve_layouts(building.layout, 1, 0.5, 1, **constraints)[0]
        self.assertEqual(baseline.placements["a"], Rect(3000, 2000, 3000, 2000))
        self.assertTrue(validate_layout(building.layout, baseline).ok)
        for recursive in (False, True):
            with self.subTest(recursive=recursive):
                evidence = {}
                sizes = {"parent": [(2000, 2000)]}
                if recursive:
                    sizes["child"] = [(2000, 2000)]
                with self.assertRaises(ZoneHierarchyFailure) as caught:
                    solve_zone_layouts(building, 1, 0.5, 1, recursive_zones=recursive,
                                       evidence=evidence, zone_size_options_mm=sizes, **constraints)
                self.assertEqual(caught.exception.model_status, "INFEASIBLE")
                self.assertEqual(caught.exception.proof_scope, "selected_zone_shapes")
                self.assertFalse(evidence["failure"]["proven_building_infeasible"])


if __name__ == "__main__":
    unittest.main()

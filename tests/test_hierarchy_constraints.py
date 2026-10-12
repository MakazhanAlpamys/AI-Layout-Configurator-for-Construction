import unittest

from layout_configurator.hierarchy import HierarchyNotApplicable, solve_layouts_auto
from layout_configurator.models import LayoutIR, Rect
from layout_configurator.solver import InfeasibleLayout
from layout_configurator.validation import validate_layout
from layout_configurator.walls import build_wall_plan


def _room(room_id, area=4, **extra):
    return {
        "id": room_id,
        "target_area": area,
        "min_area": area,
        "max_area": area,
        "min_width": 2000,
        "min_depth": 2000,
        **extra,
    }


def _single_room_spec(boundary=None, **room_extra):
    return LayoutIR.from_mapping(
        {"boundary": boundary or {"width": 6000, "height": 6000}, "rooms": [_room("room", **room_extra)]}
    )


class HierarchyBoundaryConstraintsTests(unittest.TestCase):
    def _solve(self, spec, **options):
        evidence = {}
        try:
            results = solve_layouts_auto(spec, 1, 3, 1, strategy="hierarchical", evidence=evidence, **options)
        except InfeasibleLayout as exc:
            self.fail(f"The forced hierarchy must solve this feasible constraint fixture: {exc}")
        self.assertEqual(evidence["room_solver_used"], "hierarchical")
        self.assertNotIn("hierarchy_fallback_reason", evidence)
        result = results[0]
        report = validate_layout(spec, result)
        self.assertTrue(report.ok, report.issues)
        return result

    def test_a_room_is_excluded_from_a_boundary_cutout_without_fallback(self):
        # Without the exclusion the position tie-breaker puts room at (0, 0).
        spec = _single_room_spec(
            {"width": 6000, "height": 4000, "cutouts": [{"x": 0, "y": 0, "width": 2000, "height": 4000}]}
        )
        result = self._solve(spec)
        self.assertGreaterEqual(result.placements["room"].x, 2000)

    def test_each_room_of_a_rigid_cluster_avoids_the_cutout(self):
        spec = LayoutIR.from_mapping(
            {
                "boundary": {"width": 14000, "height": 8000, "cutouts": [{"x": 0, "y": 0, "width": 4000, "height": 4000}]},
                "entry_room": "hub",
                "rooms": [
                    _room("hub", 8, min_width=4000),
                    *[_room(name, required_adjacency=["hub"]) for name in ("a", "b", "c")],
                ],
            }
        )
        result = self._solve(spec)
        cutout = Rect(0, 0, 4000, 4000)
        for rect in result.placements.values():
            self.assertEqual(rect.intersection_area(cutout), 0)

    def test_daylight_can_use_a_cutout_edge_inside_the_bounding_box(self):
        spec = _single_room_spec(
            {"width": 6000, "height": 6000, "cutouts": [{"x": 3000, "y": 3000, "width": 3000, "height": 3000}]},
            needs_daylight=True,
        )
        pinned = Rect(1000, 3500, 2000, 2000)
        result = self._solve(spec, fixed_rects={"room": pinned})
        windows = build_wall_plan(spec, result).windows
        self.assertEqual(result.placements["room"], pinned)
        self.assertEqual(len(windows), 1)
        self.assertEqual(windows[0].fixed, 3000)
        self.assertEqual(windows[0].orientation, "vertical")

    def test_a_fixed_room_inside_the_cutout_is_a_constraint_failure(self):
        spec = _single_room_spec(
            {"width": 6000, "height": 6000, "cutouts": [{"x": 0, "y": 0, "width": 3000, "height": 3000}]}
        )
        with self.assertRaises(InfeasibleLayout) as caught:
            solve_layouts_auto(spec, 1, 0.5, 1, strategy="hierarchical", fixed_rects={"room": Rect(0, 0, 2000, 2000)})
        self.assertNotIsInstance(caught.exception, HierarchyNotApplicable)

    def test_a_tiny_cutout_contact_cannot_satisfy_automatic_daylight(self):
        # 100 mm of exposed edge cannot hold the 600 mm minimum window.
        spec = _single_room_spec(
            {"width": 6000, "height": 6000, "cutouts": [{"x": 3000, "y": 3000, "width": 3000, "height": 3000}]},
            needs_daylight=True,
        )
        with self.assertRaises(InfeasibleLayout):
            solve_layouts_auto(
                spec, 1, 0.5, 1, strategy="hierarchical", fixed_rects={"room": Rect(1000, 1100, 2000, 2000)},
            )

    def test_a_cutout_edge_can_hold_the_minimum_automatic_window(self):
        spec = _single_room_spec(
            {"width": 6000, "height": 6000, "cutouts": [{"x": 3000, "y": 3000, "width": 3000, "height": 3000}]},
            needs_daylight=True,
        )
        result = self._solve(spec, fixed_rects={"room": Rect(1000, 2000, 2000, 2000)})
        windows = build_wall_plan(spec, result).windows
        self.assertEqual(len(windows), 1)
        self.assertEqual(windows[0].end - windows[0].start, 600)

    def test_selected_room_endpoints_follow_both_structural_axis_sets(self):
        spec = LayoutIR.from_mapping(
            {
                "boundary": {"width": 10000, "height": 6000},
                "entry_room": "hub",
                "rooms": [
                    _room("hub", 8, min_width=4000),
                    *[_room(name, required_adjacency=["hub"]) for name in ("a", "b", "c")],
                ],
            }
        )
        result = self._solve(
            spec, axis_aligned_room_ids=("hub",),
            structural_axes_x_mm=(0, 4000), structural_axes_y_mm=(0, 2000),
        )
        self.assertEqual(result.placements["hub"], Rect(0, 0, 4000, 2000))
        # The unselected 2 m rooms stay free rather than inheriting 4 m bays.
        self.assertEqual({result.placements[name].width for name in ("a", "b", "c")}, {2000})

    def test_axis_iterators_apply_to_every_selected_room(self):
        spec = LayoutIR.from_mapping(
            {
                "boundary": {"width": 6000, "height": 4000},
                "rooms": [_room("a", required_adjacency=["b"]), _room("b")],
            }
        )
        result = self._solve(
            spec, axis_aligned_room_ids=(name for name in ("a", "b")),
            structural_axes_x_mm=(value for value in (0, 2000, 4000, 6000)),
            structural_axes_y_mm=(value for value in (0, 2000, 4000)),
        )
        for rect in result.placements.values():
            self.assertIn(rect.x, {0, 2000, 4000, 6000})
            self.assertIn(rect.right, {0, 2000, 4000, 6000})
            self.assertIn(rect.y, {0, 2000, 4000})
            self.assertIn(rect.top, {0, 2000, 4000})

    def test_an_axis_constrained_leaf_keeps_free_dimensions_at_top_level(self):
        spec = LayoutIR.from_mapping(
            {
                "boundary": {"width": 10000, "height": 6000},
                "entry_room": "hub",
                "rooms": [
                    _room("hub", 8, min_width=4000),
                    *[_room(name, required_adjacency=["hub"]) for name in ("a", "b", "c")],
                ],
            }
        )
        result = self._solve(
            spec, axis_aligned_room_ids=("b",),
            structural_axes_x_mm=(6000, 8000), structural_axes_y_mm=(2000, 4000),
        )
        self.assertEqual(result.placements["b"], Rect(6000, 2000, 2000, 2000))

    def test_unknown_axis_rooms_are_rejected_with_the_room_id(self):
        with self.assertRaisesRegex(InfeasibleLayout, "unknown.*ghost"):
            solve_layouts_auto(_single_room_spec(), 1, 0.5, 1, strategy="hierarchical", axis_aligned_room_ids=("ghost",))

    def test_empty_axis_sets_are_rejected_with_the_axis_reason(self):
        with self.assertRaisesRegex(InfeasibleLayout, "room.*pair.*x.*axes"):
            solve_layouts_auto(
                _single_room_spec(), 1, 0.5, 1, strategy="hierarchical", axis_aligned_room_ids=("room",),
                structural_axes_x_mm=(),
            )

    def test_axes_not_on_the_room_grid_are_rejected(self):
        with self.assertRaisesRegex(InfeasibleLayout, "room.*not aligned.*100"):
            solve_layouts_auto(
                _single_room_spec(), 1, 0.5, 1, strategy="hierarchical", axis_aligned_room_ids=("room",),
                structural_axes_x_mm=(0, 2050, 6000),
            )

    def test_infeasible_axis_bays_report_the_selected_rooms(self):
        with self.assertRaises(InfeasibleLayout) as caught:
            solve_layouts_auto(
                _single_room_spec(), 1, 0.5, 1, strategy="hierarchical", axis_aligned_room_ids=("room",),
                structural_axes_x_mm=(0, 1000),
            )
        self.assertNotIsInstance(caught.exception, HierarchyNotApplicable)
        self.assertIn("room", str(caught.exception))
        self.assertRegex(str(caught.exception), "axis|axes")

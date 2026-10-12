import unittest

from layout_configurator.models import LayoutIR, Rect
from layout_configurator.solver import InfeasibleLayout, solve_layouts
from layout_configurator.validation import validate_layout
from layout_configurator.walls import build_wall_plan


def _spec(*, cutout_y=3000, cutout_x=3000, windows=(), window_width=1200):
    return LayoutIR.from_mapping(
        {
            "boundary": {
                "width": 6000,
                "height": 6000,
                "cutouts": [{"x": cutout_x, "y": cutout_y, "width": 6000 - cutout_x, "height": 6000 - cutout_y}],
            },
            "window_width_mm": window_width,
            "rooms": [{"id": "room", "target_area": 4, "min_area": 4, "max_area": 4, "min_width": 2000, "min_depth": 2000, "needs_daylight": True}],
            "windows": list(windows),
        }
    )


class DaylightFacadeTests(unittest.TestCase):
    def _solve(self, spec, fixed):
        result = solve_layouts(spec, 1, 0.5, 1, fixed_rects={"room": fixed})[0]
        report = validate_layout(spec, result)
        self.assertTrue(report.ok, report.issues)
        return build_wall_plan(spec, result).windows

    def test_100_mm_of_cutout_contact_cannot_hold_an_automatic_window(self):
        # Dropping the facade-width constraint lets the solver publish this
        # pinned layout even though the independent checker finds no window.
        with self.assertRaises(InfeasibleLayout):
            solve_layouts(_spec(), 1, 0.5, 1, fixed_rects={"room": Rect(1000, 1100, 2000, 2000)})

    def test_exactly_1000_mm_of_cutout_contact_holds_the_minimum_window(self):
        windows = self._solve(_spec(), Rect(1000, 2000, 2000, 2000))
        self.assertEqual(len(windows), 1)
        self.assertEqual(windows[0].end - windows[0].start, 600)

    def test_a_contact_just_below_the_window_threshold_is_rejected(self):
        # The facade is 999 mm long, so rounding it to 1000 must not accept it.
        with self.assertRaises(InfeasibleLayout):
            solve_layouts(_spec(cutout_y=3001), 1, 0.5, 1, fixed_rects={"room": Rect(1000, 2000, 2000, 2000)})

    def test_a_contact_just_above_the_window_threshold_is_usable(self):
        windows = self._solve(_spec(cutout_y=2999), Rect(1000, 2000, 2000, 2000))
        self.assertEqual(len(windows), 1)
        self.assertEqual(windows[0].end - windows[0].start, 601)

    def test_an_explicit_window_can_use_a_shorter_cutout_facade(self):
        spec = _spec(
            cutout_y=3400,
            windows=[{"id": "manual", "room_id": "room", "side": "right", "offset_mm": 300, "width_mm": 600}],
        )
        windows = self._solve(spec, Rect(1000, 2000, 2000, 2000))
        self.assertEqual(len(windows), 1)
        self.assertEqual(windows[0].id, "manual")
        self.assertEqual(windows[0].end - windows[0].start, 600)

    def test_an_automatic_window_width_below_the_builder_minimum_is_rejected(self):
        with self.assertRaises(InfeasibleLayout):
            solve_layouts(_spec(window_width=500), 1, 0.5, 1, fixed_rects={"room": Rect(1000, 2000, 2000, 2000)})

    def test_rounding_a_cutout_edge_cannot_invent_an_exterior_facade(self):
        # The room crosses the real cutout edge by 1 mm; rounding must not
        # turn that into an exterior side suitable for a daylight window.
        with self.assertRaises(InfeasibleLayout):
            solve_layouts(_spec(cutout_x=2999), 1, 0.5, 1, fixed_rects={"room": Rect(1000, 2000, 2000, 2000)})

    def test_rounding_a_short_facade_up_cannot_make_a_window_fit(self):
        raw = _spec().to_dict()
        raw["boundary"]["cutouts"][0].update(y=2499.9, height=999.8)
        spec = LayoutIR.from_mapping(raw)
        with self.assertRaises(InfeasibleLayout):
            solve_layouts(spec, 1, 0.5, 1, fixed_rects={"room": Rect(1000, 2000, 2000, 2000)})

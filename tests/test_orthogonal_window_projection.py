"""Opted-in facade windows preserve the closed circulation wall network."""

from dataclasses import replace
import importlib.util
import unittest

from layout_configurator.models import BoundarySpec, LayoutIR, LayoutResult, Rect, WindowSpec
from layout_configurator.orthogonal_projection import ProjectionCapabilityError, build_orthogonal_wall_plan
from layout_configurator.orthogonal_windows import project_orthogonal_windows
from test_orthogonal_flows import _l_program


class OrthogonalWindowProjectionTests(unittest.TestCase):
    def capability(self):
        self.assertIsNotNone(importlib.util.find_spec("layout_configurator.orthogonal_window_capability"),
                             "Source facade and coordination-height capability is missing")
        from layout_configurator import orthogonal_window_capability
        return orthogonal_window_capability

    def source(self):
        building, layout, _ = _l_program()
        return building.layout, layout

    def test_explicit_windows_are_source_backed_without_cutting_walls_or_creating_portals(self):
        spec, layout = self.source()
        baseline = build_orthogonal_wall_plan(spec, layout)
        spec = replace(spec, windows=(WindowSpec("source_window", "main", "bottom", 1000, 800),))
        before = spec.to_dict(), layout.to_dict()
        plan = build_orthogonal_wall_plan(spec, layout, include_windows=True)
        self.assertEqual(plan.windows, project_orthogonal_windows(spec, layout).windows)
        self.assertEqual(plan.openings, baseline.openings)
        self.assertEqual(plan.geometry.wkb_hex, baseline.geometry.wkb_hex)
        window = plan.windows[0]
        self.assertAlmostEqual(plan.geometry.intersection(window.centerline).length, window.width)
        self.assertNotIn(window.id, {opening.id for opening in plan.openings})
        self.assertEqual((spec.to_dict(), layout.to_dict()), before)

    def test_default_and_explicit_false_keep_the_existing_unsupported_guard(self):
        spec, layout = self.source()
        for current in (replace(spec, windows=(WindowSpec("window", "main", "bottom", 1000, 800),)),
                        replace(spec, rooms=(replace(spec.rooms[0], needs_daylight=True), spec.rooms[1]))):
            with self.subTest(daylight=not current.windows):
                reports = []
                for options in ({}, {"include_windows": False}):
                    with self.assertRaises(ProjectionCapabilityError) as caught:
                        build_orthogonal_wall_plan(current, layout, **options)
                    reports.append(caught.exception.to_dict())
                self.assertEqual(reports[0], reports[1])
                self.assertEqual(reports[0]["status"], "UNKNOWN")
                self.assertEqual(reports[0]["component"], "windows")

    def test_no_window_default_false_and_opted_plans_keep_exact_existing_material_and_doors(self):
        spec, layout = self.source()
        default = build_orthogonal_wall_plan(spec, layout)
        for value in (False, True):
            with self.subTest(include_windows=value):
                actual = build_orthogonal_wall_plan(spec, layout, include_windows=value)
                self.assertEqual(actual.geometry.wkb_hex, default.geometry.wkb_hex)
                self.assertEqual(actual.openings, default.openings)
                self.assertEqual(actual.windows, ())

    def test_automatic_window_avoids_entry_and_is_never_an_external_door(self):
        spec = LayoutIR.from_mapping({"boundary": {"width": 4000, "height": 3000}, "entry_room": "room",
            "rooms": [{"id": "room", "target_area": 12, "min_area": 12, "max_area": 12, "needs_daylight": True}],
            "external_entry": {"id": "entry", "room_id": "room", "side": "bottom", "offset_mm": 2000, "width_mm": 3900}})
        layout = LayoutResult(1, {"room": Rect(0, 0, 4000, 3000)})
        plan = build_orthogonal_wall_plan(spec, layout, include_windows=True)
        self.assertEqual(len(plan.windows), 1)
        self.assertEqual(plan.windows[0].center, (2000, 3000))
        self.assertEqual([opening.id for opening in plan.openings], ["entry"])
        self.assertTrue(plan.openings[0].external)
        self.assertAlmostEqual(plan.geometry.intersection(plan.windows[0].centerline).length, plan.windows[0].width)
        self.assertEqual(plan.windows[0].centerline.intersection(plan.openings[0].centerline).length, 0)

    def test_actual_cutout_window_retains_the_closed_host_wall(self):
        spec = LayoutIR.from_mapping({"boundary": {"width": 6000, "height": 6000,
            "cutouts": [{"x": 0, "y": 3000, "width": 4000, "height": 3000}]}, "entry_room": "room",
            "rooms": [{"id": "room", "target_area": 12, "min_area": 12, "max_area": 12}],
            "windows": [{"id": "cutout", "room_id": "room", "side": "top", "offset_mm": 1000, "width_mm": 800}]})
        plan = build_orthogonal_wall_plan(spec, LayoutResult(1, {"room": Rect(0, 0, 4000, 3000)}), include_windows=True)
        self.assertEqual(plan.windows[0].center, (1000, 3000))
        self.assertEqual(plan.openings, ())
        self.assertAlmostEqual(plan.geometry.intersection(plan.windows[0].centerline).length, 800)

    def test_ambiguous_facade_and_duplicate_source_identity_carry_unknown_and_fail_witnesses(self):
        api = self.capability()
        spec, layout = self.source()
        ambiguous = replace(spec, boundary=BoundarySpec(6000, 6000, (Rect(2000, 2000, 4000, 4000),)),
            rooms=(replace(spec.rooms[0], required_adjacency=()),),
            windows=(WindowSpec("ambiguous", "main", "right", 1000, 800),))
        collision = replace(spec, rooms=(replace(spec.rooms[0], needs_daylight=True), spec.rooms[1]),
            windows=(WindowSpec("auto-main", "corner", "right", 1000, 800),))
        for source, result, expected in ((ambiguous, replace(layout, placements={"main": layout.placements["main"]}), "UNKNOWN"),
                                         (collision, layout, "FAIL")):
            with self.subTest(status=expected):
                witness = api.validate_orthogonal_window_capability(source, result)
                self.assertEqual(witness.status, expected)
                with self.assertRaises(api.OrthogonalWindowCapabilityError) as caught:
                    build_orthogonal_wall_plan(source, result, include_windows=True)
                self.assertEqual(caught.exception.report.to_dict(), witness.to_dict())
                self.assertEqual(caught.exception.to_dict()["status"], expected)
                self.assertIs(caught.exception.to_dict()["evidence"]["proven_building_infeasible"], False)

    def test_source_sill_and_height_fit_only_the_declared_coordination_proxy(self):
        api = self.capability()
        spec, layout = self.source()
        spec = replace(spec, windows=(WindowSpec("window", "main", "bottom", 1000, 800),), window_height_mm=1900)
        before = spec.to_dict(), layout.to_dict()
        report = api.validate_orthogonal_window_capability(spec, layout)
        self.assertTrue(report.ok, report.to_dict())
        self.assertEqual(report.to_dict()["vertical_range_mm"], {"sill": 900, "height": 1900, "head": 2800})
        self.assertEqual(report.source, project_orthogonal_windows(spec, layout).source)
        self.assertEqual(report.to_dict()["policy"]["coordination_wall_height_mm"], 2800)
        self.assertEqual(report.to_dict()["full_facility_validation"]["status"], "UNKNOWN")
        self.assertEqual(report.to_dict()["engineering_acceptance"], "PENDING")
        self.assertEqual((spec.to_dict(), layout.to_dict()), before)
        for height in (1900.00001, 1901, 5000):
            with self.subTest(height=height):
                current = replace(spec, window_height_mm=height)
                unknown = api.validate_orthogonal_window_capability(current, layout)
                self.assertEqual(unknown.status, "UNKNOWN", unknown.to_dict())
                self.assertFalse(unknown.ok)
                self.assertIs(unknown.to_dict()["proven_building_infeasible"], False)
                self.assertTrue(project_orthogonal_windows(current, layout).ok)
                with self.assertRaises(api.OrthogonalWindowCapabilityError) as caught:
                    build_orthogonal_wall_plan(current, layout, include_windows=True)
                self.assertEqual(caught.exception.to_dict()["status"], "UNKNOWN")

    def test_zero_sill_windows_still_do_not_remove_the_circulation_barrier(self):
        spec, layout = self.source()
        spec = replace(spec, windows=(WindowSpec("window", "main", "bottom", 1000, 800),), window_sill_mm=0)
        plan = build_orthogonal_wall_plan(spec, layout, include_windows=True)
        self.assertAlmostEqual(plan.geometry.intersection(plan.windows[0].centerline).length, 800)
        self.assertNotIn("window", {opening.id for opening in plan.openings})

    def test_unused_window_height_does_not_block_a_source_without_windows(self):
        api = self.capability()
        spec, layout = self.source()
        spec = replace(spec, window_height_mm=5000)
        self.assertTrue(api.validate_orthogonal_window_capability(spec, layout).ok)
        self.assertEqual(build_orthogonal_wall_plan(spec, layout, include_windows=True).windows, ())

    def test_malformed_opt_in_and_source_extents_raise_value_error_before_geometry(self):
        spec, layout = self.source()
        for value in (None, 0, 1, "true", []):
            with self.subTest(include_windows=value), self.assertRaises(ValueError):
                build_orthogonal_wall_plan(spec, layout, include_windows=value)
        spec = replace(spec, windows=(WindowSpec("window", "main", "bottom", 1000, 800),))
        huge = replace(layout, placements={**layout.placements, "main": Rect(10**400, 0, 200.0, 200.0)})
        with self.assertRaisesRegex(ValueError, "extent"):
            build_orthogonal_wall_plan(spec, huge, include_windows=True)


if __name__ == "__main__":
    unittest.main()

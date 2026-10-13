"""Polygon windows use actual source facade segments and explicit scope."""

from dataclasses import replace
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from layout_configurator.building import BuildingIR
from layout_configurator.models import BoundarySpec, ExternalEntrySpec, LayoutIR, LayoutResult, Rect, WindowSpec
from layout_configurator.orthogonal import OrthogonalPolygon
from layout_configurator.walls import WindowOpening
from test_orthogonal_flows import _l_program


class OrthogonalWindowTests(unittest.TestCase):
    def api(self):
        self.assertIsNotNone(importlib.util.find_spec("layout_configurator.orthogonal_windows"),
                             "Actual polygon window component is missing")
        from layout_configurator import orthogonal_windows
        return orthogonal_windows

    def source(self):
        building, layout, _ = _l_program()
        return building.layout, layout

    def test_explicit_window_retains_actual_concave_facade_and_source_identity(self):
        api = self.api()
        spec, layout = self.source()
        spec = replace(spec, windows=(WindowSpec("source_window", "main", "bottom", 1000, 800),))
        before = spec.to_dict(), layout.to_dict()
        report = api.project_orthogonal_windows(spec, layout)
        self.assertEqual(report.status, "PASS", report.to_dict())
        self.assertEqual(report.windows, (WindowOpening("main", "horizontal", 0, 600, 1400, "source_window"),))
        self.assertEqual(report.to_dict()["full_facility_validation"]["status"], "UNKNOWN")
        self.assertEqual(report.to_dict()["engineering_acceptance"], "PENDING")
        self.assertTrue(api.validate_orthogonal_windows(spec, layout, report.windows).ok)
        self.assertEqual((spec.to_dict(), layout.to_dict()), before)

    def test_automatic_daylight_has_deterministic_real_span_without_physical_daylight_claim(self):
        api = self.api()
        spec, layout = self.source()
        spec = replace(spec, rooms=(replace(spec.rooms[0], needs_daylight=True), spec.rooms[1]))
        first = api.project_orthogonal_windows(spec, layout)
        second = api.project_orthogonal_windows(spec, layout)
        self.assertEqual(first.to_dict(), second.to_dict())
        self.assertEqual(first.status, "PASS", first.to_dict())
        self.assertEqual(len(first.windows), 1)
        self.assertEqual(first.windows[0].room_id, "main")
        self.assertEqual(first.windows[0].width, spec.window_width_mm)
        self.assertIn("not a daylight", first.to_dict()["scope"])

    def test_cutout_facade_is_real_but_an_internal_room_edge_is_not_exterior(self):
        api = self.api()
        spec = LayoutIR.from_mapping({"boundary": {"width": 6000, "height": 6000,
            "cutouts": [{"x": 0, "y": 3000, "width": 4000, "height": 3000}]},
            "entry_room": "room", "rooms": [{"id": "room", "target_area": 12, "min_area": 12, "max_area": 12}],
            "windows": [{"id": "cutout", "room_id": "room", "side": "top", "offset_mm": 1000, "width_mm": 800}]})
        layout = LayoutResult(1, {"room": Rect(0, 0, 4000, 3000)})
        report = api.project_orthogonal_windows(spec, layout)
        self.assertEqual(report.status, "PASS", report.to_dict())
        self.assertEqual(report.windows[0].center, (1000, 3000))
        internal = replace(spec, windows=(replace(spec.windows[0], side="right"),))
        self.assertEqual(api.project_orthogonal_windows(internal, layout).status, "FAIL")

    def test_multiple_same_side_facades_need_a_source_selector_and_remain_unknown(self):
        api = self.api()
        spec, layout = self.source()
        spec = replace(spec, boundary=BoundarySpec(6000, 6000, (Rect(2000, 2000, 4000, 4000),)),
                       rooms=(replace(spec.rooms[0], required_adjacency=()),),
                       windows=(WindowSpec("ambiguous", "main", "right", 1000, 800),))
        layout = replace(layout, placements={"main": layout.placements["main"]})
        report = api.project_orthogonal_windows(spec, layout)
        self.assertEqual(report.status, "UNKNOWN", report.to_dict())
        self.assertFalse(report.ok)
        self.assertEqual(report.windows, ())
        self.assertIn("selector", str(report.to_dict()))

    def test_entry_and_overlapping_windows_cannot_claim_clear_source_openings(self):
        api = self.api()
        spec, layout = self.source()
        window = WindowSpec("window", "main", "bottom", 1000, 800)
        for current in (replace(spec, windows=(window,),
                                external_entry=ExternalEntrySpec("entry", "main", "bottom", 1000, 900)),
                        replace(spec, windows=(window, replace(window, id="second", offset_mm=1200)))):
            with self.subTest(windows=len(current.windows)):
                self.assertEqual(api.project_orthogonal_windows(current, layout).status, "FAIL")

    def test_actual_readback_rejects_displaced_missing_duplicate_or_forged_width(self):
        api = self.api()
        spec, layout = self.source()
        spec = replace(spec, windows=(WindowSpec("window", "main", "bottom", 1000, 800),))
        window = WindowOpening("main", "horizontal", 0, 600, 1400, "window")
        for supplied in ((), (window, window), (replace(window, fixed=2000),),
                         (replace(window, end=1500),), (replace(window, room_id="corner"),)):
            with self.subTest(supplied=supplied):
                self.assertEqual(api.validate_orthogonal_windows(spec, layout, supplied).status, "FAIL")

    def test_explicit_window_satisfies_declared_daylight_without_extra_automatic_window(self):
        api = self.api()
        spec, layout = self.source()
        spec = replace(spec, rooms=(replace(spec.rooms[0], needs_daylight=True), spec.rooms[1]),
                       windows=(WindowSpec("window", "main", "bottom", 1000, 800),))
        report = api.project_orthogonal_windows(spec, layout)
        self.assertEqual(report.status, "PASS", report.to_dict())
        self.assertEqual([item.id for item in report.windows], ["window"])

    def test_malformed_source_and_supplied_resource_limits_are_rejected(self):
        api = self.api()
        spec, layout = self.source()
        with self.assertRaises(ValueError):
            api.project_orthogonal_windows(replace(spec, grid_mm=True), layout)
        with self.assertRaises(ValueError):
            api.validate_orthogonal_windows(spec, layout, [WindowOpening("main", "horizontal", 0, 0, 600, "x")] * 129)

    def test_automatic_identity_cannot_collide_with_an_explicit_source_window(self):
        api = self.api()
        spec, layout = self.source()
        spec = replace(spec, rooms=(replace(spec.rooms[0], needs_daylight=True), spec.rooms[1]),
                       windows=(WindowSpec("auto-main", "corner", "right", 1000, 800),))
        report = api.project_orthogonal_windows(spec, layout)
        self.assertEqual(report.status, "FAIL", report.to_dict())
        self.assertIn("identity", str(report.to_dict()))

    def test_automatic_window_avoids_the_actual_exterior_entry_span(self):
        api = self.api()
        spec = LayoutIR.from_mapping({"boundary": {"width": 4000, "height": 3000}, "entry_room": "room",
            "rooms": [{"id": "room", "target_area": 12, "min_area": 12, "max_area": 12, "needs_daylight": True}],
            "external_entry": {"id": "entry", "room_id": "room", "side": "bottom", "offset_mm": 2000, "width_mm": 3900}})
        layout = LayoutResult(1, {"room": Rect(0, 0, 4000, 3000)})
        report = api.project_orthogonal_windows(spec, layout)
        self.assertEqual(report.status, "PASS", report.to_dict())
        self.assertEqual(report.windows[0].fixed, 3000)
        self.assertEqual(report.windows[0].orientation, "horizontal")

    def test_malformed_actual_host_orientation_and_huge_integer_have_consistent_value_errors(self):
        api = self.api()
        spec, layout = self.source()
        window = WindowOpening("main", "horizontal", 0, 600, 1400, "window")
        for values in ({"room_id": []}, {"orientation": []}, {"start": 10**400}):
            with self.subTest(field=next(iter(values))), self.assertRaises(ValueError):
                api.validate_orthogonal_windows(spec, layout, (replace(window, **values),))

    def test_total_canonical_source_bytes_are_bounded_before_geometry(self):
        api = self.api()
        spec, layout = self.source()
        baseline = len(_encoded(replace(spec, project_name="").to_dict()))
        layout_size = len(_encoded(layout.to_dict()))
        fill = 1_048_576 - baseline - layout_size // 2
        for title in ("x" * fill, "\u0100" * 524_288):
            oversized = replace(spec, project_name=title)
            with self.subTest(multibyte=title.startswith("\u0100")):
                self.assertGreater(len(_encoded(oversized.to_dict())) + layout_size, 1_048_576)
                if title.startswith("x"):
                    self.assertLess(len(_encoded(oversized.to_dict())), 1_048_576)
                with patch("layout_configurator.orthogonal_windows.validate_result_geometry",
                           side_effect=AssertionError("Expensive geometry started before the byte guard")):
                    with self.assertRaisesRegex(ValueError, "byte"):
                        api.project_orthogonal_windows(oversized, layout)

    def test_actual_total_vertices_are_bounded_before_geometry(self):
        api = self.api()
        shape = _comb()
        shapes = {f"room_{index}": shape.translated(index * 6000, 0) for index in range(17)}
        self.assertGreater(sum(len(item.vertices) for item in shapes.values()), 4096)
        spec = LayoutIR.from_mapping({"boundary": {"width": 102_000, "height": 1000},
            "entry_room": "room_0", "rooms": [{"id": key, "target_area": item.area_m2,
                "min_area": item.area_m2, "max_area": item.area_m2, "min_width": 100, "min_depth": 100}
                for key, item in shapes.items()]})
        with patch("layout_configurator.orthogonal_windows.validate_result_geometry",
                   side_effect=AssertionError("Expensive geometry started before the vertex guard")):
            with self.assertRaisesRegex(ValueError, "vert"):
                api.project_orthogonal_windows(spec, LayoutResult(1, shapes))

    def test_source_boundary_opening_and_actual_room_extents_are_bounded_before_geometry(self):
        api = self.api()
        spec, layout = self.source()
        window = WindowSpec("window", "main", "bottom", 1000, 800)
        variants = (
            (replace(spec, boundary=replace(spec.boundary, width_mm=1_000_001)), layout),
            (replace(spec, window_width_mm=1_000_001), layout),
            (replace(spec, windows=(replace(window, offset_mm=10**400),)), layout),
            (replace(spec, boundary=replace(spec.boundary, width_mm=1_000_000)),
             replace(layout, placements={**layout.placements, "corner": Rect(999_999, 0, 2, 3000)})),
            (spec, replace(layout, placements={**layout.placements,
                                             "main": Rect(10**400, 0, 200.0, 200.0)})),
        )
        for index, (current, result) in enumerate(variants):
            with self.subTest(case=index), patch("layout_configurator.orthogonal_windows.validate_result_geometry",
                    side_effect=AssertionError("Expensive geometry started before the extent guard")):
                with self.assertRaisesRegex(ValueError, "extent"):
                    api.project_orthogonal_windows(current, result)

    def test_supplied_coordinate_extent_is_rejected_instead_of_a_geometric_fail(self):
        api = self.api()
        spec, layout = self.source()
        with self.assertRaisesRegex(ValueError, "extent"):
            api.validate_orthogonal_windows(spec, layout,
                (WindowOpening("main", "horizontal", 1_000_001, 600, 1400, "window"),))

    def test_source_hashes_remain_exact_and_change_with_the_explicit_offset(self):
        api = self.api()
        spec, layout = self.source()
        spec = replace(spec, windows=(WindowSpec("window", "main", "bottom", 1000, 800),))
        report = api.project_orthogonal_windows(spec, layout)
        self.assertEqual(report.source, {"spec_sha256": hashlib.sha256(_encoded(spec.to_dict())).hexdigest(),
                                       "layout_sha256": hashlib.sha256(_encoded(layout.to_dict())).hexdigest()})
        changed = api.project_orthogonal_windows(replace(spec, windows=(replace(spec.windows[0], offset_mm=1100),)), layout)
        self.assertNotEqual(changed.source["spec_sha256"], report.source["spec_sha256"])
        self.assertEqual(changed.source["layout_sha256"], report.source["layout_sha256"])
        self.assertEqual(changed.windows[0].id, report.windows[0].id)
        self.assertNotEqual(changed.windows[0].center, report.windows[0].center)

    def test_existing_ifc_and_pdf_guards_reject_windows_without_creating_artifacts(self):
        from layout_configurator.orthogonal_ifc import export_orthogonal_draft_ifc
        from layout_configurator.orthogonal_pdf import export_orthogonal_draft_pdf
        spec, layout = self.source()
        spec = replace(spec, windows=(WindowSpec("window", "main", "bottom", 1000, 800),))
        building = BuildingIR.from_mapping({"layout": spec.to_dict()})
        with tempfile.TemporaryDirectory() as directory:
            for suffix, exporter in (("ifc", export_orthogonal_draft_ifc), ("pdf", export_orthogonal_draft_pdf)):
                target = Path(directory) / ("guarded." + suffix)
                with self.subTest(exporter=suffix), self.assertRaises(ValueError):
                    exporter(target, building, layout)
                self.assertFalse(target.exists())

    def test_explicit_and_automatic_source_openings_share_the_supplied_window_limit(self):
        api = self.api()
        spec = LayoutIR.from_mapping({"boundary": {"width": 204_000, "height": 6000}, "entry_room": "main",
            "rooms": [{"id": "main", "target_area": 1200, "min_area": 1200, "max_area": 1200},
                      {"id": "corner", "target_area": 24, "min_area": 24, "max_area": 24, "needs_daylight": True}],
            "windows": [{"id": f"explicit_{index}", "room_id": "main", "side": "bottom",
                         "offset_mm": 1000 + index * 1000, "width_mm": 800} for index in range(128)]})
        layout = LayoutResult(1, {"main": Rect(0, 0, 200_000, 6000), "corner": Rect(200_000, 0, 4000, 6000)})
        with patch("layout_configurator.orthogonal_windows.validate_result_geometry",
                   side_effect=AssertionError("Expensive geometry started for an oversized resolved window set")):
            with self.assertRaisesRegex(ValueError, "128"):
                api.project_orthogonal_windows(spec, layout)

    def test_unknown_result_room_count_cannot_launch_pairwise_geometry(self):
        api = self.api()
        spec, layout = self.source()
        layout = replace(layout, placements={f"unknown_{index}": Rect(index * 100, 0, 100, 100) for index in range(65)})
        with patch("layout_configurator.orthogonal_windows.validate_result_geometry",
                   side_effect=AssertionError("Expensive geometry started for too many actual room records")):
            with self.assertRaisesRegex(ValueError, "64"):
                api.project_orthogonal_windows(spec, layout)

    def test_invalid_source_window_host_and_daylight_types_are_rejected_before_counting(self):
        api = self.api()
        spec, layout = self.source()
        invalid = (replace(spec, windows=(WindowSpec("window", [], "bottom", 1000, 800),)),
                   replace(spec, rooms=(replace(spec.rooms[0], needs_daylight=""), spec.rooms[1])))
        for index, current in enumerate(invalid):
            with self.subTest(case=index), self.assertRaises(ValueError):
                api.project_orthogonal_windows(current, layout)


def _encoded(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")


def _comb():
    points = [(0, 0), (6000, 0), (6000, 1000)]
    for index in range(60):
        right = 6000 - index * 100
        left = right - 100
        points.extend(((right - 50, 1000), (right - 50, 500), (left, 500), (left, 1000)))
    points.pop()  # Close from the final lower tooth without retracing its edge.
    return OrthogonalPolygon(tuple(points))


if __name__ == "__main__":
    unittest.main()

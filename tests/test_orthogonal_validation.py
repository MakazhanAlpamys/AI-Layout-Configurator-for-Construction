import importlib.util
import unittest
from dataclasses import replace

from layout_configurator.building import BuildingIR
from layout_configurator.equipment import EquipmentLayoutResult, EquipmentPlacement, validate_equipment_layout
from layout_configurator.models import LayoutIR, LayoutResult, Rect, RoomSpec
from layout_configurator.orthogonal import OrthogonalPolygon


class OrthogonalValidationTests(unittest.TestCase):
    def api(self):
        self.assertIsNotNone(importlib.util.find_spec("layout_configurator.orthogonal_validation"),
                             "Independent orthogonal room/equipment predicates are missing")
        from layout_configurator import orthogonal_validation
        return orthogonal_validation

    def test_minimum_dimension_policy_has_exact_rectangle_fits_parity(self):
        api = self.api()
        for width, depth in ((3000, 2000), (2000, 3000), (1900, 3100), (3000, 3000)):
            for rotatable in (False, True):
                with self.subTest(width=width, depth=depth, rotatable=rotatable):
                    room = replace(_room(), min_width_mm=2000, min_depth_mm=3000, rotatable=rotatable)
                    rect = Rect(50, 75, width, depth)
                    for shape in (rect, OrthogonalPolygon.from_rect(rect)):
                        evidence = api.check_room_minimum_dimensions(room, shape)
                        self.assertEqual(evidence.status == "PASS", room.fits(width, depth))
                        self.assertEqual(evidence.policy, "axis_aligned_coverage_v1")

    def test_l_shape_and_small_slab_step_are_covered_by_actual_minimum_rectangles(self):
        api = self.api()
        stepped = OrthogonalPolygon(((0, 0), (6000, 0), (6000, 4000), (5800, 4000), (5800, 4200), (0, 4200)))
        for shape, room in ((_l_shape(), _room()), (stepped, replace(_room(), min_width_mm=3000, min_depth_mm=3000))):
            with self.subTest(shape=shape):
                evidence = api.check_room_minimum_dimensions(room, shape)
                self.assertEqual(evidence.status, "PASS")
                self.assertEqual(evidence.uncovered_area_mm2, 0)
                self.assertIsNotNone(evidence.selected_dimensions_mm)

    def test_large_bbox_cannot_certify_a_narrow_neck_or_thin_spur(self):
        api = self.api()
        spur = OrthogonalPolygon(((0, 0), (6000, 0), (6000, 4000), (1000, 4000), (1000, 7000), (0, 7000)))
        for shape in (_dumbbell(1000), spur):
            with self.subTest(shape=shape):
                self.assertTrue(_room().fits(shape.bounds_rect.width, shape.bounds_rect.height))
                evidence = api.check_room_minimum_dimensions(_room(), shape)
                self.assertEqual(evidence.status, "FAIL")
                self.assertGreater(evidence.uncovered_area_mm2, 0)

    def test_rotatable_policy_uses_one_global_orientation_not_different_orientations_per_arm(self):
        api = self.api()
        cross = OrthogonalPolygon(((0, 1000), (1000, 1000), (1000, 0), (2000, 0), (2000, 1000),
                                   (3000, 1000), (3000, 2000), (2000, 2000), (2000, 3000),
                                   (1000, 3000), (1000, 2000), (0, 2000)))
        room = replace(_room(), min_width_mm=3000, min_depth_mm=1000, rotatable=True)
        evidence = api.check_room_minimum_dimensions(room, cross)
        self.assertEqual(evidence.status, "FAIL")
        self.assertEqual(evidence.uncovered_area_mm2, 2_000_000)

    def test_bounded_dimension_search_reports_unknown_and_rejects_invalid_limits(self):
        api = self.api()
        evidence = api.check_room_minimum_dimensions(_room(), _l_shape(),
                                                    policy=api.OrthogonalDimensionPolicy(max_scan_steps=1))
        self.assertEqual(evidence.status, "UNKNOWN")
        self.assertIsNone(evidence.uncovered_area_mm2)
        self.assertIn("limit", evidence.message)
        self.assertLessEqual(evidence.scan_steps, 1)
        candidates = api.check_room_minimum_dimensions(_room(), _l_shape(),
            policy=api.OrthogonalDimensionPolicy(max_candidates=1))
        self.assertEqual(candidates.status, "UNKNOWN")
        self.assertLessEqual(candidates.candidate_count, 1)
        for limit in (0, True, 1.5, -1):
            with self.subTest(limit=limit):
                with self.assertRaises(ValueError):
                    api.OrthogonalDimensionPolicy(max_scan_steps=limit)

    def test_room_report_evaluates_minimums_actual_doors_and_inset_connectivity(self):
        api = self.api()
        report = api.validate_orthogonal_layout(_program(), _layout())
        self.assertTrue(report.ok, report.to_dict())
        checks = {check.id: check.status for check in report.checks}
        for key in ("MIN_DIMENSION", "ROOM_USABLE_CONNECTIVITY", "OPENINGS", "DOOR_APPROACHES", "TOPOLOGY"):
            self.assertEqual(checks[key], "PASS")
        self.assertEqual(report.to_dict()["policy"]["name"], "axis_aligned_coverage_v1")
        self.assertIn("project", report.to_dict()["policy"]["source"].lower())
        self.assertIn("not engineering", report.to_dict()["scope"])

    def test_shared_boundary_without_an_actual_door_does_not_create_graph_connectivity(self):
        api = self.api()
        spec = replace(_program(), rooms=tuple(replace(room, required_adjacency=()) for room in _program().rooms))
        report = api.validate_orthogonal_layout(spec, _layout())
        self.assertFalse(report.ok)
        topology = next(check for check in report.checks if check.id == "TOPOLOGY")
        self.assertEqual(topology.status, "FAIL")
        self.assertTrue(any("corner" in message for message in topology.evidence))

    def test_wall_inset_splits_a_bridge_even_when_source_minimum_dimensions_are_met(self):
        api = self.api()
        shape = _dumbbell(200)
        spec = _single_program(shape, minimum=100)
        self.assertEqual(api.check_room_minimum_dimensions(spec.rooms[0], shape).status, "PASS")
        self.assertEqual(len(shape.inset_components(100)), 2)

        report = api.validate_orthogonal_layout(spec, LayoutResult(1, {"main": shape}))

        check = next(check for check in report.checks if check.id == "ROOM_USABLE_CONNECTIVITY")
        self.assertEqual(check.status, "FAIL")
        self.assertTrue(any("2" in message for message in check.evidence))

    def test_door_span_must_reach_usable_room_area_at_full_width_beyond_the_wall(self):
        api = self.api()
        raw = _program().to_dict()
        raw["rooms"][1].update(target_area=.81, min_area=.81, max_area=.81, min_width=800, min_depth=800)
        layout = LayoutResult(1, {"main": _l_shape(), "corner": Rect(2000, 2000, 900, 900)})
        report = api.validate_orthogonal_layout(LayoutIR.from_mapping(raw), layout)
        self.assertEqual(next(check.status for check in report.checks if check.id == "MIN_DIMENSION"), "PASS")
        self.assertEqual(next(check.status for check in report.checks if check.id == "OPENINGS"), "PASS")
        self.assertEqual(next(check.status for check in report.checks if check.id == "DOOR_APPROACHES"), "FAIL")
        self.assertFalse(report.ok)

    def test_unsupported_window_component_and_search_limit_cannot_become_full_pass(self):
        api = self.api()
        daylight = replace(_program(), rooms=(replace(_program().rooms[0], needs_daylight=True), _program().rooms[1]))
        report = api.validate_orthogonal_layout(daylight, _layout())
        self.assertEqual(report.status, "UNKNOWN")
        self.assertFalse(report.ok)
        bounded = api.validate_orthogonal_layout(_program(), _layout(),
                                                 dimension_policy=api.OrthogonalDimensionPolicy(max_scan_steps=1))
        self.assertEqual(next(check.status for check in bounded.checks if check.id == "MIN_DIMENSION"), "UNKNOWN")
        self.assertFalse(bounded.ok)

    def test_equipment_and_directional_service_envelope_fit_actual_inset_component(self):
        api = self.api()
        building = _building([{"id": "machine", "room_id": "main", "width_mm": 500, "depth_mm": 500, "clearance_mm": 150}])
        result = EquipmentLayoutResult((EquipmentPlacement("machine", "main", Rect(500, 500, 500, 500)),))
        report = api.validate_orthogonal_equipment(building, _layout(), result)
        self.assertTrue(report.ok, report.to_dict())
        self.assertEqual(report.status, "PASS")

    def test_equipment_in_empty_bbox_corner_and_clearance_at_reentrant_corner_fail(self):
        api = self.api()
        building = _building([{"id": "machine", "room_id": "main", "width_mm": 500, "depth_mm": 400, "clearance_front_mm": 400}])
        for rect, expected in ((Rect(2500, 2500, 500, 400), "equipment_outside_room"),
                               (Rect(1700, 1200, 500, 400), "clearance_outside_room")):
            with self.subTest(rect=rect):
                report = api.validate_orthogonal_equipment(building, _layout(),
                    EquipmentLayoutResult((EquipmentPlacement("machine", "main", rect),)))
                self.assertFalse(report.ok)
                self.assertIn(expected, {issue.code for issue in report.issues})
        reentrant = api.validate_orthogonal_equipment(building, _layout(),
            EquipmentLayoutResult((EquipmentPlacement("machine", "main", Rect(1700, 1200, 500, 400)),)))
        self.assertIn("wall_collision", {issue.code for issue in reentrant.issues})
        self.assertNotIn("equipment_outside_room", {issue.code for issue in reentrant.issues})

    def test_rotation_rotates_each_directional_clearance_instead_of_using_scalar_padding(self):
        api = self.api()
        building = _building([{"id": "machine", "room_id": "main", "width_mm": 400, "depth_mm": 300,
                              "clearance_front_mm": 500, "clearance_left_mm": 100}])
        rotated = EquipmentLayoutResult((EquipmentPlacement("machine", "main", Rect(1700, 1400, 300, 400), True),))
        plain = EquipmentLayoutResult((EquipmentPlacement("machine", "main", Rect(1700, 1400, 400, 300), False),))
        self.assertTrue(api.validate_orthogonal_equipment(building, _layout(), rotated).ok)
        self.assertIn("clearance_outside_room", {issue.code for issue in api.validate_orthogonal_equipment(building, _layout(), plain).issues})

    def test_service_clearance_collision_is_reported_for_physically_disjoint_equipment(self):
        api = self.api()
        building = _building([{"id": identifier, "room_id": "main", "width_mm": 500, "depth_mm": 500, "clearance_mm": 100}
                              for identifier in ("first", "second")])
        equipment = EquipmentLayoutResult((EquipmentPlacement("first", "main", Rect(500, 500, 500, 500)),
                                           EquipmentPlacement("second", "main", Rect(1100, 500, 500, 500))))
        report = api.validate_orthogonal_equipment(building, _layout(), equipment)
        collision = next(issue for issue in report.issues if issue.code == "clearance_collision")
        self.assertEqual(collision.overlap_area_mm2, 70_000)
        self.assertEqual((collision.equipment_id, collision.related_equipment_id), ("first", "second"))
        self.assertFalse(report.ok)

    def test_equipment_identity_host_rotation_and_invalid_numeric_geometry_are_checked(self):
        api = self.api()
        building = _building([{"id": "machine", "room_id": "main", "width_mm": 500, "depth_mm": 500, "rotation_allowed": False}])
        scenarios = (((), "missing_equipment"),
                     ((EquipmentPlacement("unknown", "main", Rect(500, 500, 500, 500)),), "unknown_equipment"),
                     ((EquipmentPlacement("machine", "corner", Rect(2500, 2500, 500, 500)),), "invalid_host_room"),
                     ((EquipmentPlacement("machine", "main", Rect(500, 500, 500, 500), True),), "rotation_not_allowed"),
                     ((EquipmentPlacement("machine", "main", Rect(float("nan"), 500, 500, 500)),), "invalid_equipment_geometry"),
                     ((EquipmentPlacement("machine", "main", Rect(500, 500, 500, 500)),) * 2, "duplicate_equipment"))
        for placements, code in scenarios:
            with self.subTest(code=code):
                report = api.validate_orthogonal_equipment(building, _layout(), EquipmentLayoutResult(placements))
                self.assertFalse(report.ok)
                self.assertIn(code, {issue.code for issue in report.issues})

    def test_anchor_is_checked_on_actual_unambiguous_inset_edge_and_ambiguity_stays_unknown(self):
        api = self.api()
        building = _building([{"id": "machine", "room_id": "main", "width_mm": 500, "depth_mm": 500,
                              "anchor_side": "left", "anchor_offset_mm": 1000, "clearance_left_mm": 150}])
        equipment = EquipmentLayoutResult((EquipmentPlacement("machine", "main", Rect(250, 1100, 500, 500)),))
        self.assertTrue(api.validate_orthogonal_equipment(building, _layout(), equipment).ok)
        shifted = EquipmentLayoutResult((EquipmentPlacement("machine", "main", Rect(350, 1100, 500, 500)),))
        self.assertIn("anchor_mismatch", {issue.code for issue in api.validate_orthogonal_equipment(building, _layout(), shifted).issues})
        right = replace(building, equipment=(replace(building.equipment[0], anchor_side="right"),))
        report = api.validate_orthogonal_equipment(right, _layout(), equipment)
        self.assertEqual(report.status, "UNKNOWN")
        self.assertFalse(report.ok)
        self.assertTrue(any(check.id == "ANCHORS" and check.status == "UNKNOWN" for check in report.checks))

    def test_malformed_equipment_scalar_ids_flags_and_rectangles_produce_fail_evidence(self):
        api = self.api()
        building = _building([{"id": "machine", "room_id": "main", "width_mm": 500, "depth_mm": 500}])
        ordinary = EquipmentPlacement("machine", "main", Rect(500, 500, 500, 500))
        for placement in (replace(ordinary, equipment_id=[]), replace(ordinary, room_id=[]),
                          replace(ordinary, rotated=[]), replace(ordinary, rect=[])):
            with self.subTest(placement=placement):
                report = api.validate_orthogonal_equipment(building, _layout(), EquipmentLayoutResult((placement,)))
                self.assertEqual(report.status, "FAIL")
                self.assertIn("invalid_equipment_geometry", {issue.code for issue in report.issues})

    def test_equipment_cannot_span_components_severed_by_the_real_wall_inset(self):
        api = self.api()
        shape = _dumbbell(200)
        building = BuildingIR.from_mapping({"layout": _single_program(shape, minimum=100).to_dict(),
            "equipment": [{"id": "bridge", "room_id": "main", "width_mm": 4000, "depth_mm": 100}]})
        physical = Rect(3000, 1950, 4000, 100)
        self.assertTrue(shape.covers_rect(physical))

        report = api.validate_orthogonal_equipment(building, LayoutResult(1, {"main": shape}),
            EquipmentLayoutResult((EquipmentPlacement("bridge", "main", physical),)))

        self.assertFalse(report.ok)
        self.assertIn("equipment_outside_room", {issue.code for issue in report.issues})
        self.assertIn("wall_collision", {issue.code for issue in report.issues})

    def test_all_rectangle_anchor_offsets_and_service_sizes_match_existing_validator(self):
        api = self.api()
        rectangle = Rect(0, 0, 6000, 6000)
        source = _single_program(OrthogonalPolygon.from_rect(rectangle)).to_dict()
        for side, position in (("left", (200, 1300)), ("right", (4900, 1300)),
                                ("bottom", (1200, 300)), ("top", (1200, 5100))):
            with self.subTest(side=side):
                building = BuildingIR.from_mapping({"layout": source, "equipment": [{"id": "machine", "room_id": "main",
                    "width_mm": 600, "depth_mm": 500, "clearance_front_mm": 300, "clearance_back_mm": 200,
                    "clearance_left_mm": 100, "clearance_right_mm": 400, "anchor_side": side, "anchor_offset_mm": 1000}]})
                equipment = EquipmentLayoutResult((EquipmentPlacement("machine", "main", Rect(*position, 600, 500)),))
                layout = LayoutResult(1, {"main": rectangle})
                self.assertTrue(validate_equipment_layout(building, layout, equipment).ok)
                self.assertTrue(api.validate_orthogonal_equipment(building, layout, equipment).ok)

    def test_parent_zone_equipment_uses_source_descendant_room_membership(self):
        api = self.api()
        building = BuildingIR.from_mapping({"layout": _program().to_dict(),
            "zones": [{"id": "production", "type": "process"},
                      {"id": "line", "type": "process", "parent_zone_id": "production", "room_ids": ["main"]}],
            "equipment": [{"id": "machine", "zone_id": "production", "width_mm": 500, "depth_mm": 500}]})
        main = EquipmentLayoutResult((EquipmentPlacement("machine", "main", Rect(500, 500, 500, 500)),))
        corner = EquipmentLayoutResult((EquipmentPlacement("machine", "corner", Rect(2500, 2500, 500, 500)),))
        self.assertTrue(api.validate_orthogonal_equipment(building, _layout(), main).ok)
        self.assertIn("invalid_host_room", {issue.code for issue in api.validate_orthogonal_equipment(building, _layout(), corner).issues})

    def test_source_contract_is_strict_before_dimension_and_equipment_checks(self):
        api = self.api()
        for spec in (replace(_program(), grid_mm=True),
                     replace(_program(), rooms=(replace(_program().rooms[0], rotatable=[]), _program().rooms[1]))):
            with self.subTest(spec=spec):
                with self.assertRaises(ValueError):
                    api.validate_orthogonal_layout(spec, _layout())
        building = _building([{"id": "machine", "room_id": "main", "width_mm": 500, "depth_mm": 500}])
        invalid = replace(building, equipment=(replace(building.equipment[0], width_mm=float("nan")),))
        with self.assertRaises(ValueError):
            api.validate_orthogonal_equipment(invalid, _layout(), EquipmentLayoutResult(()))


def _room():
    return RoomSpec("main", "process", 20, 1, 100, min_width_mm=1800, min_depth_mm=1800)


def _l_shape():
    return OrthogonalPolygon(((0, 0), (6000, 0), (6000, 2000), (2000, 2000), (2000, 6000), (0, 6000)))


def _dumbbell(neck):
    low, high = 2000 - neck / 2, 2000 + neck / 2
    return OrthogonalPolygon(((0, 0), (4000, 0), (4000, low), (6000, low), (6000, 0), (10000, 0),
                              (10000, 4000), (6000, 4000), (6000, high), (4000, high), (4000, 4000), (0, 4000)))


def _single_program(shape, minimum=1800):
    return LayoutIR.from_mapping({"project_name": "Orthogonal validation fixture", "entry_room": "main",
        "boundary": {"width": shape.bounds_rect.right, "height": shape.bounds_rect.top},
        "rooms": [{"id": "main", "type": "process", "target_area": shape.area_m2,
                   "min_area": shape.area_m2, "max_area": shape.area_m2, "min_width": minimum, "min_depth": minimum}]})


def _program():
    raw = _single_program(_l_shape()).to_dict()
    raw["rooms"][0]["required_adjacency"] = ["corner"]
    raw["rooms"].append({"id": "corner", "type": "support", "target_area": 16, "min_area": 16, "max_area": 16})
    return LayoutIR.from_mapping(raw)


def _layout():
    return LayoutResult(1, {"main": _l_shape(), "corner": Rect(2000, 2000, 4000, 4000)})


def _building(equipment):
    return BuildingIR.from_mapping({"layout": _program().to_dict(), "equipment": equipment})

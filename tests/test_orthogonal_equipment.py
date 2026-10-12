import hashlib
import importlib.util
import json
import unittest
from dataclasses import replace

from layout_configurator.building import BuildingIR
from layout_configurator.equipment import EquipmentLayoutResult, EquipmentPlacement, clearance_rect, flow_portal_offset_mm
from layout_configurator.models import LayoutIR, LayoutResult, Rect
from layout_configurator.orthogonal import OrthogonalPolygon
from layout_configurator.orthogonal_validation import validate_orthogonal_equipment


class OrthogonalEquipmentPackingTests(unittest.TestCase):
    def api(self):
        self.assertIsNotNone(importlib.util.find_spec("layout_configurator.orthogonal_equipment"),
                             "Scoped polygon equipment packing is missing")
        from layout_configurator import orthogonal_equipment
        return orthogonal_equipment

    def test_free_packing_uses_both_l_arms_and_independent_source_clearance_checks(self):
        api = self.api()
        building = _building([{"id": identifier, "room_id": "main", "width_mm": 3000, "depth_mm": 800,
                               "clearance_mm": 100} for identifier in ("first", "second")])
        original = building.to_dict()
        packed = api.place_orthogonal_equipment(building, _layout(), seed=1, deterministic_units=.05)
        self.assertEqual(packed.status, "DRAFT", packed.to_dict())
        self.assertFalse(packed.ok)
        self.assertEqual(len(packed.equipment.placements), 2)
        self.assertEqual({p.rotated for p in packed.equipment.placements}, {False, True})
        self.assertTrue(validate_orthogonal_equipment(building, _layout(), packed.equipment).ok)
        self.assertTrue(api.validate_orthogonal_door_approaches(building, _layout(), packed.equipment).ok)
        self.assertEqual(building.to_dict(), original)
        self.assertEqual(packed.to_dict()["full_facility_validation"]["status"], "UNKNOWN")
        self.assertEqual(packed.to_dict()["source_sha256"], _hash(building.to_dict()))
        self.assertEqual(packed.to_dict()["layout_sha256"], _hash(_layout().to_dict()))
        self.assertEqual(len(packed.to_dict()["catalog_sha256"]), 64)

    def test_l_arm_catalog_remains_useful_after_each_quarter_turn(self):
        api = self.api()
        building = _building([{"id": identifier, "room_id": "main", "width_mm": 3000, "depth_mm": 800,
                               "clearance_mm": 100} for identifier in ("first", "second")])
        for turns in range(4):
            with self.subTest(turns=turns):
                main = _l_shape().rotated_quarter_turns(turns, origin=(3000, 3000))
                corner = OrthogonalPolygon.from_rect(Rect(2000, 2000, 4000, 4000)).rotated_quarter_turns(turns, origin=(3000, 3000))
                layout = LayoutResult(1, {"main": main, "corner": corner})
                packed = api.place_orthogonal_equipment(building, layout, seed=1, deterministic_units=.05)
                self.assertEqual(packed.status, "DRAFT", packed.to_dict()["stop_reason"])
                self.assertEqual({p.rotated for p in packed.equipment.placements}, {False, True})
                self.assertTrue(validate_orthogonal_equipment(building, layout, packed.equipment).ok)

    def test_source_generated_polygon_room_candidate_can_feed_scoped_equipment_packing(self):
        api = self.api()
        from layout_configurator.orthogonal_solver import solve_orthogonal_candidates
        building = _building([{"id": identifier, "room_id": "main", "width_mm": 3000, "depth_mm": 800,
                               "clearance_mm": 100} for identifier in ("first", "second")])
        room_candidate = solve_orthogonal_candidates(building.layout, seed=1, deterministic_units=.05)
        self.assertEqual(room_candidate.status, "DRAFT")
        layout = room_candidate.candidates[0].result
        packed = api.place_orthogonal_equipment(building, layout, seed=1, deterministic_units=.05)
        self.assertEqual(packed.status, "DRAFT", packed.to_dict()["stop_reason"])
        self.assertTrue(packed.equipment_report.ok)
        self.assertTrue(packed.door_report.ok)
        self.assertEqual(packed.to_dict()["full_facility_validation"]["status"], "UNKNOWN")

    def test_empty_bbox_corner_and_reentrant_service_envelope_are_rejected(self):
        api = self.api()
        cases = (({"id": "machine", "room_id": "main", "width_mm": 500, "depth_mm": 500},
                  Rect(2500, 2500, 500, 500), "equipment_outside_room"),
                 ({"id": "machine", "room_id": "main", "width_mm": 500, "depth_mm": 400, "clearance_front_mm": 400},
                  Rect(1700, 1200, 500, 400), "clearance_outside_room"))
        for source, physical, reason in cases:
            with self.subTest(reason=reason):
                building = _building([source])
                self.assertEqual(_l_shape().bounds_rect.intersection_area(physical), physical.area_mm2)
                packed = api.place_orthogonal_equipment(building, _layout(),
                    catalog={"machine": [EquipmentPlacement("machine", "main", physical)]}, deterministic_units=.05)
                self.assertEqual(packed.status, "UNKNOWN")
                self.assertIsNone(packed.equipment)
                self.assertIn(reason, json.dumps(packed.to_dict()["rejections"]))

    def test_square_rotation_preserves_directional_service_envelope(self):
        api = self.api()
        building = _building([{"id": "machine", "room_id": "main", "width_mm": 500, "depth_mm": 500,
                               "clearance_front_mm": 800}])
        physical = Rect(1800, 1200, 500, 500)
        supplied = {"machine": [EquipmentPlacement("machine", "main", physical, False),
                                 EquipmentPlacement("machine", "main", physical, True)]}
        packed = api.place_orthogonal_equipment(building, _layout(), catalog=supplied, deterministic_units=.05)
        self.assertEqual(packed.status, "DRAFT", packed.to_dict())
        selected = packed.equipment.placements[0]
        self.assertTrue(selected.rotated)
        self.assertEqual(clearance_rect(building.equipment[0], selected.rect, True), Rect(1000, 1200, 1300, 500))
        self.assertTrue(validate_orthogonal_equipment(building, _layout(), packed.equipment).ok)
        unrotatable = replace(building, equipment=(replace(building.equipment[0], rotation_allowed=False),))
        rejected = api.place_orthogonal_equipment(unrotatable, _layout(), catalog=supplied, deterministic_units=.05)
        self.assertEqual(rejected.status, "UNKNOWN")
        self.assertIsNone(rejected.equipment)
        self.assertIn("rotation_not_allowed", json.dumps(rejected.to_dict()["rejections"]))

    def test_generated_square_anchor_can_use_only_the_rotated_directional_envelope(self):
        api = self.api()
        building = _building([{"id": "machine", "room_id": "main", "width_mm": 500, "depth_mm": 500,
            "clearance_front_mm": 800, "anchor_side": "left", "anchor_offset_mm": 5000, "fixed": True}])
        packed = api.place_orthogonal_equipment(building, _layout(), deterministic_units=.05)
        self.assertEqual(packed.status, "DRAFT", packed.to_dict()["stop_reason"])
        self.assertTrue(packed.equipment.placements[0].rotated)
        self.assertEqual(packed.equipment.placements[0].rect, Rect(900, 5100, 500, 500))
        self.assertTrue(validate_orthogonal_equipment(building, _layout(), packed.equipment).ok)

    def test_service_footprint_may_span_multiple_exact_decomposition_slabs(self):
        api = self.api()
        building = _building([{"id": "machine", "room_id": "main", "width_mm": 1000,
                               "depth_mm": 5000, "rotation_allowed": False}])
        physical = Rect(100, 100, 1000, 5000)
        usable = _l_shape().inset_components(100)[0]
        self.assertTrue(usable.covers_rect(physical))
        self.assertFalse(any(rect.intersection_area(physical) == physical.area_mm2 for rect in usable.decompose()))
        packed = api.place_orthogonal_equipment(building, _layout(), deterministic_units=.05)
        self.assertEqual(packed.status, "DRAFT", packed.to_dict()["stop_reason"])
        self.assertEqual(packed.equipment.placements[0].rect, physical)
        self.assertTrue(validate_orthogonal_equipment(building, _layout(), packed.equipment).ok)

    def test_physically_disjoint_items_cannot_have_overlapping_service_envelopes(self):
        api = self.api()
        building = _building([{"id": identifier, "room_id": "main", "width_mm": 500, "depth_mm": 500,
                               "clearance_mm": 100} for identifier in ("first", "second")])
        first, second = Rect(500, 500, 500, 500), Rect(1100, 500, 500, 500)
        self.assertEqual(first.intersection_area(second), 0)
        packed = api.place_orthogonal_equipment(building, _layout(),
            catalog={"first": [EquipmentPlacement("first", "main", first)],
                     "second": [EquipmentPlacement("second", "main", second)]}, deterministic_units=.05)
        self.assertEqual(packed.status, "UNKNOWN")
        self.assertIsNone(packed.equipment)
        self.assertIn("service_overlap", json.dumps(packed.to_dict()["rejections"]))

    def test_real_reentrant_door_approach_is_reserved_even_without_flows(self):
        api = self.api()
        building = _building([{"id": "machine", "room_id": "main", "width_mm": 500, "depth_mm": 500}])
        blocked = EquipmentLayoutResult((EquipmentPlacement("machine", "main", Rect(1400, 3700, 500, 500)),))
        self.assertTrue(validate_orthogonal_equipment(building, _layout(), blocked).ok)
        report = api.validate_orthogonal_door_approaches(building, _layout(), blocked)
        self.assertEqual(report.status, "FAIL")
        main = next(zone for zone in report.to_dict()["reservations"] if zone["room_id"] == "main")
        self.assertEqual(main["door_id"], "auto-corner-main")
        self.assertEqual(main["rect"], {"x": 1490, "y": 3540, "width": 410, "height": 920})
        self.assertTrue(any(issue["code"] == "door_approach_collision" for issue in report.to_dict()["issues"]))
        packed = api.place_orthogonal_equipment(building, _layout(),
            catalog={"machine": list(blocked.placements)}, deterministic_units=.05)
        self.assertEqual(packed.status, "UNKNOWN")
        self.assertIsNone(packed.equipment)
        self.assertIn("door_approach_collision", json.dumps(packed.to_dict()["rejections"]))

    def test_reservation_keeps_full_required_flow_width_and_real_portal_depth(self):
        api = self.api()
        raw = _building([]).to_dict()
        raw["flows"] = [{"id": "material", "kind": "material", "from": ["main"], "to": ["corner"],
                         "minimum_clear_width_mm": 800, "required": True}]
        building = BuildingIR.from_mapping(raw)
        report = api.validate_orthogonal_door_approaches(building, _layout(), EquipmentLayoutResult(()))
        self.assertEqual(report.status, "PASS", report.to_dict())
        depth = flow_portal_offset_mm(800, building.layout.wall_thickness_mm) + 800 / 2 + 10
        main = next(zone for zone in report.to_dict()["reservations"] if zone["room_id"] == "main")
        self.assertEqual(main["rect"]["width"], depth - building.layout.wall_thickness_mm / 2)
        self.assertEqual(main["rect"]["height"], 920)
        self.assertEqual(main["rect"]["x"] + main["rect"]["width"], 1900)
        raw["flows"][0]["minimum_clear_width_mm"] = 1000
        narrow = api.validate_orthogonal_door_approaches(BuildingIR.from_mapping(raw), _layout(), EquipmentLayoutResult(()))
        self.assertEqual(narrow.status, "FAIL")
        self.assertIn("opening_below_required_flow_width", json.dumps(narrow.to_dict()["issues"]))

    def test_ambiguous_actual_anchor_stays_unknown_and_unique_anchor_keeps_exact_offset(self):
        api = self.api()
        source = {"id": "machine", "room_id": "main", "width_mm": 500, "depth_mm": 500,
                  "fixed": True, "anchor_side": "right", "anchor_offset_mm": 1000}
        ambiguous = api.place_orthogonal_equipment(_building([source]), _layout(), deterministic_units=.05)
        self.assertEqual(ambiguous.status, "UNKNOWN")
        self.assertIsNone(ambiguous.equipment)
        self.assertIn("anchor_selector_required", json.dumps(ambiguous.to_dict()["rejections"]))
        source.update(anchor_side="left", clearance_left_mm=150)
        building = _building([source])
        unique = api.place_orthogonal_equipment(building, _layout(), deterministic_units=.05)
        self.assertEqual(unique.status, "DRAFT", unique.to_dict())
        self.assertEqual(unique.equipment.placements[0].rect, Rect(250, 1100, 500, 500))
        self.assertTrue(validate_orthogonal_equipment(building, _layout(), unique.equipment).ok)

    def test_anchor_cannot_ignore_the_real_external_entry_approach(self):
        api = self.api()
        raw = _building([{"id": "machine", "room_id": "main", "width_mm": 500, "depth_mm": 500,
                          "fixed": True, "anchor_side": "left", "anchor_offset_mm": 1000}]).to_dict()
        raw["layout"]["external_entry"] = {"id": "front_entry", "room_id": "main", "side": "left",
                                             "offset_mm": 1500, "width_mm": 900}
        building = BuildingIR.from_mapping(raw)
        declared = EquipmentLayoutResult((EquipmentPlacement("machine", "main", Rect(100, 1100, 500, 500)),))
        self.assertTrue(validate_orthogonal_equipment(building, _layout(), declared).ok)
        check = api.validate_orthogonal_door_approaches(building, _layout(), declared)
        self.assertEqual(check.status, "FAIL")
        self.assertTrue(any(issue.get("door_id") == "front_entry" for issue in check.to_dict()["issues"]))
        packed = api.place_orthogonal_equipment(building, _layout(), deterministic_units=.05)
        self.assertEqual(packed.status, "UNKNOWN")
        self.assertIsNone(packed.equipment)
        self.assertIn("door_approach_collision", json.dumps(packed.to_dict()["rejections"]))

    def test_full_approach_is_not_clipped_when_actual_room_arm_is_too_shallow(self):
        api = self.api()
        raw = _building([]).to_dict()
        raw["layout"]["door_width_mm"] = 1800
        raw["flows"] = [{"id": "wide_material", "type": "material", "from_ids": ["main"], "to_ids": ["corner"],
                          "minimum_clear_width_mm": 1800, "required": True}]
        building = BuildingIR.from_mapping(raw)
        check = api.validate_orthogonal_door_approaches(building, _layout(), EquipmentLayoutResult(()))
        self.assertEqual(check.status, "FAIL")
        self.assertTrue(any(issue.get("code") == "door_approach_geometry" and issue.get("room_id") == "main"
                            for issue in check.to_dict()["issues"]))
        self.assertFalse(any(zone["room_id"] == "main" for zone in check.to_dict()["reservations"]))
        packed = api.place_orthogonal_equipment(building, _layout(), deterministic_units=.05)
        self.assertTrue(packed.room_report.ok)
        self.assertEqual(packed.status, "UNKNOWN")
        self.assertEqual(packed.to_dict()["stop_reason"], "source_door_reservations_failed")

    def test_source_room_inset_disconnect_blocks_packing_despite_equipment_fitting_one_lobe(self):
        api = self.api()
        shape = OrthogonalPolygon(((0, 0), (4000, 0), (4000, 1900), (6000, 1900), (6000, 0), (10000, 0),
            (10000, 4000), (6000, 4000), (6000, 2100), (4000, 2100), (4000, 4000), (0, 4000)))
        layout = LayoutResult(1, {"main": shape})
        building = BuildingIR.from_mapping({"layout": {"project_name": "Disconnected usable room fixture",
            "entry_room": "main", "boundary": {"width": 10000, "height": 4000},
            "rooms": [{"id": "main", "type": "process", "target_area": 32.4, "min_area": 32.4, "max_area": 32.4,
                        "min_width": 100, "min_depth": 100}]},
            "equipment": [{"id": "machine", "room_id": "main", "width_mm": 500, "depth_mm": 500}]})
        self.assertTrue(shape.covers_rect(Rect(500, 500, 500, 500), clearance_mm=100))
        self.assertEqual(len(shape.inset_components(100)), 2)
        packed = api.place_orthogonal_equipment(building, layout, deterministic_units=.05)
        self.assertEqual(packed.status, "UNKNOWN")
        self.assertIsNone(packed.equipment)
        self.assertIn("ROOM_USABLE_CONNECTIVITY", json.dumps(packed.to_dict()["rejections"]))

    def test_all_rectangle_source_anchors_keep_existing_exact_offset_semantics(self):
        api = self.api()
        for side, expected in (("left", (200, 1300)), ("right", (4900, 1300)),
                               ("bottom", (1200, 300)), ("top", (1200, 5100))):
            with self.subTest(side=side):
                building = BuildingIR.from_mapping({"layout": {"project_name": "Rectangle anchor parity fixture",
                    "entry_room": "main", "boundary": {"width": 6000, "height": 6000},
                    "rooms": [{"id": "main", "type": "process", "target_area": 36, "min_area": 36, "max_area": 36}]},
                    "equipment": [{"id": "machine", "room_id": "main", "width_mm": 600, "depth_mm": 500,
                        "clearance_front_mm": 300, "clearance_back_mm": 200, "clearance_left_mm": 100,
                        "clearance_right_mm": 400, "rotation_allowed": False, "anchor_side": side, "anchor_offset_mm": 1000}]})
                layout = LayoutResult(1, {"main": Rect(0, 0, 6000, 6000)})
                packed = api.place_orthogonal_equipment(building, layout, deterministic_units=.05)
                self.assertEqual(packed.status, "DRAFT", packed.to_dict())
                self.assertEqual(packed.equipment.placements[0].rect, Rect(*expected, 600, 500))
                self.assertTrue(validate_orthogonal_equipment(building, layout, packed.equipment).ok)

    def test_parent_zone_hosting_and_explicit_wrong_host_are_source_validated(self):
        api = self.api()
        raw = _building([]).to_dict()
        raw["zones"] = [{"id": "production", "type": "process"},
                        {"id": "line", "type": "process", "parent_zone_id": "production", "room_ids": ["main"]}]
        raw["equipment"] = [{"id": "machine", "zone_id": "production", "width_mm": 500, "depth_mm": 500}]
        building = BuildingIR.from_mapping(raw)
        valid = api.place_orthogonal_equipment(building, _layout(), deterministic_units=.05)
        self.assertEqual(valid.status, "DRAFT", valid.to_dict())
        self.assertEqual(valid.equipment.placements[0].room_id, "main")
        wrong = api.place_orthogonal_equipment(building, _layout(),
            catalog={"machine": [EquipmentPlacement("machine", "corner", Rect(2500, 2500, 500, 500))]}, deterministic_units=.05)
        self.assertEqual(wrong.status, "UNKNOWN")
        self.assertIn("invalid_host_room", json.dumps(wrong.to_dict()["rejections"]))

    def test_catalog_pair_and_geometry_limits_remain_unknown_with_negative_evidence(self):
        api = self.api()
        building = _building([{"id": identifier, "room_id": "main", "width_mm": 500, "depth_mm": 500,
                               "clearance_mm": 100} for identifier in ("first", "second")])
        supplied = {"first": [EquipmentPlacement("first", "main", Rect(500, 500, 500, 500)),
                               EquipmentPlacement("first", "main", Rect(2200, 500, 500, 500))],
                    "second": [EquipmentPlacement("second", "main", Rect(1100, 500, 500, 500))]}
        limited = api.place_orthogonal_equipment(building, _layout(), catalog=supplied,
            budget=api.OrthogonalPackingBudget(max_pair_checks=1), deterministic_units=.05)
        self.assertEqual(limited.status, "UNKNOWN")
        self.assertIsNone(limited.equipment)
        self.assertEqual(limited.to_dict()["pair_checks"], 1)
        self.assertEqual(limited.to_dict()["stop_reason"], "pair_check_limit")
        tiny = api.place_orthogonal_equipment(building, _layout(),
            budget=api.OrthogonalPackingBudget(max_catalog_steps_per_equipment=1), deterministic_units=.05)
        self.assertEqual(tiny.status, "UNKNOWN")
        self.assertTrue(tiny.to_dict()["catalog_truncated"])
        self.assertNotIn("INFEASIBLE", json.dumps(tiny.to_dict()))

    def test_order_seed_hashes_and_source_contract_are_stable_and_strict(self):
        api = self.api()
        building = _building([{"id": "machine", "room_id": "main", "width_mm": 500, "depth_mm": 500}])
        values = [EquipmentPlacement("machine", "main", Rect(500, 500, 500, 500)),
                  EquipmentPlacement("machine", "main", Rect(1200, 500, 500, 500))]
        first = api.place_orthogonal_equipment(building, _layout(), catalog={"machine": values}, seed=7, deterministic_units=.05)
        second = api.place_orthogonal_equipment(building, _layout(), catalog={"machine": list(reversed(values))}, seed=7, deterministic_units=.05)
        self.assertEqual(first.to_dict(), second.to_dict())
        changed = api.place_orthogonal_equipment(building, _layout(), catalog={"machine": values[:1]}, seed=7, deterministic_units=.05)
        self.assertNotEqual(first.to_dict()["catalog_sha256"], changed.to_dict()["catalog_sha256"])
        for catalog in ({}, [], {"unknown": values}, {"machine": [Rect(0, 0, 500, 500)]}):
            with self.subTest(catalog=catalog):
                with self.assertRaises(ValueError):
                    api.place_orthogonal_equipment(building, _layout(), catalog=catalog)
        for value in (0, True, 1.5):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    api.OrthogonalPackingBudget(max_pair_checks=value)
        with self.assertRaises(ValueError):
            api.place_orthogonal_equipment(replace(building, equipment=(replace(building.equipment[0], width_mm=float("nan")),)), _layout())

    def test_malformed_door_check_placement_container_is_a_value_error(self):
        api = self.api()
        for placements in (None, {}, 42):
            with self.subTest(placements=placements):
                with self.assertRaises(ValueError):
                    api.validate_orthogonal_door_approaches(_building([]), _layout(), EquipmentLayoutResult(placements))


def _l_shape():
    return OrthogonalPolygon(((0, 0), (6000, 0), (6000, 2000), (2000, 2000), (2000, 6000), (0, 6000)))


def _layout():
    return LayoutResult(1, {"main": _l_shape(), "corner": Rect(2000, 2000, 4000, 4000)})


def _building(equipment):
    layout = LayoutIR.from_mapping({"project_name": "Polygon equipment packing fixture", "boundary": {"width": 6000, "height": 6000},
        "entry_room": "main", "rooms": [{"id": "main", "type": "process", "target_area": 20, "min_area": 20,
        "max_area": 20, "required_adjacency": ["corner"]}, {"id": "corner", "type": "support", "target_area": 16,
        "min_area": 16, "max_area": 16}]})
    return BuildingIR.from_mapping({"layout": layout.to_dict(), "equipment": equipment})


def _hash(payload):
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, allow_nan=False,
        sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()

"""Real finite packing alternatives exclude complete combinations only."""

from dataclasses import replace
import hashlib
import inspect
import json
import unittest

from layout_configurator.equipment import EquipmentLayoutResult, EquipmentPlacement
from layout_configurator.models import Rect
from layout_configurator.orthogonal_equipment import place_orthogonal_equipment
from test_orthogonal_equipment import _building, _layout


class OrthogonalEquipmentExclusionTests(unittest.TestCase):
    def api(self):
        self.assertIn("excluded_layouts", inspect.signature(place_orthogonal_equipment).parameters,
                      "Complete-combination packing exclusions are missing")
        return place_orthogonal_equipment

    def test_exclusion_selects_a_real_alternate_packing_and_exhaustion_is_unknown(self):
        api = self.api()
        building, catalog = _alternatives()
        first = api(building, _layout(), catalog=catalog, seed=1, deterministic_units=.05)
        self.assertEqual(first.equipment.placements[0].rect, Rect(500, 500, 500, 500))
        second = api(building, _layout(), catalog=catalog, seed=1, deterministic_units=.05,
                     excluded_layouts=(first.equipment,))
        self.assertEqual(second.equipment.placements[0].rect, Rect(1200, 500, 500, 500))
        self.assertTrue(second.equipment_report.ok)
        self.assertTrue(second.door_report.ok)
        stopped = api(building, _layout(), catalog=catalog, seed=1, deterministic_units=.05,
                      excluded_layouts=(first.equipment, second.equipment))
        self.assertIsNone(stopped.equipment)
        self.assertEqual(stopped.status, "UNKNOWN")
        self.assertEqual(stopped.evidence["stop_reason"], "finite_catalog_exhausted")
        self.assertEqual(second.evidence["excluded_layouts_count"], 1)
        self.assertEqual(second.evidence["matched_excluded_layouts"], 1)
        self.assertEqual(len(second.evidence["excluded_layouts_sha256"]), 64)

    def test_empty_and_omitted_exclusions_preserve_actual_default_serialization(self):
        api = self.api()
        building, catalog = _alternatives()
        omitted = api(building, _layout(), catalog=catalog, seed=7, deterministic_units=.05).to_dict()
        for excluded in (None, (), []):
            with self.subTest(excluded=excluded):
                actual = api(building, _layout(), catalog=catalog, seed=7, deterministic_units=.05,
                             excluded_layouts=excluded).to_dict()
                self.assertEqual(json.dumps(actual, sort_keys=True, separators=(",", ":")),
                                 json.dumps(omitted, sort_keys=True, separators=(",", ":")))
                self.assertNotIn("excluded_layouts_sha256", actual)

    def test_complete_combination_does_not_remove_other_packings_that_reuse_one_item(self):
        api = self.api()
        building = _building([{"id": name, "room_id": "main", "width_mm": 300, "depth_mm": 300}
                             for name in ("first", "second")])
        catalog = {"first": [EquipmentPlacement("first", "main", Rect(500, 500, 300, 300))],
                   "second": [EquipmentPlacement("second", "main", Rect(x, 500, 300, 300)) for x in (1000, 1500)]}
        first = api(building, _layout(), catalog=catalog, deterministic_units=.05)
        second = api(building, _layout(), catalog=catalog, deterministic_units=.05, excluded_layouts=(first.equipment,))
        self.assertIsNotNone(second.equipment)
        self.assertEqual(first.equipment.placements[0], second.equipment.placements[0])
        self.assertNotEqual(first.equipment.placements[1], second.equipment.placements[1])

    def test_invalid_or_partial_exclusions_are_rejected_and_inputs_are_unchanged(self):
        api = self.api()
        building, catalog = _alternatives()
        first = api(building, _layout(), catalog=catalog, deterministic_units=.05).equipment
        before = building.to_dict(), first.to_dict(building)
        invalid = (42, {}, [first] * 129, [EquipmentLayoutResult(())],
            [EquipmentLayoutResult((first.placements[0], first.placements[0]))],
            [EquipmentLayoutResult((replace(first.placements[0], equipment_id="unknown"),))],
            [EquipmentLayoutResult((replace(first.placements[0], room_id="corner"),))],
            [EquipmentLayoutResult((replace(first.placements[0], rect=Rect(3000, 3000, 500, 500)),))])
        for value in invalid:
            with self.subTest(value=str(value)[:80]), self.assertRaises(ValueError):
                api(building, _layout(), catalog=catalog, deterministic_units=.05, excluded_layouts=value)
        self.assertEqual((building.to_dict(), first.to_dict(building)), before)

    def test_exclusion_order_duplicates_and_stale_absent_domain_are_canonical(self):
        api = self.api()
        building, catalog = _alternatives()
        first = api(building, _layout(), catalog=catalog, deterministic_units=.05).equipment
        second = api(building, _layout(), catalog=catalog, deterministic_units=.05, excluded_layouts=(first,)).equipment
        normal = api(building, _layout(), catalog=catalog, deterministic_units=.05, excluded_layouts=(first, second)).to_dict()
        duplicate = api(building, _layout(), catalog=catalog, deterministic_units=.05, excluded_layouts=(second, first, first)).to_dict()
        self.assertEqual(normal, duplicate)
        outside_catalog = EquipmentLayoutResult((EquipmentPlacement("machine", "main", Rect(500, 1200, 500, 500)),))
        unchanged = api(building, _layout(), catalog=catalog, deterministic_units=.05, excluded_layouts=(outside_catalog,))
        self.assertEqual(unchanged.equipment, first)
        self.assertEqual(unchanged.evidence["matched_excluded_layouts"], 0)


def _alternatives():
    building = _building([{"id": "machine", "room_id": "main", "width_mm": 500, "depth_mm": 500,
                          "rotation_allowed": False}])
    return building, {"machine": [EquipmentPlacement("machine", "main", Rect(x, 500, 500, 500)) for x in (500, 1200)]}

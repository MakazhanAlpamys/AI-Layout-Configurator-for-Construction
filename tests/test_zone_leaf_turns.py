"""Legal leaf diversity preserves source constraints and old finite choices."""

from dataclasses import replace
import hashlib
import importlib
import json
import unittest
from unittest.mock import patch

from layout_configurator.building import BuildingIR
from layout_configurator.models import BoundarySpec, LayoutResult, Rect
from layout_configurator.zone_hierarchy import (
    _ZoneShape, ZoneHierarchyFailure, ZoneHierarchyNotApplicable, solve_zone_layouts, validate_zone_layout,
)
from layout_configurator.zone_hints import ZoneHints


def program():
    return BuildingIR.from_mapping({"layout": {
        "boundary": {"width": 110000, "height": 42500}, "grid_mm": 100,
        "door_width_mm": 2000, "wall_thickness_mm": 200, "entry_room": "office",
        "rooms": [
            {"id": "changing", "target_area": 36, "min_area": 30, "max_area": 45,
             "min_width": 4000, "min_depth": 5000, "required_adjacency": ["corridor"]},
            {"id": "corridor", "target_area": 54, "min_area": 45, "max_area": 70,
             "min_width": 2400, "min_depth": 2400,
             "required_adjacency": ["office", "clean-port", "support-port"]},
            {"id": "office", "target_area": 32, "min_area": 25, "max_area": 40,
             "min_width": 4000, "min_depth": 5000, "needs_daylight": True},
            {"id": "clean-port", "target_area": 30, "min_area": 24, "max_area": 40,
             "min_width": 4000, "min_depth": 5000},
            {"id": "support-port", "target_area": 49.2, "min_area": 49.2, "max_area": 49.2,
             "min_width": 2400, "min_depth": 2400}],
    }, "zones": [{"id": "staff", "room_ids": ["changing", "corridor", "office"]},
                 {"id": "clean", "room_ids": ["clean-port"]},
                 {"id": "support", "room_ids": ["support-port"]}]})


def units_and_shapes():
    units = {"staff": ("changing", "corridor", "office"), "clean": ("clean-port",),
             "support": ("support-port",)}
    shapes = {
        "staff": [_ZoneShape(11100, 11000, {
            "changing": Rect(4900, 5200, 6200, 5800),
            "corridor": Rect(0, 0, 4900, 11000), "office": Rect(4900, 0, 6200, 5200)})],
        "clean": [_ZoneShape(26000, 26000, {"clean-port": Rect(0, 5000, 4000, 7500)}),
                  _ZoneShape(26000, 26000, {"clean-port": Rect(22000, 5000, 4000, 7500)})],
        "support": [_ZoneShape(66600, 20100, {"support-port": Rect(46100, 7200, 20500, 2400)})],
    }
    return units, shapes


def source_hash(building):
    return hashlib.sha256(json.dumps(building.to_dict(), sort_keys=True, ensure_ascii=False,
        separators=(",", ":"), allow_nan=False).encode()).hexdigest()


class ZoneLeafTurnTests(unittest.TestCase):
    def api(self):
        self.assertIsNotNone(importlib.util.find_spec("layout_configurator.zone_leaf_turns"),
            "The bounded selective leaf-turn planner is missing")
        return importlib.import_module("layout_configurator.zone_leaf_turns")

    def planned(self, building=None, shapes=None, **options):
        units, original = units_and_shapes()
        return self.api().plan_selective_leaf_turns(building or program(), units, shapes or original,
            minimum_adjacency_mm=2400, **options)

    def test_one_grid_turn_uses_swapped_envelope_and_preserves_source_and_original_order(self):
        building = program()
        units, shapes = units_and_shapes()
        original_hash = source_hash(building)
        local = replace(building.layout, boundary=BoundarySpec(11100, 11000))
        legacy = ZoneHints(building, {}).leaf_orientations(local, units["staff"], {}, 2400,
            lambda _: (), "staff", solved=shapes["staff"][0].placements)
        self.assertFalse(any(max(r.right for r in p.values()) == 11000 and
            max(r.top for r in p.values()) == 11100 for p in legacy))
        augmented, report = self.planned(building, shapes)
        self.assertEqual(report["status"], "APPLIED")
        self.assertEqual(report["counts"]["added_shapes"], 4)
        self.assertEqual(len(augmented["staff"]), 5)
        self.assertIs(augmented["staff"][0], shapes["staff"][0])
        self.assertEqual(len(shapes["staff"]), 1)
        self.assertEqual(list(augmented), list(shapes))
        self.assertEqual(source_hash(building), original_hash)
        for shape in augmented["staff"][1:]:
            self.assertEqual((shape.width, shape.height), (11000, 11100))
            for room_id, rect in shape.placements.items():
                self.assertTrue(building.layout.room_by_id[room_id].fits(rect.width, rect.height))
                self.assertFalse(building.layout.room_by_id[room_id].rotatable)
        self.assertFalse(report["original_program_infeasibility_proven"])

    def test_one_joint_three_unit_actual_contact_witness_and_daylight_remain_valid(self):
        building = program()
        augmented, _ = self.planned(building)
        staff = next(shape for shape in augmented["staff"][1:]
            if shape.placements["corridor"].y == 6200 and shape.placements["office"].x == 0)
        origins = {"support": (0, 0), "staff": (66600, 0), "clean": (77600, 0)}
        placements, containers = {}, {}
        for unit_id, (x, y) in origins.items():
            shape = staff if unit_id == "staff" else augmented[unit_id][0]
            containers[unit_id] = Rect(x, y, shape.width, shape.height)
            placements.update({room_id: Rect(x + r.x, y + r.y, r.width, r.height)
                for room_id, r in shape.placements.items()})
        self.assertEqual(placements["corridor"].shared_boundary(placements["clean-port"]), 4900)
        self.assertEqual(placements["corridor"].shared_boundary(placements["support-port"]), 2400)
        self.assertEqual(placements["office"].y, 0)
        report = validate_zone_layout(building, LayoutResult(1, placements), containers,
            minimum_adjacency_mm=2400)
        self.assertTrue(report.ok, report.issues)

    def test_explicit_pools_and_canonical_parents_are_excluded(self):
        augmented, report = self.planned(explicit_unit_ids=("staff",))
        self.assertEqual(len(augmented["staff"]), 1)
        self.assertEqual(report["units"][0]["reason"], "explicit_size_options_preserved")
        raw = program().to_dict()
        raw["zones"][0]["room_ids"].remove("office")
        raw["zones"].append({"id": "child", "parent_zone_id": "staff", "room_ids": ["office"]})
        augmented, report = self.planned(BuildingIR.from_mapping(raw))
        self.assertEqual(len(augmented["staff"]), 1)
        self.assertEqual(report["units"][0]["reason"], "canonical_parent_unit")

    def test_same_room_needs_two_distinct_neighbors_not_two_other_interface_rooms(self):
        raw = program().to_dict()
        corridor = next(x for x in raw["layout"]["rooms"] if x["id"] == "corridor")
        corridor["required_adjacency"].remove("clean-port")
        next(x for x in raw["layout"]["rooms"] if x["id"] == "office")["required_adjacency"] = ["clean-port"]
        augmented, _ = self.planned(BuildingIR.from_mapping(raw))
        self.assertEqual(len(augmented["staff"]), 1)
        raw = program().to_dict()
        raw["zones"] = [raw["zones"][0], {"id": "neighbors", "room_ids": ["clean-port", "support-port"]}]
        units, shapes = units_and_shapes()
        merged = {"staff": units["staff"], "neighbors": ("clean-port", "support-port")}
        augmented, _ = self.api().plan_selective_leaf_turns(BuildingIR.from_mapping(raw), merged,
            {"staff": shapes["staff"], "neighbors": []}, minimum_adjacency_mm=2400)
        self.assertEqual(len(augmented["staff"]), 1)

    def test_anchor_equipment_and_opening_units_are_conservatively_excluded(self):
        for kind in ("fixed", "axis", "equipment", "zone-equipment", "window", "entry", "door"):
            with self.subTest(kind=kind):
                building, options = program(), {}
                raw = building.to_dict()
                if kind == "fixed":
                    options["fixed_rects"] = {"office": Rect(0, 0, 6200, 5200)}
                elif kind == "axis":
                    options["axis_aligned_room_ids"] = ("office",)
                elif kind.endswith("equipment"):
                    raw["equipment"] = [{"id": "machine", "width_mm": 200, "depth_mm": 200,
                        **({"room_id": "office"} if kind == "equipment" else {"zone_id": "staff"})}]
                elif kind == "window":
                    raw["layout"]["windows"] = [{"id": "w", "room_id": "office", "side": "bottom",
                        "offset_mm": 3000, "width_mm": 1800}]
                elif kind == "entry":
                    raw["layout"]["external_entry"] = {"id": "entry", "room_id": "office", "side": "bottom",
                        "offset_mm": 3000, "width_mm": 2000}
                else:
                    raw["layout"]["doors"] = [{"id": "d", "room_a": "office", "room_b": "corridor",
                        "offset_mm": 3000, "width_mm": 2000}]
                augmented, _ = self.planned(BuildingIR.from_mapping(raw), **options)
                self.assertEqual(len(augmented["staff"]), 1)

    def test_actual_geometry_deduplication_and_four_turn_bound(self):
        units, shapes = units_and_shapes()
        shapes["staff"] *= 12
        augmented, report = self.planned(shapes=shapes)
        self.assertEqual(len(augmented["staff"]), 16)
        self.assertEqual(report["counts"]["added_shapes"], 4)
        second, repeat = self.planned(shapes=augmented)
        self.assertEqual(second, augmented)
        self.assertEqual(repeat["counts"]["added_shapes"], 0)

    def test_source_minima_global_envelope_and_existing_support_limits_do_not_widen(self):
        raw = program().to_dict()
        next(x for x in raw["layout"]["rooms"] if x["id"] == "changing")["min_width"] = 6000
        building = BuildingIR.from_mapping(raw)
        self.assertTrue(building.layout.room_by_id["changing"].fits(6200, 5800))
        self.assertFalse(building.layout.room_by_id["changing"].fits(5800, 6200))
        augmented, _ = self.planned(building)
        self.assertEqual(len(augmented["staff"]), 1)
        raw = program().to_dict()
        raw["layout"]["boundary"]["height"] = 11000
        augmented, _ = self.planned(BuildingIR.from_mapping(raw))
        self.assertEqual(len(augmented["staff"]), 1)
        api = self.api()
        with patch.object(api, "MAX_SUPPORT_ROWS", 16):
            augmented, report = self.planned()
        self.assertEqual(report["status"], "SKIPPED_LIMIT")
        self.assertEqual(report["counts"]["added_shapes"], 0)
        self.assertEqual(len(augmented["staff"]), 1)

    def test_real_final_cp_accepts_added_choices_after_the_old_family_is_infeasible(self):
        import layout_configurator.zone_hierarchy as hierarchy

        building = program()
        _, shapes = units_and_shapes()
        source = source_hash(building)
        options = {"minimum_adjacency_mm": 2400, "deterministic_units": .1,
                   "global_diagnostics": True, "cross_root_contact_support": True}
        old, opted = {}, {}
        # The fixture supplies actual independently checked local choices;
        # both final CP models, source clauses and accepted QA remain real.
        with patch.object(hierarchy, "_local_shapes", return_value=shapes):
            with self.assertRaises(ZoneHierarchyFailure):
                solve_zone_layouts(building, 1, 1, 1, evidence=old, **options)
            result = solve_zone_layouts(building, 1, 1, 1, evidence=opted,
                selective_leaf_turns=True, **options)[0]
        self.assertEqual(old["failure"]["model_status"], "INFEASIBLE")
        self.assertEqual(opted["selective_leaf_turns"]["counts"]["added_shapes"], 4)
        self.assertEqual(opted["cross_root_contact_support"]["status"], "APPLIED")
        containers = {k: Rect(**v) for k, v in opted["variants"][0]["zone_rects"].items()}
        actual = validate_zone_layout(building, result, containers, minimum_adjacency_mm=2400)
        self.assertTrue(actual.ok, actual.issues)
        self.assertEqual(source_hash(building), source)
        self.assertEqual(old["global_diagnostics"]["source_sha256"],
                         opted["global_diagnostics"]["source_sha256"])
        self.assertFalse(opted["selective_leaf_turns"]["original_program_infeasibility_proven"])

    def test_public_flag_is_strict_and_false_keeps_existing_evidence_bytes(self):
        import layout_configurator.zone_hierarchy as hierarchy

        for value in (1, 0, None, "true"):
            with self.subTest(value=value), patch.object(hierarchy, "_local_shapes") as local:
                evidence = {}
                with self.assertRaises(ZoneHierarchyNotApplicable):
                    solve_zone_layouts(program(), evidence=evidence, selective_leaf_turns=value)
                local.assert_not_called()
                self.assertEqual(evidence["failure"]["model_status"], "NOT_BUILT")
        from test_zone_hierarchy import _two_zones
        building = _two_zones()
        options = {"zone_size_options_mm": {"left": [(4000, 2000)], "right": [(4000, 2000)]},
                   "deterministic_units": .05}
        baseline, explicit = {}, {}
        first = solve_zone_layouts(building, 1, .5, 1, evidence=baseline, **options)
        second = solve_zone_layouts(building, 1, .5, 1, evidence=explicit,
            selective_leaf_turns=False, **options)
        self.assertEqual([x.to_dict() for x in first], [x.to_dict() for x in second])
        self.assertEqual(json.dumps(baseline, sort_keys=True), json.dumps(explicit, sort_keys=True))
        self.assertNotIn("selective_leaf_turns", explicit)

    def test_malformed_shape_coordinates_and_duplicate_membership_never_add_geometry(self):
        units, shapes = units_and_shapes()
        for value in (True, float("nan"), 10 ** 400):
            with self.subTest(value=str(value)[:40]):
                bad = {**shapes, "staff": [replace(shapes["staff"][0], placements={
                    **shapes["staff"][0].placements, "office": Rect(value, 0, 6200, 5200)})]}
                augmented, report = self.planned(shapes=bad)
                self.assertEqual(len(augmented["staff"]), 1)
                self.assertEqual(report["counts"]["added_shapes"], 0)
        duplicated = {**units, "duplicate": ("office",)}
        augmented, report = self.api().plan_selective_leaf_turns(program(), duplicated,
            {**shapes, "duplicate": []}, minimum_adjacency_mm=2400)
        self.assertEqual(report["status"], "SKIPPED_UNSAFE")
        self.assertEqual(report["counts"]["added_shapes"], 0)


if __name__ == "__main__":
    unittest.main()

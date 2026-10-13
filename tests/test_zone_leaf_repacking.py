"""Source-derived leaf packing adds bounded choices to the exact final model."""

from dataclasses import replace
import importlib
import json
import unittest
from unittest.mock import patch

from layout_configurator.building import BuildingIR
from layout_configurator.models import BoundarySpec, Rect
from layout_configurator.shape_cache import _valid_shape, cluster_shape_request
from layout_configurator.zone_hierarchy import (
    _ZoneShape, ZoneHierarchyFailure, ZoneHierarchyNotApplicable,
    solve_zone_layouts, validate_zone_layout,
)
from test_zone_leaf_turns import program, units_and_shapes, source_hash


def four_root_program():
    raw = program().to_dict()
    rooms = raw["layout"]["rooms"]
    next(r for r in rooms if r["id"] == "clean-port")["required_adjacency"] = ["material-port"]
    for room_id, width, depth, neighbors in (
        ("material-port", 6000, 5000, ["raw-store"]),
        ("pack-port", 9600, 12500, ["finished-store"]),
        ("receiving", 8000, 6000, ["raw-store"]),
        ("raw-store", 7800, 9200, ["finished-store"]),
        ("finished-store", 7500, 9200, []),
    ):
        area = width * depth / 1e6
        rooms.append({"id": room_id, "target_area": area, "min_area": area,
            "max_area": area, "min_width": width, "min_depth": depth,
            "required_adjacency": neighbors})
    raw["zones"][1]["room_ids"] += ["material-port", "pack-port"]
    raw["zones"].append({"id": "logistics", "room_ids": ["receiving", "raw-store", "finished-store"]})
    return BuildingIR.from_mapping(raw)


def reflected(shape):
    return [replace(shape, placements={k: Rect(shape.width - r.right if mx else r.x,
        shape.height - r.top if my else r.y, r.width, r.height)
        for k, r in shape.placements.items()})
        for mx, my in ((False, False), (False, True), (True, False), (True, True))]


def four_root_shapes():
    units, shapes = units_and_shapes()
    units["clean"] = ("clean-port", "material-port", "pack-port")
    units["logistics"] = ("receiving", "raw-store", "finished-store")
    shapes["staff"] = reflected(shapes["staff"][0])
    # These old quarter-turns are already checked against the original per-axis
    # room minima. The new policy must fix a failure after those turns exist.
    from layout_configurator.zone_leaf_turns import plan_selective_leaf_turns
    clean = _ZoneShape(26000, 26000, {"material-port": Rect(0, 0, 6000, 5000),
        "clean-port": Rect(0, 5000, 4000, 7500), "pack-port": Rect(16000, 0, 9600, 12500)})
    shapes["clean"] = reflected(clean)
    shapes["support"] = reflected(shapes["support"][0])
    logistics = _ZoneShape(15300, 15200, {"receiving": Rect(300, 0, 8000, 6000),
        "raw-store": Rect(0, 6000, 7800, 9200), "finished-store": Rect(7800, 6000, 7500, 9200)})
    shapes["logistics"] = reflected(logistics)
    return units, plan_selective_leaf_turns(four_root_program(), units, shapes,
        minimum_adjacency_mm=2400)[0]


class ZoneLeafRepackingTests(unittest.TestCase):
    def api(self):
        self.assertIsNotNone(importlib.util.find_spec("layout_configurator.zone_leaf_repacking"),
            "The source-derived bounded leaf packing planner is missing")
        return importlib.import_module("layout_configurator.zone_leaf_repacking")

    def plan(self, building=None, **kwargs):
        units, shapes = units_and_shapes()
        return self.api().plan_selective_leaf_repacking(building or program(), units, shapes,
            minimum_adjacency_mm=2400, **kwargs)

    def test_source_depth_intervals_derive_two_checked_stacks_and_preserve_old_choices(self):
        building = program()
        units, original = units_and_shapes()
        before = source_hash(building)
        result, report = self.plan(building)
        self.assertEqual(report["status"], "APPLIED")
        self.assertEqual(report["counts"]["added_shapes"], 2)
        self.assertEqual(result["staff"][0], original["staff"][0])
        self.assertEqual(len(original["staff"]), 1)
        for shape in result["staff"][1:]:
            self.assertEqual((shape.width, shape.height), (4000, 32800))
            self.assertEqual(shape.placements["office"].height, 6300)
            self.assertEqual(shape.placements["corridor"].height, 17500)
            self.assertEqual(shape.placements["changing"].height, 9000)
            local = replace(building.layout, boundary=BoundarySpec(shape.width, shape.height))
            request = cluster_shape_request(local, units["staff"], side_contacts={}, fit_options={},
                adjacency_mm=2400, time_limit_seconds=1, seed=0, deterministic_units=None,
                aspects=(), fills=(), max_shapes=1)
            self.assertTrue(_valid_shape(request, shape.placements))
        self.assertEqual(source_hash(building), before)
        self.assertFalse(report["original_program_infeasibility_proven"])
        self.assertFalse(report["positions_are_constraints"])
        self.assertEqual(report["policy"], "source_bounded_three_room_interface_stack_v1")

    def test_actual_four_root_contact_daylight_model_rejects_old_and_accepts_source_repacking(self):
        import layout_configurator.zone_hierarchy as hierarchy

        building = four_root_program()
        units, shapes = four_root_shapes()
        options = {"minimum_adjacency_mm": 2400, "deterministic_units": .2,
            "global_diagnostics": True, "cross_root_contact_support": True}
        old, opted = {}, {}
        with patch.object(hierarchy, "_local_shapes", return_value=shapes):
            with self.assertRaises(ZoneHierarchyFailure):
                solve_zone_layouts(building, 1, 1, 1, evidence=old, **options)
            actual = solve_zone_layouts(building, 1, 1, 1, evidence=opted,
                selective_leaf_repacking=True, **options)[0]
        self.assertEqual(old["failure"]["model_status"], "INFEASIBLE")
        self.assertEqual(opted["selective_leaf_repacking"]["counts"]["added_shapes"], 2)
        self.assertEqual(opted["cross_root_contact_support"]["status"], "APPLIED")
        containers = {k: Rect(**v) for k, v in opted["variants"][0]["zone_rects"].items()}
        checked = validate_zone_layout(building, actual, containers, minimum_adjacency_mm=2400)
        self.assertTrue(checked.ok, checked.issues)
        self.assertEqual(set(actual.placements), set(building.layout.room_by_id))
        office = actual.placements["office"]
        boundary = building.layout.boundary
        self.assertTrue(office.y == 0 or office.top == boundary.height_mm)
        self.assertEqual(old["global_diagnostics"]["source_sha256"],
            opted["global_diagnostics"]["source_sha256"])

    def test_policy_is_id_independent_and_uses_original_per_axis_minima(self):
        raw = program().to_dict()
        mapping = {r["id"]: "room-" + str(i) for i, r in enumerate(raw["layout"]["rooms"])}
        for room in raw["layout"]["rooms"]:
            room["id"] = mapping[room["id"]]
            for name in ("required_adjacency", "preferred_adjacency", "forbidden_adjacency"):
                room[name] = [mapping[k] for k in room.get(name, [])]
        raw["layout"]["entry_room"] = mapping[raw["layout"]["entry_room"]]
        for zone in raw["zones"]:
            zone["room_ids"] = [mapping[k] for k in zone["room_ids"]]
        building = BuildingIR.from_mapping(raw)
        units, shapes = units_and_shapes()
        renamed_units = {k: tuple(mapping[x] for x in members) for k, members in units.items()}
        renamed_shapes = {k: [replace(s, placements={mapping[x]: r for x, r in s.placements.items()})
            for s in values] for k, values in shapes.items()}
        result, report = self.api().plan_selective_leaf_repacking(building, renamed_units,
            renamed_shapes, minimum_adjacency_mm=2400)
        self.assertEqual(report["counts"]["added_shapes"], 2)
        self.assertEqual(result["staff"][-1].width, 4000)
        changed = replace(building, layout=replace(building.layout, rooms=tuple(
            replace(r, min_width_mm=6000) if r.id == mapping["changing"] else r
            for r in building.layout.rooms)))
        result, _ = self.api().plan_selective_leaf_repacking(changed, renamed_units,
            renamed_shapes, minimum_adjacency_mm=2400)
        self.assertTrue(all(s.width >= 6000 for s in result["staff"][1:]))

    def test_existing_anchor_pool_parent_and_equipment_exclusions_remain(self):
        for kind in ("pool", "parent", "fixed", "axis", "equipment", "zone-equipment", "window", "entry", "door"):
            with self.subTest(kind=kind):
                raw, options = program().to_dict(), {}
                if kind == "pool":
                    options["explicit_unit_ids"] = ("staff",)
                elif kind == "parent":
                    raw["zones"][0]["room_ids"].remove("office")
                    raw["zones"].append({"id": "child", "parent_zone_id": "staff", "room_ids": ["office"]})
                elif kind == "fixed":
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
                    raw["layout"]["external_entry"] = {"id": "e", "room_id": "office", "side": "bottom",
                        "offset_mm": 3000, "width_mm": 2000}
                else:
                    raw["layout"]["doors"] = [{"id": "d", "room_a": "office", "room_b": "corridor",
                        "offset_mm": 3000, "width_mm": 2000}]
                result, report = self.plan(BuildingIR.from_mapping(raw), **options)
                self.assertEqual(report["counts"]["added_shapes"], 0)
                self.assertEqual(len(result["staff"]), 1)

    def test_eligibility_is_a_three_room_star_and_never_moves_a_daylight_interface(self):
        for kind in ("interface-daylight", "two-daylight-ends", "missing-internal-contact", "one-external-unit"):
            with self.subTest(kind=kind):
                raw = program().to_dict()
                by_id = {r["id"]: r for r in raw["layout"]["rooms"]}
                if kind == "interface-daylight":
                    by_id["corridor"]["needs_daylight"] = True
                elif kind == "two-daylight-ends":
                    by_id["changing"]["needs_daylight"] = True
                elif kind == "missing-internal-contact":
                    by_id["changing"]["required_adjacency"] = []
                else:
                    by_id["corridor"]["required_adjacency"].remove("clean-port")
                _, report = self.plan(BuildingIR.from_mapping(raw))
                self.assertEqual(report["counts"]["added_shapes"], 0)
        raw = program().to_dict()
        raw["zones"] = [raw["zones"][0], {"id": "one-neighbor", "room_ids": ["clean-port", "support-port"]}]
        units, shapes = units_and_shapes()
        _, report = self.api().plan_selective_leaf_repacking(BuildingIR.from_mapping(raw),
            {"staff": units["staff"], "one-neighbor": ("clean-port", "support-port")},
            {"staff": shapes["staff"], "one-neighbor": []}, minimum_adjacency_mm=2400)
        self.assertEqual(report["counts"]["added_shapes"], 0)

    def test_deduplication_budget_overflow_and_global_extent_do_not_widen(self):
        units, shapes = units_and_shapes()
        result, report = self.api().plan_selective_leaf_repacking(program(), units, shapes,
            minimum_adjacency_mm=2400)
        self.assertIs(result["staff"][0], shapes["staff"][0])
        repeated, checked = self.api().plan_selective_leaf_repacking(program(), units, result,
            minimum_adjacency_mm=2400)
        self.assertEqual(repeated, result)
        self.assertEqual(checked["counts"]["added_shapes"], 0)
        with patch.object(self.api(), "MAX_SUPPORT_ROWS", 16):
            rejected, limited = self.plan()
        self.assertEqual(limited["status"], "SKIPPED_LIMIT")
        self.assertEqual(limited["counts"]["added_shapes"], 0)
        self.assertEqual(len(rejected["staff"]), 1)
        raw = program().to_dict()
        raw["layout"]["boundary"]["height"] = 32000
        _, report = self.plan(BuildingIR.from_mapping(raw))
        self.assertEqual(report["counts"]["added_shapes"], 0)

    def test_each_existing_family_budget_rejects_all_proposed_additions(self):
        api = self.api()
        _, baseline = self.plan()
        limits = {"source_rooms": "MAX_SOURCE_ROOMS", "unit_pairs": "MAX_UNIT_PAIRS",
            "candidate_pairs": "MAX_CANDIDATE_PAIRS", "orientation_contact_checks": "MAX_ORIENTATION_CONTACT_CHECKS",
            "participating_rectangle_records": "MAX_PARTICIPATING_RECTANGLE_RECORDS",
            "support_row_upper_bound": "MAX_SUPPORT_ROWS", "candidate_shapes": "MAX_CANDIDATE_SHAPES",
            "rectangle_records": "MAX_RECTANGLE_RECORDS"}
        for name, constant in limits.items():
            with self.subTest(limit=name), patch.object(api, constant, baseline["counts"][name] - 1):
                result, report = self.plan()
                self.assertEqual(report["status"], "SKIPPED_LIMIT")
                self.assertEqual(report["counts"]["added_shapes"], 0)
                self.assertEqual(len(result["staff"]), 1)

    def test_grid_depth_intervals_and_malformed_original_source_are_checked(self):
        building = program()
        original = building.layout.room_by_id["office"]
        changed = replace(building, layout=replace(building.layout, rooms=tuple(
            replace(r, min_width_mm=4101) if r.id == "office" else r for r in building.layout.rooms)))
        result, report = self.plan(changed)
        self.assertEqual(report["counts"]["added_shapes"], 2)
        self.assertEqual(result["staff"][1].width, 4200)
        self.assertEqual(result["staff"][1].placements["office"].height, 6000)
        for value in (True, float("nan"), float("inf"), 10 ** 400):
            for field in ("min_width_mm", "min_depth_mm", "max_area_m2", "target_area_m2"):
                with self.subTest(field=field, value=str(value)[:30]):
                    changed = replace(building, layout=replace(building.layout, rooms=tuple(
                        replace(r, **{field: value}) if r.id == original.id else r for r in building.layout.rooms)))
                    result, report = self.plan(changed)
                    self.assertEqual(report["counts"]["added_shapes"], 0)
                    self.assertEqual(len(result["staff"]), 1)
        units, shapes = units_and_shapes()
        result, report = self.api().plan_selective_leaf_repacking(building,
            {**units, "duplicate": ("office",)}, {**shapes, "duplicate": []}, minimum_adjacency_mm=2400)
        self.assertEqual(report["status"], "SKIPPED_UNSAFE")
        self.assertEqual(report["counts"]["added_shapes"], 0)

    def test_nonfinite_original_boundary_or_nonboolean_rotation_never_adds_geometry(self):
        building = program()
        for value in (True, float("nan"), float("inf"), 10 ** 400):
            with self.subTest(boundary=str(value)[:30]):
                bad = replace(building, layout=replace(building.layout,
                    boundary=BoundarySpec(value, 42500)))
                _, report = self.plan(bad)
                self.assertEqual(report["counts"]["added_shapes"], 0)
        for value in (1, "false", None):
            with self.subTest(rotatable=value):
                bad = replace(building, layout=replace(building.layout, rooms=tuple(
                    replace(r, rotatable=value) if r.id == "changing" else r for r in building.layout.rooms)))
                _, report = self.plan(bad)
                self.assertEqual(report["counts"]["added_shapes"], 0)

    def test_strict_false_preserves_actual_results_evidence_and_final_model_proto(self):
        import layout_configurator.zone_hierarchy as hierarchy
        from test_zone_hierarchy import _two_zones

        for value in (None, 0, 1, "true"):
            with self.subTest(value=value), patch.object(hierarchy, "_local_shapes") as local:
                evidence = {}
                with self.assertRaises(ZoneHierarchyNotApplicable):
                    solve_zone_layouts(program(), evidence=evidence, selective_leaf_repacking=value)
                local.assert_not_called()
                self.assertEqual(evidence["failure"]["model_status"], "NOT_BUILT")
        building = _two_zones()
        options = {"zone_size_options_mm": {"left": [(4000, 2000)], "right": [(4000, 2000)]},
            "deterministic_units": .05}
        from ortools.sat.python import cp_model
        states, outputs, protos = [], [], []
        original_solve = cp_model.CpSolver.Solve
        def capture(solver, model, *args, **kwargs):
            protos.append(str(model.Proto()))
            return original_solve(solver, model, *args, **kwargs)
        for flag in ({}, {"selective_leaf_repacking": False}):
            evidence = {}
            with patch.object(cp_model.CpSolver, "Solve", capture):
                outputs.append(solve_zone_layouts(building, 1, .5, 1, evidence=evidence, **options, **flag))
            states.append(evidence)
        self.assertEqual([r.to_dict() for r in outputs[0]], [r.to_dict() for r in outputs[1]])
        self.assertEqual(json.dumps(states[0], sort_keys=True), json.dumps(states[1], sort_keys=True))
        self.assertNotIn("selective_leaf_repacking", states[1])
        self.assertEqual(len(protos) % 2, 0)
        self.assertEqual(protos[:len(protos)//2], protos[len(protos)//2:])


if __name__ == "__main__":
    unittest.main()

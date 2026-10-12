"""Physical recursive zones retain soft guidance and current acceptance gates."""

import unittest

from layout_configurator.building import BuildingIR
from layout_configurator.facility import default_facility_profile
from layout_configurator.facility_generation import solve_feasible_facility_variants
from layout_configurator.facility_incremental import re_solve_facility_after_edit
from layout_configurator.models import LayoutResult, Rect
from layout_configurator.validation import validate_layout
from layout_configurator.zone_hierarchy import ZoneHierarchyFailure, ZoneHierarchyNotApplicable, solve_zone_layouts, validate_zone_layout
from test_facility_incremental import sha256


def program():
    rooms, equipment = [], []
    for prefix in ("a", "b"):
        rooms.append({
            "id": prefix + "_hub", "type": "corridor", "target_area": 12,
            "min_area": 12, "max_area": 12, "min_width": 2000, "min_depth": 6000,
            "required_adjacency": ["b_hub"] if prefix == "a" else [],
        })
        for index in range(3):
            rooms.append({
                "id": f"{prefix}_leaf_{index}", "target_area": 4,
                "min_area": 4, "max_area": 6, "min_width": 2000, "min_depth": 2000,
                "required_adjacency": [prefix + "_hub"],
            })
        equipment.append({
            "id": prefix + "_machine", "room_id": prefix + "_leaf_1",
            "width_mm": 400, "depth_mm": 200, "clearance_mm": 100,
        })
    return BuildingIR.from_mapping({
        "layout": {"boundary": {"width": 16000, "height": 12000},
                   "entry_room": "a_leaf_0", "rooms": rooms},
        "zones": [
            {"id": "parent", "room_ids": []},
            {"id": "a_zone", "parent_zone_id": "parent",
             "room_ids": [room["id"] for room in rooms if room["id"].startswith("a_")],
             "required_adjacency": ["b_zone"]},
            {"id": "b_zone", "parent_zone_id": "parent",
             "room_ids": [room["id"] for room in rooms if room["id"].startswith("b_")]},
        ],
        "equipment": equipment,
        "flows": [{"id": "people", "type": "people", "from_ids": ["a_leaf_0"],
                   "to_ids": ["b_leaf_2"], "required": True, "minimum_clear_width_mm": 800}],
    })


def source_rects():
    result = {"a_hub": Rect(2000, 0, 2000, 6000), "b_hub": Rect(4000, 0, 2000, 6000)}
    for prefix, x in (("a", 0), ("b", 6000)):
        for index in range(3):
            result[f"{prefix}_leaf_{index}"] = Rect(x, index * 2000, 2000, 2000)
    return result


def zone_rects(evidence):
    return {unit_id: Rect(**raw) for unit_id, raw in evidence["variants"][0]["zone_rects"].items()}


class ZoneWarmStartTests(unittest.TestCase):
    def sizes(self):
        return {"parent": [(8000, 6000)], "a_zone": [(4000, 6000)], "b_zone": [(4000, 6000)]}

    def assert_physical_zones(self, building, result, evidence, **constraints):
        containers = zone_rects(evidence)
        report = validate_zone_layout(building, result, containers, recursive_zones=True, **constraints)
        self.assertTrue(report.ok, report.issues)
        self.assertEqual(set(containers), {"parent", "a_zone", "b_zone"})
        self.assertEqual(containers["a_zone"].intersection_area(containers["b_zone"]), 0)

    def assert_facility_gates(self, building, generation):
        self.assertEqual(len(generation.accepted), 1)
        candidate = generation.accepted[0]
        for report in (candidate.layout_report, candidate.equipment_report,
                       candidate.flow_report, candidate.facility_report):
            self.assertTrue(report.ok, report)
        self.assert_physical_zones(building, candidate.result, generation.room_solver_evidence)

    def test_actual_zone_cluster_room_pipeline_shares_one_checked_cache_across_leaves(self):
        building = program()
        evidence = {}
        result = solve_zone_layouts(
            building, 1, 1, 1, recursive_zones=True,
            zone_size_options_mm={"parent": [(16000, 8000)],
                                  "a_zone": [(8000, 8000)], "b_zone": [(8000, 8000)]},
            hint_rects=source_rects(), cache_cluster_shapes=True, evidence=evidence,
        )[0]
        self.assertTrue(validate_layout(building.layout, result).ok)
        self.assert_physical_zones(building, result, evidence)
        cache = evidence["cluster_shape_cache"]
        self.assertEqual(cache["scope"], "single_zone_hierarchy_invocation")
        self.assertGreaterEqual(cache["hits"], 1)
        self.assertTrue(any(len(event["room_ids"]) == 4 for event in cache["events"]))
        self.assertFalse(evidence["zone_hierarchy_hints"]["positions_are_constraints"])
        self.assertTrue(any(attempt["stage"] == "subtree" and attempt["accepted"]
                            for attempt in evidence["stage_attempts"]))

    def test_conflicting_old_positions_cannot_override_current_fixed_room_or_axes(self):
        building = program()
        evidence = {}
        constraints = {"fixed_rects": {"a_hub": Rect(4000, 0, 2000, 6000)},
                       "axis_aligned_room_ids": ["a_hub"],
                       "structural_axes_x_mm": [0, 2000, 4000, 6000, 8000, 10000]}
        result = solve_zone_layouts(
            building, 1, 1, 1, recursive_zones=True, zone_size_options_mm=self.sizes(),
            hint_rects=source_rects(), cache_cluster_shapes=True, evidence=evidence, **constraints,
        )[0]
        self.assertEqual(result.placements["a_hub"], constraints["fixed_rects"]["a_hub"])
        self.assertNotEqual(result.placements["a_hub"], source_rects()["a_hub"])
        self.assert_physical_zones(building, result, evidence, **constraints)
        self.assertFalse(evidence["zone_hierarchy_hints"]["positions_are_constraints"])

    def test_facility_bridge_accepts_physical_recursive_geometry_with_all_current_gates(self):
        building = program()
        generation = solve_feasible_facility_variants(
            building, default_facility_profile(), variants=1, max_attempts=1,
            time_limit_seconds=1, seed=1, room_solver="zone_recursive",
            hint_rects=source_rects(), cache_cluster_shapes=True,
        )
        self.assert_facility_gates(building, generation)
        self.assertEqual(generation.room_solver_used, "zone_recursive")

    def test_room_edit_repairs_stale_geometry_without_synthesizing_hard_pins(self):
        prior = program()
        previous = LayoutResult(1, source_rects())
        raw = prior.to_dict()
        next(room for room in raw["layout"]["rooms"] if room["id"] == "b_leaf_0").update(
            target_area=6, min_area=6, max_area=6, min_width=3000, min_depth=2000)
        edited = BuildingIR.from_mapping(raw)
        self.assertFalse(validate_layout(edited.layout, previous).ok)
        generation = re_solve_facility_after_edit(
            prior, previous, edited, default_facility_profile(), variants=1, max_attempts=1,
            time_limit_seconds=1, seed=1, room_solver="zone_recursive", cache_cluster_shapes=True,
        )
        candidate = generation.accepted[0]
        for report in (candidate.layout_report, candidate.equipment_report,
                       candidate.flow_report, candidate.facility_report):
            self.assertTrue(report.ok, report)
        self.assert_physical_zones(edited, candidate.result, generation.room_solver_evidence)
        self.assertEqual(candidate.result.placements["b_leaf_0"].area_m2, 6)
        evidence = generation.incremental_evidence
        self.assertEqual(evidence["source_building_sha256"], sha256(prior.to_dict()))
        self.assertEqual(evidence["source_layout_sha256"], sha256(previous.to_dict()))
        self.assertEqual(evidence["edited_building_sha256"], sha256(edited.to_dict()))
        self.assertEqual(evidence["changed_source_ids"]["rooms"], ["b_leaf_0"])
        self.assertEqual(evidence["synthesized_fixed_room_ids"], [])
        self.assertEqual(set(candidate.result.placements), set(previous.placements))
        self.assertEqual(evidence["source_ids"]["zones"], ["a_zone", "b_zone", "parent"])
        self.assertEqual(evidence["unchanged_source_ids"]["equipment"], ["a_machine", "b_machine"])

    def test_equipment_edit_rejects_stale_leaf_fit_and_repacks_the_current_item(self):
        prior = program()
        previous = LayoutResult(1, source_rects())
        raw = prior.to_dict()
        next(item for item in raw["equipment"] if item["id"] == "b_machine")["width_mm"] = 2400
        edited = BuildingIR.from_mapping(raw)
        generation = re_solve_facility_after_edit(
            prior, previous, edited, default_facility_profile(), variants=1, max_attempts=1,
            time_limit_seconds=1, seed=1, room_solver="zone_recursive", cache_cluster_shapes=True,
        )
        self.assert_facility_gates(edited, generation)
        self.assertGreater(generation.accepted[0].result.placements["b_leaf_1"].area_m2, 4)
        self.assertEqual(generation.incremental_evidence["changed_source_ids"]["equipment"], ["b_machine"])
        self.assertEqual(generation.incremental_evidence["changed_source_ids"]["rooms"], [])
        self.assertTrue(any(event["unit_id"] == "b_zone" and event["origin"] == "supplied_room_hints"
                            and event["outcome"] == "invalid_current_leaf_geometry"
                            for event in generation.room_solver_evidence["zone_hierarchy_hints"]["leaf_shapes"]))

    def test_empty_warm_options_keep_the_default_geometry_and_evidence_identical(self):
        from test_recursive_zones import _program

        building = _program(grandchild=False)
        sizes = {"parent": [(8000, 2000)], "left": [(4000, 2000)], "right": [(4000, 2000)]}
        baseline, empty = {}, {}
        first = solve_zone_layouts(building, 1, .5, 1, recursive_zones=True,
                                  zone_size_options_mm=sizes, evidence=baseline, deterministic_units=.05)
        second = solve_zone_layouts(building, 1, .5, 1, recursive_zones=True,
                                   zone_size_options_mm=sizes, evidence=empty, deterministic_units=.05,
                                   hint_rects={}, cache_cluster_shapes=False)
        self.assertEqual([result.to_dict() for result in first], [result.to_dict() for result in second])
        self.assertEqual(baseline, empty)
        self.assertNotIn("zone_hierarchy_hints", empty)
        self.assertNotIn("cluster_shape_cache", empty)

    def test_invalid_warm_inputs_retain_structured_input_failure_before_search(self):
        for options in ({"cache_cluster_shapes": 1}, {"hint_rects": []},
                        {"hint_rects": {"missing": Rect(0, 0, 2000, 2000)}},
                        {"hint_rects": {"a_hub": Rect(float("nan"), 0, 2000, 6000)}},
                        {"hint_rects": {room_id: Rect(-1e308 if room_id.startswith("a_") else 1e308,
                                                       rect.y, rect.width, rect.height)
                                        for room_id, rect in source_rects().items()}}):
            with self.subTest(options=options):
                evidence = {}
                with self.assertRaises(ZoneHierarchyNotApplicable) as caught:
                    solve_zone_layouts(program(), 1, .5, 1, evidence=evidence, **options)
                failure = caught.exception.to_dict()
                self.assertEqual((failure["stage"], failure["model_status"]), ("input", "NOT_BUILT"))
                self.assertFalse(failure["proven_building_infeasible"])
                self.assertEqual(evidence["stage_attempts"], [])

    def test_final_cutout_entry_and_window_constraints_override_conflicting_soft_positions(self):
        from test_recursive_zones import _program

        raw = _program(grandchild=False).to_dict()
        raw["layout"].update(
            boundary={"width": 12000, "height": 4000,
                      "cutouts": [{"x": 0, "y": 0, "width": 4000, "height": 2000}]},
            external_entry={"id": "entry", "room_id": "a", "side": "right", "offset_mm": 1000, "width_mm": 900},
            windows=[{"id": "window", "room_id": "d", "side": "bottom", "offset_mm": 1000, "width_mm": 800}],
        )
        raw["layout"]["rooms"][-1]["needs_daylight"] = True
        building = BuildingIR.from_mapping(raw)
        hints = {room_id: Rect(index * 2000, 0, 2000, 2000) for index, room_id in enumerate(("a", "b", "c", "d"))}
        evidence = {}
        result = solve_zone_layouts(
            building, 1, .5, 1, recursive_zones=True,
            zone_size_options_mm={"parent": [(8000, 2000)], "left": [(4000, 2000)], "right": [(4000, 2000)]},
            hint_rects=hints, cache_cluster_shapes=True, evidence=evidence,
        )[0]
        self.assertEqual(result.placements["a"].right, 12000)
        self.assertEqual(result.placements["d"].y, 0)
        self.assertTrue(validate_zone_layout(building, result, zone_rects(evidence), recursive_zones=True).ok)
        for container in zone_rects(evidence).values():
            self.assertEqual(container.intersection_area(building.layout.boundary.cutouts[0]), 0)

    def test_failed_final_constraints_keep_actual_cp_status_and_bounded_proof_scope(self):
        evidence = {}
        with self.assertRaises(ZoneHierarchyFailure) as caught:
            solve_zone_layouts(
                program(), 1, .5, 1, recursive_zones=True, zone_size_options_mm=self.sizes(),
                hint_rects=source_rects(), cache_cluster_shapes=True, evidence=evidence,
                fixed_rects={"a_hub": Rect(18000, 0, 2000, 6000)},
            )
        failure = caught.exception.to_dict()
        self.assertEqual((failure["stage"], failure["model_status"], failure["proof_scope"]),
                         ("global", "INFEASIBLE", "selected_zone_shapes"))
        self.assertFalse(failure["proven_building_infeasible"])
        self.assertFalse(evidence["zone_hierarchy_hints"]["positions_are_constraints"])


if __name__ == "__main__":
    unittest.main()

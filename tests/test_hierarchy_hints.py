"""Soft hierarchy hints repair edited layouts without removing hard gates."""

import math
import unittest
from dataclasses import replace

from layout_configurator.building import BuildingIR
from layout_configurator.equipment import place_equipment_clear_of_doors, validate_equipment_layout
from layout_configurator.facility import default_facility_profile, validate_building
from layout_configurator.flows import route_flows, validate_flow_routes
from layout_configurator.hierarchy import solve_layouts_auto, solve_layouts_hierarchical
from layout_configurator.hierarchy_hints import HierarchyHints
from layout_configurator.models import LayoutIR, LayoutResult, Rect
from layout_configurator.solver import _RoomVars
from layout_configurator.validation import validate_layout
from ortools.sat.python import cp_model


def program():
    rooms = []
    for prefix in ("a", "b"):
        rooms.append({"id": prefix + "_hub", "type": "corridor", "target_area": 8,
                      "min_area": 8, "max_area": 8, "min_width": 2000, "min_depth": 4000,
                      "required_adjacency": ["b_hub"] if prefix == "a" else []})
        for index in range(2):
            rooms.append({"id": f"{prefix}_leaf_{index}", "target_area": 4,
                          "min_area": 4, "max_area": 6, "min_width": 2000, "min_depth": 2000,
                          "required_adjacency": [prefix + "_hub"]})
    return LayoutIR.from_mapping({"boundary": {"width": 16000, "height": 12000},
                                  "entry_room": "a_leaf_0", "rooms": rooms})


def source_rects():
    return {
        "a_hub": Rect(2000, 0, 2000, 4000),
        "a_leaf_0": Rect(0, 0, 2000, 2000), "a_leaf_1": Rect(0, 2000, 2000, 2000),
        "b_hub": Rect(4000, 0, 2000, 4000),
        "b_leaf_0": Rect(6000, 0, 2000, 2000), "b_leaf_1": Rect(6000, 2000, 2000, 2000),
    }


def independent_facility_gates(test, spec, result):
    building = BuildingIR.from_mapping({
        "layout": spec.to_dict(),
        "equipment": [{"id": "machine", "room_id": "b_leaf_0", "width_mm": 400,
                       "depth_mm": 200, "clearance_mm": 100}],
        "flows": [{"id": "people", "type": "people", "from_ids": ["a_leaf_0"],
                   "to_ids": ["b_leaf_1"], "required": True, "minimum_clear_width_mm": 800}],
    })
    layout_report = validate_layout(spec, result)
    test.assertTrue(layout_report.ok, layout_report.issues)
    packed, _ = place_equipment_clear_of_doors(building, result, time_limit_seconds=0.5, seed=1)
    equipment_report = validate_equipment_layout(building, result, packed)
    test.assertTrue(equipment_report.ok, equipment_report.issues)
    flows = route_flows(building, result, packed)
    flow_report = validate_flow_routes(building, result, flows, packed)
    test.assertTrue(flow_report.ok, flow_report.issues)
    facility = validate_building(building, result, packed, flows, flow_report,
                                profile=default_facility_profile(), equipment_report=equipment_report)
    test.assertTrue(facility.ok, [(check.id, check.evidence) for check in facility.checks if not check.ok])


class HierarchyHintTests(unittest.TestCase):
    def test_bounded_completion_fills_auxiliary_values_without_fixing_the_final_model(self):
        spec = LayoutIR.from_mapping({
            "boundary": {"width": 6000, "height": 2000}, "entry_room": "room",
            "rooms": [{"id": "room", "target_area": 4, "min_area": 4, "max_area": 4,
                       "min_width": 2000, "min_depth": 2000}],
        })
        model = cp_model.CpModel()
        x = model.NewIntVar(0, 40, "room_x")
        y = model.NewIntVar(0, 0, "room_y")
        width = model.NewIntVar(20, 20, "room_width")
        height = model.NewIntVar(20, 20, "room_height")
        end = model.NewIntVar(20, 60, "room_end")
        model.Add(end == x + width)
        model.Minimize(x)
        values = {"room": _RoomVars(x, y, width, height, None)}
        hints = HierarchyHints(spec, {"room": Rect(4000, 0, 2000, 2000)})
        hints.apply_free_room(model, "room", values["room"])
        constraints_before = tuple(str(constraint) for constraint in model.Proto().constraints)
        hints.complete(model, values, seed=1, time_limit_seconds=0.5, deterministic_units=None)
        self.assertEqual(tuple(str(constraint) for constraint in model.Proto().constraints), constraints_before)
        self.assertEqual(len(model.Proto().solution_hint.vars), len(model.Proto().variables))
        self.assertEqual(hints.evidence["completion"]["outcome"], "complete_soft_hint")
        solver = cp_model.CpSolver()
        solver.parameters.num_search_workers = 1
        self.assertEqual(solver.Solve(model), cp_model.OPTIMAL)
        self.assertEqual(solver.Value(x), 0)

    def test_failed_completion_retains_soft_guidance_and_does_not_claim_source_infeasibility(self):
        spec = LayoutIR.from_mapping({
            "boundary": {"width": 6000, "height": 2000}, "entry_room": "room",
            "rooms": [{"id": "room", "target_area": 4, "min_area": 4, "max_area": 4,
                       "min_width": 2000, "min_depth": 2000}],
        })
        model = cp_model.CpModel()
        x = model.NewIntVar(0, 40, "x")
        y = model.NewIntVar(0, 0, "y")
        width = model.NewIntVar(20, 20, "width")
        height = model.NewIntVar(20, 20, "height")
        model.Add(x == 0)
        values = {"room": _RoomVars(x, y, width, height, None)}
        hints = HierarchyHints(spec, {"room": Rect(4000, 0, 2000, 2000)})
        hints.apply_free_room(model, "room", values["room"])
        original_hint = str(model.Proto().solution_hint)
        hints.complete(model, values, seed=1, time_limit_seconds=0.5, deterministic_units=None)
        self.assertEqual(str(model.Proto().solution_hint), original_hint)
        self.assertEqual(hints.evidence["completion"]["model_status"], "INFEASIBLE")
        self.assertFalse(hints.evidence["completion"]["original_program_infeasibility_proven"])
        solver = cp_model.CpSolver()
        solver.parameters.num_search_workers = 1
        self.assertEqual(solver.Solve(model), cp_model.OPTIMAL)
        self.assertEqual(solver.Value(x), 0)

    def test_independent_geometry_rejects_a_complete_cp_hint_before_transfer(self):
        spec = LayoutIR.from_mapping({
            "boundary": {"width": 6000, "height": 2000}, "entry_room": "room",
            "rooms": [{"id": "room", "target_area": 4, "min_area": 4, "max_area": 4,
                       "min_width": 2000, "min_depth": 2000}],
        })
        model = cp_model.CpModel()
        values = {"room": _RoomVars(
            model.NewIntVar(0, 40, "x"), model.NewIntVar(0, 0, "y"),
            model.NewIntVar(10, 10, "width"), model.NewIntVar(20, 20, "height"), None,
        )}
        hints = HierarchyHints(spec, {"room": Rect(4000, 0, 1000, 2000)})
        hints.apply_free_room(model, "room", values["room"])
        original_hint = str(model.Proto().solution_hint)
        hints.complete(model, values, seed=1, time_limit_seconds=0.5, deterministic_units=None)
        self.assertEqual(str(model.Proto().solution_hint), original_hint)
        self.assertEqual(hints.evidence["completion"]["outcome"], "independent_geometry_rejected")

    def test_forced_hierarchy_accepts_complete_hints_and_passes_every_facility_gate(self):
        spec = program()
        source = source_rects()
        self.assertTrue(validate_layout(spec, LayoutResult(1, source)).ok)
        evidence = {}
        result = solve_layouts_auto(spec, 1, 1, 1, strategy="hierarchical",
                                   hint_rects=source, evidence=evidence)[0]
        self.assertEqual(evidence["room_solver_used"], "hierarchical")
        self.assertTrue(evidence["hierarchy_hints"]["repair_hint"])
        self.assertEqual(evidence["hierarchy_hints"]["completion"]["outcome"], "complete_soft_hint")
        independent_facility_gates(self, spec, result)

    def test_a_valid_complete_hint_can_supplement_the_bounded_pattern_set(self):
        spec = program()
        source = {
            "a_hub": Rect(3000, 0, 2000, 4000),
            "a_leaf_0": Rect(0, 0, 3000, 2000), "a_leaf_1": Rect(1000, 2000, 2000, 2000),
            "b_hub": Rect(5000, 0, 2000, 4000),
            "b_leaf_0": Rect(7000, 0, 2000, 2000), "b_leaf_1": Rect(7000, 2000, 2000, 2000),
        }
        self.assertTrue(validate_layout(spec, LayoutResult(1, source)).ok)
        evidence = {}
        result = solve_layouts_hierarchical(spec, 1, 1, 1, hint_rects=source, evidence=evidence)[0]
        stage = next(item for item in evidence["hierarchy_hints"]["clusters"] if item["cluster_id"] == "a_hub")
        self.assertEqual(stage["outcome"], "validated_supplement")
        self.assertLessEqual(stage["added_patterns"], 8)
        self.assertTrue(validate_layout(spec, result).ok)

    def test_repeated_pinned_room_resize_repairs_old_hints_and_keeps_all_gates(self):
        spec = program()
        hints = source_rects()
        for width in (3000, 4000):
            with self.subTest(width=width):
                area = width * 2000 / 1e6
                spec = replace(spec, rooms=tuple(
                    replace(room, target_area_m2=area, min_area_m2=area, max_area_m2=area,
                            min_width_mm=width) if room.id == "a_leaf_0" else room
                    for room in spec.rooms
                ))
                pinned = Rect(0, 0, width, 2000)
                evidence = {}
                result = solve_layouts_hierarchical(
                    spec, 1, 1, 1, hint_rects=hints, fixed_rects={"a_leaf_0": pinned}, evidence=evidence,
                )[0]
                self.assertEqual(result.placements["a_leaf_0"], pinned)
                self.assertNotEqual(result.placements["a_hub"], hints["a_hub"])
                independent_facility_gates(self, spec, result)
                hints = result.placements

    def test_invalid_local_hint_is_skipped_without_removing_normal_search_patterns(self):
        hints = source_rects()
        hints["a_leaf_0"] = hints["a_hub"]
        evidence = {}
        result = solve_layouts_hierarchical(program(), 1, 1, 1, hint_rects=hints, evidence=evidence)[0]
        stage = next(item for item in evidence["hierarchy_hints"]["clusters"] if item["cluster_id"] == "a_hub")
        self.assertEqual(stage["outcome"], "invalid_local_geometry")
        self.assertTrue(validate_layout(program(), result).ok)

    def test_changed_equipment_fit_rejects_a_stale_hint_and_repairs_actual_room_size(self):
        evidence = {}
        result = solve_layouts_hierarchical(
            program(), 1, 1, 1, hint_rects=source_rects(), evidence=evidence,
            equipment_fit_options={"a_leaf_0": (("wider_machine", ((3000, 2000),)),)},
        )[0]
        self.assertGreaterEqual(result.placements["a_leaf_0"].width, 3000)
        self.assertGreaterEqual(result.placements["a_leaf_0"].height, 2000)
        stage = next(item for item in evidence["hierarchy_hints"]["clusters"] if item["cluster_id"] == "a_hub")
        self.assertEqual(stage["outcome"], "invalid_local_geometry")
        self.assertTrue(validate_layout(program(), result).ok)

    def test_partial_cluster_hint_is_explicitly_recorded_and_not_frozen(self):
        evidence = {}
        result = solve_layouts_hierarchical(program(), 1, 1, 1,
                                           hint_rects={"a_hub": source_rects()["a_hub"]}, evidence=evidence)[0]
        self.assertTrue(validate_layout(program(), result).ok)
        stage = next(item for item in evidence["hierarchy_hints"]["clusters"] if item["cluster_id"] == "a_hub")
        self.assertEqual(stage["outcome"], "partial_cluster_hint")

    def test_empty_hint_mapping_preserves_deterministic_results_and_evidence(self):
        spec = program()
        baseline = solve_layouts_auto(spec, 1, 1, 1, strategy="hierarchical", deterministic_units=0.1)
        evidence = {}
        result = solve_layouts_auto(spec, 1, 1, 1, strategy="hierarchical", deterministic_units=0.1,
                                   hint_rects={}, evidence=evidence)
        self.assertEqual(result, baseline)
        self.assertEqual(evidence, {"room_solver_used": "hierarchical"})

    def test_hint_cannot_override_hard_contacts_area_accuracy_or_a_soft_preference(self):
        fixed = {"corridor": Rect(0, 0, 8000, 2000),
                 "a": Rect(0, 2000, 2000, 2000), "b": Rect(6000, 2000, 2000, 2000)}
        for require_a, forbid_b, hinted, expected in (
            (False, False, Rect(2000, 2000, 2000, 2000), Rect(4000, 2000, 2000, 2000)),
            (True, False, Rect(2000, 2000, 4000, 2000), Rect(2000, 2000, 2000, 2000)),
            (False, True, Rect(4000, 2000, 2000, 2000), Rect(2000, 2000, 2000, 2000)),
        ):
            with self.subTest(require_a=require_a, forbid_b=forbid_b):
                spec = LayoutIR.from_mapping({
                    "boundary": {"width": 8000, "height": 4000}, "entry_room": "corridor",
                    "rooms": [
                        {"id": room_id, "target_area": area, "min_area": area,
                         "max_area": 8 if room_id == "c" else area,
                         "min_width": 2000, "min_depth": 2000,
                         "required_adjacency": (["corridor", "a"] if require_a else ["corridor"])
                         if room_id == "c" else (["corridor"] if room_id != "corridor" else []),
                         "preferred_adjacency": ["b"] if room_id == "c" else [],
                         "forbidden_adjacency": ["b"] if room_id == "c" and forbid_b else []}
                        for room_id, area in (("corridor", 16), ("a", 4), ("b", 4), ("c", 4))
                    ],
                })
                result = solve_layouts_hierarchical(spec, 1, 1, 1,
                                                    fixed_rects=fixed, hint_rects={"c": hinted})[0]
                self.assertEqual(result.placements["c"], expected)
                self.assertTrue(validate_layout(spec, result).ok)

    def test_unknown_nonfinite_offgrid_or_nonpositive_hints_are_rejected_as_input(self):
        invalid = (
            False, 0, [], "invalid",
            {"missing": Rect(0, 0, 2000, 2000)},
            {"a_hub": Rect(math.nan, 0, 2000, 4000)},
            {"a_hub": Rect(0, 0, True, 4000)},
            {"a_hub": Rect(1, 0, 2000, 4000)},
            {"a_hub": Rect(0, 0, 0, 4000)},
        )
        for hints in invalid:
            with self.subTest(hints=hints):
                with self.assertRaises(ValueError):
                    solve_layouts_hierarchical(program(), 1, 0.1, 1, hint_rects=hints)


if __name__ == "__main__":
    unittest.main()

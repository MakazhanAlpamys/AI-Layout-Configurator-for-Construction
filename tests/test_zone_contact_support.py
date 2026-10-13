"""Finite contact support extends the original integer geometry truth set."""

import importlib
import json
import unittest
from unittest.mock import patch

from layout_configurator.building import BuildingIR
from layout_configurator.models import Rect
import layout_configurator.zone_hierarchy as hierarchy
from layout_configurator.zone_hierarchy import _ZoneShape, ZoneHierarchyFailure, solve_zone_layouts, validate_zone_layout
from test_zone_contact_pruning import brute_translations, program, stacked
from test_zone_hierarchy import _two_zones, _zone_rects
import test_zone_warm_start as recursive_fixture


def planned(shapes, *, building=None, units=None, minimum=2, boundary=(6, 8)):
    module = importlib.import_module("layout_configurator.zone_contact_pruning")
    return module.plan_contact_support(
        building or program(), units or {"A": ("a1", "a2"), "B": ("b1", "b2")}, shapes,
        minimum_grid=minimum, boundary_grid=boundary,
    )


def row_points(rows, index_a, index_b):
    return {(x, y) for i, j, x_low, x_high, y_low, y_high in rows if (i, j) == (index_a, index_b)
            for x in range(x_low, x_high + 1) for y in range(y_low, y_high + 1)}


class ZoneContactSupportTests(unittest.TestCase):
    def test_all_shape_picks_and_integer_translations_equal_independent_geometry(self):
        shapes = {"A": [stacked("a"), stacked("a", reverse=True)],
                  "B": [stacked("b"), stacked("b", reverse=True)]}
        for minimum in (1, 2, 3):
            with self.subTest(minimum=minimum):
                plans, evidence = planned(shapes, minimum=minimum)
                self.assertEqual([(a, b) for a, b, _ in plans], [("A", "B")])
                rows = plans[0][2]
                for i, a in enumerate(shapes["A"]):
                    for j, b in enumerate(shapes["B"]):
                        with self.subTest(shape_a=i, shape_b=j):
                            truth = brute_translations(a, b, [("a1", "b1"), ("a2", "b2")],
                                grid_mm=1000, minimum_grid=minimum, boundary_grid=(6, 8))
                            self.assertEqual(row_points(rows, i, j), truth)
                self.assertFalse(evidence["original_program_infeasibility_proven"])

    def test_nonempty_pair_requires_actual_common_translation_and_preserves_two_sides(self):
        plans, evidence = planned({"A": [stacked("a")], "B": [stacked("b")]})
        rows = plans[0][2]
        self.assertEqual(row_points(rows, 0, 0), {(-2, 0), (2, 0)})
        # Pair no-goods cannot reject this selected shape pair: it has support.
        # Its actual delta is nevertheless incompatible with the original contacts.
        self.assertNotIn((2, 1), row_points(rows, 0, 0))
        self.assertEqual(evidence["counts"]["support_rows"], 2)

    def test_single_room_bridge_retains_all_four_sides_and_round_grid_semantics(self):
        building = BuildingIR.from_mapping({"layout": {
            "boundary": {"width": 1200, "height": 1000}, "grid_mm": 100, "entry_room": "a",
            "rooms": [{"id": "a", "target_area": .16, "min_width": 100, "min_depth": 100,
                       "required_adjacency": ["b"]},
                      {"id": "b", "target_area": .16, "min_width": 100, "min_depth": 100}]},
            "zones": [{"id": "A", "room_ids": ["a"]}, {"id": "B", "room_ids": ["b"]}]})
        a = _ZoneShape(450, 450, {"a": Rect(50, 50, 350, 350)})
        b = _ZoneShape(450, 450, {"b": Rect(50, 50, 350, 350)})
        for minimum in (1, 2, 4, 5):
            with self.subTest(minimum=minimum):
                plans, _ = planned({"A": [a], "B": [b]}, building=building,
                                   units={"A": ("a",), "B": ("b",)}, minimum=minimum, boundary=(12, 10))
                self.assertEqual(row_points(plans[0][2], 0, 0),
                    brute_translations(a, b, [("a", "b")], grid_mm=100,
                                       minimum_grid=minimum, boundary_grid=(12, 10)))
                if minimum == 1:
                    self.assertEqual(len(plans[0][2]), 4)

    def test_one_unsafe_candidate_skips_the_whole_pair_and_preserves_unknown_choices(self):
        bad = _ZoneShape(2000, 4000, {"a1": Rect(3000, 0, 2000, 2000), "a2": Rect(0, 2000, 2000, 2000)})
        plans, evidence = planned({"A": [stacked("a"), bad], "B": [stacked("b")]})
        self.assertEqual(plans, [])
        self.assertEqual(evidence["counts"]["skipped_invalid_containment_unit_pairs"], 1)
        self.assertEqual(evidence["counts"]["support_rows"], 0)
        self.assertEqual(evidence["counts"]["added_table_constraints"], 0)
        self.assertFalse(evidence["original_program_infeasibility_proven"])

    def test_every_preflight_limit_skips_every_table_before_model_mutation(self):
        module = importlib.import_module("layout_configurator.zone_contact_pruning")
        shapes = {"A": [stacked("a")], "B": [stacked("b"), stacked("b", reverse=True)]}
        for limit in ("MAX_SOURCE_ROOMS", "MAX_UNIT_PAIRS", "MAX_CANDIDATE_PAIRS",
                      "MAX_ORIENTATION_CONTACT_CHECKS", "MAX_PARTICIPATING_RECTANGLE_RECORDS", "MAX_SUPPORT_ROWS"):
            with self.subTest(limit=limit), patch.object(module, limit, 0):
                plans, evidence = planned(shapes)
                self.assertEqual(plans, [])
                self.assertEqual(evidence["status"], "SKIPPED_LIMIT")
                self.assertEqual(evidence["counts"]["examined_unit_pairs"], 0)
                self.assertEqual(evidence["counts"]["support_rows"], 0)
                self.assertEqual(evidence["counts"]["added_index_variables"], 0)
                self.assertEqual(evidence["counts"]["added_table_constraints"], 0)
        # The conservative four-orientation bound applies even if fewer rows survive.
        with patch.object(module, "MAX_SUPPORT_ROWS", 7):
            plans, evidence = planned(shapes)
        self.assertEqual(plans, [])
        self.assertEqual(evidence["skip_reason"], "support_row_upper_bound_limit")

    def test_actual_cp_preserves_accepted_geometry_and_posts_exact_small_model_delta(self):
        options = {"zone_size_options_mm": {"A": [(2000, 4000)], "B": [(2000, 4000)]},
                   "minimum_adjacency_mm": 2000, "deterministic_units": .1, "global_diagnostics": True,
                   "fixed_rects": {"a1": Rect(0, 0, 2000, 2000)}}
        baseline, evidence = {}, {}
        first = solve_zone_layouts(program(), 1, 1, 1, evidence=baseline, **options)[0]
        result = solve_zone_layouts(program(), 1, 1, 1, evidence=evidence, cross_root_contact_support=True, **options)[0]
        self.assertEqual(first.to_dict(), result.to_dict())
        for candidate, proof in ((first, baseline), (result, evidence)):
            self.assertTrue(validate_zone_layout(program(), candidate, _zone_rects(proof), minimum_adjacency_mm=2000).ok)
        report = evidence["cross_root_contact_support"]
        self.assertEqual(report["status"], "APPLIED")
        self.assertEqual(report["counts"]["participating_units"], 2)
        self.assertEqual(report["counts"]["added_index_variables"], 2)
        self.assertEqual(report["counts"]["added_endpoint_variables"], 4)
        self.assertEqual(report["counts"]["added_linear_constraints"], 6)
        self.assertEqual(report["counts"]["added_table_constraints"], 1)
        before = baseline["global_diagnostics"]["searches"][0]["model"]
        after = evidence["global_diagnostics"]["searches"][0]["model"]
        self.assertEqual(after["variables"], before["variables"] + 6)
        self.assertEqual(after["constraints"], before["constraints"] + 7)
        self.assertEqual(after["constraint_types"]["linear"], before["constraint_types"]["linear"] + 6)
        self.assertEqual(after["constraint_types"]["table"], 1)
        self.assertEqual(baseline["global_diagnostics"]["candidate_family_sha256"], evidence["global_diagnostics"]["candidate_family_sha256"])
        self.assertTrue(evidence["global_diagnostics"]["searches"][0]["response"]["solution_present"])
        self.assertFalse(report["original_program_infeasibility_proven"])
        self.assertNotIn("cross_root_contact_pruning", evidence)

    def test_both_options_keep_both_reports_and_every_pruning_clause(self):
        options = {"zone_size_options_mm": {"A": [(2000, 4000)], "B": [(2000, 4000)]},
                   "minimum_adjacency_mm": 2000, "deterministic_units": .1, "global_diagnostics": True,
                   "prune_cross_root_contacts": True, "fixed_rects": {"a1": Rect(0, 0, 2000, 2000)}}
        baseline, evidence = {}, {}
        solve_zone_layouts(program(), 1, 1, 1, evidence=baseline, **options)
        result = solve_zone_layouts(program(), 1, 1, 1, evidence=evidence, cross_root_contact_support=True, **options)[0]
        self.assertTrue(validate_zone_layout(program(), result, _zone_rects(evidence), minimum_adjacency_mm=2000).ok)
        self.assertEqual(baseline["cross_root_contact_pruning"], evidence["cross_root_contact_pruning"])
        before = baseline["global_diagnostics"]["searches"][0]["model"]
        after = evidence["global_diagnostics"]["searches"][0]["model"]
        self.assertEqual(after["constraint_types"]["bool_or"], before["constraint_types"]["bool_or"])
        self.assertEqual(after["constraints"], before["constraints"] + 7)

    def test_empty_safe_table_is_selected_family_cp_infeasibility_only(self):
        building, evidence = program(), {"stage_attempts": [], "variants": []}
        units = {"A": ("a1", "a2"), "B": ("b1", "b2")}
        shapes = {"A": [stacked("a")], "B": [stacked("b", reverse=True)]}
        plans, planned_evidence = planned(shapes)
        self.assertEqual(plans[0][2], ())
        self.assertEqual(planned_evidence["status"], "APPLIED")
        groups = {kind: building.zone_relation_groups(kind + "_adjacency") for kind in ("required", "forbidden", "preferred")}
        with self.assertRaises(ZoneHierarchyFailure) as failure:
            hierarchy._solve_shape_layouts(building, units, shapes, groups, 2000, 1, .5, 1, 1, .05, evidence,
                container_members=units, ancestors={}, cross_root_contact_support=True, global_diagnostics=True)
        self.assertEqual(failure.exception.model_status, "INFEASIBLE")
        self.assertEqual(failure.exception.proof_scope, "selected_zone_shapes")
        self.assertFalse(failure.exception.to_dict()["proven_building_infeasible"])
        self.assertEqual(evidence["cross_root_contact_support"]["counts"]["support_rows"], 0)
        self.assertEqual(evidence["cross_root_contact_support"]["counts"]["added_table_constraints"], 1)
        self.assertEqual(evidence["global_diagnostics"]["capture_status"], "COMPLETE")

    def test_budget_skip_is_an_actual_model_noop(self):
        module = importlib.import_module("layout_configurator.zone_contact_pruning")
        options = {"zone_size_options_mm": {"A": [(2000, 4000)], "B": [(2000, 4000)]},
                   "minimum_adjacency_mm": 2000, "deterministic_units": .1, "global_diagnostics": True,
                   "fixed_rects": {"a1": Rect(0, 0, 2000, 2000)}}
        baseline, evidence = {}, {}
        first = solve_zone_layouts(program(), 1, 1, 1, evidence=baseline, **options)[0]
        with patch.object(module, "MAX_SUPPORT_ROWS", 0):
            result = solve_zone_layouts(program(), 1, 1, 1, evidence=evidence, cross_root_contact_support=True, **options)[0]
        self.assertEqual(first.to_dict(), result.to_dict())
        self.assertEqual(evidence["cross_root_contact_support"]["status"], "SKIPPED_LIMIT")
        self.assertEqual(evidence["global_diagnostics"]["searches"][0]["model"], baseline["global_diagnostics"]["searches"][0]["model"])

    def test_unsafe_pair_skip_keeps_the_actual_model_and_accepted_safe_alternative(self):
        building = program()
        bad = _ZoneShape(2000, 4000, {"a1": Rect(3000, 0, 2000, 2000), "a2": Rect(0, 2000, 2000, 2000)})
        shapes = {"A": [stacked("a"), bad], "B": [stacked("b")]}
        units = {"A": ("a1", "a2"), "B": ("b1", "b2")}
        groups = {kind: building.zone_relation_groups(kind + "_adjacency") for kind in ("required", "forbidden", "preferred")}
        baseline, evidence = {"stage_attempts": [], "variants": []}, {"stage_attempts": [], "variants": []}
        options = {"container_members": units, "ancestors": {}, "global_diagnostics": True}
        first = hierarchy._solve_shape_layouts(building, units, shapes, groups, 2000, 1, .5, 1, 1, .05,
                                              baseline, **options)[0]
        result = hierarchy._solve_shape_layouts(building, units, shapes, groups, 2000, 1, .5, 1, 1, .05,
                                               evidence, cross_root_contact_support=True, **options)[0]
        self.assertEqual(first.to_dict(), result.to_dict())
        self.assertTrue(validate_zone_layout(building, result, _zone_rects(evidence), minimum_adjacency_mm=2000).ok)
        self.assertEqual(evidence["cross_root_contact_support"]["counts"]["skipped_invalid_containment_unit_pairs"], 1)
        self.assertEqual(evidence["cross_root_contact_support"]["counts"]["added_table_constraints"], 0)
        self.assertEqual(evidence["global_diagnostics"]["searches"][0]["model"],
                         baseline["global_diagnostics"]["searches"][0]["model"])

    def test_false_keeps_exact_json_and_private_arguments(self):
        first, second = {}, {}
        with patch.object(hierarchy, "_solve_shape_layouts", wraps=hierarchy._solve_shape_layouts) as calls:
            baseline = solve_zone_layouts(_two_zones(), 1, .5, 1, evidence=first, deterministic_units=.05)[0]
            explicit = solve_zone_layouts(_two_zones(), 1, .5, 1, evidence=second, deterministic_units=.05,
                                         cross_root_contact_support=False)[0]
        self.assertEqual(baseline.to_dict(), explicit.to_dict())
        self.assertEqual(json.dumps(first, sort_keys=True), json.dumps(second, sort_keys=True))
        self.assertNotIn("cross_root_contact_support", second)
        self.assertTrue(all("cross_root_contact_support" not in call.kwargs for call in calls.call_args_list))

    def test_recursive_keyword_only_reaches_final_model(self):
        evidence = {}
        with patch.object(hierarchy, "_solve_shape_layouts", wraps=hierarchy._solve_shape_layouts) as calls:
            result = solve_zone_layouts(recursive_fixture.program(), 1, 1, 1, evidence=evidence,
                recursive_zones=True, cross_root_contact_support=True, hint_rects=recursive_fixture.source_rects(),
                zone_size_options_mm={"parent": [(8000, 6000)], "a_zone": [(4000, 6000)], "b_zone": [(4000, 6000)]})[0]
        self.assertTrue(validate_zone_layout(recursive_fixture.program(), result,
                                            recursive_fixture.zone_rects(evidence), recursive_zones=True).ok)
        opted = [call for call in calls.call_args_list if call.kwargs.get("cross_root_contact_support")]
        self.assertEqual(len(opted), 1)
        self.assertNotIn("stage", opted[0].kwargs)
        self.assertGreater(len(calls.call_args_list), 1)
        self.assertEqual(evidence["cross_root_contact_support"]["counts"]["added_table_constraints"], 0)

    def test_strict_option_rejected_before_cp(self):
        for value in (1, 0, "true", None, []):
            with self.subTest(value=value):
                evidence = {}
                with self.assertRaises(ZoneHierarchyFailure) as failure:
                    solve_zone_layouts(program(), evidence=evidence, cross_root_contact_support=value)
                self.assertEqual(failure.exception.code, "invalid_contact_support_mode")
                self.assertEqual(failure.exception.model_status, "NOT_BUILT")
                self.assertEqual(evidence["stage_attempts"], [])


if __name__ == "__main__":
    unittest.main()

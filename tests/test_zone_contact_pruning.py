"""Candidate compatibility is exact for integer translations, not source proof."""

import importlib
import json
import random
import unittest
from unittest.mock import patch

from layout_configurator.building import BuildingIR
from layout_configurator.models import Rect
import layout_configurator.zone_hierarchy as hierarchy
from layout_configurator.zone_hierarchy import _ZoneShape, ZoneHierarchyFailure, solve_zone_layouts, validate_zone_layout
from test_zone_hierarchy import _two_zones, _zone_rects
import test_zone_warm_start as recursive_fixture


def program():
    room = lambda room_id, required: {"id": room_id, "target_area": 4, "min_area": 4, "max_area": 4,
                                      "min_width": 2000, "min_depth": 2000, "required_adjacency": required}
    return BuildingIR.from_mapping({
        "layout": {"boundary": {"width": 4000, "height": 4000}, "grid_mm": 1000,
                   "entry_room": "a1", "rooms": [room("a1", ["a2", "b1"]), room("a2", ["b2"]),
                                                   room("b1", ["b2"]), room("b2", [])]},
        "zones": [{"id": "A", "room_ids": ["a1", "a2"], "required_adjacency": ["B"]},
                  {"id": "B", "room_ids": ["b1", "b2"]}],
    })


def stacked(prefix, *, reverse=False):
    return _ZoneShape(2000, 4000, {prefix + "1": Rect(0, 2000 if reverse else 0, 2000, 2000),
                                  prefix + "2": Rect(0, 0 if reverse else 2000, 2000, 2000)})


def brute_translations(a, b, contacts, *, grid_mm, minimum_grid, boundary_grid):
    """Independently place integer rectangles and inspect actual shared edges."""
    wa, ha, wb, hb = [round(value / grid_mm) for value in (a.width, a.height, b.width, b.height)]
    width, height = boundary_grid
    result = set()
    for dx in range(-(width - wa), width - wb + 1):
        for dy in range(-(height - ha), height - hb + 1):
            origin_a = (max(0, -dx), max(0, -dy))
            origin_b = (origin_a[0] + dx, origin_a[1] + dy)
            envelope_a = Rect(*origin_a, wa, ha)
            envelope_b = Rect(*origin_b, wb, hb)
            if envelope_a.intersection_area(envelope_b) > 0:
                continue
            def rectangle(shape, room_id, origin):
                raw = shape.placements[room_id]
                x, y, w, h = [round(getattr(raw, name) / grid_mm) for name in ("x", "y", "width", "height")]
                return Rect(origin[0] + x, origin[1] + y, w, h)
            if all(rectangle(a, left, origin_a).shared_boundary(rectangle(b, right, origin_b)) >= minimum_grid
                   for left, right in contacts):
                result.add((dx, dy))
    return result


def expand(segments):
    return {(x, y) for x_low, x_high, y_low, y_high in segments
            for x in range(x_low, x_high + 1) for y in range(y_low, y_high + 1)}


class ZoneContactPruningTests(unittest.TestCase):
    def module(self):
        return importlib.import_module("layout_configurator.zone_contact_pruning")

    def test_two_individually_possible_contacts_cannot_use_different_translations(self):
        kernel = self.module().translation_segments
        a, same, reversed_b = stacked("a"), stacked("b"), stacked("b", reverse=True)
        contacts = (("a1", "b1"), ("a2", "b2"))
        options = {"grid_mm": 1000, "minimum_grid": 2, "boundary_grid": (6, 8)}
        self.assertEqual(expand(kernel(a, same, contacts, **options)), {(-2, 0), (2, 0)})
        self.assertEqual(kernel(a, reversed_b, contacts, **options), ())
        for contact in contacts:
            self.assertTrue(brute_translations(a, reversed_b, [contact], **options))
        self.assertFalse(brute_translations(a, reversed_b, contacts, **options))

    def test_every_tiny_integer_translation_matches_independent_geometry(self):
        kernel = self.module().translation_segments
        random_source = random.Random(17)
        cases = []
        for _ in range(100):
            wa, ha, wb, hb = [random_source.randint(2, 4) for _ in range(4)]
            def shape(prefix, width, height):
                rooms = {}
                for index in range(2):
                    w, h = random_source.randint(1, width), random_source.randint(1, height)
                    rooms[prefix + str(index)] = Rect(random_source.randint(0, width - w),
                                                    random_source.randint(0, height - h), w, h)
                return _ZoneShape(width, height, rooms)
            cases.append((shape("a", wa, ha), shape("b", wb, hb), random_source.randint(1, 3)))
        # Explicit complete vertical/horizontal faces exercise all four alternatives.
        for dimensions in ((2, 4), (4, 2), (2, 2)):
            w, h = dimensions
            cases.append((_ZoneShape(w, h, {"a0": Rect(0, 0, w, h), "a1": Rect(0, 0, w, h)}),
                          _ZoneShape(w, h, {"b0": Rect(0, 0, w, h), "b1": Rect(0, 0, w, h)}), 1))
        for index, (a, b, minimum) in enumerate(cases):
            with self.subTest(case=index):
                options = {"grid_mm": 1, "minimum_grid": minimum, "boundary_grid": (8, 8)}
                contacts = (("a0", "b0"), ("a1", "b1"))
                self.assertEqual(expand(kernel(a, b, contacts, **options)),
                                 brute_translations(a, b, contacts, **options))

    def test_round_grid_values_match_existing_pick_semantics_and_short_edges_fail(self):
        kernel = self.module().translation_segments
        a = _ZoneShape(650, 450, {"a": Rect(250, 50, 350, 350)})
        b = _ZoneShape(450, 450, {"b": Rect(50, 50, 350, 350)})
        for minimum in (1, 2, 4, 5):
            with self.subTest(minimum=minimum):
                options = {"grid_mm": 100, "minimum_grid": minimum, "boundary_grid": (12, 10)}
                self.assertEqual(expand(kernel(a, b, [("a", "b")], **options)),
                                 brute_translations(a, b, [("a", "b")], **options))

    def test_noncontained_effective_geometry_is_conservatively_skipped(self):
        module = self.module()
        bad = _ZoneShape(2000, 4000, {"a1": Rect(3000, 0, 2000, 2000), "a2": Rect(0, 2000, 2000, 2000)})
        options = {"grid_mm": 1000, "minimum_grid": 2, "boundary_grid": (4, 4)}
        self.assertIsNone(module.translation_segments(bad, stacked("b"), [("a1", "b1")], **options))
        rejected, report = module.plan_pruning(program(), {"A": ("a1", "a2"), "B": ("b1", "b2")},
            {"A": [bad], "B": [stacked("b")]}, minimum_grid=2, boundary_grid=(4, 4))
        self.assertEqual(rejected, [])
        self.assertEqual(report["counts"]["skipped_invalid_containment_pairs"], 1)
        self.assertFalse(report["original_program_infeasibility_proven"])

    def test_any_preflight_bound_skips_the_entire_pass_without_partial_rejections(self):
        module = self.module()
        units = {"A": ("a1", "a2"), "B": ("b1", "b2")}
        shapes = {"A": [stacked("a")], "B": [stacked("b"), stacked("b", reverse=True)]}
        for limit in ("MAX_SOURCE_ROOMS", "MAX_UNIT_PAIRS", "MAX_CANDIDATE_PAIRS", "MAX_ORIENTATION_CONTACT_CHECKS"):
            with self.subTest(limit=limit), patch.object(module, limit, 0):
                rejected, report = module.plan_pruning(program(), units, shapes, minimum_grid=2, boundary_grid=(4, 4))
                self.assertEqual(rejected, [])
                self.assertEqual(report["status"], "SKIPPED_LIMIT")
                self.assertEqual(report["counts"]["examined_pairs"], 0)
                self.assertEqual(report["counts"]["rejected_pairs"], 0)
                self.assertEqual(report["counts"]["added_clauses"], 0)
                self.assertGreater(report["counts"]["candidate_pairs"], 0)

    def test_actual_global_cp_adds_only_redundant_clauses_and_keeps_physical_gates(self):
        building, baseline, evidence = program(), {}, {}
        options = {"zone_size_options_mm": {"A": [(2000, 4000)], "B": [(2000, 4000)]},
                   "minimum_adjacency_mm": 2000, "deterministic_units": .05, "global_diagnostics": True}
        first = solve_zone_layouts(building, 1, .5, 1, evidence=baseline, **options)[0]
        result = solve_zone_layouts(building, 1, .5, 1, evidence=evidence, prune_cross_root_contacts=True, **options)[0]
        for candidate, proof in ((first, baseline), (result, evidence)):
            self.assertTrue(validate_zone_layout(building, candidate, _zone_rects(proof), minimum_adjacency_mm=2000).ok)
        report = evidence["cross_root_contact_pruning"]
        self.assertEqual(report["status"], "APPLIED")
        self.assertGreater(report["counts"]["rejected_pairs"], 0)
        self.assertEqual(report["counts"]["added_clauses"], report["counts"]["rejected_pairs"])
        before = baseline["global_diagnostics"]["searches"][0]["model"]
        after = evidence["global_diagnostics"]["searches"][0]["model"]
        self.assertEqual(before["variables"], after["variables"])
        self.assertEqual(after["constraints"], before["constraints"] + report["counts"]["added_clauses"])
        self.assertEqual(after["constraint_types"]["bool_or"], before["constraint_types"]["bool_or"] + report["counts"]["added_clauses"])
        self.assertEqual(baseline["global_diagnostics"]["candidate_family_sha256"], evidence["global_diagnostics"]["candidate_family_sha256"])
        self.assertFalse(report["original_program_infeasibility_proven"])

    def test_false_preserves_json_and_private_call_arguments(self):
        first, second = {}, {}
        with patch.object(hierarchy, "_solve_shape_layouts", wraps=hierarchy._solve_shape_layouts) as calls:
            baseline = solve_zone_layouts(_two_zones(), 1, .5, 1, evidence=first, deterministic_units=.05)[0]
            explicit = solve_zone_layouts(_two_zones(), 1, .5, 1, evidence=second, deterministic_units=.05,
                                         prune_cross_root_contacts=False)[0]
        self.assertEqual(baseline.to_dict(), explicit.to_dict())
        self.assertEqual(json.dumps(first, sort_keys=True), json.dumps(second, sort_keys=True))
        self.assertNotIn("cross_root_contact_pruning", second)
        self.assertTrue(all("prune_cross_root_contacts" not in call.kwargs for call in calls.call_args_list))

    def test_budget_skip_posts_no_clauses_to_the_actual_final_model(self):
        module, baseline, evidence = self.module(), {}, {}
        settings = {"zone_size_options_mm": {"A": [(2000, 4000)], "B": [(2000, 4000)]},
                    "minimum_adjacency_mm": 2000, "deterministic_units": .05, "global_diagnostics": True}
        solve_zone_layouts(program(), 1, .5, 1, evidence=baseline, **settings)
        with patch.object(module, "MAX_CANDIDATE_PAIRS", 0):
            result = solve_zone_layouts(program(), 1, .5, 1, evidence=evidence,
                                       prune_cross_root_contacts=True, **settings)[0]
        self.assertTrue(validate_zone_layout(program(), result, _zone_rects(evidence), minimum_adjacency_mm=2000).ok)
        pruning = evidence["cross_root_contact_pruning"]
        self.assertEqual(pruning["status"], "SKIPPED_LIMIT")
        self.assertEqual(pruning["counts"]["added_clauses"], 0)
        self.assertEqual(evidence["global_diagnostics"]["searches"][0]["model"],
                         baseline["global_diagnostics"]["searches"][0]["model"])

    def test_recursive_keyword_reaches_only_the_final_model(self):
        evidence = {}
        with patch.object(hierarchy, "_solve_shape_layouts", wraps=hierarchy._solve_shape_layouts) as calls:
            result = solve_zone_layouts(recursive_fixture.program(), 1, 1, 1, evidence=evidence,
                recursive_zones=True, prune_cross_root_contacts=True, hint_rects=recursive_fixture.source_rects(),
                zone_size_options_mm={"parent": [(8000, 6000)], "a_zone": [(4000, 6000)], "b_zone": [(4000, 6000)]})[0]
        self.assertTrue(validate_zone_layout(recursive_fixture.program(), result,
                                            recursive_fixture.zone_rects(evidence), recursive_zones=True).ok)
        opted = [call for call in calls.call_args_list if call.kwargs.get("prune_cross_root_contacts")]
        self.assertEqual(len(opted), 1)
        self.assertNotIn("stage", opted[0].kwargs)
        self.assertGreater(len(calls.call_args_list), 1)
        self.assertEqual(evidence["cross_root_contact_pruning"]["counts"]["unit_pairs"], 0)

    def test_incompatible_selected_family_is_cp_infeasible_without_original_source_proof(self):
        building, evidence = program(), {"stage_attempts": [], "variants": []}
        units = {"A": ("a1", "a2"), "B": ("b1", "b2")}
        groups = {kind: building.zone_relation_groups(kind + "_adjacency")
                  for kind in ("required", "forbidden", "preferred")}
        with self.assertRaises(ZoneHierarchyFailure) as failure:
            hierarchy._solve_shape_layouts(building, units, {"A": [stacked("a")], "B": [stacked("b", reverse=True)]},
                groups, 2000, 1, .5, 1, 1, .05, evidence, container_members=units, ancestors={},
                prune_cross_root_contacts=True, global_diagnostics=True)
        self.assertEqual(failure.exception.model_status, "INFEASIBLE")
        self.assertEqual(failure.exception.proof_scope, "selected_zone_shapes")
        self.assertFalse(failure.exception.to_dict()["proven_building_infeasible"])
        self.assertEqual(evidence["cross_root_contact_pruning"]["counts"]["added_clauses"], 1)
        self.assertEqual(evidence["global_diagnostics"]["capture_status"], "COMPLETE")
        self.assertEqual(evidence["global_diagnostics"]["searches"][0]["model_status"], "INFEASIBLE")

    def test_invalid_mode_is_rejected_before_any_cp_stage(self):
        for value in (1, 0, "true", None, []):
            with self.subTest(value=value):
                evidence = {}
                with self.assertRaises(ZoneHierarchyFailure) as failure:
                    solve_zone_layouts(program(), evidence=evidence, prune_cross_root_contacts=value)
                self.assertEqual(failure.exception.code, "invalid_contact_pruning_mode")
                self.assertEqual(failure.exception.model_status, "NOT_BUILT")
                self.assertEqual(evidence["stage_attempts"], [])


if __name__ == "__main__":
    unittest.main()

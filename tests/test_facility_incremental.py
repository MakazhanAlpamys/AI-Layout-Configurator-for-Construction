"""Edited canonical programs use old geometry softly and retain every gate."""

import hashlib
import json
import unittest
from dataclasses import replace
from unittest.mock import patch

from layout_configurator.building import BuildingIR
from layout_configurator.facility import FacilityProfile, default_facility_profile
from layout_configurator.facility_generation import InfeasibleFacilityGeneration, solve_feasible_facility_variants
from layout_configurator.models import LayoutResult, Rect
from layout_configurator.validation import validate_layout
from test_hierarchy_hints import program, source_rects


def building():
    return BuildingIR.from_mapping({
        "layout": program().to_dict(),
        "equipment": [{"id": prefix + "_machine", "room_id": prefix + "_leaf_1",
                       "width_mm": 400, "depth_mm": 200, "clearance_mm": 100}
                      for prefix in ("a", "b")],
        "flows": [{"id": "people", "type": "people", "from_ids": ["a_leaf_0"],
                   "to_ids": ["b_leaf_1"], "required": True, "minimum_clear_width_mm": 800}],
    })


def sha256(payload):
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"),
                                     ensure_ascii=False, allow_nan=False).encode("utf-8")).hexdigest()


class FacilityIncrementalTests(unittest.TestCase):
    def solve(self, current, **options):
        return solve_feasible_facility_variants(
            current, default_facility_profile(), variants=1, max_attempts=1,
            time_limit_seconds=1, seed=1, room_solver="hierarchical", **options)

    def repair(self, prior, previous, edited, profile=None, **options):
        from layout_configurator.facility_incremental import re_solve_facility_after_edit

        return re_solve_facility_after_edit(
            prior, previous, edited, profile or default_facility_profile(),
            variants=1, max_attempts=1, time_limit_seconds=1, seed=1, **options)

    def assert_all_gates(self, result):
        self.assertEqual(len(result.accepted), 1)
        candidate = result.accepted[0]
        self.assertTrue(candidate.layout_report.ok, candidate.layout_report.issues)
        self.assertTrue(candidate.equipment_report.ok, candidate.equipment_report.issues)
        self.assertTrue(candidate.flow_report.ok, candidate.flow_report.issues)
        self.assertTrue(candidate.facility_report.ok, candidate.facility_report.to_dict())

    def test_facility_bridge_exposes_actual_soft_hint_and_invocation_cache_evidence(self):
        result = self.solve(building(), hint_rects=source_rects(), cache_cluster_shapes=True)
        self.assert_all_gates(result)
        evidence = result.to_dict()["room_solver_evidence"]
        self.assertEqual(evidence["room_solver_used"], "hierarchical")
        self.assertFalse(evidence["hierarchy_hints"]["positions_are_constraints"])
        self.assertEqual(evidence["cluster_shape_cache"]["scope"], "single_hierarchical_invocation")
        self.assertGreaterEqual(evidence["cluster_shape_cache"]["hits"], 1)
        self.assertIn(evidence["hierarchy_hints"]["global_search"][0]["model_status"], {"FEASIBLE", "OPTIMAL"})

    def test_actual_room_program_edit_repairs_invalid_old_geometry_without_hard_pins(self):
        prior = building()
        previous = LayoutResult(1, source_rects())
        raw = prior.to_dict()
        next(room for room in raw["layout"]["rooms"] if room["id"] == "b_leaf_0").update(
            target_area=6, min_area=6, max_area=6, min_width=3000, min_depth=2000)
        edited = BuildingIR.from_mapping(raw)
        self.assertTrue(validate_layout(prior.layout, previous).ok)
        self.assertFalse(validate_layout(edited.layout, previous).ok)
        before = (prior.to_dict(), previous.to_dict(), edited.to_dict())
        result = self.repair(prior, previous, edited)
        self.assert_all_gates(result)
        repaired = result.accepted[0].result
        self.assertEqual(repaired.placements["b_leaf_0"].area_m2, 6)
        self.assertNotEqual(repaired.placements["b_leaf_0"], previous.placements["b_leaf_0"])
        self.assertEqual(set(repaired.placements), set(previous.placements))
        evidence = result.to_dict()["incremental_evidence"]
        self.assertEqual(evidence["source_building_sha256"], sha256(prior.to_dict()))
        self.assertEqual(evidence["source_layout_sha256"], sha256(previous.to_dict()))
        self.assertEqual(evidence["edited_building_sha256"], sha256(edited.to_dict()))
        self.assertEqual(evidence["profile_sha256"], sha256(default_facility_profile().to_dict()))
        self.assertTrue(evidence["prior_layout_validation"]["ok"])
        self.assertEqual(evidence["changed_source_ids"]["rooms"], ["b_leaf_0"])
        self.assertEqual(evidence["source_ids"]["equipment"], ["a_machine", "b_machine"])
        self.assertEqual(evidence["synthesized_fixed_room_ids"], [])
        self.assertFalse(result.room_solver_evidence["hierarchy_hints"]["positions_are_constraints"])
        self.assertEqual((prior.to_dict(), previous.to_dict(), edited.to_dict()), before)

    def test_actual_equipment_edit_rejects_stale_shape_and_repacks_current_item(self):
        prior = building()
        raw = prior.to_dict()
        next(item for item in raw["equipment"] if item["id"] == "b_machine")["width_mm"] = 2400
        edited = BuildingIR.from_mapping(raw)
        result = self.repair(prior, LayoutResult(1, source_rects()), edited)
        self.assert_all_gates(result)
        evidence = result.to_dict()["incremental_evidence"]
        self.assertEqual(evidence["changed_source_ids"]["equipment"], ["b_machine"])
        self.assertEqual(evidence["changed_source_ids"]["rooms"], [])
        self.assertGreater(result.accepted[0].result.placements["b_leaf_1"].area_m2, 4)
        event = next(item for item in result.room_solver_evidence["hierarchy_hints"]["clusters"]
                     if item["cluster_id"] == "b_hub")
        self.assertEqual(event["outcome"], "invalid_local_geometry")

    def test_changed_source_ids_are_rejected_before_any_room_search(self):
        prior = building()
        raw = prior.to_dict()
        raw["equipment"][0]["id"] = "replacement_machine"
        with patch("layout_configurator.facility_generation.solve_layouts_auto") as solver:
            with self.assertRaises(ValueError) as caught:
                self.repair(prior, LayoutResult(1, source_rects()), BuildingIR.from_mapping(raw))
        solver.assert_not_called()
        self.assertEqual(type(caught.exception).__name__, "IncrementalSourceError")
        self.assertEqual(caught.exception.evidence["code"], "source_ids_changed")
        self.assertEqual(caught.exception.evidence["source_building_sha256"], sha256(prior.to_dict()))
        self.assertEqual(caught.exception.evidence["source_layout_sha256"], sha256(LayoutResult(1, source_rects()).to_dict()))
        self.assertEqual(caught.exception.evidence["edited_building_sha256"], sha256(raw))
        self.assertFalse(caught.exception.evidence["proven_building_infeasible"])

    def test_invalid_or_off_grid_prior_geometry_is_independently_rejected(self):
        prior = building()
        for bad_rect in (Rect(2000, 0, 2000, 2000), Rect(1, 0, 2000, 2000)):
            with self.subTest(rect=bad_rect):
                previous = LayoutResult(1, {**source_rects(), "a_leaf_0": bad_rect})
                with patch("layout_configurator.facility_generation.solve_layouts_auto") as solver:
                    with self.assertRaises(ValueError) as caught:
                        self.repair(prior, previous, prior)
                solver.assert_not_called()
                self.assertEqual(caught.exception.evidence["code"], "prior_geometry_invalid")
                self.assertEqual(caught.exception.evidence["source_layout_sha256"], sha256(previous.to_dict()))
                self.assertFalse(caught.exception.evidence["proven_building_infeasible"])

    def test_prior_hard_zone_violation_is_rejected_before_any_room_search(self):
        raw = building().to_dict()
        raw["zones"] = [{"id": "left", "room_ids": ["a_hub"], "forbidden_adjacency": ["right"]},
                        {"id": "right", "room_ids": ["b_hub"]}]
        prior = BuildingIR.from_mapping(raw)
        previous = LayoutResult(1, source_rects())
        self.assertTrue(validate_layout(prior.layout, previous).ok)
        with patch("layout_configurator.facility_generation.solve_layouts_auto") as solver:
            with self.assertRaises(ValueError) as caught:
                self.repair(prior, previous, prior)
        solver.assert_not_called()
        evidence = caught.exception.evidence
        self.assertEqual(evidence["prior_zone_validation"]["status"], "FAIL")
        self.assertEqual(evidence["source_building_sha256"], sha256(prior.to_dict()))
        self.assertFalse(evidence["proven_building_infeasible"])

    def test_current_profile_failure_retains_incremental_provenance_and_gate_evidence(self):
        prior = building()
        profile = FacilityProfile.from_mapping({"name": "Pressure evidence", "version": "P01", "rules": [{
            "id": "pressure", "kind": "pressure_cascade", "title": "Declared pressure ordering",
            "source": "Project pressure specification", "edition": "P01", "effective_date": "2026-10-12",
            "evidence": ["Confirm parent-child pressure edges"], "parameters": {"direction": "parent_gt_child"}}]})
        with self.assertRaises(InfeasibleFacilityGeneration) as caught:
            self.repair(prior, LayoutResult(1, source_rects()), prior, profile)
        evidence = caught.exception.generation.to_dict()
        self.assertEqual(evidence["accepted_candidates"], 0)
        self.assertEqual(evidence["incremental_evidence"]["profile_sha256"], sha256(profile.to_dict()))
        stages = evidence["rejected_candidates"][0]["stage_evidence"]
        self.assertTrue(any(stage["stage"] == "facility" and stage["status"] == "UNKNOWN" for stage in stages))
        self.assertTrue(all(not stage["proven_building_infeasible"] for stage in stages))

    def test_changed_grid_skips_incompatible_soft_hints_without_rejecting_the_edited_program(self):
        prior = building()
        previous = LayoutResult(1, {room_id: replace(rect, x=rect.x + 100)
                                    for room_id, rect in source_rects().items()})
        edited = replace(prior, layout=replace(prior.layout, grid_mm=200))
        self.assertTrue(validate_layout(prior.layout, previous).ok)
        self.assert_all_gates(self.solve(edited))
        result = self.repair(prior, previous, edited)
        self.assert_all_gates(result)
        hints = result.incremental_evidence["hint_sources"]
        self.assertEqual(hints["retained_room_ids"], [])
        self.assertEqual({item["room_id"] for item in hints["skipped"]}, set(previous.placements))
        self.assertTrue(all("grid" in item["reason"] for item in hints["skipped"]))
        self.assertEqual(result.incremental_evidence["edited_building_sha256"], sha256(edited.to_dict()))

    def test_zone_modes_explicitly_reject_unimplemented_hint_or_cache_bridge(self):
        for strategy in ("zone_hierarchical", "zone_recursive"):
            for options in ({"hint_rects": source_rects()}, {"cache_cluster_shapes": True}):
                with self.subTest(strategy=strategy, options=options):
                    with self.assertRaises(InfeasibleFacilityGeneration) as caught:
                        solve_feasible_facility_variants(
                            building(), default_facility_profile(), variants=1, max_attempts=1,
                            time_limit_seconds=.5, seed=1, room_solver=strategy, **options)
                    failure = caught.exception.generation.room_solver_evidence["failure"]
                    self.assertEqual((failure["stage"], failure["code"], failure["model_status"]),
                                     ("input", "unsupported_warm_start", "NOT_BUILT"))
                    self.assertFalse(failure["proven_building_infeasible"])

    def test_empty_options_preserve_default_generation_and_invalid_cache_type_is_rejected(self):
        current = building()
        baseline = self.solve(current, deterministic_units=.1)
        empty = self.solve(current, deterministic_units=.1, hint_rects={}, cache_cluster_shapes=False)
        self.assertEqual(baseline.to_dict(), empty.to_dict())
        self.assertEqual(baseline.accepted[0].result.to_dict(), empty.accepted[0].result.to_dict())
        self.assertNotIn("room_solver_evidence", baseline.to_dict())
        self.assertNotIn("incremental_evidence", baseline.to_dict())
        with self.assertRaisesRegex(ValueError, "boolean"):
            self.solve(current, cache_cluster_shapes="true")


if __name__ == "__main__":
    unittest.main()

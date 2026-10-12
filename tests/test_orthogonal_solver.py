import hashlib
import importlib.util
import json
import unittest
from dataclasses import replace

from layout_configurator.models import LayoutIR, LayoutResult, Rect
from layout_configurator.orthogonal import OrthogonalPolygon
from layout_configurator.orthogonal_validation import validate_orthogonal_layout, OrthogonalDimensionPolicy
from layout_configurator.solver import InfeasibleLayout, solve_layouts


class OrthogonalSolverTests(unittest.TestCase):
    def api(self):
        self.assertIsNotNone(importlib.util.find_spec("layout_configurator.orthogonal_solver"),
                             "Bounded source-generated orthogonal candidate solver is missing")
        from layout_configurator import orthogonal_solver
        return orthogonal_solver

    def test_source_generated_l_shape_tiles_exact_20_and_16_square_metres_where_rectangles_cannot(self):
        api = self.api()
        spec = _program()
        with self.assertRaises(InfeasibleLayout):
            solve_layouts(spec, time_limit_seconds=2, deterministic_units=.05, seed=1)
        original = spec.to_dict()

        solved = api.solve_orthogonal_candidates(spec, seed=1, deterministic_units=.05)

        self.assertEqual(solved.status, "DRAFT", solved.to_dict())
        self.assertFalse(solved.ok)
        self.assertEqual(len(solved.candidates), 1)
        candidate = solved.candidates[0]
        self.assertIsInstance(candidate.result.placements["main"], OrthogonalPolygon)
        self.assertEqual(candidate.result.placements["main"].area_m2, 20)
        self.assertEqual(candidate.result.placements["corner"].area_m2, 16)
        self.assertTrue(candidate.room_report.ok)
        self.assertTrue(validate_orthogonal_layout(spec, candidate.result).ok)
        self.assertEqual(spec.to_dict(), original)
        evidence = solved.to_dict()
        self.assertEqual(evidence["backend"], "orthogonal_catalog_v1")
        self.assertEqual(evidence["full_facility_validation"]["status"], "UNKNOWN")
        expected = hashlib.sha256(json.dumps(spec.to_dict(), ensure_ascii=False, allow_nan=False,
            sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
        self.assertEqual(evidence["source_sha256"], expected)
        self.assertEqual(len(evidence["catalog_sha256"]), 64)
        self.assertEqual(candidate.result.to_dict()["geometry_version"], "0.2")

    def test_explicit_catalog_uses_actual_polygons_despite_overlapping_bounds(self):
        api = self.api()
        catalog = _catalog()
        self.assertGreater(_l_shape().bounds_rect.intersection_area(catalog["corner"][0]), 0)

        solved = api.solve_orthogonal_candidates(_program(), catalog=catalog, deterministic_units=.05)

        self.assertEqual(solved.status, "DRAFT", solved.to_dict())
        result = solved.candidates[0].result
        self.assertEqual(result.placements, {"main": _l_shape(), "corner": Rect(2000, 2000, 4000, 4000)})
        self.assertTrue(validate_orthogonal_layout(_program(), result).ok)

    def test_overlap_and_forbidden_actual_contact_are_hard_constraints(self):
        api = self.api()
        overlap = {"main": [_l_shape()], "corner": [Rect(1000, 1000, 4000, 4000)]}
        blocked = api.solve_orthogonal_candidates(_program(), catalog=overlap, deterministic_units=.05)
        self.assertFalse(blocked.candidates)
        self.assertEqual(blocked.status, "UNKNOWN")
        self.assertTrue(any("overlap" in str(item).lower() for item in blocked.to_dict()["rejections"]))
        spec = replace(_program(), rooms=(replace(_program().rooms[0], required_adjacency=(), forbidden_adjacency=("corner",)), _program().rooms[1]))
        forbidden = api.solve_orthogonal_candidates(spec, catalog=_catalog(), deterministic_units=.05)
        self.assertFalse(forbidden.candidates)
        self.assertTrue(any("forbidden" in str(item).lower() for item in forbidden.to_dict()["rejections"]))

    def test_one_contiguous_pair_door_segment_is_required_not_sum_of_contacts(self):
        api = self.api()
        comb = OrthogonalPolygon(((0, 0), (3000, 0), (3000, 600), (2000, 600),
                                  (2000, 2400), (3000, 2400), (3000, 3000), (0, 3000)))
        raw = _program().to_dict()
        raw["rooms"][0].update(target_area=7.2, min_area=7.2, max_area=7.2, min_width=500, min_depth=500)
        raw["rooms"][1].update(target_area=9, min_area=9, max_area=9, min_width=500, min_depth=500)
        solved = api.solve_orthogonal_candidates(LayoutIR.from_mapping(raw),
            catalog={"main": [comb], "corner": [Rect(3000, 0, 3000, 3000)]}, deterministic_units=.05)
        self.assertFalse(solved.candidates)
        self.assertTrue(any("contiguous" in str(item).lower() for item in solved.to_dict()["rejections"]))

    def test_final_room_qa_rejects_closed_wall_contact_and_narrow_door_approach(self):
        api = self.api()
        no_doors = replace(_program(), rooms=tuple(replace(room, required_adjacency=()) for room in _program().rooms))
        disconnected = api.solve_orthogonal_candidates(no_doors, catalog=_catalog(), deterministic_units=.05)
        self.assertFalse(disconnected.candidates)
        self.assertTrue(any("topology" in str(item).lower() for item in disconnected.to_dict()["rejections"]))
        raw = _program().to_dict()
        raw["rooms"][1].update(target_area=.81, min_area=.81, max_area=.81, min_width=800, min_depth=800)
        approach = api.solve_orthogonal_candidates(LayoutIR.from_mapping(raw),
            catalog={"main": [_l_shape()], "corner": [Rect(2000, 2000, 900, 900)]}, deterministic_units=.05)
        self.assertFalse(approach.candidates)
        self.assertTrue(any("door_approaches" in str(item).lower() for item in approach.to_dict()["rejections"]))

    def test_room_minimum_policy_and_cutout_are_evaluated_before_search(self):
        api = self.api()
        narrow = OrthogonalPolygon(((0, 0), (6000, 0), (6000, 2000), (1000, 2000), (1000, 6000), (0, 6000)))
        raw = _program().to_dict()
        raw["rooms"][0].update(target_area=16, min_area=16, max_area=16)
        rejected = api.solve_orthogonal_candidates(LayoutIR.from_mapping(raw),
            catalog={"main": [narrow], "corner": [Rect(2000, 2000, 4000, 4000)]}, deterministic_units=.05)
        self.assertFalse(rejected.candidates)
        self.assertTrue(any("min_dimension" in str(item).lower() for item in rejected.to_dict()["rejections"]))
        raw = _program().to_dict()
        raw["boundary"]["cutouts"] = [{"x": 0, "y": 0, "width": 1000, "height": 1000}]
        outside = api.solve_orthogonal_candidates(LayoutIR.from_mapping(raw), catalog=_catalog(), deterministic_units=.05)
        self.assertFalse(outside.candidates)
        self.assertTrue(any("boundary" in str(item).lower() for item in outside.to_dict()["rejections"]))

    def test_catalog_pair_attempt_and_b4_limits_report_unknown_without_fake_infeasibility(self):
        api = self.api()
        supplied = {"main": [_l_shape(), _l_shape().translated(100, 0)], "corner": _catalog()["corner"]}
        for budget in (api.OrthogonalSolverBudget(max_candidates_per_room=1),
                       api.OrthogonalSolverBudget(max_pair_checks=1),
                       api.OrthogonalSolverBudget(dimension_policy=OrthogonalDimensionPolicy(max_scan_steps=1))):
            with self.subTest(budget=budget):
                solved = api.solve_orthogonal_candidates(_program(), catalog=supplied, budget=budget, deterministic_units=.05)
                self.assertFalse(solved.ok)
                self.assertIn(solved.status, ("DRAFT", "UNKNOWN"))
                self.assertNotIn("INFEASIBLE", json.dumps(solved.to_dict()))
        tiny = api.solve_orthogonal_candidates(_program(), budget=api.OrthogonalSolverBudget(max_catalog_steps_per_room=1), deterministic_units=.05)
        self.assertEqual(tiny.status, "UNKNOWN")
        self.assertFalse(tiny.candidates)
        self.assertTrue(tiny.to_dict()["catalog_truncated"])

    def test_actual_pair_limit_stops_before_partial_compatibility_can_be_used(self):
        api = self.api()
        supplied = {"main": [_l_shape(), _l_shape().rotated_quarter_turns(2, origin=(3000, 3000))],
                    "corner": _catalog()["corner"]}
        solved = api.solve_orthogonal_candidates(_program(), catalog=supplied,
            budget=api.OrthogonalSolverBudget(max_pair_checks=1), deterministic_units=.05)
        self.assertEqual(solved.status, "UNKNOWN")
        self.assertFalse(solved.candidates)
        self.assertEqual(solved.to_dict()["stop_reason"], "pair_check_limit")
        self.assertEqual(solved.to_dict()["pair_checks"], 1)
        self.assertEqual(solved.to_dict()["attempts"], 0)

    def test_failed_independent_door_qa_excludes_only_that_combination_and_searches_again(self):
        api = self.api()
        spec = _single_program(1.2, boundary=2000)
        raw = spec.to_dict()
        raw["external_entry"] = {"id": "entry", "room_id": "main", "side": "bottom",
                                 "offset_mm": 550, "width_mm": 900}
        spec = LayoutIR.from_mapping(raw)
        bad, good = Rect(0, 0, 1000, 1200), Rect(0, 0, 1200, 1000)
        supplied = {"main": [bad, good]}
        solved = api.solve_orthogonal_candidates(spec, catalog=supplied, deterministic_units=.05)
        self.assertEqual(solved.status, "DRAFT", solved.to_dict())
        self.assertEqual(solved.candidates[0].result.placements["main"], good)
        self.assertEqual(solved.to_dict()["attempts"], 2)
        self.assertTrue(any("door_approaches" in str(item).lower() for item in solved.to_dict()["rejections"]))
        limited = api.solve_orthogonal_candidates(spec, catalog=supplied,
            budget=api.OrthogonalSolverBudget(max_attempts=1), deterministic_units=.05)
        self.assertEqual(limited.status, "UNKNOWN")
        self.assertFalse(limited.candidates)
        self.assertEqual(limited.to_dict()["stop_reason"], "attempt_limit")

    def test_generated_cutout_is_preserved_exactly_and_holes_have_negative_evidence(self):
        api = self.api()
        raw = _single_program(20).to_dict()
        raw["boundary"]["cutouts"] = [{"x": 2000, "y": 2000, "width": 4000, "height": 4000}]
        solved = api.solve_orthogonal_candidates(LayoutIR.from_mapping(raw), deterministic_units=.05)
        self.assertEqual(solved.status, "DRAFT", solved.to_dict())
        self.assertEqual(solved.candidates[0].result.placements["main"], _l_shape())
        self.assertTrue(solved.candidates[0].room_report.ok)
        raw = _single_program(35).to_dict()
        raw["boundary"]["cutouts"] = [{"x": 2000, "y": 2000, "width": 1000, "height": 1000}]
        unsupported = api.solve_orthogonal_candidates(LayoutIR.from_mapping(raw), deterministic_units=.05)
        self.assertEqual(unsupported.status, "UNKNOWN")
        self.assertFalse(unsupported.candidates)
        self.assertTrue(any("unrepresentable_clipping" in str(item) for item in unsupported.to_dict()["rejections"]))
        self.assertGreater(unsupported.to_dict()["catalog_stats"]["main"]["unrepresentable_clips"], 0)

    def test_bounded_negative_samples_keep_counts_and_latest_independent_qa_failure(self):
        api = self.api()
        no_doors = replace(_program(), rooms=tuple(replace(room, required_adjacency=()) for room in _program().rooms))
        supplied = {"main": [_l_shape(), Rect(0, 0, 4000, 5000)], "corner": _catalog()["corner"]}
        solved = api.solve_orthogonal_candidates(no_doors, catalog=supplied,
            budget=api.OrthogonalSolverBudget(max_rejections=1), deterministic_units=.05)
        evidence = solved.to_dict()
        self.assertFalse(solved.candidates)
        self.assertEqual(len(evidence["rejections"]), 1)
        self.assertEqual(evidence["rejection_counts"]["pair"], 1)
        self.assertEqual(evidence["rejection_counts"]["room_qa"], 1)
        self.assertEqual(evidence["last_rejection"]["stage"], "room_qa")
        self.assertIn("TOPOLOGY", evidence["last_rejection"]["reason"])

    def test_fixed_footprints_are_separate_source_hashed_constraints_and_are_preserved(self):
        api = self.api()
        fixed = {"corner": Rect(2000, 2000, 4000, 4000)}
        solved = api.solve_orthogonal_candidates(_program(), fixed_footprints=fixed, deterministic_units=.05)
        self.assertEqual(solved.status, "DRAFT", solved.to_dict())
        self.assertEqual(solved.candidates[0].result.placements["corner"], fixed["corner"])
        self.assertEqual(len(solved.to_dict()["fixed_constraints_sha256"]), 64)
        with self.assertRaises(ValueError):
            api.solve_orthogonal_candidates(_program(), fixed_footprints={"unknown": fixed["corner"]})
        with self.assertRaises(ValueError):
            api.solve_orthogonal_candidates(_program(), fixed_footprints={"corner": Rect(2050, 2000, 4000, 4000)})

    def test_seed_budget_catalog_order_and_fingerprints_repeat(self):
        api = self.api()
        first = api.solve_orthogonal_candidates(_program(), catalog=_catalog(), seed=7, deterministic_units=.05)
        reversed_catalog = {"corner": list(reversed(_catalog()["corner"])), "main": list(reversed(_catalog()["main"]))}
        second = api.solve_orthogonal_candidates(_program(), catalog=reversed_catalog, seed=7, deterministic_units=.05)
        self.assertEqual(first.to_dict(), second.to_dict())
        changed = api.solve_orthogonal_candidates(_program(), catalog={"main": [_l_shape()], "corner": [Rect(2100, 2000, 3900, 4000)]},
                                                   seed=7, deterministic_units=.05)
        self.assertNotEqual(first.to_dict()["catalog_sha256"], changed.to_dict()["catalog_sha256"])

    def test_partial_variant_request_remains_unknown_and_all_rectangles_keep_legacy_result(self):
        api = self.api()
        solved = api.solve_orthogonal_candidates(_single_program(20), catalog={"main": [Rect(0, 0, 4000, 5000)]},
                                                variants=2, deterministic_units=.05)
        self.assertEqual(solved.status, "UNKNOWN")
        self.assertFalse(solved.ok)
        self.assertEqual(len(solved.candidates), 1)
        self.assertEqual(solved.to_dict()["stop_reason"], "finite_catalog_exhausted")
        self.assertTrue(solved.candidates[0].room_report.ok)
        legacy = LayoutResult(1, {"main": Rect(0, 0, 4000, 5000)}, solved.candidates[0].result.objective_value)
        self.assertEqual(solved.candidates[0].result.to_dict(), legacy.to_dict())
        self.assertNotIn("geometry_version", solved.candidates[0].result.to_dict())

    def test_unprocessed_catalog_has_no_claimed_fingerprint(self):
        api = self.api()
        stopped = api.solve_orthogonal_candidates(_program(), catalog=_catalog(),
            budget=api.OrthogonalSolverBudget(max_rooms=1), deterministic_units=.05)
        self.assertEqual(stopped.status, "UNKNOWN")
        self.assertEqual(stopped.to_dict()["stop_reason"], "room_limit")
        self.assertFalse(stopped.to_dict()["catalog_fingerprint_complete"])
        self.assertIsNone(stopped.to_dict()["catalog_sha256"])

    def test_unknown_catalog_ids_malformed_source_and_budget_are_rejected(self):
        api = self.api()
        for catalog in ({"main": [_l_shape()]}, {**_catalog(), "unknown": [Rect(0, 0, 1000, 1000)]}, []):
            with self.subTest(catalog=catalog):
                with self.assertRaises(ValueError):
                    api.solve_orthogonal_candidates(_program(), catalog=catalog)
        for value in (0, True, 1.5):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    api.OrthogonalSolverBudget(max_pair_checks=value)
        with self.assertRaises(ValueError):
            api.solve_orthogonal_candidates(replace(_program(), grid_mm=True))
        for source in ({}, [], None):
            with self.subTest(source=source):
                with self.assertRaises(ValueError):
                    api.solve_orthogonal_candidates(source)


def _program():
    return LayoutIR.from_mapping({"project_name": "Orthogonal solver counterexample", "boundary": {"width": 6000, "height": 6000},
        "entry_room": "main", "rooms": [
            {"id": "main", "type": "process", "target_area": 20, "min_area": 20, "max_area": 20, "required_adjacency": ["corner"]},
            {"id": "corner", "type": "support", "target_area": 16, "min_area": 16, "max_area": 16}]})


def _l_shape():
    return OrthogonalPolygon(((0, 0), (6000, 0), (6000, 2000), (2000, 2000), (2000, 6000), (0, 6000)))


def _catalog():
    return {"main": [_l_shape()], "corner": [Rect(2000, 2000, 4000, 4000)]}


def _single_program(area, boundary=6000):
    return LayoutIR.from_mapping({"project_name": "Single-room orthogonal source", "entry_room": "main",
        "boundary": {"width": boundary, "height": boundary},
        "rooms": [{"id": "main", "type": "process", "target_area": area, "min_area": area, "max_area": area,
                   "min_width": 500, "min_depth": 500}]})

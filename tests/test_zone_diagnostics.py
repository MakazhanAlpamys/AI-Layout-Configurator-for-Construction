"""Opted final CP diagnostics describe actual finite families without verdicts."""

import hashlib
import json
import unittest
from unittest.mock import patch

from ortools.sat.python import cp_model

from layout_configurator.zone_hierarchy import ZoneHierarchyFailure, solve_zone_layouts, validate_zone_layout
import layout_configurator.zone_hierarchy as hierarchy
from test_zone_warm_start import program, source_rects, zone_rects
from test_zone_hierarchy import _two_zones


def digest(value):
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class ZoneDiagnosticsTests(unittest.TestCase):
    def solve(self, evidence, **options):
        settings = {"deterministic_units": .05, "hint_rects": source_rects()}
        settings.update(options)
        return solve_zone_layouts(program(), 1, 1, 1, recursive_zones=True,
            zone_size_options_mm={"parent": [(8000, 6000)], "a_zone": [(4000, 6000)], "b_zone": [(4000, 6000)]},
            evidence=evidence, **settings)[0]

    def test_actual_final_model_and_response_match_fresh_physical_result(self):
        evidence, observed = {}, []
        original = cp_model.CpSolver.Solve

        def observe(solver, model, *args, **kwargs):
            final = any(variable.name == "zone_parent_shape_0" for variable in model.Proto().variables)
            status = original(solver, model, *args, **kwargs)
            if final:
                observed.append((len(model.Proto().variables), len(model.Proto().constraints), solver.ResponseProto()))
            return status

        with patch.object(cp_model.CpSolver, "Solve", observe):
            result = self.solve(evidence, global_diagnostics=True)
        self.assertTrue(validate_zone_layout(program(), result, zone_rects(evidence), recursive_zones=True).ok)
        diagnostic = evidence["global_diagnostics"]
        self.assertEqual(diagnostic["capture_status"], "COMPLETE")
        self.assertEqual(diagnostic["source_sha256"], digest(program().to_dict()))
        self.assertFalse(diagnostic["original_program_infeasibility_proven"])
        self.assertEqual(len(diagnostic["searches"]), 1)
        search = diagnostic["searches"][0]
        variables, constraints, response = observed[-1]
        self.assertEqual(search["model"]["variables"], variables)
        self.assertEqual(search["model"]["constraints"], constraints)
        self.assertEqual(sum(search["model"]["constraint_types"].values()), constraints)
        self.assertGreater(search["model"]["constraint_types"]["no_overlap_2d"], 0)
        self.assertEqual(search["response"]["num_branches"], response.num_branches)
        self.assertEqual(search["response"]["num_conflicts"], response.num_conflicts)
        self.assertEqual(search["response"]["deterministic_time"], response.deterministic_time)
        self.assertEqual(search["response"]["wall_time"], response.wall_time)
        self.assertTrue(search["response"]["solution_present"])
        self.assertEqual(search["settings"]["workers"], 1)
        self.assertEqual(diagnostic["candidate_family_sha256"], digest(diagnostic["candidate_family"]))
        self.assertEqual(diagnostic["counts"]["source_rooms"], 8)
        self.assertEqual(diagnostic["counts"]["units"], 1)
        family = diagnostic["candidate_family"]
        unit = family["units"][0]
        self.assertEqual(unit["unit_id"], "parent")
        self.assertEqual(set(unit["room_ids"]), set(program().layout.room_by_id))
        self.assertEqual(unit["shape_count"], len(unit["shapes"]))
        container = zone_rects(evidence)["parent"]
        grid = family["grid_mm"]
        relative = {room_id: [round((rect.x - container.x) / grid), round((rect.y - container.y) / grid),
                              round(rect.width / grid), round(rect.height / grid)]
                    for room_id, rect in result.placements.items()}
        self.assertTrue(any(shape["rooms"] == relative for shape in unit["shapes"]))
        for shape in unit["shapes"]:
            payload = {key: value for key, value in shape.items() if key not in ("index", "sha256")}
            self.assertEqual(shape["sha256"], digest(payload))
        json.dumps(evidence, allow_nan=False)

    def test_final_unknown_keeps_complete_capture_and_no_infeasibility_claim(self):
        evidence = {}
        with self.assertRaises(ZoneHierarchyFailure) as failure:
            self.solve(evidence, global_diagnostics=True, global_time_limit_seconds=1e-9, deterministic_units=None)
        self.assertEqual(failure.exception.model_status, "UNKNOWN")
        self.assertEqual(failure.exception.stage, "global")
        diagnostic = evidence["global_diagnostics"]
        self.assertEqual(diagnostic["capture_status"], "COMPLETE")
        self.assertGreater(diagnostic["counts"]["candidate_shapes"], 0)
        self.assertEqual(diagnostic["searches"][0]["model_status"], "UNKNOWN")
        self.assertFalse(diagnostic["searches"][0]["response"]["solution_present"])
        self.assertFalse(diagnostic["original_program_infeasibility_proven"])
        self.assertTrue(any(attempt["stage"] == "subtree" and attempt["accepted"]
                            for attempt in evidence["stage_attempts"]))

    def test_default_false_preserves_result_evidence_and_private_call_shape(self):
        omitted, explicit = {}, {}
        with patch.object(hierarchy, "_solve_shape_layouts", wraps=hierarchy._solve_shape_layouts) as calls:
            first = solve_zone_layouts(_two_zones(), 1, .5, 1, deterministic_units=.05, evidence=omitted)[0]
            second = solve_zone_layouts(_two_zones(), 1, .5, 1, deterministic_units=.05,
                                       evidence=explicit, global_diagnostics=False)[0]
        self.assertEqual(first.to_dict(), second.to_dict())
        self.assertEqual(json.dumps(omitted, sort_keys=True), json.dumps(explicit, sort_keys=True))
        self.assertNotIn("global_diagnostics", explicit)
        self.assertTrue(all("global_diagnostics" not in call.kwargs for call in calls.call_args_list))

    def test_opted_keyword_reaches_only_final_global_model(self):
        with patch.object(hierarchy, "_solve_shape_layouts", wraps=hierarchy._solve_shape_layouts) as calls:
            self.solve({}, global_diagnostics=True)
        opted = [call for call in calls.call_args_list if call.kwargs.get("global_diagnostics")]
        self.assertEqual(len(opted), 1)
        self.assertNotIn("stage", opted[0].kwargs)
        self.assertGreater(len(calls.call_args_list), 1)

    def test_invalid_mode_is_rejected_before_any_local_cp_model(self):
        for value in (0, 1, "true", None, [], {}):
            with self.subTest(value=value):
                evidence = {}
                with self.assertRaises(ZoneHierarchyFailure) as failure:
                    self.solve(evidence, global_diagnostics=value)
                self.assertEqual(failure.exception.code, "invalid_diagnostics_mode")
                self.assertEqual(failure.exception.model_status, "NOT_BUILT")
                self.assertEqual(evidence["stage_attempts"], [])

    def test_capture_overflow_is_independent_of_success_and_contains_no_partial_geometry(self):
        import layout_configurator.zone_diagnostics as diagnostics

        for limit, reason in (("MAX_SOURCE_ROOMS", "source_rooms_limit"),
                              ("MAX_CANDIDATE_SHAPES", "candidate_shapes_limit"),
                              ("MAX_RECTANGLE_RECORDS", "rectangle_records_limit"),
                              ("MAX_FAMILY_BYTES", "family_bytes_limit")):
            with self.subTest(limit=limit):
                evidence = {}
                with patch.object(diagnostics, limit, 0):
                    result = self.solve(evidence, global_diagnostics=True)
                self.assertTrue(validate_zone_layout(program(), result, zone_rects(evidence), recursive_zones=True).ok)
                captured = evidence["global_diagnostics"]
                self.assertEqual(captured["capture_status"], "INCOMPLETE")
                self.assertEqual(captured["incomplete_reason"], reason)
                self.assertNotIn("candidate_family", captured)
                self.assertNotIn("candidate_family_sha256", captured)
                self.assertGreater(captured["counts"]["candidate_shapes"], 0)
                self.assertIn(captured["searches"][0]["model_status"], ("OPTIMAL", "FEASIBLE"))

    def test_each_actual_enumeration_attempt_counts_its_new_no_good(self):
        building, evidence = _two_zones(width=10000), {}
        results = solve_zone_layouts(building, 2, .5, 1, deterministic_units=.05,
                                     evidence=evidence, global_diagnostics=True)
        self.assertEqual(len(results), 2)
        for index, result in enumerate(results):
            containers = {unit_id: hierarchy.Rect(**raw)
                          for unit_id, raw in evidence["variants"][index]["zone_rects"].items()}
            self.assertTrue(validate_zone_layout(building, result, containers).ok)
        first, second = evidence["global_diagnostics"]["searches"]
        self.assertEqual(second["model"]["variables"], first["model"]["variables"])
        self.assertEqual(second["model"]["constraints"], first["model"]["constraints"] + 1)
        self.assertEqual(second["model"]["constraint_types"]["table"], first["model"]["constraint_types"].get("table", 0) + 1)
        self.assertEqual([first["attempt"], second["attempt"]], [1, 2])

    def test_effective_fixed_and_axis_constraints_are_bound_into_family_hash(self):
        building, plain, constrained = _two_zones(), {}, {}
        solve_zone_layouts(building, 1, .5, 1, deterministic_units=.05, evidence=plain, global_diagnostics=True)
        fixed = {"a": hierarchy.Rect(0, 0, 2000, 2000)}
        selected, axes = ["a"], [0, 2000, 4000, 6000, 8000]
        result = solve_zone_layouts(building, 1, .5, 1, deterministic_units=.05,
            evidence=constrained, global_diagnostics=True, fixed_rects=fixed,
            axis_aligned_room_ids=selected, structural_axes_x_mm=axes)[0]
        self.assertTrue(validate_zone_layout(building, result, zone_rects(constrained), fixed_rects=fixed,
                                            axis_aligned_room_ids=selected, structural_axes_x_mm=axes).ok)
        diagnostic = constrained["global_diagnostics"]
        self.assertEqual(diagnostic["source_sha256"], plain["global_diagnostics"]["source_sha256"])
        self.assertNotEqual(diagnostic["candidate_family_sha256"], plain["global_diagnostics"]["candidate_family_sha256"])
        effective = diagnostic["candidate_family"]["effective_constraints"]
        grid = building.layout.grid_mm
        self.assertEqual(effective["fixed_rooms"]["a"], [0, 0, round(2000 / grid), round(2000 / grid)])
        self.assertEqual(effective["axis_aligned_room_ids"], ["a"])
        self.assertEqual(effective["structural_axes_grid"]["x"], [round(value / grid) for value in axes])

    def test_nonfinite_response_metrics_are_null_and_explicitly_unavailable(self):
        original = cp_model.CpSolver.ResponseProto
        evidence = {}

        def nonfinite(solver):
            response = original(solver)
            response.wall_time = float("inf")
            response.best_objective_bound = float("nan")
            return response

        with patch.object(cp_model.CpSolver, "ResponseProto", nonfinite):
            self.solve(evidence, global_diagnostics=True)
        response = evidence["global_diagnostics"]["searches"][0]["response"]
        self.assertIsNone(response["wall_time"])
        self.assertIsNone(response["best_objective_bound"])
        self.assertEqual(set(response["unavailable_fields"]), {"wall_time", "best_objective_bound"})
        json.dumps(evidence, allow_nan=False)


if __name__ == "__main__":
    unittest.main()

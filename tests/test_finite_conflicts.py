"""Actual finite-contact proofs must never become whole-program claims."""

import hashlib
import importlib
import json
import unittest
from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import patch

from ortools.sat.python import cp_model

from layout_configurator.solver import _RoomVars, _add_required_adjacency


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
        ensure_ascii=False, allow_nan=False).encode("utf-8")).hexdigest()


def fixture(expanded=False):
    model = cp_model.CpModel()
    rooms, intervals = {}, [[], []]
    for name in ("A", "B"):
        x = model.NewIntVar(0, 4, name + "_x")
        y = model.NewIntVar(0, 4, name + "_y")
        intervals[0].append(model.NewFixedSizeIntervalVar(x, 2, name + "_ix"))
        intervals[1].append(model.NewFixedSizeIntervalVar(y, 4, name + "_iy"))
        reversed_shape = model.NewIntVar(0 if expanded and name == "B" else 1,
            1, name + "_reversed") if name == "B" else None
        for index in (1, 2):
            ry = model.NewIntVar(0, 6, f"{name.lower()}{index}_y")
            offset = (2 * reversed_shape if index == 1 else 2 - 2 * reversed_shape) if name == "B" else 2 * (index - 1)
            model.Add(ry == y + offset)
            rooms[f"{name.lower()}{index}"] = _RoomVars(x, ry, 2, 2, None)
    model.AddNoOverlap2D(*intervals)
    registry = []
    for index in (1, 2):
        _add_required_adjacency(model, rooms[f"a{index}"], rooms[f"b{index}"], 2, f"contact{index}")
        registry.append((f"contact:{index}", (len(model.Proto().constraints) - 1,),
            (f"/layout/rooms/{index-1}/required_adjacency/0",), (f"a{index}", f"b{index}")))
    model.Add(rooms["a1"].x + 2 <= 6)
    registry.append(("boundary:A:right", (len(model.Proto().constraints)-1,),
        ("/layout/boundary/width",), ("A",)))
    model.Minimize(rooms["a1"].x + rooms["b1"].x)
    source = {"layout": {"boundary": {"width": 6000, "height": 8000}, "grid_mm": 1000,
        "rooms": [{"id": f"a{i}", "required_adjacency": [f"b{i}"]} for i in (1, 2)]}}
    family = {"grid_mm": 1000, "A": {"shape": "stacked"},
        "B": {"shapes": ["reversed", "stacked"] if expanded else ["reversed"]}}
    capture = {"capture_status": "COMPLETE", "scope": "final_global_cp_selected_zone_shapes",
        "snapshot_phase": "pre_pruning_support_hints_enumeration", "source_sha256": digest(source),
        "candidate_family": family, "candidate_family_sha256": digest(family)}
    return model, registry, source, capture


class FiniteConflictTests(unittest.TestCase):
    def api(self):
        self.assertIsNotNone(importlib.util.find_spec("layout_configurator.finite_conflicts"),
            "A checked finite conflict explanation API is missing")
        return importlib.import_module("layout_configurator.finite_conflicts")

    def explain(self, expanded=False, budget=None, mutate=None):
        api = self.api()
        model, raw, source, capture = fixture(expanded)
        registry = [api.NamedConstraint(*item) for item in raw]
        expected = api.model_sha256(model)
        if mutate:
            mutate(model, registry, source, capture)
        options = {} if budget is None else {"budget": budget(api)}
        return api.explain_finite_conflict(model, registry, source=source, capture=capture,
            expected_model_sha256=expected, **options), model, source, capture

    def test_conflicting_contacts_have_checked_minimal_core_and_actual_deletion_witnesses(self):
        report, model, source, capture = self.explain()
        self.assertEqual(report["status"], "EXPLAINED")
        self.assertTrue(report["complete"])
        self.assertEqual(report["core"]["requirement_ids"], ["contact:1", "contact:2"])
        self.assertTrue(report["core"]["subset_minimal"])
        self.assertTrue(report["core"]["independently_verified"])
        trials = [item for item in report["searches"] if item["phase"] == "final_deletion"]
        self.assertEqual(len(trials), 2)
        for trial in trials:
            self.assertIn(trial["model_status"], ("FEASIBLE", "OPTIMAL"))
            self.assertTrue(trial["solution"])
        self.assertEqual(report["source"]["source_sha256"], digest(source))
        self.assertEqual(report["source"]["candidate_family_sha256"], capture["candidate_family_sha256"])
        self.assertEqual(list(model.Proto().assumptions), [])
        self.assertTrue(model.Proto().has_objective())
        self.assertFalse(report["proven_building_infeasible"])
        self.assertEqual(report["full_facility_validation"]["status"], "UNKNOWN")

    def test_expanded_family_is_feasible_with_exactly_the_same_source(self):
        report, _, source, _ = self.explain(expanded=True)
        _, _, original, _ = fixture()
        self.assertEqual(source, original)
        self.assertEqual(report["status"], "NO_CONFLICT")
        self.assertFalse(report["core"]["subset_minimal"])
        self.assertFalse(report["proven_building_infeasible"])

    def test_hash_and_explanation_preserve_exact_original_proto(self):
        api = self.api()
        model, raw, source, capture = fixture()
        original = str(model.Proto())
        expected = api.model_sha256(model)
        self.assertEqual(str(model.Proto()), original)
        report = api.explain_finite_conflict(model, [api.NamedConstraint(*item) for item in raw],
            source=source, capture=capture, expected_model_sha256=expected)
        self.assertEqual(report["status"], "EXPLAINED")
        self.assertEqual(str(model.Proto()), original)

    def test_source_family_model_and_capture_tamper_never_produce_a_core(self):
        mutations = [lambda m,r,s,c: s["layout"]["boundary"].update(width=7000),
            lambda m,r,s,c: c["candidate_family"]["B"].update(shapes=["stacked"]),
            lambda m,r,s,c: m.Add(0 == 1),
            lambda m,r,s,c: c.update(capture_status="INCOMPLETE"),
            lambda m,r,s,c: c.update(snapshot_phase="after_enumeration")]
        for mutate in mutations:
            with self.subTest(mutation=mutations.index(mutate)):
                report, *_ = self.explain(mutate=mutate)
                self.assertEqual(report["status"], "UNKNOWN")
                self.assertFalse(report["complete"])
                self.assertFalse(report["core"]["subset_minimal"])
                self.assertEqual(report["searches"], [])

    def test_solve_budget_retains_unfinished_verification_without_minimal_claim(self):
        report, *_ = self.explain(budget=lambda api: api.ConflictBudget(max_solves=3))
        self.assertEqual(report["status"], "UNKNOWN")
        self.assertEqual(len(report["searches"]), 3)
        self.assertFalse(report["core"]["subset_minimal"])
        self.assertFalse(report["complete"])

    def test_base_only_conflict_blames_no_named_requirement(self):
        api = self.api()
        model, raw, source, capture = fixture()
        model.Add(0 == 1)
        report = api.explain_finite_conflict(model, [api.NamedConstraint(*item) for item in raw],
            source=source, capture=capture, expected_model_sha256=api.model_sha256(model))
        self.assertEqual(report["status"], "UNKNOWN")
        self.assertEqual(report["reason"], "base_model_infeasible")
        self.assertEqual(report["core"]["requirement_ids"], [])

    def test_unsupported_global_enforcement_is_rejected_before_clone(self):
        api = self.api()
        model, _, source, capture = fixture()
        index = next(i for i,c in enumerate(model.Proto().constraints) if c.has_no_overlap_2d())
        registry = [api.NamedConstraint("unsupported", (index,), ("/layout/boundary/width",), ("A", "B"))]
        expected = api.model_sha256(model)
        with patch.object(cp_model.CpModel, "clone", side_effect=AssertionError("Unsafe clone")):
            report = api.explain_finite_conflict(model, registry, source=source, capture=capture,
                expected_model_sha256=expected)
        self.assertEqual(report["status"], "UNKNOWN")
        self.assertEqual(report["reason"], "unsupported_terminal_constraint")
        self.assertEqual(report["searches"], [])

    def test_malformed_registry_and_nonfinite_or_oversized_source_never_clone(self):
        api = self.api()
        cases = ["duplicate", "bool_index", "missing_path", "bad_utf8", "nan", "model_limit", "source_limit"]
        for case in cases:
            with self.subTest(case=case):
                model, raw, source, capture = fixture()
                registry = [api.NamedConstraint(*item) for item in raw]
                expected = api.model_sha256(model)
                if case == "duplicate":
                    registry.append(registry[0])
                elif case == "bool_index":
                    registry[0] = api.NamedConstraint("bad", (True,), ("/layout/boundary/width",), ("A",))
                elif case == "missing_path":
                    registry[0] = api.NamedConstraint("bad", raw[0][1], ("/layout/missing",), ("A",))
                elif case == "bad_utf8":
                    source["description"] = "\ud800"
                elif case == "nan":
                    source["number"] = float("nan")
                limits = {}
                if case == "model_limit":
                    limits["MAX_VARIABLES"] = 1
                elif case == "source_limit":
                    limits["MAX_JSON_BYTES"] = 32
                with (patch.multiple(api, **limits) if limits else nullcontext()), patch.object(cp_model.CpModel, "clone",
                        side_effect=AssertionError("Preflight must reject before clone")):
                    report = api.explain_finite_conflict(model, registry, source=source, capture=capture,
                        expected_model_sha256=expected)
                self.assertEqual(report["status"], "UNKNOWN")
                self.assertFalse(report["core"]["subset_minimal"])
                self.assertEqual(report["searches"], [])

    def test_actual_invalid_model_and_exhausted_wall_and_deterministic_budgets_are_unknown(self):
        api = self.api()
        model, raw, source, capture = fixture()
        model.Proto().variables[0].domain[0] = 5
        self.assertTrue(model.Validate())
        report = api.explain_finite_conflict(model, [api.NamedConstraint(*item) for item in raw],
            source=source, capture=capture, expected_model_sha256=api.model_sha256(model))
        self.assertEqual(report["reason"], "model_invalid")
        for budget in (api.ConflictBudget(wall_seconds=1e-12),
                       api.ConflictBudget(deterministic_units=1e-12)):
            with self.subTest(budget=budget):
                report, *_ = self.explain(budget=lambda api: budget)
                self.assertEqual(report["status"], "UNKNOWN")
                self.assertFalse(report["complete"])
                self.assertFalse(report["core"]["subset_minimal"])

    def test_forged_core_is_rejected_by_real_independent_cp_recheck(self):
        api = self.api()
        model, raw, source, capture = fixture()
        registry = [api.NamedConstraint(*item) for item in raw]
        expected = api.model_sha256(model)
        # Replace only upstream core extraction. All fresh solver calls remain real.
        with patch.object(cp_model.CpSolver, "sufficient_assumptions_for_infeasibility",
                return_value=[len(model.Proto().variables)]):
            report = api.explain_finite_conflict(model, registry, source=source, capture=capture,
                expected_model_sha256=expected)
        self.assertEqual(report["reason"], "core_recheck_not_infeasible")
        self.assertEqual(report["status"], "UNKNOWN")
        self.assertFalse(report["core"]["independently_verified"])
        self.assertIn(report["searches"][-1]["model_status"], ("OPTIMAL", "FEASIBLE"))

    def test_bool_or_and_table_terminal_clauses_are_checked_without_changing_source_model(self):
        api = self.api()
        for kind in ("bool_or", "table"):
            with self.subTest(kind=kind):
                model, raw, source, capture = fixture(expanded=True)
                if kind == "bool_or":
                    x = model.NewBoolVar("contradictory_clause")
                    model.Add(x == 0)
                    model.AddBoolOr([x])
                else:
                    x = model.NewIntVar(0, 1, "contradictory_table")
                    model.Add(x == 0)
                    model.AddAllowedAssignments([x], [[1]])
                registry = [api.NamedConstraint("single_conflict", (len(model.Proto().constraints)-1,),
                    ("/layout/boundary/width",), ("A",))]
                original = str(model.Proto())
                report = api.explain_finite_conflict(model, registry, source=source, capture=capture,
                    expected_model_sha256=api.model_sha256(model))
                self.assertEqual(report["status"], "EXPLAINED")
                self.assertEqual(report["core"]["requirement_ids"], ["single_conflict"])
                self.assertEqual(str(model.Proto()), original)

    def test_expired_total_deadline_does_not_clone_even_a_valid_model(self):
        api = self.api()
        model, raw, source, capture = fixture()
        expected = api.model_sha256(model)
        with patch.object(cp_model.CpModel, "clone", side_effect=AssertionError("Expired deadline")):
            report = api.explain_finite_conflict(model, [api.NamedConstraint(*item) for item in raw],
                source=source, capture=capture, expected_model_sha256=expected,
                budget=api.ConflictBudget(wall_seconds=1e-12))
        self.assertEqual(report["reason"], "verification_budget_exhausted")
        self.assertEqual(report["searches"], [])

    def test_missing_final_sat_witness_never_certifies_subset_minimality(self):
        original = cp_model.CpSolver.ResponseProto
        calls = 0

        def without_final_witness(solver):
            nonlocal calls
            calls += 1
            response = original(solver)
            return SimpleNamespace(wall_time=response.wall_time, deterministic_time=response.deterministic_time,
                solution=[] if calls == 7 else list(response.solution))

        # Only the response evidence is malformed; all CP solves stay real.
        with patch.object(cp_model.CpSolver, "ResponseProto", without_final_witness):
            report, *_ = self.explain()
        self.assertEqual(report["reason"], "missing_sat_witness")
        self.assertEqual(report["status"], "UNKNOWN")
        self.assertFalse(report["core"]["subset_minimal"])

    def test_invalid_budgets_and_contaminated_snapshots_do_not_enter_solver(self):
        api = self.api()
        budgets = [api.ConflictBudget(wall_seconds=value) for value in (0, -1, True, float("nan"), 11)]
        budgets += [api.ConflictBudget(max_solves=value) for value in (0, True, 513)]
        budgets += [api.ConflictBudget(deterministic_units=value) for value in (0, True, float("inf"))]
        for budget in budgets:
            with self.subTest(budget=budget):
                report, *_ = self.explain(budget=lambda api: budget)
                self.assertEqual(report["status"], "UNKNOWN")
                self.assertEqual(report["searches"], [])
        for kind in ("assumption", "hint", "enumeration"):
            with self.subTest(kind=kind):
                model, raw, source, capture = fixture()
                x = model.get_int_var_from_proto_index(0)
                if kind == "assumption":
                    model.AddAssumption(model.NewBoolVar("variant_diversity"))
                elif kind == "hint":
                    model.AddHint(x, 0)
                else:
                    model.AddForbiddenAssignments([x], [[0]])
                report = api.explain_finite_conflict(model, [api.NamedConstraint(*item) for item in raw],
                    source=source, capture=capture, expected_model_sha256=api.model_sha256(model))
                self.assertEqual(report["reason"], "unsafe_snapshot_phase")
                self.assertEqual(report["searches"], [])

    def test_nonfinite_solver_evidence_is_unknown_and_json_safe(self):
        original = cp_model.CpSolver.ResponseProto

        def nonfinite_response(solver):
            response = original(solver)
            return SimpleNamespace(wall_time=float("nan"), deterministic_time=response.deterministic_time,
                solution=list(response.solution))

        with patch.object(cp_model.CpSolver, "ResponseProto", nonfinite_response):
            report, *_ = self.explain()
        self.assertEqual(report["status"], "UNKNOWN")
        self.assertEqual(report["reason"], "unsafe_solver_response")
        json.dumps(report, allow_nan=False)


if __name__ == "__main__":
    unittest.main()

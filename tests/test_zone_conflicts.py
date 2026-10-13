"""Actual builder terminals explain only a fixed, conditioned finite family."""

from dataclasses import replace
import hashlib
import importlib
import json
import unittest
from unittest.mock import patch

from ortools.sat.python import cp_model

from layout_configurator.building import BuildingIR
from layout_configurator.finite_conflicts import ConflictBudget
from layout_configurator.models import BoundarySpec, Rect
import layout_configurator.zone_hierarchy as hierarchy
from layout_configurator.zone_hierarchy import ZoneHierarchyFailure, solve_zone_layouts
from test_zone_constraints import _single
from test_zone_contact_pruning import program, stacked


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
        separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def contact_program():
    building = program()
    return replace(building, layout=replace(building.layout, boundary=BoundarySpec(6000, 8000, ())))


class ZoneConflictTests(unittest.TestCase):
    def session(self, building, evidence, budget=None):
        self.assertIsNotNone(importlib.util.find_spec("layout_configurator.zone_conflict_registry"),
                             "The actual zone builder needs a source-bound terminal registry")
        module = importlib.import_module("layout_configurator.zone_conflict_registry")
        return module.ZoneConflictSession(building, evidence=evidence,
            budget=budget or ConflictBudget(wall_seconds=2, deterministic_units=.1))

    def contacts(self, building, session, *, expanded=False, pruning=False, support=False,
                 variants=1, hints=None, stage="global", deterministic_units=.05):
        evidence = {"stage_attempts": [], "variants": []}
        groups = {kind: building.zone_relation_groups(kind + "_adjacency")
                  for kind in ("required", "forbidden", "preferred")}
        shapes = {"A": [stacked("a")], "B": [stacked("b", reverse=True)]}
        if expanded:
            shapes["B"].append(stacked("b"))
        units = {"A": ("a1", "a2"), "B": ("b1", "b2")}
        return hierarchy._solve_shape_layouts(building, units, shapes, groups, 2000,
            variants, .5, 1, max(3, variants), deterministic_units, evidence,
            container_members=units, ancestors={}, conflict_session=session,
            prune_cross_root_contacts=pruning, cross_root_contact_support=support,
            hints=hints, stage=stage, stage_unit="parent" if stage == "subtree" else None)

    def test_actual_contacts_have_source_terminals_and_fresh_deletion_witnesses(self):
        building, evidence = contact_program(), {}
        session = self.session(building, evidence)
        with self.assertRaises(ZoneHierarchyFailure) as caught:
            self.contacts(building, session)
        self.assertEqual(caught.exception.model_status, "INFEASIBLE")
        proof = evidence["conflict_explanations"]["explanations"][0]["report"]
        self.assertEqual(proof["status"], "EXPLAINED")
        self.assertTrue(proof["complete"])
        self.assertEqual(proof["core"]["requirement_ids"],
                         ['room_required:["a1","b1"]', 'room_required:["a2","b2"]'])
        self.assertTrue(proof["core"]["subset_minimal"])
        trials = [entry for entry in proof["searches"] if entry["phase"] == "final_deletion"]
        self.assertEqual(len(trials), 2)
        for trial in trials:
            self.assertIn(trial["model_status"], ("FEASIBLE", "OPTIMAL"))
            self.assertTrue(trial["solution"])
        self.assertFalse(proof["proven_building_infeasible"])
        self.assertEqual(proof["full_facility_validation"]["status"], "UNKNOWN")
        self.assertIn("Source-dependent", proof["family_conditioning"])
        binding = evidence["conflict_explanations"]["captures"][0]["binding"]
        self.assertEqual(binding["original_building_sha256"], digest(building.to_dict()))
        self.assertEqual(binding["stage_building_sha256"], binding["original_building_sha256"])
        self.assertEqual(binding["diagnostic_envelope_sha256"], proof["source"]["source_sha256"])

    def test_expanded_same_source_is_feasible_and_does_not_run_conflict_solves(self):
        building, before, expanded = contact_program(), {}, {}
        with self.assertRaises(ZoneHierarchyFailure):
            self.contacts(building, self.session(building, before))
        result = self.contacts(building, self.session(building, expanded), expanded=True)[0]
        self.assertEqual(set(result.placements), {"a1", "a2", "b1", "b2"})
        first = before["conflict_explanations"]["captures"][0]
        second = expanded["conflict_explanations"]["captures"][0]
        self.assertEqual(first["binding"]["original_building_sha256"], second["binding"]["original_building_sha256"])
        self.assertNotEqual(first["capture"]["candidate_family_sha256"], second["capture"]["candidate_family_sha256"])
        self.assertEqual(expanded["conflict_explanations"]["explanations"], [])
        self.assertEqual(expanded["conflict_explanations"]["solves_used"], 0)

    def test_actual_fixed_axis_conflict_has_two_checked_terminal_groups_in_both_modes(self):
        for recursive in (False, True):
            with self.subTest(recursive=recursive):
                evidence = {}
                with self.assertRaises(ZoneHierarchyFailure):
                    solve_zone_layouts(_single(), 1, .5, 1, evidence=evidence,
                        recursive_zones=recursive, explain_conflicts=True,
                        conflict_budget=ConflictBudget(wall_seconds=2, deterministic_units=.1),
                        fixed_rects={"a": Rect(0, 0, 2000, 2000)},
                        axis_aligned_room_ids=["a"], structural_axes_x_mm=[3000, 5000])
                proof = evidence["conflict_explanations"]["explanations"][-1]["report"]
                self.assertEqual(proof["status"], "EXPLAINED")
                self.assertEqual(proof["core"]["requirement_ids"], ['room_axis:["a","x"]', 'room_fixed:["a"]'])
                trials = [entry for entry in proof["searches"] if entry["phase"] == "final_deletion"]
                self.assertEqual(len(trials), 2)
                self.assertTrue(all(entry["solution"] for entry in trials))
                self.assertFalse(proof["original_program_infeasibility_proven"])
                if recursive:
                    projected = [item for item in evidence["conflict_explanations"]["captures"] if item["stage"] == "subtree"]
                    self.assertTrue(projected)
                    self.assertTrue(all(item["binding"]["projection"] == "bounded_subtree_stage_projection" for item in projected))
                    self.assertTrue(all(item["binding"]["original_building_sha256"] != item["binding"]["stage_building_sha256"] for item in projected))

    def test_derived_pruning_and_support_do_not_contaminate_captured_core(self):
        proofs = []
        for pruning, support in ((False, False), (True, False), (False, True), (True, True)):
            with self.subTest(pruning=pruning, support=support):
                building, evidence = contact_program(), {}
                with self.assertRaises(ZoneHierarchyFailure):
                    self.contacts(building, self.session(building, evidence), pruning=pruning, support=support)
                record = evidence["conflict_explanations"]
                proof = record["explanations"][0]["report"]
                self.assertEqual(proof["status"], "EXPLAINED")
                self.assertEqual(proof["core"]["requirement_ids"],
                                 ['room_required:["a1","b1"]', 'room_required:["a2","b2"]'])
                proofs.append((record["captures"][0]["model_sha256"], proof["source"]["registry_sha256"]))
        self.assertTrue(all(item == proofs[0] for item in proofs))

    def test_shared_retry_budget_never_restarts_for_a_subtree(self):
        building, evidence = contact_program(), {}
        session = self.session(building, evidence, ConflictBudget(wall_seconds=2, max_solves=3, deterministic_units=.1))
        for _ in range(2):
            with self.assertRaises(ZoneHierarchyFailure):
                self.contacts(building, session, stage="subtree")
        record = evidence["conflict_explanations"]
        self.assertEqual(record["solves_used"], 3)
        self.assertEqual(len(record["explanations"]), 2)
        for entry in record["explanations"]:
            self.assertEqual(entry["report"]["status"], "UNKNOWN")
            self.assertFalse(entry["report"]["complete"])
            self.assertFalse(entry["report"]["core"]["subset_minimal"])
        self.assertEqual(record["explanations"][1]["report"]["searches"], [])

    def test_default_false_and_true_keep_entire_real_production_proto_unchanged(self):
        actual_solve = cp_model.CpSolver.Solve
        models, outputs, evidence = [], [], []
        for options in ({}, {"explain_conflicts": False}, {"explain_conflicts": True}):
            seen, proof = [], {}
            def observe(solver, model, *args, **kwargs):
                if not any(v.name.startswith("conflict_requirement_") for v in model.Proto().variables):
                    seen.append(str(model.Proto()))
                return actual_solve(solver, model, *args, **kwargs)
            with patch.object(cp_model.CpSolver, "Solve", observe):
                result = solve_zone_layouts(_single(), 2, .5, 1, evidence=proof,
                    deterministic_units=.05, **options)
            models.append(seen)
            outputs.append([item.to_dict() for item in result])
            evidence.append(proof)
        self.assertEqual(models[0], models[1])
        self.assertEqual(models[0], models[2])
        self.assertEqual(outputs[0], outputs[1])
        self.assertEqual(outputs[0], outputs[2])
        self.assertEqual(evidence[0], evidence[1])
        self.assertNotIn("conflict_explanations", evidence[1])
        self.assertEqual(evidence[2]["conflict_explanations"]["explanations"], [])
        captured = evidence[2]["conflict_explanations"]["captures"][0]
        self.assertEqual(captured["capture"]["snapshot_phase"], "pre_pruning_support_hints_enumeration")
        self.assertEqual(captured["snapshot_state"], {"objective": True, "hints": 0, "assumptions": 0, "enumeration_exclusions": 0})

    def test_actual_hints_and_enumeration_are_excluded_from_the_snapshot(self):
        actual_solve, models, snapshots = cp_model.CpSolver.Solve, [], []
        module = importlib.import_module("layout_configurator.zone_conflict_registry")
        actual_capture = module.ZoneConflictSession.capture
        def capture(session, model, *args, **kwargs):
            snapshots.append(str(model.Proto()))
            return actual_capture(session, model, *args, **kwargs)
        def observe(solver, model, *args, **kwargs):
            models.append(str(model.Proto()))
            return actual_solve(solver, model, *args, **kwargs)
        evidence = {}
        with patch.object(module.ZoneConflictSession, "capture", capture), patch.object(cp_model.CpSolver, "Solve", observe):
            solve_zone_layouts(_single(), 2, .5, 1, deterministic_units=.05, evidence=evidence,
                explain_conflicts=True, hint_rects={"a": Rect(1000, 1000, 2000, 2000)})
        self.assertEqual(len(snapshots), 1)
        self.assertNotIn("solution_hint", snapshots[0])
        self.assertNotIn("negated: true", snapshots[0])
        self.assertTrue(any("solution_hint" in model for model in models))
        self.assertTrue(any("negated: true" in model for model in models))
        self.assertEqual(evidence["conflict_explanations"]["explanations"], [])

    def test_rehashed_public_capture_tamper_cannot_rebind_a_core_to_another_family(self):
        building, evidence = contact_program(), {}
        session = self.session(building, evidence)
        actual_solve = cp_model.CpSolver.Solve
        changed = False
        def observe(solver, model, *args, **kwargs):
            nonlocal changed
            if not changed:
                record = evidence["conflict_explanations"]["captures"][0]
                record["capture"]["candidate_family"]["units"][0]["shapes"][0]["width"] += 1
                record["capture"]["candidate_family_sha256"] = digest(record["capture"]["candidate_family"])
                changed = True
            return actual_solve(solver, model, *args, **kwargs)
        with patch.object(cp_model.CpSolver, "Solve", observe), self.assertRaises(ZoneHierarchyFailure):
            self.contacts(building, session)
        proof = evidence["conflict_explanations"]["explanations"][0]["report"]
        self.assertEqual(proof["status"], "UNKNOWN")
        self.assertFalse(proof["complete"])
        self.assertFalse(proof["core"]["subset_minimal"])
        self.assertEqual(proof["searches"], [])

    def test_reverse_declarations_and_ambiguous_zone_labels_bind_exact_source_pairs(self):
        raw = {
            "layout": {"boundary": {"width": 8000, "height": 8000}, "grid_mm": 1000,
                "rooms": [{"id": name, "target_area": 4, "min_area": 4, "max_area": 4,
                           "min_width": 2000, "min_depth": 2000,
                           "required_adjacency": {"r1": ["r3"], "r2": ["r4"], "r3": ["r2"], "r4": []}[name],
                           "forbidden_adjacency": [other] if other else []}
                          for name, other in (("r1", "r2"), ("r2", "r1"), ("r3", None), ("r4", None))]},
            "zones": [{"id": name, "room_ids": [room], "forbidden_adjacency": [other]}
                      for name, room, other in (("a-b", "r1", "c"), ("c", "r2", "a-b"),
                                                ("a", "r3", "b-c"), ("b-c", "r4", "a"))],
        }
        building, evidence = BuildingIR.from_mapping(raw), {}
        solve_zone_layouts(building, 1, .5, 1, deterministic_units=.05, evidence=evidence,
                           explain_conflicts=True)
        registry = {item["id"]: item for item in evidence["conflict_explanations"]["captures"][0]["requirements"]}
        self.assertEqual(registry['room_forbidden:["r1","r2"]']["source_paths"],
                         ["/original_building/layout/rooms/0/forbidden_adjacency/0",
                          "/original_building/layout/rooms/1/forbidden_adjacency/0"])
        self.assertEqual(registry['room_forbidden:["r1","r2"]']["source_values"], ["r2", "r1"])
        self.assertEqual(registry['zone_forbidden:["a-b","c"]']["source_paths"],
                         ["/original_building/zones/0/forbidden_adjacency/0",
                          "/original_building/zones/1/forbidden_adjacency/0"])
        self.assertEqual(registry['zone_forbidden:["a","b-c"]']["source_paths"],
                         ["/original_building/zones/2/forbidden_adjacency/0",
                          "/original_building/zones/3/forbidden_adjacency/0"])
        self.assertEqual(len(registry['room_forbidden:["r1","r2"]']["constraint_indices"]), 1)
        self.assertEqual(len(registry['zone_forbidden:["a-b","c"]']["constraint_indices"]), 1)
        self.assertEqual(evidence["conflict_explanations"]["explanations"], [])

    def test_actual_unknown_and_enumeration_exhaustion_never_trigger_an_initial_core(self):
        building, evidence = contact_program(), {}
        session = self.session(building, evidence)
        with self.assertRaises(ZoneHierarchyFailure) as caught:
            self.contacts(building, session, deterministic_units=1e-12)
        self.assertEqual(caught.exception.model_status, "UNKNOWN")
        self.assertEqual(evidence["conflict_explanations"]["explanations"], [])
        self.assertEqual(evidence["conflict_explanations"]["solves_used"], 0)
        evidence = {}
        with self.assertRaises(ZoneHierarchyFailure) as caught:
            solve_zone_layouts(_single(), 2, .5, 1, deterministic_units=.05, evidence=evidence,
                               explain_conflicts=True, fixed_rects={"a": Rect(0, 0, 2000, 2000)})
        self.assertEqual(caught.exception.model_status, "INFEASIBLE")
        self.assertTrue(evidence["stage_attempts"][-2]["accepted"])
        self.assertEqual(evidence["conflict_explanations"]["explanations"], [])
        self.assertEqual(evidence["conflict_explanations"]["solves_used"], 0)

    def test_capture_and_wall_limits_keep_actual_failure_unknown(self):
        module = importlib.import_module("layout_configurator.zone_conflict_registry")
        for limit in ("capture", "wall"):
            with self.subTest(limit=limit):
                building, evidence = contact_program(), {}
                budget = ConflictBudget(wall_seconds=1e-12 if limit == "wall" else 2)
                session = self.session(building, evidence, budget)
                with patch.object(module, "MAX_CAPTURE_BYTES", 0 if limit == "capture" else 32 * 1024 * 1024):
                    with self.assertRaises(ZoneHierarchyFailure) as caught:
                        self.contacts(building, session)
                self.assertEqual(caught.exception.model_status, "INFEASIBLE")
                record = evidence["conflict_explanations"]
                self.assertEqual(record["captures"][0]["capture"]["capture_status"], "INCOMPLETE")
                proof = record["explanations"][0]["report"]
                self.assertEqual(proof["status"], "UNKNOWN")
                self.assertFalse(proof["complete"])
                self.assertFalse(proof["core"]["subset_minimal"])
                self.assertEqual(proof["searches"], [])

    def test_cutout_is_unnamed_base_and_fixed_pin_blame_is_scoped(self):
        evidence = {}
        with self.assertRaises(ZoneHierarchyFailure):
            solve_zone_layouts(_single(cutout=True), 1, .5, 1, deterministic_units=.05,
                explain_conflicts=True, evidence=evidence,
                fixed_rects={"a": Rect(3000, 2000, 2000, 2000)})
        proof = evidence["conflict_explanations"]["explanations"][0]["report"]
        # The fixed room is removable; the cutout itself remains in the base.
        self.assertEqual(proof["status"], "EXPLAINED")
        self.assertEqual(proof["core"]["requirement_ids"], ['room_fixed:["a"]'])
        self.assertFalse(proof["original_program_infeasibility_proven"])
        building = _single()
        building = replace(building, layout=replace(building.layout,
            boundary=BoundarySpec(2000, 2000, (Rect(0, 0, 2000, 2000),))))
        evidence = {"stage_attempts": [], "variants": []}
        session = self.session(building, evidence)
        with self.assertRaises(ZoneHierarchyFailure) as caught:
            hierarchy._solve_shape_layouts(building, {"parent": ("a",)},
                {"parent": [hierarchy._ZoneShape(2000, 2000, {"a": Rect(0, 0, 2000, 2000)})]},
                {"required": (), "forbidden": (), "preferred": ()}, 900, 1, .5, 1, 3, .05,
                evidence, container_members={"parent": ("a",)}, ancestors={}, conflict_session=session)
        self.assertEqual(caught.exception.model_status, "INFEASIBLE")
        proof = evidence["conflict_explanations"]["explanations"][0]["report"]
        self.assertEqual(proof["status"], "UNKNOWN")
        self.assertEqual(proof["reason"], "base_model_infeasible")
        self.assertEqual(proof["core"]["requirement_ids"], [])
        self.assertFalse(proof["core"]["subset_minimal"])

    def test_invalid_controls_reject_before_cp_stages(self):
        invalid = [{"explain_conflicts": value} for value in (1, "true", None, [])]
        invalid += [{"explain_conflicts": True, "conflict_budget": value} for value in
                    ({}, ConflictBudget(wall_seconds=0), ConflictBudget(max_solves=True), ConflictBudget(deterministic_units=float("nan")))]
        invalid.append({"conflict_budget": ConflictBudget()})
        for options in invalid:
            with self.subTest(options=options):
                evidence = {}
                with self.assertRaises(ZoneHierarchyFailure) as caught:
                    solve_zone_layouts(_single(), 1, .5, 1, evidence=evidence, **options)
                self.assertEqual(caught.exception.stage, "input")
                self.assertEqual(caught.exception.model_status, "NOT_BUILT")
                self.assertEqual(evidence["stage_attempts"], [])


if __name__ == "__main__":
    unittest.main()

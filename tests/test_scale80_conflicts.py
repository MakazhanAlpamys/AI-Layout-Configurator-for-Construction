"""Actual supervised zone conflict evidence has a separate shared allowance."""

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from layout_configurator.building import BuildingIR
from layout_configurator.facility import default_facility_profile
from layout_configurator.finite_conflicts import ConflictBudget
from layout_configurator.qa import validate_building_set
import test_scale80_warmstart_acceptance as existing_runner


def triangle_program():
    return BuildingIR.from_mapping({"layout": {
        "project_name": "Source triangle contact conflict", "grid_mm": 100,
        "boundary": {"width": 12000, "height": 8000}, "entry_room": "a",
        "door_width_mm": 2000, "wall_thickness_mm": 200,
        "rooms": [{"id": k, "target_area": 16, "min_area": 16, "max_area": 16,
            "min_width": 4000, "min_depth": 4000,
            "required_adjacency": [x for x in ("a", "b", "c") if x != k]}
            for k in ("a", "b", "c")]},
        "zones": [{"id": "zone-" + k, "room_ids": [k]} for k in ("a", "b", "c")]})


class Scale80ConflictTests(unittest.TestCase):
    def runner(self):
        return existing_runner.Scale80WarmstartAcceptanceTests().runner()

    def test_tiny_actual_triangle_has_checked_core_and_each_final_deletion_is_sat(self):
        runner, source = self.runner(), triangle_program()
        budget = ConflictBudget(wall_seconds=2, max_solves=64, deterministic_units=.1)
        report = runner.new_report(120, room_solver="zone_recursive", zone_explain_conflicts=True,
            zone_conflict_budget=budget)
        with tempfile.TemporaryDirectory() as directory:
            self.assertFalse(runner.accept_program(source, {}, default_facility_profile(), report,
                Path(directory), room_solver="zone_recursive", room_seconds=.5, seed=1,
                zone_explain_conflicts=True, zone_conflict_budget=budget))
            self.assertFalse((Path(directory) / "bundle").exists())
        self.assertEqual(report["status"], "ROOM_SEARCH_FAILED")
        self.assertEqual(report["error"]["stage_evidence"]["model_status"], "INFEASIBLE")
        evidence = report["room_solver"]["conflict_explanations"]
        self.assertEqual(len(evidence["explanations"]), 1)
        proof = evidence["explanations"][0]["report"]
        self.assertEqual(proof["status"], "EXPLAINED")
        self.assertTrue(proof["core"]["independently_verified"])
        self.assertTrue(proof["core"]["subset_minimal"])
        self.assertEqual(proof["core"]["requirement_ids"], [
            'room_required:["a","b"]', 'room_required:["a","c"]', 'room_required:["b","c"]'])
        trials = [v for v in proof["searches"] if v["phase"] == "final_deletion"]
        self.assertEqual(len(trials), 3)
        self.assertTrue(all(v["model_status"] in ("OPTIMAL", "FEASIBLE") and v["solution"] for v in trials))
        self.assertEqual(evidence["captures"][0]["binding"]["original_building_sha256"], runner.digest(source.to_dict()))
        self.assertEqual(evidence["captures"][0]["capture"]["snapshot_phase"], "pre_pruning_support_hints_enumeration")
        self.assertFalse(proof["original_program_infeasibility_proven"])
        self.assertEqual(proof["full_facility_validation"]["status"], "UNKNOWN")
        self.assertEqual(proof["engineering_acceptance"], "PENDING")

    def test_accepted_actual_recursive_full_producer_has_snapshots_and_zero_diagnostic_solves(self):
        from test_zone_contact_pruning import program
        import layout_configurator.zone_conflict_registry as registry

        runner = self.runner()
        report = runner.new_report(120, room_solver="zone_recursive", zone_explain_conflicts=True)
        with tempfile.TemporaryDirectory() as directory, patch.object(registry, "explain_finite_conflict",
                side_effect=AssertionError("Accepted production snapshots must not run diagnostic CP")):
            self.assertTrue(runner.accept_program(program().with_required_flow_opening_width(), {},
                default_facility_profile(), report, Path(directory), room_solver="zone_recursive",
                room_seconds=1, equipment_seconds=.5, seed=1, zone_explain_conflicts=True), report)
            actual = validate_building_set(Path(directory) / "bundle", expected_variants=1,
                profile=default_facility_profile())
            self.assertTrue(actual.ok, actual.to_dict())
            manifest = json.loads((Path(directory) / "bundle/manifest.json").read_text())
            self.assertIn("conflict_explanations", manifest["variants"][0]["generation"]["room_solver_evidence"])
        evidence = report["room_solver"]["conflict_explanations"]
        self.assertTrue(evidence["captures"])
        self.assertEqual(evidence["explanations"], [])
        self.assertEqual(evidence["solves_used"], 0)
        self.assertTrue(all(g["ok"] for g in report["gates"].values()))
        self.assertEqual(report["ifc_express"]["issues"], [])
        self.assertFalse(report["general_scratch80_acceptance"])

    def test_one_shared_budget_exhaustion_is_unknown_and_preserves_failure(self):
        runner = self.runner()
        for budget in (ConflictBudget(wall_seconds=2, max_solves=1), ConflictBudget(wall_seconds=1e-12)):
            with self.subTest(budget=budget), tempfile.TemporaryDirectory() as directory:
                report = runner.new_report(120, room_solver="zone_recursive", zone_explain_conflicts=True,
                    zone_conflict_budget=budget)
                self.assertFalse(runner.accept_program(triangle_program(), {}, default_facility_profile(), report,
                    Path(directory), room_solver="zone_recursive", room_seconds=.5, seed=1,
                    zone_explain_conflicts=True, zone_conflict_budget=budget))
                evidence = report["room_solver"]["conflict_explanations"]
                self.assertLessEqual(evidence["solves_used"], budget.max_solves)
                self.assertEqual(evidence["budget"]["deadline_origin"], "first_pre_pruning_snapshot")
                proof = evidence["explanations"][-1]["report"]
                self.assertEqual(proof["status"], "UNKNOWN")
                self.assertFalse(proof["complete"])
                self.assertFalse(proof["core"]["subset_minimal"])
                self.assertEqual(report["status"], "ROOM_SEARCH_FAILED")
                self.assertFalse((Path(directory) / "bundle").exists())

    def test_strict_typed_controls_forward_effective_budget_without_changing_defaults(self):
        runner = self.runner()
        for backend in ("hierarchical", "zone_recursive"):
            self.assertEqual(runner.new_report(120, room_solver=backend),
                runner.new_report(120, room_solver=backend, zone_explain_conflicts=False))
            self.assertEqual(runner._zone_search_options(backend, None, 1),
                runner._zone_search_options(backend, None, 1, explain_conflicts=False))
        budget = ConflictBudget(wall_seconds=2, max_solves=8, deterministic_units=None)
        options = runner._zone_search_options("zone_recursive", None, 1,
            explain_conflicts=True, conflict_budget=budget)
        self.assertIs(options["conflict_budget"], budget)
        self.assertEqual(options["explain_conflicts"], True)
        opted = runner.new_report(120, room_solver="zone_recursive", zone_explain_conflicts=True,
            zone_conflict_budget=budget)
        self.assertEqual(opted["settings"]["zone_conflict_budget"], {
            "wall_seconds": 2, "max_solves": 8, "deterministic_units": None})
        self.assertIs(type(opted["settings"]["zone_conflict_budget"]["wall_seconds"]), int)
        for value in (1, 0, None, "true", []):
            with self.subTest(value=value), self.assertRaises(ValueError):
                runner.new_report(120, zone_explain_conflicts=value)
        invalid = [{"conflict_budget": budget}, {"explain_conflicts": True, "conflict_budget": {}},
            {"explain_conflicts": True, "conflict_budget": ConflictBudget(max_solves=True)},
            {"explain_conflicts": True, "conflict_budget": ConflictBudget(wall_seconds=11)}]
        for options in invalid:
            with self.subTest(options=options), self.assertRaises(ValueError):
                runner._zone_search_options("zone_recursive", None, 1, **options)
        with self.assertRaises(ValueError):
            runner._zone_search_options("hierarchical", None, 1, explain_conflicts=True)

    def test_budget_json_is_bounded_strict_and_preserves_actual_numeric_types(self):
        runner = self.runner()
        raw = '{"wall_seconds":2,"max_solves":8,"deterministic_units":null}'
        budget = runner._decode_zone_conflict_budget(raw)
        self.assertEqual(budget, ConflictBudget(wall_seconds=2, max_solves=8, deterministic_units=None))
        self.assertIs(type(budget.wall_seconds), int)
        self.assertIs(type(runner._decode_zone_conflict_budget('{"wall_seconds":2.0}').wall_seconds), float)
        invalid = ['', '{}', 'null', '[]', 'true', '"budget"',
            '{"wall_seconds":2,"wall_seconds":3}', '{"wall_seconds":NaN}', '{"wall_seconds":Infinity}',
            '{"wall_seconds":"2"}', '{"wall_seconds":true}', '{"max_solves":2.0}',
            '{"max_solves":513}', '{"unknown":2}', '{"wall_seconds":11}', '{"deterministic_units":0}',
            '{"max_solves":' + '9' * 500 + '}', ' ' * 1025 + '{"wall_seconds":2}']
        for value in invalid:
            with self.subTest(value=value[:50]), self.assertRaises(ValueError):
                runner._decode_zone_conflict_budget(value)

    def test_invalid_main_budget_or_mode_retains_input_rejected_before_worker(self):
        runner = self.runner()
        controls = [[], ["--zone-explain-conflicts"]]
        bad = [('{}', controls[1]), ('null', controls[1]), ('{"wall_seconds":true}', controls[1]),
            ('{"max_solves":2}', controls[0]), ('{"unknown":1}', controls[1]),
            ('{"wall_seconds":2,"wall_seconds":3}', controls[1])]
        for raw, flags in bad:
            with self.subTest(raw=raw, flags=flags), tempfile.TemporaryDirectory() as directory:
                with patch.object(runner.subprocess, "Popen") as process:
                    self.assertEqual(runner.main(["--output", directory, "--room-solver", "zone_recursive",
                        "--zone-conflict-budget-json", raw, *flags]), 2)
                process.assert_not_called()
                report = json.loads((Path(directory) / "acceptance-report.json").read_text())
                self.assertEqual(report["status"], "INPUT_REJECTED")
        with tempfile.TemporaryDirectory() as directory, patch.object(runner.subprocess, "Popen") as process:
            self.assertEqual(runner.main(["--output", directory, "--zone-explain-conflicts"]), 2)
            process.assert_not_called()

    def test_invalid_public_controls_reject_before_room_backend_and_noninfeasible_status_has_no_core(self):
        from ortools.sat.python import cp_model
        from layout_configurator.models import Rect
        from layout_configurator.zone_hierarchy import _ZoneShape
        import layout_configurator.zone_hierarchy as hierarchy
        import layout_configurator.zone_conflict_registry as registry

        runner = self.runner()
        bad = [{"zone_explain_conflicts": 1}, {"zone_conflict_budget": ConflictBudget()},
            {"zone_explain_conflicts": True, "zone_conflict_budget": {}},
            {"zone_explain_conflicts": True, "zone_conflict_budget": ConflictBudget(deterministic_units=float("nan"))}]
        for options in bad:
            with self.subTest(options=options), tempfile.TemporaryDirectory() as directory, \
                    patch.object(hierarchy, "solve_zone_layouts") as solve:
                with self.assertRaises(ValueError):
                    runner.accept_program(triangle_program(), {}, default_facility_profile(), runner.new_report(120),
                        Path(directory), room_solver="zone_recursive", **options)
                solve.assert_not_called()
        shapes = {"zone-" + k: [_ZoneShape(4000, 4000, {k: Rect(0, 0, 4000, 4000)})] for k in ("a", "b", "c")}
        for status in (cp_model.UNKNOWN, cp_model.MODEL_INVALID):
            with self.subTest(status=status), tempfile.TemporaryDirectory() as directory, \
                    patch.object(hierarchy, "_recursive_shapes", return_value=shapes), \
                    patch.object(cp_model.CpSolver, "Solve", return_value=status), \
                    patch.object(registry, "explain_finite_conflict", side_effect=AssertionError("Only initial INFEASIBLE can diagnose")):
                report = runner.new_report(120, room_solver="zone_recursive", zone_explain_conflicts=True)
                self.assertFalse(runner.accept_program(triangle_program(), {}, default_facility_profile(), report,
                    Path(directory), room_solver="zone_recursive", zone_explain_conflicts=True))
                self.assertEqual(report["status"], "ROOM_SEARCH_FAILED")
                self.assertEqual(report["room_solver"]["conflict_explanations"]["solves_used"], 0)
                self.assertEqual(report["room_solver"]["conflict_explanations"]["explanations"], [])
                self.assertFalse((Path(directory) / "bundle").exists())

    def test_supervisor_forwards_exact_json_and_keeps_whole_request_timeout(self):
        runner = self.runner()
        class ExpiredProcess:
            def wait(self, **_):
                if hasattr(self, "killed"):
                    return 124
                raise runner.subprocess.TimeoutExpired("worker", .01)
            def kill(self):
                self.killed = True
        for opted in (False, True):
            with self.subTest(opted=opted), tempfile.TemporaryDirectory() as directory:
                args = ["--output", directory, "--max-seconds", ".01", "--room-solver", "zone_recursive"]
                if opted:
                    args += ["--zone-explain-conflicts", "--zone-conflict-budget-json",
                        '{"wall_seconds":2,"max_solves":8,"deterministic_units":null}']
                with patch.object(runner.subprocess, "Popen", return_value=ExpiredProcess()) as process:
                    self.assertEqual(runner.main(args), 124)
                command = process.call_args.args[0]
                self.assertEqual("--zone-explain-conflicts" in command, opted)
                self.assertEqual("--zone-conflict-budget-json" in command, opted)
                self.assertIs(process.call_args.kwargs["shell"], False)
                report = json.loads((Path(directory) / "acceptance-report.json").read_text())
                self.assertEqual(report["status"], "BUDGET_EXCEEDED")
                self.assertEqual(report["settings"]["max_total_seconds"], .01)
                if opted:
                    forwarded = json.loads(command[command.index("--zone-conflict-budget-json") + 1])
                    self.assertEqual(forwarded, report["settings"]["zone_conflict_budget"])
                    self.assertIs(type(forwarded["wall_seconds"]), int)
                else:
                    self.assertNotIn("zone_explain_conflicts", report["settings"])
                    self.assertNotIn("zone_conflict_budget", report["settings"])

    def test_default_false_and_true_leave_accepted_production_protos_and_kwargs_unchanged(self):
        from ortools.sat.python import cp_model
        from test_zone_contact_pruning import program
        import layout_configurator.zone_hierarchy as hierarchy

        runner = self.runner()
        actual_solve, original = cp_model.CpSolver.Solve, hierarchy.solve_zone_layouts
        models, outputs, evidence, kwargs = [], [], [], []
        for options in ({}, {"zone_explain_conflicts": False}, {"zone_explain_conflicts": True}):
            seen, actual = [], []
            def capture(solver, model, *args, **extra):
                if not any(v.name.startswith("conflict_requirement_") for v in model.Proto().variables):
                    seen.append(str(model.Proto()))
                return actual_solve(solver, model, *args, **extra)
            def observe(*args, **extra):
                result = original(*args, **extra)
                actual.extend(r.to_dict() for r in result)
                return result
            with tempfile.TemporaryDirectory() as directory, patch.object(cp_model.CpSolver, "Solve", capture), \
                    patch.object(hierarchy, "solve_zone_layouts", side_effect=observe) as solve:
                report = runner.new_report(120, room_solver="zone_recursive", **options)
                self.assertTrue(runner.accept_program(program().with_required_flow_opening_width(), {},
                    default_facility_profile(), report, Path(directory), room_solver="zone_recursive",
                    room_seconds=1, equipment_seconds=.5, seed=1, **options), report)
            models.append(seen)
            outputs.append(actual)
            evidence.append(report["room_solver"])
            kwargs.append(solve.call_args.kwargs)
        self.assertEqual(models[0], models[1])
        self.assertEqual(models[0], models[2])
        self.assertEqual(outputs[0], outputs[1])
        self.assertEqual(outputs[0], outputs[2])
        self.assertEqual(evidence[0], evidence[1])
        self.assertEqual(set(kwargs[0]), set(kwargs[1]))
        self.assertNotIn("explain_conflicts", kwargs[0])
        self.assertNotIn("conflict_budget", kwargs[0])
        self.assertEqual(evidence[2]["conflict_explanations"]["solves_used"], 0)

    def test_manual_control_adds_no_automatic_job_and_quotes_raw_budget_environment(self):
        import yaml
        workflow = yaml.load((Path(__file__).resolve().parents[1] / ".github/workflows/warmstart80.yml").read_text(),
            Loader=yaml.BaseLoader)
        self.assertEqual(set(workflow["on"]), {"workflow_dispatch"})
        inputs = workflow["on"]["workflow_dispatch"]["inputs"]
        self.assertEqual((inputs["zone_explain_conflicts"]["type"], inputs["zone_explain_conflicts"]["default"]),
            ("boolean", "false"))
        self.assertEqual((inputs["zone_conflict_budget_json"]["type"], inputs["zone_conflict_budget_json"]["default"]),
            ("string", ""))
        self.assertEqual(len(workflow["jobs"]), 1)
        job = next(iter(workflow["jobs"].values()))
        self.assertEqual(job["runs-on"], "ubuntu-latest")
        self.assertNotIn("strategy", job)
        step = next(s for s in job["steps"] if "scale80_warmstart_acceptance.py" in s.get("run", ""))
        self.assertEqual(step["env"]["ZONE_EXPLAIN_CONFLICTS"], "${{ inputs.zone_explain_conflicts }}")
        self.assertEqual(step["env"]["ZONE_CONFLICT_BUDGET_JSON"], "${{ inputs.zone_conflict_budget_json }}")
        self.assertIn("args+=(--zone-explain-conflicts)", step["run"])
        self.assertIn('args+=(--zone-conflict-budget-json "$ZONE_CONFLICT_BUDGET_JSON")', step["run"])
        self.assertIn("--max-seconds 120", step["run"])


if __name__ == "__main__":
    unittest.main()

"""The supplied warm-start runner must enforce real recursive zone geometry."""

import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from layout_configurator.facility import default_facility_profile
from layout_configurator.io import load_building_result
from layout_configurator.zone_hierarchy import solve_zone_layouts, validate_zone_layout
import test_scale80_warmstart_acceptance as existing_runner
import test_zone_warm_start as recursive_fixture


class Scale80RecursiveAcceptanceTests(unittest.TestCase):
    def runner(self):
        return existing_runner.Scale80WarmstartAcceptanceTests().runner()

    def test_actual_eight_room_recursive_pipeline_checks_physical_zones_and_complete_bundle(self):
        runner = self.runner()
        building = recursive_fixture.program().with_required_flow_opening_width()
        report = runner.new_report(120, room_solver="zone_recursive")
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            self.assertTrue(runner.accept_program(
                building, recursive_fixture.source_rects(), default_facility_profile(), report,
                output, room_solver="zone_recursive", room_seconds=1, equipment_seconds=.5, seed=1,
            ), report)
            self.assertEqual(report["settings"]["room_solver"], "zone_recursive")
            self.assertEqual(report["room_solver"]["room_solver_used"], "zone_recursive")
            self.assertTrue(report["cp_sat_final_result"])
            self.assertFalse(report["final_hint_positions_fixed"])
            self.assertTrue(all(gate["ok"] for gate in report["gates"].values()), report["gates"])
            containers = recursive_fixture.zone_rects(report["room_solver"])
            self.assertEqual(set(containers), {"parent", "a_zone", "b_zone"})
            saved = json.loads((output / "bundle" / "building_01.json").read_text(encoding="utf-8"))
            _, result, _ = load_building_result(output / "bundle" / "building_01.json")
            minimum = building.layout.door_width_mm + 2 * building.layout.wall_thickness_mm
            physical = validate_zone_layout(building, result, containers, recursive_zones=True,
                                            minimum_adjacency_mm=minimum)
            self.assertTrue(physical.ok, physical.issues)
            self.assertEqual(containers["a_zone"].intersection_area(containers["b_zone"]), 0)
            self.assertEqual(saved["generation"]["room_solver_used"], "zone_recursive")
            self.assertTrue(report["gates"]["physical_zones"]["ok"])
            self.assertEqual(report["stage_hashes"]["zones80"], runner.digest({
                unit_id: {name: getattr(rect, name) for name in ("x", "y", "width", "height")}
                for unit_id, rect in containers.items()
            }))
            self.assertTrue(report["bundle_qa"]["ok"])
            self.assertTrue(report["ifc_express"]["ok"])
            self.assertTrue(report["ifc_express"]["express_rules"])
            self.assertFalse(report["general_scratch80_acceptance"])
            self.assertFalse(report["recursive_zone_decomposition_complete"])

    def test_real_bounded_recursive_search_failure_keeps_stage_and_scope_without_bundle(self):
        runner = self.runner()
        building = recursive_fixture.program().with_required_flow_opening_width()
        report = runner.new_report(120, room_solver="zone_recursive")
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            self.assertFalse(runner.accept_program(
                building, recursive_fixture.source_rects(), default_facility_profile(), report,
                output, room_solver="zone_recursive", room_seconds=1e-9, seed=1,
            ))
            self.assertEqual(report["status"], "ROOM_SEARCH_FAILED")
            failure = report["error"]["stage_evidence"]
            self.assertEqual(failure["stage"], "local")
            self.assertEqual(failure["code"], "no_valid_local_shape")
            self.assertIsNone(failure["model_status"])
            self.assertFalse(failure["proven_building_infeasible"])
            self.assertIn(failure["proof_scope"], {"bounded_candidates", "selected_zone_shapes"})
            self.assertTrue(report["room_solver"]["stage_attempts"])
            searches = [search for attempt in report["room_solver"]["stage_attempts"]
                        for search in attempt.get("room_solver_evidence", {}).get("hierarchy_hints", {}).get("global_search", [])]
            self.assertTrue(searches)
            self.assertEqual(searches[-1]["model_status"], "UNKNOWN")
            self.assertFalse(searches[-1]["original_program_infeasibility_proven"])
            self.assertNotIn("equipment80", report["stage_hashes"])
            self.assertFalse((output / "bundle").exists())

    def test_tampered_real_cp_container_is_rejected_before_equipment_or_exports(self):
        runner = self.runner()
        building = recursive_fixture.program().with_required_flow_opening_width()
        report = runner.new_report(120, room_solver="zone_recursive")

        def tamper(*args, **kwargs):
            result = solve_zone_layouts(*args, **kwargs)
            # Keep the real accepted room geometry, but supply a wrong physical container.
            kwargs["evidence"]["variants"][0]["zone_rects"]["a_zone"] = {
                "x": 14000, "y": 10000, "width": 2000, "height": 2000,
            }
            return result

        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            with patch("layout_configurator.zone_hierarchy.solve_zone_layouts", side_effect=tamper):
                self.assertFalse(runner.accept_program(
                    building, recursive_fixture.source_rects(), default_facility_profile(), report,
                    output, room_solver="zone_recursive", room_seconds=1, seed=1,
                ))
            self.assertEqual(report["status"], "ZONE_GEOMETRY_REJECTED")
            self.assertFalse(report["gates"]["physical_zones"]["ok"])
            self.assertTrue(report["gates"]["physical_zones"]["issues"])
            self.assertNotIn("equipment80", report["stage_hashes"])
            self.assertFalse((output / "bundle").exists())

    def test_recursive_native_worker_deadline_preserves_choice_and_failure_evidence(self):
        runner = self.runner()
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "deadline"
            with patch.object(runner.subprocess, "Popen", wraps=subprocess.Popen) as process:
                code = runner.main(["--output", str(output), "--max-seconds", ".01",
                                    "--room-solver", "zone_recursive"])
            self.assertEqual(code, 124)
            command = process.call_args.args[0]
            self.assertEqual(command[command.index("--room-solver") + 1], "zone_recursive")
            self.assertIs(process.call_args.kwargs["shell"], False)
            report = json.loads((output / "acceptance-report.json").read_text(encoding="utf-8"))
            self.assertEqual(report["settings"]["room_solver"], "zone_recursive")
            self.assertEqual(report["status"], "BUDGET_EXCEEDED")
            self.assertFalse(report["accepted"])
            self.assertIn("preparation", report["timing_scope"])

    def test_backend_choice_cannot_change_pinned_preparation_or_source_bytes(self):
        runner = self.runner()
        before = runner.fixture_state(runner.DEFAULT_FIXTURE)
        baseline = runner.new_report(120)
        recursive = runner.new_report(120, room_solver="zone_recursive")
        ordinary_program, ordinary_hints, ordinary_profile = runner.prepare_fixture(runner.DEFAULT_FIXTURE, baseline)
        recursive_program, recursive_hints, recursive_profile = runner.prepare_fixture(runner.DEFAULT_FIXTURE, recursive)
        self.assertEqual(baseline["settings"]["room_solver"], "hierarchical")
        self.assertEqual(ordinary_program.to_dict(), recursive_program.to_dict())
        self.assertEqual(ordinary_hints, recursive_hints)
        self.assertEqual(ordinary_profile.to_dict(), recursive_profile.to_dict())
        self.assertEqual(baseline["stage_hashes"], recursive["stage_hashes"])
        self.assertEqual(runner.fixture_state(runner.DEFAULT_FIXTURE), before)

    def test_manual_recursive_choice_keeps_one_installed_wheel_ubuntu_job(self):
        import yaml

        root = Path(__file__).resolve().parents[1]
        workflow = yaml.load((root / ".github/workflows/warmstart80.yml").read_text(encoding="utf-8"),
                             Loader=yaml.BaseLoader)
        self.assertEqual(set(workflow["on"]), {"workflow_dispatch"})
        choice = workflow["on"]["workflow_dispatch"]["inputs"]["room_solver"]
        self.assertEqual(choice["type"], "choice")
        self.assertEqual(choice["default"], "hierarchical")
        self.assertEqual(choice["options"], ["hierarchical", "zone_recursive"])
        self.assertEqual(len(workflow["jobs"]), 1)
        job = next(iter(workflow["jobs"].values()))
        self.assertEqual(job["runs-on"], "ubuntu-latest")
        self.assertNotIn("strategy", job)
        run = next(step for step in job["steps"] if "scale80_warmstart_acceptance.py" in step.get("run", ""))
        self.assertEqual(run["env"]["ROOM_SOLVER"], "${{ inputs.room_solver }}")
        self.assertIn('--room-solver "$ROOM_SOLVER"', run["run"])


if __name__ == "__main__":
    unittest.main()

"""Leaf repacking is an explicit manual, supervised producer option."""

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import test_scale80_warmstart_acceptance as existing_runner


class Scale80LeafRepackingTests(unittest.TestCase):
    def runner(self):
        return existing_runner.Scale80WarmstartAcceptanceTests().runner()

    def test_strict_controls_forward_only_true_and_preserve_default_bytes(self):
        runner = self.runner()
        for backend in ("hierarchical", "zone_recursive"):
            self.assertEqual(json.dumps(runner.new_report(120, room_solver=backend), sort_keys=True),
                json.dumps(runner.new_report(120, room_solver=backend, zone_selective_leaf_repacking=False), sort_keys=True))
            self.assertEqual(runner._zone_search_options(backend, None, 1),
                runner._zone_search_options(backend, None, 1, selective_leaf_repacking=False))
        self.assertEqual(runner._zone_search_options("zone_recursive", None, 1,
            selective_leaf_repacking=True), {"selective_leaf_repacking": True})
        for value in (1, 0, None, "true"):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    runner.new_report(120, zone_selective_leaf_repacking=value)
                with self.assertRaises(ValueError):
                    runner._zone_search_options("zone_recursive", None, 1, selective_leaf_repacking=value)
        with self.assertRaises(ValueError):
            runner._zone_search_options("hierarchical", None, 1, selective_leaf_repacking=True)

    def test_supervisor_forwards_flag_without_changing_whole_request_deadline(self):
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
                    args.append("--zone-selective-leaf-repacking")
                with patch.object(runner.subprocess, "Popen", return_value=ExpiredProcess()) as process:
                    self.assertEqual(runner.main(args), 124)
                self.assertEqual("--zone-selective-leaf-repacking" in process.call_args.args[0], opted)
                report = json.loads((Path(directory) / "acceptance-report.json").read_text())
                self.assertEqual(report["settings"].get("zone_selective_leaf_repacking", False), opted)
                self.assertEqual(report["status"], "BUDGET_EXCEEDED")
                self.assertEqual(report["settings"]["max_total_seconds"], .01)

    def test_manual_workflow_is_default_false_single_ubuntu_job_with_existing_deadline(self):
        import yaml
        workflow = yaml.load((Path(__file__).resolve().parents[1] / ".github/workflows/warmstart80.yml").read_text(),
            Loader=yaml.BaseLoader)
        self.assertEqual(set(workflow["on"]), {"workflow_dispatch"})
        option = workflow["on"]["workflow_dispatch"]["inputs"]["zone_selective_leaf_repacking"]
        self.assertEqual((option["type"], option["default"]), ("boolean", "false"))
        self.assertEqual(len(workflow["jobs"]), 1)
        job = next(iter(workflow["jobs"].values()))
        self.assertEqual(job["runs-on"], "ubuntu-latest")
        self.assertNotIn("strategy", job)
        step = next(x for x in job["steps"] if "scale80_warmstart_acceptance.py" in x.get("run", ""))
        self.assertEqual(step["env"]["ZONE_SELECTIVE_LEAF_REPACKING"], "${{ inputs.zone_selective_leaf_repacking }}")
        self.assertIn("args+=(--zone-selective-leaf-repacking)", step["run"])
        self.assertIn("--max-seconds 120", step["run"])

    def test_actual_small_complete_producer_keeps_bundle_qa_and_express_gates(self):
        from layout_configurator.facility import default_facility_profile
        from layout_configurator.qa import validate_building_set
        from test_zone_leaf_repacking import four_root_program, four_root_shapes
        import layout_configurator.zone_hierarchy as hierarchy

        runner = self.runner()
        report = runner.new_report(120, room_solver="zone_recursive", zone_selective_leaf_repacking=True)
        _, shapes = four_root_shapes()
        with tempfile.TemporaryDirectory() as directory, patch.object(hierarchy, "_recursive_shapes", return_value=shapes):
            self.assertTrue(runner.accept_program(four_root_program(), {},
                default_facility_profile(), report, Path(directory), room_solver="zone_recursive",
                room_seconds=1, equipment_seconds=.5, seed=1, zone_selective_leaf_repacking=True), report)
            actual = validate_building_set(Path(directory) / "bundle", expected_variants=1,
                profile=default_facility_profile())
            self.assertTrue(actual.ok, actual.to_dict())
        self.assertEqual(report["room_solver"]["selective_leaf_repacking"]["status"], "APPLIED")
        self.assertEqual(report["room_solver"]["selective_leaf_repacking"]["counts"]["added_shapes"], 2)
        self.assertTrue(all(gate["ok"] for gate in report["gates"].values()))
        self.assertTrue(report["ifc_express"]["ok"])
        self.assertFalse(report["general_scratch80_acceptance"])


if __name__ == "__main__":
    unittest.main()

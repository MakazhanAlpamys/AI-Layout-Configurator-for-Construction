"""Translation support is opt-in and shares the existing request deadline."""

import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from layout_configurator.facility import default_facility_profile
import test_scale80_warmstart_acceptance as existing_runner
from test_zone_contact_pruning import program


class Scale80ContactSupportTests(unittest.TestCase):
    def runner(self):
        return existing_runner.Scale80WarmstartAcceptanceTests().runner()

    def test_actual_small_bundle_uses_support_and_retains_pruning_and_every_saved_gate(self):
        runner = self.runner()
        report = runner.new_report(120, room_solver="zone_recursive", zone_contact_support=True,
                                   zone_contact_pruning=True, zone_global_diagnostics=True)
        with tempfile.TemporaryDirectory() as directory:
            self.assertTrue(runner.accept_program(program().with_required_flow_opening_width(), {},
                default_facility_profile(), report, Path(directory), room_solver="zone_recursive",
                room_seconds=1, equipment_seconds=.5, seed=1, zone_contact_support=True,
                zone_contact_pruning=True, zone_global_diagnostics=True), report)
            from layout_configurator.qa import validate_building_set

            saved = validate_building_set(Path(directory) / "bundle", expected_variants=1,
                                          profile=default_facility_profile())
            self.assertTrue(saved.ok, saved.to_dict())
        self.assertTrue(all(gate["ok"] for gate in report["gates"].values()))
        self.assertTrue(report["bundle_qa"]["ok"])
        self.assertTrue(report["ifc_express"]["ok"])
        self.assertTrue(report["settings"]["zone_contact_support"])
        support = report["room_solver"]["cross_root_contact_support"]
        self.assertEqual(support["status"], "APPLIED")
        self.assertGreater(support["counts"]["support_rows"], 0)
        self.assertGreater(support["counts"]["added_table_constraints"], 0)
        self.assertGreater(report["room_solver"]["cross_root_contact_pruning"]["counts"]["added_clauses"], 0)
        self.assertFalse(support["original_program_infeasibility_proven"])
        self.assertTrue(report["room_solver"]["global_diagnostics"]["searches"][0]["response"]["solution_present"])
        self.assertFalse(report["general_scratch80_acceptance"])

    def test_parent_forwards_true_and_supervises_the_same_complete_request(self):
        runner = self.runner()
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(runner.subprocess, "Popen", wraps=subprocess.Popen) as process:
                code = runner.main(["--output", directory, "--max-seconds", ".01", "--room-solver",
                                    "zone_recursive", "--zone-contact-support", "--zone-contact-pruning"])
            self.assertEqual(code, 124)
            self.assertIn("--zone-contact-support", process.call_args.args[0])
            self.assertIn("--zone-contact-pruning", process.call_args.args[0])
            self.assertIs(process.call_args.kwargs["shell"], False)
            report = json.loads((Path(directory) / "acceptance-report.json").read_text(encoding="utf-8"))
            self.assertEqual(report["status"], "BUDGET_EXCEEDED")
            self.assertTrue(report["settings"]["zone_contact_support"])
            self.assertEqual(report["settings"]["max_total_seconds"], .01)

    def test_inapplicable_or_nonboolean_controls_reject_before_execution(self):
        runner = self.runner()
        with tempfile.TemporaryDirectory() as directory, patch.object(runner.subprocess, "Popen") as process:
            self.assertEqual(runner.main(["--output", directory, "--zone-contact-support"]), 2)
            process.assert_not_called()
            report = json.loads((Path(directory) / "acceptance-report.json").read_text(encoding="utf-8"))
            self.assertEqual(report["status"], "INPUT_REJECTED")
            self.assertTrue(report["settings"]["zone_contact_support"])
        for value in (1, 0, "true", None):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    runner._zone_search_options("zone_recursive", None, 1, contact_support=value)
                with self.assertRaises(ValueError):
                    runner.new_report(120, room_solver="zone_recursive", zone_contact_support=value)
                with self.assertRaises(ValueError):
                    runner.accept_program(None, {}, default_facility_profile(), runner.new_report(120), Path("unused"),
                                          room_solver="zone_recursive", zone_contact_support=value)

    def test_false_preserves_default_json_and_child_arguments(self):
        runner = self.runner()
        for backend in ("hierarchical", "zone_recursive"):
            with self.subTest(backend=backend), tempfile.TemporaryDirectory() as directory:
                baseline = runner.new_report(120, room_solver=backend)
                explicit = runner.new_report(120, room_solver=backend, zone_contact_support=False)
                self.assertEqual(json.dumps(baseline, sort_keys=True), json.dumps(explicit, sort_keys=True))
                self.assertNotIn("zone_contact_support", explicit["settings"])
                self.assertEqual(runner._zone_search_options(backend, None, 1),
                                 runner._zone_search_options(backend, None, 1, contact_support=False))
                with patch.object(runner.subprocess, "Popen", wraps=subprocess.Popen) as process:
                    self.assertEqual(runner.main(["--output", directory, "--max-seconds", ".01",
                                                  "--room-solver", backend]), 124)
                self.assertNotIn("--zone-contact-support", process.call_args.args[0])

    def test_manual_boolean_uses_the_same_single_ubuntu_job(self):
        import yaml

        workflow = yaml.load((Path(__file__).resolve().parents[1] / ".github/workflows/warmstart80.yml").read_text(encoding="utf-8"),
                             Loader=yaml.BaseLoader)
        self.assertEqual(set(workflow["on"]), {"workflow_dispatch"})
        option = workflow["on"]["workflow_dispatch"]["inputs"]["zone_contact_support"]
        self.assertEqual(option["type"], "boolean")
        self.assertEqual(option["default"], "false")
        self.assertEqual(len(workflow["jobs"]), 1)
        job = next(iter(workflow["jobs"].values()))
        self.assertEqual(job["runs-on"], "ubuntu-latest")
        self.assertNotIn("strategy", job)
        step = next(step for step in job["steps"] if "scale80_warmstart_acceptance.py" in step.get("run", ""))
        self.assertEqual(step["env"]["ZONE_CONTACT_SUPPORT"], "${{ inputs.zone_contact_support }}")
        self.assertIn('--max-seconds 120', step["run"])
        self.assertIn('if [[ "$ZONE_CONTACT_SUPPORT" == "true" ]]', step["run"])
        self.assertIn('args+=(--zone-contact-support)', step["run"])


if __name__ == "__main__":
    unittest.main()

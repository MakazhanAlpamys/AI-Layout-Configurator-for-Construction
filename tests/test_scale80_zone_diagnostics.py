"""Diagnostics are explicit in the same supervised source-pinned request."""

import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from layout_configurator.facility import default_facility_profile
import test_scale80_warmstart_acceptance as existing_runner
from test_zone_warm_start import program, source_rects


class Scale80ZoneDiagnosticsTests(unittest.TestCase):
    def runner(self):
        return existing_runner.Scale80WarmstartAcceptanceTests().runner()

    def test_real_eight_room_request_retains_diagnostics_and_complete_persisted_gates(self):
        runner = self.runner()
        report = runner.new_report(120, room_solver="zone_recursive", zone_global_diagnostics=True)
        with tempfile.TemporaryDirectory() as directory:
            self.assertTrue(runner.accept_program(
                program().with_required_flow_opening_width(), source_rects(), default_facility_profile(),
                report, Path(directory), room_solver="zone_recursive", room_seconds=1, equipment_seconds=.5,
                seed=1, zone_global_diagnostics=True,
            ), report)
        self.assertTrue(all(gate["ok"] for gate in report["gates"].values()))
        self.assertTrue(report["bundle_qa"]["ok"])
        self.assertTrue(report["ifc_express"]["ok"])
        diagnostic = report["room_solver"]["global_diagnostics"]
        self.assertEqual(diagnostic["source_sha256"], runner.digest(program().with_required_flow_opening_width().to_dict()))
        self.assertEqual(diagnostic["capture_status"], "COMPLETE")
        self.assertGreater(diagnostic["searches"][0]["model"]["variables"], 0)
        self.assertTrue(report["settings"]["zone_global_diagnostics"])
        self.assertFalse(report["general_scratch80_acceptance"])
        self.assertFalse(report["recursive_zone_decomposition_complete"])

    def test_supervised_parent_forwards_true_and_preserves_tiny_total_deadline(self):
        runner = self.runner()
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(runner.subprocess, "Popen", wraps=subprocess.Popen) as process:
                code = runner.main(["--output", directory, "--max-seconds", ".01",
                                    "--room-solver", "zone_recursive", "--zone-global-diagnostics"])
            self.assertEqual(code, 124)
            self.assertIn("--zone-global-diagnostics", process.call_args.args[0])
            self.assertIs(process.call_args.kwargs["shell"], False)
            report = json.loads((Path(directory) / "acceptance-report.json").read_text(encoding="utf-8"))
            self.assertEqual(report["status"], "BUDGET_EXCEEDED")
            self.assertTrue(report["settings"]["zone_global_diagnostics"])
            self.assertEqual(report["settings"]["max_total_seconds"], .01)

    def test_false_preserves_report_json_and_subprocess_arguments(self):
        runner = self.runner()
        for backend in ("hierarchical", "zone_recursive"):
            with self.subTest(backend=backend), tempfile.TemporaryDirectory() as directory:
                baseline = runner.new_report(120, room_solver=backend)
                explicit = runner.new_report(120, room_solver=backend, zone_global_diagnostics=False)
                self.assertEqual(json.dumps(baseline, sort_keys=True), json.dumps(explicit, sort_keys=True))
                self.assertNotIn("zone_global_diagnostics", explicit["settings"])
                with patch.object(runner.subprocess, "Popen", wraps=subprocess.Popen) as process:
                    self.assertEqual(runner.main(["--output", directory, "--max-seconds", ".01",
                                                  "--room-solver", backend]), 124)
                self.assertNotIn("--zone-global-diagnostics", process.call_args.args[0])

    def test_inapplicable_mode_rejects_before_starting_worker_and_values_are_strict(self):
        runner = self.runner()
        with tempfile.TemporaryDirectory() as directory, patch.object(runner.subprocess, "Popen") as process:
            self.assertEqual(runner.main(["--output", directory, "--zone-global-diagnostics"]), 2)
            process.assert_not_called()
            report = json.loads((Path(directory) / "acceptance-report.json").read_text(encoding="utf-8"))
            self.assertEqual(report["status"], "INPUT_REJECTED")
            self.assertTrue(report["settings"]["zone_global_diagnostics"])
        for value in (1, 0, "true", None):
            with self.subTest(value=value), self.assertRaises(ValueError):
                runner._zone_search_options("zone_recursive", None, 1, diagnostics=value)

    def test_manual_input_adds_no_job_or_automatic_workload(self):
        import yaml

        root = Path(__file__).resolve().parents[1]
        workflow = yaml.load((root / ".github/workflows/warmstart80.yml").read_text(encoding="utf-8"),
                             Loader=yaml.BaseLoader)
        self.assertEqual(set(workflow["on"]), {"workflow_dispatch"})
        option = workflow["on"]["workflow_dispatch"]["inputs"]["zone_global_diagnostics"]
        self.assertEqual(option["type"], "boolean")
        self.assertEqual(option["default"], "false")
        self.assertEqual(len(workflow["jobs"]), 1)
        job = next(iter(workflow["jobs"].values()))
        self.assertEqual(job["runs-on"], "ubuntu-latest")
        self.assertNotIn("strategy", job)
        step = next(step for step in job["steps"] if "scale80_warmstart_acceptance.py" in step.get("run", ""))
        self.assertEqual(step["env"]["ZONE_GLOBAL_DIAGNOSTICS"], "${{ inputs.zone_global_diagnostics }}")
        self.assertIn('--max-seconds 120', step["run"])
        self.assertIn('if [[ "$ZONE_GLOBAL_DIAGNOSTICS" == "true" ]]', step["run"])
        self.assertIn('args+=(--zone-global-diagnostics)', step["run"])


if __name__ == "__main__":
    unittest.main()

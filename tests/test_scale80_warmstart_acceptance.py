"""Focused source-integrity, solver and deadline checks for the manual pilot."""

import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from layout_configurator.building import BuildingIR
from layout_configurator.facility import default_facility_profile
from layout_configurator.models import LayoutIR, LayoutResult, Rect
from layout_configurator.validation import validate_layout
from test_hierarchy_hints import program, source_rects


class Scale80WarmstartAcceptanceTests(unittest.TestCase):
    def runner(self):
        path = Path(__file__).resolve().parents[1] / "scripts" / "scale80_warmstart_acceptance.py"
        self.assertTrue(path.is_file(), "The source-pinned warm-start runner is missing")
        spec = importlib.util.spec_from_file_location("scale80_warmstart_runner", path)
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        return module

    def test_pinned_preparation_rechecks_source_and_preserves_all_clone_constraints(self):
        runner = self.runner()
        report = runner.new_report(120)
        building, hints, profile = runner.prepare_fixture(runner.DEFAULT_FIXTURE, report)
        self.assertEqual((len(building.layout.rooms), len(building.equipment), len(building.flows)), (80, 60, 14))
        self.assertEqual(report["source_commit"], "35eff27f334a0b77741cabf841f8c5000046b316")
        self.assertTrue(all(gate["ok"] for gate in report["source_gates"].values()))
        self.assertTrue(report["derivation"]["exact_unweakened_clone_except_bridge"])
        self.assertEqual(hints["A/receiving"].shared_boundary(hints["B/receiving"]), 7500)
        self.assertTrue(validate_layout(building.layout, LayoutResult(1, hints)).ok)
        self.assertEqual(len(profile.rules), 7)
        self.assertEqual(set(report["stage_hashes"]), {
            "source40_program", "source40_prepared", "source40_layout", "source40_equipment",
            "source40_flows", "source40_facility", "profile", "program80_raw", "program80_prepared", "hints80", "derivation",
        })

    def test_rehashed_invalid_geometry_or_weakened_clone_cannot_trust_stored_pass_flags(self):
        runner = self.runner()
        for category in ("accepted40", "program80"):
            with self.subTest(category=category), tempfile.TemporaryDirectory() as directory:
                root = Path(directory) / "fixture"
                shutil.copytree(runner.DEFAULT_FIXTURE, root)
                manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
                path = root / manifest["files"][category]["path"]
                payload = json.loads(path.read_text(encoding="utf-8"))
                if category == "accepted40":
                    payload["layout"]["rooms"]["receiving"] = payload["layout"]["rooms"]["raw_storage"]
                else:
                    payload["layout"]["rooms"][0]["min_width"] = 100
                path.write_text(json.dumps(payload), encoding="utf-8")
                manifest["files"][category]["sha256"] = runner.file_digest(path)
                (root / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
                with self.assertRaisesRegex(ValueError, "validation rejects|unweakened"):
                    runner.prepare_fixture(root, runner.new_report(120))

    def test_changed_raw_fixture_is_rejected_before_any_room_search(self):
        runner = self.runner()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "fixture"
            shutil.copytree(runner.DEFAULT_FIXTURE, root)
            manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
            source = root / manifest["files"]["accepted40"]["path"]
            source.write_bytes(source.read_bytes() + b"\n")
            report = runner.new_report(120)
            with self.assertRaisesRegex(ValueError, "hash"):
                runner.prepare_fixture(root, report)
            self.assertNotIn("room_solver", report)

    def test_small_actual_pipeline_returns_unrestricted_cp_rooms_and_every_gate(self):
        runner = self.runner()
        building = BuildingIR.from_mapping({
            "layout": program().to_dict(),
            "equipment": [{"id": "machine", "room_id": "b_leaf_0", "width_mm": 400,
                           "depth_mm": 200, "clearance_mm": 100}],
            "flows": [{"id": "people", "type": "people", "from_ids": ["a_leaf_0"],
                       "to_ids": ["b_leaf_1"], "required": True, "minimum_clear_width_mm": 800}],
        }).with_required_flow_opening_width()
        report = runner.new_report(120)
        with tempfile.TemporaryDirectory() as directory:
            accepted = runner.accept_program(building, source_rects(), default_facility_profile(), report,
                                             Path(directory), room_seconds=1, equipment_seconds=.5, seed=1)
            self.assertTrue(accepted, report)
            self.assertTrue(report["cp_sat_final_result"])
            self.assertFalse(report["final_hint_positions_fixed"])
            self.assertTrue(all(gate["ok"] for gate in report["gates"].values()))
            self.assertIn(report["room_solver"]["hierarchy_hints"]["global_search"][0]["model_status"],
                          {"FEASIBLE", "OPTIMAL"})
            bundle = Path(directory) / "bundle"
            self.assertTrue((bundle / "building_01.json").is_file())
            self.assertTrue({"rooms80", "equipment80", "flows80", "facility80", "bundle80", "bundle80_qa", "ifc80_express"}
                            <= report["stage_hashes"].keys())
            self.assertTrue(report["bundle_qa"]["ok"])
            self.assertTrue(report["ifc_express"]["ok"])

    def test_real_unknown_room_search_cannot_publish_acceptance(self):
        runner = self.runner()
        building = BuildingIR.from_mapping({"layout": {
            "boundary": {"width": 4000, "height": 2000}, "entry_room": "room",
            "rooms": [{"id": "room", "target_area": 4, "min_area": 4, "max_area": 4,
                       "min_width": 2000, "min_depth": 2000}],
        }})
        report = runner.new_report(120)
        with tempfile.TemporaryDirectory() as directory:
            self.assertFalse(runner.accept_program(
                building, {"room": Rect(0, 0, 2000, 2000)}, default_facility_profile(), report,
                Path(directory), room_seconds=1e-9, equipment_seconds=.5, seed=1,
            ))
            self.assertEqual(report["status"], "ROOM_SEARCH_FAILED")
            stage = report["room_solver"]["hierarchy_hints"]["global_search"][0]
            self.assertEqual(stage["model_status"], "UNKNOWN")
            self.assertFalse(stage["original_program_infeasibility_proven"])
            self.assertFalse((Path(directory) / "bundle" / "building_01.json").exists())

    def test_native_worker_deadline_retains_failure_evidence(self):
        runner = self.runner()
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "deadline"
            with patch.object(runner.subprocess, "Popen", wraps=subprocess.Popen) as actual_process:
                self.assertEqual(runner.main(["--output", str(output), "--max-seconds", ".01"]), 124)
            self.assertIs(actual_process.call_args.kwargs.get("shell"), False)
            self.assertEqual(actual_process.call_args.kwargs.get("creationflags"),
                             subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
            report = json.loads((output / "acceptance-report.json").read_text(encoding="utf-8"))
            self.assertFalse(report["accepted"])
            self.assertEqual(report["status"], "BUDGET_EXCEEDED")
            self.assertEqual(report["settings"]["max_total_seconds"], .01)
            self.assertIn("preparation", report["timing_scope"])

    def test_manual_workflow_is_single_ubuntu_and_cancels_duplicate_requests(self):
        import yaml

        root = Path(__file__).resolve().parents[1]
        workflow = yaml.load((root / ".github" / "workflows" / "warmstart80.yml").read_text(encoding="utf-8"),
                             Loader=yaml.BaseLoader)
        self.assertEqual(set(workflow["on"]), {"workflow_dispatch"})
        self.assertEqual(len(workflow["jobs"]), 1)
        job = next(iter(workflow["jobs"].values()))
        self.assertEqual(job["runs-on"], "ubuntu-latest")
        self.assertNotIn("strategy", job)
        self.assertEqual(workflow["concurrency"], {"group": "warmstart80-${{ github.ref }}", "cancel-in-progress": "true"})
        self.assertIn("python -m pip install .", [step.get("run") for step in job["steps"]])

    def test_runtime_hashes_the_resources_resolved_by_the_executing_package(self):
        import hashlib
        from layout_configurator.resources import resource_path

        runner = self.runner()
        state = runner.runtime_state()
        resources = state["resolved_resources"]
        self.assertEqual({Path(name).parts[0] for name in resources}, {"schemas", "rules", "ids"})
        for name, entry in resources.items():
            with self.subTest(resource=name):
                actual = resource_path(name).resolve()
                expected = hashlib.sha256(actual.read_bytes()).hexdigest()
                self.assertEqual(Path(entry["path"]), actual)
                self.assertEqual(entry["sha256"], expected)
                self.assertEqual(state["files"]["resource/" + name], expected)

    def test_actual_noneditable_wheel_provenance_matches_the_executing_module(self):
        runner = self.runner()
        project = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source"
            source.mkdir()
            for name in ("pyproject.toml", "README.md", "setup.py", "MANIFEST.in"):
                if (project / name).is_file():
                    shutil.copy2(project / name, source / name)
            shutil.copytree(project / "src" / "layout_configurator", source / "src" / "layout_configurator",
                            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
            for name in ("schemas", "rules", "ids"):
                shutil.copytree(project / name, source / name)
            wheels = root / "wheels"
            installed = root / "installed"
            for command in (
                [sys.executable, "-m", "pip", "wheel", "--no-deps", "--no-build-isolation", "--wheel-dir", str(wheels), str(source)],
                [sys.executable, "-m", "pip", "install", "--no-deps", "--target", str(installed)],
            ):
                if command[3] == "install":
                    command.append(str(next(wheels.glob("*.whl"))))
                completed = subprocess.run(command, cwd=root, text=True, stdout=subprocess.PIPE,
                                           stderr=subprocess.STDOUT, timeout=60,
                                           creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
                self.assertEqual(completed.returncode, 0, completed.stdout)
            code = (
                "import sys, importlib.util, json, hashlib; from pathlib import Path; "
                "sys.path.insert(0, sys.argv[1]); "
                "spec=importlib.util.spec_from_file_location('installed_runner', sys.argv[2]); "
                "runner=importlib.util.module_from_spec(spec); spec.loader.exec_module(runner); "
                "state=runner.runtime_state(); "
                "assert Path(state['package_root']).is_relative_to(Path(sys.argv[1]).resolve()); "
                "assert state['installed_package'] and not state['editable_package']; "
                "from layout_configurator.resources import resource_path; "
                "assert {Path(name).parts[0] for name in state['resolved_resources']} == {'schemas','rules','ids'}; "
                "assert all(Path(entry['path']) == resource_path(name).resolve() and "
                "Path(entry['path']).is_relative_to(Path(state['package_root']) / '_data') and "
                "entry['sha256'] == hashlib.sha256(resource_path(name).read_bytes()).hexdigest() "
                "for name,entry in state['resolved_resources'].items()); "
                "assert any(name.endswith('.yaml') for name in state['files']); "
                "assert any(name.endswith('.ids') for name in state['files']); "
                "resource = resource_path('rules/default_facility.yaml'); "
                "resource.write_bytes(resource.read_bytes() + b'\\n# Wheel fingerprint probe\\n'); "
                "changed = runner.runtime_state(); "
                "assert changed['sha256'] != state['sha256']; "
                "assert changed['resolved_resources']['rules/default_facility.yaml']['sha256'] == "
                "hashlib.sha256(resource.read_bytes()).hexdigest(); "
                "print(json.dumps(state['distribution_root']))"
            )
            completed = subprocess.run([sys.executable, "-I", "-c", code, str(installed), str(Path(runner.__file__))],
                                       cwd=root, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=30,
                                       creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
            self.assertEqual(completed.returncode, 0, completed.stdout)
            self.assertEqual(Path(json.loads(completed.stdout.strip())).resolve(), installed.resolve())

    def test_request_cannot_raise_the_120_second_ceiling(self):
        runner = self.runner()
        for seconds in ("121", "nan", "0"):
            with self.subTest(seconds=seconds), tempfile.TemporaryDirectory() as directory:
                self.assertEqual(runner.main(["--output", directory, "--max-seconds", seconds]), 2)
                report = json.loads((Path(directory) / "acceptance-report.json").read_text(encoding="utf-8"))
                self.assertEqual(report["status"], "INPUT_REJECTED")
                self.assertFalse(report["accepted"])


if __name__ == "__main__":
    unittest.main()

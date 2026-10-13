"""Explicit orthogonal CLI output is a verified draft, never full acceptance."""

import contextlib
import copy
import hashlib
import io
import json
from pathlib import Path
import shutil
import tempfile
import unittest

import ezdxf

from layout_configurator.building import BuildingIR
from layout_configurator.cli import main
from layout_configurator.facility import default_facility_profile
import test_orthogonal_generation as generation_fixture


class OrthogonalCLITests(unittest.TestCase):
    @classmethod
    def tearDownClass(cls):
        if hasattr(cls, "_published"):
            cls._published.cleanup()

    def run_cli(self, *args):
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = main(list(map(str, args)))
        return code, stdout.getvalue(), stderr.getvalue()

    def inputs(self, root, building=None, profile=None):
        building = generation_fixture._source() if building is None else building
        profile = default_facility_profile() if profile is None else profile
        source, policy = root / "source.json", root / "profile.json"
        source.write_text(json.dumps(building.to_dict()), encoding="utf-8")
        policy.write_text(json.dumps(profile.to_dict()), encoding="utf-8")
        return source, policy

    def generate(self, source, policy, output, *extra):
        return self.run_cli("generate-orthogonal-building", source, "--profile", policy,
                            "--output", output, "--seed", 1, "--deterministic-budget", .05,
                            "--max-room-candidates", 4, "--equipment-retries", 2, *extra)

    def published(self, root):
        # Reuse one real CLI publication as immutable input to independent QA mutations.
        if not hasattr(type(self), "_published"):
            temporary = tempfile.TemporaryDirectory()
            source, policy = self.inputs(Path(temporary.name))
            output = Path(temporary.name) / "set"
            code, stdout, stderr = self.generate(source, policy, output)
            self.assertEqual(code, 0, (stdout, stderr))
            type(self)._published = temporary
        origin = Path(type(self)._published.name)
        for name in ("source.json", "profile.json"):
            shutil.copy2(origin / name, root / name)
        shutil.copytree(origin / "set", root / "set")
        return root / "set", root / "profile.json"

    def read(self, path):
        return json.loads(path.read_text(encoding="utf-8"))

    def write(self, path, payload):
        path.write_text(json.dumps(payload), encoding="utf-8")

    def test_actual_generated_polygon_bundle_and_both_qa_modes_retain_draft_scope(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output, policy = self.published(root)
            index = self.read(output / "manifest.json")
            evidence = self.read(output / "generation.json")
            self.assertEqual(index["status"], "DRAFT")
            self.assertIs(index["ok"], False)
            self.assertEqual(index["full_facility_validation"], {"status": "UNKNOWN", "ok": False})
            self.assertEqual(index["engineering_acceptance"], "PENDING")
            self.assertEqual((index["requested_variants"], index["published_variants"]), (1, 1))
            child = output / "variant_01"
            layout = self.read(child / "layout.json")
            self.assertEqual(layout["rooms"]["main"]["kind"], "orthogonal")
            self.assertEqual(evidence["source"]["building_sha256"], index["source"]["building_sha256"])
            for path in (output, child):
                with self.subTest(path=path):
                    code, stdout, stderr = self.run_cli("qa-orthogonal-building", path, "--profile", policy, "--json")
                    self.assertEqual(code, 0, stderr)
                    report = json.loads(stdout)
                    self.assertEqual(report["status"], "PASS")
                    self.assertEqual(report["full_facility_validation"], {"status": "UNKNOWN", "ok": False})
                    qa = report["variants"][0]["report"] if path == output else report
                    self.assertEqual(next(row for row in qa["checks"] if row["id"] == "IFC_EXPRESS")["status"], "PASS")

    def test_two_actual_variants_are_independently_checked_and_second_failure_is_visible(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, policy = self.inputs(root, self.single_room(4000))
            output = root / "set"
            code, stdout, stderr = self.generate(source, policy, output, "--variants", 2, "--max-room-candidates", 2)
            self.assertEqual(code, 0, (stdout, stderr))
            index = self.read(output / "manifest.json")
            self.assertEqual([row["variant"] for row in index["variants"]], [1, 2])
            self.assertEqual([row["bundle"] for row in index["variants"]], ["variant_01", "variant_02"])
            layouts = [self.read(output / f"variant_{variant:02d}" / "layout.json") for variant in (1, 2)]
            self.assertNotEqual(layouts[0]["rooms"], layouts[1]["rooms"])
            code, stdout, stderr = self.run_cli("qa-orthogonal-building", output, "--json")
            self.assertEqual(code, 0, stderr)
            self.assertEqual(len(json.loads(stdout)["variants"]), 2)
            (output / "variant_02" / "geometry.dxf").write_bytes(b"Not a DXF")
            code, stdout, _ = self.run_cli("qa-orthogonal-building", output, "--json")
            self.assertEqual(code, 4)
            report = json.loads(stdout)
            self.assertEqual([row["report"]["status"] for row in report["variants"]], ["PASS", "FAIL"])

    def test_real_finite_pool_partial_set_stays_unknown_and_reports_requested_count(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, policy = self.inputs(root, self.single_room(2000))
            output = root / "partial"
            code, stdout, stderr = self.generate(source, policy, output, "--variants", 2, "--max-room-candidates", 2)
            self.assertEqual(code, 4, (stdout, stderr))
            index = self.read(output / "manifest.json")
            evidence = self.read(output / "generation.json")
            self.assertEqual(index["status"], "UNKNOWN")
            self.assertEqual((index["requested_variants"], index["published_variants"]), (2, 1))
            self.assertEqual(evidence["accepted_candidates"], 1)
            self.assertFalse(evidence["proven_building_infeasible"])
            self.assertTrue(any(row["stage"] == "candidate_pool" for row in evidence["rejected"]))
            code, stdout, _ = self.run_cli("qa-orthogonal-building", output, "--json")
            self.assertEqual(code, 4)
            report = json.loads(stdout)
            self.assertEqual(report["status"], "UNKNOWN")
            self.assertTrue(report["variants"][0]["report"]["ok"])

    def test_actual_tiny_budget_preserves_scoped_failure_only_without_set_manifest(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, policy = self.inputs(root)
            output = root / "unknown"
            code, _, _ = self.run_cli("generate-orthogonal-building", source, "--profile", policy,
                                      "--output", output, "--time-limit", 1e-9)
            self.assertEqual(code, 4)
            failure = self.read(output / "generation-failure.json")
            self.assertEqual(failure["status"], "UNKNOWN")
            self.assertFalse(failure["proven_building_infeasible"])
            self.assertEqual(failure["generation"]["stop_reason"], "time_limit_during_preparation")
            self.assertFalse((output / "manifest.json").exists())
            self.assertFalse(list(output.glob("variant_*")))

    def test_raw_json_and_yaml_malformed_types_or_duplicate_keys_cannot_be_normalized(self):
        import yaml

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            valid = generation_fixture._source().to_dict()
            cases = []
            for suffix in ("json", "yaml"):
                for field in ("grid", "boundary", "room"):
                    raw = copy.deepcopy(valid)
                    if field == "grid":
                        raw["layout"]["grid_mm"] = True
                    elif field == "boundary":
                        raw["layout"]["boundary"]["width"] = "6000"
                    else:
                        raw["layout"]["rooms"][0]["min_width"] = "1000"
                    text = json.dumps(raw) if suffix == "json" else yaml.safe_dump(raw)
                    cases.append((suffix + "_" + field, suffix, text))
            cases.extend((
                ("duplicate_json", "json", '{"layout":{},"layout":' + json.dumps(valid["layout"]) + '}'),
                ("duplicate_yaml", "yaml", "layout: {}\n" + yaml.safe_dump(valid)),
            ))
            for name, suffix, text in cases:
                with self.subTest(name=name):
                    source = root / (name + "." + suffix)
                    source.write_text(text, encoding="utf-8")
                    output = root / name
                    code, _, _ = self.run_cli("generate-orthogonal-building", source, "--output", output)
                    self.assertEqual(code, 2)
                    failure = self.read(output / "generation-failure.json")
                    self.assertEqual(failure["status"], "INPUT_REJECTED")
                    self.assertEqual(failure["stage"], "input")
                    self.assertFalse((output / "manifest.json").exists())
                    self.assertFalse(list(output.glob("variant_*")))

    def test_existing_destination_is_preserved_even_for_invalid_input(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, policy = self.inputs(root)
            output = root / "existing"
            output.mkdir()
            (output / "sentinel").write_bytes(b"Prior output must stay intact")
            before = {path.name: path.read_bytes() for path in output.iterdir()}
            code, _, _ = self.generate(source, policy, output)
            self.assertEqual(code, 2)
            self.assertEqual({path.name: path.read_bytes() for path in output.iterdir()}, before)

    def test_set_index_cannot_traverse_duplicate_variants_or_change_source_profile_revision(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output, _ = self.published(root)
            path = output / "manifest.json"
            original = path.read_bytes()
            generation_path = output / "generation.json"
            original_generation = generation_path.read_bytes()
            for change in ("path", "duplicate", "source", "profile", "revision", "promote", "full_type", "generation_type", "candidate_report", "nonobject"):
                with self.subTest(change=change):
                    raw = json.loads(original)
                    generation_path.write_bytes(original_generation)
                    if change == "path":
                        raw["variants"][0]["bundle"] = "../outside"
                    elif change == "duplicate":
                        raw["variants"].append(copy.deepcopy(raw["variants"][0]))
                    elif change in ("source", "profile"):
                        raw["source"]["building_sha256" if change == "source" else "profile_sha256"] = "0" * 64
                    elif change == "revision":
                        raw["revision"] = "FORGED"
                    elif change == "promote":
                        raw["status"], raw["ok"] = "PASS", True
                    elif change == "full_type":
                        raw["full_facility_validation"]["ok"] = 0
                    elif change in ("generation_type", "candidate_report"):
                        generation = json.loads(original_generation)
                        if change == "generation_type":
                            generation["requested_variants"] = True
                        else:
                            generation["accepted"][0]["facility_report"]["status"] = "FAIL"
                        self.write(generation_path, generation)
                        raw["generation"]["sha256"] = hashlib.sha256(generation_path.read_bytes()).hexdigest()
                    else:
                        raw = []
                    self.write(path, raw)
                    code, stdout, _ = self.run_cli("qa-orthogonal-building", output, "--json")
                    self.assertEqual(code, 4)
                    report = json.loads(stdout)
                    self.assertEqual(report["status"], "FAIL")
                    self.assertEqual(report["full_facility_validation"], {"status": "UNKNOWN", "ok": False})
            path.write_bytes(b'{"format":"forged",' + original[1:])
            code, stdout, _ = self.run_cli("qa-orthogonal-building", output, "--json")
            self.assertEqual(code, 4)
            self.assertEqual(json.loads(stdout)["status"], "FAIL")

    def test_actual_caller_profile_is_an_independent_anchor_for_single_and_set_qa(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output, policy = self.published(root)
            raw = self.read(policy)
            raw["name"] = "Different caller-declared policy"
            self.write(policy, raw)
            for path in (output, output / "variant_01"):
                with self.subTest(path=path):
                    code, stdout, _ = self.run_cli("qa-orthogonal-building", path, "--profile", policy, "--json")
                    self.assertEqual(code, 4)
                    self.assertEqual(json.loads(stdout)["status"], "FAIL")

    def test_rehashed_physical_dxf_corruption_cannot_pass_single_bundle_qa(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output, _ = self.published(root)
            child = output / "variant_01"
            dxf_path = child / "geometry.dxf"
            document = ezdxf.readfile(dxf_path)
            polygon = list(document.modelspace().query('LWPOLYLINE[layer=="A-ROOM"]'))[0]
            points = list(polygon.get_points())
            points[0] = (points[0][0] + 100, *points[0][1:])
            polygon.set_points(points)
            document.saveas(dxf_path)
            manifest = self.read(child / "manifest.json")
            record = manifest["artifacts"]["dxf"]
            record["sha256"] = hashlib.sha256(dxf_path.read_bytes()).hexdigest()
            index_path = child / record["index"]
            index = self.read(index_path)
            index["artifact_sha256"] = record["sha256"]
            self.write(index_path, index)
            record["index_sha256"] = hashlib.sha256(index_path.read_bytes()).hexdigest()
            self.write(child / "manifest.json", manifest)
            code, stdout, _ = self.run_cli("qa-orthogonal-building", child, "--json")
            self.assertEqual(code, 4)
            report = json.loads(stdout)
            self.assertEqual(next(row for row in report["checks"] if row["id"] == "DXF_READBACK")["status"], "FAIL")

    def test_actual_export_capability_failure_keeps_evidence_without_partial_publication(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            raw = self.single_room(2000).to_dict()
            raw["structural_grid"] = {"axes_x_mm": [0, 2000], "axes_y_mm": [0, 2000]}
            source, policy = self.inputs(root, BuildingIR.from_mapping(raw))
            output = root / "unsupported"
            code, _, _ = self.generate(source, policy, output, "--max-room-candidates", 1)
            self.assertEqual(code, 3)
            failure = self.read(output / "generation-failure.json")
            self.assertEqual(failure["stage"], "export")
            self.assertFalse(failure["proven_building_infeasible"])
            self.assertFalse((output / "manifest.json").exists())
            self.assertFalse(list(output.glob("variant_*")))

    def test_declared_window_unknown_is_retained_without_silent_export_omission(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            raw = self.single_room(2000).to_dict()
            raw["layout"]["windows"] = [{"id": "window", "room_id": "room", "side": "bottom",
                                           "offset_mm": 500, "width_mm": 1000}]
            source, policy = self.inputs(root, BuildingIR.from_mapping(raw))
            output = root / "window-unknown"
            code, _, _ = self.generate(source, policy, output, "--max-room-candidates", 1)
            self.assertEqual(code, 4)
            failure = self.read(output / "generation-failure.json")
            self.assertEqual(failure["status"], "UNKNOWN")
            self.assertFalse(failure["proven_building_infeasible"])
            self.assertFalse((output / "manifest.json").exists())
            self.assertFalse(list(output.glob("variant_*")))

    def single_room(self, width):
        return BuildingIR.from_mapping({"layout": {
            "project_name": "Bounded draft variant fixture", "entry_room": "room",
            "boundary": {"width": width, "height": 2000},
            "rooms": [{"id": "room", "target_area": 4, "min_area": 4, "max_area": 4,
                       "min_width": 2000, "min_depth": 2000}],
        }})


if __name__ == "__main__":
    unittest.main()

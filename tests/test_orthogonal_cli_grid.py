"""Explicit grid CLI delivery binds raw input provenance and canonical axes."""

import contextlib
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest

import yaml

from layout_configurator.cli import main
from layout_configurator.facility import default_facility_profile
from layout_configurator.orthogonal_cli import validate_draft_input
from test_orthogonal_generation import _source


class OrthogonalCLIGridTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cache = {}

    @classmethod
    def tearDownClass(cls):
        cls.cache.clear()

    def cli(self, *args):
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            try:
                code = main(list(map(str, args)))
            except SystemExit as exc:
                code = exc.code
        return code, stdout.getvalue(), stderr.getvalue()

    def raw(self):
        raw = _source().to_dict()
        raw["structural_grid"] = {"axes_x_mm": [0, 2050.5, 6000], "axes_y_mm": [0, 3100.25, 6000],
                                  "labels_x": ["A", "B", "C"], "labels_y": ["1", "2", "3"]}
        return raw

    def published(self, root, suffix="json"):
        if suffix not in self.cache:
            with tempfile.TemporaryDirectory() as directory:
                original = Path(directory)
                source, profile = original / ("source." + suffix), original / "profile.json"
                text = json.dumps(self.raw()) if suffix == "json" else yaml.safe_dump(self.raw(), sort_keys=False)
                source.write_text(text, encoding="utf-8")
                profile.write_text(json.dumps(default_facility_profile().to_dict()), encoding="utf-8")
                code, stdout, stderr = self.cli("generate-orthogonal-building", source, "--output", original / "set",
                    "--profile", profile, "--seed", 1, "--deterministic-budget", .05,
                    "--max-room-candidates", 4, "--equipment-retries", 2, "--include-grid")
                self.assertEqual(code, 0, (stdout, stderr))
                self.cache[suffix] = tuple((path.relative_to(original), path.read_bytes())
                    for path in sorted(original.rglob("*")) if path.is_file())
        for relative, data in self.cache[suffix]:
            path = root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        return root / "set", root / ("source." + suffix), root / "profile.json"

    def restore(self, root, suffix="json"):
        for relative, data in self.cache[suffix]:
            (root / relative).write_bytes(data)

    def read(self, path):
        return json.loads(path.read_text(encoding="utf-8"))

    def save(self, path, value):
        path.write_text(json.dumps(value), encoding="utf-8")

    def test_actual_json_and_yaml_integer_source_axes_bind_input_bytes_and_canonical_all_format_grid(self):
        for suffix in ("json", "yaml"):
            with self.subTest(suffix=suffix), tempfile.TemporaryDirectory() as directory:
                output, source, profile = self.published(Path(directory), suffix)
                index = self.read(output / "manifest.json")
                generation = self.read(output / "generation.json")
                self.assertEqual(generation["input_evidence"], {
                    "source_file_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
                    "profile_file_sha256": hashlib.sha256(profile.read_bytes()).hexdigest()})
                self.assertEqual(index["grid_capability"]["policy"], "boundary_spanning_axes_v1")
                child = output / "variant_01"
                canonical = self.read(child / "building.json")
                self.assertEqual(canonical["structural_grid"]["axes_x_mm"], [0., 2050.5, 6000.])
                self.assertEqual(type(canonical["structural_grid"]["axes_x_mm"][0]), float)
                manifest = self.read(child / "manifest.json")
                capability = manifest["grid_capability"]
                self.assertEqual(index["grid_capability"]["variants"], [{"variant": 1, "report": capability}])
                self.assertEqual(capability["axes"][1]["start"], [2050.5, 0])
                self.assertEqual(capability["axes"][1]["end"], [2050.5, 6000])
                self.assertEqual(capability["source"]["structural_grid_sha256"], hashlib.sha256(json.dumps(
                    canonical["structural_grid"], sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest())
                self.assertEqual(index["source"]["building_sha256"], manifest["sources"]["building"]["canonical_sha256"])
                for path in (output, child):
                    code, stdout, stderr = self.cli("qa-orthogonal-building", path, "--profile", profile, "--json", "--include-grid")
                    self.assertEqual(code, 0, (stdout, stderr))
                    report = json.loads(stdout)
                    self.assertEqual(report["status"], "PASS")
                    self.assertEqual(report["full_facility_validation"], {"status": "UNKNOWN", "ok": False})
                    self.assertEqual(report["engineering_acceptance"], "PENDING")
                    qa = report["variants"][0]["report"] if path == output else report
                    self.assertEqual(qa["grid_capability"], capability)
                    self.assertEqual(qa["readbacks"]["ifc_express"]["statements"], [])
                    self.assertEqual(qa["readbacks"]["ifc"]["counts"]["grid_axes"], 6)
                    code, stdout, stderr = self.cli("qa-orthogonal-building", path, "--profile", profile, "--json")
                    self.assertEqual(code, 4, (stdout, stderr))
                    self.assertEqual(json.loads(stdout)["status"], "FAIL")

    def test_root_grid_capability_forgery_omission_and_child_axis_tamper_fail_fresh_set_checks(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output, _, _ = self.published(root)
            for branch in ("policy", "missing", "variant", "child"):
                with self.subTest(branch=branch):
                    self.restore(root)
                    index = self.read(output / "manifest.json")
                    expected = "SET_INDEX"
                    if branch == "policy":
                        index["grid_capability"]["variants"][0]["report"]["policy"]["room_alignment_enforced"] = True
                        expected = "VARIANT_01_IDENTITY"
                    elif branch == "missing":
                        del index["grid_capability"]
                    elif branch == "variant":
                        index["grid_capability"]["variants"][0]["variant"] = 2
                    else:
                        import ifcopenshell
                        child = output / "variant_01"
                        model = ifcopenshell.open(str(child / "geometry.ifc"))
                        axis = model.by_type("IfcGrid")[0].UAxes[1]
                        point = axis.AxisCurve.Points[0]
                        point.Coordinates = (point.Coordinates[0] + 100, point.Coordinates[1])
                        model.write(str(child / "geometry.ifc"))
                        manifest = self.read(child / "manifest.json")
                        artifact = manifest["artifacts"]["ifc"]
                        artifact["sha256"] = hashlib.sha256((child / "geometry.ifc").read_bytes()).hexdigest()
                        data = self.read(child / "ifc-index.json")
                        data["artifact_sha256"] = artifact["sha256"]
                        self.save(child / "ifc-index.json", data)
                        artifact["index_sha256"] = hashlib.sha256((child / "ifc-index.json").read_bytes()).hexdigest()
                        self.save(child / "manifest.json", manifest)
                        index["variants"][0]["manifest_sha256"] = hashlib.sha256((child / "manifest.json").read_bytes()).hexdigest()
                        expected = "VARIANT_01_DELIVERY"
                    self.save(output / "manifest.json", index)
                    report = validate_draft_input(output, include_grid=True)
                    self.assertEqual(report["status"], "FAIL", report)
                    self.assertEqual(next(row for row in report["checks"] if row["id"] == expected)["status"], "FAIL", report)

    def test_rehashed_generation_input_provenance_requires_strict_bounded_sha_fields(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output, _, _ = self.published(root)
            for branch in ("missing", "unknown", "bad_source", "bad_profile"):
                with self.subTest(branch=branch):
                    self.restore(root)
                    evidence = self.read(output / "generation.json")
                    if branch == "missing":
                        del evidence["input_evidence"]
                    elif branch == "unknown":
                        evidence["input_evidence"]["invented_approval"] = "PASS"
                    else:
                        evidence["input_evidence"]["source_file_sha256" if branch == "bad_source" else "profile_file_sha256"] = []
                    self.save(output / "generation.json", evidence)
                    index = self.read(output / "manifest.json")
                    index["generation"]["sha256"] = hashlib.sha256((output / "generation.json").read_bytes()).hexdigest()
                    self.save(output / "manifest.json", index)
                    report = validate_draft_input(output, include_grid=True)
                    self.assertEqual(report["status"], "FAIL", report)
                    self.assertEqual(next(row for row in report["checks"] if row["id"] == "SET_INDEX")["status"], "FAIL", report)

    def test_flag_types_and_raw_axis_coercions_are_rejected_without_partial_bundles(self):
        for value in (0, 1, "true", None):
            with self.subTest(value=value):
                report = validate_draft_input("missing-grid-draft", include_grid=value)
                self.assertEqual(report["status"], "FAIL")
                self.assertIn("include_grid requires a boolean", str(report))
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for suffix in ("json", "yaml"):
                for coordinate in (True, "2050.5", 10 ** 400):
                    with self.subTest(suffix=suffix, coordinate=repr(coordinate)):
                        raw = self.raw()
                        raw["structural_grid"]["axes_x_mm"][1] = coordinate
                        source = root / ("source." + suffix)
                        source.write_text(json.dumps(raw) if suffix == "json" else yaml.safe_dump(raw), encoding="utf-8")
                        output = root / (suffix + str(type(coordinate).__name__))
                        code, stdout, stderr = self.cli("generate-orthogonal-building", source, "--output", output, "--include-grid")
                        self.assertEqual(code, 2, (stdout, stderr))
                        self.assertFalse((output / "manifest.json").exists())
                        self.assertFalse(list(output.glob("variant_*")))
                        self.assertEqual(self.read(output / "generation-failure.json")["status"], "INPUT_REJECTED")

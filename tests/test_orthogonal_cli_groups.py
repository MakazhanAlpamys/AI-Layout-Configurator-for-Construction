"""Explicit IFC group CLI capability binds each independently checked variant."""

import contextlib
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace

import yaml

from layout_configurator.building import ZoneSpec
from layout_configurator.cli import main
from layout_configurator.orthogonal_cli import generate_draft_set, validate_draft_input
from test_orthogonal_generation import _source


class OrthogonalCLIGroupTests(unittest.TestCase):
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
        raw["zones"] = [ZoneSpec("parent", "process", ("main",)).to_dict(),
                        ZoneSpec("child", "support", ("corner",), parent_zone_id="parent").to_dict()]
        return raw

    def published(self, root, *, include_groups=True):
        if include_groups not in self.cache:
            with tempfile.TemporaryDirectory() as directory:
                original = Path(directory)
                source = original / "source.json"
                source.write_text(json.dumps(self.raw()), encoding="utf-8")
                options = ["--include-groups"] if include_groups else []
                code, stdout, stderr = self.cli("generate-orthogonal-building", source, "--output", original / "set",
                    "--seed", 1, "--deterministic-budget", .05, "--max-room-candidates", 4,
                    "--equipment-retries", 2, *options)
                self.assertEqual(code, 0, (stdout, stderr))
                self.cache[include_groups] = tuple((path.relative_to(original), path.read_bytes())
                    for path in sorted(original.rglob("*")) if path.is_file())
        for relative, data in self.cache[include_groups]:
            path = root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        return root / "set", root / "source.json"

    def restore(self, root):
        for relative, data in self.cache[True]:
            (root / relative).write_bytes(data)

    def read(self, path):
        return json.loads(path.read_text(encoding="utf-8"))

    def save(self, path, value):
        path.write_text(json.dumps(value, sort_keys=True, ensure_ascii=False), encoding="utf-8")

    def test_actual_source_only_cli_groups_and_both_reader_modes_require_explicit_opt_in(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output, source = self.published(root)
            index = self.read(output / "manifest.json")
            self.assertEqual(index["group_capability"]["policy"], "declared_semantic_groups_v1")
            capability = self.read(output / "variant_01" / "manifest.json")["group_capability"]
            self.assertEqual(index["group_capability"]["variants"], [{"variant": 1, "report": capability}])
            self.assertEqual(self.read(output / "generation.json")["input_evidence"],
                {"source_file_sha256": hashlib.sha256(source.read_bytes()).hexdigest()})
            for path in (output, output / "variant_01"):
                code, stdout, stderr = self.cli("qa-orthogonal-building", path, "--json", "--include-groups")
                self.assertEqual(code, 0, (stdout, stderr))
                report = json.loads(stdout)
                self.assertEqual(report["status"], "PASS")
                qa = report["variants"][0]["report"] if path == output else report
                self.assertIn("group_capability", qa)
                self.assertEqual(qa["group_capability"], capability)
                self.assertEqual(qa["readbacks"]["ifc"]["counts"]["zones"], 2)
                self.assertEqual(qa["readbacks"]["ifc"]["counts"]["process_systems"], 1)
                self.assertEqual(qa["readbacks"]["ifc_express"]["statements"], [])
                code, stdout, stderr = self.cli("qa-orthogonal-building", path, "--json")
                self.assertEqual(code, 4, (stdout, stderr))

    def test_set_group_forgery_missing_variant_and_rehashed_native_substitution_fail(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output, _ = self.published(root)
            for branch in ("policy", "members", "missing", "variant", "child"):
                with self.subTest(branch=branch):
                    self.restore(root)
                    index = self.read(output / "manifest.json")
                    expected = "SET_INDEX"
                    if branch == "policy":
                        index["group_capability"]["policy"] = "invented_approved_network"
                    elif branch == "members":
                        index["group_capability"]["variants"][0]["report"]["groups"][0]["members"] = []
                        expected = "VARIANT_01_IDENTITY"
                    elif branch == "missing":
                        del index["group_capability"]
                    elif branch == "variant":
                        index["group_capability"]["variants"][0]["variant"] = 2
                    else:
                        import ifcopenshell
                        child = output / "variant_01"
                        model = ifcopenshell.open(str(child / "geometry.ifc"))
                        relation = next(row for row in model.by_type("IfcRelAssignsToGroup") if row.RelatingGroup.is_a("IfcSystem"))
                        model.remove(relation)
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
                    report = validate_draft_input(output, include_groups=True)
                    self.assertEqual(report["status"], "FAIL", report)
                    self.assertEqual(next(row for row in report["checks"] if row["id"] == expected)["status"], "FAIL", report)

    def test_omitted_and_explicit_false_preserve_legacy_source_group_contract(self):
        with tempfile.TemporaryDirectory() as directory:
            output, _ = self.published(Path(directory), include_groups=False)
            manifest = self.read(output / "manifest.json")
            child = output / "variant_01"
            self.assertNotIn("group_capability", manifest)
            self.assertNotIn("group_capability", self.read(child / "manifest.json"))
            self.assertNotIn("group_capability", self.read(child / "ifc-index.json"))
            before = tuple((path.relative_to(output), path.read_bytes()) for path in sorted(output.rglob("*")) if path.is_file())
            omitted = validate_draft_input(output)
            explicit = validate_draft_input(output, include_groups=False)
            self.assertEqual(omitted["status"], "PASS", omitted)
            self.assertEqual(json.dumps(omitted, sort_keys=True), json.dumps(explicit, sort_keys=True))
            self.assertEqual(tuple((path.relative_to(output), path.read_bytes()) for path in sorted(output.rglob("*")) if path.is_file()), before)
            self.assertEqual(self.read(child / "manifest.json")["projection_scope"]["zones"]["native_ifc_projection"], "UNKNOWN")

    def test_rehashed_group_generation_input_evidence_requires_strict_sha_provenance(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output, _ = self.published(root)
            for branch in ("missing", "bad_source", "unknown"):
                with self.subTest(branch=branch):
                    self.restore(root)
                    generation = self.read(output / "generation.json")
                    if branch == "missing":
                        generation.pop("input_evidence", None)
                    elif branch == "bad_source":
                        generation["input_evidence"] = {"source_file_sha256": "forged"}
                    else:
                        generation["input_evidence"] = {"source_file_sha256": "0" * 64, "invented_approval": "PASS"}
                    self.save(output / "generation.json", generation)
                    index = self.read(output / "manifest.json")
                    index["generation"]["sha256"] = hashlib.sha256((output / "generation.json").read_bytes()).hexdigest()
                    self.save(output / "manifest.json", index)
                    report = validate_draft_input(output, include_groups=True)
                    self.assertEqual(report["status"], "FAIL", report)
                    self.assertEqual(next(row for row in report["checks"] if row["id"] == "SET_INDEX")["status"], "FAIL", report)

    def test_strict_opt_flags_and_raw_boolean_group_declarations_reject_before_generation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for flag in (1, None, "true", []):
                with self.subTest(flag=flag):
                    output = root / ("flag" + type(flag).__name__)
                    args = SimpleNamespace(output=output, spec=root / "missing.json", include_groups=flag)
                    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                        self.assertEqual(generate_draft_set(args), 2)
                    self.assertEqual(self.read(output / "generation-failure.json")["status"], "INPUT_REJECTED")
                    self.assertFalse((output / "manifest.json").exists())
                    self.assertEqual(validate_draft_input(root / "missing", include_groups=flag)["status"], "FAIL")
            for suffix in ("json", "yaml"):
                for field in ("airlock", "required"):
                    with self.subTest(suffix=suffix, field=field):
                        raw = self.raw()
                        raw["zones" if field == "airlock" else "flows"][0][field] = "false"
                        source = root / (field + "." + suffix)
                        source.write_text(json.dumps(raw) if suffix == "json" else yaml.safe_dump(raw), encoding="utf-8")
                        output = root / (suffix + field)
                        code, stdout, stderr = self.cli("generate-orthogonal-building", source, "--output", output, "--include-groups")
                        self.assertEqual(code, 2, (stdout, stderr))
                        self.assertEqual(self.read(output / "generation-failure.json")["status"], "INPUT_REJECTED")
                        self.assertFalse((output / "manifest.json").exists())
                        self.assertFalse(list(output.glob("variant_*")))

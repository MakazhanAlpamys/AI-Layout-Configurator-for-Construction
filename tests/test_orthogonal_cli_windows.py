"""Windows require explicit CLI opt-in through generation and saved QA."""

import contextlib
import copy
import hashlib
import io
import json
from pathlib import Path
import shutil
import tempfile
import unittest

from layout_configurator.cli import main
from layout_configurator.facility import default_facility_profile
from layout_configurator.orthogonal_cli import validate_draft_input
import test_orthogonal_generation_windows as fixtures


class OrthogonalCLIWindowTests(unittest.TestCase):
    @classmethod
    def tearDownClass(cls):
        if hasattr(cls, "_published"):
            cls._published.cleanup()

    def cli(self, *args):
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            try:
                code = main(list(map(str, args)))
            except SystemExit as exc:
                code = exc.code
        return code, stdout.getvalue(), stderr.getvalue()

    def published(self, root):
        if not hasattr(type(self), "_published"):
            temporary = tempfile.TemporaryDirectory()
            origin = Path(temporary.name)
            source, profile = origin / "source.json", origin / "profile.json"
            source.write_text(json.dumps(fixtures._source().to_dict()), encoding="utf-8")
            profile.write_text(json.dumps(default_facility_profile().to_dict()), encoding="utf-8")
            code, stdout, stderr = self.cli("generate-orthogonal-building", source, "--output", origin / "set",
                "--profile", profile, "--seed", 1, "--deterministic-budget", .05,
                "--max-room-candidates", 4, "--equipment-retries", 2, "--include-windows")
            self.assertEqual(code, 0, (stdout, stderr))
            type(self)._published = temporary
        origin = Path(type(self)._published.name)
        shutil.copytree(origin / "set", root / "set")
        shutil.copy2(origin / "profile.json", root / "profile.json")
        return root / "set", root / "profile.json"

    def test_actual_cli_source_windows_persist_three_formats_and_both_fresh_qa_modes(self):
        with tempfile.TemporaryDirectory() as directory:
            output, profile = self.published(Path(directory))
            index = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(index["window_capability"]["policy"], "source_facade_v1")
            root_capability = index["window_capability"]["variants"][0]
            self.assertEqual(root_capability["variant"], 1)
            child = output / "variant_01"
            manifest = json.loads((child / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(root_capability["report"], manifest["window_capability"])
            self.assertEqual(root_capability["report"]["facade"]["windows"][0]["id"], "source_window")
            self.assertEqual(root_capability["report"]["full_facility_validation"], {"status": "UNKNOWN", "ok": False})
            for path in (output, child):
                with self.subTest(path=path):
                    code, stdout, stderr = self.cli("qa-orthogonal-building", path, "--profile", profile,
                        "--json", "--include-windows")
                    self.assertEqual(code, 0, (stdout, stderr))
                    report = json.loads(stdout)
                    self.assertEqual(report["status"], "PASS")
                    self.assertEqual(report["full_facility_validation"], {"status": "UNKNOWN", "ok": False})
                    qa = report["variants"][0]["report"] if path == output else report
                    self.assertEqual(next(row for row in qa["checks"] if row["id"] == "IFC_EXPRESS")["status"], "PASS")
                    self.assertEqual(qa["window_capability"], manifest["window_capability"])
                    # Metadata cannot grant omitted CLI permission to windows.
                    code, stdout, stderr = self.cli("qa-orthogonal-building", path, "--profile", profile, "--json")
                    self.assertEqual(code, 4, (stdout, stderr))
                    self.assertEqual(json.loads(stdout)["status"], "FAIL")

    def test_root_capability_cannot_be_forged_or_removed_despite_matching_child_manifest_hash(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output, _ = self.published(root)
            index_path = output / "manifest.json"
            original = json.loads(index_path.read_text(encoding="utf-8"))
            changed = copy.deepcopy(original)
            changed["window_capability"]["variants"][0]["report"]["policy"]["windows_are_circulation_portals"] = True
            index_path.write_text(json.dumps(changed), encoding="utf-8")
            report = validate_draft_input(output, include_windows=True)
            self.assertEqual(report["status"], "FAIL", report)
            self.assertTrue(any(row["id"] == "VARIANT_01_IDENTITY" and row["status"] == "FAIL" for row in report["checks"]))
            self.assertEqual(hashlib.sha256((output / "variant_01" / "manifest.json").read_bytes()).hexdigest(),
                changed["variants"][0]["manifest_sha256"])
            del changed["window_capability"]
            index_path.write_text(json.dumps(changed), encoding="utf-8")
            report = validate_draft_input(output, include_windows=True)
            self.assertEqual(report["status"], "FAIL")

    def test_public_set_checker_rejects_non_boolean_opt_in_before_reading_input(self):
        for value in (0, 1, "true", None):
            with self.subTest(value=value):
                report = validate_draft_input("missing-window-draft", include_windows=value)
                self.assertEqual(report["status"], "FAIL")
                self.assertIn("include_windows requires a boolean", str(report))

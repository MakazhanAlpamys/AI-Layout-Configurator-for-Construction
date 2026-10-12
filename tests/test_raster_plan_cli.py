import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from PIL import Image

from test_raster_plan_import import TSV


class RasterPlanCliTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.source = self.root / "plan.png"
        Image.new("RGB", (80, 40), "white").save(self.source)
        self.tsv = self.root / "labels.tsv"
        self.tsv.write_bytes(TSV.encode())
        self.draft = self.root / "raster.json"

    def _run(self, *args):
        return subprocess.run([sys.executable, "-m", "layout_configurator.cli", *map(str, args)],
            capture_output=True, text=True, timeout=25,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))

    def test_import_raster_without_ocr_creates_pixel_source_draft_with_unknown_status(self):
        result = self._run("import-raster", self.source, "--output", self.draft)
        self.assertEqual(result.returncode, 0, result.stderr)
        raw = json.loads(self.draft.read_text())
        self.assertEqual(raw["format"], "FLC-raster-plan-draft")
        self.assertEqual(raw["source"]["sha256"], hashlib.sha256(self.source.read_bytes()).hexdigest())
        self.assertEqual(raw["ocr"]["status"], "UNKNOWN")
        self.assertIn("DRAFT", result.stdout)
        self.assertIn("pixels", result.stdout)
        self.assertFalse(self.draft.with_suffix(".confirmation.json").exists())

    def test_external_tsv_command_keeps_word_candidates_and_labels_without_geometry_inference(self):
        result = self._run("import-raster", self.source, "--output", self.draft, "--ocr-tsv", self.tsv)
        self.assertEqual(result.returncode, 0, result.stderr)
        raw = json.loads(self.draft.read_text())
        self.assertEqual([word["text"] for word in raw["ocr"]["words"]], ["ROOM", "9000"])
        self.assertEqual(raw["ocr"]["status"], "UNVERIFIED")
        self.assertNotIn("rooms", raw)
        self.assertNotIn("unit_scale_mm", self.draft.read_text())

    def test_requested_unavailable_ocr_engine_creates_explicit_unknown_without_traceback(self):
        result = self._run("import-raster", self.source, "--output", self.draft,
            "--ocr-tesseract", "--tesseract-path", self.root / "missing.exe")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(self.draft.read_text())["ocr"]["status"], "UNKNOWN")
        self.assertNotIn("Traceback", result.stderr)

    def test_invalid_modes_parameters_and_malformed_inputs_do_not_create_draft(self):
        for options in (("--ocr-tsv", self.tsv, "--ocr-tesseract"),
                        ("--tesseract-path", self.root / "engine"),
                        ("--ocr-tesseract", "--ocr-language", "--version"),
                        ("--ocr-tesseract", "--ocr-timeout", "nan")):
            with self.subTest(options=options):
                result = self._run("import-raster", self.source, "--output", self.draft, *options)
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertNotIn("Traceback", result.stderr)
                self.assertFalse(self.draft.exists())
        self.source.write_bytes(b"bad image")
        result = self._run("import-raster", self.source, "--output", self.draft)
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertNotIn("Traceback", result.stderr)
        self.assertFalse(self.draft.exists())

    def test_confirmed_raster_draft_is_not_accepted_by_fixed_geometry_loader(self):
        result = self._run("import-raster", self.source, "--output", self.draft, "--ocr-tsv", self.tsv)
        self.assertEqual(result.returncode, 0, result.stderr)
        spec = self.root / "spec.json"
        spec.write_text(json.dumps({"version": "0.1", "project_name": "Explicit program",
            "boundary": {"width": 6000, "height": 6000},
            "rooms": [{"id": "room", "area": 9, "min_width": 3000, "min_depth": 3000}]}))
        output = self.root / "rejected-output"
        rejected = self._run("generate", spec, "--output", output, "--imported-plan", self.draft,
                             "--confirm-import", "--import-reviewer", "Reviewer", "--room-map", "raster:word:3", "room")
        self.assertEqual(rejected.returncode, 2, rejected.stderr)
        self.assertNotIn("Traceback", rejected.stderr)
        self.assertFalse((output / "layout_01.json").exists())


if __name__ == "__main__":
    unittest.main()

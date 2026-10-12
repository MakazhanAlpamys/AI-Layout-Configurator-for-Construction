import hashlib
import importlib.util
import os
from pathlib import Path
import sys
import subprocess
import tempfile
import unittest

from PIL import Image, ImageDraw, ImageFont

from test_raster_plan_import import TSV


@unittest.skipIf(os.name == "nt", "POSIX executable fixtures; real Tesseract verified separately in Linux")
class RasterOcrProcessTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.source = self.root / "source ; touch injected.png"
        exif = Image.Exif()
        exif[274] = 6
        Image.new("RGB", (40, 80), "white").save(self.source, exif=exif)

    def api(self):
        self.assertIsNotNone(importlib.util.find_spec("layout_configurator.raster_plan_import"))
        from layout_configurator import raster_plan_import
        return raster_plan_import

    def _engine(self, body):
        engine = self.root / "engine ; touch injected"
        engine.write_text(f"#!{sys.executable}\n" + body, encoding="utf-8")
        engine.chmod(0o700)
        return engine

    def test_real_child_process_receives_upright_image_and_bounded_tsv_arguments_without_shell(self):
        api = self.api()
        engine = self._engine("from pathlib import Path\nimport sys\nfrom PIL import Image\n"
            "assert sys.argv[3:] == ['--psm', '6', '-l', 'eng', 'tsv']\n"
            "with Image.open(sys.argv[1]) as image:\n"
            "    assert image.size == (80, 40) and image.getexif().get(274, 1) == 1\n"
            f"Path(sys.argv[2] + '.tsv').write_bytes({TSV.encode()!r})\n")
        raw = api.import_raster_plan(self.source, ocr_tesseract=True, tesseract_path=engine).to_dict()
        self.assertEqual(raw["ocr"]["status"], "UNVERIFIED")
        self.assertEqual(raw["ocr"]["tsv_sha256"], hashlib.sha256(TSV.encode()).hexdigest())
        self.assertEqual([word["text"] for word in raw["ocr"]["words"]], ["ROOM", "9000"])
        self.assertEqual(raw["ocr"]["engine"], str(engine.resolve()))
        self.assertIsNone(raw["ocr"]["source_path"])
        self.assertFalse((self.root / "injected").exists())
        draft_path = self.root / "draft.json"
        api.write_raster_plan_draft(draft_path, api.import_raster_plan(self.source, ocr_tesseract=True, tesseract_path=engine))
        self.assertEqual(api.read_raster_plan_draft(draft_path).ocr["words"][0]["text"], "ROOM")

    def test_actual_child_timeout_is_unknown_without_partial_words(self):
        api = self.api()
        engine = self._engine("import time\ntime.sleep(10)\n")
        raw = api.import_raster_plan(self.source, ocr_tesseract=True,
                                    tesseract_path=engine, timeout_seconds=0.1).to_dict()
        self.assertEqual(raw["ocr"]["status"], "UNKNOWN")
        self.assertEqual(raw["ocr"]["words"], [])
        self.assertTrue(any("ocr_timeout" in issue for issue in raw["issues"]))

    def test_actual_failed_malformed_and_oversized_process_results_remain_unknown(self):
        api = self.api()
        for body, issue in (
            ("import sys\nsys.exit(3)\n", "ocr_failed"),
            ("from pathlib import Path\nimport sys\nPath(sys.argv[2]+'.tsv').write_text('not TSV')\n", "ocr_malformed"),
            ("from pathlib import Path\nimport sys\nPath(sys.argv[2]+'.tsv').write_bytes(b'x'*2097153)\n", "ocr_output_limit"),
            ("import sys\nsys.stderr.write('x'*65537)\n", "ocr_output_limit"),
        ):
            with self.subTest(issue=issue):
                raw = api.import_raster_plan(self.source, ocr_tesseract=True,
                                             tesseract_path=self._engine(body)).to_dict()
                self.assertEqual(raw["ocr"]["status"], "UNKNOWN")
                self.assertEqual(raw["ocr"]["words"], [])
                self.assertTrue(any(issue in value for value in raw["issues"]))

    def test_nonregular_source_or_tsv_cannot_block_the_bounded_reader(self):
        pipe = self.root / "pipe.png"
        os.mkfifo(pipe)
        for call in (f"import_raster_plan({str(pipe)!r})",
                     f"import_raster_plan({str(self.source)!r}, ocr_tsv={str(pipe)!r})"):
            with self.subTest(call=call):
                code = "from layout_configurator.raster_plan_import import import_raster_plan\ntry:\n    " + call + "\nexcept ValueError:\n    raise SystemExit(0)\nraise SystemExit(1)\n"
                try:
                    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=3)
                except subprocess.TimeoutExpired:
                    self.fail("Bounded source reader blocked on a nonregular input instead of rejecting it")
                self.assertEqual(result.returncode, 0, result.stderr)


class OptionalInstalledTesseractTests(unittest.TestCase):
    @unittest.skipUnless(os.environ.get("FLC_TEST_TESSERACT"), "Opt-in real local OCR executable integration")
    def test_real_tesseract_reports_known_labels_in_pixel_space_without_room_semantics(self):
        from layout_configurator.raster_plan_import import import_raster_plan
        from layout_configurator import drawing_sheets

        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "real-labels.png"
            image = Image.new("RGB", (1000, 250), "white")
            font = ImageFont.truetype(str(Path(drawing_sheets.__file__).parent / "fonts/NotoSans-Regular.ttf"), 64)
            ImageDraw.Draw(image).text((30, 60), "ROOM 9000", font=font, fill="black")
            image.save(source)
            raw = import_raster_plan(source, ocr_tesseract=True,
                                     tesseract_path=os.environ["FLC_TEST_TESSERACT"]).to_dict()
            self.assertEqual(raw["ocr"]["status"], "UNVERIFIED")
            self.assertEqual([word["text"] for word in raw["ocr"]["words"]], ["ROOM", "9000"])
            self.assertTrue(all(0 <= word["confidence"] <= 100 for word in raw["ocr"]["words"]))
            self.assertTrue(all(word["pixel_box"]["width"] > 0 for word in raw["ocr"]["words"]))
            self.assertNotIn("rooms", raw)
            self.assertEqual(raw["coordinates"]["units"], "px")


if __name__ == "__main__":
    unittest.main()

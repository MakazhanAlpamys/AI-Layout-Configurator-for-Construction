"""Cyrillic canonical programs must produce complete portable CLI bundles."""

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from pypdf import PdfReader
from layout_configurator.building import BuildingIR


def cyrillic_program():
    return BuildingIR.from_mapping({"layout": {
        "project_name": "\u0424\u0430\u0440\u043c\u0430\u0446\u0435\u0432\u0442\u0438\u0447\u0435\u0441\u043a\u0438\u0439 \u0437\u0430\u0432\u043e\u0434", "boundary": {"width": 6000, "height": 6000},
        "rooms": [{"id": "\u043f\u043e\u043c\u0435\u0449\u0435\u043d\u0438\u0435", "type": "\u043f\u0440\u043e\u0438\u0437\u0432\u043e\u0434\u0441\u0442\u0432\u043e", "area": 9,
                   "min_area": 9, "max_area": 9, "min_width": 3000, "min_depth": 3000}]},
        "zones": [{"id": "\u0437\u043e\u043d\u0430", "type": "\u0447\u0438\u0441\u0442\u0430\u044f", "room_ids": ["\u043f\u043e\u043c\u0435\u0449\u0435\u043d\u0438\u0435"]}],
        "equipment": [{"id": "\u0430\u043f\u043f\u0430\u0440\u0430\u0442", "type": "\u0441\u043c\u0435\u0441\u0438\u0442\u0435\u043b\u044c", "room_id": "\u043f\u043e\u043c\u0435\u0449\u0435\u043d\u0438\u0435",
                       "width_mm": 500, "depth_mm": 600, "clearance_mm": 200}]}).to_dict()


class CyrillicDrawingFontTests(unittest.TestCase):
    def test_real_cli_generates_complete_cyrillic_bundle_with_embedded_text_fonts(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "program.json"
            source.write_text(json.dumps(cyrillic_program(), ensure_ascii=False), encoding="utf-8")
            result = subprocess.run([sys.executable, "-m", "layout_configurator.cli", "generate-building", str(source),
                                     "--output", str(root / "bundle"), "--variants", "1",
                                     "--max-attempts", "1", "--time-limit", "3"],
                                    text=True, encoding="utf-8", stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                    env={**os.environ, "PYTHONIOENCODING": "utf-8"}, timeout=30)
            self.assertEqual(result.returncode, 0, result.stdout)
            output = root / "bundle"
            manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
            variant = manifest["variants"][0]
            for key in ("json", "pdf", "dxf", "ifc", "bcf"):
                self.assertTrue((output / variant[key]).is_file(), key)
            reader = PdfReader(output / variant["sheet_set"]["pdf"])
            texts = "\n".join(page.extract_text() for page in reader.pages)
            for expected in ("\u0424\u0430\u0440\u043c\u0430\u0446\u0435\u0432\u0442\u0438\u0447\u0435\u0441\u043a\u0438\u0439 \u0437\u0430\u0432\u043e\u0434", "\u043f\u043e\u043c\u0435\u0449\u0435\u043d\u0438\u0435", "\u043f\u0440\u043e\u0438\u0437\u0432\u043e\u0434\u0441\u0442\u0432\u043e", "\u0437\u043e\u043d\u0430", "\u0430\u043f\u043f\u0430\u0440\u0430\u0442", "\u0441\u043c\u0435\u0441\u0438\u0442\u0435\u043b\u044c"):
                self.assertIn(expected, texts)
            for page in reader.pages:
                fonts = [font.get_object() for font in page["/Resources"]["/Font"].values()]
                embedded = [font["/FontDescriptor"].get_object() for font in fonts if "/FontDescriptor" in font]
                self.assertTrue(embedded and all("/FontFile2" in font for font in embedded))


if __name__ == "__main__":
    unittest.main()

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from layout_configurator import api


class RuntimeArtifactFingerprintTests(unittest.TestCase):
    def test_font_content_names_and_addition_invalidate_cache_without_source_changes(self):
        with tempfile.TemporaryDirectory() as directory:
            package = Path(directory)
            source = package / "api.py"
            source.write_text("# Unchanged executable source")
            fonts = package / "fonts"
            fonts.mkdir()
            font = fonts / "regular.ttf"
            font.write_bytes(b"initial-font-data")
            with patch.object(api, "__file__", str(source)):
                first = api._runtime_fingerprint()
                self.assertEqual(api._runtime_fingerprint(), first)
                font.write_bytes(b"changed-font-data")
                changed = api._runtime_fingerprint()
                self.assertNotEqual(changed, first)
                font.rename(fonts / "renamed.ttf")
                renamed = api._runtime_fingerprint()
                self.assertNotEqual(renamed, changed)
                (fonts / "bold.ttf").write_bytes(b"second-font-data")
                self.assertNotEqual(api._runtime_fingerprint(), renamed)


if __name__ == "__main__":
    unittest.main()

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import ezdxf
from reportlab.pdfgen.canvas import Canvas

from layout_configurator.building import BuildingIR
from layout_configurator.io import load_building_result, load_result
from layout_configurator.models import Rect
from layout_configurator.qa import validate_building_bundle


class PlanImportCliTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.source = self.root / "existing.dxf"
        self.draft = self.root / "draft.json"
        self.building = BuildingIR.from_mapping({
            "version": "0.2", "layout": {
                "project_name": "Confirmed import", "boundary": {"width": 6000, "height": 6000},
                "rooms": [{"id": "room", "area": 9, "min_area": 9, "max_area": 9,
                           "min_width": 3000, "min_depth": 3000}],
            },
        })
        self.spec = self.root / "building.json"
        self.spec.write_text(json.dumps(self.building.to_dict()), encoding="utf-8")
        doc = ezdxf.new("R2018")
        doc.header["$INSUNITS"] = 4
        doc.layers.new("A-ROOM")
        doc.appids.new("LAYOUT_IR")
        outline = doc.modelspace().add_lwpolyline(
            [(1000, 1000), (4000, 1000), (4000, 4000), (1000, 4000)],
            close=True, dxfattribs={"layer": "A-ROOM"})
        outline.set_xdata("LAYOUT_IR", [(1000, "room_id=survey-room"), (1000, "room_type=room")])
        doc.saveas(self.source)

    def _run(self, *args):
        return subprocess.run([sys.executable, "-m", "layout_configurator.cli", *map(str, args)],
                              capture_output=True, text=True, timeout=25,
                              creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))

    def _import(self):
        result = self._run("import-plan", self.source, "--output", self.draft)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_import_command_writes_a_source_backed_draft_without_confirming_it(self):
        self._import()
        draft = json.loads(self.draft.read_text(encoding="utf-8"))
        self.assertEqual(draft["state"], "DRAFT")
        self.assertEqual(draft["rooms"][0]["id"], "survey-room")
        self.assertEqual(draft["source"]["path"], str(self.source.resolve()))
        self.assertEqual(draft["issues"], [])
        self.assertFalse(self.draft.with_suffix(".confirmation.json").exists())

    def test_unconfirmed_geometry_is_rejected_before_generating_artifacts(self):
        self._import()
        output = self.root / "rejected"
        result = self._run("generate-building", self.spec, "--output", output,
                           "--imported-plan", self.draft, "--room-map", "survey-room", "room")
        self.assertEqual(result.returncode, 2)
        self.assertIn("confirmation", result.stderr)
        self.assertFalse((output / "manifest.json").exists())

    def test_confirmed_building_import_preserves_coordinates_and_passes_bundle_qa(self):
        self._import()
        output = self.root / "accepted"
        result = self._run("generate-building", self.spec, "--output", output,
                           "--time-limit", "2", "--max-attempts", "1",
                           "--imported-plan", self.draft, "--confirm-import",
                           "--import-reviewer", "Geometry reviewer", "--room-map", "survey-room", "room")
        self.assertEqual(result.returncode, 0, result.stderr)
        _, placement, _ = load_building_result(output / "building_01.json")
        self.assertEqual(placement.placements["room"], Rect(1000, 1000, 3000, 3000))
        self.assertTrue(validate_building_bundle(output / "building_01.json").ok)
        manifest = json.loads((output / "manifest.json").read_text())
        self.assertEqual(manifest["import_confirmation"]["reviewer"], "Geometry reviewer")
        self.assertEqual(manifest["import_confirmation"]["room_mapping"], {"survey-room": "room"})

    def test_confirmed_layout_import_uses_the_same_fixed_geometry_contract(self):
        self._import()
        layout_path = self.root / "layout.json"
        layout_path.write_text(json.dumps(self.building.layout.to_dict()), encoding="utf-8")
        output = self.root / "layout-output"
        result = self._run("generate", layout_path, "--output", output, "--time-limit", "2",
                           "--imported-plan", self.draft, "--confirm-import",
                           "--import-reviewer", "Geometry reviewer", "--room-map", "survey-room", "room")
        self.assertEqual(result.returncode, 0, result.stderr)
        _, placement = load_result(output / "layout_01.json")
        self.assertEqual(placement.placements["room"], Rect(1000, 1000, 3000, 3000))

    def _pdf_import(self):
        source = self.root / "vector.pdf"
        canvas = Canvas(str(source), pagesize=(100, 100))
        canvas.rect(10, 20, 30, 30)
        canvas.save()
        result = self._run("import-plan", source, "--output", self.draft, "--page", "1",
                           "--unit-scale-mm", "100", "--origin-pt", "0", "0")
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(self.draft.read_text())

    def test_explicit_pdf_calibration_and_selection_generate_a_rechecked_fixed_bundle(self):
        draft = self._pdf_import()
        self.assertEqual(draft["state"], "DRAFT")
        self.assertEqual(draft["source"]["format"], "PDF")
        candidate = draft["candidates"][0]["id"]
        output = self.root / "pdf-output"
        result = self._run("generate-building", self.spec, "--output", output,
                           "--time-limit", "2", "--max-attempts", "1",
                           "--imported-plan", self.draft, "--confirm-import", "--import-reviewer", "Reviewer",
                           "--room-map", candidate, "room")
        self.assertEqual(result.returncode, 0, result.stderr)
        _, placement, _ = load_building_result(output / "building_01.json")
        self.assertEqual(placement.placements["room"], Rect(1000, 2000, 3000, 3000))
        self.assertTrue(validate_building_bundle(output / "building_01.json").ok)
        confirmation = json.loads((output / "manifest.json").read_text())["import_confirmation"]
        self.assertEqual(confirmation["candidate_mapping"], {candidate: "room"})
        self.assertEqual(confirmation["calibration"]["unit_scale_mm"], 100)

    def test_pdf_confirmation_without_candidate_selection_cannot_generate(self):
        self._pdf_import()
        output = self.root / "unselected-pdf"
        result = self._run("generate-building", self.spec, "--output", output,
                           "--imported-plan", self.draft, "--confirm-import", "--import-reviewer", "Reviewer")
        self.assertEqual(result.returncode, 2)
        self.assertIn("selection", result.stderr)
        self.assertFalse((output / "manifest.json").exists())

    def test_malformed_draft_root_is_rejected_as_an_input_error(self):
        self.draft.write_text("[]", encoding="utf-8")
        result = self._run("generate-building", self.spec, "--imported-plan", self.draft,
                           "--confirm-import", "--import-reviewer", "Reviewer", "--output", self.root / "invalid")
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertNotIn("Traceback", result.stderr)


if __name__ == "__main__":
    unittest.main()

"""Explicit project symbols remain separate from equipment physical geometry."""

from pathlib import Path
import tempfile
import unittest

import ezdxf
from pypdf import PdfReader
from pypdf.generic import ContentStream

from layout_configurator.facility import FacilityDrawingProfile
from layout_configurator.export import export_building_dxf, export_building_pdf
from layout_configurator.drawing_sheets import export_drawing_sheet_set
from test_drawing_sheets import example


class DrawingSymbolTests(unittest.TestCase):
    def test_explicit_symbols_round_trip_without_changing_default_profile(self):
        self.assertNotIn("equipment_symbols", FacilityDrawingProfile().to_dict())
        raw = {"preset": "equipment_service", "equipment_symbols": {"mixer_01": "process_vessel"}}
        profile = FacilityDrawingProfile.from_mapping(raw)
        self.assertEqual(profile.to_dict()["equipment_symbols"], raw["equipment_symbols"])
        self.assertEqual(FacilityDrawingProfile.from_mapping(profile.to_dict()), profile)
        for value in ({"unknown": "not_a_symbol"}, [], {"machine": True}, {"": "washbasin"}):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    FacilityDrawingProfile.from_mapping({"equipment_symbols": value})

    def test_actual_dxf_block_contains_chosen_detail_source_id_and_exact_physical_frame(self):
        building, layout, equipment, flows = example()
        original = (layout.to_dict(), equipment.to_dict(building))
        drawing = FacilityDrawingProfile.from_mapping({"equipment_symbols": {"mixer_01": "process_vessel"}})
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "symbols.dxf"
            export_building_dxf(path, building, layout, equipment, flows, drawing)
            document = ezdxf.readfile(path)
        insert = next(iter(document.modelspace().query('INSERT[layer=="A-EQUIP"]')))
        block = document.blocks[insert.dxf.name]
        identity = dict((tag.value.split("=", 1) for tag in insert.get_xdata("FLC_SYMBOL") if tag.code == 1000))
        self.assertEqual(identity["equipment_id"], "mixer_01")
        self.assertEqual(identity["symbol"], "process_vessel")
        rings = list(block.query("LWPOLYLINE"))
        self.assertTrue(any(len(entity) >= 12 for entity in rings), "Vessel detail is missing")
        self.assertTrue(any({tuple(point[:2]) for point in entity.get_points()} ==
                            {(0, 0), (500, 0), (500, 600), (0, 600)} for entity in rings))
        self.assertEqual((layout.to_dict(), equipment.to_dict(building)), original)

    def test_unknown_equipment_assignment_fails_before_any_export_file_is_written(self):
        building, layout, equipment, flows = example()
        drawing = FacilityDrawingProfile.from_mapping({"equipment_symbols": {"unknown": "toilet"}})
        with tempfile.TemporaryDirectory() as directory:
            for exporter, extension in ((export_building_dxf, "dxf"), (export_building_pdf, "pdf")):
                path = Path(directory) / ("bad." + extension)
                with self.subTest(exporter=exporter):
                    with self.assertRaisesRegex(ValueError, "unknown equipment"):
                        exporter(path, building, layout, equipment, flows=flows, drawing_profile=drawing)
                    self.assertFalse(path.exists())

    def test_thematic_pdf_prints_explicit_project_symbol_and_keeps_vector_paths(self):
        building, layout, equipment, flows = example()
        drawing = FacilityDrawingProfile.from_mapping({"equipment_symbols": {"mixer_01": "washbasin"}})
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sheets.pdf"
            export_drawing_sheet_set(path, building, layout, equipment, flows=flows, drawing_profile=drawing)
            reader = PdfReader(path)
            page = reader.pages[1]
            text = " ".join(page.extract_text().split())
            self.assertIn("project symbol: washbasin", text)
            self.assertIn("not manufacturer geometry", text)
            self.assertEqual(len(page.images), 0)
            operations = ContentStream(page.get_contents(), reader).operations
            self.assertTrue(any(operator == b"l" for _, operator in operations))

    def test_symbol_geometry_turns_counterclockwise_with_source_equipment_rotation(self):
        from layout_configurator.drawing_symbols import equipment_symbol_paths
        from layout_configurator.equipment import EquipmentPlacement
        from layout_configurator.models import Rect
        building, _, equipment, _ = example()
        item = building.equipment[0]
        original = equipment_symbol_paths("washbasin", item, equipment.placements[0])
        rotated = EquipmentPlacement(item.id, "process", Rect(100, 200, 600, 500), True)
        transformed = equipment_symbol_paths("washbasin", item, rotated)
        self.assertEqual(len(original), len(transformed))
        for (points, closed), (turned, turned_closed) in zip(original, transformed):
            self.assertEqual(closed, turned_closed)
            expected = [(item.depth_mm - (y - equipment.placements[0].rect.y) + 100,
                         x - equipment.placements[0].rect.x + 200) for x, y in points]
            for actual, target in zip(turned, expected):
                self.assertAlmostEqual(actual[0], target[0])
                self.assertAlmostEqual(actual[1], target[1])


if __name__ == "__main__":
    unittest.main()

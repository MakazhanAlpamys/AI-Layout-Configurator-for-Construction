"""A real bundle publishes an indexed sheet set without changing its main PDF."""

import hashlib
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from pypdf import PdfReader

from layout_configurator.bundle import write_facility_variant
from layout_configurator.export import export_building_pdf
from layout_configurator.facility import default_facility_profile, validate_building
from layout_configurator.equipment import validate_equipment_layout
from layout_configurator.flows import route_flows, validate_flow_routes
from layout_configurator.validation import validate_layout
from test_drawing_sheets import example


class DrawingSheetBundleTests(unittest.TestCase):
    def test_generated_bundle_indexes_hashed_vector_sheets_and_preserves_main_pdf(self):
        building, result, equipment, _ = example()
        profile = default_facility_profile()
        layout_report = validate_layout(building.layout, result)
        equipment_report = validate_equipment_layout(building, result, equipment)
        routes = route_flows(building, result, equipment)
        flow_report = validate_flow_routes(building, result, routes, equipment)
        facility_report = validate_building(building, result, equipment, routes, profile=profile,
                                           equipment_report=equipment_report, flow_report=flow_report)
        self.assertTrue(layout_report.ok, layout_report.issues)
        self.assertTrue(equipment_report.ok, equipment_report.issues)
        self.assertTrue(flow_report.ok, flow_report.issues)
        with tempfile.TemporaryDirectory() as directory, patch("reportlab.rl_config.invariant", 1):
            root = Path(directory)
            baseline = root / "original-main.pdf"
            export_building_pdf(baseline, building, result, equipment, layout_report, routes, flow_report, profile.drawing)
            original_hash = hashlib.sha256(baseline.read_bytes()).hexdigest()
            entry = write_facility_variant(root, building, profile, result, layout_report=layout_report,
                                            equipment=equipment, equipment_report=equipment_report,
                                            flow_routes=routes, flow_report=flow_report, facility_report=facility_report)
            main_pdf = root / entry["pdf"]
            self.assertEqual(hashlib.sha256(main_pdf.read_bytes()).hexdigest(), original_hash)
            self.assertIn("sheet_set", entry, "Generated variants omit the thematic sheet set")
            sheet_set = entry["sheet_set"]
            self.assertEqual(sheet_set["pdf"], "building_07_sheets.pdf")
            path = root / sheet_set["pdf"]
            self.assertTrue(path.is_file())
            self.assertEqual(sheet_set["sha256"], hashlib.sha256(path.read_bytes()).hexdigest())
            reader = PdfReader(path)
            self.assertEqual(len(reader.pages), len(sheet_set["pages"]))
            self.assertEqual({page["kind"] for page in sheet_set["pages"]}, {"zones", "equipment", "flows", "egress"})
            for page, index in zip(reader.pages, sheet_set["pages"]):
                self.assertIn(index["sheet_id"], page.extract_text())
                self.assertEqual(index["source_variant"], 7)
                self.assertEqual(len(page.images), 0)
            self.assertEqual(sheet_set["pages"][-1]["status"], "UNKNOWN")


if __name__ == "__main__":
    unittest.main()

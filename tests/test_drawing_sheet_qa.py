"""Independent readback rejects modified or misleading optional sheet sets."""

from copy import deepcopy
from dataclasses import replace
import hashlib
from io import BytesIO
import json
from pathlib import Path
import shutil
import tempfile
import unittest

from PIL import Image
from pypdf import PdfReader, PdfWriter
from pypdf.generic import ContentStream, TextStringObject
from reportlab.lib.pagesizes import A3, landscape
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

from layout_configurator.bundle import write_facility_variant
from layout_configurator.building import ZoneSpec
from layout_configurator.equipment import validate_equipment_layout
from layout_configurator.facility import default_facility_profile, validate_building
from layout_configurator.flows import route_flows, validate_flow_routes
from layout_configurator.qa import validate_building_bundle
from layout_configurator.validation import validate_layout
from test_drawing_sheets import example


class DrawingSheetReadbackTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.temp.cleanup)
        cls.original = Path(cls.temp.name) / "original"
        building, result, equipment, _ = example()
        cls.profile = default_facility_profile()
        routes = route_flows(building, result, equipment)
        lr = validate_layout(building.layout, result)
        er = validate_equipment_layout(building, result, equipment)
        fr = validate_flow_routes(building, result, routes, equipment)
        br = validate_building(building, result, equipment, routes, profile=cls.profile,
                              equipment_report=er, flow_report=fr)
        entry = write_facility_variant(cls.original, building, cls.profile, result,
                                       layout_report=lr, equipment=equipment, equipment_report=er,
                                       flow_routes=routes, flow_report=fr, facility_report=br)
        (cls.original / "manifest.json").write_text(json.dumps({"variants": [entry]}), encoding="utf-8")

    def setUp(self):
        self.root = Path(tempfile.mkdtemp(dir=self.temp.name))
        shutil.copytree(self.original, self.root, dirs_exist_ok=True)
        self.manifest_path = self.root / "manifest.json"
        self.manifest = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        self.entry = self.manifest["variants"][0]
        self.path = self.root / self.entry["sheet_set"]["pdf"]

    def check(self):
        self.manifest_path.write_text(json.dumps(self.manifest), encoding="utf-8")
        report = validate_building_bundle(self.root / "building_07.json", profile=self.profile)
        check = next((item for item in report.checks if item.id == "SHEET_SET_READBACK"), None)
        self.assertIsNotNone(check, "Present sheet_set is omitted from independent bundle QA")
        return report, check

    def rehash(self):
        self.entry["sheet_set"]["sha256"] = hashlib.sha256(self.path.read_bytes()).hexdigest()

    def rewrite_text(self, original, changed, page_number=0):
        writer = PdfWriter()
        writer.clone_document_from_reader(PdfReader(self.path))
        page = writer.pages[page_number]
        stream = ContentStream(page.get_contents(), writer)
        found = False
        for args, op in stream.operations:
            if op == b"Tj" and original in str(args[0]):
                args[0] = TextStringObject(str(args[0]).replace(original, changed))
                found = True
        self.assertTrue(found, "Tamper fixture did not alter the real PDF text stream")
        page.replace_contents(stream)
        writer.write(self.path)
        self.assertIn(changed, PdfReader(self.path).pages[page_number].extract_text())
        self.rehash()

    def test_generated_set_passes_readback_without_an_engineering_certification_claim(self):
        report, check = self.check()
        self.assertTrue(report.ok, report.to_dict())
        self.assertTrue(check.ok, check.to_dict())
        self.assertEqual(check.details["page_count"], 4)
        self.assertIn("no engineering certification", check.details["scope"])

    def test_legacy_bundle_without_optional_sheet_set_keeps_its_original_seven_checks(self):
        del self.entry["sheet_set"]
        self.manifest_path.write_text(json.dumps(self.manifest), encoding="utf-8")
        report = validate_building_bundle(self.root / "building_07.json", profile=self.profile)
        self.assertTrue(report.ok, report.to_dict())
        self.assertEqual(len(report.checks), 7)
        self.assertNotIn("SHEET_SET_READBACK", {item.id for item in report.checks})

    def test_missing_pdf_and_stale_hash_are_failures(self):
        self.path.unlink()
        report, check = self.check()
        self.assertFalse(report.ok)
        self.assertFalse(check.ok)
        shutil.copy2(self.original / self.path.name, self.path)
        self.path.write_bytes(self.path.read_bytes() + b"\nModified artifact\n")
        report, check = self.check()
        self.assertFalse(check.ok)
        self.assertTrue(any(issue["code"] == "SHEET_HASH" for issue in check.details["issues"]))

    def test_recomputed_hash_does_not_hide_a_corrupt_pdf(self):
        self.path.write_bytes(b"%PDF-1.3\nnot an actual document\n%%EOF\n")
        self.rehash()
        report, check = self.check()
        self.assertFalse(report.ok)
        self.assertFalse(check.ok)

    def test_malformed_and_incomplete_page_indexes_cannot_pass(self):
        original = deepcopy(self.entry["sheet_set"])
        mutations = (
            lambda value: value.update(pages="four"),
            lambda value: value.update(pages=[]),
            lambda value: value["pages"].pop(),
            lambda value: value["pages"].__setitem__(0, None),
            lambda value: value["pages"][1].update(sheet_id=value["pages"][0]["sheet_id"]),
            lambda value: value["pages"][0].update(kind="approval"),
            lambda value: value["pages"][0].update(page_number=True),
            lambda value: value["pages"][0].update(source_variant=99),
            lambda value: value["pages"][0].update(revision="P99"),
            lambda value: value["pages"][0].update(title="Approved design"),
            lambda value: value["pages"][-1].update(status="PASS"),
        )
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                self.entry["sheet_set"] = deepcopy(original)
                mutation(self.entry["sheet_set"])
                _, check = self.check()
                self.assertFalse(check.ok, check.to_dict())

    def test_present_null_sheet_set_is_not_treated_as_a_legacy_bundle(self):
        self.entry["sheet_set"] = None
        _, check = self.check()
        self.assertFalse(check.ok)

    def test_invalid_file_references_and_hash_types_return_failures(self):
        original = deepcopy(self.entry["sheet_set"])
        for field, value in (("pdf", "../elsewhere.pdf"), ("pdf", "broken\x00name.pdf"),
                             ("pdf", "building_07.json"), ("sha256", True), ("sha256", "0" * 64)):
            with self.subTest(field=field, value=value):
                self.entry["sheet_set"] = {**original, field: value}
                _, check = self.check()
                self.assertFalse(check.ok)

    def test_recomputed_hash_does_not_hide_a_changed_printed_sheet_id(self):
        self.rewrite_text("A-101-Z", "A-101-X")
        _, check = self.check()
        self.assertFalse(check.ok)

    def test_recomputed_hash_does_not_hide_a_changed_printed_revision(self):
        self.rewrite_text("P01", "P99")
        _, check = self.check()
        self.assertFalse(check.ok)

    def test_recomputed_hash_does_not_hide_a_changed_printed_source_variant(self):
        self.rewrite_text("Variant 7", "Variant 9")
        _, check = self.check()
        self.assertFalse(check.ok)

    def test_recomputed_hash_does_not_hide_a_changed_printed_project(self):
        self.rewrite_text("Sheet review facility", "Unrelated project")
        _, check = self.check()
        self.assertFalse(check.ok)

    def test_recomputed_hash_does_not_hide_the_drawing_profile_title(self):
        self.rewrite_text(self.profile.drawing.title, "Approved engineering plan")
        _, check = self.check()
        self.assertFalse(check.ok)

    def test_recomputed_hash_does_not_hide_a_changed_source_version(self):
        self.rewrite_text("BuildingIR 0.2", "BuildingIR 9.9")
        _, check = self.check()
        self.assertFalse(check.ok)

    def test_manifest_variant_cannot_relabel_a_canonical_result(self):
        self.entry["variant"] = 99
        _, check = self.check()
        self.assertFalse(check.ok)

    def test_generated_continuations_pass_and_require_their_own_printed_identity(self):
        building, result, equipment, _ = example()
        ancestors = tuple(ZoneSpec(f"zone_{i:03d}", "clean", (),
                                   parent_zone_id=f"zone_{i - 1:03d}" if i else None) for i in range(75))
        building = replace(building, zones=(*ancestors,
                           *(replace(zone, parent_zone_id="zone_074") if zone.id == "production" else zone
                             for zone in building.zones)))
        routes = route_flows(building, result, equipment)
        lr = validate_layout(building.layout, result)
        er = validate_equipment_layout(building, result, equipment)
        fr = validate_flow_routes(building, result, routes, equipment)
        br = validate_building(building, result, equipment, routes, profile=self.profile,
                              equipment_report=er, flow_report=fr)
        self.entry = write_facility_variant(self.root, building, self.profile, result,
                                           layout_report=lr, equipment=equipment, equipment_report=er,
                                           flow_routes=routes, flow_report=fr, facility_report=br)
        self.manifest["variants"][0] = self.entry
        report, check = self.check()
        self.assertTrue(report.ok, report.to_dict())
        self.assertGreater(check.details["page_count"], 4)
        pages = self.entry["sheet_set"]["pages"]
        continuation = next(row for row in pages if row["scale_denominator"] is None)
        page_number = continuation["page_number"] - 1
        self.assertIn("Refer to A-101-Z", PdfReader(self.path).pages[page_number].extract_text())
        self.rewrite_text(continuation["sheet_id"], "A-101-Z-99", page_number)
        # The unchanged reference to the primary ID must not masquerade as the header.
        _, check = self.check()
        self.assertFalse(check.ok)

    def test_recomputed_hash_does_not_hide_removed_pdf_pages(self):
        reader = PdfReader(self.path)
        writer = PdfWriter()
        for page in reader.pages[:-1]:
            writer.add_page(page)
        writer.write(self.path)
        self.rehash()
        _, check = self.check()
        self.assertFalse(check.ok)

    def test_recomputed_hash_does_not_accept_raster_content_in_a_vector_sheet(self):
        overlay = BytesIO()
        pdf = canvas.Canvas(overlay, pagesize=landscape(A3))
        pdf.drawImage(ImageReader(Image.new("RGB", (2, 2), (255, 0, 0))), 100, 100, 10, 10)
        pdf.save()
        overlay.seek(0)
        writer = PdfWriter()
        writer.clone_document_from_reader(PdfReader(self.path))
        writer.pages[0].merge_page(PdfReader(overlay).pages[0])
        writer.write(self.path)
        self.assertGreater(len(PdfReader(self.path).pages[0].images), 0)
        self.rehash()
        _, check = self.check()
        self.assertFalse(check.ok)
        self.assertTrue(any(issue["code"] == "SHEET_RASTER" for issue in check.details["issues"]))


if __name__ == "__main__":
    unittest.main()

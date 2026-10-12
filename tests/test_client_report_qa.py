"""Recomputed PDF hashes cannot conceal false source identities or check states."""

import copy
import hashlib
import importlib
from io import BytesIO
import json
from pathlib import Path
import tempfile
import unittest

from pypdf import PdfReader, PdfWriter
from pypdf.generic import ContentStream, TextStringObject
from PIL import Image
from reportlab.lib.pagesizes import A4
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

from layout_configurator.client_report import write_client_report
from test_client_report import write_source


class ClientReportReadbackTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.source = write_source(self.root)
        self.index = write_client_report(self.source)
        self.pdf = self.root / self.index["pdf"]

    def check(self, index=...):
        try:
            module = importlib.import_module("layout_configurator.client_report_qa")
        except ModuleNotFoundError:
            self.fail("Independent client-report readback is absent")
        return module.validate_client_report(self.root, self.index if index is ... else index, source=self.source)

    def tamper_text(self, old, new):
        reader = PdfReader(self.pdf)
        writer = PdfWriter(clone_from=reader)
        changed = False
        for page in writer.pages:
            stream = ContentStream(page.get_contents(), writer)
            for args, op in stream.operations:
                if op == b"Tj" and old in str(args[0]):
                    args[0] = TextStringObject(str(args[0]).replace(old, new))
                    changed = True
            page.replace_contents(stream)
        self.assertTrue(changed, "The fixture did not reach actual printed PDF text")
        with self.pdf.open("wb") as handle:
            writer.write(handle)
        self.index["sha256"] = hashlib.sha256(self.pdf.read_bytes()).hexdigest()

    def test_actual_source_pdf_and_page_index_pass_readback(self):
        report = self.check()
        self.assertTrue(report.ok, report.to_dict())
        self.assertEqual(report.page_count, len(PdfReader(self.pdf).pages))

    def test_missing_changed_or_path_escaping_file_is_rejected(self):
        original = self.pdf.read_bytes()
        self.pdf.unlink()
        self.assertFalse(self.check().ok)
        self.pdf.write_bytes(original + b"\nchanged")
        self.assertFalse(self.check().ok)
        self.pdf.write_bytes(original)
        for name in ("../outside.pdf", str(self.pdf.resolve()), "building_01.json"):
            index = copy.deepcopy(self.index)
            index["pdf"] = name
            with self.subTest(name=name):
                self.assertFalse(self.check(index).ok)

    def test_forged_or_incomplete_index_is_rejected_even_when_pdf_hash_is_real(self):
        changes = [lambda item: item["source"].update(variant=2),
                   lambda item: item["source"].update(revision="R99"),
                   lambda item: item["source"].update(sha256="0" * 64),
                   lambda item: item.update(engineering_acceptance="APPROVED"),
                   lambda item: item["pages"][0].update(page_number=True),
                   lambda item: item["pages"][1].update(page_id=item["pages"][0]["page_id"]),
                   lambda item: item["pages"].pop(),
                   lambda item: item["pages"][2].update(status="UNKNOWN"),
                   lambda item: item["pages"][3].update(source_id="fabricated-check")]
        for number, mutate in enumerate(changes):
            index = copy.deepcopy(self.index)
            mutate(index)
            with self.subTest(number=number):
                self.assertFalse(self.check(index).ok)
        for malformed in (None, [], {}, {"pages": [None]}):
            with self.subTest(malformed=malformed):
                self.assertFalse(self.check(malformed).ok)

    def test_recomputed_sha_does_not_hide_forged_printed_status(self):
        self.tamper_text("Check SOURCE_RULE | Status: UNKNOWN", "Check SOURCE_RULE | Status: PASS")
        result = self.check()
        self.assertFalse(result.ok, result.to_dict())
        self.assertTrue(any(issue["code"] == "REPORT_STATUS" for issue in result.issues))

    def test_recomputed_sha_does_not_hide_missing_evidence_or_rule_provenance(self):
        self.tamper_text("Pressure data unavailable for process", "Pressure data VERIFIED for process")
        self.assertFalse(self.check().ok)
        self.index = write_client_report(self.source)
        data = json.loads(self.source.read_text(encoding="utf-8"))
        edition = data["facility_validation"]["profile"]["rules"][0]["edition"]
        self.tamper_text(f"Edition: {edition}", "Edition: FORGED")
        self.assertFalse(self.check().ok)

    def test_source_change_and_real_pdf_page_identity_tamper_are_rejected(self):
        original = self.source.read_bytes()
        self.source.write_bytes(original + b"\n")
        self.assertFalse(self.check().ok)
        self.source.write_bytes(original)
        self.tamper_text("CR-0001", "CR-9999")
        self.assertFalse(self.check().ok)

    def test_recomputed_sha_cannot_hide_a_changed_metric(self):
        self.tamper_text("Rooms: 2", "Rooms: 99")
        self.assertFalse(self.check().ok)

    def test_long_continuation_source_evidence_survives_independent_readback(self):
        self.source = write_source(self.root, long=True)
        self.index = write_client_report(self.source)
        result = self.check()
        self.assertTrue(result.ok, result.to_dict())

    def test_raster_overlay_cannot_pass_with_an_updated_pdf_hash(self):
        overlay_bytes = BytesIO()
        overlay = canvas.Canvas(overlay_bytes, pagesize=A4)
        overlay.drawImage(ImageReader(Image.new("RGB", (2, 2), "red")), 48, 200, 10, 10)
        overlay.save()
        overlay_bytes.seek(0)
        writer = PdfWriter(clone_from=PdfReader(self.pdf))
        writer.pages[0].merge_page(PdfReader(overlay_bytes).pages[0])
        writer.write(self.pdf)
        self.index["sha256"] = hashlib.sha256(self.pdf.read_bytes()).hexdigest()
        result = self.check()
        self.assertFalse(result.ok, result.to_dict())
        self.assertTrue(any(issue["code"] == "REPORT_RASTER" for issue in result.issues))

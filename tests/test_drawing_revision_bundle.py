"""Revision sources, printed history and canonical bundle evidence agree."""

from dataclasses import replace
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from pypdf import PdfReader, PdfWriter
from pypdf.generic import ContentStream, TextStringObject

from layout_configurator.bundle import write_facility_variant
from layout_configurator.equipment import validate_equipment_layout
from layout_configurator.facility import default_facility_profile, validate_building
from layout_configurator.flows import route_flows, validate_flow_routes
from layout_configurator.qa import validate_building_bundle
from layout_configurator.validation import validate_layout
from test_drawing_revision_history import prior_record, supplied_history
from test_drawing_sheets import example


class DrawingRevisionBundleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "bundle"
        self.building, self.result, self.equipment, _ = example()
        self.profile = default_facility_profile()
        self.profile = replace(self.profile, drawing=replace(self.profile.drawing, revision="P02"))
        routes = route_flows(self.building, self.result, self.equipment)
        er = validate_equipment_layout(self.building, self.result, self.equipment)
        fr = validate_flow_routes(self.building, self.result, routes, self.equipment)
        br = validate_building(self.building, self.result, self.equipment, routes, profile=self.profile,
                              equipment_report=er, flow_report=fr)
        self.kwargs = dict(layout_report=validate_layout(self.building.layout, self.result),
                           equipment=self.equipment, equipment_report=er, flow_routes=routes,
                           flow_report=fr, facility_report=br)
        self.raw = supplied_history()

    def export(self, **kwargs):
        self.entry = write_facility_variant(self.root, self.building, self.profile, self.result,
                                           **self.kwargs, **kwargs)
        (self.root / "manifest.json").write_text(json.dumps({"variants": [self.entry]}), encoding="utf-8")
        return self.entry

    def check(self):
        (self.root / "manifest.json").write_text(json.dumps({"variants": [self.entry]}), encoding="utf-8")
        return validate_building_bundle(self.root / "building_07.json", profile=self.profile)

    def test_supplied_history_is_copied_exactly_indexed_and_printed_as_unverified_attribution(self):
        self.export(revision_history=self.raw)
        index = self.entry["sheet_set"]["revision_history"]
        canonical = json.loads((self.root / "building_07.json").read_text(encoding="utf-8"))
        self.assertEqual(canonical["drawing_revision_history"], index)
        self.assertEqual(index["previous_revision"], "P01")
        for row, supplied in zip(index["records"], self.raw["records"]):
            self.assertEqual((self.root / row["source_file"]).read_bytes(), supplied["source_json"].encode("utf-8"))
            self.assertEqual(row["source_identity"]["revision"], row["revision"])
        reader = PdfReader(self.root / self.entry["sheet_set"]["pdf"])
        self.assertEqual(len(reader.pages), 5)
        self.assertEqual([row["kind"] for row in self.entry["sheet_set"]["pages"]],
                         ["zones", "equipment", "flows", "egress", "revisions"])
        text = reader.pages[-1].extract_text()
        self.assertIn("Current P02 | Previous P01", text)
        self.assertIn("supplied attribution", text)
        self.assertIn("Engineer A", text)
        for row in self.raw["records"]:
            self.assertIn(row["date"], text)
            self.assertIn(row["source_sha256"], text)
        self.assertIn("History: A-101-REV", reader.pages[0].extract_text())
        report = self.check()
        self.assertTrue(report.ok, report.to_dict())
        self.assertIn("DRAWING_REVISION_HISTORY", {check.id for check in report.checks})

    def test_invalid_explicit_history_is_rejected_before_any_artifact_is_written(self):
        for value in ({**self.raw, "records": []}, {**self.raw, "previous_revision": "unrelated"}):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.export(revision_history=value)
            self.assertFalse(self.root.exists())

    def test_dense_history_continues_with_complete_ordered_source_records(self):
        records = [prior_record(f"R{i:02d}", f"R{i - 1:02d}" if i else None,
                                f"2026-09-{i + 1:02d}") for i in range(12)]
        for row in records:
            row["description"] = "Supplied review notes and room program changes. " * 8
        raw = {"current_revision": "P02", "previous_revision": "R11", "records": records}
        self.export(revision_history=raw)
        pages = self.entry["sheet_set"]["pages"]
        revisions = [row for row in pages if row["kind"] == "revisions"]
        self.assertGreater(len(revisions), 1)
        self.assertEqual(revisions[0]["sheet_id"], "A-101-REV")
        self.assertEqual(revisions[1]["sheet_id"], "A-101-REV-02")
        self.assertEqual(len({row["sheet_id"] for row in pages}), len(pages))
        report = self.check()
        self.assertTrue(report.ok, report.to_dict())
        reader = PdfReader(self.root / self.entry["sheet_set"]["pdf"])
        for row in revisions:
            page = reader.pages[row["page_number"] - 1]
            body = []
            page.extract_text(visitor_text=lambda text, cm, tm, font, size:
                              body.append(text.strip()) if text.strip() and 150 <= tm[5] <= 664 else None)
            self.assertTrue(body[0].startswith("Revision "), "A normal-size revision record was orphaned across pages")
            start = None
            for args, operation in page.get_contents().operations:
                if operation == b"m":
                    start = args
                elif operation == b"l" and start:
                    if abs(float(args[0]) - float(start[0])) < 0.01 and abs(float(args[1]) - float(start[1])) > 500:
                        self.assertFalse(30 < float(args[0]) < float(page.mediabox.width) - 30,
                                         "An internal plan divider crosses the full-width history text")
        text = " ".join(reader.pages[row["page_number"] - 1].extract_text() for row in revisions)
        self.assertEqual([text.index(row["source_sha256"]) for row in records],
                         sorted(text.index(row["source_sha256"]) for row in records))

    def test_a_large_record_continuation_names_its_source_revision(self):
        row = prior_record()
        row["description"] = "W" * 6000
        raw = {"current_revision": "P02", "previous_revision": "P00", "records": [row]}
        self.export(revision_history=raw)
        reader = PdfReader(self.root / self.entry["sheet_set"]["pdf"])
        self.assertGreater(len(reader.pages), 5)
        self.assertIn("Record continuation: P00", reader.pages[5].extract_text())
        report = self.check()
        self.assertTrue(report.ok, report.to_dict())

    def test_history_absent_keeps_four_themes_and_has_no_invented_records(self):
        self.export()
        self.assertEqual(len(self.entry["sheet_set"]["pages"]), 4)
        self.assertNotIn("revision_history", self.entry["sheet_set"])
        canonical = json.loads((self.root / "building_07.json").read_text(encoding="utf-8"))
        self.assertNotIn("drawing_revision_history", canonical)
        report = self.check()
        self.assertEqual(len(report.checks), 9)
        self.assertNotIn("DRAWING_REVISION_HISTORY", {check.id for check in report.checks})

    def test_manifest_cannot_invent_history_for_an_unchanged_canonical_result(self):
        self.export()
        self.entry["sheet_set"]["revision_history"] = {"current_revision": "P02", "previous_revision": "P00", "records": []}
        self.assertFalse(self.check().ok)

    def test_modified_prior_source_and_manifest_only_history_cannot_pass(self):
        self.export(revision_history=self.raw)
        index = self.entry["sheet_set"]["revision_history"]
        source = self.root / index["records"][0]["source_file"]
        source.write_bytes(source.read_bytes() + b"\nchanged\n")
        report = self.check()
        self.assertFalse(report.ok)
        history_check = next(item for item in report.checks if item.id == "DRAWING_REVISION_HISTORY")
        self.assertFalse(history_check.ok)
        source.write_bytes(self.raw["records"][0]["source_json"].encode("utf-8"))
        index["records"][0]["author"] = "Invented author"
        self.assertFalse(self.check().ok)

    def test_recomputed_pdf_hash_does_not_hide_changed_printed_source_hash(self):
        self.export(revision_history=self.raw)
        path = self.root / self.entry["sheet_set"]["pdf"]
        writer = PdfWriter()
        writer.clone_document_from_reader(PdfReader(path))
        page = writer.pages[-1]
        stream = ContentStream(page.get_contents(), writer)
        original = self.raw["records"][0]["source_sha256"]
        changed = ("0" if original[0] != "0" else "1") + original[1:]
        found = False
        for args, op in stream.operations:
            if op == b"Tj" and original in str(args[0]):
                args[0] = TextStringObject(str(args[0]).replace(original, changed))
                found = True
        self.assertTrue(found)
        page.replace_contents(stream)
        writer.write(path)
        self.assertIn(changed, PdfReader(path).pages[-1].extract_text())
        self.entry["sheet_set"]["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
        self.assertFalse(self.check().ok)


if __name__ == "__main__":
    unittest.main()

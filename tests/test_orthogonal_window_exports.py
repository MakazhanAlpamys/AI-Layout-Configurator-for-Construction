"""Opted windows retain actual facade lines and source elevations in DXF/PDF."""

from dataclasses import replace
import hashlib
import tempfile
import unittest
from pathlib import Path

import ezdxf
from pypdf import PdfReader, PdfWriter
from pypdf.generic import ContentStream, FloatObject, NameObject, TextStringObject

from layout_configurator.models import WindowSpec
from layout_configurator.orthogonal_pdf import export_orthogonal_draft_pdf
from layout_configurator.orthogonal_pdf_qa import validate_orthogonal_pdf
from layout_configurator.orthogonal_projection import build_orthogonal_wall_plan, export_orthogonal_draft_dxf
from layout_configurator.orthogonal_window_capability import validate_orthogonal_window_capability
from test_orthogonal_pdf import _fixture


class OrthogonalWindowExportTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.building, self.layout, self.equipment, self.routing = _fixture()
        self.control = self.building
        self.building = replace(self.building, layout=replace(self.building.layout, windows=(
            WindowSpec("main-window", "main", "bottom", 1000, 800),
            WindowSpec("corner-window", "corner", "right", 1000, 800))))

    def export_pdf(self):
        self.path = self.root / "draft.pdf"
        self.index = export_orthogonal_draft_pdf(self.path, self.building, self.layout,
            self.equipment, routing=self.routing, include_windows=True)
        return self.index

    def qa_pdf(self, **kwargs):
        return validate_orthogonal_pdf(self.path, self.building, self.layout,
            self.equipment, routing=self.routing, index=self.index, include_windows=True, **kwargs)

    def save_pdf(self, reader, streams):
        writer = PdfWriter()
        writer.clone_document_from_reader(reader)
        for page_number, stream in streams.items():
            # Explicitly invalidate encoded content after in-place operand edits.
            stream.operations = stream.operations
            writer.pages[page_number][NameObject("/Contents")] = writer._add_object(stream)
        with self.path.open("wb") as target:
            writer.write(target)
        self.index["sha256"] = hashlib.sha256(self.path.read_bytes()).hexdigest()

    def test_actual_dxf_windows_keep_closed_wall_material_and_source_elevations(self):
        target = self.root / "draft.dxf"
        evidence = export_orthogonal_draft_dxf(target, self.building.layout, self.layout, include_windows=True)
        capability = validate_orthogonal_window_capability(self.building.layout, self.layout)
        self.assertEqual(evidence.to_dict()["window_capability"], capability.to_dict())
        self.assertFalse(evidence.ok)
        document = ezdxf.readfile(target)
        lines = list(document.modelspace().query('LINE[layer=="A-WINDOW"]'))
        self.assertEqual(len(lines), 2)
        by_id = {}
        for line in lines:
            tags = line.get_xdata("LAYOUT_IR")
            strings = dict(tag.value.split("=", 1) for tag in tags if tag.code == 1000)
            numeric = [tag.value for tag in tags if tag.code == 1040]
            self.assertEqual(numeric, [800, 900, 1500])
            self.assertEqual(strings["kind"], "window")
            by_id[strings["id"]] = (strings, line)
        for window in capability.windows:
            strings, line = by_id[window.id]
            self.assertEqual(strings["room_id"], window.room_id)
            self.assertEqual(strings["orientation"], window.orientation)
            self.assertEqual(tuple(line.dxf.start)[:2], tuple(window.centerline.coords[0]))
            self.assertEqual(tuple(line.dxf.end)[:2], tuple(window.centerline.coords[1]))
        control = build_orthogonal_wall_plan(self.control.layout, self.layout)
        actual = build_orthogonal_wall_plan(self.building.layout, self.layout, include_windows=True)
        self.assertEqual(actual.geometry.wkb, control.geometry.wkb)
        self.assertEqual(actual.openings, control.openings)

    def test_pdf_actual_paths_source_inventory_and_capability_pass_current_readback(self):
        index = self.export_pdf()
        self.assertEqual(index["window_capability"],
                         validate_orthogonal_window_capability(self.building.layout, self.layout).to_dict())
        qa = self.qa_pdf()
        self.assertTrue(qa["ok"], qa)
        self.assertEqual(qa["actual"]["window_widths_mm"], {"main-window": 800, "corner-window": 800})
        self.assertEqual(qa["full_facility_validation"], {"status": "UNKNOWN", "ok": False})
        text = " ".join(page.extract_text() for page in PdfReader(self.path).pages)
        self.assertIn("source sill 900 mm", text)
        self.assertIn("height 1500 mm", text)
        self.assertIn("ENGINEERING ACCEPTANCE: PENDING", text)

    def test_rehashed_displaced_missing_and_renamed_window_vectors_fail(self):
        for mode in ("displaced", "missing", "renamed", "hidden"):
            with self.subTest(mode=mode):
                self.export_pdf()
                reader = PdfReader(self.path)
                stream = ContentStream(reader.pages[0].get_contents(), reader)
                operations = stream.operations
                start = next(i for i, (values, op) in enumerate(operations)
                             if op == b"BDC" and str(values[1].get("/Kind")) == "/window")
                end = next(i for i in range(start + 1, len(operations)) if operations[i][1] == b"EMC")
                if mode == "missing":
                    stream.operations = operations[:start] + operations[end + 1:]
                elif mode == "renamed":
                    operations[start][0][1][NameObject("/Id")] = TextStringObject("forged-window")
                else:
                    for values, op in operations[start + 1:end]:
                        if mode == "displaced" and op in (b"m", b"l"):
                            values[0] = FloatObject(float(values[0]) + 100)
                        if mode == "hidden" and op == b"RG":
                            values[:] = [FloatObject(1)] * 3
                self.save_pdf(reader, {0: stream})
                qa = self.qa_pdf()
                self.assertFalse(qa["ok"], qa)

    def test_rehashed_false_printed_window_height_and_index_capability_fail(self):
        self.export_pdf()
        reader = PdfReader(self.path)
        changed = False
        streams = {}
        for page_number, page in enumerate(reader.pages):
            stream = ContentStream(page.get_contents(), reader)
            for values, op in stream.operations:
                if op == b"Tj" and "height 1500 mm" in str(values[0]):
                    values[0] = TextStringObject(str(values[0]).replace("height 1500 mm", "height 1600 mm"))
                    changed = True
            streams[page_number] = stream
        self.assertTrue(changed)
        self.save_pdf(reader, streams)
        self.assertFalse(self.qa_pdf()["ok"])
        self.export_pdf()
        self.index["window_capability"]["vertical_range_mm"]["sill"] = 901
        self.assertFalse(self.qa_pdf()["ok"])

    def test_default_guards_and_strict_flags_create_no_unsupported_artifact(self):
        for flag in (None, 0, 1, "true", False):
            for kind in ("dxf", "pdf"):
                with self.subTest(flag=flag, kind=kind):
                    target = self.root / f"unsupported.{kind}"
                    with self.assertRaises(ValueError):
                        if kind == "dxf":
                            export_orthogonal_draft_dxf(target, self.building.layout, self.layout, include_windows=flag)
                        else:
                            export_orthogonal_draft_pdf(target, self.building, self.layout, include_windows=flag)
                    self.assertFalse(target.exists())

    def test_source_vertical_unknown_prevents_each_opted_export(self):
        source = replace(self.building, layout=replace(self.building.layout, window_sill_mm=1301))
        self.assertEqual(validate_orthogonal_window_capability(source.layout, self.layout).status, "UNKNOWN")
        for kind in ("dxf", "pdf"):
            with self.subTest(kind=kind):
                target = self.root / f"too-high.{kind}"
                with self.assertRaises(ValueError):
                    if kind == "dxf":
                        export_orthogonal_draft_dxf(target, source.layout, self.layout, include_windows=True)
                    else:
                        export_orthogonal_draft_pdf(target, source, self.layout, include_windows=True)
                self.assertFalse(target.exists())

    def test_default_no_window_evidence_and_opted_reader_permission_remain_explicit(self):
        target = self.root / "control.dxf"
        base = export_orthogonal_draft_dxf(target, self.control.layout, self.layout)
        self.assertNotIn("window_capability", base.to_dict())
        self.export_pdf()
        result = validate_orthogonal_pdf(self.path, self.building, self.layout,
            self.equipment, routing=self.routing, index=self.index)
        self.assertFalse(result["ok"])

    def test_opted_original_source_types_and_bounds_are_checked_before_normalization(self):
        malformed = (
            replace(self.building.layout, window_sill_mm=10 ** 400),
            replace(self.building.layout, window_height_mm=True),
            replace(self.building.layout, rooms=(
                replace(self.building.layout.rooms[0], needs_daylight=""), self.building.layout.rooms[1])),
        )
        for position, spec in enumerate(malformed):
            for kind in ("dxf", "pdf"):
                with self.subTest(position=position, kind=kind):
                    target = self.root / f"malformed-{position}.{kind}"
                    with self.assertRaises(ValueError):
                        if kind == "dxf":
                            export_orthogonal_draft_dxf(target, spec, self.layout, include_windows=True)
                        else:
                            export_orthogonal_draft_pdf(target, replace(self.building, layout=spec),
                                                       self.layout, include_windows=True)
                    self.assertFalse(target.exists())

    def test_rehashed_extra_conflicting_inventory_and_acceptance_text_fail(self):
        additions = (
            "Window main-window | host main | orientation horizontal | source width 800 mm | source sill 900 mm | height 1600 mm | closed facade barrier",
            "Window nonexistent | host main | orientation horizontal | source width 800 mm | source sill 900 mm | height 1500 mm | closed facade barrier",
            "ENGINEERING ACCEPTANCE: PASS",
        )
        for addition in additions:
            with self.subTest(addition=addition):
                self.export_pdf()
                reader = PdfReader(self.path)
                page = reader.pages[1]
                stream = ContentStream(page.get_contents(), reader)
                font = next(iter(page["/Resources"]["/Font"]))
                stream.operations = [*stream.operations,
                    ([], b"BT"), ([font, FloatObject(9)], b"Tf"),
                    ([FloatObject(v) for v in (1, 0, 0, 1, 50, 110)], b"Tm"),
                    ([TextStringObject(addition)], b"Tj"), ([], b"ET")]
                self.save_pdf(reader, {1: stream})
                actual = " ".join(page.extract_text() for page in PdfReader(self.path).pages)
                self.assertIn(addition, actual)
                self.assertFalse(self.qa_pdf()["ok"])

    def test_closed_window_inventory_retains_wrapped_rows_across_page_breaks(self):
        windows = tuple(WindowSpec(f"source-window-{position:02d}-with-a-long-identity", "main", "bottom",
                                   200 + position * 240, 80) for position in range(20))
        self.building = replace(self.building, layout=replace(self.building.layout, windows=windows))
        index = self.export_pdf()
        self.assertGreaterEqual(len(index["pages"]), 3)
        qa = self.qa_pdf()
        self.assertTrue(qa["ok"], qa)
        self.assertEqual(len(qa["actual"]["window_widths_mm"]), 20)


if __name__ == "__main__":
    unittest.main()

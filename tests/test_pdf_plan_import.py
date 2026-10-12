import hashlib
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

from reportlab.pdfgen import canvas

from layout_configurator.export import export_pdf
from layout_configurator.models import LayoutIR, LayoutResult, Rect


class PdfPlanImportTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.pdf_count = 0

    def api(self):
        self.assertIsNotNone(importlib.util.find_spec("layout_configurator.pdf_plan_import"),
                             "Source-backed vector PDF candidate import is missing")
        from layout_configurator import pdf_plan_import
        return pdf_plan_import

    def test_explicit_page_scale_and_origin_create_provisional_rectangles_with_provenance(self):
        api = self.api()
        source = self._pdf(border=True)

        draft = api.import_pdf_plan(source, page=1, unit_scale_mm=100, origin_pt=(10, 20))

        self.assertEqual(draft.state, "DRAFT")
        self.assertEqual(draft.source_sha256, hashlib.sha256(source.read_bytes()).hexdigest())
        self.assertEqual(draft.page, 1)
        self.assertEqual(draft.issues, ())
        self.assertEqual({candidate.rect for candidate in draft.candidates}, {
            Rect(0, 0, 3000, 3000), Rect(3000, 0, 3000, 3000), Rect(-1000, -2000, 9000, 9000),
        })
        self.assertTrue(draft.ambiguities)  # The border is not automatically classified as a room.
        self.assertTrue(all(candidate.id.startswith("pdf:page1:op") for candidate in draft.candidates))
        self.assertTrue(all(isinstance(candidate.operator_index, int) for candidate in draft.candidates))
        self.assertEqual(len(draft.content_sha256), 64)
        self.assertTrue(draft.page_object)
        self.assertEqual(api.import_pdf_plan(source, page=1, unit_scale_mm=100, origin_pt=(10, 20)).to_dict(), draft.to_dict())

    def test_roundtrip_requires_explicit_selection_named_confirmation_then_real_solver_validation(self):
        api = self.api()
        source = self._pdf(border=True)
        draft = api.import_pdf_plan(source, page=1, unit_scale_mm=100, origin_pt=(10, 20))
        path = self.root / "draft.json"
        api.write_pdf_plan_draft(path, draft)
        self.assertEqual(api.read_pdf_plan_draft(path).to_dict(), draft.to_dict())
        spec = _program()
        mapping = self._mapping(draft)
        for options, message in (({}, "confirmation"), ({"confirmed": True}, "reviewer"),
                                 ({"confirmed": True, "reviewer": "A"}, "selection")):
            with self.subTest(options=options):
                with self.assertRaisesRegex(ValueError, message):
                    api.load_pdf_imported_constraints(path, spec, **options)
        before = spec.to_dict()
        fixed = api.load_pdf_imported_constraints(path, spec, candidate_mapping=mapping,
                                                 confirmed=True, reviewer="Reviewer A")

        self.assertEqual(fixed, {"left": Rect(0, 0, 3000, 3000), "right": Rect(3000, 0, 3000, 3000)})
        self.assertEqual(spec.to_dict(), before)
        from layout_configurator.solver import solve_layouts
        from layout_configurator.validation import validate_layout
        result = solve_layouts(spec, variants=1, fixed_rects=fixed, time_limit_seconds=5)[0]
        self.assertEqual(result.placements, {"left": Rect(0, 0, 3000, 3000), "right": Rect(3000, 0, 3000, 3000)})
        self.assertTrue(validate_layout(spec, result).ok)
        evidence = json.loads(path.with_suffix(".confirmation.json").read_text(encoding="utf-8"))
        self.assertEqual(evidence["reviewer"], "Reviewer A")
        self.assertEqual(evidence["draft_sha256"], hashlib.sha256(path.read_bytes()).hexdigest())
        self.assertEqual(evidence["candidate_mapping"], mapping)
        self.assertEqual(evidence["calibration"], {"unit_scale_mm": 100, "origin_pt": [10, 20]})
        self.assertEqual(evidence["layout_sha256"], hashlib.sha256(json.dumps(
            spec.to_dict(), sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False
        ).encode("utf-8")).hexdigest())

    def test_missing_or_invalid_calibration_and_page_are_not_inferred(self):
        api = self.api()
        source = self._pdf()
        with self.assertRaises(TypeError):
            api.import_pdf_plan(source)
        for options in ({"page": 0}, {"page": 2}, {"page": True}, {"page": "1"},
                        {"unit_scale_mm": None}, {"unit_scale_mm": True}, {"unit_scale_mm": 0},
                        {"unit_scale_mm": "100"}, {"unit_scale_mm": float("nan")},
                        {"origin_pt": None}, {"origin_pt": [0]}, {"origin_pt": [0, True]},
                        {"origin_pt": [float("inf"), 0]}):
            with self.subTest(options=options):
                parameters = {"page": 1, "unit_scale_mm": 100, "origin_pt": (10, 20), **options}
                with self.assertRaises(ValueError):
                    api.import_pdf_plan(source, **parameters)

    def test_page_selection_and_nested_graphics_transforms_are_source_geometry(self):
        api = self.api()
        source = self.root / "pages.pdf"
        document = canvas.Canvas(str(source), pagesize=(100, 100))
        document.rect(1, 1, 5, 5)
        document.showPage()
        document.saveState()
        document.translate(10, 20)
        document.saveState()
        document.scale(2, 2)
        document.rect(0, 0, 15, 15)
        document.restoreState()
        document.rect(30, 0, 30, 30)
        document.restoreState()
        document.save()

        draft = api.import_pdf_plan(source, page=2, unit_scale_mm=100, origin_pt=(10, 20))

        self.assertEqual(draft.issues, ())
        self.assertEqual([candidate.rect for candidate in draft.candidates], [Rect(0, 0, 3000, 3000), Rect(3000, 0, 3000, 3000)])
        self.assertTrue(all(candidate.id.startswith("pdf:page2:") for candidate in draft.candidates))

    def test_closed_line_rectangle_is_supported_without_bbox_coercion(self):
        api = self.api()
        source = self.root / "paths.pdf"
        document = canvas.Canvas(str(source), pagesize=(100, 100))
        path = document.beginPath()
        path.moveTo(10, 20)
        path.lineTo(40, 20)
        path.lineTo(40, 50)
        path.lineTo(10, 50)
        path.close()
        document.drawPath(path)
        document.save()

        draft = api.import_pdf_plan(source, page=1, unit_scale_mm=100, origin_pt=(10, 20))

        self.assertEqual(draft.issues, ())
        self.assertEqual([candidate.rect for candidate in draft.candidates], [Rect(0, 0, 3000, 3000)])

    def test_complex_open_curved_clipped_and_rotated_paths_remain_issues(self):
        api = self.api()
        for kind in ("triangle", "open", "curve", "clip", "rotate"):
            with self.subTest(kind=kind):
                source = self.root / f"{kind}.pdf"
                document = canvas.Canvas(str(source), pagesize=(100, 100))
                path = document.beginPath()
                path.moveTo(10, 20)
                if kind == "curve":
                    path.curveTo(40, 20, 40, 50, 10, 50)
                else:
                    path.lineTo(40, 20)
                    path.lineTo(40, 50)
                    if kind != "triangle":
                        path.lineTo(10, 50)
                if kind != "open":
                    path.close()
                if kind == "clip":
                    document.clipPath(path, stroke=0)
                    document.rect(0, 0, 50, 50)
                elif kind == "rotate":
                    document.rotate(30)
                    document.rect(0, 0, 30, 30)
                else:
                    document.drawPath(path)
                document.save()
                draft = api.import_pdf_plan(source, page=1, unit_scale_mm=100, origin_pt=(10, 20))
                self.assertTrue(draft.issues)
                self.assertEqual(draft.candidates, ())

    def test_existing_pdf_export_does_not_turn_wall_gaps_into_room_bounding_boxes(self):
        api = self.api()
        source = self.root / "own.pdf"
        spec = _program()
        export_pdf(source, spec, LayoutResult(variant=1, placements={
            "left": Rect(0, 0, 3000, 3000), "right": Rect(3000, 0, 3000, 3000),
        }))

        draft = api.import_pdf_plan(source, page=1, unit_scale_mm=1, origin_pt=(0, 0))

        self.assertTrue(any("nonrectangular" in issue or "open_path" in issue for issue in draft.issues), draft.to_dict())
        self.assertTrue(all(candidate.rect.area_m2 < 1 for candidate in draft.candidates))
        path = self.root / "draft.json"
        api.write_pdf_plan_draft(path, draft)
        with self.assertRaisesRegex(ValueError, "issues"):
            api.load_pdf_imported_constraints(path, spec, candidate_mapping={draft.candidates[0].id: "left"},
                                             confirmed=True, reviewer="A")

    def test_changed_source_or_forged_candidate_is_rejected_before_confirmation_evidence(self):
        api = self.api()
        source = self._pdf()
        draft = api.import_pdf_plan(source, page=1, unit_scale_mm=100, origin_pt=(10, 20))
        path = self.root / "draft.json"
        raw = draft.to_dict()
        raw["candidates"][0]["rect"]["x"] = 100
        path.write_text(json.dumps(raw), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "source|candidate"):
            api.load_pdf_imported_constraints(path, _program(), candidate_mapping=self._mapping(draft),
                                             confirmed=True, reviewer="A")
        api.write_pdf_plan_draft(path, draft)
        source.write_bytes(source.read_bytes() + b"\n")
        with self.assertRaisesRegex(ValueError, "source.*hash|source.*changed"):
            api.load_pdf_imported_constraints(path, _program(), candidate_mapping=self._mapping(draft),
                                             confirmed=True, reviewer="A")
        self.assertFalse(path.with_suffix(".confirmation.json").exists())

    def test_selection_revalidates_known_ids_grid_bounds_area_dimensions_cutouts_and_overlaps(self):
        api = self.api()
        draft = api.import_pdf_plan(self._pdf(border=True), page=1, unit_scale_mm=100, origin_pt=(10, 20))
        path = self.root / "draft.json"
        api.write_pdf_plan_draft(path, draft)
        mapping = self._mapping(draft)
        for selected, message in (({"pdf:unknown": "left"}, "unknown candidate"),
                                  ({next(iter(mapping)): "unknown"}, "unknown room"),
                                  ({key: "left" for key in mapping}, "duplicate"),
                                  ({next(c.id for c in draft.candidates if c.rect.x == -1000): "left"}, "bounds")):
            with self.subTest(message=message):
                with self.assertRaisesRegex(ValueError, message):
                    api.load_pdf_imported_constraints(path, _program(), candidate_mapping=selected,
                                                     confirmed=True, reviewer="A")
        for rect, message in ((Rect(1, 0, 3000, 3000), "grid"), (Rect(0, 0, 3000, 4000), "area"),
                              (Rect(0, 0, 1000, 9000), "dimension"), (Rect(9000, 0, 3000, 3000), "bounds")):
            with self.subTest(message=message):
                source = self._pdf(rect=rect)
                altered = api.import_pdf_plan(source, page=1, unit_scale_mm=100, origin_pt=(10, 20))
                api.write_pdf_plan_draft(path, altered)
                with self.assertRaisesRegex(ValueError, message):
                    api.load_pdf_imported_constraints(path, _program(), candidate_mapping={altered.candidates[0].id: "left"},
                                                     confirmed=True, reviewer="A")
        api.write_pdf_plan_draft(path, draft)
        raw = _program().to_dict()
        raw["boundary"]["cutouts"] = [{"x": 1000, "y": 1000, "width": 1000, "height": 1000}]
        with self.assertRaisesRegex(ValueError, "cutout"):
            api.load_pdf_imported_constraints(path, LayoutIR.from_mapping(raw), candidate_mapping=mapping,
                                             confirmed=True, reviewer="A")
        source = self._pdf(overlap=True)
        overlap = api.import_pdf_plan(source, page=1, unit_scale_mm=100, origin_pt=(10, 20))
        api.write_pdf_plan_draft(path, overlap)
        with self.assertRaisesRegex(ValueError, "overlap"):
            api.load_pdf_imported_constraints(path, _program(), candidate_mapping={overlap.candidates[0].id: "left", overlap.candidates[1].id: "right"},
                                             confirmed=True, reviewer="A")

    def test_draft_schema_rejects_malformed_fields_unknown_fields_and_duplicate_json_keys(self):
        api = self.api()
        draft = api.import_pdf_plan(self._pdf(), page=1, unit_scale_mm=100, origin_pt=(10, 20)).to_dict()
        path = self.root / "draft.json"
        for location, key, value in (("source", "path", []), ("source", "sha256", "bad"),
                                     ("calibration", "unit_scale_mm", True), ("calibration", "origin_pt", [0]),
                                     (None, "page", True), (None, "state", "CONFIRMED"), (None, "extra", 1)):
            with self.subTest(key=key):
                raw = json.loads(json.dumps(draft))
                (raw[location] if location else raw)[key] = value
                path.write_text(json.dumps(raw), encoding="utf-8")
                with self.assertRaises(ValueError):
                    api.read_pdf_plan_draft(path)
        path.write_text(json.dumps(draft).replace('"state": "DRAFT"', '"state": "DRAFT", "state": "DRAFT"'), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            api.read_pdf_plan_draft(path)

    def test_unrecognized_operator_or_malformed_transform_is_not_silently_ignored(self):
        api = self.api()
        for stream in (b"10 20 30 30 re S 10 20 30 30 ZZ", b"1 0 0 1 20 cm 10 20 30 30 re S"):
            with self.subTest(stream=stream):
                source = self._raw_pdf(stream)
                draft = api.import_pdf_plan(source, page=1, unit_scale_mm=100, origin_pt=(10, 20))
                self.assertTrue(draft.issues, draft.to_dict())
                path = self.root / "draft.json"
                api.write_pdf_plan_draft(path, draft)
                with self.assertRaisesRegex(ValueError, "issues"):
                    api.load_pdf_imported_constraints(path, _program(), candidate_mapping={"pdf:page1:op0": "left"},
                                                     confirmed=True, reviewer="A")

    def test_forms_raster_page_rotation_userunit_crop_and_exhausted_bounds_are_reported(self):
        api = self.api()
        from pypdf import PdfReader, PdfWriter
        from pypdf.generic import NameObject, NumberObject, RectangleObject
        source = self.root / "form.pdf"
        document = canvas.Canvas(str(source), pagesize=(100, 100))
        document.beginForm("ROOM")
        document.rect(10, 20, 30, 30)
        document.endForm()
        document.doForm("ROOM")
        document.save()
        self.assertTrue(any("unsupported_resource" in issue for issue in api.import_pdf_plan(
            source, page=1, unit_scale_mm=100, origin_pt=(10, 20)).issues))
        inline = self._raw_pdf(b"q BI /W 1 /H 1 /CS /RGB /BPC 8 ID \xff\xff\xff EI Q")
        self.assertTrue(any("unsupported_resource" in issue for issue in api.import_pdf_plan(
            inline, page=1, unit_scale_mm=100, origin_pt=(10, 20)).issues))
        for name, value in (("/Rotate", NumberObject(90)), ("/UserUnit", NumberObject(2)),
                            ("/CropBox", RectangleObject([0, 0, 20, 30]))):
            with self.subTest(name=name):
                document = PdfWriter()
                page = document.add_page(PdfReader(self._pdf()).pages[0])
                page[NameObject(name)] = value
                path = self.root / f"page{name[1:]}.pdf"
                document.write(path)
                draft = api.import_pdf_plan(path, page=1, unit_scale_mm=100, origin_pt=(10, 20))
                self.assertTrue(draft.issues)
                self.assertEqual(draft.candidates, ())
        source = self._raw_pdf(b"0 g\n" * 20001)
        limited = api.import_pdf_plan(source, page=1, unit_scale_mm=100, origin_pt=(10, 20))
        self.assertTrue(any("operation_limit" in issue for issue in limited.issues))

    def test_dense_candidate_ambiguities_remain_a_bounded_writable_review_draft(self):
        api = self.api()
        source = self._raw_pdf(b"10 20 30 30 re S\n" * 1000)

        draft = api.import_pdf_plan(source, page=1, unit_scale_mm=100, origin_pt=(10, 20))

        self.assertEqual(len(draft.candidates), 1000)
        self.assertLessEqual(len(draft.ambiguities), 1001)
        self.assertTrue(any("diagnostic_limit" in message for message in draft.ambiguities))
        path = self.root / "dense.json"
        api.write_pdf_plan_draft(path, draft)
        self.assertEqual(len(api.read_pdf_plan_draft(path).candidates), 1000)

    def _raw_pdf(self, stream):
        from pypdf import PdfWriter
        from pypdf.generic import DecodedStreamObject, NameObject
        self.pdf_count += 1
        source = self.root / f"raw{self.pdf_count}.pdf"
        document = PdfWriter()
        page = document.add_blank_page(width=100, height=100)
        content = DecodedStreamObject()
        content.set_data(stream)
        page[NameObject("/Contents")] = content
        document.write(source)
        return source

    def _mapping(self, draft):
        by_rect = {candidate.rect: candidate.id for candidate in draft.candidates}
        return {by_rect[Rect(0, 0, 3000, 3000)]: "left", by_rect[Rect(3000, 0, 3000, 3000)]: "right"}

    def _pdf(self, *, border=False, rect=None, overlap=False):
        self.pdf_count += 1
        source = self.root / f"source{self.pdf_count}.pdf"
        document = canvas.Canvas(str(source), pagesize=(200, 140))
        if border:
            document.rect(0, 0, 90, 90)
        if rect is not None:
            document.rect(10 + rect.x / 100, 20 + rect.y / 100, rect.width / 100, rect.height / 100)
        else:
            document.rect(10, 20, 30, 30)
            document.rect(30 if overlap else 40, 20, 30, 30)
        document.drawString(14, 30, "Room label suggestion only")
        document.save()
        return source


def _program():
    return LayoutIR.from_mapping({
        "project_name": "PDF import test", "grid_mm": 100,
        "boundary": {"width": 10000, "height": 10000}, "entry_room": "left",
        "rooms": [{"id": "left", "type": "production", "target_area": 9, "min_width": 2000, "min_depth": 2000},
                  {"id": "right", "type": "room", "target_area": 9, "min_width": 2000, "min_depth": 2000}],
    })


if __name__ == "__main__":
    unittest.main()

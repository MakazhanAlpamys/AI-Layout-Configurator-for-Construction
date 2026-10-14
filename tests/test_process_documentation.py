"""Actual persisted process documents retain one source revision and evidence.

The fixtures never solve a new layout. Immutable artifact copies reduce PDF
generation work; every assertion about delivery invokes fresh readback.
"""

import copy
from dataclasses import replace
import hashlib
import importlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from pypdf import PdfReader, PdfWriter
from pypdf.generic import (ArrayObject, ContentStream, DictionaryObject, FloatObject,
                           NameObject, NumberObject, RectangleObject, TextStringObject)
from shapely.geometry import Polygon
from shapely.ops import unary_union

from test_process_review import _COMPONENTS, process_fixture, rebind
from test_transfer_program import _sha


_INVENTORY = {"source.json", "review.json", "documentation.pdf", "index.json", "qa.json"}
_SCHEDULES = {"rooms", "operations", "equipment", "transfers", "clean_zones",
              "ventilation_zones", "pressure", "scenarios"}
_REPORTS = {"production_program", "transfers", "environment", "library", "equipment_scenarios"}
_ROOM_L = ((0, 0), (6000, 0), (6000, 2000), (2000, 2000), (2000, 6000), (0, 6000))
_CLEAN_L = ((200, 200), (1800, 200), (1800, 600), (600, 600), (600, 1600), (200, 1600))


def concave_process_fixture():
    """An exact 20 + 12 + 4 square metre partition; no inferred portals."""
    raw = process_fixture()
    for room, area in zip(raw["building"]["layout"]["rooms"], (20.0, 12.0, 4.0)):
        room.update(target_area=area, min_area=area, max_area=area,
                    required_adjacency=[], preferred_adjacency=[], forbidden_adjacency=[])
    raw["building"]["layout"]["doors"] = []
    raw["building"]["flows"] = []
    raw["production_program"]["operations"][0]["flow_ids"] = []
    raw["flow_routes"]["routes"] = []
    raw["transfer_program"].update(flow_movements=[], transfers=[])
    raw["layout"].update(geometry_version="0.2", units="mm", rooms={
        "source": {"kind": "orthogonal", "units": "mm", "vertices": [list(p) for p in _ROOM_L], "area_m2": 20.0},
        "target": {"x": 2000, "y": 2000, "width": 4000, "height": 3000, "area_m2": 12.0},
        "upper": {"x": 2000, "y": 5000, "width": 4000, "height": 1000, "area_m2": 4.0}})
    environment = raw["environment_program"]
    environment["clean_zones"][0]["boundary"] = {
        "kind": "orthogonal", "units": "mm", "vertices": [list(p) for p in _CLEAN_L]}
    environment["ventilation_zones"][0]["boundary"] = {
        "kind": "orthogonal", "units": "mm", "vertices": [list(p) for p in _ROOM_L]}
    environment["pressure_edges"] = []
    rebind(raw)
    return raw


def _compose(current, added):
    a, b, c, d, e, f = current
    g, h, i, j, k, l = added
    return (a*g + c*h, b*g + d*h, a*i + c*j, b*i + d*j,
            a*k + c*l + e, b*k + d*l + f)


def actual_painted_paths(pdf_bytes, projection):
    """Read real marked paths through their transforms, without renderer code."""
    reader = PdfReader(io.BytesIO(pdf_bytes), strict=True)
    scale, (ox, oy) = projection["scale_pt_per_mm"], projection["origin_pt"]
    observed = {}
    for page in reader.pages:
        matrix, stack, tag, paths = (1, 0, 0, 1, 0, 0), [], None, []
        for values, operator in ContentStream(page.get_contents(), reader).operations:
            if operator == b"q":
                stack.append(matrix)
            elif operator == b"Q":
                matrix = stack.pop()
            elif operator == b"cm":
                matrix = _compose(matrix, tuple(float(value) for value in values))
            elif operator == b"BDC" and str(values[0]) == "/FLC":
                tag = (str(values[1]["/Kind"])[1:], str(values[1]["/Id"]))
                paths = []
            elif operator == b"EMC":
                tag, paths = None, []
            elif operator in (b"m", b"l") and tag is not None:
                a, b, c, d, e, f = matrix
                x, y = float(values[0]), float(values[1])
                point = ((a*x + c*y + e - ox)/scale, (b*x + d*y + f - oy)/scale)
                if operator == b"m":
                    paths.append([point])
                else:
                    paths[-1].append(point)
            elif operator in (b"S", b"s", b"f", b"f*", b"B", b"B*", b"b", b"b*") and tag is not None:
                observed.setdefault(tag, []).extend(copy.deepcopy(paths))
                paths = []
    return observed


class ProcessDocumentationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._pristine = {}

    @classmethod
    def tearDownClass(cls):
        cls._pristine.clear()

    def setUp(self):
        try:
            self.api = importlib.import_module("layout_configurator.process_documentation")
        except ModuleNotFoundError as exc:
            self.fail(f"The synchronized DRAFT process-documentation delivery is missing: {exc}")
        self.assertTrue(callable(getattr(self.api, "export_process_documentation", None)))
        self.assertTrue(callable(getattr(self.api, "validate_process_documentation", None)))
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.source = self.root / "original-review.json"
        self.destination = self.root / "documents"
        self.raw = process_fixture()

    def source_bytes(self, raw=None):
        return (json.dumps(self.raw if raw is None else raw, ensure_ascii=False,
                           allow_nan=False, indent=2) + "\r\n").encode("utf-8")

    def write_source(self, raw=None):
        self.source.write_bytes(self.source_bytes(raw))

    def export(self, *, raw=None):
        if raw is not None:
            self.raw = copy.deepcopy(raw)
        self.write_source()
        original = self.source.read_bytes()
        if original not in self._pristine:
            with tempfile.TemporaryDirectory() as temporary:
                folder = Path(temporary)
                source = folder / "original-review.json"
                source.write_bytes(original)
                target = folder / "documents"
                self.api.export_process_documentation(source, target)
                self._pristine[original] = tuple((p.name, p.read_bytes()) for p in sorted(target.iterdir()))
        self.destination.mkdir()
        for name, data in self._pristine[original]:
            (self.destination / name).write_bytes(data)
        self.pristine = tuple((p.name, p.read_bytes()) for p in sorted(self.destination.iterdir()))
        return self.read("index.json")

    def restore(self):
        for child in self.destination.iterdir():
            if child.name not in {name for name, _ in self.pristine}:
                child.unlink()
        for name, data in self.pristine:
            (self.destination / name).write_bytes(data)
        self.write_source()

    def read(self, name):
        return json.loads((self.destination / name).read_bytes())

    def save(self, name, value):
        (self.destination / name).write_bytes(json.dumps(value, ensure_ascii=False,
            allow_nan=False, sort_keys=True, separators=(",", ":")).encode("utf-8"))

    def rehash(self, kind):
        index = self.read("index.json")
        record = index["files"][kind]
        record["sha256"] = hashlib.sha256((self.destination / record["file"]).read_bytes()).hexdigest()
        self.save("index.json", index)

    def rebind_saved_qa_index(self):
        # Cached QA cannot be the reason a physical PDF forgery is rejected.
        # Independent readback must catch it after every declared hash is rebound.
        qa = self.read("qa.json")
        qa["index_sha256"] = hashlib.sha256((self.destination / "index.json").read_bytes()).hexdigest()
        self.save("qa.json", qa)

    def check(self, **kwargs):
        report = self.api.validate_process_documentation(self.source, self.destination, **kwargs)
        return report.to_dict() if hasattr(report, "to_dict") else report

    def pdf_text(self):
        return "\n".join(page.extract_text() for page in PdfReader(self.destination / "documentation.pdf").pages)

    def edit_role(self, kind, identifier, mutate):
        path = self.destination / "documentation.pdf"
        reader = PdfReader(path)
        writer = PdfWriter()
        writer.clone_document_from_reader(reader)
        changed = False
        for number, page in enumerate(reader.pages):
            stream = ContentStream(page.get_contents(), reader)
            operations = stream.operations
            starts = [i for i, (values, op) in enumerate(operations) if op == b"BDC"
                      and str(values[0]) == "/FLC" and str(values[1].get("/Kind")) == "/" + kind
                      and str(values[1].get("/Id")) == identifier]
            if not starts:
                continue
            start = starts[0]
            end = next(i for i in range(start + 1, len(operations)) if operations[i][1] == b"EMC")
            stream.operations = operations[:start + 1] + mutate(operations[start + 1:end]) + operations[end:]
            writer.pages[number][NameObject("/Contents")] = writer._add_object(stream)
            changed = True
            break
        self.assertTrue(changed, f"Actual source vector missing: {kind}/{identifier}")
        with path.open("wb") as target:
            writer.write(target)
        self.rehash("pdf")

    def replace_with_rectangle(self, operations, points):
        drawing = {b"m", b"l", b"h", b"re", b"S", b"s", b"f", b"f*", b"B", b"B*", b"b", b"b*"}
        style = [row for row in operations if row[1] not in drawing]
        return style + [([FloatObject(x), FloatObject(y)], b"m" if i == 0 else b"l")
                        for i, (x, y) in enumerate(points)] + [([], b"h"), ([], b"B*")]

    def test_complete_package_retains_exact_sources_groups_and_visible_scoped_identity_without_solver(self):
        self.write_source()
        before = self.source.read_bytes()
        with patch("ortools.sat.python.cp_model.CpSolver.solve", side_effect=AssertionError("Documentation cannot solve layouts")):
            index = self.api.export_process_documentation(self.source, self.destination)
            report = self.check()
        self.assertEqual({p.name for p in self.destination.iterdir()}, _INVENTORY)
        self.assertEqual(self.read("index.json"), index)
        self.assertEqual((self.destination / "source.json").read_bytes(), before)
        self.assertEqual(self.source.read_bytes(), before)
        self.assertEqual((index["status"], index["review_status"], index["ok"]), ("DRAFT", "UNKNOWN", False))
        self.assertEqual((report["status"], report["ok"]), ("PASS", True), report)
        self.assertEqual(report["full_facility_validation"], {"status": "UNKNOWN", "ok": False})
        self.assertEqual(report["engineering_acceptance"], "PENDING")
        review = self.read("review.json")
        identity = {"drawing_revision": "D01", "variant": 1,
                    "component_sha256": {name: _sha(self.raw[name]) for name in _COMPONENTS}}
        self.assertEqual(index["identity"], identity)
        self.assertEqual(review["source_frames"], {name: self.raw[name] for name in _COMPONENTS})
        self.assertEqual({row["source_id"] for row in index["pages"] if row["kind"] == "schedule"}, _SCHEDULES)
        self.assertEqual({row["source_id"] for row in index["pages"] if row["kind"] == "report"}, _REPORTS)
        self.assertEqual(set(index["files"]), {"source", "review", "pdf"})
        self.assertEqual(index["review_source"]["file_sha256"], hashlib.sha256(before).hexdigest())
        for number, page in enumerate(PdfReader(self.destination / "documentation.pdf").pages, 1):
            text = page.extract_text()
            self.assertIn("DRAFT", text)
            self.assertIn("D01", text)
            self.assertIn("Variant 1", text)
            self.assertIn(hashlib.sha256(before).hexdigest(), text)
            self.assertIn(index["pages"][number - 1]["page_id"], text)
            self.assertFalse(page.images)

    def test_known_pressure_failure_survives_unknown_library_measurements_and_independent_classification(self):
        raw = process_fixture(cycle=True)
        raw["production_program"]["sources"][0]["usage_rights"]["state"] = "UNKNOWN"
        rebind(raw)
        index = self.export(raw=raw)
        self.assertEqual(index["review_status"], "FAIL")
        self.assertTrue(self.check()["ok"])
        review = self.read("review.json")
        environment = review["reports"]["environment"]["evidence"]
        self.assertEqual((environment["pressure_resolved_status"], environment["pressure_measured_status"]), ("FAIL", "UNKNOWN"))
        self.assertEqual({edge["edge_id"] for edge in environment["resolved_network"]["cycle_witness"]},
                         {"source_target", "target_upper", "upper_source"})
        self.assertEqual(review["schedules"]["clean_zones"]["rows"][0]["gmp_grade"], {"status": "UNKNOWN", "value": None})
        text = self.pdf_text()
        for token in ("FAIL", "UNKNOWN", "source_target", "target_upper", "upper_source", "transfer_brief", "iso_class", "gmp_grade", "measured"):
            self.assertIn(token, text)
        self.assertIn("PENDING", text)
        self.assertIn("full_path_validation", text)

    def test_changed_current_input_cannot_be_accepted_after_rehashing_delivered_source_and_index(self):
        self.export()
        for change in ("bytes", "revision", "model"):
            with self.subTest(change=change):
                self.restore()
                raw = copy.deepcopy(self.raw)
                if change == "revision":
                    raw["drawing_revision"] = "D02"
                elif change == "model":
                    raw["production_program"]["operations"][0]["measurements"]["cycle_time"]["value"] = 30.0
                    rebind(raw)
                changed = self.source_bytes(raw) + (b" " if change == "bytes" else b"")
                self.source.write_bytes(changed)
                (self.destination / "source.json").write_bytes(changed)
                self.rehash("source")
                index = self.read("index.json")
                index["review_source"].update(file_sha256=hashlib.sha256(changed).hexdigest(), bytes=len(changed))
                index["identity"] = {"drawing_revision": raw["drawing_revision"], "variant": raw["variant"],
                    "component_sha256": {name: _sha(raw[name]) for name in _COMPONENTS}}
                self.save("index.json", index)
                self.assertFalse(self.check()["ok"])

    def test_forged_saved_report_witness_schedule_and_acceptance_fail_even_after_rehash(self):
        self.export(raw=process_fixture(cycle=True))
        for mutation in ("status", "witness", "schedule", "identity", "approval"):
            with self.subTest(mutation=mutation):
                self.restore()
                review = self.read("review.json")
                if mutation == "status":
                    review["reports"]["environment"]["status"] = "PASS"
                elif mutation == "witness":
                    review["reports"]["environment"]["evidence"]["resolved_network"]["cycle_witness"][0]["edge_id"] = "forged_edge"
                elif mutation == "schedule":
                    review["schedules"]["clean_zones"]["rows"][0]["gmp_grade"] = {"status": "KNOWN", "value": "A"}
                elif mutation == "identity":
                    review["schedules"]["rooms"]["identity"]["drawing_revision"] = "D02"
                else:
                    index = self.read("index.json")
                    index["engineering_acceptance"] = "APPROVED"
                    self.save("index.json", index)
                if mutation != "approval":
                    self.save("review.json", review)
                    self.rehash("review")
                self.assertFalse(self.check()["ok"])

    def test_actual_concave_room_and_clean_zone_paths_never_become_bounding_boxes(self):
        index = self.export(raw=concave_process_fixture())
        self.assertTrue(self.check()["ok"])
        actual = actual_painted_paths((self.destination / "documentation.pdf").read_bytes(), index["projection"])
        for kind, identifier, vertices, area in (("room", "source", _ROOM_L, 20_000_000),
                                                ("clean_zone", "local_clean", _CLEAN_L, 1_040_000)):
            geometry = unary_union([Polygon(points) for points in actual[(kind, identifier)]])
            self.assertAlmostEqual(geometry.area, area, places=2)
            self.assertLess(geometry.symmetric_difference(Polygon(vertices)).area, .01)
        for kind, identifier, rectangle in (("room", "source", ((0, 0), (6000, 0), (6000, 6000), (0, 6000))),
                                             ("clean_zone", "local_clean", ((200, 200), (1800, 200), (1800, 1600), (200, 1600)))):
            with self.subTest(kind=kind):
                self.restore()
                self.edit_role(kind, identifier, lambda operations: self.replace_with_rectangle(operations, rectangle))
                self.assertFalse(self.check()["ok"])

    def test_resolved_pressure_overlay_uses_the_actual_portal_and_detects_shifted_or_unpainted_paths(self):
        index = self.export()
        paths = actual_painted_paths((self.destination / "documentation.pdf").read_bytes(), index["projection"])
        expected = ((3000, 1050), (3000, 1950))
        self.assertEqual(len(paths[("pressure", "source_target")]), 1)
        for point, want in zip(paths[("pressure", "source_target")][0], expected):
            self.assertAlmostEqual(point[0], want[0], places=5)
            self.assertAlmostEqual(point[1], want[1], places=5)
        for mutation in ("shifted", "unpainted"):
            with self.subTest(mutation=mutation):
                self.restore()
                if mutation == "shifted":
                    self.edit_role("pressure", "source_target", lambda operations:
                        [([FloatObject(value) for value in (1, 0, 0, 1, 100, 0)], b"cm"), *operations])
                else:
                    self.edit_role("pressure", "source_target", lambda operations:
                        [(values, op) for values, op in operations if op not in (b"S", b"s")])
                self.assertFalse(self.check()["ok"])

    def test_printed_revision_source_status_and_witness_are_checked_beyond_the_pdf_hash(self):
        self.export(raw=process_fixture(cycle=True))
        for old, replacement in (("D01", "D02"), ("FAIL", "PASS"), ("source_target", "forged_edge"),
                                 (hashlib.sha256(self.source.read_bytes()).hexdigest(), "0" * 64)):
            with self.subTest(old=old):
                self.restore()
                path = self.destination / "documentation.pdf"
                reader = PdfReader(path)
                writer = PdfWriter()
                writer.clone_document_from_reader(reader)
                changed = False
                for number, page in enumerate(reader.pages):
                    stream = ContentStream(page.get_contents(), reader)
                    for values, operator in stream.operations:
                        if operator == b"Tj" and old in str(values[0]):
                            values[0] = TextStringObject(str(values[0]).replace(old, replacement))
                            changed = True
                            break
                    if changed:
                        writer.pages[number][NameObject("/Contents")] = writer._add_object(stream)
                        break
                self.assertTrue(changed, f"Required printed evidence missing: {old}")
                with path.open("wb") as target:
                    writer.write(target)
                self.rehash("pdf")
                self.assertFalse(self.check()["ok"])

    def test_dense_operations_paginate_without_losing_records_or_continuation_identity(self):
        raw = process_fixture()
        template = raw["production_program"]["operations"][0]
        for number in range(64):
            row = copy.deepcopy(template)
            row.update(id=f"dense_operation_{number:03d}", flow_ids=[],
                       label=f"Detailed operation {number:03d} with supplied requirements and unresolved capacity evidence")
            raw["production_program"]["operations"].append(row)
        rebind(raw)
        index = self.export(raw=raw)
        pages = [row for row in index["pages"] if row["kind"] == "schedule" and row["source_id"] == "operations"]
        self.assertGreater(len(pages), 1)
        self.assertEqual(len({row["page_id"] for row in index["pages"]}), len(index["pages"]))
        self.assertTrue(self.check()["ok"])
        text = self.pdf_text()
        for number in range(64):
            self.assertIn(f"dense_operation_{number:03d}", text)
        path = self.destination / "documentation.pdf"
        reader = PdfReader(path)
        writer = PdfWriter()
        writer.clone_document_from_reader(reader)
        del writer.pages[pages[-1]["page_number"] - 1]
        with path.open("wb") as target:
            writer.write(target)
        self.rehash("pdf")
        self.assertFalse(self.check()["ok"])

    def test_literal_markup_parentheses_and_backslashes_are_printed_without_parsing_or_execution(self):
        raw = process_fixture()
        literal = 'Mix <b>literal & untrusted</b> (A) \\ line'
        raw["production_program"]["operations"][0]["label"] = literal
        rebind(raw)
        self.export(raw=raw)
        self.assertTrue(self.check()["ok"])
        self.assertIn(literal, self.pdf_text())

    def test_malformed_or_incomplete_review_and_delivery_bounds_leave_no_published_directory(self):
        self.write_source()
        defaults = self.api.ProcessDocumentationLimits()
        for name, value in (("max_input_bytes", 50), ("max_report_bytes", 4096),
                            ("max_pages", 1), ("max_pdf_bytes", 100)):
            with self.subTest(name=name):
                target = self.root / name
                with self.assertRaises(ValueError):
                    self.api.export_process_documentation(self.source, target, limits=replace(defaults, **{name: value}))
                self.assertFalse(target.exists())
                self.assertEqual(self.source.read_bytes(), self.source_bytes())
        for text in ('{"version":"0.1","version":"0.2"}', "[" * 80 + "0" + "]" * 80):
            self.source.write_text(text, encoding="utf-8")
            with self.subTest(text=text[:30]), self.assertRaises(ValueError):
                self.api.export_process_documentation(self.source, self.destination)
            self.assertFalse(self.destination.exists())

    def test_existing_destinations_and_source_aliases_are_preserved_and_inventory_is_closed(self):
        self.write_source()
        original = self.source.read_bytes()
        existing = self.root / "existing"
        existing.mkdir()
        (existing / "keep.txt").write_bytes(b"Existing user work")
        with self.assertRaises((FileExistsError, ValueError)):
            self.api.export_process_documentation(self.source, existing)
        self.assertEqual((existing / "keep.txt").read_bytes(), b"Existing user work")
        with self.assertRaises((FileExistsError, ValueError)):
            self.api.export_process_documentation(self.source, self.source)
        alias = self.root / "source-alias.json"
        os.link(self.source, alias)
        with self.assertRaises((FileExistsError, ValueError)):
            self.api.export_process_documentation(self.source, alias)
        self.assertEqual(self.source.read_bytes(), original)
        self.export()
        (self.destination / "undeclared-approval.txt").write_text("Approved", encoding="utf-8")
        self.assertFalse(self.check()["ok"])
        self.restore()
        index = self.read("index.json")
        index["files"]["pdf"]["file"] = "../source-alias.json"
        self.save("index.json", index)
        self.assertFalse(self.check()["ok"])

    def test_changed_source_after_pdf_save_and_raced_destination_cannot_publish_stale_or_replace_work(self):
        from reportlab.pdfgen.canvas import Canvas
        self.write_source()
        original_save = Canvas.save

        def change_source(document):
            original_save(document)
            self.source.write_bytes(self.source.read_bytes() + b" ")

        with patch.object(Canvas, "save", change_source), self.assertRaises(ValueError):
            self.api.export_process_documentation(self.source, self.destination)
        self.assertFalse(self.destination.exists())
        self.write_source()
        original_publish = self.api._publish_directory

        def race(stage, target):
            Path(target).mkdir()
            (Path(target) / "keep.txt").write_bytes(b"Raced user work")
            return original_publish(stage, target)

        with patch.object(self.api, "_publish_directory", race), self.assertRaises((FileExistsError, ValueError)):
            self.api.export_process_documentation(self.source, self.destination)
        self.assertEqual((self.destination / "keep.txt").read_bytes(), b"Raced user work")
        self.assertEqual({p.name for p in self.destination.iterdir()}, {"keep.txt"})

    def test_crop_shifted_page_origin_and_opaque_annotation_cannot_hide_printed_evidence(self):
        self.export()
        for mutation in ("crop", "origin", "annotation"):
            with self.subTest(mutation=mutation):
                self.restore()
                path = self.destination / "documentation.pdf"
                reader = PdfReader(path)
                writer = PdfWriter()
                writer.clone_document_from_reader(reader)
                page = writer.pages[0]
                if mutation == "crop":
                    page.cropbox = RectangleObject([0, 0, 10, 10])
                elif mutation == "origin":
                    page.mediabox = RectangleObject([50, 50, float(page.mediabox.width) + 50,
                                                    float(page.mediabox.height) + 50])
                else:
                    annotation = DictionaryObject({
                        NameObject("/Type"): NameObject("/Annot"),
                        NameObject("/Subtype"): NameObject("/Square"),
                        NameObject("/Rect"): RectangleObject([0, 0, float(page.mediabox.width),
                                                              float(page.mediabox.height)]),
                        NameObject("/IC"): ArrayObject([NumberObject(1)] * 3),
                        NameObject("/F"): NumberObject(4),
                    })
                    page[NameObject("/Annots")] = ArrayObject([writer._add_object(annotation)])
                with path.open("wb") as target:
                    writer.write(target)
                self.rehash("pdf")
                self.rebind_saved_qa_index()
                self.assertFalse(self.check()["ok"], "Actual visible page bounds and covering annotations require readback")

    def test_zero_area_text_transform_cannot_authorize_extracted_source_strings(self):
        self.export()
        path = self.destination / "documentation.pdf"
        reader = PdfReader(path)
        writer = PdfWriter()
        writer.clone_document_from_reader(reader)
        stream = ContentStream(reader.pages[0].get_contents(), reader)
        changed = False
        for values, operator in stream.operations:
            if operator == b"Tm":
                values[:4] = [FloatObject(1), FloatObject(0), FloatObject(1), FloatObject(0)]
                changed = True
                break
        self.assertTrue(changed)
        writer.pages[0][NameObject("/Contents")] = writer._add_object(stream)
        with path.open("wb") as target:
            writer.write(target)
        self.rehash("pdf")
        self.rebind_saved_qa_index()
        self.assertFalse(self.check()["ok"], "Extractable text with zero glyph area is not visible evidence")


if __name__ == "__main__":
    unittest.main()

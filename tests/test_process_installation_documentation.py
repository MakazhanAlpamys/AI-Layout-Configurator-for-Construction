"""Saved process/path documents preserve literal admitted motion and source bytes.

Pristine artifact bytes are cached only to avoid redundant PDF generation.
Every acceptance or tampering assertion performs fresh persisted readback.
"""

import copy
from dataclasses import asdict, replace
import hashlib
import importlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from pypdf import PdfReader, PdfWriter
from pypdf.generic import ContentStream, FloatObject, NameObject, TextStringObject
from shapely.geometry import Polygon
from shapely.ops import unary_union

from layout_configurator.process_documentation import ProcessDocumentationLimits
from layout_configurator.result_geometry import parse_layout_result
from test_engineering_library import source_content
from test_equipment_installation_path import refresh_binding
from test_process_documentation import actual_painted_paths
from test_process_installation_review import installation_process_fixture
from test_process_review import _COMPONENTS, rebind
from test_transfer_program import _sha


_INVENTORY = {"source.json", "review.json", "documentation.pdf", "index.json", "qa.json"}


class ProcessInstallationDocumentationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._pristine = {}

    @classmethod
    def tearDownClass(cls):
        cls._pristine.clear()

    def setUp(self):
        try:
            self.api = importlib.import_module("layout_configurator.process_installation_documentation")
        except ModuleNotFoundError as exc:
            self.fail(f"Explicit source-bound process installation documents are missing: {exc}")
        self.assertTrue(callable(getattr(self.api, "export_process_documentation_with_paths", None)))
        self.assertTrue(callable(getattr(self.api, "validate_process_documentation_with_paths", None)))
        self.review_api = importlib.import_module("layout_configurator.process_installation_review")
        self.legacy_api = importlib.import_module("layout_configurator.process_documentation")
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.source = self.root / "original-process-paths.json"
        self.destination = self.root / "documents"
        self.raw, self.building, self.layout, self.equipment = installation_process_fixture()

    def source_bytes(self, raw=None):
        return (json.dumps(self.raw if raw is None else raw, ensure_ascii=False,
                           allow_nan=False, indent=2) + "\r\n").encode("utf-8")

    def write_source(self):
        self.source.write_bytes(self.source_bytes())

    def read(self, name):
        return json.loads((self.destination / name).read_bytes())

    def save(self, name, value):
        (self.destination / name).write_bytes(json.dumps(value, sort_keys=True,
            ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8"))

    def export(self, raw=None):
        if raw is not None:
            self.raw = copy.deepcopy(raw)
        self.write_source()
        original = self.source.read_bytes()
        if original not in self._pristine:
            with tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                source = root / "original.json"
                source.write_bytes(original)
                target = root / "documents"
                with patch("ortools.sat.python.cp_model.CpSolver.solve",
                           side_effect=AssertionError("A supplied document must not solve a layout")):
                    self.api.export_process_documentation_with_paths(source, target)
                self._pristine[original] = tuple((p.name, p.read_bytes()) for p in sorted(target.iterdir()))
        self.destination.mkdir()
        for name, data in self._pristine[original]:
            (self.destination / name).write_bytes(data)
        self.pristine = tuple((p.name, p.read_bytes()) for p in sorted(self.destination.iterdir()))
        return self.read("index.json")

    def restore(self):
        for child in self.destination.iterdir():
            if child.name not in _INVENTORY:
                child.unlink()
        for name, data in self.pristine:
            (self.destination / name).write_bytes(data)
        self.write_source()

    def check(self, **options):
        return self.api.validate_process_documentation_with_paths(self.source, self.destination, **options)

    def rebind_hashes(self, *kinds):
        index = self.read("index.json")
        for kind in kinds:
            row = index["files"][kind]
            row["sha256"] = hashlib.sha256((self.destination / row["file"]).read_bytes()).hexdigest()
        self.save("index.json", index)
        # Cached metadata must not be the reason physical PDF forgeries fail.
        qa = self.read("qa.json")
        qa["index_sha256"] = hashlib.sha256((self.destination / "index.json").read_bytes()).hexdigest()
        self.save("qa.json", qa)

    def pdf_text(self):
        return "\n".join(page.extract_text() for page in PdfReader(self.destination / "documentation.pdf").pages)

    def role_id(self, kind, number=None):
        digest = _sha(self.raw["installation_paths"][0])
        return f"{digest}:{kind}" + (f":{number}" if number is not None else "")

    def edit_role(self, role, identifier, mutate):
        path = self.destination / "documentation.pdf"
        reader = PdfReader(path)
        writer = PdfWriter()
        writer.clone_document_from_reader(reader)
        changed = False
        for number, page in enumerate(reader.pages):
            stream = ContentStream(page.get_contents(), reader)
            start = None
            for position, (values, operator) in enumerate(stream.operations):
                if operator == b"BDC" and str(values[0]) == "/FLC" and \
                        (str(values[1]["/Kind"])[1:], str(values[1]["/Id"])) == (role, identifier):
                    start = position
                elif operator == b"EMC" and start is not None:
                    stream.operations = (stream.operations[:start + 1] +
                        mutate(copy.deepcopy(stream.operations[start + 1:position])) + stream.operations[position:])
                    writer.pages[number][NameObject("/Contents")] = writer._add_object(stream)
                    changed = True
                    break
            if changed:
                break
        self.assertTrue(changed, f"Required actual painted path missing: {role}/{identifier}")
        with path.open("wb") as output:
            writer.write(output)
        self.rebind_hashes("pdf")

    def replace_printed(self, original, replacement):
        path = self.destination / "documentation.pdf"
        reader = PdfReader(path)
        writer = PdfWriter()
        writer.clone_document_from_reader(reader)
        changed = False
        for number, page in enumerate(reader.pages):
            stream = ContentStream(page.get_contents(), reader)
            for values, operator in stream.operations:
                if operator == b"Tj" and original in str(values[0]):
                    values[0] = TextStringObject(str(values[0]).replace(original, replacement))
                    writer.pages[number][NameObject("/Contents")] = writer._add_object(stream)
                    changed = True
                    break
            if changed:
                break
        self.assertTrue(changed, f"Required printed evidence missing: {original}")
        with path.open("wb") as output:
            writer.write(output)
        self.rebind_hashes("pdf")

    def geometry(self, paths, role, identifier):
        self.assertIn((role, identifier), paths)
        return unary_union([Polygon(points) for points in paths[(role, identifier)]])

    def assert_rectangle(self, geometry, x, y, width, depth):
        expected = Polygon(((x, y), (x + width, y), (x + width, y + depth), (x, y + depth)))
        self.assertLess(geometry.symmetric_difference(expected).area, .05)

    def test_fresh_five_file_draft_retains_original_common_and_request_identity_and_whole_body_sweeps(self):
        before = self.source_bytes()
        index = self.export()
        self.assertEqual({p.name for p in self.destination.iterdir()}, _INVENTORY)
        self.assertEqual(self.source.read_bytes(), before)
        self.assertEqual((self.destination / "source.json").read_bytes(), before)
        self.assertEqual((index["version"], index["status"], index["ok"]), ("0.2", "DRAFT", False))
        identity = {"drawing_revision": "D01", "variant": 1,
                    "component_sha256": {name: _sha(self.raw[name]) for name in _COMPONENTS}}
        self.assertEqual(index["identity"], identity)
        review = self.read("review.json")
        self.assertEqual(review["installation_paths"]["identity"], identity)
        self.assertEqual(review["installation_paths"]["request_set_sha256"], _sha(self.raw["installation_paths"]))
        self.assertEqual(review["installation_paths"]["reports"][0]["request_sha256"], _sha(self.raw["installation_paths"][0]))
        self.assertEqual(review["installation_paths"]["reports"][0]["evidence"]["local_2d_path_status"], "PASS")
        self.assertEqual(index["review_source"]["file_sha256"], hashlib.sha256(before).hexdigest())
        self.assertEqual(index["full_facility_validation"], {"status": "UNKNOWN", "ok": False})
        self.assertEqual(index["engineering_acceptance"], "PENDING")
        self.assertEqual(index["authenticated_approval"], "UNKNOWN")
        self.assertTrue(self.check()["ok"])
        paths = actual_painted_paths((self.destination / "documentation.pdf").read_bytes(), index["projection"])
        self.assert_rectangle(self.geometry(paths, "installation_pose_body", self.role_id("pose", 0)), -1200, 2600, 1000, 800)
        self.assert_rectangle(self.geometry(paths, "installation_sweep_body", self.role_id("segment", 0)), -1200, 2600, 2700, 800)
        self.assert_rectangle(self.geometry(paths, "installation_sweep_body", self.role_id("segment", 1)), 500, 2600, 7000, 800)
        self.assertIn(_sha(self.raw["installation_paths"][0]), "".join(self.pdf_text().split()))

    def test_explicitly_closed_original_room_ring_preserves_source_and_paints_its_same_corners(self):
        raw, _, _, _ = installation_process_fixture()
        points = [[0.0, 0.0], [6000.0, 0.0], [6000.0, 6000.0], [0.0, 6000.0], [0.0, 0.0]]
        raw["layout"].update(geometry_version="0.2", units="mm")
        raw["layout"]["rooms"]["entry"] = {"kind": "orthogonal", "units": "mm", "vertices": points, "area_m2": 36.0}
        actual = parse_layout_result(raw["layout"])
        raw["installation_paths"][0]["source"]["layout_sha256"] = _sha(actual.to_dict())
        rebind(raw)
        self.raw = copy.deepcopy(raw)
        self.write_source()
        original = self.source.read_bytes()
        admitted = self.review_api.load_process_review_with_paths(self.source)
        self.assertEqual(admitted["source_frames"]["layout"]["rooms"]["entry"]["vertices"], points)
        self.assertEqual(admitted["installation_paths"]["reports"][0]["evidence"]["local_2d_path_status"], "PASS")
        index = self.export(raw)
        self.assertEqual((self.destination / "source.json").read_bytes(), original)
        self.assertTrue(self.check()["ok"])
        paths = actual_painted_paths((self.destination / "documentation.pdf").read_bytes(), index["projection"])
        self.assert_rectangle(self.geometry(paths, "room", "entry"), 0, 0, 6000, 6000)

    def test_negative_exterior_staging_and_body_are_visible_and_shifted_actual_contours_fail_after_rehash(self):
        index = self.export()
        paths = actual_painted_paths((self.destination / "documentation.pdf").read_bytes(), index["projection"])
        staging = self.geometry(paths, "installation_staging_floor", self.role_id("staging"))
        self.assert_rectangle(staging, -2000, 1800, 2000, 2400)
        scale, (ox, oy) = index["projection"]["scale_pt_per_mm"], index["projection"]["origin_pt"]
        page = PdfReader(self.destination / "documentation.pdf").pages[-1]
        for key, contours in paths.items():
            if not key[0].startswith("installation_"):
                continue
            for contour in contours:
                for x, y in contour:
                    px, py = x * scale + ox, y * scale + oy
                    self.assertGreaterEqual(px, 49.98, "Negative source coordinates must remain on the page")
                    self.assertLessEqual(px, float(page.mediabox.width) - 49.98)
                    self.assertGreaterEqual(py, 142.98, "Installation paths must not enter the source footer")
                    self.assertLessEqual(py, float(page.mediabox.height) - 126.98)
        self.assertTrue(self.check()["ok"])
        for role, identifier in (("installation_staging_floor", self.role_id("staging")),
                                 ("installation_pose_body", self.role_id("pose", 0))):
            with self.subTest(role=role):
                self.restore()
                self.edit_role(role, identifier, lambda operations:
                    [([FloatObject(v) for v in (1, 0, 0, 1, -20000, 0)], b"cm"), *operations])
                self.assertFalse(self.check()["ok"], "Actual painted evidence cannot be shifted outside the page")

    def test_missing_body_paint_and_forged_narrow_sweep_are_rejected_beyond_outer_hashes(self):
        self.export()
        for mutation in ("unpainted", "narrow"):
            with self.subTest(mutation=mutation):
                self.restore()
                if mutation == "unpainted":
                    change = lambda operations: [(values, op) for values, op in operations
                        if op not in (b"S", b"s", b"B", b"B*", b"b", b"b*", b"f", b"f*")]
                else:
                    def change(operations):
                        for values, operator in operations:
                            if operator in (b"m", b"l") and float(values[0]) > 1000:
                                values[0] = FloatObject(1000)
                        return operations
                self.edit_role("installation_sweep_body", self.role_id("segment", 0), change)
                self.assertFalse(self.check()["ok"])

    def test_source_bound_transport_clearance_is_separate_and_cannot_be_omitted(self):
        raw, building, layout, equipment = installation_process_fixture()
        source = raw["library"]["sources"][0]
        document = json.loads(source["content"])
        value = {"kind": "rectangle", "unit": "mm", "x": -200.0, "y": -100.0,
                 "width": 1400.0, "depth": 1000.0}
        document["installation_path"]["transport_clearance"] = value
        source_content({"sources": [source]}, document)
        request = raw["installation_paths"][0]
        request["transport_clearance"] = {"source_id": source["id"], "source_sha256": source["sha256"],
            "source_pointer": "/installation_path/transport_clearance", "value": value}
        refresh_binding(request, building, layout, equipment, raw["library"])
        rebind(raw)
        index = self.export(raw)
        self.assertTrue(self.check()["ok"])
        paths = actual_painted_paths((self.destination / "documentation.pdf").read_bytes(), index["projection"])
        body = self.geometry(paths, "installation_pose_body", self.role_id("pose", 0))
        clearance = self.geometry(paths, "installation_pose_clearance", self.role_id("pose", 0))
        self.assert_rectangle(body, -1200, 2600, 1000, 800)
        self.assert_rectangle(clearance, -1400, 2500, 1400, 1000)
        self.assert_rectangle(self.geometry(paths, "installation_sweep_clearance", self.role_id("segment", 0)),
                              -1400, 2500, 3100, 1000)
        self.assertGreater(clearance.difference(body).area, 0)
        for role in ("installation_pose_clearance", "installation_sweep_clearance"):
            with self.subTest(role=role):
                self.restore()
                identifier = self.role_id("pose" if "pose" in role else "segment", 0)
                self.edit_role(role, identifier, lambda operations: [(values, op) for values, op in operations
                    if op not in (b"S", b"s", b"B", b"B*", b"b", b"b*", b"f", b"f*")])
                self.assertFalse(self.check()["ok"])

    def test_unsupported_diagonal_and_rotation_keep_literal_poses_without_invented_segment_sweeps(self):
        raw, _, _, _ = installation_process_fixture()
        raw["installation_paths"][0]["poses"][0]["rotated"] = True
        raw["installation_paths"][0]["poses"][1]["y_mm"] = 2700.0
        index = self.export(raw)
        self.assertTrue(self.check()["ok"])
        review = self.read("review.json")
        evidence = review["installation_paths"]["reports"][0]["evidence"]
        self.assertEqual(evidence["segments"][0]["sweeps"], [])
        self.assertEqual(evidence["segments"][1]["sweeps"], [])
        self.assertEqual(evidence["segments"][0]["status"], "UNKNOWN")
        self.assertEqual(evidence["three_dimensional_feasibility"], "UNKNOWN")
        paths = actual_painted_paths((self.destination / "documentation.pdf").read_bytes(), index["projection"])
        self.assert_rectangle(self.geometry(paths, "installation_pose_body", self.role_id("pose", 0)),
                              -1200, 2600, 800, 1000)
        for number in (0, 1):
            self.assertNotIn(("installation_sweep_body", self.role_id("segment", number)), paths)
        # Replacing a literal admitted pose with its installed orientation is a
        # physical forgery even while the overall verdict remains UNKNOWN.
        def installed_footprint(operations):
            replacement = [("m", -1200, 2600), ("l", -200, 2600),
                           ("l", -200, 3400), ("l", -1200, 3400)]
            positions = iter(replacement)
            for values, operator in operations:
                if operator in (b"m", b"l"):
                    _, x, y = next(positions)
                    values[:] = [FloatObject(x), FloatObject(y)]
            return operations
        self.edit_role("installation_pose_body", self.role_id("pose", 0), installed_footprint)
        self.assertFalse(self.check()["ok"])

    def test_known_body_collision_retains_fail_with_missing_staging_and_cannot_be_printed_as_pass(self):
        raw, _, _, _ = installation_process_fixture(neighbor_y=2500.0)
        raw["installation_paths"][0]["exterior_staging"] = None
        index = self.export(raw)
        review = self.read("review.json")
        evidence = review["installation_paths"]["reports"][0]["evidence"]
        self.assertEqual((index["review_status"], review["status"], evidence["status"]), ("FAIL", "FAIL", "FAIL"))
        self.assertIn("missing_exterior_staging", {row["code"] for row in evidence["issues"]})
        witness = next(row for row in evidence["issues"] if row["code"] == "swept_body_collision" and row["role"] == "body")
        self.assertEqual(witness["related_equipment_id"], "neighbor")
        self.assertEqual(witness["overlap_area_mm2"], 700000)
        self.assertTrue(self.check()["ok"], "Artifact consistency does not approve a known installation failure")
        text = self.pdf_text()
        self.assertIn("swept_body_collision", "".join(text.split()))
        self.assertIn("neighbor", text)
        self.assertIn("PENDING", text)
        self.replace_printed("Review status: FAIL", "Review status: PASS")
        self.assertFalse(self.check()["ok"])

    def test_original_source_change_or_forged_printed_source_sha_fails_even_after_rebinding_saved_metadata(self):
        self.export()
        original_sha = hashlib.sha256(self.source.read_bytes()).hexdigest()
        self.replace_printed(original_sha, "0" * 64)
        self.assertFalse(self.check()["ok"])
        for mutation in ("bytes", "revision", "pose"):
            with self.subTest(mutation=mutation):
                self.restore()
                raw = copy.deepcopy(self.raw)
                if mutation == "revision":
                    raw["drawing_revision"] = "D02"
                elif mutation == "pose":
                    raw["installation_paths"][0]["poses"][0]["x_mm"] = -1300.0
                changed = self.source_bytes(raw) + (b" " if mutation == "bytes" else b"")
                self.source.write_bytes(changed)
                (self.destination / "source.json").write_bytes(changed)
                fresh = self.review_api.load_process_review_with_paths(self.source)
                self.save("review.json", fresh)
                index = self.read("index.json")
                index.update(identity=fresh["identity"], review_source=fresh["review_source"], review_status=fresh["status"])
                self.save("index.json", index)
                self.rebind_hashes("source", "review")
                self.assertFalse(self.check()["ok"], "A stale PDF cannot be authorized by rebound JSON metadata")

    def test_default_version_one_contract_and_limits_are_preserved_and_explicit_api_rejects_old_source(self):
        self.write_source()
        with self.assertRaises(ValueError):
            self.legacy_api.export_process_documentation(self.source, self.root / "legacy-rejected")
        old = copy.deepcopy(self.raw)
        old.update(version="0.1")
        old.pop("installation_paths")
        self.source.write_bytes(self.source_bytes(old))
        old_target = self.root / "legacy-documents"
        self.legacy_api.export_process_documentation(self.source, old_target)
        index = json.loads((old_target / "index.json").read_bytes())
        self.assertEqual(index["version"], "0.1")
        self.assertEqual(index["limits"], asdict(ProcessDocumentationLimits()))
        self.assertNotIn("path_limits", index)
        self.assertNotIn("max_paths", index["limits"])
        self.assertNotIn("max_total_poses", index["limits"])
        self.assertTrue(self.legacy_api.validate_process_documentation(self.source, old_target)["ok"])
        with self.assertRaises(ValueError):
            self.api.export_process_documentation_with_paths(self.source, self.destination)
        self.assertFalse(self.destination.exists())

    def test_bounds_changed_source_and_raced_destination_preserve_source_and_existing_user_work(self):
        self.write_source()
        before = self.source.read_bytes()
        for name, value in (("max_pages", 1), ("max_pdf_bytes", 100), ("max_overlay_paths", 1)):
            with self.subTest(bound=name), self.assertRaises(ValueError):
                self.api.export_process_documentation_with_paths(self.source, self.root / name,
                    limits=replace(ProcessDocumentationLimits(), **{name: value}))
            self.assertFalse((self.root / name).exists())
            self.assertEqual(self.source.read_bytes(), before)
        with self.assertRaises(ValueError):
            self.api.export_process_documentation_with_paths(self.source, self.destination,
                path_limits=replace(self.review_api.ProcessInstallationPathLimits(), max_total_poses=2))
        self.assertFalse(self.destination.exists())
        existing = self.root / "existing"
        existing.mkdir()
        (existing / "keep.txt").write_bytes(b"Existing user work")
        with self.assertRaises((FileExistsError, ValueError)):
            self.api.export_process_documentation_with_paths(self.source, existing)
        self.assertEqual((existing / "keep.txt").read_bytes(), b"Existing user work")
        with self.assertRaises((FileExistsError, ValueError)):
            self.api.export_process_documentation_with_paths(self.source, self.source)
        from reportlab.pdfgen.canvas import Canvas
        original_save = Canvas.save

        def change_source(document):
            original_save(document)
            self.source.write_bytes(before + b" ")

        with patch.object(Canvas, "save", change_source), self.assertRaises(ValueError):
            self.api.export_process_documentation_with_paths(self.source, self.destination)
        self.assertFalse(self.destination.exists())
        self.write_source()
        original_publish = self.api._publish_directory

        def race(stage, target):
            Path(target).mkdir()
            (Path(target) / "keep.txt").write_bytes(b"Raced user work")
            return original_publish(stage, target)

        with patch.object(self.api, "_publish_directory", race), self.assertRaises((FileExistsError, ValueError)):
            self.api.export_process_documentation_with_paths(self.source, self.destination)
        self.assertEqual((self.destination / "keep.txt").read_bytes(), b"Raced user work")
        self.assertEqual({p.name for p in self.destination.iterdir()}, {"keep.txt"})
        self.assertEqual(self.source.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()

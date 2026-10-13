"""Reference axes require explicit source-bound three-format delivery QA."""

import copy
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

import ezdxf
from pypdf import PdfReader, PdfWriter
from pypdf.generic import ContentStream, FloatObject, NameObject

from layout_configurator.building import StructuralGridSpec
from layout_configurator.facility import FacilityProfile
from layout_configurator.models import WindowSpec
from layout_configurator.orthogonal_bundle import INVENTORY, write_orthogonal_bundle
from layout_configurator.orthogonal_bundle_qa import validate_orthogonal_bundle
from layout_configurator.orthogonal_grid import validate_orthogonal_grid
from test_orthogonal_bundle import _copy_pristine_bundle, _restore_bundle_files
from test_orthogonal_pdf import _fixture


class OrthogonalGridBundleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cache = {}

    @classmethod
    def tearDownClass(cls):
        cls.cache.clear()

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.path = self.root / "bundle"
        self.building, self.layout, self.equipment, self.routing = _fixture()
        self.building = replace(self.building, structural_grid=StructuralGridSpec(
            (0, 2050.5, 6000), (0, 3100.25, 6000), (" A ", "B", "C"), ("1", "2", "3")))
        self.profile = FacilityProfile.from_mapping({"name": "Source reference grid project", "version": "P01",
            "provenance": {"source": "Repository-owned project brief"}, "drawing": {"revision": "P01"},
            "rules": [{"id": "declared_people", "kind": "required_flow_types", "title": "People flow",
                "source": "Project brief P01", "edition": "P01", "effective_date": "2026-10-13",
                "evidence": ["Declared project requirement"], "parameters": {"flow_types": ["people"]}}]})

    def write(self, **options):
        return _copy_pristine_bundle(self.cache, self.path, write_orthogonal_bundle,
            self.building, self.layout, self.equipment, routing=self.routing, profile=self.profile, **options)

    def check(self, **options):
        return validate_orthogonal_bundle(self.path, expected_profile=self.profile, **options)

    def read(self, name):
        return json.loads((self.path / name).read_text(encoding="utf-8"))

    def save(self, name, value):
        (self.path / name).write_text(json.dumps(value, sort_keys=True, separators=(",", ":")), encoding="utf-8")

    def snapshot(self):
        return tuple((path.name, path.read_bytes()) for path in sorted(self.path.iterdir()))

    def restore(self, snapshot):
        _restore_bundle_files(self.path, snapshot)

    def rehash(self, kind):
        manifest = self.read("manifest.json")
        record = manifest["artifacts"][kind]
        digest = hashlib.sha256((self.path / record["file"]).read_bytes()).hexdigest()
        record["sha256"] = digest
        index = self.read(record["index"])
        index["artifact_sha256" if kind != "pdf" else "sha256"] = digest
        self.save(record["index"], index)
        record["index_sha256"] = hashlib.sha256((self.path / record["index"]).read_bytes()).hexdigest()
        self.save("manifest.json", manifest)

    def assert_failed(self, report, identifier):
        self.assertEqual(report["status"], "FAIL", report)
        self.assertFalse(report["ok"])
        self.assertEqual(next(row for row in report["checks"] if row["id"] == identifier)["status"], "FAIL", report)
        self.assertEqual(report["full_facility_validation"], {"status": "UNKNOWN", "ok": False})
        self.assertEqual(report["engineering_acceptance"], "PENDING")

    def test_original_typed_grid_source_and_actual_reference_axes_survive_all_formats(self):
        before = copy.deepcopy(self.building.to_dict())
        manifest = self.write(include_grid=True)
        capability = validate_orthogonal_grid(self.building.layout, self.building.structural_grid).to_dict()
        self.assertEqual(manifest["grid_capability"], capability)
        self.assertEqual({path.name for path in self.path.iterdir()}, INVENTORY)
        self.assertEqual(self.read("building.json"), before)
        self.assertEqual(self.building.to_dict(), before)
        self.assertEqual(type(self.read("building.json")["structural_grid"]["axes_x_mm"][0]), int)
        self.assertNotIn("structural_grid", manifest["projection_scope"]["unsupported_components"])
        self.assertIn("source_reference_axes", manifest["projection_scope"]["geometry_components"])
        self.assertFalse(capability["policy"]["room_alignment_enforced"])
        report = self.check(include_grid=True)
        self.assertEqual(report["status"], "PASS", report)
        self.assertEqual(report["grid_capability"], capability)
        for kind in ("dxf", "pdf", "ifc"):
            with self.subTest(kind=kind):
                self.assertEqual(self.read(kind + "-index.json")["grid_capability"], capability)
                self.assertEqual(report["readbacks"][kind]["grid_capability"], capability)
        self.assertEqual(report["readbacks"]["ifc"]["counts"]["grid_axes"], 6)
        self.assertEqual(report["readbacks"]["dxf"]["counts"]["grid_axes"], 6)
        self.assertEqual(report["readbacks"]["ifc"]["grid_axes"][1]["world_points_mm"],
                         [[2050.5, 0., 0.], [2050.5, 6000., 0.]])
        self.assertEqual(report["readbacks"]["ifc_express"]["statements"], [])
        self.assertEqual(manifest["full_facility_validation"], {"status": "UNKNOWN", "ok": False})
        self.assertEqual(manifest["engineering_acceptance"], "PENDING")

    def test_omitted_permission_cannot_be_granted_by_source_or_manifest(self):
        with self.assertRaisesRegex(ValueError, "structural_grid"):
            write_orthogonal_bundle(self.path, self.building, self.layout, self.equipment,
                routing=self.routing, profile=self.profile)
        self.assertFalse(self.path.exists())
        self.write(include_grid=True)
        self.assert_failed(self.check(), "SOURCE_CONTRACT")

    def test_invalid_flags_and_original_grid_values_reject_before_staging(self):
        for flag in (0, 1, "true", None, [], {}):
            with self.subTest(flag=flag):
                with self.assertRaisesRegex(ValueError, "include_grid"):
                    write_orthogonal_bundle(self.path, self.building, self.layout, self.equipment,
                        routing=self.routing, profile=self.profile, include_grid=flag)
                self.assertFalse(self.path.exists())
                self.assertEqual(self.check(include_grid=flag)["status"], "FAIL")
        for grid in (StructuralGridSpec((True,), (1000,)), StructuralGridSpec(("1000",), (1000,)),
                     StructuralGridSpec((10 ** 400,), (1000,)), StructuralGridSpec((float("nan"),), (1000,)),
                     StructuralGridSpec((7000,), (1000,)), StructuralGridSpec((1000, 1000), (1000,)),
                     StructuralGridSpec((1000,), (1000,), (1,))):
            with self.subTest(grid=repr(grid)):
                with self.assertRaises(ValueError):
                    write_orthogonal_bundle(self.path, replace(self.building, structural_grid=grid), self.layout,
                        self.equipment, routing=self.routing, profile=self.profile, include_grid=True)
                self.assertFalse(self.path.exists())
                self.assertFalse(list(self.root.glob(".orthogonal-draft-*")))

    def test_rehashed_capability_or_raw_source_cannot_replace_fresh_current_grid(self):
        self.write(include_grid=True)
        snapshot = self.snapshot()
        for branch in ("manifest", "dxf-index", "pdf-index", "ifc-index", "ifc-local", "source"):
            with self.subTest(branch=branch):
                self.restore(snapshot)
                expected = "ARTIFACT_INDEX"
                if branch == "manifest":
                    data = self.read("manifest.json")
                    data["grid_capability"]["policy"]["room_alignment_enforced"] = True
                    self.save("manifest.json", data)
                    expected = "SOURCE_CONTRACT"
                elif branch == "source":
                    data = self.read("building.json")
                    data["structural_grid"]["axes_x_mm"][1] = "2050.5"
                    self.save("building.json", data)
                    manifest = self.read("manifest.json")
                    manifest["sources"]["building"]["sha256"] = hashlib.sha256((self.path / "building.json").read_bytes()).hexdigest()
                    manifest["sources"]["building"]["canonical_sha256"] = hashlib.sha256(json.dumps(data,
                        sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()
                    self.save("manifest.json", manifest)
                    expected = "SOURCE_CONTRACT"
                else:
                    kind = branch.split("-")[0]
                    data = self.read(kind + "-index.json")
                    target = data["local_validation"]["grid_capability"] if branch == "ifc-local" else data["grid_capability"]
                    target["axes"][1]["coordinate_mm"] += 100
                    self.save(kind + "-index.json", data)
                    self.rehash(kind)
                    if kind == "pdf":
                        expected = "PDF_READBACK"
                self.assert_failed(self.check(include_grid=True), expected)

    def test_rehashed_actual_dxf_axis_or_visible_identity_changes_fail(self):
        self.write(include_grid=True)
        snapshot = self.snapshot()
        for branch in ("moved", "removed", "coordinate", "label", "label_position", "hidden"):
            with self.subTest(branch=branch):
                self.restore(snapshot)
                document = ezdxf.readfile(self.path / "geometry.dxf")
                model = document.modelspace()
                axis = list(model.query('LINE[layer=="A-GRID"]'))[1]
                if branch == "moved":
                    axis.dxf.start = (axis.dxf.start.x + 100, axis.dxf.start.y, 0)
                elif branch == "removed":
                    model.delete_entity(axis)
                elif branch == "coordinate":
                    tags = list(axis.get_xdata("LAYOUT_IR"))
                    tags[-1] = (1040, 2100.5)
                    axis.set_xdata("LAYOUT_IR", tags)
                elif branch == "hidden":
                    document.layers.get("A-GRID").off()
                else:
                    label = list(model.query('TEXT[layer=="A-GRID-TEXT"]'))[1]
                    if branch == "label":
                        label.dxf.text = label.dxf.text.replace("2050.5", "2100.5")
                    else:
                        label.dxf.insert = (100000, 100000, 0)
                document.saveas(self.path / "geometry.dxf")
                self.rehash("dxf")
                self.assert_failed(self.check(include_grid=True), "DXF_READBACK")

    def test_rehashed_actual_pdf_grid_path_changes_fail(self):
        self.write(include_grid=True)
        snapshot = self.snapshot()
        for branch in ("moved", "removed"):
            with self.subTest(branch=branch):
                self.restore(snapshot)
                reader = PdfReader(self.path / "geometry.pdf")
                stream = ContentStream(reader.pages[0].get_contents(), reader)
                start = next(i for i, (values, op) in enumerate(stream.operations)
                    if op == b"BDC" and str(values[1].get("/Kind")) == "/grid")
                end = next(i for i in range(start + 1, len(stream.operations)) if stream.operations[i][1] == b"EMC")
                if branch == "moved":
                    coordinate = next(values for values, op in stream.operations[start + 1:end] if op == b"m")
                    coordinate[0] = FloatObject(float(coordinate[0]) + 100)
                else:
                    del stream.operations[start:end + 1]
                writer = PdfWriter()
                writer.clone_document_from_reader(reader)
                writer.pages[0][NameObject("/Contents")] = writer._add_object(stream)
                with (self.path / "geometry.pdf").open("wb") as target:
                    writer.write(target)
                self.rehash("pdf")
                self.assert_failed(self.check(include_grid=True), "PDF_READBACK")

    def test_rehashed_actual_ifc_grid_curve_and_native_identity_changes_fail(self):
        import ifcopenshell
        self.write(include_grid=True)
        snapshot = self.snapshot()
        for branch in ("moved", "removed", "label", "guid"):
            with self.subTest(branch=branch):
                self.restore(snapshot)
                model = ifcopenshell.open(str(self.path / "geometry.ifc"))
                grid = model.by_type("IfcGrid")[0]
                if branch == "moved":
                    point = grid.UAxes[1].AxisCurve.Points[0]
                    point.Coordinates = (point.Coordinates[0] + 100, point.Coordinates[1])
                elif branch == "removed":
                    grid.UAxes = grid.UAxes[:1] + grid.UAxes[2:]
                elif branch == "label":
                    grid.UAxes[1].AxisTag = "False label"
                else:
                    grid.GlobalId = ifcopenshell.guid.new()
                model.write(str(self.path / "geometry.ifc"))
                self.rehash("ifc")
                self.assert_failed(self.check(include_grid=True), "IFC_READBACK")

    def test_grid_capability_does_not_implicitly_enable_windows(self):
        self.building = replace(self.building, layout=replace(self.building.layout,
            windows=(WindowSpec("source-window", "main", "bottom", 1000., 800.),)))
        with self.assertRaisesRegex(ValueError, "windows"):
            write_orthogonal_bundle(self.path, self.building, self.layout, self.equipment,
                routing=self.routing, profile=self.profile, include_grid=True)
        self.assertFalse(self.path.exists())
        manifest = self.write(include_windows=True, include_grid=True)
        report = self.check(include_windows=True, include_grid=True)
        self.assertEqual(report["status"], "PASS", report)
        self.assertEqual(report["grid_capability"], manifest["grid_capability"])
        self.assertEqual(report["window_capability"], manifest["window_capability"])
        self.assertEqual(report["readbacks"]["ifc"]["counts"]["windows"], 1)
        self.assertEqual(report["readbacks"]["ifc"]["counts"]["grid_axes"], 6)

    def test_opted_missing_grid_keeps_empty_capability_without_inventing_native_axes(self):
        self.building = replace(self.building, structural_grid=None)
        manifest = self.write(include_grid=True)
        self.assertEqual(manifest["grid_capability"]["axes"], [])
        report = self.check(include_grid=True)
        self.assertEqual(report["status"], "PASS", report)
        self.assertEqual(report["readbacks"]["dxf"]["counts"]["grid_axes"], 0)
        self.assertEqual(report["readbacks"]["ifc"]["counts"]["grids"], 0)
        self.assertEqual(report["readbacks"]["ifc"]["counts"]["grid_axes"], 0)
        self.assertEqual(report["readbacks"]["ifc"]["grid_axes"], [])
        self.assertEqual(report["readbacks"]["pdf"]["actual"]["grid_axes_mm"], {})
        self.assertEqual(report["readbacks"]["ifc_express"]["statements"], [])
        self.assert_failed(self.check(), "SOURCE_CONTRACT")

"""Explicit source windows must survive a bounded persisted three-format draft."""

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

from layout_configurator.facility import FacilityProfile
from layout_configurator.models import WindowSpec
from layout_configurator.orthogonal_bundle import INVENTORY, write_orthogonal_bundle
from layout_configurator.orthogonal_bundle_qa import validate_orthogonal_bundle
from layout_configurator.orthogonal_window_capability import validate_orthogonal_window_capability
from test_orthogonal_bundle import _copy_pristine_bundle, _restore_bundle_files
from test_orthogonal_pdf import _fixture


class OrthogonalWindowBundleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._pristine_bundles = {}

    @classmethod
    def tearDownClass(cls):
        cls._pristine_bundles.clear()

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.path = self.root / "bundle"
        self.building, self.layout, self.equipment, self.routing = _fixture()
        spec = self.building.layout
        self.building = replace(self.building, layout=replace(spec,
            windows=(WindowSpec("source-window", "main", "bottom", 1000.0, 800.0),),
            rooms=(spec.rooms[0], replace(spec.rooms[1], needs_daylight=True)),
            window_sill_mm=800.0, window_height_mm=1400.0))
        self.profile = FacilityProfile.from_mapping({"name": "Source window project policy", "version": "P01",
            "provenance": {"source": "Repository-owned project brief", "edition": "P01"},
            "drawing": {"revision": "P01"}, "rules": [{"id": "declared_people", "kind": "required_flow_types",
                "title": "Declared people flow", "source": "Project brief P01", "edition": "P01",
                "effective_date": "2026-10-13", "evidence": ["Explicit project requirement"],
                "parameters": {"flow_types": ["people"]}}]})

    def write(self, **kwargs):
        if set(kwargs) == {"include_windows"} and kwargs["include_windows"] is True:
            return _copy_pristine_bundle(self._pristine_bundles, self.path, write_orthogonal_bundle,
                self.building, self.layout, self.equipment, routing=self.routing, profile=self.profile, **kwargs)
        return write_orthogonal_bundle(self.path, self.building, self.layout, self.equipment,
            routing=self.routing, profile=self.profile, **kwargs)

    def check(self, **kwargs):
        return validate_orthogonal_bundle(self.path, expected_profile=self.profile, **kwargs)

    def read(self, filename):
        return json.loads((self.path / filename).read_text(encoding="utf-8"))

    def save(self, filename, value):
        (self.path / filename).write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")

    def rehash(self, kind):
        manifest = self.read("manifest.json")
        record = manifest["artifacts"][kind]
        record["sha256"] = hashlib.sha256((self.path / record["file"]).read_bytes()).hexdigest()
        index = self.read(record["index"])
        index["sha256" if kind == "pdf" else "artifact_sha256"] = record["sha256"]
        self.save(record["index"], index)
        record["index_sha256"] = hashlib.sha256((self.path / record["index"]).read_bytes()).hexdigest()
        self.save("manifest.json", manifest)

    def snapshot(self):
        return {path.name: path.read_bytes() for path in self.path.iterdir()}

    def restore(self, snapshot):
        _restore_bundle_files(self.path, snapshot.items())

    def assert_failed(self, report, identifier=None):
        self.assertEqual(report["status"], "FAIL", report)
        self.assertIs(report["ok"], False)
        self.assertEqual(report["full_facility_validation"], {"status": "UNKNOWN", "ok": False})
        self.assertEqual(report["engineering_acceptance"], "PENDING")
        if identifier:
            self.assertEqual(next(row for row in report["checks"] if row["id"] == identifier)["status"], "FAIL", report)

    def test_explicit_and_automatic_windows_preserve_sources_and_actual_all_format_readbacks(self):
        before = copy.deepcopy(self.building.to_dict())
        capability = validate_orthogonal_window_capability(self.building.layout, self.layout).to_dict()
        self.assertEqual(capability["status"], "PASS")
        self.assertEqual({item["id"] for item in capability["facade"]["windows"]}, {"source-window", "auto-corner"})
        manifest = self.write(include_windows=True)
        self.assertEqual({item.name for item in self.path.iterdir()}, INVENTORY)
        self.assertEqual(manifest["window_capability"], capability)
        self.assertEqual(self.read("building.json"), before)
        self.assertEqual(self.building.to_dict(), before)
        self.assertEqual(manifest["full_facility_validation"], {"status": "UNKNOWN", "ok": False})
        self.assertEqual(manifest["engineering_acceptance"], "PENDING")
        self.assertEqual(manifest["projection_scope"]["unsupported_components"], ["structural_grid"])
        self.assertIn("windows/window-openings=source sill+height", manifest["projection_scope"]["height_policy"])
        report = self.check(include_windows=True)
        self.assertEqual(report["status"], "PASS", report)
        self.assertEqual(report["window_capability"], capability)
        for key in ("pdf", "ifc"):
            self.assertEqual(self.read(key + "-index.json")["window_capability"], capability)
            self.assertEqual(report["readbacks"][key]["window_capability"], capability)
        self.assertEqual(self.read("dxf-index.json")["window_capability"], capability)
        self.assertEqual(report["readbacks"]["dxf"]["window_capability"], capability)
        self.assertEqual(report["readbacks"]["ifc"]["counts"]["windows"], 2)
        self.assertEqual(report["readbacks"]["dxf"]["counts"]["windows"], 2)
        self.assertEqual(report["readbacks"]["ifc_express"]["statements"], [])
        self.assertEqual(self.read("facility-predicates.json")["local_validation"]["window_capability"], capability)

    def test_default_writer_and_reader_require_explicit_window_opt_in(self):
        with self.assertRaises(ValueError):
            self.write()
        self.assertFalse(self.path.exists())
        self.write(include_windows=True)
        self.assert_failed(self.check(), "SOURCE_CONTRACT")
        self.assertEqual(self.check(include_windows=True)["status"], "PASS")

    def test_window_flag_rejects_coercions_before_any_output(self):
        for value in (1, 0, "true", None, [], {}):
            with self.subTest(value=value):
                with self.assertRaisesRegex(ValueError, "include_windows"):
                    self.write(include_windows=value)
                self.assertFalse(self.path.exists())
                self.assert_failed(self.check(include_windows=value))

    def test_actual_dxf_window_lines_and_visible_source_inventory_read_back_independently(self):
        from layout_configurator.orthogonal_bundle import OrthogonalBundleLimits, _dxf
        from layout_configurator.orthogonal_bundle_qa import _dxf_readback, _identity
        sources = {"building": self.building.to_dict(), "layout": self.layout.to_dict(),
            "equipment": self.equipment.to_dict(self.building), "routing": self.routing.to_dict(),
            "profile": self.profile.to_dict()}
        identity = _identity(self.building, self.layout, self.profile, sources)
        path = self.root / "windows.dxf"
        _dxf(path, self.building, self.layout, self.equipment, self.routing, identity, include_windows=True)
        original = path.read_bytes()
        def check():
            return _dxf_readback(path.read_bytes(), self.building, self.layout, self.equipment,
                self.routing, identity, OrthogonalBundleLimits(), include_windows=True)
        self.assertEqual(check()["status"], "PASS", check())
        for branch in ("sill", "position", "height", "rotation", "removed"):
            with self.subTest(branch=branch):
                path.write_bytes(original)
                document = ezdxf.readfile(path)
                label = list(document.modelspace().query('TEXT[layer=="A-WINDOW-TEXT"]'))[0]
                if branch == "sill":
                    label.dxf.text = label.dxf.text.replace("sill 800 mm", "sill 900 mm")
                elif branch == "position":
                    label.dxf.insert = (100000, 100000, 0)
                elif branch == "height":
                    label.dxf.height = 1
                elif branch == "rotation":
                    label.dxf.rotation = 90
                else:
                    document.modelspace().delete_entity(label)
                document.saveas(path)
                self.assertEqual(check()["status"], "FAIL", check())

    def test_unknown_vertical_or_invalid_facade_capability_leaves_no_directory(self):
        for branch in ("head", "interior", "duplicate"):
            with self.subTest(branch=branch):
                original = self.building
                spec = original.layout
                if branch == "head":
                    spec = replace(spec, window_sill_mm=1800.0, window_height_mm=1400.0)
                elif branch == "interior":
                    spec = replace(spec, windows=(replace(spec.windows[0], room_id="corner", side="left"),))
                else:
                    spec = replace(spec, windows=(*spec.windows, replace(spec.windows[0], id="duplicate")))
                self.building = replace(original, layout=spec)
                with self.assertRaisesRegex(ValueError, "window capability is (FAIL|UNKNOWN)"):
                    self.write(include_windows=True)
                self.assertFalse(self.path.exists())
                self.assertFalse(list(self.root.glob(".orthogonal-draft-*")))
                self.building = original

    def test_explicit_capability_without_declared_windows_has_zero_native_inventory(self):
        spec = self.building.layout
        self.building = replace(self.building, layout=replace(spec, windows=(),
            rooms=tuple(replace(room, needs_daylight=False) for room in spec.rooms)))
        manifest = self.write(include_windows=True)
        self.assertEqual(manifest["window_capability"]["facade"]["windows"], [])
        report = self.check(include_windows=True)
        self.assertEqual(report["status"], "PASS", report)
        self.assertEqual(report["readbacks"]["dxf"]["counts"]["windows"], 0)
        self.assertEqual(report["readbacks"]["ifc"]["counts"]["windows"], 0)
        self.assert_failed(self.check(), "SOURCE_CONTRACT")

    def test_manifest_capability_cannot_replace_fresh_source_or_opt_the_default_reader_in(self):
        self.write(include_windows=True)
        original = self.snapshot()
        for branch in ("absent", "policy", "sill", "facade", "source", "status", "malformed"):
            with self.subTest(branch=branch):
                self.restore(original)
                manifest = self.read("manifest.json")
                capability = manifest["window_capability"]
                if branch == "absent":
                    del manifest["window_capability"]
                elif branch == "policy":
                    capability["policy"]["name"] = "invented_policy"
                elif branch == "sill":
                    capability["vertical_range_mm"]["sill"] += 100
                elif branch == "facade":
                    capability["facade"]["windows"][0]["start"] += 100
                elif branch == "source":
                    capability["source"]["layout_sha256"] = "0" * 64
                elif branch == "status":
                    capability["full_facility_validation"] = {"status": "PASS", "ok": True}
                else:
                    manifest["window_capability"] = []
                self.save("manifest.json", manifest)
                self.assert_failed(self.check(include_windows=True), "SOURCE_CONTRACT")
                self.assert_failed(self.check(), "SOURCE_CONTRACT")

    def test_rehashed_window_index_capability_still_requires_actual_current_source(self):
        self.write(include_windows=True)
        original = self.snapshot()
        for kind in ("dxf", "pdf", "ifc"):
            with self.subTest(kind=kind):
                self.restore(original)
                index = self.read(kind + "-index.json")
                index["window_capability"]["vertical_range_mm"]["height"] += 100
                self.save(kind + "-index.json", index)
                self.rehash(kind)
                report = self.check(include_windows=True)
                self.assert_failed(report)
                if kind != "pdf":
                    self.assert_failed(report, "ARTIFACT_INDEX")
                else:
                    self.assert_failed(report, "PDF_READBACK")

    def test_rehashed_dxf_window_line_or_sill_metadata_cannot_claim_source_projection(self):
        self.write(include_windows=True)
        original = self.snapshot()
        for branch in ("displaced", "dropped", "width", "sill", "height", "host"):
            with self.subTest(branch=branch):
                self.restore(original)
                document = ezdxf.readfile(self.path / "geometry.dxf")
                entity = list(document.modelspace().query('LINE[layer=="A-WINDOW"]'))[0]
                if branch == "displaced":
                    entity.dxf.start = (entity.dxf.start.x + 100, entity.dxf.start.y + 100, 0)
                elif branch == "dropped":
                    document.modelspace().delete_entity(entity)
                else:
                    data = list(entity.get_xdata("LAYOUT_IR"))
                    if branch == "host":
                        data = [(tag.code, "room_id=forged" if tag.code == 1000 and tag.value.startswith("room_id=") else tag.value)
                                for tag in data]
                    else:
                        index = [i for i, tag in enumerate(data) if tag.code == 1040][("width", "sill", "height").index(branch)]
                        data[index] = (1040, data[index].value + 100)
                    entity.set_xdata("LAYOUT_IR", data)
                document.saveas(self.path / "geometry.dxf")
                self.rehash("dxf")
                self.assert_failed(self.check(include_windows=True), "DXF_READBACK")

    def test_rehashed_pdf_displaced_window_path_cannot_claim_source_projection(self):
        self.write(include_windows=True)
        reader = PdfReader(self.path / "geometry.pdf")
        stream = ContentStream(reader.pages[0].get_contents(), reader)
        start = next(i for i, (values, op) in enumerate(stream.operations)
            if op == b"BDC" and str(values[1].get("/Kind")) == "/window")
        coordinate = next(values for values, op in stream.operations[start + 1:] if op == b"m")
        coordinate[0] = FloatObject(float(coordinate[0]) + 100)
        writer = PdfWriter()
        writer.clone_document_from_reader(reader)
        writer.pages[0][NameObject("/Contents")] = writer._add_object(stream)
        with (self.path / "geometry.pdf").open("wb") as target:
            writer.write(target)
        self.rehash("pdf")
        self.assert_failed(self.check(include_windows=True), "PDF_READBACK")

    def test_rehashed_ifc_window_actual_sill_and_height_cannot_claim_source_projection(self):
        import ifcopenshell
        self.write(include_windows=True)
        original = self.snapshot()
        for branch in ("sill", "height", "width", "dropped"):
            with self.subTest(branch=branch):
                self.restore(original)
                model = ifcopenshell.open(str(self.path / "geometry.ifc"))
                window = model.by_type("IfcWindow")[0]
                if branch == "sill":
                    point = window.ObjectPlacement.RelativePlacement.Location
                    point.Coordinates = (point.Coordinates[0], point.Coordinates[1], point.Coordinates[2] + 100)
                elif branch == "height":
                    window.Representation.Representations[0].Items[0].Depth -= 100
                elif branch == "width":
                    window.OverallWidth += 100
                else:
                    model.remove(window)
                model.write(str(self.path / "geometry.ifc"))
                self.rehash("ifc")
                self.assert_failed(self.check(include_windows=True), "IFC_READBACK")


if __name__ == "__main__":
    unittest.main()

"""Unchanged facility artifacts and process evidence share exact source frames.

All geometry and routes are literal supplied results. The future root test run
may export those small results, but no fixture calls a generation/CP producer.
Only immutable file bytes are cached; actual QA always reruns independently.
"""

import copy
from dataclasses import replace
import hashlib
import importlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import ezdxf

from layout_configurator.building import BuildingIR
from layout_configurator.bundle import write_facility_variant
from layout_configurator.equipment import EquipmentLayoutResult, EquipmentPlacement, validate_equipment_layout
from layout_configurator.facility import default_facility_profile, validate_building
from layout_configurator.flows import FlowRoute, FlowRoutingResult, validate_flow_routes
from layout_configurator.models import Rect
from layout_configurator.orthogonal_bundle import write_orthogonal_bundle
from layout_configurator.orthogonal_bundle_qa import validate_orthogonal_bundle
from layout_configurator.qa import validate_building_bundle
from layout_configurator.result_geometry import parse_layout_result
from layout_configurator.validation import validate_layout
from test_process_review import _COMPONENTS, rebind
from test_transfer_program import _sha


def package_fixture(*, cycle=False):
    """Three supplied rooms, three explicit doors and one harmless body."""
    source = Path(__file__).resolve().parents[1] / "examples" / "process_review_input.json"
    raw = json.loads(source.read_bytes())
    raw["building"]["equipment"] = [{"id": "fixture_press", "type": "project_fixture_press",
        "room_id": "source", "width_mm": 500.0, "depth_mm": 500.0,
        "clearance_front_mm": 0.0, "clearance_back_mm": 0.0,
        "clearance_left_mm": 0.0, "clearance_right_mm": 0.0, "rotation_allowed": True}]
    building = BuildingIR.from_mapping(raw["building"])
    raw["building"] = building.to_dict()
    equipment = EquipmentLayoutResult((EquipmentPlacement("fixture_press", "source",
        Rect(400.0, 4200.0, 500.0, 500.0)),))
    raw["equipment"] = equipment.to_dict(building)["equipment"]
    if cycle:
        raw["environment_program"]["pressure_edges"][-1].update(
            high_node_id="pressure_upper", low_node_id="pressure_source")
        raw["production_program"]["sources"][0]["usage_rights"]["state"] = "UNKNOWN"
    rebind(raw)
    return raw


def canonical_models(raw):
    building = BuildingIR.from_mapping(raw["building"])
    layout = parse_layout_result(raw["layout"])
    equipment = EquipmentLayoutResult(tuple(EquipmentPlacement(row["equipment_id"], row["room_id"],
        Rect(row["x"], row["y"], row["width"], row["depth"]), row["rotated"]) for row in raw["equipment"]))
    routes = []
    for row in raw["flow_routes"]["routes"]:
        routes.append(FlowRoute(row["flow_id"], row["from_id"], row["to_id"], row["from_room_id"], row["to_room_id"],
            tuple(row["room_path"]), tuple((point["x"], point["y"]) for point in row["points"]), row["minimum_clear_width_mm"]))
    routing = FlowRoutingResult(tuple(routes))
    profile = default_facility_profile()
    profile = replace(profile, drawing=replace(profile.drawing, revision=raw["drawing_revision"]))
    return building, layout, equipment, routing, profile


def tree_bytes(root):
    return tuple((path.relative_to(root).as_posix(), path.read_bytes())
                 for path in sorted(root.rglob("*")) if path.is_file())


def restore_tree(root, contents):
    expected = {name for name, _ in contents}
    for path in sorted(root.rglob("*"), reverse=True):
        if path.is_file() and path.relative_to(root).as_posix() not in expected:
            path.unlink()
    for name, data in contents:
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)


class ProcessFacilityPackageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._bases, cls._packages = {}, {}

    @classmethod
    def tearDownClass(cls):
        cls._bases.clear()
        cls._packages.clear()

    def setUp(self):
        try:
            self.api = importlib.import_module("layout_configurator.process_facility_package")
        except ModuleNotFoundError as exc:
            self.fail(f"The source-bound outer facility/process package is missing: {exc}")
        self.assertTrue(callable(getattr(self.api, "export_process_facility_package", None)))
        self.assertTrue(callable(getattr(self.api, "validate_process_facility_package", None)))
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.source = self.root / "original-review.json"
        self.original_base = self.root / "original-facility"
        self.package = self.root / "package"
        self.raw, self.kind = package_fixture(), "orthogonal"

    def write_source(self, raw=None):
        self.source.write_bytes((json.dumps(self.raw if raw is None else raw, ensure_ascii=False,
            allow_nan=False, indent=2) + "\r\n").encode("utf-8"))

    def base(self, *, kind="orthogonal"):
        self.kind = kind
        models = canonical_models(self.raw)
        building, layout, equipment, routing, profile = models
        key = (kind, _sha({"building": building.to_dict(), "layout": layout.to_dict(),
            "equipment": equipment.to_dict(building), "routing": routing.to_dict(), "profile": profile.to_dict()}))
        if key not in self._bases:
            with tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary) / "facility"
                if kind == "orthogonal":
                    write_orthogonal_bundle(root, building, layout, equipment, routing=routing, profile=profile)
                    report = validate_orthogonal_bundle(root)
                    self.assertTrue(report["ok"], report)
                else:
                    layout_report = validate_layout(building.layout, layout)
                    equipment_report = validate_equipment_layout(building, layout, equipment)
                    flow_report = validate_flow_routes(building, layout, routing, equipment)
                    facility_report = validate_building(building, layout, equipment, routing, flow_report,
                        profile=profile, equipment_report=equipment_report)
                    self.assertTrue(facility_report.ok, facility_report.to_dict())
                    entry = write_facility_variant(root, building, profile, layout, layout_report=layout_report,
                        equipment=equipment, equipment_report=equipment_report, flow_routes=routing,
                        flow_report=flow_report, facility_report=facility_report)
                    (root / "manifest.json").write_bytes(json.dumps({"project": building.layout.project_name,
                        "facility_profile": profile.to_dict(), "variants": [entry]}, ensure_ascii=False).encode("utf-8"))
                    report = validate_building_bundle(root / entry["json"])
                    self.assertTrue(report.ok, report.to_dict())
                self._bases[key] = tree_bytes(root)
        self.original_base.mkdir()
        restore_tree(self.original_base, self._bases[key])
        self.base_snapshot = tree_bytes(self.original_base)
        return self.base_snapshot

    def export(self, *, kind="orthogonal", raw=None):
        if raw is not None:
            self.raw = copy.deepcopy(raw)
        self.write_source()
        self.base(kind=kind)
        original = self.source.read_bytes()
        key = (kind, original, self.base_snapshot)
        if key not in self._packages:
            with tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                source, base, package = root / "review.json", root / "facility", root / "package"
                source.write_bytes(original)
                base.mkdir()
                restore_tree(base, self.base_snapshot)
                self.api.export_process_facility_package(source, base, package, base_kind=kind)
                self._packages[key] = tree_bytes(package)
        self.package.mkdir()
        restore_tree(self.package, self._packages[key])
        self.package_snapshot = tree_bytes(self.package)
        return self.read(self.package / "manifest.json")

    def read(self, path):
        return json.loads(path.read_bytes())

    def save(self, path, value):
        path.write_bytes(json.dumps(value, ensure_ascii=False, sort_keys=True,
            allow_nan=False, separators=(",", ":")).encode("utf-8"))

    def check(self, **kwargs):
        report = self.api.validate_process_facility_package(self.source, self.original_base, self.package, **kwargs)
        return report.to_dict() if hasattr(report, "to_dict") else report

    def rehash_outer(self):
        path = self.package / "manifest.json"
        manifest = self.read(path)
        manifest["files"] = {name: {"sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)}
            for name, data in tree_bytes(self.package) if name.startswith(("facility/", "process/"))}
        self.save(path, manifest)
        qa = self.read(self.package / "qa.json")
        qa["manifest_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
        self.save(self.package / "qa.json", qa)

    def test_both_original_bundle_kinds_copy_exact_artifacts_and_bind_all_components_without_solver(self):
        for kind in ("orthogonal", "rectangular"):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory(dir=self.root) as directory:
                folder = Path(directory)
                self.original_base, self.package = folder / "original-facility", folder / "package"
                self.write_source()
                original = self.source.read_bytes()
                with patch("ortools.sat.python.cp_model.CpSolver.solve", side_effect=AssertionError("Outer delivery cannot generate layouts")):
                    before = self.base(kind=kind)
                    manifest = self.api.export_process_facility_package(self.source, self.original_base, self.package, base_kind=kind)
                    report = self.check()
                self.assertEqual({path.name for path in self.package.iterdir()}, {"facility", "process", "manifest.json", "qa.json"})
                self.assertEqual(tree_bytes(self.package / "facility"), before)
                self.assertEqual(tree_bytes(self.original_base), before)
                self.assertEqual(self.source.read_bytes(), original)
                self.assertEqual((self.package / "process" / "source.json").read_bytes(), original)
                self.assertEqual(self.read(self.package / "manifest.json"), manifest)
                self.assertEqual((manifest["status"], manifest["ok"], manifest["review_status"]), ("DRAFT", False, "UNKNOWN"))
                self.assertEqual((report["status"], report["ok"]), ("PASS", True), report)
                self.assertTrue(report["base_qa"]["ok"])
                self.assertTrue(report["process_qa"]["ok"])
                self.assertEqual(report["full_facility_validation"], {"status": "UNKNOWN", "ok": False})
                self.assertEqual(report["full_process_validation"], "UNKNOWN")
                self.assertEqual(report["engineering_acceptance"], "PENDING")
                self.assertEqual(manifest["identity"], {"drawing_revision": "D01", "variant": 1,
                    "component_sha256": {name: _sha(self.raw[name]) for name in _COMPONENTS}})
                self.assertEqual(manifest["base_kind"], kind)
                self.assertEqual(manifest["base_profile_sha256"], _sha(canonical_models(self.raw)[-1].to_dict()))

    def test_known_process_pressure_conflict_is_not_approved_by_an_accepted_base(self):
        manifest = self.export(raw=package_fixture(cycle=True))
        report = self.check()
        self.assertTrue(report["ok"], report)
        self.assertTrue(report["base_qa"]["ok"])
        self.assertEqual((manifest["review_status"], report["review_status"]), ("FAIL", "FAIL"))
        evidence = self.read(self.package / "process" / "review.json")["reports"]["environment"]["evidence"]
        self.assertEqual((evidence["pressure_resolved_status"], evidence["pressure_measured_status"]), ("FAIL", "UNKNOWN"))
        self.assertEqual({row["edge_id"] for row in evidence["resolved_network"]["cycle_witness"]},
                         {"source_target", "target_upper", "upper_source"})
        self.assertEqual(report["full_process_validation"], "UNKNOWN")
        self.assertEqual(report["engineering_acceptance"], "PENDING")

    def test_original_scalar_clearance_route_revision_and_whole_embedded_program_cannot_be_reinterpreted(self):
        self.write_source()
        before = self.base()
        for mutation in ("layout_scalar", "clearance", "route", "revision", "embedded_program"):
            with self.subTest(mutation=mutation):
                raw = copy.deepcopy(self.raw)
                if mutation == "layout_scalar":
                    raw["layout"]["rooms"]["source"]["x"] = 0.0
                elif mutation == "clearance":
                    raw["equipment"][0].pop("clearance")
                elif mutation == "route":
                    raw["flow_routes"]["routes"][0]["points"].insert(1, {"x": 2800.0, "y": 1500.0})
                elif mutation == "revision":
                    raw["drawing_revision"] = "D02"
                else:
                    raw["building"]["production_program"] = copy.deepcopy(raw["production_program"])
                rebind(raw)
                self.write_source(raw)
                target = self.root / mutation
                with self.assertRaises(ValueError):
                    self.api.export_process_facility_package(self.source, self.original_base, target, base_kind="orthogonal")
                self.assertFalse(target.exists())
                self.assertEqual(tree_bytes(self.original_base), before)

    def test_rehashed_corruption_of_original_and_copied_base_requires_actual_fresh_physical_qa(self):
        self.export()
        base = self.original_base
        path = base / "geometry.dxf"
        document = ezdxf.readfile(path)
        entity = list(document.modelspace().query('LWPOLYLINE[layer=="A-EQUIP"]'))[0]
        points = list(entity.get_points("xy"))
        entity.set_points([(x + 100.0, y) for x, y in points], format="xy")
        document.saveas(path)
        manifest = self.read(base / "manifest.json")
        record = manifest["artifacts"]["dxf"]
        record["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
        index_path = base / record["index"]
        index = self.read(index_path)
        index["artifact_sha256"] = record["sha256"]
        self.save(index_path, index)
        record["index_sha256"] = hashlib.sha256(index_path.read_bytes()).hexdigest()
        self.save(base / "manifest.json", manifest)
        # Save once and copy exact bytes: two independent DXF writes may create
        # different GUID/time headers and accidentally test byte drift instead.
        restore_tree(self.package / "facility", tree_bytes(base))
        self.rehash_outer()
        report = self.check()
        self.assertFalse(report["ok"], report)
        self.assertFalse(report["base_qa"]["ok"], report)
        checks = {row["id"]: row["status"] for row in report["base_qa"]["checks"]}
        self.assertEqual(checks["DXF_READBACK"], "FAIL")

    def test_rehashed_process_report_cannot_substitute_saved_pass_for_a_fresh_cycle_failure(self):
        self.export(raw=package_fixture(cycle=True))
        process = self.package / "process"
        review = self.read(process / "review.json")
        review["reports"]["environment"]["status"] = "PASS"
        review["reports"]["environment"]["evidence"]["pressure_resolved_status"] = "PASS"
        self.save(process / "review.json", review)
        index = self.read(process / "index.json")
        index["files"]["review"]["sha256"] = hashlib.sha256((process / "review.json").read_bytes()).hexdigest()
        self.save(process / "index.json", index)
        qa = self.read(process / "qa.json")
        qa["index_sha256"] = hashlib.sha256((process / "index.json").read_bytes()).hexdigest()
        self.save(process / "qa.json", qa)
        self.rehash_outer()
        report = self.check()
        self.assertFalse(report["ok"], report)
        self.assertFalse(report["process_qa"]["ok"], report)

    def test_input_bounds_existing_destinations_and_source_aliases_preserve_original_work(self):
        self.write_source()
        before = self.base()
        original = self.source.read_bytes()
        defaults = self.api.ProcessFacilityPackageLimits()
        for name, value in (("max_total_bytes", 100), ("max_files", 1), ("max_file_bytes", 100)):
            with self.subTest(name=name):
                target = self.root / name
                with self.assertRaises(ValueError):
                    self.api.export_process_facility_package(self.source, self.original_base, target,
                        base_kind="orthogonal", limits=replace(defaults, **{name: value}))
                self.assertFalse(target.exists())
        existing = self.root / "existing"
        existing.mkdir()
        (existing / "keep.txt").write_bytes(b"Existing user work")
        alias = self.root / "source-alias.json"
        os.link(self.source, alias)
        for target in (existing, self.source, alias, self.original_base):
            with self.subTest(target=target.name), self.assertRaises((FileExistsError, ValueError)):
                self.api.export_process_facility_package(self.source, self.original_base, target, base_kind="orthogonal")
        with self.assertRaises(ValueError):
            self.api.export_process_facility_package(self.source, self.original_base, self.package, base_kind="unsupported")
        self.assertEqual((existing / "keep.txt").read_bytes(), b"Existing user work")
        self.assertEqual(tree_bytes(self.original_base), before)
        self.assertEqual(self.source.read_bytes(), original)

    def test_original_base_changes_and_raced_destinations_cannot_publish_stale_or_replace_work(self):
        self.write_source()
        self.base()
        original_export = self.api.export_process_documentation

        def change_base(*args, **kwargs):
            result = original_export(*args, **kwargs)
            path = self.original_base / "building.json"
            path.write_bytes(path.read_bytes() + b" ")
            return result

        with patch.object(self.api, "export_process_documentation", change_base), self.assertRaises(ValueError):
            self.api.export_process_facility_package(self.source, self.original_base, self.package, base_kind="orthogonal")
        self.assertFalse(self.package.exists())
        restore_tree(self.original_base, self.base_snapshot)
        original_publish = self.api._publish_directory

        def race(stage, target):
            Path(target).mkdir()
            (Path(target) / "keep.txt").write_bytes(b"Raced user work")
            return original_publish(stage, target)

        with patch.object(self.api, "_publish_directory", race), self.assertRaises((FileExistsError, ValueError)):
            self.api.export_process_facility_package(self.source, self.original_base, self.package, base_kind="orthogonal")
        self.assertEqual((self.package / "keep.txt").read_bytes(), b"Raced user work")
        self.assertEqual({path.name for path in self.package.iterdir()}, {"keep.txt"})

    def test_outer_and_original_child_inventories_reject_extra_or_escaped_artifacts(self):
        self.export()
        (self.package / "unverified-approval.txt").write_text("Approved", encoding="utf-8")
        self.assertFalse(self.check()["ok"])
        restore_tree(self.package, self.package_snapshot)
        manifest = self.read(self.package / "manifest.json")
        manifest["files"]["facility/../../outside.json"] = {"sha256": "0" * 64, "bytes": 1}
        self.save(self.package / "manifest.json", manifest)
        self.assertFalse(self.check()["ok"])
        restore_tree(self.package, self.package_snapshot)
        (self.original_base / "undeclared.txt").write_text("Unexpected", encoding="utf-8")
        self.assertFalse(self.check()["ok"])


if __name__ == "__main__":
    unittest.main()

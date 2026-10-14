"""An unchanged facility and actual process/path documents share original inputs.

All placements and paths are supplied. Immutable artifact bytes alone are
cached; every claimed acceptance and every forgery invokes fresh readback.
"""

import copy
from dataclasses import asdict
import hashlib
import importlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import ezdxf
from pypdf import PdfReader, PdfWriter
from pypdf.generic import ContentStream, FloatObject, NameObject

from layout_configurator.result_geometry import parse_layout_result
import test_process_facility_package as facility_fixtures
from test_process_facility_package import restore_tree, tree_bytes
from test_process_installation_review import installation_process_fixture
from test_process_review import _COMPONENTS, rebind
from test_transfer_program import _sha


_ROOT = {"facility", "process", "manifest.json", "qa.json"}
_PROCESS = {"source.json", "review.json", "documentation.pdf", "index.json", "qa.json"}


def json_nodes(value):
    """Count actual containers, keys and scalar values independently of parsers."""
    if type(value) is dict:
        return 1 + sum(1 + json_nodes(child) for child in value.values())
    if type(value) is list:
        return 1 + sum(json_nodes(child) for child in value)
    return 1


class ProcessInstallationFacilityPackageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._bases, cls._packages = {}, {}

    @classmethod
    def tearDownClass(cls):
        cls._bases.clear()
        cls._packages.clear()

    def setUp(self):
        try:
            self.api = importlib.import_module("layout_configurator.process_installation_facility_package")
        except ModuleNotFoundError as exc:
            self.fail(f"The explicit original-bound facility/process/path package is missing: {exc}")
        self.assertTrue(callable(getattr(self.api, "export_process_facility_package_with_paths", None)))
        self.assertTrue(callable(getattr(self.api, "validate_process_facility_package_with_paths", None)))
        self.legacy_api = importlib.import_module("layout_configurator.process_facility_package")
        self.review_api = importlib.import_module("layout_configurator.process_installation_review")
        self.document_api = importlib.import_module("layout_configurator.process_installation_documentation")
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.source = self.root / "original-process-paths.json"
        self.original_base = self.root / "original-facility"
        self.package = self.root / "package"
        self.raw, _, _, _ = installation_process_fixture()
        self.kind = "orthogonal"

    def source_bytes(self, raw=None):
        return (json.dumps(self.raw if raw is None else raw, ensure_ascii=False,
                           allow_nan=False, indent=2) + "\r\n").encode("utf-8")

    def write_source(self, raw=None):
        self.source.write_bytes(self.source_bytes(raw))

    def base(self, kind="orthogonal"):
        self.kind = kind
        # Use the existing supplied-model helper locally, without exposing its
        # TestCase class as an additional pytest collection item in this file.
        helper = facility_fixtures.ProcessFacilityPackageTests(
            "test_both_original_bundle_kinds_copy_exact_artifacts_and_bind_all_components_without_solver")
        helper.raw, helper.original_base, helper._bases = self.raw, self.original_base, self._bases
        with patch("ortools.sat.python.cp_model.CpSolver.solve",
                   side_effect=AssertionError("Fixture acceptance must never generate a layout")):
            helper.base(kind=kind)
        self.base_snapshot = tree_bytes(self.original_base)
        return self.base_snapshot

    def export(self, *, kind="orthogonal", raw=None):
        if raw is not None:
            self.raw = copy.deepcopy(raw)
        self.write_source()
        self.base(kind)
        original = self.source.read_bytes()
        key = (kind, original, self.base_snapshot)
        if key not in self._packages:
            with tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                source, base, package = root / "original.json", root / "facility", root / "package"
                source.write_bytes(original)
                base.mkdir()
                restore_tree(base, self.base_snapshot)
                with patch("ortools.sat.python.cp_model.CpSolver.solve",
                           side_effect=AssertionError("Attachment must never generate a layout or path")):
                    self.api.export_process_facility_package_with_paths(source, base, package, base_kind=kind)
                self._packages[key] = tree_bytes(package)
        self.package.mkdir()
        restore_tree(self.package, self._packages[key])
        self.package_snapshot = tree_bytes(self.package)
        return self.read(self.package / "manifest.json")

    def restore(self):
        restore_tree(self.package, self.package_snapshot)
        restore_tree(self.original_base, self.base_snapshot)
        self.write_source()

    def read(self, path):
        return json.loads(path.read_bytes())

    def save(self, path, value):
        path.write_bytes(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                    allow_nan=False, separators=(",", ":")).encode("utf-8"))

    def check(self, **options):
        return self.api.validate_process_facility_package_with_paths(self.source, self.original_base,
                                                                    self.package, **options)

    def rehash_child(self, *kinds):
        root = self.package / "process"
        index = self.read(root / "index.json")
        for kind in kinds:
            row = index["files"][kind]
            row["sha256"] = hashlib.sha256((root / row["file"]).read_bytes()).hexdigest()
        self.save(root / "index.json", index)
        qa = self.read(root / "qa.json")
        qa["index_sha256"] = hashlib.sha256((root / "index.json").read_bytes()).hexdigest()
        self.save(root / "qa.json", qa)

    def rehash_outer(self):
        path = self.package / "manifest.json"
        manifest = self.read(path)
        manifest["files"] = {name: {"sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)}
            for name, data in tree_bytes(self.package) if name.startswith(("facility/", "process/"))}
        self.save(path, manifest)
        qa = self.read(self.package / "qa.json")
        qa["manifest_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
        qa["process_qa"] = self.read(self.package / "process" / "qa.json")
        self.save(self.package / "qa.json", qa)

    def test_both_actual_base_formats_keep_original_bytes_five_file_child_and_common_request_identity(self):
        for kind, count in (("orthogonal", 14), ("rectangular", 9)):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory(dir=self.root) as temporary:
                folder = Path(temporary)
                self.original_base, self.package = folder / "original-facility", folder / "package"
                original = self.source_bytes()
                manifest = self.export(kind=kind)
                report = self.check()
                self.assertTrue(report["ok"], report)
                self.assertTrue(report["base_qa"]["ok"])
                self.assertTrue(report["process_qa"]["ok"])
                self.assertEqual({p.name for p in self.package.iterdir()}, _ROOT)
                self.assertEqual(len(tree_bytes(self.package / "facility")), count)
                self.assertEqual(tree_bytes(self.original_base), self.base_snapshot)
                self.assertEqual(tree_bytes(self.package / "facility"), self.base_snapshot)
                self.assertEqual(self.source.read_bytes(), original)
                self.assertEqual((self.package / "process" / "source.json").read_bytes(), original)
                self.assertEqual({p.name for p in (self.package / "process").iterdir()}, _PROCESS)
                index = self.read(self.package / "process" / "index.json")
                self.assertEqual((manifest["version"], index["version"], report["version"]), ("0.2", "0.2", "0.2"))
                self.assertEqual((manifest["status"], manifest["ok"], manifest["review_status"]), ("DRAFT", False, "UNKNOWN"))
                identity = {"drawing_revision": "D01", "variant": 1,
                            "component_sha256": {name: _sha(self.raw[name]) for name in _COMPONENTS}}
                self.assertEqual(manifest["identity"], identity)
                self.assertEqual(index["identity"], identity)
                self.assertEqual(manifest["review_source"]["file_sha256"], hashlib.sha256(original).hexdigest())
                self.assertEqual(manifest["path_limits"], asdict(self.review_api.ProcessInstallationPathLimits()))
                self.assertEqual(index["path_limits"], manifest["path_limits"])
                review = self.read(self.package / "process" / "review.json")
                leaf = review["installation_paths"]["reports"][0]
                self.assertEqual(leaf["request_sha256"], _sha(self.raw["installation_paths"][0]))
                self.assertEqual(leaf["evidence"]["local_2d_path_status"], "PASS")
                self.assertEqual(report["full_facility_validation"], {"status": "UNKNOWN", "ok": False})
                self.assertEqual(report["full_process_validation"], "UNKNOWN")
                self.assertEqual(report["engineering_acceptance"], "PENDING")
                self.assertEqual(report["authenticated_approval"], "UNKNOWN")
                self.assertFalse(report["proven_building_infeasible"])

    def test_known_moving_body_collision_remains_fail_beside_fresh_accepted_installed_base(self):
        raw, _, _, _ = installation_process_fixture(neighbor_y=2500.0)
        manifest = self.export(raw=raw)
        report = self.check()
        self.assertTrue(report["ok"], report)
        self.assertTrue(report["base_qa"]["ok"])
        self.assertTrue(report["process_qa"]["ok"])
        self.assertEqual((manifest["review_status"], report["review_status"]), ("FAIL", "FAIL"))
        evidence = self.read(self.package / "process" / "review.json")["installation_paths"]["reports"][0]["evidence"]
        witness = next(row for row in evidence["issues"] if row["code"] == "swept_body_collision" and row["role"] == "body")
        self.assertEqual((witness["related_equipment_id"], witness["overlap_area_mm2"]), ("neighbor", 700000))
        self.assertEqual(evidence["three_dimensional_feasibility"], "UNKNOWN")
        self.assertEqual(evidence["full_scenario_validation"], "UNKNOWN")
        self.assertEqual(report["full_process_validation"], "UNKNOWN")
        self.assertEqual(report["engineering_acceptance"], "PENDING")

    def test_actual_path_body_shift_fails_after_child_and_outer_hashes_and_cached_qa_are_rebound(self):
        self.export()
        path = self.package / "process" / "documentation.pdf"
        reader, writer = PdfReader(path), PdfWriter()
        writer.clone_document_from_reader(reader)
        identifier = _sha(self.raw["installation_paths"][0]) + ":segment:1"
        changed = False
        for number, page in enumerate(reader.pages):
            stream = ContentStream(page.get_contents(), reader)
            for position, (values, operator) in enumerate(stream.operations):
                if operator == b"BDC" and str(values[0]) == "/FLC" and \
                        (str(values[1]["/Kind"]), str(values[1]["/Id"])) == ("/installation_sweep_body", identifier):
                    stream.operations.insert(position + 1,
                        ([FloatObject(v) for v in (1, 0, 0, 1, 100, 0)], b"cm"))
                    writer.pages[number][NameObject("/Contents")] = writer._add_object(stream)
                    changed = True
                    break
            if changed:
                break
        self.assertTrue(changed, "A real source-bound whole-body sweep must be present")
        with path.open("wb") as output:
            writer.write(output)
        self.rehash_child("pdf")
        self.rehash_outer()
        report = self.check()
        self.assertFalse(report["ok"], report)
        self.assertTrue(report["base_qa"]["ok"], report)
        self.assertFalse(report["process_qa"]["ok"], report)

    def test_original_raw_scalar_clearance_whole_program_and_coherently_rehashed_base_remain_authorities(self):
        self.write_source()
        before = self.base()
        for mutation in ("layout_scalar", "clearance", "whole_program"):
            with self.subTest(mutation=mutation):
                raw = copy.deepcopy(self.raw)
                if mutation == "layout_scalar":
                    raw["layout"]["rooms"]["entry"]["x"] = 0
                    rebind(raw)
                    raw["installation_paths"][0]["source"]["layout_sha256"] = _sha(
                        parse_layout_result(raw["layout"]).to_dict())
                elif mutation == "clearance":
                    raw["equipment"][0].pop("clearance")
                    rebind(raw)
                else:
                    raw["building"]["production_program"] = copy.deepcopy(raw["production_program"])
                    rebind(raw, production_base=False)
                    raw["installation_paths"][0]["source"]["building_sha256"] = _sha(raw["building"])
                self.write_source(raw)
                fresh = self.review_api.load_process_review_with_paths(self.source)
                self.assertEqual(fresh["source_frames"]["building"], raw["building"])
                self.assertEqual(fresh["installation_paths"]["reports"][0]["evidence"]["local_2d_path_status"], "PASS")
                target = self.root / mutation
                with self.assertRaises(ValueError):
                    self.api.export_process_facility_package_with_paths(self.source, self.original_base, target)
                self.assertFalse(target.exists())
                self.assertEqual(tree_bytes(self.original_base), before)
        self.write_source()
        self.original_base = self.root / "fresh-original-facility"
        self.export()
        path = self.original_base / "geometry.dxf"
        drawing = ezdxf.readfile(path)
        entity = list(drawing.modelspace().query('LWPOLYLINE[layer=="A-EQUIP"]'))[0]
        entity.set_points([(x + 100.0, y) for x, y in entity.get_points("xy")], format="xy")
        drawing.saveas(path)
        manifest = self.read(self.original_base / "manifest.json")
        record = manifest["artifacts"]["dxf"]
        record["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
        index_path = self.original_base / record["index"]
        index = self.read(index_path)
        index["artifact_sha256"] = record["sha256"]
        self.save(index_path, index)
        record["index_sha256"] = hashlib.sha256(index_path.read_bytes()).hexdigest()
        self.save(self.original_base / "manifest.json", manifest)
        restore_tree(self.package / "facility", tree_bytes(self.original_base))
        self.rehash_outer()
        report = self.check()
        self.assertFalse(report["ok"], report)
        self.assertFalse(report["base_qa"]["ok"], report)
        self.assertEqual({row["id"]: row["status"] for row in report["base_qa"]["checks"]}["DXF_READBACK"], "FAIL")

    def test_changed_original_request_or_bytes_cannot_authorize_a_stale_pdf_with_fresh_rebound_json(self):
        self.export()
        for mutation in ("request", "bytes"):
            with self.subTest(mutation=mutation):
                self.restore()
                raw = copy.deepcopy(self.raw)
                if mutation == "request":
                    raw["installation_paths"][0]["poses"][0]["x_mm"] = -1300.0
                changed = self.source_bytes(raw) + (b" " if mutation == "bytes" else b"")
                self.source.write_bytes(changed)
                process = self.package / "process"
                (process / "source.json").write_bytes(changed)
                fresh = self.review_api.load_process_review_with_paths(self.source)
                self.save(process / "review.json", fresh)
                for path in (process / "index.json", process / "qa.json", self.package / "manifest.json", self.package / "qa.json"):
                    record = self.read(path)
                    record.update(identity=fresh["identity"], review_source=fresh["review_source"], review_status=fresh["status"])
                    self.save(path, record)
                self.rehash_child("source", "review")
                self.rehash_outer()
                report = self.check()
                self.assertFalse(report["ok"], report)
                self.assertTrue(report["base_qa"]["ok"], report)
                self.assertFalse(report["process_qa"]["ok"], report)

    def test_closed_outer_and_child_metadata_and_effective_path_limits_are_admitted_before_source_geometry(self):
        self.export()
        outer_path, child_path = self.package / "manifest.json", self.package / "process" / "index.json"
        for place in ("outer", "child"):
            with self.subTest(admitted_tightening=place):
                self.restore()
                path = outer_path if place == "outer" else child_path
                value = self.read(path)
                value["path_limits"]["max_total_poses"] = 4
                self.save(path, value)
                self.rehash_child()
                self.rehash_outer()
                self.assertTrue(self.check()["ok"], "A stricter admitted global budget must retain unchanged evidence")
        for mutation in ("outer_boolean", "child_boolean", "outer_relaxed", "child_relaxed", "missing", "unknown",
                         "outer_version", "child_version", "outer_tighter", "child_tighter", "child_document_input"):
            with self.subTest(rejected_before_geometry=mutation):
                self.restore()
                outer, child = self.read(outer_path), self.read(child_path)
                if mutation == "outer_boolean":
                    outer["path_limits"]["max_paths"] = True
                elif mutation == "child_boolean":
                    child["path_limits"]["max_paths"] = True
                elif mutation == "outer_relaxed":
                    outer["path_limits"]["max_paths"] += 1
                elif mutation == "child_relaxed":
                    child["path_limits"]["max_paths"] += 1
                elif mutation == "missing":
                    child.pop("path_limits")
                elif mutation == "unknown":
                    outer["path_limits"]["approved"] = True
                elif mutation == "outer_version":
                    outer["version"] = "0.1"
                elif mutation == "child_version":
                    child["version"] = "0.1"
                elif mutation == "outer_tighter":
                    outer["path_limits"]["max_total_poses"] = 2
                elif mutation == "child_tighter":
                    child["path_limits"]["max_total_poses"] = 2
                else:
                    child["limits"]["max_input_bytes"] = 100
                self.save(outer_path, outer)
                self.save(child_path, child)
                self.rehash_child()
                self.rehash_outer()
                with patch("layout_configurator.transfer_review.parse_layout_result",
                           side_effect=AssertionError("Declared controls must precede source geometry")) as parse:
                    report = self.check()
                self.assertFalse(report["ok"], report)
                parse.assert_not_called()

    def test_combined_file_pdf_page_and_json_node_budgets_hold_when_each_child_fits_alone(self):
        self.export()
        base, process = tree_bytes(self.package / "facility"), tree_bytes(self.package / "process")
        base_pages = sum(len(PdfReader(self.package / "facility" / name).pages) for name, _ in base if name.endswith(".pdf"))
        process_pages = sum(len(PdfReader(self.package / "process" / name).pages) for name, _ in process if name.endswith(".pdf"))
        base_nodes = sum(json_nodes(json.loads(data)) for name, data in base if name.endswith(".json"))
        process_nodes = sum(json_nodes(json.loads(data)) for name, data in process if name.endswith(".json"))
        limits = {"max_files": max(len(base), len(process)) + 2,
                  "max_pages": max(base_pages, process_pages),
                  "max_json_nodes": max(base_nodes, process_nodes) + 1}
        self.assertLess(limits["max_files"], len(base) + len(process) + 2)
        self.assertLess(limits["max_pages"], base_pages + process_pages)
        self.assertLess(limits["max_json_nodes"], base_nodes + process_nodes)
        for name, value in limits.items():
            with self.subTest(combined_bound=name):
                self.restore()
                manifest = self.read(self.package / "manifest.json")
                manifest["limits"][name] = value
                self.save(self.package / "manifest.json", manifest)
                self.rehash_outer()
                with patch("layout_configurator.transfer_review.parse_layout_result",
                           side_effect=AssertionError("Combined artifact admission must precede source geometry")) as parse:
                    report = self.check()
                self.assertFalse(report["ok"], report)
                parse.assert_not_called()

    def test_changed_declared_controls_between_admission_snapshots_reject_before_geometry(self):
        self.export()
        original_snapshot = self.legacy_api._snapshot
        captures = 0

        def change_controls(directory, limits, **options):
            nonlocal captures
            if Path(directory).resolve() == self.package.resolve():
                captures += 1
                if captures == 2:
                    index_path = self.package / "process" / "index.json"
                    index = self.read(index_path)
                    index["limits"]["max_input_bytes"] = 100
                    self.save(index_path, index)
                    self.rehash_child()
                    self.rehash_outer()
            return original_snapshot(directory, limits, **options)

        with patch.object(self.legacy_api, "_snapshot", change_controls), \
                patch("layout_configurator.transfer_review.parse_layout_result",
                      side_effect=AssertionError("Changed declared controls must precede source geometry")) as parse:
            report = self.check()
        self.assertGreaterEqual(captures, 2, "The actual admission recapture must be exercised")
        self.assertFalse(report["ok"], report)
        parse.assert_not_called()

    def test_child_export_source_base_changes_and_native_destination_races_preserve_user_work(self):
        self.write_source()
        before = self.base()
        original = self.source.read_bytes()
        original_export = self.api.export_process_documentation_with_paths
        for mutation in ("source", "base"):
            with self.subTest(mutation=mutation):
                target = self.root / mutation

                def change_input(*args, **kwargs):
                    result = original_export(*args, **kwargs)
                    path = self.source if mutation == "source" else self.original_base / "building.json"
                    path.write_bytes(path.read_bytes() + b" ")
                    return result

                with patch.object(self.api, "export_process_documentation_with_paths", change_input), self.assertRaises(ValueError):
                    self.api.export_process_facility_package_with_paths(self.source, self.original_base, target)
                self.assertFalse(target.exists())
                self.write_source()
                restore_tree(self.original_base, before)
        existing = self.root / "existing"
        existing.mkdir()
        (existing / "keep.txt").write_bytes(b"Existing user work")
        for target in (existing, self.source, self.original_base, self.original_base / "nested-package"):
            with self.subTest(target=target.name), self.assertRaises((FileExistsError, ValueError)):
                self.api.export_process_facility_package_with_paths(self.source, self.original_base, target)
        self.assertEqual((existing / "keep.txt").read_bytes(), b"Existing user work")
        original_publish = self.api._publish_directory

        def race(stage, target):
            Path(target).mkdir()
            (Path(target) / "keep.txt").write_bytes(b"Raced user work")
            return original_publish(stage, target)

        with patch.object(self.api, "_publish_directory", race), self.assertRaises((FileExistsError, ValueError)):
            self.api.export_process_facility_package_with_paths(self.source, self.original_base, self.package)
        self.assertEqual((self.package / "keep.txt").read_bytes(), b"Raced user work")
        self.assertEqual({p.name for p in self.package.iterdir()}, {"keep.txt"})
        self.assertEqual(tree_bytes(self.original_base), before)
        self.assertEqual(self.source.read_bytes(), original)

    def test_old_version_one_public_contract_rejects_version_two_and_keeps_original_defaults(self):
        self.export()
        self.assertFalse(self.legacy_api.validate_process_facility_package(self.source, self.original_base, self.package)["ok"])
        with self.assertRaises(ValueError):
            self.legacy_api.export_process_facility_package(self.source, self.original_base, self.root / "old-rejected")
        old = copy.deepcopy(self.raw)
        old.update(version="0.1")
        old.pop("installation_paths")
        self.write_source(old)
        target = self.root / "old-package"
        manifest = self.legacy_api.export_process_facility_package(self.source, self.original_base, target)
        self.assertEqual(manifest["version"], "0.1")
        self.assertEqual(manifest["limits"], asdict(self.legacy_api.ProcessFacilityPackageLimits()))
        self.assertNotIn("path_limits", manifest)
        self.assertNotIn("max_paths", manifest["limits"])
        self.assertTrue(self.legacy_api.validate_process_facility_package(self.source, self.original_base, target)["ok"])
        with self.assertRaises(ValueError):
            self.api.export_process_facility_package_with_paths(self.source, self.original_base, self.root / "new-rejected")
        self.assertFalse((self.root / "new-rejected").exists())


if __name__ == "__main__":
    unittest.main()

"""Original facility bytes and fresh production requirement documents share a revision.

Only immutable artifact bytes are cached. Every acceptance and corruption check
performs actual original-base, process-source and saved-PDF readback.
"""

import copy
from contextlib import ExitStack
from dataclasses import asdict, replace
import hashlib
import importlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import ezdxf
from pypdf import PdfReader, PdfWriter
from pypdf.generic import ContentStream, FloatObject, NameObject, TextStringObject

from layout_configurator.building import BuildingIR
from layout_configurator.orthogonal_bundle import write_orthogonal_bundle
from layout_configurator.orthogonal_bundle_qa import validate_orthogonal_bundle
from layout_configurator.process_documentation import ProcessDocumentationLimits
from layout_configurator.process_facility_package import ProcessFacilityPackageLimits
from layout_configurator.process_installation_review import ProcessInstallationPathLimits
from layout_configurator.production_layout_review import ProductionLayoutLimits
from test_process_documentation import actual_painted_paths
import test_process_facility_package as _facility_fixtures
import test_process_installation_facility_package as _path_fixtures
from test_process_facility_package import restore_tree, tree_bytes
from test_process_installation_review import add_declared_pressure_cycle
from test_process_production_layout_review import production_process_fixture, sync_production_process
from test_process_review import _COMPONENTS
from test_transfer_program import _sha


_ROOT = {"facility", "process", "manifest.json", "qa.json"}
_PROCESS = {"source.json", "review.json", "documentation.pdf", "index.json", "qa.json"}
_MANIFEST_KEYS = {"format", "version", "scope", "status", "ok", "review_status", "identity", "review_source", "base_kind",
    "base_profile_sha256", "base_projection_options", "files", "limits", "full_facility_validation", "full_process_validation",
    "engineering_acceptance", "authenticated_approval", "path_limits", "production_limits", "include_windows"}


def forbidden_pressure_process_fixture():
    """An edited forbidden process claim beside unchanged valid touching rooms."""
    raw = production_process_fixture()
    add_declared_pressure_cycle(raw)
    source = raw["production_program"]["sources"][0]
    excerpt = "The updated process requires entry and work to remain separated."
    content = source["content"] + excerpt + "\r\n"
    source.update(content=content, sha256=hashlib.sha256(content.encode("utf-8")).hexdigest(),
        locator="Complete supplied reconstruction text")
    source["usage_rights"]["state"] = "UNKNOWN"
    raw["production_program"]["requirements"][0].update(id="entry_work_separation", kind="forbidden_room_adjacency",
        reason="Supplied edited separation requirement beside the unchanged original facility", basis="declared_fact", excerpt=excerpt)
    raw["installation_paths"][0]["exterior_staging"] = None
    sync_production_process(raw)
    return raw


def pdf_work(contents):
    """Count actual child PDF pages, operations and extracted painted text."""
    pages, operations, characters = 0, 0, 0
    for name, data in contents:
        if not name.endswith(".pdf"):
            continue
        reader = PdfReader(io.BytesIO(data))
        pages += len(reader.pages)
        for page in reader.pages:
            operations += len(ContentStream(page.get_contents(), reader).operations)
            characters += len(page.extract_text())
    return pages, operations, characters


class ProcessProductionLayoutFacilityPackageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._bases, cls._fixture_bases, cls._packages = {}, {}, {}

    @classmethod
    def tearDownClass(cls):
        cls._bases.clear()
        cls._fixture_bases.clear()
        cls._packages.clear()

    def setUp(self):
        try:
            self.api = importlib.import_module("layout_configurator.process_production_layout_facility_package")
        except ModuleNotFoundError as exc:
            self.fail(f"The explicit production-requirement facility attachment is missing: {exc}")
        self.assertTrue(callable(getattr(self.api, "export_process_facility_package_with_production_layout", None)))
        self.assertTrue(callable(getattr(self.api, "validate_process_facility_package_with_production_layout", None)))
        self.legacy_api = importlib.import_module("layout_configurator.process_facility_package")
        self.path_api = importlib.import_module("layout_configurator.process_installation_facility_package")
        self.document_api = importlib.import_module("layout_configurator.process_production_layout_documentation")
        self.review_api = importlib.import_module("layout_configurator.process_production_layout_review")
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.source = self.root / "original-production-process.json"
        self.original_base = self.root / "original-facility"
        self.package = self.root / "package"
        self.raw = production_process_fixture()
        self.kind = "orthogonal"

    read = _path_fixtures.ProcessInstallationFacilityPackageTests.read
    save = _path_fixtures.ProcessInstallationFacilityPackageTests.save
    restore = _path_fixtures.ProcessInstallationFacilityPackageTests.restore
    rehash_child = _path_fixtures.ProcessInstallationFacilityPackageTests.rehash_child
    rehash_outer = _path_fixtures.ProcessInstallationFacilityPackageTests.rehash_outer

    def source_bytes(self, raw=None):
        return (json.dumps(self.raw if raw is None else raw, ensure_ascii=False,
            allow_nan=False, indent=2) + "\r\n").encode("utf-8")

    def write_source(self, raw=None):
        self.source.write_bytes(self.source_bytes(raw))

    def base(self, kind="orthogonal", *, include_windows=False):
        self.kind = kind
        key = (kind, include_windows, _sha({name: self.raw[name] for name in ("building", "layout", "equipment", "flow_routes")}),
            self.raw["drawing_revision"], self.raw["variant"])
        if self.original_base.exists():
            self.assertEqual(getattr(self, "_base_key", None), key)
            self.assertEqual(tree_bytes(self.original_base), self.base_snapshot)
            return self.base_snapshot
        if key not in self._bases:
            with tempfile.TemporaryDirectory() as temporary:
                original = Path(temporary) / "facility"
                with patch("ortools.sat.python.cp_model.CpSolver.solve",
                    side_effect=AssertionError("Literal base preparation must not solve")) as solve:
                    if include_windows:
                        self.assertEqual(kind, "orthogonal")
                        building, layout, equipment, routing, profile = _facility_fixtures.canonical_models(self.raw)
                        write_orthogonal_bundle(original, building, layout, equipment, routing=routing, profile=profile,
                            include_windows=True)
                        self.assertTrue(validate_orthogonal_bundle(original, include_windows=True)["ok"])
                    else:
                        helper = _facility_fixtures.ProcessFacilityPackageTests(
                            "test_both_original_bundle_kinds_copy_exact_artifacts_and_bind_all_components_without_solver")
                        helper.raw, helper.original_base, helper._bases = self.raw, original, self._fixture_bases
                        helper.base(kind=kind)
                    solve.assert_not_called()
                self._bases[key] = tree_bytes(original)
        self.original_base.mkdir()
        restore_tree(self.original_base, self._bases[key])
        self.base_snapshot, self._base_key = tree_bytes(self.original_base), key
        return self.base_snapshot

    def export(self, *, kind="orthogonal", raw=None, include_windows=False, **controls):
        if raw is not None:
            self.raw = copy.deepcopy(raw)
        self.write_source()
        self.base(kind, include_windows=include_windows)
        original = self.source.read_bytes()
        serialized_controls = {name: asdict(value) if hasattr(value, "__dataclass_fields__") else value for name, value in controls.items()}
        key = (kind, include_windows, original, self.base_snapshot, _sha(serialized_controls))
        if key not in self._packages:
            with tempfile.TemporaryDirectory() as temporary:
                folder = Path(temporary)
                source, base, package = folder / "source.json", folder / "facility", folder / "package"
                source.write_bytes(original)
                base.mkdir()
                restore_tree(base, self.base_snapshot)
                with patch("ortools.sat.python.cp_model.CpSolver.solve",
                    side_effect=AssertionError("Attachment must not regenerate geometry")) as solve:
                    self.api.export_process_facility_package_with_production_layout(source, base, package,
                        base_kind=kind, include_windows=include_windows, **controls)
                    solve.assert_not_called()
                self._packages[key] = tree_bytes(package)
        self.package.mkdir()
        restore_tree(self.package, self._packages[key])
        self.package_snapshot = tree_bytes(self.package)
        return self.read(self.package / "manifest.json")

    def check(self, **controls):
        with patch("ortools.sat.python.cp_model.CpSolver.solve", side_effect=AssertionError("Package readback cannot solve")) as solve:
            report = self.api.validate_process_facility_package_with_production_layout(
                self.source, self.original_base, self.package, **controls)
            solve.assert_not_called()
        return report

    def check_without_geometry(self, **controls):
        with ExitStack() as stack:
            spies = [stack.enter_context(patch(target, side_effect=AssertionError("Whole admission precedes source constructors")))
                for target in ("layout_configurator.building.BuildingIR.from_mapping",
                    "layout_configurator.transfer_review.parse_layout_result", "layout_configurator.orthogonal.Polygon",
                    "layout_configurator.result_geometry.box", "layout_configurator.equipment_installation_path.box")]
            report = self.check(**controls)
        self.assertFalse(report["ok"], report)
        for spy in spies:
            spy.assert_not_called()
        return report

    def identity(self):
        return {"drawing_revision": self.raw["drawing_revision"], "variant": self.raw["variant"],
            "component_sha256": {name: _sha(self.raw[name]) for name in _COMPONENTS}}

    def edit_printed_production(self, original, replacement):
        process = self.package / "process"
        reader, writer = PdfReader(process / "documentation.pdf"), PdfWriter()
        writer.clone_document_from_reader(reader)
        changed = False
        for row in self.read(process / "index.json")["pages"]:
            if row["kind"] not in {"production_layout", "production_requirement"}:
                continue
            number = row["page_number"] - 1
            stream = ContentStream(reader.pages[number].get_contents(), reader)
            for values, operator in stream.operations:
                if operator == b"Tj" and original in str(values[0]):
                    values[0] = TextStringObject(str(values[0]).replace(original, replacement))
                    writer.pages[number][NameObject("/Contents")] = writer._add_object(stream)
                    changed = True
                    break
            if changed:
                break
        self.assertTrue(changed)
        with (process / "documentation.pdf").open("wb") as output:
            writer.write(output)
        self.rehash_child("pdf")
        self.rehash_outer()

    def test_both_native_base_kinds_keep_original_bytes_closed_twenty_field_identity_and_actual_requirement_pdf(self):
        for kind, count in (("orthogonal", 14), ("rectangular", 9)):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory(dir=self.root) as temporary:
                self.original_base, self.package = Path(temporary) / "facility", Path(temporary) / "package"
                original = self.source_bytes()
                manifest = self.export(kind=kind)
                report = self.check()
                process = self.package / "process"
                index, review = self.read(process / "index.json"), self.read(process / "review.json")
                evidence = review["additional_reports"]["production_layout"]["evidence"]
                self.assertTrue(report["ok"], report)
                self.assertTrue(report["base_qa"]["ok"])
                self.assertTrue(report["process_qa"]["ok"])
                self.assertEqual(set(manifest), _MANIFEST_KEYS)
                self.assertEqual(len(manifest), 20)
                self.assertEqual({p.name for p in self.package.iterdir()}, _ROOT)
                self.assertEqual({p.name for p in process.iterdir()}, _PROCESS)
                self.assertEqual(len(tree_bytes(self.package / "facility")), count)
                self.assertEqual(tree_bytes(self.package / "facility"), self.base_snapshot)
                self.assertEqual(tree_bytes(self.original_base), self.base_snapshot)
                self.assertEqual(self.source.read_bytes(), original)
                self.assertEqual((process / "source.json").read_bytes(), original)
                self.assertEqual((manifest["version"], index["version"], report["version"]), ("0.3", "0.3", "0.3"))
                self.assertEqual((manifest["status"], manifest["ok"], manifest["review_status"]), ("DRAFT", False, "UNKNOWN"))
                self.assertEqual(manifest["identity"], self.identity())
                self.assertEqual(index["identity"], self.identity())
                self.assertEqual(review["additional_reports"]["production_layout"]["identity"], self.identity())
                self.assertEqual(manifest["review_source"]["file_sha256"], hashlib.sha256(original).hexdigest())
                self.assertEqual(manifest["limits"], asdict(ProcessFacilityPackageLimits()))
                for field, value in (("path_limits", asdict(ProcessInstallationPathLimits())),
                    ("production_limits", asdict(ProductionLayoutLimits())), ("include_windows", False)):
                    self.assertEqual(manifest[field], value)
                    self.assertEqual(index[field], value)
                self.assertEqual(evidence["source"], {"building_sha256": _sha(self.raw["building"]),
                    "layout_sha256": _sha(self.raw["layout"]), "production_program_sha256": _sha(self.raw["production_program"]),
                    "program_base_building_sha256": _sha(self.raw["building"])})
                self.assertEqual(evidence["geometry_status"], "PASS")
                row = evidence["requirements"][0]
                pages = [r["page_number"] - 1 for r in index["pages"] if r["kind"] == "production_requirement" and r["source_id"] == row["requirement_id"]]
                self.assertTrue(pages)
                reader = PdfReader(process / "documentation.pdf")
                text = "".join("\n".join(reader.pages[number].extract_text() for number in pages).split())
                for token in (row["requirement_id"], row["requirement_sha256"], row["source_sha256"], row["operation_id"],
                    "maximum_contiguous_mm", "required_contiguous_mm", "Requirement status: PASS"):
                    self.assertIn("".join(token.split()), text)
                painted = actual_painted_paths((process / "documentation.pdf").read_bytes(), index["projection"])
                self.assertIn(("installation_pose_body", _sha(self.raw["installation_paths"][0]) + ":pose:0"), painted)
                self.assertEqual(report["full_process_validation"], "UNKNOWN")
                self.assertEqual(report["full_facility_validation"], {"status": "UNKNOWN", "ok": False})
                self.assertEqual(report["engineering_acceptance"], "PENDING")
                self.assertEqual(report["authenticated_approval"], "UNKNOWN")
                self.assertFalse(report["proven_building_infeasible"])

    def test_edited_forbidden_contact_and_pressure_fail_keep_valid_original_bases_and_unknown_rights_without_approval(self):
        for kind in ("orthogonal", "rectangular"):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory(dir=self.root) as temporary:
                self.original_base, self.package = Path(temporary) / "facility", Path(temporary) / "package"
                manifest = self.export(kind=kind, raw=forbidden_pressure_process_fixture())
                report = self.check()
                evidence = self.read(self.package / "process" / "review.json")["additional_reports"]["production_layout"]["evidence"]
                self.assertTrue(report["ok"], report)
                self.assertTrue(report["base_qa"]["ok"], "Original physical base acceptance must not be softened for the edited program")
                self.assertTrue(report["process_qa"]["ok"])
                self.assertEqual((manifest["review_status"], report["review_status"], evidence["geometry_status"]), ("FAIL", "FAIL", "FAIL"))
                self.assertEqual((evidence["binding_status"], evidence["provenance_status"]), ("PASS", "FAIL"))
                codes = {row["code"] for row in evidence["issues"]}
                self.assertTrue({"forbidden_room_adjacency_failed", "contradictory_adjacency_declaration", "unknown_usage_rights"} <= codes)
                row = evidence["requirements"][0]
                self.assertEqual((row["kind"], row["maximum_contiguous_mm"], row["required_contiguous_mm"], row["overlap_area_mm2"]),
                    ("forbidden_room_adjacency", 6000.0, None, 0.0))
                review = self.read(self.package / "process" / "review.json")
                self.assertEqual(review["reports"]["environment"]["evidence"]["pressure_declared_status"], "FAIL")
                self.assertIn("missing_exterior_staging", {r["code"] for r in review["installation_paths"]["reports"][0]["evidence"]["issues"]})
                self.assertEqual((manifest["status"], manifest["ok"], review["ok"]), ("DRAFT", False, False))
                self.assertEqual(tree_bytes(self.package / "facility"), self.base_snapshot)
                self.assertEqual(tree_bytes(self.original_base), self.base_snapshot)

    def test_rehashed_pdf_witness_saved_review_and_actual_base_geometry_cannot_be_authorized_by_cached_qa(self):
        self.export(raw=forbidden_pressure_process_fixture())
        for corruption in ("printed_verdict", "saved_span"):
            with self.subTest(corruption=corruption):
                self.restore()
                if corruption == "printed_verdict":
                    self.edit_printed_production("Production geometry status: FAIL", "Production geometry status: PASS")
                else:
                    path = self.package / "process" / "review.json"
                    review = self.read(path)
                    review["additional_reports"]["production_layout"]["evidence"]["requirements"][0]["maximum_contiguous_mm"] = 0
                    self.save(path, review)
                    self.rehash_child("review")
                    self.rehash_outer()
                report = self.check()
                self.assertFalse(report["ok"], report)
                self.assertTrue(report["base_qa"]["ok"])
                self.assertFalse(report["process_qa"]["ok"])
                self.assertEqual(tree_bytes(self.original_base), self.base_snapshot)
        self.restore()
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
        self.assertFalse(report["ok"])
        self.assertFalse(report["base_qa"]["ok"])
        self.assertEqual({row["id"]: row["status"] for row in report["base_qa"]["checks"]}["DXF_READBACK"], "FAIL")

    def test_closed_caller_outer_and_child_controls_take_minimums_before_any_source_construction(self):
        self.export()
        for place in ("outer", "child"):
            path = self.package / "manifest.json" if place == "outer" else self.package / "process" / "index.json"
            for section, field, value in (("production_limits", "max_pair_checks", 111),
                ("production_limits", "max_pair_checks", True), ("production_limits", "max_pair_checks", 131073),
                ("path_limits", "max_total_poses", 2), ("limits", "max_geometry_checks", 6000),
                ("limits", "max_scenario_vertices", 66), ("limits", "max_input_bytes", 100)):
                with self.subTest(place=place, section=section, field=field, value=value):
                    self.restore()
                    metadata = self.read(path)
                    metadata[section][field] = value
                    self.save(path, metadata)
                    self.rehash_child()
                    self.rehash_outer()
                    self.check_without_geometry()
            for mutation in ("missing_production", "unknown_limit", "window_type", "window_mismatch", "version"):
                with self.subTest(place=place, mutation=mutation):
                    self.restore()
                    metadata = self.read(path)
                    if mutation == "missing_production":
                        metadata.pop("production_limits")
                    elif mutation == "unknown_limit":
                        metadata["production_limits"]["approved"] = True
                    elif mutation == "window_type":
                        metadata["include_windows"] = "false"
                    elif mutation == "window_mismatch":
                        metadata["include_windows"] = True
                    else:
                        metadata["version"] = "0.2"
                    self.save(path, metadata)
                    self.rehash_child()
                    self.rehash_outer()
                    self.check_without_geometry()
        self.restore()
        for controls in ({"limits": replace(ProcessFacilityPackageLimits(), max_geometry_checks=6000)},
            {"path_limits": replace(ProcessInstallationPathLimits(), max_total_poses=2)},
            {"production_limits": replace(ProductionLayoutLimits(), max_pair_checks=111)}):
            with self.subTest(caller=controls):
                self.check_without_geometry(**controls)

    def test_combined_file_byte_json_page_text_and_operation_consumers_cannot_reset_the_outer_budget(self):
        self.export()
        base, process = tree_bytes(self.package / "facility"), tree_bytes(self.package / "process")
        base_pages, base_ops, base_text = pdf_work(base)
        process_pages, process_ops, process_text = pdf_work(process)
        base_nodes = sum(_path_fixtures.json_nodes(json.loads(data)) for name, data in base if name.endswith(".json"))
        process_nodes = sum(_path_fixtures.json_nodes(json.loads(data)) for name, data in process if name.endswith(".json"))
        choices = {"max_files": max(len(base), len(process)) + 2,
            "max_total_bytes": max(sum(len(data) for _, data in base), sum(len(data) for _, data in process)) + 1,
            "max_json_nodes": max(base_nodes, process_nodes) + 1, "max_pages": max(base_pages, process_pages),
            "max_total_pdf_operations": max(base_ops, process_ops) + 1, "max_text_chars": max(base_text, process_text) + 1}
        self.assertLess(choices["max_pages"], base_pages + process_pages)
        self.assertLess(choices["max_total_pdf_operations"], base_ops + process_ops)
        self.assertGreater(choices["max_text_chars"], base_text)
        self.assertGreater(choices["max_text_chars"], process_text)
        self.assertLess(choices["max_text_chars"], base_text + process_text)
        self.assertLess(choices["max_json_nodes"], base_nodes + process_nodes)
        for field, value in choices.items():
            with self.subTest(field=field):
                self.restore()
                path = self.package / "manifest.json"
                manifest = self.read(path)
                manifest["limits"][field] = value
                self.save(path, manifest)
                self.rehash_outer()
                self.check_without_geometry()
        self.assertEqual(tree_bytes(self.original_base), self.base_snapshot)

    def test_both_initial_metadata_snapshots_and_during_geometry_changes_remain_bound_to_the_first_controls(self):
        self.export()
        for place in ("outer", "child"):
            with self.subTest(before_geometry=place):
                self.restore()
                original_snapshot = self.legacy_api._snapshot
                captures = 0

                def replace_metadata(directory, limits, **options):
                    nonlocal captures
                    if Path(directory).resolve() == self.package.resolve():
                        captures += 1
                        if captures == 2:
                            path = self.package / "manifest.json" if place == "outer" else self.package / "process" / "index.json"
                            metadata = self.read(path)
                            metadata["production_limits"]["max_pair_checks"] = 65536
                            self.save(path, metadata)
                            self.rehash_child()
                            self.rehash_outer()
                    return original_snapshot(directory, limits, **options)

                with patch.object(self.legacy_api, "_snapshot", replace_metadata):
                    self.check_without_geometry()
                self.assertGreaterEqual(captures, 2)
            with self.subTest(during_geometry=place):
                self.restore()
                original_constructor = BuildingIR.from_mapping
                changed = False

                def replace_during_admission(*args, **kwargs):
                    nonlocal changed
                    if not changed:
                        changed = True
                        path = self.package / "manifest.json" if place == "outer" else self.package / "process" / "index.json"
                        metadata = self.read(path)
                        metadata["production_limits"]["max_pair_checks"] = 65536
                        self.save(path, metadata)
                        self.rehash_child()
                        self.rehash_outer()
                    return original_constructor(*args, **kwargs)

                with patch.object(BuildingIR, "from_mapping", side_effect=replace_during_admission) as constructor:
                    report = self.check()
                self.assertTrue(changed)
                self.assertTrue(constructor.called)
                self.assertFalse(report["ok"], "Fresh acceptance cannot switch to whichever valid metadata was captured last")

    def test_fitting_outer_and_caller_tightening_uses_child_authored_annotations_and_fresh_strict_admission(self):
        self.export(limits=replace(ProcessFacilityPackageLimits(), max_geometry_checks=16384, max_scenario_vertices=1024),
            path_limits=replace(ProcessInstallationPathLimits(), max_poses=64, max_geometry_checks=4096),
            production_limits=replace(ProductionLayoutLimits(), max_pair_checks=4096))
        process_before = tree_bytes(self.package / "process")
        child = self.read(self.package / "process" / "index.json")
        path = self.package / "manifest.json"
        outer = self.read(path)
        outer["limits"].update(max_geometry_checks=8192, max_scenario_vertices=128)
        outer["path_limits"].update(max_poses=16, max_geometry_checks=1024)
        outer["production_limits"]["max_pair_checks"] = 1024
        self.save(path, outer)
        self.rehash_outer()
        controls = {"limits": replace(ProcessFacilityPackageLimits(), max_geometry_checks=8192, max_scenario_vertices=128),
            "path_limits": replace(ProcessInstallationPathLimits(), max_poses=16, max_geometry_checks=1024),
            "production_limits": replace(ProductionLayoutLimits(), max_pair_checks=1024)}
        common = importlib.import_module("layout_configurator.process_review")
        with patch.object(self.document_api, "load_process_review_with_production_layout",
            wraps=self.review_api.load_process_review_with_production_layout) as fresh, \
                patch.object(common, "_evaluate_process_source", wraps=common._evaluate_process_source) as evaluate:
            report = self.check(**controls)
        self.assertTrue(report["ok"], report)
        self.assertTrue(fresh.called)
        self.assertTrue(evaluate.called)
        for call in evaluate.call_args_list:
            self.assertEqual((call.kwargs["limits"].max_geometry_checks, call.kwargs["limits"].max_scenario_vertices), (8192, 128))
        for call in fresh.call_args_list:
            self.assertEqual((call.kwargs["limits"].max_geometry_checks, call.kwargs["limits"].max_scenario_vertices), (8192, 128))
            self.assertEqual((call.kwargs["path_limits"].max_poses, call.kwargs["path_limits"].max_geometry_checks), (16, 1024))
            self.assertEqual(call.kwargs["production_limits"].max_pair_checks, 1024)
        self.assertEqual(tree_bytes(self.package / "process"), process_before)
        review = self.read(self.package / "process" / "review.json")
        self.assertEqual(review["reports"]["equipment_scenarios"]["evidence"]["limits"]["max_vertices"], child["limits"]["max_scenario_vertices"])
        self.assertEqual(review["installation_paths"]["reports"][0]["evidence"]["limits"]["max_poses"], child["path_limits"]["max_poses"])
        self.assertEqual(tree_bytes(self.original_base), self.base_snapshot)

    def test_true_window_policy_cross_binds_outer_child_and_actual_base_while_rectangular_true_flags_reject(self):
        manifest = self.export(include_windows=True)
        index = self.read(self.package / "process" / "index.json")
        self.assertIs(manifest["include_windows"], True)
        self.assertIs(index["include_windows"], True)
        self.assertTrue(manifest["base_projection_options"]["include_windows"])
        self.assertTrue(self.check()["ok"])
        original = self.source.read_bytes()
        for place in ("outer", "child", "base_policy"):
            with self.subTest(place=place):
                self.restore()
                path = self.package / "process" / "index.json" if place == "child" else self.package / "manifest.json"
                metadata = self.read(path)
                if place == "base_policy":
                    metadata["base_projection_options"]["include_windows"] = False
                else:
                    metadata["include_windows"] = False
                self.save(path, metadata)
                self.rehash_child()
                self.rehash_outer()
                self.check_without_geometry()
        with tempfile.TemporaryDirectory(dir=self.root) as temporary:
            self.original_base = Path(temporary) / "rectangular-base"
            self.base("rectangular")
            for flag in ("include_windows", "include_grid", "include_groups"):
                target = Path(temporary) / flag
                with self.subTest(flag=flag), patch.object(BuildingIR, "from_mapping") as constructor, self.assertRaises(ValueError):
                    self.api.export_process_facility_package_with_production_layout(self.source, self.original_base, target,
                        base_kind="rectangular", **{flag: True})
                constructor.assert_not_called()
                self.assertFalse(target.exists())
        self.assertEqual(self.source.read_bytes(), original)

    def test_whole_embedded_float_and_original_scalar_frame_mismatches_reject_without_stripping_or_rebinding_base(self):
        self.write_source()
        before = self.base()
        for mutation in ("embedded_float", "layout_scalar", "missing_clearance"):
            raw = copy.deepcopy(self.raw)
            if mutation == "embedded_float":
                raw["production_program"]["operations"][0]["measurements"]["cycle_time"]["value"] = 30.0
                raw["building"]["production_program"] = copy.deepcopy(raw["production_program"])
            elif mutation == "layout_scalar":
                raw["layout"]["rooms"]["entry"]["x"] = 0
            else:
                raw["equipment"][0].pop("clearance")
            sync_production_process(raw)
            self.write_source(raw)
            original = self.source.read_bytes()
            report = self.review_api.load_process_review_with_production_layout(self.source)
            evidence = report["additional_reports"]["production_layout"]["evidence"]
            self.assertEqual(evidence["source"]["building_sha256"], _sha(raw["building"]))
            if mutation == "embedded_float":
                self.assertNotEqual(evidence["source"]["building_sha256"], evidence["source"]["program_base_building_sha256"])
                self.assertIs(type(evidence["source_frames"]["building"]["production_program"]["operations"][0]["measurements"]["cycle_time"]["value"]), float)
            target = self.root / mutation
            with self.subTest(mutation=mutation), \
                    patch.object(BuildingIR, "from_mapping", side_effect=AssertionError("Pure original base binding must precede models")) as constructor, \
                    patch("layout_configurator.transfer_review.parse_layout_result", side_effect=AssertionError("Pure original base binding must precede layout parsing")) as layout_parser, \
                    self.assertRaises(ValueError):
                self.api.export_process_facility_package_with_production_layout(self.source, self.original_base, target)
            constructor.assert_not_called()
            layout_parser.assert_not_called()
            self.assertFalse(target.exists())
            self.assertEqual(self.source.read_bytes(), original)
            self.assertEqual(tree_bytes(self.original_base), before)

    def test_export_initial_source_and_base_admission_stay_bound_before_model_reading(self):
        self.write_source()
        base_before = self.base()
        source_before = self.source.read_bytes()
        replacement = self.source_bytes(forbidden_pressure_process_fixture())
        for mutation in ("source", "base"):
            self.write_source()
            restore_tree(self.original_base, base_before)
            with self.subTest(mutation=mutation):
                target = self.root / ("pre-admission-race-" + mutation)
                original_binding = self.legacy_api._binding
                changed = False

                def change_after_pure_binding(*args, **kwargs):
                    nonlocal changed
                    result = original_binding(*args, **kwargs)
                    if not changed:
                        changed = True
                        if mutation == "source":
                            self.source.write_bytes(replacement)
                        else:
                            path = self.original_base / "building.json"
                            path.write_bytes(path.read_bytes() + b" ")
                    return result

                with patch.object(self.legacy_api, "_binding", change_after_pure_binding), \
                        patch.object(BuildingIR, "from_mapping", side_effect=AssertionError("Pinned export inputs must reject before source models")) as constructor, \
                        patch("layout_configurator.transfer_review.parse_layout_result", side_effect=AssertionError("Pinned export inputs must reject before layout parsing")) as layout_parser, \
                        self.assertRaises(ValueError):
                    self.api.export_process_facility_package_with_production_layout(self.source, self.original_base, target)
                self.assertTrue(changed)
                constructor.assert_not_called()
                layout_parser.assert_not_called()
                self.assertFalse(target.exists())
                self.assertEqual(self.source.read_bytes(), replacement if mutation == "source" else source_before)
                expected_base = tuple((name, data + b" " if mutation == "base" and name == "building.json" else data)
                    for name, data in base_before)
                self.assertEqual(tree_bytes(self.original_base), expected_base)
                self.write_source()
                restore_tree(self.original_base, base_before)
        self.write_source()
        restore_tree(self.original_base, base_before)
        self.assertEqual(self.source.read_bytes(), source_before)
        self.assertEqual(tree_bytes(self.original_base), base_before)

    def test_old_versions_defaults_and_child_source_publication_races_preserve_originals_and_existing_work(self):
        self.write_source()
        before = self.base()
        original = self.source.read_bytes()
        legacy_raw = copy.deepcopy(self.raw)
        legacy_raw.update(version="0.1")
        legacy_raw.pop("installation_paths")
        legacy_source, legacy_target, path_target = self.root / "legacy.json", self.root / "old-one", self.root / "old-two"
        legacy_source.write_bytes(self.source_bytes(legacy_raw))
        self.legacy_api.export_process_facility_package(legacy_source, self.original_base, legacy_target)
        self.path_api.export_process_facility_package_with_paths(self.source, self.original_base, path_target)
        old_one = self.legacy_api.validate_process_facility_package(legacy_source, self.original_base, legacy_target)
        old_two = self.path_api.validate_process_facility_package_with_paths(self.source, self.original_base, path_target)
        old_bytes = (tree_bytes(legacy_target), tree_bytes(path_target))
        self.assertTrue(old_one["ok"])
        self.assertTrue(old_two["ok"])
        self.assertEqual(len(asdict(ProcessFacilityPackageLimits())), 59)
        self.assertEqual(len(asdict(ProcessDocumentationLimits())), 48)
        with self.assertRaises(ValueError):
            self.api.export_process_facility_package_with_production_layout(legacy_source, self.original_base, self.root / "wrong-old-source")
        self.assertFalse((self.root / "wrong-old-source").exists())
        self.export()
        self.assertEqual(self.legacy_api.validate_process_facility_package(legacy_source, self.original_base, legacy_target), old_one)
        self.assertEqual(self.path_api.validate_process_facility_package_with_paths(self.source, self.original_base, path_target), old_two)
        self.assertEqual((tree_bytes(legacy_target), tree_bytes(path_target)), old_bytes)
        for version, target in (("0.1", legacy_target), ("0.2", path_target)):
            metadata = self.read(target / "manifest.json")
            self.assertEqual(metadata["version"], version)
            self.assertEqual(metadata["limits"], asdict(ProcessFacilityPackageLimits()))
            self.assertNotIn("production_limits", metadata)
        self.assertFalse(self.path_api.validate_process_facility_package_with_paths(self.source, self.original_base, self.package)["ok"])
        original_export = self.api.export_process_documentation_with_production_layout
        for mutation in ("source", "base"):
            with self.subTest(mutation=mutation):
                target = self.root / ("raced-" + mutation)

                def change_after_child(*args, **kwargs):
                    result = original_export(*args, **kwargs)
                    path = self.source if mutation == "source" else self.original_base / "building.json"
                    path.write_bytes(path.read_bytes() + b" ")
                    return result

                with patch.object(self.api, "export_process_documentation_with_production_layout", change_after_child), self.assertRaises(ValueError):
                    self.api.export_process_facility_package_with_production_layout(self.source, self.original_base, target)
                self.assertFalse(target.exists())
                self.write_source()
                restore_tree(self.original_base, before)
        existing = self.root / "existing-user-work"
        existing.mkdir()
        (existing / "keep.txt").write_bytes(b"Existing user work")
        for target in (existing, self.source, self.original_base, self.original_base / "nested"):
            with self.subTest(target=target), self.assertRaises((FileExistsError, ValueError)):
                self.api.export_process_facility_package_with_production_layout(self.source, self.original_base, target)
        self.assertEqual((existing / "keep.txt").read_bytes(), b"Existing user work")
        original_publish = self.api._publish_directory
        raced = self.root / "raced-native-destination"

        def race(stage, target):
            Path(target).mkdir()
            (Path(target) / "keep.txt").write_bytes(b"Raced user work")
            return original_publish(stage, target)

        with patch.object(self.api, "_publish_directory", race), self.assertRaises((FileExistsError, ValueError)):
            self.api.export_process_facility_package_with_production_layout(self.source, self.original_base, raced)
        self.assertEqual({p.name for p in raced.iterdir()}, {"keep.txt"})
        self.assertEqual((raced / "keep.txt").read_bytes(), b"Raced user work")
        self.assertEqual(tree_bytes(self.original_base), before)
        self.assertEqual(self.source.read_bytes(), original)


if __name__ == "__main__":
    unittest.main()

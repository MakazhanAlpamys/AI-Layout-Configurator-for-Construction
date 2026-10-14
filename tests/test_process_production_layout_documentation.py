"""Actual version 0.3 PDF evidence stays bound to the original process revision.

Pristine byte copies avoid redundant rendering. Every physical forgery and
delivery assertion still invokes fresh persisted source and PDF readback.
"""

import copy
from contextlib import ExitStack
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

from layout_configurator.building import BuildingIR
from layout_configurator.process_documentation import ProcessDocumentationLimits
from layout_configurator.process_installation_review import ProcessInstallationPathLimits
from layout_configurator.production_layout_review import ProductionLayoutLimits
from test_process_documentation import actual_painted_paths
import test_process_installation_documentation as _path_tests
from test_process_installation_review import add_declared_pressure_cycle
from test_process_production_layout_review import (
    production_process_fixture, separated_production_rooms, sync_production_process,
)
from test_process_review import _COMPONENTS
from test_transfer_program import _sha


_INVENTORY = {"source.json", "review.json", "documentation.pdf", "index.json", "qa.json"}
_INDEX_KEYS = {"format", "version", "status", "ok", "review_status", "identity", "review_source", "files",
    "pages", "projection", "full_facility_validation", "engineering_acceptance", "authenticated_approval",
    "scope", "limits", "path_limits", "production_limits", "include_windows"}


def delivery_nodes(value):
    """Count original JSON occurrences, including dictionary keys, independently."""
    if type(value) is dict:
        return 1 + sum(delivery_nodes(key) + delivery_nodes(child) for key, child in value.items())
    if type(value) is list:
        return 1 + sum(delivery_nodes(child) for child in value)
    return 1


class ProcessProductionLayoutDocumentationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._pristine = {}

    @classmethod
    def tearDownClass(cls):
        cls._pristine.clear()

    def setUp(self):
        try:
            self.api = importlib.import_module("layout_configurator.process_production_layout_documentation")
        except ModuleNotFoundError as exc:
            self.fail(f"The opted production-requirement PDF contract is missing: {exc}")
        self.assertTrue(callable(getattr(self.api, "export_process_documentation_with_production_layout", None)))
        self.assertTrue(callable(getattr(self.api, "validate_process_documentation_with_production_layout", None)))
        self.review_api = importlib.import_module("layout_configurator.process_production_layout_review")
        self.legacy = importlib.import_module("layout_configurator.process_documentation")
        self.paths = importlib.import_module("layout_configurator.process_installation_documentation")
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.source = self.root / "original-production-process.json"
        self.destination = self.root / "documents"
        self.raw = production_process_fixture()

    read = _path_tests.ProcessInstallationDocumentationTests.read
    save = _path_tests.ProcessInstallationDocumentationTests.save
    restore = _path_tests.ProcessInstallationDocumentationTests.restore
    rebind_hashes = _path_tests.ProcessInstallationDocumentationTests.rebind_hashes
    edit_role = _path_tests.ProcessInstallationDocumentationTests.edit_role
    assert_rectangle = _path_tests.ProcessInstallationDocumentationTests.assert_rectangle

    def source_bytes(self, raw=None):
        return (json.dumps(self.raw if raw is None else raw, ensure_ascii=False,
            allow_nan=False, indent=2) + "\r\n").encode("utf-8")

    def write_source(self):
        self.source.write_bytes(self.source_bytes())

    def export(self, raw=None, *, include_windows=False):
        if raw is not None:
            self.raw = copy.deepcopy(raw)
        self.write_source()
        original = self.source.read_bytes()
        key = (original, include_windows)
        if key not in self._pristine:
            with tempfile.TemporaryDirectory() as temporary:
                folder = Path(temporary)
                source = folder / "original.json"
                source.write_bytes(original)
                target = folder / "documents"
                with patch("ortools.sat.python.cp_model.CpSolver.solve",
                    side_effect=AssertionError("Documentation may not generate a layout")):
                    self.api.export_process_documentation_with_production_layout(
                        source, target, include_windows=include_windows)
                self._pristine[key] = tuple((p.name, p.read_bytes()) for p in sorted(target.iterdir()))
        self.destination.mkdir()
        for name, data in self._pristine[key]:
            (self.destination / name).write_bytes(data)
        self.pristine = tuple((p.name, p.read_bytes()) for p in sorted(self.destination.iterdir()))
        return self.read("index.json")

    def check(self, **options):
        with patch("ortools.sat.python.cp_model.CpSolver.solve", side_effect=AssertionError("Readback cannot solve a layout")):
            report = self.api.validate_process_documentation_with_production_layout(self.source, self.destination, **options)
        self.assertIs(type(report), dict)
        return report

    def identity(self):
        return {"drawing_revision": self.raw["drawing_revision"], "variant": self.raw["variant"],
            "component_sha256": {name: _sha(self.raw[name]) for name in _COMPONENTS}}

    def production(self):
        return self.read("review.json")["additional_reports"]["production_layout"]["evidence"]

    def requirement_pages(self, *, identifier=None):
        return [row["page_number"] - 1 for row in self.read("index.json")["pages"]
            if row["kind"] == "production_requirement" and (identifier is None or row["source_id"] == identifier)]

    def production_pages(self):
        return [row["page_number"] - 1 for row in self.read("index.json")["pages"]
            if row["kind"] in ("production_layout", "production_requirement")]

    def pdf_text(self, *, pages=None):
        reader = PdfReader(self.destination / "documentation.pdf")
        pages = range(len(reader.pages)) if pages is None else pages
        return "\n".join(reader.pages[number].extract_text() for number in pages)

    def replace_printed(self, original, replacement, *, pages=None):
        path = self.destination / "documentation.pdf"
        reader = PdfReader(path)
        writer = PdfWriter()
        writer.clone_document_from_reader(reader)
        changed = False
        for number in (self.production_pages() if pages is None else pages):
            stream = ContentStream(reader.pages[number].get_contents(), reader)
            for values, operator in stream.operations:
                if operator == b"Tj" and original in str(values[0]):
                    values[0] = TextStringObject(str(values[0]).replace(original, replacement))
                    writer.pages[number][NameObject("/Contents")] = writer._add_object(stream)
                    changed = True
                    break
            if changed:
                break
        self.assertTrue(changed, f"Original production appendix text missing: {original}")
        with path.open("wb") as output:
            writer.write(output)
        self.rebind_hashes("pdf")

    def check_without_geometry(self, **options):
        with ExitStack() as stack:
            spies = [stack.enter_context(patch(target, side_effect=AssertionError("Saved controls must precede fresh geometry")))
                for target in ("layout_configurator.building.BuildingIR.from_mapping",
                    "layout_configurator.transfer_review.parse_layout_result", "layout_configurator.orthogonal.Polygon",
                    "layout_configurator.result_geometry.box", "layout_configurator.equipment_installation_path.box")]
            report = self.check(**options)
        self.assertFalse(report["ok"], report)
        for spy in spies:
            spy.assert_not_called()
        return report

    def assert_draft(self, value):
        self.assertEqual(value["full_facility_validation"], {"status": "UNKNOWN", "ok": False})
        self.assertEqual(value["engineering_acceptance"], "PENDING")
        self.assertEqual(value["authenticated_approval"], "UNKNOWN")

    def gap_pressure_unknown_fixture(self):
        raw = copy.deepcopy(self.raw)
        separated_production_rooms(raw)
        add_declared_pressure_cycle(raw)
        raw["production_program"]["sources"][0]["usage_rights"]["state"] = "UNKNOWN"
        raw["production_program"]["requirements"][0]["basis"] = "assumption"
        raw["installation_paths"][0]["exterior_staging"] = None
        sync_production_process(raw)
        return raw

    def test_complete_original_bound_five_file_pdf_keeps_fresh_requirement_evidence_and_actual_path_geometry(self):
        before = self.source_bytes()
        index = self.export()
        review = self.read("review.json")
        evidence = self.production()
        requirement = self.raw["production_program"]["requirements"][0]
        row = evidence["requirements"][0]
        self.assertEqual({p.name for p in self.destination.iterdir()}, _INVENTORY)
        self.assertEqual(set(index), _INDEX_KEYS)
        self.assertEqual((index["version"], index["status"], index["ok"], index["include_windows"]), ("0.3", "DRAFT", False, False))
        self.assertEqual(index["limits"], asdict(ProcessDocumentationLimits()))
        self.assertEqual(index["path_limits"], asdict(ProcessInstallationPathLimits()))
        self.assertEqual(index["production_limits"], asdict(ProductionLayoutLimits()))
        self.assertEqual(index["identity"], self.identity())
        self.assertEqual(review["additional_reports"]["production_layout"]["identity"], self.identity())
        self.assertEqual(review["source_frames"], {name: self.raw[name] for name in _COMPONENTS})
        self.assertEqual((evidence["binding_status"], evidence["geometry_status"]), ("PASS", "PASS"))
        self.assertEqual((row["maximum_contiguous_mm"], row["required_contiguous_mm"], row["overlap_area_mm2"]), (6000.0, 1600.0, 0.0))
        self.assertEqual(row["requirement_sha256"], _sha(requirement))
        self.assertEqual(index["review_source"]["file_sha256"], hashlib.sha256(before).hexdigest())
        self.assertEqual(self.source.read_bytes(), before)
        self.assertEqual((self.destination / "source.json").read_bytes(), before)
        self.assertEqual(set(review["reports"]), {"production_program", "transfers", "environment", "library", "equipment_scenarios"})
        self.assertEqual(set(review["schedules"]), {"rooms", "operations", "equipment", "transfers", "clean_zones", "ventilation_zones", "pressure", "scenarios"})
        self.assertTrue(self.check()["ok"])
        self.assert_draft(index)
        text = self.pdf_text(pages=self.production_pages())
        normalized = "".join(text.split())
        for value in (requirement["id"], requirement["operation_id"], requirement["source_id"], requirement["reason"],
            requirement["basis"], requirement["room_a"], requirement["room_b"], _sha(requirement), row["source_sha256"],
            "maximum_contiguous_mm", "required_contiguous_mm", "overlap_area_mm2", "PERMITTED",
            "Production geometry status: PASS", "Production binding status: PASS", "Production provenance status: PASS"):
            self.assertIn("".join(value.split()), normalized)
        self.assertEqual({r["source_id"] for r in index["pages"] if r["kind"] == "production_requirement"}, {requirement["id"]})
        painted = actual_painted_paths((self.destination / "documentation.pdf").read_bytes(), index["projection"])
        request_sha = _sha(self.raw["installation_paths"][0])
        body = unary_union([Polygon(points) for points in painted[("installation_pose_body", request_sha + ":pose:0")]])
        sweep = unary_union([Polygon(points) for points in painted[("installation_sweep_body", request_sha + ":segment:0")]])
        self.assert_rectangle(body, -1200, 2600, 1000, 800)
        self.assert_rectangle(sweep, -1200, 2600, 2700, 800)
        for number, page in enumerate(PdfReader(self.destination / "documentation.pdf").pages):
            self.assertIn(index["pages"][number]["page_id"], page.extract_text())
            self.assertIn(hashlib.sha256(before).hexdigest(), page.extract_text())
            self.assertFalse(page.images)

    def test_actual_required_gap_and_pressure_fail_survive_unknown_provenance_and_missing_staging_beside_artifact_pass(self):
        index = self.export(self.gap_pressure_unknown_fixture())
        review = self.read("review.json")
        evidence = self.production()
        self.assertEqual((index["review_status"], review["status"], evidence["geometry_status"]), ("FAIL", "FAIL", "FAIL"))
        self.assertEqual(evidence["provenance_status"], "UNKNOWN")
        self.assertEqual(evidence["requirements"][0]["maximum_contiguous_mm"], 0)
        self.assertEqual(evidence["requirements"][0]["required_contiguous_mm"], 1600.0)
        self.assertEqual(evidence["requirements"][0]["status"], "FAIL")
        self.assertEqual(review["reports"]["environment"]["evidence"]["pressure_declared_status"], "FAIL")
        self.assertIn("missing_exterior_staging", {r["code"] for r in review["installation_paths"]["reports"][0]["evidence"]["issues"]})
        self.assertTrue(self.check()["ok"], "Artifact consistency does not approve a conflicting production concept")
        self.assertEqual((index["status"], index["ok"], review["ok"]), ("DRAFT", False, False))
        text = "".join(self.pdf_text(pages=self.production_pages()).split())
        for token in ("required_room_adjacency_failed", "Production geometry status: FAIL",
                      "Production provenance status: UNKNOWN", "Requirement status: FAIL", "assumption", "UNKNOWN"):
            self.assertIn("".join(token.split()), text)
        self.assert_draft(index)

    def test_whole_embedded_program_and_float_scalar_keep_raw_hashes_and_separate_base_binding(self):
        raw = copy.deepcopy(self.raw)
        original_program_sha = _sha(raw["production_program"])
        raw["production_program"]["operations"][0]["measurements"]["cycle_time"]["value"] = 30.0
        raw["building"]["production_program"] = copy.deepcopy(raw["production_program"])
        sync_production_process(raw)
        original = self.source_bytes(raw)
        index = self.export(raw)
        evidence = self.production()
        source = evidence["source"]
        self.assertEqual(source["building_sha256"], _sha(raw["building"]))
        self.assertEqual(source["production_program_sha256"], _sha(raw["production_program"]))
        self.assertNotEqual(source["production_program_sha256"], original_program_sha)
        self.assertEqual(source["program_base_building_sha256"], raw["production_program"]["building_sha256"])
        self.assertNotEqual(source["building_sha256"], source["program_base_building_sha256"])
        self.assertEqual(evidence["source_frames"]["building"]["production_program"], raw["production_program"])
        self.assertIs(type(evidence["source_frames"]["production_program"]["operations"][0]["measurements"]["cycle_time"]["value"]), float)
        self.assertEqual((self.destination / "source.json").read_bytes(), original)
        self.assertEqual(self.source.read_bytes(), original)
        self.assertEqual(index["identity"], self.identity())
        self.assertTrue(self.check()["ok"])
        for digest in source.values():
            self.assertIn(digest, "".join(self.pdf_text(pages=self.production_pages()).split()))

    def test_explicit_saved_window_choice_flows_into_fresh_review_without_changing_original_identity(self):
        original = self.source_bytes()
        index = self.export(include_windows=True)
        self.assertIs(index["include_windows"], True)
        self.assertEqual(index["identity"], self.identity())
        self.assertEqual((self.destination / "source.json").read_bytes(), original)
        common = importlib.import_module("layout_configurator.process_review")
        with patch.object(common, "_evaluate_process_source", wraps=common._evaluate_process_source) as evaluate:
            report = self.check()
        self.assertTrue(report["ok"])
        self.assertTrue(evaluate.called, "Validate the saved choice through actual fresh source evaluation")
        self.assertTrue(all(call.kwargs["include_windows"] is True for call in evaluate.call_args_list))
        self.assertIn("Includewindows:True", "".join(self.pdf_text(pages=self.production_pages()).split()))
        self.assertEqual(self.source.read_bytes(), original)

    def test_rehashed_new_appendix_omission_and_fake_witness_verdict_or_identity_cannot_authorize_pdf(self):
        self.export(self.gap_pressure_unknown_fixture())
        requirement = self.raw["production_program"]["requirements"][0]
        source_sha = self.raw["production_program"]["sources"][0]["sha256"]
        for original, replacement in (("Production geometry status: FAIL", "Production geometry status: PASS"),
            ("Requirement status: FAIL", "Requirement status: PASS"), (requirement["id"], "invented_requirement"),
            (_sha(requirement), "0" * 64), (source_sha, "1" * 64),
            ("maximum_contiguous_mm", "invented_contact_span")):
            with self.subTest(original=original):
                self.restore()
                self.replace_printed(original, replacement)
                self.assertFalse(self.check()["ok"], "Actual fresh production witness text must match beyond rebound hashes")
        self.restore()
        pages = self.requirement_pages(identifier=requirement["id"])
        self.assertTrue(pages)
        path = self.destination / "documentation.pdf"
        reader, writer = PdfReader(path), PdfWriter()
        writer.clone_document_from_reader(reader)
        del writer.pages[pages[-1]]
        with path.open("wb") as output:
            writer.write(output)
        self.rebind_hashes("pdf")
        self.assertFalse(self.check()["ok"], "Omitted original requirement page cannot be accepted after rehashing")

    def test_extractable_new_requirement_text_must_remain_visible_on_its_actual_page(self):
        self.export()
        page_number = self.requirement_pages(identifier="entry_work_contact")[0]
        for mutation in ("off_page", "invisible", "collapsed"):
            with self.subTest(mutation=mutation):
                self.restore()
                path = self.destination / "documentation.pdf"
                reader, writer = PdfReader(path), PdfWriter()
                writer.clone_document_from_reader(reader)
                stream = ContentStream(reader.pages[page_number].get_contents(), reader)
                if mutation == "off_page":
                    stream.operations = [([FloatObject(v) for v in (1, 0, 0, 1, -20000, 0)], b"cm"), *stream.operations]
                elif mutation == "invisible":
                    modified = []
                    for operation in stream.operations:
                        modified.append(operation)
                        if operation[1] == b"BT":
                            modified.append(([FloatObject(3)], b"Tr"))
                    stream.operations = modified
                else:
                    changed = False
                    for values, operator in stream.operations:
                        if operator == b"Tm":
                            values[:4] = [FloatObject(1), FloatObject(0), FloatObject(1), FloatObject(0)]
                            changed = True
                    self.assertTrue(changed)
                writer.pages[page_number][NameObject("/Contents")] = writer._add_object(stream)
                with path.open("wb") as output:
                    writer.write(output)
                self.rebind_hashes("pdf")
                self.assertIn("entry_work_contact", self.pdf_text(), "The visibility forgery must retain extractable source text")
                self.assertFalse(self.check()["ok"], "Extractable but invisible, collapsed or off-page evidence is not a document")

    def test_original_requirement_inventory_is_complete_and_compact_evidence_is_rejected_before_publication(self):
        raw = copy.deepcopy(self.raw)
        template = raw["production_program"]["requirements"][0]
        raw["production_program"]["requirements"] = []
        for number in range(6):
            row = copy.deepcopy(template)
            row.update(id=f"original_contact_{number:02d}", reason=f"Original supplied handover requirement {number:02d}; no throughput or transfer permission is inferred.")
            raw["production_program"]["requirements"].append(row)
        sync_production_process(raw)
        index = self.export(raw)
        evidence = self.production()
        self.assertEqual(len(evidence["requirements"]), 6)
        self.assertEqual({r["source_id"] for r in index["pages"] if r["kind"] == "production_requirement"},
            {r["id"] for r in raw["production_program"]["requirements"]})
        text = "".join(self.pdf_text(pages=self.production_pages()).split())
        for row in raw["production_program"]["requirements"]:
            self.assertIn(row["id"], text)
            self.assertIn(_sha(row), text)
        self.assertTrue(self.check()["ok"])
        target = self.root / "incomplete-production-evidence"
        with patch("reportlab.pdfgen.canvas.Canvas.save") as saved, self.assertRaises(ValueError):
            self.api.export_process_documentation_with_production_layout(self.source, target,
                production_limits=replace(ProductionLayoutLimits(), max_report_bytes=4096))
        saved.assert_not_called()
        self.assertFalse(target.exists(), "Omitted standalone requirement rows must not become a complete-looking appendix")
        self.assertEqual(self.source.read_bytes(), self.source_bytes())

    def test_closed_saved_controls_and_all_effective_minimums_precede_fresh_source_constructors(self):
        self.export()
        for mutation in ("missing", "unknown", "bool_limit", "relaxed", "version", "window_type"):
            with self.subTest(mutation=mutation):
                self.restore()
                index = self.read("index.json")
                if mutation == "missing":
                    index.pop("production_limits")
                elif mutation == "unknown":
                    index["production_limits"]["approved"] = True
                elif mutation == "bool_limit":
                    index["production_limits"]["max_pair_checks"] = True
                elif mutation == "relaxed":
                    index["production_limits"]["max_pair_checks"] += 1
                elif mutation == "version":
                    index["version"] = "0.2"
                else:
                    index["include_windows"] = "false"
                self.save("index.json", index)
                self.rebind_hashes()
                self.check_without_geometry()
        for section, field, value in (("limits", "max_input_bytes", 100), ("limits", "max_geometry_checks", 6000),
            ("limits", "max_scenario_vertices", 66), ("path_limits", "max_total_poses", 2),
            ("production_limits", "max_pair_checks", 111), ("production_limits", "max_vertices", 11)):
            with self.subTest(section=section, field=field):
                self.restore()
                index = self.read("index.json")
                index[section][field] = value
                self.save("index.json", index)
                self.rebind_hashes()
                self.check_without_geometry()
        self.restore()
        for options in ({"limits": replace(ProcessDocumentationLimits(), max_geometry_checks=6000)},
            {"path_limits": replace(ProcessInstallationPathLimits(), max_total_poses=2)},
            {"production_limits": replace(ProductionLayoutLimits(), max_pair_checks=111)}):
            with self.subTest(options=options):
                self.check_without_geometry(**options)
        self.assertTrue(self.check(production_limits=replace(ProductionLayoutLimits(), max_pair_checks=65536))["ok"],
            "Different valid fitting caller limits use their minimum instead of demanding equality")

    def test_fitting_stricter_common_and_path_controls_preserve_authored_documents_without_relaxed_readback(self):
        index = self.export()
        tighter_common = replace(ProcessDocumentationLimits(), max_geometry_checks=8192, max_scenario_vertices=128)
        tighter_path = replace(ProcessInstallationPathLimits(), max_poses=16, max_geometry_checks=1024)
        tighter_production = replace(ProductionLayoutLimits(), max_pair_checks=1024)
        choices = ({"limits": tighter_common}, {"path_limits": tighter_path},
            {"limits": tighter_common, "path_limits": tighter_path, "production_limits": tighter_production})
        for options in choices:
            with self.subTest(options=options):
                self.restore()
                with patch.object(self.api, "load_process_review_with_production_layout",
                    wraps=self.review_api.load_process_review_with_production_layout) as fresh:
                    report = self.check(**options)
                self.assertTrue(report["ok"], report)
                self.assertEqual((report["status"], report["review_status"]), ("PASS", index["review_status"]))
                self.assertEqual(report["identity"], self.identity())
                self.assertGreater(fresh.call_count, 0, "Delivery must recompute the current original source")
                expected_common = options.get("limits", ProcessDocumentationLimits())
                expected_path = options.get("path_limits", ProcessInstallationPathLimits())
                expected_production = options.get("production_limits", ProductionLayoutLimits())
                for invocation in fresh.call_args_list:
                    actual = invocation.kwargs
                    self.assertEqual(actual["limits"].max_geometry_checks, expected_common.max_geometry_checks)
                    self.assertEqual(actual["limits"].max_scenario_vertices, expected_common.max_scenario_vertices)
                    self.assertEqual(asdict(actual["path_limits"]), asdict(expected_path))
                    self.assertEqual(asdict(actual["production_limits"]), asdict(expected_production))
                    self.assertIs(actual["include_windows"], False)
                self.assertEqual(tuple((p.name, p.read_bytes()) for p in sorted(self.destination.iterdir())), self.pristine,
                    "A fitting caller admission policy must not rewrite the authored artifact policy or PDF")
                self.assertEqual(self.source.read_bytes(), self.source_bytes())
                self.assert_draft(self.read("index.json"))

    def test_rehashed_authored_limit_annotations_actual_work_and_requirement_witness_still_require_fresh_evidence(self):
        self.export()
        options = {"limits": replace(ProcessDocumentationLimits(), max_geometry_checks=8192, max_scenario_vertices=128),
            "path_limits": replace(ProcessInstallationPathLimits(), max_poses=16, max_geometry_checks=1024)}
        original_work = self.read("review.json")["installation_paths"]["workload"]["path_geometry_upper_bound"]
        for mutation, value in (("scenario_limits", 128), ("leaf_limits", 16),
            ("shared_source_geometry_vertex_limit", 128), ("per_request_source_geometry_vertex_limit", 128),
            ("shared_process_geometry_limit", 8192), ("path_geometry_upper_bound", original_work + 1),
            ("requirement_witness", 1599)):
            with self.subTest(mutation=mutation):
                self.restore()
                review = self.read("review.json")
                if mutation == "scenario_limits":
                    review["reports"]["equipment_scenarios"]["evidence"]["limits"]["max_vertices"] = value
                elif mutation == "leaf_limits":
                    review["installation_paths"]["reports"][0]["evidence"]["limits"]["max_poses"] = value
                elif mutation == "requirement_witness":
                    review["additional_reports"]["production_layout"]["evidence"]["requirements"][0]["maximum_contiguous_mm"] = value
                else:
                    review["installation_paths"]["workload"][mutation] = value
                self.save("review.json", review)
                self.rebind_hashes("review")
                report = self.check(**options)
                self.assertFalse(report["ok"], "Coherent hashes cannot authorize forged authored annotations or fresh evidence")

    def test_fitting_stricter_controls_keep_known_production_and_pressure_failures_beside_artifact_pass(self):
        self.export(self.gap_pressure_unknown_fixture())
        options = {"limits": replace(ProcessDocumentationLimits(), max_geometry_checks=8192, max_scenario_vertices=128),
            "path_limits": replace(ProcessInstallationPathLimits(), max_poses=16, max_geometry_checks=1024),
            "production_limits": replace(ProductionLayoutLimits(), max_pair_checks=1024)}
        report = self.check(**options)
        self.assertTrue(report["ok"], report)
        self.assertEqual((report["status"], report["review_status"]), ("PASS", "FAIL"))
        saved = self.read("review.json")
        evidence = saved["additional_reports"]["production_layout"]["evidence"]
        self.assertEqual((evidence["geometry_status"], evidence["provenance_status"]), ("FAIL", "UNKNOWN"))
        self.assertEqual(evidence["requirements"][0]["status"], "FAIL")
        self.assertEqual(evidence["requirements"][0]["maximum_contiguous_mm"], 0)
        self.assertEqual(saved["reports"]["environment"]["evidence"]["pressure_declared_status"], "FAIL")
        self.assertEqual(tuple((p.name, p.read_bytes()) for p in sorted(self.destination.iterdir())), self.pristine)
        self.assert_draft(self.read("index.json"))

    def test_saved_combined_artifact_page_text_and_pdf_work_bounds_are_checked_before_fresh_geometry(self):
        self.export()
        total = sum(p.stat().st_size for p in self.destination.iterdir())
        self.assertGreater(len(PdfReader(self.destination / "documentation.pdf").pages), 1)
        for field, value in (("max_total_bytes", total - 1), ("max_pdf_bytes", 100), ("max_metadata_bytes", 100),
            ("max_pages", 1), ("max_text_chars", 1), ("max_delivery_nodes", 10),
            ("max_stream_bytes", 50), ("max_total_pdf_operations", 1)):
            with self.subTest(field=field):
                self.check_without_geometry(limits=replace(ProcessDocumentationLimits(), **{field: value}))
        payloads = [(self.destination / name).read_bytes() for name in sorted(_INVENTORY - {"documentation.pdf"})]
        counts = [delivery_nodes(json.loads(data)) for data in payloads]
        shared_nodes = max(counts) + 1
        self.assertGreater(sum(counts), shared_nodes)
        node_limits = replace(ProcessDocumentationLimits(), max_delivery_nodes=shared_nodes)
        for data in payloads:
            self.legacy._read_json(data, len(data), node_limits)
        self.check_without_geometry(limits=node_limits)
        reader = PdfReader(self.destination / "documentation.pdf")
        operations = [len(ContentStream(page.get_contents(), reader).operations) for page in reader.pages]
        shared_operations = max(operations) + 1
        self.assertGreater(sum(operations), shared_operations)
        self.check_without_geometry(limits=replace(ProcessDocumentationLimits(), max_total_pdf_operations=shared_operations))
        self.assertEqual(self.source.read_bytes(), self.source_bytes())
        self.assertTrue(self.check()["ok"])

    def test_changed_pinned_index_before_recapture_and_during_fresh_review_cannot_be_accepted(self):
        self.export()
        index_path = self.destination / "index.json"
        original_open = Path.open
        reads, changed = 0, False

        def change_on_recapture(path, *args, **kwargs):
            nonlocal reads, changed
            if path == index_path and args and args[0] == "rb":
                reads += 1
                if reads == 2:
                    changed = True
                    index = self.read("index.json")
                    index["production_limits"]["max_pair_checks"] = 65536
                    self.save("index.json", index)
                    self.rebind_hashes()
            return original_open(path, *args, **kwargs)

        with patch.object(Path, "open", change_on_recapture):
            self.check_without_geometry()
        self.assertTrue(changed, "Exercise an actual metadata admission recapture")
        self.assertGreaterEqual(reads, 2)
        self.restore()
        original_constructor = BuildingIR.from_mapping
        changed = False

        def change_during_geometry(*args, **kwargs):
            nonlocal changed
            if not changed:
                changed = True
                index = self.read("index.json")
                index["production_limits"]["max_pair_checks"] = 65536
                self.save("index.json", index)
                self.rebind_hashes()
            return original_constructor(*args, **kwargs)

        with patch.object(BuildingIR, "from_mapping", side_effect=change_during_geometry) as constructor:
            report = self.check()
        self.assertTrue(changed)
        self.assertTrue(constructor.called, "The second race must occur during real fresh admission")
        self.assertFalse(report["ok"], "Final readback must stay bound to the same initial index, not its latest replacement")

    def test_old_document_versions_defaults_and_saved_qa_remain_unchanged_and_wrong_source_versions_reject(self):
        defaults = asdict(ProcessDocumentationLimits())
        self.assertEqual(len(defaults), 48)
        self.write_source()
        old_source = copy.deepcopy(self.raw)
        old_source.update(version="0.1")
        old_source.pop("installation_paths")
        legacy_source = self.root / "legacy-source.json"
        legacy_source.write_bytes(self.source_bytes(old_source))
        legacy_target, path_target = self.root / "legacy-documents", self.root / "path-documents"
        self.legacy.export_process_documentation(legacy_source, legacy_target)
        self.paths.export_process_documentation_with_paths(self.source, path_target)
        old_one = self.legacy.validate_process_documentation(legacy_source, legacy_target)
        old_two = self.paths.validate_process_documentation_with_paths(self.source, path_target)
        old_indexes = [(folder / "index.json").read_bytes() for folder in (legacy_target, path_target)]
        index = self.export()
        self.assertEqual(index["version"], "0.3")
        self.assertEqual(self.legacy.validate_process_documentation(legacy_source, legacy_target), old_one)
        self.assertEqual(self.paths.validate_process_documentation_with_paths(self.source, path_target), old_two)
        self.assertTrue(old_one["ok"])
        self.assertTrue(old_two["ok"])
        for version, folder, saved in zip(("0.1", "0.2"), (legacy_target, path_target), old_indexes):
            self.assertEqual((folder / "index.json").read_bytes(), saved)
            old_index = json.loads(saved)
            self.assertEqual(old_index["version"], version)
            self.assertEqual(old_index["limits"], defaults)
            self.assertNotIn("production_limits", old_index)
        self.assertEqual(asdict(ProcessDocumentationLimits()), defaults)
        self.assertFalse(self.paths.validate_process_documentation_with_paths(self.source, self.destination)["ok"])
        with self.assertRaises(ValueError):
            self.api.export_process_documentation_with_production_layout(legacy_source, self.root / "wrong-version")
        self.assertFalse((self.root / "wrong-version").exists())

    def test_valid_empty_requirements_remain_unknown_and_malformed_or_missing_original_fields_publish_nothing(self):
        raw = copy.deepcopy(self.raw)
        raw["production_program"]["requirements"] = []
        sync_production_process(raw)
        index = self.export(raw)
        evidence = self.production()
        self.assertEqual((evidence["status"], evidence["geometry_status"], evidence["requirements"]), ("UNKNOWN", "UNKNOWN", []))
        self.assertEqual(index["review_status"], "UNKNOWN")
        self.assertTrue(self.check()["ok"])
        self.assert_draft(index)
        for missing in ("production_program", "requirement_source", "cached_verdict"):
            altered = production_process_fixture()
            if missing == "production_program":
                altered.pop("production_program")
            elif missing == "requirement_source":
                altered["production_program"]["requirements"][0].pop("source_id")
            else:
                altered["additional_reports"] = {"production_layout": {"status": "PASS"}}
            self.source.write_bytes(self.source_bytes(altered))
            target = self.root / missing
            with self.subTest(missing=missing), self.assertRaises(ValueError):
                self.api.export_process_documentation_with_production_layout(self.source, target)
            self.assertFalse(target.exists())

    def test_failed_producer_bounds_source_save_race_and_native_destination_race_preserve_original_user_work(self):
        from reportlab.pdfgen.canvas import Canvas
        self.write_source()
        before = self.source.read_bytes()
        for field, value in (("max_pages", 1), ("max_text_chars", 1), ("max_pdf_bytes", 100),
            ("max_total_pdf_operations", 1), ("max_overlay_vertices", 1)):
            target = self.root / field
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.api.export_process_documentation_with_production_layout(self.source, target,
                    limits=replace(ProcessDocumentationLimits(), **{field: value}))
            self.assertFalse(target.exists())
            self.assertEqual(self.source.read_bytes(), before)
        for windows in ("false", 1):
            with self.subTest(include_windows=windows), self.assertRaises(ValueError):
                self.api.export_process_documentation_with_production_layout(self.source, self.destination, include_windows=windows)
            self.assertFalse(self.destination.exists())
        existing = self.root / "existing-user-work"
        existing.mkdir()
        (existing / "keep.txt").write_bytes(b"Existing user work")
        for target in (existing, self.source):
            with self.subTest(target=target), self.assertRaises((FileExistsError, ValueError)):
                self.api.export_process_documentation_with_production_layout(self.source, target)
        self.assertEqual((existing / "keep.txt").read_bytes(), b"Existing user work")
        original_save = Canvas.save

        def change_source(document):
            original_save(document)
            self.source.write_bytes(before + b" ")

        with patch.object(Canvas, "save", change_source), self.assertRaises(ValueError):
            self.api.export_process_documentation_with_production_layout(self.source, self.destination)
        self.assertFalse(self.destination.exists())
        self.write_source()
        original_publish = self.api._publish_directory

        def race(stage, target):
            Path(target).mkdir()
            (Path(target) / "keep.txt").write_bytes(b"Raced user work")
            return original_publish(stage, target)

        with patch.object(self.api, "_publish_directory", race), self.assertRaises((FileExistsError, ValueError)):
            self.api.export_process_documentation_with_production_layout(self.source, self.destination)
        self.assertEqual({p.name for p in self.destination.iterdir()}, {"keep.txt"})
        self.assertEqual((self.destination / "keep.txt").read_bytes(), b"Raced user work")
        self.assertEqual(self.source.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()

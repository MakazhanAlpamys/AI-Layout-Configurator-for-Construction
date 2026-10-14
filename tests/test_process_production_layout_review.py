"""An opted original process revision includes fresh production room facts."""

import copy
from dataclasses import replace
import hashlib
import importlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from layout_configurator.building import BuildingIR
from layout_configurator.equipment import EquipmentLayoutResult, EquipmentPlacement
from layout_configurator.models import Rect
from layout_configurator.process_installation_review import (
    ProcessInstallationPathLimits, load_process_review_with_paths,
)
from layout_configurator.process_review import ProcessReviewLimits, load_process_review
from layout_configurator.production_layout_review import ProductionLayoutLimits, validate_production_program_layout
from layout_configurator.result_geometry import parse_layout_result
from test_process_installation_review import add_declared_pressure_cycle, installation_process_fixture
from test_process_review import _COMPONENTS, process_fixture, rebind
from test_transfer_program import _sha


def production_process_fixture():
    raw, _, _, _ = installation_process_fixture()
    program = raw["production_program"]
    source = program["sources"][0]
    excerpt = "Entry and work must be adjacent for the declared handover."
    content = source["content"] + excerpt + "\r\n"
    source.update(content=content, sha256=hashlib.sha256(content.encode("utf-8")).hexdigest())
    program["requirements"] = [{"id": "entry_work_contact", "operation_id": program["operations"][0]["id"],
        "kind": "required_room_adjacency", "room_a": "entry", "room_b": "work",
        "reason": "Supplied direct handover relation", "source_id": source["id"],
        "basis": "declared_fact", "excerpt": excerpt}]
    sync_production_process(raw)
    return raw


def sync_production_process(raw, *, canonical=True):
    """Bind explicitly edited original source and supplied path signatures."""
    program = raw["production_program"]
    base = {key: value for key, value in raw["building"].items() if key != "production_program"}
    program["building_sha256"] = _sha(base)
    if "production_program" in raw["building"]:
        raw["building"]["production_program"] = copy.deepcopy(program)
    rebind(raw, production_base=False)
    if canonical:
        building = BuildingIR.from_mapping(raw["building"], include_production_program="production_program" in raw["building"])
        layout = parse_layout_result(raw["layout"])
        equipment = EquipmentLayoutResult(tuple(EquipmentPlacement(row["equipment_id"], row["room_id"],
            Rect(row["x"], row["y"], row["width"], row["depth"]), row["rotated"]) for row in raw["equipment"]))
        canonical_equipment = equipment.to_dict(building)
        for row, derived in zip(raw["equipment"], canonical_equipment["equipment"]):
            if "clearance" in row:
                row["clearance"] = copy.deepcopy(derived["clearance"])
        signatures = {"building_sha256": _sha(building.to_dict()), "layout_sha256": _sha(layout.to_dict()),
            "equipment_sha256": _sha(canonical_equipment), "library_sha256": _sha(raw["library"])}
    else:
        # No constructor can normalize an intentionally unsupported raw frame
        # into source authority. The opted reader must stop before leaf checks.
        signatures = dict(raw["installation_paths"][0]["source"])
        signatures["building_sha256"] = _sha(raw["building"])
    for request in raw["installation_paths"]:
        request["source"] = copy.deepcopy(signatures)


def separated_production_rooms(raw):
    """A real 1,000 mm gap with unchanged overall bounds and declared areas."""
    raw["layout"]["rooms"]["work"].update(x=7000.0, width=5000.0, area_m2=30.0)
    room = next(row for row in raw["building"]["layout"]["rooms"] if row["id"] == "work")
    room.update(target_area=30.0, min_area=30.0, max_area=30.0)
    sync_production_process(raw)


class ProcessProductionLayoutReviewTests(unittest.TestCase):
    def setUp(self):
        try:
            self.api = importlib.import_module("layout_configurator.process_production_layout_review")
        except ModuleNotFoundError as exc:
            self.fail(f"The opted synchronized production layout review is missing: {exc}")
        self.assertTrue(callable(getattr(self.api, "load_process_review_with_production_layout", None)))
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "unchanged-original-process.json"
        self.raw = production_process_fixture()

    def write(self, raw):
        data = (json.dumps(raw, ensure_ascii=False, allow_nan=False, indent=2) + "\r\n").encode("utf-8")
        self.path.write_bytes(data)
        return data

    def load(self, raw=None, **options):
        self.write(self.raw if raw is None else raw)
        with patch("ortools.sat.python.cp_model.CpSolver.solve", side_effect=AssertionError("No process generation in source review")):
            report = self.api.load_process_review_with_production_layout(self.path, **options)
        self.assertIs(type(report), dict)
        return report

    def identity(self, raw=None):
        raw = self.raw if raw is None else raw
        return {"drawing_revision": raw["drawing_revision"], "variant": raw["variant"],
                "component_sha256": {name: _sha(raw[name]) for name in _COMPONENTS}}

    def production(self, report):
        return report["additional_reports"]["production_layout"]

    def assert_scoped(self, report):
        self.assertFalse(report["ok"])
        self.assertEqual(report["document_status"], "DRAFT")
        self.assertEqual(report["engineering_acceptance"], "PENDING")
        self.assertEqual(report["authenticated_approval"], "UNKNOWN")
        self.assertEqual(report["full_facility_validation"], {"status": "UNKNOWN", "ok": False})
        self.assertFalse(report["proven_building_infeasible"])

    def assert_whole_sum_stopped(self, raw, **options):
        original = self.write(raw)
        with patch.object(BuildingIR, "from_mapping", side_effect=AssertionError("Shared resource sum must precede constructors")), \
                patch("layout_configurator.orthogonal.Polygon", side_effect=AssertionError("Shared resource sum must precede any GEOS")), \
                patch("layout_configurator.result_geometry.box", side_effect=AssertionError("Shared resource sum must precede any GEOS")), \
                patch("layout_configurator.transfer_review.parse_layout_result", side_effect=AssertionError("No common geometry before whole admission")), \
                patch("layout_configurator.engineering_library._strict_json", side_effect=AssertionError("No nested decode before whole admission")), \
                patch("layout_configurator.equipment_installation_path.box", side_effect=AssertionError("No leaf geometry before whole admission")):
            report = self.api.load_process_review_with_production_layout(self.path, **options)
        self.assertEqual((report["status"], report["inventory_status"]), ("UNKNOWN", "NOT_EVALUATED"))
        self.assertIn("input_budget_exceeded", {row["code"] for row in report["issues"]})
        self.assertEqual(report["review_source"]["file_sha256"], hashlib.sha256(original).hexdigest())
        self.assertEqual(self.path.read_bytes(), original)
        self.assert_scoped(report)

    def test_fresh_additional_geometry_keeps_one_original_snapshot_raw_nine_identity_and_closed_common_inventories(self):
        original = self.write(self.raw)
        before = copy.deepcopy(self.raw)
        standalone = validate_production_program_layout(self.raw["production_program"],
            building_raw=self.raw["building"], layout_raw=self.raw["layout"])
        source_reads = []
        original_open = Path.open

        def observe_open(path, *args, **kwargs):
            if path == self.path:
                source_reads.append((args, kwargs))
            return original_open(path, *args, **kwargs)

        with patch.object(Path, "open", observe_open), \
                patch.object(Path, "write_bytes", side_effect=AssertionError("No rewritten source or stripped temporary file")), \
                patch.object(Path, "write_text", side_effect=AssertionError("No rewritten source or stripped temporary file")), \
                patch("ortools.sat.python.cp_model.CpSolver.solve", side_effect=AssertionError("No solver in an explicit review")):
            report = self.api.load_process_review_with_production_layout(self.path)
        self.assertEqual(len(source_reads), 1)
        self.assertEqual(report["identity"], self.identity())
        self.assertEqual(report["review_source"]["file_sha256"], hashlib.sha256(original).hexdigest())
        self.assertEqual(report["review_source"]["bytes"], len(original))
        self.assertEqual(report["source_frames"], {name: self.raw[name] for name in _COMPONENTS})
        self.assertEqual(set(report["reports"]), {"production_program", "transfers", "environment", "library", "equipment_scenarios"})
        self.assertEqual(set(report["schedules"]), {"rooms", "operations", "equipment", "transfers",
                                                  "clean_zones", "ventilation_zones", "pressure", "scenarios"})
        wrapper = self.production(report)
        self.assertEqual(wrapper["identity"], self.identity())
        self.assertEqual(wrapper["status"], standalone["status"])
        self.assertEqual(wrapper["evidence"]["source"], standalone["source"])
        self.assertEqual(wrapper["evidence"]["requirements"], standalone["requirements"])
        self.assertEqual(wrapper["evidence"]["geometry_status"], "PASS")
        self.assertEqual(report["installation_paths"]["reports"][0]["evidence"]["local_2d_path_status"], "PASS")
        self.assertEqual(report["status"], "UNKNOWN")
        self.assert_scoped(report)
        self.assertEqual(self.path.read_bytes(), original)
        wrapper["evidence"]["source_frames"]["production_program"]["sources"][0]["content"] = "Changed detached output"
        self.assertEqual(self.raw, before)
        self.assertEqual(self.load()["identity"], self.identity())

    def test_old_zero_one_zero_two_default_outputs_and_source_schemas_remain_unchanged(self):
        self.write(self.raw)
        old_path = load_process_review_with_paths(self.path)
        old_source = process_fixture()
        legacy_path = Path(self.directory.name) / "historical-zero-one.json"
        legacy_bytes = (json.dumps(old_source, ensure_ascii=False, indent=2) + "\r\n").encode("utf-8")
        legacy_path.write_bytes(legacy_bytes)
        old_legacy = load_process_review(legacy_path)
        self.load()
        self.write(self.raw)
        self.assertEqual(_sha(load_process_review_with_paths(self.path)), _sha(old_path))
        self.assertEqual(_sha(load_process_review(legacy_path)), _sha(old_legacy))
        self.assertNotIn("additional_reports", old_path)
        self.assertNotIn("additional_reports", old_legacy)
        self.assertEqual(legacy_path.read_bytes(), legacy_bytes)
        with self.assertRaises(ValueError):
            self.api.load_process_review_with_production_layout(legacy_path)
        for field in ("additional_reports", "production_layout", "saved_reports"):
            altered = copy.deepcopy(self.raw)
            altered[field] = {"status": "PASS"}
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.load(altered)

    def test_optional_raw_layout_fields_remain_distinct_from_canonical_path_source_signatures(self):
        canonical_layout_sha = self.raw["installation_paths"][0]["source"]["layout_sha256"]
        self.raw["layout"].pop("objective_value")
        for room in self.raw["layout"]["rooms"].values():
            room.pop("area_m2")
        sync_production_process(self.raw)
        self.assertEqual(self.raw["installation_paths"][0]["source"]["layout_sha256"], canonical_layout_sha)
        report = self.load()
        evidence = self.production(report)["evidence"]
        raw_layout_sha = report["identity"]["component_sha256"]["layout"]
        self.assertEqual(evidence["source"]["layout_sha256"], raw_layout_sha)
        self.assertEqual(evidence["source_frames"]["layout"], self.raw["layout"])
        self.assertNotEqual(raw_layout_sha, canonical_layout_sha)
        self.assertEqual(report["installation_paths"]["reports"][0]["evidence"]["source"]["layout_sha256"], canonical_layout_sha)
        self.assertEqual(evidence["geometry_status"], "PASS")

    def test_whole_embedded_integer_float_programs_keep_original_component_and_base_binding_identities(self):
        prior = None
        for value in (30, 30.0):
            raw = copy.deepcopy(self.raw)
            raw["production_program"]["operations"][0]["measurements"]["cycle_time"]["value"] = value
            raw["building"]["production_program"] = copy.deepcopy(raw["production_program"])
            sync_production_process(raw)
            report = self.load(raw)
            evidence = self.production(report)["evidence"]
            self.assertEqual(report["identity"], self.identity(raw))
            self.assertEqual(evidence["source"]["building_sha256"], _sha(raw["building"]))
            self.assertEqual(evidence["source"]["production_program_sha256"], _sha(raw["production_program"]))
            self.assertEqual(evidence["source"]["program_base_building_sha256"], raw["production_program"]["building_sha256"])
            self.assertNotEqual(evidence["source"]["building_sha256"], evidence["source"]["program_base_building_sha256"])
            self.assertEqual(evidence["source_frames"]["building"]["production_program"], raw["production_program"])
            self.assertIs(type(evidence["source_frames"]["production_program"]["operations"][0]["measurements"]["cycle_time"]["value"]), type(value))
            self.assertEqual(evidence["geometry_status"], "PASS")
            if prior is not None:
                self.assertNotEqual(prior["building_sha256"], evidence["source"]["building_sha256"])
                self.assertNotEqual(prior["production_program_sha256"], evidence["source"]["production_program_sha256"])
                self.assertEqual(prior["program_base_building_sha256"], evidence["source"]["program_base_building_sha256"])
            prior = evidence["source"]

    def test_real_adjacency_failure_survives_unknown_provenance_and_missing_staging(self):
        separated_production_rooms(self.raw)
        self.raw["production_program"]["sources"][0]["usage_rights"]["state"] = "UNKNOWN"
        self.raw["production_program"]["requirements"][0]["basis"] = "assumption"
        self.raw["installation_paths"][0]["exterior_staging"] = None
        sync_production_process(self.raw)
        report = self.load()
        evidence = self.production(report)["evidence"]
        self.assertEqual((report["status"], evidence["geometry_status"], evidence["provenance_status"]), ("FAIL", "FAIL", "UNKNOWN"))
        row = evidence["requirements"][0]
        self.assertEqual((row["requirement_id"], row["status"], row["maximum_contiguous_mm"]), ("entry_work_contact", "FAIL", 0))
        self.assertEqual(row["required_contiguous_mm"], 1600.0)
        self.assertIn("required_room_adjacency_failed", {issue["code"] for issue in evidence["issues"]})
        self.assertIsNone(report["installation_paths"]["source_requests"][0]["exterior_staging"])
        self.assert_scoped(report)

    def test_independent_pressure_and_path_body_failures_survive_an_unknown_additional_report(self):
        for kind in ("pressure", "body"):
            raw = copy.deepcopy(self.raw) if kind == "pressure" else production_process_fixture()
            if kind == "pressure":
                add_declared_pressure_cycle(raw)
            else:
                raw["equipment"][1]["y"] = 2700.0
            raw["production_program"]["sources"][0]["usage_rights"]["state"] = "UNKNOWN"
            raw["installation_paths"][0]["exterior_staging"] = None
            sync_production_process(raw)
            with self.subTest(kind=kind):
                report = self.load(raw)
                self.assertEqual(report["status"], "FAIL")
                self.assertEqual(self.production(report)["evidence"]["geometry_status"], "PASS")
                self.assertEqual(self.production(report)["status"], "UNKNOWN")
                if kind == "pressure":
                    self.assertEqual(report["reports"]["environment"]["evidence"]["pressure_declared_status"], "FAIL")
                else:
                    self.assertIn("swept_body_collision", {row["code"] for row in report["installation_paths"]["reports"][0]["evidence"]["issues"]})
                self.assert_scoped(report)

    def test_valid_raw_frame_outside_canonical_roundtrip_subset_is_unknown_without_rewriting_or_geometry(self):
        self.raw["building"]["layout"]["rooms"][0].pop("is_heated")
        sync_production_process(self.raw, canonical=False)
        original = self.write(self.raw)
        before = copy.deepcopy(self.raw)
        with patch("layout_configurator.orthogonal.Polygon", side_effect=AssertionError("No geometry for unsupported original frame")), \
                patch("layout_configurator.result_geometry.box", side_effect=AssertionError("No geometry for unsupported original frame")), \
                patch("layout_configurator.transfer_review.parse_layout_result", side_effect=AssertionError("No geometry after normalization refusal")), \
                patch("layout_configurator.equipment_installation_path.box", side_effect=AssertionError("No leaf geometry after normalization refusal")):
            report = self.api.load_process_review_with_production_layout(self.path)
        self.assertEqual((report["status"], report["inventory_status"]), ("UNKNOWN", "NOT_EVALUATED"))
        self.assertEqual(report["identity"], self.identity())
        self.assertEqual(report["review_source"]["file_sha256"], hashlib.sha256(original).hexdigest())
        evidence = self.production(report)["evidence"]
        self.assertEqual((evidence["status"], evidence["geometry_status"]), ("UNKNOWN", "NOT_EVALUATED"))
        self.assertTrue(any("unsupported" in row["code"] for row in report["issues"] + evidence["issues"]))
        self.assertEqual(self.path.read_bytes(), original)
        self.assertEqual(self.raw, before)
        self.assert_scoped(report)

    def test_individually_fitting_geometry_and_vertex_consumers_share_one_preconstructor_reservation(self):
        for kind, parent, production in (
            ("geometry", replace(ProcessReviewLimits(), max_geometry_checks=6000),
             replace(ProductionLayoutLimits(), max_pair_checks=6000)),
            ("vertices", replace(ProcessReviewLimits(), max_scenario_vertices=66),
             replace(ProductionLayoutLimits(), max_vertices=66))):
            with self.subTest(kind=kind):
                self.write(self.raw)
                path = load_process_review_with_paths(self.path, limits=parent)
                self.assertEqual(path["installation_paths"]["reports"][0]["evidence"]["local_2d_path_status"], "PASS")
                standalone = validate_production_program_layout(self.raw["production_program"],
                    building_raw=self.raw["building"], layout_raw=self.raw["layout"], limits=production)
                self.assertEqual(standalone["geometry_status"], "PASS")
                # Existing path pipeline uses 5,955 geometry units and the new
                # consumer 112: 6,000 fits either, but not their 6,067 sum.
                # The vertex reservations are 58 and 12; their 70 sum exceeds 66.
                self.assert_whole_sum_stopped(self.raw, limits=parent, production_limits=production)

    def test_stale_base_and_whole_embedded_mismatch_fail_before_an_unsupported_frame_can_be_unknown(self):
        for kind, code in (("base", "stale_building_binding"),
                           ("embedded", "embedded_production_program_mismatch")):
            raw = copy.deepcopy(self.raw)
            raw["building"]["layout"]["rooms"][0].pop("is_heated")
            sync_production_process(raw, canonical=False)
            if kind == "base":
                raw["production_program"]["building_sha256"] = "0" * 64
            else:
                raw["building"]["production_program"] = copy.deepcopy(raw["production_program"])
                raw["building"]["production_program"]["operations"][0]["measurements"]["cycle_time"]["value"] = 30.0
            rebind(raw, production_base=False)
            original = self.write(raw)
            with self.subTest(kind=kind), \
                    patch.object(BuildingIR, "from_mapping", side_effect=AssertionError("Pure stale binding precedes constructors")), \
                    patch("layout_configurator.transfer_review.parse_layout_result", side_effect=AssertionError("Pure stale binding precedes layout geometry")):
                report = self.api.load_process_review_with_production_layout(self.path)
                evidence = self.production(report)["evidence"]
                self.assertEqual((report["status"], evidence["status"], evidence["binding_status"]), ("FAIL", "FAIL", "FAIL"))
                self.assertEqual(evidence["geometry_status"], "NOT_EVALUATED")
                self.assertIn(code, {row["code"] for row in evidence["issues"]})
                self.assertEqual(report["identity"], self.identity(raw))
                self.assertEqual(evidence["source"]["building_sha256"], _sha(raw["building"]))
                self.assertEqual(evidence["source"]["production_program_sha256"], _sha(raw["production_program"]))
                self.assertEqual(self.path.read_bytes(), original)
                self.assert_scoped(report)

    def test_positive_orthogonal_area_underflow_stops_before_common_constructors_and_geometry(self):
        raw = copy.deepcopy(self.raw)
        extent = 1e-200
        raw["layout"].update(geometry_version="0.2", units="mm")
        raw["layout"]["rooms"]["work"] = {"kind": "orthogonal", "units": "mm", "vertices": [
            [0.0, 0.0], [extent, 0.0], [extent, extent], [0.0, extent]]}
        rebind(raw, production_base=False)
        original = self.write(raw)
        with patch.object(BuildingIR, "from_mapping", side_effect=AssertionError("Precision admission precedes constructors")), \
                patch("layout_configurator.orthogonal.Polygon", side_effect=AssertionError("Precision admission precedes GEOS")), \
                patch("layout_configurator.transfer_review.parse_layout_result", side_effect=AssertionError("No common geometry before precision admission")):
            report = self.api.load_process_review_with_production_layout(self.path)
        self.assertEqual((report["status"], report["inventory_status"]), ("UNKNOWN", "NOT_EVALUATED"))
        self.assertEqual(report["identity"], self.identity(raw))
        self.assertTrue(any("precision" in row["message"].lower() for row in report["issues"]))
        self.assertEqual(report["review_source"]["file_sha256"], hashlib.sha256(original).hexdigest())
        self.assertEqual(self.path.read_bytes(), original)
        self.assert_scoped(report)

    def test_new_snapshot_node_and_source_byte_work_cannot_receive_fresh_consumer_budgets(self):
        self.write(self.raw)
        old = load_process_review_with_paths(self.path)
        work = old["installation_paths"]["workload"]
        for kind in ("nodes", "bytes"):
            if kind == "nodes":
                cap = work["node_work_upper_bound"] + 1
                path_limits = replace(ProcessInstallationPathLimits(), max_node_work=cap)
                production = replace(ProductionLayoutLimits(), max_nodes=min(cap, ProductionLayoutLimits().max_nodes))
            else:
                cap = work["source_byte_work_upper_bound"] + 1
                path_limits = replace(ProcessInstallationPathLimits(), max_source_byte_work=cap)
                production = replace(ProductionLayoutLimits(),
                    max_total_source_bytes=min(cap, ProductionLayoutLimits().max_total_source_bytes))
            with self.subTest(kind=kind):
                self.write(self.raw)
                path = load_process_review_with_paths(self.path, path_limits=path_limits)
                self.assertEqual(path["installation_paths"]["reports"][0]["evidence"]["local_2d_path_status"], "PASS")
                standalone = validate_production_program_layout(self.raw["production_program"],
                    building_raw=self.raw["building"], layout_raw=self.raw["layout"], limits=production)
                self.assertEqual(standalone["geometry_status"], "PASS")
                self.assert_whole_sum_stopped(self.raw, path_limits=path_limits, production_limits=production)

    def test_capped_delivery_retains_original_production_requirement_failure_beside_earlier_common_failures(self):
        separated_production_rooms(self.raw)
        add_declared_pressure_cycle(self.raw)
        self.raw["production_program"]["sources"][0]["usage_rights"]["state"] = "UNKNOWN"
        self.raw["installation_paths"][0]["exterior_staging"] = None
        sync_production_process(self.raw)
        complete = self.load()
        self.assertEqual(complete["reports"]["environment"]["evidence"]["pressure_declared_status"], "FAIL")
        self.assertEqual(self.production(complete)["evidence"]["geometry_status"], "FAIL")
        compact = self.load(limits=replace(ProcessReviewLimits(), max_findings=1, max_report_bytes=4096),
            production_limits=replace(ProductionLayoutLimits(), max_findings=1, max_report_bytes=4096))
        self.assertEqual((compact["status"], compact["inventory_status"]), ("FAIL", "INCOMPLETE"))
        self.assertEqual(compact["identity"], self.identity())
        retained = next(row for row in compact["retained_failures"] if row["component"] == "production_layout")
        self.assertEqual(retained["identity"], self.identity())
        failure = retained["failure"]
        requirement = self.raw["production_program"]["requirements"][0]
        self.assertEqual((failure["code"], failure["requirement_id"], failure["operation_id"], failure["source_id"]),
                         ("required_room_adjacency_failed", "entry_work_contact", requirement["operation_id"], requirement["source_id"]))
        self.assertEqual(failure["requirement_sha256"], _sha(requirement))
        self.assertEqual(failure["source_sha256"], self.raw["production_program"]["sources"][0]["sha256"])
        self.assertEqual((failure["room_a"], failure["room_b"], failure["maximum_contiguous_mm"],
                          failure["required_contiguous_mm"]), ("entry", "work", 0, 1600.0))
        self.assertGreater(compact["failure_inventory"]["omitted"], 0)
        self.assertLessEqual(len(json.dumps(compact, ensure_ascii=False, allow_nan=False).encode("utf-8")), 4096)
        self.assert_scoped(compact)

    def test_full_original_snapshot_and_embedded_decode_utf_are_reserved_beyond_source_content(self):
        for kind in ("snapshot", "embedded"):
            raw = copy.deepcopy(self.raw)
            if kind == "embedded":
                raw["building"]["production_program"] = copy.deepcopy(raw["production_program"])
                sync_production_process(raw)
            self.write(raw)
            old = load_process_review_with_paths(self.path)
            snapshot = [raw["production_program"], raw["building"], raw["layout"]]
            snapshot_bytes = len(json.dumps(snapshot, ensure_ascii=False, allow_nan=False,
                                            separators=(",", ":")).encode("utf-8"))
            decode_bytes = 0 if kind == "snapshot" else len(json.dumps(raw["production_program"],
                ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8"))
            contents = sum(len(row["content"].encode("utf-8")) for row in raw["production_program"]["sources"])
            self.assertGreater(snapshot_bytes, contents)
            if kind == "embedded":
                self.assertGreater(decode_bytes, contents)
            cap = old["installation_paths"]["workload"]["source_byte_work_upper_bound"] + snapshot_bytes + decode_bytes - 1
            controls = replace(ProcessInstallationPathLimits(), max_source_byte_work=cap)
            with self.subTest(kind=kind):
                self.assert_whole_sum_stopped(raw, path_limits=controls)

    def test_known_raw_binding_failure_is_not_hidden_by_independent_positive_area_precision(self):
        for kind, code in (("base", "stale_building_binding"),
                           ("embedded", "embedded_production_program_mismatch")):
            raw = copy.deepcopy(self.raw)
            if kind == "base":
                raw["production_program"]["building_sha256"] = "0" * 64
            else:
                raw["building"]["production_program"] = copy.deepcopy(raw["production_program"])
                raw["building"]["production_program"]["operations"][0]["measurements"]["cycle_time"]["value"] = 30.0
            raw["layout"].update(geometry_version="0.2", units="mm")
            raw["layout"]["rooms"]["work"] = {"kind": "orthogonal", "units": "mm", "vertices": [
                [0., 0.], [1e-200, 0.], [1e-200, 1e-200], [0., 1e-200]]}
            rebind(raw, production_base=False)
            original = self.write(raw)
            with self.subTest(kind=kind), \
                    patch.object(BuildingIR, "from_mapping", side_effect=AssertionError("Pure binding failure precedes constructors")), \
                    patch("layout_configurator.transfer_review.parse_layout_result", side_effect=AssertionError("Pure binding failure precedes geometry")):
                report = self.api.load_process_review_with_production_layout(self.path)
                evidence = self.production(report)["evidence"]
                self.assertEqual((report["status"], evidence["status"], evidence["binding_status"]), ("FAIL", "FAIL", "FAIL"))
                self.assertEqual(evidence["geometry_status"], "NOT_EVALUATED")
                self.assertIn(code, {row["code"] for row in evidence["issues"]})
                self.assertEqual(report["identity"], self.identity(raw))
                self.assertEqual(self.path.read_bytes(), original)
                self.assert_scoped(report)


if __name__ == "__main__":
    unittest.main()

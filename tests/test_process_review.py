"""One source revision supplies every process schedule and fresh local check.

These small fixtures exercise admission and existing independent predicates;
they never call a layout solver or publish an export bundle.
"""

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
from layout_configurator.flows import FlowRoutingResult, route_flows
from test_environment_program import case as environment_case
from test_equipment_scenario_geometry import scenario_fixture
from test_transfer_program import _sha


_COMPONENTS = ("building", "layout", "equipment", "flow_routes", "production_program",
               "transfer_program", "environment_program", "library", "scenario_requests")
_SCENARIOS = ("production", "maintenance", "installation_replacement")


def process_fixture(*, cycle=False):
    building, layout, production, transfer, environment = environment_case(cycle=cycle)
    return {"version": "0.1", "id": "process_review_fixture", "drawing_revision": "D01", "variant": layout.variant,
            "building": building.to_dict(), "layout": layout.to_dict(), "equipment": [],
            "flow_routes": route_flows(building, layout).to_dict(), "production_program": production,
            "transfer_program": transfer, "environment_program": environment, "library": None,
            "scenario_requests": {"card_by_equipment": {}, "maintenance_targets": [], "installation_targets": [],
                                  "actions_by_scenario": {name: {} for name in _SCENARIOS}}}


def rebind(raw, *, production_base=True):
    """Update only deliberately changed original source identities in a fixture."""
    production = raw["production_program"]
    if production_base:
        base = {key: value for key, value in raw["building"].items() if key != "production_program"}
        production["building_sha256"] = _sha(base)
    transfer = raw["transfer_program"]
    transfer.update(building_sha256=_sha(raw["building"]), production_program_sha256=_sha(production))
    environment = raw["environment_program"]
    if environment is not None:
        environment.update(building_sha256=_sha(raw["building"]), layout_sha256=_sha(raw["layout"]),
                           production_program_sha256=_sha(production), transfer_program_sha256=_sha(transfer))


def equipment_process_fixture():
    raw = process_fixture()
    building, layout, equipment, library, mapping = scenario_fixture(second_x=4100)
    raw.update(building=building.to_dict(), layout=layout.to_dict(), equipment=equipment.to_dict(building)["equipment"],
               flow_routes=FlowRoutingResult(()).to_dict(), library=library, environment_program=None,
               scenario_requests={"card_by_equipment": mapping, "maintenance_targets": ["press"],
                   "installation_targets": ["press"], "actions_by_scenario": {
                       "production": {}, "maintenance": {"press": ["stop"]},
                       "installation_replacement": {"press": ["temporary"]}}})
    raw["production_program"]["operations"][0].update(room_id="work", equipment_ids=["press"], flow_ids=[])
    raw["transfer_program"].update(flow_movements=[], transfers=[])
    rebind(raw)
    return raw


class ProcessReviewTests(unittest.TestCase):
    def setUp(self):
        # A missing implementation must produce ordinary test assertions, not
        # an import/collection error in the parent's one-file RED invocation.
        try:
            self.api = importlib.import_module("layout_configurator.process_review")
        except ModuleNotFoundError as exc:
            self.fail(f"The synchronized source-bound process review core is missing: {exc}")
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.path = self.root / "review-input.json"
        self.raw = process_fixture()

    def load(self, raw=None, *, text=None, **options):
        if text is None:
            text = json.dumps(self.raw if raw is None else raw, ensure_ascii=False, allow_nan=False, indent=2) + "\n"
        self.path.write_bytes(text.encode("utf-8"))
        result = self.api.load_process_review(self.path, **options)
        return result.to_dict() if hasattr(result, "to_dict") else result

    def test_all_schedules_and_fresh_reports_share_exact_revision_variant_and_source_identity(self):
        report = self.load()
        self.assertEqual((report["document_status"], report["status"]), ("DRAFT", "UNKNOWN"))
        self.assertFalse(report["ok"])
        self.assertEqual(report["engineering_acceptance"], "PENDING")
        self.assertEqual(report["authenticated_approval"], "UNKNOWN")
        self.assertEqual(report["full_facility_validation"], {"status": "UNKNOWN", "ok": False})
        self.assertFalse(report["proven_building_infeasible"])
        identity = report["identity"]
        self.assertEqual((identity["drawing_revision"], identity["variant"]), ("D01", 1))
        self.assertEqual(identity["component_sha256"], {name: _sha(self.raw[name]) for name in _COMPONENTS})
        self.assertEqual(report["review_source"]["file_sha256"], hashlib.sha256(self.path.read_bytes()).hexdigest())
        self.assertEqual(report["review_source"]["bytes"], self.path.stat().st_size)
        self.assertEqual(report["source_frames"], {name: self.raw[name] for name in _COMPONENTS})
        self.assertEqual(set(report["schedules"]), {"rooms", "operations", "equipment", "transfers", "clean_zones",
                                                     "ventilation_zones", "pressure", "scenarios"})
        self.assertEqual(set(report["reports"]), {"production_program", "transfers", "environment", "library", "equipment_scenarios"})
        for record in (*report["schedules"].values(), *report["reports"].values()):
            self.assertEqual(record["identity"], identity)
        self.assertEqual({row["id"] for row in report["schedules"]["rooms"]["rows"]}, {"source", "target", "upper"})
        self.assertEqual(report["reports"]["transfers"]["evidence"]["geometry_status"], "PASS")
        self.assertEqual(report["reports"]["environment"]["evidence"]["pressure_resolved_status"], "PASS")
        self.assertEqual(report["schedules"]["clean_zones"]["rows"][0]["gmp_grade"], {"status": "UNKNOWN", "value": None})

    def test_real_pressure_cycle_fail_is_retained_beside_missing_library_measurements_and_rights(self):
        self.raw = process_fixture(cycle=True)
        self.raw["production_program"]["sources"][0]["usage_rights"]["state"] = "UNKNOWN"
        rebind(self.raw)
        report = self.load()
        self.assertEqual(report["status"], "FAIL")
        evidence = report["reports"]["environment"]["evidence"]
        self.assertEqual((evidence["pressure_declared_status"], evidence["pressure_resolved_status"]), ("FAIL", "FAIL"))
        self.assertEqual(evidence["pressure_measured_status"], "UNKNOWN")
        self.assertEqual({row["edge_id"] for row in evidence["declared_network"]["cycle_witness"]},
                         {"source_target", "target_upper", "upper_source"})
        self.assertEqual(report["reports"]["library"]["status"], "UNKNOWN")

    def test_saved_room_path_and_flow_type_never_authorize_a_personnel_pass_box_crossing(self):
        transfer = self.raw["transfer_program"]["transfers"][0]
        transfer.update(kind="pass_box", permissions=[{"direction": "forward", "movement_kinds": ["material"]}])
        rebind(self.raw)
        report = self.load()
        evidence = report["reports"]["transfers"]["evidence"]
        self.assertEqual((report["status"], evidence["geometry_status"], evidence["permission_status"]), ("FAIL", "PASS", "FAIL"))
        self.raw["flow_routes"]["routes"][0]["room_path"] = ["source"]
        evidence = self.load()["reports"]["transfers"]["evidence"]
        self.assertEqual((evidence["geometry_status"], evidence["permission_status"]), ("FAIL", "FAIL"))
        self.assertIn("room_path_mismatch", {row["code"] for row in evidence["geometry_validation"]["issues"]})
        self.raw["transfer_program"]["flow_movements"] = []
        rebind(self.raw)
        report = self.load()
        self.assertEqual(report["reports"]["transfers"]["evidence"]["permission_status"], "UNKNOWN")
        self.assertEqual(self.raw["building"]["flows"][0]["type"], "people")

    def test_missing_optional_environment_and_library_are_unknown_but_null_parents_are_invalid(self):
        self.raw["environment_program"] = None
        report = self.load()
        self.assertEqual((report["reports"]["environment"]["status"], report["reports"]["library"]["status"]), ("UNKNOWN", "UNKNOWN"))
        self.assertEqual(report["schedules"]["clean_zones"]["rows"], [])
        for field in ("building", "production_program", "transfer_program", "scenario_requests"):
            raw = process_fixture()
            raw[field] = None
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.load(raw)
        for field in ("environment_program", "library"):
            raw = process_fixture()
            raw[field] = {}
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.load(raw)

    def test_stale_parent_and_layout_bindings_are_actual_fresh_failures_not_saved_pass(self):
        for field in ("building_sha256", "layout_sha256", "production_program_sha256", "transfer_program_sha256"):
            raw = process_fixture()
            raw["environment_program"][field] = "0" * 64
            with self.subTest(field=field):
                report = self.load(raw)
                self.assertEqual((report["status"], report["reports"]["environment"]["status"]), ("FAIL", "FAIL"))

    def test_embedded_program_is_preserved_whole_and_does_not_bypass_current_capability_guard(self):
        self.raw["building"]["production_program"] = copy.deepcopy(self.raw["production_program"])
        rebind(self.raw, production_base=False)
        report = self.load()
        self.assertEqual(report["source_frames"]["building"], self.raw["building"])
        self.assertEqual(report["identity"]["component_sha256"]["building"], _sha(self.raw["building"]))
        self.assertEqual(report["reports"]["transfers"]["evidence"]["geometry_status"], "UNKNOWN")
        self.assertEqual(report["status"], "UNKNOWN")
        self.assertEqual(report["source_frames"]["production_program"]["building_sha256"],
                         _sha({key: value for key, value in self.raw["building"].items() if key != "production_program"}))

    def test_fresh_equipment_scenario_collision_fail_survives_missing_environment(self):
        report = self.load(equipment_process_fixture())
        evidence = report["reports"]["equipment_scenarios"]["evidence"]
        self.assertEqual((report["status"], evidence["canonical_equipment"]["status"]), ("FAIL", "PASS"))
        self.assertIn("scenario_envelope_overlap", {row["code"] for row in evidence["scenarios"]["production"]["issues"]})
        self.assertEqual(evidence["scenarios"]["installation_replacement"]["full_path_validation"], "UNKNOWN")
        self.assertEqual(report["reports"]["environment"]["status"], "UNKNOWN")
        self.assertEqual({row["id"] for row in report["schedules"]["equipment"]["rows"]}, {"press", "neighbor"})

    def test_raw_revision_variant_grid_and_closed_requests_are_not_normalized(self):
        edits = (("variant", True), ("variant", "1"), ("variant", 2), ("drawing_revision", " "),
                 ("output_path", "unexpected-write"), ("saved_reports", {"status": "PASS"}))
        for field, value in edits:
            raw = process_fixture()
            raw[field] = value
            with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                self.load(raw)
        raw = process_fixture()
        raw["building"]["layout"]["grid_mm"] = True
        with patch.object(self.api, "admit_transfer_review", side_effect=AssertionError("Raw boolean reached canonical constructors")), self.assertRaises(ValueError):
            self.load(raw)
        raw = process_fixture()
        raw["scenario_requests"]["maintenance_targets"] = "press"
        with self.assertRaises(ValueError):
            self.load(raw)

    def test_aggregate_equipment_and_environment_work_bounds_precede_canonical_admission(self):
        raw = equipment_process_fixture()
        raw["building"]["equipment"] *= 17
        with patch.object(self.api, "admit_transfer_review", side_effect=AssertionError("Equipment cap was checked after constructors")):
            report = self.load(raw)
        self.assertEqual(report["status"], "UNKNOWN")
        self.assertIn("input_budget_exceeded", {row["code"] for row in report["issues"]})
        with patch.object(self.api, "admit_transfer_review", side_effect=AssertionError("Geometry overflow reached common GEOS")):
            report = self.load(limits=self.api.ProcessReviewLimits(max_geometry_checks=1))
        self.assertEqual(report["status"], "UNKNOWN")
        self.assertIn("input_budget_exceeded", {row["code"] for row in report["issues"]})
        with patch.object(self.api, "admit_transfer_review", side_effect=AssertionError("Byte overflow reached constructors")), self.assertRaises(ValueError):
            self.load(limits=self.api.ProcessReviewLimits(max_input_bytes=100))

    def test_nested_structured_library_content_is_bounded_before_its_parse_and_any_geos(self):
        raw = equipment_process_fixture()
        content = "[" * 80 + "0" + "]" * 80
        raw["library"]["sources"][0].update(content=content, sha256=hashlib.sha256(content.encode("utf-8")).hexdigest())
        with patch.object(self.api, "admit_transfer_review", side_effect=AssertionError("Nested source overflow reached GEOS")), \
                patch("layout_configurator.engineering_library._strict_json", side_effect=AssertionError("Nested source overflow was parsed")):
            report = self.load(raw)
        self.assertEqual(report["status"], "UNKNOWN")
        self.assertIn("input_budget_exceeded", {row["code"] for row in report["issues"]})

    def test_duplicate_keys_and_raw_excessive_depth_are_refused_before_parse(self):
        with self.assertRaises(ValueError):
            self.load(text='{"version":"0.1","version":"0.2"}')
        with patch("layout_configurator.transfer_program.json.loads", side_effect=AssertionError("Excessive raw JSON depth was parsed")), self.assertRaises(ValueError):
            self.load(text="[" * 80 + "0" + "]" * 80)

    def test_reader_is_detached_strictly_offline_and_read_only(self):
        self.raw["production_program"]["sources"][0]["uri"] = (self.root / "must-not-be-created.json").as_uri()
        rebind(self.raw)
        report = self.load()
        before = self.path.read_bytes()
        report["source_frames"]["production_program"]["sources"][0]["content"] = "Changed returned snapshot"
        second = self.api.load_process_review(self.path)
        second = second.to_dict() if hasattr(second, "to_dict") else second
        self.assertEqual(second["source_frames"]["production_program"], self.raw["production_program"])
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual({path.name for path in self.root.iterdir()}, {self.path.name})
        for value in (True, "32", 1.5, 33):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.load(limits=replace(self.api.ProcessReviewLimits(), max_equipment=value))
        with self.assertRaises(ValueError):
            self.load(include_windows=1)

    def test_committed_reusable_example_has_fresh_identity_and_preserves_unknown_acceptance(self):
        path = Path(__file__).parents[1] / "examples/process_review_input.json"
        self.assertTrue(path.is_file(), "A reusable process-review source example is missing")
        result = self.api.load_process_review(path)
        report = result.to_dict() if hasattr(result, "to_dict") else result
        raw = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual((report["document_status"], report["status"]), ("DRAFT", "UNKNOWN"))
        self.assertEqual(report["identity"]["variant"], raw["layout"]["variant"])
        self.assertEqual(report["identity"]["drawing_revision"], raw["drawing_revision"])
        self.assertEqual(report["review_source"]["file_sha256"], hashlib.sha256(path.read_bytes()).hexdigest())

    def test_embedded_program_mismatch_is_fail_including_equal_numeric_python_values(self):
        for change in ("room", "numeric_type"):
            raw = process_fixture()
            embedded = copy.deepcopy(raw["production_program"])
            if change == "room":
                embedded["operations"][0]["room_id"] = "target"
            else:
                embedded["operations"][0]["measurements"]["cycle_time"]["value"] = 30.0
                self.assertEqual(embedded, raw["production_program"])
                self.assertNotEqual(_sha(embedded), _sha(raw["production_program"]))
            raw["building"]["production_program"] = embedded
            rebind(raw, production_base=False)
            with self.subTest(change=change):
                report = self.load(raw)
                self.assertEqual(report["status"], "FAIL")
                evidence = report["reports"]["transfers"]["evidence"]
                self.assertIn("embedded_production_program_mismatch", {row["code"] for row in evidence["issues"]})
                self.assertEqual(report["source_frames"]["building"]["production_program"], embedded)
                self.assertEqual(report["source_frames"]["production_program"], raw["production_program"])

    def test_known_canonical_body_collision_survives_absent_or_failed_library(self):
        for state in ("absent", "failed"):
            raw = equipment_process_fixture()
            neighbor = next(row for row in raw["equipment"] if row["equipment_id"] == "neighbor")
            neighbor["x"] = 3500
            neighbor.pop("clearance", None)
            if state == "absent":
                raw["library"] = None
            else:
                raw["library"]["sources"][0]["sha256"] = "0" * 64
            with self.subTest(state=state):
                report = self.load(raw)
                self.assertEqual(report["status"], "FAIL")
                evidence = report["reports"]["equipment_scenarios"]["evidence"]
                self.assertEqual(evidence["canonical_equipment"]["status"], "FAIL")
                self.assertEqual(report["reports"]["library"]["status"], "UNKNOWN" if state == "absent" else "FAIL")

    def test_multiple_shallow_library_sources_charge_combined_nodes_before_any_decode_or_geos(self):
        raw = equipment_process_fixture()
        template = copy.deepcopy(raw["library"]["sources"][0])
        content = "[" + ",".join(["0"] * 20000) + "]"
        for index in range(16):
            source = copy.deepcopy(template)
            source.update(id=f"large_source_{index}", content=content,
                sha256=hashlib.sha256(content.encode("utf-8")).hexdigest())
            raw["library"]["sources"].append(source)
        with patch.object(self.api, "admit_transfer_review", side_effect=AssertionError("Combined nested nodes reached constructors")), \
                patch("layout_configurator.engineering_library._strict_json", side_effect=AssertionError("Combined nested nodes were parsed")):
            report = self.load(raw)
        self.assertEqual(report["status"], "UNKNOWN")
        self.assertIn("input_budget_exceeded", {row["code"] for row in report["issues"]})

    def test_positive_canonical_extent_lost_in_world_precision_stops_before_geos_without_library(self):
        raw = equipment_process_fixture()
        raw["library"] = None
        spec = next(row for row in raw["building"]["equipment"] if row["id"] == "neighbor")
        spec["width_mm"] = 1e-300
        placement = next(row for row in raw["equipment"] if row["equipment_id"] == "neighbor")
        placement.update(x=6000, width=1e-300)
        placement.pop("clearance", None)
        rebind(raw)
        with patch.object(self.api, "admit_transfer_review", side_effect=AssertionError("Collapsed positive body reached geometry admission")):
            report = self.load(raw)
        self.assertEqual(report["status"], "UNKNOWN")
        self.assertIn("unrepresentable_canonical_geometry", {row["code"] for row in report["issues"]})

    def test_optional_library_does_not_bypass_exact_frames_or_source_request_ids(self):
        for change in ("footprint", "target"):
            raw = equipment_process_fixture()
            raw["library"] = None
            if change == "footprint":
                raw["equipment"][0]["width"] += 1e-7
                raw["equipment"][0].pop("clearance", None)
            else:
                raw["scenario_requests"]["maintenance_targets"] = ["unknown_machine"]
            with self.subTest(change=change), patch.object(self.api, "admit_transfer_review",
                    side_effect=AssertionError("Invalid exact frame/request reached geometry admission")), self.assertRaises(ValueError):
                self.load(raw)

    def test_tightened_production_source_byte_limit_precedes_geometry_admission(self):
        with patch.object(self.api, "admit_transfer_review", side_effect=AssertionError("Oversized PP source reached geometry")):
            report = self.load(limits=self.api.ProcessReviewLimits(max_source_bytes=1))
        self.assertEqual(report["status"], "UNKNOWN")
        self.assertIn("input_budget_exceeded", {row["code"] for row in report["issues"]})

    def test_identical_embedded_source_occurrences_share_the_aggregate_byte_budget(self):
        self.raw["building"]["production_program"] = copy.deepcopy(self.raw["production_program"])
        rebind(self.raw, production_base=False)
        one_copy = len(self.raw["production_program"]["sources"][0]["content"].encode("utf-8"))
        with patch.object(self.api, "admit_transfer_review", side_effect=AssertionError("Duplicated source budget reached geometry")):
            report = self.load(limits=self.api.ProcessReviewLimits(max_total_source_bytes=one_copy + 1))
        self.assertEqual(report["status"], "UNKNOWN")
        self.assertIn("input_budget_exceeded", {row["code"] for row in report["issues"]})

    def test_bounded_delivery_retains_an_identity_stamped_source_failure(self):
        report = self.load(process_fixture(cycle=True), limits=self.api.ProcessReviewLimits(max_report_bytes=4096, max_findings=1))
        self.assertEqual((report["status"], report["inventory_status"]), ("FAIL", "INCOMPLETE"))
        self.assertLessEqual(len(json.dumps(report, ensure_ascii=False, separators=(",", ":")).encode("utf-8")), 4096)
        retained = report.get("retained_failures", [])
        self.assertEqual(len(retained), 1, "A known failure lost all originating evidence during bounded delivery")
        record = retained[0]
        self.assertEqual((record["component"], record["identity"]), ("environment", report["identity"]))
        self.assertEqual(record["failure"]["status"], "FAIL")
        self.assertIn("cycle", record["failure"]["code"])
        self.assertEqual(record["failure"]["source_id"], "transfer_brief")
        self.assertIn(record["failure"]["edge_id"], {"source_target", "target_upper", "upper_source"})


if __name__ == "__main__":
    unittest.main()

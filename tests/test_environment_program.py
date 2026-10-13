"""Environment sidecars preserve original declarations and separate namespaces."""

import copy
from dataclasses import replace
import hashlib
import importlib
import json
import unittest
from unittest.mock import patch

from layout_configurator.building import BuildingIR
from layout_configurator.models import LayoutResult, Rect
from layout_configurator.orthogonal import OrthogonalPolygon
from test_transfer_program import case as transfer_case, _sha


def case(*, cycle=False):
    building, production, transfer = transfer_case()
    source = building.to_dict()
    source["layout"].update(boundary={"width": 6000.0, "height": 6000.0, "cutouts": []}, rooms=[
        {"id": "source", "target_area": 18, "required_adjacency": ["target", "upper"]},
        {"id": "target", "target_area": 9, "required_adjacency": ["upper"]}, {"id": "upper", "target_area": 9}],
        doors=[{"id": "passbox_portal", "room_a": "source", "room_b": "target", "offset_mm": 1500, "width_mm": 900},
               {"id": "upper_portal", "room_a": "source", "room_b": "upper", "offset_mm": 1500, "width_mm": 900},
               {"id": "side_portal", "room_a": "target", "room_b": "upper", "offset_mm": 1500, "width_mm": 900}])
    building = BuildingIR.from_mapping(source)
    layout = LayoutResult(1, {"source": Rect(0, 0, 3000, 6000), "target": Rect(3000, 0, 3000, 3000),
                              "upper": Rect(3000, 3000, 3000, 3000)})
    content = ("Environment reconstruction fixture; supplied declarations are not engineering approval.\r\n"
               "The movement is personnel. All three explicit doors permit personnel in both directions.\r\n"
               "A local ISO 8 region is declared; GMP grade is unknown and no equivalence is inferred.\r\n"
               "Production uses handover; maintenance and replacement require handover stopped.\r\n"
               "Declared pressures are source 30 Pa, target 20 Pa, upper 10 Pa on a common reference.\r\n"
               "Each required pressure difference is at least 10 Pa. Measured values and HVAC evidence are unknown.\r\n")
    production["sources"][0].update(content=content, sha256=hashlib.sha256(content.encode("utf-8")).hexdigest())
    production["building_sha256"] = _sha(building.to_dict())
    evidence = {"source_id": "transfer_brief", "basis": "declared_fact", "excerpt": "Environment reconstruction fixture"}
    transfer["flow_movements"][0].update(movement_kind="personnel", excerpt="movement is personnel")
    transfer["transfers"] = []
    for index, (opening, chain) in enumerate((("passbox_portal", ["source", "target"]),
                                             ("upper_portal", ["source", "upper"]), ("side_portal", ["target", "upper"]))):
        transfer["transfers"].append({"id": f"transfer_{index + 1}", "kind": "door", "opening_id": opening,
            "room_chain": chain, "minimum_clear_width_mm": 800, "permissions": [
                {"direction": direction, "movement_kinds": ["personnel"]} for direction in ("forward", "reverse")],
            **evidence})
    transfer.update(building_sha256=_sha(building.to_dict()), production_program_sha256=_sha(production))
    region = lambda rect: OrthogonalPolygon.from_rect(rect).to_dict()
    clean = {"id": "local_clean", "boundary": region(Rect(200, 200, 1000, 1000)), "host_room_ids": ["source"],
             "equipment_ids": [], "operation_ids": [], "iso_class": {"status": "KNOWN", "value": 8},
             "gmp_grade": {"status": "UNKNOWN", "value": None}, "state": {"status": "KNOWN", "value": "in_operation"},
             "applicability": "Supplied reconstruction only; no profile applicability is authenticated.", **evidence}
    ventilation = {"id": "ventilation_source", "boundary": region(layout.placements["source"]),
                   "host_room_ids": ["source"], "equipment_ids": [], "operation_ids": ["handover"], **evidence}
    action = {"id": "stop_for_service", "kind": "production_stopped", "operation_ids": ["handover"],
              "equipment_ids": [], "transfer_ids": [], "required_stopped_operation_ids": ["handover"], **evidence}
    scenarios = [{"id": "normal", "kind": "production", "active_operation_ids": ["handover"],
                  "stopped_operation_ids": [], "actions": [], **evidence},
                 {"id": "service", "kind": "maintenance", "active_operation_ids": [],
                  "stopped_operation_ids": ["handover"], "actions": [action], **evidence},
                 {"id": "replacement", "kind": "installation_replacement", "active_operation_ids": [],
                  "stopped_operation_ids": ["handover"], "actions": [{**action, "id": "stop_for_replacement"}], **evidence}]
    pressure = lambda value: {"status": "UNKNOWN" if value is None else "KNOWN", "value": value, "unit": "Pa", **evidence}
    nodes = [{"id": f"pressure_{room}", "space": {"namespace": "room", "id": room}, "datum_id": "common",
              "values": [{"scenario_id": scenario["id"], "design": pressure(value), "measured": pressure(None)}
                         for scenario in scenarios], **evidence}
             for room, value in (("source", 30), ("target", 20), ("upper", 10))]
    edges = [{"id": "source_target", "scenario_id": "normal", "high_node_id": "pressure_source", "low_node_id": "pressure_target",
              "transfer_id": "transfer_1", "minimum_delta_pa": 10, "maximum_delta_pa": None, **evidence},
             {"id": "target_upper", "scenario_id": "normal", "high_node_id": "pressure_target", "low_node_id": "pressure_upper",
              "transfer_id": "transfer_3", "minimum_delta_pa": 10, "maximum_delta_pa": None, **evidence},
             {"id": "upper_source", "scenario_id": "normal", "high_node_id": "pressure_upper" if cycle else "pressure_source",
              "low_node_id": "pressure_source" if cycle else "pressure_upper", "transfer_id": "transfer_2",
              "minimum_delta_pa": 10, "maximum_delta_pa": None, **evidence}]
    raw = {"version": "0.1", "id": "environment_fixture", "revision": "E01", "units": "mm",
           "building_sha256": _sha(building.to_dict()), "layout_sha256": _sha(layout.to_dict()),
           "production_program_sha256": _sha(production), "transfer_program_sha256": _sha(transfer),
           "clean_zones": [clean], "ventilation_zones": [ventilation], "scenarios": scenarios,
           "pressure_datums": [{"id": "common", "label": "Supplied common pressure reference", **evidence}],
           "pressure_nodes": nodes, "pressure_edges": edges}
    return building, layout, production, transfer, raw


class EnvironmentProgramTests(unittest.TestCase):
    def setUp(self):
        self.building, self.layout, self.production, self.transfer, self.raw = case()

    def validate(self, **options):
        try:
            api = importlib.import_module("layout_configurator.environment_program")
        except ModuleNotFoundError as exc:
            self.fail(f"Source-bound environment contract is missing: {exc}")
        return api.validate_environment_program(self.raw, building=self.building, production_program=self.production,
                                                 transfer_program=self.transfer, **options)

    def test_independent_classification_and_exact_detached_roundtrip(self):
        before = copy.deepcopy(self.raw)
        report = self.validate()
        self.assertEqual(report.schema_status, "PASS")
        self.assertEqual(report.binding_status, "PASS")
        self.assertEqual(report.program.to_dict(), before)
        self.assertEqual(report.program.to_dict()["clean_zones"][0]["gmp_grade"], {"status": "UNKNOWN", "value": None})
        self.assertEqual(report.to_dict()["engineering_acceptance"], "PENDING")
        self.assertEqual(report.to_dict()["authenticated_approval"], "UNKNOWN")

    def test_declared_cycle_is_known_failure_even_with_missing_measurements_and_rights(self):
        self.building, self.layout, self.production, self.transfer, self.raw = case(cycle=True)
        self.production["sources"][0]["usage_rights"]["state"] = "UNKNOWN"
        self.transfer["production_program_sha256"] = _sha(self.production)
        self.raw.update(production_program_sha256=_sha(self.production), transfer_program_sha256=_sha(self.transfer))
        report = self.validate()
        self.assertEqual(report.status, "FAIL")
        self.assertEqual(report.pressure_declared_status, "FAIL")
        witness = report.to_dict()["declared_network"]["cycle_witness"]
        self.assertEqual({edge["edge_id"] for edge in witness}, {"source_target", "target_upper", "upper_source"})
        self.assertTrue(all(edge["source_id"] == "transfer_brief" for edge in witness))
        self.assertFalse(report.to_dict()["proven_building_infeasible"])

    def test_zero_minimum_cycle_is_not_mistaken_for_a_contradiction(self):
        self.building, self.layout, self.production, self.transfer, self.raw = case(cycle=True)
        for edge in self.raw["pressure_edges"]:
            edge["minimum_delta_pa"] = 0
        for node in self.raw["pressure_nodes"]:
            for row in node["values"]:
                row["design"]["value"] = 0
        self.assertEqual(self.validate().pressure_declared_status, "PASS")

    def test_source_bindings_references_and_raw_types_are_strict(self):
        for edit in ("building", "production", "transfer", "room", "source", "excerpt", "pressure", "class", "closed"):
            self.building, self.layout, self.production, self.transfer, self.raw = case()
            if edit in ("building", "production", "transfer"):
                self.raw[edit + ("_program_sha256" if edit != "building" else "_sha256")] = "0" * 64
            elif edit == "room": self.raw["clean_zones"][0]["host_room_ids"] = ["missing"]
            elif edit == "source": self.raw["pressure_edges"][0]["source_id"] = "missing"
            elif edit == "excerpt": self.raw["scenarios"][0]["excerpt"] = "Absent source statement"
            elif edit == "pressure": self.raw["pressure_edges"][0]["minimum_delta_pa"] = True
            elif edit == "class": self.raw["clean_zones"][0]["iso_class"]["value"] = "8"
            else: self.raw["clean_zones"][0]["functional_zone_id"] = "invented"
            with self.subTest(edit=edit): self.assertEqual(self.validate().status, "FAIL")

    def test_explicit_scenario_partition_and_stop_premises_are_checked(self):
        self.raw["scenarios"][1]["active_operation_ids"] = ["handover"]
        report = self.validate()
        self.assertEqual(report.status, "FAIL")
        self.assertIn("scenario_operation_conflict", {issue.code for issue in report.issues})
        self.raw["scenarios"][1]["active_operation_ids"] = []
        self.raw["scenarios"][1]["stopped_operation_ids"] = []
        self.assertIn("unstopped_action_requirement", {issue.code for issue in self.validate().issues})
        self.building, self.layout, self.production, self.transfer, self.raw = case()
        self.raw["scenarios"] = self.raw["scenarios"][:1]
        for node in self.raw["pressure_nodes"]:
            node["values"] = node["values"][:1]
        self.assertEqual(self.validate().scenario_status, "UNKNOWN")

    def test_exact_declared_cycle_does_not_lose_a_tiny_positive_difference(self):
        self.raw["pressure_edges"][0]["minimum_delta_pa"] = 1e-300
        self.raw["pressure_edges"][1]["minimum_delta_pa"] = 1_000_000
        self.raw["pressure_edges"][2].update(minimum_delta_pa=0, maximum_delta_pa=1_000_000)
        self.assertEqual(self.validate().pressure_declared_status, "FAIL")

    def test_missing_parent_evidence_cannot_hide_an_internally_bound_declared_cycle(self):
        self.building, self.layout, self.production, self.transfer, self.raw = case(cycle=True)
        self.production = None
        report = self.validate()
        self.assertEqual(report.pressure_declared_status, "FAIL")
        self.assertEqual(report.status, "FAIL")
        self.assertEqual(report.binding_status, "UNKNOWN")

    def test_empty_pressure_declarations_are_incomplete_not_a_network_acceptance(self):
        self.raw["pressure_nodes"] = []
        self.raw["pressure_edges"] = []
        report = self.validate()
        self.assertEqual((report.pressure_declared_status, report.status), ("UNKNOWN", "UNKNOWN"))

    def test_unknown_rights_assumptions_and_mismatched_confirmation_preserve_unknown(self):
        self.raw["clean_zones"][0]["basis"] = "supplied_confirmation"
        report = self.validate()
        self.assertEqual(report.provenance_status, "UNKNOWN")
        self.assertIn("missing_confirmation", {issue.code for issue in report.issues})
        self.raw["clean_zones"][0]["basis"] = "assumption"
        self.assertEqual(self.validate().provenance_status, "UNKNOWN")

    def test_missing_observation_has_explicit_unknown_units_and_no_fabricated_source(self):
        measured = self.raw["pressure_nodes"][0]["values"][0]["measured"]
        measured.update(source_id=None, excerpt=None)
        report = self.validate()
        self.assertEqual((report.schema_status, report.binding_status, report.provenance_status), ("PASS", "PASS", "UNKNOWN"))
        self.assertEqual(report.program.to_dict()["pressure_nodes"][0]["values"][0]["measured"]["unit"], "Pa")
        measured.update(status="KNOWN", value=30)
        self.assertEqual(self.validate().status, "FAIL")

    def test_public_detached_snapshot_checks_depth_duplicates_and_entity_bounds(self):
        from layout_configurator.environment_program import EnvironmentProgramContract
        from layout_configurator.production_program import _InputFailure
        with patch("layout_configurator.transfer_program.json.loads",
                   side_effect=AssertionError("Raw excessive depth was parsed")):
            with self.assertRaises(_InputFailure):
                EnvironmentProgramContract("[" * 17 + "0" + "]" * 17).to_dict()
        with self.assertRaises(ValueError):
            EnvironmentProgramContract('{"id":"first","id":"second"}').to_dict()
        oversized = copy.deepcopy(self.raw)
        oversized["clean_zones"] *= 65
        with self.assertRaises(_InputFailure):
            EnvironmentProgramContract(json.dumps(oversized)).to_dict()

    def test_custom_budgets_cannot_raise_the_supported_admission_ceiling(self):
        from layout_configurator.environment_program import EnvironmentProgramLimits
        defaults = EnvironmentProgramLimits()
        report = self.validate(limits=replace(defaults, max_pressure_nodes=defaults.max_pressure_nodes + 1))
        self.assertEqual(report.status, "FAIL")
        self.assertEqual({issue.code for issue in report.issues}, {"invalid_limits"})

    def test_preflight_geometry_and_byte_budgets_precede_contracts_and_parsing(self):
        from layout_configurator.environment_program import EnvironmentProgramLimits
        with patch("layout_configurator.environment_program.validate_transfer_program",
                   side_effect=AssertionError("Parent contract ran after admission overflow")):
            report = self.validate(limits=EnvironmentProgramLimits(max_program_bytes=100))
        self.assertEqual(report.status, "FAIL")
        self.assertIn("input_budget_exceeded", {issue.code for issue in report.issues})
        for value in (True, "10", 1.5):
            with self.subTest(value=value): self.assertEqual(self.validate(limits=EnvironmentProgramLimits(max_pressure_nodes=value)).status, "FAIL")


if __name__ == "__main__": unittest.main()

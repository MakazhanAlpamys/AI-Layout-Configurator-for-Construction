"""Pressure connectivity uses exact actual boundaries and explicit transfers."""

import copy
from dataclasses import replace
import importlib
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from layout_configurator.flows import FlowRoutingResult, route_flows
from layout_configurator.models import LayoutResult, Rect
from layout_configurator.orthogonal import OrthogonalPolygon
from layout_configurator.orthogonal_flows import validate_orthogonal_flow_routes
from layout_configurator.orthogonal_projection import build_orthogonal_wall_plan
from test_environment_program import case
from test_transfer_program import _sha


class EnvironmentValidationTests(unittest.TestCase):
    def setUp(self):
        self.building, self.layout, self.production, self.transfer, self.raw = case()
        self.routes = route_flows(self.building, self.layout)

    def validate(self, **options):
        try:
            api = importlib.import_module("layout_configurator.environment_validation")
        except ModuleNotFoundError as exc:
            self.fail(f"Independent environment connectivity checker is missing: {exc}")
        return api.validate_environment_layout(self.building, self.layout, self.routes, self.raw,
            production_program=self.production, transfer_program=self.transfer, **options)

    def test_fresh_three_door_t_junction_exposes_actual_declared_cycle(self):
        self.building, self.layout, self.production, self.transfer, self.raw = case(cycle=True)
        self.assertEqual(validate_orthogonal_flow_routes(self.building, self.layout, self.routes).status, "PASS")
        self.assertEqual(len(build_orthogonal_wall_plan(self.building.layout, self.layout).openings), 3)
        report = self.validate()
        self.assertEqual((report.geometry_status, report.pressure_declared_status, report.pressure_resolved_status),
                         ("PASS", "FAIL", "FAIL"))
        self.assertEqual(report.to_dict()["resolved_network"]["resolved_edge_ids"],
                         ["source_target", "target_upper", "upper_source"])

    def test_consistent_scoped_witness_keeps_measured_hvac_and_execution_unknown(self):
        before = copy.deepcopy(self.raw)
        report = self.validate()
        self.assertEqual((report.geometry_status, report.boundary_status, report.connectivity_status,
                          report.pressure_declared_status, report.pressure_resolved_status), ("PASS",) * 5)
        self.assertEqual((report.pressure_measured_status, report.status), ("UNKNOWN", "UNKNOWN"))
        data = report.to_dict()
        self.assertEqual(data["hvac_validation"], "UNKNOWN")
        self.assertEqual(data["action_execution"], "UNKNOWN")
        self.assertEqual(data["authenticated_approval"], "UNKNOWN")
        self.assertEqual(data["source"]["layout_sha256"], _sha(self.layout.to_dict()))
        self.assertEqual(self.raw, before)

    def test_self_contained_example_has_fresh_three_portal_witness_and_honest_unknowns(self):
        from layout_configurator.building import BuildingIR
        from layout_configurator.environment_validation import validate_environment_layout
        from layout_configurator.result_geometry import parse_layout_result
        path = Path(__file__).parents[1] / "examples/environment_program_connected_pressure.json"
        self.assertTrue(path.is_file(), "A reusable exact-source environment example is missing")
        saved = json.loads(path.read_text(encoding="utf-8"))
        building = BuildingIR.from_mapping(saved["building"])
        layout = parse_layout_result(saved["layout"])
        self.assertEqual(building.to_dict(), saved["building"])
        self.assertEqual(layout.to_dict(), saved["layout"])
        report = validate_environment_layout(building, layout, route_flows(building, layout), saved["environment_program"],
            production_program=saved["production_program"], transfer_program=saved["transfer_program"])
        self.assertEqual((report.geometry_status, report.boundary_status, report.connectivity_status,
                          report.pressure_declared_status, report.pressure_resolved_status), ("PASS",) * 5)
        self.assertEqual(len(report.to_dict()["connections"]), 3)
        self.assertEqual((report.pressure_measured_status, report.status), ("UNKNOWN", "UNKNOWN"))

    def test_absent_pressure_scope_cannot_produce_vacuous_physical_or_measurement_pass(self):
        self.raw["pressure_nodes"] = []
        self.raw["pressure_edges"] = []
        report = self.validate()
        self.assertEqual((report.connectivity_status, report.pressure_resolved_status,
                          report.pressure_design_status, report.pressure_measured_status), ("UNKNOWN",) * 4)

    def test_raw_region_numbers_are_checked_before_actual_geometry_operations(self):
        for value in (True, "200", 1_000_000_001, float("inf")):
            self.building, self.layout, self.production, self.transfer, self.raw = case()
            self.raw["clean_zones"][0]["boundary"]["vertices"][0][0] = value
            with self.subTest(value=value), patch("layout_configurator.environment_validation.validate_transfer_routes",
                    side_effect=AssertionError("Invalid raw region reached actual route checks")), \
                    patch("layout_configurator.environment_validation.OrthogonalPolygon.from_mapping",
                          side_effect=AssertionError("Invalid raw region reached geometry parsing")):
                self.assertEqual(self.validate().status, "FAIL")

    def test_missing_portal_does_not_certify_resolved_network_or_hide_declared_cycle(self):
        self.building, self.layout, self.production, self.transfer, self.raw = case(cycle=True)
        self.layout = LayoutResult(1, {"source": Rect(0, 0, 2900, 6000), **{key: value for key, value in self.layout.placements.items() if key != "source"}})
        self.raw["layout_sha256"] = _sha(self.layout.to_dict())
        self.raw["ventilation_zones"][0]["boundary"] = OrthogonalPolygon.from_rect(self.layout.placements["source"]).to_dict()
        report = self.validate()
        self.assertEqual(report.pressure_declared_status, "FAIL")
        self.assertEqual(report.pressure_resolved_status, "UNKNOWN")
        self.assertNotEqual(report.connectivity_status, "PASS")
        self.assertEqual(report.status, "FAIL")

    def test_local_clean_region_uses_full_geometry_and_host_memberships(self):
        self.raw["clean_zones"][0]["boundary"] = OrthogonalPolygon.from_rect(Rect(2800, 200, 1000, 1000)).to_dict()
        report = self.validate()
        self.assertEqual(report.boundary_status, "FAIL")
        self.assertIn("environment_boundary_outside_hosts", {issue.code for issue in report.issues})

    def test_local_equipment_membership_requires_body_coverage_and_does_not_locate_operations(self):
        from layout_configurator.building import BuildingIR
        from layout_configurator.equipment import EquipmentLayoutResult, EquipmentPlacement
        source = self.building.to_dict()
        source["equipment"] = [{"id": "cabinet", "room_id": "source", "width_mm": 400, "depth_mm": 400}]
        self.building = BuildingIR.from_mapping(source)
        self.production["building_sha256"] = _sha(self.building.to_dict())
        self.transfer.update(building_sha256=_sha(self.building.to_dict()), production_program_sha256=_sha(self.production))
        self.raw.update(building_sha256=_sha(self.building.to_dict()), production_program_sha256=_sha(self.production),
                        transfer_program_sha256=_sha(self.transfer))
        self.raw["clean_zones"][0].update(equipment_ids=["cabinet"], operation_ids=["handover"])
        equipment = EquipmentLayoutResult((EquipmentPlacement("cabinet", "source", Rect(400, 400, 400, 400)),))
        report = self.validate(equipment=equipment)
        self.assertEqual((report.geometry_status, report.boundary_status), ("PASS", "PASS"))
        region = report.to_dict()["regions"][0]
        self.assertEqual(region["equipment_memberships"], [{"equipment_id": "cabinet", "status": "PASS"}])
        self.assertEqual(region["operation_memberships"], [{"operation_id": "handover", "status": "UNKNOWN"}])
        straddling = EquipmentLayoutResult((EquipmentPlacement("cabinet", "source", Rect(1000, 400, 400, 400)),))
        report = self.validate(equipment=straddling)
        self.assertEqual(report.geometry_status, "PASS")
        self.assertEqual(report.to_dict()["regions"][0]["equipment_memberships"][0]["status"], "UNKNOWN")
        self.assertIn("environment_equipment_interface_unknown", {issue.code for issue in report.issues})

    def test_local_region_cannot_claim_a_distant_room_door_as_its_pressure_boundary(self):
        self.raw["pressure_nodes"][0]["space"] = {"namespace": "clean_zone", "id": "local_clean"}
        report = self.validate()
        self.assertEqual(report.connectivity_status, "FAIL")
        self.assertIn("transfer_not_on_environment_boundary", {issue.code for issue in report.issues})

    def test_layout_identity_measurement_direction_and_datums_are_independent(self):
        self.raw["layout_sha256"] = "0" * 64
        self.assertEqual(self.validate().status, "FAIL")
        self.building, self.layout, self.production, self.transfer, self.raw = case()
        for node in self.raw["pressure_nodes"]:
            for row in node["values"]: row["measured"] = copy.deepcopy(row["design"])
        self.assertEqual(self.validate().pressure_measured_status, "PASS")
        self.raw["pressure_nodes"][0]["values"][0]["measured"]["value"] = 5
        self.assertEqual(self.validate().pressure_measured_status, "FAIL")
        self.raw["pressure_nodes"][0]["values"][0]["measured"]["status"] = "UNKNOWN"
        self.raw["pressure_nodes"][0]["values"][0]["measured"]["value"] = None
        self.assertEqual(self.validate().pressure_measured_status, "UNKNOWN")

    def test_raw_geometry_work_overflow_precedes_geos_and_parent_route_checks(self):
        from layout_configurator.environment_program import EnvironmentProgramLimits
        with patch("layout_configurator.environment_validation.validate_transfer_routes",
                   side_effect=AssertionError("Route gate ran after environment budget overflow")), \
                patch("layout_configurator.environment_validation.OrthogonalPolygon.from_mapping",
                      side_effect=AssertionError("GEOS ran after environment budget overflow")):
            report = self.validate(limits=EnvironmentProgramLimits(max_total_zone_vertices=1))
        self.assertEqual(report.status, "UNKNOWN")
        self.assertIn("input_budget_exceeded", {issue.code for issue in report.issues})

    def test_different_supplied_datums_do_not_establish_comparable_measurements(self):
        self.raw["pressure_datums"].append({**self.raw["pressure_datums"][0], "id": "other_reference"})
        self.raw["pressure_nodes"][0]["datum_id"] = "other_reference"
        for node in self.raw["pressure_nodes"]:
            for value in node["values"]: value["measured"] = copy.deepcopy(value["design"])
        report = self.validate()
        self.assertEqual(report.pressure_measured_status, "UNKNOWN")
        self.assertEqual(report.pressure_resolved_status, "PASS")

    def test_known_measured_violation_survives_an_unrelated_missing_observation(self):
        for node in self.raw["pressure_nodes"]:
            for value in node["values"]: value["measured"] = copy.deepcopy(value["design"])
        self.raw["pressure_nodes"][0]["values"][0]["measured"]["value"] = 5
        self.raw["pressure_nodes"][2]["values"][0]["measured"].update(status="UNKNOWN", value=None)
        report = self.validate()
        self.assertEqual((report.pressure_measured_status, report.status), ("FAIL", "FAIL"))

    def test_all_directions_are_bound_to_real_transfers_and_no_parent_zone_edges_appear(self):
        source = self.building.to_dict()
        source["zones"] = [{"id": "parent", "type": "functional", "room_ids": ["source"], "pressure_pa": -500},
                           {"id": "child", "type": "functional", "room_ids": ["target"], "parent_zone_id": "parent", "pressure_pa": 1000}]
        from layout_configurator.building import BuildingIR
        self.building = BuildingIR.from_mapping(source)
        self.production["building_sha256"] = _sha(self.building.to_dict())
        self.transfer.update(building_sha256=_sha(self.building.to_dict()), production_program_sha256=_sha(self.production))
        self.raw.update(building_sha256=_sha(self.building.to_dict()), production_program_sha256=_sha(self.production), transfer_program_sha256=_sha(self.transfer))
        report = self.validate()
        self.assertEqual(report.pressure_resolved_status, "PASS")
        self.assertEqual(len(report.to_dict()["connections"]), 3)
        self.assertFalse(report.to_dict()["policy"]["functional_parent_pressure_inference"])


if __name__ == "__main__": unittest.main()

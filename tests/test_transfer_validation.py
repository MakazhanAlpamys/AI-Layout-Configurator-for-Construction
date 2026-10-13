"""Permissions use fresh physical crossings, not saved room paths or flow labels."""

import copy
from dataclasses import replace
import importlib
import unittest
from unittest.mock import patch

from layout_configurator.flows import FlowRoute, FlowRoutingResult, route_flows, validate_flow_routes
from layout_configurator.building import BuildingIR
from layout_configurator.equipment import EquipmentLayoutResult, EquipmentPlacement
from layout_configurator.models import LayoutResult, Rect, WindowSpec
from layout_configurator.orthogonal_flows import route_orthogonal_flows, validate_orthogonal_flow_routes
from test_transfer_program import case, rebind, _sha


def layout():
    return LayoutResult(1, {"source": Rect(0, 0, 3000, 3000), "target": Rect(3000, 0, 3000, 3000)})


class TransferValidationTests(unittest.TestCase):
    def setUp(self):
        self.building, self.production, self.raw = case()
        self.layout = layout()
        self.routes = route_flows(self.building, self.layout)

    def validate(self, **options):
        try:
            api = importlib.import_module("layout_configurator.transfer_validation")
        except ModuleNotFoundError as exc:
            self.fail(f"Actual crossing permission validator is missing: {exc}")
        return api.validate_transfer_routes(self.building, self.layout, self.routes, self.raw,
                                            production_program=self.production, **options)

    def test_geometrically_valid_personnel_route_through_declared_passbox_fails(self):
        self.assertTrue(validate_flow_routes(self.building, self.layout, self.routes).ok)
        self.assertEqual(validate_orthogonal_flow_routes(self.building, self.layout, self.routes).status, "PASS")
        report = self.validate()
        self.assertEqual((report.status, report.geometry_status, report.permission_status), ("FAIL", "PASS", "FAIL"))
        issue = next(issue for issue in report.issues if issue.code == "transfer_movement_forbidden")
        self.assertEqual((issue.flow_id, issue.transfer_id, issue.opening_id, issue.source_id),
                         ("movement", "transfer_1", "passbox_portal", "transfer_brief"))
        crossing = report.to_dict()["crossings"][0]
        self.assertEqual((crossing["from_room"], crossing["to_room"], crossing["point"]),
                         ("source", "target", [3000.0, 1500.0]))
        self.assertFalse(report.to_dict()["proven_building_infeasible"])

    def test_material_permission_is_verified_but_passbox_mechanism_stays_unknown(self):
        self.raw["flow_movements"][0].update(movement_kind="material", excerpt="paired material exercise")
        report = self.validate()
        self.assertEqual((report.geometry_status, report.permission_status, report.mechanism_status, report.status),
                         ("PASS", "PASS", "UNKNOWN", "UNKNOWN"))
        self.assertEqual(report.to_dict()["engineering_acceptance"], "PENDING")
        self.assertEqual(report.to_dict()["full_facility_validation"]["status"], "UNKNOWN")

    def test_door_permitted_positive_is_only_a_scoped_pass_with_exact_source_hashes(self):
        self.raw["transfers"][0].update(kind="door", excerpt="paired door exercise permits personnel")
        self.raw["transfers"][0]["permissions"][0]["movement_kinds"] = ["personnel"]
        before = (self.building.to_dict(), self.layout.to_dict(), self.routes.to_dict(), copy.deepcopy(self.raw))
        report = self.validate()
        self.assertEqual((report.status, report.geometry_status, report.permission_status), ("PASS", "PASS", "PASS"))
        self.assertEqual(report.to_dict()["source"]["building_sha256"], _sha(self.building.to_dict()))
        self.assertEqual(report.to_dict()["source"]["routing_sha256"], _sha(self.routes.to_dict()))
        self.assertEqual(report.to_dict()["source"]["production_program_sha256"], _sha(self.production))
        self.assertEqual(before, (self.building.to_dict(), self.layout.to_dict(), self.routes.to_dict(), self.raw))

    def test_missing_classification_and_unbound_opening_are_unknown_without_flow_type_inference(self):
        for missing in ("flow_movements", "transfers"):
            self.building, self.production, self.raw = case()
            self.raw[missing] = []
            with self.subTest(missing=missing):
                report = self.validate()
                self.assertEqual(report.permission_status, "UNKNOWN")
                self.assertEqual(report.status, "UNKNOWN")
        self.assertEqual(self.building.flows[0].type, "people")

    def test_tampered_room_path_cannot_hide_actual_forbidden_crossing(self):
        self.routes = FlowRoutingResult((replace(self.routes.routes[0], room_path=("source",)),))
        self.assertTrue(validate_flow_routes(self.building, self.layout, self.routes).ok)
        report = self.validate()
        self.assertEqual((report.status, report.geometry_status, report.permission_status), ("FAIL", "FAIL", "FAIL"))
        self.assertIn("transfer_movement_forbidden", {issue.code for issue in report.issues})
        self.assertEqual(report.to_dict()["crossings"][0]["opening_id"], "passbox_portal")

    def test_reverse_direction_is_not_authorized_by_forward_permission(self):
        self.raw["transfers"][0].update(kind="door", excerpt="paired door exercise permits personnel")
        self.raw["transfers"][0]["permissions"][0]["movement_kinds"] = ["personnel"]
        flow = replace(self.building.flows[0], from_ids=("target",), to_ids=("source",))
        self.building = replace(self.building, flows=(flow,))
        rebind(self.building, self.production, self.raw)
        self.routes = route_flows(self.building, self.layout)
        report = self.validate()
        self.assertEqual((report.geometry_status, report.permission_status), ("PASS", "FAIL"))
        self.assertIn("transfer_direction_forbidden", {issue.code for issue in report.issues})

    def test_source_rights_unknown_cannot_pass_but_cannot_hide_personnel_violation(self):
        self.production["sources"][0]["usage_rights"]["state"] = "UNKNOWN"
        rebind(self.building, self.production, self.raw)
        self.assertEqual(self.validate().permission_status, "FAIL")
        self.raw["transfers"][0].update(kind="door", excerpt="paired door exercise permits personnel")
        self.raw["transfers"][0]["permissions"][0]["movement_kinds"] = ["personnel"]
        report = self.validate()
        self.assertEqual((report.status, report.permission_status), ("UNKNOWN", "UNKNOWN"))

    def test_multiple_geometric_opening_bindings_are_not_resolved_lexically(self):
        second = replace(self.building.layout.doors[0], id="another_portal")
        self.building = replace(self.building, layout=replace(self.building.layout,
                                                           doors=(*self.building.layout.doors, second)))
        rebind(self.building, self.production, self.raw)
        report = self.validate()
        self.assertEqual(report.permission_status, "UNKNOWN")
        self.assertIn("ambiguous_opening_binding", {issue.code for issue in report.issues})
        self.assertEqual(set(report.to_dict()["crossings"][0]["matched_opening_ids"]),
                         {"another_portal", "passbox_portal"})

    def test_facade_window_is_never_a_transfer_portal(self):
        window = WindowSpec("facade_window", "source", "top", 1500, 900)
        self.building = replace(self.building, layout=replace(self.building.layout, windows=(window,)))
        self.raw["transfers"][0]["opening_id"] = "facade_window"
        rebind(self.building, self.production, self.raw)
        report = self.validate(include_windows=True)
        self.assertEqual(report.status, "FAIL")
        self.assertIn("window_is_not_transfer", {issue.code for issue in report.issues})

    def test_airlock_permissions_do_not_certify_chamber_interlocks_or_hvac(self):
        self.raw["transfers"][0].update(kind="personnel_airlock", excerpt="paired door exercise permits personnel")
        self.raw["transfers"][0]["permissions"][0]["movement_kinds"] = ["personnel"]
        report = self.validate()
        self.assertEqual((report.geometry_status, report.permission_status, report.mechanism_status, report.status),
                         ("PASS", "PASS", "UNKNOWN", "UNKNOWN"))
        self.assertIn("unsupported_transfer_mechanism", {issue.code for issue in report.issues})

    def test_route_budget_is_checked_before_actual_geometry_or_serialization(self):
        from layout_configurator.transfer_program import TransferProgramLimits

        with patch("layout_configurator.transfer_validation.validate_orthogonal_flow_routes",
                   side_effect=AssertionError("Geometry ran after request overflow")), \
                patch.object(FlowRoutingResult, "to_dict", side_effect=AssertionError("Routing serialized before count gate")):
            report = self.validate(limits=TransferProgramLimits(max_total_points=2))
        self.assertEqual(report.status, "UNKNOWN")
        self.assertIn("input_budget_exceeded", {issue.code for issue in report.issues})

    def test_actual_equipment_endpoint_identity_and_fresh_access_are_retained(self):
        source = self.building.to_dict()
        source["equipment"] = [{"id": "source_machine", "room_id": "source", "width_mm": 500, "depth_mm": 500,
                                 "clearance_front_mm": 100, "clearance_left_mm": 150, "clearance_right_mm": 150}]
        source["flows"][0]["from_ids"] = ["source_machine"]
        self.building = BuildingIR.from_mapping(source)
        self.production["operations"][0]["equipment_ids"] = ["source_machine"]
        rebind(self.building, self.production, self.raw)
        equipment = EquipmentLayoutResult((EquipmentPlacement("source_machine", "source", Rect(1000, 400, 500, 500)),))
        self.routes = route_orthogonal_flows(self.building, self.layout, equipment).routing
        self.assertEqual(validate_orthogonal_flow_routes(self.building, self.layout, self.routes, equipment).status, "PASS")
        report = self.validate(equipment=equipment)
        self.assertEqual((report.geometry_status, report.permission_status), ("PASS", "FAIL"))
        self.assertEqual(report.to_dict()["crossings"][0]["from_id"], "source_machine")
        actual = self.routes.routes[0]
        self.routes = FlowRoutingResult((replace(actual, points=((1250, 1510), *actual.points[1:])),))
        report = self.validate(equipment=equipment)
        self.assertEqual(report.geometry_status, "FAIL")
        self.assertIn("equipment_endpoint_mismatch", {issue["code"] for issue in report.to_dict()["geometry_validation"]["issues"]})

    def test_boundary_touch_is_not_a_crossing_and_boundary_run_never_authorizes(self):
        self.routes = FlowRoutingResult((replace(self.routes.routes[0], points=((2450, 1500), (3000, 1500), (2450, 1500))),))
        report = self.validate()
        self.assertEqual(report.geometry_status, "FAIL")
        self.assertEqual(report.to_dict()["crossings"], [])
        self.assertNotIn("transfer_movement_forbidden", {issue.code for issue in report.issues})
        self.routes = FlowRoutingResult((replace(self.routes.routes[0], points=((2450, 1500), (3000, 1500), (3000, 1800), (3550, 1800))),))
        report = self.validate()
        self.assertEqual(report.geometry_status, "FAIL")
        self.assertIn("ambiguous_actual_trace", {issue.code for issue in report.issues})

    def test_strict_raw_route_values_and_flag_do_not_escape_as_exceptions(self):
        for value in (True, "800", float("nan"), float("inf")):
            self.routes = FlowRoutingResult((replace(route_flows(self.building, self.layout).routes[0], minimum_clear_width_mm=value),))
            with self.subTest(value=value):
                self.assertEqual(self.validate().status, "FAIL")
        self.routes = route_flows(self.building, self.layout)
        for value in (1, "true", None):
            with self.subTest(flag=value):
                self.assertEqual(self.validate(include_windows=value).status, "FAIL")

    def test_window_source_is_retained_and_default_guard_is_not_bypassed(self):
        window = WindowSpec("facade_window", "source", "top", 1500, 900)
        self.building = replace(self.building, layout=replace(self.building.layout, windows=(window,)))
        self.raw["transfers"][0].update(kind="door", excerpt="paired door exercise permits personnel")
        self.raw["transfers"][0]["permissions"][0]["movement_kinds"] = ["personnel"]
        rebind(self.building, self.production, self.raw)
        report = self.validate()
        self.assertEqual((report.geometry_status, report.status), ("UNKNOWN", "UNKNOWN"))
        self.assertEqual(report.to_dict()["source"]["building_sha256"], _sha(self.building.to_dict()))
        self.assertEqual(self.validate(include_windows=True).status, "PASS")

    def test_declared_transfer_minimum_applies_to_actual_crossing_span(self):
        self.raw["transfers"][0].update(kind="door", excerpt="paired door exercise permits personnel", minimum_clear_width_mm=900)
        self.raw["transfers"][0]["permissions"][0]["movement_kinds"] = ["personnel"]
        self.routes = FlowRoutingResult((replace(self.routes.routes[0], points=((2450, 1550), (3000, 1550), (3550, 1550))),))
        self.assertEqual(validate_orthogonal_flow_routes(self.building, self.layout, self.routes).status, "PASS")
        report = self.validate()
        self.assertEqual((report.geometry_status, report.permission_status), ("PASS", "FAIL"))
        self.assertIn("transfer_crossing_too_narrow", {issue.code for issue in report.issues})

    def test_unavailable_wall_projection_cannot_downgrade_fresh_known_geometry_fail(self):
        self.layout = LayoutResult(1, {"source": Rect(0, 0, 2900, 3000), "target": Rect(3000, 0, 3000, 3000)})
        self.assertEqual(validate_orthogonal_flow_routes(self.building, self.layout, self.routes).status, "FAIL")
        self.raw["transfers"][0].update(kind="door", excerpt="paired door exercise permits personnel")
        self.raw["transfers"][0]["permissions"][0]["movement_kinds"] = ["personnel"]
        report = self.validate()
        self.assertEqual((report.geometry_status, report.status), ("FAIL", "FAIL"))

    def test_material_airlock_personnel_denial_and_mechanism_are_separate(self):
        self.raw["transfers"][0]["kind"] = "material_airlock"
        report = self.validate()
        self.assertEqual((report.geometry_status, report.permission_status, report.mechanism_status), ("PASS", "FAIL", "UNKNOWN"))
        self.assertIn("transfer_movement_forbidden", {issue.code for issue in report.issues})

    def test_bad_actual_numeric_geometry_is_invalid_and_not_an_unknown_capability(self):
        self.layout = LayoutResult(1, {"source": Rect(True, 0, 3000, 3000), "target": Rect(3000, 0, 3000, 3000)})
        report = self.validate()
        self.assertEqual(report.status, "FAIL")
        self.assertIn("invalid_request", {issue.code for issue in report.issues})

    def test_transfer_width_failure_cannot_imply_passbox_mechanism_pass(self):
        self.raw["transfers"][0]["minimum_clear_width_mm"] = 900
        self.routes = FlowRoutingResult((replace(self.routes.routes[0], points=((2450, 1550), (3000, 1550), (3550, 1550))),))
        report = self.validate()
        self.assertEqual((report.permission_status, report.mechanism_status), ("FAIL", "UNKNOWN"))
        self.assertIn("transfer_movement_forbidden", {issue.code for issue in report.issues})

    def test_geometry_work_budget_covers_source_checks_even_without_routes(self):
        from layout_configurator.transfer_program import TransferProgramLimits
        self.routes = FlowRoutingResult(())
        with patch("layout_configurator.transfer_validation.validate_orthogonal_flow_routes",
                   side_effect=AssertionError("Source geometry ran after its work budget gate")):
            report = self.validate(limits=TransferProgramLimits(max_geometry_checks=1))
        self.assertEqual(report.status, "UNKNOWN")
        self.assertIn("input_budget_exceeded", {issue.code for issue in report.issues})


if __name__ == "__main__":
    unittest.main()

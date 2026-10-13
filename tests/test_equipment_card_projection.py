"""Source-bound sidecars never silently change the legacy equipment producer."""

import copy
from dataclasses import replace
import importlib
import json
import unittest

from layout_configurator.equipment import EquipmentPlacement, clearance_rect
from layout_configurator.models import Rect
from test_engineering_library import digest, fixture_spec, library_fixture, source_content


class EquipmentCardProjectionTests(unittest.TestCase):
    def setUp(self):
        try:
            self.api = importlib.import_module("layout_configurator.equipment_card_projection")
            self.library_api = importlib.import_module("layout_configurator.engineering_library")
        except ModuleNotFoundError as exc:
            self.fail(f"The source-bound local equipment projection is missing: {exc}")
        self.spec = fixture_spec()
        self.raw = library_fixture(self.spec)
        self.placement = EquipmentPlacement("press", "work", Rect(2000, 3000, 1000, 800))

    def project(self, raw=None, **kwargs):
        return self.api.project_equipment_card(self.raw if raw is None else raw, "press_card",
                                              equipment=kwargs.pop("equipment", self.spec),
                                              placement=kwargs.pop("placement", self.placement), **kwargs)

    def rejected(self, raw=None, status="UNKNOWN", **kwargs):
        with self.assertRaises(self.api.EquipmentCardCapabilityError) as caught:
            self.project(raw, **kwargs)
        self.assertEqual(caught.exception.to_dict()["status"], status)
        return caught.exception.to_dict()

    def test_translated_body_operating_and_named_ports_have_exact_source_identity(self):
        before = self.spec.to_dict()
        legacy = self.placement.to_dict(self.spec)
        result = self.project().to_dict()
        self.assertEqual((result["status"], result["local_projection_status"]), ("DRAFT", "PASS"))
        self.assertEqual(result["equipment_sha256"], digest(before))
        self.assertEqual(result["body_points_mm"], [[2000, 3000], [3000, 3000], [3000, 3800], [2000, 3800], [2000, 3000]])
        operating = next(row for row in result["envelopes"] if row["role"] == "operating")
        self.assertEqual(operating["world_points_mm"][0], [1850, 2900])
        loading = next(row for row in result["interaction_points"] if row["role"] == "loading")
        self.assertEqual(loading["world_point_mm"], [2500, 3800])
        self.assertEqual(result["full_scenario_validation"], "UNKNOWN")
        self.assertEqual(result["three_dimensional_route_feasibility"], "UNKNOWN")
        self.assertEqual(result["engineering_acceptance"], "PENDING")
        self.assertEqual(self.spec.to_dict(), before)
        self.assertEqual(self.placement.to_dict(self.spec), legacy)
        self.assertEqual(clearance_rect(self.spec, self.placement.rect), Rect(1850, 2900, 1350, 1200))

    def test_literal_rotation_preserves_concave_panel_and_explicit_stop_action(self):
        placement = EquipmentPlacement("press", "work", Rect(2000, 3000, 800, 1000), True)
        self.rejected(placement=placement, scenario="maintenance")
        result = self.project(placement=placement, scenario="maintenance", active_action_ids=("stop",)).to_dict()
        panel = next(row for row in result["envelopes"] if row["role"] == "panel")
        self.assertEqual(panel["world_points_mm"], [[2800, 2600], [2800, 2900], [2500, 2900],
                                                    [2500, 3000], [2200, 3000], [2200, 2600], [2800, 2600]])
        self.assertEqual(len(panel["world_points_mm"]), 7)
        service = next(row for row in result["interaction_points"] if row["role"] == "service")
        self.assertEqual((service["world_point_mm"], service["world_direction"]), ([2000, 3500], "left"))
        self.assertEqual(result["active_actions"][0]["value"]["kind"], "production_stopped")
        self.rejected(scenario="installation_replacement")
        self.assertEqual(self.project(scenario="installation_replacement", active_action_ids=("temporary",)).to_dict()["local_projection_status"], "PASS")

    def test_changed_equipment_or_placement_bool_finite_types_and_rotation_rejected(self):
        for spec in (replace(self.spec, width_mm=999), replace(self.spec, width_mm=True),
                     replace(self.spec, depth_mm=10**400), replace(self.spec, rotation_allowed="false")):
            with self.subTest(spec=spec):
                self.rejected(equipment=spec, status="FAIL")
        for placement in (replace(self.placement, rotated="false"), replace(self.placement, equipment_id="other"),
                          replace(self.placement, room_id="other"), replace(self.placement, rect=Rect(0, 0, 900, 800)),
                          replace(self.placement, rect=Rect(float("nan"), 0, 1000, 800))):
            with self.subTest(placement=placement):
                self.rejected(placement=placement, status="FAIL")

    def test_rights_assumed_geometry_missing_selected_number_and_source_tamper_rejected(self):
        for state in ("RESTRICTED", "UNKNOWN"):
            with self.subTest(state=state):
                raw = copy.deepcopy(self.raw)
                raw["sources"][0]["usage_rights"]["state"] = state
                self.rejected(raw)
        raw = copy.deepcopy(self.raw)
        raw["cards"][0]["envelopes"][1]["evidence"]["basis"] = "assumption"
        self.rejected(raw)
        raw = copy.deepcopy(self.raw)
        raw["cards"][0]["dimensions"]["width_mm"]["measurement"].update(status="UNKNOWN", value=None, source_id=None)
        raw["cards"][0]["dimensions"]["width_mm"]["source_pointer"] = None
        self.rejected(raw)
        raw = copy.deepcopy(self.raw)
        raw["cards"][0]["envelopes"][0]["evidence"]["value"]["width"] = 900
        self.rejected(raw, status="FAIL")

    def test_unsupported_curves_3d_conditions_concave_body_and_unknown_actions_are_explicit(self):
        for kind in ("curve", "geometry_3d"):
            with self.subTest(kind=kind):
                raw = copy.deepcopy(self.raw)
                source = json.loads(raw["sources"][0]["content"])
                value = {key: source["envelopes"]["operating"][key] for key in ("id", "role", "scenario", "required_action_ids")}
                value.update(kind=kind, description="Supplied unsupported trajectory.")
                source["envelopes"]["operating"] = value
                source_content(raw, source)
                raw["cards"][0]["envelopes"][1]["evidence"]["value"] = value
                self.rejected(raw)
        raw = copy.deepcopy(self.raw)
        raw["cards"][0]["applicability"]["conditions"] = ["Voltage suitability requires engineering review."]
        self.rejected(raw)
        raw = copy.deepcopy(self.raw)
        source = json.loads(raw["sources"][0]["content"])
        value = copy.deepcopy(source["envelopes"]["panel"])
        value.update(id="body", role="body", scenario="all", required_action_ids=[])
        source["envelopes"]["body"] = value
        source_content(raw, source)
        raw["cards"][0]["envelopes"][0]["evidence"]["value"] = value
        self.rejected(raw)
        self.rejected(active_action_ids=("invented",), status="FAIL")
        self.rejected(capability="three_dimensional_v1")

    def test_forged_detached_contract_is_freshly_revalidated_and_output_is_detached(self):
        library = self.library_api.validate_engineering_library(self.raw).library
        result = self.project(library).to_dict()
        result["envelopes"][0]["world_points_mm"][0][0] = -999
        self.assertEqual(self.project(library).to_dict()["body_points_mm"][0], [2000, 3000])
        raw = library.to_dict()
        raw["sources"][0]["content"] = "forged"
        forged = self.library_api.EngineeringLibraryContract(json.dumps(raw))
        self.rejected(forged, status="FAIL")

    def test_structured_numbers_in_visual_reference_do_not_prove_dimensions(self):
        self.raw["sources"][0]["kind"] = "visual_reference"
        self.rejected()
        report = self.library_api.validate_engineering_library(self.raw)
        self.assertEqual(report.status, "UNKNOWN")
        self.assertIn("visual_reference_not_numeric", {issue.code for issue in report.issues})

    def test_positive_source_reservation_lost_at_world_precision_is_unknown(self):
        raw = copy.deepcopy(self.raw)
        row = next(item for item in raw["cards"][0]["envelopes"] if item["role"] == "service")
        row["evidence"]["value"].update(x=0.0, y=0.0, width=1e-300, depth=100.0)
        source = json.loads(raw["sources"][0]["content"])
        source["envelopes"]["service"] = copy.deepcopy(row["evidence"]["value"])
        source_content(raw, source)
        self.assertEqual(self.library_api.validate_engineering_library(raw).binding_status, "PASS")
        with self.assertRaises(self.api.EquipmentCardCapabilityError) as caught:
            self.project(raw, scenario="maintenance", active_action_ids=("stop",))
        self.assertEqual(caught.exception.status, "UNKNOWN")
        self.assertEqual(caught.exception.code, "unrepresentable_geometry")

    def test_actual_legacy_packer_and_independent_readback_are_unchanged_by_sidecar(self):
        from layout_configurator.building import BuildingIR
        from layout_configurator.equipment import place_equipment, validate_equipment_layout
        from layout_configurator.models import LayoutResult
        building = BuildingIR.from_mapping({"layout": {"project_name": "Equipment sidecar boundary", "grid_mm": 100,
            "boundary": {"width": 6000, "height": 5000}, "rooms": [{"id": "work", "target_area": 30}]},
            "equipment": [self.spec.to_dict()]})
        layout = LayoutResult(1, {"work": Rect(0, 0, 6000, 5000)})
        options = {"seed": 42, "deterministic_units": 0.01, "time_limit_seconds": 2}
        before = place_equipment(building, layout, **options)
        self.assertTrue(validate_equipment_layout(building, layout, before).ok)
        sidecar = self.project(equipment=building.equipment[0], placement=before.placements[0]).to_dict()
        after = place_equipment(building, layout, **options)
        self.assertTrue(validate_equipment_layout(building, layout, after).ok)
        self.assertEqual(before.to_dict(building), after.to_dict(building))
        self.assertEqual(sidecar["equipment_sha256"], digest(building.equipment[0].to_dict()))

    def test_sourced_port_role_and_required_stop_premise_cannot_be_relabelled(self):
        cases = [(lambda raw: raw["cards"][0]["interaction_points"][0].update(role="unloading"), "production"),
                 (lambda raw: raw["cards"][0]["envelopes"][3].update(required_action_ids=[]), "maintenance")]
        for change, scenario in cases:
            with self.subTest(scenario=scenario):
                raw = copy.deepcopy(self.raw)
                change(raw)
                self.rejected(raw, scenario=scenario, status="FAIL")


if __name__ == "__main__":
    unittest.main()

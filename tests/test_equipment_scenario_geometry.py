"""Actual source-bound geometry, conditional actions and incomplete scenarios."""

import copy
from dataclasses import replace
import importlib
import json
import unittest
from unittest.mock import patch

from layout_configurator.building import BuildingIR
from layout_configurator.equipment import EquipmentLayoutResult, EquipmentPlacement
from layout_configurator.models import LayoutResult, Rect
from layout_configurator.orthogonal import OrthogonalPolygon
from test_engineering_library import digest, fixture_spec, library_fixture, source_content


def scenario_fixture(*, second_x=6000, second_y=3000, second_size=(1000, 800)):
    first = replace(fixture_spec(), clearance_front_mm=0.0, clearance_back_mm=0.0,
                    clearance_left_mm=0.0, clearance_right_mm=0.0)
    second = replace(first, id="neighbor", width_mm=float(second_size[0]), depth_mm=float(second_size[1]))
    building = BuildingIR.from_mapping({"layout": {"project_name": "Declared equipment scenario fixture",
        "boundary": {"width": 10000, "height": 8000}, "rooms": [{"id": "work", "target_area": 80}]},
        "equipment": [first.to_dict(), second.to_dict()]})
    layout = LayoutResult(1, {"work": Rect(0, 0, 10000, 8000)})
    equipment = EquipmentLayoutResult((EquipmentPlacement("press", "work", Rect(3000, 3000, 1000, 800)),
                                       EquipmentPlacement("neighbor", "work", Rect(second_x, second_y, *second_size))))
    library = {"version": "0.1", "id": "scenario_library", "revision": "R01", "sources": [], "cards": [], "templates": []}
    mapping = {}
    for spec in building.equipment:
        part = library_fixture(spec)
        source = part["sources"][0]
        source_id = "source_" + spec.id
        source["id"] = source_id
        card = part["cards"][0]
        card["id"] = "card_" + spec.id
        card["equipment_binding"]["source_id"] = source_id
        for row in card["dimensions"].values():
            if row["measurement"]["source_id"] is not None:
                row["measurement"]["source_id"] = source_id
        for key in ("envelopes", "interaction_points", "scenario_actions"):
            for row in card[key]:
                row["evidence"]["source_id"] = source_id
        library["sources"].append(source)
        library["cards"].append(card)
        mapping[spec.id] = card["id"]
    return building, layout, equipment, library, mapping


def update_record(library, card_index, collection, row_index, value):
    """Change the actual supplied structured source and its bound card together."""
    row = library["cards"][card_index][collection][row_index]
    evidence = row["evidence"]
    evidence["value"] = copy.deepcopy(value)
    source = library["sources"][card_index]
    content = json.loads(source["content"])
    prefix = {"envelopes": "envelopes", "interaction_points": "points", "scenario_actions": "actions"}[collection]
    content[prefix][row["id"]] = copy.deepcopy(value)
    # Source helper operates on a one-source wrapper and recomputes real bytes.
    source_content({"sources": [source]}, content)


def expanded_fixture(rectangles):
    building, layout, equipment, library, mapping = scenario_fixture()
    specs = list(building.equipment)
    placements = [replace(item, rect=rectangles[index]) for index, item in enumerate(equipment.placements)]
    for index in range(2, len(rectangles)):
        spec = replace(specs[0], id=f"machine_{index}")
        specs.append(spec)
        placements.append(replace(placements[0], equipment_id=spec.id, rect=rectangles[index]))
        card, source = copy.deepcopy(library["cards"][0]), copy.deepcopy(library["sources"][0])
        source_id = "source_" + spec.id
        source["id"] = source_id
        content = json.loads(source["content"])
        content["equipment"] = spec.to_dict()
        source_content({"sources": [source]}, content)
        card["id"] = "card_" + spec.id
        card["equipment_binding"].update(id=spec.id, sha256=digest(spec.to_dict()), source_id=source_id)
        card["applicability"]["equipment_ids"] = [spec.id]
        for quantity in card["dimensions"].values():
            if quantity["measurement"]["source_id"] is not None:
                quantity["measurement"]["source_id"] = source_id
        for collection in ("envelopes", "interaction_points", "scenario_actions"):
            for row in card[collection]:
                row["evidence"]["source_id"] = source_id
        library["sources"].append(source)
        library["cards"].append(card)
        mapping[spec.id] = card["id"]
    return replace(building, equipment=tuple(specs)), layout, EquipmentLayoutResult(tuple(placements)), library, mapping


class EquipmentScenarioGeometryTests(unittest.TestCase):
    def setUp(self):
        try:
            self.api = importlib.import_module("layout_configurator.equipment_scenario_geometry")
        except ModuleNotFoundError as exc:
            self.fail(f"The independent equipment scenario geometry checker is missing: {exc}")
        self.building, self.layout, self.equipment, self.library, self.mapping = scenario_fixture()

    def validate(self, **kwargs):
        return self.api.validate_equipment_scenarios(kwargs.pop("building", self.building), kwargs.pop("layout", self.layout),
            kwargs.pop("equipment", self.equipment), library=kwargs.pop("library", self.library),
            card_by_equipment=kwargs.pop("card_by_equipment", self.mapping),
            maintenance_targets=kwargs.pop("maintenance_targets", ("press",)),
            installation_targets=kwargs.pop("installation_targets", ("press",)), **kwargs)

    def codes(self, report, scenario=None):
        raw = report.to_dict()
        return {row["code"] for row in (raw["scenarios"][scenario]["issues"] if scenario else raw["issues"])}

    def test_three_local_reports_are_source_bound_and_installation_never_full_pass(self):
        before = copy.deepcopy(self.library)
        result = self.validate(actions_by_scenario={"maintenance": {"press": ["stop"]},
                                                    "installation_replacement": {"press": ["temporary"]}})
        raw = result.to_dict()
        self.assertEqual(raw["status"], "UNKNOWN")
        self.assertFalse(result.ok)
        self.assertEqual(raw["source"]["building_sha256"], digest(self.building.to_dict()))
        self.assertEqual(set(raw["scenarios"]), {"production", "maintenance", "installation_replacement"})
        for scenario in raw["scenarios"].values():
            self.assertEqual(scenario["local_geometry_status"], "PASS")
            self.assertEqual(scenario["full_scenario_validation"], "UNKNOWN")
            self.assertEqual(scenario["engineering_acceptance"], "PENDING")
        self.assertEqual(raw["scenarios"]["installation_replacement"]["full_path_validation"], "UNKNOWN")
        self.assertEqual(raw["scenarios"]["maintenance"]["actions"][0]["execution_verification"], "UNKNOWN")
        raw["scenarios"]["production"]["geometry"][0]["world_points_mm"][0][0] = -999
        self.assertEqual(self.library, before)
        self.assertNotEqual(result.to_dict()["scenarios"]["production"]["geometry"][0]["world_points_mm"][0][0], -999)

    def test_actual_production_envelope_overlap_fails_without_physical_body_overlap(self):
        _, _, equipment, _, _ = scenario_fixture(second_x=4100)
        report = self.validate(equipment=equipment)
        self.assertEqual(report.status, "FAIL")
        self.assertIn("scenario_envelope_overlap", self.codes(report, "production"))
        self.assertNotIn("canonical_body_overlap", self.codes(report, "production"))
        self.assertEqual(report.to_dict()["canonical_equipment"]["status"], "PASS")

    def test_blocked_service_known_collision_dominates_missing_stop_and_unknown_width(self):
        _, _, equipment, _, _ = scenario_fixture(second_x=4200)
        width = self.library["cards"][0]["dimensions"]["width_mm"]
        width["measurement"].update(status="UNKNOWN", value=None, source_id=None)
        width["source_pointer"] = None
        report = self.validate(equipment=equipment)
        maintenance = report.to_dict()["scenarios"]["maintenance"]
        self.assertEqual((maintenance["status"], maintenance["local_geometry_status"]), ("FAIL", "FAIL"))
        self.assertEqual(maintenance["declared_premises_status"], "UNKNOWN")
        self.assertIn("service_blocked", self.codes(report, "maintenance"))
        self.assertIn("missing_scenario_action", self.codes(report, "maintenance"))

    def test_rotated_concave_panel_is_blocked_using_actual_world_contour(self):
        equipment = EquipmentLayoutResult((EquipmentPlacement("press", "work", Rect(3000, 3000, 800, 1000), True),
                                           EquipmentPlacement("neighbor", "work", Rect(3200, 2600, 1000, 800))))
        report = self.validate(equipment=equipment, actions_by_scenario={"maintenance": {"press": ["stop"]}})
        rows = report.to_dict()["scenarios"]["maintenance"]["geometry"]
        panel = next(row for row in rows if row["equipment_id"] == "press" and row["role"] == "panel")
        self.assertEqual(panel["world_points_mm"], [[3800, 2600], [3800, 2900], [3500, 2900],
            [3500, 3000], [3200, 3000], [3200, 2600], [3800, 2600]])
        self.assertIn("panel_blocked", self.codes(report, "maintenance"))

    def test_concave_notch_is_not_a_bounding_box_collision(self):
        building, layout, equipment, library, mapping = scenario_fixture(second_x=2925, second_y=3025, second_size=(50, 50))
        for index in (2, 4):
            library["cards"][0]["envelopes"][index]["evidence"].update(status="UNKNOWN", value=None, source_id=None, source_pointer=None)
        report = self.validate(building=building, layout=layout, equipment=equipment, library=library,
            card_by_equipment=mapping, actions_by_scenario={"maintenance": {"press": ["stop"], "neighbor": ["stop"]}})
        maintenance = report.to_dict()["scenarios"]["maintenance"]
        self.assertEqual(maintenance["local_geometry_status"], "UNKNOWN")
        self.assertNotIn("panel_blocked", self.codes(report, "maintenance"))
        panel = next(row for row in maintenance["geometry"] if row["equipment_id"] == "press" and row["role"] == "panel")
        self.assertEqual(len(panel["world_points_mm"]), 7)

    def test_explicit_neighbor_stop_suppresses_only_operating_reservation_never_body(self):
        report = self.validate(actions_by_scenario={"maintenance": {"press": ["stop"], "neighbor": ["stop"]}})
        maintenance = report.to_dict()["scenarios"]["maintenance"]
        self.assertEqual(maintenance["conditionally_inactive_operating_ids"], ["neighbor"])
        self.assertIn("neighbor", [row["equipment_id"] for row in maintenance["geometry"] if row["role"] == "body"])
        _, _, equipment, _, _ = scenario_fixture(second_x=4200)
        report = self.validate(equipment=equipment, actions_by_scenario={"maintenance": {"press": ["stop"], "neighbor": ["stop"]}})
        self.assertIn("service_blocked", self.codes(report, "maintenance"))

    def test_temporary_action_text_cannot_remove_a_neighbor(self):
        value = copy.deepcopy(self.library["cards"][1]["scenario_actions"][0]["evidence"]["value"])
        value.update(kind="temporary_action", description="Remove the neighboring machine temporarily.")
        update_record(self.library, 1, "scenario_actions", 0, value)
        report = self.validate(actions_by_scenario={"maintenance": {"press": ["stop"], "neighbor": ["stop"]}})
        self.assertEqual(report.to_dict()["scenarios"]["maintenance"]["conditionally_inactive_operating_ids"], [])
        self.assertIn("neighbor", [row["equipment_id"] for row in report.to_dict()["scenarios"]["maintenance"]["geometry"] if row["role"] == "body"])

    def test_missing_mapping_placement_or_unknown_envelope_remains_incomplete(self):
        for changes in ({"card_by_equipment": {"press": "card_press"}},
                        {"equipment": EquipmentLayoutResult((self.equipment.placements[0],))},
                        {"maintenance_targets": (), "installation_targets": ()}):
            with self.subTest(changes=changes):
                report = self.validate(**changes)
                self.assertEqual(report.status, "UNKNOWN")
                self.assertEqual(report.to_dict()["scenarios"]["maintenance"]["local_geometry_status"], "UNKNOWN")

    def test_failed_library_binding_never_promotes_unbound_contours_but_body_collision_survives(self):
        self.library["cards"][0]["envelopes"][1]["evidence"]["value"]["width"] = 9000
        report = self.validate()
        self.assertEqual(report.status, "FAIL")
        for scenario in report.to_dict()["scenarios"].values():
            self.assertTrue(all(row["role"] == "body" for row in scenario["geometry"]))
            self.assertNotIn("scenario_envelope_overlap", {row["code"] for row in scenario["issues"]})
        _, _, equipment, _, _ = scenario_fixture(second_x=3500)
        collision = self.validate(equipment=equipment)
        self.assertIn("canonical_body_overlap", self.codes(collision, "production"))

    def test_original_directional_clearance_collision_is_retained(self):
        spec = replace(self.building.equipment[0], clearance_right_mm=500.0)
        building = replace(self.building, equipment=(spec, self.building.equipment[1]))
        _, _, equipment, _, _ = scenario_fixture(second_x=4100)
        report = self.validate(building=building, equipment=equipment)
        self.assertEqual(report.status, "FAIL")
        self.assertIn("clearance_collision", {row["code"] for row in report.to_dict()["canonical_equipment"]["report"]["issues"]})
        for scenario in report.to_dict()["scenarios"].values():
            self.assertEqual(scenario["local_geometry_status"], "FAIL")

    def test_stopped_neighbor_retains_literal_points_and_exact_port_direction(self):
        value = copy.deepcopy(self.library["cards"][1]["interaction_points"][0]["evidence"]["value"])
        value.update(x=-2500.0, y=400.0)
        update_record(self.library, 1, "interaction_points", 0, value)
        report = self.validate(actions_by_scenario={"maintenance": {"press": ["stop"], "neighbor": ["stop"]}})
        self.assertIn("interaction_point_blocked", self.codes(report, "maintenance"))
        equipment = replace(self.equipment, placements=(replace(self.equipment.placements[0],
            rect=Rect(3000, 3000, 800, 1000), rotated=True), self.equipment.placements[1]))
        production = self.validate(equipment=equipment).to_dict()["scenarios"]["production"]
        port = next(row for row in production["interaction_points"] if row["equipment_id"] == "press" and row["id"] == "loading")
        self.assertEqual((port["source_direction"], port["world_direction"]), ("front", "left"))
        self.assertEqual(port["world_point_mm"], [3000, 3500])

    def test_source_point_world_bound_excludes_out_of_capability_coordinates(self):
        value = copy.deepcopy(self.library["cards"][0]["interaction_points"][0]["evidence"]["value"])
        value.update(x=1000000.0)
        update_record(self.library, 0, "interaction_points", 0, value)
        report = self.validate()
        self.assertIn("world_coordinate_bound", self.codes(report, "production"))
        self.assertFalse(any(row["equipment_id"] == "press" and row["id"] == "loading"
            for row in report.to_dict()["scenarios"]["production"]["interaction_points"]))

    def test_nested_raw_source_budget_guard_precedes_json_allocation(self):
        content = "[" * 40 + "0" + "]" * 40
        self.library["sources"][0]["content"] = content
        with patch.object(self.api, "_strict_json", side_effect=AssertionError("Raw depth guard must precede JSON allocation")):
            report = self.validate()
        self.assertEqual(report.status, "UNKNOWN")
        self.assertIn("input_budget_exceeded", self.codes(report))

    def test_restricted_equipment_binding_keeps_provenance_unknown_and_literal_fail(self):
        source = copy.deepcopy(self.library["sources"][0])
        source["id"] = "restricted_binding"
        source["usage_rights"]["state"] = "RESTRICTED"
        self.library["sources"].append(source)
        self.library["cards"][0]["equipment_binding"]["source_id"] = source["id"]
        report = self.validate(actions_by_scenario={"maintenance": {"press": ["stop"]}})
        self.assertEqual(report.to_dict()["source_binding"]["status"], "UNKNOWN")
        self.assertEqual(report.to_dict()["scenarios"]["production"]["local_geometry_status"], "UNKNOWN")
        _, _, equipment, _, _ = scenario_fixture(second_x=4200)
        collision = self.validate(equipment=equipment)
        self.assertEqual(collision.status, "FAIL")
        self.assertIn("service_blocked", self.codes(collision, "maintenance"))

    def test_unsupported_installation_and_assumed_geometry_do_not_become_pass(self):
        original = copy.deepcopy(self.library)
        value = {key: self.library["cards"][0]["envelopes"][5]["evidence"]["value"][key]
                 for key in ("id", "role", "scenario", "required_action_ids")}
        value.update(kind="geometry_3d", description="No independently supported 3D replacement route supplied.")
        update_record(self.library, 0, "envelopes", 5, value)
        report = self.validate()
        self.assertEqual(report.to_dict()["scenarios"]["installation_replacement"]["local_geometry_status"], "UNKNOWN")
        self.assertIn("unverified_scenario_geometry", self.codes(report, "installation_replacement"))
        original["cards"][0]["envelopes"][1]["evidence"]["basis"] = "assumption"
        report = self.validate(library=original)
        self.assertEqual(report.to_dict()["scenarios"]["production"]["local_geometry_status"], "UNKNOWN")

    def test_dense_bounded_collision_report_retains_fail_without_unbounded_output(self):
        building, layout, equipment, library, mapping = expanded_fixture([Rect(3000, 3000, 1000, 800)] * 32)
        report = self.validate(building=building, layout=layout, equipment=equipment, library=library, card_by_equipment=mapping)
        raw = report.to_dict()
        self.assertEqual(report.status, "FAIL")
        self.assertEqual(raw["source_binding"]["status"], "PASS")
        self.assertIn("canonical_body_overlap", self.codes(report))
        self.assertLessEqual(len(json.dumps(raw, ensure_ascii=False).encode("utf-8")), 4194304)
        self.assertEqual(raw["full_scenario_validation"], "UNKNOWN")

    def test_long_source_pointer_inventory_is_explicitly_incomplete_when_delivery_is_bounded(self):
        rectangles = [Rect(4000 + 5000 * (index % 8), 4000 + 5000 * (index // 8), 1000, 800) for index in range(32)]
        building, _, equipment, library, mapping = expanded_fixture(rectangles)
        room = replace(building.layout.rooms[0], target_area_m2=10000.0, min_area_m2=9999.0, max_area_m2=10001.0)
        building = replace(building, layout=replace(building.layout, boundary=replace(building.layout.boundary,
            width_mm=100000, height_mm=100000), rooms=(room,)))
        layout = LayoutResult(1, {"work": Rect(0, 0, 100000, 100000)})
        prefix = "p" * 900
        for card, source in zip(library["cards"], library["sources"]):
            template = card["interaction_points"][0]
            rows, points = [], {}
            for index in range(32):
                row = copy.deepcopy(template)
                row["id"] = "point_" + str(index)
                row["evidence"]["value"]["id"] = row["id"]
                row["evidence"]["source_pointer"] = "/points/" + prefix + "/" + row["id"]
                rows.append(row)
                points[row["id"]] = copy.deepcopy(row["evidence"]["value"])
            card["interaction_points"] = rows
            content = json.loads(source["content"])
            content["points"] = {prefix: points}
            source_content({"sources": [source]}, content)
        report = self.validate(building=building, layout=layout, equipment=equipment, library=library, card_by_equipment=mapping)
        raw = report.to_dict()
        self.assertEqual(raw["source_binding"]["status"], "PASS")
        self.assertEqual(raw["canonical_equipment"]["status"], "PASS")
        self.assertEqual(raw["output_inventory"]["status"], "INCOMPLETE")
        self.assertIn("output_budget_exceeded", self.codes(report))
        self.assertEqual(report.status, "UNKNOWN")
        for scenario in raw["scenarios"].values():
            self.assertEqual(scenario["local_geometry_status"], "UNKNOWN")
        self.assertLessEqual(len(json.dumps(raw, ensure_ascii=False).encode("utf-8")), 4194304)

    def test_exact_orthogonal_room_notch_rejects_reserved_area_outside_real_host(self):
        room = OrthogonalPolygon(((0, 0), (10000, 0), (10000, 4000), (5000, 4000), (5000, 8000), (0, 8000)))
        source_room = replace(self.building.layout.rooms[0], target_area_m2=60.0, min_area_m2=58.0, max_area_m2=62.0)
        building = replace(self.building, layout=replace(self.building.layout, rooms=(source_room,)))
        report = self.validate(building=building, layout=LayoutResult(1, {"work": room}, geometry_version="0.2"))
        self.assertIn("envelope_outside_host", self.codes(report, "production"))
        self.assertEqual(report.to_dict()["canonical_equipment"]["status"], "PASS")

    def test_named_point_blockage_uses_source_point_not_assumed_radius(self):
        value = copy.deepcopy(self.library["cards"][0]["interaction_points"][0]["evidence"]["value"])
        value.update(x=1500.0)
        update_record(self.library, 0, "interaction_points", 0, value)
        _, _, equipment, _, _ = scenario_fixture(second_x=4400, second_y=3700)
        report = self.validate(equipment=equipment)
        self.assertIn("interaction_point_blocked", self.codes(report, "production"))
        blocked = next(row for row in report.to_dict()["scenarios"]["production"]["issues"] if row["code"] == "interaction_point_blocked")
        self.assertEqual(blocked["world_point_mm"], [4500, 3800])

    def test_panel_and_service_access_inside_room_is_blocked_by_actual_wall_band(self):
        equipment = replace(self.equipment, placements=(replace(self.equipment.placements[0],
            rect=Rect(450, 3000, 1000, 800)), self.equipment.placements[1]))
        report = self.validate(equipment=equipment, actions_by_scenario={"maintenance": {"press": ["stop"]}})
        self.assertEqual(report.to_dict()["canonical_equipment"]["status"], "PASS")
        self.assertIn("panel_wall_blocked", self.codes(report, "maintenance"))
        self.assertIn("service_wall_blocked", self.codes(report, "maintenance"))
        self.assertNotIn("envelope_outside_host", self.codes(report, "maintenance"))

    def test_strict_unknown_ids_bool_nan_and_aggregate_limits_before_geos(self):
        bad = [dict(card_by_equipment={"press": "card_press", "neighbor": "invented"}),
               dict(maintenance_targets=("invented",)), dict(maintenance_targets=("press", "press")),
               dict(actions_by_scenario={"maintenance": {"press": ["invented"]}}),
               dict(equipment=replace(self.equipment, placements=(replace(self.equipment.placements[0], rotated="false"), self.equipment.placements[1]))),
               dict(equipment=replace(self.equipment, placements=(replace(self.equipment.placements[0], rect=Rect(float("nan"), 0, 1000, 800)), self.equipment.placements[1])))]
        for changes in bad:
            with self.subTest(changes=changes):
                self.assertEqual(self.validate(**changes).status, "FAIL")
        with patch.object(self.api, "Polygon", side_effect=AssertionError("Aggregate guard must precede GEOS")):
            report = self.validate(limits=replace(self.api.EquipmentScenarioLimits(), max_total_utf8_bytes=32))
        self.assertEqual(report.status, "UNKNOWN")
        self.assertIn("input_budget_exceeded", self.codes(report))
        with patch.object(self.api, "Polygon", side_effect=AssertionError("Pair guard must precede GEOS")):
            report = self.validate(limits=replace(self.api.EquipmentScenarioLimits(), max_pair_checks=1))
        self.assertEqual(report.status, "UNKNOWN")

    def test_canonical_clearance_world_bound_precedes_any_library_geometry(self):
        building = replace(self.building, equipment=(replace(self.building.equipment[0],
            clearance_right_mm=1000000.0), self.building.equipment[1]))
        with patch.object(self.api, "validate_engineering_library", side_effect=AssertionError("Original clearance guard must precede library geometry")):
            report = self.validate(building=building)
        self.assertEqual(report.status, "FAIL")
        self.assertIn("invalid_geometry", self.codes(report))

    def test_reserved_rectangle_lost_by_world_precision_cannot_pass_geometry(self):
        value = copy.deepcopy(self.library["cards"][0]["envelopes"][2]["evidence"]["value"])
        value.update(x=0.0, y=0.0, width=1e-300, depth=100.0)
        update_record(self.library, 0, "envelopes", 2, value)
        report = self.validate()
        self.assertNotEqual(report.to_dict()["scenarios"]["maintenance"]["local_geometry_status"], "PASS")
        self.assertIn("unrepresentable_scenario_geometry", self.codes(report, "maintenance"))

    def test_source_body_lost_by_world_precision_is_unknown_before_geometry(self):
        building, layout, equipment, library, mapping = scenario_fixture(second_size=(1e-300, 800))
        with patch.object(self.api, "Polygon", side_effect=AssertionError("Collapsed canonical body reached geometry")):
            report = self.validate(building=building, layout=layout, equipment=equipment,
                                   library=library, card_by_equipment=mapping)
        self.assertEqual(report.status, "UNKNOWN")
        self.assertIn("unrepresentable_canonical_geometry", self.codes(report))


if __name__ == "__main__":
    unittest.main()

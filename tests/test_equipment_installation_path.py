"""Explicit source-bound whole-body installation paths; no pathfinder fixtures."""

import copy
from dataclasses import replace
import hashlib
import importlib
import json
import unittest
from unittest.mock import patch

from layout_configurator.building import BuildingIR
from layout_configurator.equipment import EquipmentLayoutResult, EquipmentPlacement
from layout_configurator.models import LayoutResult, Rect, WindowSpec
from test_engineering_library import digest, fixture_spec, library_fixture, source_content


def path_fixture(*, neighbor_y=4500.0, rotated=False, target_width=1000.0):
    """Two actual rooms, explicit doors, installed bodies and supplied clear staging."""
    target = replace(fixture_spec(), width_mm=target_width, clearance_front_mm=0.0,
                     clearance_back_mm=0.0, clearance_left_mm=0.0, clearance_right_mm=0.0)
    neighbor = replace(target, id="neighbor", room_id="entry", width_mm=1000.0)
    building = BuildingIR.from_mapping({"layout": {
        "project_name": "Supplied installation path reconstruction",
        "boundary": {"width": 12000, "height": 6000}, "entry_room": "entry",
        "external_entry": {"id": "external", "room_id": "entry", "side": "left",
                           "offset_mm": 3000, "width_mm": 1600},
        "door_width_mm": 1600,
        "doors": [{"id": "internal", "room_a": "entry", "room_b": "work",
                   "offset_mm": 3000, "width_mm": 1600}],
        "rooms": [{"id": "entry", "target_area": 36, "required_adjacency": ["work"]},
                  {"id": "work", "target_area": 36}]},
        "equipment": [target.to_dict(), neighbor.to_dict()]})
    layout = LayoutResult(1, {"entry": Rect(0.0, 0.0, 6000.0, 6000.0),
                              "work": Rect(6000.0, 0.0, 6000.0, 6000.0)})
    width, depth = (800.0, target_width) if rotated else (target_width, 800.0)
    equipment = EquipmentLayoutResult((
        EquipmentPlacement("press", "work", Rect(9000.0, 2600.0, width, depth), rotated),
        EquipmentPlacement("neighbor", "entry", Rect(3000.0, neighbor_y, 1000.0, 800.0))))
    library = {"version": "0.1", "id": "path_library", "revision": "R01",
               "sources": [], "cards": [], "templates": []}
    mapping = {}
    for spec in building.equipment:
        part = library_fixture(spec)
        source, card = part["sources"][0], part["cards"][0]
        source["id"] = "source_" + spec.id
        card["id"] = "card_" + spec.id
        card["equipment_binding"]["source_id"] = source["id"]
        for row in card["dimensions"].values():
            if row["measurement"]["source_id"] is not None:
                row["measurement"]["source_id"] = source["id"]
        for key in ("envelopes", "interaction_points", "scenario_actions"):
            for row in card[key]:
                row["evidence"]["source_id"] = source["id"]
        library["sources"].append(source)
        library["cards"].append(card)
        mapping[spec.id] = card["id"]
    staging = {"kind": "exterior_staging_2d", "unit": "mm",
               "floor": {"kind": "rectangle", "x": -2000.0, "y": 1800.0,
                         "width": 2000.0, "depth": 2400.0},
               "obstacles": [], "inventory_scope": "supplied_2d_only"}
    content = json.loads(library["sources"][0]["content"])
    content["installation_path"] = {"staging": staging}
    source_content({"sources": [library["sources"][0]]}, content)
    request = {"version": "0.1", "id": "install_press", "equipment_id": "press",
               "card_id": "card_press", "external_entry_id": "external", "source": {},
               "poses": [{"x_mm": x, "y_mm": 2600.0, "rotated": rotated}
                         for x in (-1200.0, 500.0, 6500.0, 9000.0)],
               "transport_clearance": None,
               "exterior_staging": {"source_id": "source_press",
                   "source_sha256": library["sources"][0]["sha256"],
                   "source_pointer": "/installation_path/staging", "value": staging}}
    refresh_binding(request, building, layout, equipment, library)
    actions = {"installation_replacement": {"press": ["temporary"]}}
    return building, layout, equipment, library, mapping, request, actions


def refresh_binding(request, building, layout, equipment, library):
    request["source"] = {"building_sha256": digest(building.to_dict()),
                         "layout_sha256": digest(layout.to_dict()),
                         "equipment_sha256": digest(equipment.to_dict(building)),
                         "library_sha256": digest(library)}
    for key in ("exterior_staging", "transport_clearance"):
        row = request.get(key)
        if row is not None:
            source = next(item for item in library["sources"] if item["id"] == row["source_id"])
            row["source_sha256"] = source["sha256"]


class EquipmentInstallationPathTests(unittest.TestCase):
    def setUp(self):
        try:
            self.api = importlib.import_module("layout_configurator.equipment_installation_path")
        except ModuleNotFoundError as exc:
            self.fail(f"The explicit whole-body installation path checker is missing: {exc}")
        self.assertTrue(callable(getattr(self.api, "validate_equipment_installation_path", None)),
                        "The explicit whole-body installation path API is missing")
        (self.building, self.layout, self.equipment, self.library, self.mapping,
         self.request, self.actions) = path_fixture()

    def validate(self, **kwargs):
        return self.api.validate_equipment_installation_path(kwargs.pop("request", self.request),
            building=kwargs.pop("building", self.building), layout=kwargs.pop("layout", self.layout),
            equipment=kwargs.pop("equipment", self.equipment), library=kwargs.pop("library", self.library),
            card_by_equipment=kwargs.pop("card_by_equipment", self.mapping),
            actions_by_scenario=kwargs.pop("actions_by_scenario", self.actions), **kwargs)

    def codes(self, report):
        return {row["code"] for row in report.to_dict()["issues"]}

    def rebind(self):
        refresh_binding(self.request, self.building, self.layout, self.equipment, self.library)

    def test_complete_narrow_body_path_binds_actual_sources_without_sweeping_stationary_reservation(self):
        before = copy.deepcopy((self.building, self.layout, self.equipment, self.library, self.request))
        report = self.validate()
        raw = report.to_dict()
        self.assertEqual(raw["local_2d_path_status"], "PASS")
        self.assertEqual(raw["entry_validation"]["status"], "PASS")
        self.assertEqual([row["opening_id"] for row in raw["opening_crossings"]], ["external", "internal"])
        self.assertEqual(len(raw["segments"]), len(self.request["poses"]) - 1)
        self.assertEqual(raw["source"], self.request["source"])
        self.assertEqual(raw["status"], "UNKNOWN")
        self.assertFalse(report.ok)
        self.assertEqual(raw["three_dimensional_feasibility"], "UNKNOWN")
        self.assertEqual(raw["full_scenario_validation"], "UNKNOWN")
        self.assertEqual(raw["engineering_acceptance"], "PENDING")
        self.assertEqual((self.building, self.layout, self.equipment, self.library, self.request), before)

    def test_fixed_permitted_ninety_degree_installed_orientation_has_exact_body_sweep(self):
        (self.building, self.layout, self.equipment, self.library, self.mapping,
         self.request, self.actions) = path_fixture(rotated=True)
        raw = self.validate().to_dict()
        self.assertEqual(raw["local_2d_path_status"], "PASS")
        self.assertEqual(raw["moving_body"]["width_mm"], 800.0)
        self.assertEqual(raw["moving_body"]["depth_mm"], 1000.0)

    def test_actual_entry_and_internal_openings_reject_insufficient_transverse_width(self):
        for kind in ("entry", "internal"):
            with self.subTest(kind=kind):
                building = replace(self.building, layout=replace(self.building.layout,
                    external_entry=replace(self.building.layout.external_entry, width_mm=700.0)
                    if kind == "entry" else self.building.layout.external_entry,
                    doors=(replace(self.building.layout.doors[0], width_mm=700.0),)
                    if kind == "internal" else self.building.layout.doors))
                request = copy.deepcopy(self.request)
                refresh_binding(request, building, self.layout, self.equipment, self.library)
                report = self.validate(building=building, request=request)
                self.assertEqual(report.status, "FAIL")
                self.assertIn("opening_too_narrow", self.codes(report))

    def test_clear_waypoints_do_not_hide_a_body_collision_between_them(self):
        (self.building, self.layout, self.equipment, self.library, self.mapping,
         self.request, self.actions) = path_fixture(neighbor_y=2700.0)
        report = self.validate()
        self.assertEqual(report.status, "FAIL")
        self.assertIn("swept_body_collision", self.codes(report))

    def test_window_shortcut_keeps_the_original_wall_barrier_closed(self):
        self.building = replace(self.building, layout=replace(self.building.layout,
            windows=(WindowSpec("window", "entry", "left", 1400.0, 1600.0),)))
        self.request["poses"] = [{"x_mm": x, "y_mm": y, "rotated": False}
            for x, y in ((-1200.0, 1000.0), (500.0, 1000.0), (500.0, 2600.0),
                         (6500.0, 2600.0), (9000.0, 2600.0))]
        self.rebind()
        report = self.validate()
        self.assertEqual(report.status, "FAIL")
        self.assertIn("swept_wall_collision", self.codes(report))

    def test_stale_layout_library_or_original_equipment_hash_cannot_be_promoted(self):
        for key in ("building_sha256", "layout_sha256", "equipment_sha256", "library_sha256"):
            with self.subTest(key=key):
                request = copy.deepcopy(self.request)
                request["source"][key] = "0" * 64
                report = self.validate(request=request)
                self.assertEqual(report.status, "FAIL")
                self.assertIn("source_binding_mismatch", self.codes(report))

    def test_staging_binding_preserves_exact_bytes_pointer_and_numeric_scalar_types(self):
        for mutation in ("digest", "pointer", "numeric_type"):
            with self.subTest(mutation=mutation):
                request = copy.deepcopy(self.request)
                evidence = request["exterior_staging"]
                if mutation == "digest":
                    evidence["source_sha256"] = "0" * 64
                elif mutation == "pointer":
                    evidence["source_pointer"] = "/installation_path/missing"
                else:
                    evidence["value"]["floor"]["x"] = -2000
                report = self.validate(request=request)
                self.assertEqual(report.status, "FAIL")
                self.assertIn("staging_source_mismatch", self.codes(report))

    def test_missing_exterior_coverage_is_unknown_but_known_body_collision_survives(self):
        self.request["exterior_staging"] = None
        self.assertEqual(self.validate().to_dict()["local_2d_path_status"], "UNKNOWN")
        (self.building, self.layout, self.equipment, self.library, self.mapping,
         self.request, self.actions) = path_fixture(neighbor_y=2700.0)
        self.request["exterior_staging"] = None
        report = self.validate()
        self.assertEqual(report.status, "FAIL")
        self.assertIn("swept_body_collision", self.codes(report))

    def test_temporary_action_description_cannot_remove_a_physical_neighbor(self):
        (self.building, self.layout, self.equipment, self.library, self.mapping,
         self.request, self.actions) = path_fixture(neighbor_y=2700.0)
        card = self.library["cards"][0]
        action = next(row for row in card["scenario_actions"] if row["id"] == "temporary")
        action["evidence"]["value"]["description"] = "Remove the neighbor before installation."
        source = self.library["sources"][0]
        content = json.loads(source["content"])
        content["actions"]["temporary"] = copy.deepcopy(action["evidence"]["value"])
        source_content({"sources": [source]}, content)
        self.rebind()
        report = self.validate()
        self.assertEqual(report.status, "FAIL")
        self.assertIn("swept_body_collision", self.codes(report))

    def test_missing_required_action_rotation_changes_and_diagonal_motion_remain_unknown(self):
        self.assertEqual(self.validate(actions_by_scenario={}).to_dict()["declared_premises_status"], "UNKNOWN")
        for mutation in ("rotation", "diagonal"):
            with self.subTest(mutation=mutation):
                request = copy.deepcopy(self.request)
                if mutation == "rotation":
                    request["poses"][1]["rotated"] = True
                else:
                    request["poses"][1]["y_mm"] = 2500.0
                self.assertEqual(self.validate(request=request).to_dict()["local_2d_path_status"], "UNKNOWN")

    def test_partial_entry_crossing_or_wrong_final_pose_is_not_a_complete_path(self):
        for mutation in ("partial_entry", "wrong_end", "wrong_entry_id"):
            with self.subTest(mutation=mutation):
                request = copy.deepcopy(self.request)
                if mutation == "partial_entry":
                    request["poses"][0]["x_mm"] = -200.0
                elif mutation == "wrong_end":
                    request["poses"][-1]["x_mm"] = 8900.0
                else:
                    request["external_entry_id"] = "internal"
                self.assertEqual(self.validate(request=request).status, "FAIL")

    def test_explicit_source_bound_transport_clearance_is_swept_in_addition_to_the_body(self):
        value = {"kind": "rectangle", "unit": "mm", "x": 0.0, "y": -500.0,
                 "width": 1000.0, "depth": 1800.0}
        source = self.library["sources"][0]
        content = json.loads(source["content"])
        content["installation_path"]["transport_clearance"] = value
        source_content({"sources": [source]}, content)
        self.request["transport_clearance"] = {"source_id": source["id"],
            "source_sha256": source["sha256"], "source_pointer": "/installation_path/transport_clearance",
            "value": value}
        self.rebind()
        report = self.validate()
        self.assertEqual(report.status, "FAIL")
        self.assertIn("opening_too_narrow", self.codes(report))

    def test_input_work_and_nested_source_bounds_precede_geometry_constructors(self):
        requests = []
        request = copy.deepcopy(self.request)
        request["poses"][0]["x_mm"] = True
        requests.append((request, self.library, None, "FAIL"))
        request = copy.deepcopy(self.request)
        request["poses"][0]["x_mm"] = 10 ** 400
        requests.append((request, self.library, None, "FAIL"))
        requests.append((self.request, self.library,
                         replace(self.api.EquipmentInstallationPathLimits(), max_poses=2), "UNKNOWN"))
        requests.append((self.request, self.library,
                         replace(self.api.EquipmentInstallationPathLimits(), max_geometry_checks=1), "UNKNOWN"))
        library = copy.deepcopy(self.library)
        library["sources"][0]["content"] = '{"unused":' + '[' * 40 + '0' + ']' * 40 + '}'
        library["sources"][0]["sha256"] = hashlib.sha256(library["sources"][0]["content"].encode()).hexdigest()
        request = copy.deepcopy(self.request)
        refresh_binding(request, self.building, self.layout, self.equipment, library)
        requests.append((request, library, None, "UNKNOWN"))
        for index, (request, library, limits, expected) in enumerate(requests):
            with self.subTest(index=index), patch.object(self.api, "Polygon", side_effect=AssertionError("No GEOS before admission")), \
                    patch.object(self.api, "box", side_effect=AssertionError("No GEOS before admission")), \
                    patch.object(self.api, "build_orthogonal_wall_plan", side_effect=AssertionError("No walls before admission")):
                kwargs = {} if limits is None else {"limits": limits}
                self.assertEqual(self.validate(request=request, library=library, **kwargs).status, expected)

    def test_positive_body_extent_lost_at_world_precision_is_unknown_before_geometry(self):
        (self.building, self.layout, self.equipment, self.library, self.mapping,
         self.request, self.actions) = path_fixture(target_width=1e-30)
        with patch.object(self.api, "Polygon", side_effect=AssertionError("Collapsed body cannot reach GEOS")), \
                patch.object(self.api, "box", side_effect=AssertionError("Collapsed body cannot reach GEOS")), \
                patch.object(self.api, "build_orthogonal_wall_plan", side_effect=AssertionError("Collapsed body cannot reach walls")):
            self.assertEqual(self.validate().status, "UNKNOWN")

    def test_required_adjacency_cannot_authorize_an_inferred_installation_aperture(self):
        self.building = replace(self.building, layout=replace(self.building.layout, doors=()))
        self.rebind()
        report = self.validate()
        self.assertEqual(report.status, "FAIL")
        self.assertIn("swept_wall_collision", self.codes(report))

    def test_supplied_exterior_obstacle_blocks_the_sweep_between_clear_poses(self):
        staging = self.request["exterior_staging"]["value"]
        staging["obstacles"] = [{"kind": "rectangle", "x": -150.0, "y": 2700.0,
                                 "width": 50.0, "depth": 300.0}]
        source = self.library["sources"][0]
        content = json.loads(source["content"])
        content["installation_path"]["staging"] = copy.deepcopy(staging)
        source_content({"sources": [source]}, content)
        self.rebind()
        report = self.validate()
        self.assertEqual(report.status, "FAIL")
        self.assertIn("swept_staging_obstacle_collision", self.codes(report))

    def test_exact_staging_floor_must_cover_the_entire_supplied_exterior_body(self):
        staging = self.request["exterior_staging"]["value"]
        staging["floor"].update(x=-500.0, width=500.0)
        source = self.library["sources"][0]
        content = json.loads(source["content"])
        content["installation_path"]["staging"] = copy.deepcopy(staging)
        source_content({"sources": [source]}, content)
        self.rebind()
        report = self.validate()
        self.assertEqual(report.status, "FAIL")
        self.assertNotEqual(report.to_dict()["local_2d_path_status"], "PASS")

    def test_piecewise_axis_corner_path_does_not_fill_its_global_bounding_box(self):
        # The actual neighbor at (3000,3600) lies in the path's global AABB.
        # Each supplied segment stays clear: cross at y=2400, turn in work.
        (self.building, self.layout, self.equipment, self.library, self.mapping,
         self.request, self.actions) = path_fixture(neighbor_y=3600.0)
        self.request["poses"] = [{"x_mm": x, "y_mm": y, "rotated": False}
            for x, y in ((-1200.0, 2600.0), (500.0, 2600.0), (500.0, 2400.0),
                         (6500.0, 2400.0), (6500.0, 3700.0), (8500.0, 3700.0),
                         (8500.0, 2600.0), (9000.0, 2600.0))]
        self.assertEqual(self.validate().to_dict()["local_2d_path_status"], "PASS")

    def test_neighbor_operating_reservation_requires_its_own_bound_stop(self):
        (self.building, self.layout, self.equipment, self.library, self.mapping,
         self.request, self.actions) = path_fixture(neighbor_y=3450.0)
        card = next(row for row in self.library["cards"] if row["id"] == "card_neighbor")
        action = copy.deepcopy(next(row for row in card["scenario_actions"] if row["id"] == "stop"))
        action.update(id="installation_stop", scenario="installation_replacement")
        action["evidence"]["source_pointer"] = "/actions/installation_stop"
        action["evidence"]["value"].update(id="installation_stop", scenario="installation_replacement")
        card["scenario_actions"].append(action)
        source = next(row for row in self.library["sources"] if row["id"] == "source_neighbor")
        content = json.loads(source["content"])
        content["actions"]["installation_stop"] = copy.deepcopy(action["evidence"]["value"])
        source_content({"sources": [source]}, content)
        self.rebind()
        blocked = self.validate()
        self.assertEqual(blocked.status, "FAIL")
        self.assertIn("swept_operating_collision", self.codes(blocked))
        self.actions["installation_replacement"]["neighbor"] = ["installation_stop"]
        self.assertEqual(self.validate().to_dict()["local_2d_path_status"], "PASS")

    def test_combined_nested_source_nodes_are_charged_before_geometry(self):
        for source in self.library["sources"]:
            content = json.loads(source["content"])
            content["unused"] = [0] * 3000
            source_content({"sources": [source]}, content)
        self.rebind()
        limits = replace(self.api.EquipmentInstallationPathLimits(), max_nodes=5000)
        with patch.object(self.api, "Polygon", side_effect=AssertionError("No GEOS before aggregate source admission")), \
                patch.object(self.api, "box", side_effect=AssertionError("No GEOS before aggregate source admission")), \
                patch.object(self.api, "build_orthogonal_wall_plan", side_effect=AssertionError("No walls before admission")):
            self.assertEqual(self.validate(limits=limits).status, "UNKNOWN")

    def test_unverified_reuse_or_visual_reference_does_not_authorize_local_path_pass(self):
        for mutation in ("rights", "kind"):
            with self.subTest(mutation=mutation):
                library = copy.deepcopy(self.library)
                if mutation == "rights":
                    library["sources"][0]["usage_rights"]["state"] = "UNKNOWN"
                else:
                    library["sources"][0]["kind"] = "visual_reference"
                request = copy.deepcopy(self.request)
                refresh_binding(request, self.building, self.layout, self.equipment, library)
                self.assertEqual(self.validate(request=request, library=library).to_dict()["local_2d_path_status"], "UNKNOWN")

    def test_unsupported_diagonal_motion_cannot_hide_a_known_fixed_pose_body_collision(self):
        self.request["poses"] = [{"x_mm": x, "y_mm": y, "rotated": False}
            for x, y in ((-1200.0, 2600.0), (500.0, 2600.0),
                         (3000.0, 4500.0), (9000.0, 2600.0))]
        report = self.validate()
        self.assertEqual(report.status, "FAIL")
        self.assertTrue({"pose_body_collision", "swept_body_collision"} & self.codes(report),
                        "The literal colliding pose needs independent failure evidence")

    def test_narrow_external_aperture_cannot_have_a_positive_entry_subreport(self):
        building = replace(self.building, layout=replace(self.building.layout,
            external_entry=replace(self.building.layout.external_entry, width_mm=700.0)))
        request = copy.deepcopy(self.request)
        refresh_binding(request, building, self.layout, self.equipment, self.library)
        report = self.validate(building=building, request=request).to_dict()
        self.assertEqual(report["entry_validation"]["status"], "FAIL")
        self.assertIn("opening_too_narrow", {row["code"] for row in report["entry_validation"]["issues"]})


if __name__ == "__main__":
    unittest.main()

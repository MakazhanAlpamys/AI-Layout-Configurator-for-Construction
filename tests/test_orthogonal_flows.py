import hashlib
import importlib.util
import json
import unittest
from dataclasses import replace

from shapely.geometry import LineString, box

from layout_configurator.building import BuildingIR
from layout_configurator.equipment import EquipmentLayoutResult, EquipmentPlacement, clearance_rect
from layout_configurator.flows import FlowRoute, FlowRoutingResult
from layout_configurator.models import LayoutIR, LayoutResult, Rect
from layout_configurator.orthogonal import OrthogonalPolygon
from layout_configurator.orthogonal_projection import build_orthogonal_wall_plan
from layout_configurator.orthogonal_validation import validate_orthogonal_equipment, validate_orthogonal_layout


class OrthogonalFlowTests(unittest.TestCase):
    def api(self):
        self.assertIsNotNone(importlib.util.find_spec("layout_configurator.orthogonal_flows"),
                             "Actual polygon flow routing and independent corridor checks are missing")
        from layout_configurator import orthogonal_flows
        return orthogonal_flows

    def test_equipment_to_room_route_turns_inside_the_actual_l_instead_of_crossing_the_notch(self):
        api = self.api()
        building, layout, equipment = _l_program()
        original = building.to_dict()
        result = api.route_orthogonal_flows(building, layout, equipment)
        self.assertEqual(result.status, "DRAFT", result.to_dict())
        self.assertFalse(result.ok)
        self.assertIsInstance(result.routing, FlowRoutingResult)
        self.assertEqual(len(result.routing.routes), 1)
        route = result.routing.routes[0]
        self.assertEqual(route.room_path, ("main", "corner"))
        self.assertEqual(route.points[0], (5050, 1410))
        self.assertGreaterEqual(len(route.points), 4)
        self.assertTrue(any(x <= 1500 and y > 2000 for x, y in route.points))
        self.assertTrue(all(x1 == x2 or y1 == y2 for (x1, y1), (x2, y2) in zip(route.points, route.points[1:])))
        walls = build_orthogonal_wall_plan(building.layout, layout)
        corridor = LineString(route.points).buffer(400, cap_style=3, join_style=2)
        self.assertLessEqual(corridor.intersection(walls.geometry).area, 1e-6)
        service = clearance_rect(building.equipment[0], equipment.placements[0].rect, False)
        self.assertLessEqual(corridor.intersection(box(service.x, service.y, service.right, service.top)).area, 1e-6)
        self.assertTrue(api.validate_orthogonal_flow_routes(building, layout, result.routing, equipment).ok)
        self.assertEqual(result.to_dict()["full_facility_validation"]["status"], "UNKNOWN")
        self.assertEqual(result.to_dict()["source_sha256"], _hash(building.to_dict()))
        self.assertEqual(result.to_dict()["layout_sha256"], _hash(layout.to_dict()))
        self.assertEqual(building.to_dict(), original)

    def test_source_usable_neck_can_still_disconnect_the_required_width_corridor(self):
        api = self.api()
        building, layout, equipment = _neck_program()
        self.assertTrue(validate_orthogonal_layout(building.layout, layout).ok)
        self.assertTrue(validate_orthogonal_equipment(building, layout, equipment).ok)
        result = api.route_orthogonal_flows(building, layout, equipment)
        self.assertEqual(result.status, "UNKNOWN")
        self.assertEqual(result.routing.routes, ())
        self.assertIn("no_free_component_connection", json.dumps(result.to_dict()))
        self.assertNotIn("INFEASIBLE", json.dumps(result.to_dict()))
        direct = FlowRoute("process", "left", "right", "main", "main", ("main",),
                           ((750, 1710), (4750, 1710)), 1200)
        report = api.validate_orthogonal_flow_routes(building, layout, FlowRoutingResult((direct,)), equipment)
        self.assertFalse(report.ok)
        self.assertIn("wall_collision", {issue.code for issue in report.issues})

    def test_independent_checker_rebuilds_paths_doors_width_and_front_endpoints(self):
        api = self.api()
        building, layout, equipment = _l_program()
        valid = _valid_route()
        self.assertTrue(api.validate_orthogonal_flow_routes(building, layout, FlowRoutingResult((valid,)), equipment).ok)
        cases = ((replace(valid, points=((5050, 1410), (5050, 4000), (4000, 4000))), "route_missing_door"),
                 (replace(valid, room_path=("main", "invented", "corner")), "room_path_mismatch"),
                 (replace(valid, points=((5050, 1410), (1500, 1410), (1500, 3600), (4000, 3600))), "door_crossing_clearance"),
                 (replace(valid, minimum_clear_width_mm=400), "route_width_below_source"),
                 (replace(valid, points=((5050, 900), (1500, 900), (1500, 4000), (4000, 4000))), "equipment_endpoint_mismatch"),
                 (replace(valid, points=((5050, 1410), (1500, 4000), (4000, 4000))), "nonorthogonal_route"))
        for route, code in cases:
            with self.subTest(code=code):
                report = api.validate_orthogonal_flow_routes(building, layout, FlowRoutingResult((route,)), equipment)
                self.assertFalse(report.ok)
                self.assertIn(code, {issue.code for issue in report.issues})
        own_service = api.validate_orthogonal_flow_routes(building, layout, FlowRoutingResult((cases[4][0],)), equipment)
        self.assertIn("equipment_clearance_collision", {issue.code for issue in own_service.issues})

    def test_an_empty_bounding_box_corner_is_never_route_space(self):
        api = self.api()
        building, layout, equipment = _single_l_program()
        result = api.route_orthogonal_flows(building, layout, equipment)
        self.assertEqual(result.status, "DRAFT", result.to_dict())
        self.assertTrue(result.report.ok)
        self.assertTrue(any(x <= 1500 and y > 2000 for x, y in result.routing.routes[0].points))
        fake = FlowRoute("process", "source", "target", "main", "main", ("main",),
                         ((5050, 1410), (5050, 5410), (650, 5410)), 800)
        report = api.validate_orthogonal_flow_routes(building, layout, FlowRoutingResult((fake,)), equipment)
        self.assertIn("corridor_outside_rooms", {issue.code for issue in report.issues})

    def test_raw_room_contact_without_a_generated_door_fails(self):
        api = self.api()
        building, layout, equipment = _l_program()
        first, second = building.layout.rooms
        building = replace(building, layout=replace(building.layout,
                           rooms=(replace(first, required_adjacency=()), second)))
        self.assertEqual(build_orthogonal_wall_plan(building.layout, layout).openings, ())
        result = api.route_orthogonal_flows(building, layout, equipment)
        self.assertEqual(result.status, "UNKNOWN")
        self.assertEqual(result.routing.routes, ())
        report = api.validate_orthogonal_flow_routes(building, layout, FlowRoutingResult((_valid_route(),)), equipment)
        self.assertIn("route_missing_door", {issue.code for issue in report.issues})
        self.assertIn("wall_collision", {issue.code for issue in report.issues})

    def test_narrow_opening_is_checked_at_the_actual_crossing(self):
        api = self.api()
        building, layout, equipment = _l_program(width=1000)
        equipment = EquipmentLayoutResult((replace(equipment.placements[0], rect=Rect(4800, 200, 500, 500)),))
        route = replace(_valid_route(), points=((5050, 1310), (1400, 1310), (1400, 4000), (4000, 4000)),
                        minimum_clear_width_mm=1000)
        report = api.validate_orthogonal_flow_routes(building, layout, FlowRoutingResult((route,)), equipment)
        self.assertIn("opening_too_narrow", {issue.code for issue in report.issues})
        self.assertEqual(api.route_orthogonal_flows(building, layout, equipment).status, "UNKNOWN")

    def test_an_unrelated_service_barrier_blocks_the_actual_l_arm(self):
        api = self.api()
        blocker = {"id": "barrier", "room_id": "main", "width_mm": 1800, "depth_mm": 400}
        building, layout, equipment = _l_program(extra_equipment=(blocker,))
        equipment = EquipmentLayoutResult((*equipment.placements, EquipmentPlacement("barrier", "main", Rect(100, 2400, 1800, 400))))
        self.assertTrue(validate_orthogonal_equipment(building, layout, equipment).ok)
        result = api.route_orthogonal_flows(building, layout, equipment)
        self.assertEqual(result.status, "UNKNOWN")
        self.assertIn("no_free_component_connection", json.dumps(result.to_dict()))
        report = api.validate_orthogonal_flow_routes(building, layout, FlowRoutingResult((_valid_route(),)), equipment)
        self.assertIn("equipment_clearance_collision", {issue.code for issue in report.issues})

    def test_front_access_strip_checks_other_services_independently_of_the_route(self):
        api = self.api()
        blocker = {"id": "access_blocker", "room_id": "main", "width_mm": 500, "depth_mm": 10}
        building, layout, equipment = _l_program(extra_equipment=(blocker,))
        equipment = EquipmentLayoutResult((*equipment.placements, EquipmentPlacement("access_blocker", "main", Rect(4800, 1000, 500, 10))))
        self.assertTrue(validate_orthogonal_equipment(building, layout, equipment).ok)
        report = api.validate_orthogonal_flow_routes(building, layout, FlowRoutingResult((_valid_route(),)), equipment)
        self.assertIn("endpoint_access_collision", {issue.code for issue in report.issues})
        self.assertNotIn("equipment_clearance_collision", {issue.code for issue in report.issues})
        self.assertEqual(api.route_orthogonal_flows(building, layout, equipment).status, "UNKNOWN")

    def test_exact_width_supplied_route_can_pass_even_if_search_is_bounded(self):
        api = self.api()
        building, layout, equipment = _l_program(door_width=800)
        report = api.validate_orthogonal_flow_routes(building, layout, FlowRoutingResult((_valid_route(),)), equipment)
        self.assertTrue(report.ok, report.to_dict())
        result = api.route_orthogonal_flows(building, layout, equipment, budget=api.OrthogonalFlowBudget(max_nodes=2))
        self.assertEqual(result.status, "UNKNOWN")
        self.assertIn("zero-area", json.dumps(result.to_dict()))

    def test_zone_descendants_expand_to_source_host_pairs_in_stable_order(self):
        api = self.api()
        building, layout, _ = _l_program()
        source = {"layout": building.layout.to_dict(), "zones": [
            {"id": "root", "room_ids": []}, {"id": "child", "parent_zone_id": "root", "room_ids": ["main"]}],
            "flows": [{"id": "people", "from": ["root"], "to": ["corner"], "minimum_clear_width_mm": 800}]}
        building = BuildingIR.from_mapping(source)
        result = api.route_orthogonal_flows(building, layout)
        self.assertEqual(result.status, "DRAFT", result.to_dict())
        route = result.routing.routes[0]
        self.assertEqual((route.from_id, route.from_room_id, route.to_id, route.to_room_id), ("root", "main", "corner", "corner"))
        self.assertTrue(api.validate_orthogonal_flow_routes(building, layout, result.routing).ok)

    def test_required_completeness_optional_routes_duplicates_and_unknown_sources(self):
        api = self.api()
        building, layout, equipment = _l_program()
        missing = api.validate_orthogonal_flow_routes(building, layout, FlowRoutingResult(()), equipment)
        self.assertIn("no_route", {issue.code for issue in missing.issues})
        optional = replace(building, flows=(replace(building.flows[0], required=False),))
        self.assertTrue(api.validate_orthogonal_flow_routes(optional, layout, FlowRoutingResult(()), equipment).ok)
        bad_optional = replace(_valid_route(), points=((5050, 1410), (5050, 4000), (4000, 4000)))
        self.assertFalse(api.validate_orthogonal_flow_routes(optional, layout, FlowRoutingResult((bad_optional,)), equipment).ok)
        duplicate = api.validate_orthogonal_flow_routes(building, layout, FlowRoutingResult((_valid_route(),) * 2), equipment)
        self.assertIn("duplicate_route", {issue.code for issue in duplicate.issues})
        unknown = api.validate_orthogonal_flow_routes(building, layout, FlowRoutingResult((replace(_valid_route(), flow_id="invented"),)), equipment)
        self.assertIn("unknown_flow", {issue.code for issue in unknown.issues})
        unexpected = api.validate_orthogonal_flow_routes(building, layout, FlowRoutingResult((replace(_valid_route(), from_id="corner"),)), equipment)
        self.assertIn("unexpected_endpoint_pair", {issue.code for issue in unexpected.issues})

    def test_work_bounds_produce_unknown_with_real_negative_evidence(self):
        api = self.api()
        building, layout, equipment = _l_program()
        for budget, code in ((api.OrthogonalFlowBudget(max_nodes=2), "visibility_grid_budget"),
                             (api.OrthogonalFlowBudget(max_edge_checks=1), "visibility_edge_budget"),
                             (api.OrthogonalFlowBudget(max_search_steps=1), "path_search_budget")):
            with self.subTest(code=code):
                result = api.route_orthogonal_flows(building, layout, equipment, budget=budget)
                self.assertEqual(result.status, "UNKNOWN")
                self.assertTrue(result.to_dict()["search_truncated"])
                self.assertIn(code, json.dumps(result.to_dict()))
                self.assertNotIn("INFEASIBLE", json.dumps(result.to_dict()))

    def test_packing_can_feed_routes_but_no_full_polygon_consumer_is_dispatched(self):
        api = self.api()
        from layout_configurator.orthogonal_equipment import place_orthogonal_equipment
        from layout_configurator.flows import route_flows
        from layout_configurator.result_geometry import UnsupportedGeometryError
        building, layout, equipment = _l_program()
        packed = place_orthogonal_equipment(building, layout, catalog={"source": list(equipment.placements)}, deterministic_units=.05)
        self.assertEqual(packed.status, "DRAFT", packed.to_dict())
        result = api.route_orthogonal_flows(building, layout, packed.equipment)
        self.assertEqual(result.status, "DRAFT", result.to_dict())
        self.assertTrue(result.report.ok)
        with self.assertRaises(UnsupportedGeometryError):
            route_flows(building, layout, packed.equipment)

    def test_source_layout_equipment_order_and_search_fingerprints_are_stable(self):
        api = self.api()
        building, layout, equipment = _l_program()
        first = api.route_orthogonal_flows(building, layout, equipment)
        reverse = replace(building, layout=replace(building.layout, rooms=tuple(reversed(building.layout.rooms))))
        second = api.route_orthogonal_flows(reverse, LayoutResult(1, dict(reversed(list(layout.placements.items())))), equipment)
        self.assertEqual(first.routing.to_dict(), second.routing.to_dict())
        self.assertEqual(first.to_dict()["search_space_sha256"], second.to_dict()["search_space_sha256"])
        changed = api.route_orthogonal_flows(building, layout, EquipmentLayoutResult((replace(equipment.placements[0], rect=Rect(4700, 400, 500, 500)),)))
        self.assertNotEqual(first.to_dict()["equipment_sha256"], changed.to_dict()["equipment_sha256"])
        self.assertNotEqual(first.to_dict()["search_space_sha256"], changed.to_dict()["search_space_sha256"])

    def test_scalar_and_coordinate_contract_rejects_malformed_inputs_as_value_errors(self):
        api = self.api()
        building, layout, equipment = _l_program()
        for value in (0, True, 1.5):
            with self.subTest(value=value), self.assertRaises(ValueError):
                api.OrthogonalFlowBudget(max_nodes=value)
        for routing in (FlowRoutingResult(None), FlowRoutingResult((replace(_valid_route(), minimum_clear_width_mm=True),)),
                        FlowRoutingResult((replace(_valid_route(), points=((float("nan"), 1), (2, 3))),)),
                        FlowRoutingResult((replace(_valid_route(), points=((10 ** 400, 1), (2, 3))),)),
                        FlowRoutingResult((replace(_valid_route(), points=([], (2, 3))),))):
            with self.subTest(routing=routing), self.assertRaises(ValueError):
                api.validate_orthogonal_flow_routes(building, layout, routing, equipment)

    def test_valid_numeric_list_point_pairs_do_not_escape_as_unhashable_type_error(self):
        api = self.api()
        building, layout, equipment = _l_program()
        route = replace(_valid_route(), points=[list(point) for point in _valid_route().points])
        report = api.validate_orthogonal_flow_routes(building, layout, FlowRoutingResult((route,)), equipment)
        self.assertTrue(report.ok, report.to_dict())

    def test_rotated_square_equipment_front_maps_to_actual_global_left(self):
        api = self.api()
        building, layout, equipment = _l_program()
        equipment = EquipmentLayoutResult((replace(equipment.placements[0], rect=Rect(1200, 400, 500, 500), rotated=True),))
        self.assertTrue(validate_orthogonal_equipment(building, layout, equipment).ok)
        result = api.route_orthogonal_flows(building, layout, equipment)
        self.assertEqual(result.status, "DRAFT", result.to_dict())
        self.assertEqual(result.routing.routes[0].points[0], (690, 650))
        self.assertTrue(result.report.ok)
        evidence = result.report.proofs[0]["endpoint_access"][0]
        self.assertEqual(evidence["expected_point"], [690, 650])

    def test_actual_room_and_equipment_solver_outputs_can_feed_independent_routes(self):
        api = self.api()
        from layout_configurator.orthogonal_solver import solve_orthogonal_candidates
        from layout_configurator.orthogonal_equipment import place_orthogonal_equipment
        building, _, _ = _l_program()
        building = replace(building, equipment=(replace(building.equipment[0], clearance_left_mm=400, clearance_right_mm=400),))
        room_search = solve_orthogonal_candidates(building.layout, seed=1, deterministic_units=.05)
        self.assertEqual(room_search.status, "DRAFT")
        layout = room_search.candidates[0].result
        packing = place_orthogonal_equipment(building, layout, seed=1, deterministic_units=.05)
        self.assertEqual(packing.status, "DRAFT", packing.to_dict())
        result = api.route_orthogonal_flows(building, layout, packing.equipment)
        self.assertEqual(result.status, "DRAFT", result.to_dict())
        self.assertTrue(api.validate_orthogonal_flow_routes(building, layout, result.routing, packing.equipment).ok)
        self.assertEqual(result.to_dict()["full_facility_validation"]["status"], "UNKNOWN")

    def test_scoped_packing_pass_does_not_imply_front_access_is_wide_enough(self):
        api = self.api()
        from layout_configurator.orthogonal_solver import solve_orthogonal_candidates
        from layout_configurator.orthogonal_equipment import place_orthogonal_equipment
        building, _, _ = _l_program()
        layout = solve_orthogonal_candidates(building.layout, seed=1, deterministic_units=.05).candidates[0].result
        packing = place_orthogonal_equipment(building, layout, seed=1, deterministic_units=.05)
        self.assertEqual(packing.status, "DRAFT", packing.to_dict())
        result = api.route_orthogonal_flows(building, layout, packing.equipment)
        self.assertEqual(result.status, "UNKNOWN")
        self.assertIn("endpoint_has_no_full_width_access", json.dumps(result.to_dict()))

    def test_blocked_direct_chain_uses_real_doors_in_the_longer_upper_chain(self):
        api = self.api()
        building, layout, equipment = _alternate_program()
        result = api.route_orthogonal_flows(building, layout, equipment)
        self.assertEqual(result.status, "DRAFT", result.to_dict())
        route = result.routing.routes[0]
        self.assertEqual(route.room_path, ("source", "upper_left", "upper_middle", "upper_right", "target"))
        self.assertTrue(api.validate_orthogonal_flow_routes(building, layout, result.routing, equipment).ok)
        crossings = result.report.proofs[0]["actual_crossings"]
        self.assertEqual(len(crossings), 4)
        self.assertTrue(all(item["width_mm"] >= 800 for item in crossings))

    def test_equipment_and_program_mutation_invalidate_the_independent_predicates(self):
        api = self.api()
        building, layout, equipment = _l_program()
        result = api.route_orthogonal_flows(building, layout, equipment)
        changed = replace(building, equipment=(replace(building.equipment[0], clearance_front_mm=200),))
        report = api.validate_orthogonal_flow_routes(changed, layout, result.routing, equipment)
        self.assertFalse(report.ok)
        self.assertIn("equipment_endpoint_mismatch", {issue.code for issue in report.issues})
        invalid_area = replace(building, layout=replace(building.layout, rooms=(
            replace(building.layout.rooms[0], min_area_m2=21, max_area_m2=21, target_area_m2=21), building.layout.rooms[1])))
        self.assertFalse(api.validate_orthogonal_flow_routes(invalid_area, layout, result.routing, equipment).ok)

    def test_unknown_source_equipment_in_a_forged_route_does_not_escape_as_key_error(self):
        api = self.api()
        building, layout, equipment = _l_program()
        forged = EquipmentLayoutResult((*equipment.placements, EquipmentPlacement("invented", "main", Rect(500, 500, 500, 500))))
        route = replace(_valid_route(), from_id="invented")
        report = api.validate_orthogonal_flow_routes(building, layout, FlowRoutingResult((route,)), forged)
        self.assertFalse(report.ok)
        self.assertIn("unexpected_endpoint_pair", {issue.code for issue in report.issues})

    def test_endpoint_pair_budget_limits_allocated_pairs_without_claiming_program_infeasibility(self):
        api = self.api()
        building, layout, equipment = _l_program()
        raw = building.to_dict()
        raw["flows"][0]["from"] = ["source", "main"]
        raw["flows"][0].pop("from_ids", None)
        building = BuildingIR.from_mapping(raw)
        result = api.route_orthogonal_flows(building, layout, equipment, budget=api.OrthogonalFlowBudget(max_endpoint_pairs=1))
        self.assertEqual(result.status, "UNKNOWN")
        self.assertTrue(result.to_dict()["search_truncated"])
        self.assertLessEqual(len(result.to_dict()["pairs"]), 1)
        self.assertNotIn("INFEASIBLE", json.dumps(result.to_dict()))

    def test_many_zone_endpoint_pairs_have_bounded_allocation_and_exact_source_count(self):
        api = self.api()
        building, layout, _ = _l_program()
        zones = []
        for prefix, room in (("source", "main"), ("target", "corner")):
            for index in range(8):
                zone = {"id": f"{prefix}_{index}", "room_ids": [room] if index == 7 else []}
                if index:
                    zone["parent_zone_id"] = f"{prefix}_{index - 1}"
                zones.append(zone)
        building = BuildingIR.from_mapping({"layout": building.layout.to_dict(), "zones": zones,
            "flows": [{"id": "people", "from": [f"source_{index}" for index in range(8)],
                       "to": [f"target_{index}" for index in range(8)], "minimum_clear_width_mm": 800}]})
        result = api.route_orthogonal_flows(building, layout, budget=api.OrthogonalFlowBudget(max_endpoint_pairs=2))
        self.assertEqual(result.status, "UNKNOWN")
        self.assertEqual(result.to_dict()["expected_pairs"], 64)
        self.assertLessEqual(result.to_dict()["allocated_endpoint_pairs"], 3)
        self.assertTrue(result.to_dict()["search_truncated"])

    def test_bounded_rejection_samples_preserve_counts_and_last_real_negative(self):
        api = self.api()
        building, layout, equipment = _l_program(width=1000)
        equipment = EquipmentLayoutResult((replace(equipment.placements[0], rect=Rect(4800, 200, 500, 500)),))
        building = replace(building, flows=tuple(replace(building.flows[0], id=f"flow_{index}") for index in range(8)))
        result = api.route_orthogonal_flows(building, layout, equipment, budget=api.OrthogonalFlowBudget(max_rejections=1))
        self.assertEqual(result.status, "UNKNOWN")
        evidence = result.to_dict()
        self.assertEqual(len(evidence["rejections"]), 1)
        self.assertTrue(evidence["rejections_truncated"])
        self.assertEqual(evidence["rejection_counts"]["no_free_component_connection"], 8)
        self.assertEqual(evidence["last_rejection"]["endpoint_pair"][0], "flow_7")

    def test_concave_corner_must_fit_actual_wall_insets_even_when_flat_wall_bands_do_not_overlap(self):
        api = self.api()
        building, layout, _ = _l_program()
        building = replace(building, equipment=(replace(building.equipment[0], clearance_left_mm=400, clearance_right_mm=400),))
        layout = LayoutResult(1, {"main": layout.placements["main"].rotated_quarter_turns(2, origin=(3000, 3000)),
                                 "corner": Rect(0, 0, 4000, 4000)})
        equipment = EquipmentLayoutResult((EquipmentPlacement("source", "main", Rect(4500, 200, 500, 500)),))
        route = replace(_valid_route(), points=((4750, 1210), (4500, 1210), (4500, 4400),
                        (4400, 4400), (4400, 4500), (2000, 4500), (2000, 2000)))
        corridor = LineString(route.points).buffer(400, cap_style=3, join_style=2)
        self.assertLessEqual(corridor.intersection(build_orthogonal_wall_plan(building.layout, layout).geometry).area, 1e-6)
        inset = layout.placements["main"].inset_components(100)[0].geometry
        self.assertGreater(corridor.intersection(box(4000, 4000, 4100, 4100)).difference(inset).area, 0)
        report = api.validate_orthogonal_flow_routes(building, layout, FlowRoutingResult((route,)), equipment)
        self.assertFalse(report.ok)
        self.assertIn("corridor_outside_usable_components", {issue.code for issue in report.issues})
        result = api.route_orthogonal_flows(building, layout, equipment)
        self.assertEqual(result.status, "DRAFT", result.to_dict())
        self.assertTrue(api.validate_orthogonal_flow_routes(building, layout, result.routing, equipment).ok)


def _l_program(*, width=800, door_width=900, optional=False, extra_equipment=()):
    layout_spec = LayoutIR.from_mapping({"project_name": "Actual polygon flow fixture",
        "boundary": {"width": 6000, "height": 6000}, "entry_room": "main", "door_width": door_width,
        "rooms": [{"id": "main", "type": "process", "target_area": 20, "min_area": 20, "max_area": 20,
                   "required_adjacency": ["corner"]},
                  {"id": "corner", "type": "support", "target_area": 16, "min_area": 16, "max_area": 16}]})
    shape = OrthogonalPolygon(((0, 0), (6000, 0), (6000, 2000), (2000, 2000), (2000, 6000), (0, 6000)))
    layout = LayoutResult(1, {"main": shape, "corner": Rect(2000, 2000, 4000, 4000)})
    source = {"id": "source", "room_id": "main", "width_mm": 500, "depth_mm": 500,
              "clearance_mm": 100}
    building = BuildingIR.from_mapping({"layout": layout_spec.to_dict(), "equipment": [source, *extra_equipment],
        "flows": [{"id": "process", "from": ["source"], "to": ["corner"],
                   "minimum_clear_width_mm": width, "required": not optional}]})
    equipment = EquipmentLayoutResult((EquipmentPlacement("source", "main", Rect(4800, 400, 500, 500)),))
    return building, layout, equipment


def _valid_route():
    return FlowRoute("process", "source", "corner", "main", "corner", ("main", "corner"),
                     ((5050, 1410), (1500, 1410), (1500, 4000), (4000, 4000)), 800)


def _single_l_program():
    building, layout, equipment = _l_program()
    main = replace(building.layout.rooms[0], required_adjacency=())
    spec = replace(building.layout, rooms=(main,))
    building = BuildingIR.from_mapping({"layout": spec.to_dict(), "equipment": [building.equipment[0].to_dict(),
        {"id": "target", "room_id": "main", "width_mm": 500, "depth_mm": 500, "clearance_mm": 100}],
        "flows": [{"id": "process", "from": ["source"], "to": ["target"], "minimum_clear_width_mm": 800}]})
    return building, LayoutResult(1, {"main": layout.placements["main"]}), EquipmentLayoutResult((*equipment.placements,
        EquipmentPlacement("target", "main", Rect(400, 4400, 500, 500))))


def _alternate_program():
    positions = {"source": (0, 0), "blocked": (3000, 0), "target": (6000, 0),
                 "upper_left": (0, 3000), "upper_middle": (3000, 3000), "upper_right": (6000, 3000)}
    relations = {"source": ["blocked", "upper_left"], "blocked": ["target"], "upper_left": ["upper_middle"],
                 "upper_middle": ["upper_right"], "upper_right": ["target"]}
    spec = LayoutIR.from_mapping({"project_name": "Actual alternate-door chain fixture", "boundary": {"width": 9000, "height": 6000},
        "entry_room": "source", "rooms": [{"id": identifier, "type": "process", "target_area": 9,
        "min_area": 9, "max_area": 9, "required_adjacency": relations.get(identifier, [])} for identifier in positions]})
    placements = {identifier: Rect(x, y, 3000, 3000) for identifier, (x, y) in positions.items()}
    placements["source"] = OrthogonalPolygon.from_rect(placements["source"])
    building = BuildingIR.from_mapping({"layout": spec.to_dict(),
        "equipment": [{"id": "barrier", "room_id": "blocked", "width_mm": 400, "depth_mm": 2800}],
        "flows": [{"id": "people", "from": ["source"], "to": ["target"], "minimum_clear_width_mm": 800}]})
    equipment = EquipmentLayoutResult((EquipmentPlacement("barrier", "blocked", Rect(4300, 100, 400, 2800)),))
    return building, LayoutResult(1, placements), equipment


def _neck_program():
    shape = OrthogonalPolygon(((0, 0), (2000, 0), (2000, 900), (4000, 900), (4000, 0), (6000, 0),
        (6000, 3000), (4000, 3000), (4000, 2100), (2000, 2100), (2000, 3000), (0, 3000)))
    spec = LayoutIR.from_mapping({"project_name": "Required-width neck fixture", "boundary": {"width": 6000, "height": 3000},
        "entry_room": "main", "rooms": [{"id": "main", "type": "process", "target_area": shape.area_m2,
        "min_area": shape.area_m2, "max_area": shape.area_m2, "min_width": 100, "min_depth": 100}]})
    items = [{"id": name, "room_id": "main", "width_mm": 500, "depth_mm": 500, "clearance_front_mm": 100}
             for name in ("left", "right")]
    building = BuildingIR.from_mapping({"layout": spec.to_dict(), "equipment": items,
        "flows": [{"id": "process", "from": ["left"], "to": ["right"], "minimum_clear_width_mm": 1200}]})
    equipment = EquipmentLayoutResult((EquipmentPlacement("left", "main", Rect(500, 500, 500, 500)),
                                       EquipmentPlacement("right", "main", Rect(4500, 500, 500, 500))))
    return building, LayoutResult(1, {"main": shape}), equipment


def _hash(payload):
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, allow_nan=False,
        sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()

"""Existing project policies evaluate actual orthogonal geometry independently."""

from dataclasses import replace
import hashlib
import importlib.util
import json
import unittest

from layout_configurator.building import BuildingIR
from layout_configurator.equipment import EquipmentLayoutResult
from layout_configurator.facility import FacilityProfile, default_facility_profile
from layout_configurator.flows import FlowRoute, FlowRoutingResult
from layout_configurator.models import LayoutResult, Rect
from layout_configurator.orthogonal import OrthogonalPolygon
from test_orthogonal_flows import _l_program, _valid_route


def profile(kind, parameters):
    return FacilityProfile.from_mapping({"name": "Source-owned polygon policy", "version": "P01",
        "rules": [{"id": "declared_policy", "kind": kind, "title": "Declared source policy",
                   "source": "Repository-owned project specification", "edition": "P01",
                   "effective_date": "2026-10-12", "evidence": ["Explicit project requirements only"],
                   "parameters": parameters}]})


class OrthogonalFacilityTests(unittest.TestCase):
    def api(self):
        self.assertIsNotNone(importlib.util.find_spec("layout_configurator.orthogonal_facility"),
                             "Independent polygon facility/profile predicates are missing")
        from layout_configurator.orthogonal_facility import validate_orthogonal_building
        return validate_orthogonal_building

    def source(self):
        building, layout, equipment = _l_program()
        raw = building.to_dict()
        raw["zones"] = [{"id": "process", "type": "process", "room_ids": ["main"], "required_adjacency": ["support"]},
                        {"id": "support", "type": "support", "room_ids": ["corner"]}]
        return BuildingIR.from_mapping(raw), layout, equipment, FlowRoutingResult((_valid_route(),))

    def check(self, report, check_id):
        return next(row for row in report.to_dict()["checks"] if row["id"] == check_id)

    def test_actual_mixed_footprints_equipment_routes_zones_and_declared_profile_pass(self):
        source, layout, equipment, routes = self.source()
        policy = profile("required_flow_types", {"flow_types": [source.flows[0].type]})
        before = source.to_dict(), layout.to_dict(), equipment.to_dict(source), routes.to_dict(), policy.to_dict()
        report = self.api()(source, layout, equipment, routes, profile=policy)
        self.assertEqual(report.status, "PASS", report.to_dict())
        self.assertTrue(report.ok)
        self.assertEqual(self.check(report, "ZONE_RELATIONS")["status"], "PASS")
        self.assertEqual(report.to_dict()["full_facility_validation"]["status"], "UNKNOWN")
        self.assertEqual(report.to_dict()["engineering_acceptance"], "PENDING")
        for name, raw in zip(("building", "layout", "equipment", "routing", "profile"), before):
            digest = hashlib.sha256(json.dumps(raw, sort_keys=True, ensure_ascii=False,
                allow_nan=False, separators=(",", ":")).encode()).hexdigest()
            self.assertEqual(report.to_dict()["source"][name + "_sha256"], digest)
        self.assertEqual((source.to_dict(), layout.to_dict(), equipment.to_dict(source), routes.to_dict(), policy.to_dict()), before)

    def test_absent_declared_equipment_and_routes_remain_unknown(self):
        source, layout, _, _ = self.source()
        report = self.api()(source, layout, None, None, profile=default_facility_profile())
        self.assertEqual(report.status, "UNKNOWN")
        self.assertFalse(report.ok)
        self.assertEqual(self.check(report, "EQUIPMENT_CLEARANCE")["status"], "UNKNOWN")
        self.assertEqual(self.check(report, "FLOW_COMPLETENESS")["status"], "UNKNOWN")

    def test_supplied_empty_equipment_and_notch_shortcut_fail_actual_predicates(self):
        source, layout, equipment, routes = self.source()
        bad = FlowRoutingResult((replace(routes.routes[0], points=((5050, 1410), (5050, 4000), (4000, 4000))),))
        for items, paths, failed in ((EquipmentLayoutResult(()), routes, "EQUIPMENT_CLEARANCE"),
                                     (equipment, bad, "FLOW_COMPLETENESS")):
            with self.subTest(check=failed):
                report = self.api()(source, layout, items, paths, profile=default_facility_profile())
                self.assertEqual(report.status, "FAIL")
                self.assertEqual(self.check(report, failed)["status"], "FAIL")

    def test_existing_required_flow_policy_preserves_failed_rule_provenance(self):
        source, layout, equipment, routes = self.source()
        policy = profile("required_flow_types", {"flow_types": ["missing_declared_type"]})
        report = self.api()(source, layout, equipment, routes, profile=policy)
        self.assertEqual(report.status, "FAIL")
        row = next(row for row in report.to_dict()["checks"] if row.get("rule", {}).get("id") == "declared_policy")
        self.assertEqual(row["status"], "FAIL")
        self.assertEqual(row["rule"]["source"], "Repository-owned project specification")
        self.assertIn("missing_declared_type", " ".join(row["evidence"]))

    def test_unknown_pressure_conditions_do_not_become_a_positive_profile_verdict(self):
        source, layout, equipment, routes = self.source()
        report = self.api()(source, layout, equipment, routes,
                            profile=profile("pressure_cascade", {"direction": "parent_gt_child"}))
        self.assertEqual(report.status, "UNKNOWN")
        self.assertFalse(report.ok)
        self.assertTrue(any(row["status"] == "UNKNOWN" and row.get("rule", {}).get("id") == "declared_policy"
                            for row in report.to_dict()["checks"]))

    def test_zone_contacts_require_one_contiguous_usable_span_not_summed_disjoint_edges(self):
        source, _, _, _ = self.source()
        raw = source.to_dict()
        raw.update(equipment=[], flows=[])
        raw["layout"].update(boundary={"width": 6000, "height": 4000, "cutouts": []}, door_width_mm=1500)
        for room, area in zip(raw["layout"]["rooms"], (12, 8)):
            room.update(target_area=area, min_area=area, max_area=area, required_adjacency=[],
                        min_width=500, min_depth=500)
        current = BuildingIR.from_mapping(raw)
        c_shape = OrthogonalPolygon(((0, 0), (4000, 0), (4000, 1000), (2000, 1000),
                                     (2000, 3000), (4000, 3000), (4000, 4000), (0, 4000)))
        layout = LayoutResult(1, {"main": c_shape, "corner": Rect(4000, 0, 2000, 4000)})
        self.assertEqual(c_shape.shared_boundary(OrthogonalPolygon.from_rect(layout.placements["corner"])), 2000)
        report = self.api()(current, layout, None, None, profile=default_facility_profile())
        row = self.check(report, "ZONE_RELATIONS")
        self.assertEqual(row["status"], "FAIL")
        self.assertIn("1500", " ".join(row["evidence"]))

    def test_forbidden_zone_contact_uses_actual_footprints_instead_of_bounds(self):
        source, _, _, _ = self.source()
        raw = source.to_dict()
        raw.update(equipment=[], flows=[])
        raw["zones"][0].update(required_adjacency=[], forbidden_adjacency=["support"])
        raw["layout"]["rooms"][0].update(required_adjacency=[], target_area=12, min_area=12, max_area=12,
                                          min_width=500, min_depth=500)
        raw["layout"]["rooms"][1].update(required_adjacency=[], target_area=1, min_area=1, max_area=1,
                                          min_width=500, min_depth=500)
        current = BuildingIR.from_mapping(raw)
        c_shape = OrthogonalPolygon(((0, 0), (4000, 0), (4000, 1000), (2000, 1000),
                                     (2000, 3000), (4000, 3000), (4000, 4000), (0, 4000)))
        layout = LayoutResult(1, {"main": c_shape, "corner": Rect(2500, 1500, 1000, 1000)})
        report = self.api()(current, layout, None, None, profile=default_facility_profile())
        self.assertEqual(self.check(report, "ZONE_RELATIONS")["status"], "PASS")

    def test_windows_remain_explicitly_unknown_until_their_exact_component_is_supported(self):
        source, layout, equipment, routes = self.source()
        source = replace(source, layout=replace(source.layout, rooms=(
            replace(source.layout.rooms[0], needs_daylight=True), source.layout.rooms[1])))
        report = self.api()(source, layout, equipment, routes, profile=default_facility_profile())
        self.assertEqual(report.status, "UNKNOWN", report.to_dict())
        self.assertEqual(self.check(report, "FACILITY_GEOMETRY")["status"], "UNKNOWN")

    def three_room_routes(self):
        source, layout, equipment, _ = self.source()
        raw = source.to_dict()
        raw["zones"] = []
        raw["layout"]["rooms"][1].update(target_area=8, min_area=8, max_area=8,
                                         required_adjacency=["third"])
        raw["layout"]["rooms"].append({**raw["layout"]["rooms"][1], "id": "third", "required_adjacency": []})
        equipment_id = source.equipment[0].id
        raw["flows"] = [{"id": "people", "type": "people", "from": [equipment_id], "to": ["third"],
                         "minimum_clear_width_mm": 800},
                        {"id": "waste", "type": "waste", "from": ["third"], "to": [equipment_id],
                         "minimum_clear_width_mm": 800}]
        current = BuildingIR.from_mapping(raw)
        geometry = LayoutResult(1, {"main": layout.placements["main"],
                                   "corner": Rect(2000, 2000, 2000, 4000), "third": Rect(4000, 2000, 2000, 4000)})
        points = ((5050, 1410), (1500, 1410), (1500, 4000), (5000, 4000))
        forward = FlowRoute("people", equipment_id, "third", "main", "third", ("main", "corner", "third"), points, 800)
        backward = FlowRoute("waste", "third", equipment_id, "third", "main", ("third", "corner", "main"), tuple(reversed(points)), 800)
        return current, geometry, equipment, FlowRoutingResult((forward, backward))

    def test_actual_three_room_routes_apply_separation_only_after_independent_geometry(self):
        source, layout, equipment, routes = self.three_room_routes()
        policy = profile("flow_separation", {"incompatible_flow_type_pairs": [["people", "waste"]]})
        report = self.api()(source, layout, equipment, routes, profile=policy)
        self.assertEqual(self.check(report, "FLOW_COMPLETENESS")["status"], "PASS", report.to_dict())
        separation = self.check(report, "FLOW_TYPE_SEPARATION")
        self.assertEqual(separation["status"], "FAIL")
        self.assertIn("corner", " ".join(separation["evidence"]))
        self.assertEqual(separation["rule"]["source"], policy.rules[0].source)
        forged = FlowRoutingResult(tuple(replace(route, room_path=(route.from_room_id, route.to_room_id)) for route in routes.routes))
        invalid = self.api()(source, layout, equipment, forged, profile=policy)
        self.assertEqual(self.check(invalid, "FLOW_COMPLETENESS")["status"], "FAIL")
        self.assertEqual(self.check(invalid, "FLOW_TYPE_SEPARATION")["status"], "UNKNOWN")

    def test_every_declared_separation_rule_is_evaluated_with_its_source(self):
        source, layout, equipment, routes = self.three_room_routes()
        first = profile("flow_separation", {"incompatible_flow_type_pairs": [["material", "service"]]})
        second = replace(profile("flow_separation", {"incompatible_flow_type_pairs": [["people", "waste"]]}).rules[0],
                         id="second_policy", source="Second explicit project source")
        policy = replace(first, rules=first.rules + (second,))
        report = self.api()(source, layout, equipment, routes, profile=policy)
        self.assertEqual(report.status, "FAIL", report.to_dict())
        rows = {row["rule"]["id"]: row for row in report.to_dict()["checks"] if "rule" in row}
        self.assertEqual(rows["declared_policy"]["status"], "PASS")
        self.assertEqual(rows["second_policy"]["status"], "FAIL")
        self.assertEqual(rows["second_policy"]["rule"]["source"], second.source)

    def test_supplied_undeclared_equipment_or_routes_are_not_ignored(self):
        source, layout, equipment, routes = self.source()
        for field, check_id in (("equipment", "EQUIPMENT_CLEARANCE"), ("flows", "FLOW_COMPLETENESS")):
            with self.subTest(field=field):
                raw = source.to_dict()
                raw[field] = []
                if field == "equipment":
                    raw["flows"] = []
                report = self.api()(BuildingIR.from_mapping(raw), layout, equipment,
                                    routes if field == "flows" else None, profile=default_facility_profile())
                self.assertEqual(self.check(report, check_id)["status"], "FAIL")
                self.assertFalse(report.ok)

    def test_invalid_source_profile_and_unbounded_policy_input_are_rejected(self):
        source, layout, equipment, routes = self.source()
        for policy in (replace(default_facility_profile(), version=None),
                       replace(default_facility_profile(), rules=tuple(
                           profile("required_flow_types", {"flow_types": ["material"]}).rules[0]
                           for _ in range(1001)))):
            with self.subTest(policy=str(policy)[:70]), self.assertRaises(ValueError):
                self.api()(source, layout, equipment, routes, profile=policy)

    def test_nested_layout_is_strict_before_building_normalization(self):
        source, layout, equipment, routes = self.source()
        for field, value in (("grid_mm", True), ("wall_thickness_mm", "200")):
            with self.subTest(field=field), self.assertRaises(ValueError):
                malformed = replace(source, layout=replace(source.layout, **{field: value}))
                self.api()(malformed, layout, equipment, routes, profile=default_facility_profile())

    def test_malformed_numeric_rule_parameters_remain_unknown_in_both_policy_paths(self):
        from layout_configurator.facility import _profile_rule_checks
        source, layout, equipment, routes = self.source()
        raw = source.to_dict()
        raw["zones"][0]["pressure_pa"] = 20
        raw["zones"][1].update(pressure_pa=10, parent_zone_id="process", airlock=True)
        current = BuildingIR.from_mapping(raw)
        for kind, name, values in (("pressure_cascade", "minimum_delta_pa", ("NaN", "10", True, -1)),
                                   ("airlock_presence", "minimum_count", (1.9, "1", True, -1))):
            for value in values:
                with self.subTest(kind=kind, value=value):
                    policy = profile(kind, {name: value})
                    shared = _profile_rule_checks(current, policy)
                    self.assertEqual(shared[0].status, "UNKNOWN", shared[0].to_dict())
                    report = self.api()(current, layout, equipment, routes, profile=policy)
                    self.assertEqual(report.status, "UNKNOWN", report.to_dict())
                    self.assertFalse(report.ok)
        for kind, parameters in (("pressure_cascade", {"minimum_delta_pa": 10}),
                                 ("airlock_presence", {"minimum_count": 1})):
            with self.subTest(valid=kind):
                report = self.api()(current, layout, equipment, routes, profile=profile(kind, parameters))
                self.assertEqual(report.status, "PASS", report.to_dict())


if __name__ == "__main__":
    unittest.main()

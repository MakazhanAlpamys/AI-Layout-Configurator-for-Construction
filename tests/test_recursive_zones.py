import unittest
from dataclasses import replace

from layout_configurator.building import BuildingIR
from layout_configurator.models import LayoutResult, Rect
from layout_configurator.orthogonal import OrthogonalPolygon
from layout_configurator.solver import solve_layouts
from layout_configurator.validation import validate_layout
from layout_configurator.zone_hierarchy import (
    ZoneHierarchyFailure,
    ZoneHierarchyNotApplicable,
    solve_zone_layouts,
    validate_zone_layout,
)


def _program(grandchild=True, height=2000):
    zones = [
        {"id": "parent", "room_ids": []},
        {"id": "left", "parent_zone_id": "parent", "room_ids": ["a", "b"],
         "required_adjacency": ["right"]},
        {"id": "right", "parent_zone_id": "parent", "room_ids": ["c", "d"]},
    ]
    if grandchild:
        zones[1]["room_ids"] = ["a"]
        zones.append({"id": "leaf_b", "parent_zone_id": "left", "room_ids": ["b"]})
    rooms = [{"id": room_id, "target_area": 4, "min_area": 4, "max_area": 4,
              "min_width": 2000, "min_depth": 2000,
              "required_adjacency": [next_id] if next_id else []}
             for room_id, next_id in (("a", "b"), ("b", "c"), ("c", "d"), ("d", None))]
    return BuildingIR.from_mapping({
        "layout": {"boundary": {"width": 8000, "height": height}, "entry_room": "a", "rooms": rooms},
        "zones": zones,
    })


def _rects(evidence):
    return {zone_id: Rect(**raw) for zone_id, raw in evidence["variants"][0]["zone_rects"].items()}


class RecursiveZoneTests(unittest.TestCase):
    def _solve(self, building, sizes):
        evidence = {}
        result = solve_zone_layouts(building, 1, 0.5, 1, recursive_zones=True,
                                   zone_size_options_mm=sizes, evidence=evidence)[0]
        report = validate_zone_layout(building, result, _rects(evidence), recursive_zones=True)
        self.assertTrue(report.ok, report.issues)
        self.assertTrue(validate_layout(building.layout, result).ok)
        return result, evidence

    def test_three_levels_have_actual_contained_containers_with_sibling_exclusion(self):
        building = _program()
        result, evidence = self._solve(building, {
            "parent": [(8000, 2000)], "left": [(4000, 2000)],
            "right": [(4000, 2000)], "leaf_b": [(2000, 2000)],
        })
        containers = _rects(evidence)
        self.assertEqual(set(containers), {"parent", "left", "right", "leaf_b"})
        self.assertEqual(containers["parent"], Rect(0, 0, 8000, 2000))
        self.assertEqual(containers["left"].intersection_area(containers["right"]), 0)
        self.assertEqual(containers["left"].intersection_area(containers["parent"]), containers["left"].area_mm2)
        self.assertEqual(containers["leaf_b"], result.placements["b"])
        self.assertGreaterEqual(result.placements["b"].shared_boundary(result.placements["c"]), 900)
        self.assertTrue(evidence["descendant_container_geometry"])
        self.assertEqual(evidence["scope"], "recursive_zone_containers")
        self.assertTrue(any(attempt["stage"] == "subtree" and attempt["unit_id"] == "parent"
                            and attempt["accepted"] for attempt in evidence["stage_attempts"]))

    def test_supplied_child_shapes_are_solved_containers_not_room_bounding_boxes(self):
        building = _program(grandchild=False, height=4000)
        result, evidence = self._solve(building, {
            "parent": [(8000, 4000)], "left": [(4000, 4000)], "right": [(4000, 4000)],
        })
        containers = _rects(evidence)
        for zone_id in ("left", "right"):
            container = containers[zone_id]
            rooms = [result.placements[room_id] for room_id in building.room_ids_for_zone(zone_id)]
            self.assertEqual((container.width, container.height), (4000, 4000))
            self.assertGreater(container.area_mm2, sum(room.area_mm2 for room in rooms))
            self.assertGreater(container.height, max(room.top for room in rooms) - min(room.y for room in rooms))

    def test_default_root_contract_still_has_no_descendant_geometry(self):
        building = _program()
        evidence = {}
        solve_zone_layouts(building, 1, 0.5, 1, zone_size_options_mm={"parent": [(8000, 2000)]}, evidence=evidence)
        self.assertEqual(set(_rects(evidence)), {"parent"})
        self.assertFalse(evidence["descendant_container_geometry"])
        self.assertEqual(evidence["scope"], "root_zone_containers")

    def test_subtree_infeasibility_is_a_proof_only_for_selected_child_shapes(self):
        building = _program(grandchild=False)
        baseline = solve_layouts(building.layout, 1, 0.5, 1)[0]
        self.assertTrue(validate_layout(building.layout, baseline).ok)
        evidence = {}
        with self.assertRaises(ZoneHierarchyFailure) as caught:
            solve_zone_layouts(building, 1, 0.5, 1, recursive_zones=True, evidence=evidence,
                               zone_size_options_mm={"parent": [(8000, 2000)],
                                                     "left": [(6000, 2000)], "right": [(6000, 2000)]})
        self.assertEqual(caught.exception.stage, "subtree")
        self.assertEqual(caught.exception.unit_ids, ("parent",))
        self.assertFalse(caught.exception.to_dict()["proven_building_infeasible"])
        failures = [attempt["failure"] for attempt in evidence["stage_attempts"] if "failure" in attempt]
        self.assertTrue(failures)
        self.assertEqual(failures[-1]["model_status"], "INFEASIBLE")
        self.assertEqual(failures[-1]["proof_scope"], "selected_subtree_shapes")

    def test_independent_validator_allows_ancestors_and_rejects_missing_miscontained_or_overlapping_children(self):
        building = _program()
        result, evidence = self._solve(building, {
            "parent": [(8000, 2000)], "left": [(4000, 2000)],
            "right": [(4000, 2000)], "leaf_b": [(2000, 2000)],
        })
        containers = _rects(evidence)
        missing = {key: value for key, value in containers.items() if key != "leaf_b"}
        report = validate_zone_layout(building, result, missing, recursive_zones=True)
        self.assertIn("missing_zone_container", {issue.code for issue in report.issues})
        outside = {**containers, "leaf_b": containers["right"]}
        report = validate_zone_layout(building, result, outside, recursive_zones=True)
        self.assertIn("child_zone_containment", {issue.code for issue in report.issues})
        overlap = {**containers, "right": containers["left"]}
        report = validate_zone_layout(building, result, overlap, recursive_zones=True)
        self.assertIn("zone_overlap", {issue.code for issue in report.issues})

    def test_ancestor_duplicate_membership_assigns_the_room_only_to_the_deepest_child(self):
        building = _program()
        zones = tuple(replace(zone, room_ids=("b",)) if zone.id == "parent" else zone for zone in building.zones)
        building = replace(building, zones=zones)
        result, evidence = self._solve(building, {
            "parent": [(8000, 2000)], "left": [(4000, 2000)],
            "right": [(4000, 2000)], "leaf_b": [(2000, 2000)],
        })
        self.assertEqual(_rects(evidence)["leaf_b"], result.placements["b"])
        direct = [unit for unit in evidence["units"] if unit["kind"] == "direct_room"]
        self.assertEqual([unit["room_ids"] for unit in direct], [["a"]])

    def test_recursive_flag_rejects_non_boolean_types_before_search(self):
        for value in (1, "true", None):
            with self.subTest(value=value):
                evidence = {}
                with self.assertRaises(ZoneHierarchyNotApplicable) as caught:
                    solve_zone_layouts(_program(), 1, 0.5, 1, recursive_zones=value, evidence=evidence)
                self.assertEqual(caught.exception.stage, "input")
                self.assertEqual(caught.exception.code, "invalid_recursive_mode")
                self.assertEqual(evidence["stage_attempts"], [])

    def test_rectangular_backend_rejects_polygon_results_before_relation_methods(self):
        building = _program()
        polygon = OrthogonalPolygon(((0, 0), (2000, 0), (2000, 2000), (0, 2000)))
        result = LayoutResult(1, {"a": polygon, "b": Rect(2000, 0, 2000, 2000),
                                  "c": Rect(4000, 0, 2000, 2000), "d": Rect(6000, 0, 2000, 2000)})
        for recursive in (False, True):
            with self.subTest(recursive=recursive):
                report = validate_zone_layout(building, result, {"parent": Rect(0, 0, 8000, 2000)},
                                              recursive_zones=recursive)
                self.assertEqual([issue.code for issue in report.issues], ["unsupported_geometry"])

    def test_empty_descendants_and_unassigned_rooms_remain_explicit_without_fake_containers(self):
        raw = _program().to_dict()
        raw["layout"]["boundary"]["width"] = 10000
        raw["layout"]["rooms"][-1]["required_adjacency"] = ["extra"]
        raw["layout"]["rooms"].append({"id": "extra", "target_area": 4, "min_area": 4,
                                        "max_area": 4, "min_width": 2000, "min_depth": 2000})
        raw["zones"].append({"id": "empty_child", "parent_zone_id": "left", "room_ids": []})
        raw["zones"].append({"id": "empty_root", "room_ids": []})
        building = BuildingIR.from_mapping(raw)
        result, evidence = self._solve(building, {
            "parent": [(8000, 2000)], "left": [(4000, 2000)],
            "right": [(4000, 2000)], "leaf_b": [(2000, 2000)], "room:extra": [(2000, 2000)],
        })
        self.assertEqual(set(evidence["empty_zone_ids"]), {"empty_child", "empty_root"})
        containers = _rects(evidence)
        self.assertNotIn("empty_child", containers)
        self.assertEqual(containers["room:extra"], result.placements["extra"])
        self.assertEqual(containers["parent"].intersection_area(containers["room:extra"]), 0)

    def test_global_entry_window_and_cutout_constraints_survive_descendant_stages(self):
        building = BuildingIR.from_mapping({
            "layout": {
                "boundary": {"width": 12000, "height": 4000,
                             "cutouts": [{"x": 0, "y": 0, "width": 4000, "height": 2000}]},
                "entry_room": "entry",
                "rooms": [{"id": room_id, "target_area": 4, "min_area": 4, "max_area": 4,
                           "min_width": 2000, "min_depth": 2000,
                           "required_adjacency": ["entry"] if room_id == "office" else [],
                           "needs_daylight": room_id == "office"} for room_id in ("office", "entry")],
                "windows": [{"id": "office_window", "room_id": "office", "side": "bottom",
                             "offset_mm": 1000, "width_mm": 800}],
                "external_entry": {"id": "entrance", "room_id": "entry", "side": "right",
                                   "offset_mm": 1000, "width_mm": 900},
            },
            "zones": [{"id": "parent", "room_ids": []},
                      {"id": "work", "parent_zone_id": "parent", "room_ids": ["office"]},
                      {"id": "access", "parent_zone_id": "parent", "room_ids": ["entry"]}],
        })
        result, evidence = self._solve(building, {
            "parent": [(4000, 2000)], "work": [(2000, 2000)], "access": [(2000, 2000)],
        })
        self.assertEqual(result.placements["entry"].right, 12000)
        self.assertEqual(result.placements["office"].y, 0)
        for container in _rects(evidence).values():
            self.assertEqual(container.intersection_area(building.layout.boundary.cutouts[0]), 0)

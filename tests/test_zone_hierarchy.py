import unittest
from dataclasses import replace

from layout_configurator.building import BuildingIR, ZoneSpec
from layout_configurator.hierarchy import SOLVER_STRATEGIES
from layout_configurator.models import Rect
from layout_configurator.solver import solve_layouts
from layout_configurator.validation import validate_layout
from layout_configurator.zone_hierarchy import (
    ZoneHierarchyFailure,
    ZoneHierarchyNotApplicable,
    solve_zone_layouts,
    validate_zone_layout,
)


def _room(room_id, **extra):
    return {
        "id": room_id, "target_area": 4, "min_area": 4, "max_area": 4,
        "min_width": 2000, "min_depth": 2000, **extra,
    }


def _two_zones(*, width=8000, descendants=False):
    zones = [
        {"id": "left", "room_ids": ["a", "b"], "required_adjacency": ["right"]},
        {"id": "right", "room_ids": ["c", "d"]},
    ]
    if descendants:
        zones[0]["room_ids"] = ["a"]
        zones.append({"id": "left_child", "parent_zone_id": "left", "room_ids": ["b"]})
    return BuildingIR.from_mapping({
        "layout": {
            "boundary": {"width": width, "height": 2000}, "entry_room": "a",
            "rooms": [
                _room("a", required_adjacency=["b"]),
                _room("b", required_adjacency=["c"]),
                _room("c", required_adjacency=["d"]), _room("d"),
            ],
        },
        "zones": zones,
    })


def _zone_rects(evidence, variant=0):
    return {unit_id: Rect(**raw) for unit_id, raw in evidence["variants"][variant]["zone_rects"].items()}


class ZoneHierarchyTests(unittest.TestCase):
    def _solve(self, building, **options):
        evidence = {}
        result = solve_zone_layouts(building, 1, 0.5, 1, evidence=evidence, **options)[0]
        self.assertTrue(validate_layout(building.layout, result).ok)
        report = validate_zone_layout(building, result, _zone_rects(evidence))
        self.assertTrue(report.ok, report.issues)
        return result, evidence

    def test_rooms_are_solved_inside_two_nonoverlapping_zone_containers(self):
        building = _two_zones()
        result, evidence = self._solve(
            building, zone_size_options_mm={"left": [(4000, 2000)], "right": [(4000, 2000)]},
        )
        containers = _zone_rects(evidence)
        self.assertEqual(set(containers), {"left", "right"})
        self.assertEqual(containers["left"].intersection_area(containers["right"]), 0)
        for zone_id in containers:
            for room_id in building.room_ids_for_zone(zone_id):
                room = result.placements[room_id]
                container = containers[zone_id]
                self.assertGreaterEqual(room.x, container.x)
                self.assertGreaterEqual(room.y, container.y)
                self.assertLessEqual(room.right, container.right)
                self.assertLessEqual(room.top, container.top)
        self.assertEqual(evidence["room_solver_used"], "zone_hierarchical")
        self.assertEqual(evidence["scope"], "root_zone_containers")

    def test_external_room_and_zone_relations_are_final_hard_constraints(self):
        building = _two_zones()
        result, _ = self._solve(building)
        self.assertGreaterEqual(result.placements["b"].shared_boundary(result.placements["c"]), 900)
        self.assertEqual(result.placements["a"].shared_boundary(result.placements["d"]), 0)

    def test_descendants_use_the_ancestor_container_without_claiming_child_geometry(self):
        building = _two_zones(descendants=True)
        result, evidence = self._solve(building)
        self.assertEqual(set(_zone_rects(evidence)), {"left", "right"})
        self.assertFalse(evidence["descendant_container_geometry"])
        left = _zone_rects(evidence)["left"]
        child_room = result.placements["b"]
        self.assertGreaterEqual(child_room.x, left.x)
        self.assertLessEqual(child_room.right, left.right)

    def test_empty_roots_are_explicit_and_unassigned_rooms_get_singleton_units(self):
        building = BuildingIR.from_mapping({
            "layout": {
                "boundary": {"width": 4000, "height": 2000},
                "rooms": [_room("a", required_adjacency=["b"]), _room("b")],
            },
            "zones": [{"id": "left", "room_ids": ["a"]}, {"id": "empty"}],
        })
        _, evidence = self._solve(building)
        self.assertEqual(evidence["empty_zone_ids"], ["empty"])
        unassigned = [unit for unit in evidence["units"] if unit["kind"] == "unassigned_room"]
        self.assertEqual(len(unassigned), 1)
        self.assertEqual(unassigned[0]["room_ids"], ["b"])
        self.assertIn(unassigned[0]["id"], _zone_rects(evidence))

    def test_overlapping_sibling_memberships_are_not_applicable_with_provenance(self):
        building = replace(_two_zones(), zones=(
            ZoneSpec("parent", "zone"),
            ZoneSpec("left", "zone", ("a", "b"), parent_zone_id="parent"),
            ZoneSpec("right", "zone", ("b", "c", "d"), parent_zone_id="parent"),
        ))
        evidence = {}
        with self.assertRaises(ZoneHierarchyNotApplicable) as caught:
            solve_zone_layouts(building, 1, 0.1, evidence=evidence)
        failure = caught.exception.to_dict()
        self.assertEqual(failure["stage"], "membership")
        self.assertEqual(failure["code"], "ambiguous_membership")
        self.assertEqual(set(failure["unit_ids"]), {"left", "right"})
        self.assertEqual(failure["room_ids"], ["b"])
        self.assertEqual(evidence["failure"], failure)

    def test_ancestor_direct_and_inherited_membership_is_unambiguous(self):
        # BuildingIR's public dataclass can express repeated ancestry; its
        # YAML parser currently rejects duplicate direct memberships.
        building = replace(_two_zones(), zones=(
            ZoneSpec("left", "zone", ("a", "b")),
            ZoneSpec("child", "zone", ("b",), parent_zone_id="left"),
            ZoneSpec("right", "zone", ("c", "d")),
        ))
        _, evidence = self._solve(building)
        self.assertEqual(next(unit["room_ids"] for unit in evidence["units"] if unit["id"] == "left"), ["a", "b"])

    def test_invalid_size_options_are_rejected_before_search(self):
        for options in (
            {"missing": [(4000, 2000)]}, {"left": [(0, 2000)]},
            {"left": [(float("nan"), 2000)]}, {"left": [(float("inf"), 2000)]},
            {"left": [(4050, 2000)]}, {"left": [(1e-9, 2000)]}, {"left": []},
        ):
            with self.subTest(options=options):
                with self.assertRaises(ZoneHierarchyNotApplicable) as caught:
                    solve_zone_layouts(_two_zones(), 1, 0.1, zone_size_options_mm=options)
                self.assertEqual(caught.exception.to_dict()["stage"], "input")

    def test_search_counts_reject_float_bool_and_string_before_model_creation(self):
        for field in ("variants", "max_local_options", "max_validation_attempts"):
            for value in (1.0, True, "1"):
                with self.subTest(field=field, value=value):
                    evidence = {}
                    with self.assertRaises(ZoneHierarchyNotApplicable) as caught:
                        solve_zone_layouts(_two_zones(), time_limit_seconds=0.1, evidence=evidence, **{field: value})
                    failure = caught.exception.to_dict()
                    self.assertEqual(failure["stage"], "input")
                    self.assertEqual(failure["code"], "invalid_budget")
                    self.assertEqual(evidence["stage_attempts"], [])

    def test_zone_sizes_reject_numeric_strings_and_boolean_coordinates(self):
        building = replace(_two_zones(), layout=replace(_two_zones().layout, grid_mm=1))
        for dimensions in (("4000", 2000), (4000, "2000"), (True, 2000), (4000, True)):
            with self.subTest(dimensions=dimensions):
                evidence = {}
                with self.assertRaises(ZoneHierarchyNotApplicable) as caught:
                    solve_zone_layouts(
                        building, 1, 0.1, evidence=evidence,
                        zone_size_options_mm={"left": [dimensions]},
                    )
                failure = caught.exception.to_dict()
                self.assertEqual(failure["stage"], "input")
                self.assertEqual(failure["code"], "invalid_zone_size")
                self.assertEqual(evidence["stage_attempts"], [])

    def test_a_local_shape_failure_does_not_claim_the_building_is_infeasible(self):
        evidence = {}
        with self.assertRaises(ZoneHierarchyFailure) as caught:
            solve_zone_layouts(
                _two_zones(), 1, 0.1, zone_size_options_mm={"left": [(2000, 2000)]}, evidence=evidence,
            )
        failure = caught.exception.to_dict()
        self.assertEqual(failure["stage"], "local")
        self.assertEqual(failure["unit_ids"], ["left"])
        self.assertFalse(failure["proven_building_infeasible"])
        self.assertTrue(evidence["stage_attempts"])

    def test_failed_local_option_is_recorded_before_a_valid_option_is_used(self):
        _, evidence = self._solve(
            _two_zones(), zone_size_options_mm={"left": [(2000, 2000), (4000, 2000)]},
        )
        attempts = [attempt for attempt in evidence["stage_attempts"] if attempt.get("unit_id") == "left"]
        self.assertTrue(any(attempt["accepted"] is False for attempt in attempts))
        self.assertTrue(any(attempt["accepted"] is True for attempt in attempts))

    def test_global_infeasibility_identifies_the_restricted_macro_model(self):
        evidence = {}
        with self.assertRaises(ZoneHierarchyFailure) as caught:
            solve_zone_layouts(
                _two_zones(width=6000), 1, 0.1,
                zone_size_options_mm={"left": [(4000, 2000)], "right": [(4000, 2000)]},
                evidence=evidence,
            )
        failure = caught.exception.to_dict()
        self.assertEqual(failure["stage"], "global")
        self.assertEqual(failure["model_status"], "INFEASIBLE")
        self.assertEqual(failure["proof_scope"], "selected_zone_shapes")
        self.assertFalse(failure["proven_building_infeasible"])

    def test_container_infeasibility_can_coexist_with_a_valid_monolithic_layout(self):
        # Alternating memberships can form a valid room chain, but two solid
        # disjoint rectangular zone containers cannot represent that chain.
        building = replace(_two_zones(), zones=(
            ZoneSpec("odd", "zone", ("a", "c")),
            ZoneSpec("even", "zone", ("b", "d")),
        ))
        monolithic = solve_layouts(building.layout, 1, 0.5, 1)[0]
        self.assertTrue(validate_layout(building.layout, monolithic).ok)
        with self.assertRaises(ZoneHierarchyFailure) as caught:
            solve_zone_layouts(
                building, 1, 0.1,
                zone_size_options_mm={"odd": [(4000, 2000)], "even": [(4000, 2000)]},
            )
        self.assertEqual(caught.exception.to_dict()["stage"], "global")
        self.assertEqual(caught.exception.to_dict()["proof_scope"], "selected_zone_shapes")
        self.assertFalse(caught.exception.to_dict()["proven_building_infeasible"])

    def test_local_room_shapes_preserve_equipment_clearance_envelopes(self):
        building = BuildingIR.from_mapping({
            "layout": {
                "boundary": {"width": 4000, "height": 4000}, "wall_thickness_mm": 200,
                "rooms": [{**_room("machine_room"), "target_area": 6, "min_area": 6, "max_area": 6}],
            },
            "zones": [{"id": "production", "room_ids": ["machine_room"]}],
            "equipment": [{
                "id": "machine", "room_id": "machine_room", "width_mm": 2500, "depth_mm": 1500,
                "clearance_mm": 100, "rotation_allowed": False,
            }],
        })
        result, evidence = self._solve(
            building, zone_size_options_mm={"production": [(2000, 3000), (3000, 2000)]},
        )
        self.assertEqual(result.placements["machine_room"].width, 3000)
        self.assertEqual(result.placements["machine_room"].height, 2000)
        attempts = [item for item in evidence["stage_attempts"] if item.get("unit_id") == "production"]
        self.assertFalse(attempts[0]["accepted"])
        self.assertTrue(attempts[1]["accepted"])

    def test_cross_zone_preference_beats_position_in_the_final_model(self):
        building = BuildingIR.from_mapping({
            "layout": {
                "boundary": {"width": 8000, "height": 4000}, "entry_room": "a",
                "rooms": [
                    {**_room("corridor"), "target_area": 16, "min_area": 16, "max_area": 16, "min_width": 8000},
                    _room("a", required_adjacency=["corridor"]),
                    _room("c", required_adjacency=["corridor"]),
                ],
                "external_entry": {"id": "entrance", "room_id": "a", "side": "right", "offset_mm": 1000, "width_mm": 900},
            },
            "zones": [
                {"id": "corridor_zone", "room_ids": ["corridor"]},
                {"id": "receiving", "room_ids": ["a"], "preferred_adjacency": ["dispatch"]},
                {"id": "dispatch", "room_ids": ["c"]},
            ],
        })
        result, _ = self._solve(building, zone_size_options_mm={"corridor_zone": [(8000, 2000)]})
        self.assertEqual(result.placements["c"].x, 4000)
        self.assertGreaterEqual(result.placements["c"].shared_boundary(result.placements["a"]), 900)

    def test_impossible_zone_preference_remains_soft_below_forbidden_relations(self):
        building = BuildingIR.from_mapping({
            "layout": {
                "boundary": {"width": 8000, "height": 4000}, "entry_room": "corridor",
                "rooms": [
                    {**_room("corridor"), "target_area": 16, "min_area": 16, "max_area": 16, "min_width": 8000},
                    _room("a", required_adjacency=["corridor"]), _room("b", required_adjacency=["corridor"]),
                ],
            },
            "zones": [
                {"id": "corridor_zone", "room_ids": ["corridor"]},
                {"id": "left", "room_ids": ["a"], "preferred_adjacency": ["right"], "forbidden_adjacency": ["right"]},
                {"id": "right", "room_ids": ["b"]},
            ],
        })
        result, _ = self._solve(building, zone_size_options_mm={"corridor_zone": [(8000, 2000)]})
        self.assertEqual(result.placements["a"].shared_boundary(result.placements["b"]), 0)

    def test_daylight_cutouts_and_external_entry_survive_local_filtering(self):
        building = BuildingIR.from_mapping({
            "layout": {
                "boundary": {"width": 6000, "height": 4000, "cutouts": [{"x": 0, "y": 0, "width": 2000, "height": 2000}]},
                "rooms": [_room("office", needs_daylight=True, required_adjacency=["entry"]), _room("entry")],
                "entry_room": "entry",
                "external_entry": {"id": "entrance", "room_id": "entry", "side": "right", "offset_mm": 1000, "width_mm": 900},
            },
            "zones": [{"id": "work", "room_ids": ["office"]}, {"id": "access", "room_ids": ["entry"]}],
        })
        result, evidence = self._solve(building)
        self.assertEqual(result.placements["entry"].right, 6000)
        for container in _zone_rects(evidence).values():
            self.assertEqual(container.intersection_area(building.layout.boundary.cutouts[0]), 0)

    def test_independent_validation_detects_containment_and_container_overlap(self):
        building = _two_zones()
        result, evidence = self._solve(building)
        containers = _zone_rects(evidence)
        wrong = {"left": containers["right"], "right": containers["right"]}
        report = validate_zone_layout(building, result, wrong)
        self.assertFalse(report.ok)
        self.assertIn("zone_containment", {issue.code for issue in report.issues})
        self.assertIn("zone_overlap", {issue.code for issue in report.issues})

    def test_explicit_window_side_is_enforced_in_the_global_stage(self):
        building = BuildingIR.from_mapping({
            "layout": {
                "boundary": {"width": 4000, "height": 4000},
                "rooms": [_room("office", needs_daylight=True)],
                "windows": [{"id": "east_window", "room_id": "office", "side": "right", "offset_mm": 1000, "width_mm": 600}],
            },
            "zones": [{"id": "work", "room_ids": ["office"]}],
        })
        result, _ = self._solve(building, zone_size_options_mm={"work": [(2000, 2000)]})
        self.assertEqual(result.placements["office"].right, 4000)

    def test_independently_invalid_candidates_have_validation_stage_evidence(self):
        # Both rooms fit geometrically, but forbidden contact prevents entry
        # reachability. A feasible macro model is not an accepted room layout.
        building = BuildingIR.from_mapping({
            "layout": {
                "boundary": {"width": 5000, "height": 2000},
                "rooms": [_room("a"), _room("b")],
            },
            "zones": [
                {"id": "left", "room_ids": ["a"], "forbidden_adjacency": ["right"]},
                {"id": "right", "room_ids": ["b"]},
            ],
        })
        evidence = {}
        with self.assertRaises(ZoneHierarchyFailure) as caught:
            solve_zone_layouts(building, 1, 0.1, evidence=evidence, max_validation_attempts=2)
        failure = caught.exception.to_dict()
        self.assertEqual(failure["stage"], "validation")
        self.assertTrue(any("unreachable_room" in issue for issue in failure["issues"]))
        self.assertFalse(failure["proven_building_infeasible"])
        self.assertEqual(len([item for item in evidence["stage_attempts"] if item["stage"] == "global"]), 2)

    def test_an_empty_required_zone_relation_is_not_silently_dropped(self):
        building = BuildingIR.from_mapping({
            "layout": {"boundary": {"width": 2000, "height": 2000}, "rooms": [_room("a")]},
            "zones": [
                {"id": "left", "room_ids": ["a"], "required_adjacency": ["empty"]},
                {"id": "empty"},
            ],
        })
        with self.assertRaises(ZoneHierarchyFailure) as caught:
            solve_zone_layouts(building, 1, 0.1)
        self.assertEqual(caught.exception.to_dict()["stage"], "global")
        self.assertEqual(caught.exception.to_dict()["code"], "empty_required_group")

    def test_existing_dispatcher_strategies_are_unchanged(self):
        self.assertEqual(SOLVER_STRATEGIES, ("auto", "monolithic", "hierarchical"))

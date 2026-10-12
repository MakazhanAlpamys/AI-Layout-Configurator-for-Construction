import unittest

from layout_configurator.building import BuildingIR
from layout_configurator.equipment import EquipmentLayoutResult, EquipmentPlacement
from layout_configurator.flows import route_flows, validate_flow_routes
from layout_configurator.models import LayoutResult, Rect


class AlternativeRoomRouteTests(unittest.TestCase):
    def test_uses_longer_room_chain_when_shortest_chain_has_blocking_equipment(self):
        """Keeping only BFS's first path must fail this real clearance fixture."""
        building, layout, equipment = _blocked_bottom_path()

        routes = route_flows(building, layout, equipment)
        report = validate_flow_routes(building, layout, routes, equipment)

        self.assertTrue(report.ok, report.to_dict())
        self.assertEqual(len(routes.routes), 1)
        self.assertEqual(
            routes.routes[0].room_path,
            ("source", "upper_left", "upper_middle", "upper_right", "target"),
        )

    def test_uses_wider_door_chain_when_direct_opening_is_too_narrow(self):
        """A shorter chain cannot win by ignoring the flow's door width."""
        placements = {
            "source": Rect(0, 0, 3000, 3000),
            "target": Rect(3000, 0, 3000, 3000),
            "upper_left": Rect(0, 3000, 3000, 3000),
            "upper_right": Rect(3000, 3000, 3000, 3000),
        }
        building = _building(
            placements,
            (("source", "target"), ("source", "upper_left"),
             ("upper_left", "upper_right"), ("upper_right", "target")),
            doors=[{
                "id": "narrow_direct", "room_a": "source", "room_b": "target",
                "width_mm": 600, "offset_mm": 1500,
            }],
        )
        layout = LayoutResult(variant=1, placements=placements)

        routes = route_flows(building, layout)
        report = validate_flow_routes(building, layout, routes)

        self.assertTrue(report.ok, report.to_dict())
        self.assertEqual(
            routes.routes[0].room_path,
            ("source", "upper_left", "upper_right", "target"),
        )

    def test_equal_length_alternatives_are_stable_when_input_room_order_changes(self):
        """A graph's insertion order must not choose a different clear lane."""
        placements = {
            "source": Rect(0, 0, 3000, 9000),
            "a_blocked": Rect(3000, 3000, 3000, 3000),
            "b_lower": Rect(3000, 0, 3000, 3000),
            "c_upper": Rect(3000, 6000, 3000, 3000),
            "target": Rect(6000, 0, 3000, 9000),
        }
        pairs = tuple(
            pair
            for middle in ("a_blocked", "b_lower", "c_upper")
            for pair in (("source", middle), (middle, "target"))
        )
        equipment_specs = [{
            "id": "barrier", "type": "machine", "room_id": "a_blocked",
            "width_mm": 400, "depth_mm": 2600,
        }]
        equipment = EquipmentLayoutResult((
            EquipmentPlacement("barrier", "a_blocked", Rect(4300, 3200, 400, 2600)),
        ))
        original = _building(placements, pairs, equipment=equipment_specs)
        reordered = _building(dict(reversed(tuple(placements.items()))), pairs, equipment=equipment_specs)
        layout = LayoutResult(variant=1, placements=placements)

        routes = route_flows(original, layout, equipment)
        reordered_routes = route_flows(reordered, layout, equipment)

        self.assertTrue(validate_flow_routes(original, layout, routes, equipment).ok)
        self.assertEqual(routes.routes[0].room_path, ("source", "b_lower", "target"))
        self.assertEqual(reordered_routes.to_dict(), routes.to_dict())

    def test_keeps_collision_evidence_when_every_room_chain_is_blocked(self):
        """Trying alternatives must not erase the primary route's failure."""
        building, layout, equipment = _blocked_bottom_path()
        payload = building.to_dict()
        payload["layout"]["doors"] = [{
            "id": "narrow_upper", "room_a": "upper_left", "room_b": "upper_middle",
            "width_mm": 600, "offset_mm": 1500,
        }]
        building = BuildingIR.from_mapping(payload)

        routes = route_flows(building, layout, equipment)
        report = validate_flow_routes(building, layout, routes, equipment)

        self.assertFalse(report.ok)
        self.assertEqual(routes.routes[0].room_path, ("source", "blocked", "target"))
        self.assertIn("equipment_clearance_collision", {issue.code for issue in report.issues})

    def test_candidate_budget_stops_before_clear_longer_path_without_losing_evidence(self):
        """A finite route budget must leave an invalid route visible to QA."""
        building, layout, equipment = _blocked_bottom_path()

        routes = route_flows(building, layout, equipment, max_route_candidates=1)
        report = validate_flow_routes(building, layout, routes, equipment)

        self.assertFalse(report.ok)
        self.assertEqual(routes.routes[0].room_path, ("source", "blocked", "target"))
        self.assertIn("equipment_clearance_collision", {issue.code for issue in report.issues})

    def test_search_state_budget_stops_before_clear_path_without_losing_evidence(self):
        """A route search must obey its state bound even before it finds a path."""
        building, layout, equipment = _blocked_bottom_path()

        routes = route_flows(building, layout, equipment, max_room_path_expansions=1)
        report = validate_flow_routes(building, layout, routes, equipment)

        self.assertFalse(report.ok)
        self.assertEqual(routes.routes[0].room_path, ("source", "blocked", "target"))
        self.assertIn("equipment_clearance_collision", {issue.code for issue in report.issues})

    def test_rejects_nonpositive_search_budgets(self):
        building, layout, equipment = _blocked_bottom_path()
        for option in ("max_route_candidates", "max_room_path_expansions"):
            for budget in (0, -1):
                with self.subTest(option=option, budget=budget):
                    with self.assertRaises(ValueError):
                        route_flows(building, layout, equipment, **{option: budget})


def _blocked_bottom_path():
    placements = {
        "source": Rect(0, 0, 3000, 3000),
        "blocked": Rect(3000, 0, 3000, 3000),
        "target": Rect(6000, 0, 3000, 3000),
        "upper_left": Rect(0, 3000, 3000, 3000),
        "upper_middle": Rect(3000, 3000, 3000, 3000),
        "upper_right": Rect(6000, 3000, 3000, 3000),
    }
    building = _building(
        placements,
        (("source", "blocked"), ("blocked", "target"),
         ("source", "upper_left"), ("upper_left", "upper_middle"),
         ("upper_middle", "upper_right"), ("upper_right", "target")),
        equipment=[{
            "id": "barrier", "type": "machine", "room_id": "blocked",
            "width_mm": 400, "depth_mm": 2600,
        }],
    )
    equipment = EquipmentLayoutResult((
        EquipmentPlacement("barrier", "blocked", Rect(4300, 200, 400, 2600)),
    ))
    return building, LayoutResult(variant=1, placements=placements), equipment


def _building(placements, pairs, *, equipment=(), doors=()):
    adjacency = {room_id: [] for room_id in placements}
    for left, right in pairs:
        adjacency[left].append(right)
        adjacency[right].append(left)
    return BuildingIR.from_mapping({
        "version": "0.2",
        "layout": {
            "boundary": {
                "width": max(rect.right for rect in placements.values()),
                "height": max(rect.top for rect in placements.values()),
            },
            "entry_room": "source",
            "wall_thickness_mm": 200,
            "door_width_mm": 1200,
            "doors": list(doors),
            "rooms": [{
                "id": room_id, "type": "room", "target_area": rect.area_m2,
                "min_area": 1, "max_area": 30,
                "min_width": 1000, "min_depth": 1000,
                "required_adjacency": adjacency[room_id],
            } for room_id, rect in placements.items()],
        },
        "equipment": list(equipment),
        "flows": [{
            "id": "material_flow", "type": "material",
            "from_ids": ["source"], "to_ids": ["target"],
            "minimum_clear_width_mm": 800,
        }],
    })


if __name__ == "__main__":
    unittest.main()

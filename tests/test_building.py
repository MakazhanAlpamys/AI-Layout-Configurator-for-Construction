import unittest

from layout_configurator.building import BuildingIR, BuildingSpecError
from layout_configurator.schema import validate_building


class BuildingIRTests(unittest.TestCase):
    def test_pharmaceutical_clean_production_pilot_contains_program_objects(self):
        building = BuildingIR.from_mapping(_load_pilot())

        self.assertEqual(building.layout.project_name, "Pharmaceutical clean-production pilot")
        self.assertEqual(building.layout.boundary.width_mm, 42000)
        self.assertEqual(len(building.layout.rooms), 10)
        self.assertEqual(len(building.zones), 3)
        self.assertEqual(len(building.equipment), 5)
        self.assertEqual(len(building.flows), 5)
        self.assertEqual(building.structural_grid.axes_x_mm[-1], 42000)

    def test_canonical_building_round_trip_and_schema(self):
        building = BuildingIR.from_mapping(_load_pilot())
        canonical = building.to_dict()
        round_tripped = BuildingIR.from_mapping(canonical)

        self.assertEqual(round_tripped, building)
        self.assertEqual(validate_building(building, "schemas/building_ir.schema.json"), ())

    def test_equipment_shared_clearance_is_expanded(self):
        raw = _minimal_building()
        raw["equipment"] = [
            {
                "id": "machine",
                "type": "machine",
                "room_id": "room",
                "width_mm": 1000,
                "depth_mm": 800,
                "clearance_mm": 500,
            }
        ]

        building = BuildingIR.from_mapping(raw)

        self.assertEqual(building.equipment[0].clearance_front_mm, 500)
        self.assertEqual(building.equipment[0].clearance_right_mm, 500)

    def test_unknown_flow_endpoint_is_rejected(self):
        raw = _minimal_building()
        raw["flows"] = [
            {"id": "flow", "type": "people", "from_ids": ["room"], "to_ids": ["missing"]}
        ]

        with self.assertRaisesRegex(BuildingSpecError, "unknown endpoints"):
            BuildingIR.from_mapping(raw)

    def test_duplicate_room_membership_is_rejected(self):
        raw = _minimal_building()
        raw["zones"] = [
            {"id": "zone_a", "type": "area", "room_ids": ["room"]},
            {"id": "zone_b", "type": "area", "room_ids": ["room"]},
        ]

        with self.assertRaisesRegex(BuildingSpecError, "multiple zones"):
            BuildingIR.from_mapping(raw)

    def test_zone_hierarchy_cycle_is_rejected(self):
        raw = _minimal_building()
        raw["zones"] = [
            {"id": "zone_a", "type": "area", "parent_zone_id": "zone_b"},
            {"id": "zone_b", "type": "area", "parent_zone_id": "zone_a"},
        ]

        with self.assertRaisesRegex(BuildingSpecError, "cycle"):
            BuildingIR.from_mapping(raw)

    def test_schema_rejects_generated_coordinates_at_building_boundary(self):
        building = BuildingIR.from_mapping(_minimal_building())
        canonical = building.to_dict()
        canonical["placements"] = {"room": {"x": 0, "y": 0}}

        issues = validate_building_mapping(canonical)

        self.assertIn("placements", "\n".join(issue.message for issue in issues))


def validate_building_mapping(mapping):
    from layout_configurator.schema import validate_mapping

    return validate_mapping(mapping, "schemas/building_ir.schema.json")


def _minimal_building():
    return {
        "version": "0.2",
        "project_name": "minimal",
        "layout": {
            "boundary": {"width": 6000, "height": 4000},
            "entry_room": "room",
            "rooms": [{"id": "room", "type": "room", "target_area": 12}],
        },
        "zones": [],
        "equipment": [],
        "flows": [],
    }


def _load_pilot():
    import yaml

    with open("examples/commercial_pilot.yaml", encoding="utf-8") as handle:
        return yaml.safe_load(handle)


if __name__ == "__main__":
    unittest.main()

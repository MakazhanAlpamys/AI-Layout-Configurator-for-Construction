import json
import tempfile
import unittest
from pathlib import Path

import ezdxf
import ifcopenshell

from layout_configurator.building import BuildingIR
from layout_configurator.cli import main
from layout_configurator.equipment import (
    EquipmentLayoutResult,
    EquipmentPlacement,
    place_equipment,
    validate_equipment_layout,
)
from layout_configurator.models import LayoutResult, Rect


class EquipmentTests(unittest.TestCase):
    def test_first_fit_respects_wall_inset_and_clearance(self):
        building = _building(
            [
                {
                    "id": "machine",
                    "type": "machine",
                    "room_id": "production",
                    "width_mm": 2000,
                    "depth_mm": 1000,
                    "clearance_front_mm": 1200,
                    "clearance_left_mm": 500,
                    "clearance_right_mm": 500,
                }
            ]
        )

        result = place_equipment(building, _rooms())
        placement = result.placements[0]
        clearance = result.to_dict(building)["equipment"][0]["clearance"]

        self.assertEqual(placement.room_id, "production")
        self.assertGreaterEqual(placement.rect.x, 100)
        self.assertGreaterEqual(placement.rect.y, 100)
        self.assertGreaterEqual(clearance["x"], 100)
        self.assertGreaterEqual(clearance["y"], 100)
        self.assertTrue(validate_equipment_layout(building, _rooms(), result).ok)

    def test_rotates_when_only_rotated_footprint_fits(self):
        building = _building(
            [
                {
                    "id": "long_machine",
                    "type": "machine",
                    "room_id": "production",
                    "width_mm": 4000,
                    "depth_mm": 2000,
                    "rotation_allowed": True,
                }
            ],
            room_width=3000,
            room_height=5000,
        )
        result = place_equipment(building, _rooms(room_width=3000, room_height=5000))

        self.assertTrue(result.placements[0].rotated)
        self.assertEqual(result.placements[0].rect.width, 2000)
        self.assertEqual(result.placements[0].rect.height, 4000)

    def test_fixed_equipment_uses_wall_anchor_without_input_coordinates(self):
        building = _building(
            [
                {
                    "id": "line",
                    "type": "production_line",
                    "room_id": "production",
                    "width_mm": 2000,
                    "depth_mm": 1000,
                    "clearance_front_mm": 500,
                    "fixed": True,
                    "anchor_side": "top",
                    "anchor_offset_mm": 600,
                }
            ]
        )
        placement = place_equipment(building, _rooms()).placements[0]

        self.assertEqual(placement.rect.x, 700)
        self.assertEqual(placement.rect.top, 4400)

    def test_zone_equipment_can_be_assigned_to_a_descendant_room(self):
        building = _building(
            [
                {
                    "id": "zone_machine",
                    "type": "machine",
                    "zone_id": "production_zone",
                    "width_mm": 1000,
                    "depth_mm": 1000,
                }
            ],
            zones=[{"id": "production_zone", "type": "production", "room_ids": ["production"]}],
        )

        result = place_equipment(building, _rooms())

        self.assertEqual(result.placements[0].room_id, "production")

    def test_independent_validation_reports_clearance_collision(self):
        building = _building(
            [
                {"id": "a", "type": "machine", "room_id": "production", "width_mm": 1200, "depth_mm": 1000, "clearance_mm": 600},
                {"id": "b", "type": "machine", "room_id": "production", "width_mm": 1200, "depth_mm": 1000, "clearance_mm": 600},
            ]
        )
        equipment_layout = EquipmentLayoutResult(
            (
                EquipmentPlacement("a", "production", Rect(500, 500, 1200, 1000)),
                EquipmentPlacement("b", "production", Rect(1800, 500, 1200, 1000)),
            )
        )

        report = validate_equipment_layout(building, _rooms(), equipment_layout, check_walls=False)

        self.assertFalse(report.ok)
        self.assertIn("clearance_collision", {issue.code for issue in report.issues})

    def test_independent_validation_reports_wall_collision(self):
        building = _building(
            [{"id": "machine", "type": "machine", "room_id": "production", "width_mm": 1000, "depth_mm": 1000}]
        )
        equipment_layout = EquipmentLayoutResult(
            (EquipmentPlacement("machine", "production", Rect(0, 0, 1000, 1000)),)
        )

        report = validate_equipment_layout(building, _rooms(), equipment_layout)

        self.assertFalse(report.ok)
        self.assertIn("equipment_outside_room", {issue.code for issue in report.issues})
        self.assertIn("wall_collision", {issue.code for issue in report.issues})

    def test_generate_building_cli_writes_equipment_solver_output(self):
        raw = _building(
            [{"id": "machine", "type": "machine", "room_id": "production", "width_mm": 1000, "depth_mm": 1000}]
        ).to_dict()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            spec_path = root / "building.json"
            output = root / "output"
            spec_path.write_text(json.dumps(raw), encoding="utf-8")

            code = main(
                [
                    "generate-building",
                    str(spec_path),
                    "--output",
                    str(output),
                    "--time-limit",
                    "5",
                ]
            )

            self.assertEqual(code, 0)
            payload = json.loads((output / "building_01.json").read_text(encoding="utf-8"))
            self.assertEqual(len(payload["equipment"]), 1)
            self.assertEqual(payload["equipment"][0]["equipment_id"], "machine")
            document = ezdxf.readfile(output / "building_01.dxf")
            modelspace = document.modelspace()
            equipment_entities = [entity for entity in modelspace if entity.dxf.layer == "A-EQUIP"]
            clearance_entities = [entity for entity in modelspace if entity.dxf.layer == "A-CLEARANCE"]
            self.assertEqual(len(equipment_entities), 1)
            self.assertEqual(equipment_entities[0].dxftype(), "INSERT")
            self.assertEqual(len(clearance_entities), 1)
            self.assertEqual(clearance_entities[0].dxftype(), "LWPOLYLINE")
            self.assertEqual((output / "building_01.pdf").read_bytes()[:8], b"%PDF-1.3")
            model = ifcopenshell.open(output / "building_01.ifc")
            proxies = model.by_type("IfcBuildingElementProxy")
            self.assertEqual(len(proxies), 1)
            self.assertEqual(proxies[0].Name, "machine")
            psets = {
                definition.RelatingPropertyDefinition.Name
                for definition in proxies[0].IsDefinedBy
                if definition.RelatingPropertyDefinition.is_a("IfcPropertySet")
            }
            self.assertEqual(psets, {"Pset_LayoutEquipment", "Pset_LayoutClearance"})
            manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["variants"][0]["dxf"], "building_01.dxf")
            self.assertEqual(manifest["variants"][0]["pdf"], "building_01.pdf")
            self.assertEqual(manifest["variants"][0]["ifc"], "building_01.ifc")
            self.assertEqual(manifest["variants"][0]["ifc_entities"]["equipment"], 1)


def _building(equipment, *, room_width=6000, room_height=5000, zones=()):
    return BuildingIR.from_mapping(
        {
            "version": "0.2",
            "project_name": "equipment test",
            "layout": {
                "boundary": {"width": room_width + 4000, "height": room_height},
                "entry_room": "production",
                "wall_thickness_mm": 200,
                "rooms": [
                    {
                        "id": "production",
                        "type": "production",
                        "target_area": room_width * room_height / 1_000_000,
                        "min_area": 1,
                        "max_area": 100,
                        "min_width": 1800,
                        "min_depth": 1800,
                        "required_adjacency": ["storage"],
                    },
                    {
                        "id": "storage",
                        "type": "storage",
                        "target_area": 20,
                        "min_area": 1,
                        "max_area": 100,
                        "min_width": 1800,
                        "min_depth": 1800,
                        "required_adjacency": ["production"],
                    },
                ],
            },
            "zones": list(zones),
            "equipment": equipment,
            "flows": [],
        }
    )


def _rooms(*, room_width=6000, room_height=5000):
    return LayoutResult(
        variant=1,
        placements={
            "production": Rect(0, 0, room_width, room_height),
            "storage": Rect(room_width, 0, 4000, room_height),
        },
    )


if __name__ == "__main__":
    unittest.main()

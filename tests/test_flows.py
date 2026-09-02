import tempfile
import unittest
from pathlib import Path

import ezdxf

from layout_configurator.building import BuildingIR
from layout_configurator.equipment import EquipmentLayoutResult, EquipmentPlacement
from layout_configurator.export import export_building_bundle
from layout_configurator.flows import route_flows, validate_flow_routes
from layout_configurator.models import LayoutResult, Rect


class FlowRoutingTests(unittest.TestCase):
    def test_routes_through_generated_door_and_passes_when_width_fits(self):
        building = _building(minimum_width=800)
        layout = _layout()

        routes = route_flows(building, layout)
        report = validate_flow_routes(building, layout, routes)

        self.assertTrue(report.ok, report.issues)
        self.assertEqual(len(routes.routes), 1)
        self.assertEqual(routes.routes[0].room_path, ("source", "target"))
        self.assertEqual(routes.routes[0].points[1], (3000.0, 1500.0))

    def test_reports_opening_that_is_narrower_than_flow(self):
        building = _building(minimum_width=1200)
        report = validate_flow_routes(building, _layout(), route_flows(building, _layout()))

        self.assertIn("opening_too_narrow", {issue.code for issue in report.issues})

    def test_reports_flow_intersection_with_unrelated_equipment_clearance(self):
        building = _building(
            minimum_width=800,
            equipment=[
                {
                    "id": "machine",
                    "type": "machine",
                    "room_id": "source",
                    "width_mm": 500,
                    "depth_mm": 500,
                    "clearance_mm": 500,
                }
            ],
        )
        equipment = EquipmentLayoutResult((EquipmentPlacement("machine", "source", Rect(1800, 1000, 500, 500)),))
        routes = route_flows(building, _layout(), equipment)
        report = validate_flow_routes(building, _layout(), routes, equipment)

        self.assertIn("equipment_clearance_collision", {issue.code for issue in report.issues})

    def test_routes_around_clearance_when_free_room_space_exists(self):
        building = _building(
            minimum_width=800,
            equipment=[
                {
                    "id": "source_machine",
                    "type": "machine",
                    "room_id": "source",
                    "width_mm": 500,
                    "depth_mm": 500,
                    "clearance_mm": 100,
                },
                {
                    "id": "obstacle",
                    "type": "machine",
                    "room_id": "source",
                    "width_mm": 400,
                    "depth_mm": 400,
                    "clearance_mm": 100,
                },
            ],
        )
        payload = building.to_dict()
        payload["flows"][0]["from_ids"] = ["source_machine"]
        building = BuildingIR.from_mapping(payload)
        equipment = EquipmentLayoutResult(
            (
                EquipmentPlacement("source_machine", "source", Rect(700, 1200, 500, 500)),
                EquipmentPlacement("obstacle", "source", Rect(1600, 1000, 400, 400)),
            )
        )
        routes = route_flows(building, _layout(), equipment)
        report = validate_flow_routes(building, _layout(), routes, equipment)

        self.assertNotIn("equipment_clearance_collision", {issue.code for issue in report.issues})
        self.assertGreater(len(routes.routes[0].points), 3)

    def test_exports_flow_route_as_editable_dxf_and_vector_pdf_geometry(self):
        building = _building(minimum_width=800)
        layout = _layout()
        routes = route_flows(building, layout)

        with tempfile.TemporaryDirectory() as directory:
            dxf_path, pdf_path = export_building_bundle(
                directory,
                building,
                layout,
                EquipmentLayoutResult(()),
                flows=routes,
            )
            document = ezdxf.readfile(dxf_path)
            flow_entities = [entity for entity in document.modelspace() if entity.dxf.layer == "A-FLOW"]
            axis_entities = [entity for entity in document.modelspace() if entity.dxf.layer == "A-AXIS"]
            self.assertEqual(len([entity for entity in flow_entities if entity.dxftype() == "LWPOLYLINE"]), 1)
            self.assertEqual(len([entity for entity in flow_entities if entity.dxftype() == "TEXT"]), 1)
            self.assertEqual(len([entity for entity in axis_entities if entity.dxftype() == "LINE"]), 5)
            self.assertEqual(len([entity for entity in axis_entities if entity.dxftype() == "TEXT"]), 5)
            self.assertEqual(Path(pdf_path).read_bytes()[:8], b"%PDF-1.3")


def _building(*, minimum_width, equipment=()):
    return BuildingIR.from_mapping(
        {
            "version": "0.2",
            "project_name": "flow test",
            "layout": {
                "boundary": {"width": 6000, "height": 3000},
                "entry_room": "source",
                "wall_thickness_mm": 200,
                "door_width_mm": 900,
                "rooms": [
                    {
                        "id": "source",
                        "type": "room",
                        "target_area": 9,
                        "min_area": 1,
                        "max_area": 20,
                        "min_width": 1800,
                        "min_depth": 1800,
                        "required_adjacency": ["target"],
                    },
                    {
                        "id": "target",
                        "type": "room",
                        "target_area": 9,
                        "min_area": 1,
                        "max_area": 20,
                        "min_width": 1800,
                        "min_depth": 1800,
                        "required_adjacency": ["source"],
                    },
                ],
            },
            "zones": [],
            "equipment": list(equipment),
            "flows": [
                {
                    "id": "flow",
                    "type": "material",
                    "from_ids": ["source"],
                    "to_ids": ["target"],
                    "minimum_clear_width_mm": minimum_width,
                }
            ],
            "structural_grid": {
                "axes_x_mm": [0, 3000, 6000],
                "axes_y_mm": [0, 3000],
                "labels_x": ["A", "B", "C"],
                "labels_y": ["1", "2"],
            },
        }
    )


def _layout():
    return LayoutResult(
        variant=1,
        placements={
            "source": Rect(0, 0, 3000, 3000),
            "target": Rect(3000, 0, 3000, 3000),
        },
    )


if __name__ == "__main__":
    unittest.main()

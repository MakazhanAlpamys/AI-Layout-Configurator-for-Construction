import hashlib
import importlib.util
import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

import ifcopenshell
import ifcopenshell.validate
from shapely.geometry import Point, Polygon, box

from layout_configurator.building import BuildingIR
from layout_configurator.equipment import EquipmentLayoutResult, EquipmentPlacement
from layout_configurator.flows import FlowRoute, FlowRoutingResult
from layout_configurator.models import LayoutIR, LayoutResult, Rect
from layout_configurator.orthogonal import OrthogonalPolygon


class OrthogonalIFCTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "draft.ifc"

    def api(self):
        self.assertIsNotNone(importlib.util.find_spec("layout_configurator.orthogonal_ifc"),
                             "Exact polygon IFC draft export is missing")
        self.assertIsNotNone(importlib.util.find_spec("layout_configurator.orthogonal_ifc_qa"),
                             "Independent polygon IFC representation readback is missing")
        from layout_configurator import orthogonal_ifc, orthogonal_ifc_qa
        return orthogonal_ifc, orthogonal_ifc_qa

    def test_concave_space_wall_holes_and_actual_openings_survive_strict_ifc4_exchange(self):
        exporter, checker = self.api()
        building, layout, _, _ = _source()
        evidence = exporter.export_orthogonal_draft_ifc(self.path, building, layout)
        model = ifcopenshell.open(self.path)
        self.assertEqual(model.schema, "IFC4")
        space = next(item for item in model.by_type("IfcSpace") if item.Name == "main")
        profile = space.Representation.Representations[0].Items[0].SweptArea
        outline = Polygon([point.Coordinates for point in profile.OuterCurve.Points])
        self.assertEqual(outline.area, 20_000_000)
        self.assertEqual(len(outline.exterior.coords), 7)
        self.assertFalse(outline.covers(Point(4000, 4000)))
        self.assertTrue(model.by_type("IfcArbitraryProfileDefWithVoids"), "Wall material must retain actual room voids")
        self.assertEqual(len(model.by_type("IfcDoor")), 1)
        self.assertEqual(len(model.by_type("IfcOpeningElement")), 1)
        self.assertEqual(len(model.by_type("IfcRelVoidsElement")), 1)
        self.assertEqual(len(model.by_type("IfcRelFillsElement")), 1)
        self.assertEqual(evidence["status"], "DRAFT")
        self.assertIs(evidence["ok"], False)
        self.assertEqual(evidence["full_facility_validation"], {"status": "UNKNOWN", "ok": False})
        self.assertEqual(evidence["engineering_acceptance"], "PENDING")
        self.assertEqual(evidence["source"]["building_sha256"], _hash(building.to_dict()))
        self.assertEqual(evidence["source"]["layout_sha256"], _hash(layout.to_dict()))
        self.assertEqual(evidence["artifact_sha256"], _file_hash(self.path))
        report = checker.validate_orthogonal_ifc(self.path, building, layout, expected_sha256=evidence["artifact_sha256"])
        self.assertEqual(report["status"], "PASS", report)
        self.assertIs(report["ok"], True)
        self.assertEqual(report["full_facility_validation"]["status"], "UNKNOWN")
        self.assertEqual(report["counts"]["rooms"], 2)
        logger = ifcopenshell.validate.json_logger()
        ifcopenshell.validate.validate(model, logger, express_rules=True)
        self.assertEqual(logger.statements, [], logger.statements)

    def test_directional_service_and_full_width_route_are_real_geometry_with_source_identity(self):
        exporter, checker = self.api()
        building, layout, equipment, routing = _source()
        evidence = exporter.export_orthogonal_draft_ifc(self.path, building, layout, equipment, routing=routing)
        report = checker.validate_orthogonal_ifc(self.path, building, layout, equipment, routing=routing,
                                                 expected_sha256=evidence["artifact_sha256"])
        self.assertEqual(report["status"], "PASS", report)
        self.assertEqual(report["counts"]["equipment"], 1)
        self.assertEqual(report["counts"]["services"], 1)
        self.assertEqual(report["counts"]["routes"], 1)
        self.assertEqual(evidence["source"]["equipment_sha256"], _hash(equipment.to_dict(building)))
        self.assertEqual(evidence["source"]["routing_sha256"], _hash(routing.to_dict()))
        model = ifcopenshell.open(self.path)
        route = next(item for item in model.by_type("IfcBuildingElementProxy") if item.ObjectType == "OrthogonalFlowCorridor")
        axis = next(item for item in route.Representation.Representations if item.RepresentationIdentifier == "Axis")
        self.assertEqual(tuple(tuple(point.Coordinates[:2]) for point in axis.Items[0].Points),
                         ((5050, 1410), (1500, 1410), (1500, 4000), (4000, 4000)))
        service = next(item for item in model.by_type("IfcBuildingElementProxy") if item.ObjectType == "OrthogonalEquipmentService")
        polygon = Polygon([point.Coordinates for point in service.Representation.Representations[0].Items[0].SweptArea.OuterCurve.Points])
        self.assertTrue(polygon.equals(box(4700, 300, 5400, 1000)))

    def test_rehashed_concave_bounding_box_and_missing_wall_holes_fail_geometry_readback(self):
        exporter, checker = self.api()
        building, layout, _, _ = _source()
        for mutation in ("bounding_box", "wall_hole"):
            with self.subTest(mutation=mutation):
                exporter.export_orthogonal_draft_ifc(self.path, building, layout)
                model = ifcopenshell.open(self.path)
                if mutation == "bounding_box":
                    main = next(item for item in model.by_type("IfcSpace") if item.Name == "main")
                    profile = main.Representation.Representations[0].Items[0].SweptArea
                    profile.OuterCurve.Points = tuple(model.create_entity("IfcCartesianPoint", Coordinates=point)
                        for point in ((0., 0.), (6000., 0.), (6000., 6000.), (0., 6000.), (0., 0.)))
                else:
                    solid = next(item for item in model.by_type("IfcExtrudedAreaSolid")
                                 if item.SweptArea.is_a("IfcArbitraryProfileDefWithVoids"))
                    solid.SweptArea = model.create_entity("IfcArbitraryClosedProfileDef", ProfileType="AREA",
                                                        OuterCurve=solid.SweptArea.OuterCurve)
                model.write(self.path)
                report = checker.validate_orthogonal_ifc(self.path, building, layout, expected_sha256=_file_hash(self.path))
                self.assertFalse(report["ok"], report)
                self.assertTrue(any("geometry" in issue.lower() for issue in report["issues"]), report)

    def test_rehashed_offsets_missing_geometry_wrong_ids_and_forged_pass_fail(self):
        exporter, checker = self.api()
        building, layout, equipment, routing = _source()
        mutations = ("room_offset", "solid_offset", "rotation", "missing", "room_id", "status", "full_status",
                     "equipment_offset", "service_offset", "route_width", "route_axis", "door_width", "opening_link")
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                exporter.export_orthogonal_draft_ifc(self.path, building, layout, equipment, routing=routing)
                model = ifcopenshell.open(self.path)
                main = next(item for item in model.by_type("IfcSpace") if item.Name == "main")
                if mutation == "room_offset":
                    main.ObjectPlacement.RelativePlacement.Location.Coordinates = (1., 0., 0.)
                elif mutation == "solid_offset":
                    main.Representation.Representations[0].Items[0].Position.Location.Coordinates = (0., 2., 0.)
                elif mutation == "rotation":
                    main.ObjectPlacement.RelativePlacement.RefDirection.DirectionRatios = (0., 1., 0.)
                elif mutation == "missing":
                    main.Representation = None
                elif mutation == "room_id":
                    _property(main, "SourceId").NominalValue = model.create_entity("IfcLabel", "wrong")
                elif mutation in ("status", "full_status"):
                    _property(model.by_type("IfcProject")[0], "Status" if mutation == "status" else "FullFacilityValidation").NominalValue = model.create_entity("IfcLabel", "PASS")
                elif mutation in ("equipment_offset", "service_offset"):
                    kind = "OrthogonalEquipment" if mutation == "equipment_offset" else "OrthogonalEquipmentService"
                    item = next(item for item in model.by_type("IfcBuildingElementProxy") if item.ObjectType == kind)
                    item.ObjectPlacement.RelativePlacement.Location.Coordinates = (5., 0., 0.)
                elif mutation == "route_width":
                    route = next(item for item in model.by_type("IfcBuildingElementProxy") if item.ObjectType == "OrthogonalFlowCorridor")
                    curve = route.Representation.Representations[0].Items[0].SweptArea.OuterCurve
                    old = curve.Points[0].Coordinates
                    curve.Points[0].Coordinates = (old[0] + 40, old[1])
                elif mutation == "route_axis":
                    route = next(item for item in model.by_type("IfcBuildingElementProxy") if item.ObjectType == "OrthogonalFlowCorridor")
                    axis = next(item for item in route.Representation.Representations if item.RepresentationIdentifier == "Axis")
                    axis.Items[0].Points[0].Coordinates = (5050., 1510., 0.)
                elif mutation == "door_width":
                    model.by_type("IfcDoor")[0].OverallWidth = 800.
                elif mutation == "opening_link":
                    model.remove(model.by_type("IfcRelFillsElement")[0])
                model.write(self.path)
                report = checker.validate_orthogonal_ifc(self.path, building, layout, equipment, routing=routing,
                                                         expected_sha256=_file_hash(self.path))
                self.assertFalse(report["ok"], report)
                self.assertEqual(report["status"], "FAIL")

    def test_invalid_optional_equipment_route_or_room_never_creates_destination(self):
        exporter, _ = self.api()
        building, layout, equipment, routing = _source()
        cases = (
            (building, LayoutResult(3, {"main": Rect(0, 0, 6000, 6000), "corner": layout.placements["corner"]}), None, None),
            (building, layout, EquipmentLayoutResult((replace(equipment.placements[0], rect=Rect(3800, 3500, 500, 500)),)), None),
            (building, layout, equipment, FlowRoutingResult((replace(routing.routes[0], points=((5050, 1410), (5050, 4000), (4000, 4000))),))),
        )
        for source, rooms, items, routes in cases:
            with self.subTest(routes=routes is not None, equipment=items is not None):
                with self.assertRaises(ValueError):
                    exporter.export_orthogonal_draft_ifc(self.path, source, rooms, items, routing=routes)
                self.assertFalse(self.path.exists())

    def test_missing_file_changed_source_wrong_hash_and_oversized_input_are_not_pass(self):
        exporter, checker = self.api()
        building, layout, _, _ = _source()
        self.assertFalse(checker.validate_orthogonal_ifc(self.path, building, layout)["ok"])
        exporter.export_orthogonal_draft_ifc(self.path, building, layout)
        self.assertFalse(checker.validate_orthogonal_ifc(self.path, building, layout, expected_sha256="0" * 64)["ok"])
        changed = replace(building, layout=replace(building.layout, project_name="A different source"))
        self.assertFalse(checker.validate_orthogonal_ifc(self.path, changed, layout)["ok"])
        self.assertFalse(checker.validate_orthogonal_ifc(self.path, building, replace(layout, variant=4))["ok"])
        self.assertFalse(checker.validate_orthogonal_ifc(self.path, building, layout, max_file_bytes=100)["ok"])

    def test_numeric_identity_cannot_replace_a_source_boolean_with_an_equal_number(self):
        exporter, checker = self.api()
        building, layout, equipment, routing = _source()
        exporter.export_orthogonal_draft_ifc(self.path, building, layout, equipment, routing=routing)
        model = ifcopenshell.open(self.path)
        machine = next(item for item in model.by_type("IfcBuildingElementProxy") if item.ObjectType == "OrthogonalEquipment")
        _property(machine, "Rotated").NominalValue = model.create_entity("IfcReal", 0.)
        model.write(self.path)
        report = checker.validate_orthogonal_ifc(self.path, building, layout, equipment, routing=routing,
                                                 expected_sha256=_file_hash(self.path))
        self.assertFalse(report["ok"], "IFC numeric zero must not forge a canonical boolean identity")

    def test_rehashed_unique_but_wrong_global_id_fails_source_identity_readback(self):
        exporter, checker = self.api()
        building, layout, _, _ = _source()
        exporter.export_orthogonal_draft_ifc(self.path, building, layout)
        model = ifcopenshell.open(self.path)
        main = next(item for item in model.by_type("IfcSpace") if item.Name == "main")
        main.GlobalId = ifcopenshell.guid.new()
        model.write(self.path)
        report = checker.validate_orthogonal_ifc(self.path, building, layout, expected_sha256=_file_hash(self.path))
        self.assertFalse(report["ok"], "A different unique GlobalId must not replace stable canonical occurrence identity")

    def test_native_ifc_geometry_engine_retains_the_concave_space_and_real_wall_void(self):
        exporter, _ = self.api()
        import ifcopenshell.geom
        from shapely.ops import unary_union
        building, layout, equipment, routing = _source()
        exporter.export_orthogonal_draft_ifc(self.path, building, layout, equipment, routing=routing)
        model = ifcopenshell.open(self.path)
        settings = ifcopenshell.geom.settings()
        settings.set(settings.USE_WORLD_COORDS, True)
        main = next(item for item in model.by_type("IfcSpace") if item.Name == "main")
        wall = model.by_type("IfcWall")[0]
        footprints = []
        for element in (main, wall):
            shape = ifcopenshell.geom.create_shape(settings, element)
            vertices = list(zip(*[iter(shape.geometry.verts)] * 3))
            faces = list(zip(*[iter(shape.geometry.faces)] * 3))
            triangles = [Polygon([(vertices[index][0], vertices[index][1]) for index in face]) for face in faces
                         if all(abs(vertices[index][2]) <= 0.0000001 for index in face)]
            footprints.append(unary_union(triangles))
        self.assertAlmostEqual(footprints[0].area, 20, places=6)
        self.assertFalse(footprints[0].covers(Point(4, 4)))
        self.assertFalse(footprints[1].covers(Point(4, 4)))
        self.assertFalse(footprints[1].covers(Point(2, 4)), "Required door gap must remain an actual IFC material void")
        self.assertTrue(footprints[1].covers(Point(2, 2)))
        logger = ifcopenshell.validate.json_logger()
        ifcopenshell.validate.validate(model, logger, express_rules=True)
        self.assertEqual(logger.statements, [], logger.statements)

    def test_semantic_guids_survive_room_geometry_equipment_routes_and_variant_changes(self):
        exporter, checker = self.api()
        building, layout, equipment, routing = _source()
        first_evidence = exporter.export_orthogonal_draft_ifc(self.path, building, layout, equipment, routing=routing)
        first = ifcopenshell.open(self.path)
        first_roots = {(item.is_a(), item.Name, item.GlobalId) for item in first.by_type("IfcRoot")}
        changed_building = replace(building, layout=replace(building.layout, boundary=replace(building.layout.boundary,
                                   width_mm=6400, height_mm=6400)))
        changed_layout = LayoutResult(4, {"main": layout.placements["main"].translated(200, 200),
                                         "corner": Rect(2200, 2200, 4000, 4000)})
        changed_equipment = EquipmentLayoutResult((replace(equipment.placements[0], rect=Rect(5000, 600, 500, 500)),))
        changed_routes = FlowRoutingResult((replace(routing.routes[0], points=tuple((x + 200, y + 200)
                                                                                  for x, y in routing.routes[0].points)),))
        second_evidence = exporter.export_orthogonal_draft_ifc(self.path, changed_building, changed_layout,
                                                               changed_equipment, routing=changed_routes)
        second = ifcopenshell.open(self.path)
        self.assertEqual({(item.is_a(), item.Name, item.GlobalId) for item in second.by_type("IfcRoot")}, first_roots,
                         "Geometry and variant changes must not rotate stable source object/relation identities")
        self.assertNotEqual(first_evidence["source"], second_evidence["source"])
        self.assertNotEqual(first_evidence["artifact_sha256"], second_evidence["artifact_sha256"])
        self.assertTrue(checker.validate_orthogonal_ifc(self.path, changed_building, changed_layout,
                         changed_equipment, routing=changed_routes)["ok"])

    def test_rotated_asymmetric_service_directions_remain_source_backed_geometry(self):
        exporter, checker = self.api()
        building, layout, equipment, _ = _source()
        building = replace(building, equipment=(replace(building.equipment[0], depth_mm=600,
            clearance_front_mm=150, clearance_back_mm=100, clearance_left_mm=200, clearance_right_mm=50),))
        equipment = EquipmentLayoutResult((replace(equipment.placements[0], rect=Rect(4800, 400, 600, 500), rotated=True),))
        exporter.export_orthogonal_draft_ifc(self.path, building, layout, equipment)
        model = ifcopenshell.open(self.path)
        service = next(item for item in model.by_type("IfcBuildingElementProxy") if item.ObjectType == "OrthogonalEquipmentService")
        polygon = Polygon([point.Coordinates for point in service.Representation.Representations[0].Items[0].SweptArea.OuterCurve.Points])
        self.assertTrue(polygon.equals(box(4650, 200, 5500, 950)),
                        "Local front/back/left/right must rotate to left/right/bottom/top")
        self.assertIs(_property(service, "Rotated").NominalValue.wrappedValue, True)
        self.assertTrue(checker.validate_orthogonal_ifc(self.path, building, layout, equipment)["ok"])

    def test_rehashed_cycles_height_unit_and_representation_tampering_fail(self):
        exporter, checker = self.api()
        building, layout, _, _ = _source()
        for mutation in ("placement_cycle", "height", "units", "duplicate_body", "context", "unknown_geometry"):
            with self.subTest(mutation=mutation):
                exporter.export_orthogonal_draft_ifc(self.path, building, layout)
                model = ifcopenshell.open(self.path)
                main = next(item for item in model.by_type("IfcSpace") if item.Name == "main")
                if mutation == "placement_cycle":
                    main.ObjectPlacement.PlacementRelTo = main.ObjectPlacement
                elif mutation == "height":
                    main.Representation.Representations[0].Items[0].Depth = -1.
                elif mutation == "units":
                    next(unit for unit in model.by_type("IfcSIUnit") if unit.UnitType == "LENGTHUNIT").Prefix = None
                elif mutation == "duplicate_body":
                    main.Representation.Representations = (*main.Representation.Representations, main.Representation.Representations[0])
                elif mutation == "context":
                    model.by_type("IfcGeometricRepresentationContext")[0].WorldCoordinateSystem.Location.Coordinates = (10., 0., 0.)
                elif mutation == "unknown_geometry":
                    model.create_entity("IfcBuildingElementProxy", GlobalId=ifcopenshell.guid.new(), Name="Unexpected element")
                model.write(self.path)
                report = checker.validate_orthogonal_ifc(self.path, building, layout, expected_sha256=_file_hash(self.path))
                self.assertFalse(report["ok"], report)
                self.assertEqual(report["status"], "FAIL")


def _source():
    spec = LayoutIR.from_mapping({"project_name": "Exact orthogonal IFC fixture",
        "boundary": {"width": 6000, "height": 6000}, "entry_room": "main", "door_width": 900,
        "rooms": [{"id": "main", "type": "process", "target_area": 20, "min_area": 20, "max_area": 20,
                   "required_adjacency": ["corner"]},
                  {"id": "corner", "type": "support", "target_area": 16, "min_area": 16, "max_area": 16}]})
    shape = OrthogonalPolygon(((0, 0), (6000, 0), (6000, 2000), (2000, 2000), (2000, 6000), (0, 6000)))
    layout = LayoutResult(3, {"main": shape, "corner": Rect(2000, 2000, 4000, 4000)})
    building = BuildingIR.from_mapping({"layout": spec.to_dict(),
        "equipment": [{"id": "machine", "room_id": "main", "width_mm": 500, "depth_mm": 500,
                       "clearance_front_mm": 100, "clearance_back_mm": 100,
                       "clearance_left_mm": 100, "clearance_right_mm": 100}],
        "flows": [{"id": "process", "from": ["machine"], "to": ["corner"], "minimum_clear_width_mm": 800}]})
    equipment = EquipmentLayoutResult((EquipmentPlacement("machine", "main", Rect(4800, 400, 500, 500)),))
    route = FlowRoute("process", "machine", "corner", "main", "corner", ("main", "corner"),
                      ((5050, 1410), (1500, 1410), (1500, 4000), (4000, 4000)), 800)
    return building, layout, equipment, FlowRoutingResult((route,))


def _property(element, name):
    return next(prop for relation in element.IsDefinedBy if relation.is_a("IfcRelDefinesByProperties")
                for prop in relation.RelatingPropertyDefinition.HasProperties if prop.Name == name)


def _hash(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, allow_nan=False,
        sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def _file_hash(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

"""Facility grouping survives IFC exchange without replacing existing products."""

from dataclasses import replace
from pathlib import Path
import tempfile
import unittest

import ifcopenshell
import ifcopenshell.util.element
import ifcopenshell.validate

from layout_configurator.building import BuildingIR
from layout_configurator.equipment import EquipmentLayoutResult, EquipmentPlacement
from layout_configurator.flows import FlowRoute, FlowRoutingResult, FlowValidationReport
from layout_configurator.ifc import export_building_ifc, export_ifc, export_multifloor_ifc, validate_ifc_roundtrip
from layout_configurator.models import LayoutResult, Rect
from layout_configurator.multifloor import MultiFloorResult, MultiFloorSpec, MultiFloorValidationReport


def _facility():
    building = BuildingIR.from_mapping(
        {
            "version": "0.2",
            "layout": {
                "project_name": "Facility group exchange",
                "boundary": {"width": 9000, "height": 6000},
                "grid": 100,
                "rooms": [
                    {"id": "source", "area": 9, "min_area": 9, "max_area": 9},
                    {"id": "process", "area": 9, "min_area": 9, "max_area": 9},
                    {"id": "destination", "area": 9, "min_area": 9, "max_area": 9},
                ],
            },
            "zones": [
                {"id": "support", "type": "support", "room_ids": ["source"]},
                {"id": "production", "type": "clean_production", "room_ids": ["process"],
                 "cleanroom_class": "ISO 7", "pressure_pa": 20},
                {"id": "packaging", "type": "material_airlock", "room_ids": ["destination"],
                 "parent_zone_id": "production", "airlock": True},
                {"id": "empty", "type": "buffer", "room_ids": []},
            ],
            "equipment": [
                {"id": "mixer", "type": "machine", "room_id": "process", "width_mm": 500, "depth_mm": 500},
                {"id": "sealer", "type": "machine", "room_id": "destination", "width_mm": 500, "depth_mm": 500},
                {"id": "tool", "type": "bench", "room_id": "source", "width_mm": 500, "depth_mm": 500},
            ],
            "flows": [
                {"id": "material_transfer", "type": "material", "from_ids": ["mixer"],
                 "to_ids": ["sealer", "source"], "stage": "packing", "minimum_clear_width_mm": 1200},
                {"id": "people_transfer", "type": "people", "from_ids": ["source"], "to_ids": ["process"]},
                {"id": "optional_service", "type": "service", "from_ids": ["process"],
                 "to_ids": ["destination"], "required": False},
            ],
        }
    )
    result = LayoutResult(
        1, {"source": Rect(0, 0, 3000, 3000), "process": Rect(3000, 0, 3000, 3000),
            "destination": Rect(6000, 0, 3000, 3000)}
    )
    equipment = EquipmentLayoutResult(
        (EquipmentPlacement("mixer", "process", Rect(3500, 500, 500, 500)),
         EquipmentPlacement("sealer", "destination", Rect(6500, 500, 500, 500)),
         EquipmentPlacement("tool", "source", Rect(500, 500, 500, 500)))
    )
    routes = FlowRoutingResult(
        (FlowRoute("material_transfer", "mixer", "sealer", "process", "destination",
                   ("process", "destination"), ((3750, 1000), (6750, 1000)), 1200),
         FlowRoute("material_transfer", "mixer", "source", "process", "source",
                   ("process", "source"), ((3750, 1000), (1500, 1500)), 1200),
         FlowRoute("people_transfer", "source", "process", "source", "process",
                   ("source", "process"), ((1500, 1500), (4500, 1500)), 1200))
    )
    return building, result, equipment, routes


def _members(group):
    return [member for relation in group.IsGroupedBy for member in relation.RelatedObjects]


class IfcFacilityGroupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.building, self.result, self.equipment, self.routes = _facility()

    def _export(self, name="facility.ifc", **changes):
        values = dict(building=self.building, result=self.result, equipment=self.equipment,
                      flows=self.routes, flow_report=FlowValidationReport(()))
        values.update(changes)
        path = self.root / name
        summary = export_building_ifc(path, **values)
        return ifcopenshell.open(path), summary

    def test_zones_preserve_direct_space_membership_and_nested_zone_hierarchy(self):
        # Omitting group assignments, flattening descendants, or grouping every
        # room would make a reviewer see the wrong functional zone membership.
        model, _ = self._export()
        zones = {zone.Name: zone for zone in model.by_type("IfcZone")}
        self.assertEqual(set(zones), {"support", "production", "packaging", "empty"})
        self.assertEqual({(member.is_a(), member.Name) for member in _members(zones["production"])},
                         {("IfcSpace", "process"), ("IfcZone", "packaging")})
        self.assertEqual({member.Name for member in _members(zones["packaging"])}, {"destination"})
        self.assertEqual({member.Name for member in _members(zones["support"])}, {"source"})
        self.assertEqual(_members(zones["empty"]), [])

    def test_zone_properties_distinguish_declared_metadata_and_source_contract(self):
        # Losing direct-membership or declaration provenance changes the meaning
        # of the received IFC, even when its coloured geometry looks unchanged.
        model, _ = self._export()
        zones = {zone.Name: zone for zone in model.by_type("IfcZone")}
        self.assertIn("production", zones)
        properties = ifcopenshell.util.element.get_psets(zones["production"])["Pset_LayoutZone"]
        for key, value in {"ZoneId": "production", "ZoneType": "clean_production",
                           "DirectRoomIds": "process", "CleanroomClass": "ISO 7",
                           "PressurePa": 20, "SourceContract": "BuildingIR 0.2",
                           "SourceEntityType": "ZoneSpec"}.items():
            self.assertEqual(properties[key], value)
        child = ifcopenshell.util.element.get_psets(zones["packaging"])["Pset_LayoutZone"]
        self.assertEqual(child["ParentZoneId"], "production")
        self.assertTrue(child["Airlock"])
        self.assertNotIn("PressurePa", child)

    def test_flow_system_groups_all_its_routes_and_only_declared_endpoint_equipment(self):
        # Joining by route index, including every machine in an endpoint room,
        # or keeping only one route loses the declared process system contract.
        model, _ = self._export()
        systems = {system.Name: system for system in model.by_type("IfcSystem", include_subtypes=False)}
        self.assertEqual(set(systems), {"material_transfer", "people_transfer", "optional_service"})
        members = _members(systems["material_transfer"])
        self.assertEqual({member.Name for member in members},
                         {"mixer", "sealer", "Flow material_transfer: mixer -> sealer",
                          "Flow material_transfer: mixer -> source"})
        self.assertEqual(len(members), 4)
        self.assertEqual({member.Name for member in _members(systems["people_transfer"])},
                         {"Flow people_transfer: source -> process"})
        properties = ifcopenshell.util.element.get_psets(systems["material_transfer"])["Pset_LayoutFlowSystem"]
        for key, value in {"FlowId": "material_transfer", "FlowType": "material",
                           "FromIds": "mixer", "ToIds": "sealer,source", "Stage": "packing",
                           "SourceContract": "BuildingIR 0.2", "SourceEntityType": "FlowSpec",
                           "Required": True, "MinimumClearWidthMm": 1200}.items():
            self.assertEqual(properties[key], value)
        self.assertIn("not an MEP", properties["RepresentationKind"])

    def test_unrouted_declaration_survives_without_an_invalid_empty_assignment(self):
        # A missing optional route must not silently remove its declaration or
        # fabricate a route/physical system, and empty IFC SETs are invalid.
        model, _ = self._export()
        systems = {system.Name: system for system in model.by_type("IfcSystem", include_subtypes=False)}
        self.assertIn("optional_service", systems)
        system = systems["optional_service"]
        self.assertEqual(tuple(system.IsGroupedBy), ())
        properties = ifcopenshell.util.element.get_psets(system)["Pset_LayoutFlowSystem"]
        self.assertFalse(properties["Required"])
        self.assertEqual(properties["FromIds"], "process")
        self.assertEqual(properties["ToIds"], "destination")

    def test_group_ids_and_members_survive_program_reordering_and_geometry_revision(self):
        # Geometry-derived or list-position IDs break review links across variants.
        first, _ = self._export()
        building = replace(self.building, zones=tuple(reversed(self.building.zones)),
                           flows=tuple(reversed(self.building.flows)))
        result = replace(self.result, variant=2, placements={
            key: replace(rect, y=rect.y + 100) for key, rect in self.result.placements.items()})
        equipment = replace(self.equipment, placements=tuple(
            replace(item, rect=replace(item.rect, y=item.rect.y + 100)) for item in self.equipment.placements))
        routes = replace(self.routes, routes=tuple(
            replace(route, points=tuple((x, y + 100) for x, y in route.points)) for route in self.routes.routes))
        second, _ = self._export("revision.ifc", building=building, result=result,
                                 equipment=equipment, flows=routes)
        self.assertEqual(len(first.by_type("IfcZone")), 4)
        for entity_type in ("IfcZone", "IfcSystem", "IfcRelAssignsToGroup"):
            self.assertEqual({entity.GlobalId for entity in first.by_type(entity_type)},
                             {entity.GlobalId for entity in second.by_type(entity_type)})
        for name in ("production", "material_transfer"):
            groups = [next(entity for entity in model.by_type("IfcGroup") if entity.Name == name)
                      for model in (first, second)]
            self.assertEqual({member.GlobalId for member in _members(groups[0])},
                             {member.GlobalId for member in _members(groups[1])})

    def test_existing_product_guids_and_exchange_readback_are_preserved(self):
        # Adding semantic groups must not replace products or break the IDs used
        # by BCF viewpoints and the existing QA reader.
        grouped, summary = self._export()
        self.assertEqual(len(grouped.by_type("IfcZone")), 4)
        legacy_path = self.root / "legacy.ifc"
        export_ifc(legacy_path, self.building.layout, self.result, self.equipment,
                   self.building.equipment, self.routes, FlowValidationReport(()),
                   {flow.id: flow.type for flow in self.building.flows})
        legacy = ifcopenshell.open(legacy_path)
        self.assertFalse(legacy.by_type("IfcZone"))
        self.assertFalse(legacy.by_type("IfcSystem"))
        self.assertEqual({(product.is_a(), product.Name, product.GlobalId) for product in grouped.by_type("IfcProduct")},
                         {(product.is_a(), product.Name, product.GlobalId) for product in legacy.by_type("IfcProduct")})
        readback = validate_ifc_roundtrip(summary.path, summary,
                                         expected_flow_ids=("material_transfer", "material_transfer", "people_transfer"))
        self.assertTrue(readback.ok, readback.issues)
        self.assertEqual(readback.summary.equipment_ids, ("mixer", "sealer", "tool"))
        self.assertEqual(readback.summary.flow_ids, ("material_transfer", "material_transfer", "people_transfer"))

    def test_exported_facility_groups_pass_ifc_schema_validation(self):
        # Incorrect relation types/cardinality, missing required attributes or
        # duplicate GUIDs can make apparently usable files fail BIM import.
        model, _ = self._export()
        self.assertEqual(len(model.by_type("IfcZone")), 4)
        self.assertEqual(len(model.by_type("IfcSystem", include_subtypes=False)), 3)
        logger = ifcopenshell.validate.json_logger()
        ifcopenshell.validate.validate(model, logger)
        self.assertEqual(logger.statements, [])

    def test_full_facility_export_passes_express_geometry_and_spatial_rules(self):
        # A receiving BIM tool must get two-dimensional swept profiles, spaces
        # aggregated by their storey, and valid modification history. Attribute
        # validation alone does not catch these three exporter mistakes.
        model, _ = self._export()
        logger = ifcopenshell.validate.json_logger()
        ifcopenshell.validate.validate(model, logger, express_rules=True)
        violations = [(item.get("attribute"), item["instance"].is_a())
                      for item in logger.statements]
        self.assertEqual(violations, [])

    def test_multifloor_spaces_have_valid_storey_decomposition_after_exchange(self):
        # The multi-storey exporter has its own containment path: a single-floor
        # fix cannot protect spaces on other floors from being orphaned.
        layout = self.building.layout.to_dict()
        spec = MultiFloorSpec.from_mapping({
            "project_name": "Multi-floor spatial exchange",
            "floors": [{"level": 0, "layout": layout}, {"level": 1, "layout": layout}],
            "vertical_cores": [{"id": "stair", "room_ids": ["source", "source"]}],
        })
        result = MultiFloorResult(spec, (self.result, self.result), MultiFloorValidationReport(()))
        path = self.root / "floors.ifc"
        export_multifloor_ifc(path, spec, result)
        model = ifcopenshell.open(path)
        spaces = model.by_type("IfcSpace")
        self.assertEqual(len(spaces), 6)
        self.assertEqual({space.Decomposes[0].RelatingObject.Name for space in spaces if space.Decomposes},
                         {"Level 0", "Level 1"})
        self.assertTrue(all(
            not member.is_a("IfcSpace")
            for relation in model.by_type("IfcRelContainedInSpatialStructure")
            for member in relation.RelatedElements
        ))
        logger = ifcopenshell.validate.json_logger()
        ifcopenshell.validate.validate(model, logger, express_rules=True)
        self.assertEqual([(item.get("attribute"), item["instance"].is_a())
                          for item in logger.statements], [])


if __name__ == "__main__":
    unittest.main()

"""Declared native zone/system membership and independent IFC graph readback."""

from dataclasses import replace
import tempfile
import unittest
from pathlib import Path

import ifcopenshell
import ifcopenshell.validate

from layout_configurator.building import BuildingIR, StructuralGridSpec, ZoneSpec
from layout_configurator.equipment import EquipmentLayoutResult
from layout_configurator.flows import FlowRoutingResult
from layout_configurator.models import LayoutResult, Rect, WindowSpec
from layout_configurator.orthogonal_ifc import export_orthogonal_draft_ifc
from layout_configurator.orthogonal_ifc_qa import validate_orthogonal_ifc
from test_orthogonal_ifc import _file_hash, _hash, _property, _source


def _group_source():
    building, layout, equipment, routing = _source()
    building = replace(building, zones=(
        ZoneSpec("production", "functional", ("main",), cleanroom_class="ISO 8", pressure_pa=15),
        ZoneSpec("packaging", "functional", ("corner",), parent_zone_id="production", airlock=True),
        ZoneSpec("buffer", "functional", (), parent_zone_id="production")),
        flows=(replace(building.flows[0], id="production", type="material", to_ids=("packaging",), stage="packing"),))
    routing = replace(routing, routes=(replace(routing.routes[0], flow_id="production", to_id="packaging"),))
    return BuildingIR.from_mapping(building.to_dict()), layout, equipment, routing


def _members(group):
    return [(member.is_a(), _property(member, "Kind").NominalValue.wrappedValue, member.Name)
            for relation in group.IsGroupedBy for member in relation.RelatedObjects]


class OrthogonalIFCGroupTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name) / "groups.ifc"

    def export(self, building, layout, equipment=None, routing=None, **options):
        return export_orthogonal_draft_ifc(self.path, building, layout, equipment,
            routing=routing, include_groups=True, **options)

    def check(self, building, layout, equipment=None, routing=None, **options):
        return validate_orthogonal_ifc(self.path, building, layout, equipment,
            routing=routing, include_groups=True, expected_sha256=_file_hash(self.path), **options)

    def test_native_direct_zone_hierarchy_and_exact_process_members_are_not_physical_networks(self):
        building, layout, equipment, routing = _group_source()
        evidence = self.export(building, layout, equipment, routing)
        model = ifcopenshell.open(self.path)
        zones = {zone.Name: zone for zone in model.by_type("IfcZone")}
        systems = model.by_type("IfcSystem", include_subtypes=False)
        self.assertEqual(set(zones), {"production", "packaging", "buffer"})
        self.assertEqual(set(_members(zones["production"])),
            {("IfcSpace", "room", "main"), ("IfcZone", "zone", "packaging"), ("IfcZone", "zone", "buffer")})
        self.assertEqual(set(_members(zones["packaging"])), {("IfcSpace", "room", "corner")})
        self.assertEqual(_members(zones["buffer"]), [])
        self.assertEqual(tuple(zones["buffer"].IsGroupedBy), ())
        self.assertEqual(len(systems), 1)
        self.assertEqual(systems[0].Name, "production")
        self.assertEqual(systems[0].ObjectType, "material")
        self.assertEqual(set(_members(systems[0])), {
            ("IfcBuildingElementProxy", "equipment", "machine"),
            ("IfcBuildingElementProxy", "route", '["production","machine","packaging","main","corner"]')})
        self.assertNotEqual(systems[0].GlobalId, zones["production"].GlobalId)
        self.assertFalse(model.by_type("IfcDistributionSystem"))
        self.assertFalse(model.by_type("IfcDistributionPort"))
        self.assertFalse(model.by_type("IfcSpatialZone"))
        self.assertEqual(evidence["counts"]["zones"], 3)
        self.assertEqual(evidence["counts"]["process_systems"], 1)
        self.assertEqual(evidence["counts"]["group_relations"], 3)
        report = self.check(building, layout, equipment, routing)
        self.assertEqual(report["status"], "PASS", report)
        self.assertEqual(report["full_facility_validation"], {"status": "UNKNOWN", "ok": False})
        self.assertEqual(report["engineering_acceptance"], "PENDING")
        logger = ifcopenshell.validate.json_logger()
        ifcopenshell.validate.validate(model, logger, express_rules=True)
        self.assertEqual(logger.statements, [], logger.statements)

    def test_omitted_and_false_default_ifc_data_evidence_and_readback_are_exact(self):
        building, layout, equipment, routing = _group_source()
        first = export_orthogonal_draft_ifc(self.path, building, layout, equipment, routing=routing)
        first_data = self.path.read_text(encoding="utf-8").split("DATA;", 1)[1]
        first_report = validate_orthogonal_ifc(self.path, building, layout, equipment, routing=routing)
        self.assertEqual(first_report["artifact_sha256"], _file_hash(self.path))
        self.assertEqual(first_report, validate_orthogonal_ifc(self.path, building, layout, equipment,
            routing=routing, include_groups=False))
        second = export_orthogonal_draft_ifc(self.path, building, layout, equipment,
            routing=routing, include_groups=False)
        self.assertEqual(first_data, self.path.read_text(encoding="utf-8").split("DATA;", 1)[1])
        self.assertEqual({key: value for key, value in first.items() if key != "artifact_sha256"},
                         {key: value for key, value in second.items() if key != "artifact_sha256"})
        second_report = validate_orthogonal_ifc(self.path, building, layout, equipment,
            routing=routing, include_groups=False)
        self.assertEqual(second_report["artifact_sha256"], _file_hash(self.path))
        # Each physical export has its own volatile STEP header timestamp.
        self.assertEqual({key: value for key, value in first_report.items() if key != "artifact_sha256"},
                         {key: value for key, value in second_report.items() if key != "artifact_sha256"})
        self.assertNotIn("group_capability", second)
        self.assertNotIn("zones", second["counts"])
        self.assertFalse(ifcopenshell.open(self.path).by_type("IfcGroup"))

    def test_strict_flag_and_empty_native_declarations_preserve_omitted_component_scope(self):
        building, layout, _, _ = _group_source()
        for mode in (None, 0, 1, "true", []):
            with self.subTest(mode=mode):
                self.path.write_bytes(b"Existing destination")
                with self.assertRaisesRegex(ValueError, "include_groups"):
                    export_orthogonal_draft_ifc(self.path, building, layout, include_groups=mode)
                self.assertEqual(self.path.read_bytes(), b"Existing destination")
                report = validate_orthogonal_ifc(self.path, building, layout, include_groups=mode)
                self.assertEqual(report["status"], "FAIL")
                self.assertIn("include_groups", " ".join(report["issues"]))
        evidence = self.export(building, layout)
        model = ifcopenshell.open(self.path)
        system = model.by_type("IfcSystem", include_subtypes=False)[0]
        self.assertEqual(tuple(system.IsGroupedBy), ())
        self.assertEqual(evidence["local_validation"]["equipment"]["status"], "NOT_SUPPLIED")
        self.assertEqual(evidence["local_validation"]["routes"]["status"], "NOT_SUPPLIED")
        declaration = next(group for group in evidence["group_capability"]["groups"] if group["kind"] == "flow-system")
        self.assertEqual(declaration["unprojected_equipment_ids"], ["machine"])
        self.assertEqual(self.check(building, layout)["status"], "PASS")

    def test_rehashed_actual_membership_flattening_duplicates_missing_and_foreign_elements_fail(self):
        building, layout, equipment, routing = _group_source()
        for mode in ("flatten", "missing_child", "missing_empty", "foreign_service", "foreign_room",
                     "duplicate_relation", "duplicate_members", "missing_relation", "wrong_group",
                     "wrong_unique_guid", "missing_system", "wrong_group_kind"):
            with self.subTest(mode=mode):
                self.export(building, layout, equipment, routing)
                model = ifcopenshell.open(self.path)
                zones = {zone.Name: zone for zone in model.by_type("IfcZone")}
                system = model.by_type("IfcSystem", include_subtypes=False)[0]
                relation = zones["production"].IsGroupedBy[0]
                if mode == "flatten":
                    relation.RelatedObjects = tuple(member for member in relation.RelatedObjects
                        if member != zones["packaging"]) + (next(room for room in model.by_type("IfcSpace")
                                                                if room.Name == "corner"),)
                elif mode == "missing_child":
                    relation.RelatedObjects = tuple(member for member in relation.RelatedObjects if member != zones["packaging"])
                elif mode == "missing_empty":
                    model.remove(zones["buffer"])
                elif mode in ("foreign_service", "foreign_room"):
                    added = next(member for member in model.by_type("IfcProduct")
                        if member.Name == ("machine" if mode == "foreign_service" else "corner")
                        and _property(member, "Kind").NominalValue.wrappedValue == ("service" if mode == "foreign_service" else "room"))
                    link = system.IsGroupedBy[0]
                    link.RelatedObjects = (*link.RelatedObjects, added)
                elif mode == "duplicate_relation":
                    model.create_entity("IfcRelAssignsToGroup", GlobalId=ifcopenshell.guid.new(),
                        RelatedObjects=relation.RelatedObjects, RelatingGroup=relation.RelatingGroup)
                elif mode == "duplicate_members":
                    relation.RelatedObjects = (*relation.RelatedObjects, relation.RelatedObjects[0])
                elif mode == "missing_relation":
                    model.remove(relation)
                elif mode == "wrong_group":
                    relation.RelatingGroup = system
                elif mode == "wrong_unique_guid":
                    zones["production"].GlobalId = ifcopenshell.guid.new()
                elif mode == "missing_system":
                    model.remove(system)
                elif mode == "wrong_group_kind":
                    _property(system, "Kind").NominalValue = model.create_entity("IfcLabel", "zone")
                model.write(self.path)
                self.assertEqual(self.check(building, layout, equipment, routing)["status"], "FAIL")

    def test_rehashed_declared_properties_direction_class_pressure_and_scope_promotions_fail(self):
        building, layout, equipment, routing = _group_source()
        for mode in ("from_ids", "to_ids", "stage", "required", "width", "class", "pressure", "airlock",
                     "declaration_json", "json_type", "physical_scope", "network_scope", "zone_description",
                     "system_description", "group_hash", "status", "full_status", "engineering"):
            with self.subTest(mode=mode):
                self.export(building, layout, equipment, routing)
                model = ifcopenshell.open(self.path)
                zone = next(zone for zone in model.by_type("IfcZone") if zone.Name == "production")
                system = model.by_type("IfcSystem", include_subtypes=False)[0]
                if mode in ("zone_description", "system_description"):
                    (zone if mode == "zone_description" else system).Description = "Engineering acceptance: PASS; qualified physical network"
                else:
                    element, field, value = {
                        "from_ids": (system, "FromIdsJSON", '["packaging"]'),
                        "to_ids": (system, "ToIdsJSON", '["machine"]'),
                        "stage": (system, "Stage", "wrong"),
                        "required": (system, "Required", False),
                        "width": (system, "MinimumClearWidthMM", 801.),
                        "class": (zone, "CleanroomClass", "ISO 5"),
                        "pressure": (zone, "PressurePa", 16.),
                        "airlock": (zone, "Airlock", True),
                        "declaration_json": (zone, "DeclarationJSON", '{}'),
                        "json_type": (zone, "DirectRoomIdsJSON", '["main"]'),
                        "physical_scope": (zone, "PhysicalContainerValidation", "PASS"),
                        "network_scope": (system, "NetworkEngineering", "PASS"),
                        "group_hash": (model.by_type("IfcProject")[0], "GroupCapabilitySHA256", "PASS"),
                        "status": (model.by_type("IfcProject")[0], "Status", "PASS"),
                        "full_status": (model.by_type("IfcProject")[0], "FullFacilityValidation", "PASS"),
                        "engineering": (model.by_type("IfcProject")[0], "EngineeringAcceptance", "PASS"),
                    }[mode]
                    nominal_type = "IfcLabel" if mode == "json_type" else "IfcText" if field.endswith("JSON") else "IfcBoolean" if isinstance(value, bool) else "IfcReal" if isinstance(value, (int, float)) else "IfcLabel"
                    _property(element, field).NominalValue = model.create_entity(nominal_type, value)
                model.write(self.path)
                self.assertEqual(self.check(building, layout, equipment, routing)["status"], "FAIL")

    def test_fresh_common_capability_original_integer_pressure_and_complete_native_readback_bind_source(self):
        from layout_configurator.orthogonal_groups import validate_orthogonal_groups
        building, layout, equipment, routing = _group_source()
        building = replace(building, zones=(replace(building.zones[0], pressure_pa=15), *building.zones[1:]))
        before = building.to_dict(), layout.to_dict()
        capability = validate_orthogonal_groups(building, layout, equipment, routing=routing).to_dict()
        evidence = self.export(building, layout, equipment, routing)
        self.assertEqual(evidence["source"]["building_sha256"], _hash(before[0]))
        self.assertEqual(evidence["source"], capability["source"])
        self.assertEqual(evidence["group_capability"], capability)
        self.assertEqual(evidence["local_validation"]["group_capability"], capability)
        report = self.check(building, layout, equipment, routing)
        self.assertEqual(report["status"], "PASS", report)
        self.assertEqual(report["group_capability"], capability)
        self.assertEqual(report["source"], evidence["source"])
        self.assertEqual([{"kind": group["kind"], "id": group["id"], "members": group["members"]}
                          for group in report["groups"]],
                         [{key: group[key] for key in ("kind", "id", "members")} for group in capability["groups"]])
        self.assertEqual((building.to_dict(), layout.to_dict()), before)

    def test_rehashed_extra_physical_scope_native_links_optional_text_and_property_units_fail(self):
        building, layout, equipment, routing = _group_source()
        for mode in ("zone_long_name", "assignment_description", "assignment_type", "served_building",
                     "decomposed_space", "nested_space", "pressure_unit"):
            with self.subTest(mode=mode):
                self.export(building, layout, equipment, routing)
                model = ifcopenshell.open(self.path)
                zone = next(zone for zone in model.by_type("IfcZone") if zone.Name == "production")
                system = model.by_type("IfcSystem", include_subtypes=False)[0]
                if mode == "zone_long_name":
                    zone.LongName = "Qualified physical cleanroom; engineering acceptance PASS"
                elif mode == "assignment_description":
                    zone.IsGroupedBy[0].Description = "Physical engineering acceptance PASS"
                elif mode == "assignment_type":
                    zone.IsGroupedBy[0].RelatedObjectsType = "PRODUCT"
                elif mode == "served_building":
                    model.create_entity("IfcRelServicesBuildings", GlobalId=ifcopenshell.guid.new(),
                        RelatingSystem=system, RelatedBuildings=model.by_type("IfcBuilding"))
                elif mode in ("decomposed_space", "nested_space"):
                    model.create_entity("IfcRelAggregates" if mode == "decomposed_space" else "IfcRelNests",
                        GlobalId=ifcopenshell.guid.new(), RelatingObject=zone,
                        RelatedObjects=[next(room for room in model.by_type("IfcSpace") if room.Name == "corner")])
                elif mode == "pressure_unit":
                    _property(zone, "PressurePa").Unit = model.create_entity("IfcSIUnit",
                        UnitType="PRESSUREUNIT", Prefix="KILO", Name="PASCAL")
                model.write(self.path)
                report = self.check(building, layout, equipment, routing)
                self.assertEqual(report["status"], "FAIL", report)

    def test_original_typed_malformed_group_source_is_rejected_before_normalization_and_write(self):
        from layout_configurator.orthogonal_groups import OrthogonalGroupsCapabilityError
        building, layout, equipment, routing = _group_source()
        self.export(building, layout, equipment, routing)
        held = self.path.read_bytes()
        for mode in ("airlock", "required", "huge_pressure", "nan_pressure", "list_id"):
            with self.subTest(mode=mode):
                bad = (replace(building, flows=(replace(building.flows[0], required="false"),))
                    if mode == "required" else replace(building, zones=(replace(building.zones[0], **{
                        "airlock": {"airlock": "false"}, "huge_pressure": {"pressure_pa": 10 ** 400},
                        "nan_pressure": {"pressure_pa": float("nan")}, "list_id": {"id": ["production"]},
                    }[mode]), *building.zones[1:])))
                with self.assertRaises(OrthogonalGroupsCapabilityError) as caught:
                    self.export(bad, layout, equipment, routing)
                self.assertFalse(caught.exception.report.ok)
                self.assertFalse(caught.exception.report.to_dict()["proven_building_infeasible"])
                self.assertEqual(self.path.read_bytes(), held)
                self.assertEqual(self.check(bad, layout, equipment, routing)["status"], "FAIL")

    def test_scoped_semantic_pass_never_bypasses_actual_equipment_or_route_geometry_gates(self):
        from layout_configurator.orthogonal_groups import validate_orthogonal_groups
        building, layout, equipment, routing = _group_source()
        self.export(building, layout, equipment, routing)
        held = self.path.read_bytes()
        for mode in ("equipment", "route"):
            with self.subTest(mode=mode):
                bad_equipment = (replace(equipment, placements=(replace(equipment.placements[0],
                    rect=Rect(6200, 400, 500, 500)),)) if mode == "equipment" else equipment)
                bad_routes = (replace(routing, routes=(replace(routing.routes[0],
                    points=((6200, 1410), *routing.routes[0].points[1:])),)) if mode == "route" else routing)
                semantic = validate_orthogonal_groups(building, layout, bad_equipment, routing=bad_routes)
                self.assertEqual(semantic.status, "PASS", semantic.to_dict())
                self.assertEqual(semantic.to_dict()["policy"]["geometry_validation"], "NOT_EVALUATED")
                with self.assertRaisesRegex(ValueError, "local validation"):
                    self.export(building, layout, bad_equipment, bad_routes)
                self.assertEqual(self.path.read_bytes(), held)
                self.assertEqual(self.check(building, layout, bad_equipment, bad_routes)["status"], "FAIL")

    def test_group_and_existing_product_guids_survive_source_declaration_geometry_and_variant_changes(self):
        building, layout, equipment, routing = _group_source()
        first = self.export(building, layout, equipment, routing)
        model = ifcopenshell.open(self.path)
        roots = {(item.is_a(), item.Name, item.GlobalId) for item in model.by_type("IfcRoot")}
        changed_building = replace(building, layout=replace(building.layout,
            boundary=replace(building.layout.boundary, width_mm=6400, height_mm=6400)),
            zones=tuple(reversed((replace(building.zones[0], pressure_pa=20), *building.zones[1:]))),
            flows=(replace(building.flows[0], stage="changed declared stage"),))
        changed_layout = LayoutResult(4, {"main": layout.placements["main"].translated(200, 200),
            "corner": Rect(2200, 2200, 4000, 4000)})
        changed_equipment = EquipmentLayoutResult((replace(equipment.placements[0], rect=Rect(5000, 600, 500, 500)),))
        changed_routes = FlowRoutingResult((replace(routing.routes[0],
            points=tuple((x + 200, y + 200) for x, y in routing.routes[0].points)),))
        second = self.export(changed_building, changed_layout, changed_equipment, changed_routes)
        model = ifcopenshell.open(self.path)
        self.assertEqual({(item.is_a(), item.Name, item.GlobalId)
            for item in model.by_type("IfcRoot")}, roots)
        self.assertNotEqual(first["source"], second["source"])
        self.assertEqual(self.check(changed_building, changed_layout, changed_equipment, changed_routes)["status"], "PASS")

    def test_independent_explicit_group_window_grid_flags_preserve_exact_source_and_material(self):
        from layout_configurator.orthogonal_groups import validate_orthogonal_groups
        from layout_configurator.orthogonal_grid import validate_orthogonal_grid
        from layout_configurator.orthogonal_window_capability import validate_orthogonal_window_capability
        building, layout, equipment, routing = _group_source()
        building = replace(building, layout=replace(building.layout,
            windows=(WindowSpec("source-window", "main", "bottom", 1000, 800),),
            window_sill_mm=900, window_height_mm=1500),
            structural_grid=StructuralGridSpec((1000, 2050.5, 5000), (1000, 5000),
                                              (" A ", "B", "C"), ("1", "2")))
        self.path.write_bytes(b"Held source proof")
        with self.assertRaises(ValueError):
            self.export(building, layout, equipment, routing)
        self.assertEqual(self.path.read_bytes(), b"Held source proof")
        index = self.export(building, layout, equipment, routing, include_windows=True, include_grid=True)
        self.assertEqual(index["source"]["building_sha256"], _hash(building.to_dict()))
        self.assertEqual(index["group_capability"], validate_orthogonal_groups(building, layout, equipment, routing=routing).to_dict())
        self.assertEqual(index["grid_capability"], validate_orthogonal_grid(building.layout, building.structural_grid).to_dict())
        self.assertEqual(index["window_capability"], validate_orthogonal_window_capability(building.layout, layout).to_dict())
        report = self.check(building, layout, equipment, routing, include_windows=True, include_grid=True)
        self.assertEqual(report["status"], "PASS", report)
        self.assertEqual((report["counts"]["zones"], report["counts"]["process_systems"],
            report["counts"]["grid_axes"], report["counts"]["windows"]), (3, 1, 5, 1))
        self.assertEqual(report["grid_axes"][0]["label"], " A ")
        self.assertEqual(report["window_material"][0]["z_intervals_mm"], [[0, 900], [900, 2400], [2400, 2800]])

    def test_omitted_routes_supplied_equipment_and_absent_declarations_have_exact_native_inventory(self):
        building, layout, equipment, _ = _group_source()
        index = self.export(building, layout, equipment)
        model = ifcopenshell.open(self.path)
        system = model.by_type("IfcSystem", include_subtypes=False)[0]
        self.assertEqual(_members(system), [("IfcBuildingElementProxy", "equipment", "machine")])
        self.assertEqual(index["group_capability"]["states"], {"equipment": "SUPPLIED", "routing": "NOT_SUPPLIED"})
        self.assertEqual(self.check(building, layout, equipment)["status"], "PASS")
        empty_source = replace(building, zones=(), flows=())
        index = self.export(empty_source, layout)
        self.assertFalse(ifcopenshell.open(self.path).by_type("IfcGroup"))
        self.assertEqual(index["group_capability"]["groups"], [])
        self.assertEqual(index["counts"]["group_relations"], 0)
        self.assertEqual(self.check(empty_source, layout)["status"], "PASS")

    def test_comma_bearing_source_identities_remain_exact_native_json_lists_and_members(self):
        import json
        building, layout, equipment, routing = _group_source()
        main, corner = building.layout.rooms
        production, packaging, buffer = building.zones
        building = replace(building, layout=replace(building.layout,
            rooms=(replace(main, required_adjacency=("corner, bay",)), replace(corner, id="corner, bay"))),
            zones=(replace(production, id="production, raw"),
                   replace(packaging, id="packaging, clean", room_ids=("corner, bay",), parent_zone_id="production, raw"),
                   replace(buffer, parent_zone_id="production, raw")),
            flows=(replace(building.flows[0], id="production, raw", to_ids=("packaging, clean",)),))
        layout = replace(layout, placements={"main": layout.placements["main"], "corner, bay": layout.placements["corner"]})
        routing = replace(routing, routes=(replace(routing.routes[0], flow_id="production, raw",
            to_id="packaging, clean", to_room_id="corner, bay", room_path=("main", "corner, bay")),))
        self.export(building, layout, equipment, routing)
        model = ifcopenshell.open(self.path)
        child = next(zone for zone in model.by_type("IfcZone") if zone.Name == "packaging, clean")
        system = model.by_type("IfcSystem", include_subtypes=False)[0]
        self.assertEqual(json.loads(_property(child, "DirectRoomIdsJSON").NominalValue.wrappedValue), ["corner, bay"])
        self.assertEqual(json.loads(_property(system, "ToIdsJSON").NominalValue.wrappedValue), ["packaging, clean"])
        self.assertIn(("IfcSpace", "room", "corner, bay"), _members(child))
        self.assertEqual(self.check(building, layout, equipment, routing)["status"], "PASS")

    def test_rehashed_declared_property_relationships_guid_scope_and_extra_claims_fail(self):
        building, layout, equipment, routing = _group_source()
        for mode in ("property_guid", "definition_guid", "foreign_definition_member", "extra_claim"):
            with self.subTest(mode=mode):
                self.export(building, layout, equipment, routing)
                model = ifcopenshell.open(self.path)
                zone = next(zone for zone in model.by_type("IfcZone") if zone.Name == "production")
                link = next(link for link in zone.IsDefinedBy
                            if link.RelatingPropertyDefinition.Name == "Pset_OrthogonalGroup")
                if mode == "property_guid":
                    link.RelatingPropertyDefinition.GlobalId = ifcopenshell.guid.new()
                elif mode == "definition_guid":
                    link.GlobalId = ifcopenshell.guid.new()
                elif mode == "foreign_definition_member":
                    link.RelatedObjects = (*link.RelatedObjects, model.by_type("IfcSpace")[0])
                elif mode == "extra_claim":
                    prop = model.create_entity("IfcPropertySingleValue", Name="EngineeringAcceptance",
                        NominalValue=model.create_entity("IfcLabel", "PASS"))
                    definition = model.create_entity("IfcPropertySet", GlobalId=ifcopenshell.guid.new(),
                        Name="Pset_PhysicalAcceptance", HasProperties=[prop])
                    model.create_entity("IfcRelDefinesByProperties", GlobalId=ifcopenshell.guid.new(),
                        RelatedObjects=[zone], RelatingPropertyDefinition=definition)
                model.write(self.path)
                report = self.check(building, layout, equipment, routing)
                self.assertEqual(report["status"], "FAIL", report["issues"])


if __name__ == "__main__":
    unittest.main()

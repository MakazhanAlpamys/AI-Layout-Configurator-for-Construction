"""A shared facility grid and semantic floor identities survive real IFC exchange."""

from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest

import ifcopenshell
import ifcopenshell.geom
import ifcopenshell.util.element
import ifcopenshell.util.placement
import ifcopenshell.validate

from layout_configurator.building import BuildingIR, StructuralGridSpec
from layout_configurator.equipment import EquipmentLayoutResult, EquipmentPlacement, validate_equipment_layout
from layout_configurator.facility import default_facility_profile, validate_building
from layout_configurator.facility_floors import (
    FacilityFloorsError, FloorStack, facility_floors_from_mapping, solve_facility_floors, vertical_checks,
)
from layout_configurator.facility_generation import FeasibleFacilityCandidate
from layout_configurator.flows import route_flows, validate_flow_routes
from layout_configurator import ifc as exporter
from layout_configurator.models import LayoutResult, Rect
from layout_configurator.validation import validate_layout


GRID = {"axes_x_mm": [0, 3000, 6000, 9000], "axes_y_mm": [0, 3000, 6000],
        "labels_x": ["A", "B", "C", "D"], "labels_y": ["1", "2", "3"]}


def _program():
    return {
        "version": "0.2", "layout": {
            "project_name": "Repeated floor program", "boundary": {"width": 9000, "height": 6000},
            "grid_mm": 100, "entry_room": "core",
            "rooms": [{"id": name, "area": 9, "min_area": 9, "max_area": 9,
                       "min_width": 3000, "min_depth": 3000,
                       "required_adjacency": {"core": ["process"], "process": ["destination"]}.get(name, []),
                       "needs_daylight": name == "destination"}
                      for name in ("core", "process", "destination")],
        },
        "zones": [{"id": "production", "type": "process", "room_ids": ["process"]},
                  {"id": "packaging", "type": "support", "room_ids": ["destination"],
                   "parent_zone_id": "production"}],
        "equipment": [{"id": name, "type": "machine", "room_id": room,
                       "width_mm": 500, "depth_mm": 500}
                      for name, room in (("mixer", "process"), ("sealer", "destination"))],
        "flows": [{"id": "transfer", "type": "material", "from_ids": ["mixer"],
                   "to_ids": ["sealer"], "minimum_clear_width_mm": 1200}],
    }


def _payload():
    return {"project_name": "Combined facility", "floor_height_mm": 4500, "structural_grid": deepcopy(GRID),
            "floors": [{"level": 0, "elevation_mm": 0, "program": "ground.json"},
                       {"level": 1, "elevation_mm": 4500, "program": "upper.json"}],
            "vertical_cores": [{"id": "goods_core", "kind": "goods_lift", "rooms": {0: "core", 1: "core"}}],
            "cross_floor_flows": [{"id": "retained_samples", "type": "material",
                                   "from": {"level": 0, "id": "mixer"},
                                   "to": {"level": 1, "id": "mixer"}, "via": ["goods_core"],
                                   "minimum_clear_width_mm": 1200}]}


def _write_programs(root, program=None):
    for filename in ("ground.json", "upper.json"):
        (root / filename).write_text(json.dumps(program or _program()), encoding="utf-8")


def _candidate(building, variant=1, *, dy=0):
    result = LayoutResult(variant, {"core": Rect(0, dy, 3000, 3000),
                                   "process": Rect(3000, dy, 3000, 3000),
                                   "destination": Rect(6000, dy, 3000, 3000)})
    equipment = EquipmentLayoutResult((
        EquipmentPlacement("mixer", "process", Rect(3500, dy + 500, 500, 500)),
        EquipmentPlacement("sealer", "destination", Rect(6500, dy + 500, 500, 500)),
    ))
    layout_report = validate_layout(building.layout, result)
    equipment_report = validate_equipment_layout(building, result, equipment)
    routes = route_flows(building, result, equipment)
    flow_report = validate_flow_routes(building, result, routes, equipment)
    facility_report = validate_building(building, result, equipment, routes,
                                       profile=default_facility_profile(),
                                       equipment_report=equipment_report, flow_report=flow_report)
    return FeasibleFacilityCandidate(1, 1, 1, 1, result, layout_report, equipment, equipment_report,
                                     routes, flow_report, facility_report)


def _properties(entity, name):
    return ifcopenshell.util.element.get_psets(entity).get(name, {})


def _members(group):
    return [member for relation in group.IsGroupedBy for member in relation.RelatedObjects]


class SharedFacilityGridTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        _write_programs(self.root)

    def test_one_declared_structural_grid_is_inherited_by_every_floor(self):
        spec = facility_floors_from_mapping(_payload(), base_dir=self.root)
        expected = StructuralGridSpec.from_mapping(GRID)
        self.assertEqual(getattr(spec, "structural_grid", None), expected)
        self.assertEqual([entry.building.structural_grid for entry in spec.floors], [expected, expected])
        self.assertEqual([building.structural_grid for building in spec.floor_programs().values()],
                         [expected, expected])

    def test_a_floor_cannot_override_the_shared_axes_or_labels(self):
        local = _program()
        local["structural_grid"] = {**GRID, "axes_x_mm": [0, 2000, 6000, 9000]}
        (self.root / "upper.json").write_text(json.dumps(local), encoding="utf-8")
        with self.assertRaisesRegex(FacilityFloorsError, "shared.*grid|grid.*shared"):
            facility_floors_from_mapping(_payload(), base_dir=self.root)

    def test_invalid_shared_grids_and_floor_elevations_are_rejected_before_solving(self):
        for change in (
            lambda p: p["structural_grid"].update(axes_x_mm=[3000, 0]),
            lambda p: p["structural_grid"].update(axes_y_mm=[0, float("nan"), 6000]),
            lambda p: p["structural_grid"].update(labels_x=["A", "A", "C", "D"]),
            lambda p: p["floors"][1].update(elevation_mm=0),
            lambda p: p["floors"][1].update(elevation_mm=float("inf")),
            lambda p: p.update(floor_height_mm=0),
        ):
            raw = _payload()
            change(raw)
            with self.subTest(payload=raw), self.assertRaises(FacilityFloorsError):
                facility_floors_from_mapping(raw, base_dir=self.root)

    def test_duplicate_cross_floor_and_core_ids_cannot_produce_ambiguous_exchange_ids(self):
        for collection in ("vertical_cores", "cross_floor_flows"):
            raw = _payload()
            raw[collection].append(deepcopy(raw[collection][0]))
            with self.subTest(collection=collection), self.assertRaisesRegex(FacilityFloorsError, "Duplicate"):
                facility_floors_from_mapping(raw, base_dir=self.root)

    def test_shared_grid_is_an_actual_solver_constraint_for_vertical_core_rooms(self):
        # There is exactly one 2 m by 2 m bay. Post-export grid metadata cannot
        # make an unconstrained core at another coordinate satisfy this check.
        program = {"layout": {
            "project_name": "Grid constrained facility", "boundary": {"width": 10000, "height": 6000},
            "grid_mm": 100, "entry_room": "core",
            "rooms": [{"id": name, "area": 4, "min_area": 4, "max_area": 4,
                       "min_width": 2000, "min_depth": 2000,
                       "required_adjacency": ["process", "destination"] if name == "core" else []}
                      for name in ("core", "process", "destination")],
        }}
        _write_programs(self.root, program)
        raw = _payload()
        raw["structural_grid"] = {"axes_x_mm": [3000, 5000], "axes_y_mm": [2000, 4000]}
        raw["cross_floor_flows"] = []
        spec = facility_floors_from_mapping(raw, base_dir=self.root)
        solved = solve_facility_floors(spec, variants=1, time_limit_seconds=5, seed=1,
                                      max_attempts=1, room_solver="monolithic")
        self.assertTrue(solved.stacks[0].ok)
        for level, candidate in solved.stacks[0].floors.items():
            self.assertEqual(candidate.result.placements["core"], Rect(3000, 2000, 2000, 2000), level)

    def test_independent_vertical_checks_detect_coaxial_cores_off_the_shared_axes(self):
        spec = facility_floors_from_mapping(_payload(), base_dir=self.root)
        programs = {level: building.with_required_flow_opening_width()
                    for level, building in spec.floor_programs().items()}
        floors = {level: _candidate(building) for level, building in programs.items()}
        # Shift every floor together: CORE_ALIGNMENT still passes, but both
        # core x endpoints are off the declared structural axes.
        for level, candidate in tuple(floors.items()):
            placements = {key: replace(rect, x=rect.x + 100) for key, rect in candidate.result.placements.items()}
            floors[level] = replace(candidate, result=replace(candidate.result, placements=placements))
        checks = {check.id: check.status for check in vertical_checks(spec, floors)}
        self.assertEqual(checks["CORE_ALIGNMENT"], "PASS")
        self.assertEqual(checks.get("SHARED_STRUCTURAL_GRID"), "FAIL")


class FacilityMultiFloorIfcTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        _write_programs(self.root)
        self.spec = facility_floors_from_mapping(_payload(), base_dir=self.root)
        self.programs = {level: building.with_required_flow_opening_width()
                         for level, building in self.spec.floor_programs().items()}
        floors = {level: _candidate(building) for level, building in self.programs.items()}
        self.stack = FloorStack(1, floors, vertical_checks(self.spec, floors))
        self.assertTrue(self.stack.ok, [(level, [(check.id, check.status) for check in candidate.facility_report.checks],
                                                candidate.flow_report.issues)
                                       for level, candidate in floors.items()])

    def _export(self, name="facility.ifc", *, spec=None, stack=None, programs=None):
        method = getattr(exporter, "export_facility_floors_ifc", None)
        self.assertTrue(callable(method), "Unified facility IFC exporter is missing")
        summary = method(self.root / name, spec or self.spec, stack or self.stack,
                         self.programs if programs is None else programs)
        return ifcopenshell.open(summary.path), summary

    def test_same_local_ids_on_different_floors_have_distinct_stable_semantic_ids(self):
        model, summary = self._export()
        self.assertEqual((summary.storeys, summary.spaces, summary.equipment), (2, 6, 4))
        spaces = {_properties(space, "Pset_FacilityIdentity").get("SemanticId"): space
                  for space in model.by_type("IfcSpace")}
        self.assertEqual(set(spaces), {f"L{level}/room/{room}" for level in (0, 1)
                                      for room in ("core", "process", "destination")})
        self.assertEqual({space.Name for space in spaces.values()}, {"core", "process", "destination"})
        equipment = [proxy for proxy in model.by_type("IfcBuildingElementProxy")
                     if _properties(proxy, "Pset_LayoutEquipment")]
        self.assertEqual({_properties(proxy, "Pset_FacilityIdentity")["SemanticId"] for proxy in equipment},
                         {f"L{level}/equipment/{name}" for level in (0, 1) for name in ("mixer", "sealer")})
        roots = model.by_type("IfcRoot")
        self.assertEqual(len({entity.GlobalId for entity in roots}), len(roots), "Every IFC root GUID must be unique")

    def test_storey_decomposition_and_geometry_preserve_declared_elevations(self):
        model, _ = self._export()
        storeys = {int(_properties(entity, "Pset_FacilityFloor")["Level"]): entity
                   for entity in model.by_type("IfcBuildingStorey")}
        self.assertEqual({level: storey.Elevation for level, storey in storeys.items()}, {0: 0, 1: 4500})
        for level, storey in storeys.items():
            spaces = [entity for relation in storey.IsDecomposedBy for entity in relation.RelatedObjects]
            self.assertEqual({space.Name for space in spaces}, {"core", "process", "destination"})
            self.assertTrue(all(space.is_a("IfcSpace") for space in spaces))
            for space in spaces:
                self.assertEqual(ifcopenshell.util.placement.get_local_placement(space.ObjectPlacement)[2, 3],
                                 level * 4500)
            contained = [entity for relation in storey.ContainsElements for entity in relation.RelatedElements]
            self.assertFalse(any(entity.is_a("IfcSpace") for entity in contained))
            self.assertEqual(sum(bool(_properties(entity, "Pset_LayoutEquipment")) for entity in contained), 2)
            for entity in contained:
                if _properties(entity, "Pset_LayoutEquipment"):
                    self.assertEqual(ifcopenshell.util.placement.get_local_placement(entity.ObjectPlacement)[2, 3],
                                     level * 4500)

    def test_native_zone_system_membership_and_vertical_core_relationships_are_floor_correct(self):
        model, _ = self._export()
        zones = {_properties(zone, "Pset_FacilityIdentity")["SemanticId"]: zone
                 for zone in model.by_type("IfcZone")}
        self.assertEqual(set(zones), {f"L{level}/zone/{name}" for level in (0, 1)
                                     for name in ("production", "packaging")})
        for level in (0, 1):
            self.assertEqual({_properties(entity, "Pset_FacilityIdentity")["SemanticId"]
                              for entity in _members(zones[f"L{level}/zone/production"])},
                             {f"L{level}/room/process", f"L{level}/zone/packaging"})
        cores = [group for group in model.by_type("IfcGroup", include_subtypes=False)
                 if _properties(group, "Pset_FacilityCore")]
        self.assertEqual(len(cores), 1)
        self.assertEqual({_properties(entity, "Pset_FacilityIdentity")["SemanticId"]
                          for entity in _members(cores[0])}, {"L0/room/core", "L1/room/core"})
        systems = {_properties(system, "Pset_FacilityIdentity")["SemanticId"]: system
                   for system in model.by_type("IfcSystem", include_subtypes=False)}
        self.assertEqual(set(systems), {"L0/flow-system/transfer", "L1/flow-system/transfer",
                                       "L0/flow-system/retained_samples@L0", "L1/flow-system/retained_samples@L1",
                                       "cross-floor-flow/retained_samples"})
        self.assertEqual({_properties(member, "Pset_FacilityIdentity")["SemanticId"]
                          for member in _members(systems["cross-floor-flow/retained_samples"])},
                         {"L0/flow-system/retained_samples@L0", "L1/flow-system/retained_samples@L1",
                          "vertical-core/goods_core"})
        for level in (0, 1):
            members = _members(systems[f"L{level}/flow-system/transfer"])
            self.assertEqual({_properties(member, "Pset_FacilityIdentity")["Level"] for member in members}, {level})
            self.assertEqual(sum(bool(_properties(member, "Pset_LayoutEquipment")) for member in members), 2)

    def test_one_native_grid_has_the_declared_axis_labels_coordinates_and_floor_references(self):
        model, _ = self._export()
        self.assertEqual(len(model.by_type("IfcGrid")), 1)
        grid = model.by_type("IfcGrid")[0]
        self.assertEqual(grid.PredefinedType, "RECTANGULAR")
        self.assertEqual({axis.AxisTag: tuple(tuple(point.Coordinates) for point in axis.AxisCurve.Points)
                          for axis in grid.UAxes},
                         {"1": ((0, 0), (9000, 0)), "2": ((0, 3000), (9000, 3000)),
                          "3": ((0, 6000), (9000, 6000))})
        self.assertEqual({axis.AxisTag: tuple(tuple(point.Coordinates) for point in axis.AxisCurve.Points)
                          for axis in grid.VAxes},
                         {"A": ((0, 0), (0, 6000)), "B": ((3000, 0), (3000, 6000)),
                          "C": ((6000, 0), (6000, 6000)), "D": ((9000, 0), (9000, 6000))})
        self.assertTrue(all(axis.SameSense for axis in (*grid.UAxes, *grid.VAxes)))
        refs = [relation for relation in model.by_type("IfcRelReferencedInSpatialStructure")
                if grid in relation.RelatedElements]
        self.assertEqual({relation.RelatingStructure for relation in refs}, set(model.by_type("IfcBuildingStorey")))

    def test_grid_and_floor_solids_tessellate_at_their_declared_world_elevations(self):
        # A native grid without its FootPrint projection can survive attribute
        # validation but disappear in geometry consumers. Tessellate the actual
        # received grid, spaces and equipment rather than trusting elevations.
        model, _ = self._export()
        grid = model.by_type("IfcGrid")[0]
        self.assertIsNotNone(grid.Representation, "Grid has no receiving-tool FootPrint representation")
        footprint = grid.Representation.Representations[0]
        self.assertEqual((footprint.RepresentationIdentifier, footprint.RepresentationType),
                         ("FootPrint", "GeometricCurveSet"))
        settings = ifcopenshell.geom.settings()
        settings.set(settings.USE_WORLD_COORDS, True)
        settings.set("dimensionality", ifcopenshell.ifcopenshell_wrapper.CURVES_SURFACES_AND_SOLIDS)
        shape = ifcopenshell.geom.create_shape(settings, grid)
        self.assertEqual(len(shape.geometry.edges), 14)  # Seven complete straight axes.
        self.assertEqual({round(value, 6) for value in shape.geometry.verts[2::3]}, {0})
        self.assertEqual((min(shape.geometry.verts[0::3]), max(shape.geometry.verts[0::3])), (0, 9))
        self.assertEqual((min(shape.geometry.verts[1::3]), max(shape.geometry.verts[1::3])), (0, 6))
        for entity in model.by_type("IfcSpace") + model.by_type("IfcBuildingElementProxy"):
            identity = _properties(entity, "Pset_FacilityIdentity")
            equipment = _properties(entity, "Pset_LayoutEquipment")
            if not (entity.is_a("IfcSpace") or equipment):
                continue
            shape = ifcopenshell.geom.create_shape(settings, entity)
            zs = shape.geometry.verts[2::3]
            base = identity["Level"] * 4.5
            self.assertAlmostEqual(min(zs), base)
            self.assertAlmostEqual(max(zs), base + (1 if equipment else 2.8))

    def test_independent_ifc_readback_contains_unique_qualified_ids_and_passes_strict_express(self):
        model, summary = self._export()
        logger = ifcopenshell.validate.json_logger()
        ifcopenshell.validate.validate(model, logger, express_rules=True)
        self.assertEqual([(item.get("attribute"), item.get("message")) for item in logger.statements], [])
        report = exporter.validate_ifc_roundtrip(summary.path, summary)
        self.assertTrue(report.ok, report.issues)
        semantic_ids = getattr(report.summary, "semantic_ids", ())
        self.assertIn("L0/equipment/mixer", semantic_ids)
        self.assertIn("L1/equipment/mixer", semantic_ids)
        self.assertEqual(len(semantic_ids), len(set(semantic_ids)))

    def test_reordering_programs_and_geometry_revision_keep_semantic_guids_and_membership(self):
        first, _ = self._export()
        programs = {level: replace(building, zones=tuple(reversed(building.zones)),
                                   flows=tuple(reversed(building.flows)))
                    for level, building in reversed(tuple(self.programs.items()))}
        floors = {level: _candidate(building, variant=2, dy=3000) for level, building in programs.items()}
        stack = FloorStack(2, floors, vertical_checks(self.spec, floors))
        self.assertTrue(stack.ok)
        second, _ = self._export("revision.ifc", stack=stack, programs=programs)
        for kind in ("IfcSpace", "IfcZone", "IfcSystem", "IfcBuildingElementProxy"):
            self.assertEqual({_properties(entity, "Pset_FacilityIdentity")["SemanticId"]: entity.GlobalId
                              for entity in first.by_type(kind)},
                             {_properties(entity, "Pset_FacilityIdentity")["SemanticId"]: entity.GlobalId
                              for entity in second.by_type(kind)})

    def test_new_export_requires_grid_and_rechecks_alignment_instead_of_trusting_stale_gates(self):
        raw = _payload()
        raw.pop("structural_grid")
        old_spec = facility_floors_from_mapping(raw, base_dir=self.root)
        method = getattr(exporter, "export_facility_floors_ifc", None)
        self.assertTrue(callable(method), "Unified facility IFC exporter is missing")
        with self.assertRaisesRegex(ValueError, "shared.*grid|grid.*shared"):
            method(self.root / "missing-grid.ifc", old_spec, self.stack)
        upper = self.stack.floors[1]
        placements = dict(upper.result.placements)
        placements["core"] = replace(placements["core"], x=100)
        changed = replace(upper, result=replace(upper.result, placements=placements))
        stale = replace(self.stack, floors={0: self.stack.floors[0], 1: changed})
        with self.assertRaisesRegex(ValueError, "CORE_ALIGNMENT|SHARED_STRUCTURAL_GRID"):
            method(self.root / "bad-core.ifc", self.spec, stale, self.programs)
        self.assertFalse((self.root / "bad-core.ifc").exists())


if __name__ == "__main__":
    unittest.main()

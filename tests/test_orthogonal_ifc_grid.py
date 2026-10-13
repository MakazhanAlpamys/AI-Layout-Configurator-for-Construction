"""Source reference axes in native IFC and source-independent readback."""

from dataclasses import replace
import tempfile
import unittest
from pathlib import Path

import ifcopenshell
import ifcopenshell.geom
import ifcopenshell.validate
import numpy as np

from layout_configurator.building import BuildingIR, StructuralGridSpec
from layout_configurator.models import LayoutResult, Rect, WindowSpec
from layout_configurator.orthogonal import OrthogonalPolygon
from layout_configurator.orthogonal_grid import OrthogonalGridCapabilityError, validate_orthogonal_grid
from layout_configurator.orthogonal_ifc import export_orthogonal_draft_ifc
from layout_configurator.orthogonal_ifc_qa import validate_orthogonal_ifc
from test_orthogonal_ifc import _file_hash, _hash, _property, _source


class OrthogonalIFCGridTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "grid.ifc"

    def source(self):
        building, layout, equipment, routing = _source()
        grid = StructuralGridSpec((1000, 2050.5, 5000), (1000, 5000), ("A", "B", "C"), ("1", "2"))
        return replace(building, structural_grid=grid), layout, equipment, routing

    def export(self, building, layout, equipment=None, routing=None, **options):
        return export_orthogonal_draft_ifc(self.path, building, layout, equipment,
            routing=routing, include_grid=True, **options)

    def check(self, building, layout, equipment=None, routing=None, **options):
        return validate_orthogonal_ifc(self.path, building, layout, equipment,
            routing=routing, include_grid=True, expected_sha256=_file_hash(self.path), **options)

    def test_native_grid_actual_fractional_axes_footprint_and_strict_ifc4(self):
        building, layout, equipment, routing = self.source()
        index = self.export(building, layout, equipment, routing)
        model = ifcopenshell.open(self.path)
        grid = model.by_type("IfcGrid")[0]
        self.assertEqual(len(model.by_type("IfcGrid")), 1)
        self.assertEqual(grid.PredefinedType, "RECTANGULAR")
        self.assertEqual([axis.AxisTag for axis in grid.UAxes], ["A", "B", "C"])
        self.assertEqual([axis.AxisTag for axis in grid.VAxes], ["1", "2"])
        self.assertEqual([tuple(point.Coordinates) for point in grid.UAxes[1].AxisCurve.Points],
                         [(2050.5, 0.), (2050.5, 6000.)])
        self.assertTrue(all(axis.SameSense is True for axis in (*grid.UAxes, *grid.VAxes)))
        footprint = grid.Representation.Representations[0]
        self.assertEqual((footprint.RepresentationIdentifier, footprint.RepresentationType),
                         ("FootPrint", "GeometricCurveSet"))
        self.assertEqual({curve.id() for curve in footprint.Items[0].Elements},
                         {axis.AxisCurve.id() for axis in (*grid.UAxes, *grid.VAxes)})
        self.assertIn(grid, model.by_type("IfcRelContainedInSpatialStructure")[0].RelatedElements)
        capability = validate_orthogonal_grid(building.layout, building.structural_grid).to_dict()
        self.assertEqual(index["grid_capability"], capability)
        self.assertEqual(index["local_validation"]["grid_capability"], capability)
        self.assertEqual(index["source"]["building_sha256"], _hash(building.to_dict()))
        self.assertEqual(index["counts"]["grids"], 1)
        self.assertEqual(index["counts"]["grid_axes"], 5)
        report = self.check(building, layout, equipment, routing)
        self.assertEqual(report["status"], "PASS", report)
        self.assertEqual(report["grid_capability"], capability)
        self.assertEqual(report["grid_axes"][1]["id"], "grid:x:0002")
        self.assertEqual(report["grid_axes"][1]["world_points_mm"], [[2050.5, 0., 0.], [2050.5, 6000., 0.]])
        self.assertEqual(report["full_facility_validation"], {"status": "UNKNOWN", "ok": False})
        self.assertEqual(report["engineering_acceptance"], "PENDING")
        settings = ifcopenshell.geom.settings()
        settings.set(settings.USE_WORLD_COORDS, True)
        settings.set("dimensionality", ifcopenshell.ifcopenshell_wrapper.CURVES_SURFACES_AND_SOLIDS)
        shape = ifcopenshell.geom.create_shape(settings, grid)
        vertices = np.asarray(shape.geometry.verts).reshape(-1, 3)
        edges = np.asarray(shape.geometry.edges).reshape(-1, 2)
        self.assertEqual(len(edges), 5)
        actual_segments = {tuple(sorted(tuple(vertices[index]) for index in edge)) for edge in edges}
        expected_segments = {tuple(sorted((tuple((*axis.start, 0)), tuple((*axis.end, 0)))))
                             for axis in validate_orthogonal_grid(building.layout, building.structural_grid).axes}
        scaled = {tuple(tuple(coordinate * 1000 for coordinate in point) for point in segment)
                  for segment in actual_segments}
        self.assertEqual(scaled, expected_segments)
        logger = ifcopenshell.validate.json_logger()
        ifcopenshell.validate.validate(model, logger, express_rules=True)
        self.assertEqual(logger.statements, [], logger.statements)

    def test_cutout_crossing_axis_is_continuous_and_never_snapped_to_room_grid(self):
        building = BuildingIR.from_mapping({"layout": {"project_name": "Continuous source grid",
            "boundary": {"width": 6000, "height": 6000,
                         "cutouts": [{"x": 0, "y": 2000, "width": 4000, "height": 2000}]},
            "entry_room": "room", "grid_mm": 100,
            "rooms": [{"id": "room", "target_area": 28, "min_area": 28, "max_area": 28}]},
            "structural_grid": {"axes_x_mm": [2050.5], "axes_y_mm": [1000]}})
        shape = OrthogonalPolygon(((0, 0), (6000, 0), (6000, 6000), (0, 6000),
                                   (0, 4000), (4000, 4000), (4000, 2000), (0, 2000)))
        layout = LayoutResult(1, {"room": shape})
        self.export(building, layout)
        grid = ifcopenshell.open(self.path).by_type("IfcGrid")[0]
        self.assertEqual(grid.UAxes[0].AxisTag, "1")
        self.assertEqual([point.Coordinates for point in grid.UAxes[0].AxisCurve.Points],
                         [(2050.5, 0.), (2050.5, 6000.)])
        self.assertEqual(self.check(building, layout)["status"], "PASS")

    def test_default_false_evidence_and_ifc_data_are_exactly_unchanged(self):
        building, layout, equipment, routing = self.source()
        first = export_orthogonal_draft_ifc(self.path, building, layout, equipment, routing=routing)
        first_data = self.path.read_text(encoding="utf-8").split("DATA;", 1)[1]
        before = validate_orthogonal_ifc(self.path, building, layout, equipment, routing=routing)
        self.assertEqual(before["artifact_sha256"], _file_hash(self.path))
        self.assertEqual(before, validate_orthogonal_ifc(self.path, building, layout, equipment,
            routing=routing, include_grid=False))
        second = export_orthogonal_draft_ifc(self.path, building, layout, equipment,
                                            routing=routing, include_grid=False)
        self.assertEqual(first_data, self.path.read_text(encoding="utf-8").split("DATA;", 1)[1])
        self.assertEqual({key: value for key, value in first.items() if key != "artifact_sha256"},
                         {key: value for key, value in second.items() if key != "artifact_sha256"})
        self.assertNotIn("grid_capability", second)
        self.assertNotIn("grids", second["counts"])
        after = validate_orthogonal_ifc(self.path, building, layout, equipment, routing=routing, include_grid=False)
        self.assertEqual(after["artifact_sha256"], _file_hash(self.path))
        # Each physical export has its own volatile STEP header timestamp.
        self.assertEqual({key: value for key, value in before.items() if key != "artifact_sha256"},
                         {key: value for key, value in after.items() if key != "artifact_sha256"})
        self.assertFalse(ifcopenshell.open(self.path).by_type("IfcGrid"))

    def test_opted_missing_grid_does_not_invent_axes(self):
        building, layout, _, _ = _source()
        index = self.export(building, layout)
        self.assertEqual(index["grid_capability"]["axes"], [])
        self.assertEqual(index["counts"]["grids"], 0)
        self.assertEqual(index["counts"]["grid_axes"], 0)
        report = self.check(building, layout)
        self.assertEqual(report["status"], "PASS", report)
        self.assertEqual(report["grid_axes"], [])
        self.assertFalse(ifcopenshell.open(self.path).by_type("IfcGrid"))

    def test_strict_boolean_and_original_invalid_grid_preflight_preserve_destination(self):
        building, layout, _, _ = self.source()
        for flag in (None, 0, 1, "true", []):
            with self.subTest(flag=flag):
                self.path.write_bytes(b"Existing destination")
                with self.assertRaisesRegex(ValueError, "include_grid"):
                    export_orthogonal_draft_ifc(self.path, building, layout, include_grid=flag)
                self.assertEqual(self.path.read_bytes(), b"Existing destination")
        for grid in (StructuralGridSpec((True,), (1000,)), StructuralGridSpec((10 ** 400,), (1000,)),
                     StructuralGridSpec((float("nan"),), (1000,)), StructuralGridSpec((1000,), (1000,), ([],)),
                     StructuralGridSpec((7000,), (1000,))):
            with self.subTest(grid=grid):
                self.path.write_bytes(b"Existing destination")
                with self.assertRaises(OrthogonalGridCapabilityError):
                    self.export(replace(building, structural_grid=grid), layout)
                self.assertEqual(self.path.read_bytes(), b"Existing destination")

    def test_source_grid_does_not_enable_unsupported_windows(self):
        building, layout, _, _ = self.source()
        building = replace(building, layout=replace(building.layout,
            windows=(WindowSpec("source-window", "main", "bottom", 1000, 800),)))
        with self.assertRaises(ValueError):
            self.export(building, layout)
        self.assertFalse(self.path.exists())
        index = self.export(building, layout, include_windows=True)
        report = self.check(building, layout, include_windows=True)
        self.assertEqual(report["status"], "PASS", report)
        self.assertEqual(index["counts"]["windows"], 1)
        self.assertEqual(report["counts"]["grids"], 1)

    def test_original_integer_coordinates_and_visible_label_whitespace_keep_source_identity(self):
        building, layout, _, _ = self.source()
        building = replace(building, structural_grid=StructuralGridSpec((1000, 2050.5, 5000), (1000, 5000),
            (" A ", "B", "C"), (" A ", "2")))
        before = building.to_dict(), layout.to_dict()
        capability = validate_orthogonal_grid(building.layout, building.structural_grid).to_dict()
        index = self.export(building, layout)
        self.assertEqual(index["source"]["building_sha256"], _hash(before[0]))
        self.assertEqual(index["grid_capability"], capability)
        grid = ifcopenshell.open(self.path).by_type("IfcGrid")[0]
        self.assertEqual(grid.UAxes[0].AxisTag, " A ")
        self.assertEqual(grid.VAxes[0].AxisTag, " A ")
        report = self.check(building, layout)
        self.assertEqual(report["status"], "PASS", report)
        self.assertEqual(report["source"], index["source"])
        self.assertEqual(report["grid_capability"], capability)
        self.assertEqual((building.to_dict(), layout.to_dict()), before)

    def test_grid_and_existing_occurrence_guids_survive_source_geometry_and_variant_changes(self):
        building, layout, equipment, routing = self.source()
        first_index = self.export(building, layout, equipment, routing)
        first_roots = {(item.is_a(), item.Name, item.GlobalId)
                       for item in ifcopenshell.open(self.path).by_type("IfcRoot")}
        changed_building = replace(building, layout=replace(building.layout,
            boundary=replace(building.layout.boundary, width_mm=6400, height_mm=6400)),
            structural_grid=replace(building.structural_grid, axes_x_mm=(1000, 2150.5, 5000)))
        changed_layout = LayoutResult(4, {"main": layout.placements["main"].translated(200, 200),
                                         "corner": Rect(2200, 2200, 4000, 4000)})
        changed_equipment = replace(equipment, placements=(replace(equipment.placements[0],
            rect=Rect(5000, 600, 500, 500)),))
        changed_routes = replace(routing, routes=(replace(routing.routes[0],
            points=tuple((x + 200, y + 200) for x, y in routing.routes[0].points)),))
        second_index = self.export(changed_building, changed_layout, changed_equipment, changed_routes)
        self.assertEqual(first_roots, {(item.is_a(), item.Name, item.GlobalId)
                                      for item in ifcopenshell.open(self.path).by_type("IfcRoot")})
        self.assertNotEqual(first_index["source"], second_index["source"])
        self.assertNotEqual(first_index["grid_capability"]["source"], second_index["grid_capability"]["source"])
        self.assertEqual(self.check(changed_building, changed_layout, changed_equipment, changed_routes)["status"], "PASS")

    def test_equivalent_actual_local_grid_transform_passes_but_metadata_cannot_opt_in(self):
        building, layout, _, _ = self.source()
        self.export(building, layout)
        model = ifcopenshell.open(self.path)
        grid = model.by_type("IfcGrid")[0]
        grid.ObjectPlacement.RelativePlacement.Location.Coordinates = (-100., 100., 0.)
        for axis in (*grid.UAxes, *grid.VAxes):
            for point in axis.AxisCurve.Points:
                x, y = point.Coordinates
                point.Coordinates = (x + 100., y - 100.)
        model.write(self.path)
        self.assertEqual(self.check(building, layout)["status"], "PASS")
        for mode in (False, 0, None, "true"):
            with self.subTest(mode=mode):
                report = validate_orthogonal_ifc(self.path, building, layout, include_grid=mode,
                                                 expected_sha256=_file_hash(self.path))
                self.assertEqual(report["status"], "FAIL", report)

    def test_grid_preflight_rejects_invalid_nested_source_before_constructor_coercion(self):
        building, layout, _, _ = self.source()
        source = replace(building, layout=replace(building.layout,
            rooms=(replace(building.layout.rooms[0], needs_daylight=""), building.layout.rooms[1])))
        with self.assertRaises(OrthogonalGridCapabilityError):
            self.export(source, layout)
        self.assertFalse(self.path.exists())

    def test_rehashed_axis_geometry_identity_order_and_footprint_tampering_fail(self):
        building, layout, equipment, routing = self.source()
        mutations = ("shift", "snap", "truncate", "missing_axis", "extra_axis", "wrong_label", "reverse",
                     "swap_groups", "reorder", "same_sense", "missing_footprint", "different_footprint",
                     "extra_footprint", "footprint_context", "grid_translation", "grid_rotation",
                     "grid_z", "missing_grid", "wrong_id", "wrong_guid", "wrong_containment", "units",
                     "metadata", "status", "full_status", "engineering", "orphan_axis",
                     "three_dimensional_axis", "w_axis", "duplicate_grid", "identical_unbound_footprint")
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                self.export(building, layout, equipment, routing)
                model = ifcopenshell.open(self.path)
                grid = model.by_type("IfcGrid")[0]
                axis = grid.UAxes[1]
                curve = axis.AxisCurve
                representation = grid.Representation.Representations[0]
                if mutation in ("shift", "snap", "truncate"):
                    point = curve.Points[0] if mutation == "truncate" else curve.Points[1]
                    x, y = point.Coordinates
                    point.Coordinates = (x + (49.5 if mutation == "snap" else 1.) if mutation != "truncate" else x,
                                         y if mutation != "truncate" else 2000.)
                elif mutation == "missing_axis":
                    grid.UAxes = (grid.UAxes[0], grid.UAxes[2])
                elif mutation == "extra_axis":
                    grid.UAxes = (*grid.UAxes, model.create_entity("IfcGridAxis", AxisTag="extra",
                        AxisCurve=curve, SameSense=True))
                elif mutation == "wrong_label":
                    axis.AxisTag = "wrong"
                elif mutation == "reverse":
                    curve.Points = tuple(reversed(curve.Points))
                elif mutation == "swap_groups":
                    grid.UAxes, grid.VAxes = grid.VAxes, grid.UAxes
                elif mutation == "reorder":
                    grid.UAxes = tuple(reversed(grid.UAxes))
                elif mutation == "same_sense":
                    axis.SameSense = False
                elif mutation == "orphan_axis":
                    model.create_entity("IfcGridAxis", AxisTag="orphan", AxisCurve=curve, SameSense=True)
                elif mutation == "three_dimensional_axis":
                    curve.Points = tuple(model.create_entity("IfcCartesianPoint",
                        Coordinates=(*point.Coordinates, 0.)) for point in curve.Points)
                elif mutation == "w_axis":
                    grid.WAxes = (axis,)
                elif mutation == "duplicate_grid":
                    duplicate = model.create_entity("IfcGrid", GlobalId=ifcopenshell.guid.new(),
                        UAxes=grid.UAxes, VAxes=grid.VAxes, ObjectPlacement=grid.ObjectPlacement,
                        Representation=grid.Representation, PredefinedType="RECTANGULAR")
                    relation = next(link for link in grid.IsDefinedBy if link.is_a("IfcRelDefinesByProperties"))
                    relation.RelatedObjects = (*relation.RelatedObjects, duplicate)
                elif mutation == "identical_unbound_footprint":
                    representation.Items[0].Elements = tuple(model.create_entity("IfcPolyline",
                        Points=item.Points) for item in representation.Items[0].Elements)
                elif mutation == "missing_footprint":
                    grid.Representation = None
                elif mutation in ("different_footprint", "extra_footprint"):
                    clone = model.create_entity("IfcPolyline", Points=[model.create_entity("IfcCartesianPoint",
                        Coordinates=(x, y)) for x, y in ((2050.5, 0.), (2050.5, 2000.))])
                    elements = representation.Items[0].Elements
                    representation.Items[0].Elements = (*elements, clone) if mutation == "extra_footprint" else (clone,)
                elif mutation == "footprint_context":
                    representation.ContextOfItems = model.create_entity("IfcGeometricRepresentationContext",
                        ContextType="Model", CoordinateSpaceDimension=3,
                        WorldCoordinateSystem=model.by_type("IfcGeometricRepresentationContext")[0].WorldCoordinateSystem)
                elif mutation in ("grid_translation", "grid_z"):
                    grid.ObjectPlacement.RelativePlacement.Location.Coordinates = (1., 0., 0.) if mutation == "grid_translation" else (0., 0., 1.)
                elif mutation == "grid_rotation":
                    grid.ObjectPlacement.RelativePlacement.RefDirection.DirectionRatios = (0., 1., 0.)
                elif mutation == "missing_grid":
                    model.remove(grid)
                elif mutation == "wrong_id":
                    _property(grid, "SourceId").NominalValue = model.create_entity("IfcLabel", "room")
                elif mutation == "wrong_guid":
                    grid.GlobalId = ifcopenshell.guid.new()
                elif mutation == "wrong_containment":
                    link = model.by_type("IfcRelContainedInSpatialStructure")[0]
                    link.RelatedElements = tuple(element for element in link.RelatedElements if element != grid)
                elif mutation == "units":
                    next(unit for unit in model.by_type("IfcSIUnit") if unit.UnitType == "LENGTHUNIT").Prefix = None
                else:
                    field = {"metadata": "StructuralGridSHA256", "status": "Status", "full_status": "FullFacilityValidation",
                             "engineering": "EngineeringAcceptance"}[mutation]
                    _property(model.by_type("IfcProject")[0], field).NominalValue = model.create_entity("IfcLabel", "PASS")
                model.write(self.path)
                report = self.check(building, layout, equipment, routing)
                self.assertEqual(report["status"], "FAIL", report)


if __name__ == "__main__":
    unittest.main()

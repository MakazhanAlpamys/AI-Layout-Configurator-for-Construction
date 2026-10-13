"""Native source-height windows and independent orthogonal IFC readback."""

from dataclasses import replace
import tempfile
import unittest
from pathlib import Path

import ifcopenshell
import ifcopenshell.geom
import ifcopenshell.validate
import numpy as np
from shapely.geometry import Point, Polygon

from layout_configurator.building import BuildingIR
from layout_configurator.models import BoundarySpec, LayoutResult, Rect, WindowSpec
from layout_configurator.orthogonal_ifc import export_orthogonal_draft_ifc
from layout_configurator.orthogonal_ifc_qa import validate_orthogonal_ifc
from layout_configurator.orthogonal_projection import build_orthogonal_wall_plan
from layout_configurator.orthogonal_window_capability import OrthogonalWindowCapabilityError, validate_orthogonal_window_capability
from test_orthogonal_ifc import _file_hash, _hash, _property, _source


class OrthogonalIFCWindowTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "windows.ifc"

    def source(self, *, side="bottom", offset=1000, sill=900, height=1500, identifier="source-window"):
        building, layout, equipment, routing = _source()
        source = replace(building, layout=replace(building.layout,
            windows=(WindowSpec(identifier, "main", side, offset, 800),),
            window_sill_mm=sill, window_height_mm=height))
        return BuildingIR.from_mapping(source.to_dict()), layout, equipment, routing

    def export(self, building, layout, equipment=None, routing=None):
        return export_orthogonal_draft_ifc(self.path, building, layout, equipment,
                                          routing=routing, include_windows=True)

    def check(self, building, layout, equipment=None, routing=None):
        return validate_orthogonal_ifc(self.path, building, layout, equipment, routing=routing,
                                       include_windows=True, expected_sha256=_file_hash(self.path))

    def test_native_source_sill_height_void_fill_and_retained_wall_material(self):
        building, layout, _, _ = self.source()
        evidence = self.export(building, layout)
        model = ifcopenshell.open(self.path)
        window = model.by_type("IfcWindow")[0]
        opening = _kind(model, "window-opening")
        self.assertEqual(window.Name, "source-window")
        self.assertEqual(window.OverallWidth, 800)
        self.assertEqual(window.OverallHeight, 1500)
        self.assertEqual(window.PredefinedType, "WINDOW")
        self.assertEqual(_property(window, "HostRoomId").NominalValue.wrappedValue, "main")
        self.assertEqual(_property(window, "SillMM").NominalValue.wrappedValue, 900)
        self.assertEqual(_property(window, "HeightMM").NominalValue.wrappedValue, 1500)
        self.assertEqual(window.ObjectPlacement.RelativePlacement.Location.Coordinates, (0., 0., 900.))
        self.assertEqual(opening.ObjectPlacement.RelativePlacement.Location.Coordinates, (0., 0., 900.))
        self.assertEqual(len(model.by_type("IfcRelVoidsElement")), 2)
        self.assertEqual(len(model.by_type("IfcRelFillsElement")), 2)
        wall = model.by_type("IfcWall")[0]
        self.assertTrue(any(link.RelatingBuildingElement == wall and link.RelatedOpeningElement == opening
                            for link in model.by_type("IfcRelVoidsElement")))
        self.assertTrue(any(link.RelatingOpeningElement == opening and link.RelatedBuildingElement == window
                            for link in model.by_type("IfcRelFillsElement")))
        self.assertEqual(evidence["source"]["building_sha256"], _hash(building.to_dict()))
        self.assertEqual(evidence["window_capability"]["policy"]["name"], "source_facade_v1")
        self.assertEqual(evidence["counts"]["windows"], 1)
        self.assertEqual(evidence["counts"]["window_openings"], 1)
        self.assertEqual(evidence["counts"]["openings"], 2)
        report = self.check(building, layout)
        self.assertEqual(report["status"], "PASS", report)
        self.assertEqual(report["window_material"][0]["z_intervals_mm"], [[0, 900], [900, 2400], [2400, 2800]])
        self.assertEqual(report["full_facility_validation"], {"status": "UNKNOWN", "ok": False})
        self.assertEqual(report["engineering_acceptance"], "PENDING")
        plan = build_orthogonal_wall_plan(building.layout, layout, include_windows=True)
        void_xy = plan.windows[0].centerline.buffer(building.layout.wall_thickness_mm / 2 + 1,
                                                   cap_style=2, join_style=2)
        expected_volume = (plan.geometry.area * 2800 - plan.geometry.intersection(void_xy).area * 1500) / 1e9
        settings = ifcopenshell.geom.settings()
        settings.set(settings.USE_WORLD_COORDS, True)
        shape = ifcopenshell.geom.create_shape(settings, wall)
        geometry = shape.geometry
        vertices = np.asarray(geometry.verts).reshape(-1, 3)
        triangles = vertices[np.asarray(geometry.faces).reshape(-1, 3)]
        volume = abs(np.einsum("ij,ij->i", triangles[:, 0], np.cross(triangles[:, 1], triangles[:, 2])).sum()) / 6
        self.assertAlmostEqual(volume, expected_volume, places=7)
        for z, expected_material in ((.45, plan.geometry), (1.65, plan.geometry.difference(void_xy)), (2.6, plan.geometry)):
            with self.subTest(native_slice_m=z):
                self.assertEqual(_native_inside(triangles, (1., 0, z)),
                                 expected_material.covers(Point(1000, 0)))
        logger = ifcopenshell.validate.json_logger()
        ifcopenshell.validate.validate(model, logger, express_rules=True)
        self.assertEqual(logger.statements, [], logger.statements)

    def test_source_windows_keep_fresh_equipment_door_and_full_width_route_gates(self):
        building, layout, equipment, routing = self.source()
        evidence = self.export(building, layout, equipment, routing)
        report = self.check(building, layout, equipment, routing)
        self.assertEqual(report["status"], "PASS", report)
        self.assertEqual(report["counts"]["equipment"], 1)
        self.assertEqual(report["counts"]["routes"], 1)
        for name in ("rooms", "equipment", "door_approaches", "routes"):
            self.assertEqual(evidence["local_validation"][name]["status"], "PASS")
        self.assertEqual(evidence["source"]["equipment_sha256"], _hash(equipment.to_dict(building)))
        self.assertEqual(evidence["source"]["routing_sha256"], _hash(routing.to_dict()))

    def test_invalid_supplied_local_geometry_preserves_existing_destination_bytes(self):
        building, layout, equipment, routing = self.source()
        invalid_equipment = replace(equipment, placements=(replace(equipment.placements[0],
            rect=Rect(3800, 3500, 500, 500)),))
        invalid_routes = replace(routing, routes=(replace(routing.routes[0],
            points=((5050, 1410), (5050, 4000), (4000, 4000))),))
        for items, routes in ((invalid_equipment, routing), (equipment, invalid_routes)):
            with self.subTest(invalid_equipment=items is invalid_equipment):
                self.path.write_bytes(b"Existing destination must survive failed source validation")
                before = self.path.read_bytes()
                with self.assertRaises(ValueError):
                    self.export(building, layout, items, routes)
                self.assertEqual(self.path.read_bytes(), before)

    def test_multiple_windows_share_source_height_without_merging_native_identity_or_wall_voids(self):
        building, layout, _, _ = self.source()
        building = BuildingIR.from_mapping(replace(building, layout=replace(building.layout,
            windows=(*building.layout.windows, WindowSpec("left-window", "main", "left", 1200., 800.)))).to_dict())
        self.export(building, layout)
        report = self.check(building, layout)
        self.assertEqual(report["status"], "PASS", report)
        self.assertEqual(report["counts"]["windows"], 2)
        self.assertEqual(report["counts"]["openings"], 3)
        self.assertEqual({item["id"] for item in report["window_material"]}, {"source-window", "left-window"})

    def test_default_and_false_guard_source_windows_before_writing(self):
        building, layout, _, _ = self.source()
        for options in ({}, {"include_windows": False}):
            with self.subTest(options=options):
                with self.assertRaises(ValueError):
                    export_orthogonal_draft_ifc(self.path, building, layout, **options)
                self.assertFalse(self.path.exists())

    def test_default_false_output_contract_and_ifc_data_bytes_are_unchanged(self):
        building, layout, equipment, routing = _source()
        first = export_orthogonal_draft_ifc(self.path, building, layout, equipment, routing=routing)
        first_data = self.path.read_text(encoding="utf-8").split("DATA;", 1)[1]
        second = export_orthogonal_draft_ifc(self.path, building, layout, equipment, routing=routing,
                                            include_windows=False)
        second_data = self.path.read_text(encoding="utf-8").split("DATA;", 1)[1]
        self.assertEqual(first_data, second_data)
        self.assertEqual({key: value for key, value in first.items() if key != "artifact_sha256"},
                         {key: value for key, value in second.items() if key != "artifact_sha256"})
        self.assertNotIn("window_capability", second)
        self.assertNotIn("windows", second["counts"])
        default = validate_orthogonal_ifc(self.path, building, layout, equipment, routing=routing)
        explicit = validate_orthogonal_ifc(self.path, building, layout, equipment, routing=routing,
                                          include_windows=False)
        self.assertEqual(default, explicit)

    def test_strict_boolean_flag_and_unrepresentable_source_height_never_create_output(self):
        building, layout, _, _ = self.source()
        for value in (1, 0, None, "true", []):
            with self.subTest(flag=value):
                with self.assertRaisesRegex(ValueError, "include_windows"):
                    export_orthogonal_draft_ifc(self.path, building, layout, include_windows=value)
                self.assertFalse(self.path.exists())
        building = replace(building, layout=replace(building.layout, window_sill_mm=1301))
        with self.assertRaises(OrthogonalWindowCapabilityError) as caught:
            self.export(building, layout)
        self.assertEqual(caught.exception.to_dict()["status"], "UNKNOWN")
        self.assertFalse(self.path.exists())

    def test_opted_source_guards_precede_normalization_of_malformed_daylight_and_huge_sill(self):
        building, layout, _, _ = self.source()
        for spec in (replace(building.layout, rooms=(replace(building.layout.rooms[0], needs_daylight=""),
                                                       building.layout.rooms[1])),
                     replace(building.layout, window_sill_mm=10 ** 400)):
            with self.subTest(huge_sill=isinstance(spec.window_sill_mm, int)):
                with self.assertRaises(ValueError):
                    self.export(replace(building, layout=spec), layout)
                self.assertFalse(self.path.exists())

    def test_opted_valid_integer_source_values_keep_exact_caller_hash_and_common_capability(self):
        building, layout, _, _ = self.source()
        building = replace(building, layout=replace(building.layout,
            windows=(WindowSpec("source-window", "main", "bottom", 1000, 800),),
            window_sill_mm=900, window_height_mm=1500))
        before = building.to_dict(), layout.to_dict()
        capability = validate_orthogonal_window_capability(building.layout, layout).to_dict()
        evidence = self.export(building, layout)
        self.assertEqual(evidence["source"]["building_sha256"], _hash(building.to_dict()))
        self.assertEqual(evidence["window_capability"], capability)
        self.assertEqual(evidence["local_validation"]["window_capability"], capability)
        self.assertEqual(evidence["local_validation"]["rooms"]["window_capability"], capability)
        report = self.check(building, layout)
        self.assertEqual(report["status"], "PASS", report)
        self.assertEqual(report["source"], evidence["source"])
        self.assertEqual(report["window_capability"], capability)
        self.assertEqual((building.to_dict(), layout.to_dict()), before)

    def test_actual_vertical_cutout_and_automatic_facades_preserve_source_contours(self):
        explicit, layout, _, _ = self.source(side="left", sill=800, height=1600)
        cutout = BuildingIR.from_mapping({"layout": {"project_name": "Source cutout IFC window",
            "boundary": {"width": 6000, "height": 6000, "cutouts": [{"x": 0, "y": 3000, "width": 4000, "height": 3000}]},
            "entry_room": "room", "rooms": [{"id": "room", "target_area": 12, "min_area": 12, "max_area": 12}],
            "windows": [{"id": "cutout-window", "room_id": "room", "side": "top", "offset_mm": 1000, "width_mm": 800}]}})
        automatic = BuildingIR.from_mapping({"layout": {"project_name": "Source automatic IFC window",
            "boundary": {"width": 4000, "height": 3000}, "entry_room": "room",
            "rooms": [{"id": "room", "target_area": 12, "min_area": 12, "max_area": 12, "needs_daylight": True}],
            "external_entry": {"id": "entry", "room_id": "room", "side": "bottom", "offset_mm": 2000, "width_mm": 3700}}})
        for building, result, center in ((explicit, layout, (0, 1000)),
                (cutout, LayoutResult(1, {"room": Rect(0, 0, 4000, 3000)}), (1000, 3000)),
                (automatic, LayoutResult(1, {"room": Rect(0, 0, 4000, 3000)}), (2000, 3000))):
            with self.subTest(project=building.layout.project_name):
                self.export(building, result)
                report = self.check(building, result)
                self.assertEqual(report["status"], "PASS", report)
                model = ifcopenshell.open(self.path)
                profile = model.by_type("IfcWindow")[0].Representation.Representations[0].Items[0].SweptArea
                actual = Polygon([point.Coordinates for point in profile.OuterCurve.Points])
                self.assertEqual((actual.centroid.x, actual.centroid.y), center)
                self.assertEqual(report["counts"]["windows"], 1)

    def test_zero_sill_and_wall_head_boundary_have_only_real_nonempty_material_intervals(self):
        for sill, height, intervals in ((0, 1500, [[0, 1500], [1500, 2800]]),
                                        (900, 1900, [[0, 900], [900, 2800]])):
            with self.subTest(sill=sill, height=height):
                building, layout, _, _ = self.source(sill=sill, height=height)
                self.export(building, layout)
                report = self.check(building, layout)
                self.assertEqual(report["status"], "PASS", report)
                self.assertEqual(report["window_material"][0]["z_intervals_mm"], intervals)

    def test_ambiguous_facade_rejects_with_source_unknown_and_no_destination(self):
        building, layout, _, _ = self.source(side="right")
        building = replace(building, layout=replace(building.layout,
            boundary=BoundarySpec(6000, 6000, (Rect(2000, 2000, 4000, 4000),)),
            rooms=(replace(building.layout.rooms[0], required_adjacency=()),)), flows=())
        layout = replace(layout, placements={"main": layout.placements["main"]})
        with self.assertRaises(OrthogonalWindowCapabilityError) as caught:
            self.export(building, layout)
        self.assertEqual(caught.exception.to_dict()["status"], "UNKNOWN")
        self.assertFalse(self.path.exists())

    def test_equivalent_actual_sill_transforms_pass_and_false_or_invalid_readback_modes_fail(self):
        building, layout, _, _ = self.source()
        self.export(building, layout)
        model = ifcopenshell.open(self.path)
        for element in (model.by_type("IfcWindow")[0], _kind(model, "window-opening")):
            element.ObjectPlacement.RelativePlacement.Location.Coordinates = (0., 0., 0.)
            element.Representation.Representations[0].Items[0].Position.Location.Coordinates = (0., 0., 900.)
        model.write(self.path)
        self.assertEqual(self.check(building, layout)["status"], "PASS")
        for mode in (False, 1, None, "true"):
            with self.subTest(mode=mode):
                report = validate_orthogonal_ifc(self.path, building, layout, include_windows=mode,
                                                 expected_sha256=_file_hash(self.path))
                self.assertEqual(report["status"], "FAIL", report)

    def test_rehashed_sill_height_xy_geometry_and_scalar_forgeries_fail(self):
        building, layout, _, _ = self.source()
        mutations = ("window_sill", "opening_sill", "solid_sill", "context_z", "window_height",
                     "opening_height", "window_xy", "missing_body", "missing_window", "width", "identity",
                     "metadata_sill", "metadata_height", "status", "full_status", "engineering")
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                self.export(building, layout)
                model = ifcopenshell.open(self.path)
                window, opening = model.by_type("IfcWindow")[0], _kind(model, "window-opening")
                if mutation in ("window_sill", "opening_sill", "window_xy"):
                    target = opening if mutation == "opening_sill" else window
                    target.ObjectPlacement.RelativePlacement.Location.Coordinates = (1., 0., 900.) if mutation == "window_xy" else (0., 0., 901.)
                elif mutation == "solid_sill":
                    opening.Representation.Representations[0].Items[0].Position.Location.Coordinates = (0., 0., 1.)
                elif mutation == "context_z":
                    model.by_type("IfcGeometricRepresentationContext")[0].WorldCoordinateSystem.Location.Coordinates = (0., 0., 1.)
                elif mutation in ("window_height", "opening_height"):
                    target = window if mutation == "window_height" else opening
                    target.Representation.Representations[0].Items[0].Depth = 1501.
                elif mutation == "missing_body":
                    window.Representation = None
                elif mutation == "missing_window":
                    model.remove(window)
                elif mutation == "width":
                    window.OverallWidth = 801.
                elif mutation == "identity":
                    _property(window, "HostRoomId").NominalValue = model.create_entity("IfcLabel", "corner")
                else:
                    prop = {"metadata_sill": "WindowSillMM", "metadata_height": "WindowHeightMM", "status": "Status",
                            "full_status": "FullFacilityValidation", "engineering": "EngineeringAcceptance"}[mutation]
                    value = model.create_entity("IfcBoolean", True) if mutation.startswith("metadata_") else model.create_entity("IfcLabel", "PASS")
                    _property(model.by_type("IfcProject")[0], prop).NominalValue = value
                model.write(self.path)
                report = self.check(building, layout)
                self.assertEqual(report["status"], "FAIL", report)

    def test_rehashed_missing_wrong_void_fill_and_window_namespace_fail(self):
        building, layout, _, _ = self.source()
        for mutation in ("missing_void", "missing_fill", "wrong_wall", "wrong_opening", "wrong_fill", "guid", "void_guid", "fill_guid"):
            with self.subTest(mutation=mutation):
                self.export(building, layout)
                model = ifcopenshell.open(self.path)
                opening, window = _kind(model, "window-opening"), model.by_type("IfcWindow")[0]
                void = next(link for link in model.by_type("IfcRelVoidsElement") if link.RelatedOpeningElement == opening)
                fill = next(link for link in model.by_type("IfcRelFillsElement") if link.RelatingOpeningElement == opening)
                if mutation == "missing_void":
                    model.remove(void)
                elif mutation == "missing_fill":
                    model.remove(fill)
                elif mutation == "wrong_wall":
                    void.RelatingBuildingElement = model.by_type("IfcDoor")[0]
                elif mutation == "wrong_opening":
                    void.RelatedOpeningElement = _kind(model, "opening")
                elif mutation == "wrong_fill":
                    fill.RelatedBuildingElement = model.by_type("IfcDoor")[0]
                elif mutation in ("void_guid", "fill_guid"):
                    target = void if mutation == "void_guid" else fill
                    target.GlobalId = ifcopenshell.guid.new()
                else:
                    window.GlobalId = model.by_type("IfcDoor")[0].GlobalId
                model.write(self.path)
                self.assertEqual(self.check(building, layout)["status"], "FAIL")

    def test_rehashed_full_height_window_gap_fails_and_door_body_stays_unchanged(self):
        building, layout, _, _ = self.source()
        self.export(building, layout)
        model = ifcopenshell.open(self.path)
        plan = build_orthogonal_wall_plan(building.layout, layout, include_windows=True)
        source_band = plan.geometry
        wall = model.by_type("IfcWall")[0]
        door = model.by_type("IfcDoor")[0]
        self.assertEqual(door.Representation.Representations[0].Items[0].Depth, 2100)
        gap = plan.windows[0].centerline.buffer(building.layout.wall_thickness_mm / 2 + 1, cap_style=2, join_style=2)
        changed = source_band.difference(gap)
        wall.Representation.Representations[0].Items = tuple(_solid(model, polygon, 2800)
            for polygon in ([changed] if isinstance(changed, Polygon) else changed.geoms))
        model.write(self.path)
        report = self.check(building, layout)
        self.assertEqual(report["status"], "FAIL", report)
        self.assertTrue(any("material geometry" in issue for issue in report["issues"]), report)

    def test_source_window_and_door_same_id_have_distinct_stable_geometry_identities(self):
        building, layout, equipment, routing = _source()
        door_id = build_orthogonal_wall_plan(building.layout, layout).openings[0].id
        building = replace(building, layout=replace(building.layout,
            windows=(WindowSpec(door_id, "main", "bottom", 1000, 800),)))
        self.export(building, layout, equipment, routing)
        model = ifcopenshell.open(self.path)
        roots = model.by_type("IfcRoot")
        self.assertEqual(len({item.GlobalId for item in roots}), len(roots))
        identities = {(item.is_a(), _property(item, "Kind").NominalValue.wrappedValue, item.Name): item.GlobalId
                      for item in model.by_type("IfcElement")}
        changed = replace(building, layout=replace(building.layout,
            windows=(WindowSpec(door_id, "main", "bottom", 1400, 800),), window_sill_mm=700, window_height_mm=1700))
        self.export(changed, replace(layout, variant=9), equipment, routing)
        model = ifcopenshell.open(self.path)
        self.assertEqual(identities, {(item.is_a(), _property(item, "Kind").NominalValue.wrappedValue, item.Name): item.GlobalId
                                     for item in model.by_type("IfcElement")})
        self.assertEqual(self.check(changed, replace(layout, variant=9), equipment, routing)["status"], "PASS")


def _kind(model, kind):
    return next(item for item in model.by_type("IfcElement")
                if _property(item, "Kind").NominalValue.wrappedValue == kind)


def _solid(model, polygon, height):
    def curve(ring):
        return model.create_entity("IfcPolyline", Points=[model.create_entity("IfcCartesianPoint", Coordinates=point)
                                                         for point in ring.coords])
    values = {"ProfileType": "AREA", "OuterCurve": curve(polygon.exterior)}
    if polygon.interiors:
        values["InnerCurves"] = [curve(ring) for ring in polygon.interiors]
    profile = model.create_entity("IfcArbitraryProfileDefWithVoids" if polygon.interiors else "IfcArbitraryClosedProfileDef", **values)
    position = model.create_entity("IfcAxis2Placement3D", Location=model.create_entity("IfcCartesianPoint", Coordinates=(0., 0., 0.)))
    return model.create_entity("IfcExtrudedAreaSolid", SweptArea=profile, Position=position,
        ExtrudedDirection=model.create_entity("IfcDirection", DirectionRatios=(0., 0., 1.)), Depth=float(height))


def _native_inside(triangles, point):
    """Ray intersection parity against the actual tessellated IFC wall body."""
    origin, direction = np.asarray(point), np.asarray((1., .137, .019))
    hits = []
    for a, b, c in triangles:
        edge1, edge2 = b - a, c - a
        h = np.cross(direction, edge2)
        determinant = np.dot(edge1, h)
        if abs(determinant) < 1e-10:
            continue
        inverse = 1 / determinant
        s = origin - a
        u = inverse * np.dot(s, h)
        if not 0 <= u <= 1:
            continue
        q = np.cross(s, edge1)
        v = inverse * np.dot(direction, q)
        distance = inverse * np.dot(edge2, q)
        if v >= 0 and u + v <= 1 and distance > 1e-8:
            hits.append(round(float(distance), 8))
    return len(set(hits)) % 2 == 1

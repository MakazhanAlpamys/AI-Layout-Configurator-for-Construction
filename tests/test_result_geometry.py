import importlib.util
import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from layout_configurator.building import BuildingIR
from layout_configurator.equipment import EquipmentLayoutResult, place_equipment, validate_equipment_layout
from layout_configurator.export import export_dxf, export_pdf
from layout_configurator.flows import FlowRoutingResult, route_flows, validate_flow_routes
from layout_configurator.io import load_building_result, load_result, write_building_result, write_result
from layout_configurator.models import LayoutIR, LayoutResult, Rect
from layout_configurator.orthogonal import OrthogonalPolygon
from layout_configurator.validation import validate_layout
from layout_configurator.walls import build_wall_plan


class ResultGeometryTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)

    def api(self):
        self.assertIsNotNone(importlib.util.find_spec("layout_configurator.result_geometry"),
                             "Versioned result geometry contract is missing")
        from layout_configurator import result_geometry
        return result_geometry

    def test_legacy_rectangle_result_json_is_byte_compatible_and_restores_rect(self):
        self.api()
        spec = _program()
        result = LayoutResult(variant=2, objective_value=17, placements={"room": Rect(0, 0, 1000, 7000)})
        expected = {"variant": 2, "objective_value": 17, "rooms": {
            "room": {"x": 0, "y": 0, "width": 1000, "height": 7000, "area_m2": 7.0},
        }}
        self.assertEqual(result.to_dict(), expected)
        path = self.root / "layout.json"

        write_result(path, spec, result)

        self.assertEqual(path.read_text(encoding="utf-8"), json.dumps({"spec": spec.to_dict(), "layout": expected}, ensure_ascii=False, indent=2))
        self.assertEqual(load_result(path)[1].placements["room"], Rect(0, 0, 1000, 7000))
        self.assertIsInstance(load_result(path)[1].placements["room"], Rect)

    def test_versioned_polygon_result_roundtrip_preserves_vertices_and_real_area(self):
        self.api()
        shape = _shape()
        result = LayoutResult(variant=1, placements={"room": shape})
        expected = {"variant": 1, "objective_value": None, "geometry_version": "0.2", "units": "mm", "rooms": {
            "room": {"kind": "orthogonal", "units": "mm", "vertices": [[0, 0], [4000, 0], [4000, 1000], [1000, 1000], [1000, 4000], [0, 4000]], "area_m2": 7.0},
        }}

        self.assertEqual(result.to_dict(), expected)
        path = self.root / "layout.json"
        write_result(path, _program(), result)
        restored = load_result(path)[1]

        self.assertEqual(restored.placements["room"], shape)
        self.assertEqual(restored.to_dict(), expected)
        self.assertNotIn("width", expected["rooms"]["room"])
        self.assertFalse(restored.placements["room"].contains_point((2000, 2000)))

    def test_building_result_restores_mixed_geometry_without_losing_equipment_payload(self):
        self.api()
        building = BuildingIR.from_mapping({"layout": _program(two_rooms=True).to_dict()})
        result = LayoutResult(variant=1, placements={"room": _shape(), "corner": Rect(1000, 1000, 3000, 3000)})
        path = self.root / "building.json"

        write_building_result(path, building, result, EquipmentLayoutResult(()))
        restored_building, restored, equipment = load_building_result(path)

        self.assertEqual(restored_building.to_dict(), building.to_dict())
        self.assertEqual(restored.placements, {"room": _shape(), "corner": Rect(1000, 1000, 3000, 3000)})
        self.assertEqual(equipment.placements, ())

    def test_schema_rejects_unknown_fields_units_versions_nonfinite_values_and_wrong_derived_area(self):
        api = self.api()
        base = {"variant": 1, "objective_value": None, "rooms": {
            "room": {"x": 0, "y": 0, "width": 1000, "height": 7000, "area_m2": 7},
        }}
        for path, value in ((["extra"], 1), (["variant"], True), (["geometry_version"], "0.3"),
                            (["rooms", "room", "width"], "1000"), (["rooms", "room", "height"], 0),
                            (["rooms", "room", "x"], float("nan")), (["rooms", "room", "area_m2"], 16),
                            (["rooms", "room", "extra"], 1)):
            with self.subTest(path=path, value=value):
                raw = json.loads(json.dumps(base))
                parent = raw
                for key in path[:-1]:
                    parent = parent[key]
                parent[path[-1]] = value
                with self.assertRaises(ValueError):
                    api.parse_layout_result(raw)
        polygon = {**base, "geometry_version": "0.2", "units": "mm", "rooms": {
            "room": {**_shape().to_dict(), "area_m2": 7},
        }}
        for mutation in ({"units": "m"}, {"geometry_version": "0.1"}):
            with self.assertRaises(ValueError):
                api.parse_layout_result({**polygon, **mutation})
        polygon["rooms"]["room"]["area_m2"] = 16
        with self.assertRaisesRegex(ValueError, "area"):
            api.parse_layout_result(polygon)

    def test_geometry_evidence_uses_actual_polygon_for_area_bounds_cutouts_and_overlap_without_full_pass(self):
        api = self.api()
        spec = _program(two_rooms=True)
        result = LayoutResult(variant=1, placements={"room": _shape(), "corner": Rect(1000, 1000, 3000, 3000)})
        evidence = api.validate_result_geometry(spec, result)

        checks = {check.id: check.status for check in evidence.checks}
        self.assertEqual(checks["ROOM_AREA"], "PASS")
        self.assertEqual(checks["BOUNDARY"], "PASS")
        self.assertEqual(checks["ROOM_OVERLAP"], "PASS")
        self.assertEqual(checks["TOPOLOGY"], "UNKNOWN")
        self.assertEqual(checks["OPENINGS"], "UNKNOWN")
        self.assertEqual(checks["MIN_DIMENSION"], "UNKNOWN")
        self.assertFalse(evidence.ok)
        self.assertEqual(evidence.status, "UNKNOWN")
        self.assertIn("not engineering", evidence.to_dict()["scope"])
        overlap = LayoutResult(variant=1, placements={"room": _shape(), "corner": Rect(500, 500, 3000, 3000)})
        self.assertEqual(api.validate_result_geometry(spec, overlap).status, "FAIL")
        cutout = spec.to_dict()
        cutout["boundary"]["cutouts"] = [{"x": 1500, "y": 1500, "width": 1000, "height": 1000}]
        single = LayoutResult(variant=1, placements={"room": _shape()})
        single_spec = _program().to_dict()
        single_spec["boundary"]["cutouts"] = cutout["boundary"]["cutouts"]
        self.assertEqual(next(c.status for c in api.validate_result_geometry(LayoutIR.from_mapping(single_spec), single).checks if c.id == "BOUNDARY"), "PASS")
        self.assertEqual(api.validate_result_geometry(_program(), LayoutResult(1, {"room": _shape().translated(8000, 0)})).status, "FAIL")

    def test_rectangle_consumers_reject_polygon_with_explicit_capability_evidence_before_work(self):
        api = self.api()
        result = LayoutResult(variant=1, placements={"room": _shape()})
        spec = _program()
        building = BuildingIR.from_mapping({"layout": spec.to_dict()})
        outputs = (self.root / "out.dxf", self.root / "out.pdf")
        operations = (
            lambda: validate_layout(spec, result), lambda: build_wall_plan(spec, result),
            lambda: place_equipment(building, result),
            lambda: validate_equipment_layout(building, result, EquipmentLayoutResult(())),
            lambda: route_flows(building, result),
            lambda: validate_flow_routes(building, result, FlowRoutingResult(())),
            lambda: export_dxf(outputs[0], spec, result), lambda: export_pdf(outputs[1], spec, result),
        )
        for operation in operations:
            with self.subTest(operation=operation):
                with self.assertRaises(api.UnsupportedGeometryError) as caught:
                    operation()
                self.assertEqual(caught.exception.to_dict()["status"], "UNKNOWN")
                self.assertEqual(caught.exception.unsupported_rooms, ("room",))
                self.assertIn("orthogonal", str(caught.exception))
        self.assertTrue(all(not output.exists() for output in outputs))

    def test_viewer_entry_rejects_polygon_instead_of_drawing_undefined_rectangle_fields(self):
        api = self.api()
        from layout_configurator.ui import FacilityReviewSession, UiSession
        path = self.root / "layout.json"
        write_result(path, _program(), LayoutResult(1, {"room": _shape()}))
        with self.assertRaises(api.UnsupportedGeometryError):
            UiSession.from_input(path, self.root / "output")
        building_path = self.root / "building.json"
        building = BuildingIR.from_mapping({"layout": _program().to_dict()})
        write_building_result(building_path, building, LayoutResult(1, {"room": _shape()}), EquipmentLayoutResult(()))
        with self.assertRaises(api.UnsupportedGeometryError):
            FacilityReviewSession.from_input(building_path, self.root / "output")

    def test_writers_reject_invalid_result_before_creating_files(self):
        self.api()
        for result in (LayoutResult(True, {"room": Rect(0, 0, 1000, 7000)}),
                       LayoutResult(1, {"room": Rect(0, 0, 0, 7000)}),
                       LayoutResult(1, {1: Rect(0, 0, 1000, 7000)})):
            with self.subTest(result=result):
                path = self.root / "invalid.json"
                with self.assertRaises(ValueError):
                    write_result(path, _program(), result)
                self.assertFalse(path.exists())

    def test_rules_and_variant_ranking_report_explicit_unsupported_geometry(self):
        api = self.api()
        from layout_configurator.compare import variant_metrics
        from layout_configurator.norms import check_layout, load_ruleset
        result = LayoutResult(1, {"room": _shape()})
        with self.assertRaises(api.UnsupportedGeometryError):
            check_layout(_program(), result, load_ruleset("rules/kz_sn_3_02_02_2023_partial.yaml"))
        path = self.root / "result.json"
        write_result(path, _program(), result)
        with self.assertRaises(api.UnsupportedGeometryError):
            variant_metrics(path)

    def test_result_loaders_reject_duplicate_fields_and_malformed_envelopes_as_value_errors(self):
        self.api()
        path = self.root / "invalid.json"
        valid = {"spec": _program().to_dict(), "layout": {
            "variant": 1, "rooms": {"room": {"x": 0, "y": 0, "width": 1000, "height": 7000}},
        }}
        duplicate = json.dumps(valid).replace('"variant": 1', '"variant": 1, "variant": 2')
        for payload in (duplicate, "[]", json.dumps({"spec": {}}), json.dumps({"spec": {}, "layout": []})):
            with self.subTest(payload=payload):
                path.write_text(payload, encoding="utf-8")
                with self.assertRaises(ValueError):
                    load_result(path)

    def test_partial_evidence_does_not_pass_predicates_for_missing_geometry(self):
        api = self.api()
        evidence = api.validate_result_geometry(_program(), LayoutResult(1, {}))

        self.assertEqual(evidence.status, "FAIL")
        checks = {check.id: check.status for check in evidence.checks}
        self.assertEqual(checks["RESULT_IDENTITIES"], "FAIL")
        self.assertEqual(checks["ROOM_AREA"], "UNKNOWN")
        self.assertEqual(checks["BOUNDARY"], "UNKNOWN")
        self.assertEqual(checks["ROOM_OVERLAP"], "UNKNOWN")

    def test_polygon_inside_outer_bounds_still_fails_when_it_trespasses_a_cutout(self):
        api = self.api()
        raw = _program().to_dict()
        raw["boundary"]["cutouts"] = [{"x": 2000, "y": 300, "width": 500, "height": 400}]
        shape = _shape()
        self.assertLessEqual(shape.bounds_rect.right, raw["boundary"]["width"])
        self.assertLessEqual(shape.bounds_rect.top, raw["boundary"]["height"])

        evidence = api.validate_result_geometry(LayoutIR.from_mapping(raw), LayoutResult(1, {"room": shape}))

        boundary = next(check for check in evidence.checks if check.id == "BOUNDARY")
        self.assertEqual(boundary.status, "FAIL")
        self.assertIn("cutout", boundary.evidence[0])
        self.assertEqual(evidence.status, "FAIL")

    def test_all_ifc_exports_reject_polygon_with_operation_and_floor_scoped_evidence(self):
        api = self.api()
        from layout_configurator.building import StructuralGridSpec
        from layout_configurator.facility_floors import FacilityFloorsSpec, FloorEntry, FloorStack, VerticalCore
        from layout_configurator.ifc import export_building_ifc, export_facility_floors_ifc, export_ifc, export_multifloor_ifc
        from layout_configurator.multifloor import FloorSpec, MultiFloorResult, MultiFloorSpec, MultiFloorValidationReport, VerticalCoreSpec
        spec = _program()
        building = BuildingIR.from_mapping({"layout": spec.to_dict()})
        candidate = _rectangular_candidate(building)
        self.assertTrue(candidate.facility_report.ok)
        polygon = LayoutResult(1, {"room": _shape()})
        legacy = MultiFloorSpec("Legacy geometry", (FloorSpec(0, spec, 0), FloorSpec(10, spec, 4500)),
                               (VerticalCoreSpec("core", ("room", "room")),))
        legacy_result = MultiFloorResult(legacy, (candidate.result, polygon), MultiFloorValidationReport(()))
        grid = StructuralGridSpec((0, 3500, 10000), (0, 2000, 10000))
        facility = FacilityFloorsSpec("Unified geometry", 4500,
                                     (FloorEntry(0, 0, building, "ground.json"), FloorEntry(10, 4500, building, "upper.json")),
                                     (VerticalCore("core", "goods_lift", {0: "room", 10: "room"}),), (), grid)
        stack = FloorStack(1, {10: replace(candidate, result=polygon), 0: candidate}, ())
        operations = (
            ("IFC export", lambda path: export_ifc(path, spec, polygon)),
            ("Building IFC export", lambda path: export_building_ifc(path, building, polygon, candidate.equipment)),
            ("Legacy multi-floor IFC export floor 10", lambda path: export_multifloor_ifc(path, legacy, legacy_result)),
            ("Unified facility IFC export floor 10", lambda path: export_facility_floors_ifc(path, facility, stack)),
        )
        for index, (name, operation) in enumerate(operations):
            with self.subTest(operation=name):
                output = self.root / f"unsupported_{index}.ifc"
                with self.assertRaises(api.UnsupportedGeometryError) as caught:
                    operation(output)
                self.assertEqual(caught.exception.operation, name)
                self.assertEqual(caught.exception.unsupported_rooms, ("room",))
                self.assertEqual(caught.exception.to_dict()["status"], "UNKNOWN")
                self.assertFalse(output.exists())


def _shape():
    return OrthogonalPolygon(((0, 0), (4000, 0), (4000, 1000), (1000, 1000), (1000, 4000), (0, 4000)))


def _rectangular_candidate(building):
    from layout_configurator.facility import default_facility_profile, validate_building
    from layout_configurator.facility_generation import FeasibleFacilityCandidate
    result = LayoutResult(1, {"room": Rect(0, 0, 3500, 2000)})
    equipment = EquipmentLayoutResult(())
    layout_report = validate_layout(building.layout, result)
    equipment_report = validate_equipment_layout(building, result, equipment)
    routes = route_flows(building, result, equipment)
    flow_report = validate_flow_routes(building, result, routes, equipment)
    facility_report = validate_building(building, result, equipment, routes, flow_report,
                                       profile=default_facility_profile(), equipment_report=equipment_report)
    return FeasibleFacilityCandidate(1, 1, 1, 1, result, layout_report, equipment, equipment_report,
                                     routes, flow_report, facility_report)


def _program(two_rooms=False):
    rooms = [{"id": "room", "type": "production", "target_area": 7, "min_width": 1000, "min_depth": 1000}]
    if two_rooms:
        rooms.append({"id": "corner", "type": "room", "target_area": 9, "min_width": 1000, "min_depth": 1000})
    return LayoutIR.from_mapping({"project_name": "geometry result", "grid_mm": 100,
                                  "boundary": {"width": 10000, "height": 10000}, "entry_room": "room", "rooms": rooms})


if __name__ == "__main__":
    unittest.main()

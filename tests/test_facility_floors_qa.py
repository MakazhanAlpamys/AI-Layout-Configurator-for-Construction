"""Independent QA detects altered floor programs and receiving-tool geometry."""

import contextlib
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import shutil
import tempfile
import unittest

import ifcopenshell

from layout_configurator.cli import main
from layout_configurator.facility import default_facility_profile, load_facility_profile
from layout_configurator.facility_floors import load_facility_floors


class FacilityFloorsQATests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.directory.cleanup)
        cls.root = Path(cls.directory.name)
        cls.program = {
            "layout": {"project_name": "QA source floor", "boundary": {"width": 9000, "height": 6000},
                       "grid_mm": 100, "entry_room": "core",
                       "rooms": [{"id": room, "area": 9, "min_area": 9, "max_area": 9,
                                  "min_width": 3000, "min_depth": 3000,
                                  "required_adjacency": ["core"] if room != "core" else []}
                                 for room in ("core", "process", "support")]},
            "zones": [{"id": "production", "type": "process", "room_ids": ["process"]}],
            "equipment": [{"id": "machine", "type": "machine", "room_id": "process",
                           "width_mm": 500, "depth_mm": 500}],
            "flows": [{"id": "transfer", "type": "material", "from_ids": ["machine"],
                       "to_ids": ["support"], "minimum_clear_width_mm": 400}],
        }
        for name in ("ground.json", "upper.json"):
            (cls.root / name).write_text(json.dumps(cls.program), encoding="utf-8")
        cls.source = cls.root / "source.json"
        cls.source.write_text(json.dumps({"facility_multi_floor": {
            "project_name": "Independent QA stack", "floor_height_mm": 4500,
            "structural_grid": {"axes_x_mm": [0, 3000, 6000, 9000], "axes_y_mm": [0, 3000, 6000]},
            "floors": [{"level": 0, "elevation_mm": 0, "program": "ground.json"},
                       {"level": 1, "elevation_mm": 4500, "program": "upper.json"}],
            "vertical_cores": [{"id": "goods_core", "kind": "goods_lift", "rooms": {"0": "core", "1": "core"}}],
            "cross_floor_flows": [{"id": "samples", "type": "material",
                                   "from": {"level": 0, "id": "machine"},
                                   "to": {"level": 1, "id": "machine"}, "via": ["goods_core"],
                                   "minimum_clear_width_mm": 400}],
        }}), encoding="utf-8")
        cls.baseline = cls.root / "baseline"
        cls._generate(cls.baseline)

    @classmethod
    def _generate(cls, output, *options):
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()) as error:
            code = main(["generate-facility-floors", str(cls.source), "--output", str(output),
                         "--variants", "1", "--max-attempts", "3", "--time-limit", "5",
                         "--seed", "1", "--room-solver", "monolithic", *options])
        if code != 0:
            raise AssertionError(f"Real six-room QA fixture failed: {error.getvalue()}")

    def setUp(self):
        self.working = tempfile.TemporaryDirectory()
        self.addCleanup(self.working.cleanup)
        self.output = Path(self.working.name) / "bundle"
        shutil.copytree(self.baseline, self.output)
        for name in ("ground.json", "upper.json"):
            (self.root / name).write_text(json.dumps(self.program), encoding="utf-8")
        self.spec = load_facility_floors(self.source)

    def api(self):
        self.assertIsNotNone(importlib.util.find_spec("layout_configurator.facility_floors_qa"),
                             "Independent facility-stack QA is missing")
        from layout_configurator.facility_floors_qa import validate_facility_floors_bundle
        return validate_facility_floors_bundle

    def check(self):
        return self.api()(self.spec, self.output, expected_stacks=1)

    def assert_failed(self, report, check_id):
        self.assertFalse(report.ok, report.to_dict())
        self.assertTrue(any(check["id"] == check_id and check["status"] == "FAIL"
                            for check in report.to_dict()["checks"]), report.to_dict())

    def edit_report(self, edit):
        path = self.output / "multi_floor_report.json"
        raw = json.loads(path.read_text(encoding="utf-8"))
        edit(raw)
        path.write_text(json.dumps(raw), encoding="utf-8")

    def edit_ifc(self, edit, *, refresh_hash=True):
        path = self.output / "facility_stack_01.ifc"
        model = ifcopenshell.open(path)
        edit(model)
        model.write(str(path))
        if refresh_hash:
            self.edit_report(lambda raw: raw["stacks"][0]["unified_ifc"].update(
                sha256=hashlib.sha256(path.read_bytes()).hexdigest()))

    def test_real_six_room_bundle_passes_with_fresh_floor_and_ifc_geometry_evidence(self):
        report = self.check()

        self.assertTrue(report.ok, report.to_dict())
        checks = {check["id"]: check["status"] for check in report.to_dict()["checks"]}
        for name in ("SOURCE_PROGRAMS", "FLOOR_BUNDLES", "VERTICAL_CHECKS", "IFC_EXPRESS",
                     "IFC_SPATIAL", "IFC_ROOM_GEOMETRY", "IFC_GRID", "IFC_SEMANTICS", "IFC_HASHES"):
            self.assertEqual(checks[name], "PASS")
        self.assertTrue(report.to_dict()["artifact_hashes"])

    def test_reported_pass_does_not_hide_changed_core_alignment_or_grid(self):
        path = self.output / "floor_01/building_01.json"
        raw = json.loads(path.read_text(encoding="utf-8"))
        raw["layout"]["rooms"]["core"]["x"] += 100
        path.write_text(json.dumps(raw), encoding="utf-8")
        self.edit_report(lambda raw: raw.update(ok=True))

        self.assert_failed(self.check(), "VERTICAL_CHECKS")

    def test_saved_or_original_program_change_cannot_be_accepted_from_hash_or_reported_pass(self):
        path = self.output / "floor_00/building_01.json"
        raw = json.loads(path.read_text(encoding="utf-8"))
        raw["spec"]["layout"]["rooms"][0]["type"] = "different process"
        path.write_text(json.dumps(raw), encoding="utf-8")
        self.assert_failed(self.check(), "SOURCE_PROGRAMS")
        shutil.copyfile(self.baseline / "floor_00/building_01.json", path)
        source = self.root / "ground.json"
        raw = json.loads(source.read_text(encoding="utf-8"))
        raw["layout"]["rooms"][0]["type"] = "changed source"
        source.write_text(json.dumps(raw), encoding="utf-8")
        self.assert_failed(self.check(), "SOURCE_PROGRAMS")

    def test_shifted_ifc_axis_fails_even_when_metadata_and_updated_hash_say_pass(self):
        def shift(model):
            for point in model.by_type("IfcGrid")[0].UAxes[0].AxisCurve.Points:
                point.Coordinates = (point.Coordinates[0], point.Coordinates[1] + 100)
        self.edit_ifc(shift)

        report = self.check()
        self.assert_failed(report, "IFC_GRID")
        self.assertTrue(all(check["status"] == "PASS" for check in report.to_dict()["checks"]
                            if check["id"] == "IFC_HASHES"))

    def test_changed_storey_elevation_is_rejected_independently_of_metadata(self):
        self.edit_ifc(lambda model: setattr(model.by_type("IfcBuildingStorey")[1], "Elevation", 4700.0))

        self.assert_failed(self.check(), "IFC_SPATIAL")

    def test_notched_ifc_space_with_same_bbox_does_not_match_canonical_rectangle(self):
        def notch(model):
            solid = model.by_type("IfcSpace")[0].Representation.Representations[0].Items[0]
            points = solid.SweptArea.OuterCurve.Points
            width, depth = points[2].Coordinates
            outline = ((0, 0), (width, 0), (width, depth), (width / 2, depth),
                       (width / 2, depth / 2), (0, depth / 2), (0, 0))
            solid.SweptArea.OuterCurve.Points = tuple(model.create_entity("IfcCartesianPoint", Coordinates=(float(x), float(y)))
                                                    for x, y in outline)
        self.edit_ifc(notch)

        self.assert_failed(self.check(), "IFC_ROOM_GEOMETRY")

    def test_duplicate_qualified_semantic_identity_is_rejected(self):
        def duplicate(model):
            properties = [prop for prop in model.by_type("IfcPropertySingleValue") if prop.Name == "SemanticId"]
            properties[1].NominalValue = model.create_entity("IfcLabel", properties[0].NominalValue.wrappedValue)
        self.edit_ifc(duplicate)

        self.assert_failed(self.check(), "IFC_SEMANTICS")

    def test_qualified_identity_must_keep_its_typed_floor_and_local_id(self):
        def wrong_level(model):
            space = model.by_type("IfcSpace")[-1]
            identity = next(relation.RelatingPropertyDefinition for relation in space.IsDefinedBy
                            if relation.RelatingPropertyDefinition.Name == "Pset_FacilityIdentity")
            level = next(prop for prop in identity.HasProperties if prop.Name == "Level")
            level.NominalValue = model.create_entity("IfcInteger", 999)
        self.edit_ifc(wrong_level)

        self.assert_failed(self.check(), "IFC_SEMANTICS")

    def test_grid_must_be_referenced_by_every_declared_storey(self):
        self.edit_ifc(lambda model: model.remove(model.by_type("IfcRelReferencedInSpatialStructure")[0]))

        self.assert_failed(self.check(), "IFC_GRID")

    def test_physical_storey_placement_cannot_hide_behind_correct_elevation_metadata(self):
        def move(model):
            storey = model.by_type("IfcBuildingStorey")[1]
            storey.ObjectPlacement.RelativePlacement.Location.Coordinates = (0.0, 0.0, 4600.0)
        self.edit_ifc(move)

        self.assert_failed(self.check(), "IFC_SPATIAL")

    def test_strict_express_rules_detect_invalid_positive_length_when_report_says_pass(self):
        self.edit_ifc(lambda model: setattr(model.by_type("IfcDoor")[0], "OverallWidth", -100.0))

        self.assert_failed(self.check(), "IFC_EXPRESS")

    def test_missing_floor_artifact_and_stale_ifc_hash_fail_without_trusting_readback(self):
        (self.output / "floor_00/building_01.pdf").write_bytes(b"corrupt PDF artifact")
        self.assert_failed(self.check(), "FLOOR_BUNDLES")
        self.edit_ifc(lambda model: setattr(model.by_type("IfcProject")[0], "Name", "Altered"), refresh_hash=False)
        self.assert_failed(self.check(), "IFC_HASHES")

    def test_explicit_default_profile_override_requires_exact_persisted_profile(self):
        api = self.api()
        raw = default_facility_profile().to_dict()
        raw["name"] = "Explicit project override"
        path = self.root / "override.json"
        path.write_text(json.dumps(raw), encoding="utf-8")
        profile = load_facility_profile(path)
        output = Path(self.working.name) / "override_bundle"
        self._generate(output, "--profile", str(path))

        accepted = api(self.spec, output, default_profile=profile)
        self.assertTrue(accepted.ok, accepted.to_dict())
        self.assert_failed(api(self.spec, output), "SOURCE_PROFILES")

    def test_stack_count_and_malformed_report_return_failed_evidence(self):
        api = self.api()
        self.assert_failed(api(self.spec, self.output, expected_stacks=2), "REPORT_CONTRACT")
        (self.output / "multi_floor_report.json").write_text("[]", encoding="utf-8")
        self.assert_failed(self.check(), "REPORT_CONTRACT")

    def test_boolean_stack_identity_is_not_coerced_to_integer_one(self):
        self.edit_report(lambda raw: raw["stacks"][0].update(stack=True))

        self.assert_failed(self.check(), "REPORT_CONTRACT")

    def test_unreported_floor_variant_cannot_be_hidden_by_expected_stack_count(self):
        shutil.copyfile(self.output / "floor_00/building_01.json", self.output / "floor_00/building_99.json")

        self.assert_failed(self.check(), "FLOOR_BUNDLES")

    def test_malformed_ifc_returns_failed_readback_instead_of_escaping_parser_error(self):
        path = self.output / "facility_stack_01.ifc"
        path.write_text("invalid IFC exchange data", encoding="utf-8")
        self.edit_report(lambda raw: raw["stacks"][0]["unified_ifc"].update(
            sha256=hashlib.sha256(path.read_bytes()).hexdigest()))

        report = self.check()

        self.assert_failed(report, "IFC_READBACK")
        self.assertTrue(any("parse" in evidence.lower() or "header" in evidence.lower()
                            for check in report.checks if check.id == "IFC_READBACK"
                            for evidence in check.evidence))
        self.assertFalse(any(check.id == "IFC_EXPRESS" and check.ok for check in report.checks))

    def test_malformed_floor_ifc_returns_failed_floor_evidence(self):
        (self.output / "floor_00/building_01.ifc").write_text("invalid IFC exchange data", encoding="utf-8")

        self.assert_failed(self.check(), "FLOOR_BUNDLES")


if __name__ == "__main__":
    unittest.main()

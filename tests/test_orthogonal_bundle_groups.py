"""Declared native IFC groups require explicit fresh persisted source QA."""

from dataclasses import replace
import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from layout_configurator.building import StructuralGridSpec, ZoneSpec
from layout_configurator.facility import FacilityProfile
from layout_configurator.orthogonal_bundle import INVENTORY, write_orthogonal_bundle
from layout_configurator.orthogonal_bundle_qa import validate_orthogonal_bundle
from layout_configurator.models import WindowSpec
from test_orthogonal_bundle import _copy_pristine_bundle, _restore_bundle_files
from test_orthogonal_pdf import _fixture


class OrthogonalGroupBundleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cache = {}

    @classmethod
    def tearDownClass(cls):
        cls.cache.clear()

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.path = self.root / "bundle"
        self.building, self.layout, self.equipment, self.routing = _fixture()
        self.building = replace(self.building,
            zones=(ZoneSpec("parent", "process", ("main",)),
                   ZoneSpec("child", "support", ("corner",), parent_zone_id="parent")),
            flows=(replace(self.building.flows[0], type="material", stage="declared_transfer"),))
        self.profile = FacilityProfile.from_mapping({"name": "Declared IFC group project", "version": "P01",
            "provenance": {"source": "Repository-owned project brief"}, "drawing": {"revision": "P01"},
            "rules": [{"id": "declared_material", "kind": "required_flow_types", "title": "Material flow",
                "source": "Project brief P01", "edition": "P01", "effective_date": "2026-10-13",
                "evidence": ["Declared project requirement"], "parameters": {"flow_types": ["material"]}}]})

    def write(self, **options):
        return _copy_pristine_bundle(self.cache, self.path, write_orthogonal_bundle,
            self.building, self.layout, self.equipment, routing=self.routing, profile=self.profile, **options)

    def check(self, **options):
        return validate_orthogonal_bundle(self.path, expected_profile=self.profile, **options)

    def read(self, name):
        return json.loads((self.path / name).read_text(encoding="utf-8"))

    def save(self, name, value):
        (self.path / name).write_text(json.dumps(value, sort_keys=True, ensure_ascii=False,
            separators=(",", ":")), encoding="utf-8")

    def snapshot(self):
        return tuple((path.name, path.read_bytes()) for path in sorted(self.path.iterdir()))

    def rehash_ifc(self):
        manifest = self.read("manifest.json")
        record = manifest["artifacts"]["ifc"]
        record["sha256"] = hashlib.sha256((self.path / record["file"]).read_bytes()).hexdigest()
        index = self.read(record["index"])
        index["artifact_sha256"] = record["sha256"]
        self.save(record["index"], index)
        record["index_sha256"] = hashlib.sha256((self.path / record["index"]).read_bytes()).hexdigest()
        self.save("manifest.json", manifest)

    def assert_failed(self, report, identifier):
        self.assertEqual(report["status"], "FAIL", report)
        self.assertFalse(report["ok"])
        self.assertEqual(next(row for row in report["checks"] if row["id"] == identifier)["status"], "FAIL", report)
        self.assertEqual(report["full_facility_validation"], {"status": "UNKNOWN", "ok": False})
        self.assertEqual(report["engineering_acceptance"], "PENDING")

    def test_opted_native_groups_bind_current_source_and_actual_ifc_readback(self):
        from layout_configurator.orthogonal_groups import validate_orthogonal_groups
        before = copy.deepcopy(self.building.to_dict())
        manifest = self.write(include_groups=True)
        capability = validate_orthogonal_groups(self.building, self.layout, self.equipment, routing=self.routing).to_dict()
        self.assertEqual({path.name for path in self.path.iterdir()}, INVENTORY)
        self.assertIn("group_capability", manifest)
        self.assertEqual(manifest["group_capability"], capability)
        self.assertEqual(self.read("building.json"), before)
        self.assertEqual(self.building.to_dict(), before)
        groups = {(row["kind"], row["id"]): row for row in capability["groups"]}
        self.assertEqual(groups["zone", "parent"]["members"], [{"kind": "room", "id": "main"}, {"kind": "zone", "id": "child"}])
        self.assertEqual(groups["zone", "child"]["members"], [{"kind": "room", "id": "corner"}])
        self.assertEqual([member for member in groups["flow-system", "process"]["members"] if member["kind"] == "equipment"],
            [{"kind": "equipment", "id": "source"}])
        self.assertEqual(groups["flow-system", "process"]["declaration"], self.building.flows[0].to_dict())
        self.assertEqual(len([member for member in groups["flow-system", "process"]["members"] if member["kind"] == "route"]), 1)
        self.assertNotIn("group_capability", self.read("dxf-index.json"))
        self.assertNotIn("group_capability", self.read("pdf-index.json"))
        self.assertEqual(manifest["projection_scope"]["zones"]["native_ifc_projection"], "SOURCE_BOUND_DECLARED_GROUPS")
        self.assertEqual(manifest["projection_scope"]["process_systems"]["network_engineering"], "UNKNOWN")
        report = self.check(include_groups=True)
        self.assertEqual(report["status"], "PASS", report)
        self.assertEqual(report["group_capability"], manifest["group_capability"])
        self.assertEqual(report["readbacks"]["ifc"]["group_capability"], manifest["group_capability"])
        self.assertEqual(report["readbacks"]["ifc"]["counts"]["zones"], 2)
        self.assertEqual(report["readbacks"]["ifc"]["counts"]["process_systems"], 1)
        self.assertEqual(report["readbacks"]["ifc"]["counts"]["group_relations"], 3)
        self.assertEqual(report["readbacks"]["ifc_express"]["statements"], [])
        self.assertEqual(manifest["full_facility_validation"], {"status": "UNKNOWN", "ok": False})
        self.assertEqual(manifest["engineering_acceptance"], "PENDING")

    def test_group_flags_are_strict_and_metadata_never_grants_default_reader_permission(self):
        for value in (0, 1, None, "true", [], {}):
            with self.subTest(value=value):
                with self.assertRaisesRegex(ValueError, "include_groups"):
                    write_orthogonal_bundle(self.path, self.building, self.layout, self.equipment,
                        routing=self.routing, profile=self.profile, include_groups=value)
                self.assertFalse(self.path.exists())
                self.assertEqual(self.check(include_groups=value)["status"], "FAIL")
        self.write(include_groups=True)
        self.assertEqual(self.check()["status"], "FAIL")

    def test_original_malformed_group_values_reject_before_serialization_or_staging(self):
        for building in (replace(self.building, zones=(replace(self.building.zones[0], airlock="false"), self.building.zones[1])),
                replace(self.building, zones=(replace(self.building.zones[0], pressure_pa=True), self.building.zones[1])),
                replace(self.building, flows=(replace(self.building.flows[0], required="false"),)),
                replace(self.building, flows=(replace(self.building.flows[0], minimum_clear_width_mm=float("nan")),))):
            with self.subTest(building=repr(building)):
                with self.assertRaises(ValueError):
                    write_orthogonal_bundle(self.path, building, self.layout, self.equipment,
                        routing=self.routing, profile=self.profile, include_groups=True)
                self.assertFalse(self.path.exists())
                self.assertFalse(list(self.root.glob(".orthogonal-draft-*")))

    def test_noncanonical_typed_group_numbers_are_rejected_without_silent_source_normalization(self):
        for building in (replace(self.building, zones=(replace(self.building.zones[0], pressure_pa=10), self.building.zones[1])),
                replace(self.building, flows=(replace(self.building.flows[0], minimum_clear_width_mm=800),))):
            for options in ({}, {"include_groups": True}):
                with self.subTest(building=repr(building), options=options):
                    with self.assertRaisesRegex(ValueError, "canonical values"):
                        write_orthogonal_bundle(self.path, building, self.layout, self.equipment,
                            routing=self.routing, profile=self.profile, **options)
                    self.assertFalse(self.path.exists())
                    self.assertFalse(list(self.root.glob(".orthogonal-draft-*")))

    def test_rehashed_metadata_and_source_declarations_require_fresh_group_capability(self):
        self.write(include_groups=True)
        snapshot = self.snapshot()
        for branch in ("manifest", "ifc-index", "ifc-local", "source", "scope"):
            with self.subTest(branch=branch):
                _restore_bundle_files(self.path, snapshot)
                expected = "SOURCE_CONTRACT"
                if branch == "source":
                    source = self.read("building.json")
                    source["zones"][0]["airlock"] = "false"
                    self.save("building.json", source)
                    manifest = self.read("manifest.json")
                    manifest["sources"]["building"].update(
                        sha256=hashlib.sha256((self.path / "building.json").read_bytes()).hexdigest(),
                        canonical_sha256=hashlib.sha256(json.dumps(source, sort_keys=True, ensure_ascii=False,
                            separators=(",", ":")).encode()).hexdigest())
                    self.save("manifest.json", manifest)
                elif branch == "scope":
                    manifest = self.read("manifest.json")
                    manifest["projection_scope"]["process_systems"]["network_engineering"] = "PASS"
                    self.save("manifest.json", manifest)
                elif branch == "manifest":
                    manifest = self.read("manifest.json")
                    manifest["group_capability"]["policy"]["physical_container_validation"] = "PASS"
                    self.save("manifest.json", manifest)
                else:
                    index = self.read("ifc-index.json")
                    target = index["group_capability"] if branch == "ifc-index" else index["local_validation"]["group_capability"]
                    target["groups"][0]["members"] = []
                    self.save("ifc-index.json", index)
                    self.rehash_ifc()
                    expected = "ARTIFACT_INDEX"
                self.assert_failed(self.check(include_groups=True), expected)

    def test_rehashed_actual_native_group_membership_and_relations_cannot_substitute_semantics(self):
        import ifcopenshell
        self.write(include_groups=True)
        snapshot = self.snapshot()
        for branch in ("missing_zone", "missing_system", "missing_member", "wrong_endpoint", "duplicate_relation", "changed_id"):
            with self.subTest(branch=branch):
                _restore_bundle_files(self.path, snapshot)
                model = ifcopenshell.open(str(self.path / "geometry.ifc"))
                zones, systems = model.by_type("IfcZone"), model.by_type("IfcSystem")
                relations = model.by_type("IfcRelAssignsToGroup")
                if branch == "missing_zone":
                    model.remove(zones[0])
                elif branch == "missing_system":
                    model.remove(systems[0])
                elif branch == "missing_member":
                    relation = next(row for row in relations if row.RelatingGroup == systems[0])
                    relation.RelatedObjects = relation.RelatedObjects[:1]
                elif branch == "wrong_endpoint":
                    relation = next(row for row in relations if row.RelatingGroup == systems[0])
                    equipment = next(row for row in model.by_type("IfcBuildingElementProxy") if row.Name == "rotated")
                    source = next(row for row in relation.RelatedObjects if row.Name == "source")
                    relation.RelatedObjects = tuple(row for row in relation.RelatedObjects if row != source) + (equipment,)
                elif branch == "duplicate_relation":
                    relation = next(row for row in relations if row.RelatingGroup == systems[0])
                    duplicate = model.create_entity("IfcRelAssignsToGroup", GlobalId=ifcopenshell.guid.new(),
                        RelatedObjects=relation.RelatedObjects, RelatingGroup=relation.RelatingGroup)
                    self.assertNotEqual(duplicate.id(), relation.id())
                else:
                    zones[0].GlobalId = ifcopenshell.guid.new()
                model.write(str(self.path / "geometry.ifc"))
                self.rehash_ifc()
                self.assert_failed(self.check(include_groups=True), "IFC_READBACK")

    def test_rehashed_ifc_group_index_counts_must_match_exact_declared_memberships(self):
        self.write(include_groups=True)
        snapshot = self.snapshot()
        for name in ("zones", "process_systems", "group_relations"):
            with self.subTest(name=name):
                _restore_bundle_files(self.path, snapshot)
                index = self.read("ifc-index.json")
                index["counts"][name] += 1
                self.save("ifc-index.json", index)
                self.rehash_ifc()
                self.assert_failed(self.check(include_groups=True), "IFC_READBACK")

    def test_empty_declared_groups_and_missing_optional_geometry_preserve_honest_native_inventory(self):
        self.building = replace(self.building, zones=(), flows=())
        self.routing = None
        self.profile = FacilityProfile.from_mapping(replace(self.profile, rules=(replace(self.profile.rules[0], kind="flow_separation",
            title="Declared source flow separation", parameters={"incompatible_flow_type_pairs": [["material", "people"]]}),)).to_dict())
        manifest = self.write(include_groups=True)
        self.assertEqual(manifest["group_capability"]["groups"], [])
        report = self.check(include_groups=True)
        self.assertEqual(report["status"], "PASS", report)
        self.assertEqual({name: report["readbacks"]["ifc"]["counts"][name]
            for name in ("zones", "process_systems", "group_relations")}, {"zones": 0, "process_systems": 0, "group_relations": 0})
        self.assertEqual(report["readbacks"]["ifc_express"]["statements"], [])

    def test_unsupplied_equipment_endpoint_is_declared_unprojected_and_never_invented(self):
        self.equipment = None
        self.routing = None
        manifest = self.write(include_groups=True)
        capability = manifest["group_capability"]
        system = next(row for row in capability["groups"] if row["kind"] == "flow-system")
        self.assertEqual(capability["states"], {"equipment": "NOT_SUPPLIED", "routing": "NOT_SUPPLIED"})
        self.assertEqual(system["members"], [])
        self.assertEqual(system["unprojected_equipment_ids"], ["source"])
        report = self.check(include_groups=True)
        self.assertEqual(report["status"], "UNKNOWN", report)
        self.assertEqual(report["readbacks"]["ifc"]["status"], "PASS")
        self.assertEqual(report["readbacks"]["ifc"]["counts"]["process_systems"], 1)
        self.assertEqual(report["readbacks"]["ifc"]["counts"]["group_relations"], 2)
        self.assertEqual(next(row for row in report["readbacks"]["ifc"]["groups"] if row["kind"] == "flow-system")["members"], [])
        self.assertEqual(report["readbacks"]["ifc_express"]["statements"], [])

    def test_group_opt_in_does_not_enable_windows_or_grid_and_combined_capabilities_stay_independent(self):
        self.building = replace(self.building,
            layout=replace(self.building.layout, windows=(WindowSpec("source_window", "main", "bottom", 1000., 800.),)),
            structural_grid=StructuralGridSpec((0., 2050.5, 6000.), (0., 3100.25, 6000.)))
        with self.assertRaises(ValueError):
            write_orthogonal_bundle(self.path, self.building, self.layout, self.equipment,
                routing=self.routing, profile=self.profile, include_groups=True)
        self.assertFalse(self.path.exists())
        manifest = self.write(include_windows=True, include_grid=True, include_groups=True)
        report = self.check(include_windows=True, include_grid=True, include_groups=True)
        self.assertEqual(report["status"], "PASS", report)
        self.assertEqual(report["group_capability"], manifest["group_capability"])
        self.assertEqual(report["readbacks"]["ifc"]["counts"]["windows"], 1)
        self.assertEqual(report["readbacks"]["ifc"]["counts"]["grid_axes"], 6)
        self.assertEqual(report["readbacks"]["ifc"]["counts"]["group_relations"], 3)
        self.assertEqual(report["readbacks"]["ifc_express"]["statements"], [])
        for options in ({"include_windows": True, "include_grid": True}, {"include_windows": True, "include_groups": True},
                {"include_grid": True, "include_groups": True}):
            with self.subTest(options=options):
                self.assertEqual(self.check(**options)["status"], "FAIL")

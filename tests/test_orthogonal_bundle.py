"""A persisted draft must independently bind actual three-format geometry."""

import copy
from dataclasses import replace
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import ezdxf
from pypdf import PdfReader, PdfWriter
from pypdf.generic import ContentStream, FloatObject, NameObject

from layout_configurator.equipment import EquipmentLayoutResult
from layout_configurator.building import StructuralGridSpec, ZoneSpec
from layout_configurator.facility import FacilityProfile
from test_orthogonal_pdf import _fixture


def _restore_bundle_files(path, snapshot):
    """Restore independent files from immutable actual writer bytes."""
    contents = dict(snapshot)
    for child in path.iterdir():
        if child.name not in contents:
            child.unlink()
    for name, data in contents.items():
        (path / name).write_bytes(data)


def _copy_pristine_bundle(cache, path, writer, building, layout, equipment, *, routing, profile, **options):
    """Generate each exact canonical fixture once; never cache QA execution."""
    key = json.dumps({"building": building.to_dict(), "layout": layout.to_dict(),
        "equipment": equipment.to_dict(building) if equipment is not None else None,
        "routing": routing.to_dict() if routing is not None else None,
        "profile": profile.to_dict(), "options": options},
        sort_keys=True, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")
    if key not in cache:
        with tempfile.TemporaryDirectory() as temporary:
            pristine = Path(temporary) / "bundle"
            writer(pristine, building, layout, equipment, routing=routing, profile=profile, **options)
            # Tuples and bytes have no mutable aliases to source models or
            # test files. Each negative edits only its own filesystem copy.
            cache[key] = tuple((child.name, child.read_bytes()) for child in sorted(pristine.iterdir()))
    path.mkdir()
    _restore_bundle_files(path, cache[key])
    return json.loads(next(data for name, data in cache[key] if name == "manifest.json"))


class OrthogonalBundleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._pristine_bundles = {}

    @classmethod
    def tearDownClass(cls):
        cls._pristine_bundles.clear()

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.building, self.layout, self.equipment, self.routing = _fixture()
        self.profile = FacilityProfile.from_mapping({"name": "Exact draft project policy", "version": "P01",
            "provenance": {"source": "Repository-owned project brief", "edition": "P01"},
            "drawing": {"revision": "P01"}, "rules": [{"id": "declared_people",
                "kind": "required_flow_types", "title": "People flow declaration", "source": "Project brief P01",
                "edition": "P01", "effective_date": "2026-10-13", "evidence": ["Explicit project requirement"],
                "parameters": {"flow_types": ["people"]}}]})

    def api(self):
        self.assertIsNotNone(importlib.util.find_spec("layout_configurator.orthogonal_bundle"),
                             "Independent persisted orthogonal draft bundle is missing")
        from layout_configurator import orthogonal_bundle
        return orthogonal_bundle

    def write(self, *, fresh=False, **kwargs):
        self.path = self.root / "bundle"
        writer = self.api().write_orthogonal_bundle
        if fresh or kwargs:
            self.manifest = writer(self.path, self.building, self.layout,
                self.equipment, routing=self.routing, profile=self.profile, **kwargs)
        else:
            self.manifest = _copy_pristine_bundle(self._pristine_bundles, self.path, writer,
                self.building, self.layout, self.equipment, routing=self.routing, profile=self.profile)
        self._pristine_files = tuple((child.name, child.read_bytes()) for child in sorted(self.path.iterdir()))
        return self.manifest

    def restore_pristine(self):
        _restore_bundle_files(self.path, self._pristine_files)

    def check(self, **kwargs):
        from layout_configurator.orthogonal_bundle_qa import validate_orthogonal_bundle
        return validate_orthogonal_bundle(self.path, **kwargs)

    def read(self, filename):
        return json.loads((self.path / filename).read_text(encoding="utf-8"))

    def save(self, filename, raw):
        (self.path / filename).write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")

    def rehash(self, artifact):
        manifest = self.read("manifest.json")
        record = manifest["artifacts"][artifact]
        record["sha256"] = hashlib.sha256((self.path / record["file"]).read_bytes()).hexdigest()
        index = self.read(record["index"])
        index["sha256" if artifact == "pdf" else "artifact_sha256"] = record["sha256"]
        self.save(record["index"], index)
        record["index_sha256"] = hashlib.sha256((self.path / record["index"]).read_bytes()).hexdigest()
        self.save("manifest.json", manifest)

    def assert_failed(self, report, identifier=None):
        summary = {"status": report["status"], "checks": report["checks"]}
        self.assertFalse(report["ok"], summary)
        self.assertEqual(report["status"], "FAIL", summary)
        self.assertEqual(report["full_facility_validation"], {"status": "UNKNOWN", "ok": False})
        self.assertEqual(report["engineering_acceptance"], "PENDING")
        if identifier:
            self.assertEqual(next(row for row in report["checks"] if row["id"] == identifier)["status"], "FAIL", summary)

    def test_saved_three_format_bundle_reloads_actual_canonical_sources_and_current_profile(self):
        manifest = self.write()
        self.assertEqual(manifest["status"], "DRAFT")
        self.assertIs(manifest["ok"], False)
        self.assertEqual(manifest["revision"], "P01")
        self.assertEqual(manifest["variant"], 3)
        report = self.check(expected_profile=self.profile)
        self.assertTrue(report["ok"], report)
        self.assertEqual(report["status"], "PASS")
        for identifier in ("DXF_READBACK", "PDF_READBACK", "IFC_READBACK", "IFC_EXPRESS", "FACILITY_PREDICATES", "SAVED_QA"):
            self.assertEqual(next(row for row in report["checks"] if row["id"] == identifier)["status"], "PASS")
        self.assertEqual(report["readbacks"]["ifc_express"]["statements"], [])
        self.assertEqual(self.read("building.json"), self.building.to_dict())
        self.assertEqual(self.read("layout.json"), self.layout.to_dict())
        self.assertEqual(self.read("equipment.json"), self.equipment.to_dict(self.building))
        self.assertEqual(self.read("routing.json"), self.routing.to_dict())
        self.assertEqual(self.read("profile.json"), self.profile.to_dict())
        self.assertEqual(self.read("facility-predicates.json")["profile"], self.profile.to_dict())
        self.assertEqual(self.read("qa.json")["status"], "PASS")
        dxf = ezdxf.readfile(self.path / "geometry.dxf")
        self.assertEqual(len(dxf.modelspace().query('LWPOLYLINE[layer=="A-EQUIP"]')), 2)
        self.assertGreater(len(dxf.modelspace().query('LWPOLYLINE[layer=="A-FLOW-WIDTH"]')), 0)

    def test_snapshot_and_returned_manifest_do_not_alias_caller_policy_or_geometry(self):
        before = copy.deepcopy(self.profile.to_dict())
        manifest = self.write(fresh=True)
        self.profile.rules[0].parameters["flow_types"].append("unrelated")
        self.profile.provenance["source"] = "Changed caller source"
        self.layout.placements.clear()
        manifest["status"] = "PASS"
        self.assertEqual(self.read("profile.json"), before)
        self.assertEqual(self.read("manifest.json")["status"], "DRAFT")
        self.assertTrue(self.check()["ok"])

    def test_missing_optional_declared_evidence_is_null_and_unknown_without_inference(self):
        self.path = self.root / "source-only"
        self.api().write_orthogonal_bundle(self.path, self.building, self.layout, None, profile=self.profile)
        self.assertIsNone(self.read("equipment.json"))
        self.assertIsNone(self.read("routing.json"))
        report = self.check()
        self.assertEqual(report["status"], "UNKNOWN", report)
        self.assertFalse(report["ok"])
        self.assertEqual(next(row for row in report["checks"] if row["id"] == "FACILITY_PREDICATES")["status"], "UNKNOWN")
        self.assertEqual(next(row for row in report["checks"] if row["id"] == "DXF_READBACK")["status"], "PASS")

    def test_invalid_local_geometry_or_unsupported_windows_reject_before_output(self):
        for branch in ("equipment", "rooms", "daylight", "profile", "revision"):
            with self.subTest(branch=branch):
                target = self.root / branch
                building, layout, equipment, policy = self.building, self.layout, self.equipment, self.profile
                extra = {}
                if branch == "equipment":
                    equipment = EquipmentLayoutResult(())
                elif branch == "rooms":
                    layout = replace(layout, placements={"corner": layout.placements["corner"]})
                elif branch == "daylight":
                    building = replace(building, layout=replace(building.layout, rooms=(
                        replace(building.layout.rooms[0], needs_daylight=True), building.layout.rooms[1])))
                elif branch == "profile":
                    rule = replace(policy.rules[0], parameters={"flow_types": ["missing"]})
                    policy = replace(policy, rules=(rule,))
                else:
                    extra["revision"] = "FORGED"
                with self.assertRaises(ValueError):
                    self.api().write_orthogonal_bundle(target, building, layout, equipment,
                        routing=self.routing, profile=policy, **extra)
                self.assertFalse(target.exists())

    def test_existing_destination_is_preserved_and_not_overwritten(self):
        self.write()
        before = {p.name: p.read_bytes() for p in self.path.iterdir()}
        with self.assertRaises(FileExistsError):
            self.api().write_orthogonal_bundle(self.path, self.building, self.layout, self.equipment,
                                              routing=self.routing, profile=self.profile)
        self.assertEqual({p.name: p.read_bytes() for p in self.path.iterdir()}, before)

    def test_expected_profile_and_preserved_rule_provenance_are_independent_anchors(self):
        self.write()
        other = replace(self.profile, provenance={"source": "Different project source"})
        self.assert_failed(self.check(expected_profile=other), "SOURCE_CONTRACT")
        report = self.check()
        rule = next(row for row in report["readbacks"]["facility"]["checks"] if "rule" in row)
        self.assertEqual(rule["rule"]["source"], "Project brief P01")
        self.assertEqual(rule["rule"]["effective_date"], "2026-10-13")

    def test_missing_artifact_hash_mismatch_malformed_index_and_traversal_fail(self):
        self.write()
        for branch in ("missing", "hash", "index", "path", "extra", "false_status"):
            with self.subTest(branch=branch):
                self.restore_pristine()
                manifest = self.read("manifest.json")
                if branch == "missing":
                    (self.path / "geometry.pdf").unlink()
                elif branch == "hash":
                    artifact = self.path / "geometry.dxf"
                    artifact.write_bytes(artifact.read_bytes() + b"\n")
                elif branch == "index":
                    self.save("pdf-index.json", [])
                    manifest["artifacts"]["pdf"]["index_sha256"] = hashlib.sha256((self.path / "pdf-index.json").read_bytes()).hexdigest()
                    self.save("manifest.json", manifest)
                elif branch == "path":
                    manifest["artifacts"]["dxf"]["file"] = "../geometry.dxf"
                    self.save("manifest.json", manifest)
                elif branch == "extra":
                    (self.path / "undeclared.json").write_text("{}")
                else:
                    manifest["status"], manifest["ok"] = "PASS", True
                    self.save("manifest.json", manifest)
                self.assert_failed(self.check())

    def test_rehashed_dxf_bbox_ring_door_service_corridor_and_physical_frame_tamper_fail_actual_readback(self):
        self.write()
        for branch in ("bbox", "wall_ring", "door", "service", "corridor", "ocs", "hidden"):
            with self.subTest(branch=branch):
                self.restore_pristine()
                document = ezdxf.readfile(self.path / "geometry.dxf")
                model = document.modelspace()
                if branch in ("bbox", "ocs", "hidden"):
                    room = next(item for item in model.query('LWPOLYLINE[layer=="A-ROOM"]')
                                if any(tag.value == "room_id=main" for tag in item.get_xdata("LAYOUT_IR")))
                    if branch == "bbox":
                        room.set_points([(0, 0), (6000, 0), (6000, 6000), (0, 6000)])
                    elif branch == "ocs":
                        room.dxf.elevation = 500
                    else:
                        document.layers.get("A-WALL").off()
                elif branch == "wall_ring":
                    model.delete_entity(list(model.query('LWPOLYLINE[layer=="A-WALL"]'))[-1])
                elif branch == "door":
                    door = list(model.query('LINE[layer=="A-DOOR"]'))[0]
                    door.dxf.start = (door.dxf.start.x + 300, door.dxf.start.y, 0)
                    door.dxf.end = (door.dxf.end.x + 300, door.dxf.end.y, 0)
                elif branch == "service":
                    list(model.query('LWPOLYLINE[layer=="A-EQUIP-CLEAR"]'))[0].set_points([(4800, 400), (5300, 400), (5300, 900), (4800, 900)])
                else:
                    list(model.query('LWPOLYLINE[layer=="A-FLOW-WIDTH"]'))[0].set_points(list(self.routing.routes[0].points))
                document.saveas(self.path / "geometry.dxf")
                self.rehash("dxf")
                self.assert_failed(self.check(), "DXF_READBACK")

    def test_rehashed_pdf_bbox_and_ifc_bbox_fail_their_actual_exchange_check(self):
        self.write()
        reader = PdfReader(self.path / "geometry.pdf")
        stream = ContentStream(reader.pages[0].get_contents(), reader)
        operations = stream.operations
        start = next(i for i, (v, op) in enumerate(operations) if op == b"BDC" and str(v[1].get("/Kind")) == "/room" and str(v[1].get("/Id")) == "main")
        end = next(i for i in range(start + 1, len(operations)) if operations[i][1] == b"EMC")
        kept = [(v, op) for v, op in operations[start + 1:end] if op not in (b"m", b"l", b"h", b"B*")]
        rectangle = [([FloatObject(x), FloatObject(y)], b"m" if i == 0 else b"l")
                     for i, (x, y) in enumerate(((0, 0), (6000, 0), (6000, 6000), (0, 6000)))]
        stream.operations = operations[:start + 1] + kept + rectangle + [([], b"h"), ([], b"B*")] + operations[end:]
        writer = PdfWriter(); writer.clone_document_from_reader(reader)
        writer.pages[0][NameObject("/Contents")] = writer._add_object(stream)
        with (self.path / "geometry.pdf").open("wb") as target:
            writer.write(target)
        self.rehash("pdf")
        self.assert_failed(self.check(), "PDF_READBACK")
        self.restore_pristine()
        import ifcopenshell
        model = ifcopenshell.open(str(self.path / "geometry.ifc"))
        room = next(item for item in model.by_type("IfcSpace") if item.Name == "main")
        curve = room.Representation.Representations[0].Items[0].SweptArea.OuterCurve
        curve.Points = [model.create_entity("IfcCartesianPoint", Coordinates=point) for point in
                        ((0., 0.), (6000., 0.), (6000., 6000.), (0., 6000.), (0., 0.))]
        model.write(str(self.path / "geometry.ifc"))
        self.rehash("ifc")
        self.assert_failed(self.check(), "IFC_READBACK")

    def test_forged_saved_positive_report_never_replaces_current_source_predicates(self):
        self.write()
        stored = self.read("facility-predicates.json")
        stored["profile"]["rules"][0]["parameters"]["flow_types"] = ["missing"]
        self.save("facility-predicates.json", stored)
        manifest = self.read("manifest.json")
        manifest["facility_predicates"]["sha256"] = hashlib.sha256((self.path / "facility-predicates.json").read_bytes()).hexdigest()
        self.save("manifest.json", manifest)
        self.assert_failed(self.check(), "FACILITY_REPORT_IDENTITY")

    def test_source_duplicate_keys_nonnumeric_rotations_and_forged_clearance_fail_after_rehash(self):
        self.write()
        for branch in ("duplicate", "rotation", "clearance"):
            with self.subTest(branch=branch):
                self.restore_pristine()
                raw = self.read("equipment.json")
                if branch == "duplicate":
                    (self.path / "equipment.json").write_bytes(b'{"equipment":[],"equipment":[]}')
                elif branch == "rotation":
                    raw["equipment"][0]["rotated"] = 1
                    self.save("equipment.json", raw)
                else:
                    raw["equipment"][0]["clearance"]["width"] = 500
                    self.save("equipment.json", raw)
                manifest = self.read("manifest.json")
                manifest["sources"]["equipment"]["sha256"] = hashlib.sha256((self.path / "equipment.json").read_bytes()).hexdigest()
                self.save("manifest.json", manifest)
                self.assert_failed(self.check(), "SOURCE_CONTRACT")

    def test_bounded_source_and_artifact_budgets_prevent_publishing_output(self):
        api = self.api()
        for setting, value in (("max_json_bytes", 128), ("max_artifact_bytes", 128), ("max_total_bytes", 1024)):
            with self.subTest(setting=setting):
                path = self.root / setting
                with self.assertRaises(ValueError):
                    api.write_orthogonal_bundle(path, self.building, self.layout, self.equipment,
                        routing=self.routing, profile=self.profile,
                        limits=replace(api.OrthogonalBundleLimits(), **{setting: value}))
                self.assertFalse(path.exists())

    def test_declared_grid_rejects_before_output_and_after_persisted_source_rehash(self):
        api = self.api()
        grid = StructuralGridSpec.from_mapping({"axes_x_mm": [0, 6000], "axes_y_mm": [0, 6000]})
        building = replace(self.building, structural_grid=grid)
        target = self.root / "unsupported-grid"
        with self.assertRaisesRegex(ValueError, "structural_grid"):
            api.write_orthogonal_bundle(target, building, self.layout, self.equipment,
                                       routing=self.routing, profile=self.profile)
        self.assertFalse(target.exists())
        self.write()
        self.save("building.json", building.to_dict())
        manifest = self.read("manifest.json")
        record = manifest["sources"]["building"]
        record["sha256"] = hashlib.sha256((self.path / "building.json").read_bytes()).hexdigest()
        record["canonical_sha256"] = hashlib.sha256(json.dumps(building.to_dict(), sort_keys=True,
            ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()
        self.save("manifest.json", manifest)
        self.assert_failed(self.check(), "SOURCE_CONTRACT")

    def test_zone_sources_and_projection_limits_are_explicit_and_cannot_claim_native_pass(self):
        self.building = replace(self.building, zones=(ZoneSpec("process_zone", "process", ("main",),
            required_adjacency=("support_zone",)), ZoneSpec("support_zone", "support", ("corner",))))
        manifest = self.write()
        scope = manifest.get("projection_scope")
        self.assertIsInstance(scope, dict, "Native zone/system projection limits need explicit inventory")
        self.assertEqual(scope["zones"], {"declared": 2, "source_preserved": True,
            "software_predicates": "RECOMPUTED", "native_ifc_projection": "UNKNOWN"})
        self.assertEqual(scope["process_systems"]["native_ifc_projection"], "UNKNOWN")
        self.assertEqual(scope["process_systems"]["declared"], 1)
        report = self.check()
        self.assertTrue(report["ok"], report)
        self.assertEqual(report["projection_scope"], scope)
        self.assertEqual(self.read("building.json")["zones"], self.building.to_dict()["zones"])
        self.assertEqual(next(row for row in report["readbacks"]["facility"]["checks"]
            if row["id"] == "ZONE_RELATIONS")["status"], "PASS")
        manifest["projection_scope"]["zones"]["native_ifc_projection"] = "PASS"
        self.save("manifest.json", manifest)
        self.assert_failed(self.check(), "SOURCE_CONTRACT")

    def test_rehashed_ifc_index_false_local_evidence_or_scope_is_rejected(self):
        self.write()
        for branch in ("status", "evidence", "scope"):
            with self.subTest(branch=branch):
                self.restore_pristine()
                index = self.read("ifc-index.json")
                if branch == "status":
                    index["local_validation"]["rooms"]["status"] = "UNKNOWN"
                elif branch == "evidence":
                    index["local_validation"]["rooms"]["checks"][0]["evidence"] = ["Invented approval"]
                else:
                    index["scope"] = "Full engineering acceptance PASS"
                self.save("ifc-index.json", index)
                self.rehash("ifc")
                self.assert_failed(self.check(), "ARTIFACT_INDEX")

    def test_actual_source_generated_candidate_persists_without_coordinate_fixture_input(self):
        from test_orthogonal_generation import _source
        from layout_configurator.facility import default_facility_profile
        from layout_configurator.orthogonal_generation import generate_orthogonal_facility_variants
        building, profile = _source(), default_facility_profile()
        generated = generate_orthogonal_facility_variants(building, profile=profile, seed=1, deterministic_units=.05)
        self.assertEqual(len(generated.accepted), 1, generated.to_dict(building))
        candidate = generated.accepted[0]
        self.path = self.root / "generated"
        manifest = self.api().write_orthogonal_bundle(self.path, building, candidate.result, candidate.equipment,
            routing=candidate.flow_routes, profile=profile)
        self.assertEqual(manifest["variant"], 1)
        self.assertEqual(self.read("building.json"), building.to_dict())
        self.assertNotIn("placements", self.read("building.json"))
        report = self.check(expected_profile=profile)
        self.assertTrue(report["ok"], report["checks"])
        self.assertEqual(report["readbacks"]["pdf"]["actual"]["room_areas_mm2"],
                         {"main": 20_000_000, "corner": 16_000_000})
        self.assertEqual(report["readbacks"]["ifc"]["counts"]["equipment"], 1)

    def test_real_ifc_frame_revision_and_express_errors_fail_after_actual_hash_recomputation(self):
        self.write()
        original = (self.path / "geometry.ifc").read_bytes()
        import ifcopenshell
        for branch, identifier in (("frame", "IFC_READBACK"), ("revision", "IFC_READBACK"), ("express", "IFC_EXPRESS")):
            with self.subTest(branch=branch):
                self.restore_pristine()
                model = ifcopenshell.file.from_string(original.decode("utf-8"))
                room = next(item for item in model.by_type("IfcSpace") if item.Name == "main")
                if branch == "frame":
                    room.ObjectPlacement.RelativePlacement.Location.Coordinates = (100., 0., 0.)
                elif branch == "revision":
                    properties = next(item for item in model.by_type("IfcPropertySet") if item.Name == "Pset_OrthogonalBundle")
                    next(item for item in properties.HasProperties if item.Name == "revision").NominalValue = model.create_entity("IfcLabel", "FORGED")
                else:
                    room.PredefinedType, room.ObjectType = "USERDEFINED", None
                model.write(str(self.path / "geometry.ifc"))
                self.rehash("ifc")
                report = self.check()
                self.assert_failed(report, identifier)
                if branch == "express":
                    self.assertTrue(report["readbacks"]["ifc_express"]["statements"])

    def test_equivalent_dxf_ocs_coordinates_keep_actual_source_geometry(self):
        self.write()
        document = ezdxf.readfile(self.path / "geometry.dxf")
        for entity in document.modelspace().query('LWPOLYLINE[layer=="A-ROOM"]'):
            points = entity.get_points("xy")
            entity.dxf.extrusion = (0, 0, -1)
            entity.set_points([(-x, y) for x, y in points])
        document.saveas(self.path / "geometry.dxf")
        self.rehash("dxf")
        report = self.check()
        self.assertEqual(next(row for row in report["checks"] if row["id"] == "DXF_READBACK")["status"], "PASS", report["checks"])

    def test_rehashed_current_profile_is_recomputed_instead_of_cached_positive_predicates(self):
        self.write()
        raw = self.read("profile.json")
        raw["rules"][0]["parameters"]["flow_types"] = ["missing"]
        self.save("profile.json", raw)
        manifest = self.read("manifest.json")
        record = manifest["sources"]["profile"]
        record["sha256"] = hashlib.sha256((self.path / "profile.json").read_bytes()).hexdigest()
        record["canonical_sha256"] = hashlib.sha256(json.dumps(raw, sort_keys=True,
            ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()
        self.save("manifest.json", manifest)
        report = self.check()
        self.assert_failed(report, "FACILITY_PREDICATES")
        self.assertEqual(report["readbacks"]["facility"]["profile"], raw)

    def test_saved_qa_binds_actual_manifest_and_artifact_index_bytes(self):
        self.write()
        (self.path / "geometry.dxf").write_bytes((self.path / "geometry.dxf").read_bytes() + b"\n")
        self.rehash("dxf")
        report = self.check()
        self.assertEqual(next(row for row in report["checks"] if row["id"] == "DXF_READBACK")["status"], "PASS")
        self.assert_failed(report, "SAVED_QA")

    def test_rehashed_manifest_cannot_claim_smaller_bounds_than_actual_saved_resources(self):
        self.write()
        for field, value in (("max_json_bytes", 128), ("max_artifact_bytes", 128), ("max_total_bytes", 1024), ("max_dxf_entities", 1)):
            with self.subTest(field=field):
                self.restore_pristine()
                manifest = self.read("manifest.json")
                manifest["limits"][field] = value
                self.save("manifest.json", manifest)
                report = self.check()
                self.assert_failed(report, "DXF_READBACK" if field == "max_dxf_entities" else "SOURCE_CONTRACT")

    def test_atomic_publication_cannot_replace_an_existing_empty_destination(self):
        publish = getattr(self.api(), "_publish_directory", None)
        self.assertTrue(callable(publish), "Atomic no-replace publication is missing")
        stage, target = self.root / "stage", self.root / "destination"
        stage.mkdir(); target.mkdir()
        (stage / "source.txt").write_text("Keep the staged source")
        with self.assertRaises(FileExistsError):
            publish(stage, target)
        self.assertEqual((stage / "source.txt").read_text(), "Keep the staged source")
        self.assertEqual(list(target.iterdir()), [])
        target.rmdir()
        publish(stage, target)
        self.assertFalse(stage.exists())
        self.assertEqual((target / "source.txt").read_text(), "Keep the staged source")

    def test_raced_in_destination_after_complete_validation_is_not_overwritten(self):
        api = self.api()
        publish = getattr(api, "_publish_directory", None)
        self.assertTrue(callable(publish), "Atomic no-replace publication is missing")
        target = self.root / "race"
        def raced(stage, destination):
            # Real filesystem destination appears after the writer's last
            # existence check, immediately before its actual native rename.
            self.assertEqual(destination, target)
            destination.mkdir()
            publish(stage, destination)
        with patch.object(api, "_publish_directory", side_effect=raced):
            with self.assertRaises(FileExistsError):
                api.write_orthogonal_bundle(target, self.building, self.layout, self.equipment,
                                           routing=self.routing, profile=self.profile)
        self.assertTrue(target.is_dir())
        self.assertEqual(list(target.iterdir()), [])
        self.assertFalse(any(self.root.glob(".orthogonal-draft-*")))


if __name__ == "__main__":
    unittest.main()

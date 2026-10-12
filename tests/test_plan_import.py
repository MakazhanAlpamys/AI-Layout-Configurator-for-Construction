import hashlib
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

import ezdxf
import ifcopenshell

from layout_configurator.export import export_dxf
from layout_configurator.ifc import export_ifc
from layout_configurator.models import LayoutIR, LayoutResult, Rect


class PlanImportTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)

    def api(self):
        self.assertIsNotNone(
            importlib.util.find_spec("layout_configurator.plan_import"),
            "Source-backed plan import is not implemented",
        )
        from layout_configurator import plan_import
        return plan_import

    def test_dxf_export_preserves_nonplotting_semantic_room_polygons(self):
        """Removing semantic room outlines must break editable plan import."""
        spec, layout = _program()
        path = self.root / "export.dxf"
        export_dxf(path, spec, layout)
        document = ezdxf.readfile(path)
        outlines = list(document.modelspace().query('LWPOLYLINE[layer=="A-ROOM"]'))

        self.assertEqual(len(outlines), 2)
        self.assertEqual(document.layers.get("A-ROOM").dxf.plot, 0)
        self.assertTrue(all(entity.closed for entity in outlines))
        self.assertEqual(
            [(tag.code, tag.value) for tag in outlines[0].get_xdata("LAYOUT_IR")],
            [(1000, "room_id=left"), (1000, "room_type=production")],
        )

    def test_exported_dxf_roundtrip_requires_named_human_confirmation(self):
        api = self.api()
        spec, layout = _program()
        source = self.root / "rooms.dxf"
        export_dxf(source, spec, layout)
        draft = api.import_plan(source)
        draft_path = self.root / "draft.json"
        api.write_import_draft(draft_path, draft)

        self.assertEqual(draft.state, "DRAFT")
        self.assertEqual(draft.source_sha256, hashlib.sha256(source.read_bytes()).hexdigest())
        self.assertEqual(draft.issues, ())
        self.assertEqual([(room.id, room.rect) for room in draft.rooms], [
            ("left", Rect(0, 0, 3000, 3000)), ("right", Rect(3000, 0, 3000, 3000)),
        ])
        self.assertEqual(api.read_import_draft(draft_path).to_dict(), draft.to_dict())
        with self.assertRaisesRegex(ValueError, "confirm"):
            api.load_imported_constraints(draft_path, spec)
        with self.assertRaisesRegex(ValueError, "reviewer"):
            api.load_imported_constraints(draft_path, spec, confirmed=True)
        before = spec.to_dict()
        fixed = api.load_imported_constraints(draft_path, spec, confirmed=True, reviewer="Engineer A")
        self.assertEqual(fixed, layout.placements)
        self.assertEqual(spec.to_dict(), before)
        confirmation = json.loads(draft_path.with_suffix(".confirmation.json").read_text(encoding="utf-8"))
        self.assertEqual(confirmation["draft_sha256"], hashlib.sha256(draft_path.read_bytes()).hexdigest())
        self.assertEqual(confirmation["layout_sha256"], hashlib.sha256(
            json.dumps(spec.to_dict(), sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        ).hexdigest())
        self.assertEqual(confirmation["reviewer"], "Engineer A")

    def test_independent_meter_dxf_uses_source_handle_and_explicit_room_mapping(self):
        api = self.api()
        path, handle = self._dxf(units=6, points=[(0, 0), (3, 0), (3, 3), (0, 3)])
        draft = api.import_plan(path)
        spec, _ = _program()
        draft_path = self.root / "draft.json"
        api.write_import_draft(draft_path, draft)

        self.assertEqual(draft.unit_scale_mm, 1000)
        self.assertEqual(draft.rooms[0].id, f"dxf:{handle}")
        self.assertEqual(draft.rooms[0].source_entity_id, handle)
        self.assertTrue(draft.rooms[0].provisional)
        self.assertEqual(draft.rooms[0].rect, Rect(0, 0, 3000, 3000))
        with self.assertRaisesRegex(ValueError, "unknown room"):
            api.load_imported_constraints(draft_path, spec, confirmed=True, reviewer="A")
        fixed = api.load_imported_constraints(
            draft_path, spec, confirmed=True, reviewer="A", room_mapping={f"dxf:{handle}": "left"},
        )
        self.assertEqual(fixed, {"left": Rect(0, 0, 3000, 3000)})

    def test_unitless_dxf_is_a_rejected_draft_until_scale_is_supplied(self):
        api = self.api()
        path, _ = self._dxf(units=0, points=[(0, 0), (3, 0), (3, 3), (0, 3)])
        rejected = api.import_plan(path)
        self.assertTrue(any("unknown_units" in issue for issue in rejected.issues))
        self.assertEqual(rejected.rooms, ())
        accepted = api.import_plan(path, unit_scale_mm=1000)
        self.assertEqual(accepted.issues, ())
        self.assertEqual(accepted.rooms[0].rect, Rect(0, 0, 3000, 3000))
        self.assertTrue(any("explicit" in item.lower() for item in accepted.assumptions))

    def test_invalid_negative_dxf_unit_code_is_not_a_conversion_table_index(self):
        api = self.api()
        path, _ = self._dxf(units=-5, points=[(0, 0), (3, 0), (3, 3), (0, 3)])

        draft = api.import_plan(path)

        self.assertTrue(any("unknown_units" in issue for issue in draft.issues), draft.to_dict())
        self.assertEqual(draft.rooms, ())

    def test_rejects_nonrectangular_open_bulged_and_duplicate_room_geometry(self):
        api = self.api()
        for points, closed, bulge in (
            ([(0, 0), (3000, 0), (0, 3000)], True, False),
            ([(0, 0), (3000, 0), (3000, 3000), (0, 3000)], False, False),
            ([(0, 0), (3000, 0), (3000, 3000), (0, 3000)], True, True),
        ):
            with self.subTest(points=points, closed=closed, bulge=bulge):
                path, _ = self._dxf(units=4, points=points, closed=closed, bulge=bulge)
                self.assertTrue(api.import_plan(path).issues)
        path, _ = self._dxf(units=4, points=[(0, 0), (3000, 0), (3000, 3000), (0, 3000)], room_id="left")
        document = ezdxf.readfile(path)
        duplicate = document.modelspace().add_lwpolyline(
            [(3000, 0), (6000, 0), (6000, 3000), (3000, 3000)], close=True, dxfattribs={"layer": "A-ROOM"},
        )
        duplicate.set_xdata("LAYOUT_IR", [(1000, "room_id=left"), (1000, "room_type=room")])
        document.saveas(path)
        self.assertTrue(any("duplicate_id" in issue for issue in api.import_plan(path).issues))

    def test_confirmation_rejects_changed_original_source(self):
        api = self.api()
        path, _ = self._dxf(units=4, points=[(0, 0), (3000, 0), (3000, 3000), (0, 3000)], room_id="left")
        draft_path = self.root / "draft.json"
        api.write_import_draft(draft_path, api.import_plan(path))
        spec, _ = _program()
        path.write_bytes(path.read_bytes() + b"\n")
        with self.assertRaisesRegex(ValueError, "source.*hash|source.*changed"):
            api.load_imported_constraints(draft_path, spec, confirmed=True, reviewer="A")

    def test_confirmation_revalidates_grid_bounds_area_dimensions_and_cutouts(self):
        api = self.api()
        spec, _ = _program()
        for rect, code in (
            (Rect(1, 0, 3000, 3000), "grid"),
            (Rect(9000, 0, 3000, 3000), "bounds"),
            (Rect(0, 0, 3000, 4000), "area"),
            (Rect(0, 0, 1000, 9000), "dimension"),
        ):
            with self.subTest(code=code):
                path, _ = self._dxf(units=4, points=[
                    (rect.x, rect.y), (rect.right, rect.y), (rect.right, rect.top), (rect.x, rect.top),
                ], room_id="left")
                draft_path = self.root / "draft.json"
                api.write_import_draft(draft_path, api.import_plan(path))
                with self.assertRaisesRegex(ValueError, code):
                    api.load_imported_constraints(draft_path, spec, confirmed=True, reviewer="A")
        path, _ = self._dxf(units=4, points=[(0, 0), (3000, 0), (3000, 3000), (0, 3000)], room_id="left")
        draft_path = self.root / "draft.json"
        api.write_import_draft(draft_path, api.import_plan(path))
        payload = spec.to_dict()
        payload["boundary"]["cutouts"] = [{"x": 1000, "y": 1000, "width": 1000, "height": 1000}]
        with self.assertRaisesRegex(ValueError, "cutout"):
            api.load_imported_constraints(draft_path, LayoutIR.from_mapping(payload), confirmed=True, reviewer="A")

    def test_overlapping_source_rooms_remain_invalid_after_confirmation(self):
        api = self.api()
        source, _ = self._dxf(units=4, points=[(0, 0), (3000, 0), (3000, 3000), (0, 3000)], room_id="left")
        document = ezdxf.readfile(source)
        other = document.modelspace().add_lwpolyline(
            [(2000, 0), (5000, 0), (5000, 3000), (2000, 3000)], close=True, dxfattribs={"layer": "A-ROOM"},
        )
        other.set_xdata("LAYOUT_IR", [(1000, "room_id=right"), (1000, "room_type=room")])
        document.saveas(source)
        draft = api.import_plan(source)
        self.assertTrue(any("overlap" in issue for issue in draft.issues))
        draft_path = self.root / "draft.json"
        api.write_import_draft(draft_path, draft)
        with self.assertRaisesRegex(ValueError, "overlap"):
            api.load_imported_constraints(draft_path, _program()[0], confirmed=True, reviewer="A")

    def test_ifc_import_uses_world_geometry_and_retains_globalids(self):
        api = self.api()
        spec, layout = _program()
        source = self.root / "rooms.ifc"
        export_ifc(source, spec, layout)
        document = ifcopenshell.open(source)
        storey = document.by_type("IfcBuildingStorey")[0]
        storey.ObjectPlacement.RelativePlacement = document.create_entity(
            "IfcAxis2Placement3D", Location=document.create_entity("IfcCartesianPoint", Coordinates=(6000.0, 1000.0, 0.0)),
            RefDirection=document.create_entity("IfcDirection", DirectionRatios=(0.0, 1.0, 0.0)),
        )
        globalids = {space.Name: space.GlobalId for space in document.by_type("IfcSpace")}
        document.write(str(source))

        draft = api.import_plan(source)

        self.assertEqual(draft.issues, ())
        self.assertEqual({room.id: room.rect for room in draft.rooms}, {
            "left": Rect(3000, 1000, 3000, 3000), "right": Rect(3000, 4000, 3000, 3000),
        })
        self.assertEqual({room.id: room.source_entity_id for room in draft.rooms}, globalids)

    def test_ifc_nonrectangular_space_is_not_replaced_with_its_bbox(self):
        api = self.api()
        spec, layout = _program()
        source = self.root / "nonrectangular.ifc"
        export_ifc(source, spec, layout)
        document = ifcopenshell.open(source)
        space = next(space for space in document.by_type("IfcSpace") if space.Name == "left")
        points = [document.create_entity("IfcCartesianPoint", Coordinates=(float(x), float(y))) for x, y in (
            (0, 0), (3000, 0), (3000, 1000), (1000, 1000), (1000, 3000), (0, 3000), (0, 0),
        )]
        space.Representation.Representations[0].Items[0].SweptArea = document.create_entity(
            "IfcArbitraryClosedProfileDef", ProfileType="AREA", OuterCurve=document.create_entity("IfcPolyline", Points=points),
        )
        document.write(str(source))

        draft = api.import_plan(source)

        self.assertTrue(any("nonrectangular" in issue for issue in draft.issues), draft.to_dict())
        self.assertNotIn("left", {room.id for room in draft.rooms})

    def test_ifc_multiple_storeys_require_filter_before_projection(self):
        api = self.api()
        spec, layout = _program()
        source = self.root / "storeys.ifc"
        export_ifc(source, spec, layout)
        document = ifcopenshell.open(source)
        original = document.by_type("IfcSpace")[0]
        upper = document.create_entity("IfcBuildingStorey", GlobalId=ifcopenshell.guid.new(), Name="Upper")
        copied = document.create_entity(
            "IfcSpace", GlobalId=ifcopenshell.guid.new(), Name="upstairs",
            ObjectPlacement=original.ObjectPlacement, Representation=original.Representation,
        )
        document.create_entity(
            "IfcRelContainedInSpatialStructure", GlobalId=ifcopenshell.guid.new(),
            RelatedElements=[copied], RelatingStructure=upper,
        )
        document.write(str(source))

        unresolved = api.import_plan(source)
        self.assertTrue(any("storey_selection" in issue for issue in unresolved.issues), unresolved.to_dict())
        ground = api.import_plan(source, storey="Ground floor")
        self.assertEqual(ground.issues, ())
        self.assertEqual({room.id for room in ground.rooms}, {"left", "right"})

    def test_ifc_other_declared_units_do_not_mask_its_length_unit(self):
        api = self.api()
        spec, layout = _program()
        source = self.root / "currency.ifc"
        export_ifc(source, spec, layout)
        document = ifcopenshell.open(source)
        units = document.by_type("IfcProject")[0].UnitsInContext
        units.Units = (*units.Units, document.create_entity("IfcMonetaryUnit", Currency="USD"))
        document.write(str(source))

        draft = api.import_plan(source)

        self.assertEqual(draft.issues, ())
        self.assertEqual(draft.unit_scale_mm, 1)

    def test_malformed_draft_is_rejected_as_input_without_shape_coercion(self):
        api = self.api()
        path = self.root / "draft.json"
        for raw in ([], {"format": "FLC-import-draft", "version": "0.1", "state": "DRAFT"}):
            with self.subTest(raw=raw):
                path.write_text(json.dumps(raw), encoding="utf-8")
                with self.assertRaises(ValueError):
                    api.read_import_draft(path)

    def test_draft_source_metadata_requires_nonempty_strings_and_a_sha256(self):
        api = self.api()
        source, _ = self._dxf(units=4, points=[(0, 0), (3000, 0), (3000, 3000), (0, 3000)], room_id="left")
        draft = api.import_plan(source).to_dict()
        path = self.root / "draft.json"
        for field, value in (
            ("path", []), ("path", 42), ("path", None), ("path", "  "),
            ("sha256", []), ("sha256", 42), ("sha256", None),
            ("sha256", "a" * 63), ("sha256", "g" * 64),
            ("format", []), ("format", 42), ("format", None), ("format", "pdf"),
        ):
            with self.subTest(field=field, value=value):
                raw = json.loads(json.dumps(draft))
                raw["source"][field] = value
                path.write_text(json.dumps(raw), encoding="utf-8")
                with self.assertRaises(ValueError):
                    api.read_import_draft(path)
                with self.assertRaises(ValueError):
                    api.load_imported_constraints(path, _program()[0], confirmed=True, reviewer="A")
                self.assertFalse(path.with_suffix(".confirmation.json").exists())

    def test_draft_units_and_storey_reject_malformed_scalar_metadata(self):
        api = self.api()
        source, _ = self._dxf(units=4, points=[(0, 0), (3000, 0), (3000, 3000), (0, 3000)], room_id="left")
        draft = api.import_plan(source).to_dict()
        path = self.root / "draft.json"
        for section, field, value in (
            ("units", "source_units", []), ("units", "source_units", 42),
            ("units", "source_units", None), ("units", "source_units", "  "),
            ("units", "unit_scale_mm", []), ("units", "unit_scale_mm", "1"),
            ("units", "unit_scale_mm", True), ("units", "unit_scale_mm", False),
            ("units", "unit_scale_mm", 0), ("units", "unit_scale_mm", -1),
            ("units", "unit_scale_mm", float("nan")), ("units", "unit_scale_mm", float("inf")),
            (None, "storey", []), (None, "storey", 42), (None, "storey", True),
            (None, "storey", "  "),
        ):
            with self.subTest(field=field, value=value):
                raw = json.loads(json.dumps(draft))
                (raw[section] if section else raw)[field] = value
                path.write_text(json.dumps(raw), encoding="utf-8")
                with self.assertRaises(ValueError):
                    api.read_import_draft(path)

    def test_rejected_unitless_draft_remains_readable_with_unknown_scale(self):
        api = self.api()
        source, _ = self._dxf(units=0, points=[(0, 0), (3, 0), (3, 3), (0, 3)])
        path = self.root / "draft.json"
        api.write_import_draft(path, api.import_plan(source))

        draft = api.read_import_draft(path)

        self.assertIsNone(draft.unit_scale_mm)
        self.assertTrue(any("unknown_units" in issue for issue in draft.issues))

    def test_confirmation_rejects_unit_or_format_metadata_changed_after_import(self):
        api = self.api()
        source, _ = self._dxf(units=4, points=[(0, 0), (3000, 0), (3000, 3000), (0, 3000)], room_id="left")
        draft = api.import_plan(source).to_dict()
        path = self.root / "draft.json"
        for section, field, value in (("source", "format", "IFC"), ("units", "source_units", "unverified units")):
            with self.subTest(field=field):
                raw = json.loads(json.dumps(draft))
                raw[section][field] = value
                path.write_text(json.dumps(raw), encoding="utf-8")
                with self.assertRaisesRegex(ValueError, "metadata|units|format"):
                    api.load_imported_constraints(path, _program()[0], confirmed=True, reviewer="A")
                self.assertFalse(path.with_suffix(".confirmation.json").exists())

    def test_confirmed_ifc_roundtrip_is_fixed_geometry_in_existing_solver(self):
        api = self.api()
        from layout_configurator.solver import solve_layouts
        spec, layout = _program()
        source = self.root / "rooms.ifc"
        export_ifc(source, spec, layout)
        draft_path = self.root / "draft.json"
        api.write_import_draft(draft_path, api.import_plan(source))

        fixed = api.load_imported_constraints(draft_path, spec, confirmed=True, reviewer="Engineer A")
        regenerated = solve_layouts(spec, variants=1, time_limit_seconds=5, fixed_rects=fixed)[0]

        self.assertEqual(regenerated.placements, {
            "left": Rect(0, 0, 3000, 3000), "right": Rect(3000, 0, 3000, 3000),
        })

    def _dxf(self, *, units, points, closed=True, bulge=False, room_id=None):
        path = self.root / "source.dxf"
        document = ezdxf.new("R2018")
        document.header["$INSUNITS"] = units
        document.layers.add("A-ROOM")
        entity = document.modelspace().add_lwpolyline(points, close=closed, dxfattribs={"layer": "A-ROOM"})
        if bulge:
            entity.set_points([(x, y, 0, 0, 1 if index == 0 else 0) for index, (x, y) in enumerate(points)])
        if room_id:
            document.appids.add("LAYOUT_IR")
            entity.set_xdata("LAYOUT_IR", [(1000, f"room_id={room_id}"), (1000, "room_type=room")])
        document.saveas(path)
        return path, entity.dxf.handle


def _program():
    spec = LayoutIR.from_mapping({
        "project_name": "plan import test", "grid_mm": 100,
        "boundary": {"width": 10000, "height": 10000}, "entry_room": "left",
        "rooms": [
            {"id": "left", "type": "production", "target_area": 9, "min_width": 2000, "min_depth": 2000},
            {"id": "right", "type": "room", "target_area": 9, "min_width": 2000, "min_depth": 2000},
        ],
    })
    return spec, LayoutResult(variant=1, placements={"left": Rect(0, 0, 3000, 3000), "right": Rect(3000, 0, 3000, 3000)})


if __name__ == "__main__":
    unittest.main()

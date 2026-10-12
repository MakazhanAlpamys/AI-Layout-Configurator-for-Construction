import importlib.util
import hashlib
import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

import ezdxf
from shapely.geometry import Point, Polygon

from layout_configurator.io import load_result, write_result
from layout_configurator.models import LayoutIR, LayoutResult, Rect
from layout_configurator.orthogonal import OrthogonalPolygon
from layout_configurator.plan_import import import_plan
from layout_configurator.walls import build_wall_plan


class OrthogonalProjectionTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)

    def api(self):
        self.assertIsNotNone(importlib.util.find_spec("layout_configurator.orthogonal_projection"),
                             "Exact polygon wall and DXF projection is missing")
        from layout_configurator import orthogonal_projection
        return orthogonal_projection

    def test_canonical_l_shape_projects_exact_semantic_dxf_and_source_evidence(self):
        api = self.api()
        source = self.root / "source.json"
        write_result(source, _program(), _result())
        spec, result = load_result(source)
        before = source.read_bytes()
        target = self.root / "draft.dxf"

        evidence = api.export_orthogonal_draft_dxf(target, spec, result)

        doc = ezdxf.readfile(target)
        self.assertEqual(doc.header["$INSUNITS"], 4)
        self.assertFalse(doc.layers.get("A-ROOM").dxf.plot)
        rooms = list(doc.modelspace().query('LWPOLYLINE[layer=="A-ROOM"]'))
        self.assertEqual(len(rooms), 2)
        outlines = {}
        for entity in rooms:
            self.assertTrue(entity.closed)
            tags = dict(tag.value.split("=", 1) for tag in entity.get_xdata("LAYOUT_IR"))
            outlines[tags["room_id"]] = Polygon(list(entity.get_points("xy")))
            self.assertEqual(tags["room_type"], spec.room_by_id[tags["room_id"]].type)
        self.assertEqual(outlines["main"].area, 20_000_000)
        self.assertEqual(len(outlines["main"].exterior.coords), 7)
        self.assertFalse(outlines["main"].covers(Point(4000, 4000)))
        self.assertEqual(outlines["corner"].area, 16_000_000)
        self.assertEqual(source.read_bytes(), before)
        self.assertEqual(evidence.status, "DRAFT")
        self.assertFalse(evidence.ok)
        payload = evidence.to_dict()
        self.assertEqual(payload["units"], "mm")
        self.assertEqual(len(payload["source"]["spec_sha256"]), 64)
        self.assertEqual(len(payload["source"]["layout_sha256"]), 64)
        self.assertEqual(len(payload["artifact_sha256"]), 64)
        self.assertIn("not engineering", payload["scope"])
        title = " ".join(entity.dxf.text for entity in doc.modelspace().query('TEXT[layer=="A-TITLE"]'))
        self.assertIn("DRAFT", title)
        self.assertIn("20.00 m2", " ".join(entity.dxf.text for entity in doc.modelspace().query('TEXT[layer=="A-TEXT"]')))
        metadata = list(doc.modelspace().query('POINT[layer=="A-TITLE"]'))
        self.assertEqual(len(metadata), 1)
        tags = dict(tag.value.split("=", 1) for tag in metadata[0].get_xdata("LAYOUT_IR"))
        self.assertEqual(tags["spec_sha256"], payload["source"]["spec_sha256"])
        self.assertEqual(tags["layout_sha256"], payload["source"]["layout_sha256"])
        self.assertEqual(tags["status"], "DRAFT")
        self.assertEqual(payload["source"]["spec_sha256"], hashlib.sha256(
            json.dumps(spec.to_dict(), ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest())
        self.assertEqual(payload["source"]["layout_sha256"], hashlib.sha256(
            json.dumps(result.to_dict(), ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest())
        self.assertEqual(payload["artifact_sha256"], hashlib.sha256(target.read_bytes()).hexdigest())

    def test_dxf_wall_rings_and_door_line_read_back_as_real_boolean_geometry(self):
        api = self.api()
        path = self.root / "draft.dxf"
        api.export_orthogonal_draft_dxf(path, _program(), _result())
        modelspace = ezdxf.readfile(path).modelspace()
        material = Polygon()
        rings = list(modelspace.query('LWPOLYLINE[layer=="A-WALL"]'))
        self.assertTrue(rings)
        # Ring parity rebuilds faces and voids without trusting WallPlan.
        for ring in rings:
            self.assertTrue(ring.closed)
            self.assertTrue(all(point[4] == 0 for point in ring.get_points()))
            material = material.symmetric_difference(Polygon(list(ring.get_points("xy"))))
        self.assertTrue(material.is_valid)
        self.assertTrue(material.covers(Point(2000, 2000)))
        self.assertTrue(material.covers(Point(2000, 2500)))
        self.assertFalse(material.covers(Point(2000, 4000)))
        self.assertFalse(material.covers(Point(4000, 4000)))
        doors = list(modelspace.query('LINE[layer=="A-DOOR"]'))
        self.assertEqual(len(doors), 1)
        self.assertEqual(tuple(doors[0].dxf.start)[:2], (2000, 3550))
        self.assertEqual(tuple(doors[0].dxf.end)[:2], (2000, 4450))
        self.assertIn("door_id=auto-corner-main", [tag.value for tag in doors[0].get_xdata("LAYOUT_IR")])

    def test_rectangular_import_boundary_reports_polygon_instead_of_substituting_bbox(self):
        api = self.api()
        path = self.root / "draft.dxf"
        api.export_orthogonal_draft_dxf(path, _program(), _result())

        draft = import_plan(path)

        self.assertTrue(any("nonrectangular" in issue for issue in draft.issues))
        self.assertEqual([room.id for room in draft.rooms], ["corner"])
        self.assertEqual(draft.rooms[0].rect, Rect(2000, 2000, 4000, 4000))

    def test_exact_concave_wall_network_has_real_corner_and_required_door_gap(self):
        api = self.api()
        wall_plan = api.build_orthogonal_wall_plan(_program(), _result())

        self.assertTrue(wall_plan.geometry.is_valid)
        self.assertEqual(len(wall_plan.openings), 1)
        door = wall_plan.openings[0]
        self.assertEqual(door.width, 900)
        self.assertEqual((door.orientation, door.fixed, door.start, door.end), ("vertical", 2000, 3550, 4450))
        self.assertFalse(wall_plan.geometry.covers(Point(2000, 4000)))
        self.assertTrue(wall_plan.geometry.covers(Point(2000, 2500)))
        self.assertTrue(wall_plan.geometry.covers(Point(2000, 2000)))
        self.assertFalse(wall_plan.geometry.covers(Point(4000, 4000)))
        single = replace(_program(), rooms=(_program().rooms[0],))
        single = replace(single, rooms=(replace(single.rooms[0], required_adjacency=()),))
        band = api.build_orthogonal_wall_plan(single, LayoutResult(1, {"main": _l_shape()})).geometry
        self.assertTrue(band.covers(Point(4000, 2000)))
        self.assertFalse(band.covers(Point(6000, 4000)), "Bounding-box right wall must not replace the concave footprint")

    def test_disconnected_contact_lengths_cannot_be_added_to_fit_one_door(self):
        api = self.api()
        comb = OrthogonalPolygon(((0, 0), (3000, 0), (3000, 600), (2000, 600),
                                  (2000, 2400), (3000, 2400), (3000, 3000), (0, 3000)))
        result = LayoutResult(1, {"main": comb, "corner": Rect(3000, 0, 3000, 3000)})
        raw = _program().to_dict()
        raw["rooms"][0].update(target_area=7.2, min_area=7.2, max_area=7.2)
        raw["rooms"][1].update(target_area=9, min_area=9, max_area=9)
        spec = LayoutIR.from_mapping(raw)

        with self.assertRaisesRegex(ValueError, "contiguous.*900|900.*contiguous"):
            api.build_orthogonal_wall_plan(spec, result)
        with self.assertRaises(ValueError):
            api.export_orthogonal_draft_dxf(self.root / "bad.dxf", spec, result)
        self.assertFalse((self.root / "bad.dxf").exists())

    def test_door_order_is_stable_under_room_and_placement_order(self):
        api = self.api()
        first = api.build_orthogonal_wall_plan(_program(), _result())
        second = api.build_orthogonal_wall_plan(replace(_program(), rooms=tuple(reversed(_program().rooms))),
                                               LayoutResult(1, dict(reversed(list(_result().placements.items())))))
        self.assertEqual(first.openings, second.openings)
        self.assertTrue(first.geometry.equals(second.geometry))

    def test_exterior_entry_uses_actual_exterior_segment_and_preserves_source_offset(self):
        api = self.api()
        raw = _program().to_dict()
        raw["external_entry"] = {"id": "entry", "room_id": "main", "side": "left", "offset_mm": 1000, "width_mm": 900}
        plan = api.build_orthogonal_wall_plan(LayoutIR.from_mapping(raw), _result())
        entry = next(door for door in plan.openings if door.external)
        self.assertEqual((entry.fixed, entry.start, entry.end, entry.id), (0, 550, 1450, "entry"))
        self.assertFalse(plan.geometry.covers(Point(0, 1000)))
        self.assertTrue(plan.geometry.covers(Point(0, 2500)))

    def test_single_segment_manual_door_preserves_legacy_rectangle_geometry_and_offset(self):
        api = self.api()
        raw = _program().to_dict()
        for room in raw["rooms"]:
            room.update(target_area=18, min_area=18, max_area=18)
        raw["doors"] = [{"id": "manual", "room_a": "main", "room_b": "corner", "offset_mm": 1500, "width_mm": 900}]
        spec = LayoutIR.from_mapping(raw)
        rectangles = LayoutResult(1, {"main": Rect(0, 0, 3000, 6000), "corner": Rect(3000, 0, 3000, 6000)})
        polygon = LayoutResult(1, {"main": OrthogonalPolygon.from_rect(rectangles.placements["main"]),
                                   "corner": rectangles.placements["corner"]})

        legacy = build_wall_plan(spec, rectangles)
        plan = api.build_orthogonal_wall_plan(spec, polygon)

        self.assertEqual(plan.openings, legacy.openings)
        self.assertEqual(len(plan.openings), 1)
        self.assertEqual((plan.openings[0].start, plan.openings[0].end), (1050, 1950))
        self.assertTrue(plan.geometry.equals(legacy.geometry))
        raw["doors"][0]["offset_mm"] = 200
        with self.assertRaisesRegex(ValueError, "does not fit"):
            api.build_orthogonal_wall_plan(LayoutIR.from_mapping(raw), polygon)

    def test_exterior_entry_cannot_use_polygon_bounds_side_that_has_no_exterior_edge(self):
        api = self.api()
        raw = _program().to_dict()
        raw["external_entry"] = {"id": "entry", "room_id": "main", "side": "top", "offset_mm": 3000, "width_mm": 900}
        with self.assertRaisesRegex(ValueError, "does not fit"):
            api.build_orthogonal_wall_plan(LayoutIR.from_mapping(raw), _result())

    def test_unsupported_openings_raise_explicit_capability_evidence_before_writing(self):
        api = self.api()
        for mutation in ("daylight", "window", "ambiguous_manual_door"):
            with self.subTest(mutation=mutation):
                raw = _program().to_dict()
                if mutation == "daylight":
                    raw["rooms"][0]["needs_daylight"] = True
                elif mutation == "window":
                    raw["windows"] = [{"id": "window", "room_id": "main", "side": "left", "offset_mm": 2000, "width_mm": 1200}]
                else:
                    raw["doors"] = [{"id": "door", "room_a": "main", "room_b": "corner", "offset_mm": 2000, "width_mm": 900}]
                with self.assertRaises(api.ProjectionCapabilityError) as caught:
                    api.export_orthogonal_draft_dxf(self.root / "bad.dxf", LayoutIR.from_mapping(raw), _result())
                self.assertEqual(caught.exception.to_dict()["status"], "UNKNOWN")
                self.assertFalse((self.root / "bad.dxf").exists())

    def test_overlap_cutout_unknown_identity_and_nonfinite_geometry_are_rejected(self):
        api = self.api()
        bad_results = (LayoutResult(1, {"main": _l_shape(), "corner": Rect(1000, 1000, 4000, 4000)}),
                       LayoutResult(1, {"main": _l_shape(), "unknown": Rect(2000, 2000, 4000, 4000)}),
                       LayoutResult(1, {"main": _l_shape(), "corner": Rect(float("nan"), 2000, 4000, 4000)}))
        for result in bad_results:
            with self.subTest(result=result):
                with self.assertRaises(ValueError):
                    api.export_orthogonal_draft_dxf(self.root / "bad.dxf", _program(), result)
                self.assertFalse((self.root / "bad.dxf").exists())
        raw = _program().to_dict()
        raw["boundary"]["cutouts"] = [{"x": 0, "y": 0, "width": 1000, "height": 1000}]
        with self.assertRaises(ValueError):
            api.build_orthogonal_wall_plan(LayoutIR.from_mapping(raw), _result())

    def test_room_text_anchor_stays_inside_l_shape_instead_of_its_empty_bbox_center(self):
        api = self.api()
        path = self.root / "draft.dxf"
        api.export_orthogonal_draft_dxf(path, _program(), _result())
        labels = ezdxf.readfile(path).modelspace().query('TEXT[layer=="A-TEXT"]')
        main = next(label for label in labels if label.dxf.text.startswith("main "))
        self.assertTrue(_l_shape().contains_point(tuple(main.dxf.align_point)[:2]))
        self.assertNotEqual(tuple(main.dxf.align_point)[:2], (3000, 3000))

    def test_malformed_typed_source_program_cannot_be_hashed_as_canonical_provenance(self):
        api = self.api()
        source = _program()
        malformed = (replace(source, project_name=[]), replace(source, grid_mm=True),
                     replace(source, rooms=(replace(source.rooms[0], type=[]), source.rooms[1])),
                     replace(source, rooms=(replace(source.rooms[0], required_adjacency=("unknown",)), source.rooms[1])))
        for index, spec in enumerate(malformed):
            with self.subTest(index=index):
                path = self.root / f"invalid-{index}.dxf"
                with self.assertRaises(ValueError):
                    api.export_orthogonal_draft_dxf(path, spec, _result())
                self.assertFalse(path.exists())


def _program():
    return LayoutIR.from_mapping({
        "project_name": "Polygon source fixture", "boundary": {"width": 6000, "height": 6000},
        "entry_room": "main", "wall_thickness_mm": 200, "door_width_mm": 900,
        "rooms": [{"id": "main", "type": "process", "target_area": 20, "min_area": 20, "max_area": 20,
                   "required_adjacency": ["corner"]},
                  {"id": "corner", "type": "support", "target_area": 16, "min_area": 16, "max_area": 16}],
    })


def _l_shape():
    return OrthogonalPolygon(((0, 0), (6000, 0), (6000, 2000), (2000, 2000), (2000, 6000), (0, 6000)))


def _result():
    return LayoutResult(1, {"main": _l_shape(), "corner": Rect(2000, 2000, 4000, 4000)})

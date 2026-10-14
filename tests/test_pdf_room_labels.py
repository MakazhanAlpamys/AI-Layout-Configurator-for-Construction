"""Room lettering avoids actual painted equipment ink in the saved main PDF.

The oracle reads font metrics, text transforms and graphics-state paint from
the artifact. Supplied placements stay literal; no annotation planner or
solver supplies expected caption positions.
"""

import copy
from dataclasses import replace
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from pypdf import PdfReader
from pypdf.generic import ContentStream
from reportlab.pdfbase import pdfmetrics
from shapely.affinity import affine_transform
from shapely.geometry import LineString, Polygon, box
from shapely.ops import unary_union

from layout_configurator.equipment import EquipmentLayoutResult, clearance_rect
from layout_configurator.export import export_building_pdf
from layout_configurator.facility import FacilityDrawingProfile
from layout_configurator.flows import FlowRoute, FlowRoutingResult
from layout_configurator.models import Rect
from layout_configurator.walls import build_wall_plan
import test_pdf_equipment_labels as equipment_pdf_fixture
import test_process_installation_review as process_fixture


BLUE_BODY = (.05, .25, .65)
BLUE_CLEARANCE = (.35, .35, .75)


def color_is(actual, expected):
    return all(abs(first - second) < .00001 for first, second in zip(actual, expected))


def saved_lettering(data):
    """Reuse the independent glyph/CM/TM oracle and check actual text paint."""
    records = equipment_pdf_fixture.saved_text(data)
    reader = PdfReader(io.BytesIO(data), strict=True)
    paint = []
    for page_number, page in enumerate(reader.pages):
        state = {"render_mode": 0, "fill_alpha": 1.0, "fill": (0.0, 0.0, 0.0)}
        stack = []
        for values, operator in ContentStream(page.get_contents(), reader).operations:
            if operator == b"q":
                stack.append(copy.deepcopy(state))
            elif operator == b"Q":
                state = stack.pop()
            elif operator == b"Tr":
                state["render_mode"] = int(values[0])
            elif operator == b"rg":
                state["fill"] = tuple(float(value) for value in values)
            elif operator == b"g":
                state["fill"] = (float(values[0]),) * 3
            elif operator == b"gs":
                graphics = page["/Resources"]["/ExtGState"][values[0]].get_object()
                state["fill_alpha"] = float(graphics.get("/ca", state["fill_alpha"]))
            elif operator in (b"Tj", b"TJ"):
                for value in [values[0]] if operator == b"Tj" else values[0]:
                    if isinstance(value, str):
                        paint.append({"text": str(value), "page": page_number, **state})
    if len(records) != len(paint):
        raise AssertionError("Glyph and visible paint inventories disagree")
    for record, visibility in zip(records, paint):
        if (record["text"], record["page"]) != (visibility["text"], visibility["page"]):
            raise AssertionError("Visible paint does not identify the same literal glyphs")
        record.update(visibility)
    return records


def saved_painted_paths(data):
    """Capture actual path CM, stroke width, dash, caps and paint operators."""
    reader = PdfReader(io.BytesIO(data), strict=True)
    records = []
    for page_number, page in enumerate(reader.pages):
        state = {"matrix": (1.0, 0.0, 0.0, 1.0, 0.0, 0.0), "stroke": (0.0, 0.0, 0.0),
                 "width": 1.0, "dash": ((), 0.0), "cap": 0, "join": 0, "miter": 10.0}
        stack, paths = [], []
        for values, operator in ContentStream(page.get_contents(), reader).operations:
            if operator == b"q":
                stack.append(copy.deepcopy(state))
            elif operator == b"Q":
                state = stack.pop()
            elif operator == b"cm":
                state["matrix"] = equipment_pdf_fixture.compose(state["matrix"], tuple(float(value) for value in values))
            elif operator == b"RG":
                state["stroke"] = tuple(float(value) for value in values)
            elif operator == b"G":
                state["stroke"] = (float(values[0]),) * 3
            elif operator == b"w":
                state["width"] = float(values[0])
            elif operator == b"d":
                state["dash"] = (tuple(float(value) for value in values[0]), float(values[1]))
            elif operator == b"J":
                state["cap"] = int(values[0])
            elif operator == b"j":
                state["join"] = int(values[0])
            elif operator == b"M":
                state["miter"] = float(values[0])
            elif operator == b"m":
                paths.append({"points": [equipment_pdf_fixture.point(state["matrix"], *map(float, values))], "closed": False})
            elif operator == b"l":
                paths[-1]["points"].append(equipment_pdf_fixture.point(state["matrix"], *map(float, values)))
            elif operator == b"h":
                paths[-1]["closed"] = True
            elif operator == b"re":
                x, y, width, height = map(float, values)
                paths.append({"points": [equipment_pdf_fixture.point(state["matrix"], px, py) for px, py in
                    ((x, y), (x + width, y), (x + width, y + height), (x, y + height))], "closed": True})
            elif operator in (b"c", b"v", b"y"):
                raise AssertionError("This straight-path fixture must not silently omit a painted curve")
            elif operator in (b"S", b"s", b"B", b"B*", b"b", b"b*", b"f", b"f*"):
                if operator in (b"s", b"b", b"b*"):
                    paths[-1]["closed"] = True
                records.extend({**path, **copy.deepcopy(state), "page": page_number,
                                "paint": operator, "stroked": operator not in (b"f", b"f*")} for path in paths)
                paths = []
            elif operator == b"n":
                paths = []
    return records


def centerline(path):
    points = list(path["points"])
    if path["closed"] and points[-1] != points[0]:
        points.append(points[0])
    return LineString(points)


def painted_stroke(path):
    """Expand in the actual paint user space, then apply the actual CTM."""
    if not path["stroked"] or path["dash"][0]:
        raise AssertionError("The collision oracle requires a genuinely painted solid stroke")
    a, b, c, d, e, f = path["matrix"]
    determinant = a * d - b * c
    if determinant == 0 or path["width"] <= 0:
        raise AssertionError("The fixture stroke must have a representable nonzero physical width")
    local = affine_transform(centerline(path), [d/determinant, -c/determinant,
        -b/determinant, a/determinant, (c*f-d*e)/determinant, (b*e-a*f)/determinant])
    ink = local.buffer(path["width"] / 2, cap_style={0: 2, 1: 1, 2: 3}[path["cap"]],
                       join_style={0: 2, 1: 1, 2: 3}[path["join"]], mitre_limit=path["miter"])
    return affine_transform(ink, [a, c, b, d, e, f])


class PDFRoomLabelTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.raw, self.building, self.layout, self.equipment = process_fixture.installation_process_fixture()
        self.source = self.root / "original-source.json"
        self.original_bytes = (json.dumps(self.raw, ensure_ascii=False, indent=2, allow_nan=False) + "\r\n").encode("utf-8")
        self.source.write_bytes(self.original_bytes)

    def export(self, *, equipment=None, flows=None, drawing=None, name="main.pdf"):
        equipment = self.equipment if equipment is None else equipment
        before = copy.deepcopy((self.raw, self.building.to_dict(), self.layout.to_dict(),
                                equipment.to_dict(self.building), None if flows is None else flows.to_dict()))
        destination = self.root / name
        with patch("ortools.sat.python.cp_model.CpSolver.solve", side_effect=AssertionError("Supplied drawing geometry must not be solved")) as solve:
            try:
                export_building_pdf(destination, self.building, self.layout, equipment, flows=flows, drawing_profile=drawing)
            finally:
                solve.assert_not_called()
                self.assertEqual((self.raw, self.building.to_dict(), self.layout.to_dict(), equipment.to_dict(self.building),
                                  None if flows is None else flows.to_dict()), before)
                self.assertEqual(self.source.read_bytes(), self.original_bytes)
        return destination.read_bytes()

    def label(self, records, text):
        rows = [row for row in records if row["text"] == text]
        self.assertEqual(len(rows), 1, "Required full literal caption omitted or duplicated: " + text)
        row = rows[0]
        self.assertEqual(row["render_mode"], 0, "A required caption cannot be hidden with an invisible render mode")
        self.assertEqual(row["fill_alpha"], 1.0, "A required caption must retain opaque lettering")
        return row

    def projection(self, data):
        boundaries = [path for path in saved_painted_paths(data) if path["closed"] and len(path["points"]) == 4
                      and color_is(path["stroke"], (0, 0, 0)) and path["width"] == .5
                      and path["dash"] == ((3.0, 2.0), 0.0) and path["stroked"]]
        self.assertEqual(len(boundaries), 1)
        left, bottom, right, top = Polygon(boundaries[0]["points"]).bounds
        scale = (right - left) / self.building.layout.boundary.width_mm
        self.assertAlmostEqual(scale, (top - bottom) / self.building.layout.boundary.height_mm, places=6)
        return scale, left, bottom

    def assert_host_and_plot(self, data, row, room_id):
        scale, ox, oy = self.projection(data)
        rect = self.layout.placements[room_id]
        expected = (ox + rect.x*scale, oy + rect.y*scale, ox + rect.right*scale, oy + rect.top*scale)
        for actual, boundary in zip(row["bounds"][:2], expected[:2]):
            self.assertGreaterEqual(actual, boundary - .001)
        for actual, boundary in zip(row["bounds"][2:], expected[2:]):
            self.assertLessEqual(actual, boundary + .001)
        page = PdfReader(io.BytesIO(data)).pages[row["page"]]
        self.assertTrue(box(0, 0, float(page.mediabox.width), float(page.mediabox.height)).covers(box(*row["bounds"])))

    def assert_room_avoids_blue_ink(self, data, row):
        paths = [path for path in saved_painted_paths(data) if path["stroked"] and color_is(path["stroke"], BLUE_BODY)]
        self.assertTrue(paths, "The saved drawing must retain the actual literal equipment ink")
        for path in paths:
            self.assertEqual(path["dash"], ((), 0.0))
            self.assertLessEqual(box(*row["bounds"]).intersection(painted_stroke(path)).area, .0001,
                "Room glyph bounds meet a body, symbol or guide's actual painted half-width")

    def assert_literal_paths(self, data, equipment):
        scale, ox, oy = self.projection(data)
        paths = saved_painted_paths(data)
        bodies = [path for path in paths if path["closed"] and len(path["points"]) == 4
                  and path["stroked"] and color_is(path["stroke"], BLUE_BODY)]
        self.assertEqual(len(bodies), len(equipment.placements))
        clearances = [path for path in paths if path["closed"] and path["stroked"]
                      and color_is(path["stroke"], BLUE_CLEARANCE)]
        self.assertEqual(len(clearances), len(equipment.placements))
        specs = {item.id: item for item in self.building.equipment}
        for placement in equipment.placements:
            for rect, candidates, width, dash in (
                    (placement.rect, bodies, .8, ((), 0.0)),
                    (clearance_rect(specs[placement.equipment_id], placement.rect, placement.rotated), clearances, .45, ((3.0, 2.0), 0.0))):
                expected = box(ox + rect.x*scale, oy + rect.y*scale, ox + rect.right*scale, oy + rect.top*scale).boundary
                matches = [path for path in candidates if centerline(path).hausdorff_distance(expected) <= .001]
                self.assertEqual(len(matches), 1, "A literal source footprint or clearance was altered")
                self.assertEqual(matches[0]["width"], width)
                self.assertEqual(matches[0]["dash"], dash)
                self.assertEqual(matches[0]["paint"], b"S")
        walls = [centerline(path) for path in paths if path["stroked"] and path["closed"]
                 and path["width"] == .7 and color_is(path["stroke"], (0, 0, 0))]
        expected_walls = [LineString([(ox + x*scale, oy + y*scale) for x, y in ring])
                          for ring in build_wall_plan(self.building.layout, self.layout).rings()]
        self.assertEqual(len(walls), len(expected_walls))
        self.assertLessEqual(unary_union(walls).hausdorff_distance(unary_union(expected_walls)), .001)

    def test_observed_clear_work_caption_avoids_actual_body_stroke_and_retains_legacy_caption_baselines(self):
        data = self.export()
        records = saved_lettering(data)
        room = self.label(records, "work (36.0 m2)")
        entry = self.label(records, "entry (36.0 m2)")
        press, neighbor = [self.label(records, name) for name in ("press", "neighbor")]
        scale, ox, oy = self.projection(data)
        self.assertEqual((room["font"], room["size"], room["fill"]), ("Helvetica", 11, (0, 0, 0)))
        self.assertAlmostEqual((room["bounds"][0] + room["bounds"][2])/2, ox + 9000*scale, delta=.001)
        self.assert_room_avoids_blue_ink(data, room)
        self.assertGreater(abs(room["baseline"][1] - (oy + 3000*scale)), .001,
                           "The observed work caption must leave its intersecting centered baseline")
        self.assertEqual((entry["font"], entry["size"], entry["fill"]), ("Helvetica", 11, (0, 0, 0)))
        self.assertAlmostEqual((entry["bounds"][0] + entry["bounds"][2])/2, ox + 3000*scale, delta=.001)
        self.assertAlmostEqual(entry["baseline"][1], oy + 3000*scale, delta=.001)
        for label, x, y, displacement in ((press, 9500, 3000, 11.2), (neighbor, 3500, 4900, 0)):
            self.assertEqual((label["font"], label["size"]), ("Helvetica-Bold", 7))
            self.assertTrue(color_is(label["fill"], BLUE_BODY))
            self.assertAlmostEqual((label["bounds"][0] + label["bounds"][2])/2, ox + x*scale, delta=.001)
            self.assertAlmostEqual(label["baseline"][1], oy + y*scale + displacement, delta=.001,
                                   msg="Room placement must not replan the current QA.24 equipment caption")
            self.assertLessEqual(box(*room["bounds"]).intersection(box(*label["bounds"])).area, .0001)
        dimensions = [row for row in records if row["text"] == "6000 x 6000 mm"]
        self.assertEqual(len(dimensions), 2)
        for dimension in dimensions:
            self.assertEqual((dimension["font"], dimension["size"]), ("Helvetica", 4.5))
            self.assertAlmostEqual(dimension["baseline"][1], oy + 5, delta=.001)
        self.assert_host_and_plot(data, room, "work")
        self.assert_host_and_plot(data, entry, "entry")
        self.assert_literal_paths(data, self.equipment)

    def test_coincident_room_body_and_generic_symbol_keep_literal_paths_and_readable_full_room_text(self):
        equipment = EquipmentLayoutResult((replace(self.equipment.placements[0],
            rect=Rect(8500.0, 2600.0, 1000.0, 800.0)), self.equipment.placements[1]))
        drawing = FacilityDrawingProfile(equipment_symbols=(("press", "generic"),))
        data = self.export(equipment=equipment, drawing=drawing)
        room = self.label(saved_lettering(data), "work (36.0 m2)")
        self.assertEqual((room["font"], room["size"]), ("Helvetica", 11))
        self.assert_room_avoids_blue_ink(data, room)
        self.assert_host_and_plot(data, room, "work")
        self.assert_literal_paths(data, equipment)
        scale, ox, oy = self.projection(data)
        rect = equipment.placements[0].rect
        expected = [LineString([(ox + rect.x*scale, oy + rect.y*scale), (ox + rect.right*scale, oy + rect.top*scale)]),
                    LineString([(ox + rect.x*scale, oy + rect.top*scale), (ox + rect.right*scale, oy + rect.y*scale)])]
        symbols = [centerline(path) for path in saved_painted_paths(data) if not path["closed"] and path["stroked"]
                   and color_is(path["stroke"], BLUE_BODY) and path["width"] == .8]
        self.assertEqual(len(symbols), 2)
        self.assertLessEqual(unary_union(symbols).hausdorff_distance(unary_union(expected)), .001)

    def test_room_caption_misses_centerline_but_must_also_clear_actual_half_stroke_width(self):
        reference = self.export(equipment=EquipmentLayoutResult(()), name="room-only.pdf")
        original_room = self.label(saved_lettering(reference), "work (36.0 m2)")
        scale, ox, oy = self.projection(reference)
        x = (original_room["bounds"][2] + .2 - ox) / scale
        equipment = EquipmentLayoutResult((replace(self.equipment.placements[0],
            rect=Rect(x, 2600.0, 1000.0, 800.0)), self.equipment.placements[1]))
        data = self.export(equipment=equipment, name="stroke-grazing.pdf")
        expected = box(ox + x*scale, oy + 2600*scale, ox + (x + 1000)*scale, oy + 3400*scale).boundary
        body = next(path for path in saved_painted_paths(data) if path["closed"] and color_is(path["stroke"], BLUE_BODY)
                    and centerline(path).hausdorff_distance(expected) <= .001)
        glyphs = box(*original_room["bounds"])
        self.assertAlmostEqual(centerline(body).distance(glyphs), .2, delta=.001)
        self.assertGreater(glyphs.intersection(painted_stroke(body)).area, .1,
                           "The independent fixture must actually meet the painted half-width, not the centerline")
        room = self.label(saved_lettering(data), "work (36.0 m2)")
        self.assertEqual((room["font"], room["size"]), ("Helvetica", 11))
        self.assert_room_avoids_blue_ink(data, room)
        self.assert_host_and_plot(data, room, "work")
        self.assert_literal_paths(data, equipment)

    def test_tiny_native_symbol_miter_ink_outside_body_does_not_strike_room_caption(self):
        reference = self.export(equipment=EquipmentLayoutResult(()), name="miter-reference.pdf")
        original = self.label(saved_lettering(reference), "work (36.0 m2)")
        scale, ox, oy = self.projection(reference)
        # The literal body and its .4 pt half-width miss the glyphs. The
        # actual narrow native polyline's miter can extend beyond that box.
        bottom = (original["bounds"][3] + .5 - oy) / scale
        self.building = replace(self.building, equipment=tuple(
            replace(spec, width_mm=1.0, depth_mm=10.0, clearance_front_mm=0.0,
                    clearance_back_mm=0.0, clearance_left_mm=0.0, clearance_right_mm=0.0)
            if spec.id == "press" else spec for spec in self.building.equipment))
        equipment = EquipmentLayoutResult((replace(self.equipment.placements[0],
            rect=Rect(8999.5, bottom, 1.0, 10.0)), self.equipment.placements[1]))
        drawing = FacilityDrawingProfile(equipment_symbols=(("press", "process_vessel"),))
        data = self.export(equipment=equipment, drawing=drawing, name="native-miter.pdf")
        paths = [path for path in saved_painted_paths(data) if path["stroked"] and color_is(path["stroke"], BLUE_BODY)]
        body = next(path for path in paths if path["closed"] and len(path["points"]) == 4
                    and abs(min(x for x, _ in path["points"]) - (ox + 8999.5*scale)) < .001)
        symbol = next(path for path in paths if path["closed"] and len(path["points"]) == 16)
        glyphs = box(*original["bounds"])
        self.assertEqual((symbol["width"], symbol["join"], symbol["miter"]), (.8, 0, 10.0))
        self.assertEqual(glyphs.intersection(painted_stroke(body)).area, 0)
        self.assertGreater(glyphs.intersection(painted_stroke(symbol)).area, .001,
                           "The actual miter must extend beyond the clear literal body stroke")
        room = self.label(saved_lettering(data), "work (36.0 m2)")
        self.assert_room_avoids_blue_ink(data, room)
        self.assert_host_and_plot(data, room, "work")
        self.assert_literal_paths(data, equipment)
        self.assertEqual((room["font"], room["size"]), ("Helvetica", 11))

    def test_real_flow_label_reserves_the_final_room_caption_and_frozen_equipment_captions(self):
        reference = self.export(name="without-flow.pdf")
        original = saved_lettering(reference)
        room = self.label(original, "work (36.0 m2)")
        scale, ox, oy = self.projection(reference)
        x = (room["bounds"][0] + 3 - ox - 2) / scale
        y = (room["baseline"][1] - oy - 2) / scale - 300
        route = FlowRoute("room_annotation_fixture", "work", "work", "work", "work", ("work",),
                          ((x - 400, y), (x, y)), 20.0)
        flows = FlowRoutingResult((route,))
        data = self.export(flows=flows, name="with-flow.pdf")
        records = saved_lettering(data)
        flow = self.label(records, "FLOW room_annotation_fixture [unspecified] / 20 mm")
        self.assertEqual((flow["font"], flow["size"]), ("Helvetica", 5))
        for text in ("work (36.0 m2)", "entry (36.0 m2)", "press", "neighbor"):
            final = self.label(records, text)
            baseline = self.label(original, text)
            for actual, expected_value in zip(final["baseline"], baseline["baseline"]):
                self.assertAlmostEqual(actual, expected_value, delta=.001)
            self.assertLessEqual(box(*flow["bounds"]).intersection(box(*final["bounds"])).area, .0001,
                                 "Flow lettering paints over a finalized room or equipment reservation")
        self.assert_room_avoids_blue_ink(data, self.label(records, "work (36.0 m2)"))
        actual_route = [path for path in saved_painted_paths(data) if path["stroked"]
                        and color_is(path["stroke"], (.9, .45, .05))]
        self.assertEqual(len(actual_route), 1)
        self.assertEqual((actual_route[0]["width"], actual_route[0]["dash"], actual_route[0]["paint"]),
                         (1.0, ((5.0, 3.0), 0.0), b"S"))
        expected_route = LineString([(ox + px*scale, oy + py*scale) for px, py in route.points])
        self.assertLessEqual(centerline(actual_route[0]).hausdorff_distance(expected_route), .001)
        self.assert_host_and_plot(data, flow, "work")
        self.assert_literal_paths(data, self.equipment)

    def test_unplaceable_full_room_caption_rejects_without_overwriting_previous_destination_or_source(self):
        identifier = "work_" + "x" * 600
        reference = self.export(equipment=EquipmentLayoutResult(()), name="original-room-only.pdf")
        scale, _, _ = self.projection(reference)
        rect = self.layout.placements["work"]
        literal = f"{identifier} ({rect.area_m2:.1f} m2)"
        self.assertGreater(pdfmetrics.stringWidth(literal, "Helvetica", 11), rect.width * scale,
                           "No vertical caption candidate can fit the full literal source name in its host")
        rooms = tuple(replace(room, id=identifier if room.id == "work" else room.id,
            required_adjacency=tuple(identifier if other == "work" else other for other in room.required_adjacency))
            for room in self.building.layout.rooms)
        doors = tuple(replace(door, room_a=identifier if door.room_a == "work" else door.room_a,
                              room_b=identifier if door.room_b == "work" else door.room_b)
                      for door in self.building.layout.doors)
        self.building = replace(self.building, layout=replace(self.building.layout, rooms=rooms, doors=doors),
            equipment=tuple(replace(spec, room_id=identifier if spec.room_id == "work" else spec.room_id)
                            for spec in self.building.equipment))
        self.layout = replace(self.layout, placements={identifier if key == "work" else key: value
                                                       for key, value in self.layout.placements.items()})
        output = self.root / "retained.pdf"
        sentinel = b"previous user-owned main PDF bytes"
        output.write_bytes(sentinel)
        with self.assertRaisesRegex(ValueError, "Room annotation density exceeds readable plan capacity"):
            self.export(equipment=EquipmentLayoutResult(()), name=output.name)
        self.assertEqual(output.read_bytes(), sentinel)
        self.assertEqual(self.source.read_bytes(), self.original_bytes)


if __name__ == "__main__":
    unittest.main()

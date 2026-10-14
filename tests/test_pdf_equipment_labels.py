"""Persisted main-PDF room, equipment and flow text remain visibly distinct.

Fixtures contain only supplied placements and drawing routes. Tests inspect
actual PDF text transforms and painted paths, not annotation helper outputs.
"""

import copy
from dataclasses import replace
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from pypdf import PdfReader
from pypdf.generic import ContentStream
from reportlab.pdfbase import pdfmetrics
from shapely.geometry import LineString, Polygon, box
from shapely.ops import unary_union

from layout_configurator.equipment import EquipmentLayoutResult
from layout_configurator.export import export_building_pdf
from layout_configurator.flows import FlowRoute, FlowRoutingResult
from layout_configurator.models import Rect
from layout_configurator.walls import build_wall_plan
from test_process_installation_review import installation_process_fixture


def compose(first, second):
    a, b, c, d, e, f = first
    g, h, i, j, k, l = second
    return a*g + c*h, b*g + d*h, a*i + c*j, b*i + d*j, a*k + c*l + e, b*k + d*l + f


def point(matrix, x, y):
    a, b, c, d, e, f = matrix
    return a*x + c*y + e, b*x + d*y + f


def saved_text(pdf_bytes):
    """Actual displayed Type1 text bounds from PDF fonts, sizes and CM/TM."""
    reader = PdfReader(io.BytesIO(pdf_bytes), strict=True)
    records = []
    for page_number, page in enumerate(reader.pages):
        fonts = page["/Resources"]["/Font"]
        state = {"matrix": (1, 0, 0, 1, 0, 0), "font": None, "size": 0, "leading": 0, "horizontal": 1.0}
        stack, tm = [], (1, 0, 0, 1, 0, 0)

        def show(text):
            name = str(fonts[state["font"]].get_object()["/BaseFont"])[1:]
            width = pdfmetrics.stringWidth(text, name, state["size"]) * state["horizontal"]
            ascent, descent = pdfmetrics.getAscentDescent(name, state["size"])
            matrix = compose(state["matrix"], tm)
            corners = [point(matrix, x, y) for x, y in ((0, descent), (width, descent), (width, ascent), (0, ascent))]
            records.append({"text": text, "font": name, "size": state["size"], "page": page_number,
                "baseline": point(matrix, 0, 0), "bounds": (min(x for x, _ in corners), min(y for _, y in corners),
                    max(x for x, _ in corners), max(y for _, y in corners))})
            return width

        # Observe the matrices at the actual paint operator. A visitor invoked
        # after T* can expose a later baseline rather than the painted one.
        for values, operator in ContentStream(page.get_contents(), reader).operations:
            if operator == b"q":
                stack.append(copy.deepcopy(state))
            elif operator == b"Q":
                state = stack.pop()
            elif operator == b"cm":
                state["matrix"] = compose(state["matrix"], tuple(float(value) for value in values))
            elif operator == b"BT":
                tm = (1, 0, 0, 1, 0, 0)
            elif operator == b"Tf":
                state["font"], state["size"] = values[0], float(values[1])
            elif operator == b"TL":
                state["leading"] = float(values[0])
            elif operator == b"Tz":
                state["horizontal"] = float(values[0])/100
            elif operator == b"Tm":
                tm = tuple(float(value) for value in values)
            elif operator in (b"Td", b"TD"):
                if operator == b"TD":
                    state["leading"] = -float(values[1])
                tm = compose(tm, (1, 0, 0, 1, float(values[0]), float(values[1])))
            elif operator == b"T*":
                tm = compose(tm, (1, 0, 0, 1, 0, -state["leading"]))
            elif operator in (b"Tj", b"TJ"):
                for value in [values[0]] if operator == b"Tj" else values[0]:
                    advance = show(str(value)) if isinstance(value, str) else -float(value)/1000*state["size"]*state["horizontal"]
                    tm = compose(tm, (1, 0, 0, 1, advance, 0))
    return records


def saved_paths(pdf_bytes):
    """Read real painted straight paths through PDF graphics transforms."""
    reader = PdfReader(io.BytesIO(pdf_bytes), strict=True)
    records = []
    for page in reader.pages:
        state = {"matrix": (1, 0, 0, 1, 0, 0), "stroke": (0, 0, 0)}
        stack, paths = [], []
        for values, operator in ContentStream(page.get_contents(), reader).operations:
            if operator == b"q":
                stack.append(copy.deepcopy(state))
            elif operator == b"Q":
                state = stack.pop()
            elif operator == b"cm":
                state["matrix"] = compose(state["matrix"], tuple(float(value) for value in values))
            elif operator == b"RG":
                state["stroke"] = tuple(float(value) for value in values)
            elif operator == b"m":
                paths.append(([point(state["matrix"], *map(float, values))], False))
            elif operator == b"l":
                paths[-1][0].append(point(state["matrix"], *map(float, values)))
            elif operator == b"h":
                paths[-1] = (paths[-1][0], True)
            elif operator == b"re":
                x, y, width, height = map(float, values)
                paths.append(([point(state["matrix"], px, py) for px, py in
                    ((x, y), (x + width, y), (x + width, y + height), (x, y + height))], True))
            elif operator in (b"S", b"s", b"B", b"B*", b"b", b"b*", b"f", b"f*"):
                records.extend({"points": pts, "closed": closed, "stroke": state["stroke"]} for pts, closed in paths)
                paths = []
            elif operator == b"n":
                paths = []
    return records


class PDFEquipmentLabelTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.raw, self.building, self.layout, self.equipment = installation_process_fixture()

    def export(self, *, equipment=None, flows=None, name="main.pdf"):
        path = self.root / name
        with patch("ortools.sat.python.cp_model.CpSolver.solve",
                   side_effect=AssertionError("Drawing annotation must not solve or alter supplied geometry")):
            export_building_pdf(path, self.building, self.layout, self.equipment if equipment is None else equipment,
                                flows=flows)
        return path.read_bytes()

    def label(self, records, text):
        rows = [row for row in records if row["text"] == text]
        self.assertEqual(len(rows), 1, f"Required literal PDF label omitted or duplicated: {text}")
        return rows[0]

    def overlap(self, first, second):
        return box(*first["bounds"]).intersection(box(*second["bounds"])).area

    def projection(self, pdf_bytes):
        rectangles = [row for row in saved_paths(pdf_bytes) if row["closed"] and len(row["points"]) == 4]
        outer = max(rectangles, key=lambda row: Polygon(row["points"]).area)
        polygon = Polygon(outer["points"])
        left, bottom, right, top = polygon.bounds
        scale_x = (right - left) / self.building.layout.boundary.width_mm
        scale_y = (top - bottom) / self.building.layout.boundary.height_mm
        self.assertAlmostEqual(scale_x, scale_y, places=6)
        return scale_x, left, bottom

    def assert_on_page(self, data, records):
        reader = PdfReader(io.BytesIO(data))
        scale, ox, oy = self.projection(data)
        right_plot = ox + self.building.layout.boundary.width_mm*scale
        top_plot = oy + self.building.layout.boundary.height_mm*scale
        for row in records:
            page = reader.pages[row["page"]]
            left, bottom, right, top = row["bounds"]
            self.assertGreaterEqual(left, 0)
            self.assertGreaterEqual(bottom, 0)
            self.assertLessEqual(right, float(page.mediabox.width))
            self.assertLessEqual(top, float(page.mediabox.height))
            self.assertGreaterEqual(left, ox)
            self.assertGreaterEqual(bottom, oy)
            self.assertLessEqual(right, right_plot)
            self.assertLessEqual(top, top_plot)

    def test_observed_work_and_press_labels_remain_visible_and_separate_in_the_saved_main_pdf(self):
        data = self.export()
        records = saved_text(data)
        room = self.label(records, "work (36.0 m2)")
        equipment = self.label(records, "press")
        self.assertEqual((room["font"], room["size"]), ("Helvetica", 11))
        self.assertEqual((equipment["font"], equipment["size"]), ("Helvetica-Bold", 7))
        self.assertLessEqual(self.overlap(room, equipment), .0001,
                             "The supplied room label and equipment ID overlap in the actual PDF")
        self.assert_on_page(data, [room, equipment])

    def test_clear_neighbor_anchor_source_values_and_literal_wall_and_equipment_paths_stay_unchanged(self):
        original = copy.deepcopy(self.raw)
        models = (self.building.to_dict(), self.layout.to_dict(), self.equipment.to_dict(self.building))
        data = self.export()
        records = saved_text(data)
        neighbor = self.label(records, "neighbor")
        scale, ox, oy = self.projection(data)
        center_x = (neighbor["bounds"][0] + neighbor["bounds"][2]) / 2
        self.assertAlmostEqual(center_x, ox + 3500*scale, delta=.001)
        self.assertAlmostEqual(neighbor["baseline"][1], oy + 4900*scale, delta=.001)
        self.assertEqual((self.building.to_dict(), self.layout.to_dict(), self.equipment.to_dict(self.building)), models)
        self.assertEqual(self.raw, original)
        actual = saved_paths(data)
        bodies = [Polygon(row["points"]) for row in actual if row["closed"] and
                  all(abs(a - b) < .0001 for a, b in zip(row["stroke"], (.05, .25, .65)))]
        expected = []
        for placed in self.equipment.placements:
            rect = placed.rect
            expected.append(box(ox + rect.x*scale, oy + rect.y*scale,
                                ox + rect.right*scale, oy + rect.top*scale))
        self.assertEqual(len(bodies), len(expected))
        # ReportLab serializes page coordinates to limited decimal precision.
        # Bound actual corner displacement in points rather than an absolute
        # area allowance that grows with the fixture's perimeter.
        self.assertLessEqual(unary_union(bodies).hausdorff_distance(unary_union(expected)), .001)
        walls = [Polygon(row["points"]) for row in actual if row["closed"] and len(row["points"]) > 4]
        source = [Polygon([(ox + x*scale, oy + y*scale) for x, y in ring])
                  for ring in build_wall_plan(self.building.layout, self.layout).rings()]
        self.assertEqual(len(walls), len(source))
        self.assertLessEqual(unary_union(walls).hausdorff_distance(unary_union(source)), .001)

    def test_flow_labels_avoid_the_final_equipment_label_reservations(self):
        # Use the actual saved equipment baseline, not an assumed repair
        # offset, to supply a drawing route whose label initially lands there.
        baseline_data = self.export(name="without-flow.pdf")
        equipment = self.label(saved_text(baseline_data), "press")
        scale, ox, oy = self.projection(baseline_data)
        x = (equipment["bounds"][0] - 5 - ox - 2) / scale
        y = (equipment["baseline"][1] - oy - 2) / scale - 300
        route = FlowRoute("annotation_fixture", "work", "work", "work", "work", ("work",),
                          ((x - 400, y), (x, y)), 20.0)
        flows = FlowRoutingResult((route,))
        before = flows.to_dict()
        data = self.export(flows=flows, name="with-flow.pdf")
        records = saved_text(data)
        label = self.label(records, "FLOW annotation_fixture [unspecified] / 20 mm")
        final_equipment = self.label(records, "press")
        self.assertEqual(final_equipment["baseline"], equipment["baseline"],
                         "Flow drawing must reserve the actual finalized equipment annotation")
        self.assertLessEqual(self.overlap(label, final_equipment), .0001,
                             "The flow label paints over the final equipment label")
        self.assertEqual(flows.to_dict(), before)
        self.assert_on_page(data, [label, final_equipment])

    def test_coincident_room_and_body_centers_do_not_trade_text_overlap_for_a_leader_through_room_text(self):
        # The source dimensions and body remain literal; only this supplied
        # placement puts its body center at the actual room center.
        placed = self.equipment.placements[0]
        equipment = EquipmentLayoutResult((replace(placed, rect=Rect(8500.0, 2600.0, 1000.0, 800.0)),
                                           self.equipment.placements[1]))
        original = equipment.to_dict(self.building)
        data = self.export(equipment=equipment)
        records = saved_text(data)
        room, gear = self.label(records, "work (36.0 m2)"), self.label(records, "press")
        self.assertLessEqual(self.overlap(room, gear), .0001)
        room_box = box(*room["bounds"])
        for path in saved_paths(data):
            if not path["closed"] and all(abs(a - b) < .0001 for a, b in zip(path["stroke"], (.05, .25, .65))):
                self.assertLessEqual(LineString(path["points"]).intersection(room_box).length, .0001,
                                     "A displaced equipment annotation leader crosses the room lettering")
        self.assertEqual(equipment.to_dict(self.building), original)
        self.assert_on_page(data, [room, gear])

    def test_later_equipment_text_does_not_cross_an_earlier_displaced_annotation_leader(self):
        # Calibrate supplied positions from the actual saved room text and
        # boundary. The first small body needs a separate caption; the second
        # caption initially lies in the clear gap below it, across its guide.
        reference = self.export(equipment=EquipmentLayoutResult(()), name="room-only.pdf")
        room = self.label(saved_text(reference), "work (36.0 m2)")
        scale, ox, oy = self.projection(reference)
        center_x = (room["bounds"][2] + 2.0 - ox) / scale
        centers_y = [(room["baseline"][1] + offset - oy) / scale for offset in (6.5, 8.8)]
        self.building = replace(self.building, equipment=tuple(replace(spec, room_id="work",
            width_mm=1.0, depth_mm=1.0, clearance_front_mm=0.0, clearance_back_mm=0.0,
            clearance_left_mm=0.0, clearance_right_mm=0.0) for spec in self.building.equipment))
        equipment = EquipmentLayoutResult(tuple(replace(placement, room_id="work",
            rect=Rect(center_x - .5, y - .5, 1.0, 1.0))
            for placement, y in zip(self.equipment.placements, centers_y)))
        original = equipment.to_dict(self.building)

        data = self.export(equipment=equipment, name="leader-order.pdf")
        records = saved_text(data)
        room = self.label(records, "work (36.0 m2)")
        labels = [self.label(records, identifier) for identifier in ("press", "neighbor")]
        for label in labels:
            self.assertEqual((label["font"], label["size"]), ("Helvetica-Bold", 3.5))
            self.assertLessEqual(self.overlap(room, label), .0001)
        self.assertLessEqual(self.overlap(*labels), .0001)
        leaders = [row for row in saved_paths(data) if not row["closed"] and
                   all(abs(a - b) < .0001 for a, b in zip(row["stroke"], (.05, .25, .65)))]
        self.assertTrue(leaders, "A caption separated from a one-millimetre body needs an actual association guide")
        for leader in leaders:
            line = LineString(leader["points"])
            for label in labels:
                self.assertLessEqual(line.intersection(box(*label["bounds"])).length, .0001,
                    "An earlier equipment guide strikes a later equipment caption in the saved PDF")
        self.assertEqual(equipment.to_dict(self.building), original)
        self.assert_on_page(data, [room, *labels])

    def test_unplaceable_literal_identifier_rejects_without_overwriting_a_previous_pdf_or_source(self):
        identifier = "press_" + "x" * 600
        self.building = replace(self.building, equipment=(
            replace(self.building.equipment[0], id=identifier), self.building.equipment[1]))
        equipment = EquipmentLayoutResult((
            replace(self.equipment.placements[0], equipment_id=identifier), self.equipment.placements[1]))
        original = (self.building.to_dict(), equipment.to_dict(self.building), copy.deepcopy(self.raw))
        output = self.root / "retained.pdf"
        sentinel = b"previous user-owned PDF bytes"
        output.write_bytes(sentinel)
        with self.assertRaisesRegex(ValueError, "Equipment annotation density exceeds readable plan capacity"):
            self.export(equipment=equipment, name=output.name)
        self.assertEqual(output.read_bytes(), sentinel)
        self.assertEqual((self.building.to_dict(), equipment.to_dict(self.building), self.raw), original)


if __name__ == "__main__":
    unittest.main()

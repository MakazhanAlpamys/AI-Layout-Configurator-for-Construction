"""Thematic sheets preserve source geometry and readable, auditable legends."""

from dataclasses import replace
import importlib
from pathlib import Path
import tempfile
import unittest

from pypdf import PdfReader
from pypdf.generic import ContentStream

from layout_configurator.building import BuildingIR
from layout_configurator.equipment import EquipmentLayoutResult, EquipmentPlacement
from layout_configurator.facility import FacilityDrawingProfile
from layout_configurator.flows import FlowRoute, FlowRoutingResult
from layout_configurator.models import LayoutResult, Rect


def example():
    building = BuildingIR.from_mapping({
        "layout": {"project_name": "Sheet review facility", "boundary": {"width": 9000, "height": 6000},
                   "entry_room": "lobby", "external_entry": {
                       "id": "entrance_west", "room_id": "lobby", "side": "left",
                       "offset_mm": 1500, "width_mm": 900},
                   "rooms": [{"id": "lobby", "area": 9, "required_adjacency": ["process"]},
                             {"id": "process", "area": 9, "required_adjacency": ["pack"]},
                             {"id": "pack", "area": 9}]},
        "zones": [{"id": "production", "type": "clean", "room_ids": ["process"]},
                  {"id": "packaging", "type": "support", "room_ids": ["pack"],
                   "parent_zone_id": "production"}],
        "equipment": [{"id": "mixer_01", "type": "mixer", "room_id": "process",
                       "width_mm": 500, "depth_mm": 600, "clearance_mm": 200}],
        "flows": [{"id": "goods_transfer", "type": "material", "from_ids": ["process"],
                   "to_ids": ["pack"], "minimum_clear_width_mm": 900}],
        "structural_grid": {"axes_x_mm": [0, 3000, 6000, 9000], "axes_y_mm": [0, 3000, 6000],
                            "labels_x": ["A", "B", "C", "D"], "labels_y": ["1", "2", "3"]},
    })
    result = LayoutResult(7, {"lobby": Rect(0, 0, 3000, 3000), "process": Rect(3000, 0, 3000, 3000),
                              "pack": Rect(6000, 0, 3000, 3000)})
    equipment = EquipmentLayoutResult((EquipmentPlacement("mixer_01", "process", Rect(3400, 400, 500, 600)),))
    routes = FlowRoutingResult((FlowRoute("goods_transfer", "process", "pack", "process", "pack",
                                        ("process", "pack"), ((5500, 1500), (6500, 1500)), 900),))
    return building, result, equipment, routes


class DrawingSheetTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "sheets.pdf"
        self.profile = FacilityDrawingProfile(sheet_id="T-400", title="Coordination issue", revision="R03")

    def export(self, building=None, result=None, equipment=None, routes=None, **options):
        default_building, default_result, default_equipment, default_routes = example()
        try:
            module = importlib.import_module("layout_configurator.drawing_sheets")
        except ModuleNotFoundError:
            self.fail("The facility thematic sheet exporter is absent")
        self.metadata = module.export_drawing_sheet_set(
            self.path, building or default_building, result or default_result,
            equipment if equipment is not None else default_equipment,
            flows=default_routes if routes is None else routes, drawing_profile=self.profile, **options,
        )
        return PdfReader(self.path)

    def test_four_vector_themes_share_source_revision_and_keep_overlay_legends_separate(self):
        reader = self.export()
        self.assertEqual(len(reader.pages), 4)
        for index, (kind, sheet_id, title) in enumerate((
            ("zones", "T-400-Z", "Functional zones"), ("equipment", "T-400-E", "Equipment and service envelopes"),
            ("flows", "T-400-F", "Process flow routes"), ("egress", "T-400-R", "Egress source and candidates"),
        )):
            page = reader.pages[index]
            text = page.extract_text()
            with self.subTest(kind=kind):
                self.assertIn(sheet_id, text)
                self.assertIn(title, text)
                self.assertIn("Sheet review facility", text)
                self.assertIn("R03", text)
                self.assertIn("Variant 7", text)
                self.assertIn("LayoutIR 0.1 / BuildingIR 0.2", text)
                self.assertIn("1:50", text)
                self.assertIn("Current revision only", text)
                self.assertEqual(self.metadata[index].kind, kind)
                self.assertEqual(self.metadata[index].page_number, index + 1)
                operations = ContentStream(page.get_contents(), reader).operations
                self.assertTrue(any(op in (b"l", b"re") for _, op in operations), "Missing vector linework")
                self.assertEqual(len(page.images), 0, "Drawing was flattened into raster images")
        texts = [page.extract_text() for page in reader.pages]
        self.assertIn("production", texts[0])
        self.assertIn("parent production", texts[0])
        self.assertIn("direct: pack", texts[0])
        self.assertIn("mixer_01", texts[1])
        self.assertIn("500 x 600 mm", texts[1])
        self.assertIn("goods_transfer", texts[2])
        self.assertNotIn("goods_transfer", texts[0] + texts[1])
        self.assertNotIn("mixer_01", texts[0] + texts[2])

    def test_missing_external_entry_is_unknown_and_does_not_invent_an_exit(self):
        building, result, equipment, routes = example()
        building = replace(building, layout=replace(building.layout, external_entry=None))
        reader = self.export(building, result, equipment, routes)
        text = reader.pages[-1].extract_text()
        self.assertIn("UNKNOWN", text)
        self.assertIn("No explicit external entry", text)
        self.assertNotIn("entrance_west", text)
        self.assertEqual(self.metadata[-1].candidate_route_count, 0)

    def test_zone_tints_do_not_paint_over_the_shared_room_tags(self):
        reader = self.export()
        operations = ContentStream(reader.pages[0].get_contents(), reader).operations
        # In this fixture the 3000 mm rooms have 180 mm tint insets on both sides.
        # The opaque fill would erase the tags if it were painted after them.
        fills = [index for index, (args, op) in enumerate(operations)
                 if op == b"re" and abs(float(args[2]) - 149.6693) < 0.001
                 and abs(float(args[3]) - 149.6693) < 0.001]
        tags = [index for index, (args, op) in enumerate(operations)
                if op == b"Tj" and str(args[0]) in ("R02", "R03")]
        self.assertEqual(len(fills), 2)
        self.assertEqual(len(tags), 2)
        self.assertGreater(min(tags), max(fills), "Opaque zone fill hides the room identity")

    def test_actual_equipment_and_route_vectors_keep_mm_scale_and_profile_visibility(self):
        reader = self.export()
        rectangles = [args for args, op in ContentStream(reader.pages[1].get_contents(), reader).operations if op == b"re"]
        self.assertTrue(any(abs(float(r[2]) - 28.34646) < 0.001 and abs(float(r[3]) - 34.01575) < 0.001
                            for r in rectangles), "500 x 600 mm source footprint is missing or distorted")
        self.assertTrue(any(abs(float(r[2]) - 51.02362) < 0.001 and abs(float(r[3]) - 56.69291) < 0.001
                            for r in rectangles), "900 x 1000 mm declared envelope is missing")
        operations = ContentStream(reader.pages[2].get_contents(), reader).operations
        segments = [(args, operations[index + 1][0]) for index, (args, op) in enumerate(operations[:-1])
                    if op == b"m" and operations[index + 1][1] == b"l"]
        self.assertTrue(any(abs(float(b[0]) - float(a[0]) - 56.69291) < 0.001 and abs(float(b[1]) - float(a[1])) < 0.001
                            for a, b in segments), "Supplied 1000 mm eastbound route changed")
        self.profile = replace(self.profile, show_equipment_clearance=False)
        reader = self.export()
        rectangles = [args for args, op in ContentStream(reader.pages[1].get_contents(), reader).operations if op == b"re"]
        self.assertFalse(any(abs(float(r[2]) - 51.02362) < 0.001 and abs(float(r[3]) - 56.69291) < 0.001
                             for r in rectangles), "Profile hides clearance but sheet still draws it")

    def test_declared_entry_uses_actual_door_graph_but_retains_unknown_compliance(self):
        reader = self.export()
        text = reader.pages[-1].extract_text()
        self.assertIn("entrance_west", text)
        self.assertIn("clearance unverified", text)
        self.assertIn("UNKNOWN", text)
        self.assertEqual(self.metadata[-1].candidate_route_count, 3)
        self.assertNotIn("APPROVED", text)
        self.assertNotIn("PASS", text)

    def test_a_disconnected_room_gets_no_synthetic_egress_route(self):
        building, result, equipment, routes = example()
        building = replace(building, layout=replace(building.layout, rooms=tuple(
            replace(room, required_adjacency=()) for room in building.layout.rooms)))
        reader = self.export(building, result, equipment, routes)
        text = reader.pages[-1].extract_text()
        self.assertIn("no door path", text)
        self.assertEqual(self.metadata[-1].candidate_route_count, 1)

    def test_coincident_route_labels_remain_readable_and_clear_of_room_annotations(self):
        building, result, equipment, routes = example()
        building = replace(building, flows=(*building.flows, replace(building.flows[0], id="second_transfer")))
        route = replace(routes.routes[0], points=((4500, 2500), (6500, 2500)))
        routes = FlowRoutingResult((route, replace(route, flow_id="second_transfer")))
        reader = self.export(building, result, equipment, routes)
        labels = []
        from reportlab.pdfbase.pdfmetrics import stringWidth
        def visit(text, cm, tm, font, size):
            text = text.strip()
            if text in ("F01", "F02", "R03", "3000 x 3000"):
                labels.append((text, float(tm[4]), float(tm[5]) - size * .25,
                               float(tm[4]) + stringWidth(text, "Helvetica", size), float(tm[5]) + size))
        reader.pages[2].extract_text(visitor_text=visit)
        flows = [label for label in labels if label[0] in ("F01", "F02")]
        self.assertEqual(len(flows), 2)
        for label in flows:
            for other in labels:
                if label is not other:
                    self.assertFalse(label[1] < other[3] and other[1] < label[3]
                                     and label[2] < other[4] and other[2] < label[4],
                                     f"Overlay label {label[0]} hides {other[0]}")

    def test_absent_routes_and_unplaced_equipment_are_visible_unknowns(self):
        reader = self.export(equipment=EquipmentLayoutResult(()), routes=FlowRoutingResult(()))
        self.assertIn("UNPLACED", reader.pages[1].extract_text())
        self.assertIn("goods_transfer", reader.pages[2].extract_text())
        self.assertIn("UNKNOWN", reader.pages[2].extract_text())
        self.assertIn("no supplied route", reader.pages[2].extract_text())

    def test_dense_legends_continue_on_readable_pages_without_dropping_source_ids(self):
        building, result, equipment, routes = example()
        from layout_configurator.building import ZoneSpec
        zones = tuple(ZoneSpec(f"zone_{i:03d}_long_process_description", "clean", ("process",)) for i in range(75))
        reader = self.export(replace(building, zones=zones), result, equipment, routes)
        self.assertGreater(len(reader.pages), 4)
        zone_pages = [page for page, meta in zip(reader.pages, self.metadata) if meta.kind == "zones"]
        joined = "\n".join(page.extract_text() for page in zone_pages)
        for zone in zones:
            self.assertIn(zone.id, joined)
        self.assertEqual(len({meta.sheet_id for meta in self.metadata}), len(self.metadata))
        self.assertTrue(any("Legend continuation" in page.extract_text() for page in zone_pages[1:]))

    def test_unsupported_glyphs_fail_before_publishing_a_broken_pdf(self):
        building, result, equipment, routes = example()
        building = replace(building, layout=replace(building.layout, project_name="\u5de5\u5382\u5e03\u5c40"))
        with self.assertRaisesRegex(ValueError, "font|glyph|Unicode"):
            self.export(building, result, equipment, routes)
        self.assertFalse(self.path.exists())

    def test_explicit_portable_ttf_is_embedded_in_the_actual_pdf(self):
        import reportlab
        font = Path(reportlab.__file__).parent / "fonts" / "Vera.ttf"
        reader = self.export(font_path=font)
        embedded = []
        for page in reader.pages:
            for entry in page["/Resources"]["/Font"].values():
                descriptor = entry.get_object().get("/FontDescriptor")
                if descriptor:
                    embedded.append(descriptor.get_object().get("/FontFile2"))
        self.assertTrue(embedded and all(embedded), "Caller TTF was not embedded for portable printing")
        self.assertIn("goods_transfer", reader.pages[2].extract_text())

    def test_continuation_uses_a_dedicated_schedule_area_within_the_print_frame(self):
        from layout_configurator.building import ZoneSpec
        building, result, equipment, routes = example()
        building = replace(building, zones=tuple(ZoneSpec(f"zone_{i:03d}", "clean", ("process",)) for i in range(75)))
        reader = self.export(building, result, equipment, routes)
        continuations = [page for page, meta in zip(reader.pages, self.metadata)
                         if meta.kind == "zones" and meta.scale_denominator is None]
        self.assertTrue(continuations)
        positions = []
        for page in continuations:
            page.extract_text(visitor_text=lambda text, cm, tm, font, size:
                              positions.append((text.strip(), float(tm[4]), float(tm[5]))) if "zone_" in text else None)
        self.assertTrue(any(x < 500 for _, x, _ in positions), "Continuation left its schedule in a narrow side legend")
        self.assertTrue(all(120 <= y <= 700 for _, _, y in positions), "Schedule text crosses the header or title block")

    def test_unknown_source_equipment_is_rejected_instead_of_silently_omitted(self):
        equipment = EquipmentLayoutResult((EquipmentPlacement("missing_machine", "process", Rect(3500, 400, 500, 600)),))
        with self.assertRaisesRegex(ValueError, "equipment|Equipment"):
            self.export(equipment=equipment)
        self.assertFalse(self.path.exists())

    def test_exact_polygon_is_reported_as_unsupported_before_any_pdf_is_published(self):
        from layout_configurator.orthogonal import OrthogonalPolygon
        from layout_configurator.result_geometry import UnsupportedGeometryError
        building, result, equipment, routes = example()
        shape = OrthogonalPolygon(((0, 0), (3000, 0), (3000, 1000), (1000, 1000), (1000, 3000), (0, 3000)))
        result = replace(result, placements={**result.placements, "lobby": shape}, geometry_version="0.2")
        with self.assertRaises(UnsupportedGeometryError) as caught:
            self.export(building, result, equipment, routes)
        self.assertEqual(caught.exception.to_dict()["status"], "UNKNOWN")
        self.assertEqual(caught.exception.to_dict()["code"], "UNSUPPORTED_GEOMETRY")
        self.assertEqual(caught.exception.to_dict()["room_ids"], ["lobby"])
        self.assertFalse(self.path.exists(), "An unsupported room was flattened to a bounding rectangle")


if __name__ == "__main__":
    unittest.main()

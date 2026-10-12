"""Actual orthogonal PDF geometry must survive independent vector readback."""

import copy
import hashlib
import importlib.util
import tempfile
import unittest
from dataclasses import asdict, replace
from pathlib import Path

from pypdf import PdfReader, PdfWriter
from pypdf.generic import ContentStream, FloatObject, NameObject, TextStringObject
from shapely.geometry import box

from layout_configurator.building import BuildingIR
from layout_configurator.equipment import EquipmentLayoutResult, EquipmentPlacement, clearance_rect
from layout_configurator.flows import FlowRoute, FlowRoutingResult
from layout_configurator.models import LayoutIR, LayoutResult, Rect
from layout_configurator.orthogonal import OrthogonalPolygon
from layout_configurator.orthogonal_equipment import validate_orthogonal_door_approaches
from layout_configurator.orthogonal_flows import validate_orthogonal_flow_routes
from layout_configurator.orthogonal_projection import build_orthogonal_wall_plan
from layout_configurator.orthogonal_validation import validate_orthogonal_equipment


def _fixture():
    spec = LayoutIR.from_mapping({"project_name": "Orthogonal vector draft \u0416",
        "boundary": {"width": 6000, "height": 6000}, "entry_room": "main",
        "rooms": [{"id": "main", "type": "process", "target_area": 20,
                   "min_area": 20, "max_area": 20, "min_width": 1000, "min_depth": 1000,
                   "required_adjacency": ["corner"]},
                  {"id": "corner", "type": "support", "target_area": 16,
                   "min_area": 16, "max_area": 16, "min_width": 1000, "min_depth": 1000}]})
    shape = OrthogonalPolygon(((0, 0), (6000, 0), (6000, 2000), (2000, 2000), (2000, 6000), (0, 6000)))
    layout = LayoutResult(3, {"main": shape, "corner": Rect(2000, 2000, 4000, 4000)})
    building = BuildingIR.from_mapping({"layout": spec.to_dict(), "equipment": [
        {"id": "source", "room_id": "main", "width_mm": 500, "depth_mm": 500,
         "clearance_front_mm": 100, "clearance_back_mm": 60,
         "clearance_left_mm": 80, "clearance_right_mm": 120},
        {"id": "rotated", "room_id": "corner", "width_mm": 300, "depth_mm": 600,
         "clearance_front_mm": 240, "clearance_back_mm": 70,
         "clearance_left_mm": 110, "clearance_right_mm": 90}],
        "flows": [{"id": "process", "from": ["source"], "to": ["corner"], "minimum_clear_width_mm": 800}]})
    equipment = EquipmentLayoutResult((EquipmentPlacement("source", "main", Rect(4800, 400, 500, 500)),
        EquipmentPlacement("rotated", "corner", Rect(4200, 4800, 600, 300), True)))
    routing = FlowRoutingResult((FlowRoute("process", "source", "corner", "main", "corner", ("main", "corner"),
        ((5050, 1410), (1500, 1410), (1500, 4000), (4000, 4000)), 800),))
    return building, layout, equipment, routing


class OrthogonalPDFTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.building, self.layout, self.equipment, self.routing = _fixture()

    def api(self):
        self.assertIsNotNone(importlib.util.find_spec("layout_configurator.orthogonal_pdf"),
                             "Exact orthogonal PDF draft projection is missing")
        from layout_configurator import orthogonal_pdf
        return orthogonal_pdf

    def export(self, **kwargs):
        self.api()
        from layout_configurator.orthogonal_pdf import export_orthogonal_draft_pdf
        self.path = self.root / "draft.pdf"
        self.index = export_orthogonal_draft_pdf(self.path, self.building, self.layout, self.equipment,
            routing=self.routing, revision="P03", **kwargs)
        return self.index

    def check(self, **kwargs):
        from layout_configurator.orthogonal_pdf_qa import validate_orthogonal_pdf
        return validate_orthogonal_pdf(self.path, self.building, self.layout, self.equipment,
            routing=self.routing, revision="P03", index=self.index, **kwargs)

    def edit_role(self, kind, identifier, mutate):
        reader = PdfReader(self.path)
        stream = ContentStream(reader.pages[0].get_contents(), reader)
        operations = stream.operations
        start = next(i for i, (values, op) in enumerate(operations) if op == b"BDC" and
                     str(values[1].get("/Kind")) == "/" + kind and str(values[1].get("/Id")) == identifier)
        end = next(i for i in range(start + 1, len(operations)) if operations[i][1] == b"EMC")
        stream.operations = operations[:start + 1] + mutate(operations[start + 1:end]) + operations[end:]
        writer = PdfWriter()
        writer.clone_document_from_reader(reader)
        writer.pages[0][NameObject("/Contents")] = writer._add_object(stream)
        with self.path.open("wb") as target:
            writer.write(target)
        self.index["sha256"] = hashlib.sha256(self.path.read_bytes()).hexdigest()

    def rectangle(self, rect, operator=b"B*"):
        points = ((rect.x, rect.y), (rect.right, rect.y), (rect.right, rect.top), (rect.x, rect.top))
        return [([FloatObject(x), FloatObject(y)], b"m" if i == 0 else b"l")
                for i, (x, y) in enumerate(points)] + [([], b"h"), ([], operator)]

    def replace_geometry(self, original, replacement):
        constructors = {b"m", b"l", b"h", b"re", b"S", b"f", b"f*", b"B", b"B*"}
        return [operation for operation in original if operation[1] not in constructors] + replacement

    def test_actual_concave_rooms_walls_doors_services_and_width_corridor_are_read_back(self):
        self.assertTrue(validate_orthogonal_equipment(self.building, self.layout, self.equipment).ok)
        self.assertTrue(validate_orthogonal_door_approaches(self.building, self.layout, self.equipment).ok)
        self.assertTrue(validate_orthogonal_flow_routes(self.building, self.layout, self.routing, self.equipment).ok)
        index = self.export()
        self.assertEqual(index["status"], "DRAFT")
        self.assertIs(index["ok"], False)
        self.assertEqual(index["full_facility_validation"], {"status": "UNKNOWN", "ok": False})
        self.assertEqual(index["source_variant"], 3)
        self.assertEqual(index["revision"], "P03")
        self.assertEqual(index["sha256"], hashlib.sha256(self.path.read_bytes()).hexdigest())
        reader = PdfReader(self.path)
        self.assertTrue(all(not page.images for page in reader.pages))
        text = "\n".join(page.extract_text() for page in reader.pages)
        self.assertIn("Orthogonal vector draft \u0416", text)
        self.assertIn("FULL FACILITY: UNKNOWN", text)
        report = self.check()
        self.assertTrue(report["ok"], report)
        self.assertEqual(report["full_facility_validation"]["status"], "UNKNOWN")
        self.assertEqual(report["actual"]["room_areas_mm2"]["main"], 20_000_000)
        self.assertEqual(report["actual"]["room_vertex_counts"]["main"], 6)
        self.assertAlmostEqual(report["actual"]["wall_area_mm2"], build_orthogonal_wall_plan(self.building.layout, self.layout).geometry.area, places=4)
        self.assertEqual(report["actual"]["door_widths_mm"]["auto-corner-main"], 900)
        for item in self.equipment.placements:
            spec = next(value for value in self.building.equipment if value.id == item.equipment_id)
            self.assertAlmostEqual(report["actual"]["service_areas_mm2"][item.equipment_id],
                clearance_rect(spec, item.rect, item.rotated).area_mm2, places=4)
        self.assertGreater(report["actual"]["corridor_areas_mm2"]["process"], 800 * 800)

    def test_dense_source_inventory_paginates_with_unique_visible_draft_identity(self):
        from layout_configurator.orthogonal_pdf import export_orthogonal_draft_pdf
        from layout_configurator.orthogonal_pdf_qa import validate_orthogonal_pdf
        raw = self.building.to_dict()
        raw["equipment"], raw["flows"] = [], []
        placements = []
        for i in range(30):
            room = "main" if i < 15 else "corner"
            x = (1000 if i < 15 else 3000) + (i % 5) * 300
            y = (300 if i < 15 else 4500) + ((i % 15) // 5) * 300
            identifier = f"equipment_{i:02d}"
            raw["equipment"].append({"id": identifier, "room_id": room, "width_mm": 200, "depth_mm": 200,
                "clearance_front_mm": 20, "clearance_back_mm": 20, "clearance_left_mm": 20, "clearance_right_mm": 20})
            placements.append(EquipmentPlacement(identifier, room, Rect(x, y, 200, 200)))
        building = BuildingIR.from_mapping(raw)
        equipment = EquipmentLayoutResult(tuple(placements))
        path = self.root / "dense.pdf"
        index = export_orthogonal_draft_pdf(path, building, self.layout, equipment, revision="P04")
        self.assertGreater(len(index["pages"]), 2)
        self.assertEqual(len({entry["page_id"] for entry in index["pages"]}), len(index["pages"]))
        report = validate_orthogonal_pdf(path, building, self.layout, equipment, revision="P04", index=index)
        self.assertTrue(report["ok"], report)
        self.assertEqual(len(report["actual"]["service_areas_mm2"]), 30)
        for number, page in enumerate(PdfReader(path).pages, 1):
            self.assertIn(f"Page OP-{number:03d} ({number}/{len(index['pages'])})", page.extract_text())

    def test_bbox_substitution_and_displaced_or_dropped_actual_room_fail_with_recomputed_hash(self):
        for mutation in ("bbox", "displaced", "dropped"):
            with self.subTest(mutation=mutation):
                self.export()
                if mutation == "bbox":
                    self.edit_role("room", "main", lambda operations: self.replace_geometry(operations,
                        self.rectangle(Rect(0, 0, 6000, 6000))))
                elif mutation == "displaced":
                    self.edit_role("room", "main", lambda operations: [([FloatObject(v) for v in (1, 0, 0, 1, 500, 0)], b"cm"), *operations])
                else:
                    self.edit_role("room", "main", lambda operations: self.replace_geometry(operations, []))
                report = self.check()
                self.assertFalse(report["ok"], report)

    def test_readback_uses_effective_transforms_and_accepts_equivalent_coordinate_frames(self):
        self.export()
        def shifted(operations):
            changed = [([FloatObject(v) for v in (1, 0, 0, 1, 100, 200)], b"cm")]
            for values, op in operations:
                if op in (b"m", b"l"):
                    values = [FloatObject(float(values[0]) - 100), FloatObject(float(values[1]) - 200)]
                changed.append((values, op))
            return changed
        self.edit_role("room", "main", shifted)
        report = self.check()
        self.assertTrue(report["ok"], report)

    def test_number_reserialization_passes_but_one_hundredth_mm_displacement_fails(self):
        self.export()
        self.edit_role("room", "main", lambda operations: operations)
        self.assertTrue(self.check()["ok"])
        self.edit_role("room", "main", lambda operations:
            [([FloatObject(v) for v in (1, 0, 0, 1, .01, 0)], b"cm"), *operations])
        report = self.check()
        self.assertFalse(report["ok"], report)
        self.assertEqual(report["issues"][0]["code"], "PATH_GEOMETRY")

    def test_index_requires_exact_json_types_and_actual_resource_contract(self):
        for field, value in (("ok", 0), ("components_supplied", {"equipment": 1, "routing": True}),
                             ("limits", []), ("limits", {"max_rooms": "32"})):
            with self.subTest(field=field, value=value):
                self.export()
                self.index[field] = value
                report = self.check()
                self.assertFalse(report["ok"], report)
                self.assertEqual(report["issues"][0]["code"], "INDEX_IDENTITY")
        self.export()
        self.index["pages"][0]["page_number"] = True
        self.assertFalse(self.check()["ok"])

    def test_actual_missing_or_malformed_pdf_returns_failure_even_with_recomputed_hash(self):
        self.export()
        self.path.unlink()
        self.assertFalse(self.check()["ok"])
        for data in (b"not a PDF", b"%PDF-1.7\n%%EOF\n"):
            with self.subTest(data=data):
                self.path.write_bytes(data)
                self.index["sha256"] = hashlib.sha256(data).hexdigest()
                report = self.check()
                self.assertFalse(report["ok"], report)

    def test_false_printed_status_and_dropped_actual_page_fail_after_rehash(self):
        for change in ("status", "page"):
            with self.subTest(change=change):
                self.export()
                reader = PdfReader(self.path)
                writer = PdfWriter()
                writer.clone_document_from_reader(reader)
                if change == "status":
                    stream = ContentStream(reader.pages[0].get_contents(), reader)
                    replaced = False
                    for values, op in stream.operations:
                        if op == b"Tj" and "DRAFT" in str(values[0]):
                            values[0] = TextStringObject(str(values[0]).replace("DRAFT", "PASS"))
                            replaced = True
                    self.assertTrue(replaced)
                    writer.pages[0][NameObject("/Contents")] = writer._add_object(stream)
                else:
                    del writer.pages[-1]
                with self.path.open("wb") as target:
                    writer.write(target)
                self.index["sha256"] = hashlib.sha256(self.path.read_bytes()).hexdigest()
                report = self.check()
                self.assertFalse(report["ok"], report)
                self.assertIn(report["issues"][0]["code"], ("PDF_TEXT_IDENTITY", "PDF_PAGE_IDENTITY"))

    def test_budget_limits_and_malformed_canonical_containers_reject_before_output(self):
        api = self.api()
        for setting, value in (("max_rooms", 1), ("max_vertices", 5), ("max_source_bytes", 32),
                               ("max_text_chars", 20), ("max_pages", 1), ("max_pdf_bytes", 100),
                               ("max_stream_bytes", 128), ("max_operations", 1), ("max_resources", 1)):
            with self.subTest(setting=setting):
                path = self.root / setting / "draft.pdf"
                with self.assertRaises(ValueError):
                    api.export_orthogonal_draft_pdf(path, self.building, self.layout, self.equipment,
                        routing=self.routing, limits=replace(api.OrthogonalPDFLimits(), **{setting: value}))
                self.assertFalse(path.exists())
        for building, equipment, routing in ((replace(self.building, equipment=None), self.equipment, self.routing),
                (self.building, replace(self.equipment, placements=None), self.routing),
                (self.building, self.equipment, replace(self.routing, routes=None))):
            with self.subTest(building=building.equipment, equipment=equipment, routing=routing):
                path = self.root / "malformed" / "draft.pdf"
                with self.assertRaises(ValueError):
                    api.export_orthogonal_draft_pdf(path, building, self.layout, equipment, routing=routing)
                self.assertFalse(path.exists())

    def test_actual_stream_operator_and_resource_budgets_are_enforced(self):
        api = self.api()
        for setting, value in (("max_stream_bytes", 128), ("max_operations", 1), ("max_resources", 1)):
            with self.subTest(setting=setting):
                self.export()
                limits = replace(api.OrthogonalPDFLimits(), **{setting: value})
                self.index["limits"] = asdict(limits)
                report = self.check(limits=limits)
                self.assertFalse(report["ok"], report)
                self.assertEqual(report["issues"][0]["code"], "RESOURCE_LIMIT")

    def test_invisible_identity_and_wall_hidden_under_room_fill_fail_after_rehash(self):
        for change in ("zero_font", "zero_text_transform", "hidden_wall"):
            with self.subTest(change=change):
                self.export()
                reader = PdfReader(self.path)
                stream = ContentStream(reader.pages[0].get_contents(), reader)
                operations = stream.operations
                if change == "hidden_wall":
                    first = next(i for i, (v, op) in enumerate(operations)
                                 if op == b"BDC" and str(v[1]["/Kind"]) == "/room") - 1
                    start = next(i for i, (v, op) in enumerate(operations)
                                 if op == b"BDC" and str(v[1]["/Kind"]) == "/wall") - 1
                    end = next(i for i in range(start + 1, len(operations)) if operations[i][1] == b"EMC") + 2
                    stream.operations = operations[:first] + operations[start:end] + operations[first:start] + operations[end:]
                else:
                    index = next(i for i, (v, op) in enumerate(operations) if op == b"Tj" and "DRAFT" in str(v[0]))
                    if change == "zero_font":
                        font = next(v[0] for v, op in reversed(operations[:index]) if op == b"Tf")
                        added = ([font, FloatObject(0)], b"Tf")
                    else:
                        matrix = next(v for v, op in reversed(operations[:index]) if op == b"Tm")
                        added = ([FloatObject(0)] * 4 + list(matrix[4:]), b"Tm")
                    stream.operations = operations[:index] + [added] + operations[index:]
                writer = PdfWriter()
                writer.clone_document_from_reader(reader)
                writer.pages[0][NameObject("/Contents")] = writer._add_object(stream)
                with self.path.open("wb") as target:
                    writer.write(target)
                self.index["sha256"] = hashlib.sha256(self.path.read_bytes()).hexdigest()
                report = self.check()
                self.assertFalse(report["ok"], report)
                self.assertIn(report["issues"][0]["code"], ("PDF_TEXT_IDENTITY", "PDF_VECTOR_STRUCTURE"))

    def test_invalid_pdf_number_types_and_missing_text_operands_are_not_vectors(self):
        for change in ("string_coordinate", "empty_text"):
            with self.subTest(change=change):
                self.export()
                if change == "string_coordinate":
                    def string_coordinates(operations):
                        return [([TextStringObject(str(v)) for v in values] if op in (b"m", b"l") else values, op)
                                for values, op in operations]
                    self.edit_role("room", "main", string_coordinates)
                else:
                    reader = PdfReader(self.path)
                    stream = ContentStream(reader.pages[0].get_contents(), reader)
                    stream.operations = [([], op) if op == b"Tj" else (values, op) for values, op in stream.operations]
                    writer = PdfWriter()
                    writer.clone_document_from_reader(reader)
                    writer.pages[0][NameObject("/Contents")] = writer._add_object(stream)
                    with self.path.open("wb") as target:
                        writer.write(target)
                    self.index["sha256"] = hashlib.sha256(self.path.read_bytes()).hexdigest()
                report = self.check()
                self.assertFalse(report["ok"], report)

    def test_directional_service_or_full_width_corridor_cannot_be_replaced_by_footprint_or_centerline(self):
        for kind, identifier in (("service", "rotated"), ("corridor", "process")):
            with self.subTest(kind=kind):
                self.export()
                if kind == "service":
                    self.edit_role(kind, identifier, lambda ops: self.replace_geometry(ops,
                        self.rectangle(self.equipment.placements[1].rect, b"S")))
                else:
                    points = self.routing.routes[0].points
                    thin = [([FloatObject(x), FloatObject(y)], b"m" if i == 0 else b"l")
                            for i, (x, y) in enumerate(points)] + [([], b"S")]
                    self.edit_role(kind, identifier, lambda ops: self.replace_geometry(ops, thin))
                self.assertFalse(self.check()["ok"])

    def test_dropped_wall_ring_and_displaced_door_are_independently_detected(self):
        self.export()
        def drop_ring(operations):
            starts = [i for i, (_, op) in enumerate(operations) if op == b"m"]
            self.assertGreater(len(starts), 1)
            end = next(i for i in range(starts[1], len(operations)) if operations[i][1] == b"h")
            return operations[:starts[1]] + operations[end + 1:]
        self.edit_role("wall", "walls", drop_ring)
        self.assertFalse(self.check()["ok"])
        self.export()
        self.edit_role("door", "auto-corner-main", lambda ops: [([FloatObject(v) for v in (1, 0, 0, 1, 0, 300)], b"cm"), *ops])
        self.assertFalse(self.check()["ok"])

    def test_false_index_status_identity_or_hash_cannot_claim_acceptance(self):
        self.export()
        original = copy.deepcopy(self.index)
        for key, value in (("status", "PASS"), ("source_variant", 4), ("revision", "P99"),
                           ("sha256", "0" * 64), ("full_facility_validation", {"status": "PASS", "ok": True})):
            with self.subTest(field=key):
                self.index = {**copy.deepcopy(original), key: value}
                self.assertFalse(self.check()["ok"])
        self.index = original
        self.building = replace(self.building, layout=replace(self.building.layout, project_name="Different source"))
        self.assertFalse(self.check()["ok"])

    def test_invalid_optional_services_or_routes_reject_before_destination_creation(self):
        api = self.api()
        for kind in ("equipment", "routing"):
            with self.subTest(kind=kind):
                equipment, routing = self.equipment, self.routing
                if kind == "equipment":
                    equipment = EquipmentLayoutResult((replace(equipment.placements[0], rect=Rect(4000, 4000, 500, 500)),
                                                       equipment.placements[1]))
                else:
                    routing = FlowRoutingResult((replace(routing.routes[0], minimum_clear_width_mm=400),))
                path = self.root / f"invalid_{kind}.pdf"
                with self.assertRaises(ValueError):
                    api.export_orthogonal_draft_pdf(path, self.building, self.layout, equipment, routing=routing)
                self.assertFalse(path.exists())

    def test_omitted_optional_components_are_unknown_and_not_silently_inferred(self):
        api = self.api()
        from layout_configurator.orthogonal_pdf_qa import validate_orthogonal_pdf
        path = self.root / "rooms-only.pdf"
        index = api.export_orthogonal_draft_pdf(path, self.building, self.layout)
        self.assertEqual(index["local_checks"]["equipment"], "UNKNOWN")
        self.assertEqual(index["local_checks"]["routes"], "UNKNOWN")
        report = validate_orthogonal_pdf(path, self.building, self.layout, index=index)
        self.assertTrue(report["ok"], report)
        self.assertEqual(report["actual"]["service_areas_mm2"], {})
        self.assertEqual(report["actual"]["corridor_areas_mm2"], {})

    def test_clipping_or_hidden_vector_styles_are_rejected_even_with_correct_coordinates(self):
        for operator in (b"W", b"white"):
            with self.subTest(operator=operator):
                self.export()
                extra = [([], b"W")] if operator == b"W" else [([FloatObject(1)] * 3, b"rg")]
                self.edit_role("room", "main", lambda ops: [*ops[:-1], *extra, ops[-1]])
                self.assertFalse(self.check()["ok"])

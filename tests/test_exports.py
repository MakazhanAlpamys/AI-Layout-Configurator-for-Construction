import tempfile
import unittest
from pathlib import Path

import ezdxf
import ifcopenshell

from layout_configurator.compliance import validate_ids
from layout_configurator.export import export_bundle
from layout_configurator.ifc import export_ifc
from layout_configurator.io import load_spec
from layout_configurator.models import LayoutResult, Rect
from layout_configurator.solver import solve_layouts
from layout_configurator.validation import validate_layout


class ExportTests(unittest.TestCase):
    def test_dxf_and_pdf_round_trip(self):
        spec = load_spec("examples/basic.yaml")
        result = solve_layouts(spec, variants=1, time_limit_seconds=10)[0]
        report = validate_layout(spec, result)
        self.assertTrue(report.ok, report.issues)
        with tempfile.TemporaryDirectory() as directory:
            dxf_path, pdf_path = export_bundle(directory, spec, result, report)
            document = ezdxf.readfile(dxf_path)
            modelspace = document.modelspace()
            wall_entities = [entity for entity in modelspace if entity.dxf.layer == "A-WALL"]
            door_entities = [entity for entity in modelspace if entity.dxf.layer == "A-DOOR"]
            room_text = [entity for entity in modelspace if entity.dxf.layer == "A-TEXT"]
            self.assertGreaterEqual(len(wall_entities), 2)
            self.assertEqual(len(door_entities), 3)
            self.assertTrue(all(entity.dxftype() == "LWPOLYLINE" for entity in wall_entities))
            self.assertEqual(len(room_text), len(spec.rooms))
            for entity in room_text:
                room_id = entity.dxf.text.split(" ", 1)[0]
                rect = result.placements[room_id]
                self.assertLessEqual(
                    len(entity.dxf.text) * entity.dxf.height * 0.78,
                    rect.width - 400 + 1e-6,
                )
            self.assertEqual(Path(pdf_path).read_bytes()[:8], b"%PDF-1.3")

    def test_ifc_round_trip_has_spatial_structure_and_quantities(self):
        spec = load_spec("examples/basic.yaml")
        result = solve_layouts(spec, variants=1, time_limit_seconds=10)[0]
        with tempfile.TemporaryDirectory() as directory:
            ifc_path = Path(directory) / "layout.ifc"
            summary = export_ifc(ifc_path, spec, result)
            model = ifcopenshell.open(ifc_path)
            self.assertEqual(summary.spaces, len(model.by_type("IfcSpace")))
            self.assertEqual(summary.walls, len(model.by_type("IfcWall")))
            self.assertEqual(summary.doors, len(model.by_type("IfcDoor")))
            self.assertEqual(summary.windows, len(model.by_type("IfcWindow")))
            self.assertEqual(summary.openings, 5)
            self.assertEqual(summary.space_boundaries, len(model.by_type("IfcRelSpaceBoundary")))
            self.assertEqual(summary.voids, 5)
            self.assertEqual(summary.fills, 5)
            self.assertEqual(summary.wall_types, 1)
            self.assertEqual(summary.door_types, 1)
            self.assertEqual(summary.window_types, 1)
            self.assertEqual(summary.windows, 2)
            self.assertEqual(len(model.by_type("IfcProject")), 1)
            self.assertEqual(len(model.by_type("IfcBuildingStorey")), 1)
            application = model.by_type("IfcApplication")[0]
            self.assertEqual(application.ApplicationFullName, "Facility Layout Compiler")
            self.assertEqual(application.ApplicationIdentifier, "FACILITY-LAYOUT-COMPILER")
            self.assertEqual(application.ApplicationDeveloper.Name, "Facility Layout Compiler")
            self.assertEqual(len(model.by_type("IfcElementQuantity")), len(spec.rooms))
            self.assertGreaterEqual(len(model.by_type("IfcPropertySet")), len(spec.rooms) + summary.walls + summary.doors + summary.windows)
            self.assertEqual(len(model.by_type("IfcMaterial")), 3)
            self.assertGreater(ifc_path.stat().st_size, 1_000)

    def test_ifc_round_trip_has_bim_relationship_graph(self):
        spec = load_spec("examples/basic.yaml")
        result = solve_layouts(spec, variants=1, time_limit_seconds=10)[0]
        with tempfile.TemporaryDirectory() as directory:
            ifc_path = Path(directory) / "layout.ifc"
            export_ifc(ifc_path, spec, result)
            model = ifcopenshell.open(ifc_path)

            self.assertGreater(len(model.by_type("IfcRelSpaceBoundary")), len(spec.rooms))
            self.assertEqual(len(model.by_type("IfcRelVoidsElement")), 5)
            self.assertEqual(len(model.by_type("IfcRelFillsElement")), 5)
            self.assertTrue(all(space.BoundedBy for space in model.by_type("IfcSpace")))
            self.assertTrue(all(door.FillsVoids for door in model.by_type("IfcDoor")))
            self.assertTrue(all(window.FillsVoids for window in model.by_type("IfcWindow")))
            self.assertTrue(all(opening.VoidsElements for opening in model.by_type("IfcOpeningElement")))

    def test_ifc_round_trip_has_semantic_element_types(self):
        spec = load_spec("examples/basic.yaml")
        result = solve_layouts(spec, variants=1, time_limit_seconds=10)[0]
        with tempfile.TemporaryDirectory() as directory:
            ifc_path = Path(directory) / "layout.ifc"
            export_ifc(ifc_path, spec, result)
            model = ifcopenshell.open(ifc_path)

            self.assertEqual(len(model.by_type("IfcWallType")), 1)
            self.assertEqual(len(model.by_type("IfcDoorType")), 1)
            self.assertEqual(len(model.by_type("IfcWindowType")), 1)
            self.assertEqual(len(model.by_type("IfcRelDefinesByType")), 3)
            self.assertTrue(all(wall.IsTypedBy for wall in model.by_type("IfcWall")))
            self.assertTrue(all(door.IsTypedBy for door in model.by_type("IfcDoor")))
            self.assertTrue(all(window.IsTypedBy for window in model.by_type("IfcWindow")))
            self.assertEqual(model.by_type("IfcWallType")[0].PredefinedType, "PARTITIONING")
            self.assertEqual(model.by_type("IfcDoorType")[0].OperationType, "NOTDEFINED")
            self.assertEqual(model.by_type("IfcWindowType")[0].PartitioningType, "NOTDEFINED")

    def test_ids_baseline_passes_exported_ifc(self):
        spec = load_spec("examples/basic.yaml")
        result = solve_layouts(spec, variants=1, time_limit_seconds=10)[0]
        with tempfile.TemporaryDirectory() as directory:
            ifc_path = Path(directory) / "layout.ifc"
            export_ifc(ifc_path, spec, result)
            report = validate_ids(ifc_path, "ids/layout_baseline.ids")
            self.assertTrue(report.ok)
            self.assertTrue(all(item.failed == 0 for item in report.specifications))

    def test_ids_kz_exchange_profile_passes_exported_ifc(self):
        spec = load_spec("examples/kz_entry_pass.yaml")
        result = solve_layouts(spec, variants=1, time_limit_seconds=10)[0]
        with tempfile.TemporaryDirectory() as directory:
            ifc_path = Path(directory) / "layout.ifc"
            export_ifc(ifc_path, spec, result)
            report = validate_ids(ifc_path, "ids/kz_layout_exchange.ids")
            self.assertTrue(report.ok)
            self.assertTrue(all(item.failed == 0 for item in report.specifications))

    def test_ifc_preserves_external_entry_and_heated_room_metadata(self):
        spec = load_spec("examples/kz_entry_pass.yaml")
        result = LayoutResult(
            variant=1,
            placements={
                "tambour": Rect(0, 0, 3000, 2000),
                "hall": Rect(3000, 0, 2000, 6000),
                "living": Rect(5000, 0, 4800, 5000),
                "kitchen": Rect(0, 2000, 3000, 4000),
                "bedroom": Rect(0, 6000, 5000, 3200),
            },
        )
        with tempfile.TemporaryDirectory() as directory:
            ifc_path = Path(directory) / "layout.ifc"
            summary = export_ifc(ifc_path, spec, result)
            model = ifcopenshell.open(ifc_path)

            self.assertEqual(summary.external_entries, 1)
            self.assertEqual(summary.doors, 5)
            self.assertEqual(summary.openings, 8)
            external_doors = [door for door in model.by_type("IfcDoor") if door.Name == "External entry tambour"]
            self.assertEqual(len(external_doors), 1)
            door_pset = next(
                definition.RelatingPropertyDefinition
                for definition in external_doors[0].IsDefinedBy
                if definition.RelatingPropertyDefinition.Name == "Pset_LayoutDoor"
            )
            values = {prop.Name: prop.NominalValue.wrappedValue for prop in door_pset.HasProperties}
            self.assertTrue(values["IsExternal"])
            self.assertEqual(values["EntryId"], "main_entry")

            hall = next(space for space in model.by_type("IfcSpace") if space.Name == "hall")
            room_pset = next(
                definition.RelatingPropertyDefinition
                for definition in hall.IsDefinedBy
                if definition.RelatingPropertyDefinition.Name == "Pset_LayoutRoom"
            )
            room_values = {prop.Name: prop.NominalValue.wrappedValue for prop in room_pset.HasProperties}
            self.assertTrue(room_values["IsHeated"])


if __name__ == "__main__":
    unittest.main()

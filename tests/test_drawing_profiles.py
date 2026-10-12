"""Named drawing settings change presentation through actual export consumers."""

from dataclasses import replace
import importlib
from pathlib import Path
import tempfile
import unittest

import ezdxf

from layout_configurator.facility import FacilityDrawingProfile, load_facility_profile
from layout_configurator.export import export_building_dxf
from test_drawing_sheets import example


class DrawingProfileTests(unittest.TestCase):
    def test_named_presets_resolve_to_existing_canonical_drawing_settings(self):
        profile = FacilityDrawingProfile.from_mapping({"preset": "equipment_service", "sheet_id": "E-201", "revision": "R02"})
        self.assertEqual(profile.sheet_id, "E-201")
        self.assertEqual(profile.revision, "R02")
        self.assertTrue(profile.show_equipment_clearance)
        self.assertFalse(profile.show_room_dimensions)
        self.assertFalse(profile.show_flow_labels)
        self.assertFalse(profile.show_flow_legend)
        self.assertNotIn("preset", profile.to_dict())
        self.assertEqual(FacilityDrawingProfile.from_mapping({}), FacilityDrawingProfile())

    def test_unknown_preset_override_and_primitive_coercion_are_rejected(self):
        for mapping in ({"preset": "unknown"}, {"preset": "equipment_service", "unknown": True},
                        {"preset": "equipment_service", "show_room_dimensions": "false"},
                        {"preset": "equipment_service", "revision": 2},
                        {"preset": "equipment_service", "legend_offset_x_mm": True},
                        {"preset": "equipment_service", "legend_offset_y_mm": float("nan")},
                        {"preset": "equipment_service", "title": " "}, {"preset": True}):
            with self.subTest(mapping=mapping):
                with self.assertRaises(ValueError):
                    FacilityDrawingProfile.from_mapping(mapping)

    def test_actual_dxf_profile_projection_changes_annotations_without_changing_source(self):
        building, result, equipment, flows = example()
        original = (building.to_dict(), result.to_dict(), equipment.to_dict(building), flows.to_dict())
        with tempfile.TemporaryDirectory() as directory:
            paths = [Path(directory) / f"{name}.dxf" for name in ("coordination_review", "process_flows")]
            for path in paths:
                export_building_dxf(path, building, result, equipment, flows,
                    FacilityDrawingProfile.from_mapping({"preset": path.stem}))
            drawings = [ezdxf.readfile(path) for path in paths]
        layer_counts = [len(list(document.modelspace().query('*[layer=="A-CLEARANCE"]'))) for document in drawings]
        self.assertGreater(layer_counts[0], 0)
        self.assertEqual(layer_counts[1], 0)
        room_rings = [list(document.modelspace().query('LWPOLYLINE[layer=="A-ROOM"]')) for document in drawings]
        self.assertEqual([tuple(entity.get_points()) for entity in room_rings[0]],
                         [tuple(entity.get_points()) for entity in room_rings[1]])
        self.assertEqual((building.to_dict(), result.to_dict(), equipment.to_dict(building), flows.to_dict()), original)

    def test_profile_loader_accepts_preset_and_catalog_returns_independent_mappings(self):
        catalog = importlib.import_module("layout_configurator.drawing_profiles")
        self.assertEqual(set(catalog.drawing_profile_catalog()),
                         {"coordination_review", "architecture_dimensions", "equipment_service", "process_flows"})
        first = catalog.drawing_profile_catalog()
        first["equipment_service"]["show_equipment_clearance"] = False
        self.assertTrue(catalog.drawing_profile_catalog()["equipment_service"]["show_equipment_clearance"])
        baseline = load_facility_profile("rules/pharma_cleanroom_pilot.yaml")
        import json
        raw = baseline.to_dict()
        raw["drawing"] = {"preset": "architecture_dimensions", "revision": "P03"}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "profile.json"
            path.write_text(json.dumps(raw), encoding="utf-8")
            loaded = load_facility_profile(path)
        self.assertTrue(loaded.drawing.show_structural_grid)
        self.assertTrue(loaded.drawing.show_room_dimensions)
        self.assertFalse(loaded.drawing.show_equipment_clearance)
        self.assertEqual(loaded.drawing.revision, "P03")
        self.assertEqual(replace(loaded, drawing=baseline.drawing), baseline)


if __name__ == "__main__":
    unittest.main()

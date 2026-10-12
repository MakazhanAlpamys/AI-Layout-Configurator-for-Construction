"""Editable source dimensions retain actual millimetres and reject artifact tampering."""

from dataclasses import replace
from pathlib import Path
import tempfile
import unittest

import ezdxf
from pypdf import PdfReader

from layout_configurator.export import export_building_dxf
from layout_configurator.drawing_sheets import export_drawing_sheet_set
from layout_configurator.facility import FacilityDrawingProfile
from layout_configurator.models import Rect
from test_drawing_sheets import example


class DimensionChainTests(unittest.TestCase):
    def test_architecture_preset_exports_native_editable_source_dimension_chains(self):
        building, layout, equipment, flows = example()
        before = (building.to_dict(), layout.to_dict(), equipment.to_dict(building), flows.to_dict())
        profile = FacilityDrawingProfile.from_mapping({'preset': 'architecture_dimensions'})
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'dimensions.dxf'
            export_building_dxf(path, building, layout, equipment, flows, profile)
            document = ezdxf.readfile(path)
        dimensions = list(document.modelspace().query('DIMENSION[layer=="A-DIMS"]'))
        self.assertGreaterEqual(len(dimensions), 10)
        groups = set()
        for entity in dimensions:
            self.assertEqual(entity.dxf.text, '<>')
            self.assertGreater(float(entity.get_measurement()), 0)
            self.assertIn(entity.dxf.geometry, document.blocks)
            identity = dict(tag.value.split('=', 1) for tag in entity.get_xdata('FLC_DIM') if tag.code == 1000)
            groups.add(identity['kind'])
            self.assertTrue(identity['dimension_id'])
        self.assertEqual(groups, {'room_edges', 'structural_grid', 'overall'})
        self.assertEqual((building.to_dict(), layout.to_dict(), equipment.to_dict(building), flows.to_dict()), before)

    def test_default_and_explicit_disabled_preserve_legacy_profile_serialization(self):
        self.assertNotIn('show_dimension_chains', FacilityDrawingProfile().to_dict())
        enabled = FacilityDrawingProfile.from_mapping({'show_dimension_chains': True})
        self.assertTrue(enabled.to_dict()['show_dimension_chains'])
        self.assertEqual(FacilityDrawingProfile.from_mapping(enabled.to_dict()), enabled)
        for value in (1, 'false', None):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    FacilityDrawingProfile.from_mapping({'show_dimension_chains': value})
        building, layout, equipment, flows = example()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'default.dxf'
            export_building_dxf(path, building, layout, equipment, flows)
            self.assertEqual(len(ezdxf.readfile(path).modelspace().query('DIMENSION')), 0)

    def test_source_builder_is_order_independent_and_preserves_fractional_edges(self):
        from layout_configurator.dimension_chains import build_dimension_chains
        building, layout, _, _ = example()
        layout = replace(layout, placements={**layout.placements, 'pack': Rect(6000.125, 0, 2999.875, 3000)})
        chains = build_dimension_chains(building.layout, layout, building.structural_grid)
        reordered = replace(building.layout, rooms=tuple(reversed(building.layout.rooms)))
        self.assertEqual(chains, build_dimension_chains(reordered, layout, building.structural_grid))
        self.assertTrue(any(chain.measurement_mm == .125 for chain in chains))
        self.assertTrue(any(chain.measurement_mm == 9000 for chain in chains))

    def test_independent_actual_dxf_readback_rejects_changed_definition_and_forced_text(self):
        from layout_configurator.dimension_chains import validate_dimension_chains_dxf
        building, layout, equipment, flows = example()
        drawing = FacilityDrawingProfile.from_mapping({'show_dimension_chains': True})
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'checked.dxf'
            export_building_dxf(path, building, layout, equipment, flows, drawing)
            self.assertTrue(validate_dimension_chains_dxf(path, building.layout, layout, building.structural_grid)['ok'])
            original = path.read_bytes()
            for change in ('definition', 'text', 'units', 'factor', 'missing'):
                path.write_bytes(original)
                document = ezdxf.readfile(path)
                dimension = next(iter(document.modelspace().query('DIMENSION')))
                if change == 'definition':
                    dimension.dxf.defpoint3 = (9999, 0, 0)
                elif change == 'text':
                    dimension.dxf.text = '12345'
                elif change == 'units':
                    document.header['$INSUNITS'] = 1
                elif change == 'factor':
                    dimension.set_acad_dstyle({'dimlfac': 100})
                else:
                    document.modelspace().delete_entity(dimension)
                document.saveas(path)
                with self.subTest(change=change):
                    self.assertFalse(validate_dimension_chains_dxf(path, building.layout, layout, building.structural_grid)['ok'])

    def test_thematic_pdf_draws_vector_chains_and_prints_their_source_scope(self):
        building, layout, equipment, flows = example()
        drawing = FacilityDrawingProfile.from_mapping({'preset': 'architecture_dimensions'})
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'sheets.pdf'
            export_drawing_sheet_set(path, building, layout, equipment, flows=flows, drawing_profile=drawing)
            reader = PdfReader(path)
            self.assertEqual(len(reader.pages), 4)
            for page in reader.pages:
                self.assertIn('Dimension chains: projected room edges, structural axes and overall boundary',
                              ' '.join(page.extract_text().split()))
                self.assertEqual(len(page.images), 0)

    def test_full_bundle_qa_checks_opted_in_actual_dimension_definitions(self):
        import json
        from layout_configurator.bundle import write_facility_variant
        from layout_configurator.equipment import validate_equipment_layout
        from layout_configurator.facility import default_facility_profile, validate_building
        from layout_configurator.flows import route_flows, validate_flow_routes
        from layout_configurator.qa import validate_building_bundle
        from layout_configurator.validation import validate_layout
        building, layout, equipment, _ = example()
        profile = replace(default_facility_profile(), drawing=FacilityDrawingProfile.from_mapping({'preset': 'architecture_dimensions'}))
        room_report = validate_layout(building.layout, layout)
        equipment_report = validate_equipment_layout(building, layout, equipment)
        routes = route_flows(building, layout, equipment)
        flow_report = validate_flow_routes(building, layout, routes, equipment)
        report = validate_building(building, layout, equipment, routes, profile=profile,
                                  equipment_report=equipment_report, flow_report=flow_report)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            entry = write_facility_variant(root, building, profile, layout,
                layout_report=room_report, equipment=equipment, equipment_report=equipment_report,
                flow_routes=routes, flow_report=flow_report, facility_report=report)
            (root / 'manifest.json').write_text(json.dumps({'variants': [entry]}), encoding='utf-8')
            source = root / entry['json']
            checks = {check.id: check for check in validate_building_bundle(source, profile=profile).checks}
            self.assertIn('DIMENSION_CHAINS_READBACK', checks)
            self.assertTrue(checks['DIMENSION_CHAINS_READBACK'].ok)
            path = root / entry['dxf']
            document = ezdxf.readfile(path)
            next(iter(document.modelspace().query('DIMENSION'))).dxf.text = '12345'
            document.saveas(path)
            checks = {check.id: check for check in validate_building_bundle(source, profile=profile).checks}
            self.assertFalse(checks['DIMENSION_CHAINS_READBACK'].ok)


if __name__ == '__main__':
    unittest.main()

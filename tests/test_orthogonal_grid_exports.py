"""Actual reference axes and their visible claims must survive physical readback."""

import hashlib
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

import ezdxf
from pypdf import PdfReader, PdfWriter
from pypdf.generic import ArrayObject, ContentStream, FloatObject, NameObject, TextStringObject

from layout_configurator.building import StructuralGridSpec
from layout_configurator.models import WindowSpec
from layout_configurator.orthogonal_grid import validate_orthogonal_grid
from layout_configurator.orthogonal_pdf import export_orthogonal_draft_pdf
from layout_configurator.orthogonal_pdf_qa import validate_orthogonal_pdf
from layout_configurator.orthogonal_projection import export_orthogonal_draft_dxf
from test_orthogonal_pdf import _fixture


class OrthogonalGridExportTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        building, self.layout, self.equipment, self.routing = _fixture()
        self.grid = StructuralGridSpec((0, 2050.5, 6000), (0, 3100.25, 6000), ('A', 'B', 'C'), ('1', '2', '3'))
        self.building = replace(building, structural_grid=self.grid)

    def export_pdf(self, **options):
        self.path = self.root / 'grid.pdf'
        self.index = export_orthogonal_draft_pdf(self.path, self.building, self.layout,
            self.equipment, routing=self.routing, include_grid=True, **options)
        return self.index

    def check(self, **options):
        return validate_orthogonal_pdf(self.path, self.building, self.layout, self.equipment,
            routing=self.routing, index=self.index, include_grid=True, **options)

    def save_pdf(self, reader, streams):
        writer = PdfWriter()
        writer.clone_document_from_reader(reader)
        for number, stream in streams.items():
            stream.operations = stream.operations
            writer.pages[number][NameObject('/Contents')] = writer._add_object(stream)
        with self.path.open('wb') as target:
            writer.write(target)
        self.index['sha256'] = hashlib.sha256(self.path.read_bytes()).hexdigest()

    def test_actual_dxf_reference_axes_preserve_native_coordinates_and_source_claims(self):
        path = self.root / 'grid.dxf'
        report = export_orthogonal_draft_dxf(path, self.building.layout, self.layout,
            structural_grid=self.grid, include_grid=True)
        capability = validate_orthogonal_grid(self.building.layout, self.grid).to_dict()
        self.assertEqual(report.to_dict()['grid_capability'], capability)
        document = ezdxf.readfile(path)
        lines = list(document.modelspace().query('LINE[layer=="A-GRID"]'))
        self.assertEqual(len(lines), 6)
        expected = {axis['id']: axis for axis in capability['axes']}
        for line in lines:
            tags = line.get_xdata('LAYOUT_IR')
            strings = dict(tag.value.split('=', 1) for tag in tags if tag.code == 1000)
            axis = expected.pop(strings['id'])
            self.assertEqual(strings, {'kind': 'grid-axis', 'id': axis['id'],
                                      'direction': axis['direction'], 'label': axis['label']})
            self.assertEqual([tag.value for tag in tags if tag.code == 1040], [axis['coordinate_mm']])
            self.assertEqual(tuple(line.dxf.start), (*axis['start'], 0))
            self.assertEqual(tuple(line.dxf.end), (*axis['end'], 0))
        self.assertFalse(expected)
        self.assertEqual(len(list(document.modelspace().query('TEXT[layer=="A-GRID-TEXT"]'))), 6)

    def test_pdf_native_axes_are_source_bound_and_reader_requires_explicit_capability(self):
        index = self.export_pdf()
        self.assertEqual(index['grid_capability'], validate_orthogonal_grid(self.building.layout, self.grid).to_dict())
        report = self.check()
        self.assertTrue(report['ok'], report)
        self.assertEqual(len(report['actual']['grid_axes_mm']), 6)
        text = ' '.join(page.extract_text() for page in PdfReader(self.path).pages)
        self.assertIn('coordinate 2050.5 mm', text)
        self.assertIn('FULL FACILITY: UNKNOWN', text)
        self.assertFalse(validate_orthogonal_pdf(self.path, self.building, self.layout,
            self.equipment, routing=self.routing, index=index)['ok'])

    def test_rehashed_shift_truncation_missing_identity_hidden_and_dash_axes_fail(self):
        for mode in ('shift', 'truncate', 'missing', 'identity', 'hidden', 'dash', 'phase', 'anisotropic'):
            with self.subTest(mode=mode):
                self.export_pdf()
                reader = PdfReader(self.path)
                stream = ContentStream(reader.pages[0].get_contents(), reader)
                start = next(i for i, (values, op) in enumerate(stream.operations)
                             if op == b'BDC' and values[1].get('/Kind') == '/grid')
                end = next(i for i in range(start + 1, len(stream.operations)) if stream.operations[i][1] == b'EMC')
                if mode == 'missing':
                    del stream.operations[start:end + 1]
                elif mode == 'identity':
                    stream.operations[start][0][1][NameObject('/Id')] = TextStringObject('forged-grid-axis')
                elif mode == 'anisotropic':
                    for values, op in stream.operations[start + 1:end]:
                        if op in (b'm', b'l'):
                            values[0] = FloatObject(float(values[0]) * 10000)
                    stream.operations.insert(start + 1, ([FloatObject(value) for value in (.0001, 0, 0, 1, 0, 0)], b'cm'))
                else:
                    for values, op in stream.operations[start + 1:end]:
                        if mode == 'shift' and op in (b'm', b'l'):
                            values[0] = FloatObject(float(values[0]) + 100)
                        elif mode == 'truncate' and op == b'l':
                            values[1] = FloatObject(float(values[1]) - 100)
                        elif mode == 'hidden' and op == b'RG':
                            values[:] = [FloatObject(1)] * 3
                        elif mode == 'dash' and op == b'd':
                            values[0] = ArrayObject([FloatObject(100), FloatObject(1)])
                        elif mode == 'phase' and op == b'd':
                            values[1] = FloatObject(1)
                self.save_pdf(reader, {0: stream})
                self.assertFalse(self.check()['ok'])

    def test_rehashed_contradictory_visible_source_claim_and_capability_fail(self):
        self.export_pdf()
        reader = PdfReader(self.path)
        stream = ContentStream(reader.pages[1].get_contents(), reader)
        changed = False
        for values, op in stream.operations:
            if op == b'Tj' and 'coordinate 2050.5 mm' in str(values[0]):
                values[0] = TextStringObject(str(values[0]).replace('2050.5', '2150.5'))
                changed = True
        self.assertTrue(changed)
        self.save_pdf(reader, {1: stream})
        self.assertFalse(self.check()['ok'])

    def test_extra_plan_claims_cannot_promote_status_or_contradict_source(self):
        for claim in ('FULL FACILITY: ACCEPTED', 'STRUCTURAL ENGINEERING: APPROVED',
                      'Grid grid:x:0002 | label B | direction x | coordinate 2150.5 mm'):
            with self.subTest(claim=claim):
                self.export_pdf()
                reader = PdfReader(self.path)
                stream = ContentStream(reader.pages[0].get_contents(), reader)
                font = next(iter(reader.pages[0]['/Resources']['/Font']))
                stream.operations.extend([
                    ([], b'q'), ([], b'BT'), ([NameObject(font), FloatObject(9)], b'Tf'),
                    ([FloatObject(value) for value in (1, 0, 0, 1, 50, 110)], b'Tm'),
                    ([TextStringObject(claim)], b'Tj'), ([], b'ET'), ([], b'Q')])
                self.save_pdf(reader, {0: stream})
                self.assertIn(claim, PdfReader(self.path).pages[0].extract_text())
                self.assertFalse(self.check()['ok'])

    def test_visible_label_word_boundaries_cannot_be_changed_after_rehash(self):
        self.building = replace(self.building, structural_grid=replace(self.grid, labels_x=('A B', 'B', 'C')))
        self.export_pdf()
        reader = PdfReader(self.path)
        stream = ContentStream(reader.pages[1].get_contents(), reader)
        changed = False
        for values, op in stream.operations:
            if op == b'Tj' and 'label A B |' in str(values[0]):
                values[0] = TextStringObject(str(values[0]).replace('label A B |', 'label AB |'))
                changed = True
        self.assertTrue(changed)
        self.save_pdf(reader, {1: stream})
        self.assertFalse(self.check()['ok'])
        self.export_pdf()
        self.index['grid_capability']['axes'][0]['coordinate_mm'] = 500
        self.assertFalse(self.check()['ok'])

    def test_strict_flags_and_original_malformed_grid_fail_before_output(self):
        for value in (None, 1, 'true'):
            for format_name in ('dxf', 'pdf'):
                with self.subTest(value=value, format=format_name):
                    path = self.root / ('invalid.' + format_name)
                    with self.assertRaises(ValueError):
                        if format_name == 'dxf':
                            export_orthogonal_draft_dxf(path, self.building.layout, self.layout,
                                structural_grid=self.grid, include_grid=value)
                        else:
                            export_orthogonal_draft_pdf(path, self.building, self.layout, include_grid=value)
                    self.assertFalse(path.exists())
        for invalid in (replace(self.grid, axes_x_mm=(True,)), replace(self.grid, axes_x_mm=(10**400,))):
            for format_name in ('dxf', 'pdf'):
                with self.subTest(format=format_name):
                    path = self.root / ('invalid.' + format_name)
                    with self.assertRaises(ValueError):
                        if format_name == 'dxf':
                            export_orthogonal_draft_dxf(path, self.building.layout, self.layout,
                                structural_grid=invalid, include_grid=True)
                        else:
                            export_orthogonal_draft_pdf(path, replace(self.building, structural_grid=invalid),
                                self.layout, include_grid=True)
                    self.assertFalse(path.exists())

    def test_combined_window_and_grid_capabilities_are_independent(self):
        self.building = replace(self.building, layout=replace(self.building.layout,
            windows=(WindowSpec('source-window', 'main', 'bottom', 1000, 800),)))
        with self.assertRaises(ValueError):
            self.export_pdf()
        index = self.export_pdf(include_windows=True)
        self.assertIn('window_capability', index)
        self.assertTrue(self.check(include_windows=True)['ok'])
        path = self.root / 'combined.dxf'
        export_orthogonal_draft_dxf(path, self.building.layout, self.layout,
            structural_grid=self.grid, include_grid=True, include_windows=True)
        document = ezdxf.readfile(path)
        labels = list(document.modelspace().query('TEXT[layer=="A-GRID-TEXT"]'))
        windows = list(document.modelspace().query('TEXT[layer=="A-WINDOW-TEXT"]'))
        self.assertEqual(len(labels), 6)
        self.assertEqual(len(windows), 1)
        self.assertFalse({tuple(label.dxf.insert) for label in labels} &
                         {tuple(label.dxf.insert) for label in windows})


if __name__ == '__main__':
    unittest.main()

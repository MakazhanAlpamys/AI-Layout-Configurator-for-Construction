"""Source axes are complete reference spans, not snapped room constraints."""

import hashlib
import json
import unittest
from dataclasses import replace

from layout_configurator.building import StructuralGridSpec
from layout_configurator.models import LayoutIR, LayoutResult
from layout_configurator.orthogonal import OrthogonalPolygon
from layout_configurator.orthogonal_validation import validate_orthogonal_layout
from layout_configurator.orthogonal_grid import validate_orthogonal_grid


class OrthogonalGridTests(unittest.TestCase):
    def setUp(self):
        self.spec = LayoutIR.from_mapping({
            'boundary': {'width': 6000, 'height': 6000,
                         'cutouts': [{'x': 0, 'y': 2000, 'width': 4000, 'height': 2000}]},
            'entry_room': 'main',
            'rooms': [{'id': 'main', 'target_area': 28, 'min_area': 28, 'max_area': 28,
                       'min_width': 1000, 'min_depth': 1000}],
        })
        self.grid = StructuralGridSpec((2050.5,), (3100.25,), ('A',), ('1',))

    def test_fractional_axes_span_cutout_without_snapping_or_room_binding(self):
        shape = OrthogonalPolygon(((0, 0), (6000, 0), (6000, 6000), (0, 6000),
                                   (0, 4000), (4000, 4000), (4000, 2000), (0, 2000)))
        layout = LayoutResult(1, {'main': shape})
        self.assertTrue(validate_orthogonal_layout(self.spec, layout).ok)
        report = validate_orthogonal_grid(self.spec, self.grid)
        self.assertTrue(report.ok, report.to_dict())
        axes = {axis.direction: axis for axis in report.axes}
        self.assertEqual(axes['x'].start, (2050.5, 0))
        self.assertEqual(axes['x'].end, (2050.5, 6000))
        self.assertEqual(axes['y'].start, (0, 3100.25))
        self.assertEqual(axes['y'].end, (6000, 3100.25))
        self.assertEqual([axis.label for axis in report.axes], ['A', '1'])
        self.assertEqual([axis.id for axis in report.axes], ['grid:x:0001', 'grid:y:0001'])
        self.assertEqual(report.to_dict()['policy']['name'], 'boundary_spanning_axes_v1')
        self.assertFalse(report.to_dict()['policy']['room_alignment_enforced'])
        self.assertEqual(report.to_dict()['full_facility_validation']['status'], 'UNKNOWN')

    def test_original_integer_grid_source_hash_is_preserved(self):
        grid = StructuralGridSpec((1000,), (2000,))
        report = validate_orthogonal_grid(self.spec, grid)
        canonical = json.dumps(grid.to_dict(), sort_keys=True, separators=(',', ':'),
                               ensure_ascii=False, allow_nan=False).encode('utf-8')
        self.assertTrue(report.ok)
        self.assertEqual(report.to_dict()['source']['structural_grid_sha256'],
                         hashlib.sha256(canonical).hexdigest())
        self.assertEqual([axis.label for axis in report.axes], ['1', '1'])
        self.assertEqual(report.to_dict()['engineering_acceptance'], 'PENDING')

    def test_no_grid_is_an_explicit_empty_reference_capability(self):
        report = validate_orthogonal_grid(self.spec, None)
        self.assertTrue(report.ok)
        self.assertEqual(report.axes, ())

    def test_exact_axis_bound_and_source_byte_bound_are_enforced(self):
        grid = replace(self.grid, axes_x_mm=tuple(range(127)), labels_x=())
        self.assertEqual(len(validate_orthogonal_grid(self.spec, grid).axes), 128)
        oversized = replace(self.spec, project_name='X' * (1024 * 1024))
        report = validate_orthogonal_grid(oversized, self.grid)
        self.assertFalse(report.ok)
        self.assertEqual(report.axes, ())
        self.assertIn('source byte budget', ' '.join(report.issues))

    def test_malformed_source_cannot_pass_through_dataclass_coercion(self):
        invalid = [replace(self.grid, axes_x_mm=(True,)),
                   replace(self.grid, axes_y_mm=('3100.25',)),
                   replace(self.grid, axes_x_mm=(float('nan'),)),
                   replace(self.grid, axes_x_mm=(10 ** 400,)),
                   replace(self.grid, axes_x_mm=(6001,)),
                   replace(self.grid, labels_x=(1,)),
                   replace(self.grid, labels_x=('A' * 129,)),
                   replace(self.grid, axes_x_mm=(1000, 1000), labels_x=('A', 'B')),
                   replace(self.grid, axes_x_mm=(2000, 1000), labels_x=('A', 'B')),
                   replace(self.grid, axes_x_mm=(1000, 2000), labels_x=('A',)),
                   replace(self.grid, axes_x_mm=(), labels_x=()),
                   replace(self.grid, axes_x_mm=tuple(range(128)), labels_x=()),
                   {'axes_x_mm': [1000], 'axes_y_mm': [2000]}]
        for grid in invalid:
            with self.subTest(grid=type(grid).__name__):
                report = validate_orthogonal_grid(self.spec, grid)
                self.assertFalse(report.ok)
                self.assertEqual(report.status, 'FAIL')
                self.assertEqual(report.axes, ())
                self.assertFalse(report.to_dict()['proven_building_infeasible'])
                json.dumps(report.to_dict(), allow_nan=False)


if __name__ == '__main__':
    unittest.main()

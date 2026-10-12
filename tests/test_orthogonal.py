import importlib.util
import unittest

from shapely.geometry import MultiPolygon, Polygon, box
from shapely.ops import unary_union

from layout_configurator.models import Rect


class OrthogonalFootprintTests(unittest.TestCase):
    def footprint(self):
        self.assertIsNotNone(importlib.util.find_spec("layout_configurator.orthogonal"),
                             "Canonical orthogonal footprint is not implemented")
        from layout_configurator.orthogonal import OrthogonalPolygon
        return OrthogonalPolygon

    def test_l_shape_area_and_containment_preserve_missing_corner(self):
        Footprint = self.footprint()
        shape = Footprint(_l_vertices())

        self.assertEqual(shape.area_mm2, 7_000_000)
        self.assertEqual(shape.area_m2, 7)
        self.assertEqual(shape.bounds_rect, Rect(0, 0, 4000, 4000))
        self.assertTrue(shape.contains_point((500, 3500)))
        self.assertTrue(shape.contains_point((1000, 1000)))
        self.assertFalse(shape.contains_point((2500, 2500)))
        self.assertTrue(shape.covers_rect(Rect(0, 1000, 1000, 3000)))
        self.assertFalse(shape.covers_rect(Rect(500, 500, 2000, 2000)))
        self.assertFalse(shape.covers_rect(Rect(1100, 1100, 200, 200)))

    def test_normalization_is_ccw_lexicographic_and_discards_only_redundant_collinear_vertices(self):
        Footprint = self.footprint()
        expected = ((0, 0), (4000, 0), (4000, 1000), (1000, 1000), (1000, 4000), (0, 4000))
        variants = (expected, expected[3:] + expected[:3], tuple(reversed(expected)),
                    ((0, 0), (2000, 0), *expected[1:], (0, 0)))

        for vertices in variants:
            with self.subTest(vertices=vertices):
                shape = Footprint(vertices)
                self.assertEqual(shape.vertices, expected)
                self.assertEqual(shape.to_dict(), {
                    "kind": "orthogonal", "units": "mm", "vertices": [list(vertex) for vertex in expected],
                })
                self.assertEqual(Footprint.from_mapping(shape.to_dict()), shape)

    def test_decomposition_is_disjoint_exact_and_deterministic_without_bbox_fill(self):
        Footprint = self.footprint()
        shape = Footprint(_l_vertices())

        rectangles = shape.decompose()

        self.assertEqual(rectangles, (Rect(0, 0, 4000, 1000), Rect(0, 1000, 1000, 3000)))
        self.assertEqual(sum(rect.area_mm2 for rect in rectangles), 7_000_000)
        for index, first in enumerate(rectangles):
            self.assertTrue(shape.covers_rect(first))
            for second in rectangles[:index]:
                self.assertEqual(first.intersection_area(second), 0)
        actual = unary_union([box(rect.x, rect.y, rect.right, rect.top) for rect in rectangles])
        self.assertEqual(actual.symmetric_difference(Polygon(_l_vertices())).area, 0)
        self.assertEqual(Footprint(tuple(reversed(_l_vertices()))).decompose(), rectangles)

    def test_decomposition_handles_multiple_spans_in_the_same_slab(self):
        Footprint = self.footprint()
        shape = Footprint(((0, 0), (4000, 0), (4000, 4000), (3000, 4000),
                           (3000, 1000), (1000, 1000), (1000, 4000), (0, 4000)))

        self.assertEqual(shape.decompose(), (Rect(0, 0, 4000, 1000), Rect(0, 1000, 1000, 3000), Rect(3000, 1000, 1000, 3000)))
        self.assertEqual(shape.area_m2, 10)
        self.assertFalse(shape.contains_point((2000, 2000)))

    def test_clearance_components_split_a_narrow_neck_and_reject_bbox_fit(self):
        Footprint = self.footprint()
        # Two 4m squares joined by a 0.5m neck: total footprint is connected.
        shape = Footprint(((0, 0), (4000, 0), (4000, 1750), (6000, 1750),
                           (6000, 0), (10000, 0), (10000, 4000), (6000, 4000),
                           (6000, 2250), (4000, 2250), (4000, 4000), (0, 4000)))

        components = shape.inset_components(300)

        self.assertEqual(len(components), 2)
        self.assertEqual([part.bounds_rect for part in components], [Rect(300, 300, 3400, 3400), Rect(6300, 300, 3400, 3400)])
        self.assertTrue(shape.covers_rect(Rect(300, 300, 3400, 3400), clearance_mm=300))
        self.assertFalse(shape.covers_rect(Rect(4100, 1800, 1800, 400), clearance_mm=300))
        self.assertFalse(any(part.contains_point((5000, 2000)) for part in components))
        self.assertEqual(shape.inset_components(2500), ())

    def test_inset_l_shape_is_orthogonal_with_exact_concave_clearance(self):
        Footprint = self.footprint()
        shape = Footprint(_l_vertices())

        parts = shape.inset_components(100)

        self.assertEqual(len(parts), 1)
        self.assertEqual(parts[0].vertices, ((100, 100), (3900, 100), (3900, 900), (900, 900), (900, 3900), (100, 3900)))
        self.assertEqual(parts[0].area_mm2, 5_440_000)
        self.assertFalse(shape.covers_rect(Rect(850, 850, 100, 100), clearance_mm=100))
        self.assertTrue(shape.covers_rect(Rect(100, 100, 800, 800), clearance_mm=100))

    def test_shared_edges_return_real_separate_segments_and_not_missing_corner_edges(self):
        Footprint = self.footprint()
        first = Footprint(_l_vertices())
        adjacent = Footprint.from_rect(Rect(1000, 1000, 3000, 3000))

        self.assertEqual(first.shared_edges(adjacent), (((1000, 1000), (1000, 4000)), ((1000, 1000), (4000, 1000))))
        self.assertEqual(first.shared_boundary(adjacent), 6000)
        self.assertEqual(first.shared_boundary(Footprint.from_rect(Rect(4000, 1000, 1000, 1000))), 0)
        self.assertEqual(first.intersection_area(adjacent), 0)
        self.assertEqual(first.intersection_area(Footprint.from_rect(Rect(500, 500, 1000, 1000))), 750000)

    def test_translation_and_quarter_turn_transform_real_shape_without_rounding(self):
        Footprint = self.footprint()
        shape = Footprint(_l_vertices())

        moved = shape.translated(100, -200)
        turned = shape.rotated_quarter_turns(1, origin=(0, 0))

        self.assertEqual(moved.vertices, ((100, -200), (4100, -200), (4100, 800), (1100, 800), (1100, 3800), (100, 3800)))
        self.assertEqual(turned.vertices, ((-4000, 0), (0, 0), (0, 4000), (-1000, 4000), (-1000, 1000), (-4000, 1000)))
        self.assertEqual(turned.rotated_quarter_turns(3), shape)
        self.assertEqual(shape.rotated_quarter_turns(4), shape)
        self.assertEqual(moved.area_mm2, 7_000_000)
        self.assertEqual(turned.area_mm2, 7_000_000)

    def test_invalid_diagonals_self_intersections_degenerate_values_and_holes_are_rejected(self):
        Footprint = self.footprint()
        for vertices in ([], [(0, 0), (1, 1), (1, 0)], [(0, 0), (4, 0), (4, 4), (1, 4)],
                         [(0, 0), (4, 0), (4, 4), (2, 4), (2, -1), (1, -1), (1, 3), (0, 3)],
                         [(0, 0), (4, 0), (4, 0), (4, 4), (0, 4)],
                         [(0, 0), (1, 0), (2, 0), (3, 0)],
                         [(False, 0), (4, 0), (4, 4), (0, 4)],
                         [("0", 0), (4, 0), (4, 4), (0, 4)],
                         [(float("nan"), 0), (4, 0), (4, 4), (0, 4)]):
            with self.subTest(vertices=vertices):
                with self.assertRaises(ValueError):
                    Footprint(vertices)
        holed = Polygon([(0, 0), (4, 0), (4, 4), (0, 4)], holes=[[(1, 1), (1, 2), (2, 2), (2, 1)]])
        for geometry in (holed, MultiPolygon([box(0, 0, 1, 1), box(2, 0, 3, 1)])):
            with self.subTest(geometry=geometry):
                with self.assertRaisesRegex(ValueError, "single|holes"):
                    Footprint.from_geometry(geometry)

    def test_strict_mapping_and_operation_parameters_do_not_coerce_unsafe_geometry(self):
        Footprint = self.footprint()
        shape = Footprint(_l_vertices())
        for changes in ({"units": "m"}, {"kind": "rectangle"}, {"holes": []}, {"x": 0}):
            with self.subTest(changes=changes):
                with self.assertRaises(ValueError):
                    Footprint.from_mapping({**shape.to_dict(), **changes})
        for clearance in (-1, True, "1", float("inf")):
            with self.subTest(clearance=clearance):
                with self.assertRaises(ValueError):
                    shape.inset_components(clearance)
        for operation in (lambda: shape.translated(True, 1), lambda: shape.rotated_quarter_turns(0.5),
                          lambda: shape.covers_rect(Rect(0, 0, -1, 1)),
                          lambda: shape.covers_rect(Rect("0", 0, 1, 1))):
            with self.assertRaises(ValueError):
                operation()


def _l_vertices():
    return ((0, 0), (4000, 0), (4000, 1000), (1000, 1000), (1000, 4000), (0, 4000))


if __name__ == "__main__":
    unittest.main()

import unittest

from layout_configurator.hierarchy import solve_layouts_hierarchical
from layout_configurator.models import LayoutIR, Rect
from layout_configurator.solver import solve_layouts
from layout_configurator.validation import validate_layout


BACKENDS = (solve_layouts, solve_layouts_hierarchical)


def _room(room_id, area, **extra):
    return {
        "id": room_id,
        "target_area": area,
        "min_area": area,
        "max_area": area,
        "min_width": 2000,
        "min_depth": 2000,
        **extra,
    }


def _corridor_spec(*, preferred=True, forbidden=False, require_a=False, width=8000):
    return LayoutIR.from_mapping(
        {
            "boundary": {"width": width, "height": 4000},
            "entry_room": "corridor",
            "rooms": [
                _room("corridor", width * 2000 / 1_000_000),
                _room("a", 4, required_adjacency=["corridor"]),
                _room("b", 4, required_adjacency=["corridor"]),
                _room(
                    "c", 4,
                    required_adjacency=["corridor", "a"] if require_a else ["corridor"],
                    preferred_adjacency=["b"] if preferred else [],
                    forbidden_adjacency=["b"] if forbidden else [],
                ),
            ],
        }
    )


def _fixed_corridor(width=8000):
    return {
        "corridor": Rect(0, 0, width, 2000),
        "a": Rect(0, 2000, 2000, 2000),
        "b": Rect(width - 2000, 2000, 2000, 2000),
    }


class PreferredAdjacencyTests(unittest.TestCase):
    def _solve(self, backend, spec, fixed):
        result = backend(spec, 1, 3, 1, fixed_rects=fixed)[0]
        report = validate_layout(spec, result)
        self.assertTrue(report.ok, report.issues)
        return result

    def test_preference_beats_position_for_rooms_in_separate_clusters(self):
        # Dropping the preference objective moves c back to x=2000, beside a.
        for backend in BACKENDS:
            with self.subTest(backend=backend.__name__):
                result = self._solve(backend, _corridor_spec(), _fixed_corridor())
                self.assertEqual(result.placements["c"], Rect(4000, 2000, 2000, 2000))

    def test_no_preferences_preserve_the_position_tie_breaker(self):
        for backend in BACKENDS:
            with self.subTest(backend=backend.__name__):
                result = self._solve(backend, _corridor_spec(preferred=False), _fixed_corridor())
                self.assertEqual(result.placements["c"], Rect(2000, 2000, 2000, 2000))

    def test_preferred_neighbours_inside_a_cluster_are_honoured(self):
        # The three leaves share a rigid motif; dropping local preferences
        # must not hide the requested a/c contact from the top-level search.
        spec = LayoutIR.from_mapping(
            {
                "boundary": {"width": 10000, "height": 6000},
                "entry_room": "hub",
                "rooms": [
                    _room("hub", 16, min_width=8000),
                    _room("a", 4, required_adjacency=["hub"], preferred_adjacency=["c"]),
                    _room("b", 4, required_adjacency=["hub"]),
                    _room("c", 4, required_adjacency=["hub"]),
                ],
            }
        )
        result = self._solve(solve_layouts_hierarchical, spec, {})
        self.assertGreaterEqual(result.placements["a"].shared_boundary(result.placements["c"]), 900)

    def test_an_impossible_preference_does_not_override_forbidden_adjacency(self):
        for backend in BACKENDS:
            with self.subTest(backend=backend.__name__):
                result = self._solve(backend, _corridor_spec(forbidden=True), _fixed_corridor())
                self.assertEqual(result.placements["c"], Rect(2000, 2000, 2000, 2000))

    def test_an_impossible_preference_does_not_override_required_adjacency(self):
        for backend in BACKENDS:
            with self.subTest(backend=backend.__name__):
                result = self._solve(backend, _corridor_spec(require_a=True), _fixed_corridor())
                self.assertEqual(result.placements["c"], Rect(2000, 2000, 2000, 2000))

    def test_area_accuracy_beats_an_achievable_preference(self):
        # c can touch b only by growing to 6 m2; its exact 4 m2 target wins.
        spec = LayoutIR.from_mapping(
            {
                "boundary": {"width": 8000, "height": 7000},
                "entry_room": "corridor",
                "rooms": [
                    _room("corridor", 16),
                    _room("a", 30, required_adjacency=["corridor", "b"]),
                    _room("b", 4),
                    _room("c", 4, max_area=8, required_adjacency=["corridor"], preferred_adjacency=["b"]),
                ],
            }
        )
        fixed = {"corridor": Rect(0, 0, 8000, 2000), "a": Rect(0, 2000, 6000, 5000), "b": Rect(6000, 5000, 2000, 2000)}
        for backend in BACKENDS:
            with self.subTest(backend=backend.__name__):
                result = self._solve(backend, spec, fixed)
                self.assertEqual(result.placements["c"], Rect(6000, 2000, 2000, 2000))

    def test_preference_weight_covers_large_position_ranges(self):
        # A constant position weight would make the distant preference lose.
        width = 1_000_000
        for backend in BACKENDS:
            with self.subTest(backend=backend.__name__):
                result = self._solve(backend, _corridor_spec(width=width), _fixed_corridor(width))
                self.assertEqual(result.placements["c"], Rect(width - 4000, 2000, 2000, 2000))

    def test_unsafe_weighted_objectives_are_rejected_explicitly(self):
        spec = LayoutIR.from_mapping(
            {
                "boundary": {"width": 1_000_000_000, "height": 1_000_000_000},
                "rooms": [_room("a", 4, preferred_adjacency=["b"]), _room("b", 4)],
            }
        )
        for backend in BACKENDS:
            with self.subTest(backend=backend.__name__):
                with self.assertRaisesRegex(ValueError, "objective.*integer|integer.*objective"):
                    backend(spec, 1, 0.1, 1)

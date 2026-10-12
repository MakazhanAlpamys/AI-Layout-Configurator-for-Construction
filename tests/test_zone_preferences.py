import threading
import unittest
from pathlib import Path

from layout_configurator.building import BuildingIR
from layout_configurator.equipment import EquipmentLayoutResult, validate_equipment_layout
from layout_configurator.facility import default_facility_profile, validate_building
from layout_configurator.facility_generation import solve_feasible_facility_variants
from layout_configurator.flows import route_flows, validate_flow_routes
from layout_configurator.hierarchy import solve_layouts_auto, solve_layouts_hierarchical
from layout_configurator.models import LayoutIR, LayoutResult, Rect
from layout_configurator.solver import InfeasibleLayout, solve_layouts
from layout_configurator.validation import validate_layout
from layout_configurator.ui import FacilityEditSession


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


def _corridor_spec(width=8000):
    return LayoutIR.from_mapping(
        {
            "boundary": {"width": width, "height": 4000},
            "entry_room": "corridor",
            "rooms": [
                _room("corridor", width * 2000 / 1_000_000),
                _room("a", 4, required_adjacency=["corridor"]),
                _room("b", 4, required_adjacency=["corridor"]),
                _room("c", 4, required_adjacency=["corridor"]),
            ],
        }
    )


def _fixed_corridor(width=8000):
    return {
        "corridor": Rect(0, 0, width, 2000),
        "a": Rect(0, 2000, 2000, 2000),
        "b": Rect(width - 2000, 2000, 2000, 2000),
    }


def _editor_session(building, result):
    profile = default_facility_profile()
    equipment = EquipmentLayoutResult(())
    equipment_report = validate_equipment_layout(building, result, equipment)
    routes = route_flows(building, result, equipment)
    flow_report = validate_flow_routes(building, result, routes, equipment)
    facility_report = validate_building(
        building, result, equipment, routes, flow_report,
        profile=profile, equipment_report=equipment_report,
    )
    return FacilityEditSession(
        input_path=Path("in_memory.json"), output_dir=Path("."),
        building=building, result=result, equipment=equipment,
        equipment_report=equipment_report, flow_routes=routes,
        flow_report=flow_report, facility_report=facility_report,
        profile=profile, lock=threading.RLock(), edit_time_limit_seconds=1,
    )


class ZonePreferredAdjacencyTests(unittest.TestCase):
    def _solve(self, backend, spec, fixed, **options):
        result = backend(spec, 1, 1, 1, fixed_rects=fixed, **options)[0]
        report = validate_layout(spec, result)
        self.assertTrue(report.ok, report.issues)
        return result

    def test_facility_generation_optimises_inherited_zone_preferences(self):
        # The parent has split membership, and its descendant contains c.
        # Only c can reach b. Reverse declarations must give just one credit.
        building = BuildingIR.from_mapping(
            {
                "version": "0.2",
                "layout": _corridor_spec().to_dict(),
                "zones": [
                    {
                        "id": "logistics",
                        "room_ids": ["a"],
                        "preferred_adjacency": ["production"],
                    },
                    {"id": "dispatch", "parent_zone_id": "logistics", "room_ids": ["c"]},
                    {"id": "production", "room_ids": ["b"], "preferred_adjacency": ["logistics"]},
                ],
            }
        )
        self.assertEqual(
            building.zone_relation_groups("preferred_adjacency"),
            (("logistics-production", ("a", "c"), ("b",)),),
        )
        for strategy in ("monolithic", "hierarchical", "auto"):
            with self.subTest(strategy=strategy):
                generated = solve_feasible_facility_variants(
                    building, default_facility_profile(), variants=1,
                    max_attempts=1, time_limit_seconds=1, seed=1,
                    room_solver=strategy, fixed_rects=_fixed_corridor(),
                )
                accepted = generated.accepted[0]
                self.assertEqual(accepted.result.placements["c"], Rect(4000, 2000, 2000, 2000))
                self.assertTrue(accepted.layout_report.ok)
                self.assertTrue(accepted.facility_report.ok)
                self.assertEqual(generated.room_solver_used, "hierarchical" if strategy != "monolithic" else strategy)

    def test_soft_group_cannot_override_required_group(self):
        for backend in BACKENDS:
            with self.subTest(backend=backend.__name__):
                result = self._solve(
                    backend, _corridor_spec(), _fixed_corridor(),
                    preferred_adjacency_groups=[("prefer_b", ["c"], ["b"])],
                    required_adjacency_groups=[("require_a", ["c"], ["a"])],
                )
                self.assertEqual(result.placements["c"], Rect(2000, 2000, 2000, 2000))

    def test_editor_replacement_honours_zone_preference_after_a_colliding_move(self):
        raw = _corridor_spec().to_dict()
        raw["rooms"][0]["min_width"] = 8000
        building = BuildingIR.from_mapping(
            {
                "layout": raw,
                "zones": [
                    {"id": "dispatch", "room_ids": ["c"], "preferred_adjacency": ["production"]},
                    {"id": "production", "room_ids": ["b"]},
                ],
            }
        )
        before = LayoutResult(1, {
            "corridor": Rect(0, 0, 8000, 2000), "a": Rect(6000, 2000, 2000, 2000),
            "b": Rect(4000, 2000, 2000, 2000), "c": Rect(2000, 2000, 2000, 2000),
        })
        self.assertTrue(validate_layout(building.layout, before).ok)
        session = _editor_session(building, before)
        snapshot = session.apply_payload({"type": "move_room", "room_id": "b", "dx_mm": 2000, "dy_mm": 0})
        self.assertEqual(session.result.placements["b"], Rect(6000, 2000, 2000, 2000))
        self.assertGreaterEqual(session.result.placements["c"].shared_boundary(session.result.placements["b"]), 900)
        self.assertTrue(validate_layout(building.layout, session.result).ok)
        self.assertTrue(session.facility_report.ok)
        self.assertTrue(snapshot["can_undo"])
        session.undo()
        self.assertEqual(session.result.placements, before.placements)

    def test_soft_group_cannot_override_forbidden_group(self):
        for backend in BACKENDS:
            with self.subTest(backend=backend.__name__):
                result = self._solve(
                    backend, _corridor_spec(), _fixed_corridor(),
                    preferred_adjacency_groups=[("prefer_b", ["c"], ["b"])],
                    forbidden_adjacency_groups=[("forbid_b", ["c"], ["b"])],
                )
                self.assertEqual(result.placements["c"], Rect(2000, 2000, 2000, 2000))

    def test_area_accuracy_beats_a_zone_contact_that_requires_growth(self):
        spec = LayoutIR.from_mapping(
            {
                "boundary": {"width": 8000, "height": 7000},
                "entry_room": "corridor",
                "rooms": [
                    _room("corridor", 16),
                    _room("a", 30, required_adjacency=["corridor", "b"]),
                    _room("b", 4),
                    _room("c", 4, max_area=8, required_adjacency=["corridor"]),
                ],
            }
        )
        fixed = {"corridor": Rect(0, 0, 8000, 2000), "a": Rect(0, 2000, 6000, 5000), "b": Rect(6000, 5000, 2000, 2000)}
        for backend in BACKENDS:
            with self.subTest(backend=backend.__name__):
                result = self._solve(
                    backend, spec, fixed,
                    preferred_adjacency_groups=[("prefer_b", ["c"], ["b"])],
                )
                self.assertEqual(result.placements["c"], Rect(6000, 2000, 2000, 2000))

    def test_a_group_counts_once_even_when_two_member_pairs_touch(self):
        # At x=8000 c touches both a1 and a2. This gives the group one credit,
        # equal to the room-level c/b preference at x=2000; position wins.
        spec = LayoutIR.from_mapping(
            {
                "boundary": {"width": 12000, "height": 4000},
                "entry_room": "corridor",
                "rooms": [
                    _room("corridor", 24),
                    _room("b", 4, required_adjacency=["corridor"]),
                    _room("a1", 4, required_adjacency=["corridor"]),
                    _room("a2", 4, required_adjacency=["corridor"]),
                    _room("c", 4, required_adjacency=["corridor"], preferred_adjacency=["b"]),
                ],
            }
        )
        fixed = {
            "corridor": Rect(0, 0, 12000, 2000), "b": Rect(0, 2000, 2000, 2000),
            "a1": Rect(6000, 2000, 2000, 2000), "a2": Rect(10000, 2000, 2000, 2000),
        }
        for backend in BACKENDS:
            with self.subTest(backend=backend.__name__):
                result = self._solve(
                    backend, spec, fixed,
                    preferred_adjacency_groups=[("prefer_a", ["c"], ["a1", "a2"])],
                )
                self.assertEqual(result.placements["c"], Rect(2000, 2000, 2000, 2000))
                self.assertEqual(result.objective_value, 1061)

    def test_unfulfillable_empty_and_self_only_preferences_remain_soft(self):
        for backend in BACKENDS:
            with self.subTest(backend=backend.__name__):
                result = self._solve(
                    backend, _corridor_spec(), _fixed_corridor(),
                    preferred_adjacency_groups=[("empty", [], ["b"]), ("self", ["c"], ["c"])],
                )
                self.assertEqual(result.placements["c"], Rect(2000, 2000, 2000, 2000))

    def test_unknown_group_rooms_are_rejected_with_provenance(self):
        for backend in BACKENDS:
            with self.subTest(backend=backend.__name__):
                with self.assertRaisesRegex(InfeasibleLayout, "preferred.*production.*missing"):
                    backend(
                        _corridor_spec(), 1, 0.1, 1,
                        preferred_adjacency_groups=[("production", ["c"], ["missing"])],
                    )

    def test_zone_preference_weight_covers_large_position_ranges(self):
        width = 1_000_000
        for backend in BACKENDS:
            with self.subTest(backend=backend.__name__):
                result = self._solve(
                    backend, _corridor_spec(width), _fixed_corridor(width),
                    preferred_adjacency_groups=[("prefer_b", ["c"], ["b"])],
                )
                self.assertEqual(result.placements["c"], Rect(width - 4000, 2000, 2000, 2000))

    def test_unsafe_group_objective_is_rejected_explicitly(self):
        spec = LayoutIR.from_mapping(
            {
                "boundary": {"width": 1_000_000_000, "height": 1_000_000_000},
                "rooms": [_room("a", 4), _room("b", 4)],
            }
        )
        for backend in BACKENDS:
            with self.subTest(backend=backend.__name__):
                with self.assertRaisesRegex(ValueError, "objective.*integer|integer.*objective"):
                    backend(spec, 1, 0.1, 1, preferred_adjacency_groups=[("prefer_b", ["a"], ["b"])])

    def test_dispatcher_accepts_preferred_group_generators(self):
        evidence = {}
        groups = (("prefer_b", iter(["c"]), iter(["b"])) for _ in range(1))
        result = solve_layouts_auto(
            _corridor_spec(), 1, 1, 1, strategy="hierarchical", evidence=evidence,
            fixed_rects=_fixed_corridor(), preferred_adjacency_groups=groups,
        )[0]
        self.assertEqual(result.placements["c"], Rect(4000, 2000, 2000, 2000))
        self.assertEqual(evidence["room_solver_used"], "hierarchical")
        self.assertTrue(validate_layout(_corridor_spec(), result).ok)

    def test_group_generators_survive_auto_fallback_after_hierarchy_consumes_them(self):
        # Every rectangle is fixed: the hierarchy can supply only one of two
        # requested variants. The monolithic fallback must retain the soft
        # c/a criterion, even though it cannot be fulfilled in this layout.
        evidence = {}
        fixed = {**_fixed_corridor(), "c": Rect(4000, 2000, 2000, 2000)}
        groups = (("prefer_a", iter(["c"]), iter(["a"])) for _ in range(1))
        results = solve_layouts_auto(
            _corridor_spec(), 2, 1, 1, strategy="auto", evidence=evidence,
            fixed_rects=fixed, preferred_adjacency_groups=groups,
        )
        self.assertEqual(len(results), 1)
        self.assertEqual(evidence["room_solver_used"], "monolithic")
        self.assertIn("1 of 2", evidence["hierarchy_fallback_reason"])
        self.assertEqual(results[0].objective_value, 641)
        self.assertTrue(validate_layout(_corridor_spec(), results[0]).ok)

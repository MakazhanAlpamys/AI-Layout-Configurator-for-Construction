"""Opted-in source windows must pass every current polygon generation gate."""

from dataclasses import replace
import unittest

from layout_configurator.equipment import EquipmentLayoutResult, EquipmentPlacement
from layout_configurator.facility import default_facility_profile
from layout_configurator.flows import FlowRoutingResult
from layout_configurator.models import BoundarySpec, LayoutResult, Rect, WindowSpec
from layout_configurator.orthogonal_equipment import place_orthogonal_equipment, validate_orthogonal_door_approaches
from layout_configurator.orthogonal_facility import validate_orthogonal_building
from layout_configurator.orthogonal_flows import route_orthogonal_flows, validate_orthogonal_flow_routes
from layout_configurator.orthogonal_generation import OrthogonalGenerationBudget, generate_orthogonal_facility_variants
from layout_configurator.orthogonal_projection import build_orthogonal_wall_plan
from layout_configurator.orthogonal_solver import solve_orthogonal_candidates
from layout_configurator.orthogonal_validation import validate_orthogonal_equipment, validate_orthogonal_layout
import test_orthogonal_generation as fixtures
from test_orthogonal_solver import _catalog, _l_shape


def _source():
    building = fixtures._source()
    return replace(building, layout=replace(building.layout,
        windows=(WindowSpec("source_window", "main", "bottom", 1000.0, 800.0),)))


def _layout():
    return LayoutResult(1, {"main": _l_shape(), "corner": Rect(2000, 2000, 4000, 4000)})


def _good():
    return EquipmentPlacement("machine", "main", Rect(4800, 400, 500, 500))


class OrthogonalGenerationWindowTests(unittest.TestCase):
    def test_source_only_20_and_16_m2_candidate_passes_fresh_room_equipment_route_profile_gates(self):
        building = _source()
        profile = default_facility_profile()
        before = building.to_dict()
        generated = generate_orthogonal_facility_variants(building, profile=profile,
            seed=1, deterministic_units=.05, include_windows=True)
        self.assertEqual(generated.status, "DRAFT", generated.to_dict(building))
        self.assertFalse(generated.ok)
        candidate = generated.accepted[0]
        self.assertEqual(candidate.result.placements["main"].area_m2, 20)
        self.assertEqual(candidate.result.placements["corner"].area_m2, 16)
        self.assertEqual(len(candidate.equipment.placements), 1)
        self.assertEqual(len(candidate.flow_routes.routes), 1)
        reports = (validate_orthogonal_layout(building.layout, candidate.result, include_windows=True),
            validate_orthogonal_equipment(building, candidate.result, candidate.equipment, include_windows=True),
            validate_orthogonal_door_approaches(building, candidate.result, candidate.equipment, include_windows=True),
            validate_orthogonal_flow_routes(building, candidate.result, candidate.flow_routes, candidate.equipment, include_windows=True),
            validate_orthogonal_building(building, candidate.result, candidate.equipment, candidate.flow_routes,
                profile=profile, include_windows=True))
        for report in reports:
            self.assertTrue(report.ok, report.to_dict())
        capability = reports[-1].local_validation["window_capability"]
        self.assertEqual(capability["policy"]["name"], "source_facade_v1")
        self.assertEqual(capability["facade"]["windows"][0]["id"], "source_window")
        self.assertEqual(capability["source"]["layout_sha256"], fixtures._hash(candidate.result.to_dict()))
        self.assertEqual(capability["full_facility_validation"], {"status": "UNKNOWN", "ok": False})
        self.assertEqual(generated.to_dict(building)["full_facility_validation"], {"status": "UNKNOWN", "ok": False})
        self.assertEqual(building.to_dict(), before)

    def test_omitted_and_false_preserve_legacy_guards_and_serialized_evidence(self):
        for building in (fixtures._source(), _source()):
            with self.subTest(windows=bool(building.layout.windows)):
                layout, equipment = _layout(), EquipmentLayoutResult((_good(),))
                calls = (
                    lambda **kw: solve_orthogonal_candidates(building.layout, catalog=_catalog(), deterministic_units=.05, **kw),
                    lambda **kw: place_orthogonal_equipment(building, layout, catalog={"machine": [_good()]}, deterministic_units=.05, **kw),
                    lambda **kw: validate_orthogonal_layout(building.layout, layout, **kw),
                    lambda **kw: validate_orthogonal_equipment(building, layout, equipment, **kw),
                    lambda **kw: validate_orthogonal_door_approaches(building, layout, equipment, **kw),
                    lambda **kw: route_orthogonal_flows(building, layout, equipment, **kw),
                    lambda **kw: validate_orthogonal_flow_routes(building, layout, FlowRoutingResult(()), equipment, **kw),
                    lambda **kw: validate_orthogonal_building(building, layout, equipment, FlowRoutingResult(()), profile=default_facility_profile(), **kw))
                for call in calls:
                    self.assertEqual(call().to_dict(), call(include_windows=False).to_dict())
                kwargs = dict(profile=default_facility_profile(), room_catalog=_catalog(),
                    equipment_catalog={"machine": [_good()]}, deterministic_units=.05,
                    budget=OrthogonalGenerationBudget(max_room_candidates=1))
                omitted = generate_orthogonal_facility_variants(building, **kwargs)
                explicit = generate_orthogonal_facility_variants(building, include_windows=False, **kwargs)
                self.assertEqual(omitted.to_dict(building), explicit.to_dict(building))
                self.assertEqual(omitted.status, "UNKNOWN" if building.layout.windows else "DRAFT")
                self.assertNotIn("window_capability", omitted.evidence)

    def test_every_public_opt_in_requires_an_actual_boolean(self):
        building, layout = _source(), _layout()
        equipment, routing = EquipmentLayoutResult((_good(),)), FlowRoutingResult(())
        calls = (
            lambda value: validate_orthogonal_layout(building.layout, layout, include_windows=value),
            lambda value: validate_orthogonal_equipment(building, layout, equipment, include_windows=value),
            lambda value: validate_orthogonal_door_approaches(building, layout, equipment, include_windows=value),
            lambda value: place_orthogonal_equipment(building, layout, include_windows=value),
            lambda value: solve_orthogonal_candidates(building.layout, include_windows=value),
            lambda value: route_orthogonal_flows(building, layout, equipment, include_windows=value),
            lambda value: validate_orthogonal_flow_routes(building, layout, routing, equipment, include_windows=value),
            lambda value: validate_orthogonal_building(building, layout, equipment, routing,
                profile=default_facility_profile(), include_windows=value),
            lambda value: generate_orthogonal_facility_variants(building, profile=default_facility_profile(), include_windows=value))
        for index, call in enumerate(calls):
            for value in (0, 1, "true", None):
                with self.subTest(api=index, value=value), self.assertRaisesRegex(ValueError, "include_windows.*boolean"):
                    call(value)

    def test_internal_facade_and_displaced_source_window_fail_without_accepting_room_candidate(self):
        building = _source()
        for window in (WindowSpec("internal", "corner", "left", 1000, 800),
                       replace(building.layout.windows[0], offset_mm=5900)):
            with self.subTest(window=window.id):
                spec = replace(building.layout, windows=(window,))
                report = validate_orthogonal_layout(spec, _layout(), include_windows=True)
                self.assertEqual(report.status, "FAIL", report.to_dict())
                self.assertEqual(report.to_dict()["window_capability"]["status"], "FAIL")
                solved = solve_orthogonal_candidates(spec, catalog=_catalog(), deterministic_units=.05, include_windows=True)
                self.assertEqual(solved.status, "UNKNOWN")
                self.assertFalse(solved.candidates)
                rejection = next(row for row in solved.evidence["rejections"] if row["stage"] == "room_qa")
                self.assertEqual(rejection["status"], "FAIL")
                self.assertEqual(rejection["report"]["window_capability"]["status"], "FAIL")
                self.assertIs(solved.to_dict()["proven_building_infeasible"], False)

    def test_ambiguous_facade_and_vertical_proxy_limit_remain_scoped_unknown(self):
        building = _source()
        ambiguous = replace(building.layout,
            boundary=BoundarySpec(6000, 6000, (Rect(2000, 2000, 4000, 4000),)),
            rooms=(replace(building.layout.rooms[0], required_adjacency=()),),
            windows=(WindowSpec("ambiguous", "main", "right", 1000, 800),))
        report = validate_orthogonal_layout(ambiguous, LayoutResult(1, {"main": _l_shape()}), include_windows=True)
        self.assertEqual(report.status, "UNKNOWN", report.to_dict())
        self.assertEqual(report.to_dict()["window_capability"]["status"], "UNKNOWN")
        source = replace(building, layout=replace(building.layout, window_height_mm=2000.0, window_sill_mm=900.0))
        generated = generate_orthogonal_facility_variants(source, profile=default_facility_profile(),
            room_catalog=_catalog(), deterministic_units=.05, include_windows=True,
            budget=OrthogonalGenerationBudget(max_room_candidates=1))
        self.assertEqual(generated.status, "UNKNOWN")
        self.assertFalse(generated.accepted)
        self.assertIn("coordination wall proxy", str(generated.to_dict(source)))
        self.assertIs(generated.to_dict(source)["proven_building_infeasible"], False)

    def test_window_opt_in_preserves_no_good_retry_and_fresh_source_identity(self):
        building = _source()
        bad = EquipmentPlacement("machine", "main", Rect(1500, 500, 500, 500))
        generated = generate_orthogonal_facility_variants(building, profile=default_facility_profile(),
            room_catalog=_catalog(), equipment_catalog={"machine": [bad, _good()]},
            deterministic_units=.05, include_windows=True,
            budget=OrthogonalGenerationBudget(max_room_candidates=1, max_equipment_retries=2))
        self.assertEqual(generated.status, "DRAFT", generated.to_dict(building))
        candidate = generated.accepted[0]
        self.assertEqual(candidate.equipment.placements, (_good(),))
        self.assertEqual(candidate.evidence["equipment_attempt"], 2)
        self.assertEqual(candidate.evidence["packing"]["matched_excluded_layouts"], 1)
        changed = replace(building, layout=replace(building.layout,
            windows=(WindowSpec("forged_host", "corner", "left", 1000, 800),)))
        fresh = validate_orthogonal_building(changed, candidate.result, candidate.equipment,
            candidate.flow_routes, profile=default_facility_profile(), include_windows=True)
        self.assertEqual(fresh.status, "FAIL")
        self.assertNotEqual(fresh.source["building_sha256"], candidate.facility_report.source["building_sha256"])

    def test_windows_preserve_closed_wall_geometry_and_are_never_circulation_portals(self):
        building, layout = _source(), _layout()
        plain = replace(building.layout, windows=())
        old_walls = build_orthogonal_wall_plan(plain, layout)
        new_walls = build_orthogonal_wall_plan(building.layout, layout, include_windows=True)
        self.assertTrue(old_walls.geometry.equals(new_walls.geometry))
        self.assertEqual(old_walls.openings, new_walls.openings)
        self.assertEqual(len(new_walls.windows), 1)
        routes = route_orthogonal_flows(building, layout, EquipmentLayoutResult((_good(),)), include_windows=True)
        self.assertEqual(routes.status, "DRAFT", routes.to_dict())
        route = routes.routing.routes[0]
        # Leave the allowed building through the real window and return.
        forged = replace(route, points=(route.points[0], (route.points[0][0], 1000), (1000, 1000),
            (1000, -500), (3000, -500), (3000, route.points[-1][1]), route.points[-1]))
        checked = validate_orthogonal_flow_routes(building, layout, FlowRoutingResult((forged,)),
            EquipmentLayoutResult((_good(),)), include_windows=True)
        self.assertEqual(checked.status, "FAIL", checked.to_dict())
        self.assertTrue(any(issue.code in {"wall_collision", "corridor_outside_usable_components"} for issue in checked.issues),
            [issue.to_dict() for issue in checked.issues])

    def test_automatic_daylight_keeps_source_room_and_resolved_window_without_daylight_acceptance(self):
        building = fixtures._source()
        building = replace(building, layout=replace(building.layout,
            rooms=tuple(replace(room, needs_daylight=room.id == "main") for room in building.layout.rooms)))
        generated = generate_orthogonal_facility_variants(building, profile=default_facility_profile(),
            room_catalog=_catalog(), equipment_catalog={"machine": [_good()]},
            deterministic_units=.05, include_windows=True,
            budget=OrthogonalGenerationBudget(max_room_candidates=1))
        self.assertEqual(generated.status, "DRAFT", generated.to_dict(building))
        capability = generated.accepted[0].facility_report.local_validation["window_capability"]
        self.assertEqual(capability["facade"]["windows"][0]["id"], "auto-main")
        self.assertIn("not engineering or daylight acceptance", capability["scope"])
        self.assertTrue(building.layout.room_by_id["main"].needs_daylight)

    def test_equipment_service_cannot_use_a_real_window_as_an_open_wall_gap(self):
        building, layout = _source(), _layout()
        at_window = EquipmentPlacement("machine", "main", Rect(800, 100, 500, 500))
        supplied = EquipmentLayoutResult((at_window,))
        report = validate_orthogonal_equipment(building, layout, supplied, include_windows=True)
        self.assertEqual(report.status, "FAIL", report.to_dict())
        self.assertTrue(any(issue.code == "wall_collision" for issue in report.issues), report.to_dict())
        packed = place_orthogonal_equipment(building, layout, catalog={"machine": [at_window]},
            deterministic_units=.05, include_windows=True)
        self.assertEqual(packed.status, "UNKNOWN")
        self.assertIsNone(packed.equipment)
        self.assertEqual(packed.evidence["stop_reason"], "no_candidate_in_finite_catalog")

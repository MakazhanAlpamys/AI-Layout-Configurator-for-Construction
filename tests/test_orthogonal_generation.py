"""Canonical polygon generation composes fresh local predicates honestly."""

from dataclasses import replace
import hashlib
import importlib.util
import json
import unittest

from layout_configurator.building import BuildingIR
from layout_configurator.equipment import EquipmentPlacement
from layout_configurator.facility import FacilityProfile, default_facility_profile
from layout_configurator.models import Rect
from layout_configurator.orthogonal import OrthogonalPolygon
from layout_configurator.orthogonal_facility import validate_orthogonal_building
from layout_configurator.solver import InfeasibleLayout, solve_layouts
from test_orthogonal_solver import _program, _l_shape, _catalog


class OrthogonalGenerationTests(unittest.TestCase):
    def api(self):
        self.assertIsNotNone(importlib.util.find_spec("layout_configurator.orthogonal_generation"),
                             "Canonical orthogonal facility candidate generation is missing")
        from layout_configurator import orthogonal_generation
        return orthogonal_generation

    def test_source_generated_rectangular_counterexample_passes_all_current_local_components(self):
        api = self.api()
        building = _source()
        with self.assertRaises(InfeasibleLayout):
            solve_layouts(building.layout, seed=1, time_limit_seconds=2, deterministic_units=.05)
        profile = default_facility_profile()
        before = building.to_dict(), profile.to_dict()
        result = api.generate_orthogonal_facility_variants(building, profile=profile, seed=1,
                                                          deterministic_units=.05)
        self.assertEqual(result.status, "DRAFT", result.to_dict(building))
        self.assertFalse(result.ok)
        self.assertEqual(len(result.accepted), 1)
        candidate = result.accepted[0]
        self.assertIsInstance(candidate.result.placements["main"], OrthogonalPolygon)
        self.assertEqual(candidate.result.placements["main"].area_m2, 20)
        self.assertEqual(candidate.result.placements["corner"].area_m2, 16)
        self.assertEqual(len(candidate.equipment.placements), 1)
        self.assertEqual(len(candidate.flow_routes.routes), 1)
        self.assertTrue(candidate.facility_report.ok)
        self.assertTrue(validate_orthogonal_building(building, candidate.result, candidate.equipment,
                        candidate.flow_routes, profile=profile).ok)
        self.assertEqual(candidate.result.variant, 1)
        evidence = result.to_dict(building)
        self.assertEqual(evidence["source"]["building_sha256"], _hash(building.to_dict()))
        self.assertEqual(evidence["source"]["profile_sha256"], _hash(profile.to_dict()))
        self.assertEqual(evidence["full_facility_validation"]["status"], "UNKNOWN")
        self.assertEqual(evidence["engineering_acceptance"], "PENDING")
        self.assertIs(evidence["proven_building_infeasible"], False)
        self.assertEqual((building.to_dict(), profile.to_dict()), before)

    def test_real_route_rejection_retries_a_different_complete_equipment_combination(self):
        api = self.api()
        building = _source()
        bad = EquipmentPlacement("machine", "main", Rect(1500, 500, 500, 500))
        good = EquipmentPlacement("machine", "main", Rect(4800, 400, 500, 500))
        result = api.generate_orthogonal_facility_variants(building, profile=default_facility_profile(),
            room_catalog=_catalog(), equipment_catalog={"machine": [bad, good]}, seed=1, deterministic_units=.05,
            budget=api.OrthogonalGenerationBudget(max_room_candidates=1, max_equipment_retries=2))
        self.assertEqual(result.status, "DRAFT", result.to_dict(building))
        candidate = result.accepted[0]
        self.assertEqual(candidate.equipment.placements, (good,))
        self.assertEqual(candidate.evidence["equipment_attempt"], 2)
        self.assertTrue(any(row["stage"] == "routes" for row in result.rejected))
        route_failure = next(row for row in result.rejected if row["stage"] == "routes")
        self.assertEqual(route_failure["status"], "UNKNOWN")
        self.assertIs(route_failure["proven_building_infeasible"], False)
        self.assertEqual(candidate.evidence["packing"]["matched_excluded_layouts"], 1)

    def test_partial_verified_room_pool_is_consumed_even_when_requested_pool_is_incomplete(self):
        api = self.api()
        building = _source()
        result = api.generate_orthogonal_facility_variants(building, profile=default_facility_profile(),
            room_catalog=_catalog(), equipment_catalog={"machine": [EquipmentPlacement("machine", "main", Rect(4800, 400, 500, 500))]},
            seed=1, deterministic_units=.05, budget=api.OrthogonalGenerationBudget(max_room_candidates=4))
        self.assertEqual(result.status, "DRAFT", result.to_dict(building))
        self.assertEqual(len(result.accepted), 1)
        self.assertEqual(result.evidence["room_search"]["status"], "UNKNOWN")
        self.assertEqual(result.evidence["room_search"]["stop_reason"], "finite_catalog_exhausted")

    def test_failed_and_unresolved_profile_evidence_are_not_published_as_accepted(self):
        api = self.api()
        building = _source()
        for policy, expected in ((_profile("required_flow_types", {"flow_types": ["missing"]}), "FAIL"),
                                 (_profile("pressure_cascade", {"direction": "parent_gt_child"}), "UNKNOWN")):
            with self.subTest(status=expected):
                result = api.generate_orthogonal_facility_variants(building, profile=policy, room_catalog=_catalog(),
                    equipment_catalog={"machine": [EquipmentPlacement("machine", "main", Rect(4800, 400, 500, 500))]},
                    deterministic_units=.05, budget=api.OrthogonalGenerationBudget(max_room_candidates=1, max_equipment_retries=2))
                self.assertEqual(result.status, "UNKNOWN")
                self.assertFalse(result.accepted)
                row = next(row for row in result.rejected if row["stage"] == "profile")
                self.assertEqual(row["status"], expected)
                self.assertEqual(row["evidence"]["report"]["source"]["profile_sha256"], _hash(policy.to_dict()))
                self.assertTrue(any(check.get("rule", {}).get("id") == "declared_policy"
                                    for check in row["evidence"]["report"]["checks"]))

    def test_packing_failure_and_attempt_limit_preserve_scoped_unknown(self):
        api = self.api()
        building = _source()
        result = api.generate_orthogonal_facility_variants(building, profile=default_facility_profile(), room_catalog=_catalog(),
            equipment_catalog={"machine": [EquipmentPlacement("machine", "main", Rect(3000, 3000, 500, 500))]},
            deterministic_units=.05, budget=api.OrthogonalGenerationBudget(max_room_candidates=1, max_equipment_retries=1))
        self.assertEqual(result.status, "UNKNOWN")
        self.assertFalse(result.accepted)
        self.assertEqual(result.rejected[0]["stage"], "equipment")
        self.assertEqual(result.rejected[0]["evidence"]["packing"]["stop_reason"], "no_candidate_in_finite_catalog")
        self.assertIs(result.to_dict(building)["proven_building_infeasible"], False)
        self.assertLessEqual(result.evidence["equipment_attempts"], 1)

    def test_requested_variant_counts_and_rejection_samples_are_bounded(self):
        api = self.api()
        building = _source()
        result = api.generate_orthogonal_facility_variants(building, profile=_profile("required_flow_types", {"flow_types": ["missing"]}),
            deterministic_units=.05, budget=api.OrthogonalGenerationBudget(max_room_candidates=4, max_equipment_retries=3, max_rejections=1))
        self.assertEqual(result.status, "UNKNOWN")
        self.assertFalse(result.accepted)
        self.assertLessEqual(result.evidence["evaluated_room_candidates"], 4)
        self.assertLessEqual(result.evidence["equipment_attempts"], 12)
        self.assertLessEqual(len(result.rejected), 1)
        self.assertGreater(result.evidence["rejection_count"], 1)
        self.assertTrue(result.evidence["rejections_truncated"])
        self.assertIsNotNone(result.evidence["last_rejection"])
        self.assertEqual(sum(result.evidence["rejection_counts"].values()), result.evidence["rejection_count"])

    def test_partial_accepted_variant_set_remains_unknown_with_actual_request_counts(self):
        api = self.api()
        building = _source()
        result = api.generate_orthogonal_facility_variants(building, profile=default_facility_profile(), variants=2,
            room_catalog=_catalog(), equipment_catalog={"machine": [EquipmentPlacement("machine", "main", Rect(4800, 400, 500, 500))]},
            deterministic_units=.05, budget=api.OrthogonalGenerationBudget(max_room_candidates=4))
        self.assertEqual(result.status, "UNKNOWN")
        self.assertFalse(result.ok)
        self.assertEqual(result.requested_variants, 2)
        self.assertEqual(len(result.accepted), 1)
        self.assertTrue(result.accepted[0].facility_report.ok)
        self.assertEqual(result.to_dict(building)["accepted_candidates"], 1)
        self.assertTrue(any(row["stage"] == "candidate_pool" and row["status"] == "UNKNOWN" for row in result.rejected),
                        "Incomplete accepted sets need explicit scoped stage evidence")

    def test_real_wall_clock_deadline_stops_before_launching_geometry_search(self):
        api = self.api()
        building = _source()
        result = api.generate_orthogonal_facility_variants(building, profile=default_facility_profile(), time_limit_seconds=1e-9)
        self.assertEqual(result.status, "UNKNOWN")
        self.assertFalse(result.accepted)
        self.assertEqual(result.evidence["stop_reason"], "time_limit_during_preparation")
        self.assertEqual(result.evidence["equipment_attempts"], 0)
        self.assertEqual(result.rejected[0]["stage"], "budget")
        self.assertIs(result.to_dict(building)["proven_building_infeasible"], False)

    def test_serializing_accepted_evidence_against_a_changed_source_is_rejected(self):
        api = self.api()
        building = _source()
        result = api.generate_orthogonal_facility_variants(building, profile=default_facility_profile(),
            room_catalog=_catalog(), equipment_catalog={"machine": [EquipmentPlacement("machine", "main", Rect(4800, 400, 500, 500))]},
            deterministic_units=.05, budget=api.OrthogonalGenerationBudget(max_room_candidates=1))
        self.assertEqual(result.status, "DRAFT")
        changed = replace(building, equipment=(replace(building.equipment[0], clearance_front_mm=200),))
        for evidence in (result, result.accepted[0]):
            with self.subTest(type=type(evidence).__name__), self.assertRaises(ValueError):
                evidence.to_dict(changed)

    def test_invalid_source_profile_budgets_catalogs_and_options_are_rejected(self):
        api = self.api()
        source = _source()
        for values in ({"variants": True}, {"seed": -1}, {"time_limit_seconds": 0}, {"deterministic_units": float("nan")},
                       {"budget": {}}, {"equipment_catalog": {}}, {"room_catalog": {}}, {"fixed_footprints": {"wrong": _l_shape()}}):
            with self.subTest(values=values), self.assertRaises(ValueError):
                api.generate_orthogonal_facility_variants(source, profile=default_facility_profile(), **values)
        with self.assertRaises(ValueError):
            api.generate_orthogonal_facility_variants(replace(source, layout=replace(source.layout, grid_mm=True)), profile=default_facility_profile())
        with self.assertRaises(ValueError):
            api.generate_orthogonal_facility_variants(source, profile=replace(default_facility_profile(), version=None))
        for name in ("max_room_candidates", "max_equipment_retries", "max_rejections"):
            with self.subTest(name=name), self.assertRaises(ValueError):
                api.OrthogonalGenerationBudget(**{name: True})


def _source():
    return BuildingIR.from_mapping({"layout": _program().to_dict(),
        "zones": [{"id": "process", "type": "process", "room_ids": ["main"]},
                  {"id": "support", "type": "support", "room_ids": ["corner"]}],
        "equipment": [{"id": "machine", "room_id": "main", "width_mm": 500, "depth_mm": 500,
                       "clearance_front_mm": 100, "clearance_back_mm": 100,
                       "clearance_left_mm": 400, "clearance_right_mm": 400, "rotation_allowed": False}],
        "flows": [{"id": "material", "type": "material", "from": ["machine"], "to": ["corner"], "minimum_clear_width_mm": 800}]})


def _profile(kind, parameters):
    return FacilityProfile.from_mapping({"name": "Declared polygon generation policy", "version": "P01",
        "rules": [{"id": "declared_policy", "kind": kind, "title": "Declared project requirement",
                   "source": "Repository-owned project specification", "edition": "P01",
                   "effective_date": "2026-10-12", "evidence": ["Explicit project requirements only"], "parameters": parameters}]})


def _hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
        allow_nan=False, separators=(",", ":")).encode()).hexdigest()

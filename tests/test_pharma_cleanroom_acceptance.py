import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from layout_configurator.building import BuildingIR
from layout_configurator.cli import main
from layout_configurator.equipment import EquipmentLayoutResult, EquipmentPlacementError, EquipmentValidationReport
from layout_configurator.facility import FacilityValidationReport, load_facility_profile, validate_building
from layout_configurator.facility_generation import (
    InfeasibleFacilityGeneration,
    solve_feasible_facility_variants,
)
from layout_configurator.flows import FlowRoutingResult, FlowValidationReport, route_flows, validate_flow_routes
from layout_configurator.models import LayoutResult, Rect
from layout_configurator.qa import validate_building_bundle
from layout_configurator.validation import ValidationReport


class PharmaCleanroomAcceptanceTests(unittest.TestCase):
    def setUp(self):
        self.profile = load_facility_profile("rules/pharma_cleanroom_pilot.yaml")

    def test_composite_profile_is_a_versioned_non_regulatory_project_policy(self):
        self.assertEqual(self.profile.domain, "pharma-cleanroom")
        self.assertEqual(self.profile.version, "1.0")
        self.assertEqual(self.profile.jurisdiction, "project-profile-not-a-regulatory-verdict")
        self.assertEqual(
            {rule.kind for rule in self.profile.rules},
            {
                "required_flow_types",
                "process_sequence",
                "flow_separation",
                "required_zone_fields",
                "zone_classification",
                "airlock_presence",
                "pressure_cascade",
            },
        )
        self.assertTrue(all(rule.source and rule.edition and rule.effective_date and rule.evidence for rule in self.profile.rules))

    def test_positive_golden_case_passes_every_composite_gate(self):
        report = _report(_golden_mapping(), self.profile)

        self.assertTrue(report.ok, report.to_dict())
        self.assertTrue(all(check.ok for check in report.checks))

    def test_missing_airlock_role_is_a_fail(self):
        raw = _golden_mapping()
        raw["zones"] = [zone for zone in raw["zones"] if zone["id"] != "material_gateway"]

        report = _report(raw, self.profile)

        self.assertEqual(_status(report, "CLEANROOM_AIRLOCK_ROLES"), "FAIL")

    def test_missing_class_evidence_is_unknown(self):
        raw = _golden_mapping()
        cleanroom = next(zone for zone in raw["zones"] if zone["id"] == "cleanroom_core")
        cleanroom.pop("cleanroom_class")

        report = _report(raw, self.profile)

        self.assertEqual(_status(report, "CLEANROOM_ZONE_CLASSIFICATION"), "UNKNOWN")

    def test_invalid_pressure_cascade_is_a_fail(self):
        raw = _golden_mapping()
        personnel = next(zone for zone in raw["zones"] if zone["id"] == "personnel_gateway")
        personnel["pressure_pa"] = 25

        report = _report(raw, self.profile)

        self.assertEqual(_status(report, "CLEANROOM_PRESSURE_CASCADE"), "FAIL")

    def test_disconnected_process_sequence_is_a_fail(self):
        raw = _golden_mapping()
        packaging = next(flow for flow in raw["flows"] if flow["id"] == "packaging_transfer")
        packaging["from_ids"] = ["preparation"]

        report = _report(raw, self.profile)

        self.assertEqual(_status(report, "PHARMA_CLEANROOM_PROCESS_SEQUENCE"), "FAIL")

    def test_conflicting_clean_dirty_routes_are_a_fail(self):
        raw = _golden_mapping()
        waste = next(flow for flow in raw["flows"] if flow["id"] == "waste_out")
        waste["from_ids"] = ["raw"]

        report = _report(raw, self.profile)

        self.assertEqual(_status(report, "FLOW_TYPE_SEPARATION"), "FAIL")

    def test_candidate_selection_retries_after_a_rejected_equipment_layout(self):
        building = BuildingIR.from_mapping(_minimal_mapping())
        first = LayoutResult(1, {"source": Rect(0, 0, 3000, 3000), "target": Rect(3000, 0, 3000, 3000)})
        second = LayoutResult(2, {"source": Rect(0, 0, 3000, 3000), "target": Rect(3000, 0, 3000, 3000)})
        profile = load_facility_profile("rules/default_facility.yaml")

        with (
            patch("layout_configurator.facility_generation.solve_layouts", return_value=[first, second]),
            patch("layout_configurator.facility_generation.validate_layout", return_value=ValidationReport(())),
            patch(
                "layout_configurator.facility_generation.place_equipment_clear_of_doors",
                side_effect=[EquipmentPlacementError("does not fit"), (EquipmentLayoutResult(()), ())],
            ),
            patch(
                "layout_configurator.facility_generation.validate_equipment_layout",
                return_value=EquipmentValidationReport(()),
            ),
            patch(
                "layout_configurator.facility_generation.route_flows",
                return_value=FlowRoutingResult(()),
            ),
            patch(
                "layout_configurator.facility_generation.validate_flow_routes",
                return_value=FlowValidationReport(()),
            ),
            patch(
                "layout_configurator.facility_generation.validate_building",
                return_value=FacilityValidationReport(profile, ()),
            ),
        ):
            generation = solve_feasible_facility_variants(
                building,
                profile,
                variants=1,
                time_limit_seconds=1,
                seed=42,
                max_attempts=2,
                equipment_retries=1,
            )

        self.assertEqual(len(generation.accepted), 1)
        self.assertEqual(generation.accepted[0].attempt, 2)
        self.assertEqual(generation.accepted[0].result.variant, 1)
        self.assertEqual(generation.rejected[0].attempt, 1)
        self.assertIn("equipment.infeasible", generation.rejected[0].reasons[0])

    def test_generation_respects_zone_relations_on_the_first_candidate(self):
        raw = {
            "layout": {
                "boundary": {"width": 9000, "height": 6000},
                "entry_room": "a",
                "rooms": [
                    {"id": room, "target_area": 9, "min_area": 9, "max_area": 9,
                     "min_width": 3000, "min_depth": 3000}
                    for room in ("a", "b", "c")
                ],
            },
            "zones": [
                {"id": "zone_a", "type": "clean", "room_ids": ["a"],
                 "required_adjacency": ["zone_c"], "forbidden_adjacency": ["zone_b"]},
                {"id": "zone_b", "type": "dirty", "room_ids": ["b"],
                 "required_adjacency": ["zone_c"]},
                {"id": "zone_c", "type": "gateway", "room_ids": ["c"]},
            ],
        }
        generation = solve_feasible_facility_variants(
            BuildingIR.from_mapping(raw), load_facility_profile("rules/default_facility.yaml"),
            variants=1, time_limit_seconds=5, seed=42, max_attempts=1,
        )
        candidate = generation.accepted[0]
        rooms = candidate.result.placements
        self.assertGreaterEqual(rooms["a"].shared_boundary(rooms["c"]), 900)
        self.assertEqual(rooms["a"].shared_boundary(rooms["b"]), 0)
        self.assertTrue(candidate.facility_report.ok)

    def test_generation_reserves_wall_allowance_for_a_wide_flow(self):
        raw = _minimal_mapping()
        raw["layout"]["boundary"] = {"width": 10000, "height": 8000}
        raw["layout"]["wall_thickness_mm"] = 200
        for room in raw["layout"]["rooms"]:
            room.update(min_area=9, max_area=20, min_width=3000, min_depth=3000)
        raw["flows"] = [{"id": "transfer", "type": "material", "from_ids": ["source"],
                         "to_ids": ["target"], "minimum_clear_width_mm": 2800}]
        generation = solve_feasible_facility_variants(
            BuildingIR.from_mapping(raw), load_facility_profile("rules/default_facility.yaml"),
            variants=1, time_limit_seconds=5, seed=42, max_attempts=1,
        )
        candidate = generation.accepted[0]
        rooms = candidate.result.placements
        self.assertGreaterEqual(rooms["source"].shared_boundary(rooms["target"]), 3400)
        self.assertTrue(candidate.flow_report.ok)

    def test_cli_expands_room_for_equipment_and_exports_a_valid_bundle(self):
        raw = {
            "layout": {
                "boundary": {"width": 10000, "height": 6000},
                "entry_room": "production", "wall_thickness_mm": 200,
                "rooms": [{"id": "production", "target_area": 9, "min_area": 9,
                           "max_area": 30, "min_width": 1000, "min_depth": 1000}],
            },
            "equipment": [{"id": "machine", "type": "machine", "room_id": "production",
                           "width_mm": 5000, "depth_mm": 1000, "clearance_mm": 500,
                           "rotation_allowed": False}],
        }
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            spec_path = root / "program.json"
            output = root / "bundle"
            spec_path.write_text(json.dumps(raw), encoding="utf-8")
            with contextlib.redirect_stdout(io.StringIO()):
                code = main(["generate-building", str(spec_path), "--output", str(output),
                             "--time-limit", "5", "--max-attempts", "1"])
            self.assertEqual(code, 0)
            report = validate_building_bundle(output / "building_01.json")
            self.assertTrue(report.ok, report.to_dict())
            payload = json.loads((output / "building_01.json").read_text(encoding="utf-8"))
            self.assertTrue(payload["equipment_validation"]["ok"])
            self.assertEqual(payload["generation"]["candidate_attempt"], 1)
            # No flows, so no door approaches to keep clear and nothing given up.
            self.assertEqual(payload["generation"]["door_approach_exceptions"], [])

    def test_candidate_selection_rejects_partial_variant_sets(self):
        building = BuildingIR.from_mapping(_minimal_mapping())
        candidate = LayoutResult(1, {"source": Rect(0, 0, 3000, 3000), "target": Rect(3000, 0, 3000, 3000)})
        second = LayoutResult(2, candidate.placements)
        profile = load_facility_profile("rules/default_facility.yaml")

        with (
            patch("layout_configurator.facility_generation.solve_layouts", return_value=[candidate, second]),
            patch(
                "layout_configurator.facility_generation.place_equipment_clear_of_doors",
                side_effect=[(EquipmentLayoutResult(()), ()), EquipmentPlacementError("does not fit")],
            ),
        ):
            with self.assertRaisesRegex(InfeasibleFacilityGeneration, "Only 1 of 2") as caught:
                solve_feasible_facility_variants(
                    building,
                    profile,
                    variants=2,
                    time_limit_seconds=1,
                    seed=42,
                    max_attempts=2,
                    equipment_retries=1,
                )
        evidence = caught.exception.generation.to_dict()
        self.assertEqual(evidence["accepted_candidates"], 1)
        self.assertEqual(evidence["evaluated_candidates"], 2)
        self.assertEqual(evidence["seed"], 42)
        self.assertIn("does not fit", evidence["rejected_candidates"][0]["reasons"][0])

    def test_matrix_retains_failed_candidates_and_does_not_claim_identity_pass(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            spec_path = root / "program.json"
            output = root / "matrix"
            spec_path.write_text(json.dumps(_minimal_mapping()), encoding="utf-8")
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                code = main([
                    "acceptance-building-matrix", str(spec_path), "--output", str(output),
                    "--profile", "rules/pharma_cleanroom_pilot.yaml", "--variants", "1",
                    "--seeds", "7", "--time-limit", "5", "--max-attempts", "1",
                ])
            self.assertEqual(code, 4)
            report = json.loads((output / "acceptance-matrix-report.json").read_text(encoding="utf-8"))
            self.assertFalse(report["ok"])
            self.assertEqual(report["cross_seed_semantic_ids"]["status"], "UNKNOWN")
            self.assertEqual(report["search"]["time_limit_seconds"], 5)
            failure = report["runs"][0]["generation_failure"]
            self.assertEqual(failure["status"], "NO_ACCEPTED_SET_WITHIN_BUDGET")
            self.assertEqual(failure["generation"]["seed"], 7)
            self.assertTrue(failure["generation"]["rejected_candidates"][0]["reasons"])
            self.assertFalse(list(output.glob("seed_*/building_*.json")))

    def test_cli_retains_room_solver_failure_without_publishing_variants(self):
        raw = _minimal_mapping()
        raw["equipment"] = [{"id": "oversized", "type": "machine", "room_id": "source",
                             "width_mm": 20000, "depth_mm": 20000}]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            spec_path = root / "program.json"
            output = root / "bundle"
            spec_path.write_text(json.dumps(raw), encoding="utf-8")
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                code = main(["generate-building", str(spec_path), "--output", str(output),
                             "--time-limit", "5", "--max-attempts", "1"])
            self.assertEqual(code, 2)
            failure = json.loads((output / "generation-failure.json").read_text(encoding="utf-8"))
            self.assertEqual(failure["generation"]["attempted_candidates"], 0)
            self.assertEqual(failure["generation"]["accepted_candidates"], 0)
            self.assertIn("room solver returned no candidate", failure["message"])
            self.assertFalse((output / "manifest.json").exists())


def _report(raw, profile):
    building = BuildingIR.from_mapping(raw)
    result = _golden_layout()
    routes = route_flows(building, result)
    flow_report = validate_flow_routes(building, result, routes)
    return validate_building(
        building,
        result,
        EquipmentLayoutResult(()),
        routes,
        flow_report,
        profile=profile,
        equipment_report=EquipmentValidationReport(()),
    )


def _status(report, check_id):
    return next(check.status for check in report.checks if check.id == check_id)


def _golden_mapping():
    rooms = [
        ("raw", ["material_airlock"]),
        ("material_airlock", ["raw", "preparation"]),
        ("preparation", ["material_airlock", "production"]),
        ("production", ["preparation", "packaging", "waste_hold", "personnel_airlock"]),
        ("packaging", ["production", "finished"]),
        ("finished", ["packaging"]),
        ("waste_hold", ["production"]),
        ("personnel_airlock", ["production", "staff"]),
        ("staff", ["personnel_airlock"]),
    ]
    return {
        "version": "0.2",
        "project_name": "composite profile golden case",
        "layout": {
            "boundary": {"width": 21000, "height": 9000},
            "entry_room": "raw",
            "door_width_mm": 900,
            "rooms": [
                {
                    "id": room_id,
                    "type": room_id,
                    "target_area": 9 if room_id != "production" else 18,
                    "min_area": 1,
                    "max_area": 30,
                    "min_width": 1800,
                    "min_depth": 1800,
                    "required_adjacency": adjacency,
                }
                for room_id, adjacency in rooms
            ],
        },
        "zones": [
            {"id": "logistics", "type": "logistics", "room_ids": ["raw", "finished", "waste_hold", "staff"]},
            {
                "id": "cleanroom_core",
                "type": "cleanroom",
                "room_ids": ["preparation", "production", "packaging"],
                "cleanroom_class": "ISO 7",
                "pressure_pa": 30,
            },
            {
                "id": "personnel_gateway",
                "type": "personnel_airlock",
                "room_ids": ["personnel_airlock"],
                "parent_zone_id": "cleanroom_core",
                "cleanroom_class": "ISO 8",
                "pressure_pa": 20,
                "airlock": True,
            },
            {
                "id": "material_gateway",
                "type": "material_airlock",
                "room_ids": ["material_airlock"],
                "parent_zone_id": "cleanroom_core",
                "cleanroom_class": "ISO 8",
                "pressure_pa": 20,
                "airlock": True,
            },
        ],
        "equipment": [],
        "flows": [
            {"id": "raw_material", "type": "material", "stage": "raw_material", "from_ids": ["raw"], "to_ids": ["material_airlock"], "minimum_clear_width_mm": 800},
            {"id": "production_transfer", "type": "material", "stage": "production", "from_ids": ["material_airlock"], "to_ids": ["production"], "minimum_clear_width_mm": 800},
            {"id": "packaging_transfer", "type": "material", "stage": "packaging", "from_ids": ["production"], "to_ids": ["packaging"], "minimum_clear_width_mm": 800},
            {"id": "finished_goods", "type": "finished_goods", "stage": "finished_goods", "from_ids": ["packaging"], "to_ids": ["finished"], "minimum_clear_width_mm": 800},
            {"id": "personnel", "type": "people", "from_ids": ["staff"], "to_ids": ["production"], "minimum_clear_width_mm": 800},
            {"id": "dirty_material", "type": "dirty_material", "from_ids": ["production"], "to_ids": ["waste_hold"], "minimum_clear_width_mm": 800},
            {"id": "waste_out", "type": "waste", "stage": "waste", "from_ids": ["production"], "to_ids": ["waste_hold"], "minimum_clear_width_mm": 800},
        ],
    }


def _golden_layout():
    return LayoutResult(
        1,
        {
            "raw": Rect(0, 6000, 3000, 3000),
            "material_airlock": Rect(3000, 6000, 3000, 3000),
            "preparation": Rect(6000, 6000, 3000, 3000),
            "production": Rect(9000, 6000, 6000, 3000),
            "packaging": Rect(15000, 6000, 3000, 3000),
            "finished": Rect(18000, 6000, 3000, 3000),
            "waste_hold": Rect(9000, 3000, 3000, 3000),
            "personnel_airlock": Rect(12000, 3000, 3000, 3000),
            "staff": Rect(15000, 3000, 3000, 3000),
        },
    )


def _minimal_mapping():
    return {
        "version": "0.2",
        "project_name": "candidate selection",
        "layout": {
            "boundary": {"width": 6000, "height": 3000},
            "entry_room": "source",
            "rooms": [
                {"id": "source", "type": "room", "target_area": 9, "required_adjacency": ["target"]},
                {"id": "target", "type": "room", "target_area": 9, "required_adjacency": ["source"]},
            ],
        },
        "zones": [],
        "equipment": [],
        "flows": [],
    }


if __name__ == "__main__":
    unittest.main()

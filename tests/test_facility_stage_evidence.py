"""Real failed candidate predicates retain structured, scoped stage evidence."""

from dataclasses import replace
import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from layout_configurator.building import BuildingIR
from layout_configurator.cli import main
from layout_configurator.equipment import EquipmentLayoutResult, EquipmentPlacement
from layout_configurator.facility import FacilityProfile, default_facility_profile
from layout_configurator.facility_generation import InfeasibleFacilityGeneration, solve_feasible_facility_variants
from layout_configurator.models import LayoutResult, Rect


def program(*, equipment=(), flows=(), width=10000):
    return BuildingIR.from_mapping({
        "layout": {"project_name": "Structured stage evidence", "boundary": {"width": width, "height": 5000},
                   "entry_room": "source", "rooms": [
                       {"id": "source", "target_area": 25, "min_area": 25, "max_area": 25,
                        "required_adjacency": ["target"]},
                       {"id": "target", "target_area": 25, "min_area": 25, "max_area": 25}]},
        "equipment": list(equipment), "flows": list(flows),
    })


def candidate():
    return LayoutResult(1, {"source": Rect(0, 0, 5000, 5000), "target": Rect(5000, 0, 5000, 5000)})


class FacilityStageEvidenceTests(unittest.TestCase):
    def solve(self, building, profile=None, **options):
        return solve_feasible_facility_variants(
            building, profile or default_facility_profile(), variants=1, time_limit_seconds=.5,
            deterministic_units=.01, seed=7, max_attempts=1, equipment_retries=2, **options,
        )

    def failed(self, building, profile=None, **options):
        with self.assertRaises(InfeasibleFacilityGeneration) as caught:
            self.solve(building, profile, **options)
        return caught.exception.generation.to_dict()

    def test_actual_room_search_failure_retains_budget_scope_without_infeasibility_claim(self):
        result = self.failed(program(width=5000), room_solver="monolithic")
        stage = result["failure_evidence"][0]
        self.assertEqual(stage["stage"], "rooms")
        self.assertEqual(stage["status"], "UNKNOWN")
        self.assertEqual(stage["proof_scope"], "configured_room_search")
        self.assertFalse(stage["proven_building_infeasible"])
        self.assertEqual(stage["source_ids"], ["source", "target"])
        self.assertEqual(stage["search"]["deterministic_units"], .01)
        self.assertTrue(stage["message"])
        self.assertEqual(result["accepted_candidates"], 0)

    def test_independent_layout_rejection_retains_actual_codes_and_candidate_identity(self):
        overlapping = replace(candidate(), placements={"source": Rect(0, 0, 5000, 5000),
                                                        "target": Rect(0, 0, 5000, 5000)})
        with patch("layout_configurator.facility_generation.solve_layouts_auto", return_value=[overlapping]):
            result = self.failed(program())
        rejected = result["rejected_candidates"][0]
        stage = rejected["stage_evidence"][0]
        self.assertEqual((stage["stage"], stage["status"]), ("rooms", "FAIL"))
        self.assertEqual(stage["proof_scope"], "candidate_predicates")
        self.assertFalse(stage["proven_building_infeasible"])
        self.assertIn("overlap", {issue["code"] for issue in stage["issues"]})
        self.assertEqual((rejected["attempt"], rejected["layout_seed"]), (1, 7))

    def test_actual_impossible_equipment_search_preserves_each_retry_as_unknown(self):
        building = program(equipment=[{"id": identifier, "room_id": "source", "width_mm": 4000,
                                       "depth_mm": 4000} for identifier in ("machine_a", "machine_b")])
        with patch("layout_configurator.facility_generation.solve_layouts_auto", return_value=[candidate()]):
            result = self.failed(building)
        stages = result["rejected_candidates"][0]["stage_evidence"]
        self.assertEqual(len(stages), 2)
        for retry, stage in enumerate(stages, 1):
            self.assertEqual((stage["stage"], stage["status"]), ("equipment", "UNKNOWN"))
            self.assertEqual(stage["proof_scope"], "configured_equipment_search")
            self.assertEqual(stage["retry"], retry)
            self.assertEqual(stage["equipment_seed"], 6 + retry)
            self.assertFalse(stage["proven_building_infeasible"])
            self.assertEqual(stage["source_ids"], ["machine_a", "machine_b"])
            self.assertTrue(stage["message"])

    def test_real_blocked_flow_keeps_endpoint_issue_and_facility_checks(self):
        building = program(equipment=[
            {"id": "machine", "room_id": "source", "width_mm": 500, "depth_mm": 500, "clearance_mm": 100},
            {"id": "barrier", "room_id": "source", "width_mm": 300, "depth_mm": 4800}],
            flows=[{"id": "material", "type": "material", "from_ids": ["machine"],
                    "to_ids": ["target"], "minimum_clear_width_mm": 800}])
        packed = EquipmentLayoutResult((EquipmentPlacement("machine", "source", Rect(400, 400, 500, 500)),
                                        EquipmentPlacement("barrier", "source", Rect(2300, 100, 300, 4800))))
        with (patch("layout_configurator.facility_generation.solve_layouts_auto", return_value=[candidate()]),
              patch("layout_configurator.facility_generation.place_equipment_clear_of_doors", return_value=(packed, ()))):
            result = self.failed(building)
        stages = result["rejected_candidates"][0]["stage_evidence"]
        flow = next(stage for stage in stages if stage["stage"] == "flows")
        self.assertEqual(flow["status"], "FAIL")
        self.assertTrue(flow["issues"])
        self.assertTrue(any(issue.get("flow_id") == "material" for issue in flow["issues"]))
        facility = next(stage for stage in stages if stage["stage"] == "facility")
        self.assertTrue(any(check["status"] == "FAIL" for check in facility["checks"]))
        self.assertTrue(all(not stage["proven_building_infeasible"] for stage in stages))

    def test_independent_equipment_failure_preserves_item_identity_on_each_retry(self):
        building = program(equipment=[{"id": "machine", "room_id": "source", "width_mm": 500, "depth_mm": 500}])
        wrong = EquipmentLayoutResult((EquipmentPlacement("machine", "source", Rect(-400, 400, 500, 500)),))
        with (patch("layout_configurator.facility_generation.solve_layouts_auto", return_value=[candidate()]),
              patch("layout_configurator.facility_generation.place_equipment_clear_of_doors", return_value=(wrong, ()))):
            result = self.failed(building)
        stages = result["rejected_candidates"][0]["stage_evidence"]
        self.assertEqual(len(stages), 2)
        for stage in stages:
            self.assertEqual((stage["stage"], stage["status"]), ("equipment", "FAIL"))
            self.assertEqual(stage["proof_scope"], "candidate_predicates")
            self.assertTrue(any(issue.get("equipment_id") == "machine" for issue in stage["issues"]))
            self.assertFalse(stage["proven_building_infeasible"])

    def test_real_unknown_profile_preserves_rule_provenance_and_unknown_status(self):
        profile = FacilityProfile.from_mapping({"name": "Pressure evidence", "version": "P01",
            "rules": [{"id": "pressure", "kind": "pressure_cascade", "title": "Declared pressure ordering",
                       "source": "Project pressure specification", "edition": "P01", "effective_date": "2026-10-12",
                       "evidence": ["Confirm parent-child pressure edges"],
                       "parameters": {"direction": "parent_gt_child"}}]})
        with patch("layout_configurator.facility_generation.solve_layouts_auto", return_value=[candidate()]):
            result = self.failed(program(), profile)
        stage = next(stage for stage in result["rejected_candidates"][0]["stage_evidence"] if stage["stage"] == "facility")
        self.assertEqual(stage["status"], "UNKNOWN")
        check = next(check for check in stage["checks"] if check.get("rule", {}).get("id") == "pressure")
        self.assertEqual(check["status"], "UNKNOWN")
        self.assertEqual(check["rule"]["source"], "Project pressure specification")
        self.assertTrue(check["evidence"])
        self.assertFalse(stage["proven_building_infeasible"])

    def test_zone_input_failure_retains_original_structured_scope_and_unbuilt_status(self):
        raw = program().to_dict()
        raw["zones"] = [{"id": "process", "room_ids": ["source", "target"]}]
        result = self.failed(BuildingIR.from_mapping(raw), room_solver="zone_recursive",
                             axis_aligned_room_ids=["invented"])
        stage = result["failure_evidence"][0]
        original = result["room_solver_evidence"]["failure"]
        self.assertEqual(stage["solver_failure"], original)
        self.assertEqual(original["code"], "unknown_axis_room")
        self.assertEqual(original["model_status"], "NOT_BUILT")
        self.assertFalse(stage["proven_building_infeasible"])

    def test_success_does_not_add_empty_failure_records_or_change_canonical_geometry(self):
        with patch("layout_configurator.facility_generation.solve_layouts_auto", return_value=[candidate()]):
            result = self.solve(program())
        self.assertNotIn("failure_evidence", result.to_dict())
        self.assertEqual(result.accepted[0].result.to_dict(), candidate().to_dict())
        self.assertEqual(result.to_dict()["rejected_candidates"], [])

    def test_actual_cli_failure_persists_structured_evidence_without_artifacts(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "program.json"
            source.write_text(json.dumps(program(width=5000).to_dict()), encoding="utf-8")
            output = root / "bundle"
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                code = main(["generate-building", str(source), "--output", str(output), "--variants", "1",
                             "--max-attempts", "1", "--time-limit", ".1", "--room-solver", "monolithic"])
            self.assertEqual(code, 2)
            result = json.loads((output / "generation-failure.json").read_text(encoding="utf-8"))
            stage = result["generation"]["failure_evidence"][0]
            self.assertEqual((stage["stage"], stage["status"]), ("rooms", "UNKNOWN"))
            self.assertFalse(stage["proven_building_infeasible"])
            self.assertFalse((output / "manifest.json").exists())
            self.assertFalse(list(output.glob("building_*")))


if __name__ == "__main__":
    unittest.main()

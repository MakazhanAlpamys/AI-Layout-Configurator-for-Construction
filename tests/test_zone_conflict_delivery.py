"""Public facility and CLI delivery retain opted actual conflict evidence."""

from contextlib import redirect_stderr, redirect_stdout
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from layout_configurator.building import BuildingIR
from layout_configurator.cli import main
from layout_configurator.facility import default_facility_profile
from layout_configurator.facility_generation import InfeasibleFacilityGeneration, solve_feasible_facility_variants
from layout_configurator.finite_conflicts import ConflictBudget
from layout_configurator.models import Rect
from test_zone_constraints import _single


def _packing_conflict():
    return BuildingIR.from_mapping({
        "layout": {"project_name": "Captured finite packing conflict",
            "boundary": {"width": 4000, "height": 4000}, "entry_room": "a",
            "rooms": [{"id": room, "target_area": 16, "min_area": 16, "max_area": 16,
                       "min_width": 4000, "min_depth": 4000,
                       "required_adjacency": ["b"] if room == "a" else []}
                      for room in ("a", "b")]},
        "zones": [{"id": room + "_zone", "room_ids": [room]} for room in ("a", "b")]})


class ZoneConflictDeliveryTests(unittest.TestCase):
    def solve(self, building=None, **kwargs):
        return solve_feasible_facility_variants(building or _single(), default_facility_profile(),
            variants=1, max_attempts=1, time_limit_seconds=.5, seed=1,
            deterministic_units=.05, **kwargs)

    def test_actual_fixed_axis_explanation_reaches_public_generation_failure_in_both_modes(self):
        for mode in ("zone_hierarchical", "zone_recursive"):
            with self.subTest(mode=mode):
                try:
                    self.solve(room_solver=mode, explain_zone_conflicts=True,
                        zone_conflict_budget=ConflictBudget(wall_seconds=2, deterministic_units=.1),
                        fixed_rects={"a": Rect(0, 0, 2000, 2000)}, axis_aligned_room_ids=["a"],
                        structural_axes_x_mm=[3000, 5000])
                except TypeError as exc:
                    self.fail("Public generation cannot deliver actual zone conflict explanations: " + str(exc))
                except InfeasibleFacilityGeneration as exc:
                    report = exc.generation.to_dict()
                else:
                    self.fail("Contradictory fixed and axis requirements must not produce an accepted set")
                self.assertEqual(report["accepted_candidates"], 0)
                evidence = report["room_solver_evidence"]["conflict_explanations"]
                proof = evidence["explanations"][-1]["report"]
                self.assertEqual(proof["status"], "EXPLAINED")
                self.assertEqual(proof["core"]["requirement_ids"],
                    ['room_axis:["a","x"]', 'room_fixed:["a"]'])
                self.assertTrue(proof["core"]["subset_minimal"])
                self.assertEqual(len([trial for trial in proof["searches"] if trial["phase"] == "final_deletion"]), 2)
                self.assertFalse(proof["proven_building_infeasible"])
                self.assertEqual(report["failure_evidence"][0]["status"], "UNKNOWN")

    def test_default_and_false_do_not_forward_new_solver_arguments_or_change_serialization(self):
        from layout_configurator.facility_generation import solve_zone_layouts

        outcomes, arguments = [], []
        for options in ({}, {"explain_zone_conflicts": False}):
            with patch("layout_configurator.facility_generation.solve_zone_layouts", wraps=solve_zone_layouts) as solve:
                try:
                    result = self.solve(room_solver="zone_hierarchical", **options)
                except TypeError as exc:
                    self.fail("Explicit false mode is not available: " + str(exc))
            outcomes.append(result.to_dict())
            arguments.append(set(solve.call_args.kwargs))
        self.assertEqual(outcomes[0], outcomes[1])
        self.assertEqual(arguments[0], arguments[1])
        self.assertNotIn("explain_conflicts", arguments[0])
        self.assertNotIn("conflict_budget", arguments[0])
        self.assertNotIn("conflict_explanations", outcomes[0]["room_solver_evidence"])

    def test_invalid_controls_stop_before_either_room_backend(self):
        invalid = [{"explain_zone_conflicts": value} for value in (1, "true", None)]
        invalid += [{"zone_conflict_budget": ConflictBudget()},
                    {"room_solver": "monolithic", "explain_zone_conflicts": True},
                    {"explain_zone_conflicts": True, "zone_conflict_budget": {}}]
        for options in invalid:
            with self.subTest(options=options):
                with patch("layout_configurator.facility_generation.solve_zone_layouts") as zone, \
                        patch("layout_configurator.facility_generation.solve_layouts_auto") as ordinary:
                    try:
                        with self.assertRaises(ValueError):
                            self.solve(**{"room_solver": "zone_hierarchical", **options})
                    except TypeError as exc:
                        self.fail("Missing strict public conflict control: " + str(exc))
                zone.assert_not_called()
                ordinary.assert_not_called()

    def test_actual_cli_retains_initial_global_unknown_base_conflict_with_exact_source(self):
        building = _packing_conflict()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, output = root / "source.json", root / "output"
            source.write_text(json.dumps(building.to_dict()), encoding="utf-8")
            before = source.read_bytes()
            try:
                with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                    code = main(["generate-building", str(source), "--output", str(output),
                        "--room-solver", "zone_hierarchical", "--variants", "1", "--max-attempts", "1",
                        "--time-limit", ".5", "--deterministic-budget", ".05", "--explain-zone-conflicts"])
            except SystemExit as exc:
                self.fail("CLI cannot persist actual opted zone explanations: " + str(exc))
            self.assertEqual(code, 2)
            self.assertEqual(source.read_bytes(), before)
            failure = json.loads((output / "generation-failure.json").read_text(encoding="utf-8"))
            self.assertEqual(failure["spec"], building.to_dict())
            self.assertFalse(failure["ok"])
            evidence = failure["generation"]["room_solver_evidence"]
            self.assertEqual(evidence["failure"]["stage"], "global")
            explanation = evidence["conflict_explanations"]["explanations"][0]
            self.assertEqual(explanation["production_model_status"], "INFEASIBLE")
            self.assertEqual(explanation["report"]["status"], "UNKNOWN")
            self.assertFalse(explanation["report"]["core"]["subset_minimal"])
            self.assertFalse(explanation["report"]["proven_building_infeasible"])
            self.assertFalse((output / "manifest.json").exists())

    def test_cli_rejects_unsupported_modes_before_source_or_output_mutation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, output = root / "missing.json", root / "output"
            for extras in (("--room-solver", "auto"),
                           ("--room-solver", "zone_recursive", "--incremental-from", str(root / "previous.json"))):
                with self.subTest(extras=extras), redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                    try:
                        code = main(["generate-building", str(source), "--output", str(output),
                                     "--explain-zone-conflicts", *extras])
                    except SystemExit as exc:
                        self.fail("CLI missing scoped conflict mode: " + str(exc))
                    self.assertEqual(code, 2)
                    self.assertFalse(output.exists())

    def test_actual_opted_cli_accepted_bundle_keeps_snapshot_and_passes_fresh_saved_qa(self):
        from layout_configurator.qa import validate_building_set

        building = _single()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, output = root / "source.json", root / "accepted"
            source.write_text(json.dumps(building.to_dict()), encoding="utf-8")
            before = source.read_bytes()
            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                code = main(["generate-building", str(source), "--output", str(output),
                    "--room-solver", "zone_hierarchical", "--variants", "1", "--max-attempts", "1",
                    "--time-limit", ".5", "--deterministic-budget", ".05", "--explain-zone-conflicts"])
            self.assertEqual(code, 0)
            self.assertEqual(source.read_bytes(), before)
            manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
            evidence = manifest["generation"]["room_solver_evidence"]["conflict_explanations"]
            self.assertEqual(evidence["solves_used"], 0)
            self.assertEqual(evidence["explanations"], [])
            self.assertEqual(evidence["captures"][0]["capture"]["capture_status"], "COMPLETE")
            self.assertFalse(evidence["proven_building_infeasible"])
            qa = validate_building_set(output, expected_variants=1)
            self.assertTrue(qa.ok, qa.to_dict())
            self.assertTrue(all(check.ok for report in qa.variant_reports for check in report.checks))


if __name__ == "__main__":
    unittest.main()

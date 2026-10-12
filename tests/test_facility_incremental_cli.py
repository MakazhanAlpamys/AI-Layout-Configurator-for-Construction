"""Actual edited-source CLI regeneration retains soft-hint provenance and gates."""

import contextlib
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest

from layout_configurator.cli import main
from layout_configurator.facility import FacilityProfile
from layout_configurator.models import LayoutResult
from layout_configurator.qa import validate_building_set
from test_facility_incremental import building
from test_hierarchy_hints import source_rects


class FacilityIncrementalCLITests(unittest.TestCase):
    def inputs(self, root, mutation="room"):
        prior = root / "prior.json"
        prior.write_text(json.dumps({"spec": building().to_dict(),
                                    "layout": LayoutResult(1, source_rects()).to_dict(),
                                    "equipment": []}), encoding="utf-8")
        raw = building().to_dict()
        if mutation == "room":
            next(room for room in raw["layout"]["rooms"] if room["id"] == "b_leaf_0").update(
                target_area=6, min_area=6, max_area=6, min_width=3000, min_depth=2000)
        elif mutation == "ids":
            raw["equipment"][0]["id"] = "replacement_machine"
        edited = root / "edited.json"
        edited.write_text(json.dumps(raw), encoding="utf-8")
        return prior, edited

    def run_cli(self, prior, edited, output, *extra):
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            return main(["generate-building", str(edited), "--incremental-from", str(prior),
                         "--output", str(output), "--variants", "1", "--max-attempts", "1",
                         "--time-limit", "1", "--seed", "1", "--room-solver", "hierarchical", *extra])

    def test_actual_cli_repairs_room_edit_and_persists_complete_shared_bundle_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            prior, edited = self.inputs(root)
            before = {path: path.read_bytes() for path in (prior, edited)}
            output = root / "bundle"
            self.assertEqual(self.run_cli(prior, edited, output), 0)
            manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
            result = json.loads((output / "building_01.json").read_text(encoding="utf-8"))
            evidence = manifest["generation"]["incremental_evidence"]
            self.assertEqual(evidence["changed_source_ids"]["rooms"], ["b_leaf_0"])
            self.assertEqual(evidence["synthesized_fixed_room_ids"], [])
            self.assertEqual(evidence["source_file_sha256"], hashlib.sha256(before[prior]).hexdigest())
            self.assertEqual(result["generation"]["incremental_evidence"], evidence)
            self.assertFalse(result["generation"]["room_solver_evidence"]["hierarchy_hints"]["positions_are_constraints"])
            self.assertEqual(result["layout"]["rooms"]["b_leaf_0"]["width"] *
                             result["layout"]["rooms"]["b_leaf_0"]["height"], 6_000_000)
            qa = validate_building_set(output, expected_variants=1)
            self.assertTrue(qa.ok, qa.to_dict())
            self.assertEqual({path: path.read_bytes() for path in before}, before)

    def test_changed_source_ids_keep_structured_negative_hashes_without_bundle(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            prior, edited = self.inputs(root, "ids")
            output = root / "failed"
            self.assertEqual(self.run_cli(prior, edited, output), 2)
            failure = json.loads((output / "generation-failure.json").read_text(encoding="utf-8"))
            self.assertEqual(failure["status"], "INCREMENTAL_SOURCE_REJECTED")
            self.assertEqual(failure["input_evidence"]["code"], "source_ids_changed")
            self.assertEqual(failure["input_evidence"]["source_file_sha256"],
                             hashlib.sha256(prior.read_bytes()).hexdigest())
            self.assertFalse(failure["input_evidence"]["proven_building_infeasible"])
            self.assertFalse((output / "manifest.json").exists())
            self.assertFalse(list(output.glob("building_*")))

    def test_actual_current_profile_unknown_keeps_incremental_and_stage_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            prior, edited = self.inputs(root)
            profile = root / "profile.json"
            profile.write_text(json.dumps(FacilityProfile.from_mapping({"name": "Pressure evidence", "version": "P01", "rules": [{
                "id": "pressure", "kind": "pressure_cascade", "title": "Declared pressure ordering",
                "source": "Project pressure specification", "edition": "P01", "effective_date": "2026-10-12",
                "evidence": ["Confirm parent-child pressure edges"],
                "parameters": {"direction": "parent_gt_child"}}]}).to_dict()), encoding="utf-8")
            output = root / "failed"
            self.assertEqual(self.run_cli(prior, edited, output, "--profile", str(profile)), 2)
            failure = json.loads((output / "generation-failure.json").read_text(encoding="utf-8"))
            self.assertEqual(failure["status"], "NO_ACCEPTED_SET_WITHIN_BUDGET")
            self.assertEqual(failure["generation"]["incremental_evidence"]["synthesized_fixed_room_ids"], [])
            stages = failure["generation"]["rejected_candidates"][0]["stage_evidence"]
            self.assertTrue(any(stage["stage"] == "facility" and stage["status"] == "UNKNOWN" for stage in stages))
            self.assertFalse((output / "manifest.json").exists())

    def test_import_pins_cannot_be_combined_with_soft_incremental_geometry(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            prior, edited = self.inputs(root)
            output = root / "failed"
            self.assertEqual(self.run_cli(prior, edited, output, "--imported-plan", "unavailable.json"), 2)
            self.assertFalse(output.exists())

    def test_malformed_or_missing_prior_result_has_structured_input_rejection(self):
        for mutation in ("missing", "malformed", "duplicate"):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                prior, edited = self.inputs(root)
                if mutation == "missing":
                    prior.unlink()
                else:
                    prior.write_text("not-json" if mutation == "malformed" else
                                     '{"spec": {}, "spec": {}, "layout": {}}', encoding="utf-8")
                output = root / "failed"
                self.assertEqual(self.run_cli(prior, edited, output), 2)
                failure = json.loads((output / "generation-failure.json").read_text(encoding="utf-8"))
                self.assertEqual(failure["status"], "INCREMENTAL_SOURCE_REJECTED")
                self.assertEqual(failure["input_evidence"]["code"], "invalid_source_result")
                self.assertFalse(failure["input_evidence"]["proven_building_infeasible"])
                self.assertFalse(list(output.glob("building_*")))

    def test_output_must_not_overwrite_a_supplied_incremental_source(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            prior, edited = self.inputs(root)
            before = {path: path.read_bytes() for path in (prior, edited)}
            self.assertEqual(self.run_cli(prior, edited, root), 2)
            self.assertEqual({path: path.read_bytes() for path in before}, before)
            self.assertFalse((root / "manifest.json").exists())


if __name__ == "__main__":
    unittest.main()

import copy
from dataclasses import replace
import hashlib
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

from layout_configurator.building import BuildingIR


class ProposedBuildingEditsTests(unittest.TestCase):
    def api(self):
        self.assertIsNotNone(importlib.util.find_spec("layout_configurator.proposed_edits"),
                             "Confirmed typed BuildingIR edit proposals are not implemented")
        from layout_configurator import proposed_edits
        return proposed_edits

    def test_proposal_preserves_base_program_and_records_exact_source(self):
        """A proposal must not silently mutate a solver's current program."""
        api = self.api()
        building = _building()
        before = building.to_dict()
        source = "Use a 12 m2 production room with a 2.2 m minimum width."

        draft = api.propose_edits(building, _edits(), source_text=source, producer="offline-model", source_kind="ai_proposal")

        self.assertEqual(building.to_dict(), before)
        self.assertEqual(draft.state, "DRAFT")
        self.assertEqual(draft.source_text, source)
        self.assertEqual(draft.source_sha256, hashlib.sha256(source.encode("utf-8")).hexdigest())
        self.assertEqual(draft.producer, "offline-model")
        self.assertEqual(draft.source_kind, "ai_proposal")

    def test_apply_requires_confirmation_and_named_reviewer(self):
        api = self.api()
        building = _building()
        draft = api.propose_edits(building, _edits(), source_text="Enlarge production and revise circulation.")
        with self.assertRaisesRegex(ValueError, "confirm"):
            api.apply_proposed_edits(building, draft)
        with self.assertRaisesRegex(ValueError, "reviewer"):
            api.apply_proposed_edits(building, draft, confirmed=True)

    def test_confirmed_program_edits_are_typed_and_keep_solver_checks_pending(self):
        api = self.api()
        building = _building()
        draft = api.propose_edits(building, _edits(), source_text="Program revision requested by the process owner.")

        applied = api.apply_proposed_edits(building, draft, confirmed=True, reviewer="Planner A")

        room = applied.building.layout.room_by_id["production"]
        self.assertEqual((room.target_area_m2, room.min_area_m2, room.max_area_m2, room.min_width_mm), (12, 11.64, 12.36, 2200))
        self.assertEqual(applied.building.equipment[0].clearance_front_mm, 600)
        self.assertEqual(applied.building.flows[0].minimum_clear_width_mm, 1000)
        self.assertEqual(applied.evidence["reviewer"], "Planner A")
        self.assertEqual(applied.evidence["validation_status"], "PENDING_SOLVER_AND_CHECKS")
        self.assertTrue(applied.evidence["derived_artifacts_invalidated"])
        self.assertNotEqual(applied.evidence["base_sha256"], applied.evidence["result_sha256"])

    def test_changed_small_program_goes_through_real_solver_and_independent_checks(self):
        api = self.api()
        from layout_configurator.facility import default_facility_profile
        from layout_configurator.facility_generation import solve_feasible_facility_variants
        building = _building()
        draft = api.propose_edits(building, _edits(), source_text="Expand production to 12 m2 and reserve service clearance.")
        applied = api.apply_proposed_edits(building, draft, confirmed=True, reviewer="Planner A")

        generated = solve_feasible_facility_variants(
            applied.building, default_facility_profile(), variants=1, time_limit_seconds=5,
            seed=42, max_attempts=3, room_solver="monolithic",
        )

        self.assertEqual(len(generated.accepted), 1)
        candidate = generated.accepted[0]
        self.assertGreaterEqual(candidate.result.placements["production"].area_m2, 11.64)
        self.assertLessEqual(candidate.result.placements["production"].area_m2, 12.36)
        self.assertTrue(candidate.layout_report.ok)
        self.assertTrue(candidate.equipment_report.ok)
        self.assertTrue(candidate.flow_report.ok)
        self.assertTrue(candidate.facility_report.ok)

    def test_stale_proposal_cannot_apply_to_a_different_base(self):
        api = self.api()
        building = _building()
        draft = api.propose_edits(building, _edits(), source_text="A program revision.")
        changed = replace(building, layout=replace(building.layout, project_name="Another revision"))
        with self.assertRaisesRegex(ValueError, "base|stale"):
            api.apply_proposed_edits(changed, draft, confirmed=True, reviewer="Planner A")

    def test_unknown_paths_coordinates_exports_and_verdict_are_rejected(self):
        api = self.api()
        building = _building()
        bad_inputs = [
            {"edits": _edits()["edits"], "normative_verdict": "PASS"},
            {"edits": [{"operation": "json_patch", "path": "/layout/rooms/0/x", "value": 100}]},
            {"edits": [{"operation": "set_room", "target_id": "production", "changes": {"x": {"value": 100, "unit": "mm"}}}]},
            {"edits": [{"operation": "set_equipment", "target_id": "machine", "changes": {"dxf": "some geometry"}}]},
            {"edits": [{"operation": "set_zone", "target_id": "clean", "changes": {"compliance": "PASS"}}]},
        ]
        for mapping in bad_inputs:
            with self.subTest(mapping=mapping):
                with self.assertRaises(ValueError):
                    api.propose_edits(building, mapping, source_text="Untrusted model output.")

    def test_quantity_units_and_json_types_are_exact_without_conversion(self):
        api = self.api()
        building = _building()
        for quantity in (
            2200, {"value": 2.2, "unit": "m"}, {"value": 2200, "unit": "m2"},
            {"value": "2200", "unit": "mm"}, {"value": True, "unit": "mm"},
            {"value": float("nan"), "unit": "mm"}, {"value": float("inf"), "unit": "mm"},
            {"value": 2200, "unit": "mm", "confidence": 0.99},
        ):
            with self.subTest(quantity=quantity):
                with self.assertRaises(ValueError):
                    api.propose_edits(building, {"edits": [{
                        "operation": "set_room", "target_id": "production", "changes": {"min_width": quantity},
                    }]}, source_text="A dimension proposal.")

    def test_unknown_targets_and_references_are_rejected(self):
        api = self.api()
        building = _building()
        for edit in (
            {"operation": "set_room", "target_id": "missing", "changes": {"needs_daylight": True}},
            {"operation": "set_room", "target_id": "production", "changes": {"required_adjacency": ["missing"]}},
            {"operation": "set_flow", "target_id": "process", "changes": {"to_ids": ["missing"]}},
        ):
            with self.subTest(edit=edit):
                with self.assertRaises(ValueError):
                    api.propose_edits(building, {"edits": [edit]}, source_text="A program proposal.")

    def test_duplicate_field_edits_cannot_silently_overwrite_each_other(self):
        api = self.api()
        edit = {"operation": "set_equipment", "target_id": "machine", "changes": {"width_mm": {"value": 700, "unit": "mm"}}}
        with self.assertRaisesRegex(ValueError, "duplicate"):
            api.propose_edits(_building(), {"edits": [edit, copy.deepcopy(edit)]}, source_text="Ambiguous dimensions.")

    def test_apply_revalidates_domain_atomically_instead_of_trusting_draft_dataclass(self):
        api = self.api()
        building = _building()
        before = building.to_dict()
        draft = api.propose_edits(building, _edits(), source_text="A program proposal.")
        invalid = replace(draft, edits=(replace(draft.edits[0], changes={"max_area": {"value": 1, "unit": "m2"}}),))

        with self.assertRaises(ValueError):
            api.apply_proposed_edits(building, invalid, confirmed=True, reviewer="Planner A")
        self.assertEqual(building.to_dict(), before)

    def test_draft_json_roundtrip_preserves_source_and_rejects_mutated_evidence(self):
        api = self.api()
        draft = api.propose_edits(_building(), _edits(), source_text="The submitted program brief.")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "proposal.json"
            api.write_proposed_edits(path, draft)
            self.assertEqual(api.read_proposed_edits(path).to_dict(), draft.to_dict())
            raw = json.loads(path.read_text(encoding="utf-8"))
            raw["source"]["text"] = "Changed after extraction."
            path.write_text(json.dumps(raw), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "source.*hash"):
                api.read_proposed_edits(path)


def _edits():
    return {"edits": [
        {"operation": "set_room", "target_id": "production", "changes": {
            "target_area": {"value": 12, "unit": "m2"},
            "min_area": {"value": 11.64, "unit": "m2"},
            "max_area": {"value": 12.36, "unit": "m2"},
            "min_width": {"value": 2200, "unit": "mm"},
        }},
        {"operation": "set_equipment", "target_id": "machine", "changes": {"clearance_front_mm": {"value": 600, "unit": "mm"}}},
        {"operation": "set_flow", "target_id": "process", "changes": {"minimum_clear_width_mm": {"value": 1000, "unit": "mm"}}},
    ]}


def _building():
    return BuildingIR.from_mapping({
        "layout": {
            "project_name": "typed edits test", "boundary": {"width": 8000, "height": 4000}, "entry_room": "production",
            "rooms": [
                {"id": "production", "type": "room", "target_area": 9, "min_width": 2000, "min_depth": 2000, "required_adjacency": ["dispatch"]},
                {"id": "dispatch", "type": "room", "target_area": 9, "min_width": 2000, "min_depth": 2000, "required_adjacency": ["production"]},
            ],
        },
        "equipment": [{"id": "machine", "type": "machine", "room_id": "production", "width_mm": 500, "depth_mm": 500}],
        "flows": [{"id": "process", "type": "material", "from_ids": ["production"], "to_ids": ["dispatch"], "minimum_clear_width_mm": 800}],
    })


if __name__ == "__main__":
    unittest.main()

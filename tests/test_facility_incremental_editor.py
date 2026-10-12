"""Opt-in editor repairs keep only the user-selected position fixed."""

import json
import tempfile
import threading
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from layout_configurator.building import BuildingIR
from layout_configurator.commands import EditError, MoveRoom
from layout_configurator.equipment import place_equipment_clear_of_doors, validate_equipment_layout
from layout_configurator.facility import default_facility_profile, validate_building
from layout_configurator.flows import route_flows, validate_flow_routes
from layout_configurator.models import LayoutResult, Rect
from layout_configurator.qa import validate_building_bundle
from layout_configurator.ui import FacilityEditSession
from layout_configurator.validation import validate_layout
from test_facility_incremental import building
from test_hierarchy_hints import source_rects


def editor_program():
    raw = building().to_dict()
    raw["layout"]["rooms"].append({"id": "link", "target_area": 8, "min_area": 8, "max_area": 8,
                                   "min_width": 4000, "min_depth": 2000,
                                   "required_adjacency": ["a_hub", "b_hub"]})
    return BuildingIR.from_mapping(raw), LayoutResult(1, {**source_rects(), "link": Rect(2000, 4000, 4000, 2000)})


class FacilityIncrementalEditorTests(unittest.TestCase):
    def session(self, **options):
        current, result = editor_program()
        profile = default_facility_profile()
        equipment, _ = place_equipment_clear_of_doors(current, result, time_limit_seconds=.5, seed=1)
        equipment_report = validate_equipment_layout(current, result, equipment)
        routes = route_flows(current, result, equipment)
        flow_report = validate_flow_routes(current, result, routes, equipment)
        facility_report = validate_building(current, result, equipment, routes, flow_report,
                                           profile=profile, equipment_report=equipment_report)
        self.assertTrue(facility_report.ok, facility_report.to_dict())
        session = FacilityEditSession(
            input_path=Path("in_memory.json"), output_dir=Path("."), building=current, result=result,
            equipment=equipment, equipment_report=equipment_report, flow_routes=routes,
            flow_report=flow_report, facility_report=facility_report, profile=profile,
            lock=threading.RLock(), edit_time_limit_seconds=1, **options)
        session.initial_state = session._state()
        return session

    def assert_all_gates(self, session):
        self.assertTrue(validate_layout(session.building.layout, session.result).ok)
        self.assertTrue(session.equipment_report.ok, session.equipment_report.issues)
        self.assertTrue(session.flow_report.ok, session.flow_report.issues)
        self.assertTrue(session.facility_report.ok, session.facility_report.to_dict())

    def move(self, session):
        return session.apply_payload({"type": "move_room", "room_id": "link", "dx_mm": 2000, "dy_mm": 0})

    def test_actual_hierarchy_repair_uses_cache_and_soft_neighbors_with_every_gate(self):
        session = self.session(edit_room_solver="hierarchical", edit_cache_cluster_shapes=True)
        previous = dict(session.result.placements)
        snapshot = self.move(session)
        self.assert_all_gates(session)
        self.assertEqual(session.result.placements["link"], replace(previous["link"], x=4000))
        self.assertNotEqual(session.result.placements["a_hub"], previous["a_hub"])
        self.assertNotEqual(session.result.placements["b_hub"], previous["b_hub"])
        self.assertEqual(set(session.result.placements), set(previous))
        evidence = snapshot["room_solver_evidence"]
        self.assertEqual(evidence["room_solver_used"], "hierarchical")
        self.assertEqual(evidence["user_fixed_room_ids"], ["link"])
        self.assertFalse(evidence["hierarchy_hints"]["positions_are_constraints"])
        self.assertEqual(evidence["cluster_shape_cache"]["hits"], 1)
        self.assertEqual(evidence["cluster_shape_cache"]["misses"], 1)
        self.assertIn(evidence["hierarchy_hints"]["global_search"][-1]["model_status"], {"FEASIBLE", "OPTIMAL"})

    def test_undo_redo_reset_restore_geometry_and_the_corresponding_evidence(self):
        session = self.session(edit_room_solver="hierarchical", edit_cache_cluster_shapes=True)
        before = dict(session.result.placements)
        after = self.move(session)
        repaired = dict(session.result.placements)
        self.assertNotIn("room_solver_evidence", session.undo())
        self.assertEqual(session.result.placements, before)
        redone = session.redo()
        self.assertEqual(session.result.placements, repaired)
        self.assertEqual(redone["room_solver_evidence"], after["room_solver_evidence"])
        self.assertNotIn("room_solver_evidence", session.reset())
        self.assertEqual(session.result.placements, before)
        self.assert_all_gates(session)

    def test_failed_edit_preserves_the_last_accepted_state_and_evidence(self):
        session = self.session(edit_room_solver="hierarchical", edit_cache_cluster_shapes=True)
        before = self.move(session)
        state = session._state()
        with self.assertRaises(EditError):
            session.apply_payload({"type": "move_room", "room_id": "link", "dx_mm": 500000, "dy_mm": 0})
        self.assertEqual(session._state(), state)
        self.assertEqual(session.snapshot(), before)

    def test_default_repair_keeps_existing_behavior_without_new_metadata(self):
        session = self.session()
        snapshot = self.move(session)
        self.assertEqual(session.result.placements["link"], Rect(4000, 4000, 4000, 2000))
        self.assert_all_gates(session)
        self.assertNotIn("room_solver_evidence", snapshot)
        self.assertNotIn("edit_solver", snapshot)

    def test_hierarchy_without_cache_still_repairs_with_soft_hint_evidence(self):
        session = self.session(edit_room_solver="hierarchical")
        snapshot = self.move(session)
        self.assert_all_gates(session)
        self.assertIn("hierarchy_hints", snapshot["room_solver_evidence"])
        self.assertNotIn("cluster_shape_cache", snapshot["room_solver_evidence"])

    def test_invalid_editor_options_are_rejected_before_an_edit(self):
        for options in ({"edit_room_solver": "zone_recursive"}, {"edit_cache_cluster_shapes": 1}):
            with self.subTest(options=options), self.assertRaises(ValueError):
                self.session(**options)

    def test_saved_revision_keeps_solver_evidence_and_passes_independent_bundle_readback(self):
        session = self.session(edit_room_solver="hierarchical", edit_cache_cluster_shapes=True)
        self.move(session)
        with tempfile.TemporaryDirectory() as directory:
            session.output_dir = Path(directory)
            saved = session.save_revision()
            result_path = Path(saved["saved_revision"]) / "building_01.json"
            payload = json.loads(result_path.read_text(encoding="utf-8"))
            self.assertEqual(payload["generation"]["room_solver_evidence"], session.room_solver_evidence)
            report = validate_building_bundle(result_path)
            self.assertTrue(report.ok, report.to_dict())

    def test_opt_in_reopen_and_no_edit_save_preserve_persisted_solver_provenance(self):
        session = self.session(edit_room_solver="hierarchical", edit_cache_cluster_shapes=True)
        self.move(session)
        with tempfile.TemporaryDirectory() as directory:
            session.output_dir = Path(directory)
            revision = Path(session.save_revision()["saved_revision"])
            source = revision / "building_01.json"
            reopened = FacilityEditSession.from_input(
                source, revision, edit_room_solver="hierarchical", edit_cache_cluster_shapes=True)
            self.assert_all_gates(reopened)
            self.assertEqual(reopened.snapshot()["room_solver_evidence"], session.room_solver_evidence)
            saved = Path(reopened.save_revision()["saved_revision"]) / "building_01.json"
            payload = json.loads(saved.read_text(encoding="utf-8"))
            self.assertEqual(payload["generation"]["room_solver_evidence"], session.room_solver_evidence)
            self.assertTrue(validate_building_bundle(saved).ok)

    def test_geometry_valid_move_clears_stale_solver_evidence_and_undo_restores_it(self):
        session = self.session(edit_room_solver="hierarchical", edit_cache_cluster_shapes=True)
        repaired = self.move(session)
        payload = None
        for room_id in ("a_leaf_0", "a_leaf_1", "b_leaf_0", "b_leaf_1"):
            for dx, dy in ((0, 100), (0, -100), (100, 0), (-100, 0)):
                _, candidate = MoveRoom(room_id, dx, dy).apply(session.building.layout, session.result)
                if validate_layout(session.building.layout, candidate).ok:
                    payload = {"type": "move_room", "room_id": room_id, "dx_mm": dx, "dy_mm": dy}
                    break
            if payload:
                break
        self.assertIsNotNone(payload, "The fixture must allow a direct geometry-valid room move")
        with patch("layout_configurator.ui.solve_layouts_auto") as solver:
            snapshot = session.apply_payload(payload)
        solver.assert_not_called()
        self.assert_all_gates(session)
        self.assertNotIn("room_solver_evidence", snapshot)
        self.assertEqual(session.undo()["room_solver_evidence"], repaired["room_solver_evidence"])

    def test_snapshot_evidence_mutation_cannot_change_the_accepted_state_or_undo_history(self):
        session = self.session(edit_room_solver="hierarchical", edit_cache_cluster_shapes=True)
        snapshot = self.move(session)
        snapshot["room_solver_evidence"]["cluster_shape_cache"]["hits"] = 999
        self.assertEqual(session.snapshot()["room_solver_evidence"]["cluster_shape_cache"]["hits"], 1)
        session.undo()
        restored = session.redo()
        self.assertEqual(restored["room_solver_evidence"]["cluster_shape_cache"]["hits"], 1)


if __name__ == "__main__":
    unittest.main()

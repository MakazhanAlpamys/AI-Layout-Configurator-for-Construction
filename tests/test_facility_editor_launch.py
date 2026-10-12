"""Actual CLI launch controls reach facility edits and their saved evidence."""

import contextlib
import io
import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.request import Request, urlopen

import layout_configurator.ui as ui
import test_facility_incremental_editor as editor_fixture
from layout_configurator.cli import main
from layout_configurator.io import write_building_result, write_result
from layout_configurator.qa import validate_building_bundle


class FacilityEditorLaunchTests(unittest.TestCase):
    def source(self, root):
        session = editor_fixture.FacilityIncrementalEditorTests().session()
        path = root / "building_01.json"
        write_building_result(path, session.building, session.result, session.equipment,
                              session.flow_routes, session.flow_report,
                              equipment_report=session.equipment_report, facility_report=session.facility_report)
        return path

    def launch(self, source, root, arguments):
        observed = {}
        real_factory = ui.create_ui_server

        def factory(session, address, **options):
            observed["factory_options"] = options
            observed["session"] = session
            session.edit_time_limit_seconds = 1  # Bound this actual small fixture.
            server = real_factory(session, address, **options)
            native_serve = server.serve_forever
            worker = threading.Thread(target=native_serve, daemon=True)
            worker.start()
            base = f"http://127.0.0.1:{server.server_port}"

            def exercise_http():
                try:
                    with urlopen(base + "/api/state", timeout=5) as response:
                        observed["before"] = json.load(response)
                    command = Request(base + "/api/command", method="POST",
                                      headers={"Content-Type": "application/json"},
                                      data=json.dumps({"type": "move_room", "room_id": "link",
                                                       "dx_mm": 2000, "dy_mm": 0}).encode())
                    with urlopen(command, timeout=5) as response:
                        observed["after"] = json.load(response)
                    with urlopen(Request(base + "/api/save", method="POST"), timeout=5) as response:
                        observed["saved"] = json.load(response)
                finally:
                    server.shutdown()
                    worker.join(timeout=5)
                    self.assertFalse(worker.is_alive())

            server.serve_forever = exercise_http
            return server

        with patch.object(ui, "create_ui_server", side_effect=factory), contextlib.redirect_stdout(io.StringIO()):
            observed["code"] = main(["ui", str(source), "--output", str(root / "exports"),
                                     "--host", "127.0.0.1", "--port", "0", "--edit", *arguments])
        return observed

    def test_actual_cli_launch_http_edit_and_save_retain_hierarchy_cache_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            observed = self.launch(self.source(root), root,
                                   ["--edit-room-solver", "hierarchical", "--edit-cache-cluster-shapes"])
            self.assertEqual(observed["code"], 0)
            self.assertEqual(observed["before"]["edit_solver"],
                             {"room_solver": "hierarchical", "cache_cluster_shapes": True})
            evidence = observed["after"]["room_solver_evidence"]
            self.assertEqual(evidence["room_solver_used"], "hierarchical")
            self.assertEqual(evidence["cluster_shape_cache"]["hits"], 1)
            self.assertFalse(evidence["hierarchy_hints"]["positions_are_constraints"])
            self.assertEqual(evidence["user_fixed_room_ids"], ["link"])
            editor_fixture.FacilityIncrementalEditorTests().assert_all_gates(observed["session"])
            source = Path(observed["saved"]["saved_revision"]) / "building_01.json"
            payload = json.loads(source.read_text(encoding="utf-8"))
            self.assertEqual(payload["generation"]["room_solver_evidence"], evidence)
            self.assertTrue(validate_building_bundle(source).ok)

    def test_default_cli_launch_keeps_existing_factory_call_and_metadata(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            observed = self.launch(self.source(root), root, [])
            self.assertEqual(observed["code"], 0)
            self.assertEqual(observed["factory_options"], {})
            self.assertNotIn("edit_solver", observed["after"])
            self.assertNotIn("room_solver_evidence", observed["after"])
            source = Path(observed["saved"]["saved_revision"]) / "building_01.json"
            self.assertNotIn("room_solver_evidence", json.loads(source.read_text(encoding="utf-8"))["generation"])

    def test_cli_controls_without_edit_are_rejected_before_opening_a_server(self):
        for flags in (["--edit-room-solver", "monolithic"], ["--edit-cache-cluster-shapes"]):
            with self.subTest(flags=flags), patch.object(ui, "create_ui_server") as factory:
                errors = io.StringIO()
                with contextlib.redirect_stderr(errors):
                    code = main(["ui", "missing.json", *flags])
                self.assertEqual(code, 2)
                self.assertIn("--edit", errors.getvalue())
                factory.assert_not_called()

    def test_explicit_controls_reject_plain_layout_fallback_even_with_edit_flag(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            current, result = editor_fixture.editor_program()
            source = root / "layout.json"
            write_result(source, current.layout, result)
            with patch.object(ui, "create_ui_server") as factory:
                with self.assertRaisesRegex(ValueError, "facility"):
                    ui.serve_ui(source, root, edit=True, edit_room_solver="hierarchical")
            factory.assert_not_called()

    def test_server_factory_rejects_controls_for_read_only_facility_review(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            session = ui.FacilityReviewSession.from_input(self.source(root), root)
            with self.assertRaisesRegex(ValueError, "facility editor"):
                ui.create_ui_server(session, edit_room_solver="hierarchical")

    def test_invalid_controls_are_rejected_before_input_work_or_session_mutation(self):
        for options in ({"edit_room_solver": "zone_recursive"}, {"edit_cache_cluster_shapes": 1}):
            with self.subTest(options=options):
                with self.assertRaises(ValueError):
                    ui.serve_ui("missing.json", ".", edit=True, **options)
                session = editor_fixture.FacilityIncrementalEditorTests().session()
                before = (session.edit_room_solver, session.edit_cache_cluster_shapes)
                with self.assertRaises(ValueError):
                    ui.create_ui_server(session, **options)
                self.assertEqual((session.edit_room_solver, session.edit_cache_cluster_shapes), before)


if __name__ == "__main__":
    unittest.main()

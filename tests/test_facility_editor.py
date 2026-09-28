import contextlib
import io
import json
import shutil
import tempfile
import threading
import unittest
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from layout_configurator.cli import main
from layout_configurator.commands import EditError
from layout_configurator.qa import validate_building_bundle
from layout_configurator.ui import FacilityEditSession, FacilityReviewSession, create_ui_server


class FacilityEditorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.generated = Path(cls._tmp.name) / "generated"
        with contextlib.redirect_stdout(io.StringIO()):
            code = main([
                "generate-building", "examples/pharma_cleanroom_pilot.yaml",
                "--profile", "rules/pharma_cleanroom_pilot.yaml",
                "--output", str(cls.generated), "--variants", "1", "--max-attempts", "3",
                "--time-limit", "20", "--seed", "1",
            ])
        assert code == 0

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def _session(self):
        bundle = Path(tempfile.mkdtemp(dir=self._tmp.name))
        shutil.copytree(self.generated, bundle, dirs_exist_ok=True)
        return FacilityEditSession.from_input(bundle / "building_01.json", bundle, profile_path="rules/pharma_cleanroom_pilot.yaml")

    def test_edit_recomputes_everything_and_undo_redo_reset_restore_it(self):
        session = self._session()
        before = session.result.placements["production"]
        snapshot = session.apply_payload({"type": "move_room", "room_id": "production", "dx_mm": 6000, "dy_mm": 0})
        after = session.result.placements["production"]
        self.assertEqual((after.x, after.y), (before.x + 6000, before.y))
        self.assertTrue(snapshot["editable"])
        self.assertTrue(snapshot["can_undo"])
        # Equipment and routes follow the room; the gates are recomputed.
        self.assertIn("checks", snapshot["facility"])
        self.assertEqual([entry["type"] for entry in snapshot["journal"]], ["MoveRoom"])

        session.undo()
        self.assertEqual(session.result.placements["production"], before)
        session.redo()
        self.assertEqual(session.result.placements["production"], after)
        session.reset()
        self.assertEqual(session.result.placements["production"], before)

    def test_impossible_edit_is_refused_and_leaves_the_state_unchanged(self):
        session = self._session()
        before = dict(session.result.placements)
        with self.assertRaises(EditError):
            session.apply_payload({"type": "move_room", "room_id": "production", "dx_mm": 500000, "dy_mm": 0})
        self.assertEqual(session.result.placements, before)
        self.assertFalse(session.snapshot()["can_undo"])

    def test_door_and_window_edits_belong_in_the_program(self):
        session = self._session()
        with self.assertRaises(EditError):
            session.apply_payload({"type": "remove_external_entry"})

    def test_saved_revision_is_a_complete_bundle_and_the_original_is_untouched(self):
        session = self._session()
        original = (session.output_dir / "building_01.json").read_bytes()
        session.apply_payload({"type": "move_room", "room_id": "dispatch", "dx_mm": 0, "dy_mm": 1000})
        snapshot = session.save_revision()
        revision = Path(snapshot["saved_revision"])
        self.assertEqual(revision.name, "rev_01")
        self.assertEqual((session.output_dir / "building_01.json").read_bytes(), original)
        report = validate_building_bundle(revision / "building_01.json")
        self.assertTrue(report.ok, report.to_dict())
        payload = json.loads((revision / "building_01.json").read_text(encoding="utf-8"))
        self.assertEqual(payload["generation"]["revision_of"], "building_01.json")
        self.assertEqual(payload["generation"]["edits"][0]["type"], "MoveRoom")
        self.assertEqual(Path(session.save_revision()["saved_revision"]).name, "rev_02")

    def test_http_routes(self):
        session = self._session()
        server = create_ui_server(session)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{server.server_port}"
        try:
            request = Request(f"{base}/api/command", method="POST", headers={"Content-Type": "application/json"},
                              data=json.dumps({"type": "move_room", "room_id": "dispatch", "dx_mm": 0, "dy_mm": 1000}).encode())
            with urlopen(request) as response:
                self.assertTrue(json.load(response)["can_undo"])
            with urlopen(Request(f"{base}/api/save", method="POST")) as response:
                self.assertEqual(json.load(response)["saved_revisions"], ["revisions/rev_01"])
        finally:
            server.shutdown()
            server.server_close()

        review = FacilityReviewSession.from_input(session.input_path, session.output_dir, profile_path="rules/pharma_cleanroom_pilot.yaml")
        server = create_ui_server(review)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{server.server_port}"
        try:
            with self.assertRaises(HTTPError) as raised:
                urlopen(Request(f"{base}/api/save", method="POST"))
            self.assertEqual(raised.exception.code, 405)
        finally:
            server.shutdown()
            server.server_close()


if __name__ == "__main__":
    unittest.main()

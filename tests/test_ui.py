import json
import tempfile
import threading
import unittest
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from layout_configurator.ui import UiSession, create_ui_server


class UiTests(unittest.TestCase):
    def test_local_ui_serves_state_and_applies_typed_command(self):
        with tempfile.TemporaryDirectory() as directory:
            generated = UiSession.from_input(
                "examples/kz_daylight.yaml",
                Path(directory) / "exports",
                solve_time_limit_seconds=10,
            )
            session = UiSession.from_input(
                generated.output_dir / "layout_01.json",
                Path(directory) / "loaded-exports",
                rules_path="rules/kz_sn_3_02_02_2023_partial.yaml",
                require_provenance=True,
            )
            server = create_ui_server(session)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            base_url = f"http://127.0.0.1:{server.server_port}"
            try:
                with urlopen(f"{base_url}/api/state") as response:
                    state = json.load(response)
                self.assertEqual(state["spec"]["project_name"], "Kazakhstan daylight demo")
                self.assertEqual(len(state["rooms"]), 4)
                self.assertTrue(all(file["url"].startswith("/files/") for file in state["files"]))

                with urlopen(f"{base_url}/") as response:
                    self.assertIn(b"Typed", response.read())
                with urlopen(f"{base_url}/app.js") as response:
                    app_js = response.read()
                self.assertIn(b"/api/command", app_js)
                self.assertIn(b"pointerdown", app_js)
                self.assertIn(b"resize_room", app_js)

                command = Request(
                    f"{base_url}/api/command",
                    data=json.dumps(
                        {"type": "move_room", "room_id": "hall", "dx_mm": 0, "dy_mm": 0}
                    ).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urlopen(command) as response:
                    changed = json.load(response)
                self.assertEqual(changed["history"], ["MoveRoom"])
                self.assertEqual(changed["validation"]["ok"], True)
                self.assertIsNotNone(changed["norms"])
                self.assertEqual(changed["norms"]["ruleset"]["jurisdiction"], "KZ")
                self.assertTrue(changed["norms"]["ok"])
                self.assertEqual(Path(directory, "loaded-exports", "layout_01.json").is_file(), True)

                hall = next(room for room in changed["rooms"] if room["id"] == "hall")
                resize_command = Request(
                    f"{base_url}/api/command",
                    data=json.dumps(
                        {
                            "type": "resize_room",
                            "room_id": "hall",
                            "width_mm": hall["rect"]["width"],
                            "height_mm": hall["rect"]["height"],
                            "anchor": "bottom_left",
                        }
                    ).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urlopen(resize_command) as response:
                    resized = json.load(response)
                self.assertEqual(resized["history"], ["MoveRoom", "ResizeRoom"])

                bad_command = Request(
                    f"{base_url}/api/command",
                    data=b'{"type":"not_a_command"}',
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with self.assertRaises(HTTPError) as raised:
                    urlopen(bad_command)
                self.assertEqual(raised.exception.code, 400)
                error_state = json.load(raised.exception)
                self.assertEqual(error_state["state"]["history"], ["MoveRoom", "ResizeRoom"])
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=5)


if __name__ == "__main__":
    unittest.main()

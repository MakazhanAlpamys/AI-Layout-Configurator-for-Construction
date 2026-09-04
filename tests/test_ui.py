import json
import tempfile
import threading
import unittest
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from layout_configurator.building import BuildingIR
from layout_configurator.bcf import write_bcf_package
from layout_configurator.equipment import place_equipment
from layout_configurator.facility import CoordinationIssue, FacilityValidationReport
from layout_configurator.io import write_building_result
from layout_configurator.solver import solve_layouts
from layout_configurator.ui import FacilityReviewSession, UiSession, create_ui_server


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
                self.assertFalse(state["can_undo"])
                self.assertFalse(state["can_redo"])
                self.assertEqual(state["journal"], [])

                with urlopen(f"{base_url}/") as response:
                    self.assertIn(b"Typed", response.read())
                with urlopen(f"{base_url}/app.js") as response:
                    app_js = response.read()
                self.assertIn(b"/api/command", app_js)
                self.assertIn(b"/api/undo", app_js)
                self.assertIn(b"/api/redo", app_js)
                self.assertIn(b"pointerdown", app_js)
                self.assertIn(b"resize_room", app_js)
                self.assertIn(b"grid-pattern", app_js)
                self.assertIn(b"journal", app_js)
                self.assertIn(b"facility-review", app_js)
                self.assertIn(b"selectCoordinationIssue", app_js)
                self.assertIn(b"issue_history", app_js)
                self.assertIn(b"data-issue-filter", app_js)
                with urlopen(f"{base_url}/style.css") as response:
                    self.assertIn(b"issue-marker-resolved", response.read())

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
                self.assertTrue(changed["can_undo"])
                self.assertFalse(changed["can_redo"])
                self.assertEqual(changed["journal"][0]["type"], "MoveRoom")
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
                self.assertTrue(resized["can_undo"])
                self.assertFalse(resized["can_redo"])

                with urlopen(Request(f"{base_url}/api/undo", method="POST")) as response:
                    undone = json.load(response)
                self.assertEqual(undone["history"], ["MoveRoom"])
                self.assertTrue(undone["can_undo"])
                self.assertTrue(undone["can_redo"])
                self.assertEqual(undone["journal"][-1]["action"], "undo")

                with urlopen(Request(f"{base_url}/api/redo", method="POST")) as response:
                    redone = json.load(response)
                self.assertEqual(redone["history"], ["MoveRoom", "ResizeRoom"])
                self.assertTrue(redone["can_undo"])
                self.assertFalse(redone["can_redo"])
                self.assertEqual(redone["journal"][-1]["action"], "redo")

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
                self.assertFalse(error_state["state"]["can_redo"])
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=5)


    def test_building_result_opens_as_read_only_facility_review(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            building = BuildingIR.from_mapping(
                {
                    "project_name": "Facility review test",
                    "boundary": {"width": 10000, "height": 8000},
                    "rooms": [
                        {
                            "id": "production",
                            "type": "clean_production",
                            "target_area": 40,
                            "min_area": 30,
                            "max_area": 60,
                            "min_width": 5000,
                            "min_depth": 5000,
                        }
                    ],
                    "equipment": [
                        {
                            "id": "mixer",
                            "type": "process_mixer",
                            "room_id": "production",
                            "width_mm": 1000,
                            "depth_mm": 800,
                            "clearance_mm": 300,
                        }
                    ],
                    "flows": [],
                }
            )
            result = solve_layouts(building.layout, variants=1, time_limit_seconds=5, seed=42)[0]
            equipment = place_equipment(building, result, time_limit_seconds=5, seed=42)
            input_path = root / "building_01.json"
            write_building_result(input_path, building, result, equipment)
            session = FacilityReviewSession.from_input(input_path, root)
            snapshot = session.snapshot()
            self.assertEqual(snapshot["mode"], "facility-review")
            self.assertTrue(snapshot["read_only"])
            self.assertEqual(len(snapshot["equipment"]), 1)
            self.assertEqual(snapshot["equipment"][0]["equipment_id"], "mixer")
            self.assertFalse(snapshot["can_undo"])

            server = create_ui_server(session)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            base_url = f"http://127.0.0.1:{server.server_port}"
            try:
                with urlopen(f"{base_url}/api/state") as response:
                    served = json.load(response)
                self.assertEqual(served["mode"], "facility-review")
                self.assertEqual(served["issue_history"], [])
                readonly_command = Request(
                    f"{base_url}/api/command",
                    data=b'{"type":"move_room","room_id":"production","dx_mm":100,"dy_mm":0}',
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with self.assertRaises(HTTPError) as raised:
                    urlopen(readonly_command)
                self.assertEqual(raised.exception.code, 405)
                error_state = json.load(raised.exception)
                self.assertEqual(error_state["state"]["mode"], "facility-review")
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=5)

    def test_facility_review_exposes_bcf_statuses_and_resolved_history(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            building = BuildingIR.from_mapping(
                {
                    "project_name": "BCF history review",
                    "boundary": {"width": 10000, "height": 8000},
                    "rooms": [
                        {
                            "id": "source",
                            "type": "room",
                            "target_area": 40,
                            "min_area": 30,
                            "max_area": 60,
                            "min_width": 5000,
                            "min_depth": 5000,
                        }
                    ],
                    "equipment": [],
                    "flows": [],
                }
            )
            result = solve_layouts(building.layout, variants=1, time_limit_seconds=5, seed=42)[0]
            equipment = place_equipment(building, result, time_limit_seconds=5, seed=42)
            input_path = root / "building_01.json"
            write_building_result(input_path, building, result, equipment)
            first_session = FacilityReviewSession.from_input(input_path, root)
            historical = CoordinationIssue(
                issue_id="FLC-OLD-ROUTE",
                code="FLOW_TYPE_SEPARATION",
                title="Resolved route conflict",
                message="The route was separated in a later revision",
                severity="WARNING",
                source="TEST_HISTORY",
                status="OPEN",
                location=(2500, 3000),
            )
            previous_report = FacilityValidationReport(
                profile=first_session.profile,
                checks=first_session.facility_report.checks,
                issues=(historical,),
            )
            previous_path = root / "previous.bcf"
            write_bcf_package(previous_path, previous_report, project_name="BCF history review")
            write_bcf_package(
                root / "building_01.bcf",
                first_session.facility_report,
                project_name="BCF history review",
                variant=1,
                previous=previous_path,
            )

            session = FacilityReviewSession.from_input(input_path, root)
            snapshot = session.snapshot()
            self.assertEqual(snapshot["bcf"]["version"], "2.1")
            self.assertEqual(snapshot["bcf"]["resolved_topics"], 1)
            records = {item["issue_id"]: item for item in snapshot["issue_history"]}
            self.assertEqual(records["FLC-OLD-ROUTE"]["status"], "RESOLVED")
            self.assertEqual(records["FLC-OLD-ROUTE"]["record_type"], "bcf")
            self.assertEqual(records["FLC-OLD-ROUTE"]["location"], {"x": 2500.0, "y": 3000.0})


if __name__ == "__main__":
    unittest.main()

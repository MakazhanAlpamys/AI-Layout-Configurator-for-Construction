import json
import tempfile
import threading
import unittest
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from jsonschema import Draft202012Validator

from layout_configurator.building import BuildingIR
from layout_configurator.bcf import write_bcf_package
from layout_configurator.equipment import EquipmentLayoutResult, EquipmentPlacement, place_equipment
from layout_configurator.facility import CoordinationIssue, FacilityValidationReport
from layout_configurator.io import write_building_result
from layout_configurator.models import Rect
from layout_configurator.solver import solve_layouts
from layout_configurator.ui import FacilityReviewSession, UiSession, create_ui_server, resolve_ui_input


class UiTests(unittest.TestCase):
    def test_generated_bundle_directory_resolves_selected_facility_variant(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "building_01.json").write_text("{}", encoding="utf-8")
            (root / "building_02.json").write_text("{}", encoding="utf-8")
            (root / "building_01.issue-management.json").write_text("{}", encoding="utf-8")

            self.assertEqual(resolve_ui_input(root, variant=2), root / "building_02.json")
            self.assertEqual(resolve_ui_input(root / "building_01.json"), root / "building_01.json")
            with self.assertRaises(ValueError):
                resolve_ui_input(root, variant=3)

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
                self.assertIn(b"flowLabelAnchor", app_js)
                self.assertIn(b"selectCoordinationIssue", app_js)
                self.assertIn(b"issue_history", app_js)
                self.assertIn(b"data-issue-filter", app_js)
                self.assertIn(b"/api/issue-action", app_js)
                self.assertIn(b"submitIssueAction", app_js)
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

    def test_facility_review_manages_issue_lifecycle_and_exports_projections(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            building = BuildingIR.from_mapping(
                {
                    "project_name": "Issue lifecycle review",
                    "boundary": {"width": 8000, "height": 6000},
                    "rooms": [
                        {
                            "id": "production",
                            "type": "production",
                            "target_area": 48,
                            "min_area": 30,
                            "max_area": 60,
                            "min_width": 5000,
                            "min_depth": 5000,
                        }
                    ],
                    "equipment": [
                        {
                            "id": "machine",
                            "type": "process_machine",
                            "room_id": "production",
                            "width_mm": 1000,
                            "depth_mm": 1000,
                            "clearance_mm": 300,
                        }
                    ],
                    "flows": [],
                }
            )
            result = solve_layouts(building.layout, variants=1, time_limit_seconds=5, seed=42)[0]
            equipment = EquipmentLayoutResult(
                (
                    EquipmentPlacement(
                        equipment_id="machine",
                        room_id="production",
                        rect=Rect(7600, 5000, 1000, 1000),
                        rotated=False,
                    ),
                )
            )
            input_path = root / "building_01.json"
            write_building_result(input_path, building, result, equipment)
            initial = FacilityReviewSession.from_input(input_path, root)
            write_bcf_package(root / "building_01.bcf", initial.facility_report, project_name="Issue lifecycle review", variant=1)
            session = FacilityReviewSession.from_input(input_path, root)
            initial_issue_count = len(session.facility_report.issues)
            issue_id = session.snapshot()["facility"]["issues"][0]["issue_id"]

            server = create_ui_server(session)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            base_url = f"http://127.0.0.1:{server.server_port}"

            def action(action_name, **extra):
                payload = {"action": action_name, "issue_id": issue_id, "author": "qa-reviewer", **extra}
                request = Request(
                    f"{base_url}/api/issue-action",
                    data=json.dumps(payload).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urlopen(request) as response:
                    return json.load(response)

            try:
                resolved = action("resolve", comment="Clearance fix verified in revision R02")
                managed = next(item for item in resolved["facility"]["issues"] if item["issue_id"] == issue_id)
                self.assertEqual(managed["status"], "RESOLVED")
                self.assertEqual(resolved["bcf"]["open_topics"], initial_issue_count - 1)
                self.assertEqual(resolved["bcf"]["resolved_topics"], 1)
                self.assertEqual(len(managed["comments"]), 1)
                self.assertEqual(len(managed["management_history"]), 1)
                self.assertTrue((root / "building_01.issue-management.json").is_file())
                self.assertEqual(json.loads((root / "building_01.coordination.json").read_text(encoding="utf-8"))["open_count"], initial_issue_count - 1)

                commented = action("comment", comment="Owner confirmed the measurement").copy()
                managed = next(item for item in commented["facility"]["issues"] if item["issue_id"] == issue_id)
                self.assertEqual(len(managed["comments"]), 2)
                self.assertEqual(len(managed["management_history"]), 2)

                assigned = action("assign", assignee="Facilities QA", comment="Handed to facilities coordinator")
                managed = next(item for item in assigned["facility"]["issues"] if item["issue_id"] == issue_id)
                self.assertEqual(managed["assignee"], "Facilities QA")
                self.assertEqual(len(managed["management_history"]), 3)

                reopened = action("reopen", comment="Reopened after a new clash was found")
                managed = next(item for item in reopened["facility"]["issues"] if item["issue_id"] == issue_id)
                self.assertEqual(managed["status"], "OPEN")
                self.assertEqual(reopened["bcf"]["open_topics"], initial_issue_count)
                self.assertEqual(reopened["bcf"]["resolved_topics"], 0)
                self.assertEqual(len(managed["management_history"]), 4)
                management_schema = json.loads(Path("schemas/issue_management.schema.json").read_text(encoding="utf-8"))
                management_payload = json.loads((root / "building_01.issue-management.json").read_text(encoding="utf-8"))
                self.assertEqual(list(Draft202012Validator(management_schema).iter_errors(management_payload)), [])

                readonly_command = Request(
                    f"{base_url}/api/command",
                    data=b'{"type":"move_room","room_id":"production","dx_mm":100,"dy_mm":0}',
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with self.assertRaises(HTTPError) as raised:
                    urlopen(readonly_command)
                self.assertEqual(raised.exception.code, 405)
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=5)


class IssueStatusConsistencyTests(unittest.TestCase):
    """Closing and reopening a finding must move the plan, the list and the count together.

    The badge used to count every issue regardless of status, and a finding
    resolved in-session vanished from the plan instead of becoming a resolved
    viewpoint, so the three surfaces disagreed with each other.
    """

    def _session(self, root):
        building = BuildingIR.from_mapping(
            {
                "project_name": "Issue status review",
                "boundary": {"width": 8000, "height": 6000},
                "rooms": [
                    {
                        "id": "production",
                        "type": "production",
                        "target_area": 48,
                        "min_area": 30,
                        "max_area": 60,
                        "min_width": 5000,
                        "min_depth": 5000,
                    }
                ],
                "equipment": [
                    {
                        "id": "machine",
                        "type": "process_machine",
                        "room_id": "production",
                        "width_mm": 1000,
                        "depth_mm": 1000,
                        "clearance_mm": 300,
                    }
                ],
                "flows": [],
            }
        )
        result = solve_layouts(building.layout, variants=1, time_limit_seconds=5, seed=42)[0]
        equipment = EquipmentLayoutResult(
            (
                EquipmentPlacement(
                    equipment_id="machine",
                    room_id="production",
                    rect=Rect(7600, 5000, 1000, 1000),
                    rotated=False,
                ),
            )
        )
        source = root / "building_01.json"
        write_building_result(source, building, result, equipment)
        initial = FacilityReviewSession.from_input(source, root)
        write_bcf_package(root / "building_01.bcf", initial.facility_report, project_name="Issue status review", variant=1)
        return FacilityReviewSession.from_input(source, root)

    def test_counts_follow_resolve_and_reopen(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            session = self._session(root)
            total = len(session.facility_report.issues)
            self.assertGreater(total, 0)
            issue_id = session.snapshot()["facility"]["issues"][0]["issue_id"]

            counts = session.snapshot()["issue_counts"]
            self.assertEqual(counts, {"open": total, "resolved": 0, "total": total})

            session.apply_issue_payload(
                {"action": "resolve", "issue_id": issue_id, "author": "qa", "comment": "checked"}
            )
            resolved = session.snapshot()
            self.assertEqual(
                resolved["issue_counts"], {"open": total - 1, "resolved": 1, "total": total}
            )
            # The deterministic report is the authority and still reports the
            # geometry problem; only the workflow status moved.
            self.assertFalse(resolved["validation"]["ok"])
            statuses = {item["issue_id"]: item["status"] for item in resolved["facility"]["issues"]}
            self.assertEqual(statuses[issue_id], "RESOLVED")

            session.apply_issue_payload(
                {"action": "reopen", "issue_id": issue_id, "author": "qa", "comment": "still there"}
            )
            reopened = session.snapshot()
            self.assertEqual(
                reopened["issue_counts"], {"open": total, "resolved": 0, "total": total}
            )

    def test_client_draws_resolved_current_issues_and_counts_open_only(self):
        app_js = (
            Path(__file__).resolve().parents[1] / "src" / "layout_configurator" / "static" / "app.js"
        ).read_text(encoding="utf-8")
        # The badge must not report a resolved finding as open.
        self.assertNotIn("data.validation.issues.length} issue(s)", app_js)
        self.assertIn("issue_counts", app_js)
        # A current issue that was resolved becomes a resolved viewpoint rather
        # than disappearing from the plan.
        self.assertNotIn('issue.record_type !== "bcf"', app_js)
        self.assertIn("resolvedIssues", app_js)


class PlanProjectionUnitTests(unittest.TestCase):
    """Guard the SVG projection against mixing screen pixels and millimetres.

    ``vector-effect: non-scaling-stroke`` makes ``stroke-width`` a screen-pixel
    value, so a millimetre-looking width covers the drawing. Text sizes and
    marker radii live in user units instead, so a pixel-looking value becomes
    invisible. Both mistakes were shipped at once and made the plan unreadable.
    """

    MAX_NON_SCALING_STROKE_PX = 6.0
    SCALED_TEXT_CLASSES = (
        ".room-label",
        ".room-area",
        ".flow-label",
        ".structural-label",
        ".equipment-label",
        ".preview-dimension",
    )

    @staticmethod
    def _blocks(css: str) -> list[tuple[str, str]]:
        blocks = []
        for rule in css.split("}"):
            if "{" not in rule:
                continue
            selector, _, body = rule.partition("{")
            blocks.append((selector.strip(), body.strip()))
        return blocks

    @staticmethod
    def _declaration(body: str, name: str) -> str | None:
        for declaration in body.split(";"):
            key, _, value = declaration.partition(":")
            if key.strip() == name:
                return value.strip()
        return None

    def setUp(self) -> None:
        css_path = Path(__file__).resolve().parents[1] / "src" / "layout_configurator" / "static" / "style.css"
        self.css = css_path.read_text(encoding="utf-8")
        self.blocks = self._blocks(self.css)

    def test_non_scaling_strokes_stay_within_screen_pixel_range(self):
        non_scaling = {
            selector
            for selector, body in self.blocks
            if self._declaration(body, "vector-effect") == "non-scaling-stroke"
        }
        self.assertTrue(non_scaling, "no non-scaling-stroke rule was found")
        oversized = []
        for selector, body in self.blocks:
            base = selector.split(":")[0].split(".selected")[0]
            if not any(base.startswith(root) or selector.startswith(root) for root in non_scaling):
                continue
            width = self._declaration(body, "stroke-width")
            if width is None or "var(" in width or "calc(" in width:
                continue
            value = float(width.replace("px", "").strip())
            if value > self.MAX_NON_SCALING_STROKE_PX:
                oversized.append((selector, value))
        self.assertEqual(oversized, [], f"non-scaling strokes wider than {self.MAX_NON_SCALING_STROKE_PX} px")

    def test_plan_text_scales_with_the_rendered_viewbox(self):
        for name in self.SCALED_TEXT_CLASSES:
            body = next((body for selector, body in self.blocks if selector == name), None)
            self.assertIsNotNone(body, f"{name} rule is missing")
            font_size = self._declaration(body, "font-size")
            self.assertIsNotNone(font_size, f"{name} has no font-size")
            self.assertIn(
                "var(--u",
                font_size,
                f"{name} font-size must scale with the projection unit, got {font_size}",
            )

    def test_projection_unit_is_published_by_the_client(self):
        app_js = (
            Path(__file__).resolve().parents[1] / "src" / "layout_configurator" / "static" / "app.js"
        ).read_text(encoding="utf-8")
        self.assertIn("--u", app_js)
        self.assertIn("unitsPerPixel", app_js)
        self.assertIn("flow-corridor", app_js)
        self.assertIn("minimum_clear_width_mm", app_js)


if __name__ == "__main__":
    unittest.main()

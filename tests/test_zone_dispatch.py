import contextlib
import io
import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from urllib.request import Request, urlopen

from layout_configurator.api import create_api_server
from layout_configurator.building import BuildingIR
from layout_configurator.cli import main
from layout_configurator.facility import default_facility_profile
from layout_configurator.facility_generation import (
    InfeasibleFacilityGeneration,
    solve_feasible_facility_variants,
)
from layout_configurator.models import Rect
from layout_configurator.zone_hierarchy import ZoneHierarchyNotApplicable


def _building(width=8000, with_process=False):
    rooms = []
    for index, room_id in enumerate(("a", "b", "c", "d")):
        room = {"id": room_id, "target_area": 4, "min_area": 4, "max_area": 4,
                "min_width": 2000, "min_depth": 2000}
        if index < 3:
            room["required_adjacency"] = [("b", "c", "d")[index]]
        rooms.append(room)
    raw = {
        "layout": {"boundary": {"width": width, "height": 2000}, "entry_room": "a", "rooms": rooms},
        "zones": [{"id": "left", "room_ids": ["a", "b"], "required_adjacency": ["right"]},
                  {"id": "right", "room_ids": ["c", "d"]}],
    }
    if with_process:
        raw["equipment"] = [{"id": "machine", "room_id": "a", "width_mm": 400,
                             "depth_mm": 400, "clearance_mm": 100}]
        raw["flows"] = [{"id": "material", "type": "material", "from_ids": ["a"],
                         "to_ids": ["d"], "minimum_clear_width_mm": 800, "required": True}]
    return BuildingIR.from_mapping(raw)


class ZoneFacilityDispatchTests(unittest.TestCase):
    def _solve(self, **kwargs):
        return solve_feasible_facility_variants(
            _building(), default_facility_profile(), variants=1, time_limit_seconds=0.5,
            seed=1, max_attempts=1, room_solver="zone_hierarchical", **kwargs,
        )

    def test_explicit_zone_strategy_passes_every_facility_gate_and_keeps_containment_evidence(self):
        generation = solve_feasible_facility_variants(
            _building(with_process=True), default_facility_profile(), variants=1, time_limit_seconds=0.5,
            seed=1, max_attempts=1, room_solver="zone_hierarchical",
        )
        self.assertEqual(len(generation.accepted), 1)
        candidate = generation.accepted[0]
        self.assertEqual(len(candidate.equipment.placements), 1)
        self.assertEqual(len(candidate.flow_routes.routes), 1)
        for report in (candidate.layout_report, candidate.equipment_report,
                       candidate.flow_report, candidate.facility_report):
            self.assertTrue(report.ok, str(report))
        raw = generation.to_dict()
        self.assertEqual(raw["room_solver_used"], "zone_hierarchical")
        evidence = raw["room_solver_evidence"]
        self.assertEqual(evidence["scope"], "root_zone_containers")
        containers = {unit_id: Rect(**value) for unit_id, value in evidence["variants"][0]["zone_rects"].items()}
        for unit_id, container in containers.items():
            for room_id in _building().room_ids_for_zone(unit_id):
                room = candidate.result.placements[room_id]
                self.assertGreaterEqual(room.x, container.x)
                self.assertLessEqual(room.right, container.right)
        self.assertEqual(containers["left"].intersection_area(containers["right"]), 0)

    def test_explicit_fixed_and_axis_constraints_reach_actual_zone_solver(self):
        options = (
            {"fixed_rects": {"a": Rect(6000, 0, 2000, 2000)}},
            {"axis_aligned_room_ids": ["a"], "structural_axes_x_mm": [6000, 8000]},
            {"axis_aligned_room_ids": []},
            {"structural_axes_x_mm": [0, 2000, 4000]},
            {"structural_axes_y_mm": [0, 2000]},
        )
        for kwargs in options:
            with self.subTest(kwargs=kwargs):
                generation = self._solve(**kwargs)
                candidate = generation.accepted[0]
                self.assertTrue(candidate.layout_report.ok)
                self.assertTrue(candidate.facility_report.ok)
                self.assertEqual(generation.room_solver_used, "zone_hierarchical")
                self.assertIsNone(generation.hierarchy_fallback_reason)
                if "fixed_rects" in kwargs or kwargs.get("axis_aligned_room_ids"):
                    self.assertEqual(candidate.result.placements["a"], Rect(6000, 0, 2000, 2000))

    def test_invalid_constraints_keep_structured_input_failure_without_fallback(self):
        with self.assertRaises(InfeasibleFacilityGeneration) as caught:
            self._solve(axis_aligned_room_ids=["missing"])
        self.assertIsInstance(caught.exception.__cause__, ZoneHierarchyNotApplicable)
        evidence = caught.exception.generation.to_dict()["room_solver_evidence"]
        self.assertEqual(evidence["stage_attempts"], [])
        self.assertEqual(evidence["failure"]["stage"], "input")
        self.assertEqual(evidence["failure"]["code"], "unknown_axis_room")
        self.assertEqual(evidence["failure"]["model_status"], "NOT_BUILT")
        self.assertIsNone(caught.exception.generation.hierarchy_fallback_reason)

    def test_default_strategy_keeps_existing_manifest_shape(self):
        generation = solve_feasible_facility_variants(
            _building(), default_facility_profile(), variants=1, time_limit_seconds=0.5,
            seed=1, max_attempts=1,
        )
        self.assertEqual(generation.room_solver, "auto")
        self.assertNotIn("room_solver_evidence", generation.to_dict())

    def test_cli_explicit_strategy_writes_structured_failure_instead_of_rejecting_the_choice(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            program = root / "program.json"
            program.write_text(json.dumps(_building(width=2000).to_dict()), encoding="utf-8")
            output = root / "out"
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                code = main(["generate-building", str(program), "--output", str(output),
                             "--room-solver", "zone_hierarchical", "--variants", "1",
                             "--max-attempts", "1", "--time-limit", "0.5"])
            self.assertEqual(code, 2)
            raw = json.loads((output / "generation-failure.json").read_text(encoding="utf-8"))
            failure = raw["generation"]["room_solver_evidence"]["failure"]
            self.assertEqual(failure["stage"], "local")
            self.assertFalse(failure["proven_building_infeasible"])
            self.assertFalse((output / "manifest.json").exists())

    def test_http_explicit_strategy_retains_stage_evidence_on_failed_job(self):
        with tempfile.TemporaryDirectory() as temporary:
            server = create_api_server(temporary, ("127.0.0.1", 0), token="fixture")
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                base = f"http://127.0.0.1:{server.server_port}"
                request = Request(base + "/jobs", data=json.dumps({
                    "program": _building(width=2000).to_dict(), "room_solver": "zone_hierarchical",
                    "variants": 1, "max_attempts": 1, "time_limit_seconds": 0.5,
                }).encode(), headers={"Authorization": "Bearer fixture", "Content-Type": "application/json"})
                with urlopen(request, timeout=5) as response:
                    self.assertEqual(response.status, 202)
                    job_id = json.loads(response.read())["id"]
                deadline = time.monotonic() + 5
                while time.monotonic() < deadline:
                    with urlopen(Request(base + f"/jobs/{job_id}", headers={"Authorization": "Bearer fixture"}), timeout=5) as response:
                        job = json.loads(response.read())
                    if job["status"] in ("SUCCEEDED", "FAILED"):
                        break
                    time.sleep(0.02)
                self.assertEqual(job["status"], "FAILED", job)
                failure = job["error"]["generation"]["room_solver_evidence"]["failure"]
                self.assertEqual(failure["stage"], "local")
                self.assertFalse(failure["proven_building_infeasible"])
                disk = json.loads((Path(temporary) / job_id / "generation-failure.json").read_text(encoding="utf-8"))
                self.assertEqual(disk["generation"]["room_solver_evidence"]["failure"], failure)
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=5)

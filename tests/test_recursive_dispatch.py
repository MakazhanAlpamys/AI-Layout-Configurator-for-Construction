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
from layout_configurator.facility_generation import InfeasibleFacilityGeneration, solve_feasible_facility_variants
from layout_configurator.models import Rect
from layout_configurator.zone_hierarchy import ZoneHierarchyNotApplicable, validate_zone_layout


def _program(width=6000):
    return BuildingIR.from_mapping({
        "layout": {
            "boundary": {"width": width, "height": 2000}, "entry_room": "a",
            "rooms": [{"id": room_id, "target_area": 4, "min_area": 4, "max_area": 4,
                       "min_width": 2000, "min_depth": 2000,
                       "required_adjacency": [other] if other else []}
                      for room_id, other in (("a", "b"), ("b", "c"), ("c", None))],
        },
        "zones": [{"id": "parent", "room_ids": ["a"]},
                  {"id": "child", "parent_zone_id": "parent", "room_ids": ["b"]},
                  {"id": "grandchild", "parent_zone_id": "child", "room_ids": ["c"]}],
        "equipment": [{"id": "machine", "room_id": "a", "width_mm": 400,
                       "depth_mm": 400, "clearance_mm": 100}],
        "flows": [{"id": "material", "type": "material", "from_ids": ["a"], "to_ids": ["c"],
                   "minimum_clear_width_mm": 800, "required": True}],
    })


class RecursiveDispatchTests(unittest.TestCase):
    def _solve(self, **kwargs):
        return solve_feasible_facility_variants(
            _program(), default_facility_profile(), variants=1, time_limit_seconds=0.5,
            seed=1, max_attempts=1, room_solver="zone_recursive", **kwargs,
        )

    def test_explicit_recursive_strategy_packs_equipment_routes_flow_and_preserves_three_containers(self):
        generation = self._solve()
        self.assertEqual(len(generation.accepted), 1)
        candidate = generation.accepted[0]
        for report in (candidate.layout_report, candidate.equipment_report,
                       candidate.flow_report, candidate.facility_report):
            self.assertTrue(report.ok, str(report))
        self.assertEqual(len(candidate.equipment.placements), 1)
        self.assertEqual(len(candidate.flow_routes.routes), 1)
        evidence = generation.to_dict()["room_solver_evidence"]
        self.assertEqual(generation.room_solver_used, "zone_recursive")
        self.assertEqual(evidence["scope"], "recursive_zone_containers")
        containers = {key: Rect(**raw) for key, raw in evidence["variants"][0]["zone_rects"].items()}
        self.assertEqual(set(containers), {"parent", "child", "grandchild"})
        self.assertTrue(validate_zone_layout(_program(), candidate.result, containers, recursive_zones=True).ok)

    def test_recursive_strategy_applies_explicit_fixed_and_axes_before_facility_gates(self):
        for kwargs in ({"fixed_rects": {"a": Rect(4000, 0, 2000, 2000)}},
                       {"axis_aligned_room_ids": ["a"], "structural_axes_x_mm": [4000, 6000]},
                       {"structural_axes_x_mm": [0, 2000]},
                       {"structural_axes_y_mm": [0, 2000]}):
            with self.subTest(kwargs=kwargs):
                generation = self._solve(**kwargs)
                candidate = generation.accepted[0]
                for report in (candidate.layout_report, candidate.equipment_report,
                               candidate.flow_report, candidate.facility_report):
                    self.assertTrue(report.ok, str(report))
                self.assertEqual(len(candidate.equipment.placements), 1)
                self.assertEqual(len(candidate.flow_routes.routes), 1)
                self.assertEqual(generation.room_solver_used, "zone_recursive")
                self.assertIsNone(generation.hierarchy_fallback_reason)
                if "fixed_rects" in kwargs or "axis_aligned_room_ids" in kwargs:
                    self.assertEqual(candidate.result.placements["a"], Rect(4000, 0, 2000, 2000))

    def test_recursive_invalid_axes_preserve_input_evidence_without_fallback(self):
        with self.assertRaises(InfeasibleFacilityGeneration) as caught:
            self._solve(axis_aligned_room_ids=["a"], structural_axes_x_mm=[])
        self.assertIsInstance(caught.exception.__cause__, ZoneHierarchyNotApplicable)
        raw = caught.exception.generation.to_dict()
        self.assertEqual(raw["room_solver_used"], "zone_recursive")
        self.assertEqual(raw["room_solver_evidence"]["failure"]["code"], "invalid_axes")
        self.assertEqual(raw["room_solver_evidence"]["failure"]["model_status"], "NOT_BUILT")
        self.assertEqual(raw["room_solver_evidence"]["stage_attempts"], [])
        self.assertIsNone(raw["hierarchy_fallback_reason"])

    def test_cli_publishes_an_accepted_recursive_bundle_with_stage_evidence(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            program = root / "program.json"
            program.write_text(json.dumps(_program().to_dict()), encoding="utf-8")
            output = root / "out"
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                code = main(["generate-building", str(program), "--output", str(output),
                             "--room-solver", "zone_recursive", "--max-attempts", "1",
                             "--time-limit", "0.5", "--seed", "1"])
            self.assertEqual(code, 0)
            manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
            evidence = manifest["generation"]["room_solver_evidence"]
            self.assertEqual(manifest["generation"]["room_solver_used"], "zone_recursive")
            self.assertEqual(set(evidence["variants"][0]["zone_rects"]), {"parent", "child", "grandchild"})
            self.assertTrue((output / "building_01.ifc").exists())
            self.assertTrue((output / "building_01.json").exists())

    def test_http_accepts_recursive_strategy_and_persists_structured_failure_scope(self):
        with tempfile.TemporaryDirectory() as temporary:
            server = create_api_server(temporary, ("127.0.0.1", 0), token="fixture")
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                base = f"http://127.0.0.1:{server.server_port}"
                request = Request(base + "/jobs", data=json.dumps({
                    "program": _program(width=2000).to_dict(), "room_solver": "zone_recursive",
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
                evidence = job["error"]["generation"]["room_solver_evidence"]
                self.assertEqual(evidence["room_solver_used"], "zone_recursive")
                self.assertFalse(evidence["failure"]["proven_building_infeasible"])
                disk = json.loads((Path(temporary) / job_id / "generation-failure.json").read_text(encoding="utf-8"))
                self.assertEqual(disk["generation"]["room_solver_evidence"], evidence)
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=5)

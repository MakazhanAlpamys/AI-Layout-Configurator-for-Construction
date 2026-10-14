"""One explicit process revision retains supplied installation-path evidence.

No fixture searches for a layout or invents a transport route. Original raw
component identities and standalone canonical model signatures are separate.
"""

import copy
from dataclasses import replace
import hashlib
import importlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from layout_configurator.equipment_installation_path import validate_equipment_installation_path
from layout_configurator.process_review import ProcessReviewLimits, load_process_review
from test_engineering_library import source_content
from test_equipment_installation_path import path_fixture
from test_process_review import _COMPONENTS, process_fixture, rebind
from test_transfer_program import _sha


def installation_process_fixture(*, neighbor_y=4500.0):
    """Combine a supplied process declaration and already supplied path frames."""
    raw = process_fixture()
    building, layout, equipment, library, mapping, request, actions = path_fixture(neighbor_y=neighbor_y)
    raw.update(version="0.2", id="installation_process_review", building=building.to_dict(),
        layout=layout.to_dict(), equipment=equipment.to_dict(building)["equipment"],
        flow_routes={"routes": []}, environment_program=None, library=library,
        scenario_requests={"card_by_equipment": mapping, "maintenance_targets": ["press"],
            "installation_targets": ["press"], "actions_by_scenario": {"production": {},
                "maintenance": {"press": ["stop"]}, "installation_replacement": actions["installation_replacement"]}},
        installation_paths=[request])
    production = raw["production_program"]
    production["operations"][0].update(room_id="work", equipment_ids=["press"], flow_ids=[])
    production["requirements"] = []
    content = ("Process installation integration fixture; supplied reconstruction is not engineering approval.\r\n"
               "Handover is assigned to the work room and the press. No process flows are supplied.\r\n"
               "Declared cycle 30 s, throughput 120 items/hour, movements 12/hour and one operator.\r\n")
    production["sources"][0].update(content=content, sha256=hashlib.sha256(content.encode("utf-8")).hexdigest())
    raw["transfer_program"].update(flow_movements=[], transfers=[])
    rebind(raw)
    return raw, building, layout, equipment


def add_declared_pressure_cycle(raw):
    """A literal two-node cycle on the actual declared internal door."""
    environment = copy.deepcopy(process_fixture()["environment_program"])
    source = raw["production_program"]["sources"][0]
    content = source["content"] + (
        "Environment reconstruction fixture: entry 30 Pa and work 20 Pa on one common reference.\r\n"
        "Both declared directions require at least 10 Pa, forming an inconsistent supplied pressure cycle.\r\n"
        "All explicit doors permit personnel in both directions.\r\n"
        "Production uses handover; maintenance and replacement require handover stopped.\r\n")
    source.update(content=content, sha256=hashlib.sha256(content.encode("utf-8")).hexdigest())
    evidence = {"source_id": "transfer_brief", "basis": "declared_fact", "excerpt": "Environment reconstruction fixture"}
    raw["transfer_program"]["transfers"] = [{"id": "internal_transfer", "kind": "door", "opening_id": "internal",
        "room_chain": ["entry", "work"], "minimum_clear_width_mm": 800,
        "permissions": [{"direction": direction, "movement_kinds": ["personnel"]} for direction in ("forward", "reverse")],
        **evidence}]
    environment["clean_zones"][0]["host_room_ids"] = ["entry"]
    ventilation = environment["ventilation_zones"][0]
    ventilation.update(host_room_ids=["work"], boundary={"kind": "orthogonal", "units": "mm",
        "vertices": [[6000.0, 0.0], [12000.0, 0.0], [12000.0, 6000.0], [6000.0, 6000.0]]})
    environment["pressure_nodes"] = environment["pressure_nodes"][:2]
    for node, room in zip(environment["pressure_nodes"], ("entry", "work")):
        node.update(id="pressure_" + room, space={"namespace": "room", "id": room})
    environment["pressure_edges"] = [{"id": edge_id, "scenario_id": "normal",
        "high_node_id": "pressure_" + high, "low_node_id": "pressure_" + low,
        "transfer_id": "internal_transfer", "minimum_delta_pa": 10, "maximum_delta_pa": None,
        **evidence} for edge_id, high, low in (("entry_work", "entry", "work"), ("work_entry", "work", "entry"))]
    raw["environment_program"] = environment
    rebind(raw)


class ProcessInstallationReviewTests(unittest.TestCase):
    def setUp(self):
        try:
            self.api = importlib.import_module("layout_configurator.process_installation_review")
        except ModuleNotFoundError as exc:
            self.fail(f"The explicit process revision with installation paths is missing: {exc}")
        self.assertTrue(callable(getattr(self.api, "load_process_review_with_paths", None)))
        self.assertTrue(callable(getattr(self.api, "ProcessInstallationPathLimits", None)))
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.path = self.root / "original-process-with-paths.json"
        self.raw, self.building, self.layout, self.equipment = installation_process_fixture()

    def write(self, raw):
        data = (json.dumps(raw, ensure_ascii=False, allow_nan=False, indent=2) + "\r\n").encode("utf-8")
        self.path.write_bytes(data)
        return data

    def load(self, raw=None, **options):
        self.write(self.raw if raw is None else raw)
        result = self.api.load_process_review_with_paths(self.path, **options)
        self.assertIs(type(result), dict)
        return result

    def leaf(self, report, request_id="install_press"):
        return next(row for row in report["installation_paths"]["reports"] if row["request_id"] == request_id)

    def assert_rejected(self, raw):
        try:
            report = self.load(raw)
        except ValueError:
            return
        self.assertEqual(report["status"], "FAIL", "An invalid declaration must not become a scoped UNKNOWN or PASS")

    def assert_scoped(self, report):
        self.assertFalse(report["ok"])
        self.assertEqual(report["document_status"], "DRAFT")
        self.assertEqual(report["full_facility_validation"], {"status": "UNKNOWN", "ok": False})
        self.assertEqual(report["engineering_acceptance"], "PENDING")
        self.assertEqual(report["authenticated_approval"], "UNKNOWN")
        self.assertFalse(report["proven_building_infeasible"])

    def test_clear_fresh_leaf_keeps_common_identity_original_bytes_and_detached_requests_without_solver_or_rewrite(self):
        original = self.write(self.raw)
        before = copy.deepcopy(self.raw)
        standalone = validate_equipment_installation_path(self.raw["installation_paths"][0], building=self.building,
            layout=self.layout, equipment=self.equipment, library=self.raw["library"],
            card_by_equipment=self.raw["scenario_requests"]["card_by_equipment"],
            actions_by_scenario=self.raw["scenario_requests"]["actions_by_scenario"]).to_dict()
        with patch("ortools.sat.python.cp_model.CpSolver.solve", side_effect=AssertionError("No solver in source review")), \
                patch.object(Path, "write_bytes", side_effect=AssertionError("No stripped intermediate source")), \
                patch.object(Path, "write_text", side_effect=AssertionError("No stripped intermediate source")):
            report = self.api.load_process_review_with_paths(self.path)
        identity = {"drawing_revision": "D01", "variant": 1,
            "component_sha256": {name: _sha(self.raw[name]) for name in _COMPONENTS}}
        self.assertEqual(report["identity"], identity)
        self.assertEqual(report["source_frames"], {name: self.raw[name] for name in _COMPONENTS})
        self.assertEqual(report["review_source"]["file_sha256"], hashlib.sha256(original).hexdigest())
        self.assertEqual(report["review_source"]["bytes"], len(original))
        section = report["installation_paths"]
        self.assertEqual(section["identity"], identity)
        self.assertEqual(section["source_requests"], self.raw["installation_paths"])
        self.assertEqual(section["request_set_sha256"], _sha(self.raw["installation_paths"]))
        self.assertEqual((section["requested_targets"], section["covered_targets"], section["missing_targets"]), (["press"], ["press"], []))
        wrapper = self.leaf(report)
        self.assertEqual(wrapper["identity"], identity)
        self.assertEqual(wrapper["request_sha256"], _sha(self.raw["installation_paths"][0]))
        evidence = wrapper["evidence"]
        self.assertEqual(evidence["local_2d_path_status"], "PASS")
        self.assertEqual(evidence["source"], standalone["source"])
        self.assertEqual(evidence["opening_crossings"], standalone["opening_crossings"])
        self.assertEqual(evidence["segments"], standalone["segments"])
        self.assertEqual((wrapper["status"], evidence["status"], section["status"], report["status"]), ("UNKNOWN", "UNKNOWN", "UNKNOWN", "UNKNOWN"))
        self.assertEqual(evidence["three_dimensional_feasibility"], "UNKNOWN")
        self.assertEqual(evidence["full_scenario_validation"], "UNKNOWN")
        self.assert_scoped(report)
        self.assertEqual(self.path.read_bytes(), original)
        self.assertEqual(self.raw, before)
        section["source_requests"][0]["id"] = "changed_detached_copy"
        self.assertEqual(self.raw, before)
        self.assertEqual(self.load()["installation_paths"]["source_requests"], before["installation_paths"])

    def test_raw_optional_fields_and_array_hash_do_not_replace_canonical_leaf_source_signatures(self):
        for placement in self.raw["equipment"]:
            placement.pop("clearance")
        self.raw["layout"].pop("objective_value")
        for room in self.raw["layout"]["rooms"].values():
            room.pop("area_m2")
        original_request = copy.deepcopy(self.raw["installation_paths"][0])
        rebind(self.raw)
        report = self.load()
        self.assertEqual(self.leaf(report)["evidence"]["local_2d_path_status"], "PASS")
        self.assertEqual(report["source_frames"]["equipment"], self.raw["equipment"])
        self.assertEqual(report["source_frames"]["layout"], self.raw["layout"])
        signatures = self.leaf(report)["evidence"]["source"]
        self.assertEqual(signatures, original_request["source"])
        self.assertEqual(signatures["equipment_sha256"], _sha(self.equipment.to_dict(self.building)))
        self.assertEqual(signatures["layout_sha256"], _sha(self.layout.to_dict()))
        self.assertNotEqual(signatures["equipment_sha256"], report["identity"]["component_sha256"]["equipment"])
        self.assertNotEqual(signatures["layout_sha256"], report["identity"]["component_sha256"]["layout"])
        self.assertEqual(report["installation_paths"]["source_requests"], [original_request])

    def test_version_zero_one_remains_unchanged_and_closed_while_version_zero_two_is_explicit(self):
        old = process_fixture()
        old_path = self.root / "historical-process-review.json"
        old_bytes = (json.dumps(old, ensure_ascii=False, indent=2) + "\r\n").encode("utf-8")
        old_path.write_bytes(old_bytes)
        before = load_process_review(old_path)
        self.load()
        after = load_process_review(old_path)
        self.assertEqual(_sha(after), _sha(before))
        self.assertEqual(old_path.read_bytes(), old_bytes)
        self.assertNotIn("installation_paths", before)
        self.assertEqual(set(before["identity"]["component_sha256"]), set(_COMPONENTS))
        self.write(self.raw)
        with self.assertRaises(ValueError):
            load_process_review(self.path)
        with self.assertRaises(ValueError):
            self.api.load_process_review_with_paths(old_path)
        for field in ("saved_reports", "output_directory"):
            changed = copy.deepcopy(self.raw)
            changed[field] = {"status": "PASS"} if field == "saved_reports" else "untrusted-output"
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.load(changed)

    def test_embedded_program_is_preserved_whole_and_equal_python_scalars_cannot_hide_program_mismatch(self):
        self.raw["building"]["production_program"] = copy.deepcopy(self.raw["production_program"])
        rebind(self.raw, production_base=False)
        self.raw["installation_paths"][0]["source"]["building_sha256"] = _sha(self.raw["building"])
        report = self.load()
        self.assertEqual(report["source_frames"]["building"], self.raw["building"])
        self.assertEqual(report["identity"]["component_sha256"]["building"], _sha(self.raw["building"]))
        self.assertEqual(self.leaf(report)["evidence"]["source"]["building_sha256"], _sha(self.raw["building"]))
        self.assertEqual(report["reports"]["transfers"]["evidence"]["geometry_status"], "UNKNOWN")
        changed = copy.deepcopy(self.raw)
        embedded = changed["building"]["production_program"]
        embedded["operations"][0]["measurements"]["cycle_time"]["value"] = 30.0
        self.assertEqual(embedded, changed["production_program"])
        self.assertNotEqual(_sha(embedded), _sha(changed["production_program"]))
        rebind(changed, production_base=False)
        changed["installation_paths"][0]["source"]["building_sha256"] = _sha(changed["building"])
        mismatch = self.load(changed)
        self.assertEqual(mismatch["status"], "FAIL")
        self.assertIn("embedded_production_program_mismatch",
            {row["code"] for row in mismatch["reports"]["transfers"]["evidence"]["issues"]})
        self.assertEqual(mismatch["source_frames"]["building"]["production_program"], embedded)

    def test_stale_sources_wrong_card_target_duplicate_ids_and_cached_leaf_reports_are_not_accepted(self):
        for name in self.raw["installation_paths"][0]["source"]:
            changed = copy.deepcopy(self.raw)
            changed["installation_paths"][0]["source"][name] = "0" * 64
            with self.subTest(stale=name):
                report = self.load(changed)
                self.assertEqual(report["status"], "FAIL")
                self.assertIn("source_binding_mismatch", {row["code"] for row in self.leaf(report)["evidence"]["issues"]})
        for mutation in ("card", "target", "duplicate", "cached_report"):
            changed = copy.deepcopy(self.raw)
            request = changed["installation_paths"][0]
            if mutation == "card":
                request["card_id"] = "card_neighbor"
            elif mutation == "target":
                request.update(equipment_id="neighbor", card_id="card_neighbor",
                    poses=[{"x_mm": x, "y_mm": y, "rotated": False} for x, y in
                        ((-1200.0, 2600.0), (500.0, 2600.0), (2000.0, 2600.0), (2000.0, 4500.0), (3000.0, 4500.0))])
            elif mutation == "duplicate":
                second = copy.deepcopy(request)
                second["poses"][1]["x_mm"] = 600.0
                changed["installation_paths"].append(second)
            else:
                request["saved_report"] = {"status": "PASS", "local_2d_path_status": "PASS"}
            with self.subTest(mutation=mutation):
                self.assert_rejected(changed)

    def test_missing_coverage_and_staging_remain_unknown_but_independent_body_and_pressure_failures_survive(self):
        missing = copy.deepcopy(self.raw)
        missing["scenario_requests"]["installation_targets"].append("neighbor")
        report = self.load(missing)
        self.assertEqual(report["installation_paths"]["missing_targets"], ["neighbor"])
        self.assertEqual(report["installation_paths"]["status"], "UNKNOWN")
        self.assertEqual(self.leaf(report)["evidence"]["local_2d_path_status"], "PASS")
        unknown = copy.deepcopy(self.raw)
        unknown["installation_paths"][0]["exterior_staging"] = None
        report = self.load(unknown)
        self.assertEqual(self.leaf(report)["evidence"]["local_2d_path_status"], "UNKNOWN")
        blocked, _, _, _ = installation_process_fixture(neighbor_y=2700.0)
        blocked["installation_paths"][0]["exterior_staging"] = None
        report = self.load(blocked)
        self.assertEqual(report["status"], "FAIL")
        self.assertIn("swept_body_collision", {row["code"] for row in self.leaf(report)["evidence"]["issues"]})
        pressure = copy.deepcopy(self.raw)
        pressure["installation_paths"][0]["exterior_staging"] = None
        add_declared_pressure_cycle(pressure)
        report = self.load(pressure)
        self.assertEqual(report["status"], "FAIL")
        self.assertEqual(report["reports"]["environment"]["evidence"]["pressure_declared_status"], "FAIL")
        self.assertEqual(self.leaf(report)["evidence"]["local_2d_path_status"], "UNKNOWN")
        self.assert_scoped(report)

    def test_joint_pose_predicate_node_and_source_byte_work_is_bounded_before_any_geometry_or_nested_decode(self):
        defaults = self.api.ProcessInstallationPathLimits()
        variants = []
        base = copy.deepcopy(self.raw)
        second = copy.deepcopy(base["installation_paths"][0])
        second.update(id="install_press_alternative")
        second["poses"][1]["x_mm"] = 600.0
        base["installation_paths"].append(second)
        variants.append((base, replace(defaults, max_total_poses=6), "poses"))
        variants.append((base, replace(defaults, max_geometry_checks=850), "predicates"))
        nested = copy.deepcopy(base)
        for source in nested["library"]["sources"]:
            document = json.loads(source["content"])
            document["unused"] = [0] * 3000
            source_content({"sources": [source]}, document)
        for request in nested["installation_paths"]:
            request["source"]["library_sha256"] = _sha(nested["library"])
            request["exterior_staging"]["source_sha256"] = nested["library"]["sources"][0]["sha256"]
        # Fresh core and leaf checks each decode every nested source three
        # times. This bound admits one complete path, not a unique-source sum.
        variants.append((nested, replace(defaults, max_node_work=60000), "nodes"))
        source_work = sum(len(source["content"].encode("utf-8")) for source in base["library"]["sources"])
        production_work = sum(len(source["content"].encode("utf-8")) for source in base["production_program"]["sources"])
        # Raw occurrence plus actual repeated nested decodes: one path reserves
        # 7*library bytes, two reserve 10*library bytes, plus process sources.
        variants.append((base, replace(defaults, max_source_byte_work=production_work + 8 * source_work), "source_bytes"))
        for raw, limits, kind in variants:
            with self.subTest(kind=kind):
                # Each complete standalone request fits the same selected bound;
                # the combined review may not grant every request a new budget.
                for request in raw["installation_paths"]:
                    single = copy.deepcopy(raw)
                    single["installation_paths"] = [request]
                    accepted = self.load(single, path_limits=limits)
                    self.assertNotEqual(accepted.get("inventory_status"), "NOT_EVALUATED")
                    self.assertEqual(self.leaf(accepted, request["id"])["evidence"]["local_2d_path_status"], "PASS")
                self.write(raw)
                with patch("layout_configurator.transfer_review.parse_layout_result", side_effect=AssertionError("No GEOS before whole review admission")), \
                        patch("layout_configurator.engineering_library._strict_json", side_effect=AssertionError("Charge every source before decoding")), \
                        patch("layout_configurator.equipment_installation_path.Polygon", side_effect=AssertionError("No path GEOS before admission")), \
                        patch("layout_configurator.equipment_installation_path.box", side_effect=AssertionError("No path GEOS before admission")), \
                        patch("layout_configurator.equipment_installation_path.build_orthogonal_wall_plan", side_effect=AssertionError("No walls before admission")):
                    report = self.api.load_process_review_with_paths(self.path, path_limits=limits)
                self.assertEqual(report["status"], "UNKNOWN")
                self.assertEqual(report["inventory_status"], "NOT_EVALUATED")
                self.assertIn("input_budget_exceeded", {row["code"] for row in report["issues"]})

    def test_joint_literal_path_vertices_are_admitted_before_common_geometry_and_source_decoding(self):
        raw = copy.deepcopy(self.raw)
        source = raw["library"]["sources"][0]
        document = json.loads(source["content"])
        floor = {"kind": "orthogonal_polygon", "unit": "mm", "points": [
            [-2000.0, 1800.0], [0.0, 1800.0], [0.0, 4200.0], [-2000.0, 4200.0], [-2000.0, 1800.0]]}
        document["installation_path"]["staging"]["floor"] = floor
        source_content({"sources": [source]}, document)
        request = raw["installation_paths"][0]
        request["exterior_staging"]["value"]["floor"] = copy.deepcopy(floor)
        request["exterior_staging"]["source_sha256"] = source["sha256"]
        request["source"]["library_sha256"] = _sha(raw["library"])
        alternative = copy.deepcopy(request)
        alternative["id"] = "install_press_alternative"
        raw["installation_paths"].append(alternative)
        # Existing source envelopes use 54 vertices; one literal floor adds
        # five and fits. Both requests together add ten and exceed 62.
        limits = replace(ProcessReviewLimits(), max_scenario_vertices=62)
        for row in raw["installation_paths"]:
            single = copy.deepcopy(raw)
            single["installation_paths"] = [row]
            report = self.load(single, limits=limits)
            self.assertEqual(self.leaf(report, row["id"])["evidence"]["local_2d_path_status"], "PASS")
        self.write(raw)
        with patch("layout_configurator.transfer_review.parse_layout_result", side_effect=AssertionError("No common GEOS before joint path vertex admission")), \
                patch("layout_configurator.engineering_library._strict_json", side_effect=AssertionError("No nested decode before joint vertex admission")):
            report = self.api.load_process_review_with_paths(self.path, limits=limits)
        self.assertEqual(report["inventory_status"], "NOT_EVALUATED")
        self.assertEqual(report["status"], "UNKNOWN")
        self.assertIn("input_budget_exceeded", {row["code"] for row in report["issues"]})

    def test_default_leaf_vertex_ceiling_is_reserved_before_common_geometry_for_unbound_raw_shapes(self):
        raw = copy.deepcopy(self.raw)
        raw["library"] = None
        request = raw["installation_paths"][0]
        request["source"]["library_sha256"] = _sha(None)
        points = [[0, 0], [64, 0], [64, 64]]
        for step in range(63, -1, -1):
            points.extend([[step, step + 1], [step, step]])
        self.assertEqual(points[0], points[-1])
        request["exterior_staging"]["value"]["obstacles"] = [
            {"kind": "orthogonal_polygon", "unit": "mm", "points": copy.deepcopy(points)} for _ in range(40)]
        self.assertGreater(40 * len(points), 4096)
        original = self.write(raw)
        with patch("layout_configurator.transfer_review.parse_layout_result", side_effect=AssertionError("No common GEOS before the standalone vertex ceiling")), \
                patch("layout_configurator.engineering_library._strict_json", side_effect=AssertionError("No decode before raw geometry admission")):
            report = self.api.load_process_review_with_paths(self.path)
        self.assertEqual(report["inventory_status"], "NOT_EVALUATED")
        self.assertEqual(report["status"], "UNKNOWN")
        self.assertIn("input_budget_exceeded", {row["code"] for row in report["issues"]})
        self.assertEqual(self.path.read_bytes(), original)

    def test_actual_orthogonal_room_vertices_share_the_tightened_leaf_ceiling_before_common_parse(self):
        raw = copy.deepcopy(self.raw)
        points = [[0.0, 0.0], [6000.0, 0.0], [6000.0, 6000.0]]
        for step in range(1, 21):
            points.extend([[6000.0 - 300.0 * step, 6000.0 - 5.0 * (step - 1)],
                           [6000.0 - 300.0 * step, 6000.0 - 5.0 * step]])
        raw["layout"].update(geometry_version="0.2", units="mm")
        raw["layout"]["rooms"]["entry"] = {"kind": "orthogonal", "units": "mm", "vertices": points}
        rebind(raw)
        self.write(raw)
        with patch("layout_configurator.transfer_review.parse_layout_result", side_effect=AssertionError("Actual room vertices need admission before GEOS")), \
                patch("layout_configurator.engineering_library._strict_json", side_effect=AssertionError("No decode before actual room vertex admission")):
            report = self.api.load_process_review_with_paths(self.path,
                limits=replace(ProcessReviewLimits(), max_scenario_vertices=64))
        self.assertEqual(report["inventory_status"], "NOT_EVALUATED")
        self.assertEqual(report["status"], "UNKNOWN")
        self.assertIn("input_budget_exceeded", {row["code"] for row in report["issues"]})

    def test_bound_staging_source_copies_and_escaped_point_keys_reserve_all_leaf_vertices_before_geometry(self):
        points = [[-2000, 1800], [-1936, 1800], [-1936, 1864]]
        for step in range(63, -1, -1):
            points.extend([[-2000 + step, 1800 + step + 1], [-2000 + step, 1800 + step]])
        for escaped in (False, True):
            raw = copy.deepcopy(self.raw)
            source = raw["library"]["sources"][0]
            document = json.loads(source["content"])
            staging = document["installation_path"]["staging"]
            staging["obstacles"] = [{"kind": "orthogonal_polygon", "unit": "mm", "points": copy.deepcopy(points)} for _ in range(18)]
            source_content({"sources": [source]}, document)
            if escaped:
                source["content"] = source["content"].replace('"points"', r'"\u0070oints"')
                source["sha256"] = hashlib.sha256(source["content"].encode("utf-8")).hexdigest()
            request = raw["installation_paths"][0]
            request["exterior_staging"].update(value=copy.deepcopy(staging), source_sha256=source["sha256"])
            request["source"]["library_sha256"] = _sha(raw["library"])
            self.assertLess(18 * len(points), 4096)
            self.assertGreater(2 * 18 * len(points), 4096)
            self.write(raw)
            with self.subTest(escaped=escaped), \
                    patch("layout_configurator.transfer_review.parse_layout_result", side_effect=AssertionError("Source copies also consume the leaf vertex ceiling")), \
                    patch("layout_configurator.engineering_library._strict_json", side_effect=AssertionError("No source decode before aggregate source vertex admission")):
                report = self.api.load_process_review_with_paths(self.path)
                self.assertEqual(report["inventory_status"], "NOT_EVALUATED")
                self.assertEqual(report["status"], "UNKNOWN")
                self.assertIn("input_budget_exceeded", {row["code"] for row in report["issues"]})

    def test_structured_source_text_about_point_arrays_is_not_geometry(self):
        raw = copy.deepcopy(self.raw)
        source = raw["library"]["sources"][0]
        document = json.loads(source["content"])
        document["unused_note"] = 'Literal text: "points": [[0, 0]], brackets [], escaped quote \\". ' * 1000
        source_content({"sources": [source]}, document)
        request = raw["installation_paths"][0]
        request["exterior_staging"]["source_sha256"] = source["sha256"]
        request["source"]["library_sha256"] = _sha(raw["library"])
        report = self.load(raw, limits=replace(ProcessReviewLimits(), max_scenario_vertices=62))
        self.assertNotEqual(report.get("inventory_status"), "NOT_EVALUATED")
        self.assertEqual(self.leaf(report)["evidence"]["local_2d_path_status"], "PASS")
        self.assertEqual(report["source_frames"]["library"], raw["library"])

    def test_missing_library_is_unknown_while_independent_whole_body_collision_still_fails(self):
        for neighbor_y, expected in ((4500.0, "UNKNOWN"), (2700.0, "FAIL")):
            raw, _, _, _ = installation_process_fixture(neighbor_y=neighbor_y)
            raw["library"] = None
            request = raw["installation_paths"][0]
            request["source"]["library_sha256"] = _sha(None)
            request["exterior_staging"] = None
            with self.subTest(expected=expected):
                report = self.load(raw)
                evidence = self.leaf(report)["evidence"]
                self.assertEqual(report["status"], expected)
                self.assertEqual(evidence["status"], expected)
                self.assertEqual(report["reports"]["library"]["status"], "UNKNOWN")
                self.assertEqual(evidence["source"]["library_sha256"], _sha(None))
                if expected == "FAIL":
                    self.assertIn("swept_body_collision", {row["code"] for row in evidence["issues"]})
                else:
                    self.assertFalse(any(row["status"] == "FAIL" for row in evidence["issues"]))
                self.assert_scoped(report)

    def test_malformed_original_equipment_fields_reject_before_path_arithmetic_or_geometry(self):
        for mutation in ("numeric_string", "boolean_dimension", "missing_identity"):
            changed = copy.deepcopy(self.raw)
            spec = changed["building"]["equipment"][0]
            if mutation == "numeric_string":
                spec["width_mm"] = "1000"
            elif mutation == "boolean_dimension":
                spec["depth_mm"] = True
            else:
                spec.pop("id")
            self.write(changed)
            with self.subTest(mutation=mutation), \
                    patch("layout_configurator.transfer_review.parse_layout_result", side_effect=AssertionError("No GEOS for malformed original frame")), \
                    patch("layout_configurator.engineering_library._strict_json", side_effect=AssertionError("No nested decode for malformed original frame")), \
                    self.assertRaises(ValueError):
                self.api.load_process_review_with_paths(self.path)

    def test_translated_clearance_precision_is_rejected_before_common_geometry_or_nested_decode(self):
        source = self.raw["library"]["sources"][0]
        document = json.loads(source["content"])
        value = {"kind": "rectangle", "unit": "mm", "x": 0.0, "y": 0.0,
            "width": 1e-30, "depth": 800.0}
        document["installation_path"]["transport_clearance"] = value
        source_content({"sources": [source]}, document)
        request = self.raw["installation_paths"][0]
        request["transport_clearance"] = {"source_id": source["id"], "source_sha256": source["sha256"],
            "source_pointer": "/installation_path/transport_clearance", "value": value}
        request["exterior_staging"]["source_sha256"] = source["sha256"]
        request["source"]["library_sha256"] = _sha(self.raw["library"])
        self.assertGreater(value["x"] + value["width"], value["x"])
        self.assertEqual(9000.0 + value["width"], 9000.0)
        original = self.write(self.raw)
        with patch("layout_configurator.transfer_review.parse_layout_result", side_effect=AssertionError("No GEOS before translated precision admission")), \
                patch("layout_configurator.engineering_library._strict_json", side_effect=AssertionError("No nested decode before translated precision admission")), \
                patch("layout_configurator.equipment_installation_path.Polygon", side_effect=AssertionError("No path GEOS before translated precision admission")), \
                patch("layout_configurator.equipment_installation_path.box", side_effect=AssertionError("No path GEOS before translated precision admission")):
            report = self.api.load_process_review_with_paths(self.path)
        self.assertEqual(report["status"], "UNKNOWN")
        self.assertEqual(report["inventory_status"], "NOT_EVALUATED")
        self.assertTrue(any(row["status"] == "UNKNOWN" and "precision" in row["message"].lower() for row in report["issues"]))
        self.assert_scoped(report)
        self.assertEqual(self.path.read_bytes(), original)

    def test_bounded_delivery_retains_identity_stamped_path_failure_and_original_request_ids(self):
        raw, _, _, _ = installation_process_fixture(neighbor_y=2700.0)
        expected_identity = {"drawing_revision": raw["drawing_revision"], "variant": raw["variant"],
            "component_sha256": {name: _sha(raw[name]) for name in _COMPONENTS}}
        report = self.load(raw, limits=replace(ProcessReviewLimits(), max_report_bytes=4096, max_findings=1))
        self.assertEqual(report["status"], "FAIL")
        self.assertEqual(report["inventory_status"], "INCOMPLETE")
        encoded = json.dumps(report, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        self.assertLessEqual(len(encoded), 4096)
        failure = next(row for row in report["retained_failures"] if row["component"] == "installation_paths")
        self.assertEqual(failure["identity"], expected_identity)
        self.assertEqual(failure["request_id"], "install_press")
        self.assertEqual(failure["request_sha256"], _sha(raw["installation_paths"][0]))
        self.assertEqual(failure["failure"]["status"], "FAIL")
        self.assertEqual(failure["failure"]["code"], "swept_body_collision")
        self.assertEqual(failure["failure"]["related_equipment_id"], "neighbor")
        self.assertIn("segment_index", failure["failure"])
        self.assert_scoped(report)

    def test_compact_path_witness_survives_an_earlier_independent_pressure_failure(self):
        raw, _, _, _ = installation_process_fixture(neighbor_y=2700.0)
        add_declared_pressure_cycle(raw)
        complete = self.load(raw)
        self.assertEqual(complete["reports"]["environment"]["evidence"]["pressure_declared_status"], "FAIL")
        self.assertIn("swept_body_collision", {row["code"] for row in self.leaf(complete)["evidence"]["issues"]})
        compact = self.load(raw, limits=replace(ProcessReviewLimits(), max_report_bytes=4096, max_findings=1))
        self.assertEqual(compact["status"], "FAIL")
        self.assertEqual(compact["inventory_status"], "INCOMPLETE")
        path_failure = next(row for row in compact["retained_failures"] if row["component"] == "installation_paths")
        self.assertEqual(path_failure["identity"], complete["identity"])
        self.assertEqual(path_failure["request_id"], "install_press")
        self.assertEqual(path_failure["request_sha256"], _sha(raw["installation_paths"][0]))
        self.assertEqual(path_failure["failure"]["code"], "swept_body_collision")
        self.assertEqual(path_failure["failure"]["related_equipment_id"], "neighbor")
        self.assertIn("segment_index", path_failure["failure"])
        self.assertLessEqual(len(json.dumps(compact, ensure_ascii=False, allow_nan=False).encode("utf-8")), 4096)
        self.assertGreater(compact["failure_inventory"]["omitted"], 0)
        self.assert_scoped(compact)


if __name__ == "__main__":
    unittest.main()

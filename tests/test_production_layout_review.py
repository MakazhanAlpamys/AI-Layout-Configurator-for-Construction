"""Actual source-bound room contacts do not establish process acceptance."""

import copy
from dataclasses import fields, replace
import importlib
import json
import unittest
from unittest.mock import patch

from layout_configurator.building import BuildingIR
from layout_configurator.production_program import ProductionProgramLimits
from test_production_program import _building, _digest, _program


def production_layout_fixture():
    """The existing source contract and literal supplied room footprints."""
    building = _building()
    program = _program(building)
    layout = {"variant": 1, "rooms": {
        "molding": {"x": 0.0, "y": 0.0, "width": 4000.0, "height": 3000.0},
        "assembly": {"x": 4000.0, "y": 0.0, "width": 4000.0, "height": 3000.0},
        "waste": {"x": 0.0, "y": 4000.0, "width": 4000.0, "height": 2000.0},
    }}
    return program, building.to_dict(), layout


def rebind_production_layout(program, building):
    """Bind deliberately edited original declarations, never placed geometry."""
    base = {key: value for key, value in building.items() if key != "production_program"}
    program["building_sha256"] = _digest(base)
    if "production_program" in building:
        building["production_program"] = copy.deepcopy(program)


def set_room_area(program, building, room_id, area):
    """Make the supplied polygon's actual area an explicit project declaration."""
    row = next(row for row in building["layout"]["rooms"] if row["id"] == room_id)
    row.update(target_area=area, min_area=area, max_area=area, min_width=100.0, min_depth=100.0)
    rebind_production_layout(program, building)


def orthogonal_layout(layout):
    layout.update(geometry_version="0.2", units="mm")


class ProductionLayoutReviewTests(unittest.TestCase):
    def setUp(self):
        try:
            self.api = importlib.import_module("layout_configurator.production_layout_review")
        except ModuleNotFoundError as exc:
            self.fail(f"The independent source-bound production layout review is missing: {exc}")
        self.assertTrue(callable(getattr(self.api, "validate_production_program_layout", None)))
        self.assertTrue(callable(getattr(self.api, "ProductionLayoutLimits", None)))
        self.program, self.building, self.layout = production_layout_fixture()

    def validate(self, program=None, building=None, layout=None, **options):
        with patch("ortools.sat.python.cp_model.CpSolver.solve", side_effect=AssertionError("Review cannot solve a layout")):
            result = self.api.validate_production_program_layout(
                self.program if program is None else program,
                building_raw=self.building if building is None else building,
                layout_raw=self.layout if layout is None else layout, **options)
        self.assertIs(type(result), dict)
        return result

    def requirement(self, report, requirement_id):
        return next(row for row in report["requirements"] if row["requirement_id"] == requirement_id)

    def raw_identity(self, program=None, building=None, layout=None):
        program = self.program if program is None else program
        building = self.building if building is None else building
        layout = self.layout if layout is None else layout
        base = {key: value for key, value in building.items() if key != "production_program"}
        return {"building_sha256": _digest(building), "layout_sha256": _digest(layout),
            "production_program_sha256": _digest(program), "program_base_building_sha256": _digest(base)}

    def assert_scoped(self, report):
        self.assertFalse(report["ok"])
        self.assertEqual(report["document_status"], "DRAFT")
        self.assertEqual(report["engineering_acceptance"], "PENDING")
        self.assertEqual(report["authenticated_approval"], "UNKNOWN")
        self.assertEqual(report["full_facility_validation"], {"status": "UNKNOWN", "ok": False})
        self.assertEqual(report["full_scenario_validation"], "UNKNOWN")
        self.assertEqual(report["three_dimensional_feasibility"], "UNKNOWN")
        self.assertFalse(report["proven_building_infeasible"])

    def assert_rejected(self, program=None, building=None, layout=None, **options):
        try:
            result = self.validate(program, building, layout, **options)
        except ValueError:
            return
        self.assertEqual(result["status"], "FAIL")

    def test_clear_rectangular_contacts_retain_original_hashes_requirement_sources_and_detached_frames(self):
        before = copy.deepcopy((self.program, self.building, self.layout))
        report = self.validate()
        self.assertEqual((report["status"], report["geometry_status"], report["binding_status"], report["provenance_status"]),
                         ("PASS", "PASS", "PASS", "PASS"))
        self.assertEqual(report["source"], self.raw_identity())
        self.assertEqual(report["source_frames"], {"building": self.building, "layout": self.layout,
                                                  "production_program": self.program})
        required = self.requirement(report, "near_assembly")
        self.assertEqual((required["status"], required["operation_id"], required["source_id"],
                          required["room_a"], required["room_b"]), ("PASS", "mold", "brief", "molding", "assembly"))
        self.assertEqual(required["source_sha256"], self.program["sources"][0]["sha256"])
        self.assertEqual(required["reason"], self.program["requirements"][0]["reason"])
        self.assertEqual(required["excerpt"], self.program["requirements"][0]["excerpt"])
        self.assertEqual(required["basis"], "declared_fact")
        self.assertEqual(required["maximum_contiguous_mm"], 3000.0)
        self.assertEqual(required["required_contiguous_mm"], self.building["layout"]["door_width_mm"])
        self.assertEqual(self.requirement(report, "separate_waste")["status"], "PASS")
        self.assertEqual(report["counts"]["evaluated_requirements"], 2)
        self.assert_scoped(report)
        report["source_frames"]["production_program"]["sources"][0]["content"] = "Changed detached report"
        self.assertEqual((self.program, self.building, self.layout), before)
        self.assertEqual(self.validate()["source"], self.raw_identity())

    def test_whole_embedded_program_and_equal_python_numeric_scalars_keep_distinct_raw_identities(self):
        self.program["operations"][0]["measurements"]["cycle_time"]["value"] = 30.0
        self.building["production_program"] = copy.deepcopy(self.program)
        report = self.validate()
        self.assertEqual(report["status"], "PASS")
        self.assertEqual(report["source"], self.raw_identity())
        self.assertNotEqual(report["source"]["building_sha256"], report["source"]["program_base_building_sha256"])
        self.assertEqual(report["source_frames"]["building"]["production_program"], self.program)
        self.assertIs(type(report["source_frames"]["production_program"]["operations"][0]["measurements"]["cycle_time"]["value"]), float)
        integer_program = copy.deepcopy(self.program)
        integer_program["operations"][0]["measurements"]["cycle_time"]["value"] = 30
        integer_building = copy.deepcopy(self.building)
        integer_building["production_program"] = copy.deepcopy(integer_program)
        integer_layout = copy.deepcopy(self.layout)
        integer_layout["rooms"]["molding"]["width"] = 4000
        self.assertEqual(integer_program, self.program)
        self.assertEqual(integer_layout, self.layout)
        integer = self.validate(integer_program, integer_building, integer_layout)
        self.assertEqual(integer["status"], "PASS")
        self.assertNotEqual(integer["source"]["production_program_sha256"], report["source"]["production_program_sha256"])
        self.assertNotEqual(integer["source"]["building_sha256"], report["source"]["building_sha256"])
        self.assertNotEqual(integer["source"]["layout_sha256"], report["source"]["layout_sha256"])
        self.assertEqual(integer["source"]["program_base_building_sha256"], report["source"]["program_base_building_sha256"])
        self.assertIn("\r\n", report["source_frames"]["production_program"]["sources"][0]["content"])

    def test_actual_orthogonal_contiguous_contact_accepts_collinear_input_without_rewriting_raw_vertices(self):
        orthogonal_layout(self.layout)
        vertices = [[4000.0, 0.0], [8000.0, 0.0], [8000.0, 3000.0],
                    [4000.0, 3000.0], [4000.0, 1800.0], [4000.0, 900.0]]
        self.layout["rooms"]["assembly"] = {"kind": "orthogonal", "units": "mm", "vertices": vertices}
        original = copy.deepcopy(self.layout)
        report = self.validate()
        required = self.requirement(report, "near_assembly")
        self.assertEqual((report["geometry_status"], required["status"]), ("PASS", "PASS"))
        self.assertEqual(required["maximum_contiguous_mm"], 3000.0)
        self.assertEqual(len(required["shared_segments_mm"]), 1)
        self.assertEqual(report["source"]["layout_sha256"], _digest(original))
        self.assertEqual(report["source_frames"]["layout"], original)
        self.assertEqual(self.layout, original)

    def test_corner_contact_and_a_gap_equal_to_wall_thickness_cannot_supply_required_adjacency(self):
        for mutation in ("corner", "wall_gap"):
            layout = copy.deepcopy(self.layout)
            if mutation == "corner":
                layout["rooms"]["assembly"]["y"] = 3000.0
            else:
                layout["rooms"]["assembly"]["x"] += self.building["layout"]["wall_thickness_mm"]
            with self.subTest(mutation=mutation):
                report = self.validate(layout=layout)
                required = self.requirement(report, "near_assembly")
                self.assertEqual((report["status"], required["status"]), ("FAIL", "FAIL"))
                self.assertEqual(required["maximum_contiguous_mm"], 0)
                self.assertEqual(required["shared_segments_mm"], [])
                self.assertIn("required_room_adjacency_failed", {row["code"] for row in report["issues"]})

    def test_narrow_contact_uses_the_canonical_project_door_threshold_without_a_universal_or_flow_rule(self):
        self.layout["rooms"]["assembly"]["y"] = 2200.0
        report = self.validate()
        required = self.requirement(report, "near_assembly")
        self.assertEqual(required["maximum_contiguous_mm"], 800.0)
        self.assertEqual(required["required_contiguous_mm"], 900.0)
        self.assertEqual(required["status"], "FAIL")
        # The program has a flow, but this standalone gate evaluates only the
        # canonical room relation. Flow corridor/transfer reservations remain
        # separate predicates and cannot invent a different program threshold.
        self.building["layout"]["door_width_mm"] = 700.0
        rebind_production_layout(self.program, self.building)
        narrowed_project = self.validate()
        self.assertEqual(self.requirement(narrowed_project, "near_assembly")["required_contiguous_mm"], 700.0)
        self.assertEqual(narrowed_project["geometry_status"], "PASS")
        self.assert_scoped(narrowed_project)

    def test_disconnected_shared_contacts_cannot_be_summed_into_one_required_span(self):
        orthogonal_layout(self.layout)
        self.layout["rooms"]["assembly"] = {"kind": "orthogonal", "units": "mm", "vertices": [
            [4000.0, 0.0], [8000.0, 0.0], [8000.0, 3000.0], [4000.0, 3000.0],
            [4000.0, 2400.0], [5000.0, 2400.0], [5000.0, 600.0], [4000.0, 600.0]]}
        set_room_area(self.program, self.building, "assembly", 10.2)
        report = self.validate()
        required = self.requirement(report, "near_assembly")
        spans = required["shared_segments_mm"]
        lengths = [abs(end[0] - start[0]) + abs(end[1] - start[1]) for start, end in spans]
        self.assertEqual(sorted(lengths), [600.0, 600.0])
        self.assertGreater(sum(lengths), required["required_contiguous_mm"])
        self.assertEqual(required["maximum_contiguous_mm"], 600.0)
        self.assertEqual((report["status"], required["status"]), ("FAIL", "FAIL"))

    def test_forbidden_positive_contact_fails_while_corner_and_separated_rooms_pass(self):
        for x, y, expected in ((3999.0, 3000.0, "FAIL"), (4000.0, 3000.0, "PASS"), (0.0, 4000.0, "PASS")):
            layout = copy.deepcopy(self.layout)
            layout["rooms"]["waste"].update(x=x, y=y)
            with self.subTest(x=x, y=y):
                report = self.validate(layout=layout)
                forbidden = self.requirement(report, "separate_waste")
                self.assertEqual(forbidden["status"], expected)
                if expected == "FAIL":
                    self.assertEqual(forbidden["maximum_contiguous_mm"], 1.0)
                    self.assertIn("forbidden_room_adjacency_failed", {row["code"] for row in report["issues"]})
                else:
                    self.assertEqual(forbidden["maximum_contiguous_mm"], 0)
                    self.assertEqual(report["geometry_status"], "PASS")

    def test_polygon_bounding_boxes_cannot_invent_required_contact_or_room_overlap(self):
        orthogonal_layout(self.layout)
        self.layout["rooms"]["molding"] = {"kind": "orthogonal", "units": "mm", "vertices": [
            [0.0, 0.0], [4000.0, 0.0], [4000.0, 1500.0], [2000.0, 1500.0],
            [2000.0, 3000.0], [0.0, 3000.0]]}
        set_room_area(self.program, self.building, "molding", 9.0)
        false_contact = copy.deepcopy(self.layout)
        false_contact["rooms"]["assembly"]["y"] = 1800.0
        report = self.validate(layout=false_contact)
        self.assertEqual(self.requirement(report, "near_assembly")["maximum_contiguous_mm"], 0)
        self.assertEqual(self.requirement(report, "near_assembly")["status"], "FAIL")
        self.layout["rooms"]["waste"] = {"x": 2500.0, "y": 2000.0, "width": 1000.0, "height": 1000.0}
        set_room_area(self.program, self.building, "waste", 1.0)
        notch = self.validate()
        self.assertEqual(notch["geometry_status"], "PASS")
        self.assertEqual(self.requirement(notch, "near_assembly")["maximum_contiguous_mm"], 1500.0)
        self.assertEqual(self.requirement(notch, "separate_waste")["maximum_contiguous_mm"], 0)
        self.assertNotIn("room_overlap", {row["code"] for row in notch["issues"]})

    def test_real_positive_room_overlap_is_an_independent_hard_failure(self):
        self.layout["rooms"]["assembly"]["x"] = 3000.0
        report = self.validate()
        self.assertEqual((report["status"], report["geometry_status"]), ("FAIL", "FAIL"))
        overlap = next(row for row in report["issues"] if row["code"] == "room_overlap")
        self.assertEqual({overlap["room_a"], overlap["room_b"]}, {"molding", "assembly"})
        self.assertEqual(overlap["overlap_area_mm2"], 3000000.0)
        self.assert_scoped(report)

    def test_known_requirement_failure_survives_unknown_source_provenance_and_a_missing_other_room(self):
        self.layout["rooms"]["assembly"]["y"] = 3000.0
        self.program["sources"][0]["usage_rights"]["state"] = "UNKNOWN"
        self.program["requirements"][0]["basis"] = "assumption"
        self.program["operations"][0]["measurements"]["cycle_time"].update(status="UNKNOWN", value=None, source_id=None)
        report = self.validate()
        self.assertEqual((report["status"], report["geometry_status"], report["provenance_status"]), ("FAIL", "FAIL", "UNKNOWN"))
        self.assertEqual(self.requirement(report, "near_assembly")["status"], "FAIL")
        self.assertTrue({"unknown_usage_rights", "assumed_requirement", "unknown_measurement"} <=
                        {row["code"] for row in report["program_validation"]["issues"]})
        missing = copy.deepcopy(self.layout)
        missing["rooms"].pop("waste")
        mixed = self.validate(layout=missing)
        self.assertEqual((mixed["status"], mixed["geometry_status"]), ("FAIL", "FAIL"))
        self.assertEqual(self.requirement(mixed, "near_assembly")["status"], "FAIL")
        self.assertEqual(self.requirement(mixed, "separate_waste")["status"], "UNKNOWN")
        self.assert_scoped(mixed)

    def test_stale_bindings_malformed_original_scalars_and_embedded_mismatch_reject_before_geometry(self):
        stale = copy.deepcopy(self.program)
        stale["building_sha256"] = "0" * 64
        with patch("layout_configurator.orthogonal.Polygon", side_effect=AssertionError("No geometry for a stale program")), \
                patch("layout_configurator.result_geometry.box", side_effect=AssertionError("No geometry for a stale program")):
            report = self.validate(program=stale)
        self.assertEqual((report["status"], report["binding_status"]), ("FAIL", "FAIL"))
        self.assertIn("stale_building_binding", {row["code"] for row in report["issues"]})
        for value in (True, "1000"):
            changed = copy.deepcopy(self.building)
            changed["equipment"][0]["width_mm"] = value
            with self.subTest(value=value), \
                    patch.object(BuildingIR, "from_mapping", side_effect=AssertionError("Malformed raw source was normalized")):
                self.assert_rejected(building=changed)
        embedded = copy.deepcopy(self.building)
        embedded["production_program"] = copy.deepcopy(self.program)
        embedded["production_program"]["operations"][0]["measurements"]["cycle_time"]["value"] = 30.0
        self.assertEqual(embedded["production_program"], self.program)
        self.assertNotEqual(_digest(embedded["production_program"]), _digest(self.program))
        with patch("layout_configurator.orthogonal.Polygon", side_effect=AssertionError("No geometry for embedded program mismatch")), \
                patch("layout_configurator.result_geometry.box", side_effect=AssertionError("No geometry for embedded program mismatch")):
            report = self.validate(building=embedded)
        self.assertEqual(report["status"], "FAIL")
        self.assertIn("embedded_production_program_mismatch", {row["code"] for row in report["issues"]})

    def test_joint_raw_resource_work_and_lost_positive_endpoint_precision_stop_before_normalization_or_geometry(self):
        defaults = self.api.ProductionLayoutLimits()
        cases = [(replace(defaults, max_nodes=64), self.layout, "nodes"),
                 (replace(defaults, max_vertices=11), self.layout, "vertices"),
                 (replace(defaults, max_pair_checks=1), self.layout, "pairs"),
                 (replace(defaults, max_source_bytes=10), self.layout, "source_bytes"),
                 (replace(defaults, max_depth=3), self.layout, "depth")]
        collapsed = copy.deepcopy(self.layout)
        collapsed["rooms"]["assembly"].update(x=4000.0, width=1e-30)
        self.assertEqual(4000.0 + 1e-30, 4000.0)
        cases.append((defaults, collapsed, "precision"))
        before = copy.deepcopy((self.program, self.building, self.layout))
        for limits, layout, kind in cases:
            with self.subTest(kind=kind), \
                    patch.object(BuildingIR, "from_mapping", side_effect=AssertionError("Whole input admission must precede constructors")), \
                    patch("layout_configurator.orthogonal.Polygon", side_effect=AssertionError("Whole input admission must precede GEOS")), \
                    patch("layout_configurator.result_geometry.box", side_effect=AssertionError("Whole input admission must precede GEOS")):
                report = self.validate(layout=layout, limits=limits)
            self.assertEqual(report["status"], "UNKNOWN")
            self.assertEqual(report["inventory_status"], "NOT_EVALUATED")
            self.assertTrue(any(row["status"] == "UNKNOWN" and
                                ("precision" in row["message"].lower() if kind == "precision" else row["code"] == "input_budget_exceeded")
                                for row in report["issues"]))
            self.assert_scoped(report)
        self.assertEqual((self.program, self.building, self.layout), before)

    def test_positive_polygon_area_underflow_is_unknown_before_constructors_or_geometry(self):
        orthogonal_layout(self.layout)
        extent = 1e-200
        self.assertGreater(extent, 0)
        self.assertEqual(extent * extent, 0)
        self.layout["rooms"]["molding"] = {"kind": "orthogonal", "units": "mm", "vertices": [
            [0.0, 0.0], [extent, 0.0], [extent, extent], [0.0, extent],
        ]}
        with patch.object(BuildingIR, "from_mapping",
                          side_effect=AssertionError("Polygon precision admission must precede constructors")) as construct, \
                patch("layout_configurator.orthogonal.Polygon",
                      side_effect=AssertionError("Polygon precision admission must precede GEOS")) as polygon:
            report = self.validate()
        self.assertEqual(report["status"], "UNKNOWN")
        self.assertEqual(report["inventory_status"], "NOT_EVALUATED")
        self.assertTrue(any("precision" in row["message"].lower() for row in report["issues"]))
        construct.assert_not_called()
        polygon.assert_not_called()
        self.assert_scoped(report)

    def test_genuinely_zero_area_polygon_remains_an_invalid_source_failure(self):
        orthogonal_layout(self.layout)
        self.layout["rooms"]["molding"] = {"kind": "orthogonal", "units": "mm", "vertices": [
            [0.0, 0.0], [1000.0, 0.0], [2000.0, 0.0], [3000.0, 0.0],
        ]}
        report = self.validate()
        self.assertEqual(report["status"], "FAIL")
        self.assertNotIn("invalid_original_source", {row["code"] for row in report["issues"]})
        self.assert_scoped(report)

    def test_capped_delivery_retains_original_requirement_failure_ids_hashes_and_contact_measurements(self):
        self.layout["rooms"]["assembly"]["y"] = 3000.0
        self.layout["rooms"]["waste"]["y"] = 3000.0
        self.program["sources"][0]["usage_rights"]["state"] = "UNKNOWN"
        report = self.validate(limits=replace(self.api.ProductionLayoutLimits(), max_findings=1, max_report_bytes=4096))
        self.assertEqual((report["status"], report["geometry_status"]), ("FAIL", "FAIL"))
        self.assertEqual(report["inventory_status"], "INCOMPLETE")
        self.assertEqual(report["source"], self.raw_identity())
        failure = report["retained_failures"][0]
        self.assertEqual(failure["code"], "required_room_adjacency_failed")
        self.assertEqual((failure["requirement_id"], failure["operation_id"], failure["source_id"],
                          failure["room_a"], failure["room_b"]), ("near_assembly", "mold", "brief", "molding", "assembly"))
        self.assertEqual(failure["source_sha256"], self.program["sources"][0]["sha256"])
        self.assertEqual(failure["maximum_contiguous_mm"], 0)
        self.assertEqual(failure["required_contiguous_mm"], 900.0)
        self.assertGreater(report["failure_inventory"]["omitted"], 0)
        self.assertLessEqual(len(json.dumps(report, ensure_ascii=False, allow_nan=False).encode("utf-8")), 4096)
        self.assert_scoped(report)

    def test_separate_limits_reuse_the_program_contract_and_empty_requirements_make_no_process_geometry_claim(self):
        defaults = self.api.ProductionLayoutLimits()
        inherited = {row.name for row in fields(ProductionProgramLimits())}
        self.assertTrue(inherited <= {row.name for row in fields(defaults)})
        for name in inherited:
            self.assertEqual(getattr(defaults, name), getattr(ProductionProgramLimits(), name))
        self.assertEqual((defaults.max_rooms, defaults.max_vertices, defaults.max_pair_checks,
                          defaults.max_findings, defaults.max_report_bytes), (128, 16384, 131072, 256, 1048576))
        for limits in (replace(defaults, max_rooms=True), replace(defaults, max_pair_checks=0),
                       replace(defaults, max_vertices=16385), replace(defaults, max_report_bytes=1024)):
            with self.subTest(limits=limits):
                self.assert_rejected(limits=limits)
        self.program["requirements"] = []
        empty = self.validate()
        self.assertEqual((empty["status"], empty["geometry_status"]), ("UNKNOWN", "UNKNOWN"))
        self.assertEqual(empty["requirements"], [])
        self.assertEqual(empty["counts"]["evaluated_requirements"], 0)
        self.assert_scoped(empty)


if __name__ == "__main__":
    unittest.main()

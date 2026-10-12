import unittest
from dataclasses import replace

from layout_configurator.building import BuildingIR
from layout_configurator.equipment import place_equipment_clear_of_doors, validate_equipment_layout
from layout_configurator.flows import route_flows, validate_flow_routes
from layout_configurator.models import Rect
from layout_configurator.orthogonal import OrthogonalPolygon
from layout_configurator.solver import solve_layouts
from layout_configurator.validation import validate_layout
from layout_configurator.zone_hierarchy import ZoneHierarchyFailure, ZoneHierarchyNotApplicable, solve_zone_layouts


PARTITION = {"logistics": {"inbound": ["a", "b"], "outbound": ["e", "f"]}}
SIZES = {"inbound": [(4000, 2000)], "outbound": [(4000, 2000)], "process": [(4000, 2000)]}
PINS = {"a": Rect(0, 0, 2000, 2000), "f": Rect(10000, 0, 2000, 2000)}


def _program(*, leftover=False, process=False):
    ids = "abcdefg" if leftover else "abcdef"
    raw = {
        "layout": {"boundary": {"width": len(ids) * 2000, "height": 2000}, "entry_room": "a",
                   "rooms": [{"id": room_id, "target_area": 4, "min_area": 4, "max_area": 4,
                              "min_width": 2000, "min_depth": 2000,
                              "required_adjacency": [ids[index + 1]] if index + 1 < len(ids) else []}
                             for index, room_id in enumerate(ids)]},
        "zones": [{"id": "logistics", "room_ids": ["a", "b", "e", "f"],
                   "required_adjacency": ["process"]},
                  {"id": "process", "room_ids": ["c", "d"]},
                  {"id": "material_gateway", "parent_zone_id": "process", "room_ids": []}],
    }
    if process:
        raw["equipment"] = [{"id": "machine", "room_id": "c", "width_mm": 400,
                             "depth_mm": 200, "clearance_mm": 100}]
        raw["flows"] = [{"id": "material", "type": "material", "from_ids": ["a"],
                         "to_ids": ["f"], "minimum_clear_width_mm": 800, "required": True}]
    return BuildingIR.from_mapping(raw)


def _geometry(evidence):
    variant = evidence["variants"][0]
    return ({key: Rect(**raw) for key, raw in variant["block_rects"].items()},
            {key: tuple(Rect(**raw) for raw in pieces) for key, pieces in variant["zone_geometry"].items()})


class ZoneBlockTests(unittest.TestCase):
    def _solve(self, building=None, **kwargs):
        from layout_configurator.zone_blocks import solve_block_zone_layouts, validate_block_zone_layout
        building = building or _program()
        evidence = {}
        kwargs.setdefault("zone_size_options_mm", SIZES)
        kwargs.setdefault("fixed_rects", PINS)
        result = solve_block_zone_layouts(building, 1, 0.5, 1, zone_blocks=PARTITION,
                                         evidence=evidence, **kwargs)[0]
        self.assertTrue(validate_layout(building.layout, result).ok)
        blocks, geometry = _geometry(evidence)
        constraints = {key: value for key, value in kwargs.items()
                       if key in {"fixed_rects", "axis_aligned_room_ids", "structural_axes_x_mm", "structural_axes_y_mm"}}
        report = validate_block_zone_layout(building, result, blocks, zone_blocks=PARTITION,
                                            zone_geometry=geometry, **constraints)
        self.assertTrue(report.ok, report.issues)
        return result, evidence

    def test_two_logistics_blocks_have_a_physical_union_excluding_the_process_gap(self):
        building = _program(process=True)
        original = building.to_dict()
        result, evidence = self._solve(building)
        blocks, geometry = _geometry(evidence)
        self.assertEqual(blocks["inbound"], Rect(0, 0, 4000, 2000))
        self.assertEqual(blocks["process"], Rect(4000, 0, 4000, 2000))
        self.assertEqual(blocks["outbound"], Rect(8000, 0, 4000, 2000))
        self.assertEqual(geometry["logistics"], (blocks["inbound"], blocks["outbound"]))
        self.assertEqual(sum(rect.area_mm2 for rect in geometry["logistics"]), 16000000)
        self.assertEqual(sum(rect.intersection_area(blocks["process"]) for rect in geometry["logistics"]), 0)
        self.assertFalse(evidence["descendant_container_geometry"])
        self.assertNotIn("material_gateway", geometry)
        self.assertEqual(building.to_dict(), original)
        equipment, _ = place_equipment_clear_of_doors(building, result, time_limit_seconds=0.5, seed=1)
        routes = route_flows(building, result, equipment)
        self.assertTrue(validate_equipment_layout(building, result, equipment).ok)
        self.assertTrue(validate_flow_routes(building, result, routes, equipment).ok)

    def test_split_union_solves_a_valid_program_excluded_by_a_single_zone_rectangle(self):
        building = _program()
        baseline = solve_layouts(building.layout, 1, 0.5, 1, fixed_rects=PINS)[0]
        self.assertTrue(validate_layout(building.layout, baseline).ok)
        evidence = {}
        with self.assertRaises(ZoneHierarchyFailure) as caught:
            solve_zone_layouts(building, 1, 0.5, 1, fixed_rects=PINS, evidence=evidence,
                               zone_size_options_mm={"logistics": [(8000, 2000)], "process": [(4000, 2000)]})
        self.assertEqual(caught.exception.model_status, "INFEASIBLE")
        self.assertFalse(caught.exception.to_dict()["proven_building_infeasible"])
        solved, _ = self._solve(building)
        self.assertEqual(solved.placements, baseline.placements)

    def test_invalid_partitions_are_rejected_before_any_local_model(self):
        from layout_configurator.zone_blocks import solve_block_zone_layouts
        invalid = (
            ({"missing": {"part": ["a"]}}, "unknown_block_zone"),
            ({"material_gateway": {"part": ["c"]}}, "nested_block_zone"),
            ({"logistics": {}}, "invalid_block_partition"),
            ({"logistics": {"part": []}}, "invalid_block_partition"),
            ({"logistics": {"part": ["a", "b"]}}, "incomplete_block_partition"),
            ({"logistics": {"inbound": ["a", "b"], "outbound": ["b", "e", "f"]}}, "duplicate_block_membership"),
            ({"logistics": {"part": ["a", "b", "e", "f", "c"]}}, "foreign_block_room"),
            ({"logistics": {"part": ["a", "b", "e", "f", "missing"]}}, "foreign_block_room"),
            ({"logistics": {"process": ["a", "b", "e", "f"]}}, "duplicate_block_id"),
            ({"logistics": {"part": "abef"}}, "invalid_block_partition"),
        )
        for partition, code in invalid:
            with self.subTest(partition=partition):
                evidence = {}
                with self.assertRaises(ZoneHierarchyNotApplicable) as caught:
                    solve_block_zone_layouts(_program(), 1, 0.5, 1, zone_blocks=partition, evidence=evidence)
                self.assertEqual(caught.exception.stage, "input")
                self.assertEqual(caught.exception.code, code)
                self.assertEqual(caught.exception.model_status, "NOT_BUILT")
                self.assertEqual(evidence["stage_attempts"], [])

    def test_independent_validator_rejects_a_bounding_box_presented_as_the_zone_union(self):
        from layout_configurator.zone_blocks import validate_block_zone_layout
        result, evidence = self._solve()
        blocks, geometry = _geometry(evidence)
        geometry["logistics"] = (Rect(0, 0, 12000, 2000),)
        report = validate_block_zone_layout(_program(), result, blocks, zone_blocks=PARTITION, zone_geometry=geometry)
        codes = {issue.code for issue in report.issues}
        self.assertIn("zone_union_mismatch", codes)
        self.assertIn("zone_union_unrelated_room", codes)

    def test_independent_validator_rejects_missing_overlapping_and_uncontained_blocks(self):
        from layout_configurator.zone_blocks import validate_block_zone_layout
        result, evidence = self._solve()
        blocks, _ = _geometry(evidence)
        malformed = (
            ({key: rect for key, rect in blocks.items() if key != "outbound"}, "missing_zone_container"),
            ({**blocks, "inbound": Rect(4000, 0, 4000, 2000)}, "zone_overlap"),
            ({**blocks, "inbound": Rect(0, 0, 2000, 2000)}, "zone_containment"),
        )
        for rectangles, code in malformed:
            with self.subTest(code=code):
                report = validate_block_zone_layout(_program(), result, rectangles, zone_blocks=PARTITION)
                self.assertIn(code, {issue.code for issue in report.issues})

    def test_invalid_local_size_options_have_bounded_block_stage_evidence(self):
        from layout_configurator.zone_blocks import solve_block_zone_layouts
        evidence = {}
        with self.assertRaises(ZoneHierarchyFailure) as caught:
            solve_block_zone_layouts(_program(), 1, 0.5, 1, zone_blocks=PARTITION, evidence=evidence,
                                     zone_size_options_mm={**SIZES, "inbound": [(2000, 2000)]})
        self.assertEqual(caught.exception.stage, "local")
        self.assertEqual(caught.exception.unit_ids, ("inbound",))
        self.assertEqual(caught.exception.proof_scope, "bounded_candidates")
        self.assertFalse(evidence["failure"]["proven_building_infeasible"])

    def test_unassigned_room_remains_an_explicit_singleton_outside_zone_unions(self):
        result, evidence = self._solve(_program(leftover=True), zone_size_options_mm={**SIZES, "room:g": [(2000, 2000)]})
        blocks, geometry = _geometry(evidence)
        self.assertEqual(blocks["room:g"], result.placements["g"])
        self.assertNotIn("room:g", geometry)
        self.assertEqual(sum(piece.intersection_area(blocks["room:g"]) for pieces in geometry.values() for piece in pieces), 0)

    def test_rectangular_block_backend_guards_polygon_results_before_union_checks(self):
        from layout_configurator.zone_blocks import validate_block_zone_layout
        result, evidence = self._solve()
        polygon = OrthogonalPolygon(((0, 0), (2000, 0), (2000, 2000), (0, 2000)))
        replaced = replace(result, placements={**result.placements, "a": polygon})
        report = validate_block_zone_layout(_program(), replaced, _geometry(evidence)[0], zone_blocks=PARTITION)
        self.assertIn("unsupported_geometry", {issue.code for issue in report.issues})

    def test_budget_and_pin_input_errors_stay_structured_before_models(self):
        from layout_configurator.zone_blocks import solve_block_zone_layouts
        for kwargs, code in (({"variants": True}, "invalid_budget"), ({"max_local_options": 1.5}, "invalid_budget"),
                             ({"max_validation_attempts": "3"}, "invalid_budget"),
                             ({"fixed_rects": {"missing": Rect(0, 0, 2000, 2000)}}, "unknown_fixed_room"),
                             ({"axis_aligned_room_ids": ["a"], "structural_axes_x_mm": []}, "invalid_axes")):
            with self.subTest(kwargs=kwargs):
                evidence = {}
                with self.assertRaises(ZoneHierarchyNotApplicable) as caught:
                    solve_block_zone_layouts(_program(), time_limit_seconds=0.5, seed=1,
                                             zone_blocks=PARTITION, evidence=evidence, **kwargs)
                self.assertEqual(caught.exception.code, code)
                self.assertEqual(evidence["stage_attempts"], [])

    def test_global_fixed_axes_entry_window_and_cutout_constraints_survive_block_projection(self):
        from layout_configurator.zone_blocks import solve_block_zone_layouts, validate_block_zone_layout
        building = BuildingIR.from_mapping({
            "layout": {
                "boundary": {"width": 6000, "height": 6000,
                             "cutouts": [{"x": 2000, "y": 2000, "width": 2000, "height": 2000}]},
                "entry_room": "a",
                "rooms": [{"id": "a", "target_area": 4, "min_area": 4, "max_area": 4,
                           "min_width": 2000, "min_depth": 2000, "needs_daylight": True}],
                "external_entry": {"id": "entrance", "room_id": "a", "side": "top", "offset_mm": 1000, "width_mm": 900},
                "windows": [{"id": "window", "room_id": "a", "side": "right", "offset_mm": 1000, "width_mm": 1200}],
            },
            "zones": [{"id": "work", "room_ids": []}, {"id": "child", "parent_zone_id": "work", "room_ids": ["a"]}],
        })
        partition = {"work": {"office": ["a"]}}
        constraints = {"fixed_rects": {"a": Rect(4000, 4000, 2000, 2000)},
                       "axis_aligned_room_ids": ["a"], "structural_axes_x_mm": [4000, 6000],
                       "structural_axes_y_mm": [4000, 6000]}
        evidence = {}
        result = solve_block_zone_layouts(building, 1, 0.5, 1, zone_blocks=partition,
                                         evidence=evidence, **constraints)[0]
        self.assertEqual(result.placements["a"], constraints["fixed_rects"]["a"])
        blocks, geometry = _geometry(evidence)
        self.assertEqual(blocks["office"].intersection_area(building.layout.boundary.cutouts[0]), 0)
        self.assertNotIn("child", geometry)
        report = validate_block_zone_layout(building, result, blocks, zone_blocks=partition,
                                            zone_geometry=geometry, **constraints)
        self.assertTrue(report.ok, report.issues)

    def test_exact_count_failure_preserves_valid_block_and_zone_union_stage_geometry(self):
        from layout_configurator.zone_blocks import solve_block_zone_layouts, validate_block_zone_layout
        evidence = {}
        with self.assertRaises(ZoneHierarchyFailure) as caught:
            solve_block_zone_layouts(_program(), 2, 0.5, 1, zone_blocks=PARTITION,
                                     zone_size_options_mm=SIZES, fixed_rects=PINS, evidence=evidence)
        self.assertEqual(caught.exception.model_status, "INFEASIBLE")
        self.assertEqual(len(evidence["variants"]), 1)
        self.assertNotIn("zone_rects", evidence["variants"][0])
        blocks, geometry = _geometry(evidence)
        baseline = solve_layouts(_program().layout, 1, 0.5, 1, fixed_rects=PINS)[0]
        report = validate_block_zone_layout(_program(), baseline, blocks, zone_blocks=PARTITION, zone_geometry=geometry)
        self.assertTrue(report.ok, report.issues)

    def test_independent_validator_checks_internal_block_connectivity(self):
        from layout_configurator.zone_blocks import validate_block_zone_layout
        result, evidence = self._solve()
        blocks, _ = _geometry(evidence)
        disconnected = {"logistics": {"whole": ["a", "b", "e", "f"]}}
        rectangles = {"whole": Rect(0, 0, 12000, 2000), "process": blocks["process"]}
        report = validate_block_zone_layout(_program(), result, rectangles, zone_blocks=disconnected)
        self.assertIn("disconnected_zone_block", {issue.code for issue in report.issues})


if __name__ == "__main__":
    unittest.main()

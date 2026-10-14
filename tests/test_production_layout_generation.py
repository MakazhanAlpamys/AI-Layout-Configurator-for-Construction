"""Finite supplied room choices retain original production source authority.

The admission fixture is independently checked before the missing feature's
RED. Candidates must satisfy fresh actual room/door QA and strict contiguous
production contacts; finite failure is not a whole-building impossibility.
"""

import copy
from contextlib import contextmanager, ExitStack
from dataclasses import fields, replace
import importlib
import inspect
import json
import unittest
from unittest.mock import patch

from layout_configurator.building import BuildingIR
from layout_configurator.orthogonal_validation import OrthogonalDimensionPolicy, validate_orthogonal_layout
from layout_configurator.production_layout_review import ProductionLayoutLimits, validate_production_program_layout
from layout_configurator.result_geometry import parse_layout_result
import test_production_program as program_fixture
import layout_configurator.facility_generation as original_facility
import layout_configurator.solver as original_solver
import layout_configurator.orthogonal_solver as original_orthogonal


def json_bytes(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")


def json_nodes(value):
    if type(value) is dict:
        return 1 + sum(1 + json_nodes(child) for child in value.values())
    if type(value) is list:
        return 1 + sum(json_nodes(child) for child in value)
    return 1


def rows(value):
    """Find retained originating records without depending on nesting labels."""
    if type(value) is dict:
        yield value
        for child in value.values():
            yield from rows(child)
    elif type(value) is list:
        for child in value:
            yield from rows(child)


def production_generation_fixture():
    """Author a connected original before making its production binding."""
    raw = program_fixture._building().to_dict()
    raw["layout"]["external_entry"] = {"id": "molding_exterior", "room_id": "molding",
        "side": "bottom", "offset_mm": 2000.0, "width_mm": 900.0}
    next(room for room in raw["layout"]["rooms"] if room["id"] == "assembly")["required_adjacency"] = ["waste"]
    raw["layout"]["doors"] = [
        {"id": "molding_assembly", "room_a": "molding", "room_b": "assembly", "offset_mm": 1500.0, "width_mm": 900.0},
        {"id": "assembly_waste", "room_a": "assembly", "room_b": "waste", "offset_mm": 2000.0, "width_mm": 900.0}]
    building = BuildingIR.from_mapping(raw)
    program = program_fixture._program(building)
    catalog = {
        "molding": [{"x": 0.0, "y": 0.0, "width": 4000.0, "height": 3000.0}],
        "assembly": [{"x": 4000.0, "y": 0.0, "width": 4000.0, "height": 3000.0}],
        "waste": [{"x": 4000.0, "y": 3000.0, "width": 4000.0, "height": 2000.0}],
    }
    return program, building.to_dict(), catalog


def production_threshold_fixture(*, epsilon=False):
    """A real narrower door leaves the strict program span as the final gate."""
    raw = program_fixture._building().to_dict()
    raw["layout"]["rooms"] = [room for room in raw["layout"]["rooms"] if room["id"] != "waste"]
    raw["layout"]["rooms"][0]["forbidden_adjacency"] = []
    raw["layout"]["door_width_mm"] = 1600.0
    raw["layout"]["external_entry"] = {"id": "molding_exterior", "room_id": "molding",
        "side": "left", "offset_mm": 1500.0, "width_mm": 900.0}
    raw["layout"]["doors"] = [{"id": "molding_assembly", "room_a": "molding", "room_b": "assembly",
                               "offset_mm": 800.0, "width_mm": 900.0}]
    building = BuildingIR.from_mapping(raw)
    program = program_fixture._program(building)
    program["requirements"] = program["requirements"][:1]
    catalog = {"molding": [{"x": 0.0, "y": 0.0, "width": 4000.0, "height": 3000.0}],
        "assembly": [{"x": 4000.0, "y": 1400.0000005 if epsilon else 1400.0,
                      "width": 4000.0, "height": 3000.0}]}
    return program, building.to_dict(), catalog


def layout_for(catalog, selections=None):
    selections = {} if selections is None else selections
    footprints = {room: copy.deepcopy(values[selections.get(room, 0)]) for room, values in catalog.items()}
    layout = {"variant": 1, "rooms": footprints}
    if any(shape.get("kind") == "orthogonal" for shape in footprints.values()):
        layout.update(geometry_version="0.2", units="mm")
    return layout


def production_disjoint_contact_fixture():
    """Two 800 mm spans cannot supply one 1600 mm handover opening."""
    _, raw, catalog = production_threshold_fixture()
    assembly = next(room for room in raw["layout"]["rooms"] if room["id"] == "assembly")
    assembly.update(target_area=10.6, min_area=10.6, max_area=10.6, min_width=500.0, min_depth=500.0)
    raw["layout"]["doors"][0].update(offset_mm=400.0, width_mm=400.0)
    canonical = BuildingIR.from_mapping(raw)
    program = program_fixture._program(canonical)
    program["requirements"] = program["requirements"][:1]
    catalog["assembly"] = [{"kind": "orthogonal", "units": "mm", "vertices": [
        [4000.0, 0.0], [8000.0, 0.0], [8000.0, 3000.0], [4000.0, 3000.0],
        [4000.0, 2200.0], [5000.0, 2200.0], [5000.0, 800.0], [4000.0, 800.0]]}]
    return program, canonical.to_dict(), catalog


class ProductionGenerationFixtureAdmissionTests(unittest.TestCase):
    def test_fixture_admission_checks_supplied_positive_rooms_doors_and_real_epsilon_boundary(self):
        program, building, catalog = production_generation_fixture()
        canonical = BuildingIR.from_mapping(building)
        layout = layout_for(catalog)
        room = validate_orthogonal_layout(canonical.layout, parse_layout_result(layout)).to_dict()
        production = validate_production_program_layout(program, building_raw=building, layout_raw=layout)
        self.assertEqual((room["status"], production["geometry_status"], production["binding_status"], production["provenance_status"]),
                         ("PASS", "PASS", "PASS", "PASS"))
        checks = {row["id"]: row["status"] for row in room["checks"]}
        self.assertTrue(all(checks[name] == "PASS" for name in ("OPENINGS", "DOOR_APPROACHES", "TOPOLOGY", "ROOM_USABLE_CONNECTIVITY")))
        self.assertEqual(next(row for row in production["requirements"] if row["requirement_id"] == "separate_waste")["maximum_contiguous_mm"], 0)
        program, building, catalog = production_threshold_fixture(epsilon=True)
        canonical = BuildingIR.from_mapping(building)
        for y, strict_status, expected_span in ((0.0, "FAIL", 1599.9999995), (100.0, "PASS", 1699.9999995)):
            selected = copy.deepcopy(catalog)
            selected["molding"][0]["y"] = y
            layout = layout_for(selected)
            with self.subTest(molding_y=y):
                room = validate_orthogonal_layout(canonical.layout, parse_layout_result(layout)).to_dict()
                strict = validate_production_program_layout(program, building_raw=building, layout_raw=layout)
                self.assertEqual(room["status"], "PASS", "The epsilon proof must reach actual independent production QA")
                requirement = strict["requirements"][0]
                self.assertEqual(requirement["status"], strict_status)
                self.assertAlmostEqual(requirement["maximum_contiguous_mm"], expected_span, delta=1e-7)
                self.assertEqual(requirement["required_contiguous_mm"], 1600.0)
        program, building, catalog = production_disjoint_contact_fixture()
        strict = validate_production_program_layout(program, building_raw=building, layout_raw=layout_for(catalog))
        requirement = strict["requirements"][0]
        self.assertEqual((strict["geometry_status"], requirement["maximum_contiguous_mm"], requirement["required_contiguous_mm"]),
                         ("FAIL", 800.0, 1600.0))
        self.assertEqual(sum(abs(end[0] - start[0]) + abs(end[1] - start[1])
                             for start, end in requirement["shared_segments_mm"]), 1600.0)


class ProductionLayoutGenerationTests(unittest.TestCase):
    def setUp(self):
        try:
            self.api = importlib.import_module("layout_configurator.production_layout_generation")
        except ModuleNotFoundError as error:
            self.fail(f"The opt-in source-bound finite production layout generator is missing: {error}")
        self.assertTrue(callable(getattr(self.api, "generate_production_layout_candidates", None)))
        self.assertTrue(callable(getattr(self.api, "ProductionLayoutGenerationLimits", None)))
        self.program, self.building, self.catalog = production_generation_fixture()

    def generate(self, program=None, building=None, catalog=None, **options):
        program = self.program if program is None else program
        building = self.building if building is None else building
        catalog = self.catalog if catalog is None else catalog
        original = copy.deepcopy((program, building, catalog))
        options.setdefault("deterministic_units", .1)
        try:
            report = self.api.generate_production_layout_candidates(program, building_raw=building, room_catalog=catalog, **options)
        finally:
            self.assertEqual((program, building, catalog), original, "Generation must not rewrite original declarations or footprints")
        self.assertIs(type(report), dict)
        return report

    def identity(self, program=None, building=None, catalog=None):
        program = self.program if program is None else program
        building = self.building if building is None else building
        catalog = self.catalog if catalog is None else catalog
        return {"building_sha256": program_fixture._digest(building), "production_program_sha256": program_fixture._digest(program),
            "program_base_building_sha256": program_fixture._digest({key: value for key, value in building.items() if key != "production_program"}),
            "room_catalog_sha256": program_fixture._digest(catalog)}

    def scoped(self, report):
        self.assertFalse(report["ok"])
        self.assertEqual(report["document_status"], "DRAFT")
        self.assertEqual(report["engineering_acceptance"], "PENDING")
        self.assertEqual(report["authenticated_approval"], "UNKNOWN")
        self.assertEqual(report["full_facility_validation"], {"status": "UNKNOWN", "ok": False})
        self.assertEqual(report["full_scenario_validation"], "UNKNOWN")
        self.assertEqual(report["three_dimensional_feasibility"], "UNKNOWN")
        self.assertFalse(report["proven_building_infeasible"])

    @contextmanager
    def no_cp(self):
        with ExitStack() as stack:
            mocks = [stack.enter_context(patch(target, side_effect=AssertionError("Invalid or unsupported source must not enter CP"))) for target in
                ("ortools.sat.python.cp_model.CpModel", "ortools.sat.python.cp_model.CpSolver.solve", "ortools.sat.python.cp_model.CpSolver.Solve")]
            try:
                yield
            finally:
                for mocked in mocks:
                    mocked.assert_not_called()

    @contextmanager
    def no_constructors_or_cp(self):
        with ExitStack() as stack:
            targets = ("layout_configurator.building.BuildingIR.from_mapping", "layout_configurator.result_geometry.parse_layout_result",
                "shapely.geometry.polygon.Polygon.__new__", "layout_configurator.result_geometry.box",
                "layout_configurator.orthogonal_validation.box", "layout_configurator.orthogonal_solver.box",
                "ortools.sat.python.cp_model.CpModel", "ortools.sat.python.cp_model.CpSolver.solve", "ortools.sat.python.cp_model.CpSolver.Solve")
            mocks = [stack.enter_context(patch(target, side_effect=AssertionError("Pure joint admission must precede constructors, geometry and CP"))) for target in targets]
            try:
                yield
            finally:
                for mocked in mocks:
                    mocked.assert_not_called()

    def fresh(self, candidate, program=None, building=None):
        program = self.program if program is None else program
        building = self.building if building is None else building
        canonical = BuildingIR.from_mapping(building, include_production_program="production_program" in building)
        parsed = parse_layout_result(candidate["layout"])
        with self.no_cp():
            room = validate_orthogonal_layout(canonical.layout, parsed).to_dict()
            production = validate_production_program_layout(program, building_raw=building, layout_raw=candidate["layout"])
        self.assertEqual(candidate["room_report"]["status"], "PASS")
        self.assertEqual(room["status"], "PASS")
        checks = {row["id"]: row["status"] for row in room["checks"]}
        self.assertTrue(all(checks[name] == "PASS" for name in ("OPENINGS", "DOOR_APPROACHES", "TOPOLOGY", "ROOM_USABLE_CONNECTIVITY")))
        self.assertEqual((candidate["production_review"]["geometry_status"], candidate["production_review"]["binding_status"]), ("PASS", "PASS"))
        self.assertEqual((production["geometry_status"], production["binding_status"]), ("PASS", "PASS"))
        self.assertEqual(candidate["production_review"]["source"], production["source"])
        self.assertEqual(candidate["production_review"]["requirements"], production["requirements"])
        self.assertEqual(set(candidate["candidate_ids"]), set(candidate["layout"]["rooms"]))
        return production

    def rejected(self, **options):
        try:
            report = self.generate(**options)
        except ValueError:
            return
        self.assertEqual(report["status"], "FAIL")
        self.assertEqual(report["candidates"], [])
        self.scoped(report)

    def test_real_connected_candidates_have_fresh_room_and_production_qa_original_hashes_and_detached_sources(self):
        before = copy.deepcopy((self.program, self.building, self.catalog))
        report = self.generate()
        self.assertEqual((report["status"], report["generation_status"]), ("PASS", "PASS"))
        self.assertEqual(report["source"], self.identity())
        self.assertEqual(report["source_frames"], {"building": self.building, "production_program": self.program, "room_catalog": self.catalog})
        self.assertEqual(len(report["candidates"]), 1)
        candidate = report["candidates"][0]
        self.fresh(candidate)
        for room_id, descriptors in self.catalog.items():
            self.assertEqual(candidate["candidate_ids"][room_id], program_fixture._digest(descriptors[0]))
        self.scoped(report)
        report["source_frames"]["building"]["layout"]["rooms"][0]["required_adjacency"] = []
        report["source_frames"]["production_program"]["sources"][0]["content"] = "Edited detached report"
        report["source_frames"]["room_catalog"]["molding"][0]["x"] = 777
        self.assertEqual((self.program, self.building, self.catalog), before)

    def test_whole_embedded_float_raw_polygon_and_unknown_provenance_keep_separate_source_identities(self):
        integer = self.generate()
        self.program["operations"][0]["measurements"]["cycle_time"]["value"] = 30.0
        self.program["sources"][0]["usage_rights"]["state"] = "UNKNOWN"
        self.building["production_program"] = copy.deepcopy(self.program)
        self.catalog["molding"][0]["x"] = 0
        self.catalog["assembly"] = [{"kind": "orthogonal", "units": "mm", "vertices": [
            [4000.0, 0.0], [8000.0, 0.0], [8000.0, 3000.0], [4000.0, 3000.0], [4000.0, 1800.0], [4000.0, 900.0]]}]
        report = self.generate()
        self.assertEqual((report["status"], report["generation_status"]), ("UNKNOWN", "PASS"))
        self.assertEqual(report["source"], self.identity())
        self.assertNotEqual(report["source"]["building_sha256"], report["source"]["program_base_building_sha256"])
        self.assertNotEqual(report["source"]["production_program_sha256"], integer["source"]["production_program_sha256"])
        self.assertEqual(report["source_frames"]["building"]["production_program"], self.program)
        self.assertEqual(report["source_frames"]["room_catalog"], self.catalog)
        self.assertIs(type(report["source_frames"]["production_program"]["operations"][0]["measurements"]["cycle_time"]["value"]), float)
        self.assertIs(type(report["source_frames"]["room_catalog"]["molding"][0]["x"]), int)
        self.assertIn("\r\n", report["source_frames"]["production_program"]["sources"][0]["content"])
        proof = self.fresh(report["candidates"][0])
        self.assertEqual(proof["provenance_status"], "UNKNOWN")
        self.assertEqual(report["candidates"][0]["candidate_ids"]["assembly"], program_fixture._digest(self.catalog["assembly"][0]))
        self.scoped(report)

    def test_unsupported_original_frames_empty_requirements_and_source_contradictions_stop_before_cp(self):
        with self.no_cp():
            empty = copy.deepcopy(self.program)
            empty["requirements"] = []
            report = self.generate(program=empty)
            self.assertEqual(report["status"], "UNKNOWN")
            self.assertEqual(report["candidates"], [])
            self.assertNotEqual(report["generation_status"], "PASS")
            self.scoped(report)
            unsupported = copy.deepcopy(self.building)
            unsupported["layout"]["rooms"][0].pop("is_heated")
            supplied = copy.deepcopy(self.program)
            supplied["building_sha256"] = program_fixture._digest(unsupported)
            report = self.generate(program=supplied, building=unsupported)
            self.assertEqual(report["status"], "UNKNOWN", "A valid raw frame outside canonical roundtrip cannot be rewritten into matching authority")
            self.assertEqual(report["candidates"], [])
            self.assertEqual(report["source"]["building_sha256"], program_fixture._digest(unsupported))
            contradictory = copy.deepcopy(self.program)
            contradictory["requirements"][0]["room_b"] = "waste"
            self.rejected(program=contradictory)
            malformed = copy.deepcopy(self.program)
            malformed["requirements"][0]["operation_id"] = "absent"
            self.rejected(program=malformed)
            for missing, extra in ((True, False), (False, True)):
                invalid = copy.deepcopy(self.catalog)
                if missing:
                    invalid.pop("waste")
                if extra:
                    invalid["unknown"] = copy.deepcopy(invalid["waste"])
                self.rejected(catalog=invalid)
            for field, value in (("width", True), ("trusted_geometry", True)):
                invalid = copy.deepcopy(self.catalog)
                invalid["molding"][0][field] = value
                self.rejected(catalog=invalid)

    def test_binding_and_whole_embedded_type_failures_dominate_underflow_before_any_constructor(self):
        tiny = copy.deepcopy(self.catalog)
        tiny["molding"] = [{"kind": "orthogonal", "units": "mm", "vertices": [
            [0.0, 0.0], [1e-200, 0.0], [1e-200, 1e-200], [0.0, 1e-200]]}]
        stale = copy.deepcopy(self.program)
        stale["building_sha256"] = "0" * 64
        embedded = copy.deepcopy(self.building)
        embedded["production_program"] = copy.deepcopy(self.program)
        floating = copy.deepcopy(self.program)
        floating["operations"][0]["measurements"]["cycle_time"]["value"] = 30.0
        with self.no_constructors_or_cp():
            for program, building, expected_code in ((stale, self.building, "stale_building_binding"),
                    (floating, embedded, "embedded_production_program_mismatch")):
                with self.subTest(expected_code=expected_code):
                    report = self.generate(program=program, building=building, catalog=tiny)
                    self.assertEqual(report["status"], "FAIL")
                    self.assertEqual(report["candidates"], [])
                    self.assertIn(expected_code, {row.get("code") for row in rows(report)})
            report = self.generate(catalog=tiny)
            self.assertEqual(report["status"], "UNKNOWN")
            self.assertEqual(report["candidates"], [])
            self.assertNotEqual(report["generation_status"], "PASS")
            self.scoped(report)

    def test_shared_utf_nodes_geometry_vertices_and_cp_model_work_stop_before_any_constructor(self):
        defaults = self.api.ProductionLayoutGenerationLimits()
        parts = (self.program, self.building, self.catalog)
        byte_cap = max(len(json_bytes(value)) for value in parts) + 1
        node_cap = max(json_nodes(value) for value in parts) + 1
        self.assertGreater(sum(len(json_bytes(value)) for value in parts), byte_cap)
        self.assertGreater(sum(json_nodes(value) for value in parts), node_cap)
        catalog_vertices = sum(4 for choices in self.catalog.values() for _ in choices)
        boundary_vertices = 4 * (1 + len(self.building["layout"]["boundary"]["cutouts"]))
        self.assertLess(catalog_vertices, 15)
        self.assertLess(boundary_vertices, 15)
        self.assertGreater(catalog_vertices + boundary_vertices, 15)
        program, building, catalog = production_threshold_fixture()
        catalog["molding"].append({**catalog["molding"][0], "y": 100.0})
        catalog["assembly"].append({**catalog["assembly"][0], "y": 1500.0})
        preferred_building = copy.deepcopy(building)
        preferred_building["layout"]["rooms"][0]["preferred_adjacency"] = ["assembly"]
        preferred_program = copy.deepcopy(program)
        preferred_program["building_sha256"] = program_fixture._digest(preferred_building)
        fresh = validate_production_program_layout(program, building_raw=building, layout_raw=layout_for(catalog))
        self.assertEqual(fresh["geometry_status"], "PASS")
        self.assertEqual(fresh["counts"]["geometry_work_upper_bound"], 112)
        self.assertLessEqual(112, 150)
        self.assertGreater(2 * 112, 150)
        cases = (
            ("joint_utf8", self.program, self.building, self.catalog, replace(defaults, max_total_utf8_bytes=byte_cap)),
            ("joint_nodes", self.program, self.building, self.catalog, replace(defaults, max_nodes=node_cap)),
            ("joint_vertices", self.program, self.building, self.catalog, replace(defaults, max_vertices=15)),
            ("two_fresh_production_reviews", program, building, catalog, replace(defaults, max_pair_checks=150, max_attempts=2)),
            ("total_catalog_candidates", program, building, catalog, replace(defaults, max_candidates=3)),
            ("per_room_catalog_candidates", program, building, catalog, replace(defaults, max_candidates_per_room=1)),
            ("all_candidate_variables", program, building, catalog, replace(defaults, max_cp_variables=3)),
            ("preferred_support_variables", preferred_program, preferred_building, catalog, replace(defaults, max_cp_variables=7)),
            ("preferred_support_constraints", preferred_program, preferred_building, catalog, replace(defaults, max_cp_constraints=10)),
            ("all_objective_terms", program, building, catalog, replace(defaults, max_cp_objective_terms=3)),
        )
        for name, source, frame, choices, limit in cases:
            with self.subTest(bound=name), self.no_constructors_or_cp():
                report = self.generate(program=source, building=frame, catalog=choices, limits=limit)
                self.assertEqual(report["status"], "UNKNOWN")
                self.assertEqual(report["candidates"], [])
                self.assertNotEqual(report["generation_status"], "PASS")
                self.scoped(report)

    def test_exact_contact_then_epsilon_rejection_retries_a_new_combination_with_original_requirement_witness(self):
        program, building, catalog = production_threshold_fixture()
        exact = self.generate(program=program, building=building, catalog=catalog)
        self.assertEqual(exact["generation_status"], "PASS")
        self.assertEqual(self.fresh(exact["candidates"][0], program, building)["requirements"][0]["maximum_contiguous_mm"], 1600.0)
        program, building, catalog = production_threshold_fixture(epsilon=True)
        bad = copy.deepcopy(catalog["molding"][0])
        good = {**bad, "y": 100.0}
        catalog["molding"].append(good)
        report = self.generate(program=program, building=building, catalog=catalog)
        self.assertEqual(report["generation_status"], "PASS")
        self.assertEqual(len(report["candidates"]), 1)
        self.assertGreaterEqual(report["attempts"], 2, "The actual epsilon-invalid selected combination must be excluded and retried")
        candidate = report["candidates"][0]
        self.assertEqual(candidate["candidate_ids"]["molding"], program_fixture._digest(good))
        proof = self.fresh(candidate, program, building)
        self.assertAlmostEqual(proof["requirements"][0]["maximum_contiguous_mm"], 1699.9999995, delta=1e-7)
        failures = [row for row in rows(report.get("rejections", [])) if row.get("code") == "required_room_adjacency_failed"]
        self.assertTrue(failures, "The originating exact failed contact must survive retry delivery")
        witness = failures[0]
        self.assertEqual((witness["requirement_id"], witness["requirement_sha256"], witness["source_id"], witness["source_sha256"]),
            ("near_assembly", program_fixture._digest(program["requirements"][0]), "brief", program["sources"][0]["sha256"]))
        self.assertAlmostEqual(witness["maximum_contiguous_mm"], 1599.9999995, delta=1e-7)
        self.assertEqual(witness["required_contiguous_mm"], 1600.0)
        only_bad = copy.deepcopy(catalog)
        only_bad["molding"] = [bad]
        exhausted = self.generate(program=program, building=building, catalog=only_bad)
        self.assertEqual(exhausted["status"], "UNKNOWN")
        self.assertEqual(exhausted["candidates"], [])
        self.scoped(exhausted)
        one_attempt = self.generate(program=program, building=building, catalog=catalog,
                                   limits=replace(self.api.ProductionLayoutGenerationLimits(), max_attempts=1))
        self.assertEqual(one_attempt["status"], "UNKNOWN")
        self.assertEqual(one_attempt["candidates"], [])
        self.scoped(one_attempt)

    def test_forbidden_corner_is_legal_but_positive_span_and_overlap_cannot_be_accepted_or_rewrite_source(self):
        source = copy.deepcopy(self.building)
        clear = self.generate()
        self.assertEqual(clear["generation_status"], "PASS")
        self.assertEqual(self.fresh(clear["candidates"][0])["requirements"][1]["maximum_contiguous_mm"], 0)
        for name, change, failure_code in (("positive_span", {"x": 3900.0}, "forbidden_room_adjacency_failed"),
                                          ("overlap", {"y": 2900.0}, "room_overlap")):
            invalid = copy.deepcopy(self.catalog)
            invalid["waste"][0].update(change)
            independent = validate_production_program_layout(self.program, building_raw=self.building, layout_raw=layout_for(invalid))
            self.assertEqual(independent["geometry_status"], "FAIL")
            self.assertIn(failure_code, {row["code"] for row in independent["issues"]})
            with self.subTest(candidate=name):
                rejected = self.generate(catalog=invalid)
                self.assertEqual(rejected["status"], "UNKNOWN")
                self.assertEqual(rejected["candidates"], [])
                self.assertEqual(rejected["source_frames"]["building"], source)
                self.assertFalse(rejected["proven_building_infeasible"])
        # Requesting two variants from one supplied combination may retain one
        # checked candidate, but cannot pretend the requested set was complete.
        finite = self.generate(variants=2)
        self.assertEqual((finite["status"], finite["generation_status"]), ("UNKNOWN", "UNKNOWN"))
        self.assertEqual(len(finite["candidates"]), 1)
        self.fresh(finite["candidates"][0])
        self.scoped(finite)
        self.assertEqual(self.building, source)
        program, building, catalog = production_disjoint_contact_fixture()
        split = self.generate(program=program, building=building, catalog=catalog)
        self.assertEqual(split["status"], "UNKNOWN")
        self.assertEqual(split["candidates"], [])
        witnesses = [row for row in rows(split) if row.get("requirement_id") == "near_assembly"
                     and row.get("maximum_contiguous_mm") == 800.0 and row.get("required_contiguous_mm") == 1600.0]
        self.assertTrue(witnesses, "Unknown multiple-door-edge selection must not erase the known noncontiguous production failure")
        self.scoped(split)

    def test_closed_tight_limits_controls_and_old_solver_or_facility_capabilities_stay_unchanged(self):
        defaults = self.api.ProductionLayoutGenerationLimits()
        self.assertIsInstance(defaults, ProductionLayoutLimits)
        extra = {"max_candidates_per_room", "max_candidates", "max_attempts", "max_cp_variables", "max_cp_constraints",
                 "max_cp_objective_terms", "dimension_policy"}
        self.assertEqual({row.name for row in fields(defaults)}, {row.name for row in fields(ProductionLayoutLimits())} | extra)
        self.assertIs(type(defaults.dimension_policy), OrthogonalDimensionPolicy)
        cases = ({"variants": True}, {"variants": 0}, {"seed": True}, {"seed": -1},
            {"time_limit_seconds": float("nan")}, {"time_limit_seconds": True}, {"time_limit_seconds": 0},
            {"deterministic_units": float("inf")}, {"deterministic_units": True}, {"deterministic_units": 0},
            {"include_windows": 1}, {"limits": {"max_attempts": 1, "unexpected": 1}})
        with self.no_constructors_or_cp():
            for options in cases:
                with self.subTest(options=options):
                    self.rejected(**options)
            for name, value in (("max_cp_variables", True), ("max_attempts", 0),
                    ("max_candidates_per_room", defaults.max_candidates_per_room + 1),
                    ("dimension_policy", {"max_scan_steps": 1, "max_candidates": 1}),
                    ("dimension_policy", OrthogonalDimensionPolicy(max_scan_steps=100001))):
                with self.subTest(limit=name, value=value):
                    try:
                        malformed = replace(defaults, **{name: value})
                    except ValueError:
                        continue
                    self.rejected(limits=malformed)
        signature = inspect.signature(original_solver.solve_layouts)
        self.assertEqual(list(signature.parameters), ["spec", "variants", "time_limit_seconds", "seed", "fixed_rects",
            "axis_aligned_room_ids", "structural_axes_x_mm", "structural_axes_y_mm", "required_adjacency_groups",
            "forbidden_adjacency_groups", "minimum_adjacency_mm", "equipment_fit_options", "deterministic_units",
            "boundary_side_contacts", "hint_rects", "first_solution_only", "preferred_adjacency_groups", "statistics"])
        self.assertEqual(list(inspect.signature(original_orthogonal.solve_orthogonal_candidates).parameters),
            ["spec", "catalog", "fixed_footprints", "variants", "seed", "time_limit_seconds",
             "deterministic_units", "budget", "include_windows"])
        embedded = copy.deepcopy(self.building)
        embedded["production_program"] = copy.deepcopy(self.program)
        with self.assertRaisesRegex(ValueError, "explicit source-only validation admission"):
            BuildingIR.from_mapping(embedded)
        admitted = BuildingIR.from_mapping(embedded, include_production_program=True)
        with self.no_cp(), self.assertRaises(original_facility.InfeasibleFacilityGeneration) as failure:
            original_facility.solve_feasible_facility_variants(admitted, None, variants=1, time_limit_seconds=1, seed=42)
        self.assertEqual(failure.exception.generation.to_dict()["failure_evidence"][0]["code"], "unsupported_production_program")


if __name__ == "__main__":
    unittest.main()

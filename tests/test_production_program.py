"""Source identity and declared requirements, without geometry or approval."""

import copy
from dataclasses import replace
import hashlib
import importlib
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from layout_configurator.building import BuildingIR


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                    allow_nan=False, separators=(",", ":")).encode("utf-8")).hexdigest()


def _building():
    return BuildingIR.from_mapping({"layout": {
        "project_name": "Source-bound process fixture",
        "boundary": {"width": 9000, "height": 6000}, "entry_room": "molding",
        "rooms": [
            {"id": "molding", "target_area": 12, "required_adjacency": ["assembly"],
             "forbidden_adjacency": ["waste"]},
            {"id": "assembly", "target_area": 12}, {"id": "waste", "target_area": 8}],
    }, "equipment": [{"id": "press", "room_id": "molding", "width_mm": 1000, "depth_mm": 800}],
        "flows": [{"id": "parts", "type": "material", "from_ids": ["press"], "to_ids": ["assembly"]}]})


def _known(unit, value=1):
    return {"status": "KNOWN", "value": value, "unit": unit, "basis": "declared_fact",
            "source_id": "brief", "note": "Supplied project value; not a measured characteristic."}


def _program(building):
    content = "Project brief edition 1.\r\nMolding and assembly must be adjacent.\r\nMolding and waste must not be adjacent.\r\nDeclared cycle: 30 s; throughput: 120 items/hour; movements: 12 movements/hour; operators: 1.\r\n"
    measurements = {"cycle_time": _known("s", 30), "target_throughput": _known("items/hour", 120),
                    "movement_frequency": _known("movements/hour", 12), "operators": _known("persons", 1)}
    return {"version": "0.1", "id": "parts_program", "revision": "P01",
            "building_sha256": _digest(building.to_dict()), "origin": "supplied_project_program",
            "sources": [{"id": "brief", "kind": "functional_diagram", "title": "Supplied process brief",
                         "edition": "1", "uri": "urn:project:process-brief:1", "locator": "Lines 1-4",
                         "content": content, "sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
                         "usage_rights": {"state": "PERMITTED", "note": "Supplied for this project review."}}],
            "materials": [{"id": "parts", "label": "Polymer components"}],
            "states": [{"id": state, "material_id": "parts", "label": state} for state in ("raw", "formed", "assembled")],
            "operations": [
                {"id": "mold", "label": "Molding", "room_id": "molding", "equipment_ids": ["press"],
                 "flow_ids": ["parts"], "inputs": ["raw"], "outputs": ["formed"], "measurements": copy.deepcopy(measurements)},
                {"id": "assemble", "label": "Assembly", "room_id": "assembly", "equipment_ids": [],
                 "flow_ids": ["parts"], "inputs": ["formed"], "outputs": ["assembled"], "measurements": copy.deepcopy(measurements)}],
            "requirements": [
                {"id": "near_assembly", "operation_id": "mold", "kind": "required_room_adjacency",
                 "room_a": "molding", "room_b": "assembly", "reason": "Direct component handover",
                 "source_id": "brief", "basis": "declared_fact", "excerpt": "Molding and assembly must be adjacent."},
                {"id": "separate_waste", "operation_id": "mold", "kind": "forbidden_room_adjacency",
                 "room_a": "molding", "room_b": "waste", "reason": "Declared waste separation",
                 "source_id": "brief", "basis": "declared_fact", "excerpt": "Molding and waste must not be adjacent."}],
            "engineer_confirmation": None}


class ProductionProgramTests(unittest.TestCase):
    def setUp(self):
        # A missing implementation is an explicit behavior failure, not a collection error.
        try:
            self.api = importlib.import_module("layout_configurator.production_program")
        except ModuleNotFoundError as exc:
            self.fail(f"The source-bound production-program validator is missing: {exc}")
        self.building = _building()
        self.raw = _program(self.building)

    def validate(self, raw=None, **kwargs):
        return self.api.validate_production_program(self.raw if raw is None else raw,
                                                    building=self.building, **kwargs)

    def test_valid_contract_preserves_source_bytes_scalars_and_detached_roundtrip(self):
        self.raw["operations"][0]["measurements"]["cycle_time"]["value"] = 30.0
        before = copy.deepcopy(self.raw)
        original = self.building.to_dict()
        result = self.validate()
        self.assertEqual((result.status, result.schema_status, result.binding_status, result.evidence_status),
                         ("PASS", "PASS", "PASS", "PASS"))
        self.assertEqual(result.program.to_dict(), before)
        self.assertIs(type(result.program.to_dict()["operations"][0]["measurements"]["cycle_time"]["value"]), float)
        detached = result.program.to_dict()
        detached["sources"][0]["content"] = "edited"
        self.raw["sources"][0]["content"] = "edited"
        self.assertEqual(result.program.to_dict(), before)
        self.assertEqual(self.building.to_dict(), original)
        self.assertEqual(result.to_dict()["engineering_acceptance"], "PENDING")
        self.assertEqual(result.to_dict()["authenticated_approval"], "UNKNOWN")

    def test_unknown_loads_assumptions_and_rights_remain_explicit_unknown(self):
        metric = self.raw["operations"][0]["measurements"]["target_throughput"]
        metric.update(status="UNKNOWN", value=None, source_id=None, note="No cycle study supplied.")
        self.raw["requirements"][0]["basis"] = "assumption"
        self.raw["sources"][0]["usage_rights"]["state"] = "UNKNOWN"
        result = self.validate()
        self.assertEqual((result.status, result.schema_status, result.binding_status, result.evidence_status),
                         ("UNKNOWN", "PASS", "PASS", "UNKNOWN"))
        self.assertEqual(result.program.to_dict()["operations"][0]["measurements"]["target_throughput"]["value"], None)
        self.assertTrue({"unknown_measurement", "assumed_requirement", "unknown_usage_rights"} <=
                        {issue.code for issue in result.issues})

    def test_missing_or_contradictory_source_relations_report_requirement_and_source_ids(self):
        for change, code in (("missing", "missing_adjacency_declaration"), ("opposite", "contradictory_adjacency_declaration")):
            with self.subTest(change=change):
                self.raw = _program(self.building)
                self.raw["requirements"][0]["room_b"] = "waste" if change == "opposite" else "assembly"
                building = self.building
                if change == "missing":
                    building = replace(building, layout=replace(building.layout, rooms=(
                        replace(building.layout.rooms[0], required_adjacency=()), *building.layout.rooms[1:])))
                self.raw["building_sha256"] = _digest(building.to_dict())
                result = self.api.validate_production_program(self.raw, building=building)
                self.assertEqual((result.status, result.schema_status, result.binding_status, result.evidence_status),
                                 ("FAIL", "PASS", "PASS", "FAIL"))
                issue = next(issue for issue in result.issues if issue.code == code)
                self.assertEqual((issue.requirement_id, issue.source_id), ("near_assembly", "brief"))

    def test_machine_edit_invalidates_original_canonical_binding(self):
        changed = replace(self.building, equipment=(replace(self.building.equipment[0], width_mm=1200),))
        result = self.api.validate_production_program(self.raw, building=changed)
        self.assertEqual((result.status, result.binding_status), ("FAIL", "FAIL"))
        self.assertIsNone(result.program)
        self.assertIn("stale_building_binding", {issue.code for issue in result.issues})

    def test_source_tampering_and_false_excerpt_are_rejected(self):
        for change, code in (("bytes", "source_hash_mismatch"), ("excerpt", "source_excerpt_mismatch")):
            with self.subTest(change=change):
                raw = copy.deepcopy(self.raw)
                if change == "bytes":
                    raw["sources"][0]["content"] = raw["sources"][0]["content"].replace("\r\n", "\n")
                else:
                    raw["requirements"][0]["excerpt"] = "The engineer approved this facility."
                result = self.validate(raw)
                self.assertEqual((result.status, result.binding_status), ("FAIL", "FAIL"))
                self.assertIn(code, {issue.code for issue in result.issues})

    def test_unknown_typed_references_and_duplicate_ids_are_rejected(self):
        cases = (("operation_room", lambda r: r["operations"][0].update(room_id="absent")),
                 ("equipment", lambda r: r["operations"][0].update(equipment_ids=["assembly"])),
                 ("flow", lambda r: r["operations"][0].update(flow_ids=["press"])),
                 ("state", lambda r: r["operations"][0].update(inputs=["absent"])),
                 ("material", lambda r: r["states"][0].update(material_id="absent")),
                 ("operation", lambda r: r["requirements"][0].update(operation_id="absent")),
                 ("source", lambda r: r["requirements"][0].update(source_id="absent")),
                 ("duplicate", lambda r: r["states"].append(copy.deepcopy(r["states"][0]))))
        for name, change in cases:
            with self.subTest(name=name):
                raw = copy.deepcopy(self.raw)
                change(raw)
                result = self.validate(raw)
                self.assertEqual((result.status, result.binding_status), ("FAIL", "FAIL"))
                self.assertIsNone(result.program)
        # Material and canonical flow IDs already both equal "parts": namespaces stay distinct.
        self.assertEqual(self.validate().status, "PASS")

    def test_equipment_binding_must_belong_to_the_declared_operation_room(self):
        self.raw["operations"][1]["equipment_ids"] = ["press"]
        result = self.validate()
        self.assertEqual(result.status, "FAIL")
        self.assertIn("equipment_room_mismatch", {issue.code for issue in result.issues})

    def test_raw_numerics_units_unknown_values_and_nested_fields_are_strict(self):
        changes = [lambda r, v=v: r["operations"][0]["measurements"]["cycle_time"].update(value=v)
                   for v in (True, "30", float("nan"), float("inf"), 10 ** 10000, -1, 0)]
        changes += [lambda r: r["operations"][0]["measurements"]["cycle_time"].update(unit="items/hour"),
                    lambda r: r["operations"][0]["measurements"]["cycle_time"].update(status="UNKNOWN", value=30),
                    lambda r: r["operations"][0]["measurements"]["operators"].update(value=1.5),
                    lambda r: r["sources"][0].update(authenticated=True),
                    lambda r: r["operations"][0].update(placements={"x": 0}),
                    lambda r: r.update(version="2.0")]
        for number, change in enumerate(changes):
            with self.subTest(number=number):
                raw = copy.deepcopy(self.raw)
                change(raw)
                result = self.validate(raw)
                self.assertEqual(result.status, "FAIL")
                self.assertIsNone(result.program)

    def test_count_source_utf8_and_total_budgets_reject_before_acceptance(self):
        for kwargs, change in (({"max_operations": 1}, lambda r: None),
                               ({"max_source_bytes": 8}, lambda r: r["sources"][0].update(content="é" * 5)),
                               ({"max_total_source_bytes": 8}, lambda r: None),
                               ({"max_total_utf8_bytes": 100}, lambda r: None),
                               ({"max_nodes": 10}, lambda r: None)):
            with self.subTest(kwargs=kwargs):
                raw = copy.deepcopy(self.raw)
                change(raw)
                result = self.validate(raw, limits=self.api.ProductionProgramLimits(**kwargs))
                self.assertEqual((result.status, result.schema_status), ("FAIL", "NOT_EVALUATED"))
                self.assertIsNone(result.program)
                self.assertIn("input_budget_exceeded", {issue.code for issue in result.issues})
        for value in (True, 1.5, "2", 0):
            with self.subTest(limit=value):
                result = self.validate(limits=self.api.ProductionProgramLimits(max_operations=value))
                self.assertEqual(result.status, "FAIL")
                self.assertIn("invalid_limits", {issue.code for issue in result.issues})

    def test_supplied_engineer_attribution_is_bound_but_never_authenticated_approval(self):
        source = self.raw["sources"][0]
        self.raw["engineer_confirmation"] = {
            "author": "Supplied process engineer", "date": "2026-10-13", "revision": "P01",
            "source_id": "brief", "source_sha256": source["sha256"], "statement": "Supplied discussion record."}
        self.raw["requirements"][0]["basis"] = "supplied_confirmation"
        result = self.validate()
        self.assertEqual(result.status, "PASS")
        self.assertEqual(result.to_dict()["engineering_acceptance"], "PENDING")
        self.assertEqual(result.to_dict()["authenticated_approval"], "UNKNOWN")
        self.raw["engineer_confirmation"]["revision"] = "P00"
        self.assertIn("stale_confirmation", {issue.code for issue in self.validate().issues})

    def test_confirmation_cannot_be_reused_for_measurement_and_requirement_from_another_source(self):
        second = copy.deepcopy(self.raw["sources"][0])
        second["id"] = "second_brief"
        self.raw["sources"].append(second)
        self.raw["engineer_confirmation"] = {
            "author": "Supplied reviewer", "date": "2026-10-13", "revision": "P01",
            "source_id": "brief", "source_sha256": self.raw["sources"][0]["sha256"],
            "statement": "Supplied source attribution, not authenticated approval."}
        self.raw["operations"][0]["measurements"]["cycle_time"].update(
            basis="supplied_confirmation", source_id="second_brief")
        self.raw["requirements"][0].update(basis="supplied_confirmation", source_id="second_brief")
        result = self.validate()
        self.assertEqual((result.status, result.schema_status, result.binding_status, result.evidence_status),
                         ("UNKNOWN", "PASS", "PASS", "UNKNOWN"))
        mismatches = [issue for issue in result.issues if issue.code == "mismatched_confirmation"]
        self.assertEqual(len(mismatches), 2)
        self.assertEqual({issue.source_id for issue in mismatches}, {"second_brief"})
        self.assertEqual({issue.requirement_id for issue in mismatches}, {None, "near_assembly"})
        self.assertTrue(all(issue.operation_id == "mold" for issue in mismatches))
        self.assertEqual(result.to_dict()["authenticated_approval"], "UNKNOWN")
        self.assertEqual(result.to_dict()["engineering_acceptance"], "PENDING")
        # Even identical document content/hash does not merge distinct source identities.
        self.raw["engineer_confirmation"].update(source_id="second_brief", source_sha256=second["sha256"])
        matching = self.validate()
        self.assertEqual(matching.status, "PASS")
        self.assertEqual(matching.to_dict()["authenticated_approval"], "UNKNOWN")
        self.assertEqual(matching.to_dict()["engineering_acceptance"], "PENDING")

    def test_declared_flow_must_touch_the_operation_room_through_real_endpoints(self):
        self.raw["operations"][1]["room_id"] = "waste"
        result = self.validate()
        self.assertEqual(result.status, "FAIL")
        issue = next(issue for issue in result.issues if issue.code == "flow_room_mismatch")
        self.assertEqual(issue.operation_id, "assemble")

    def test_reference_budget_has_no_partial_pass(self):
        result = self.validate(limits=self.api.ProductionProgramLimits(max_references=5))
        self.assertEqual((result.status, result.schema_status), ("FAIL", "NOT_EVALUATED"))
        self.assertIn("input_budget_exceeded", {issue.code for issue in result.issues})

    def test_combined_canonical_bytes_have_no_partial_pass(self):
        # A limit sufficient for either document separately, but insufficient for both.
        limit = len(json.dumps(self.raw, sort_keys=True, ensure_ascii=False,
                               separators=(",", ":")).encode("utf-8")) + 10
        result = self.validate(limits=self.api.ProductionProgramLimits(max_total_utf8_bytes=limit))
        self.assertEqual((result.status, result.binding_status), ("FAIL", "FAIL"))
        self.assertIsNone(result.program)
        self.assertIn("input_budget_exceeded", {issue.code for issue in result.issues})

    def test_explicit_unknown_does_not_require_a_fabricated_confirmation(self):
        self.raw["requirements"][0]["basis"] = "supplied_confirmation"
        result = self.validate()
        self.assertEqual(result.status, "UNKNOWN")
        issue = next(issue for issue in result.issues if issue.code == "missing_confirmation")
        self.assertEqual((issue.requirement_id, issue.source_id), ("near_assembly", "brief"))

    def test_reconstruction_example_is_source_bound_and_truthfully_incomplete(self):
        from layout_configurator.io import load_building

        path = Path(__file__).resolve().parents[1] / "examples/production_program_reconstruction.json"
        self.assertTrue(path.is_file(), "The explicit reconstruction example is missing")
        raw = json.loads(path.read_text(encoding="utf-8"))
        building = load_building("examples/pharma_cleanroom_pilot.yaml")
        result = self.api.validate_production_program(raw, building=building)
        self.assertEqual((result.schema_status, result.binding_status, result.status), ("PASS", "PASS", "UNKNOWN"))
        self.assertEqual(raw["origin"], "reconstruction")
        self.assertIn("unknown_measurement", {issue.code for issue in result.issues})
        self.assertEqual(result.to_dict()["engineering_acceptance"], "PENDING")

    def test_building_opening_records_are_bounded_before_canonical_serialization(self):
        from layout_configurator.models import WindowSpec

        windows = tuple(WindowSpec(f"window_{index}", "molding", "top", index * 1000, 500)
                        for index in range(2))
        building = replace(self.building, layout=replace(self.building.layout, windows=windows))
        raw = _program(building)
        # Canonical serialization allocates one record per opening; it must not
        # run once the cheap record count has already exceeded the budget.
        with patch.object(BuildingIR, "to_dict", side_effect=AssertionError("Serialization happened before the object gate")):
            result = self.api.validate_production_program(raw, building=building,
                                                         limits=self.api.ProductionProgramLimits(max_building_objects=6))
        self.assertEqual((result.status, result.binding_status), ("FAIL", "FAIL"))
        self.assertIn("input_budget_exceeded", {issue.code for issue in result.issues})

    def test_original_canonical_numeric_scalars_are_part_of_identity(self):
        building = replace(self.building, equipment=(replace(self.building.equipment[0], width_mm=1000),))
        result = self.api.validate_production_program(self.raw, building=building)
        self.assertEqual(result.status, "FAIL")
        self.assertIn("stale_building_binding", {issue.code for issue in result.issues})


if __name__ == "__main__":
    unittest.main()

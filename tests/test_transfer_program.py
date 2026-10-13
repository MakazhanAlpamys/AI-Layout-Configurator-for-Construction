"""Typed transfer declarations bind existing sources without engineering approval."""

import copy
from dataclasses import replace
import hashlib
import importlib
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from layout_configurator.building import BuildingIR
from layout_configurator.production_program import validate_production_program


def _sha(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                    allow_nan=False, separators=(",", ":")).encode("utf-8")).hexdigest()


def case():
    building = BuildingIR.from_mapping({"layout": {
        "project_name": "Synthetic transfer permission fixture", "boundary": {"width": 6000, "height": 3000},
        "entry_room": "source", "wall_thickness_mm": 200,
        "rooms": [{"id": "source", "target_area": 9, "required_adjacency": ["target"]},
                  {"id": "target", "target_area": 9}],
        "doors": [{"id": "passbox_portal", "room_a": "source", "room_b": "target", "offset": 1500, "width": 900}]},
        "flows": [{"id": "movement", "type": "people", "from_ids": ["source"], "to_ids": ["target"],
                   "minimum_clear_width_mm": 800}]})
    content = ("Synthetic software test declaration, not an approved manufacturing design.\r\n"
               "The movement flow is explicitly classified as personnel for the rejection exercise.\r\n"
               "The passbox_portal permits material only, from source to target, with at least 800 mm clear width.\r\n"
               "The paired material exercise uses the same source geometry.\r\n"
               "The paired door exercise permits personnel from source to target.\r\n"
               "No chamber, interlock, HVAC or three-dimensional loading evidence is supplied.\r\n")
    measurement = lambda unit, value: {"status": "KNOWN", "value": value, "unit": unit,
        "basis": "declared_fact", "source_id": "transfer_brief", "note": "Declared test value; no capacity simulation."}
    production = {"version": "0.1", "id": "transfer_process", "revision": "P01", "origin": "supplied_project_program",
        "building_sha256": _sha(building.to_dict()),
        "sources": [{"id": "transfer_brief", "kind": "functional_diagram", "title": "Synthetic transfer test declaration",
                     "edition": "1", "uri": "urn:fixture:transfer:1", "locator": "Lines 1-6", "content": content,
                     "sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
                     "usage_rights": {"state": "PERMITTED", "note": "Supplied software test declaration."}}],
        "materials": [{"id": "parts", "label": "Test parts"}],
        "states": [{"id": "raw", "material_id": "parts", "label": "Raw test parts"}],
        "operations": [{"id": "handover", "label": "Synthetic handover", "room_id": "source", "equipment_ids": [],
                        "flow_ids": ["movement"], "inputs": ["raw"], "outputs": [], "measurements": {
                            "cycle_time": measurement("s", 30), "target_throughput": measurement("items/hour", 120),
                            "movement_frequency": measurement("movements/hour", 12), "operators": measurement("persons", 1)}}],
        "requirements": [], "engineer_confirmation": None}
    transfer = {"version": "0.1", "id": "transfer_policy", "revision": "T01", "units": "mm",
        "building_sha256": _sha(building.to_dict()), "production_program_sha256": _sha(production),
        "flow_movements": [{"flow_id": "movement", "movement_kind": "personnel", "source_id": "transfer_brief",
                            "basis": "declared_fact", "excerpt": "explicitly classified as personnel"}],
        "transfers": [{"id": "transfer_1", "kind": "pass_box", "opening_id": "passbox_portal",
                       "room_chain": ["source", "target"], "minimum_clear_width_mm": 800,
                       "permissions": [{"direction": "forward", "movement_kinds": ["material"]}],
                       "source_id": "transfer_brief", "basis": "declared_fact", "excerpt": "permits material only"}]}
    return building, production, transfer


def rebind(building, production, transfer):
    production["building_sha256"] = _sha(building.to_dict())
    transfer.update(building_sha256=_sha(building.to_dict()), production_program_sha256=_sha(production))


class TransferProgramTests(unittest.TestCase):
    def setUp(self):
        try:
            self.api = importlib.import_module("layout_configurator.transfer_program")
        except ModuleNotFoundError as exc:
            self.fail(f"Typed source-bound transfer contract is missing: {exc}")
        self.building, self.production, self.raw = case()

    def validate(self, **options):
        return self.api.validate_transfer_program(self.raw, building=self.building,
                                                 production_program=self.production, **options)

    def test_source_bound_contract_is_detached_and_roundtrips_exact_scalars(self):
        self.assertEqual(validate_production_program(self.production, building=self.building).status, "PASS")
        self.raw["transfers"][0]["minimum_clear_width_mm"] = 800.0
        original = copy.deepcopy(self.raw)
        report = self.validate()
        self.assertEqual((report.status, report.schema_status, report.binding_status, report.provenance_status),
                         ("PASS", "PASS", "PASS", "PASS"))
        self.assertEqual(report.program.to_dict(), original)
        self.assertIs(type(report.program.to_dict()["transfers"][0]["minimum_clear_width_mm"]), float)
        report.program.to_dict()["transfers"][0]["kind"] = "door"
        self.assertEqual(report.program.to_dict(), original)
        self.assertEqual(report.to_dict()["engineering_acceptance"], "PENDING")
        self.assertEqual(report.to_dict()["full_facility_validation"]["status"], "UNKNOWN")

    def test_stale_source_program_scalar_and_content_identities_fail(self):
        for edit in ("building", "production", "content", "scalar"):
            self.building, self.production, self.raw = case()
            if edit == "building":
                self.building = replace(self.building, version="edited")
            elif edit == "production":
                self.production["operations"][0]["label"] = "Changed handover"
            elif edit == "content":
                self.production["sources"][0]["content"] = "tampered"
                self.raw["production_program_sha256"] = _sha(self.production)
            else:
                self.raw["building_sha256"] = "0" * 64
            with self.subTest(edit=edit):
                self.assertEqual(self.validate().status, "FAIL")

    def test_unknown_rights_assumption_and_wrong_source_confirmation_cannot_pass(self):
        for edit in ("rights", "assumption", "confirmation"):
            self.building, self.production, self.raw = case()
            if edit == "rights":
                self.production["sources"][0]["usage_rights"]["state"] = "UNKNOWN"
            elif edit == "assumption":
                self.raw["transfers"][0]["basis"] = "assumption"
            else:
                second = copy.deepcopy(self.production["sources"][0])
                second["id"] = "other_brief"
                self.production["sources"].append(second)
                self.production["engineer_confirmation"] = {"author": "Supplied reviewer", "date": "2026-10-13",
                    "revision": "P01", "source_id": "other_brief", "source_sha256": second["sha256"],
                    "statement": "Supplied attribution; not authenticated approval."}
                self.raw["transfers"][0]["basis"] = "supplied_confirmation"
            rebind(self.building, self.production, self.raw)
            with self.subTest(edit=edit):
                report = self.validate()
                self.assertEqual(report.status, "UNKNOWN")
                self.assertEqual(report.provenance_status, "UNKNOWN")

    def test_missing_evidence_does_not_hide_forbidden_personnel_declaration(self):
        self.raw["transfers"][0]["permissions"][0]["movement_kinds"] = ["personnel"]
        report = self.api.validate_transfer_program(self.raw, building=self.building, production_program=None)
        self.assertEqual(report.status, "FAIL")
        self.assertIn("forbidden_transfer_movement", {issue.code for issue in report.issues})
        self.assertFalse(report.to_dict()["proven_building_infeasible"])

    def test_opening_room_flow_source_and_excerpt_references_are_strict(self):
        for edit in ("opening", "room", "flow", "source", "excerpt", "duplicate"):
            self.building, self.production, self.raw = case()
            if edit == "opening":
                self.raw["transfers"][0]["opening_id"] = "auto-source-target"
            elif edit == "room":
                self.raw["transfers"][0]["room_chain"] = ["source", "missing"]
            elif edit == "flow":
                self.raw["flow_movements"][0]["flow_id"] = "missing"
            elif edit == "source":
                self.raw["transfers"][0]["source_id"] = "missing"
            elif edit == "excerpt":
                self.raw["transfers"][0]["excerpt"] = "not in the verified document"
            else:
                self.raw["transfers"].append(copy.deepcopy(self.raw["transfers"][0]))
            with self.subTest(edit=edit):
                self.assertEqual(self.validate().status, "FAIL")

    def test_raw_numeric_closed_and_budget_gates_run_before_provenance_validation(self):
        for value in (True, "800", float("nan"), float("inf"), 0):
            self.building, self.production, self.raw = case()
            self.raw["transfers"][0]["minimum_clear_width_mm"] = value
            with self.subTest(value=value):
                self.assertEqual(self.validate().status, "FAIL")
        self.building, self.production, self.raw = case()
        self.raw["transfers"][0]["coordinates"] = [0, 0]
        self.assertEqual(self.validate().status, "FAIL")
        self.building, self.production, self.raw = case()
        limits = self.api.TransferProgramLimits(max_program_bytes=100)
        with patch("layout_configurator.transfer_program.validate_production_program",
                   side_effect=AssertionError("Raw request was normalized before its budget gate")):
            report = self.validate(limits=limits)
        self.assertEqual(report.status, "FAIL")
        self.assertIn("input_budget_exceeded", {issue.code for issue in report.issues})

    def test_declared_minimum_opening_width_conflict_is_source_bound(self):
        self.raw["transfers"][0]["minimum_clear_width_mm"] = 1000
        report = self.validate()
        self.assertEqual(report.status, "FAIL")
        issue = next(issue for issue in report.issues if issue.code == "source_opening_too_narrow")
        self.assertEqual((issue.transfer_id, issue.opening_id, issue.source_id),
                         ("transfer_1", "passbox_portal", "transfer_brief"))

    def test_detached_contracts_are_revalidated_with_duplicate_depth_and_node_gates(self):
        valid = self.validate().program
        self.assertEqual(self.api.validate_transfer_program(valid, building=self.building,
            production_program=self.production).status, "PASS")
        for text in ('{"version":"0.1","version":"0.1"}', '[' * 20 + '0' + ']' * 20):
            with self.subTest(text=text):
                report = self.api.validate_transfer_program(self.api.TransferProgramContract(text),
                    building=self.building, production_program=self.production)
                self.assertEqual(report.status, "FAIL")
        with patch("layout_configurator.transfer_program.json.loads",
                   side_effect=AssertionError("Manual contract parsed before its node budget gate")):
            report = self.api.validate_transfer_program(self.api.TransferProgramContract("[" + ",".join("0" for _ in range(50)) + "]"),
                building=self.building, production_program=self.production, limits=self.api.TransferProgramLimits(max_nodes=10))
        self.assertEqual(report.status, "FAIL")
        self.assertIn("input_budget_exceeded", {issue.code for issue in report.issues})

    def test_limits_and_oversized_model_are_rejected_before_source_serialization(self):
        for value in (True, 2.0, "2", 0):
            with self.subTest(value=value):
                self.assertEqual(self.validate(limits=self.api.TransferProgramLimits(max_rooms=value)).status, "FAIL")
        with patch.object(BuildingIR, "to_dict", side_effect=AssertionError("Source serialized after object overflow")):
            report = self.validate(limits=self.api.TransferProgramLimits(max_rooms=1))
        self.assertEqual(report.status, "FAIL")
        self.assertIn("input_budget_exceeded", {issue.code for issue in report.issues})

    def test_self_contained_example_keeps_exact_canonical_source_and_known_denial(self):
        path = Path(__file__).parents[1] / "examples/transfer_program_passbox.json"
        self.assertTrue(path.is_file(), "A source-bound reusable transfer example is missing")
        example = json.loads(path.read_text(encoding="utf-8"))
        building = BuildingIR.from_mapping(example["building"])
        self.assertEqual(building.to_dict(), example["building"])
        report = self.api.validate_transfer_program(example["transfer_program"], building=building,
            production_program=example["production_program"])
        self.assertEqual(report.status, "PASS")
        from layout_configurator.transfer_validation import validate_transfer_routes
        from layout_configurator.flows import route_flows
        from layout_configurator.result_geometry import parse_layout_result
        placements = parse_layout_result(example["layout"])
        routing = route_flows(building, placements)
        permissions = validate_transfer_routes(building, placements, routing, report.program,
            production_program=example["production_program"])
        self.assertEqual((permissions.geometry_status, permissions.permission_status), ("PASS", "FAIL"))

    def test_manual_source_duplicate_opening_ids_cannot_be_authority(self):
        duplicated = replace(self.building.layout.doors[0], offset_mm=1200)
        self.building = replace(self.building, layout=replace(self.building.layout,
            doors=(*self.building.layout.doors, duplicated)))
        rebind(self.building, self.production, self.raw)
        report = self.validate()
        self.assertEqual(report.status, "FAIL")
        self.assertIn("duplicate_source_id", {issue.code for issue in report.issues})

    def test_matching_confirmation_preserves_scoped_identity_without_authenticated_approval(self):
        source = self.production["sources"][0]
        self.production["engineer_confirmation"] = {"author": "Supplied reviewer", "date": "2026-10-13",
            "revision": "P01", "source_id": source["id"], "source_sha256": source["sha256"],
            "statement": "Supplied source attribution, not authenticated approval."}
        self.raw["transfers"][0]["basis"] = "supplied_confirmation"
        rebind(self.building, self.production, self.raw)
        report = self.validate()
        self.assertEqual(report.status, "PASS")
        self.assertEqual(report.to_dict()["authenticated_approval"], "UNKNOWN")

    def test_whole_source_binding_includes_embedded_program_without_rebinding_its_base(self):
        from layout_configurator.production_program import ProductionProgramContract
        self.building = replace(self.building, production_program=ProductionProgramContract(json.dumps(self.production)))
        # The production program keeps its original base hash; the transfer
        # contract binds the whole source with its embedded program included.
        self.raw["building_sha256"] = _sha(self.building.to_dict())
        report = self.validate()
        self.assertEqual(report.status, "PASS")
        other = copy.deepcopy(self.production)
        other["operations"][0]["label"] = "Changed embedded handover"
        self.production = other
        self.raw["production_program_sha256"] = _sha(other)
        self.assertEqual(self.validate().status, "FAIL")


if __name__ == "__main__":
    unittest.main()

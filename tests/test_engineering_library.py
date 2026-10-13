"""Independent structured source evidence and bounded finite equipment cards."""

import copy
from dataclasses import replace
import hashlib
import importlib
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from layout_configurator.building import BuildingIR, EquipmentSpec


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                    allow_nan=False, separators=(",", ":")).encode("utf-8")).hexdigest()


def fixture_spec():
    return EquipmentSpec("press", "project_press", "work", None, 1000.0, 800.0,
                         300.0, 100.0, 150.0, 200.0, True)


def source_content(raw, source):
    content = json.dumps(source, ensure_ascii=False, indent=2) + "\r\n"
    raw["sources"][0]["content"] = content
    raw["sources"][0]["sha256"] = hashlib.sha256(content.encode("utf-8")).hexdigest()


def library_fixture(spec=None):
    spec = spec or fixture_spec()
    values = {name: {"value": getattr(spec, name), "unit": "mm"}
              for name in ("width_mm", "depth_mm", "clearance_front_mm", "clearance_back_mm",
                           "clearance_left_mm", "clearance_right_mm")}
    rectangles = {
        "body": {"kind": "rectangle", "unit": "mm", "x": 0.0, "y": 0.0,
                 "width": spec.width_mm, "depth": spec.depth_mm},
        "operating": {"kind": "rectangle", "unit": "mm", "x": -150.0, "y": -100.0,
                      "width": spec.width_mm + 350.0, "depth": spec.depth_mm + 400.0},
        "service": {"kind": "rectangle", "unit": "mm", "x": -400.0, "y": -200.0,
                    "width": spec.width_mm + 800.0, "depth": spec.depth_mm + 700.0},
        "panel": {"kind": "orthogonal_polygon", "unit": "mm",
                  "points": [[-400, 0], [-100, 0], [-100, 300], [0, 300], [0, 600],
                             [-400, 600], [-400, 0]]},
        "tool_removal": {"kind": "rectangle", "unit": "mm", "x": 0, "y": -500,
                         "width": spec.width_mm, "depth": 500},
        "installation": {"kind": "rectangle", "unit": "mm", "x": -1000, "y": -1000,
                         "width": spec.width_mm + 2000, "depth": spec.depth_mm + 2000},
    }
    scenarios = {"body": "all", "operating": "production", "service": "maintenance",
                 "panel": "maintenance", "tool_removal": "maintenance",
                 "installation": "installation_replacement"}
    ports = {role: {"kind": "point", "unit": "mm", "x": spec.width_mm / 2,
                    "y": spec.depth_mm, "direction": "front"}
             for role in ("loading", "unloading", "operator", "service", "connection")}
    actions = {"stop": {"kind": "production_stopped", "description": "Declared stop for panel access."},
               "temporary": {"kind": "temporary_action", "description": "Declared temporary lifting area."}}
    for role, value in rectangles.items():
        value.update(id=role, role=role, scenario=scenarios[role], required_action_ids=
                     ["stop"] if role == "panel" else ["temporary"] if role == "installation" else [])
    for role, value in ports.items():
        value.update(id=role, role=role, scenario="maintenance" if role == "service" else "production")
    for key, value in actions.items():
        value.update(id=key, scenario="maintenance" if key == "stop" else "installation_replacement")
    template_source = {"capability": "finite_variant_selection_v1", "parameters": {
        "width": {"dimension": "width_mm", "minimum": {"value": spec.width_mm, "unit": "mm"},
                  "maximum": {"value": spec.width_mm + 1000, "unit": "mm"}}},
        "variants": [{"id": "original", "parameters": {"width": spec.width_mm}, "card_id": "press_card"}]}
    source = {"equipment": spec.to_dict(), "dimensions": values, "envelopes": rectangles,
              "points": ports, "actions": actions, "templates": {"press_template": template_source},
              "classification": "Project reconstruction; no manufacturer or legal clearance claim."}

    def evidence(value, pointer):
        return {"status": "KNOWN", "basis": "declared_fact", "source_id": "project",
                "source_pointer": pointer, "value": copy.deepcopy(value),
                "note": "Supplied project reconstruction only."}

    def measurement(value, pointer):
        return {"measurement": {"status": "KNOWN", "value": value, "unit": "mm",
                                "basis": "declared_fact", "source_id": "project",
                                "note": "Supplied project dimension."}, "source_pointer": pointer}

    dimensions = {name: measurement(value["value"], "/dimensions/" + name)
                  for name, value in values.items()}
    dimensions["height_mm"] = {"measurement": {"status": "UNKNOWN", "value": None, "unit": "mm",
                                             "basis": "declared_fact", "source_id": None,
                                             "note": "No height supplied."}, "source_pointer": None}
    raw = {"version": "0.1", "id": "project_library", "revision": "R01", "sources": [{
        "id": "project", "kind": "cad_geometry", "title": "Project-authored finite fixture",
        "edition": "R01", "uri": "urn:project:equipment:R01", "locator": "Structured JSON pointers",
        "content": "pending", "sha256": "0" * 64,
        "usage_rights": {"state": "PERMITTED", "note": "Supplied reusable test reconstruction."}}],
        "cards": [{"id": "press_card", "revision": "R01", "label": "Project press reconstruction",
                   "equipment_type": spec.type, "origin": "reconstruction",
                   "equipment_binding": {"id": spec.id, "sha256": digest(spec.to_dict()),
                                         "source_id": "project", "source_pointer": "/equipment"},
                   "dimensions": dimensions,
                   "envelopes": [{"id": role, "role": role, "scenario": scenarios[role],
                                  "evidence": evidence(value, "/envelopes/" + role),
                                  "required_action_ids": ["stop"] if role == "panel" else
                                      ["temporary"] if role == "installation" else []}
                                 for role, value in rectangles.items()],
                   "interaction_points": [{"id": role, "role": role, "scenario":
                                            "maintenance" if role == "service" else "production",
                                            "evidence": evidence(value, "/points/" + role)}
                                           for role, value in ports.items()],
                   "scenario_actions": [{"id": key, "scenario": "maintenance" if key == "stop" else
                                          "installation_replacement", "evidence": evidence(value, "/actions/" + key)}
                                         for key, value in actions.items()],
                   "applicability": {"equipment_ids": [spec.id], "equipment_types": [spec.type],
                                     "scenarios": ["production", "maintenance", "installation_replacement"],
                                     "conditions": []}, "engineer_confirmation": None}],
        "templates": [{"id": "press_template", "revision": "R01", "capability": "finite_variant_selection_v1",
                       "source_id": "project", "source_pointer": "/templates/press_template",
                       "parameters": [{"id": "width", "dimension": "width_mm", "unit": "mm",
                                       "minimum": measurement(spec.width_mm, "/templates/press_template/parameters/width/minimum"),
                                       "maximum": measurement(spec.width_mm + 1000, "/templates/press_template/parameters/width/maximum")}],
                       "variants": copy.deepcopy(template_source["variants"])}]}
    source_content(raw, source)
    return raw


class EngineeringLibraryTests(unittest.TestCase):
    def setUp(self):
        try:
            self.api = importlib.import_module("layout_configurator.engineering_library")
        except ModuleNotFoundError as exc:
            self.fail(f"The independent source-bound engineering library is missing: {exc}")
        self.raw = library_fixture()

    def validate(self, raw=None, **kwargs):
        return self.api.validate_engineering_library(self.raw if raw is None else raw, **kwargs)

    def codes(self, result):
        return {issue.code for issue in result.issues}

    def test_detached_roundtrip_unknown_height_and_offline_shared_source_contract(self):
        with patch("socket.socket", side_effect=AssertionError("Schema resolution must stay offline")):
            report = self.validate()
        self.assertEqual((report.status, report.schema_status, report.binding_status), ("UNKNOWN", "PASS", "PASS"))
        self.assertIn("unknown_quantity", self.codes(report))
        before = copy.deepcopy(self.raw)
        detached = report.library.to_dict()
        self.assertEqual(detached, before)
        self.assertIs(type(detached["cards"][0]["dimensions"]["width_mm"]["measurement"]["value"]), float)
        self.raw["sources"][0]["content"] = "tampered"
        detached["cards"][0]["id"] = "changed"
        self.assertEqual(report.library.to_dict(), before)
        self.assertEqual(report.to_dict()["engineering_acceptance"], "PENDING")
        self.assertEqual(report.to_dict()["source_authentication"], "UNKNOWN")

    def test_structured_number_mismatch_unit_bool_and_rehash_do_not_prove_truth(self):
        for value in (True, "1000", 2000, {"value": 1000, "unit": "m"}):
            with self.subTest(value=value):
                raw = copy.deepcopy(self.raw)
                source = json.loads(raw["sources"][0]["content"])
                source["dimensions"]["width_mm"] = value if type(value) is dict else {"value": value, "unit": "mm"}
                source_content(raw, source)
                report = self.validate(raw)
                self.assertEqual(report.status, "FAIL")
                self.assertIn("source_value_mismatch", self.codes(report))

    def test_source_bytes_pointer_geometry_and_binding_tamper_fail_independently(self):
        mutations = [lambda raw: raw["sources"][0].update(content="changed"),
                     lambda raw: raw["cards"][0]["dimensions"]["width_mm"].update(source_pointer="/missing"),
                     lambda raw: raw["cards"][0]["envelopes"][0]["evidence"]["value"].update(width=999),
                     lambda raw: raw["cards"][0]["equipment_binding"].update(sha256="a" * 64)]
        for change in mutations:
            with self.subTest(change=change):
                raw = copy.deepcopy(self.raw)
                change(raw)
                self.assertEqual(self.validate(raw).status, "FAIL")

    def test_text_excerpt_assumption_and_restricted_rights_are_not_reusable_pass(self):
        for state in ("RESTRICTED", "UNKNOWN"):
            with self.subTest(state=state):
                raw = copy.deepcopy(self.raw)
                raw["sources"][0]["usage_rights"]["state"] = state
                report = self.validate(raw)
                self.assertEqual(report.status, "UNKNOWN")
                self.assertIn("unavailable_usage_rights", self.codes(report))
        source = "Width 1000 mm: this excerpt is not a structured numeric source."
        self.raw["sources"][0].update(content=source, sha256=hashlib.sha256(source.encode()).hexdigest())
        report = self.validate()
        self.assertEqual(report.status, "UNKNOWN")
        self.assertIn("unstructured_source", self.codes(report))

    def test_bounds_actual_scalar_types_cycles_duplicate_source_keys_and_dates(self):
        changes = [lambda raw: raw["cards"][0]["dimensions"]["width_mm"]["measurement"].update(value=True),
                   lambda raw: raw["cards"][0]["envelopes"][0]["evidence"]["value"].update(x=float("nan")),
                   lambda raw: raw.update(extra=True),
                   lambda raw: raw["cards"][0]["envelopes"][0]["evidence"]["value"].update(x=10**400)]
        for change in changes:
            with self.subTest(change=change):
                raw = copy.deepcopy(self.raw)
                change(raw)
                self.assertEqual(self.validate(raw).status, "FAIL")
        raw = copy.deepcopy(self.raw)
        raw["sources"][0].update(content='{"a":1,"a":2}', sha256=hashlib.sha256(b'{"a":1,"a":2}').hexdigest())
        self.assertIn("invalid_structured_source", self.codes(self.validate(raw)))
        raw = copy.deepcopy(self.raw)
        raw["cards"].append(raw)
        self.assertEqual(self.validate(raw).status, "FAIL")
        self.assertEqual(self.validate(limits=replace(self.api.EngineeringLibraryLimits(), max_source_bytes=16)).status, "FAIL")
        raw = copy.deepcopy(self.raw)
        raw["cards"][0]["engineer_confirmation"] = {
            "author": "Supplied reviewer", "date": "2026-02-30", "revision": "R01", "source_id": "project",
            "source_sha256": raw["sources"][0]["sha256"], "statement": "Supplied statement only."}
        self.assertIn("invalid_confirmation", self.codes(self.validate(raw)))

    def test_geometric_source_is_literal_no_bbox_and_unknown_curves_retained(self):
        raw = copy.deepcopy(self.raw)
        source = json.loads(raw["sources"][0]["content"])
        bad = {**source["envelopes"]["panel"], "kind": "orthogonal_polygon", "unit": "mm",
               "points": [[0, 0], [100, 100], [100, 200], [0, 200], [0, 0]]}
        source["envelopes"]["panel"] = bad
        source_content(raw, source)
        raw["cards"][0]["envelopes"][3]["evidence"]["value"] = bad
        self.assertIn("invalid_local_geometry", self.codes(self.validate(raw)))
        curve = {key: source["envelopes"]["panel"][key] for key in ("id", "role", "scenario", "required_action_ids")}
        curve.update(kind="curve", description="Unsupported curved installation trajectory.")
        source["envelopes"]["panel"] = curve
        source_content(raw, source)
        raw["cards"][0]["envelopes"][3]["evidence"]["value"] = curve
        report = self.validate(raw)
        self.assertEqual(report.status, "UNKNOWN")
        self.assertEqual(report.library.to_dict()["cards"][0]["envelopes"][3]["evidence"]["value"], curve)

    def test_finite_template_exact_selection_parameter_limits_and_source_tamper(self):
        library = self.validate().library
        selected = self.api.instantiate_equipment_template(library, "press_template", {"width": 1000.0})
        self.assertEqual(selected.to_dict()["card_id"], "press_card")
        self.assertEqual(selected.to_dict()["template_capability"], "finite_variant_selection_v1")
        for parameters, status in (({"width": 999}, "FAIL"), ({"width": 2001}, "FAIL"),
                                   ({"width": True}, "FAIL"), ({"width": float("inf")}, "FAIL"),
                                   ({"width": 1500}, "UNKNOWN"), ({"width": 1000, "extra": 1}, "FAIL")):
            with self.subTest(parameters=parameters):
                with self.assertRaises(self.api.EngineeringTemplateError) as caught:
                    self.api.instantiate_equipment_template(library, "press_template", parameters)
                self.assertEqual(caught.exception.to_dict()["status"], status)
        self.raw["templates"][0]["variants"][0]["parameters"]["width"] = 1500
        self.assertIn("source_value_mismatch", self.codes(self.validate()))

    def test_reconstruction_example_matches_actual_canonical_pilot_equipment(self):
        import yaml
        root = Path(__file__).resolve().parents[1]
        raw = json.loads((root / "examples/engineering_library_reconstruction.json").read_text(encoding="utf-8"))
        building = BuildingIR.from_mapping(yaml.safe_load((root / "examples/pharma_cleanroom_pilot.yaml").read_text(encoding="utf-8")))
        spec = next(item for item in building.equipment if item.id == "production_line_01")
        report = self.validate(raw)
        self.assertEqual(report.binding_status, "PASS")
        self.assertEqual(raw["cards"][0]["equipment_binding"]["sha256"], digest(spec.to_dict()))
        self.assertEqual(raw["cards"][0]["origin"], "reconstruction")
        self.assertEqual(raw["cards"][0]["dimensions"]["height_mm"]["measurement"]["status"], "UNKNOWN")

    def test_detached_utf8_bound_is_checked_before_parsing(self):
        forged = self.api.EngineeringLibraryContract('{"text":"' + "\U0001f642" * 600000 + '"}')
        with patch.object(self.api, "_strict_json", wraps=self.api._strict_json) as parser:
            with self.assertRaises(ValueError):
                forged.to_dict()
        self.assertEqual(parser.call_count, 0)

    def test_numeric_equivalent_variant_vectors_are_ambiguous_not_first_match(self):
        raw = copy.deepcopy(self.raw)
        second = {"id": "same_numeric_vector", "parameters": {"width": 1000}, "card_id": "press_card"}
        raw["templates"][0]["variants"].append(second)
        source = json.loads(raw["sources"][0]["content"])
        source["templates"]["press_template"]["variants"].append(second)
        source_content(raw, source)
        self.assertIn("ambiguous_variant", self.codes(self.validate(raw)))

    def test_explicit_parameter_dimension_must_match_selected_card(self):
        raw = copy.deepcopy(self.raw)
        raw["templates"][0]["parameters"][0]["dimension"] = "width_mm"
        source = json.loads(raw["sources"][0]["content"])
        source["templates"]["press_template"]["parameters"]["width"]["dimension"] = "width_mm"
        source_content(raw, source)
        self.assertEqual(self.validate(raw).binding_status, "PASS")
        raw["templates"][0]["variants"][0]["parameters"]["width"] = 1500.0
        source["templates"]["press_template"]["variants"][0]["parameters"]["width"] = 1500.0
        source_content(raw, source)
        report = self.validate(raw)
        self.assertEqual(report.status, "FAIL")
        self.assertIn("invalid_variant_dimension", self.codes(report))

    def test_supplied_confirmation_identity_and_missing_values_are_not_authenticated(self):
        raw = copy.deepcopy(self.raw)
        row = raw["cards"][0]["dimensions"]["width_mm"]["measurement"]
        row["basis"] = "supplied_confirmation"
        self.assertIn("missing_confirmation", self.codes(self.validate(raw)))
        raw["cards"][0]["engineer_confirmation"] = {"author": "Declared project reviewer", "date": "2026-10-13",
            "revision": "R01", "source_id": "project", "source_sha256": raw["sources"][0]["sha256"],
            "statement": "Supplied example dimension confirmation, not authenticated approval."}
        report = self.validate(raw)
        self.assertEqual(report.binding_status, "PASS")
        self.assertNotIn("missing_confirmation", self.codes(report))
        self.assertEqual(report.to_dict()["source_authentication"], "UNKNOWN")
        raw["cards"][0]["engineer_confirmation"]["revision"] = "stale"
        self.assertIn("invalid_confirmation", self.codes(self.validate(raw)))

    def test_unknown_values_still_require_declared_source_references(self):
        raw = copy.deepcopy(self.raw)
        raw["cards"][0]["dimensions"]["height_mm"]["measurement"]["source_id"] = "undeclared"
        report = self.validate(raw)
        self.assertEqual(report.status, "FAIL")
        self.assertIn("unknown_source_id", self.codes(report))

    def test_confirmation_cannot_cover_another_identical_source_identity(self):
        raw = copy.deepcopy(self.raw)
        duplicate = copy.deepcopy(raw["sources"][0])
        duplicate["id"] = "another_source"
        raw["sources"].append(duplicate)
        card = raw["cards"][0]
        card["engineer_confirmation"] = {"author": "Declared reviewer", "date": "2026-10-13",
            "revision": "R01", "source_id": "another_source", "source_sha256": duplicate["sha256"],
            "statement": "Confirmation applies only to another_source."}
        for collection, key in (("dimensions", "width_mm"), ("envelopes", 0), ("scenario_actions", 0)):
            with self.subTest(collection=collection):
                changed = copy.deepcopy(raw)
                row = changed["cards"][0][collection][key]
                (row["measurement"] if collection == "dimensions" else row["evidence"])["basis"] = "supplied_confirmation"
                report = self.validate(changed)
                self.assertEqual(report.status, "FAIL")
                self.assertIn("confirmation_source_mismatch", self.codes(report))

    def test_detached_depth_and_nodes_are_bounded_before_json_parsing(self):
        for content in ("[" * 25 + "0" + "]" * 25, "[" + ",".join("0" for _ in range(65537)) + "]"):
            with self.subTest(length=len(content)):
                with patch.object(self.api, "_strict_json", wraps=self.api._strict_json) as parser:
                    with self.assertRaises(ValueError):
                        self.api.EngineeringLibraryContract(content).to_dict()
                self.assertEqual(parser.call_count, 0)

    def test_structured_source_depth_is_bounded_before_json_parsing(self):
        raw = copy.deepcopy(self.raw)
        content = "[" * 25 + "0" + "]" * 25
        raw["sources"][0].update(content=content, sha256=hashlib.sha256(content.encode()).hexdigest())
        with patch.object(self.api, "_strict_json", wraps=self.api._strict_json) as parser:
            report = self.validate(raw)
        self.assertEqual(report.status, "FAIL")
        self.assertEqual(parser.call_count, 0)

    def test_actual_second_finite_variant_binds_its_distinct_sourced_dimensions(self):
        raw = copy.deepcopy(self.raw)
        wide_spec = replace(fixture_spec(), id="wide_press", width_mm=1500.0)
        wide = library_fixture(wide_spec)
        card = copy.deepcopy(wide["cards"][0])
        card["id"] = "wide_card"
        card["equipment_binding"]["source_pointer"] = "/wide/equipment"
        for row in card["dimensions"].values():
            if row["source_pointer"] is not None:
                row["source_pointer"] = "/wide" + row["source_pointer"]
        for key in ("envelopes", "interaction_points", "scenario_actions"):
            for row in card[key]:
                row["evidence"]["source_pointer"] = "/wide" + row["evidence"]["source_pointer"]
        raw["cards"].append(card)
        variant = {"id": "wide", "parameters": {"width": 1500.0}, "card_id": "wide_card"}
        raw["templates"][0]["variants"].append(variant)
        source = json.loads(raw["sources"][0]["content"])
        source["wide"] = json.loads(wide["sources"][0]["content"])
        source["templates"]["press_template"]["variants"].append(variant)
        source_content(raw, source)
        report = self.validate(raw)
        self.assertEqual(report.binding_status, "PASS")
        selected = self.api.instantiate_equipment_template(report.library, "press_template", {"width": 1500})
        self.assertEqual(selected.to_dict()["equipment_sha256"], digest(wide_spec.to_dict()))
        self.assertEqual(selected.to_dict()["card_id"], "wide_card")
        from layout_configurator.equipment_card_projection import project_equipment_card
        from layout_configurator.equipment import EquipmentPlacement
        from layout_configurator.models import Rect
        projected = project_equipment_card(report.library, "wide_card", equipment=wide_spec,
            placement=EquipmentPlacement("wide_press", "work", Rect(100, 200, 1500, 800))).to_dict()
        self.assertEqual(projected["body_points_mm"][1], [1600, 200])

    def test_complete_structured_project_card_pass_is_scoped_and_height_not_route_proof(self):
        raw = copy.deepcopy(self.raw)
        height = raw["cards"][0]["dimensions"]["height_mm"]
        height["measurement"].update(status="KNOWN", value=2000.0, source_id="project", note="Declared project fixture height only.")
        height["source_pointer"] = "/dimensions/height_mm"
        source = json.loads(raw["sources"][0]["content"])
        source["dimensions"]["height_mm"] = {"value": 2000.0, "unit": "mm"}
        source_content(raw, source)
        report = self.validate(raw)
        self.assertEqual((report.status, report.binding_status, report.evidence_status), ("PASS", "PASS", "PASS"))
        self.assertEqual(report.to_dict()["full_scenario_validation"], "UNKNOWN")
        self.assertEqual(report.to_dict()["engineering_acceptance"], "PENDING")


if __name__ == "__main__":
    unittest.main()

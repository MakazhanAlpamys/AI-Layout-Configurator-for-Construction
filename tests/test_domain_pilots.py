"""Coordinate-free domain pilots exercise actual routing and persisted acceptance."""

import contextlib
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

from jsonschema import Draft202012Validator
import yaml

from layout_configurator.building import BuildingIR
from layout_configurator.equipment import validate_equipment_layout
from layout_configurator.facility import load_facility_profile, validate_building
from layout_configurator.flows import route_flows, validate_flow_routes
from layout_configurator.io import load_building, load_building_result


ROOT = Path(__file__).resolve().parents[1]
DOMAINS = ("laboratory", "hospital", "industrial")
RUNNER = ROOT / "scripts" / "domain_pilots_acceptance.py"


def _runner():
    spec = importlib.util.spec_from_file_location("domain_pilots_acceptance", RUNNER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class CanonicalDomainPilotTests(unittest.TestCase):
    def test_every_typology_has_a_strict_canonical_coordinate_free_program(self):
        validators = [Draft202012Validator(json.loads((ROOT / "schemas" / name).read_text(encoding="utf-8")))
                      for name in ("building_ir.schema.json", "layout_ir.schema.json")]
        for domain in DOMAINS:
            with self.subTest(domain=domain):
                source = ROOT / "examples" / f"{domain}_pilot.yaml"
                self.assertTrue(source.is_file(), f"No canonical {domain} acceptance pilot")
                text = source.read_text(encoding="utf-8")
                raw = yaml.safe_load(text)
                self.assertEqual(list(validators[0].iter_errors(raw)), [])
                self.assertEqual(list(validators[1].iter_errors(raw["layout"])), [])
                self.assertEqual(load_building(source).to_dict(), raw, "Pilot depends on implicit normalization")
                self.assertNotIn("structural_grid", raw)
                for room in raw["layout"]["rooms"]:
                    self.assertFalse({"x", "y", "rect", "vertices"} & room.keys())
                self.assertGreaterEqual(len(raw["zones"]), 3)
                self.assertEqual(len(raw["equipment"]), 2)
                self.assertTrue(all(not item["fixed"] for item in raw["equipment"]))
                profile = load_facility_profile(ROOT / "rules" / f"{domain}_pilot.yaml")
                required = next(rule.parameters["flow_types"] for rule in profile.rules
                                if rule.kind == "required_flow_types")
                self.assertEqual({flow["type"] for flow in raw["flows"]}, set(required))

class DomainPilotInputTests(unittest.TestCase):
    def test_runner_rejects_noncanonical_program_or_unsourced_profile_before_artifacts(self):
        module = _runner()
        source = ROOT / "examples" / "hospital_pilot.yaml"
        profile = ROOT / "rules" / "hospital_pilot.yaml"
        for mutation in ("coordinate", "missing_zone_type", "unknown_zone_member", "missing_rule_source", "malformed_yaml"):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as temporary:
                repository = Path(temporary)
                for folder in ("examples", "rules", "schemas"):
                    (repository / folder).mkdir()
                for name in ("building_ir.schema.json", "layout_ir.schema.json"):
                    shutil.copy2(ROOT / "schemas" / name, repository / "schemas" / name)
                program = yaml.safe_load(source.read_text(encoding="utf-8"))
                rule_pack = yaml.safe_load(profile.read_text(encoding="utf-8"))
                if mutation == "coordinate":
                    program["layout"]["rooms"][0]["x"] = 0
                elif mutation == "missing_zone_type":
                    del program["zones"][0]["type"]
                elif mutation == "unknown_zone_member":
                    program["zones"][0]["room_ids"] = ["missing_room"]
                elif mutation == "missing_rule_source":
                    del rule_pack["rules"][0]["source"]
                (repository / "examples" / source.name).write_text(
                    "layout: [unterminated" if mutation == "malformed_yaml" else yaml.safe_dump(program), encoding="utf-8")
                (repository / "rules" / profile.name).write_text(yaml.safe_dump(rule_pack), encoding="utf-8")
                output = repository / "output"
                with patch.object(module, "ROOT", repository), contextlib.redirect_stdout(io.StringIO()):
                    code = module.main(["--output", str(output), "--domains", "hospital"])
                self.assertEqual(code, 2)
                report = json.loads((output / "acceptance-report.json").read_text(encoding="utf-8"))
                self.assertEqual(report["status"], "INPUT_REJECTED")
                self.assertFalse(any(output.glob("*/building_01.*")))


class DomainPilotAcceptanceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.temporary.cleanup)
        cls.output = Path(cls.temporary.name) / "accepted"
        cls.runner = _runner()
        stream = io.StringIO()
        with contextlib.redirect_stdout(stream), contextlib.redirect_stderr(stream):
            code = cls.runner.main(["--output", str(cls.output)])
        if code:
            failed = json.loads((cls.output / "acceptance-report.json").read_text(encoding="utf-8"))
            causes = [{key: item.get(key) for key in ("domain", "status", "message", "ifc_express")}
                      for item in failed["pilots"]]
            raise AssertionError(f"Actual domain CLI/shared export acceptance returned {code}: {causes}")
        cls.report = json.loads((cls.output / "acceptance-report.json").read_text(encoding="utf-8"))

    def accepted(self, domain):
        directory = self.output / domain
        building, layout, equipment = load_building_result(directory / "building_01.json")
        return building, layout, equipment, load_facility_profile(ROOT / "rules" / f"{domain}_pilot.yaml")

    def validate(self, building, layout, equipment, profile):
        routes = route_flows(building, layout, equipment)
        flow_report = validate_flow_routes(building, layout, routes, equipment)
        self.assertTrue(flow_report.ok, flow_report.to_dict())
        report = validate_building(building, layout, equipment, routes, flow_report, profile=profile,
                                  equipment_report=validate_equipment_layout(building, layout, equipment))
        return report, routes

    def test_actual_shared_bundles_have_strict_express_and_independent_saved_qa(self):
        self.assertTrue(self.report["ok"], self.report)
        self.assertEqual(self.report["engineering_acceptance"], "PENDING")
        self.assertEqual({item["domain"] for item in self.report["pilots"]}, set(DOMAINS))
        for entry in self.report["pilots"]:
            with self.subTest(domain=entry["domain"]):
                self.assertTrue(entry["ok"], entry)
                self.assertEqual(entry["ifc_express"]["statements"], [])
                self.assertIs(entry["ifc_express"]["express_rules"], True)
                qa = json.loads((self.output / entry["domain"] / "qa-report.json").read_text(encoding="utf-8"))
                self.assertTrue(qa["ok"], qa)
                ids = {check["id"] for check in qa["variants"][0]["checks"]}
                self.assertTrue({"FACILITY_VALIDATION", "IFC_READBACK", "SHEET_SET_READBACK",
                                 "CLIENT_REPORT_READBACK"} <= ids, ids)
                self.assertGreater(entry["elapsed_seconds"], 0)
                self.assertEqual(entry["source_sha256"], hashlib.sha256(
                    (ROOT / "examples" / f"{entry['domain']}_pilot.yaml").read_bytes()).hexdigest())
                self.assertEqual(entry["profile_source_sha256"], hashlib.sha256(
                    (ROOT / "rules" / f"{entry['domain']}_pilot.yaml").read_bytes()).hexdigest())
                for artifact in entry["artifact_sha256"]:
                    path = self.output / entry["domain"] / artifact["file"]
                    self.assertEqual(artifact["sha256"], hashlib.sha256(path.read_bytes()).hexdigest())

    def test_saved_program_and_profile_preserve_the_exact_loaded_source_semantics(self):
        for domain in DOMAINS:
            with self.subTest(domain=domain):
                directory = self.output / domain
                payload = json.loads((directory / "building_01.json").read_text(encoding="utf-8"))
                original = load_building(ROOT / "examples" / f"{domain}_pilot.yaml")
                profile = load_facility_profile(ROOT / "rules" / f"{domain}_pilot.yaml")
                self.assertEqual(payload["spec"], original.with_required_flow_opening_width().to_dict())
                self.assertEqual(payload["facility_validation"]["profile"], profile.to_dict())
                self.assertEqual(payload["facility_validation"]["ok"], True)
                building, layout, equipment, profile = self.accepted(domain)
                report, routes = self.validate(building, layout, equipment, profile)
                self.assertTrue(report.ok, report.to_dict())
                self.assertEqual({route.flow_id for route in routes.routes}, {flow.id for flow in building.flows})
                self.assertTrue(all(len(route.room_path) == 3 for route in routes.routes), routes.to_dict())
                self.assertTrue(all(route.room_path[-1] == "coordination_hub" for route in routes.routes))

    def test_missing_or_retyped_required_flows_fail_the_actual_domain_rule(self):
        for domain in DOMAINS:
            building, layout, equipment, profile = self.accepted(domain)
            for flow in building.flows:
                for mutation in ("missing", "wrong_type"):
                    with self.subTest(domain=domain, flow=flow.id, mutation=mutation):
                        raw = building.to_dict()
                        if mutation == "missing":
                            raw["flows"] = [item for item in raw["flows"] if item["id"] != flow.id]
                        else:
                            next(item for item in raw["flows"] if item["id"] == flow.id)["type"] = "undeclared_test_type"
                        report, _ = self.validate(BuildingIR.from_mapping(raw), layout, equipment, profile)
                        check = next(item for item in report.checks if item.rule_id and item.rule_id.endswith("REQUIRED_FLOW_TYPES"))
                        self.assertEqual(check.status, "FAIL", report.to_dict())
                        self.assertFalse(report.ok)
                        self.assertIn(flow.type, " ".join(check.evidence))

    def test_each_declared_incompatibility_rejects_real_shared_intermediate_routing(self):
        for domain in DOMAINS:
            building, layout, equipment, profile = self.accepted(domain)
            for left_type, right_type in profile.incompatible_flow_type_pairs:
                with self.subTest(domain=domain, pair=(left_type, right_type)):
                    raw = building.to_dict()
                    left = next(item for item in raw["flows"] if item["type"] == left_type)
                    right = next(item for item in raw["flows"] if item["type"] == right_type)
                    right["from_ids"] = left["from_ids"][:]
                    report, routes = self.validate(BuildingIR.from_mapping(raw), layout, equipment, profile)
                    check = next(item for item in report.checks if item.id == "FLOW_TYPE_SEPARATION")
                    self.assertEqual(check.status, "FAIL", report.to_dict())
                    self.assertEqual(check.rule_id, f"{'LAB' if domain == 'laboratory' else domain.upper()}_FLOW_SEPARATION")
                    actual = {route.flow_id: route.room_path for route in routes.routes}
                    self.assertTrue(set(actual[left["id"]][1:-1]) & set(actual[right["id"]][1:-1]))

    def test_wrong_typology_profiles_fail_missing_categories_on_real_pilots(self):
        for domain in DOMAINS:
            building, layout, equipment, _ = self.accepted(domain)
            for other in set(DOMAINS) - {domain}:
                with self.subTest(source=domain, profile=other):
                    profile = load_facility_profile(ROOT / "rules" / f"{other}_pilot.yaml")
                    report, _ = self.validate(building, layout, equipment, profile)
                    self.assertFalse(report.ok, report.to_dict())
                    self.assertEqual(next(check.status for check in report.checks
                                          if check.rule_id and check.rule_id.endswith("REQUIRED_FLOW_TYPES")), "FAIL")

    def test_same_geometry_union_of_types_proves_three_distinct_profile_policies(self):
        building, layout, equipment, _ = self.accepted("hospital")
        baseline = {"specimen": 1, "people": 1, "clean_supply": 1, "patients": 1,
                    "material": 1, "maintenance": 1, "dirty_supply": 2,
                    "hazardous_material": 2, "vehicle": 3, "waste": 3}
        cases = {"laboratory": {"specimen": 3}, "hospital": {"dirty_supply": 1},
                 "industrial": {"vehicle": 1}}
        for failing, change in cases.items():
            raw = building.to_dict()
            allocation = {**baseline, **change}
            raw["flows"] = [{"id": f"probe_{kind}", "type": kind,
                "from_ids": [f"channel_{channel}_source"], "to_ids": ["coordination_hub"],
                "minimum_clear_width_mm": 800, "required": True} for kind, channel in allocation.items()]
            probe = BuildingIR.from_mapping(raw)
            for domain in DOMAINS:
                with self.subTest(conflict=failing, profile=domain):
                    report, _ = self.validate(probe, layout, equipment,
                        load_facility_profile(ROOT / "rules" / f"{domain}_pilot.yaml"))
                    self.assertEqual(report.ok, domain != failing, report.to_dict())

    def test_read_only_runner_rejects_missing_current_report_artifact(self):
        target = Path(self.temporary.name) / "tampered"
        shutil.copytree(self.output, target)
        (target / "hospital" / "building_01_report.pdf").unlink()
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            code = self.runner.main(["--output", str(target), "--check-only"])
        self.assertEqual(code, 4)
        report = json.loads((target / "acceptance-report.json").read_text(encoding="utf-8"))
        self.assertFalse(report["ok"])
        self.assertFalse(next(item for item in report["pilots"] if item["domain"] == "hospital")["ok"])

    def test_runner_rejects_nonpositive_nonfinite_or_duplicate_settings_before_generation(self):
        for options in (["--time-limit", "0"], ["--deterministic-budget", "nan"],
                        ["--domains", "hospital", "hospital"]):
            with self.subTest(options=options):
                output = Path(self.temporary.name) / ("invalid_" + str(len(options)) + str(options[-1]))
                with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                    code = self.runner.main(["--output", str(output), *options])
                self.assertEqual(code, 2)
                self.assertFalse(any(output.glob("*/building_01.json")))

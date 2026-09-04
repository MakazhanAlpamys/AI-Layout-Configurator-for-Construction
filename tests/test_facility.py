import json
import tempfile
import unittest
from pathlib import Path
from zipfile import ZipFile

from jsonschema import Draft202012Validator
import yaml

from layout_configurator.building import BuildingIR
from layout_configurator.bcf import read_bcf_package, write_bcf_package
from layout_configurator.equipment import EquipmentLayoutResult
from layout_configurator.facility import (
    FacilityValidationReport,
    FacilityProfile,
    default_facility_profile,
    load_facility_profile,
    validate_building,
)
from layout_configurator.flows import route_flows, validate_flow_routes
from layout_configurator.io import write_coordination_issues
from layout_configurator.models import LayoutResult, Rect
from layout_configurator.solver import solve_layouts


class FacilityTests(unittest.TestCase):
    def test_pharma_profile_loads_and_declares_flow_separation(self):
        profile = load_facility_profile("rules/pharma_clean_production.yaml")

        self.assertEqual(profile.domain, "pharma-clean-production")
        self.assertIn(("material", "waste"), profile.incompatible_flow_type_pairs)
        self.assertEqual(
            {rule.id for rule in profile.rules},
            {"PHARMA_REQUIRED_FLOW_TYPES", "PHARMA_PROCESS_SEQUENCE", "PHARMA_FLOW_SEPARATION"},
        )

    def test_domain_rule_packs_are_schema_valid_and_auditable(self):
        filenames = (
            "default_facility.yaml",
            "cleanroom_pilot.yaml",
            "pharma_clean_production.yaml",
            "laboratory_pilot.yaml",
            "hospital_pilot.yaml",
            "industrial_pilot.yaml",
        )
        schema = json.loads(Path("schemas/facility_profile.schema.json").read_text(encoding="utf-8"))
        validator = Draft202012Validator(schema)
        for filename in filenames:
            raw = yaml.safe_load((Path("rules") / filename).read_text(encoding="utf-8"))
            self.assertEqual(list(validator.iter_errors(raw)), [], filename)
            profile = load_facility_profile(Path("rules") / filename)
            self.assertEqual(list(validator.iter_errors(profile.to_dict())), [], filename)
            self.assertTrue(profile.rules)
            self.assertEqual(len({rule.id for rule in profile.rules}), len(profile.rules))
            for rule in profile.rules:
                self.assertTrue(rule.source)
                self.assertTrue(rule.edition)
                self.assertTrue(rule.effective_date)
                self.assertTrue(rule.evidence)
                self.assertEqual(rule.to_dict()["source"], rule.source)

    def test_yaml_loader_rejects_rule_without_provenance(self):
        raw = yaml.safe_load(Path("rules/pharma_clean_production.yaml").read_text(encoding="utf-8"))
        del raw["rules"][0]["source"]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "invalid.yaml"
            path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "source"):
                load_facility_profile(path)

    def test_required_flow_rule_is_reported_as_a_coordination_issue(self):
        profile = FacilityProfile.from_mapping(
            {
                "name": "Specimen test profile",
                "version": "test",
                "domain": "laboratory",
                "jurisdiction": "project-profile-not-a-regulatory-verdict",
                "rules": [
                    {
                        "id": "LAB_REQUIRED_FLOW_TYPES",
                        "kind": "required_flow_types",
                        "title": "Required laboratory flow types",
                        "source": "https://example.test/lab",
                        "edition": "test edition",
                        "effective_date": "test-date",
                        "evidence": ["Test evidence"],
                        "parameters": {"flow_types": ["specimen"]},
                    }
                ],
            }
        )
        building = _building()
        result = LayoutResult(
            1,
            {
                "source": Rect(0, 0, 3000, 3000),
                "target": Rect(3000, 0, 3000, 3000),
            },
        )
        report = validate_building(
            building,
            result,
            EquipmentLayoutResult(()),
            None,
            None,
            profile=profile,
        )

        check = next(item for item in report.checks if item.id == "LAB_REQUIRED_FLOW_TYPES")
        self.assertEqual(check.status, "FAIL")
        self.assertEqual(check.rule_id, "LAB_REQUIRED_FLOW_TYPES")
        self.assertEqual(check.source, "https://example.test/lab")
        self.assertTrue(any(issue.code == "LAB_REQUIRED_FLOW_TYPES" for issue in report.issues))

    def test_cleanroom_zone_fields_airlock_and_pressure_cascade_are_checked(self):
        building = _building(
            zones=[
                {
                    "id": "clean_parent",
                    "type": "cleanroom",
                    "room_ids": ["source"],
                    "cleanroom_class": "ISO 7",
                    "pressure_pa": 20,
                },
                {
                    "id": "clean_child",
                    "type": "cleanroom",
                    "room_ids": ["target"],
                    "parent_zone_id": "clean_parent",
                    "cleanroom_class": "ISO 8",
                    "pressure_pa": 10,
                },
                {"id": "material_airlock", "type": "airlock", "airlock": True},
            ]
        )
        profile = FacilityProfile.from_mapping(
            {
                "name": "Cleanroom test profile",
                "version": "test",
                "domain": "cleanroom",
                "jurisdiction": "project-profile-not-a-regulatory-verdict",
                "rules": [
                    {
                        "id": "ZONE_FIELDS",
                        "kind": "required_zone_fields",
                        "title": "Zone fields",
                        "source": "https://example.test/iso",
                        "edition": "test edition",
                        "effective_date": "test-date",
                        "evidence": ["Zone evidence"],
                        "parameters": {
                            "zone_types": ["cleanroom"],
                            "fields": ["cleanroom_class", "pressure_pa"],
                        },
                    },
                    {
                        "id": "AIRLOCK",
                        "kind": "airlock_presence",
                        "title": "Airlock",
                        "source": "https://example.test/gateway",
                        "edition": "test edition",
                        "effective_date": "test-date",
                        "evidence": ["Gateway evidence"],
                        "parameters": {"zone_types": ["airlock"], "required": True},
                    },
                    {
                        "id": "PRESSURE",
                        "kind": "pressure_cascade",
                        "title": "Pressure cascade",
                        "source": "https://example.test/pressure",
                        "edition": "test edition",
                        "effective_date": "test-date",
                        "evidence": ["Pressure evidence"],
                        "parameters": {"zone_types": ["cleanroom"], "direction": "parent_gt_child"},
                    },
                ],
            }
        )
        result = LayoutResult(
            1,
            {
                "source": Rect(0, 0, 3000, 3000),
                "target": Rect(3000, 0, 3000, 3000),
            },
        )
        report = validate_building(
            building,
            result,
            EquipmentLayoutResult(()),
            None,
            None,
            profile=profile,
        )

        statuses = {item.id: item.status for item in report.checks}
        self.assertEqual(statuses["ZONE_FIELDS"], "PASS")
        self.assertEqual(statuses["AIRLOCK"], "PASS")
        self.assertEqual(statuses["PRESSURE"], "PASS")
        self.assertTrue(report.ok, report.to_dict())

    def test_process_sequence_checks_declared_stage_transitions(self):
        building = _building(
            flows=[
                {"id": "raw", "type": "material", "stage": "raw_material", "from_ids": ["source"], "to_ids": ["target"], "minimum_clear_width_mm": 800},
                {"id": "production", "type": "material", "stage": "production", "from_ids": ["target"], "to_ids": ["source"], "minimum_clear_width_mm": 800},
                {"id": "packaging", "type": "material", "stage": "packaging", "from_ids": ["source"], "to_ids": ["target"], "minimum_clear_width_mm": 800},
                {"id": "finished", "type": "finished_goods", "stage": "finished_goods", "from_ids": ["target"], "to_ids": ["source"], "minimum_clear_width_mm": 800},
                {"id": "waste", "type": "waste", "stage": "waste", "from_ids": ["source"], "to_ids": ["target"], "minimum_clear_width_mm": 800},
            ]
        )
        profile = FacilityProfile.from_mapping(
            {
                "name": "Pharma sequence test profile",
                "version": "test",
                "domain": "pharma-clean-production",
                "jurisdiction": "project-profile-not-a-regulatory-verdict",
                "rules": [
                    {
                        "id": "PHARMA_PROCESS_SEQUENCE",
                        "kind": "process_sequence",
                        "title": "Process sequence",
                        "source": "https://example.test/gmp",
                        "edition": "test edition",
                        "effective_date": "test-date",
                        "evidence": ["Sequence evidence"],
                        "parameters": {
                            "sequence": ["raw_material", "production", "packaging", "finished_goods"],
                            "branches": [["production", "waste"]],
                        },
                    }
                ],
            }
        )
        result = LayoutResult(
            1,
            {
                "source": Rect(0, 0, 3000, 3000),
                "target": Rect(3000, 0, 3000, 3000),
            },
        )
        routes = route_flows(building, result)
        report = validate_building(
            building,
            result,
            EquipmentLayoutResult(()),
            routes,
            validate_flow_routes(building, result, routes),
            profile=profile,
        )

        check = next(item for item in report.checks if item.id == "PHARMA_PROCESS_SEQUENCE")
        self.assertEqual(check.status, "PASS", report.to_dict())
        self.assertIn("transition", " ".join(check.evidence))

    def test_starter_domain_profiles_are_versioned_and_non_regulatory(self):
        expected = {
            "cleanroom_pilot.yaml": "cleanroom",
            "laboratory_pilot.yaml": "laboratory",
            "hospital_pilot.yaml": "hospital",
            "industrial_pilot.yaml": "industrial",
        }
        for filename, domain in expected.items():
            profile = load_facility_profile(Path("rules") / filename)
            self.assertEqual(profile.domain, domain)
            self.assertEqual(profile.version, "0.1")
            self.assertEqual(profile.jurisdiction, "project-profile-not-a-regulatory-verdict")
            self.assertTrue(profile.incompatible_flow_type_pairs)
            self.assertEqual(profile.drawing.sheet_id, "A-101")
            self.assertTrue(profile.drawing.show_flow_legend)

    def test_zone_required_adjacency_is_a_solver_constraint(self):
        building = _building(
            zones=[
                {
                    "id": "source_zone",
                    "type": "source",
                    "room_ids": ["source"],
                    "required_adjacency": ["target_zone"],
                },
                {"id": "target_zone", "type": "target", "room_ids": ["target"]},
            ]
        )

        result = solve_layouts(
            building.layout,
            time_limit_seconds=5,
            required_adjacency_groups=building.zone_relation_groups("required_adjacency"),
        )[0]

        self.assertGreaterEqual(
            result.placements["source"].shared_boundary(result.placements["target"]),
            building.layout.door_width_mm,
        )

    def test_facility_report_contains_independent_domain_evidence(self):
        building = _building(
            zones=[
                {
                    "id": "source_zone",
                    "type": "source",
                    "room_ids": ["source"],
                    "required_adjacency": ["target_zone"],
                },
                {"id": "target_zone", "type": "target", "room_ids": ["target"]},
            ]
        )
        result = LayoutResult(
            1,
            {
                "source": Rect(0, 0, 3000, 3000),
                "target": Rect(3000, 0, 3000, 3000),
            },
        )
        flow_routes = route_flows(building, result)
        flow_report = validate_flow_routes(building, result, flow_routes)
        report = validate_building(
            building,
            result,
            EquipmentLayoutResult(()),
            flow_routes,
            flow_report,
            profile=default_facility_profile(),
        )

        self.assertTrue(report.ok, report.to_dict())
        self.assertEqual(
            {check.id: check.status for check in report.checks},
            {
                "FACILITY_GEOMETRY": "PASS",
                "ZONE_RELATIONS": "PASS",
                "EQUIPMENT_CLEARANCE": "NOT_APPLICABLE",
                "FLOW_COMPLETENESS": "NOT_APPLICABLE",
                "FLOW_TYPE_SEPARATION": "NOT_APPLICABLE",
            },
        )
        self.assertTrue(any("source_zone-target_zone" in item for item in report.checks[1].evidence))

    def test_optional_flow_does_not_fail_when_no_route_exists(self):
        building = _building(
            flows=[
                {
                    "id": "optional",
                    "type": "waste",
                    "from_ids": ["source"],
                    "to_ids": ["target"],
                    "required": False,
                }
            ]
        )
        result = LayoutResult(
            1,
            {
                "source": Rect(0, 0, 3000, 3000),
                "target": Rect(3500, 0, 2500, 3000),
            },
        )

        report = validate_flow_routes(building, result, route_flows(building, result))

        self.assertTrue(report.ok, report.to_dict())

    def test_facility_failures_become_stable_coordination_issues(self):
        building = _building(
            flows=[
                {
                    "id": "required_material",
                    "type": "material",
                    "from_ids": ["source"],
                    "to_ids": ["target"],
                }
            ]
        )
        result = LayoutResult(
            1,
            {
                "source": Rect(0, 0, 3000, 3000),
                "target": Rect(3500, 0, 2500, 3000),
            },
        )
        routes = route_flows(building, result)
        flow_report = validate_flow_routes(building, result, routes)
        report = validate_building(
            building,
            result,
            EquipmentLayoutResult(()),
            routes,
            flow_report,
            profile=default_facility_profile(),
        )
        repeat = validate_building(
            building,
            result,
            EquipmentLayoutResult(()),
            routes,
            flow_report,
            profile=default_facility_profile(),
        )

        self.assertFalse(report.ok)
        self.assertTrue(report.issues)
        self.assertEqual(
            [issue.issue_id for issue in report.issues],
            [issue.issue_id for issue in repeat.issues],
        )
        self.assertTrue(any(issue.flow_id == "required_material" for issue in report.issues))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "coordination.json"
            write_coordination_issues(path, report)
            payload = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(payload["format"], "FLC-BCF-like-json")
            self.assertEqual(payload["open_count"], len(report.issues))
            schema = json.loads(Path("schemas/coordination_issues.schema.json").read_text(encoding="utf-8"))
            self.assertEqual(list(Draft202012Validator(schema).iter_errors(payload)), [])

    def test_coordination_issues_export_to_bcf_21_and_read_back(self):
        building = _building(
            flows=[
                {
                    "id": "required_material",
                    "type": "material",
                    "from_ids": ["source"],
                    "to_ids": ["target"],
                }
            ]
        )
        result = LayoutResult(
            1,
            {
                "source": Rect(0, 0, 3000, 3000),
                "target": Rect(3500, 0, 2500, 3000),
            },
        )
        routes = route_flows(building, result)
        flow_report = validate_flow_routes(building, result, routes)
        report = validate_building(
            building,
            result,
            EquipmentLayoutResult(()),
            routes,
            flow_report,
            profile=default_facility_profile(),
        )

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "coordination.bcf"
            summary = write_bcf_package(
                path,
                report,
                project_name="facility test",
                variant=1,
                model_references={"ifc": "building_01.ifc", "pdf": "building_01.pdf"},
            )
            reread = read_bcf_package(path)

            self.assertEqual(summary.version, "2.1")
            self.assertEqual(len(reread.topics), len(report.issues))
            self.assertEqual(reread.open_topics, len(report.issues))
            self.assertTrue(all(topic.issue_id for topic in reread.topics))
            self.assertTrue(all(topic.viewpoint_id and topic.has_snapshot for topic in reread.topics))
            with ZipFile(path) as archive:
                names = set(archive.namelist())
            self.assertIn("bcf.version", names)
            self.assertIn("project.bcfp", names)
            self.assertTrue(any(name.endswith("/markup.bcf") for name in names))
            self.assertTrue(any(name.endswith(".bcfv") for name in names))

    def test_bcf_history_carries_disappeared_issue_as_resolved(self):
        building = _building(
            flows=[
                {
                    "id": "required_material",
                    "type": "material",
                    "from_ids": ["source"],
                    "to_ids": ["target"],
                }
            ]
        )
        result = LayoutResult(
            1,
            {
                "source": Rect(0, 0, 3000, 3000),
                "target": Rect(3500, 0, 2500, 3000),
            },
        )
        routes = route_flows(building, result)
        report = validate_building(
            building,
            result,
            EquipmentLayoutResult(()),
            routes,
            validate_flow_routes(building, result, routes),
            profile=default_facility_profile(),
        )
        clean_report = FacilityValidationReport(
            profile=default_facility_profile(),
            checks=(),
            issues=(),
        )

        with tempfile.TemporaryDirectory() as directory:
            previous = Path(directory) / "previous.bcf"
            current = Path(directory) / "current.bcf"
            write_bcf_package(previous, report, project_name="facility test")
            summary = write_bcf_package(
                current,
                clean_report,
                project_name="facility test",
                previous=previous,
            )
            self.assertEqual(len(summary.topics), len(report.issues))
            self.assertEqual(summary.open_topics, 0)
            self.assertEqual(summary.resolved_topics, len(report.issues))
            self.assertTrue(all(topic.status == "Closed" for topic in summary.topics))


def _building(*, zones=(), flows=()):
    return BuildingIR.from_mapping(
        {
            "version": "0.2",
            "project_name": "facility test",
            "layout": {
                "boundary": {"width": 6000, "height": 3000},
                "entry_room": "source",
                "door_width_mm": 900,
                "rooms": [
                    {
                        "id": "source",
                        "type": "room",
                        "target_area": 9,
                        "min_area": 1,
                        "max_area": 20,
                        "min_width": 1800,
                        "min_depth": 1800,
                        "required_adjacency": ["target"],
                    },
                    {
                        "id": "target",
                        "type": "room",
                        "target_area": 9,
                        "min_area": 1,
                        "max_area": 20,
                        "min_width": 1800,
                        "min_depth": 1800,
                        "required_adjacency": ["source"],
                    },
                ],
            },
            "zones": list(zones),
            "equipment": [],
            "flows": list(flows),
        }
    )


if __name__ == "__main__":
    unittest.main()

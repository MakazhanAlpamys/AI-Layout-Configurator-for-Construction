import json
import tempfile
import unittest
from pathlib import Path
from zipfile import ZipFile

from jsonschema import Draft202012Validator

from layout_configurator.building import BuildingIR
from layout_configurator.bcf import read_bcf_package, write_bcf_package
from layout_configurator.equipment import EquipmentLayoutResult
from layout_configurator.facility import (
    FacilityValidationReport,
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

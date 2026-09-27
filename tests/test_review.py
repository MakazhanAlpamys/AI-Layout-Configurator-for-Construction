import json
import tempfile
import unittest
from pathlib import Path

from layout_configurator.cli import main
from layout_configurator.equipment import place_equipment, validate_equipment_layout
from layout_configurator.facility import load_facility_profile, validate_building
from layout_configurator.flows import route_flows, validate_flow_routes
from layout_configurator.io import load_building, write_building_result
from layout_configurator.review import write_review_dossier
from layout_configurator.solver import solve_layouts

PROFILE = "rules/pharma_cleanroom_pilot.yaml"
PROGRAM = "examples/pharma_cleanroom_pilot.yaml"


_CACHED_PILOT = None


def _pilot_result():
    # Solving the pilot takes tens of seconds, and every test in this module
    # wants the same accepted result, so solve once per process.
    global _CACHED_PILOT
    if _CACHED_PILOT is not None:
        return _CACHED_PILOT
    building = load_building(PROGRAM)
    result = solve_layouts(building.layout, variants=1, time_limit_seconds=60, seed=1)[0]
    equipment = place_equipment(building, result, time_limit_seconds=30)
    routes = route_flows(building, result, equipment)
    profile = load_facility_profile(PROFILE)
    equipment_report = validate_equipment_layout(building, result, equipment)
    flow_report = validate_flow_routes(building, result, routes, equipment)
    report = validate_building(
        building,
        result,
        equipment,
        routes,
        flow_report,
        profile=profile,
        equipment_report=equipment_report,
    )
    _CACHED_PILOT = (building, result, equipment, routes, report, profile)
    return _CACHED_PILOT


class ReviewDossierTests(unittest.TestCase):
    """The external gates get a document per role, not a pile of JSON."""

    def test_dossier_carries_the_declarations_each_role_must_confirm(self):
        building, result, equipment, routes, report, profile = _pilot_result()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "building_01.json"
            write_building_result(source, building, result, equipment)

            written = write_review_dossier(
                root / "review", building, result, equipment, routes, report, profile, source=source
            )

            technologist = written["technologist.md"].read_text(encoding="utf-8")
            cleanroom = written["cleanroom-hvac.md"].read_text(encoding="utf-8")
            architect = written["architect-bim.md"].read_text(encoding="utf-8")

            # Every declared flow reaches the technologist, with its stage.
            for flow in building.flows:
                self.assertIn(flow.id, technologist)
            self.assertIn("raw_material", technologist)
            self.assertIn("waste", technologist)
            # The separation policy is stated rather than implied by a PASS.
            for first, second in profile.incompatible_flow_type_pairs:
                self.assertIn(first, technologist)
                self.assertIn(second, technologist)

            # The cleanroom reviewer sees declared classes, pressures and airlocks.
            for zone in building.zones:
                self.assertIn(zone.id, cleanroom)
            self.assertIn("ISO 7", cleanroom)
            self.assertIn("personnel_airlock", cleanroom)
            self.assertIn("material_airlock", cleanroom)

            # The architect gets the exchange inventory and the sheet identity.
            self.assertIn(profile.drawing.sheet_id, architect)
            for placement in equipment.placements:
                self.assertIn(placement.equipment_id, architect)

    def test_every_dossier_states_the_boundary_and_the_return_path(self):
        building, result, equipment, routes, report, profile = _pilot_result()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "building_01.json"
            write_building_result(source, building, result, equipment)

            written = write_review_dossier(
                root / "review", building, result, equipment, routes, report, profile, source=source
            )

            for name in ("technologist.md", "cleanroom-hvac.md", "architect-bim.md"):
                text = written[name].read_text(encoding="utf-8")
                self.assertIn("It is not", text, f"{name} must state what the package is not")
                self.assertIn("Outside the scope of the tool", text)
                self.assertIn("BCF-topic", text)
                self.assertIn("building_01.bcf", text)

    def test_cli_writes_a_dossier_for_every_variant_of_a_bundle(self):
        building, result, equipment, routes, report, profile = _pilot_result()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bundle = root / "bundle"
            bundle.mkdir()
            for variant in (1, 2):
                write_building_result(bundle / f"building_{variant:02d}.json", building, result, equipment)
            # A coordination sidecar must not be mistaken for a result.
            (bundle / "building_01.coordination.json").write_text("{}", encoding="utf-8")

            code = main(
                [
                    "review-dossier",
                    str(bundle),
                    "--profile",
                    PROFILE,
                    "--output",
                    str(root / "review"),
                ]
            )

            self.assertEqual(code, 0)
            for variant in ("building_01", "building_02"):
                for name in ("technologist", "cleanroom-hvac", "architect-bim"):
                    self.assertTrue((root / "review" / f"{variant}.{name}.md").is_file())
                inventory = json.loads(
                    (root / "review" / f"{variant}.artifact-inventory.json").read_text(encoding="utf-8")
                )
                self.assertIn("sha256", inventory)


if __name__ == "__main__":
    unittest.main()

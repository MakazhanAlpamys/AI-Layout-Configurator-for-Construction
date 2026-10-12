"""The browser producer validates raw canonical input before model coercion."""

import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import unittest

from layout_configurator.io import load_building
from layout_configurator.facility import default_facility_profile


PROJECT = Path(__file__).resolve().parents[1]


class ProgramFormBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(importlib.util.find_spec("layout_configurator.program_form"),
                             "The strict browser program boundary is missing")
        from layout_configurator.program_form import validate_program_form, ProgramFormError
        self.validate = validate_program_form
        self.error = ProgramFormError
        self.program = load_building(PROJECT / "examples/pharma_cleanroom_pilot.yaml").to_dict()

    def test_canonical_pilot_retains_every_ordered_semantic_field_and_exact_identity(self):
        response = self.validate({"program": self.program})
        self.assertEqual(response["program"], self.program)
        self.assertEqual(response["profile"], default_facility_profile().to_dict())
        expected = hashlib.sha256(json.dumps(self.program, sort_keys=True, ensure_ascii=False,
                                             allow_nan=False, separators=(",", ":")).encode()).hexdigest()
        self.assertEqual(response["program_sha256"], expected)
        self.assertEqual(response["status"], "VALID_INPUT")
        self.assertEqual(response["engineering_acceptance"], "PENDING")

    def test_raw_nested_input_rejects_coercion_unknown_geometry_and_bad_references(self):
        def set_value(path, value):
            program = copy.deepcopy(self.program)
            target = program
            for part in path[:-1]:
                target = target[part]
            target[path[-1]] = value
            return program

        cases = [
            (("layout", "grid_mm"), "100"),
            (("layout", "grid_mm"), True),
            (("layout", "rooms", 0, "min_width"), True),
            (("layout", "rooms", 0, "needs_daylight"), "false"),
            (("layout", "rooms", 0, "coordinates"), [0, 0]),
            (("layout", "boundary", "width"), float("nan")),
            (("layout", "boundary", "width"), 10 ** 400),
            (("layout", "rooms", 0, "type"), "\ud800"),
            (("layout", "entry_room"), "missing"),
            (("layout", "rooms", 1, "id"), "receiving"),
            (("equipment", 0, "room_id"), "missing"),
            (("flows", 0, "from_ids"), ["missing"]),
            (("zones", 1, "parent_zone_id"), "personnel_gateway"),
            (("project_name",), "A different project"),
        ]
        for path, value in cases:
            with self.subTest(path=path, value=value):
                with self.assertRaises(self.error) as raised:
                    self.validate({"program": set_value(path, value)})
                self.assertTrue(raised.exception.issues)
                self.assertTrue(all(issue["path"].startswith("$.program") for issue in raised.exception.issues))

    def test_profile_and_envelope_are_strict_without_silently_dropping_fields(self):
        for payload in ({"program": self.program, "output": "outside"},
                        {"program": self.program, "profile": {"name": "fake"}},
                        {"program": self.program, "profile": {**default_facility_profile().to_dict(), "approval": True}},
                        {"program": None}, {}, []):
            with self.subTest(payload=payload), self.assertRaises(self.error):
                self.validate(payload)

    def test_drawing_subprofile_rejects_unknown_fields_and_primitive_coercion(self):
        for key, value in (("certified", True), ("show_flow_labels", "false"),
                           ("legend_offset_x_mm", "900"), ("legend_offset_y_mm", True),
                           ("revision", 1), ("title", "   ")):
            profile = default_facility_profile().to_dict()
            profile["drawing"][key] = value
            with self.subTest(key=key, value=value):
                with self.assertRaises(self.error) as raised:
                    self.validate({"program": self.program, "profile": profile})
                self.assertTrue(any(issue["path"].startswith("$.profile.drawing") for issue in raised.exception.issues))

    def test_named_drawing_preset_is_resolved_to_effective_canonical_settings(self):
        profile = default_facility_profile().to_dict()
        profile["drawing"] = {"preset": "equipment_service", "revision": "P02"}
        response = self.validate({"program": self.program, "profile": profile})
        self.assertEqual(response["profile"]["drawing"]["revision"], "P02")
        self.assertFalse(response["profile"]["drawing"]["show_room_dimensions"])
        self.assertNotIn("preset", response["profile"]["drawing"])

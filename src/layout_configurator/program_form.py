"""Strict canonical input boundary for the browser program producer.

Validation establishes input identity only. Geometry, exchange checks and human
engineering acceptance remain separate steps in the existing job pipeline.
"""

from __future__ import annotations

import hashlib
import json

from .building import BuildingIR
from .facility import (DEFAULT_FACILITY_PROFILE_SCHEMA_PATH, FacilityProfile,
                       default_facility_profile, load_facility_profile)
from .resources import resource_path
from .drawing_profiles import resolve_drawing_settings
from .drawing_symbols import validate_symbol_assignments
from .facility_generation import FACILITY_SOLVER_STRATEGIES
from .schema import (DEFAULT_BUILDING_SCHEMA_PATH, DEFAULT_SCHEMA_PATH,
                     load_json_schema, validate_mapping)


class ProgramFormError(ValueError):
    def __init__(self, issues):
        self.issues = list(issues)
        super().__init__("; ".join(f"{issue['path']}: {issue['message']}" for issue in self.issues))


def _fail(path, message):
    raise ProgramFormError([{"path": path, "message": message}])


def _schema_issues(value, schema, prefix):
    return [{"path": prefix + issue.path[1:], "message": issue.message}
            for issue in validate_mapping(value, schema)]


def validate_program_form(payload):
    """Check raw types in both IR schemas, then resolve domain references."""
    if not isinstance(payload, dict) or set(payload) - {"program", "profile"} or "program" not in payload:
        _fail("$", "Expected an object with program and optional profile only")
    try:
        json.dumps(payload["program"], ensure_ascii=False, allow_nan=False).encode("utf-8")
    except (ValueError, TypeError, UnicodeError, OverflowError) as exc:
        _fail("$.program", f"Program must contain finite JSON values: {exc}")
    raw = payload["program"]
    issues = _schema_issues(raw, DEFAULT_BUILDING_SCHEMA_PATH, "$.program")
    if isinstance(raw, dict) and isinstance(raw.get("layout"), dict):
        issues.extend(_schema_issues(raw["layout"], DEFAULT_SCHEMA_PATH, "$.program.layout"))
    if issues:
        raise ProgramFormError(issues)
    if raw["project_name"] != raw["layout"]["project_name"]:
        _fail("$.program.project_name", "Must match layout.project_name")
    try:
        building = BuildingIR.from_mapping(raw)
    except (ValueError, TypeError, OverflowError) as exc:
        _fail("$.program", str(exc))
    profile_raw = payload.get("profile")
    if "profile" in payload:
        try:
            json.dumps(profile_raw, ensure_ascii=False, allow_nan=False).encode("utf-8")
        except (ValueError, TypeError, UnicodeError, OverflowError) as exc:
            _fail("$.profile", f"Profile must contain finite JSON values: {exc}")
        issues = _schema_issues(profile_raw, DEFAULT_FACILITY_PROFILE_SCHEMA_PATH, "$.profile")
        if issues:
            raise ProgramFormError(issues)
        drawing = profile_raw.get("drawing", {})
        if not isinstance(drawing, dict):
            _fail("$.profile.drawing", "Must be an object")
        try:
            resolve_drawing_settings(drawing)
        except ValueError as exc:
            _fail("$.profile.drawing", str(exc))
        try:
            profile = FacilityProfile.from_mapping(profile_raw)
        except (ValueError, TypeError, OverflowError) as exc:
            _fail("$.profile", str(exc))
    else:
        profile = default_facility_profile()
    try:
        validate_symbol_assignments(profile.drawing, building.equipment)
    except (ValueError, TypeError) as exc:
        _fail("$.profile.drawing.equipment_symbols", str(exc))
    program = building.to_dict()
    canonical = json.dumps(program, sort_keys=True, ensure_ascii=False,
                           allow_nan=False, separators=(",", ":")).encode("utf-8")
    return {"status": "VALID_INPUT", "program": program, "profile": profile.to_dict(),
            "program_sha256": hashlib.sha256(canonical).hexdigest(),
            "engineering_acceptance": "PENDING"}


def program_form_config():
    """Public field definitions and packaged profiles; no filesystem selectors."""
    default = default_facility_profile()
    pilot = load_facility_profile(resource_path("rules/pharma_cleanroom_pilot.yaml"))
    return {
        "schemas": {"building": load_json_schema(DEFAULT_BUILDING_SCHEMA_PATH),
                    "layout": load_json_schema(DEFAULT_SCHEMA_PATH)},
        "defaults": {"version": "0.1", "project_name": "", "tolerance": 0.03,
                     "grid_mm": 100, "wall_thickness_mm": 200, "door_width_mm": 900,
                     "window_width_mm": 1200, "window_height_mm": 1500, "window_sill_mm": 900,
                     "entry_room": "", "boundary": {"width": 10000, "height": 10000, "cutouts": []},
                     "rooms": [], "doors": [], "windows": []},
        "profiles": [{"id": "default", "profile": default.to_dict()},
                     {"id": "pharma-cleanroom", "profile": pilot.to_dict()}],
        "engineering_acceptance": "PENDING",
        "room_solvers": list(FACILITY_SOLVER_STRATEGIES),
    }

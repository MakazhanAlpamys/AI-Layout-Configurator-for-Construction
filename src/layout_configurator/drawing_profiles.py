"""Named presentation presets resolved into the existing drawing contract."""

from collections.abc import Mapping
import math


_PRESETS = {
    "coordination_review": {},
    "architecture_dimensions": {
        "show_room_dimensions": True, "show_structural_grid": True,
        "show_dimension_chains": True,
        "show_equipment_clearance": False, "show_flow_legend": False,
        "show_flow_labels": False,
    },
    "equipment_service": {
        "show_room_dimensions": False, "show_equipment_clearance": True,
        "show_flow_legend": False, "show_flow_labels": False,
    },
    "process_flows": {
        "show_room_dimensions": False, "show_equipment_clearance": False,
        "show_flow_legend": True, "show_flow_labels": True,
    },
}


def drawing_profile_catalog() -> dict[str, dict]:
    """Return fresh effective settings; callers cannot mutate the preset table."""
    from .facility import FacilityDrawingProfile

    defaults = FacilityDrawingProfile().to_dict()
    return {name: {**defaults, **overrides} for name, overrides in _PRESETS.items()}


def resolve_drawing_settings(raw: Mapping) -> dict:
    """Strictly validate overrides and expand a named presentation preset.

    These settings change drawing projection only. They add no geometry,
    engineering standard, rule thresholds or approval. Resolved settings use
    FacilityDrawingProfile's existing canonical fields and defaults.
    """
    from .facility import FacilityDrawingProfile

    if not isinstance(raw, Mapping):
        raise ValueError("Drawing settings must be an object")
    defaults = FacilityDrawingProfile().to_dict()
    if set(raw) - set(defaults) - {"preset", "equipment_symbols", "show_dimension_chains"}:
        raise ValueError("Drawing settings contain unknown fields")
    preset = raw.get("preset", "coordination_review")
    if not isinstance(preset, str) or preset not in _PRESETS:
        raise ValueError("Unknown drawing preset; use " + ", ".join(_PRESETS))
    for key, value in raw.items():
        if key == "preset":
            continue
        if key == "equipment_symbols":
            from .drawing_symbols import parse_symbol_assignments

            parse_symbol_assignments(value)
            continue
        if key == 'show_dimension_chains':
            if type(value) is not bool:
                raise ValueError('Drawing show_dimension_chains must be a boolean')
            continue
        if isinstance(defaults[key], bool):
            if type(value) is not bool:
                raise ValueError(f"Drawing {key} must be a boolean")
        elif isinstance(defaults[key], str):
            if not isinstance(value, str) or not value.strip() or len(value) > 1024:
                raise ValueError(f"Drawing {key} must be nonempty text of at most 1024 characters")
        else:
            try:
                finite = not isinstance(value, bool) and isinstance(value, (int, float)) and math.isfinite(value)
            except OverflowError:
                finite = False
            if not finite or value < 0:
                raise ValueError(f"Drawing {key} must be a finite nonnegative number")
    return {**defaults, **_PRESETS[preset], **{key: value for key, value in raw.items() if key != "preset"}}

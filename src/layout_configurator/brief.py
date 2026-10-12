"""Constrained text-to-LayoutIR parsing boundary.

The built-in parser is intentionally small and deterministic. A future LLM can
replace only the text interpretation step, but its output must still enter via
``parse_llm_mapping`` and the canonical JSON Schema before it reaches the
solver. No parser in this module accepts coordinates or evaluates rules.
"""

from __future__ import annotations

import re
from typing import Any, Mapping

from .models import LayoutIR
from .schema import DEFAULT_SCHEMA_PATH, normalize_mapping


# Russian room names accepted in text briefs (parser vocabulary), mapped to room types.
ROOM_TYPE_ALIASES = {
    "\u0433\u043e\u0441\u0442\u0438\u043d\u0430\u044f": "living_room",
    "\u0436\u0438\u043b\u0430\u044f": "living_room",
    "\u043a\u0443\u0445\u043d\u044f": "kitchen",
    "\u0441\u0442\u043e\u043b\u043e\u0432\u0430\u044f": "dining_room",
    "\u0441\u043f\u0430\u043b\u044c\u043d\u044f": "bedroom",
    "\u0434\u0435\u0442\u0441\u043a\u0430\u044f": "bedroom",
    "\u0445\u043e\u043b\u043b": "corridor",
    "\u043a\u043e\u0440\u0438\u0434\u043e\u0440": "corridor",
    "\u0442\u0430\u043c\u0431\u0443\u0440": "vestibule",
    "\u043f\u0440\u0438\u0445\u043e\u0436\u0430\u044f": "entry_lobby",
    "\u0432\u0430\u043d\u043d\u0430\u044f": "bathroom",
    "\u0441\u0430\u043d\u0443\u0437\u0435\u043b": "bathroom",
    "\u0442\u0435\u0440\u0440\u0430\u0441\u0430": "terrace",
    "\u0432\u0435\u0440\u0430\u043d\u0434\u0430": "veranda",
    "\u043b\u043e\u0434\u0436\u0438\u044f": "loggia",
}


def parse_llm_mapping(mapping: Mapping[str, Any], schema_path: str = str(DEFAULT_SCHEMA_PATH)) -> LayoutIR:
    """Validate one future LLM-produced canonical object before LayoutIR use."""

    return normalize_mapping(mapping, schema_path)


def parse_text_brief(text: str, schema_path: str = str(DEFAULT_SCHEMA_PATH)) -> LayoutIR:
    """Parse a compact human brief into canonical LayoutIR without geometry."""

    project_name = "Layout"
    boundary: tuple[float, float] | None = None
    entry_room = ""
    rooms: list[dict[str, Any]] = []
    for raw_line in str(text).splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        lower = line.lower().replace("\u0451", "\u0435")
        if lower.startswith(("\u043f\u0440\u043e\u0435\u043a\u0442", "project", "\u043d\u0430\u0437\u0432\u0430\u043d\u0438\u0435", "name")):
            project_name = _value_after_separator(line) or project_name
            continue
        if lower.startswith(("\u043a\u043e\u043d\u0442\u0443\u0440", "\u0433\u0430\u0431\u0430\u0440\u0438\u0442\u044b", "boundary", "\u0440\u0430\u0437\u043c\u0435\u0440")):
            boundary = _parse_dimensions(line)
            continue
        if lower.startswith(("\u0432\u0445\u043e\u0434", "entry_room", "entry")):
            entry_room = _value_after_separator(line).strip()
            continue
        if lower in {"\u043a\u043e\u043c\u043d\u0430\u0442\u044b:", "rooms:", "\u043f\u043e\u043c\u0435\u0449\u0435\u043d\u0438\u044f:"}:
            continue
        if lower.startswith(("\u043a\u043e\u043c\u043d\u0430\u0442\u0430", "room", "-")) or "|" in line:
            rooms.append(_parse_room(line))

    if boundary is None:
        raise ValueError("Brief must define boundary as WIDTH x HEIGHT in millimetres")
    if not rooms:
        raise ValueError("Brief must define at least one room")
    room_ids = {str(room["id"]) for room in rooms}
    if not entry_room:
        entry_room = str(rooms[0]["id"])
    if entry_room not in room_ids:
        raise ValueError(f"entry_room {entry_room!r} is not present in rooms")

    canonical = {
        "version": "0.1",
        "project_name": project_name,
        "tolerance": 0.03,
        "grid_mm": 100,
        "wall_thickness_mm": 200,
        "door_width_mm": 900,
        "window_width_mm": 1200,
        "window_height_mm": 1500,
        "window_sill_mm": 900,
        "doors": [],
        "windows": [],
        "entry_room": entry_room,
        "boundary": {"width": boundary[0], "height": boundary[1], "cutouts": []},
        "rooms": rooms,
    }
    return parse_llm_mapping(canonical, schema_path)


def _value_after_separator(line: str) -> str:
    _, separator, value = line.partition(":")
    if not separator:
        _, separator, value = line.partition("=")
    return value.strip()


def _parse_dimensions(line: str) -> tuple[float, float]:
    values = re.findall(r"\d+(?:[.,]\d+)?", line)
    if len(values) < 2:
        raise ValueError("Boundary must contain two dimensions")
    width, height = (float(value.replace(",", ".")) for value in values[:2])
    if width <= 0 or height <= 0:
        raise ValueError("Boundary dimensions must be positive")
    return width, height


def _parse_room(line: str) -> dict[str, Any]:
    body = line.lstrip("- ").strip()
    if ":" in body and body.split(":", 1)[0].lower() in {"\u043a\u043e\u043c\u043d\u0430\u0442\u0430", "room"}:
        body = body.split(":", 1)[1].strip()
    if "|" in body:
        fields = [part.strip() for part in body.split("|")]
        if len(fields) < 3:
            raise ValueError("Room pipe format is: id | type | area_m2 | required_a,required_b | flags")
        room_id, room_type, area = fields[:3]
        required = fields[3] if len(fields) > 3 else ""
        flags = " ".join(fields[4:])
        values: dict[str, str] = {
            "id": room_id,
            "type": room_type,
            "area": area,
            "required": required,
            "flags": flags,
        }
    else:
        values = _key_values(body)
        if "id" not in values:
            first = body.split(",", 1)[0].strip()
            values["id"] = first
    room_id = values.get("id", "").strip()
    if not room_id:
        raise ValueError("Every room in a brief needs an id")
    room_type = _normalize_room_type(values.get("type", room_id))
    area = _parse_area(values.get("area", values.get("target_area", "")))
    required_raw = values.get("required", values.get("adjacency", values.get("adjacent", "")))
    required = [item.strip() for item in re.split(r"[,;/+]+", required_raw) if item.strip()]
    flags = f"{values.get('flags', '')} {body}".lower().replace("\u0451", "\u0435")
    return {
        "id": room_id,
        "type": room_type,
        "target_area": area,
        "min_area": area * 0.97,
        "max_area": area * 1.03,
        "min_width": float(values.get("min_width", 1800)),
        "min_depth": float(values.get("min_depth", 1800)),
        "required_adjacency": required,
        "preferred_adjacency": [],
        "forbidden_adjacency": [],
        "needs_daylight": any(word in flags for word in ("daylight", "\u043e\u0441\u0432\u0435\u0449", "\u0441\u0432\u0435\u0442")),
        "is_heated": any(word in flags for word in ("heated", "\u043e\u0442\u0430\u043f", "\u0442\u0435\u043f\u043b")),
    }


def _key_values(body: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for part in re.split(r"[;,]", body):
        if ":" in part:
            key, value = part.split(":", 1)
        elif "=" in part:
            key, value = part.split("=", 1)
        else:
            continue
        values[key.strip().lower().replace(" ", "_")] = value.strip()
    return values


def _normalize_room_type(value: str) -> str:
    normalized = value.strip().lower().replace("\u0451", "\u0435")
    return ROOM_TYPE_ALIASES.get(normalized, normalized or "room")


def _parse_area(value: str) -> float:
    match = re.search(r"\d+(?:[.,]\d+)?", value)
    if not match:
        raise ValueError("Room must define area in m2")
    area = float(match.group(0).replace(",", "."))
    if area <= 0:
        raise ValueError("Room area must be positive")
    return area

"""JSON Schema boundary for canonical ``LayoutIR`` documents."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any, Mapping

from .models import LayoutIR

DEFAULT_SCHEMA_PATH = Path(__file__).resolve().parents[2] / "schemas" / "layout_ir.schema.json"


@dataclass(frozen=True)
class SchemaIssue:
    path: str
    message: str

    def to_dict(self) -> dict[str, str]:
        return {"path": self.path, "message": self.message}


def load_json_schema(path: str | Path = DEFAULT_SCHEMA_PATH) -> dict[str, Any]:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("JSON Schema root must be an object")
    return raw


def validate_mapping(mapping: Mapping[str, Any], schema_path: str | Path = DEFAULT_SCHEMA_PATH) -> tuple[SchemaIssue, ...]:
    """Validate one canonical mapping and return stable, printable issues."""

    if not isinstance(mapping, Mapping):
        return (SchemaIssue("$", "document must be an object"),)
    try:
        from jsonschema import Draft202012Validator
    except ImportError as exc:  # pragma: no cover - dependency is declared in pyproject
        raise RuntimeError("jsonschema is required; reinstall the project dependencies") from exc
    validator = Draft202012Validator(load_json_schema(schema_path))
    issues = []
    for error in sorted(validator.iter_errors(mapping), key=lambda item: tuple(item.absolute_path)):
        path = "$"
        for part in error.absolute_path:
            path += f"[{part}]" if isinstance(part, int) else f".{part}"
        issues.append(SchemaIssue(path, error.message))
    return tuple(issues)


def validate_spec(spec, schema_path: str | Path = DEFAULT_SCHEMA_PATH) -> tuple[SchemaIssue, ...]:
    """Validate the normalized serialization emitted by ``LayoutIR.to_dict``."""

    return validate_mapping(spec.to_dict(), schema_path)


def normalize_mapping(mapping: Mapping[str, Any], schema_path: str | Path = DEFAULT_SCHEMA_PATH) -> LayoutIR:
    """Validate a canonical mapping before converting it to ``LayoutIR``.

    This is the strict hand-off for a future text/LLM producer: unknown fields,
    coordinates and generated layout data are rejected by the schema first.
    """

    issues = validate_mapping(mapping, schema_path)
    if issues:
        details = "; ".join(f"{issue.path}: {issue.message}" for issue in issues)
        raise ValueError(f"Canonical LayoutIR does not match schema: {details}")
    return LayoutIR.from_mapping(mapping)


def load_mapping(path: str | Path) -> Mapping[str, Any]:
    """Load a raw JSON/YAML mapping without silently normalizing unknown fields."""

    source = Path(path)
    text = source.read_text(encoding="utf-8")
    if source.suffix.lower() == ".json":
        raw = json.loads(text)
    elif source.suffix.lower() in {".yaml", ".yml"}:
        import yaml

        raw = yaml.safe_load(text)
    else:
        raise ValueError("Schema input must have .json, .yaml or .yml extension")
    if not isinstance(raw, Mapping):
        raise ValueError("Schema input root must be an object")
    return raw


def load_canonical_spec(path: str | Path, schema_path: str | Path = DEFAULT_SCHEMA_PATH) -> LayoutIR:
    """Load a file through the strict canonical LayoutIR boundary."""

    return normalize_mapping(load_mapping(path), schema_path)

"""Persistent review workflow state for facility coordination issues."""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any, Mapping

from .schema import validate_mapping


ISSUE_MANAGEMENT_VERSION = "0.1"
DEFAULT_ISSUE_MANAGEMENT_SCHEMA_PATH = Path(__file__).resolve().parents[2] / "schemas" / "issue_management.schema.json"


def new_issue_management_state() -> dict[str, Any]:
    return {
        "format": "FLC-BCF-issue-management",
        "version": ISSUE_MANAGEMENT_VERSION,
        "issues": [],
    }


def load_issue_management(path: str | Path) -> dict[str, Any]:
    """Load and validate a local issue workflow sidecar."""

    source = Path(path)
    if not source.is_file():
        return new_issue_management_state()
    try:
        raw = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ValueError(f"Issue management sidecar is not valid JSON: {source}") from exc
    if not isinstance(raw, Mapping):
        raise ValueError("Issue management sidecar root must be an object")
    issues = validate_mapping(raw, DEFAULT_ISSUE_MANAGEMENT_SCHEMA_PATH)
    if issues:
        details = "; ".join(f"{issue.path}: {issue.message}" for issue in issues)
        raise ValueError(f"Issue management sidecar is invalid: {details}")
    return dict(raw)


def write_issue_management(path: str | Path, state: Mapping[str, Any]) -> None:
    """Write one schema-valid issue workflow sidecar."""

    payload = dict(state)
    payload.setdefault("format", "FLC-BCF-issue-management")
    payload.setdefault("version", ISSUE_MANAGEMENT_VERSION)
    issues = validate_mapping(payload, DEFAULT_ISSUE_MANAGEMENT_SCHEMA_PATH)
    if issues:
        details = "; ".join(f"{issue.path}: {issue.message}" for issue in issues)
        raise ValueError(f"Issue management state is invalid: {details}")
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def utc_timestamp() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def issue_state_map(state: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        str(item["issue_id"]): dict(item)
        for item in state.get("issues", [])
        if isinstance(item, Mapping) and item.get("issue_id")
    }


def replace_issue_state(state: Mapping[str, Any], records: Mapping[str, Mapping[str, Any]], *, updated_at: str) -> dict[str, Any]:
    payload = dict(state)
    payload["format"] = "FLC-BCF-issue-management"
    payload["version"] = ISSUE_MANAGEMENT_VERSION
    payload["updated_at"] = updated_at
    payload["issues"] = [dict(records[key]) for key in sorted(records)]
    return payload

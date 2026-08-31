"""Optional provider adapter for the text-to-LayoutIR boundary.

The adapter speaks the common chat-completions JSON shape but keeps the
provider optional. It sends a text brief plus the input contract, accepts only
the model's JSON object, and immediately validates it through ``parse_llm_mapping``.
The provider never receives a solved layout, export files, or a norms verdict.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Mapping
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .brief import parse_llm_mapping
from .models import LayoutIR
from .schema import DEFAULT_SCHEMA_PATH, load_json_schema


def parse_with_openai_compatible(
    text: str,
    *,
    endpoint: str,
    model: str,
    api_key: str,
    schema_path: str | Path = DEFAULT_SCHEMA_PATH,
    timeout_seconds: float = 60,
) -> LayoutIR:
    """Call an opt-in compatible provider and enforce canonical LayoutIR output."""

    if not str(endpoint).strip() or not str(model).strip() or not str(api_key).strip():
        raise ValueError("LLM endpoint, model and API key are required")
    schema = json.dumps(load_json_schema(schema_path), ensure_ascii=False, separators=(",", ":"))
    prompt = (
        "Convert the following architectural brief into exactly one canonical "
        "LayoutIR JSON object. Return JSON only. Never return coordinates, "
        "placements, DXF/PDF/IFC data, or a building-code verdict. Use only the "
        "fields allowed by this schema.\n\n"
        f"JSON Schema:\n{schema}\n\nBrief:\n{text}"
    )
    request = Request(
        str(endpoint),
        data=json.dumps(
            {
                "model": model,
                "temperature": 0,
                "messages": [
                    {"role": "system", "content": "You produce constrained architectural input JSON."},
                    {"role": "user", "content": prompt},
                ],
                "response_format": {"type": "json_object"},
            },
            ensure_ascii=False,
        ).encode("utf-8"),
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=timeout_seconds) as response:
            raw = response.read(2_000_000)
    except (HTTPError, URLError, TimeoutError) as exc:
        raise ValueError(f"LLM provider request failed: {exc}") from exc
    try:
        envelope = json.loads(raw)
        content = envelope["choices"][0]["message"]["content"]
        if not isinstance(content, str):
            raise ValueError("LLM message content must be a JSON string")
        mapping = json.loads(_strip_json_fence(content))
    except (KeyError, IndexError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ValueError(f"LLM provider returned invalid JSON response: {exc}") from exc
    if not isinstance(mapping, Mapping):
        raise ValueError("LLM response must be a JSON object")
    return parse_llm_mapping(mapping, str(schema_path))


def llm_settings_from_environment() -> dict[str, str]:
    """Read opt-in settings without exposing the secret in logs or output."""

    return {
        "endpoint": os.environ.get("LAYOUT_LLM_ENDPOINT", ""),
        "model": os.environ.get("LAYOUT_LLM_MODEL", ""),
        "api_key": os.environ.get("LAYOUT_LLM_API_KEY", ""),
    }


def _strip_json_fence(content: str) -> str:
    value = content.strip()
    if value.startswith("```") and value.endswith("```"):
        value = value[3:-3].strip()
        if value.lower().startswith("json"):
            value = value[4:].lstrip()
    return value

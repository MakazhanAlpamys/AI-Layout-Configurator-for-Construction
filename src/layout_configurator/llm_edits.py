"""Opt-in chat-compatible provider adapter for strict program proposals."""

from __future__ import annotations

import json
import math
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .building import BuildingIR
from .llm import _strip_json_fence
from .proposed_edits import PROPOSED_EDITS_SCHEMA_PATH, ProposedEditsDraft, propose_edits, _unique_object
from .schema import load_json_schema, normalize_building_mapping, normalize_mapping


def propose_with_compatible_provider(
    building: BuildingIR,
    text: str,
    *,
    endpoint: str,
    model: str,
    api_key: str,
    timeout_seconds: float = 60,
) -> ProposedEditsDraft:
    """Request typed edits and validate them into an unconfirmed DRAFT.

    The provider receives canonical input and the operation catalog. Model
    output goes through the same quantity, reference and schema checks as
    manual proposals. Confirmation and regeneration remain separate steps.
    """
    if any(not isinstance(value, str) or not value.strip() for value in (endpoint, model, api_key, text)):
        raise ValueError("Provider endpoint, model, API key and request text are required")
    if isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, (int, float)) or not math.isfinite(timeout_seconds) or not 0 < timeout_seconds <= 300:
        raise ValueError("Provider timeout must be finite and between 0 and 300 seconds")
    normalize_mapping(building.layout.to_dict())
    canonical = normalize_building_mapping(building.to_dict())
    contract = load_json_schema(PROPOSED_EDITS_SCHEMA_PATH)
    response_schema = {"type": "object", "additionalProperties": False, "required": ["edits"],
                       "properties": {"edits": contract["properties"]["edits"]}, "$defs": contract["$defs"]}
    prompt = (
        "Convert the request into exactly one JSON object containing an edits array. "
        "Use only the operation catalog, known target/reference IDs and explicit mm/m2 quantities. "
        "Return JSON only. Never produce coordinates, placements, export files, changed rules, "
        "classification or a compliance verdict. The proposal requires human confirmation and "
        "a new solver run. If the request cannot be expressed by this catalog, return an empty "
        "edits array so the deterministic boundary rejects it.\n\n"
        f"Response schema:\n{json.dumps(response_schema, ensure_ascii=False)}\n\n"
        f"Canonical program:\n{json.dumps(canonical.to_dict(), ensure_ascii=False)}\n\n"
        f"Request:\n{text}"
    )
    request = Request(endpoint, data=json.dumps({
        "model": model, "temperature": 0, "response_format": {"type": "json_object"},
        "messages": [{"role": "system", "content": "Produce constrained facility program edits as JSON."},
                     {"role": "user", "content": prompt}],
    }, ensure_ascii=False, allow_nan=False).encode(),
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}, method="POST")
    try:
        with urlopen(request, timeout=timeout_seconds) as response:
            raw = response.read(2_000_001)
    except (HTTPError, URLError, ConnectionError, TimeoutError) as exc:
        # Avoid including a provider response, URL query or credential in errors.
        raise ValueError(f"Proposal provider request failed ({type(exc).__name__})") from exc
    if len(raw) > 2_000_000:
        raise ValueError("Proposal provider response exceeds the size limit")
    try:
        envelope = json.loads(raw, object_pairs_hook=_unique_object)
        content = envelope["choices"][0]["message"]["content"]
        if not isinstance(content, str):
            raise ValueError("Message content must contain a JSON string")
        mapping = json.loads(_strip_json_fence(content), object_pairs_hook=_unique_object)
    except (KeyError, IndexError, TypeError, ValueError, UnicodeError) as exc:
        raise ValueError("Proposal provider returned an invalid JSON envelope or content") from exc
    return propose_edits(canonical, mapping, source_text=text, producer=model, source_kind="ai_proposal")

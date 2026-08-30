"""Specification loading and result serialisation."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .models import LayoutIR, LayoutResult, Rect


def load_spec(path: str | Path) -> LayoutIR:
    source = Path(path)
    raw_text = source.read_text(encoding="utf-8")
    if source.suffix.lower() == ".json":
        raw: Any = json.loads(raw_text)
    elif source.suffix.lower() in {".yaml", ".yml"}:
        import yaml

        raw = yaml.safe_load(raw_text)
    else:
        raise ValueError("Файл спецификации должен иметь расширение .json, .yaml или .yml")
    return LayoutIR.from_mapping(raw)


def write_result(path: str | Path, spec: LayoutIR, result: LayoutResult) -> None:
    payload = {"spec": spec.to_dict(), "layout": result.to_dict()}
    Path(path).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def load_result(path: str | Path) -> tuple[LayoutIR, LayoutResult]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    spec = LayoutIR.from_mapping(payload["spec"])
    layout = payload["layout"]
    placements = {
        room_id: Rect(
            x=float(values["x"]),
            y=float(values["y"]),
            width=float(values["width"]),
            height=float(values["height"]),
        )
        for room_id, values in layout["rooms"].items()
    }
    return spec, LayoutResult(
        variant=int(layout.get("variant", 1)),
        placements=placements,
        objective_value=layout.get("objective_value"),
    )

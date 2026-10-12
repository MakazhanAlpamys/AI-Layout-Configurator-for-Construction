"""Explicit project schematic symbols, never manufacturer physical geometry."""

from collections.abc import Mapping
import math


def _ellipse(cx, cy, rx, ry):
    return tuple((cx + rx * math.cos(index * math.pi / 8),
                  cy + ry * math.sin(index * math.pi / 8)) for index in range(16))


_SYMBOLS = {
    "generic": ((((0, 0), (1, 1)), False), (((0, 1), (1, 0)), False)),
    "rectangular_unit": ((((.1, .1), (.9, .1), (.9, .9), (.1, .9)), True),
                         (((.2, .75), (.8, .75)), False)),
    "process_vessel": ((_ellipse(.5, .5, .36, .36), True),
                       (((.5, .2), (.5, .8)), False), (((.25, .5), (.75, .5)), False)),
    "washbasin": ((_ellipse(.5, .42, .35, .28), True),
                  (((.5, .7), (.5, .88), (.65, .88)), False)),
    "toilet": ((((.15, .72), (.85, .72), (.85, .93), (.15, .93)), True),
               (_ellipse(.5, .4, .3, .3), True)),
    "workbench": ((((.08, .12), (.92, .12), (.92, .88), (.08, .88)), True),
                  (((.12, .3), (.88, .3)), False), (((.35, .12), (.35, .3)), False),
                  (((.65, .12), (.65, .3)), False)),
}


def drawing_symbol_catalog() -> dict:
    return {name: {"scope": "Project schematic symbol; not manufacturer geometry",
                   "paths": [{"points": [list(point) for point in points], "closed": closed}
                             for points, closed in paths]} for name, paths in _SYMBOLS.items()}


def parse_symbol_assignments(raw) -> tuple[tuple[str, str], ...]:
    if not isinstance(raw, Mapping) or len(raw) > 1000:
        raise ValueError("equipment_symbols requires a bounded equipment-ID to symbol mapping")
    for equipment_id, symbol in raw.items():
        if not isinstance(equipment_id, str) or not equipment_id.strip() or len(equipment_id) > 1024:
            raise ValueError("Symbol equipment IDs require nonempty bounded text")
        if not isinstance(symbol, str) or symbol not in _SYMBOLS:
            raise ValueError("Unknown project equipment symbol")
    return tuple(sorted(raw.items()))


def validate_symbol_assignments(drawing, equipment_specs) -> dict[str, str]:
    symbols = dict(drawing.equipment_symbols)
    if len(symbols) != len(drawing.equipment_symbols):
        raise ValueError("Duplicate equipment symbol assignments")
    parse_symbol_assignments(symbols)
    unknown = set(symbols) - {item.id for item in equipment_specs}
    if unknown:
        raise ValueError("Symbol assignment references unknown equipment: " + ", ".join(sorted(unknown)))
    return symbols


def equipment_symbol_paths(symbol, item, placement):
    """Return world-mm paths inside the source physical frame, with actual rotation."""
    if symbol not in _SYMBOLS or placement.equipment_id != item.id:
        raise ValueError("Unknown project symbol or mismatched source equipment")
    expected = (item.depth_mm, item.width_mm) if placement.rotated else (item.width_mm, item.depth_mm)
    if (not all(isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
                for value in (placement.rect.x, placement.rect.y, placement.rect.width, placement.rect.height, *expected))
            or min(expected) <= 0 or any(not math.isclose(actual, declared, abs_tol=1e-6, rel_tol=0)
                                         for actual, declared in zip((placement.rect.width, placement.rect.height), expected))):
        raise ValueError("Symbol frame differs from finite source equipment dimensions")
    paths = []
    for points, closed in _SYMBOLS[symbol]:
        transformed = []
        for u, v in points:
            x, y = u * item.width_mm, v * item.depth_mm
            if placement.rotated:
                x, y = item.depth_mm - y, x
            transformed.append((placement.rect.x + x, placement.rect.y + y))
        paths.append((tuple(transformed), closed))
    return tuple(paths)


def draw_equipment_symbol_pdf(pdf, point, symbol, item, placement):
    for points, closed in equipment_symbol_paths(symbol, item, placement):
        path = pdf.beginPath()
        path.moveTo(*point(*points[0]))
        for xy in points[1:]:
            path.lineTo(*point(*xy))
        if closed:
            path.close()
        pdf.drawPath(path, stroke=1, fill=0)

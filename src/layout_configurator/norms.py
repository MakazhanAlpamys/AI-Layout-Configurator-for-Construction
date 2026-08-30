"""Configurable deterministic design-rule checks for layout results.

This module deliberately does not claim to encode a building code.  A ruleset is
an explicit, versioned collection of numeric checks.  Every result keeps the
rule source and evidence so a human can audit why it passed or failed.
"""

from __future__ import annotations

from dataclasses import dataclass
import heapq
from pathlib import Path
from typing import Any, Literal, Mapping

from .models import LayoutIR, LayoutResult, RoomSpec
from .validation import validate_layout
from .walls import build_wall_plan


RuleStatus = Literal["PASS", "FAIL", "NOT_APPLICABLE"]
SUPPORTED_RULES = frozenset(
    {
        "GEOMETRY_VALID",
        "MIN_ROOM_AREA",
        "MIN_CORRIDOR_WIDTH",
        "DAYLIGHT_OPENING",
        "EGRESS_REACHABILITY",
        "MAX_EGRESS_DISTANCE",
    }
)


@dataclass(frozen=True)
class RuleDefinition:
    id: str
    title: str
    source: str
    clause: str
    params: Mapping[str, Any]


@dataclass(frozen=True)
class RuleResult:
    id: str
    title: str
    status: RuleStatus
    source: str
    clause: str
    evidence: tuple[str, ...]

    @property
    def ok(self) -> bool:
        return self.status != "FAIL"

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "status": self.status,
            "source": self.source,
            "clause": self.clause,
            "evidence": list(self.evidence),
        }


@dataclass(frozen=True)
class RuleSet:
    name: str
    version: str
    jurisdiction: str
    rules: tuple[RuleDefinition, ...]

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> "RuleSet":
        if not isinstance(raw, Mapping):
            raise ValueError("Ruleset root must be an object")
        rules_raw = raw.get("rules", ())
        if not isinstance(rules_raw, (list, tuple)) or not rules_raw:
            raise ValueError("Ruleset must contain a non-empty rules list")
        rules: list[RuleDefinition] = []
        seen: set[str] = set()
        for index, item in enumerate(rules_raw):
            if not isinstance(item, Mapping):
                raise ValueError(f"rules[{index}] must be an object")
            rule_id = str(item.get("id", "")).strip().upper()
            if rule_id not in SUPPORTED_RULES:
                raise ValueError(f"Unsupported rule id: {rule_id or '<empty>'}")
            if rule_id in seen:
                raise ValueError(f"Duplicate rule id: {rule_id}")
            seen.add(rule_id)
            title = str(item.get("title", rule_id)).strip()
            source = str(item.get("source", "unspecified")).strip()
            clause = str(item.get("clause", rule_id)).strip()
            params = item.get("params", {})
            if not isinstance(params, Mapping):
                raise ValueError(f"rules[{index}].params must be an object")
            rules.append(RuleDefinition(rule_id, title, source, clause, dict(params)))
        return cls(
            name=str(raw.get("name", "Unnamed ruleset")),
            version=str(raw.get("version", "0.1")),
            jurisdiction=str(raw.get("jurisdiction", "unspecified")),
            rules=tuple(rules),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "version": self.version,
            "jurisdiction": self.jurisdiction,
            "rules": [
                {
                    "id": rule.id,
                    "title": rule.title,
                    "source": rule.source,
                    "clause": rule.clause,
                    "params": dict(rule.params),
                }
                for rule in self.rules
            ],
        }


@dataclass(frozen=True)
class NormsReport:
    ruleset_name: str
    ruleset_version: str
    jurisdiction: str
    results: tuple[RuleResult, ...]

    @property
    def ok(self) -> bool:
        return all(result.status != "FAIL" for result in self.results)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ruleset": {
                "name": self.ruleset_name,
                "version": self.ruleset_version,
                "jurisdiction": self.jurisdiction,
            },
            "ok": self.ok,
            "results": [result.to_dict() for result in self.results],
        }


def load_ruleset(path: str | Path) -> RuleSet:
    source = Path(path)
    import yaml

    raw = yaml.safe_load(source.read_text(encoding="utf-8"))
    return RuleSet.from_mapping(raw)


def check_layout(spec: LayoutIR, result: LayoutResult, ruleset: RuleSet) -> NormsReport:
    """Run every rule in a ruleset against one LayoutIR/LayoutResult pair."""

    results = tuple(_run_rule(rule, spec, result) for rule in ruleset.rules)
    return NormsReport(ruleset.name, ruleset.version, ruleset.jurisdiction, results)


def _run_rule(rule: RuleDefinition, spec: LayoutIR, result: LayoutResult) -> RuleResult:
    handlers = {
        "GEOMETRY_VALID": _geometry_valid,
        "MIN_ROOM_AREA": _min_room_area,
        "MIN_CORRIDOR_WIDTH": _min_corridor_width,
        "DAYLIGHT_OPENING": _daylight_opening,
        "EGRESS_REACHABILITY": _egress_reachability,
        "MAX_EGRESS_DISTANCE": _max_egress_distance,
    }
    status, evidence = handlers[rule.id](rule.params, spec, result)
    return RuleResult(rule.id, rule.title, status, rule.source, rule.clause, tuple(evidence))


def _geometry_valid(params: Mapping[str, Any], spec: LayoutIR, result: LayoutResult) -> tuple[RuleStatus, list[str]]:
    del params
    report = validate_layout(spec, result)
    if report.ok:
        return "PASS", ["Independent LayoutIR geometry and topology validation passed"]
    return "FAIL", [f"{issue.code}: {issue.message}" for issue in report.issues]


def _min_room_area(params: Mapping[str, Any], spec: LayoutIR, result: LayoutResult) -> tuple[RuleStatus, list[str]]:
    raw_thresholds = params.get("min_area_by_type", {})
    if not isinstance(raw_thresholds, Mapping):
        raise ValueError("MIN_ROOM_AREA.min_area_by_type must be an object")
    thresholds = {str(room_type).lower(): float(value) for room_type, value in raw_thresholds.items()}
    applicable = [(room, result.placements[room.id], thresholds[room.type.lower()]) for room in spec.rooms if room.type.lower() in thresholds and room.id in result.placements]
    if not applicable:
        return "NOT_APPLICABLE", ["No room type in the ruleset has a placement"]
    failures = [
        f"{room.id}: actual {rect.area_m2:.2f} m2 < minimum {minimum:.2f} m2"
        for room, rect, minimum in applicable
        if rect.area_m2 + 1e-6 < minimum
    ]
    evidence = [
        f"{room.id}: actual {rect.area_m2:.2f} m2 >= minimum {minimum:.2f} m2"
        for room, rect, minimum in applicable
        if rect.area_m2 + 1e-6 >= minimum
    ]
    return ("FAIL", failures + evidence) if failures else ("PASS", evidence)


def _min_corridor_width(params: Mapping[str, Any], spec: LayoutIR, result: LayoutResult) -> tuple[RuleStatus, list[str]]:
    types = {str(value).lower() for value in params.get("corridor_types", ("corridor", "hallway"))}
    minimum = float(params.get("minimum_clear_width_mm", 1_200))
    applicable = [(room, result.placements[room.id]) for room in spec.rooms if room.type.lower() in types and room.id in result.placements]
    if not applicable:
        return "NOT_APPLICABLE", [f"No room type matched: {', '.join(sorted(types))}"]
    evidence: list[str] = []
    failures: list[str] = []
    for room, rect in applicable:
        clear_width = min(rect.width, rect.height)
        message = f"{room.id}: clear rectangular width {clear_width:.0f} mm; minimum {minimum:.0f} mm"
        (failures if clear_width + 1e-6 < minimum else evidence).append(message)
    return ("FAIL", failures + evidence) if failures else ("PASS", evidence)


def _daylight_opening(params: Mapping[str, Any], spec: LayoutIR, result: LayoutResult) -> tuple[RuleStatus, list[str]]:
    if not bool(params.get("require_for_needs_daylight", True)):
        return "NOT_APPLICABLE", ["Rule disabled by ruleset parameters"]
    daylight_rooms = [room for room in spec.rooms if room.needs_daylight]
    if not daylight_rooms:
        return "NOT_APPLICABLE", ["No room declares needs_daylight=true"]
    if not set(room.id for room in spec.rooms).issubset(result.placements):
        return "FAIL", ["Cannot derive openings: result is missing one or more rooms"]
    windows_by_room = {window.room_id for window in build_wall_plan(spec, result).windows}
    failures = [f"{room.id}: no exterior window opening was derived" for room in daylight_rooms if room.id not in windows_by_room]
    evidence = [f"{room.id}: exterior window opening derived" for room in daylight_rooms if room.id in windows_by_room]
    return ("FAIL", failures + evidence) if failures else ("PASS", evidence)


def _egress_reachability(params: Mapping[str, Any], spec: LayoutIR, result: LayoutResult) -> tuple[RuleStatus, list[str]]:
    del params
    graph = _adjacency_graph(spec, result)
    if spec.entry_room not in result.placements:
        return "FAIL", [f"Entry room {spec.entry_room} is missing from the result"]
    reachable = _reachable(graph, spec.entry_room)
    missing = [room.id for room in spec.rooms if room.id not in reachable]
    if missing:
        return "FAIL", [f"Unreachable from {spec.entry_room}: {', '.join(missing)}"]
    return "PASS", [f"All {len(spec.rooms)} rooms are reachable from {spec.entry_room} through {spec.door_width_mm:.0f} mm openings"]


def _max_egress_distance(params: Mapping[str, Any], spec: LayoutIR, result: LayoutResult) -> tuple[RuleStatus, list[str]]:
    maximum = float(params.get("max_travel_distance_mm", 30_000))
    graph = _adjacency_graph(spec, result)
    distances, paths = _shortest_paths(graph, spec.entry_room, result)
    applicable = [(room_id, distance) for room_id, distance in distances.items() if room_id != spec.entry_room]
    if len(applicable) < max(0, len(spec.rooms) - 1):
        return "NOT_APPLICABLE", ["Disconnected rooms are reported by EGRESS_REACHABILITY; no complete distance check"]
    if not applicable:
        return "NOT_APPLICABLE", ["Only the entry room is present"]
    failures: list[str] = []
    evidence: list[str] = []
    for room_id, distance in sorted(applicable, key=lambda item: item[1], reverse=True):
        path = " -> ".join(paths[room_id])
        message = f"{room_id}: room-center proxy {distance:.0f} mm via {path}; maximum {maximum:.0f} mm"
        (failures if distance > maximum + 1e-6 else evidence).append(message)
    return ("FAIL", failures + evidence) if failures else ("PASS", evidence)


def _adjacency_graph(spec: LayoutIR, result: LayoutResult) -> dict[str, set[str]]:
    graph = {room.id: set() for room in spec.rooms}
    for index, room_a in enumerate(spec.rooms):
        rect_a = result.placements.get(room_a.id)
        if rect_a is None:
            continue
        for room_b in spec.rooms[index + 1 :]:
            rect_b = result.placements.get(room_b.id)
            if rect_b is None:
                continue
            if rect_a.shared_boundary(rect_b) + 1e-6 >= spec.door_width_mm:
                graph[room_a.id].add(room_b.id)
                graph[room_b.id].add(room_a.id)
    return graph


def _reachable(graph: Mapping[str, set[str]], start: str) -> set[str]:
    if start not in graph:
        return set()
    reachable = {start}
    frontier = [start]
    while frontier:
        current = frontier.pop()
        for neighbour in sorted(graph[current]):
            if neighbour not in reachable:
                reachable.add(neighbour)
                frontier.append(neighbour)
    return reachable


def _shortest_paths(graph: Mapping[str, set[str]], start: str, result: LayoutResult) -> tuple[dict[str, float], dict[str, list[str]]]:
    if start not in result.placements:
        return {}, {}
    distances = {start: 0.0}
    paths = {start: [start]}
    queue: list[tuple[float, str]] = [(0.0, start)]
    while queue:
        distance, room_id = heapq.heappop(queue)
        if distance > distances[room_id] + 1e-6:
            continue
        for neighbour in sorted(graph.get(room_id, ())):
            a = result.placements[room_id].center
            b = result.placements[neighbour].center
            weight = abs(a[0] - b[0]) + abs(a[1] - b[1])
            candidate = distance + weight
            if candidate + 1e-6 < distances.get(neighbour, float("inf")):
                distances[neighbour] = candidate
                paths[neighbour] = [*paths[room_id], neighbour]
                heapq.heappush(queue, (candidate, neighbour))
    return distances, paths

"""Configurable deterministic design-rule checks for layout results.

This module deliberately does not claim to encode a building code.  A ruleset is
an explicit, versioned collection of numeric checks.  Every result keeps the
rule source and evidence so a human can audit why it passed or failed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
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
        "FORBIDDEN_TYPE_ADJACENCY",
        "ENTRY_VESTIBULE",
        "AUXILIARY_SPACE_ATTACHMENT",
        "HEATED_ROOM_COVERAGE",
        "EGRESS_REACHABILITY",
        "MAX_EGRESS_DISTANCE",
    }
)
REQUIRED_PROVENANCE = ("authority", "edition", "effective_date", "source_url", "document_hash")


@dataclass(frozen=True)
class RuleDefinition:
    id: str
    title: str
    source: str
    clause: str
    params: Mapping[str, Any]
    keywords: tuple[str, ...] = ()


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
    provenance: Mapping[str, str] = field(default_factory=dict)

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
            keywords_raw = item.get("keywords", ())
            if isinstance(keywords_raw, str):
                keywords = (keywords_raw,)
            elif isinstance(keywords_raw, (list, tuple)):
                keywords = tuple(str(value).strip() for value in keywords_raw if str(value).strip())
            else:
                raise ValueError(f"rules[{index}].keywords must be a string or a list")
            rules.append(RuleDefinition(rule_id, title, source, clause, dict(params), keywords))
        return cls(
            name=str(raw.get("name", "Unnamed ruleset")),
            version=str(raw.get("version", "0.1")),
            jurisdiction=str(raw.get("jurisdiction", "unspecified")),
            rules=tuple(rules),
            provenance=_provenance(raw.get("provenance", {})),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "version": self.version,
            "jurisdiction": self.jurisdiction,
            "provenance": dict(self.provenance),
            "rules": [
                {
                    "id": rule.id,
                    "title": rule.title,
                    "source": rule.source,
                    "clause": rule.clause,
                    "params": dict(rule.params),
                    "keywords": list(rule.keywords),
                }
                for rule in self.rules
            ],
        }

    def provenance_issues(self) -> tuple[str, ...]:
        """Return missing provenance fields required by strict profile checks."""

        return tuple(
            f"provenance.{key} is required"
            for key in REQUIRED_PROVENANCE
            if not str(self.provenance.get(key, "")).strip()
        )


@dataclass(frozen=True)
class NormsReport:
    ruleset_name: str
    ruleset_version: str
    jurisdiction: str
    results: tuple[RuleResult, ...]
    provenance: Mapping[str, str] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return all(result.status != "FAIL" for result in self.results)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ruleset": {
                "name": self.ruleset_name,
                "version": self.ruleset_version,
                "jurisdiction": self.jurisdiction,
                "provenance": dict(self.provenance),
            },
            "ok": self.ok,
            "results": [result.to_dict() for result in self.results],
        }


def load_ruleset(path: str | Path) -> RuleSet:
    """Load a ruleset, optionally resolving relative ``extends`` profiles.

    A jurisdiction profile can override only the thresholds that differ from
    the project baseline. The merged result is still a plain ``RuleSet`` so
    the checker remains deterministic and auditable.
    """

    import yaml

    def load(source: Path, stack: tuple[Path, ...]) -> RuleSet:
        source = source.resolve()
        if source in stack:
            chain = " -> ".join(str(item) for item in (*stack, source))
            raise ValueError(f"Ruleset inheritance cycle: {chain}")
        raw = yaml.safe_load(source.read_text(encoding="utf-8"))
        if not isinstance(raw, Mapping):
            raise ValueError("Ruleset root must be an object")

        extends = raw.get("extends", ())
        if isinstance(extends, str):
            extends = (extends,)
        elif isinstance(extends, (list, tuple)):
            extends = tuple(extends)
        elif extends is None:
            extends = ()
        else:
            raise ValueError("Ruleset extends must be a path or a list of paths")

        bases = [load(source.parent / str(reference), (*stack, source)) for reference in extends]
        if not bases:
            return RuleSet.from_mapping(raw)

        merged: dict[str, Any] = bases[0].to_dict()
        merged_rules: list[dict[str, Any]] = []
        for base in bases:
            merged_rules = _merge_rule_mappings(merged_rules, base.to_dict()["rules"])
        merged_rules = _merge_rule_mappings(merged_rules, raw.get("rules", ()))
        merged["rules"] = merged_rules
        merged_provenance: dict[str, str] = {}
        for base in bases:
            merged_provenance.update(base.provenance)
        raw_provenance = raw.get("provenance", {})
        if raw_provenance is not None:
            if not isinstance(raw_provenance, Mapping):
                raise ValueError("Ruleset provenance must be an object")
            merged_provenance.update({str(key): str(value) for key, value in raw_provenance.items()})
        merged["provenance"] = merged_provenance
        for key in ("name", "version", "jurisdiction"):
            if key in raw:
                merged[key] = raw[key]
        return RuleSet.from_mapping(merged)

    return load(Path(path), ())


def _provenance(raw) -> dict[str, str]:
    if raw is None:
        return {}
    if not isinstance(raw, Mapping):
        raise ValueError("Ruleset provenance must be an object")
    return {str(key): str(value) for key, value in raw.items()}


def _merge_rule_mappings(base: list[dict[str, Any]], updates) -> list[dict[str, Any]]:
    if updates is None:
        return base
    if not isinstance(updates, (list, tuple)):
        raise ValueError("Ruleset rules must be a list")
    merged = [dict(rule) for rule in base]
    indexes = {str(rule.get("id", "")).strip().upper(): index for index, rule in enumerate(merged)}
    for index, rule in enumerate(updates):
        if not isinstance(rule, Mapping):
            raise ValueError(f"rules[{index}] must be an object")
        rule_copy = dict(rule)
        rule_id = str(rule_copy.get("id", "")).strip().upper()
        if rule_id in indexes:
            inherited = merged[indexes[rule_id]]
            combined = dict(inherited)
            combined.update(rule_copy)
            if isinstance(inherited.get("params"), Mapping) and isinstance(rule_copy.get("params"), Mapping):
                combined["params"] = {**inherited["params"], **rule_copy["params"]}
            merged[indexes[rule_id]] = combined
        else:
            indexes[rule_id] = len(merged)
            merged.append(rule_copy)
    return merged


def check_layout(spec: LayoutIR, result: LayoutResult, ruleset: RuleSet) -> NormsReport:
    """Run every rule in a ruleset against one LayoutIR/LayoutResult pair."""

    results = tuple(_run_rule(rule, spec, result) for rule in ruleset.rules)
    return NormsReport(ruleset.name, ruleset.version, ruleset.jurisdiction, results, ruleset.provenance)


@dataclass(frozen=True)
class RuleCitation:
    """A retrieved rule reference; retrieval never evaluates the layout."""

    id: str
    title: str
    clause: str
    source: str
    source_url: str
    keywords: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "clause": self.clause,
            "source": self.source,
            "source_url": self.source_url,
            "keywords": list(self.keywords),
        }


def retrieve_rule_citations(ruleset: RuleSet, query: str) -> tuple[RuleCitation, ...]:
    """Retrieve auditable rule references without producing a compliance verdict."""

    terms = tuple(term for term in _search_terms(query) if term)
    scored: list[tuple[int, RuleCitation]] = []
    source_url = str(ruleset.provenance.get("source_url", ""))
    for rule in ruleset.rules:
        haystack = " ".join(
            (rule.id, rule.title, rule.source, rule.clause, *rule.keywords)
        ).lower()
        score = sum(1 for term in terms if term in haystack)
        if score:
            scored.append(
                (
                    score,
                    RuleCitation(
                        rule.id,
                        rule.title,
                        rule.clause,
                        rule.source,
                        source_url,
                        rule.keywords,
                    ),
                )
            )
    return tuple(citation for _, citation in sorted(scored, key=lambda item: (-item[0], item[1].id)))


def _search_terms(query: str) -> tuple[str, ...]:
    normalized = str(query).lower().replace("ё", "е")
    aliases = {
        "освещ": ("daylight", "естествен", "свет", "окн"),
        "окн": ("window", "daylight", "естествен"),
        "тамбур": ("vestibule", "entry", "вход"),
        "вход": ("entry", "vestibule", "тамбур"),
        "отоп": ("heated", "heating", "отап"),
        "эвакуац": ("egress", "reachability", "distance"),
        "площад": ("area", "minimum"),
        "коридор": ("corridor", "width"),
        "террас": ("terrace", "attachment"),
        "лодж": ("loggia", "attachment"),
    }
    tokens = set(normalized.split())
    expanded = set(tokens)
    for token in tuple(tokens):
        for prefix, values in aliases.items():
            if token.startswith(prefix):
                expanded.update(values)
    return tuple(sorted(expanded))


def _run_rule(rule: RuleDefinition, spec: LayoutIR, result: LayoutResult) -> RuleResult:
    handlers = {
        "GEOMETRY_VALID": _geometry_valid,
        "MIN_ROOM_AREA": _min_room_area,
        "MIN_CORRIDOR_WIDTH": _min_corridor_width,
        "DAYLIGHT_OPENING": _daylight_opening,
        "FORBIDDEN_TYPE_ADJACENCY": _forbidden_type_adjacency,
        "ENTRY_VESTIBULE": _entry_vestibule,
        "AUXILIARY_SPACE_ATTACHMENT": _auxiliary_space_attachment,
        "HEATED_ROOM_COVERAGE": _heated_room_coverage,
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
    raw_required_types = params.get("required_room_types", ())
    if isinstance(raw_required_types, str):
        required_types = {raw_required_types.lower()}
    elif isinstance(raw_required_types, (list, tuple, set, frozenset)):
        required_types = {str(room_type).lower() for room_type in raw_required_types}
    else:
        raise ValueError("DAYLIGHT_OPENING.required_room_types must be a string or a list")
    daylight_rooms = [room for room in spec.rooms if room.needs_daylight or room.type.lower() in required_types]
    if not daylight_rooms:
        if required_types:
            return "NOT_APPLICABLE", [
                "No room declares needs_daylight=true or matches required room types: "
                + ", ".join(sorted(required_types))
            ]
        return "NOT_APPLICABLE", ["No room declares needs_daylight=true"]
    if not set(room.id for room in spec.rooms).issubset(result.placements):
        return "FAIL", ["Cannot derive openings: result is missing one or more rooms"]
    windows_by_room = {window.room_id for window in build_wall_plan(spec, result).windows}
    failures = [f"{room.id}: no exterior window opening was derived" for room in daylight_rooms if room.id not in windows_by_room]
    evidence = [f"{room.id}: exterior window opening derived" for room in daylight_rooms if room.id in windows_by_room]
    return ("FAIL", failures + evidence) if failures else ("PASS", evidence)


def _forbidden_type_adjacency(
    params: Mapping[str, Any], spec: LayoutIR, result: LayoutResult
) -> tuple[RuleStatus, list[str]]:
    source_types = _room_type_set(params.get("source_room_types", ()), "source_room_types")
    target_types = _room_type_set(params.get("target_room_types", ()), "target_room_types")
    minimum_shared_boundary = float(params.get("minimum_shared_boundary_mm", 1))
    if minimum_shared_boundary < 0:
        raise ValueError("FORBIDDEN_TYPE_ADJACENCY.minimum_shared_boundary_mm must be non-negative")

    source_rooms = [room for room in spec.rooms if room.type.lower() in source_types]
    target_rooms = [room for room in spec.rooms if room.type.lower() in target_types]
    if not source_rooms or not target_rooms:
        return "NOT_APPLICABLE", [
            "No room pair matched source types "
            + ", ".join(sorted(source_types))
            + " and target types "
            + ", ".join(sorted(target_types))
        ]

    failures: list[str] = []
    evidence: list[str] = []
    for source in source_rooms:
        source_rect = result.placements.get(source.id)
        if source_rect is None:
            failures.append(f"{source.id}: placement is missing")
            continue
        for target in target_rooms:
            if source.id == target.id:
                continue
            target_rect = result.placements.get(target.id)
            if target_rect is None:
                failures.append(f"{target.id}: placement is missing")
                continue
            shared_boundary = source_rect.shared_boundary(target_rect)
            message = (
                f"{source.id} ({source.type}) - {target.id} ({target.type}): "
                f"shared boundary {shared_boundary:.0f} mm; forbidden threshold "
                f"{minimum_shared_boundary:.0f} mm"
            )
            (failures if shared_boundary + 1e-6 >= minimum_shared_boundary else evidence).append(message)
    if failures:
        return "FAIL", failures + evidence
    return "PASS", evidence or ["No forbidden type adjacency was found"]


def _room_type_set(value: Any, field_name: str) -> set[str]:
    if isinstance(value, str):
        values = (value,)
    elif isinstance(value, (list, tuple, set, frozenset)):
        values = value
    else:
        raise ValueError(f"FORBIDDEN_TYPE_ADJACENCY.{field_name} must be a string or a list")
    normalized = {str(item).strip().lower() for item in values if str(item).strip()}
    if not normalized:
        raise ValueError(f"FORBIDDEN_TYPE_ADJACENCY.{field_name} must not be empty")
    return normalized


def _entry_vestibule(params: Mapping[str, Any], spec: LayoutIR, result: LayoutResult) -> tuple[RuleStatus, list[str]]:
    del result
    entry = spec.external_entry
    if entry is None:
        return "NOT_APPLICABLE", ["No external_entry is defined in LayoutIR"]
    room = spec.room_by_id.get(entry.room_id)
    if room is None:  # pragma: no cover - LayoutIR rejects this at the input boundary
        return "FAIL", [f"External entry {entry.id} references missing room {entry.room_id}"]
    vestibule_types = _room_type_set(
        params.get("vestibule_room_types", ("vestibule", "tambour", "entry_lobby")),
        "vestibule_room_types",
    )
    if room.type.lower() in vestibule_types:
        return "PASS", [
            f"External entry {entry.id} opens into vestibule room {room.id} ({room.type})"
        ]
    if not room.is_heated:
        return "PASS", [
            f"External entry {entry.id} opens into unheated room {room.id}; direct heated-room entry is not present"
        ]
    return "FAIL", [
        f"External entry {entry.id} opens directly into heated room {room.id} ({room.type}); vestibule room is required"
    ]


def _auxiliary_space_attachment(
    params: Mapping[str, Any], spec: LayoutIR, result: LayoutResult
) -> tuple[RuleStatus, list[str]]:
    auxiliary_types = _room_type_set(
        params.get("auxiliary_room_types", ("veranda", "terrace", "loggia")),
        "auxiliary_room_types",
    )
    main_types = _room_type_set(
        params.get("main_room_types", ("living_room", "kitchen", "bedroom")),
        "main_room_types",
    )
    minimum_shared_boundary = float(params.get("minimum_shared_boundary_mm", 1))
    if minimum_shared_boundary < 0:
        raise ValueError("AUXILIARY_SPACE_ATTACHMENT.minimum_shared_boundary_mm must be non-negative")

    auxiliary_rooms = [room for room in spec.rooms if room.type.lower() in auxiliary_types]
    if not auxiliary_rooms:
        return "NOT_APPLICABLE", [
            "No room matched auxiliary types " + ", ".join(sorted(auxiliary_types))
        ]
    main_rooms = [room for room in spec.rooms if room.type.lower() in main_types]
    if not main_rooms:
        return "FAIL", [
            "No main room matched target types " + ", ".join(sorted(main_types))
        ]

    failures: list[str] = []
    evidence: list[str] = []
    for auxiliary in auxiliary_rooms:
        auxiliary_rect = result.placements.get(auxiliary.id)
        if auxiliary_rect is None:
            failures.append(f"{auxiliary.id}: placement is missing")
            continue
        attached_to: list[str] = []
        for main in main_rooms:
            main_rect = result.placements.get(main.id)
            if main_rect is None:
                failures.append(f"{main.id}: placement is missing")
                continue
            shared_boundary = auxiliary_rect.shared_boundary(main_rect)
            if shared_boundary + 1e-6 >= minimum_shared_boundary:
                attached_to.append(f"{main.id} ({shared_boundary:.0f} mm)")
        if attached_to:
            evidence.append(
                f"{auxiliary.id} ({auxiliary.type}) is attached to "
                + ", ".join(attached_to)
                + f"; minimum shared boundary {minimum_shared_boundary:.0f} mm"
            )
        else:
            failures.append(
                f"{auxiliary.id} ({auxiliary.type}) has no shared boundary with main room types "
                + ", ".join(sorted(main_types))
            )
    return ("FAIL", failures + evidence) if failures else ("PASS", evidence)


def _heated_room_coverage(
    params: Mapping[str, Any], spec: LayoutIR, result: LayoutResult
) -> tuple[RuleStatus, list[str]]:
    required_types = _room_type_set(
        params.get("required_room_types", ("living_room", "bedroom", "kitchen")),
        "required_room_types",
    )
    applicable = [room for room in spec.rooms if room.type.lower() in required_types]
    if not applicable:
        return "NOT_APPLICABLE", [
            "No room matched required heated types " + ", ".join(sorted(required_types))
        ]

    failures: list[str] = []
    evidence: list[str] = []
    for room in applicable:
        if room.id not in result.placements:
            failures.append(f"{room.id}: placement is missing")
        elif room.is_heated:
            evidence.append(f"{room.id} ({room.type}) is marked heated")
        else:
            failures.append(f"{room.id} ({room.type}) is not marked heated")
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

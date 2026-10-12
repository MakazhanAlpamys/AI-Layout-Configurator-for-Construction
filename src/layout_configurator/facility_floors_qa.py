"""Independent coordination QA for persisted facility floor stacks.

Reported PASS flags are descriptive only. Source programs, floor predicates,
and actual IFC geometry are rechecked. This does not provide engineering sign-off.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
import hashlib
import json
import math
from pathlib import Path
import re

from shapely.geometry import Polygon, box
from shapely.ops import unary_union

from .equipment import validate_equipment_layout
from .facility import FacilityProfile, default_facility_profile, load_facility_profile, validate_building
from .facility_floors import FacilityFloorsSpec, vertical_checks
from .facility_generation import FeasibleFacilityCandidate
from .flows import route_flows, validate_flow_routes
from .ifc import IFC_HEIGHT_MM
from .io import load_building, load_building_result
from .qa import validate_building_bundle
from .result_geometry import require_rectangular_layout
from .validation import validate_layout
from .walls import build_wall_plan


@dataclass(frozen=True)
class FacilityFloorsQACheck:
    id: str
    status: str
    evidence: tuple[str, ...]
    stack: int | None = None
    level: int | None = None

    @property
    def ok(self) -> bool:
        return self.status in {"PASS", "NOT_APPLICABLE"}

    def to_dict(self) -> dict:
        return {"id": self.id, "status": self.status, "evidence": list(self.evidence),
                "stack": self.stack, "level": self.level}


@dataclass(frozen=True)
class FacilityFloorsQAReport:
    output_dir: Path
    checks: tuple[FacilityFloorsQACheck, ...]
    artifact_hashes: tuple[dict, ...]

    @property
    def ok(self) -> bool:
        return bool(self.checks) and all(check.ok for check in self.checks)

    def to_dict(self) -> dict:
        return {"ok": self.ok, "output": str(self.output_dir),
                "scope": "Deterministic coordination QA; external engineering review is not evaluated",
                "checks": [check.to_dict() for check in self.checks], "artifact_hashes": list(self.artifact_hashes)}


def validate_facility_floors_bundle(
    spec: FacilityFloorsSpec,
    output_dir: str | Path,
    expected_stacks: int = 1,
    *,
    default_profile: FacilityProfile | None = None,
) -> FacilityFloorsQAReport:
    """Recheck complete floor bundles and the shared IFC against source inputs."""
    if isinstance(expected_stacks, bool) or not isinstance(expected_stacks, int) or expected_stacks < 1:
        raise ValueError("expected_stacks must be a positive integer")
    from ifcopenshell import Error as IfcParseError
    root = Path(output_dir).resolve()
    checks, hashes = [], []

    def add(name, failures=(), evidence=(), *, stack=None, level=None):
        checks.append(FacilityFloorsQACheck(name, "FAIL" if failures else "PASS",
                                          tuple(failures) or tuple(evidence) or ("Recomputed predicate passed",), stack, level))

    report_path = root / "multi_floor_report.json"
    try:
        report = _json(report_path)
        if not isinstance(report, dict):
            raise ValueError("multi_floor_report must be an object")
        stacks = report.get("stacks")
        if not isinstance(stacks, list) or any(not isinstance(item, dict) for item in stacks):
            raise ValueError("Report requires a stacks array of objects")
        levels = {entry.level for entry in spec.floors}
        failures = []
        if (len(stacks) != expected_stacks or any(type(item.get("stack")) is not int for item in stacks)
                or [item.get("stack") for item in stacks] != list(range(1, expected_stacks + 1))):
            failures.append("Stack count/identities differ from the requested complete stack set")
        expected_floors = {entry.level: entry for entry in spec.floors}
        metadata = report.get("floors")
        if not isinstance(metadata, list) or len(metadata) != len(levels) or any(not isinstance(item, dict) for item in metadata):
            failures.append("Report floor metadata is incomplete")
        else:
            seen = []
            for item in metadata:
                level = item.get("level")
                if isinstance(level, bool) or level not in expected_floors:
                    failures.append("Report floor level is not declared")
                    continue
                seen.append(level)
                entry = expected_floors[level]
                if item.get("elevation_mm") != entry.elevation_mm or item.get("bundle") != f"floor_{level:02d}":
                    failures.append(f"Floor {level}: elevation/bundle metadata differs from source")
                if not isinstance(item.get("program"), str) or Path(item["program"]).resolve() != Path(entry.program_path).resolve():
                    failures.append(f"Floor {level}: source program reference differs")
            if set(seen) != levels or len(seen) != len(levels):
                failures.append("Report floor metadata has duplicate/missing levels")
        declared_grid = None if spec.structural_grid is None else spec.structural_grid.to_dict()
        if (report.get("project") != spec.project_name or report.get("floor_height_mm") != spec.floor_height_mm
                or report.get("structural_grid") != declared_grid):
            failures.append("Report project, floor height or shared grid differs from source")
        add("REPORT_CONTRACT", failures)
        _hash_record(report_path, hashes)
    except (OSError, ValueError, TypeError, KeyError) as exc:
        add("REPORT_CONTRACT", (str(exc),))
        return FacilityFloorsQAReport(root, tuple(checks), tuple(hashes))

    programs = {level: building.with_required_flow_opening_width() for level, building in spec.floor_programs().items()}
    inventory = []
    directories = {path.name for path in root.glob("floor_*") if path.is_dir()}
    if directories != {f"floor_{level:02d}" for level in levels}:
        inventory.append("Floor directories differ from the declared complete floor set")
    for level in levels:
        directory = root / f"floor_{level:02d}"
        actual = {path.name for path in directory.glob("building_*.json") if re.fullmatch(r"building_[0-9]+\.json", path.name)}
        expected = {f"building_{number:02d}.json" for number in range(1, expected_stacks + 1)}
        if actual != expected:
            inventory.append(f"Floor {level}: canonical variant inventory differs from requested stack set")
        try:
            entries = _json(directory / "manifest.json").get("variants")
            if (not isinstance(entries, list) or len(entries) != expected_stacks
                    or any(not isinstance(item, dict) or type(item.get("variant")) is not int for item in entries)
                    or {item["variant"] for item in entries} != set(range(1, expected_stacks + 1))):
                inventory.append(f"Floor {level}: manifest variant inventory is incomplete or duplicated")
        except (OSError, ValueError, TypeError, AttributeError) as exc:
            inventory.append(f"Floor {level}: {exc}")
    add("FLOOR_BUNDLES", inventory)
    profiles = {}
    for entry in spec.floors:
        try:
            source = load_building(entry.program_path)
            if spec.structural_grid is not None:
                if source.structural_grid not in (None, spec.structural_grid):
                    raise ValueError("Source program overrides the shared grid")
                source = replace(source, structural_grid=spec.structural_grid)
            if source.to_dict() != entry.building.to_dict():
                raise ValueError("Source program changed since FacilityFloorsSpec was loaded")
            _hash_record(Path(entry.program_path), hashes, kind="source_program")
            add("SOURCE_PROGRAMS", level=entry.level)
        except (OSError, ValueError, TypeError) as exc:
            add("SOURCE_PROGRAMS", (str(exc),), level=entry.level)
        try:
            profiles[entry.level] = (load_facility_profile(entry.profile_path) if entry.profile_path
                                     else default_profile or default_facility_profile())
            if entry.profile_path:
                _hash_record(Path(entry.profile_path), hashes, kind="source_profile")
        except (OSError, ValueError, TypeError) as exc:
            add("SOURCE_PROFILES", (str(exc),), level=entry.level)

    for number, recorded in enumerate(stacks, 1):
        candidates = {}
        files = recorded.get("floors")
        if not isinstance(files, dict) or set(files) != {str(level) for level in levels}:
            add("REPORT_CONTRACT", ("Stack does not reference exactly the declared floors",), stack=number)
            continue
        for entry in spec.floors:
            level, program = entry.level, programs[entry.level]
            expected_file = f"floor_{level:02d}/building_{number:02d}.json"
            if files[str(level)] != expected_file:
                add("REPORT_CONTRACT", (f"Floor {level} result path differs from the stack convention",), stack=number)
                continue
            path = root / expected_file
            try:
                building, result, equipment = load_building_result(path)
                require_rectangular_layout(result, "Facility floor-stack QA")
                if building.to_dict() != program.to_dict():
                    add("SOURCE_PROGRAMS", ("Saved canonical program differs from the source floor program and cross-floor legs",),
                        stack=number, level=level)
                else:
                    add("SOURCE_PROGRAMS", stack=number, level=level)
                if result.variant != number:
                    raise ValueError("Saved variant number differs from its stack")
                profile = profiles[level]
                payload, manifest = _json(path), _json(path.parent / "manifest.json")
                if (payload.get("facility_validation", {}).get("profile") != profile.to_dict()
                        or manifest.get("facility_profile") != profile.to_dict()
                        or manifest.get("drawing_profile") != profile.drawing.to_dict()):
                    add("SOURCE_PROFILES", ("Persisted facility/drawing profile differs from the source or explicit default override",),
                        stack=number, level=level)
                else:
                    add("SOURCE_PROFILES", stack=number, level=level)
                qa = validate_building_bundle(path, profile=profile)
                layout_report = validate_layout(program.layout, result)
                equipment_report = validate_equipment_layout(program, result, equipment)
                routes = route_flows(program, result, equipment)
                flow_report = validate_flow_routes(program, result, routes, equipment)
                facility_report = validate_building(program, result, equipment, routes, flow_report,
                                                   profile=profile, equipment_report=equipment_report)
                candidates[level] = FeasibleFacilityCandidate(number, 0, 0, 0, result, layout_report, equipment,
                                                              equipment_report, routes, flow_report, facility_report)
                problems = [f"{check.id}: {check.message}" for check in qa.checks if not check.ok]
                problems.extend(f"{check.id}: {check.status}" for check in facility_report.checks if not check.ok)
                _floor_hashes(path, manifest, number, hashes, problems)
                add("FLOOR_BUNDLES", problems, stack=number, level=level)
            except (OSError, ValueError, RuntimeError, TypeError, KeyError, IndexError, AttributeError, IfcParseError) as exc:
                add("FLOOR_BUNDLES", (str(exc),), stack=number, level=level)
        if set(candidates) != levels:
            add("VERTICAL_CHECKS", ("Cannot evaluate a complete stack: floor evidence is missing or malformed",), stack=number)
        else:
            recomputed = vertical_checks(spec, candidates)
            add("VERTICAL_CHECKS", [f"{check.id}: {'; '.join(check.evidence)}" for check in recomputed if check.status != "PASS"],
                [f"{check.id}: {check.status}" for check in recomputed], stack=number)
        if spec.structural_grid is None:
            checks.append(FacilityFloorsQACheck("UNIFIED_IFC", "NOT_APPLICABLE", ("Source does not declare a shared grid/unified IFC",), number))
            continue
        unified = recorded.get("unified_ifc")
        path = root / f"facility_stack_{number:02d}.ifc"
        if not isinstance(unified, dict) or unified.get("path") != path.name:
            add("IFC_HASHES", ("Shared-grid stack requires its named unified IFC",), stack=number)
            continue
        try:
            digest = _hash_record(path, hashes)
            add("IFC_HASHES", () if unified.get("sha256") == digest else ("Unified IFC SHA256 differs from recorded artifact",), stack=number)
            _ifc_checks(spec, path, programs, candidates, number, add)
        except (OSError, ValueError, RuntimeError, TypeError, KeyError, IndexError, AttributeError, IfcParseError) as exc:
            add("IFC_READBACK", (str(exc),), stack=number)
    return FacilityFloorsQAReport(root, tuple(checks), tuple(hashes))


def _json(path: Path):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"Duplicate JSON key: {key}")
            result[key] = value
        return result
    def invalid(value):
        raise ValueError(f"Nonfinite JSON constant: {value}")
    return json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=unique, parse_constant=invalid)


def _hash_record(path, records, *, kind="artifact"):
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    if not any(record["path"] == str(path) for record in records):
        records.append({"path": str(path), "sha256": digest, "size_bytes": path.stat().st_size, "kind": kind})
    return digest


def _floor_hashes(path, manifest, number, hashes, failures):
    for artifact in [path, path.parent / "manifest.json", *(path.with_suffix(suffix) for suffix in (".dxf", ".pdf", ".ifc", ".bcf", ".coordination.json"))]:
        _hash_record(artifact, hashes)
    variants = manifest.get("variants", ())
    saved = next((item for item in variants if isinstance(item, dict) and item.get("variant") == number), {})
    sheets = saved.get("sheet_set")
    if sheets is not None:
        expected = f"building_{number:02d}_sheets.pdf"
        if not isinstance(sheets, dict) or sheets.get("pdf") != expected:
            failures.append("Drawing-sheet artifact reference is malformed")
        elif _hash_record(path.parent / expected, hashes) != sheets.get("sha256"):
            failures.append("Drawing-sheet SHA256 differs from recorded artifact")


def _ifc_checks(spec, path, programs, candidates, number, add):
    import ifcopenshell
    import ifcopenshell.geom
    import ifcopenshell.util.element
    import ifcopenshell.util.placement
    import ifcopenshell.util.unit
    import ifcopenshell.validate

    model = ifcopenshell.open(path)
    logger = ifcopenshell.validate.json_logger()
    ifcopenshell.validate.validate(model, logger, express_rules=True)
    add("IFC_EXPRESS", [str(item.get("message", "EXPRESS violation")) for item in logger.statements], stack=number)
    psets = lambda entity: ifcopenshell.util.element.get_psets(entity)
    identities, duplicate = {}, []
    for entity in model.by_type("IfcObject"):
        identity = psets(entity).get("Pset_FacilityIdentity", {})
        semantic = identity.get("SemanticId")
        if semantic:
            if not isinstance(semantic, str) or semantic in identities:
                duplicate.append(f"Duplicate or malformed semantic identity: {semantic}")
            else:
                identities[semantic] = entity
    roots = model.by_type("IfcRoot")
    if len({entity.GlobalId for entity in roots}) != len(roots):
        duplicate.append("IFC root GlobalIds are not unique")
    expected_ids, memberships = _expected_semantics(spec, programs, candidates)
    if set(identities) != expected_ids:
        duplicate.append(f"Semantic IDs differ: missing {sorted(expected_ids - set(identities))}; extra {sorted(set(identities) - expected_ids)}")
    expected_types = {"storey": "IfcBuildingStorey", "room": "IfcSpace", "equipment": "IfcBuildingElementProxy",
                      "flow-route": "IfcBuildingElementProxy", "zone": "IfcZone", "flow-system": "IfcSystem",
                      "wall": "IfcWall", "door": "IfcDoor", "window": "IfcWindow", "opening": "IfcOpeningElement"}
    for semantic, entity in identities.items():
        values = psets(entity).get("Pset_FacilityIdentity", {})
        if semantic in expected_ids and semantic.startswith("L"):
            floor, kind, local_id = semantic.split("/", 2)
            source_level = values.get("Level")
            if (not isinstance(source_level, (int, float)) or isinstance(source_level, bool)
                    or not math.isfinite(source_level) or source_level != int(floor[1:])
                    or values.get("LocalId") != local_id or values.get("SourceEntityType") != kind
                    or values.get("SourceContract") != "FacilityFloorsSpec" or not entity.is_a(expected_types[kind])):
                duplicate.append(f"{semantic}: typed identity differs from its canonical level/kind/local ID")
    for semantic, members in memberships.items():
        group = identities.get(semantic)
        actual = set() if group is None else {
            psets(member).get("Pset_FacilityIdentity", {}).get("SemanticId")
            for relation in getattr(group, "IsGroupedBy", ()) for member in relation.RelatedObjects}
        if actual != members:
            duplicate.append(f"{semantic}: native group membership differs from source")
    add("IFC_SEMANTICS", duplicate, (f"Checked {len(identities)} unique source-backed semantic identities",), stack=number)

    spatial, geometry = [], []
    if len(model.by_type("IfcProject")) != 1 or len(model.by_type("IfcBuilding")) != 1:
        spatial.append("Unified IFC requires exactly one project and building")
    if len(model.by_type("IfcBuildingStorey")) != len(spec.floors):
        spatial.append("IFC storey count differs from source")
    if not math.isclose(ifcopenshell.util.unit.calculate_unit_scale(model), 0.001, abs_tol=1e-12):
        spatial.append("Unified IFC length units differ from canonical millimetres")
    settings = ifcopenshell.geom.settings()
    settings.set("use-world-coords", True)
    for entry in spec.floors:
        level = entry.level
        storey = identities.get(f"L{level}/storey/{level}")
        if storey is None or not storey.is_a("IfcBuildingStorey"):
            spatial.append(f"Floor {level}: missing typed storey")
            continue
        if storey.ObjectPlacement is None:
            spatial.append(f"Floor {level}: no physical placement")
        else:
            matrix = ifcopenshell.util.placement.get_local_placement(storey.ObjectPlacement)
            if not _near(matrix[0, 3], 0) or not _near(matrix[1, 3], 0) or not _near(matrix[2, 3], entry.elevation_mm):
                spatial.append(f"Floor {level}: world placement differs from source elevation/origin")
        if storey.Elevation is None or not _near(storey.Elevation, entry.elevation_mm):
            spatial.append(f"Floor {level}: actual IFC Elevation differs from source")
        members = [entity for relation in storey.IsDecomposedBy for entity in relation.RelatedObjects]
        if {psets(entity).get("Pset_FacilityIdentity", {}).get("SemanticId") for entity in members} != {
                f"L{level}/room/{room.id}" for room in programs[level].layout.rooms}:
            spatial.append(f"Floor {level}: actual storey/space decomposition differs from source")
        for room in programs[level].layout.rooms:
            semantic = f"L{level}/room/{room.id}"
            space = identities.get(semantic)
            if space is None or not space.is_a("IfcSpace") or level not in candidates:
                geometry.append(f"{semantic}: missing source-backed space or canonical geometry")
                continue
            try:
                shape = ifcopenshell.geom.create_shape(settings, space)
                footprint, zmin, zmax = _world_footprint(shape.geometry)
                rect = candidates[level].result.placements[room.id]
                expected = box(rect.x, rect.y, rect.right, rect.top)
                if footprint.symmetric_difference(expected).area > max(1e-3, expected.area * 1e-9):
                    geometry.append(f"{semantic}: actual world footprint differs from canonical room geometry")
                if not _near(zmin, entry.elevation_mm) or not _near(zmax, entry.elevation_mm + IFC_HEIGHT_MM):
                    spatial.append(f"{semantic}: actual world geometry elevation/height differs from export contract")
                if space not in members:
                    spatial.append(f"{semantic}: space is aggregated by the wrong storey")
            except (ValueError, RuntimeError, TypeError, IndexError, KeyError) as exc:
                geometry.append(f"{semantic}: {exc}")
    if len(model.by_type("IfcSpace")) != sum(len(program.layout.rooms) for program in programs.values()):
        geometry.append("IFC space count differs from canonical room set")
    add("IFC_SPATIAL", spatial, stack=number)
    add("IFC_ROOM_GEOMETRY", geometry, ("Compared actual world triangle projections, not bounding boxes",), stack=number)
    add("IFC_GRID", _grid_failures(model, spec, ifcopenshell.util.placement.get_local_placement, psets), stack=number)


def _world_footprint(mesh):
    vertices, faces = mesh.verts, mesh.faces
    if not vertices or len(vertices) % 3 or len(faces) % 3 or len(faces) > 300000:
        raise ValueError("Malformed or excessive IFC space tessellation")
    points = [(vertices[index] * 1000, vertices[index + 1] * 1000) for index in range(0, len(vertices), 3)]
    if not all(math.isfinite(value) for value in vertices):
        raise ValueError("Nonfinite IFC world geometry")
    triangles = [Polygon([points[faces[index + offset]] for offset in range(3)]) for index in range(0, len(faces), 3)]
    footprint = unary_union([triangle for triangle in triangles if triangle.area > 1e-6])
    if footprint.is_empty or not footprint.is_valid:
        raise ValueError("Invalid IFC world footprint")
    zs = vertices[2::3]
    return footprint, min(zs) * 1000, max(zs) * 1000


def _grid_failures(model, spec, placement, psets):
    failures = []
    grids = model.by_type("IfcGrid")
    if len(grids) != 1:
        return ["Unified IFC requires exactly one shared physical grid"]
    grid, declared = grids[0], spec.structural_grid
    if grid.PredefinedType != "RECTANGULAR" or grid.ObjectPlacement is None:
        return ["Shared IFC grid has no rectangular physical placement"]
    matrix = placement(grid.ObjectPlacement)
    references = [relation.RelatingStructure for relation in model.by_type("IfcRelReferencedInSpatialStructure")
                  if grid in relation.RelatedElements]
    expected = {f"L{entry.level}/storey/{entry.level}" for entry in spec.floors}
    if (len(references) != len(expected) or any(not item.is_a("IfcBuildingStorey") for item in references)
            or {psets(item).get("Pset_FacilityIdentity", {}).get("SemanticId") for item in references} != expected):
        failures.append("Shared physical grid is not referenced by exactly the declared storeys")
    width = max(entry.building.layout.boundary.width_mm for entry in spec.floors)
    depth = max(entry.building.layout.boundary.height_mm for entry in spec.floors)
    for axes, values, labels, orientation in (
            (grid.UAxes, declared.axes_y_mm, declared.labels_y or tuple(f"Y{i + 1}" for i in range(len(declared.axes_y_mm))), "y"),
            (grid.VAxes, declared.axes_x_mm, declared.labels_x or tuple(f"X{i + 1}" for i in range(len(declared.axes_x_mm))), "x")):
        if len(axes) != len(values):
            failures.append(f"Shared grid {orientation} axis count differs")
            continue
        for axis, value, label in zip(axes, values, labels):
            if axis.AxisTag != label or not axis.SameSense or not axis.AxisCurve.is_a("IfcPolyline"):
                failures.append(f"Shared grid {orientation} axis labels/type differ")
                continue
            points = [tuple(point.Coordinates) for point in axis.AxisCurve.Points]
            world = []
            for point in points:
                if len(point) not in (2, 3):
                    failures.append(f"Shared grid {orientation} axis {label}: invalid coordinate dimensions")
                    break
                homogeneous = (*point, 0, 1) if len(point) == 2 else (*point, 1)
                world.append(tuple(sum(matrix[row, col] * homogeneous[col] for col in range(4)) for row in range(3)))
            expected = [(0, value, 0), (width, value, 0)] if orientation == "y" else [(value, 0, 0), (value, depth, 0)]
            if len(world) != 2 or any(not _near(a, b) for actual, wanted in zip(world, expected) for a, b in zip(actual, wanted)):
                failures.append(f"Shared grid {orientation} axis {label}: actual world curve differs from source")
    return failures


def _expected_semantics(spec, programs, candidates):
    identities, memberships = set(), {}
    for entry in spec.floors:
        level, program = entry.level, programs[entry.level]
        identity = lambda kind, local: f"L{level}/{kind}/{local}"
        identities.add(identity("storey", level))
        identities.update(identity("room", room.id) for room in program.layout.rooms)
        identities.update(identity("equipment", item.id) for item in program.equipment)
        for zone in program.zones:
            semantic = identity("zone", zone.id)
            identities.add(semantic)
            memberships[semantic] = {identity("room", room_id) for room_id in zone.room_ids} | {
                identity("zone", child.id) for child in program.zones if child.parent_zone_id == zone.id}
        routes = [] if level not in candidates else sorted(candidates[level].flow_routes.routes,
                                                         key=lambda route: (route.flow_id, route.from_id, route.to_id))
        route_ids = [(route, identity("flow-route", f"{route.flow_id}:{route.from_id}:{route.to_id}:{index}"))
                     for index, route in enumerate(route for route in routes if len(route.points) >= 2)]
        identities.update(semantic for _, semantic in route_ids)
        equipment = {item.id for item in program.equipment}
        for flow in program.flows:
            semantic = identity("flow-system", flow.id)
            identities.add(semantic)
            memberships[semantic] = {route_id for route, route_id in route_ids if route.flow_id == flow.id} | {
                identity("equipment", endpoint) for endpoint in (*flow.from_ids, *flow.to_ids) if endpoint in equipment}
        if level in candidates:
            plan = build_wall_plan(program.layout, candidates[level].result)
            for kind, count in (("wall", _room_edge_count(candidates[level].result)), ("door", len(plan.openings)),
                                ("window", len(plan.windows)), ("opening", len(plan.openings) + len(plan.windows))):
                identities.update(identity(kind, index) for index in range(count))
    for core in spec.cores:
        semantic = f"vertical-core/{core.id}"
        identities.add(semantic)
        memberships[semantic] = {f"L{level}/room/{room}" for level, room in core.rooms.items()}
    for flow in spec.cross_flows:
        semantic = f"cross-floor-flow/{flow.id}"
        identities.add(semantic)
        memberships[semantic] = {f"L{level}/flow-system/{flow.leg_id(level)}" for level in (flow.from_level, flow.to_level)} | {
            f"vertical-core/{spec.core_for(flow).id}"}
    return identities, memberships


def _room_edge_count(result):
    edges = set()
    for rect in result.placements.values():
        corners = ((rect.x, rect.y), (rect.right, rect.y), (rect.right, rect.top), (rect.x, rect.top))
        edges.update(tuple(sorted((first, second))) for first, second in zip(corners, (*corners[1:], corners[0])))
    return len(edges)


def _near(first, second):
    return isinstance(first, (int, float)) and math.isfinite(first) and abs(first - second) <= 1e-3

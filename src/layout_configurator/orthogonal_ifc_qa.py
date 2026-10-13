"""Independent source-to-IFC geometry readback for orthogonal DRAFT exports.

This checker never imports the exporter or trusts its generated evidence.
It rebuilds expected geometry from canonical sources, reads actual profile
rings and placements, and compares both material and openings. PASS describes
this bounded exchange predicate only; full facility acceptance is UNKNOWN.
"""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

from shapely.geometry import LineString, Polygon, box
from shapely.ops import unary_union

from .building import BuildingIR
from .equipment import EquipmentLayoutResult, clearance_rect
from .flows import FlowRoutingResult
from .models import LayoutResult, Rect
from .orthogonal_equipment import validate_orthogonal_door_approaches
from .orthogonal_flows import validate_orthogonal_flow_routes
from .orthogonal_grid import OrthogonalGridCapabilityError, validate_orthogonal_grid
from .orthogonal_projection import build_orthogonal_wall_plan
from .orthogonal_validation import validate_orthogonal_equipment, validate_orthogonal_layout
from .orthogonal_window_capability import OrthogonalWindowCapabilityError, validate_orthogonal_window_capability
from .result_geometry import parse_layout_result


def validate_orthogonal_ifc(path: str | Path, building: BuildingIR, layout: LayoutResult,
                            equipment: EquipmentLayoutResult | None = None, *,
                            routing: FlowRoutingResult | None = None,
                            expected_sha256: str | None = None,
                            max_file_bytes: int = 16 * 1024 * 1024,
                            include_windows: bool = False,
                            include_grid: bool = False,
                            include_groups: bool = False) -> dict:
    """Read actual IFC4 geometry with bounded native input and strict identity.

    Supported representations are closed polyline area-profile extrusions
    and a route Axis polyline. Mapped/boolean/tilted/curved representations are
    rejected rather than replaced by their bounds. Numeric transform tolerance
    is 0.00001 mm; material difference tolerance is 0.0001 mm2. Strict EXPRESS
    validation is a separate executable check and is never inferred here.
    Opted-in windows require fresh source_facade_v1 eligibility, native
    relationships, actual world sill/head geometry and every wall material
    interval. The checker does not trust exporter indexes or generated reports.
    Explicit source grids require actual native ordered axes and the same
    curves in a visible FootPrint, with independent world-coordinate readback.
    Explicit groups require exact declared source properties and actual native
    memberships; no physical zone container or MEP network is inferred.
    """
    report = {"status": "FAIL", "ok": False, "scope": "Independent exact orthogonal IFC exchange geometry only",
              "full_facility_validation": {"status": "UNKNOWN", "ok": False}, "engineering_acceptance": "PENDING",
              "ifc_express": {"status": "NOT_RUN"}, "issues": [], "counts": {},
              "tolerance": {"coordinates_mm": 0.00001, "material_area_mm2": 0.0001}}
    issues = report["issues"]
    try:
        if not isinstance(include_windows, bool):
            raise ValueError("include_windows requires a boolean")
        if not isinstance(include_grid, bool):
            raise ValueError("include_grid requires a boolean")
        if not isinstance(include_groups, bool):
            raise ValueError("include_groups requires a boolean")
        options = {"include_windows": True} if include_windows else {}
        grid_options = {"include_grid": True} if include_grid else {}
        group_options = {"include_groups": True} if include_groups else {}
        if not isinstance(max_file_bytes, int) or isinstance(max_file_bytes, bool) or not 1 <= max_file_bytes <= 16 * 1024 * 1024:
            raise ValueError("max_file_bytes requires an integer between 1 and 16777216")
        target = Path(path)
        if target.stat().st_size > max_file_bytes:
            raise ValueError("IFC file exceeds the bounded readback byte limit")
        raw = target.read_bytes()
        if len(raw) > max_file_bytes:
            raise ValueError("IFC bytes exceed the bounded readback limit")
        digest = hashlib.sha256(raw).hexdigest()
        report["artifact_sha256"] = digest
        if expected_sha256 is not None and (not isinstance(expected_sha256, str) or expected_sha256 != digest):
            raise ValueError("IFC artifact SHA256 does not match the supplied expected bytes")
        building, layout, expected, wall_plan, capability, grid_capability, group_capability = _expected(
            building, layout, equipment, routing, **options, **grid_options, **group_options)
        if include_windows:
            report["window_capability"] = capability.to_dict()
        if include_grid:
            report["grid_capability"] = grid_capability.to_dict()
            report["grid_axes"] = []
        if include_groups:
            report["group_capability"] = group_capability.to_dict()
        source = {"building_sha256": _hash(building.to_dict()), "layout_sha256": _hash(layout.to_dict()),
                  "equipment_sha256": _hash(equipment.to_dict(building) if equipment is not None else None),
                  "routing_sha256": _hash(routing.to_dict() if routing is not None else None)}
        report["source"] = source
        import ifcopenshell
        import ifcopenshell.util.placement as placement
        import numpy as np

        # Parse the bytes just hashed, rather than reopening a mutable path.
        model = ifcopenshell.file.from_string(raw.decode("utf-8"))
        if model.schema != "IFC4":
            raise ValueError("Readback requires IFC4")
        if sum(1 for _ in model) > 100_000:
            raise ValueError("IFC entity limit of 100000 exceeded")
        projects = model.by_type("IfcProject")
        if len(projects) != 1:
            raise ValueError("IFC must have exactly one canonical project")
        project = projects[0]
        if project.GlobalId != _guid(building, "project"):
            issues.append("IFC project GlobalId differs from stable canonical source identity")
        if project.Name != building.layout.project_name:
            issues.append("IFC project name does not match the canonical source")
        metadata = _properties(project, "Pset_OrthogonalDraft")
        contract = {"Schema": "orthogonal-ifc-draft/0.1", "Status": "DRAFT", "FullFacilityValidation": "UNKNOWN",
                    "EngineeringAcceptance": "PENDING", "Units": "mm", "Variant": layout.variant,
                    "BuildingSHA256": source["building_sha256"], "LayoutSHA256": source["layout_sha256"],
                    "EquipmentSHA256": source["equipment_sha256"], "RoutingSHA256": source["routing_sha256"],
                    "EquipmentState": "SUPPLIED" if equipment is not None else "NOT_SUPPLIED",
                    "RoutingState": "SUPPLIED" if routing is not None else "NOT_SUPPLIED",
                    "HeightPolicy": "coordination_proxy_v1; rooms/walls=2800; doors/openings=2100; equipment=1000; service/routes=20 mm"}
        if include_windows:
            contract.update(WindowCapability="source_facade_v1", WindowSillMM=building.layout.window_sill_mm,
                            WindowHeightMM=building.layout.window_height_mm,
                            HeightPolicy="coordination_proxy_v1; rooms/walls=2800; doors/door-openings=2100; windows/window-openings=source sill+height; equipment=1000; service/routes=20 mm")
        if include_grid:
            contract.update(GridCapability="boundary_spanning_axes_v1",
                            GridSpecSHA256=grid_capability.spec_sha256,
                            StructuralGridSHA256=grid_capability.structural_grid_sha256)
        if include_groups:
            contract.update(GroupCapability="declared_semantic_groups_v1",
                            GroupCapabilitySHA256=_hash(group_capability.to_dict()))
        if not _same_properties(metadata, contract):
            issues.append("IFC source hash, variant, optional state or DRAFT acceptance metadata does not match the canonical source")
        length_units = [unit for unit in project.UnitsInContext.Units if unit.UnitType == "LENGTHUNIT"]
        if len(length_units) != 1 or not length_units[0].is_a("IfcSIUnit") or length_units[0].Name != "METRE" or length_units[0].Prefix != "MILLI":
            raise ValueError("IFC length units must be millimetres")
        contexts = tuple(project.RepresentationContexts or ())
        if len(contexts) != 1 or not contexts[0].is_a("IfcGeometricRepresentationContext") or contexts[0].CoordinateSpaceDimension != 3:
            raise ValueError("IFC requires one 3D model context")
        context = contexts[0]
        world = placement.get_axis2placement(context.WorldCoordinateSystem)
        if not np.isfinite(world).all():
            raise ValueError("IFC model context transform must be finite")
        actual, materials = {}, {}
        scaffolding = {"IfcSite", "IfcBuilding", "IfcBuildingStorey"}
        for element in model.by_type("IfcProduct"):
            if element.is_a() in scaffolding:
                suffix = {"IfcSite": "site", "IfcBuilding": "building", "IfcBuildingStorey": "storey"}[element.is_a()]
                if element.GlobalId != _guid(building, suffix):
                    issues.append(f"IFC {suffix} GlobalId differs from stable canonical source identity")
                continue
            identity = _properties(element, "Pset_OrthogonalIdentity")
            key = (identity.get("Kind"), identity.get("SourceId"))
            if key not in expected or key in actual:
                raise ValueError(f"IFC unexpected or duplicate semantic geometry identity {key}")
            record = expected[key]
            if element.GlobalId != _guid(building, key[0] + ":" + key[1]):
                issues.append(f"IFC {key} GlobalId differs from stable canonical source identity")
            if element.is_a() != record["class"] or element.Name != key[1] or not _same_properties(identity, record["identity"]):
                issues.append(f"IFC {key} source identity, class or scalar semantics mismatch")
            if record.get("object_type") and element.ObjectType != record["object_type"]:
                issues.append(f"IFC {key} object type mismatch")
            if key[0] == "room" and element.LongName != record["identity"]["RoomType"]:
                issues.append(f"IFC {key} room type mismatch")
            if key[0] == "grid":
                report["grid_axes"] = _grid_axes(element, grid_capability, context, world, placement, np)
                actual[key] = element
                continue
            material, matrix, representations, vertical_range = _material(element, context, world, placement, np,
                record["height"], record["base_elevation"])
            materials[key] = (material, vertical_range)
            if material.symmetric_difference(record["geometry"]).area > 0.0001 or material.hausdorff_distance(record["geometry"]) > 0.00001:
                issues.append(f"IFC {key} actual material geometry differs from canonical source")
            if key[0] == "route":
                points = _axis(representations, context, matrix, np)
                expected_points = record["points"]
                if len(points) != len(expected_points) or any(abs(a - b) > 0.00001 for pair, expected_pair in zip(points, expected_points) for a, b in zip(pair, expected_pair)):
                    issues.append(f"IFC {key} actual route Axis geometry differs from the supplied validated route")
            elif len(representations) != 1:
                issues.append(f"IFC {key} has unexpected representation geometry")
            if key[0] == "door" and (not _close(element.OverallWidth, record["identity"]["WidthMM"]) or not _close(element.OverallHeight, 2100)):
                issues.append(f"IFC {key} actual door width or height differs from source")
            if key[0] == "window" and (not _close(element.OverallWidth, record["identity"]["WidthMM"])
                    or not _close(element.OverallHeight, record["height"]) or element.PredefinedType != "WINDOW"):
                issues.append(f"IFC {key} actual window width, height or type differs from source")
            if key[0] == "window-opening" and element.PredefinedType != "OPENING":
                issues.append(f"IFC {key} actual window void type differs from source")
            actual[key] = element
        if set(actual) != set(expected):
            issues.append(f"IFC missing source geometry identities: {sorted(set(expected) - set(actual))}")
        _relations(model, actual, wall_plan, issues, building=building if include_windows else None)
        if include_grid:
            native_axes = [axis for key, element in actual.items() if key[0] == "grid"
                           for axis in (*element.UAxes, *element.VAxes)]
            if {axis.id() for axis in model.by_type("IfcGridAxis")} != {axis.id() for axis in native_axes}:
                issues.append("IFC unexpected or missing native grid axis inventory")
        if include_groups:
            report["groups"] = _groups(model, building, group_capability, actual)
        roots = model.by_type("IfcRoot")
        if len({item.GlobalId for item in roots}) != len(roots):
            issues.append("IFC duplicate GlobalId")
        report["counts"] = {plural: sum(key[0] == kind for key in actual) for plural, kind in
                            (("rooms", "room"), ("walls", "wall"), ("doors", "door"), ("openings", "opening"),
                             ("equipment", "equipment"), ("services", "service"), ("routes", "route"))}
        if include_windows:
            report["counts"].update(windows=sum(key[0] == "window" for key in actual),
                window_openings=sum(key[0] == "window-opening" for key in actual))
            report["counts"]["openings"] += report["counts"]["window_openings"]
            report["window_material"] = _window_material(materials, expected, wall_plan, issues)
        if include_grid:
            report["counts"].update(grids=sum(key[0] == "grid" for key in actual),
                                    grid_axes=len(report["grid_axes"]))
        if include_groups:
            report["counts"].update(zones=sum(group["kind"] == "zone" for group in report["groups"]),
                process_systems=sum(group["kind"] == "flow-system" for group in report["groups"]),
                group_relations=len(model.by_type("IfcRelAssignsToGroup")))
    except Exception as exc:
        issues.append(f"IFC independent readback failed: {exc}")
    if not issues:
        report.update(status="PASS", ok=True)
    return report


def _expected(building, layout, equipment, routing, *, include_windows=False, include_grid=False, include_groups=False):
    if not isinstance(building, BuildingIR) or not isinstance(layout, LayoutResult):
        raise ValueError("Canonical BuildingIR and LayoutResult required")
    options = {"include_windows": True} if include_windows else {}
    group_capability = None
    if include_groups:
        from .orthogonal_groups import OrthogonalGroupsCapabilityError, validate_orthogonal_groups
        group_capability = validate_orthogonal_groups(building, layout, equipment, routing=routing)
        if not group_capability.ok:
            raise OrthogonalGroupsCapabilityError(group_capability)
    grid_capability = None
    if include_grid:
        grid_capability = validate_orthogonal_grid(building.layout, building.structural_grid)
        if not grid_capability.ok:
            raise OrthogonalGridCapabilityError(grid_capability)
    capability = None
    if include_windows:
        capability = validate_orthogonal_window_capability(building.layout, layout)
        if not capability.ok:
            raise OrthogonalWindowCapabilityError(capability)
    canonical_building = BuildingIR.from_mapping(building.to_dict())
    canonical_layout = parse_layout_result(layout.to_dict())
    if not include_windows and not include_grid and not include_groups:
        building, layout = canonical_building, canonical_layout
    if len(building.layout.rooms) > 64 or len(building.equipment) > 64:
        raise ValueError("Source room/equipment limit of 64 exceeded")
    if not validate_orthogonal_layout(building.layout, layout, **options).ok:
        raise ValueError("Source room/opening local validation is not PASS")
    if equipment is not None:
        if not isinstance(equipment, EquipmentLayoutResult) or not validate_orthogonal_equipment(building, layout, equipment, **options).ok:
            raise ValueError("Source equipment local validation is not PASS")
        if not validate_orthogonal_door_approaches(building, layout, equipment, **options).ok:
            raise ValueError("Source local door approaches are not PASS")
    if routing is not None:
        if not isinstance(routing, FlowRoutingResult) or len(routing.routes) > 256 or not validate_orthogonal_flow_routes(building, layout, routing, equipment, **options).ok:
            raise ValueError("Source route validation is not PASS or route limit of 256 exceeded")
    walls = build_orthogonal_wall_plan(building.layout, layout, **options)
    expected = {}

    def record(kind, identifier, ifc_class, geometry, height, extra=None, object_type=None, points=None, base_elevation=0):
        expected[(kind, identifier)] = {"class": ifc_class, "geometry": geometry, "height": height,
            "identity": {"Kind": kind, "SourceId": identifier, **(extra or {})}, "object_type": object_type,
            "points": points, "base_elevation": base_elevation}

    for room in building.layout.rooms:
        shape = layout.placements[room.id]
        geometry = box(shape.x, shape.y, shape.right, shape.top) if isinstance(shape, Rect) else shape.geometry
        record("room", room.id, "IfcSpace", geometry, 2800, {"RoomType": room.type})
    record("wall", "wall-network", "IfcWall", walls.geometry, 2800, object_type="OrthogonalWallNetwork")
    for opening in walls.openings:
        identity = {"RoomA": opening.room_a, "RoomB": opening.room_b, "External": opening.external,
                    "WidthMM": opening.width, "Orientation": opening.orientation}
        record("opening", opening.id, "IfcOpeningElement",
               opening.centerline.buffer(building.layout.wall_thickness_mm / 2 + 1, cap_style=2, join_style=2), 2100, identity)
        record("door", opening.id, "IfcDoor",
               opening.centerline.buffer(building.layout.wall_thickness_mm / 2, cap_style=2, join_style=2), 2100, identity)
    for opening in walls.windows:
        sill, height = building.layout.window_sill_mm, building.layout.window_height_mm
        identity = {"HostRoomId": opening.room_id, "Orientation": opening.orientation,
                    "WidthMM": opening.width, "SillMM": sill, "HeightMM": height}
        record("window-opening", opening.id, "IfcOpeningElement",
            opening.centerline.buffer(building.layout.wall_thickness_mm / 2 + 1, cap_style=2, join_style=2),
            height, identity, base_elevation=sill)
        record("window", opening.id, "IfcWindow",
            opening.centerline.buffer(building.layout.wall_thickness_mm / 2, cap_style=2, join_style=2),
            height, identity, base_elevation=sill)
    if equipment is not None:
        specs = {item.id: item for item in building.equipment}
        for item in equipment.placements:
            identity = {"HostRoomId": item.room_id, "Rotated": item.rotated}
            for kind, rect, height, object_type in (("equipment", item.rect, 1000, "OrthogonalEquipment"),
                ("service", clearance_rect(specs[item.equipment_id], item.rect, item.rotated), 20, "OrthogonalEquipmentService")):
                record(kind, item.equipment_id, "IfcBuildingElementProxy", box(rect.x, rect.y, rect.right, rect.top),
                       height, identity, object_type)
    if routing is not None:
        for route in routing.routes:
            identifier = json.dumps([route.flow_id, route.from_id, route.to_id, route.from_room_id, route.to_room_id],
                                    ensure_ascii=False, separators=(",", ":"))
            identity = {"FlowId": route.flow_id, "FromId": route.from_id, "ToId": route.to_id,
                "FromRoomId": route.from_room_id, "ToRoomId": route.to_room_id,
                "RoomPath": json.dumps(list(route.room_path), separators=(",", ":")),
                "MinimumClearWidthMM": route.minimum_clear_width_mm}
            record("route", identifier, "IfcBuildingElementProxy",
                LineString(route.points).buffer(route.minimum_clear_width_mm / 2, cap_style=3, join_style=2),
                20, identity, "OrthogonalFlowCorridor", tuple((x, y, 0.) for x, y in route.points))
    if include_grid and grid_capability.axes:
        expected[("grid", "structural-grid")] = {"class": "IfcGrid",
            "identity": {"Kind": "grid", "SourceId": "structural-grid", "Policy": "boundary_spanning_axes_v1"}}
    return building, layout, expected, walls, capability, grid_capability, group_capability


def _group_contract(building, group):
    """Rebuild exact source declaration properties without importing exporter."""
    source = group.declaration
    contract = {"DeclarationJSON": json.dumps(source, sort_keys=True, ensure_ascii=False,
                                              allow_nan=False, separators=(",", ":")),
                "DeclarationState": "DECLARED", "SourceContract": "BuildingIR " + building.version,
                "SourceEntityType": "ZoneSpec" if group.kind == "zone" else "FlowSpec"}
    if group.kind == "zone":
        contract.update(ZoneId=group.id, ZoneType=source["type"],
            DirectRoomIdsJSON=json.dumps(source["room_ids"], ensure_ascii=False, separators=(",", ":")),
            Airlock=source.get("airlock", False), PhysicalContainerValidation="UNKNOWN")
        for name, key in (("parent_zone_id", "ParentZoneId"), ("cleanroom_class", "CleanroomClass"),
                           ("pressure_pa", "PressurePa")):
            if name in source:
                contract[key] = source[name]
    else:
        contract.update(FlowId=group.id, FlowType=source["type"],
            FromIdsJSON=json.dumps(source["from_ids"], ensure_ascii=False, separators=(",", ":")),
            ToIdsJSON=json.dumps(source["to_ids"], ensure_ascii=False, separators=(",", ":")),
            Required=source["required"], MinimumClearWidthMM=source["minimum_clear_width_mm"],
            NetworkEngineering="UNKNOWN", RepresentationKind="Declared process movement group; not an MEP network")
        if "stage" in source:
            contract["Stage"] = source["stage"]
    return contract


def _groups(model, building, capability, products):
    expected = {(group.kind, group.id): group for group in capability.groups}
    actual = {}
    for element in model.by_type("IfcGroup"):
        identity = _properties(element, "Pset_OrthogonalIdentity")
        key = (identity.get("Kind"), identity.get("SourceId"))
        if key not in expected or key in actual:
            raise ValueError("IFC unexpected, duplicate or missing source native group identity")
        group = expected[key]
        if (element.is_a() != ("IfcZone" if group.kind == "zone" else "IfcSystem")
                or element.Name != group.id or element.ObjectType != group.declaration["type"]
                or element.Description != ("Declared functional zone; not a qualified physical container"
                    if group.kind == "zone" else "Declared process movement; not a designed MEP network")
                or element.GlobalId != _guid(building, group.kind + ":" + group.id)
                or not _same_properties(identity, {"Kind": group.kind, "SourceId": group.id,
                                                  "Policy": "declared_semantic_groups_v1"})):
            raise ValueError("IFC native group class, source identity, type, policy or stable GUID differs")
        if (group.kind == "zone" and element.LongName is not None
                or element.Decomposes or element.IsDecomposedBy or element.Nests or element.IsNestedBy):
            raise ValueError("IFC declared native groups cannot acquire physical scope text or decomposition links")
        contract = _group_contract(building, group)
        if not _same_properties(_properties(element, "Pset_OrthogonalGroup"), contract):
            raise ValueError("IFC native group declared scalar or JSON source properties differ")
        definitions = tuple(element.IsDefinedBy)
        if (len(definitions) != 2
                or any(link.is_a() != "IfcRelDefinesByProperties"
                    or not link.RelatingPropertyDefinition.is_a("IfcPropertySet")
                    or tuple(link.RelatedObjects) != (element,) for link in definitions)
                or {link.RelatingPropertyDefinition.Name for link in definitions}
                    != {"Pset_OrthogonalIdentity", "Pset_OrthogonalGroup"}):
            raise ValueError("IFC declared native groups require exactly their own identity/source property relationships")
        link = next(link for link in definitions if link.RelatingPropertyDefinition.Name == "Pset_OrthogonalGroup")
        definition = link.RelatingPropertyDefinition
        if (definition.GlobalId != _guid(building, "group-properties:" + element.GlobalId)
                or link.GlobalId != _guid(building, "group-defines:" + element.GlobalId)):
            raise ValueError("IFC native group source property or defining relationship GUID differs")
        for prop in definition.HasProperties:
            value = contract[prop.Name]
            nominal_type = "IfcText" if prop.Name.endswith("JSON") else "IfcBoolean" if isinstance(value, bool) else "IfcReal" if isinstance(value, (int, float)) else "IfcLabel"
            if prop.NominalValue.is_a() != nominal_type or prop.Unit is not None:
                raise ValueError("IFC native group source scalar or JSON property types differ")
        actual[key] = element
    if set(actual) != set(expected):
        raise ValueError("IFC missing declared native zone or process system")
    if any(link.RelatingSystem in actual.values() for link in model.by_type("IfcRelServicesBuildings")):
        raise ValueError("IFC declared process groups cannot acquire an unprojected building service relationship")
    occurrences = {**products, **actual}
    assignments = model.by_type("IfcRelAssignsToGroup")
    if any(relation.is_a() != "IfcRelAssignsToGroup" or relation.RelatingGroup not in actual.values()
           for relation in assignments):
        raise ValueError("IFC unexpected native group assignment kind or target")
    records = []
    for key, group in expected.items():
        element = actual[key]
        links = [relation for relation in assignments if relation.RelatingGroup == element]
        if len(links) != int(bool(group.members)):
            raise ValueError("IFC source group requires exactly one nonempty assignment, or none when empty")
        members = []
        if links:
            relation = links[0]
            refs = tuple(relation.RelatedObjects)
            if (relation.GlobalId != _guid(building, "group-membership:" + group.kind + ":" + group.id)
                    or relation.Name is not None or relation.Description is not None
                    or relation.RelatedObjectsType is not None
                    or not refs or len({member.id() for member in refs}) != len(refs)):
                raise ValueError("IFC group assignment GUID or duplicate/empty member inventory differs")
            expected_refs = {occurrences[(member.kind, member.id)].id() for member in group.members}
            if {member.id() for member in refs} != expected_refs:
                raise ValueError("IFC actual direct native group membership differs from source")
            for member in refs:
                identity = _properties(member, "Pset_OrthogonalIdentity")
                members.append({"kind": identity["Kind"], "id": identity["SourceId"]})
        records.append({"kind": group.kind, "id": group.id, "class": element.is_a(),
                        "global_id": element.GlobalId, "members": sorted(members, key=lambda item: (item["kind"], item["id"]))})
    return records


def _grid_axes(element, capability, context, world, placement, np):
    """Read native axes and visible curve membership from the parsed IFC."""
    if not element.is_a("IfcGrid") or element.PredefinedType != "RECTANGULAR" or element.WAxes is not None:
        raise ValueError("IFC source grid requires one rectangular native grid without W axes")
    native = tuple(element.UAxes or ()) + tuple(element.VAxes or ())
    x_count = sum(axis.direction == "x" for axis in capability.axes)
    if len(element.UAxes or ()) != x_count or len(element.VAxes or ()) != len(capability.axes) - x_count:
        raise ValueError("IFC actual native grid direction/count differs from source")
    if len({axis.id() for axis in native}) != len(native):
        raise ValueError("IFC native grid axes have duplicate membership")
    if element.Representation is None or not element.Representation.is_a("IfcProductDefinitionShape"):
        raise ValueError("IFC source grid is missing actual FootPrint geometry")
    representations = tuple(element.Representation.Representations or ())
    if len(representations) != 1:
        raise ValueError("IFC source grid requires exactly one actual FootPrint representation")
    representation = representations[0]
    if (representation.ContextOfItems != context or representation.RepresentationIdentifier != "FootPrint"
            or representation.RepresentationType != "GeometricCurveSet" or len(representation.Items) != 1
            or not representation.Items[0].is_a("IfcGeometricCurveSet")):
        raise ValueError("IFC source grid FootPrint context/type/curve-set contract failed")
    curves = tuple(representation.Items[0].Elements)
    if len(curves) != len(native) or {curve.id() for curve in curves} != {axis.AxisCurve.id() for axis in native}:
        raise ValueError("IFC source grid visible FootPrint must contain exactly its native axis curves")
    matrix = _matrix(element, world, placement, np)
    records = []
    for actual, expected in zip(native, capability.axes):
        if actual.AxisTag != expected.label or actual.SameSense is not True:
            raise ValueError("IFC native grid source labels/order/SameSense differs from source")
        curve = actual.AxisCurve
        if not curve.is_a("IfcPolyline") or len(curve.Points) != 2:
            raise ValueError("IFC source grid axes require actual continuous two-point polylines")
        points = []
        for point, source_point in zip(curve.Points, (expected.start, expected.end)):
            coords = tuple(point.Coordinates)
            if len(coords) != 2 or not all(math.isfinite(value) for value in coords):
                raise ValueError("IFC native grid coordinates require finite 2D source points")
            transformed = matrix @ np.asarray((*coords, 0., 1.))
            target = np.asarray((*source_point, 0.))
            if not np.isfinite(transformed).all() or not np.allclose(transformed[:3], target, atol=0.00001, rtol=0):
                raise ValueError("IFC actual grid world geometry differs from continuous source axes")
            points.append([float(value) for value in transformed[:3]])
        records.append({"id": expected.id, "direction": expected.direction, "label": actual.AxisTag,
                        "world_points_mm": points})
    return records


def _properties(element, name):
    groups = [relation.RelatingPropertyDefinition for relation in element.IsDefinedBy
              if relation.is_a("IfcRelDefinesByProperties") and relation.RelatingPropertyDefinition.is_a("IfcPropertySet")
              and relation.RelatingPropertyDefinition.Name == name]
    if len(groups) != 1:
        raise ValueError(f"{element.is_a()} requires exactly one {name}")
    properties = groups[0].HasProperties
    if len(properties) > 32 or len({item.Name for item in properties}) != len(properties):
        raise ValueError("IFC property set is oversized or contains duplicate names")
    values = {}
    for prop in properties:
        if not prop.is_a("IfcPropertySingleValue") or prop.NominalValue is None:
            raise ValueError("IFC identity properties require nonempty scalar values")
        value = prop.NominalValue.wrappedValue
        if isinstance(value, str) and len(value) > 65_536 or isinstance(value, (float, int)) and not math.isfinite(value):
            raise ValueError("IFC property value must be finite and bounded")
        values[prop.Name] = value
    return values


def _matrix(element, world, placement, np):
    current, seen = element.ObjectPlacement, set()
    if current is None:
        raise ValueError("IFC geometry is missing its actual object placement")
    while current is not None:
        if not current.is_a("IfcLocalPlacement") or current.id() in seen or len(seen) >= 32:
            raise ValueError("IFC placement chain must be local, acyclic and at most 32 levels")
        seen.add(current.id())
        current = current.PlacementRelTo
    matrix = world @ placement.get_local_placement(element.ObjectPlacement)
    if not np.isfinite(matrix).all():
        raise ValueError("IFC actual placement transform must be finite")
    return matrix


def _material(element, context, world, placement, np, height, base_elevation=0):
    if element.Representation is None:
        raise ValueError("IFC actual material geometry is missing")
    representations = tuple(element.Representation.Representations or ())
    bodies = [item for item in representations if item.RepresentationIdentifier == "Body"]
    if len(bodies) != 1 or len(representations) > 2:
        raise ValueError("IFC actual geometry requires exactly one Body representation")
    body = bodies[0]
    if body.ContextOfItems != context or body.RepresentationType != "SweptSolid" or not 1 <= len(body.Items) <= 4096:
        raise ValueError("IFC actual Body context/type/solid-count contract failed")
    matrix = _matrix(element, world, placement, np)
    polygons, vertical_ranges = [], []
    for solid in body.Items:
        if not solid.is_a("IfcExtrudedAreaSolid") or not _close(solid.Depth, height):
            raise ValueError("IFC actual material geometry must have the expected coordination extrusion height")
        transform = matrix @ placement.get_axis2placement(solid.Position)
        direction = np.asarray(solid.ExtrudedDirection.DirectionRatios, dtype=float)
        if len(direction) != 3 or not np.isfinite(direction).all() or np.linalg.norm(direction) == 0:
            raise ValueError("IFC extrusion direction must be finite and nonzero")
        direction = transform[:3, :3] @ (direction / np.linalg.norm(direction))
        if not np.allclose(direction, (0, 0, 1), atol=0.0000001, rtol=0):
            raise ValueError("IFC actual material geometry requires an upright positive extrusion")
        profile = solid.SweptArea
        if profile.is_a() not in ("IfcArbitraryClosedProfileDef", "IfcArbitraryProfileDefWithVoids") or profile.ProfileType != "AREA":
            raise ValueError("Unsupported IFC actual material profile geometry")
        holes = tuple(profile.InnerCurves) if profile.is_a("IfcArbitraryProfileDefWithVoids") else ()
        if len(holes) > 4096:
            raise ValueError("IFC profile hole-count limit exceeded")
        polygon = Polygon(_ring(profile.OuterCurve, transform, np, base_elevation),
                          [_ring(hole, transform, np, base_elevation) for hole in holes])
        if polygon.is_empty or not polygon.is_valid or polygon.area <= 0:
            raise ValueError("IFC actual profile geometry is not a valid nonempty polygon with contained holes")
        polygons.append(polygon)
        vertical_ranges.append((float(transform[2, 3]), float(transform[2, 3] + solid.Depth)))
    material = unary_union(polygons)
    if sum(polygon.area for polygon in polygons) - material.area > 0.0001:
        raise ValueError("IFC actual material solids overlap")
    return material, matrix, representations, (min(item[0] for item in vertical_ranges), max(item[1] for item in vertical_ranges))


def _ring(curve, transform, np, base_elevation=0):
    if not curve.is_a("IfcPolyline") or not 4 <= len(curve.Points) <= 8192:
        raise ValueError("IFC profile geometry requires a closed 4..8192 point polyline")
    points = []
    for point in curve.Points:
        coordinates = tuple(point.Coordinates)
        if len(coordinates) != 2 or not all(math.isfinite(value) for value in coordinates):
            raise ValueError("IFC area profile coordinates must be finite 2D points")
        projected = transform @ np.asarray((*coordinates, 0., 1.))
        if not np.isfinite(projected).all() or abs(projected[2] - base_elevation) > 0.00001:
            message = "IFC actual material geometry has a nonzero or invalid base elevation" if base_elevation == 0 else "IFC actual material geometry has an invalid base elevation differing from canonical source"
            raise ValueError(message)
        points.append((float(projected[0]), float(projected[1])))
    if any(abs(a - b) > 0.00001 for a, b in zip(points[0], points[-1])):
        raise ValueError("IFC profile geometry is not closed")
    if any(abs(first[0] - second[0]) > 0.00001 and abs(first[1] - second[1]) > 0.00001 for first, second in zip(points, points[1:])):
        raise ValueError("IFC actual profile geometry is not orthogonal")
    return points


def _axis(representations, context, matrix, np):
    axes = [item for item in representations if item.RepresentationIdentifier == "Axis"]
    if len(axes) != 1 or len(representations) != 2 or axes[0].ContextOfItems != context or axes[0].RepresentationType != "Curve3D":
        raise ValueError("IFC route requires one actual Axis and Body representation")
    items = axes[0].Items
    if len(items) != 1 or not items[0].is_a("IfcPolyline") or not 2 <= len(items[0].Points) <= 8192:
        raise ValueError("IFC route Axis geometry requires a bounded 3D polyline")
    points = []
    for point in items[0].Points:
        coords = tuple(point.Coordinates)
        if len(coords) != 3 or not all(math.isfinite(value) for value in coords):
            raise ValueError("IFC route Axis coordinates must be finite 3D points")
        transformed = matrix @ np.asarray((*coords, 1.))
        if not np.isfinite(transformed).all():
            raise ValueError("IFC route Axis geometry transform is nonfinite")
        points.append(tuple(float(value) for value in transformed[:3]))
    return tuple(points)


def _relations(model, actual, walls, issues, *, building=None):
    storeys = model.by_type("IfcBuildingStorey")
    if len(model.by_type("IfcSite")) != 1 or len(model.by_type("IfcBuilding")) != 1 or len(storeys) != 1:
        issues.append("IFC must have exactly one site, building and ground storey")
        return
    storey = storeys[0]
    spaces = {element.id() for key, element in actual.items() if key[0] == "room"}
    aggregates = [relation for relation in model.by_type("IfcRelAggregates") if relation.RelatingObject == storey]
    if len(aggregates) != 1 or {item.id() for item in aggregates[0].RelatedObjects} != spaces:
        issues.append("IFC actual room aggregation does not match canonical source rooms")
    contained = {element.id() for key, element in actual.items() if key[0] not in ("room", "opening", "window-opening")}
    relations = model.by_type("IfcRelContainedInSpatialStructure")
    if len(relations) != 1 or relations[0].RelatingStructure != storey or {item.id() for item in relations[0].RelatedElements} != contained:
        issues.append("IFC physical geometry containment does not match the canonical source")
    voids, fills = model.by_type("IfcRelVoidsElement"), model.by_type("IfcRelFillsElement")
    if len(voids) != len(walls.openings) + len(walls.windows) or len(fills) != len(walls.openings) + len(walls.windows):
        issues.append("IFC actual source opening void/fill relation counts differ")
    for opening in walls.openings:
        void, door, wall = actual.get(("opening", opening.id)), actual.get(("door", opening.id)), actual.get(("wall", "wall-network"))
        if void is None or door is None or wall is None:
            continue
        if sum(relation.RelatingBuildingElement == wall and relation.RelatedOpeningElement == void for relation in voids) != 1:
            issues.append(f"IFC {opening.id} missing or incorrect actual wall void relation")
        if sum(relation.RelatingOpeningElement == void and relation.RelatedBuildingElement == door for relation in fills) != 1:
            issues.append(f"IFC {opening.id} missing or incorrect actual door filling relation")
    for opening in walls.windows:
        void, window, wall = actual.get(("window-opening", opening.id)), actual.get(("window", opening.id)), actual.get(("wall", "wall-network"))
        if void is None or window is None or wall is None:
            continue
        matching_voids = [relation for relation in voids if relation.RelatingBuildingElement == wall and relation.RelatedOpeningElement == void]
        matching_fills = [relation for relation in fills if relation.RelatingOpeningElement == void and relation.RelatedBuildingElement == window]
        if len(matching_voids) != 1:
            issues.append(f"IFC {opening.id} missing or incorrect actual window wall void relation")
        if len(matching_fills) != 1:
            issues.append(f"IFC {opening.id} missing or incorrect actual window filling relation")
        if building is not None and (any(relation.GlobalId != _guid(building, "void-window:" + opening.id) for relation in matching_voids)
                or any(relation.GlobalId != _guid(building, "fill-window:" + opening.id) for relation in matching_fills)):
            issues.append(f"IFC {opening.id} window relation GlobalId differs from stable canonical source identity")


def _window_material(materials, expected, walls, issues):
    """Compare actual native void semantics in every wall-height interval.

    Body profiles and world elevations come from the parsed IFC, not exporter
    reports. Native relations have already been checked against the exact
    window inventory. Door-cut body material retains its existing semantics.
    """
    keys = [("window-opening", window.id) for window in walls.windows]
    if ("wall", "wall-network") not in materials or any(key not in materials for key in keys):
        return []
    wall, wall_range = materials[("wall", "wall-network")]
    levels = sorted({*wall_range, *(level for key in keys for level in materials[key][1])})
    intervals, areas = [], []
    for low, high in zip(levels, levels[1:]):
        if high <= low or low < wall_range[0] or high > wall_range[1]:
            continue
        midpoint = (low + high) / 2
        active = [materials[key][0] for key in keys if materials[key][1][0] <= midpoint < materials[key][1][1]]
        actual_slice = wall.difference(unary_union(active)) if active else wall
        source_active = [expected[key]["geometry"] for key in keys
            if expected[key]["base_elevation"] <= midpoint < expected[key]["base_elevation"] + expected[key]["height"]]
        source_wall = expected[("wall", "wall-network")]["geometry"]
        source_slice = source_wall.difference(unary_union(source_active)) if source_active else source_wall
        if actual_slice.symmetric_difference(source_slice).area > 0.0001:
            issues.append(f"IFC actual wall material interval {low:g}..{high:g} mm differs from canonical source")
        intervals.append([low, high])
        areas.append(actual_slice.area)
    return [{"id": window.id, "host_room_id": window.room_id,
             "z_intervals_mm": intervals, "actual_material_area_mm2": areas,
             "scope": "Actual wall body minus native window voids; source sill/head coordination material only"}
            for window in walls.windows]


def _close(actual, expected):
    return isinstance(actual, (float, int)) and not isinstance(actual, bool) and math.isfinite(actual) and abs(actual - expected) <= 0.00001


def _same_properties(actual, expected):
    if set(actual) != set(expected):
        return False
    for key, value in expected.items():
        observed = actual[key]
        if isinstance(value, bool):
            if not isinstance(observed, bool) or observed != value:
                return False
        elif isinstance(value, str):
            if not isinstance(observed, str) or observed != value:
                return False
        elif isinstance(observed, bool) or not isinstance(observed, (float, int)) or observed != value:
            return False
    return True


def _hash(payload):
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, allow_nan=False,
        sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def _guid(building, suffix):
    import ifcopenshell.guid
    value = f"{building.layout.project_name}:{building.layout.version}:orthogonal-ifc-draft/0.1:{suffix}"
    return ifcopenshell.guid.compress(str(uuid5(NAMESPACE_URL, value)))

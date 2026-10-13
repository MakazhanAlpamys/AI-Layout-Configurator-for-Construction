"""Exact orthogonal IFC4 projection with explicit DRAFT acceptance scope.

The canonical building and supplied layout are the only geometry sources.
Walls contain their actual holes and door gaps; there is no bounding-box room
substitution. Heights are coordination proxies, not engineering calculations.
The existing rectangular facility dispatcher remains unchanged.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from shapely.geometry import LineString, Polygon, box

from .building import BuildingIR
from .equipment import EquipmentLayoutResult, clearance_rect
from .flows import FlowRoutingResult
from .ifc import _add_properties, _axis_placement, _direction, _guid_value, _model_context, _owner_history, _point, _units
from .models import LayoutResult, Rect
from .orthogonal_equipment import validate_orthogonal_door_approaches
from .orthogonal_flows import validate_orthogonal_flow_routes
from .orthogonal_grid import OrthogonalGridCapabilityError, validate_orthogonal_grid
from .orthogonal_projection import build_orthogonal_wall_plan
from .orthogonal_validation import validate_orthogonal_equipment, validate_orthogonal_layout
from .orthogonal_window_capability import OrthogonalWindowCapabilityError, validate_orthogonal_window_capability
from .result_geometry import parse_layout_result


def export_orthogonal_draft_ifc(path: str | Path, building: BuildingIR, layout: LayoutResult,
                                equipment: EquipmentLayoutResult | None = None, *,
                                routing: FlowRoutingResult | None = None,
                                include_windows: bool = False,
                                include_grid: bool = False,
                                include_groups: bool = False) -> dict:
    """Export source-backed rooms, actual walls/openings and optional geometry.

    Supplied equipment must pass independent directional service and complete
    local door-approach checks. Supplied routes must pass B7's independently
    rebuilt full-width corridor checks. Omitted equipment/routes are explicitly
    NOT_SUPPLIED, never a claim of complete facility validation.
    Only include_windows=True enables fresh source_facade_v1 eligibility and
    native voids/fills at the source global sill and height. The underlying
    two-dimensional wall band remains closed across windows.
    Only include_grid=True enables declared continuous boundary-spanning
    reference axes. Axes do not constrain rooms or create structural material.
    Only include_groups=True projects declared functional zones and process
    movement groups. Native membership adds no physical containers or networks.
    All input and capability checks precede opening the destination file.
    """
    if not isinstance(include_windows, bool):
        raise ValueError("include_windows requires a boolean")
    if not isinstance(include_grid, bool):
        raise ValueError("include_grid requires a boolean")
    if not isinstance(include_groups, bool):
        raise ValueError("include_groups requires a boolean")
    options = {"include_windows": True} if include_windows else {}
    grid_options = {"include_grid": True} if include_grid else {}
    group_options = {"include_groups": True} if include_groups else {}
    building, layout, reports = _inputs(building, layout, equipment, routing,
                                       **options, **grid_options, **group_options)
    grid_capability = validate_orthogonal_grid(building.layout, building.structural_grid) if include_grid else None
    group_capability = None
    if include_groups:
        from .orthogonal_groups import validate_orthogonal_groups
        group_capability = validate_orthogonal_groups(building, layout, equipment, routing=routing)
    walls = build_orthogonal_wall_plan(building.layout, layout, **options)
    source = {"building_sha256": _hash(building.to_dict()), "layout_sha256": _hash(layout.to_dict()),
              "equipment_sha256": _hash(equipment.to_dict(building) if equipment is not None else None),
              "routing_sha256": _hash(routing.to_dict() if routing is not None else None)}
    # Match the existing IFC identity policy: project name and source version
    # identify the project; semantic IDs identify occurrences. Geometry hashes
    # and variant numbers are provenance, never identity namespace inputs.
    namespace = f"{building.layout.project_name}:{building.layout.version}:orthogonal-ifc-draft/0.1"
    import ifcopenshell

    document = ifcopenshell.file(schema="IFC4")
    owner, context = _owner_history(document), _model_context(document)

    def guid(name):
        return _guid_value(f"{namespace}:{name}")

    def placement():
        return document.create_entity("IfcLocalPlacement", RelativePlacement=_axis_placement(document, (0, 0, 0)))

    project = document.create_entity("IfcProject", GlobalId=guid("project"), OwnerHistory=owner,
        Name=building.layout.project_name, RepresentationContexts=[context], UnitsInContext=_units(document))
    site = document.create_entity("IfcSite", GlobalId=guid("site"), OwnerHistory=owner,
        Name="Draft coordination site", CompositionType="ELEMENT", ObjectPlacement=placement())
    facility = document.create_entity("IfcBuilding", GlobalId=guid("building"), OwnerHistory=owner,
        Name=building.layout.project_name, CompositionType="ELEMENT", ObjectPlacement=placement())
    storey = document.create_entity("IfcBuildingStorey", GlobalId=guid("storey"), OwnerHistory=owner,
        Name="Draft ground floor", CompositionType="ELEMENT", ObjectPlacement=placement(), Elevation=0.)
    for parent, child, name in ((project, site, "site"), (site, facility, "building"), (facility, storey, "storey")):
        document.create_entity("IfcRelAggregates", GlobalId=guid("aggregate-" + name), OwnerHistory=owner,
            RelatingObject=parent, RelatedObjects=[child])
    metadata = {
        "Schema": "orthogonal-ifc-draft/0.1", "Status": "DRAFT", "FullFacilityValidation": "UNKNOWN",
        "EngineeringAcceptance": "PENDING", "Units": "mm", "Variant": layout.variant,
        "BuildingSHA256": source["building_sha256"], "LayoutSHA256": source["layout_sha256"],
        "EquipmentSHA256": source["equipment_sha256"], "RoutingSHA256": source["routing_sha256"],
        "EquipmentState": "SUPPLIED" if equipment is not None else "NOT_SUPPLIED",
        "RoutingState": "SUPPLIED" if routing is not None else "NOT_SUPPLIED",
        "HeightPolicy": "coordination_proxy_v1; rooms/walls=2800; doors/openings=2100; equipment=1000; service/routes=20 mm"}
    if include_windows:
        metadata.update(WindowCapability="source_facade_v1", WindowSillMM=building.layout.window_sill_mm,
                        WindowHeightMM=building.layout.window_height_mm,
                        HeightPolicy="coordination_proxy_v1; rooms/walls=2800; doors/door-openings=2100; windows/window-openings=source sill+height; equipment=1000; service/routes=20 mm")
    if include_grid:
        metadata.update(GridCapability="boundary_spanning_axes_v1",
                        GridSpecSHA256=grid_capability.spec_sha256,
                        StructuralGridSHA256=grid_capability.structural_grid_sha256)
    if include_groups:
        metadata.update(GroupCapability="declared_semantic_groups_v1",
                        GroupCapabilitySHA256=_hash(group_capability.to_dict()))
    _add_properties(document, owner, [project], "Pset_OrthogonalDraft", metadata)

    spaces, contained, occurrences = [], [], {}

    def product(ifc_class, kind, source_id, geometry, height, *, object_type=None, extra=None, base_elevation=0):
        attributes = {"GlobalId": guid(kind + ":" + source_id), "OwnerHistory": owner,
                      "Name": source_id, "ObjectPlacement": document.create_entity("IfcLocalPlacement",
                          PlacementRelTo=storey.ObjectPlacement, RelativePlacement=_axis_placement(document, (0, 0, base_elevation))),
                      "Representation": _representation(document, context, geometry, height)}
        if object_type:
            attributes["ObjectType"] = object_type
        element = document.create_entity(ifc_class, **attributes)
        _add_properties(document, owner, [element], "Pset_OrthogonalIdentity",
                        {"Kind": kind, "SourceId": source_id, **(extra or {})})
        occurrences[(kind, source_id)] = element
        return element

    for room in sorted(building.layout.rooms, key=lambda item: item.id):
        shape = layout.placements[room.id]
        geometry = box(shape.x, shape.y, shape.right, shape.top) if isinstance(shape, Rect) else shape.geometry
        element = product("IfcSpace", "room", room.id, geometry, 2800., extra={"RoomType": room.type})
        element.LongName = room.type
        spaces.append(element)
    wall = product("IfcWall", "wall", "wall-network", walls.geometry, 2800., object_type="OrthogonalWallNetwork")
    wall.PredefinedType = "NOTDEFINED"
    contained.append(wall)
    for opening in walls.openings:
        line = opening.centerline
        host_data = {"RoomA": opening.room_a, "RoomB": opening.room_b, "External": opening.external,
                     "WidthMM": opening.width, "Orientation": opening.orientation}
        void = product("IfcOpeningElement", "opening", opening.id,
                       line.buffer(building.layout.wall_thickness_mm / 2 + 1, cap_style=2, join_style=2),
                       2100., extra=host_data)
        void.PredefinedType = "OPENING"
        door = product("IfcDoor", "door", opening.id,
                       line.buffer(building.layout.wall_thickness_mm / 2, cap_style=2, join_style=2),
                       2100., extra=host_data)
        door.OverallWidth, door.OverallHeight = opening.width, 2100.
        door.PredefinedType = "DOOR"
        document.create_entity("IfcRelVoidsElement", GlobalId=guid("void:" + opening.id), OwnerHistory=owner,
            RelatingBuildingElement=wall, RelatedOpeningElement=void)
        document.create_entity("IfcRelFillsElement", GlobalId=guid("fill:" + opening.id), OwnerHistory=owner,
            RelatingOpeningElement=void, RelatedBuildingElement=door)
        contained.append(door)
    # The base wall remains the existing door-cut network. Native window
    # voids remove only the source sill-to-head range, retaining the wall
    # below and above each window; windows never become 2D route portals.
    for opening in walls.windows:
        sill, height = building.layout.window_sill_mm, building.layout.window_height_mm
        identity = {"HostRoomId": opening.room_id, "Orientation": opening.orientation,
                    "WidthMM": opening.width, "SillMM": sill, "HeightMM": height}
        void = product("IfcOpeningElement", "window-opening", opening.id,
            opening.centerline.buffer(building.layout.wall_thickness_mm / 2 + 1, cap_style=2, join_style=2),
            height, extra=identity, base_elevation=sill)
        void.PredefinedType = "OPENING"
        window = product("IfcWindow", "window", opening.id,
            opening.centerline.buffer(building.layout.wall_thickness_mm / 2, cap_style=2, join_style=2),
            height, extra=identity, base_elevation=sill)
        window.OverallWidth, window.OverallHeight, window.PredefinedType = opening.width, height, "WINDOW"
        document.create_entity("IfcRelVoidsElement", GlobalId=guid("void-window:" + opening.id), OwnerHistory=owner,
            RelatingBuildingElement=wall, RelatedOpeningElement=void)
        document.create_entity("IfcRelFillsElement", GlobalId=guid("fill-window:" + opening.id), OwnerHistory=owner,
            RelatingOpeningElement=void, RelatedBuildingElement=window)
        contained.append(window)
    if equipment is not None:
        specs = {item.id: item for item in building.equipment}
        for item in sorted(equipment.placements, key=lambda item: item.equipment_id):
            physical, service = item.rect, clearance_rect(specs[item.equipment_id], item.rect, item.rotated)
            identity = {"HostRoomId": item.room_id, "Rotated": item.rotated}
            for kind, rect, height, object_type in (("equipment", physical, 1000., "OrthogonalEquipment"),
                ("service", service, 20., "OrthogonalEquipmentService")):
                contained.append(product("IfcBuildingElementProxy", kind, item.equipment_id,
                    box(rect.x, rect.y, rect.right, rect.top), height, object_type=object_type, extra=identity))
    if routing is not None:
        for route in sorted(routing.routes, key=_route_id):
            identifier = _route_id(route)
            corridor = LineString(route.points).buffer(route.minimum_clear_width_mm / 2, cap_style=3, join_style=2)
            element = product("IfcBuildingElementProxy", "route", identifier, corridor, 20.,
                object_type="OrthogonalFlowCorridor", extra={"FlowId": route.flow_id, "FromId": route.from_id,
                    "ToId": route.to_id, "FromRoomId": route.from_room_id, "ToRoomId": route.to_room_id,
                    "RoomPath": json.dumps(list(route.room_path), separators=(",", ":")),
                    "MinimumClearWidthMM": route.minimum_clear_width_mm})
            axis = document.create_entity("IfcPolyline", Points=[_point(document, (x, y, 0)) for x, y in route.points])
            representation = document.create_entity("IfcShapeRepresentation", ContextOfItems=context,
                RepresentationIdentifier="Axis", RepresentationType="Curve3D", Items=[axis])
            element.Representation.Representations = (*element.Representation.Representations, representation)
            contained.append(element)
    if include_grid and grid_capability.axes:
        native_axes = []
        for axis in grid_capability.axes:
            curve = document.create_entity("IfcPolyline", Points=[_point(document, point)
                                            for point in (axis.start, axis.end)])
            native_axes.append((axis.direction, document.create_entity("IfcGridAxis",
                AxisTag=axis.label, AxisCurve=curve, SameSense=True)))
        curves = document.create_entity("IfcGeometricCurveSet",
                                       Elements=[axis.AxisCurve for _, axis in native_axes])
        footprint = document.create_entity("IfcShapeRepresentation", ContextOfItems=context,
            RepresentationIdentifier="FootPrint", RepresentationType="GeometricCurveSet", Items=[curves])
        grid = document.create_entity("IfcGrid", GlobalId=guid("grid:structural-grid"), OwnerHistory=owner,
            Name="structural-grid", PredefinedType="RECTANGULAR",
            ObjectPlacement=document.create_entity("IfcLocalPlacement", PlacementRelTo=storey.ObjectPlacement,
                                                    RelativePlacement=_axis_placement(document, (0, 0, 0))),
            Representation=document.create_entity("IfcProductDefinitionShape", Representations=[footprint]),
            UAxes=[axis for direction, axis in native_axes if direction == "x"],
            VAxes=[axis for direction, axis in native_axes if direction == "y"])
        _add_properties(document, owner, [grid], "Pset_OrthogonalIdentity",
                        {"Kind": "grid", "SourceId": "structural-grid", "Policy": "boundary_spanning_axes_v1"})
        contained.append(grid)
    group_relations = 0
    if include_groups:
        for group in group_capability.groups:
            element = document.create_entity("IfcZone" if group.kind == "zone" else "IfcSystem",
                GlobalId=guid(group.kind + ":" + group.id), OwnerHistory=owner,
                Name=group.id, ObjectType=group.declaration["type"],
                Description="Declared functional zone; not a qualified physical container" if group.kind == "zone"
                            else "Declared process movement; not a designed MEP network")
            _add_properties(document, owner, [element], "Pset_OrthogonalIdentity",
                {"Kind": group.kind, "SourceId": group.id, "Policy": "declared_semantic_groups_v1"})
            _group_properties(document, owner, element, _group_declaration(building, group), guid)
            occurrences[(group.kind, group.id)] = element
        for group in group_capability.groups:
            if not group.members:
                continue
            document.create_entity("IfcRelAssignsToGroup",
                GlobalId=guid("group-membership:" + group.kind + ":" + group.id), OwnerHistory=owner,
                RelatingGroup=occurrences[(group.kind, group.id)],
                RelatedObjects=[occurrences[(member.kind, member.id)] for member in group.members])
            group_relations += 1
    document.create_entity("IfcRelAggregates", GlobalId=guid("aggregate-spaces"), OwnerHistory=owner,
        RelatingObject=storey, RelatedObjects=spaces)
    document.create_entity("IfcRelContainedInSpatialStructure", GlobalId=guid("containment"), OwnerHistory=owner,
        RelatingStructure=storey, RelatedElements=contained)
    document.write(str(path))
    evidence = {"schema": "orthogonal-ifc-draft/0.1", "status": "DRAFT", "ok": False, "units": "mm",
            "source": source, "variant": layout.variant,
            "artifact_sha256": hashlib.sha256(Path(path).read_bytes()).hexdigest(),
            "full_facility_validation": {"status": "UNKNOWN", "ok": False}, "engineering_acceptance": "PENDING",
            "scope": "Exact IFC coordination geometry and local supplied predicates; not full facility or engineering acceptance",
            "local_validation": reports,
            "counts": {"rooms": len(spaces), "walls": 1, "doors": len(walls.openings), "openings": len(walls.openings),
                       "equipment": len(equipment.placements) if equipment is not None else 0,
                       "services": len(equipment.placements) if equipment is not None else 0,
                       "routes": len(routing.routes) if routing is not None else 0}}
    if include_windows:
        evidence["window_capability"] = reports["window_capability"]
        evidence["counts"].update(windows=len(walls.windows), window_openings=len(walls.windows),
                                  openings=len(walls.openings) + len(walls.windows))
    if include_grid:
        evidence["grid_capability"] = reports["grid_capability"]
        evidence["counts"].update(grids=int(bool(grid_capability.axes)), grid_axes=len(grid_capability.axes))
    if include_groups:
        evidence["group_capability"] = reports["group_capability"]
        evidence["counts"].update(zones=sum(group.kind == "zone" for group in group_capability.groups),
            process_systems=sum(group.kind == "flow-system" for group in group_capability.groups),
            group_relations=group_relations)
    return evidence


def _inputs(building, layout, equipment, routing, *, include_windows=False, include_grid=False, include_groups=False):
    if not isinstance(building, BuildingIR) or not isinstance(layout, LayoutResult):
        raise ValueError("Orthogonal IFC requires canonical BuildingIR and LayoutResult")
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
        # Guard exact supplied source types and numeric bounds before any
        # constructor can coerce them. Preserve caller provenance in this
        # explicit mode, including valid integer-valued source dimensions.
        capability = validate_orthogonal_window_capability(building.layout, layout)
        if not capability.ok:
            raise OrthogonalWindowCapabilityError(capability)
    canonical_building = BuildingIR.from_mapping(building.to_dict())
    canonical_layout = parse_layout_result(layout.to_dict())
    if not include_windows and not include_grid and not include_groups:
        building, layout = canonical_building, canonical_layout
    if len(building.layout.rooms) > 64 or len(building.equipment) > 64:
        raise ValueError("Orthogonal IFC source limit is 64 rooms and 64 equipment items")
    if routing is not None and (not isinstance(routing, FlowRoutingResult) or len(routing.routes) > 256):
        raise ValueError("Orthogonal IFC routing requires at most 256 canonical routes")
    room_report = validate_orthogonal_layout(building.layout, layout, **options)
    if not room_report.ok:
        raise ValueError("Orthogonal IFC room/opening local validation must PASS: " + json.dumps(room_report.to_dict()))
    reports = {"rooms": room_report.to_dict(), "equipment": {"status": "NOT_SUPPLIED"},
               "door_approaches": {"status": "NOT_SUPPLIED"}, "routes": {"status": "NOT_SUPPLIED"}}
    if capability is not None:
        reports["window_capability"] = capability.to_dict()
    if grid_capability is not None:
        reports["grid_capability"] = grid_capability.to_dict()
    if group_capability is not None:
        reports["group_capability"] = group_capability.to_dict()
    if equipment is not None:
        if not isinstance(equipment, EquipmentLayoutResult):
            raise ValueError("Orthogonal IFC equipment requires EquipmentLayoutResult")
        equipment_report = validate_orthogonal_equipment(building, layout, equipment, **options)
        door_report = validate_orthogonal_door_approaches(building, layout, equipment, **options)
        if not equipment_report.ok or not door_report.ok:
            raise ValueError("Orthogonal IFC equipment/service/door local validation must PASS")
        reports.update(equipment=equipment_report.to_dict(), door_approaches=door_report.to_dict())
    if routing is not None:
        flow_report = validate_orthogonal_flow_routes(building, layout, routing, equipment, **options)
        if not flow_report.ok:
            raise ValueError("Orthogonal IFC supplied-route local validation must PASS: " + json.dumps(flow_report.to_dict()))
        reports["routes"] = flow_report.to_dict()
    _hash(building.to_dict())
    _hash(layout.to_dict())
    return building, layout, reports


def _group_declaration(building, group):
    declaration = group.declaration
    values = {"DeclarationJSON": json.dumps(declaration, sort_keys=True, ensure_ascii=False,
                                            allow_nan=False, separators=(",", ":")),
              "DeclarationState": "DECLARED", "SourceContract": "BuildingIR " + building.version,
              "SourceEntityType": "ZoneSpec" if group.kind == "zone" else "FlowSpec"}
    if group.kind == "zone":
        values.update(ZoneId=group.id, ZoneType=declaration["type"],
            DirectRoomIdsJSON=json.dumps(declaration["room_ids"], ensure_ascii=False, separators=(",", ":")),
            Airlock=declaration.get("airlock", False), PhysicalContainerValidation="UNKNOWN")
        for source, target in (("parent_zone_id", "ParentZoneId"), ("cleanroom_class", "CleanroomClass"),
                               ("pressure_pa", "PressurePa")):
            if source in declaration:
                values[target] = declaration[source]
    else:
        values.update(FlowId=group.id, FlowType=declaration["type"],
            FromIdsJSON=json.dumps(declaration["from_ids"], ensure_ascii=False, separators=(",", ":")),
            ToIdsJSON=json.dumps(declaration["to_ids"], ensure_ascii=False, separators=(",", ":")),
            Required=declaration["required"], MinimumClearWidthMM=declaration["minimum_clear_width_mm"],
            NetworkEngineering="UNKNOWN", RepresentationKind="Declared process movement group; not an MEP network")
        if "stage" in declaration:
            values["Stage"] = declaration["stage"]
    return values


def _group_properties(document, owner, element, values, guid):
    """Keep structured source lists in native text, rather than ambiguous CSV."""
    properties = []
    for name, value in values.items():
        if name.endswith("JSON"):
            nominal = document.create_entity("IfcText", value)
        elif isinstance(value, bool):
            nominal = document.create_entity("IfcBoolean", value)
        elif isinstance(value, (int, float)):
            nominal = document.create_entity("IfcReal", float(value))
        else:
            nominal = document.create_entity("IfcLabel", value)
        properties.append(document.create_entity("IfcPropertySingleValue", Name=name, NominalValue=nominal))
    definition = document.create_entity("IfcPropertySet", GlobalId=guid("group-properties:" + element.GlobalId),
        OwnerHistory=owner, Name="Pset_OrthogonalGroup", HasProperties=properties)
    document.create_entity("IfcRelDefinesByProperties", GlobalId=guid("group-defines:" + element.GlobalId),
        OwnerHistory=owner, RelatedObjects=[element], RelatingPropertyDefinition=definition)


def _representation(document, context, geometry, height):
    polygons = [geometry] if isinstance(geometry, Polygon) else list(geometry.geoms)
    if not polygons or len(polygons) > 4096 or any(not isinstance(item, Polygon) or item.is_empty or not item.is_valid for item in polygons):
        raise ValueError("Orthogonal IFC requires nonempty valid polygon material")
    solids = []
    for polygon in sorted(polygons, key=lambda item: (item.bounds, item.area)):
        def curve(ring):
            points = tuple(ring.coords)
            if not 4 <= len(points) <= 8192:
                raise ValueError("Orthogonal IFC ring point limit is 8192")
            return document.create_entity("IfcPolyline", Points=[_point(document, point) for point in points])
        attributes = {"ProfileType": "AREA", "OuterCurve": curve(polygon.exterior)}
        if polygon.interiors:
            attributes["InnerCurves"] = [curve(ring) for ring in polygon.interiors]
        profile = document.create_entity("IfcArbitraryProfileDefWithVoids" if polygon.interiors else "IfcArbitraryClosedProfileDef", **attributes)
        solids.append(document.create_entity("IfcExtrudedAreaSolid", SweptArea=profile,
            Position=_axis_placement(document, (0, 0, 0)), ExtrudedDirection=_direction(document, (0, 0, 1)), Depth=height))
    representation = document.create_entity("IfcShapeRepresentation", ContextOfItems=context,
        RepresentationIdentifier="Body", RepresentationType="SweptSolid", Items=solids)
    return document.create_entity("IfcProductDefinitionShape", Representations=[representation])


def _route_id(route):
    return json.dumps([route.flow_id, route.from_id, route.to_id, route.from_room_id, route.to_room_id],
                      ensure_ascii=False, separators=(",", ":"))


def _hash(payload):
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, allow_nan=False,
        sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()

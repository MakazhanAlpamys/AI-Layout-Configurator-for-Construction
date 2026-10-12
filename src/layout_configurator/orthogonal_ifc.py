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
from .orthogonal_projection import build_orthogonal_wall_plan
from .orthogonal_validation import validate_orthogonal_equipment, validate_orthogonal_layout
from .result_geometry import parse_layout_result


def export_orthogonal_draft_ifc(path: str | Path, building: BuildingIR, layout: LayoutResult,
                                equipment: EquipmentLayoutResult | None = None, *,
                                routing: FlowRoutingResult | None = None) -> dict:
    """Export source-backed rooms, actual walls/openings and optional geometry.

    Supplied equipment must pass independent directional service and complete
    local door-approach checks. Supplied routes must pass B7's independently
    rebuilt full-width corridor checks. Omitted equipment/routes are explicitly
    NOT_SUPPLIED, never a claim of complete facility validation.
    All input and capability checks precede opening the destination file.
    """
    building, layout, reports = _inputs(building, layout, equipment, routing)
    walls = build_orthogonal_wall_plan(building.layout, layout)
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
    _add_properties(document, owner, [project], "Pset_OrthogonalDraft", {
        "Schema": "orthogonal-ifc-draft/0.1", "Status": "DRAFT", "FullFacilityValidation": "UNKNOWN",
        "EngineeringAcceptance": "PENDING", "Units": "mm", "Variant": layout.variant,
        "BuildingSHA256": source["building_sha256"], "LayoutSHA256": source["layout_sha256"],
        "EquipmentSHA256": source["equipment_sha256"], "RoutingSHA256": source["routing_sha256"],
        "EquipmentState": "SUPPLIED" if equipment is not None else "NOT_SUPPLIED",
        "RoutingState": "SUPPLIED" if routing is not None else "NOT_SUPPLIED",
        "HeightPolicy": "coordination_proxy_v1; rooms/walls=2800; doors/openings=2100; equipment=1000; service/routes=20 mm"})

    spaces, contained = [], []

    def product(ifc_class, kind, source_id, geometry, height, *, object_type=None, extra=None):
        attributes = {"GlobalId": guid(kind + ":" + source_id), "OwnerHistory": owner,
                      "Name": source_id, "ObjectPlacement": document.create_entity("IfcLocalPlacement",
                          PlacementRelTo=storey.ObjectPlacement, RelativePlacement=_axis_placement(document, (0, 0, 0))),
                      "Representation": _representation(document, context, geometry, height)}
        if object_type:
            attributes["ObjectType"] = object_type
        element = document.create_entity(ifc_class, **attributes)
        _add_properties(document, owner, [element], "Pset_OrthogonalIdentity",
                        {"Kind": kind, "SourceId": source_id, **(extra or {})})
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
    document.create_entity("IfcRelAggregates", GlobalId=guid("aggregate-spaces"), OwnerHistory=owner,
        RelatingObject=storey, RelatedObjects=spaces)
    document.create_entity("IfcRelContainedInSpatialStructure", GlobalId=guid("containment"), OwnerHistory=owner,
        RelatingStructure=storey, RelatedElements=contained)
    document.write(str(path))
    return {"schema": "orthogonal-ifc-draft/0.1", "status": "DRAFT", "ok": False, "units": "mm",
            "source": source, "variant": layout.variant,
            "artifact_sha256": hashlib.sha256(Path(path).read_bytes()).hexdigest(),
            "full_facility_validation": {"status": "UNKNOWN", "ok": False}, "engineering_acceptance": "PENDING",
            "scope": "Exact IFC coordination geometry and local supplied predicates; not full facility or engineering acceptance",
            "local_validation": reports,
            "counts": {"rooms": len(spaces), "walls": 1, "doors": len(walls.openings), "openings": len(walls.openings),
                       "equipment": len(equipment.placements) if equipment is not None else 0,
                       "services": len(equipment.placements) if equipment is not None else 0,
                       "routes": len(routing.routes) if routing is not None else 0}}


def _inputs(building, layout, equipment, routing):
    if not isinstance(building, BuildingIR) or not isinstance(layout, LayoutResult):
        raise ValueError("Orthogonal IFC requires canonical BuildingIR and LayoutResult")
    building = BuildingIR.from_mapping(building.to_dict())
    layout = parse_layout_result(layout.to_dict())
    if len(building.layout.rooms) > 64 or len(building.equipment) > 64:
        raise ValueError("Orthogonal IFC source limit is 64 rooms and 64 equipment items")
    if routing is not None and (not isinstance(routing, FlowRoutingResult) or len(routing.routes) > 256):
        raise ValueError("Orthogonal IFC routing requires at most 256 canonical routes")
    room_report = validate_orthogonal_layout(building.layout, layout)
    if not room_report.ok:
        raise ValueError("Orthogonal IFC room/opening local validation must PASS: " + json.dumps(room_report.to_dict()))
    reports = {"rooms": room_report.to_dict(), "equipment": {"status": "NOT_SUPPLIED"},
               "door_approaches": {"status": "NOT_SUPPLIED"}, "routes": {"status": "NOT_SUPPLIED"}}
    if equipment is not None:
        if not isinstance(equipment, EquipmentLayoutResult):
            raise ValueError("Orthogonal IFC equipment requires EquipmentLayoutResult")
        equipment_report = validate_orthogonal_equipment(building, layout, equipment)
        door_report = validate_orthogonal_door_approaches(building, layout, equipment)
        if not equipment_report.ok or not door_report.ok:
            raise ValueError("Orthogonal IFC equipment/service/door local validation must PASS")
        reports.update(equipment=equipment_report.to_dict(), door_approaches=door_report.to_dict())
    if routing is not None:
        flow_report = validate_orthogonal_flow_routes(building, layout, routing, equipment)
        if not flow_report.ok:
            raise ValueError("Orthogonal IFC supplied-route local validation must PASS: " + json.dumps(flow_report.to_dict()))
        reports["routes"] = flow_report.to_dict()
    _hash(building.to_dict())
    _hash(layout.to_dict())
    return building, layout, reports


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

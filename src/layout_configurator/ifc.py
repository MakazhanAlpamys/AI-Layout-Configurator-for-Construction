"""Minimal IFC4 export from the canonical LayoutIR.

This is an exchange model for coordination and inspection, not a code
compliance verdict.  IFC is kept as a derived projection of LayoutIR, just
like DXF and PDF.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

from .models import LayoutIR, LayoutResult, Rect
from .walls import WallPlan, build_wall_plan


IFC_HEIGHT_MM = 2_800
DOOR_HEIGHT_MM = 2_100


@dataclass(frozen=True)
class IfcExportSummary:
    path: Path
    spaces: int
    walls: int
    doors: int
    windows: int


def export_ifc(path: str | Path, spec: LayoutIR, result: LayoutResult) -> IfcExportSummary:
    """Write a small, readable IFC4 model and return entity counts."""

    import ifcopenshell

    document = ifcopenshell.file(schema="IFC4")
    owner_history = _owner_history(document)
    context = _model_context(document)
    units = _units(document)
    origin = _axis_placement(document, (0, 0, 0))

    project = document.create_entity(
        "IfcProject",
        GlobalId=_guid(spec, "project"),
        OwnerHistory=owner_history,
        Name=spec.project_name,
        RepresentationContexts=[context],
        UnitsInContext=units,
    )
    site = document.create_entity(
        "IfcSite",
        GlobalId=_guid(spec, "site"),
        OwnerHistory=owner_history,
        Name=f"{spec.project_name} site",
        CompositionType="ELEMENT",
        ObjectPlacement=document.create_entity("IfcLocalPlacement", RelativePlacement=origin),
    )
    building = document.create_entity(
        "IfcBuilding",
        GlobalId=_guid(spec, "building"),
        OwnerHistory=owner_history,
        Name=spec.project_name,
        CompositionType="ELEMENT",
        ObjectPlacement=document.create_entity("IfcLocalPlacement", RelativePlacement=origin),
    )
    storey_placement = document.create_entity("IfcLocalPlacement", RelativePlacement=origin)
    storey = document.create_entity(
        "IfcBuildingStorey",
        GlobalId=_guid(spec, "storey-0"),
        OwnerHistory=owner_history,
        Name="Ground floor",
        CompositionType="ELEMENT",
        ObjectPlacement=storey_placement,
    )
    document.create_entity(
        "IfcRelAggregates",
        GlobalId=_guid(spec, "aggregate-project"),
        OwnerHistory=owner_history,
        RelatingObject=project,
        RelatedObjects=[site],
    )
    document.create_entity(
        "IfcRelAggregates",
        GlobalId=_guid(spec, "aggregate-site"),
        OwnerHistory=owner_history,
        RelatingObject=site,
        RelatedObjects=[building],
    )
    document.create_entity(
        "IfcRelAggregates",
        GlobalId=_guid(spec, "aggregate-building"),
        OwnerHistory=owner_history,
        RelatingObject=building,
        RelatedObjects=[storey],
    )

    elements = []
    spaces = []
    for room in spec.rooms:
        rect = result.placements[room.id]
        space = document.create_entity(
            "IfcSpace",
            GlobalId=_guid(spec, f"space-{room.id}"),
            OwnerHistory=owner_history,
            Name=room.id,
            LongName=room.type,
            ObjectPlacement=_product_placement(document, storey_placement, rect.x, rect.y),
            Representation=_box_representation(document, context, rect.width, rect.height, IFC_HEIGHT_MM),
        )
        _add_space_quantities(document, owner_history, space, rect)
        _add_properties(
            document,
            owner_history,
            [space],
            "Pset_LayoutRoom",
            {
                "RoomType": room.type,
                "TargetAreaM2": room.target_area_m2,
                "NeedsDaylight": room.needs_daylight,
            },
        )
        spaces.append(space)
        elements.append(space)

    wall_plan = build_wall_plan(spec, result)
    walls = _make_walls(document, owner_history, context, storey_placement, spec, result, wall_plan)
    doors = _make_doors(document, owner_history, context, storey_placement, wall_plan)
    windows = _make_windows(document, owner_history, context, storey_placement, spec, wall_plan)
    elements.extend(walls)
    elements.extend(doors)
    elements.extend(windows)
    _associate_material(document, owner_history, walls, "Generic partition wall")
    _associate_material(document, owner_history, doors, "Generic internal door")
    _associate_material(document, owner_history, windows, "Generic window")
    document.create_entity(
        "IfcRelContainedInSpatialStructure",
        GlobalId=_guid(spec, "containment-storey"),
        OwnerHistory=owner_history,
        RelatedElements=elements,
        RelatingStructure=storey,
    )
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    document.write(str(output))
    return IfcExportSummary(path=output, spaces=len(spaces), walls=len(walls), doors=len(doors), windows=len(windows))


def _make_walls(document, owner_history, context, storey_placement, spec, result, wall_plan: WallPlan):
    segments = _unique_wall_segments(spec, result)
    walls = []
    for index, (orientation, fixed, start, end) in enumerate(segments):
        fragments = _subtract_openings(orientation, fixed, start, end, wall_plan)
        for fragment_index, (fragment_start, fragment_end) in enumerate(fragments):
            length = fragment_end - fragment_start
            if length <= 0:
                continue
            if orientation == "horizontal":
                width, depth = length, spec.wall_thickness_mm
                x, y = fragment_start, fixed - depth / 2
            else:
                width, depth = spec.wall_thickness_mm, length
                x, y = fixed - width / 2, fragment_start
            wall = document.create_entity(
                "IfcWall",
                GlobalId=_guid(spec, f"wall-{index}-{fragment_index}"),
                OwnerHistory=owner_history,
                Name=f"Wall {index + 1}.{fragment_index + 1}",
                ObjectPlacement=_product_placement(document, storey_placement, x, y),
                Representation=_box_representation(document, context, width, depth, IFC_HEIGHT_MM),
                PredefinedType="PARTITIONING",
            )
            _add_properties(
                document,
                owner_history,
                [wall],
                "Pset_LayoutWall",
                {"ThicknessMm": spec.wall_thickness_mm, "HeightMm": IFC_HEIGHT_MM},
            )
            walls.append(wall)
    return walls


def _make_doors(document, owner_history, context, storey_placement, wall_plan: WallPlan):
    doors = []
    for index, opening in enumerate(wall_plan.openings):
        x, y = opening.center
        if opening.orientation == "horizontal":
            width, depth = opening.width, 50
            x, y = opening.start, opening.fixed - depth / 2
        else:
            width, depth = 50, opening.width
            x, y = opening.fixed - width / 2, opening.start
        door = document.create_entity(
            "IfcDoor",
            GlobalId=_guid_value(f"door-{index}-{opening.room_a}-{opening.room_b}"),
            OwnerHistory=owner_history,
            Name=f"Door {opening.room_a}–{opening.room_b}",
            ObjectPlacement=_product_placement(document, storey_placement, x, y),
            Representation=_box_representation(document, context, width, depth, DOOR_HEIGHT_MM),
            OverallHeight=DOOR_HEIGHT_MM,
            OverallWidth=opening.width,
            PredefinedType="DOOR",
        )
        _add_properties(
            document,
            owner_history,
            [door],
            "Pset_LayoutDoor",
            {"RoomA": opening.room_a, "RoomB": opening.room_b, "ClearWidthMm": opening.width},
        )
        doors.append(door)
    return doors


def _make_windows(document, owner_history, context, storey_placement, spec, wall_plan: WallPlan):
    windows = []
    for index, opening in enumerate(wall_plan.windows):
        if opening.orientation == "horizontal":
            width, depth = opening.width, 50
            x, y = opening.start, opening.fixed - depth / 2
        else:
            width, depth = 50, opening.width
            x, y = opening.fixed - width / 2, opening.start
        window = document.create_entity(
            "IfcWindow",
            GlobalId=_guid_value(f"window-{index}-{opening.room_id}"),
            OwnerHistory=owner_history,
            Name=f"Window {opening.room_id}",
            ObjectPlacement=_product_placement(document, storey_placement, x, y),
            Representation=_box_representation(document, context, width, depth, spec.window_height_mm),
            OverallHeight=spec.window_height_mm,
            OverallWidth=opening.width,
            PredefinedType="WINDOW",
        )
        _add_properties(
            document,
            owner_history,
            [window],
            "Pset_LayoutWindow",
            {"RoomId": opening.room_id, "ClearWidthMm": opening.width, "HeightMm": spec.window_height_mm, "SillMm": spec.window_sill_mm},
        )
        windows.append(window)
    return windows


def _add_space_quantities(document, owner_history, space, rect: Rect) -> None:
    quantity = document.create_entity(
        "IfcQuantityArea",
        Name="NetFloorArea",
        Description="Room clear area in project units",
        AreaValue=rect.area_mm2,
    )
    quantities = document.create_entity(
        "IfcElementQuantity",
        GlobalId=_guid_value(f"quantity-{space.GlobalId}"),
        OwnerHistory=owner_history,
        Name="Qto_SpaceBaseQuantities",
        Quantities=[quantity],
    )
    document.create_entity(
        "IfcRelDefinesByProperties",
        GlobalId=_guid_value(f"defines-{space.GlobalId}"),
        OwnerHistory=owner_history,
        RelatedObjects=[space],
        RelatingPropertyDefinition=quantities,
    )


def _add_properties(document, owner_history, objects, name: str, values: dict[str, object]) -> None:
    properties = []
    for property_name, value in values.items():
        properties.append(
            document.create_entity(
                "IfcPropertySingleValue",
                Name=property_name,
                NominalValue=_typed_value(document, value),
            )
        )
    property_set = document.create_entity(
        "IfcPropertySet",
        GlobalId=_guid_value(f"{name}:{','.join(obj.GlobalId for obj in objects)}"),
        OwnerHistory=owner_history,
        Name=name,
        HasProperties=properties,
    )
    document.create_entity(
        "IfcRelDefinesByProperties",
        GlobalId=_guid_value(f"defines:{name}:{','.join(obj.GlobalId for obj in objects)}"),
        OwnerHistory=owner_history,
        RelatedObjects=objects,
        RelatingPropertyDefinition=property_set,
    )


def _typed_value(document, value):
    if isinstance(value, bool):
        return document.create_entity("IfcBoolean", value)
    if isinstance(value, (int, float)):
        return document.create_entity("IfcReal", float(value))
    return document.create_entity("IfcLabel", str(value))


def _associate_material(document, owner_history, objects, name: str) -> None:
    if not objects:
        return
    material = document.create_entity("IfcMaterial", Name=name)
    document.create_entity(
        "IfcRelAssociatesMaterial",
        GlobalId=_guid_value(f"material:{name}"),
        OwnerHistory=owner_history,
        RelatedObjects=objects,
        RelatingMaterial=material,
    )


def _box_representation(document, context, width: float, depth: float, height: float):
    points = [
        _point(document, (0, 0, 0)),
        _point(document, (width, 0, 0)),
        _point(document, (width, depth, 0)),
        _point(document, (0, depth, 0)),
        _point(document, (0, 0, 0)),
    ]
    profile = document.create_entity(
        "IfcArbitraryClosedProfileDef",
        ProfileType="AREA",
        OuterCurve=document.create_entity("IfcPolyline", Points=points),
    )
    solid = document.create_entity(
        "IfcExtrudedAreaSolid",
        SweptArea=profile,
        Position=_axis_placement(document, (0, 0, 0)),
        ExtrudedDirection=_direction(document, (0, 0, 1)),
        Depth=height,
    )
    return document.create_entity(
        "IfcProductDefinitionShape",
        Representations=[
            document.create_entity(
                "IfcShapeRepresentation",
                ContextOfItems=context,
                RepresentationIdentifier="Body",
                RepresentationType="SweptSolid",
                Items=[solid],
            )
        ],
    )


def _product_placement(document, storey_placement, x: float, y: float):
    return document.create_entity(
        "IfcLocalPlacement",
        PlacementRelTo=storey_placement,
        RelativePlacement=_axis_placement(document, (x, y, 0)),
    )


def _model_context(document):
    return document.create_entity(
        "IfcGeometricRepresentationContext",
        ContextIdentifier="Model",
        ContextType="Model",
        CoordinateSpaceDimension=3,
        Precision=1e-5,
        WorldCoordinateSystem=_axis_placement(document, (0, 0, 0)),
    )


def _units(document):
    length = document.create_entity("IfcSIUnit", UnitType="LENGTHUNIT", Prefix="MILLI", Name="METRE")
    area = document.create_entity("IfcSIUnit", UnitType="AREAUNIT", Prefix="MILLI", Name="SQUARE_METRE")
    return document.create_entity("IfcUnitAssignment", Units=[length, area])


def _owner_history(document):
    organization = document.create_entity("IfcOrganization", Identification="LC", Name="Layout Configurator")
    person = document.create_entity("IfcPerson", Identification="layout-configurator", FamilyName="Generator")
    user = document.create_entity("IfcPersonAndOrganization", ThePerson=person, TheOrganization=organization)
    application = document.create_entity(
        "IfcApplication",
        ApplicationDeveloper=organization,
        Version="0.1.0",
        ApplicationFullName="AI Layout Configurator",
        ApplicationIdentifier="LAYOUT-CONFIGURATOR",
    )
    return document.create_entity(
        "IfcOwnerHistory",
        OwningUser=user,
        OwningApplication=application,
        ChangeAction="ADDED",
        CreationDate=0,
    )


def _axis_placement(document, location):
    return document.create_entity(
        "IfcAxis2Placement3D",
        Location=_point(document, location),
        Axis=_direction(document, (0, 0, 1)),
        RefDirection=_direction(document, (1, 0, 0)),
    )


def _point(document, coordinates):
    return document.create_entity("IfcCartesianPoint", Coordinates=tuple(float(value) for value in coordinates))


def _direction(document, ratios):
    return document.create_entity("IfcDirection", DirectionRatios=tuple(float(value) for value in ratios))


def _unique_wall_segments(spec: LayoutIR, result: LayoutResult):
    segments = {}
    for room in spec.rooms:
        rect = result.placements[room.id]
        candidates = (
            ("horizontal", rect.y, rect.x, rect.right),
            ("vertical", rect.right, rect.y, rect.top),
            ("horizontal", rect.top, rect.x, rect.right),
            ("vertical", rect.x, rect.y, rect.top),
        )
        for orientation, fixed, start, end in candidates:
            key = (orientation, round(fixed, 6), round(start, 6), round(end, 6))
            segments[key] = (orientation, fixed, start, end)
    return list(segments.values())


def _subtract_openings(orientation, fixed, start, end, wall_plan: WallPlan):
    intervals = []
    for opening in (*wall_plan.openings, *wall_plan.windows):
        if opening.orientation != orientation or abs(opening.fixed - fixed) > 1e-6:
            continue
        overlap_start = max(start, opening.start)
        overlap_end = min(end, opening.end)
        if overlap_end > overlap_start:
            intervals.append((overlap_start, overlap_end))
    if not intervals:
        return [(start, end)]
    intervals.sort()
    fragments = []
    cursor = start
    for opening_start, opening_end in intervals:
        if opening_start > cursor:
            fragments.append((cursor, opening_start))
        cursor = max(cursor, opening_end)
    if cursor < end:
        fragments.append((cursor, end))
    return fragments


def _guid(spec: LayoutIR, suffix: str) -> str:
    return _guid_value(f"{spec.project_name}:{spec.version}:{suffix}")


def _guid_value(value: str) -> str:
    import ifcopenshell

    return ifcopenshell.guid.compress(str(uuid5(NAMESPACE_URL, value)))

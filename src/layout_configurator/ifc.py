"""Minimal IFC4 export from the canonical LayoutIR.

This is an exchange model for coordination and inspection, not a code
compliance verdict.  IFC is kept as a derived projection of LayoutIR, just
like DXF and PDF.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

from .models import LayoutIR, LayoutResult, Rect
from .multifloor import MultiFloorResult, MultiFloorSpec
from .walls import DoorOpening, WallPlan, WindowOpening, build_wall_plan


IFC_HEIGHT_MM = 2_800
DOOR_HEIGHT_MM = 2_100


@dataclass(frozen=True)
class IfcExportSummary:
    path: Path
    spaces: int
    walls: int
    doors: int
    windows: int
    openings: int = 0
    space_boundaries: int = 0
    voids: int = 0
    fills: int = 0
    wall_types: int = 0
    door_types: int = 0
    window_types: int = 0
    external_entries: int = 0
    storeys: int = 1
    stairs: int = 0


@dataclass(frozen=True)
class _WallRecord:
    element: object
    orientation: str
    fixed: float
    start: float
    end: float


@dataclass(frozen=True)
class _OpeningRecord:
    source: DoorOpening | WindowOpening
    element: object
    filler: object
    kind: str


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
    spaces_by_id = {}
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
                "IsHeated": room.is_heated,
            },
        )
        spaces.append(space)
        spaces_by_id[room.id] = space
        elements.append(space)

    wall_plan = build_wall_plan(spec, result)
    walls, wall_records = _make_walls(document, owner_history, context, storey_placement, spec, result, wall_plan)
    doors = _make_doors(document, owner_history, context, storey_placement, wall_plan)
    windows = _make_windows(document, owner_history, context, storey_placement, spec, wall_plan)
    opening_records = _make_openings(document, owner_history, context, storey_placement, spec, wall_plan, doors, windows)
    type_records = _make_element_types(document, owner_history, spec, walls, doors, windows)
    _add_bim_relationships(document, owner_history, spec, result, spaces_by_id, wall_records, opening_records)
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
    return IfcExportSummary(
        path=output,
        spaces=len(spaces),
        walls=len(walls),
        doors=len(doors),
        windows=len(windows),
        openings=len(opening_records),
        space_boundaries=len(document.by_type("IfcRelSpaceBoundary")),
        voids=len(document.by_type("IfcRelVoidsElement")),
        fills=len(document.by_type("IfcRelFillsElement")),
        wall_types=len(type_records[0]),
        door_types=len(type_records[1]),
        window_types=len(type_records[2]),
        external_entries=sum(1 for opening in wall_plan.openings if opening.external),
    )


def export_multifloor_ifc(path: str | Path, multi_spec: MultiFloorSpec, result: MultiFloorResult) -> IfcExportSummary:
    """Write one IFC4 model containing all coordinated floors and stair cores."""

    import ifcopenshell

    if result.spec != multi_spec or len(result.floors) != len(multi_spec.floors):
        raise ValueError("Multi-floor IFC result does not match its specification")
    document = ifcopenshell.file(schema="IFC4")
    owner_history = _owner_history(document)
    context = _model_context(document)
    units = _units(document)
    origin = _axis_placement(document, (0, 0, 0))
    project = document.create_entity(
        "IfcProject",
        GlobalId=_guid_value(f"{multi_spec.project_name}:multi-floor:project"),
        OwnerHistory=owner_history,
        Name=multi_spec.project_name,
        RepresentationContexts=[context],
        UnitsInContext=units,
    )
    site = document.create_entity(
        "IfcSite",
        GlobalId=_guid_value(f"{multi_spec.project_name}:multi-floor:site"),
        OwnerHistory=owner_history,
        Name=f"{multi_spec.project_name} site",
        CompositionType="ELEMENT",
        ObjectPlacement=document.create_entity("IfcLocalPlacement", RelativePlacement=origin),
    )
    building = document.create_entity(
        "IfcBuilding",
        GlobalId=_guid_value(f"{multi_spec.project_name}:multi-floor:building"),
        OwnerHistory=owner_history,
        Name=multi_spec.project_name,
        CompositionType="ELEMENT",
        ObjectPlacement=document.create_entity("IfcLocalPlacement", RelativePlacement=origin),
    )
    document.create_entity(
        "IfcRelAggregates",
        GlobalId=_guid_value(f"{multi_spec.project_name}:multi-floor:aggregate-project"),
        OwnerHistory=owner_history,
        RelatingObject=project,
        RelatedObjects=[site],
    )
    document.create_entity(
        "IfcRelAggregates",
        GlobalId=_guid_value(f"{multi_spec.project_name}:multi-floor:aggregate-site"),
        OwnerHistory=owner_history,
        RelatingObject=site,
        RelatedObjects=[building],
    )
    _add_properties(
        document,
        owner_history,
        [building],
        "Pset_LayoutBuilding",
        {
            "FloorCount": len(multi_spec.floors),
            "FloorHeightMm": multi_spec.floor_height_mm,
            "StructuralAxesX": ",".join(str(value) for value in multi_spec.structural_axes_x_mm),
            "StructuralAxesY": ",".join(str(value) for value in multi_spec.structural_axes_y_mm),
        },
    )

    core_by_room = {
        (floor_index, room_id): core.id
        for core in multi_spec.vertical_cores
        for floor_index, room_id in enumerate(core.room_ids)
    }
    storeys = []
    all_walls: list[object] = []
    all_doors: list[object] = []
    all_windows: list[object] = []
    all_openings = 0
    all_wall_types = 0
    all_door_types = 0
    all_window_types = 0
    all_external_entries = 0
    stairs = []

    for floor_index, (floor, floor_result) in enumerate(zip(multi_spec.floors, result.floors)):
        floor_layout = replace(floor.layout, project_name=f"{multi_spec.project_name} / level {floor.level}")
        storey_placement = document.create_entity(
            "IfcLocalPlacement",
            RelativePlacement=_axis_placement(document, (0, 0, floor.elevation_mm)),
        )
        storey = document.create_entity(
            "IfcBuildingStorey",
            GlobalId=_guid_value(f"{multi_spec.project_name}:storey:{floor.level}"),
            OwnerHistory=owner_history,
            Name=f"Level {floor.level}",
            CompositionType="ELEMENT",
            ObjectPlacement=storey_placement,
        )
        storeys.append(storey)
        spaces = []
        spaces_by_id = {}
        for room in floor_layout.rooms:
            rect = floor_result.placements[room.id]
            properties = {
                "RoomType": room.type,
                "TargetAreaM2": room.target_area_m2,
                "NeedsDaylight": room.needs_daylight,
                "IsHeated": room.is_heated,
            }
            core_id = core_by_room.get((floor_index, room.id))
            if core_id:
                properties["VerticalCoreId"] = core_id
            space = document.create_entity(
                "IfcSpace",
                GlobalId=_guid(floor_layout, f"space-{room.id}"),
                OwnerHistory=owner_history,
                Name=room.id,
                LongName=room.type,
                ObjectPlacement=_product_placement(document, storey_placement, rect.x, rect.y),
                Representation=_box_representation(document, context, rect.width, rect.height, IFC_HEIGHT_MM),
            )
            _add_space_quantities(document, owner_history, space, rect)
            _add_properties(document, owner_history, [space], "Pset_LayoutRoom", properties)
            spaces.append(space)
            spaces_by_id[room.id] = space

        wall_plan = build_wall_plan(floor_layout, floor_result)
        walls, wall_records = _make_walls(document, owner_history, context, storey_placement, floor_layout, floor_result, wall_plan)
        doors = _make_doors(document, owner_history, context, storey_placement, wall_plan)
        windows = _make_windows(document, owner_history, context, storey_placement, floor_layout, wall_plan)
        opening_records = _make_openings(document, owner_history, context, storey_placement, floor_layout, wall_plan, doors, windows)
        type_records = _make_element_types(document, owner_history, floor_layout, walls, doors, windows)
        _add_bim_relationships(document, owner_history, floor_layout, floor_result, spaces_by_id, wall_records, opening_records)
        floor_elements = [*spaces, *walls, *doors, *windows]
        document.create_entity(
            "IfcRelContainedInSpatialStructure",
            GlobalId=_guid(floor_layout, "containment-storey"),
            OwnerHistory=owner_history,
            RelatedElements=floor_elements,
            RelatingStructure=storey,
        )
        all_walls.extend(walls)
        all_doors.extend(doors)
        all_windows.extend(windows)
        all_openings += len(opening_records)
        all_wall_types += len(type_records[0])
        all_door_types += len(type_records[1])
        all_window_types += len(type_records[2])
        all_external_entries += sum(1 for opening in wall_plan.openings if opening.external)
        for core in multi_spec.vertical_cores:
            room_id = core.room_ids[floor_index]
            rect = floor_result.placements[room_id]
            stair = document.create_entity(
                "IfcStair",
                GlobalId=_guid(floor_layout, f"stair-{core.id}"),
                OwnerHistory=owner_history,
                Name=f"Stair {core.id} / level {floor.level}",
                ObjectPlacement=_product_placement(document, storey_placement, rect.x, rect.y),
                Representation=_box_representation(document, context, rect.width, rect.height, IFC_HEIGHT_MM),
                PredefinedType="STRAIGHT_RUN_STAIR",
            )
            _add_properties(
                document,
                owner_history,
                [stair],
                "Pset_LayoutStair",
                {"VerticalCoreId": core.id, "ClearWidthMm": core.stair_width_mm, "RunLengthMm": core.stair_run_length_mm},
            )
            stairs.append(stair)
        if multi_spec.vertical_cores:
            document.create_entity(
                "IfcRelContainedInSpatialStructure",
                GlobalId=_guid(floor_layout, "containment-stairs"),
                OwnerHistory=owner_history,
                RelatedElements=stairs[-len(multi_spec.vertical_cores):],
                RelatingStructure=storey,
            )

    document.create_entity(
        "IfcRelAggregates",
        GlobalId=_guid_value(f"{multi_spec.project_name}:multi-floor:aggregate-building"),
        OwnerHistory=owner_history,
        RelatingObject=building,
        RelatedObjects=storeys,
    )
    _associate_material(document, owner_history, all_walls, "Generic partition wall")
    _associate_material(document, owner_history, all_doors, "Generic internal door")
    _associate_material(document, owner_history, all_windows, "Generic window")
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    document.write(str(output))
    return IfcExportSummary(
        path=output,
        spaces=len(document.by_type("IfcSpace")),
        walls=len(all_walls),
        doors=len(all_doors),
        windows=len(all_windows),
        openings=all_openings,
        space_boundaries=len(document.by_type("IfcRelSpaceBoundary")),
        voids=len(document.by_type("IfcRelVoidsElement")),
        fills=len(document.by_type("IfcRelFillsElement")),
        wall_types=all_wall_types,
        door_types=all_door_types,
        window_types=all_window_types,
        external_entries=all_external_entries,
        storeys=len(storeys),
        stairs=len(stairs),
    )


def _make_walls(document, owner_history, context, storey_placement, spec, result, wall_plan: WallPlan):
    segments = _unique_wall_segments(spec, result)
    walls = []
    records = []
    for index, (orientation, fixed, start, end) in enumerate(segments):
        length = end - start
        if length <= 0:
            continue
        if orientation == "horizontal":
            width, depth = length, spec.wall_thickness_mm
            x, y = start, fixed - depth / 2
        else:
            width, depth = spec.wall_thickness_mm, length
            x, y = fixed - width / 2, start
        wall = document.create_entity(
            "IfcWall",
            GlobalId=_guid(spec, f"wall-{index}"),
            OwnerHistory=owner_history,
            Name=f"Wall {index + 1}",
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
        records.append(_WallRecord(wall, orientation, fixed, start, end))
    return walls, records


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
            Name=(f"External entry {opening.room_a}" if opening.external else f"Door {opening.room_a}–{opening.room_b}"),
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
            {
                "RoomA": opening.room_a,
                "RoomB": opening.room_b,
                "ClearWidthMm": opening.width,
                "IsExternal": opening.external,
                **({"EntryId": opening.id} if opening.external else {}),
            },
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
            ObjectPlacement=_product_placement(document, storey_placement, x, y, spec.window_sill_mm),
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


def _make_openings(document, owner_history, context, storey_placement, spec, wall_plan: WallPlan, doors, windows):
    records = []
    for index, (opening, door) in enumerate(zip(wall_plan.openings, doors)):
        width, depth, x, y = _opening_box(opening, spec.wall_thickness_mm)
        element = document.create_entity(
            "IfcOpeningElement",
            GlobalId=_guid(spec, f"opening-door-{index}-{opening.id or index}"),
            OwnerHistory=owner_history,
            Name=f"Door opening {opening.room_a}-{opening.room_b}",
            ObjectPlacement=_product_placement(document, storey_placement, x, y, 0),
            Representation=_box_representation(document, context, width, depth, DOOR_HEIGHT_MM),
            PredefinedType="OPENING",
        )
        _add_properties(
            document,
            owner_history,
            [element],
            "Pset_LayoutOpening",
            {"OpeningKind": "DOOR", "ClearWidthMm": opening.width},
        )
        records.append(_OpeningRecord(opening, element, door, "DOOR"))
    for index, (opening, window) in enumerate(zip(wall_plan.windows, windows)):
        width, depth, x, y = _opening_box(opening, spec.wall_thickness_mm)
        element = document.create_entity(
            "IfcOpeningElement",
            GlobalId=_guid(spec, f"opening-window-{index}-{opening.id or index}"),
            OwnerHistory=owner_history,
            Name=f"Window opening {opening.room_id}",
            ObjectPlacement=_product_placement(document, storey_placement, x, y, spec.window_sill_mm),
            Representation=_box_representation(document, context, width, depth, spec.window_height_mm),
            PredefinedType="OPENING",
        )
        _add_properties(
            document,
            owner_history,
            [element],
            "Pset_LayoutOpening",
            {"OpeningKind": "WINDOW", "ClearWidthMm": opening.width},
        )
        records.append(_OpeningRecord(opening, element, window, "WINDOW"))
    return records


def _add_bim_relationships(document, owner_history, spec, result, spaces_by_id, wall_records, opening_records):
    for wall_index, record in enumerate(wall_records):
        room_ids = _rooms_for_wall_segment(spec, result, record.orientation, record.fixed, record.start, record.end)
        boundary_kind = "INTERNAL" if len(room_ids) > 1 else "EXTERNAL"
        for room_id in room_ids:
            document.create_entity(
                "IfcRelSpaceBoundary",
                GlobalId=_guid(spec, f"space-boundary-{wall_index}-{room_id}"),
                OwnerHistory=owner_history,
                Name=f"{room_id} boundary at {record.element.Name}",
                RelatingSpace=spaces_by_id[room_id],
                RelatedBuildingElement=record.element,
                PhysicalOrVirtualBoundary="PHYSICAL",
                InternalOrExternalBoundary=boundary_kind,
            )

    for opening_index, opening_record in enumerate(opening_records):
        wall = _wall_for_opening(wall_records, opening_record.source)
        if wall is None:
            raise ValueError(f"No IFC wall found for {opening_record.kind.lower()} opening {opening_record.source.id}")
        document.create_entity(
            "IfcRelVoidsElement",
            GlobalId=_guid(spec, f"voids-{opening_index}-{wall.element.GlobalId}-{opening_record.element.GlobalId}"),
            OwnerHistory=owner_history,
            RelatingBuildingElement=wall.element,
            RelatedOpeningElement=opening_record.element,
        )
        document.create_entity(
            "IfcRelFillsElement",
            GlobalId=_guid(spec, f"fills-{opening_index}-{opening_record.element.GlobalId}-{opening_record.filler.GlobalId}"),
            OwnerHistory=owner_history,
            RelatingOpeningElement=opening_record.element,
            RelatedBuildingElement=opening_record.filler,
        )


def _make_element_types(document, owner_history, spec, walls, doors, windows):
    type_records = ([], [], [])
    if walls:
        wall_type = document.create_entity(
            "IfcWallType",
            GlobalId=_guid(spec, "wall-type-generic"),
            OwnerHistory=owner_history,
            Name="Generic partition wall",
            ApplicableOccurrence="IfcWall",
            ElementType="Generic partition wall",
            PredefinedType="PARTITIONING",
        )
        _assign_type(document, owner_history, spec, walls, wall_type, "wall-type-assignment")
        type_records[0].append(wall_type)
    if doors:
        door_type = document.create_entity(
            "IfcDoorType",
            GlobalId=_guid(spec, "door-type-generic"),
            OwnerHistory=owner_history,
            Name="Generic internal door",
            ApplicableOccurrence="IfcDoor",
            ElementType="Generic internal door",
            PredefinedType="DOOR",
            OperationType="NOTDEFINED",
            ParameterTakesPrecedence=True,
        )
        _assign_type(document, owner_history, spec, doors, door_type, "door-type-assignment")
        type_records[1].append(door_type)
    if windows:
        window_type = document.create_entity(
            "IfcWindowType",
            GlobalId=_guid(spec, "window-type-generic"),
            OwnerHistory=owner_history,
            Name="Generic window",
            ApplicableOccurrence="IfcWindow",
            ElementType="Generic window",
            PredefinedType="WINDOW",
            PartitioningType="NOTDEFINED",
            ParameterTakesPrecedence=True,
        )
        _assign_type(document, owner_history, spec, windows, window_type, "window-type-assignment")
        type_records[2].append(window_type)
    return type_records


def _assign_type(document, owner_history, spec, objects, type_element, suffix):
    document.create_entity(
        "IfcRelDefinesByType",
        GlobalId=_guid(spec, suffix),
        OwnerHistory=owner_history,
        RelatedObjects=objects,
        RelatingType=type_element,
    )


def _opening_box(opening: DoorOpening | WindowOpening, wall_thickness: float):
    depth = wall_thickness + 2
    if opening.orientation == "horizontal":
        return opening.width, depth, opening.start, opening.fixed - depth / 2
    return depth, opening.width, opening.fixed - depth / 2, opening.start


def _wall_for_opening(wall_records, opening):
    matches = [
        record
        for record in wall_records
        if record.orientation == opening.orientation
        and abs(record.fixed - opening.fixed) <= 1e-6
        and opening.start + 1e-6 >= record.start
        and opening.end - 1e-6 <= record.end
    ]
    return matches[0] if matches else None


def _rooms_for_wall_segment(spec, result, orientation, fixed, start, end):
    room_ids = []
    for room in spec.rooms:
        rect = result.placements[room.id]
        edges = (
            ("horizontal", rect.y, rect.x, rect.right),
            ("vertical", rect.right, rect.y, rect.top),
            ("horizontal", rect.top, rect.x, rect.right),
            ("vertical", rect.x, rect.y, rect.top),
        )
        if any(
            edge_orientation == orientation
            and abs(edge_fixed - fixed) <= 1e-6
            and min(end, edge_end) - max(start, edge_start) > 1e-6
            for edge_orientation, edge_fixed, edge_start, edge_end in edges
        ):
            room_ids.append(room.id)
    return tuple(room_ids)


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


def _product_placement(document, storey_placement, x: float, y: float, z: float = 0):
    return document.create_entity(
        "IfcLocalPlacement",
        PlacementRelTo=storey_placement,
        RelativePlacement=_axis_placement(document, (x, y, z)),
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


def _guid(spec: LayoutIR, suffix: str) -> str:
    return _guid_value(f"{spec.project_name}:{spec.version}:{suffix}")


def _guid_value(value: str) -> str:
    import ifcopenshell

    return ifcopenshell.guid.compress(str(uuid5(NAMESPACE_URL, value)))

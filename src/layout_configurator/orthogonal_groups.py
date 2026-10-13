"""Bounded declared group identities for explicit orthogonal IFC exchange.

This derives direct source memberships and supplied product references only.
Existing geometry, equipment and route validators remain separate authorities.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math

from .building import BuildingIR, EquipmentSpec, FlowSpec, StructuralGridSpec, ZoneSpec
from .equipment import EquipmentLayoutResult, EquipmentPlacement
from .flows import FlowRoute, FlowRoutingResult
from .models import LayoutIR, LayoutResult, Rect, RoomSpec
from .orthogonal import OrthogonalPolygon
from .orthogonal_projection import ProjectionCapabilityError
from .result_geometry import parse_layout_result
from .schema import normalize_building_mapping, normalize_mapping


MAX_GROUP_ROOMS = 64
MAX_GROUP_ZONES = 64
MAX_GROUP_FLOWS = 64
MAX_GROUP_EQUIPMENT = 32
MAX_GROUP_ROUTES = 256
MAX_GROUP_ROUTE_POINTS = 8192
MAX_GROUP_MEMBERS = 16384
MAX_GROUP_SCALAR_BYTES = 128
MAX_GROUP_HEADER_BYTES = 4096
MAX_GROUP_OPTIONAL_SOURCE_ITEMS = 128
MAX_GROUP_SOURCE_BYTES = 1024 * 1024
MAX_GROUP_NUMBER = 1_000_000


@dataclass(frozen=True)
class OrthogonalGroupMember:
    kind: str
    id: str

    def to_dict(self):
        return {'kind': self.kind, 'id': self.id}


@dataclass(frozen=True)
class OrthogonalGroup:
    kind: str
    id: str
    declaration: dict
    members: tuple[OrthogonalGroupMember, ...]
    unprojected_equipment_ids: tuple[str, ...] = ()

    def to_dict(self):
        return {'kind': self.kind, 'id': self.id,
                'declaration': json.loads(json.dumps(self.declaration, allow_nan=False)),
                'members': [member.to_dict() for member in self.members],
                'unprojected_equipment_ids': list(self.unprojected_equipment_ids)}


@dataclass(frozen=True)
class OrthogonalGroupsReport:
    status: str
    groups: tuple[OrthogonalGroup, ...] = ()
    source: dict | None = None
    equipment_supplied: bool = False
    routing_supplied: bool = False
    issues: tuple[str, ...] = ()

    @property
    def ok(self):
        return self.status == 'PASS'

    def to_dict(self):
        return {'status': self.status, 'ok': self.ok,
                'scope': 'Exact declared semantic groups and supplied product references only',
                'policy': {'name': 'declared_semantic_groups_v1',
                    'zone_members': 'direct_spaces_and_immediate_child_zones',
                    'process_system_members': 'supplied_routes_and_explicit_supplied_equipment_endpoints',
                    'physical_container_validation': 'UNKNOWN', 'network_engineering': 'UNKNOWN',
                    'geometry_validation': 'NOT_EVALUATED'},
                'source': dict(self.source or {key: None for key in (
                    'building_sha256', 'layout_sha256', 'equipment_sha256', 'routing_sha256')}),
                'states': {'equipment': 'SUPPLIED' if self.equipment_supplied else 'NOT_SUPPLIED',
                           'routing': 'SUPPLIED' if self.routing_supplied else 'NOT_SUPPLIED'},
                'groups': [group.to_dict() for group in self.groups],
                'limits': {'rooms': MAX_GROUP_ROOMS, 'zones': MAX_GROUP_ZONES,
                    'flows': MAX_GROUP_FLOWS, 'equipment': MAX_GROUP_EQUIPMENT,
                    'routes': MAX_GROUP_ROUTES, 'total_route_points': MAX_GROUP_ROUTE_POINTS,
                    'total_members': MAX_GROUP_MEMBERS, 'scalar_utf8_bytes': MAX_GROUP_SCALAR_BYTES,
                    'header_utf8_bytes': MAX_GROUP_HEADER_BYTES,
                    'per_optional_source_collection': MAX_GROUP_OPTIONAL_SOURCE_ITEMS,
                    'total_source_bytes': MAX_GROUP_SOURCE_BYTES,
                    'absolute_coordinate_or_numeric_value': MAX_GROUP_NUMBER},
                'issues': list(self.issues), 'proven_building_infeasible': False,
                'full_facility_validation': {'status': 'UNKNOWN', 'ok': False},
                'engineering_acceptance': 'PENDING'}


class OrthogonalGroupsCapabilityError(ProjectionCapabilityError):
    def __init__(self, report: OrthogonalGroupsReport):
        self.report = report
        super().__init__('native_groups', 'Orthogonal group capability is ' + report.status + ': ' + '; '.join(report.issues))

    def to_dict(self):
        return {'status': self.report.status, 'code': 'ORTHOGONAL_GROUPS_CAPABILITY',
                'component': self.component, 'message': str(self), 'evidence': self.report.to_dict()}


def _text(value, limit=MAX_GROUP_SCALAR_BYTES):
    if (type(value) is not str or not value.strip() or len(value) > limit
            or len(value.encode('utf-8')) > limit
            or any(ord(character) < 32 or ord(character) == 127 for character in value)):
        raise ValueError('Group source scalars require bounded nonempty visible text')
    return value


def _number(value, *, positive=False):
    if (type(value) not in (int, float) or abs(value) > MAX_GROUP_NUMBER
            or type(value) is float and not math.isfinite(value) or positive and value <= 0):
        raise ValueError('Group source numbers require actual bounded finite numeric values')


def _ids(values, universe):
    if not isinstance(values, tuple) or len(values) > len(universe):
        raise ValueError('Group source references require bounded canonical tuples')
    for value in values:
        _text(value)
    if len(set(values)) != len(values) or set(values) - universe:
        raise ValueError('Group source references contain duplicate or unknown identities')


def _rect(rect):
    if not isinstance(rect, Rect):
        raise ValueError('Group supplied equipment requires canonical rectangular placement')
    for name in ('x', 'y', 'width', 'height'):
        _number(getattr(rect, name), positive=name in ('width', 'height'))


def _bounded_source(values):
    encoder = json.JSONEncoder(sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False)
    total, hashes = 0, {}
    for key, value in values.items():
        digest = hashlib.sha256()
        for part in encoder.iterencode(value):
            encoded = part.encode('utf-8')
            total += len(encoded)
            if total > MAX_GROUP_SOURCE_BYTES:
                raise ValueError('Group canonical source byte budget exceeded')
            digest.update(encoded)
        hashes[key + '_sha256'] = digest.hexdigest()
    return hashes


def _preflight(building, layout, equipment, routing):
    if not isinstance(building, BuildingIR) or not isinstance(building.layout, LayoutIR) or not isinstance(layout, LayoutResult):
        raise ValueError('Group capability requires canonical BuildingIR and LayoutResult')
    for name, collection, limit in (('rooms', building.layout.rooms, MAX_GROUP_ROOMS),
            ('zones', building.zones, MAX_GROUP_ZONES), ('flows', building.flows, MAX_GROUP_FLOWS),
            ('equipment', building.equipment, MAX_GROUP_EQUIPMENT)):
        if not isinstance(collection, tuple) or len(collection) > limit:
            raise ValueError('Group source ' + name + ' budget or canonical tuple exceeded')
    if len(layout.placements) > MAX_GROUP_ROOMS:
        raise ValueError('Group supplied room budget exceeded')
    if type(layout.variant) is not int or not 0 < layout.variant <= MAX_GROUP_NUMBER or layout.geometry_version not in (None, '0.2'):
        raise ValueError('Group supplied result metadata has unsupported identity/version')
    if (layout.objective_value is not None and (type(layout.objective_value) not in (int, float)
            or abs(layout.objective_value) > 1e15 or type(layout.objective_value) is float and not math.isfinite(layout.objective_value))):
        raise ValueError('Group supplied result objective must be an actual bounded finite number')
    for value in (building.version, building.layout.project_name, building.layout.version):
        _text(value, MAX_GROUP_HEADER_BYTES)
    for collection in (building.layout.doors, building.layout.windows, building.layout.boundary.cutouts):
        if not isinstance(collection, tuple) or len(collection) > MAX_GROUP_OPTIONAL_SOURCE_ITEMS:
            raise ValueError('Group optional source collection budget/type exceeded')
    for opening in (*building.layout.doors, *building.layout.windows,
                    *((building.layout.external_entry,) if building.layout.external_entry is not None else ())):
        _text(opening.id)
        for field in ('room_id', 'room_a', 'room_b', 'side'):
            value = getattr(opening, field, None)
            if value is not None:
                _text(value)
        _number(opening.offset_mm)
        _number(opening.width_mm, positive=True)
    for cutout in building.layout.boundary.cutouts:
        _rect(cutout)
    _text(building.layout.entry_room)
    _number(building.layout.boundary.width_mm, positive=True)
    _number(building.layout.boundary.height_mm, positive=True)
    grid = building.structural_grid
    if grid is not None:
        if (not isinstance(grid, StructuralGridSpec)
                or len(grid.axes_x_mm) + len(grid.axes_y_mm) > MAX_GROUP_OPTIONAL_SOURCE_ITEMS):
            raise ValueError('Group source grid count/type budget exceeded')
        for coordinates, labels in ((grid.axes_x_mm, grid.labels_x), (grid.axes_y_mm, grid.labels_y)):
            if not isinstance(coordinates, tuple) or not isinstance(labels, tuple) or len(labels) > MAX_GROUP_OPTIONAL_SOURCE_ITEMS:
                raise ValueError('Group source grid arrays exceed canonical bounds')
            for coordinate in coordinates:
                _number(coordinate)
            for label in labels:
                _text(label)
    rooms, zones, items = ({item.id for item in collection} for collection in
                           (building.layout.rooms, building.zones, building.equipment))
    if any(len(ids) != len(collection) for ids, collection in
           ((rooms, building.layout.rooms), (zones, building.zones), (items, building.equipment))):
        raise ValueError('Group source contains duplicate identities')
    if set(layout.placements) != rooms:
        raise ValueError('Group supplied room identities must match the source')
    for room in building.layout.rooms:
        if (not isinstance(room, RoomSpec) or any(type(getattr(room, field)) is not bool
                for field in ('needs_daylight', 'is_heated', 'rotatable'))):
            raise ValueError('Group room source requires actual canonical boolean fields')
        _text(room.id)
        _text(room.type)
        for field in ('required_adjacency', 'preferred_adjacency', 'forbidden_adjacency'):
            _ids(getattr(room, field), rooms)
    for zone in building.zones:
        if not isinstance(zone, ZoneSpec) or type(zone.airlock) is not bool:
            raise ValueError('Group zone source requires ZoneSpec and actual boolean airlock')
        _text(zone.id)
        _text(zone.type)
        if zone.parent_zone_id is not None:
            _text(zone.parent_zone_id)
        if zone.cleanroom_class is not None:
            _text(zone.cleanroom_class)
        if zone.pressure_pa is not None:
            _number(zone.pressure_pa)
        _ids(zone.room_ids, rooms)
        for field in ('required_adjacency', 'preferred_adjacency', 'forbidden_adjacency'):
            _ids(getattr(zone, field), zones)
    for item in building.equipment:
        if not isinstance(item, EquipmentSpec) or type(item.rotation_allowed) is not bool or type(item.fixed) is not bool:
            raise ValueError('Group equipment source requires actual canonical types')
        _text(item.id)
        _text(item.type)
        for value in (item.room_id, item.zone_id, item.anchor_side):
            if value is not None:
                _text(value)
        for field in ('width_mm', 'depth_mm', 'clearance_front_mm', 'clearance_back_mm',
                      'clearance_left_mm', 'clearance_right_mm', 'anchor_offset_mm'):
            value = getattr(item, field)
            _number(value, positive=field in ('width_mm', 'depth_mm'))
            if value < 0:
                raise ValueError('Group equipment dimensions/clearances cannot be negative')
    flows = {}
    for flow in building.flows:
        if not isinstance(flow, FlowSpec) or type(flow.required) is not bool:
            raise ValueError('Group flow source requires FlowSpec and actual boolean required')
        _text(flow.id)
        _text(flow.type)
        if flow.stage is not None:
            _text(flow.stage)
        _number(flow.minimum_clear_width_mm, positive=True)
        _ids(flow.from_ids, rooms | zones | items)
        _ids(flow.to_ids, rooms | zones | items)
        if not flow.from_ids or not flow.to_ids or set(flow.from_ids) & set(flow.to_ids) or flow.id in flows:
            raise ValueError('Group flow endpoints/identity must be nonempty, directed and unique')
        flows[flow.id] = flow
    for shape in layout.placements.values():
        if isinstance(shape, Rect):
            _rect(shape)
        elif isinstance(shape, OrthogonalPolygon):
            for point in shape.vertices:
                for value in point:
                    _number(value)
        else:
            raise ValueError('Group supplied room requires canonical result geometry')
    placed = set()
    if equipment is not None:
        if not isinstance(equipment, EquipmentLayoutResult) or len(equipment.placements) > MAX_GROUP_EQUIPMENT:
            raise ValueError('Group supplied equipment budget/type exceeded')
        for placement in equipment.placements:
            if (not isinstance(placement, EquipmentPlacement) or type(placement.rotated) is not bool
                    or placement.equipment_id not in items or placement.equipment_id in placed
                    or placement.room_id not in rooms):
                raise ValueError('Group supplied equipment has invalid or duplicate identity')
            _rect(placement.rect)
            placed.add(placement.equipment_id)
    routes, points = {}, 0
    if routing is not None:
        if not isinstance(routing, FlowRoutingResult) or len(routing.routes) > MAX_GROUP_ROUTES:
            raise ValueError('Group supplied route budget/type exceeded')
        for route in routing.routes:
            if not isinstance(route, FlowRoute) or route.flow_id not in flows:
                raise ValueError('Group supplied route references unknown flow')
            flow = flows[route.flow_id]
            if (route.from_id not in flow.from_ids or route.to_id not in flow.to_ids
                    or route.from_room_id not in rooms or route.to_room_id not in rooms):
                raise ValueError('Group supplied route direction/source identities differ')
            _ids(route.room_path, rooms)
            if not route.room_path or route.room_path[0] != route.from_room_id or route.room_path[-1] != route.to_room_id:
                raise ValueError('Group supplied route host chain differs from its source identity')
            if not isinstance(route.points, tuple) or not 1 <= len(route.points) <= 1024:
                raise ValueError('Group supplied route points require bounded canonical tuples')
            points += len(route.points)
            if points > MAX_GROUP_ROUTE_POINTS:
                raise ValueError('Group total supplied route point budget exceeded')
            for point in route.points:
                if not isinstance(point, tuple) or len(point) != 2:
                    raise ValueError('Group route point must contain two coordinates')
                for value in point:
                    _number(value)
            _number(route.minimum_clear_width_mm, positive=True)
            identity = json.dumps([route.flow_id, route.from_id, route.to_id, route.from_room_id, route.to_room_id],
                                  ensure_ascii=False, separators=(',', ':'))
            if identity in routes:
                raise ValueError('Group supplied route contains duplicate semantic identity')
            routes[identity] = route.flow_id
    return items, placed, routes


def validate_orthogonal_groups(building: BuildingIR, layout: LayoutResult,
                               equipment: EquipmentLayoutResult | None = None, *,
                               routing: FlowRoutingResult | None = None) -> OrthogonalGroupsReport:
    """Derive source declarations; PASS is independent of physical predicates."""
    try:
        items, placed, routes = _preflight(building, layout, equipment, routing)
        raw = {'building': building.to_dict(), 'layout': layout.to_dict(),
               'equipment': equipment.to_dict(building) if equipment is not None else None,
               'routing': routing.to_dict() if routing is not None else None}
        source = _bounded_source(raw)
        normalize_mapping(raw['building']['layout'])
        normalize_building_mapping(raw['building'])
        parse_layout_result(raw['layout'])
        groups = []
        for zone in sorted(building.zones, key=lambda item: item.id):
            references = [('room', room_id) for room_id in zone.room_ids]
            references += [('zone', child.id) for child in building.zones if child.parent_zone_id == zone.id]
            groups.append(OrthogonalGroup('zone', zone.id, zone.to_dict(),
                tuple(OrthogonalGroupMember(kind, identity) for kind, identity in sorted(references))))
        for flow in sorted(building.flows, key=lambda item: item.id):
            endpoints = (set(flow.from_ids) | set(flow.to_ids)) & items
            references = [('equipment', identity) for identity in endpoints & placed]
            references += [('route', identity) for identity, flow_id in routes.items() if flow_id == flow.id]
            groups.append(OrthogonalGroup('flow-system', flow.id, flow.to_dict(),
                tuple(OrthogonalGroupMember(kind, identity) for kind, identity in sorted(references)),
                tuple(sorted(endpoints - placed))))
        if sum(len(group.members) for group in groups) > MAX_GROUP_MEMBERS:
            raise ValueError('Group derived membership budget exceeded')
        return OrthogonalGroupsReport('PASS', tuple(groups), source, equipment is not None, routing is not None)
    except (ValueError, TypeError, AttributeError, KeyError, OverflowError) as exc:
        return OrthogonalGroupsReport('FAIL', equipment_supplied=equipment is not None,
                                      routing_supplied=routing is not None, issues=(str(exc)[:1000],))

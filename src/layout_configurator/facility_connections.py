"""Declared multi-storey functional blocks and service-zone coordination.

Service links produce ordinary geometrically checked floor routes. They are
coordination requirements, not pipe/duct design or permission for people to
travel through shafts. Source identities remain explicit and stable.
"""

from dataclasses import dataclass
import math
from collections.abc import Mapping


@dataclass(frozen=True)
class FacilityBlock:
    id: str
    zones: dict[int, tuple[str, ...]]

    def to_dict(self):
        return {"id": self.id, "zones": {str(level): list(zones) for level, zones in sorted(self.zones.items())}}


@dataclass(frozen=True)
class ServiceZoneLink:
    id: str
    from_level: int
    from_zone: str
    to_level: int
    to_zone: str
    via: str
    minimum_clear_width_mm: float
    required: bool

    @property
    def flow_id(self):
        return f"service-link:{self.id}"

    def to_dict(self):
        return {"id": self.id, "from": {"level": self.from_level, "zone_id": self.from_zone},
                "to": {"level": self.to_level, "zone_id": self.to_zone}, "via": self.via,
                "minimum_clear_width_mm": self.minimum_clear_width_mm, "required": self.required,
                "derived_flow_id": self.flow_id}


def parse_facility_connections(payload, floors, cores, existing_flows):
    """Validate strict optional contracts and referential integrity before solving."""
    from .facility_floors import CrossFloorFlow, FacilityFloorsError
    from .resources import resource_path
    from .schema import validate_mapping

    try:
        blocks_raw = payload.get("facility_blocks", [])
        links_raw = payload.get("service_zone_links", [])
        # YAML accepts integer mapping keys; canonical JSON names are strings.
        normalized_blocks = []
        if not isinstance(blocks_raw, list):
            raise ValueError("facility_blocks must be an array")
        for item in blocks_raw:
            if not isinstance(item, Mapping) or not isinstance(item.get("zones"), Mapping):
                raise ValueError("Each facility block requires a zones mapping")
            zones = {}
            for key, value in item["zones"].items():
                if isinstance(key, bool) or not isinstance(key, (str, int)):
                    raise ValueError("Block floor keys must name integer levels")
                name = str(key)
                if name in zones:
                    raise ValueError("Duplicate block floor key")
                zones[name] = value
            normalized_blocks.append({**item, "zones": zones})
        raw = {"facility_blocks": normalized_blocks, "service_zone_links": links_raw}
        issues = validate_mapping(raw, resource_path("schemas/facility_connections.schema.json"))
        if issues:
            raise ValueError("; ".join(f"{issue.path}: {issue.message}" for issue in issues))
        blocks, links, derived = [], [], []
        known_cores = {core.id: core for core in cores}
        flow_ids = {flow.id for flow in existing_flows}
        for item in normalized_blocks:
            if any(block.id == item["id"] for block in blocks):
                raise ValueError(f"Duplicate facility block {item['id']}")
            zones = {}
            for key, names in item["zones"].items():
                level = int(key)
                if level in zones or str(level) != key:
                    raise ValueError("Block floor keys must have unique canonical integer spelling")
                if level not in floors:
                    raise ValueError(f"Block {item['id']} references unknown floor {level}")
                building = floors[level].building
                for zone_id in names:
                    if zone_id not in {zone.id for zone in building.zones} or not building.room_ids_for_zone(zone_id):
                        raise ValueError(f"Block {item['id']} references missing or empty zone {zone_id} on floor {level}")
                zones[level] = tuple(sorted(names))
            blocks.append(FacilityBlock(item["id"], zones))
        for item in links_raw:
            first, second = item["from"], item["to"]
            link = ServiceZoneLink(item["id"], first["level"], first["zone_id"], second["level"], second["zone_id"],
                                   item["via"], float(item.get("minimum_clear_width_mm", 1200)), item.get("required", True))
            if any(existing.id == link.id for existing in links):
                raise ValueError(f"Duplicate service-zone link {link.id}")
            if link.from_level == link.to_level:
                raise ValueError(f"Service link {link.id} must connect different floors")
            if not math.isfinite(link.minimum_clear_width_mm) or link.minimum_clear_width_mm <= 0:
                raise ValueError("Service-link clear width must be positive and finite")
            core = known_cores.get(link.via)
            if core is None or core.kind != "service_shaft":
                raise ValueError(f"Service link {link.id} requires a declared service_shaft core")
            for level, zone_id in ((link.from_level, link.from_zone), (link.to_level, link.to_zone)):
                if level not in floors or level not in core.rooms:
                    raise ValueError(f"Service link {link.id} core does not serve floor {level}")
                building = floors[level].building
                if zone_id not in {zone.id for zone in building.zones} or not (members := building.room_ids_for_zone(zone_id)):
                    raise ValueError(f"Service link {link.id} references missing or empty zone {zone_id} on floor {level}")
                if core.rooms[level] in members:
                    raise ValueError(f"Service link {link.id} zone must exclude its shaft room")
            if link.flow_id in flow_ids:
                raise ValueError(f"Derived service-flow identity collides: {link.flow_id}")
            for level in (link.from_level, link.to_level):
                if f"{link.flow_id}@L{level}" in {flow.id for flow in floors[level].building.flows}:
                    raise ValueError(f"Service link {link.id} leg collides with a declared floor flow")
            flow_ids.add(link.flow_id)
            derived.append(CrossFloorFlow(link.flow_id, "service", link.from_level, link.from_zone,
                                         link.to_level, link.to_zone, (link.via,), link.minimum_clear_width_mm, link.required))
            links.append(link)
        return tuple(blocks), tuple(links), tuple(derived)
    except (ValueError, TypeError, KeyError) as exc:
        raise FacilityFloorsError(f"Invalid facility connections: {exc}") from exc

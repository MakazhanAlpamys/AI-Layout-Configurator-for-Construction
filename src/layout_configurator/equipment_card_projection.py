"""Literal source_local_2d_v1 equipment sidecars; no legacy solver enforcement.

Only selected scenario geometry is projected. Supplied actions are declared
premises, not verified operational events. Room containment, collisions,
maintenance completion and whole 3D installation routes remain UNKNOWN.
"""

from dataclasses import dataclass, fields
from shapely.geometry import Polygon

from .building import EquipmentSpec
from .equipment import EquipmentPlacement
from .models import Rect
from .engineering_library import (EngineeringLibraryContract, _digest, _equipment_frame, _json_bytes, _number, _utf8_size,
                                  validate_engineering_library)


class EquipmentCardCapabilityError(ValueError):
    def __init__(self, code, message, *, status="UNKNOWN", report=None):
        self.code, self.status, self.report = code, status, report
        super().__init__(message)

    def to_dict(self):
        output = {"status": self.status, "code": self.code, "message": str(self),
                  "capability": "source_local_2d_v1", "full_scenario_validation": "UNKNOWN",
                  "three_dimensional_route_feasibility": "UNKNOWN", "engineering_acceptance": "PENDING"}
        if self.report is not None:
            output["library_validation"] = self.report.to_dict()
        return output


@dataclass(frozen=True)
class EquipmentCardProjection:
    _json: str

    def to_dict(self):
        return EngineeringLibraryContract(self._json).to_dict()


def _original_equipment(equipment):
    if type(equipment) is not EquipmentSpec:
        raise ValueError("An original EquipmentSpec identity is required")
    raw = {field.name: getattr(equipment, field.name) for field in fields(equipment)
           if getattr(equipment, field.name) is not None}
    _equipment_frame(raw)
    # Source serializer is reached only after strict original scalar checks.
    return equipment.to_dict()


def _placement_frame(placement, equipment):
    if type(placement) is not EquipmentPlacement or type(placement.rect) is not Rect or type(placement.rotated) is not bool:
        raise ValueError("Placement/rectangle/rotation need original strict types")
    if placement.equipment_id != equipment.id or type(placement.room_id) is not str or not 1 <= len(placement.room_id) <= 128 or placement.room_id != placement.room_id.strip():
        raise ValueError("Placement must retain exact equipment and bounded host identity")
    if any(char in placement.room_id for char in "\r\n"):
        raise ValueError("Placement host identity must be a single line")
    _utf8_size(placement.room_id, 512, "$.placement.room_id")
    if equipment.room_id is not None and placement.room_id != equipment.room_id:
        raise ValueError("Placement host differs from the original equipment room")
    if placement.rotated and not equipment.rotation_allowed:
        raise ValueError("Placement rotation is forbidden by original equipment source")
    rect = placement.rect
    if any(not _number(getattr(rect, key), lower=-1000000, upper=1000000) for key in ("x", "y", "width", "height")) or rect.width <= 0 or rect.height <= 0:
        raise ValueError("Placement requires bounded finite actual coordinates")
    width, depth = (equipment.depth_mm, equipment.width_mm) if placement.rotated else (equipment.width_mm, equipment.depth_mm)
    if rect.width != width or rect.height != depth:
        raise ValueError("Placement footprint must equal exact oriented equipment dimensions")
    return {"equipment_id": placement.equipment_id, "room_id": placement.room_id,
            "x": rect.x, "y": rect.y, "width": rect.width, "depth": rect.height, "rotated": placement.rotated}


def _points(value):
    if value["kind"] == "orthogonal_polygon":
        return value["points"]
    if value["kind"] == "rectangle":
        x, y, width, depth = (value[key] for key in ("x", "y", "width", "depth"))
        return [[x, y], [x + width, y], [x + width, y + depth], [x, y + depth], [x, y]]
    raise EquipmentCardCapabilityError("unsupported_geometry", "Selected envelope needs literal rectangle or orthogonal ring")


def project_equipment_card(library, card_id, *, equipment, placement, scenario="production",
                           active_action_ids=(), capability="source_local_2d_v1"):
    """Freshly bind exact equipment/card/source, then translate or turn its geometry.

    A 90 degree turn uses the existing equipment convention: (depth-y, x).
    It never replaces a concave envelope by a bounding box, adds a default port,
    or supplies these envelopes to the existing equipment or flow solvers.
    """
    if type(capability) is not str or capability != "source_local_2d_v1":
        raise EquipmentCardCapabilityError("unsupported_capability", "Only source_local_2d_v1 is supported")
    if type(card_id) is not str or not 1 <= len(card_id) <= 128 or type(scenario) is not str or scenario not in ("production", "maintenance", "installation_replacement"):
        raise EquipmentCardCapabilityError("invalid_selection", "Card and scenario need strict known identities", status="FAIL")
    if type(active_action_ids) not in (tuple, list) or len(active_action_ids) > 32 or any(type(item) is not str or not 1 <= len(item) <= 128 for item in active_action_ids) or len(set(active_action_ids)) != len(active_action_ids):
        raise EquipmentCardCapabilityError("invalid_actions", "Active actions require unique bounded explicit identities", status="FAIL")
    try:
        equipment_frame = _original_equipment(equipment)
        placement_frame = _placement_frame(placement, equipment)
    except ValueError as exc:
        raise EquipmentCardCapabilityError("invalid_original_equipment", str(exc), status="FAIL") from exc
    report = validate_engineering_library(library)
    if report.library is None or report.binding_status != "PASS":
        raise EquipmentCardCapabilityError("invalid_library", "Fresh independent structured source binding is required",
                                           status="FAIL" if report.status == "FAIL" else "UNKNOWN", report=report)
    raw = report.library.to_dict()
    card = next((row for row in raw["cards"] if row["id"] == card_id), None)
    if card is None:
        raise EquipmentCardCapabilityError("unknown_card", "Card identity was not supplied", status="FAIL")
    if card["equipment_binding"]["id"] != equipment.id or card["equipment_binding"]["sha256"] != _digest(equipment_frame) or card["equipment_type"] != equipment.type:
        raise EquipmentCardCapabilityError("equipment_binding_mismatch", "Card must bind the exact original equipment source", status="FAIL")
    applicability = card["applicability"]
    if applicability["conditions"]:
        raise EquipmentCardCapabilityError("unsupported_applicability", "Additional applicability predicates need an independent implementation")
    if equipment.id not in applicability["equipment_ids"] or equipment.type not in applicability["equipment_types"] or scenario not in applicability["scenarios"]:
        raise EquipmentCardCapabilityError("outside_applicability", "Equipment or scenario is outside supplied applicability", status="FAIL")
    sources = {row["id"]: row for row in raw["sources"]}

    def permitted(source_id):
        if sources[source_id]["usage_rights"]["state"] != "PERMITTED":
            raise EquipmentCardCapabilityError("unavailable_usage_rights", "Reusable source rights were not supplied")
        if sources[source_id]["kind"] == "visual_reference":
            raise EquipmentCardCapabilityError("visual_reference_not_numeric", "Visual references do not establish numeric physical geometry")

    permitted(card["equipment_binding"]["source_id"])

    def known(evidence):
        if evidence["status"] != "KNOWN" or evidence["basis"] == "assumption":
            raise EquipmentCardCapabilityError("unverified_evidence", "Selected geometry/dimensions/actions require non-assumed structured source values")
        if evidence["basis"] == "supplied_confirmation" and card["engineer_confirmation"] is None:
            raise EquipmentCardCapabilityError("missing_confirmation", "Selected confirmation basis needs source-bound confirmation")
        permitted(evidence["source_id"])

    for name, dimension in card["dimensions"].items():
        if name != "height_mm":
            known(dimension["measurement"])
            if dimension["measurement"]["value"] != getattr(equipment, name):
                raise EquipmentCardCapabilityError("dimension_binding_mismatch", "Selected 2D card dimensions differ from original equipment", status="FAIL")
    actions = {row["id"]: row for row in card["scenario_actions"]}
    for action_id in active_action_ids:
        if action_id not in actions or actions[action_id]["scenario"] != scenario:
            raise EquipmentCardCapabilityError("invalid_actions", "Active action must be declared for the selected scenario", status="FAIL")
        known(actions[action_id]["evidence"])

    def transform(point):
        x, y = point
        if placement.rotated:
            x, y = equipment.depth_mm - y, x
        world = [placement.rect.x + x, placement.rect.y + y]
        if any(not _number(coordinate, lower=-1000000, upper=1000000) for coordinate in world):
            raise EquipmentCardCapabilityError("world_coordinate_bound", "Projected coordinate exceeds local capability bounds", status="FAIL")
        return world

    envelopes = []
    for row in card["envelopes"]:
        if row["scenario"] not in ("all", scenario):
            continue
        known(row["evidence"])
        if not set(row["required_action_ids"]) <= set(active_action_ids):
            raise EquipmentCardCapabilityError("missing_scenario_action", "Selected envelope premise requires explicitly supplied active actions")
        value = row["evidence"]["value"]
        if row["role"] == "body" and (value["kind"] != "rectangle" or value["x"] != 0 or value["y"] != 0 or value["width"] != equipment.width_mm or value["depth"] != equipment.depth_mm):
            raise EquipmentCardCapabilityError("unsupported_body_projection", "Legacy physical footprint requires its exact source rectangle; no bounding-box coercion")
        local = _points(value)
        world = [transform(point) for point in local]
        geometry = Polygon(world)
        if not geometry.is_valid or geometry.area <= 0:
            raise EquipmentCardCapabilityError("unrepresentable_geometry",
                "Envelope " + row["id"] + " has no valid positive-area world contour at supported precision", report=report)
        envelopes.append({"id": row["id"], "role": row["role"], "scenario": row["scenario"],
                          "source_evidence": row["evidence"], "required_action_ids": row["required_action_ids"],
                          "local_points_mm": local, "world_points_mm": world})
    ports = []
    normal_directions = {"front": "top", "back": "bottom", "left": "left", "right": "right"}
    rotated_directions = {"front": "left", "back": "right", "left": "bottom", "right": "top"}
    for row in card["interaction_points"]:
        if row["scenario"] != scenario:
            continue
        known(row["evidence"])
        value = row["evidence"]["value"]
        ports.append({"id": row["id"], "role": row["role"], "scenario": row["scenario"], "source_evidence": row["evidence"],
                      "local_point_mm": [value["x"], value["y"]], "world_point_mm": transform([value["x"], value["y"]]),
                      "source_direction": value["direction"], "world_direction":
                          (rotated_directions if placement.rotated else normal_directions)[value["direction"]]})
    output = {"schema_version": 1, "capability": capability, "status": "DRAFT", "local_projection_status": "PASS",
              "local_projection_scope": "Literal supplied scenario envelopes and points only; no collision or route validation",
              "library_sha256": report.library_sha256, "card_id": card_id, "card_sha256": _digest(card),
              "equipment_id": equipment.id, "equipment_sha256": _digest(equipment_frame),
              "placement": placement_frame, "placement_sha256": _digest(placement_frame), "origin": card["origin"],
              "scenario": scenario, "dimensions": card["dimensions"], "applicability": applicability,
              "body_points_mm": next(row["world_points_mm"] for row in envelopes if row["role"] == "body"),
              "envelopes": envelopes, "interaction_points": ports,
              "unprojected_envelope_ids": [row["id"] for row in card["envelopes"] if row["scenario"] not in ("all", scenario)],
              "active_actions": [{"id": item, "value": actions[item]["evidence"]["value"],
                                   "source_evidence": actions[item]["evidence"], "operational_verification": "UNKNOWN"}
                                  for item in active_action_ids],
              "full_scenario_validation": "UNKNOWN", "room_containment_and_collisions": "UNKNOWN",
              "three_dimensional_route_feasibility": "UNKNOWN", "engineering_acceptance": "PENDING",
              "source_authentication": "UNKNOWN", "usage_rights_scope": "supplied_declaration_only",
              "library_validation": report.to_dict()}
    return EquipmentCardProjection(_json_bytes(output).decode("utf-8"))

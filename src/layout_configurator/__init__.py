"""Deterministic building layout generator."""

from .building import BuildingIR, BuildingSpecError, EquipmentSpec, FlowSpec, StructuralGridSpec, ZoneSpec
from .equipment import (
    EquipmentLayoutResult,
    EquipmentPlacement,
    EquipmentPlacementError,
    EquipmentValidationIssue,
    EquipmentValidationReport,
    clearance_rect,
    place_equipment,
    validate_equipment_layout,
)
from .flows import FlowRoute, FlowRoutingResult, FlowValidationIssue, FlowValidationReport, route_flows, validate_flow_routes
from .models import DoorSpec, ExternalEntrySpec, LayoutIR, LayoutResult, Rect, RoomSpec, WindowSpec
from .brief import parse_llm_mapping, parse_text_brief
from .multifloor import MultiFloorResult, MultiFloorSpec, MultiFloorValidationReport, VerticalCoreSpec, solve_multifloor, validate_multifloor
from .commands import AddDoor, AddWindow, EditError, MoveRoom, RemoveDoor, RemoveExternalEntry, RemoveWindow, ResizeRoom, SetExternalEntry
from .compliance import IdsSpecificationResult, IdsValidationReport, validate_ids
from .editor import EditorState
from .ifc import IfcExportSummary, export_ifc, export_multifloor_ifc
from .llm import llm_settings_from_environment, parse_with_openai_compatible
from .norms import NormsReport, RuleCitation, RuleDefinition, RuleResult, RuleSet, RuleStatus, check_layout, load_ruleset, retrieve_rule_citations
from .solver import InfeasibleLayout, solve_layouts
from .validation import ValidationReport, validate_layout
from .walls import DoorOpening, WallPlan, WindowOpening, build_wall_plan

__all__ = [
    "InfeasibleLayout",
    "BuildingIR",
    "BuildingSpecError",
    "ZoneSpec",
    "EquipmentSpec",
    "FlowSpec",
    "StructuralGridSpec",
    "EquipmentLayoutResult",
    "EquipmentPlacement",
    "EquipmentPlacementError",
    "EquipmentValidationIssue",
    "EquipmentValidationReport",
    "clearance_rect",
    "place_equipment",
    "validate_equipment_layout",
    "FlowRoute",
    "FlowRoutingResult",
    "FlowValidationIssue",
    "FlowValidationReport",
    "route_flows",
    "validate_flow_routes",
    "LayoutIR",
    "LayoutResult",
    "Rect",
    "RoomSpec",
    "DoorSpec",
    "WindowSpec",
    "ExternalEntrySpec",
    "AddDoor",
    "AddWindow",
    "EditError",
    "EditorState",
    "IfcExportSummary",
    "IdsSpecificationResult",
    "IdsValidationReport",
    "NormsReport",
    "RuleDefinition",
    "RuleResult",
    "RuleSet",
    "RuleStatus",
    "MoveRoom",
    "ResizeRoom",
    "RemoveDoor",
    "RemoveWindow",
    "SetExternalEntry",
    "RemoveExternalEntry",
    "DoorOpening",
    "WallPlan",
    "WindowOpening",
    "ValidationReport",
    "build_wall_plan",
    "export_ifc",
    "export_multifloor_ifc",
    "parse_with_openai_compatible",
    "llm_settings_from_environment",
    "check_layout",
    "load_ruleset",
    "RuleCitation",
    "retrieve_rule_citations",
    "validate_ids",
    "solve_layouts",
    "validate_layout",
    "MultiFloorSpec",
    "VerticalCoreSpec",
    "MultiFloorResult",
    "MultiFloorValidationReport",
    "solve_multifloor",
    "validate_multifloor",
    "parse_text_brief",
    "parse_llm_mapping",
]

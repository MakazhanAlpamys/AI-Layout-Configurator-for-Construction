"""Deterministic residential layout generator."""

from .models import DoorSpec, ExternalEntrySpec, LayoutIR, LayoutResult, Rect, RoomSpec, WindowSpec
from .brief import parse_llm_mapping, parse_text_brief
from .multifloor import MultiFloorResult, MultiFloorSpec, MultiFloorValidationReport, VerticalCoreSpec, solve_multifloor, validate_multifloor
from .commands import AddDoor, AddWindow, EditError, MoveRoom, RemoveDoor, RemoveExternalEntry, RemoveWindow, ResizeRoom, SetExternalEntry
from .compliance import IdsSpecificationResult, IdsValidationReport, validate_ids
from .editor import EditorState
from .ifc import IfcExportSummary, export_ifc, export_multifloor_ifc
from .norms import NormsReport, RuleCitation, RuleDefinition, RuleResult, RuleSet, RuleStatus, check_layout, load_ruleset, retrieve_rule_citations
from .solver import InfeasibleLayout, solve_layouts
from .validation import ValidationReport, validate_layout
from .walls import DoorOpening, WallPlan, WindowOpening, build_wall_plan

__all__ = [
    "InfeasibleLayout",
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

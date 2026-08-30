"""Deterministic residential layout generator."""

from .models import DoorSpec, LayoutIR, LayoutResult, Rect, RoomSpec, WindowSpec
from .commands import AddDoor, AddWindow, EditError, MoveRoom, RemoveDoor, RemoveWindow, ResizeRoom
from .compliance import IdsSpecificationResult, IdsValidationReport, validate_ids
from .editor import EditorState
from .ifc import IfcExportSummary, export_ifc
from .norms import NormsReport, RuleDefinition, RuleResult, RuleSet, RuleStatus, check_layout, load_ruleset
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
    "DoorOpening",
    "WallPlan",
    "WindowOpening",
    "ValidationReport",
    "build_wall_plan",
    "export_ifc",
    "check_layout",
    "load_ruleset",
    "validate_ids",
    "solve_layouts",
    "validate_layout",
]

"""Deterministic residential layout generator."""

from .models import LayoutIR, LayoutResult, Rect, RoomSpec
from .solver import InfeasibleLayout, solve_layouts
from .validation import ValidationReport, validate_layout

__all__ = [
    "InfeasibleLayout",
    "LayoutIR",
    "LayoutResult",
    "Rect",
    "RoomSpec",
    "ValidationReport",
    "solve_layouts",
    "validate_layout",
]

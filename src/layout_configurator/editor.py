"""Atomic command application and validation for editable layouts."""

from __future__ import annotations

from dataclasses import dataclass

from .commands import Command, EditError
from .models import LayoutIR, LayoutResult
from .solver import InfeasibleLayout, solve_layouts
from .validation import ValidationReport, validate_layout
from .walls import build_wall_plan


@dataclass(frozen=True)
class EditorState:
    spec: LayoutIR
    result: LayoutResult
    report: ValidationReport
    history: tuple[str, ...] = ()
    solve_time_limit_seconds: float = 10
    seed: int = 42

    @classmethod
    def from_layout(cls, spec: LayoutIR, result: LayoutResult) -> "EditorState":
        report = validate_layout(spec, result)
        if not report.ok:
            raise EditError("Нельзя редактировать невалидную планировку: " + _format_issues(report))
        try:
            build_wall_plan(spec, result)
        except (KeyError, ValueError) as exc:
            raise EditError(f"Invalid opening placement: {exc}") from exc
        return cls(spec=spec, result=result, report=report)

    def apply(self, command: Command) -> "EditorState":
        try:
            new_spec, new_result = command.apply(self.spec, self.result)
            fixed_room_ids = command.locked_room_ids(new_spec, new_result)
            fixed_rects = {room_id: new_result.placements[room_id] for room_id in fixed_room_ids}
            recalculated = solve_layouts(
                new_spec,
                variants=1,
                time_limit_seconds=self.solve_time_limit_seconds,
                seed=self.seed,
                fixed_rects=fixed_rects,
            )[0]
        except (KeyError, ValueError) as exc:
            if isinstance(exc, EditError):
                raise
            raise EditError(str(exc)) from exc
        except (InfeasibleLayout, RuntimeError) as exc:
            raise EditError(f"Правка не может быть пересчитана: {exc}") from exc
        report = validate_layout(new_spec, recalculated)
        if not report.ok:
            raise EditError(f"Правка отклонена: {_format_issues(report)}")
        try:
            build_wall_plan(new_spec, recalculated)
        except (KeyError, ValueError) as exc:
            raise EditError(f"Edit rejected: invalid opening placement: {exc}") from exc
        return EditorState(
            spec=new_spec,
            result=recalculated,
            report=report,
            history=self.history + (type(command).__name__,),
            solve_time_limit_seconds=self.solve_time_limit_seconds,
            seed=self.seed,
        )


def _format_issues(report: ValidationReport) -> str:
    return "; ".join(f"{issue.code}: {issue.message}" for issue in report.issues)

"""Machine-checkable IFC information validation via IDS."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class IdsSpecificationResult:
    name: str
    applicable: int
    passed: int
    failed: int
    ok: bool


@dataclass(frozen=True)
class IdsValidationReport:
    ids_path: Path
    ifc_path: Path
    specifications: tuple[IdsSpecificationResult, ...]

    @property
    def ok(self) -> bool:
        return all(item.ok for item in self.specifications)


def validate_ids(ifc_path: str | Path, ids_path: str | Path) -> IdsValidationReport:
    import ifcopenshell
    from ifctester import ids

    ids_file = ids.open(str(ids_path), validate=True)
    ifc_file = ifcopenshell.open(str(ifc_path))
    ids_file.validate(ifc_file, should_filter_version=True, filepath=str(ids_path))
    specifications = tuple(
        _specification_result(specification)
        for specification in ids_file.specifications
    )
    return IdsValidationReport(ids_path=Path(ids_path), ifc_path=Path(ifc_path), specifications=specifications)


def _specification_result(specification) -> IdsSpecificationResult:
    """Treat a specification with no applicable entities as vacuously passing."""

    applicable = len(specification.applicable_entities)
    return IdsSpecificationResult(
        name=specification.name,
        applicable=applicable,
        passed=len(specification.passed_entities),
        failed=len(specification.failed_entities),
        ok=applicable == 0 or bool(specification.status),
    )

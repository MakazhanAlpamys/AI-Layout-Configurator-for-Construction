"""Source facade and vertical eligibility for opted-in window coordination.

The existing orthogonal IFC wall height is a 2800 mm coordination proxy.
This component checks representation eligibility against that proxy, without
changing B13's two-dimensional facade policy or claiming building feasibility.
"""

from dataclasses import dataclass

from .models import LayoutIR, LayoutResult
from .orthogonal_projection import ProjectionCapabilityError
from .orthogonal_windows import OrthogonalWindowReport, project_orthogonal_windows
from .result_geometry import GeometryCheck


ORTHOGONAL_COORDINATION_WALL_HEIGHT_MM = 2800
ORTHOGONAL_WINDOW_CAPABILITY = "source_facade_v1"


@dataclass(frozen=True)
class OrthogonalWindowCapabilityReport:
    facade: OrthogonalWindowReport
    vertical_check: GeometryCheck
    sill_mm: float
    height_mm: float

    @property
    def windows(self):
        return self.facade.windows

    @property
    def source(self):
        return dict(self.facade.source)

    @property
    def checks(self):
        return (*self.facade.checks, self.vertical_check)

    @property
    def status(self):
        statuses = {check.status for check in self.checks}
        return "FAIL" if "FAIL" in statuses else "UNKNOWN" if "UNKNOWN" in statuses else "PASS"

    @property
    def ok(self):
        return self.status == "PASS"

    def to_dict(self):
        return {"status": self.status, "ok": self.ok,
            "scope": "Source facade lines and coordination representation eligibility only; not engineering or daylight acceptance",
            "source": self.source, "facade": self.facade.to_dict(),
            "policy": {"name": ORTHOGONAL_WINDOW_CAPABILITY, "height_policy": "coordination_proxy_v1",
                "coordination_wall_height_mm": ORTHOGONAL_COORDINATION_WALL_HEIGHT_MM,
                "source_sill_field": "LayoutIR.window_sill_mm", "source_height_field": "LayoutIR.window_height_mm",
                "closed_facade_wall_band": True, "windows_are_circulation_portals": False},
            "vertical_range_mm": {"sill": self.sill_mm, "height": self.height_mm, "head": self.sill_mm + self.height_mm},
            "checks": [{"id": item.id, "status": item.status, "evidence": list(item.evidence)} for item in self.checks],
            "proven_building_infeasible": False,
            "full_facility_validation": {"status": "UNKNOWN", "ok": False}, "engineering_acceptance": "PENDING"}


class OrthogonalWindowCapabilityError(ProjectionCapabilityError):
    """Reject window projection with its fresh FAIL or UNKNOWN source witness."""

    def __init__(self, report: OrthogonalWindowCapabilityReport):
        self.report = report
        failures = [evidence for check in report.checks if check.status != "PASS" for evidence in check.evidence]
        super().__init__("windows", "Orthogonal window capability is " + report.status + ": " + "; ".join(failures))

    def to_dict(self):
        return {"status": self.report.status, "code": "ORTHOGONAL_WINDOW_CAPABILITY",
                "component": self.component, "message": str(self), "evidence": self.report.to_dict()}


def validate_orthogonal_window_capability(spec: LayoutIR, layout: LayoutResult) -> OrthogonalWindowCapabilityReport:
    """Rebuild bounded B13 facade lines and check source sill/head eligibility.

    A window above the renderer's existing wall proxy stays UNKNOWN. It does
    not prove that the source building is physically infeasible. Sources with
    no explicit or daylight windows do not use the vertical window settings.
    """
    facade = project_orthogonal_windows(spec, layout)
    required = bool(spec.windows) or any(room.needs_daylight for room in spec.rooms)
    head = spec.window_sill_mm + spec.window_height_mm
    if required and head > ORTHOGONAL_COORDINATION_WALL_HEIGHT_MM:
        vertical = GeometryCheck("WINDOW_VERTICAL_CAPABILITY", "UNKNOWN",
            (f"Source window sill {spec.window_sill_mm:g} mm plus height {spec.window_height_mm:g} mm gives head {head:g} mm",
             "Source window range exceeds the existing 2800 mm coordination wall proxy; another height capability is required",
             "Representation eligibility only; no structural, daylight or whole-building infeasibility conclusion"))
    else:
        evidence = "Source window sill/head fits the existing 2800 mm coordination wall proxy" if required else "No declared windows require vertical window projection"
        vertical = GeometryCheck("WINDOW_VERTICAL_CAPABILITY", "PASS", (evidence,))
    return OrthogonalWindowCapabilityReport(facade, vertical, spec.window_sill_mm, spec.window_height_mm)

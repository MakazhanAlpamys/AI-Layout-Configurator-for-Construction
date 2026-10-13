"""Bounded source reference axes for explicit orthogonal draft delivery."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math

from .building import StructuralGridSpec
from .models import LayoutIR
from .orthogonal_projection import ProjectionCapabilityError
from .schema import normalize_mapping


MAX_GRID_AXES = 128
MAX_GRID_LABEL_BYTES = 128
MAX_GRID_SOURCE_BYTES = 1024 * 1024
MAX_GRID_EXTENT_MM = 1_000_000


@dataclass(frozen=True)
class OrthogonalGridAxis:
    id: str
    direction: str
    label: str
    coordinate_mm: float
    start: tuple[float, float]
    end: tuple[float, float]

    def to_dict(self):
        return {'id': self.id, 'direction': self.direction, 'label': self.label,
                'coordinate_mm': self.coordinate_mm, 'start': list(self.start), 'end': list(self.end)}


@dataclass(frozen=True)
class OrthogonalGridReport:
    status: str
    axes: tuple[OrthogonalGridAxis, ...] = ()
    spec_sha256: str | None = None
    structural_grid_sha256: str | None = None
    issues: tuple[str, ...] = ()

    @property
    def ok(self):
        return self.status == 'PASS'

    def to_dict(self):
        return {'status': self.status, 'ok': self.ok,
                'scope': 'Declared boundary-spanning reference axes only; not structural engineering or room alignment',
                'policy': {'name': 'boundary_spanning_axes_v1', 'units': 'mm',
                           'extent': 'Full source boundary width or height at elevation zero',
                           'cutout_clipping': False, 'room_alignment_enforced': False,
                           'axes_are_circulation_barriers': False},
                'source': {'spec_sha256': self.spec_sha256,
                           'structural_grid_sha256': self.structural_grid_sha256},
                'axes': [axis.to_dict() for axis in self.axes],
                'limits': {'total_axes': MAX_GRID_AXES, 'label_utf8_bytes': MAX_GRID_LABEL_BYTES,
                           'total_source_bytes': MAX_GRID_SOURCE_BYTES,
                           'absolute_coordinate_or_dimension_mm': MAX_GRID_EXTENT_MM},
                'issues': list(self.issues), 'proven_building_infeasible': False,
                'full_facility_validation': {'status': 'UNKNOWN', 'ok': False},
                'engineering_acceptance': 'PENDING'}


class OrthogonalGridCapabilityError(ProjectionCapabilityError):
    def __init__(self, report: OrthogonalGridReport):
        self.report = report
        super().__init__('structural_grid', 'Orthogonal grid capability is ' + report.status + ': ' + '; '.join(report.issues))

    def to_dict(self):
        return {'status': self.report.status, 'code': 'ORTHOGONAL_GRID_CAPABILITY',
                'component': self.component, 'message': str(self), 'evidence': self.report.to_dict()}


def _number(value, *, positive=False):
    # Check integers before conversion: math.isfinite(10**400) raises OverflowError.
    if (type(value) not in (int, float) or abs(value) > MAX_GRID_EXTENT_MM
            or type(value) is float and not math.isfinite(value)
            or value < 0 or positive and value == 0):
        raise ValueError('Grid coordinates require actual bounded finite non-negative numbers')
    return value


def _source_bytes(value):
    encoder = json.JSONEncoder(sort_keys=True, separators=(',', ':'),
                               ensure_ascii=False, allow_nan=False)
    chunks, size = [], 0
    for part in encoder.iterencode(value):
        encoded = part.encode('utf-8')
        size += len(encoded)
        if size > MAX_GRID_SOURCE_BYTES:
            raise ValueError('Grid canonical source byte budget exceeded')
        chunks.append(encoded)
    return b''.join(chunks)


def validate_orthogonal_grid(spec: LayoutIR, structural_grid: StructuralGridSpec | None) -> OrthogonalGridReport:
    """Resolve source axes without coercing, clipping, snapping or solving rooms."""
    try:
        if not isinstance(spec, LayoutIR) or (structural_grid is not None
                                             and not isinstance(structural_grid, StructuralGridSpec)):
            raise ValueError('Grid capability requires the canonical LayoutIR and StructuralGridSpec')
        if structural_grid is not None and len(structural_grid.axes_x_mm) + len(structural_grid.axes_y_mm) > MAX_GRID_AXES:
            raise ValueError('Grid source axis budget exceeded')
        raw_spec, raw_grid = spec.to_dict(), structural_grid.to_dict() if structural_grid is not None else None
        spec_bytes, grid_bytes = _source_bytes(raw_spec), _source_bytes(raw_grid)
        if len(spec_bytes) + len(grid_bytes) > MAX_GRID_SOURCE_BYTES:
            raise ValueError('Grid canonical source byte budget exceeded')
        width = _number(spec.boundary.width_mm, positive=True)
        height = _number(spec.boundary.height_mm, positive=True)
        normalize_mapping(raw_spec)
        axes = []
        if structural_grid is not None:
            for direction, coordinates, labels, maximum in (
                    ('x', structural_grid.axes_x_mm, structural_grid.labels_x, width),
                    ('y', structural_grid.axes_y_mm, structural_grid.labels_y, height)):
                if not isinstance(coordinates, tuple) or not coordinates or not isinstance(labels, tuple):
                    raise ValueError('Grid axes and labels require canonical tuples and both axis directions')
                if labels and len(labels) != len(coordinates):
                    raise ValueError('Grid labels must match the exact source axis count')
                if any(type(label) is not str or not label.strip()
                       or len(label.encode('utf-8')) > MAX_GRID_LABEL_BYTES
                       or any(ord(character) < 32 or ord(character) == 127 for character in label)
                       for label in labels):
                    raise ValueError('Grid labels require bounded nonempty visible text')
                if len(set(labels)) != len(labels):
                    raise ValueError('Grid labels must be unique within each axis direction')
                previous = None
                for index, coordinate in enumerate(coordinates, 1):
                    _number(coordinate)
                    if coordinate > maximum or previous is not None and coordinate <= previous:
                        raise ValueError('Grid axes must strictly increase within the source boundary')
                    previous = coordinate
                    start = (coordinate, 0) if direction == 'x' else (0, coordinate)
                    end = (coordinate, height) if direction == 'x' else (width, coordinate)
                    axes.append(OrthogonalGridAxis(f'grid:{direction}:{index:04d}', direction,
                                                  labels[index - 1] if labels else str(index), coordinate, start, end))
        return OrthogonalGridReport('PASS', tuple(axes), hashlib.sha256(spec_bytes).hexdigest(),
                                    hashlib.sha256(grid_bytes).hexdigest())
    except (ValueError, TypeError, AttributeError, OverflowError) as exc:
        return OrthogonalGridReport('FAIL', issues=(str(exc)[:1000],))

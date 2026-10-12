"""Source-derived editable dimension chains and scoped actual-artifact readback."""

from dataclasses import dataclass
import hashlib
import json
import math

from .result_geometry import require_rectangular_layout


DIMENSION_PADDING_MM = 3000
DIMENSION_MIN_PADDING_PT = 64
DIMENSION_SCOPE = 'Dimension chains: projected room edges, structural axes and overall boundary'


@dataclass(frozen=True)
class DimensionSegment:
    dimension_id: str
    axis: str
    kind: str
    start_mm: float
    end_mm: float
    offset_mm: float
    sources: tuple[str, ...]

    @property
    def measurement_mm(self):
        return self.end_mm - self.start_mm

    @property
    def points(self):
        if self.axis == 'x':
            return (self.start_mm, 0), (self.end_mm, 0), (self.start_mm, -self.offset_mm)
        return (0, self.start_mm), (0, self.end_mm), (-self.offset_mm, self.start_mm)

    @property
    def source_sha256(self):
        return hashlib.sha256(json.dumps(self.sources, ensure_ascii=True).encode()).hexdigest()


def _finite(value):
    try:
        return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
    except OverflowError:
        return False


def build_dimension_chains(spec, result, structural_grid=None):
    """Project ordered source coordinate stations; no continuous facade is inferred."""
    require_rectangular_layout(result, 'editable dimension chains')
    if set(result.placements) != {room.id for room in spec.rooms}:
        raise ValueError('Dimension sources must match the complete room program')
    extents = {'x': spec.boundary.width_mm, 'y': spec.boundary.height_mm}
    if any(not _finite(value) or value <= 0 for value in extents.values()):
        raise ValueError('Dimension boundary requires finite positive extents')
    room_edges = {axis: {0: {'boundary:start'}, end: {'boundary:end'}} for axis, end in extents.items()}
    for room_id, rect in sorted(result.placements.items()):
        if (not all(_finite(value) for value in (rect.x, rect.y, rect.width, rect.height))
                or min(rect.width, rect.height) <= 0 or rect.x < 0 or rect.y < 0
                or rect.right > extents['x'] or rect.top > extents['y']):
            raise ValueError('Dimension room geometry must be finite and inside the source boundary')
        for axis, edges in (('x', (rect.x, rect.right)), ('y', (rect.y, rect.top))):
            for side, value in zip(('start', 'end'), edges):
                room_edges[axis].setdefault(value, set()).add(f'room:{room_id}:{side}')
    groups = [('room_edges', room_edges, 900)]
    if structural_grid is not None:
        stations = {}
        for axis in ('x', 'y'):
            values = getattr(structural_grid, f'axes_{axis}_mm')
            labels = getattr(structural_grid, f'labels_{axis}')
            stations[axis] = {}
            for index, value in enumerate(values):
                if not _finite(value) or not 0 <= value <= extents[axis]:
                    raise ValueError('Dimension axes must be finite source-boundary coordinates')
                label = labels[index] if labels else f'{axis.upper()}{index + 1}'
                stations[axis].setdefault(value, set()).add('axis:' + label)
        groups.append(('structural_grid', stations, 1700))
    groups.append(('overall', {axis: {0: {'boundary:start'}, end: {'boundary:end'}}
                               for axis, end in extents.items()}, 2500))
    chains = []
    for kind, axes, offset in groups:
        for axis, stations in axes.items():
            coordinates = sorted(stations)
            for start, end in zip(coordinates, coordinates[1:]):
                sources = tuple(sorted(stations[start] | stations[end]))
                identity = json.dumps([axis, kind, float(start), float(end), sources], ensure_ascii=True)
                identifier = 'DIM_' + hashlib.sha256(identity.encode()).hexdigest()[:20]
                chains.append(DimensionSegment(identifier, axis, kind, start, end, offset, sources))
    if len(chains) > 1000:
        raise ValueError('Dimension projection is bounded to 1000 source segments')
    return tuple(chains)


def draw_dimension_chains_dxf(document, modelspace, chains):
    if 'FLC_DIM' not in document.appids:
        document.appids.add('FLC_DIM')
    for chain in chains:
        first, second, base = chain.points
        dimension = modelspace.add_linear_dim(base=base, p1=first, p2=second,
            angle=0 if chain.axis == 'x' else 90, text='<>',
            override={'dimtxt': 90, 'dimasz': 90, 'dimexo': 120, 'dimexe': 100,
                      'dimgap': 45, 'dimlfac': 1, 'dimdec': 6, 'dimzin': 8},
            dxfattribs={'layer': 'A-DIMS'})
        dimension.dimension.set_xdata('FLC_DIM', [(1000, 'dimension_id=' + chain.dimension_id),
            (1000, 'axis=' + chain.axis), (1000, 'kind=' + chain.kind),
            (1000, 'source_sha256=' + chain.source_sha256)])
        dimension.render()


def dimension_chain_schedule(chains):
    rows = [DIMENSION_SCOPE + '; millimetres; source projection only.']
    for kind in ('room_edges', 'structural_grid', 'overall'):
        for axis in ('x', 'y'):
            selected = [chain for chain in chains if chain.axis == axis and chain.kind == kind]
            if selected:
                rows.append(f'{axis.upper()} {kind}: ' + '; '.join(
                    f'{chain.start_mm:.12g} to {chain.end_mm:.12g} = {chain.measurement_mm:.12g} mm'
                    for chain in selected))
    return rows


def draw_dimension_chains_pdf(pdf, point, chains, font='Helvetica'):
    """Draw true scaled ticks; dense values remain in the complete source schedule."""
    pdf.saveState()
    pdf.setStrokeColorRGB(.25, .3, .35)
    pdf.setFillColorRGB(.25, .3, .35)
    pdf.setLineWidth(.35)
    pdf.setFont(font, 5)
    unit = abs(point(1, 0)[0] - point(0, 0)[0])
    for chain in chains:
        first, second, base = chain.points
        start = point(first[0], base[1]) if chain.axis == 'x' else point(base[0], first[1])
        end = point(second[0], base[1]) if chain.axis == 'x' else point(base[0], second[1])
        lane = ('room_edges', 'structural_grid', 'overall').index(chain.kind) + 1
        # Paper-space spacing keeps chains clear of the 10 pt axis-label gutter.
        gutter = max(chain.offset_mm * unit, lane * 20)
        if chain.axis == 'x':
            start, end = (start[0], point(0, 0)[1] - gutter), (end[0], point(0, 0)[1] - gutter)
        else:
            start, end = (point(0, 0)[0] - gutter, start[1]), (point(0, 0)[0] - gutter, end[1])
        pdf.line(*point(*first), *start)
        pdf.line(*point(*second), *end)
        pdf.line(*start, *end)
        for x, y in (start, end):
            pdf.line(x - 2, y - 2, x + 2, y + 2)
        text = f'{chain.measurement_mm:.12g}'
        span = abs(end[0] - start[0]) if chain.axis == 'x' else abs(end[1] - start[1])
        if pdf.stringWidth(text, font, 5) + 4 <= span:
            if chain.axis == 'x':
                pdf.drawCentredString((start[0] + end[0]) / 2, start[1] + 4, text)
            else:
                pdf.saveState()
                pdf.translate(start[0] - 4, (start[1] + end[1]) / 2)
                pdf.rotate(90)
                pdf.drawCentredString(0, 0, text)
                pdf.restoreState()
    pdf.restoreState()


def validate_dimension_chains_dxf(path, spec, result, structural_grid=None):
    """Read real definitions, units, native measurement and source identity."""
    import ezdxf

    issues = []
    try:
        expected = {chain.dimension_id: chain for chain in build_dimension_chains(spec, result, structural_grid)}
        document = ezdxf.readfile(path)
        if document.header.get('$INSUNITS') != 4:
            issues.append('Dimension drawing does not declare millimetres')
        seen = set()
        for entity in document.modelspace().query('DIMENSION[layer=="A-DIMS"]'):
            identity = dict(tag.value.split('=', 1) for tag in entity.get_xdata('FLC_DIM') if tag.code == 1000)
            identifier = identity.get('dimension_id')
            if identifier not in expected or identifier in seen:
                issues.append('Unknown or duplicate dimension source identity')
                continue
            seen.add(identifier)
            chain = expected[identifier]
            first, second, base = chain.points
            if identity != {'dimension_id': identifier, 'axis': chain.axis, 'kind': chain.kind,
                            'source_sha256': chain.source_sha256}:
                issues.append('Dimension metadata differs from source')
            pairs = ((entity.dxf.defpoint2, first), (entity.dxf.defpoint3, second),
                     (entity.dxf.defpoint, base))
            if any(not all(math.isclose(actual[index], target[index], rel_tol=0, abs_tol=1e-6)
                           for index in (0, 1)) or abs(actual.z) > 1e-6 for actual, target in pairs):
                issues.append('Dimension definition coordinates differ from source')
            if (entity.dxf.text != '<>' or entity.dxf.geometry not in document.blocks
                    or not math.isclose(float(entity.get_measurement()), chain.measurement_mm, rel_tol=0, abs_tol=1e-6)
                    or not math.isclose(entity.dxf.angle, 0 if chain.axis == 'x' else 90, rel_tol=0, abs_tol=1e-6)):
                issues.append('Dimension native measurement, text or rendered block differs')
            style = document.dimstyles.get(entity.dxf.dimstyle)
            overrides = entity.get_acad_dstyle(style)
            if not math.isclose(float(overrides.get('dimlfac', style.dxf.dimlfac)), 1, rel_tol=0, abs_tol=1e-12):
                issues.append('Dimension style changes the source millimetre measurement factor')
        if seen != set(expected):
            issues.append('Dimension source inventory is incomplete')
    except Exception as exc:
        issues.append(f'Dimension readback failed: {type(exc).__name__}: {exc}')
    return {'status': 'FAIL' if issues else 'PASS', 'ok': not issues,
            'scope': 'Source dimension projection and native DXF readback only; not engineering approval',
            'issues': issues}

# Supplied Equipment Installation Paths

Checkpoint I.4.2 checks an explicitly supplied, source-bound whole-equipment
path from a declared external entry to its actual installed position. It is
an independent bounded 2D review; it does not generate a path or change the
ordinary facility producer.

```python
from layout_configurator.equipment_installation_path import (
    validate_equipment_installation_path,
)

report = validate_equipment_installation_path(
    request,
    building=building,
    layout=layout,
    equipment=equipment,
    library=library,
    card_by_equipment=card_by_equipment,
    actions_by_scenario=actions_by_scenario,
)
```

The closed version `0.1` request names the path, equipment, card and actual
external entry. `source` supplies exact whole canonical building, layout,
equipment-result and library SHA-256 identities. `poses` is an ordered list
of `{x_mm, y_mm, rotated}` records identifying the oriented body's lower-left
world coordinates. The final pose must equal the actual installed placement.
All translations use that one permitted installed orientation; changing
orientation or diagonal motion remains unsupported and UNKNOWN.

Each supported axis segment sweeps the complete physical body. Piecewise
turns use the union of those segment rectangles, rather than the bounding
rectangle of the whole path. Supplied fixed-orientation positions retain
independent known collision evidence even when motion between them is unknown.
Original installed body, host, anchor and directional-clearance predicates
remain required. An I.3 stationary installation reservation is not swept as
an invented transport envelope.

Optional `transport_clearance` is a separate explicit source-local rectangle.
Optional `exterior_staging` supplies a literal floor, obstacle list and
`supplied_2d_only` inventory scope. Each evidence record includes a library
source ID, exact original source-byte SHA-256, structured JSON pointer and
the exact source value, including original numeric scalar types. Missing
exterior coverage yields UNKNOWN; independently known collisions remain FAIL.
Actual source authentication and real-world obstacle completeness are UNKNOWN.

Wall checks retain original contours and thickness. A detached conservative
material projection cuts only explicit source doors and the declared external
entry; window bands and inferred adjacency apertures remain closed. Complete
crossing and transverse envelope fit are checked against actual resolved
opening geometry. Windows and an unrecorded removable panel cannot authorize
passage.

Physical neighboring bodies always remain obstacles. Their supplied source-bound
operating reservations remain active by default. Only a neighbor's own bound
`production_stopped` action in the installation/replacement scenario can
conditionally suppress its operating reservation; it cannot remove its body.
A temporary action's description has no geometric effect. Required target
installation premises remain explicit and execution verification is UNKNOWN.

Admission bounds all original sources before geometry: 64 rooms, 32 equipment
items, 128 poses, 64 library sources, 256 KiB per source, one MiB combined
source content, four MiB aggregate UTF-8, 131,072 nodes, depth 24, 4,096
vertices, 256 staging obstacles and 131,072 geometry checks. Nested source
lexical work is charged across the whole input before any nested decoding.
Limits only tighten. Lost positive extents at world-coordinate precision
remain UNKNOWN; bounded finding delivery retains known failures.

`local_2d_path_status` can be PASS for complete checked supplied geometry and
declared premises. Overall status remains UNKNOWN unless a known conflict
makes it FAIL; `.ok` is always false. Full scenario validation, transport
equipment, lifting, disassembly, changing orientation, three-dimensional
feasibility and source authentication remain UNKNOWN. Engineering acceptance
is PENDING. No whole-building infeasibility or complete Cycle I acceptance
follows from this local check.

Fresh root verification passes 23 tests and 21 subtests in a single-file run
(3.00 s). Actual regressions first exposed unsupported-motion collision loss
and a positive narrow-entry subreport, then passed after repair. A regular
installed wheel from `95d363a` plus the two frozen new files reproduces the
clear and permitted 90-degree cases (local PASS, overall UNKNOWN) and an actual
700,000 mm2 swept neighbor collision (FAIL) outside the checkout. Sources stay
unchanged. Evidence is retained locally under
`out/checkpoints/equipment-installation-path-i42/root-final/`.

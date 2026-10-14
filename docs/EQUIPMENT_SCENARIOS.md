# Independent Local Equipment Scenario Checks

Checkpoint I.4.2 separately checks explicitly supplied whole-body 2D paths
from external access. [Path contract](EQUIPMENT_INSTALLATION_PATHS.md).
These stationary scenario reports retain their own scope; complete path/3D
integration and engineering acceptance remain separate.

Checkpoint I.4.1 adds
`validate_equipment_scenarios(building, layout, equipment, library=...,
card_by_equipment=..., maintenance_targets=..., installation_targets=...,
actions_by_scenario=...)`. Inputs retain original canonical equipment, actual
placements and the [source-bound finite library](ENGINEERING_LIBRARY.md).

Each invocation freshly validates library binding and unchanged canonical
body, directional clearance, host, anchor and wall predicates. It independently
transforms literal contours and named points rather than trusting a projection
verdict. Exact concave geometry is preserved. Failed library binding excludes
library contours; independently known canonical body collisions remain FAIL.

All three local scenarios retain every placed physical body. Production uses
operating reservations; maintenance and installation/replacement require
explicit targets and their corresponding envelopes. Neighbors continue
production by default. A selected source-bound stop may conditionally suppress
only its own non-target operating reservation: its physical body, named points
and other geometry remain. Textual temporary actions remove no obstacles.
Actual action execution remains UNKNOWN.

Checks cover complete host containment, positive-area overlap between different
equipment owners, service/panel/tool obstruction, original B3 wall material and
named points strictly inside another body or wall. Same-owner envelopes may
overlap. Point checks invent no access radius; boundary contact does not become
an invented clearance violation. Supplied reservations cannot pass after their
positive extent is lost at supported world-coordinate precision: unrepresentable
canonical geometry or transformed contours remain UNKNOWN.

Source-bound known conflicts survive unrelated missing dimensions, actions or
rights. Unavailable geometry, unsupported applicability and incomplete targets
remain UNKNOWN. Reports separate `local_geometry_status` and
`declared_premises_status`; overall complete scenarios, external installation
paths, 3D feasibility and authentication remain UNKNOWN. `.ok` is always false
for this scope; engineering acceptance remains PENDING. This checker does not
alter existing equipment packing, routes or source admission.

Default limits bound32 equipment items,64 rooms,1,024 source objects,4 MiB
combined JSON/output,131,072 nodes, depth32,16,384 vertices,131,072 predicate
pairs and256 retained findings per inventory. Original raw objects and nested
source JSON are bounded before parsing, serialization or GEOS. Limits only
tighten. Detailed finding/output overflow is explicitly INCOMPLETE, preserves
known FAIL and downgrades unsupported complete delivery to UNKNOWN; physical
predicates still execute within admitted bounds.

On2026-10-14, a single-file fresh run passed24 tests/nine subtests in10.29 s.
Two root regressions first exposed world-precision false PASS/unbounded geometry
entry and then passed. Root independently checks a clear local witness, original
wall obstruction inside a valid room (panel30,000 and service75,000 mm²),
invalid-card exclusion with canonical body collision400,000 mm², and
source-bound precision UNKNOWN. Sources remain unchanged. A clean regular wheel
from `d96b029` plus two reviewed files repeats clear-local/full-UNKNOWN and
actual wall FAIL from a foreign working directory using exact module hashes;
wheel SHA-256 is
`10f8ec1e0d7ca0dd0449a9f17ea8b37fadaab164ceaa0cd2e0052aee29ef4c24`.
Detailed local evidence is in `out/checkpoints/equipment-scenarios-i41/`.
Full transport paths and synchronized documentation remain subsequent work.

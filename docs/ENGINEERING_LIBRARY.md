# Source-bound Engineering Library

Checkpoint I.3.1 supplies a detached finite equipment library and an independent
literal 2D projector. It does not authenticate sources, grant usage rights,
approve engineering, or prove a complete operating or maintenance scenario.

`validate_engineering_library(raw)` validates the closed offline
[schema](../schemas/engineering_library.schema.json), exact UTF-8 source hashes,
structured JSON pointers, scalar types and units, source equipment identities,
card revisions, source geometry and explicit applicability. Rehashing a changed
card or source does not make inconsistent dimensions or semantic roles valid.
Declared confirmation must refer to the exact claim source identity, even when
two sources have identical bytes and hashes. Authentication remains UNKNOWN.

Each card explicitly carries width, depth, height, four clearances, six envelope
roles (body, operating, service, panel, tool removal, installation), interaction
points, scenario actions and applicability. Missing values remain UNKNOWN.
Curves, 3D geometry, visual references, assumptions and additional applicability
predicates do not establish supported reusable geometry. Rights are checked as
supplied declarations; restricted or unknown rights prevent reusable projection.

`instantiate_equipment_template` selects one explicitly supplied finite variant
at an exact parameter vector within sourced bounds. It does not evaluate code,
interpolate dimensions, or derive measurements from text, images or drawings.
Numerically equivalent duplicate parameter vectors are ambiguous and rejected.

`project_equipment_card(library, card_id, equipment=..., placement=...,
scenario=..., active_action_ids=...)` freshly validates the original library and
exact original equipment/placement. It preserves literal concave rings, named
ports, action premises and source references. A permitted 90-degree turn uses
the existing equipment convention `(depth - y, x)` before translation. It cannot
invent a port or replace a concave contour with its bounding rectangle.

Production, maintenance and installation/replacement select only their declared
envelopes and actions. A required stop or temporary action must be explicitly
selected; its actual execution remains UNKNOWN. The projector returns DRAFT
with scoped `local_projection_status: PASS`. Host containment, collisions,
scenario feasibility and 3D transport remain UNKNOWN; engineering acceptance
remains PENDING. Existing equipment placement and routing are unchanged.

Default limits bound 64 sources, 128 cards, 64 templates, 256 KiB per source,
1 MiB total source text, 2 MiB whole JSON, 65,536 nodes, depth 24 and 4,096
geometry points. Exact raw scalar/type/cycle/byte checks precede serialization
and geometry; detached and nested source JSON nesting/node checks precede JSON
parsing. Limits can only be tightened.

The [example](../examples/engineering_library_reconstruction.json) is an explicit
project-authored reconstruction of the canonical pharma pilot's
`production_line_01`, with unknown height. It is not a manufacturer card or an
accepted engineering design.

Verification on 2026-10-13: 45 related tests passed in 1.920 s. Three additional
root regression methods first failed with six assertions and then passed for
confirmation identity and raw parsing bounds. Independent actual pilot checks
verify every translated contour/point in all three scenarios and reject changed
source dimensions. A regular wheel built from `1d3c7bb` plus six reviewed files
repeats all three projections and the source negative from a foreign working
directory using its packaged offline schema; wheel SHA-256 is
`8ce8a5e84287c353b2f81f3a01b783c6a4d545555ec21f4632393aefa47b0406`.
Local detailed evidence is under `out/checkpoints/engineering-library-i31/`.
Full scenario checks and producer integration remain subsequent I.4 work.

I.4.1 now supplies [independent local scenario checks](EQUIPMENT_SCENARIOS.md)
for actual supplied placements; this does not complete external installation
paths,3D or whole-scenario engineering acceptance.

I.3.1.1 freshly checks transformed contours for valid positive area before
declaring local projection PASS. A positive source extent that cannot survive
the supported world-coordinate precision returns `unrepresentable_geometry`
with UNKNOWN and the fresh library binding report. This does not mark valid
supplied source numbers mathematically inconsistent or prove physical safety.
On2026-10-14, a real regression first failed and a single-file run then passed
ten tests/15 subtests in1.42 s. All three ordinary pilot projections remain
valid; a source-bound numerical negative remains UNKNOWN. A regular wheel
from `f5a01d5` plus the two reviewed files repeats normal PASS/numerical UNKNOWN
from a foreign working directory with exact module hashes; wheel SHA-256 is
`76b374faaf1d4b9b6fec48dab860f844d636b69ad0dabc4014967a1b73f87e33`.
Local detailed evidence is in `out/checkpoints/equipment-card-precision-i311/`.

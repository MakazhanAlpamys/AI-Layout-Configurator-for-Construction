# Source-bound Environment and Pressure Review

Checkpoint I.2.2 supplies a separate versioned
[environment contract](../schemas/environment_program.schema.json), bound to
the exact original BuildingIR, production program, transfer program and current
layout. It annotates supported planar geometry without admitting unsupported
production semantics to the existing generator.

`validate_environment_program(raw, building=..., production_program=...,
transfer_program=...)` checks original hashes, typed identities, independent
clean/ventilation memberships, three explicit operating scenarios, source
references and the supplied pressure graph. Clean regions retain independent
ISO class, GMP grade, state and applicability, including explicit UNKNOWN.
No ISO/GMP mapping, legal threshold or functional-parent pressure connection is
inferred. Stop/action premises identify their original operations, equipment
and transfers; their execution remains UNKNOWN.

Pressure nodes explicitly name a room, clean region or ventilation region and
a supplied common datum. Edges name an original typed transfer, scenario and
minimum/optional maximum differential in Pa. Design values and supplied measured
claims remain separate. Unknown observations can have null source/excerpt and
retain their units. Known claims require their source references. Supplied
numeric observations are not authenticated measurements or built performance.

The declared graph uses exact rational difference constraints derived from
canonical decimal tokens. A contradictory cycle returns source/edge/scenario
identities and exact rational weights. This FAIL survives unrelated missing
measurements or rights and establishes inconsistency only of those declarations.
An empty graph is UNKNOWN; logical consistency alone is not network acceptance.

`validate_environment_layout(building, layout, routing, raw,
production_program=..., transfer_program=..., equipment=...)` freshly checks
original room/equipment/route/transfer predicates. Full literal region contours
must fit the union of declared host rooms. Local equipment and operation
memberships need actual contained bodies or location evidence; a missing or
straddled interface remains UNKNOWN. The complete fresh source opening segment
must lie on both pressure-space boundaries. A distant room opening cannot prove
a local clean-region connection.

Declared and resolved graphs are reported independently. Unavailable portals
cannot establish complete resolved connectivity or hide a declared contradiction.
Known supplied numerical violations remain FAIL when another observation is
missing. Measurements on different datums are incomparable and remain UNKNOWN.
HVAC, action execution, authentication and full facility validation remain
UNKNOWN; engineering acceptance remains PENDING.

Default limits bound64 clean regions,64 ventilation regions, three scenarios,
128 actions,32 datums,128 pressure nodes,256 edges,256 vertices per region,
8,192 total region vertices and1,000,000 pressure relaxations, alongside the
typed transfer source/route/byte limits. Combined raw source and geometry-work
admission precedes constructors, serialization and GEOS. Detached JSON checks
bytes, depth, nodes and duplicate keys before parsing. Limits only tighten.

The [self-contained example](../examples/environment_program_connected_pressure.json)
is a labeled reconstruction with three rooms and three explicit doors. Its local
ISO8 claim is independent of unknown GMP classification; design30/20/10 Pa is
supplied numerical data with unknown measurements and HVAC evidence.

Verification on2026-10-13: fresh root79 related tests passed in3.887 s; the
broader agent scoped run passed163 tests/165 subtests in21.15 s. Real regression
failures cover graph cycles, tiny1e-300 rational differences, bounded detached
JSON, missing observations and memberships. Root independently checks all three
literal door segments, a cycle with rational weight sum-30 Pa and a local-region
boundary negative while preserving original objects. A regular wheel built from
`19ac3c7` plus six frozen files repeats consistent UNKNOWN and cyclic FAIL using
its offline packaged schema from a foreign working directory; wheel SHA-256 is
`f5e1d56414529a2a7d99a2ea2536037d39c233e20dbc601389b3edbcb3374a33`.
Detailed local evidence is in `out/checkpoints/environment-program-i22/`.
CLI, scenario computation and synchronized documentation remain separate steps.

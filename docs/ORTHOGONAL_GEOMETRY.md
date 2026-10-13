# Orthogonal geometry foundation

`OrthogonalPolygon` in `layout_configurator.orthogonal` represents one connected,
simple, axis-aligned ring in millimetres. It rejects holes, disconnected parts,
diagonal edges, self-intersections and nonfinite coordinates. It normalizes
orientation and starting vertex, removes redundant collinear points, and never
snaps or repairs invalid input. The input limit is 256 vertices.

```python
from layout_configurator.orthogonal import OrthogonalPolygon

room = OrthogonalPolygon(((0, 0), (4000, 0), (4000, 1000),
                          (1000, 1000), (1000, 4000), (0, 4000)))
assert room.area_m2 == 7
assert not room.contains_point((2000, 2000))
rectangles = room.decompose()
```

`bounds_rect` is a derived index. Area, containment, rectangle clearance,
intersection and shared edges use the actual footprint. `decompose()` returns
an exact disjoint horizontal-slab partition; it does not minimize rectangle
count. Translation and quarter-turn rotation preserve the ring.

`inset_components(distance)` returns every surviving component, including a
split at a narrow neck. Its mitre offset implements an axis-aligned clearance
policy. It is not a regulatory clearance verdict or a round-corner Euclidean
offset. Predicates use floating-point GEOS geometry.

Result JSON can contain both `Rect` and `OrthogonalPolygon`. Polygon results
declare `geometry_version: '0.2'` and `units: 'mm'`, with typed vertex arrays;
an all-rectangle result retains the legacy JSON representation. Loaders preserve
the original footprint and reject unknown fields, versions, units, duplicate
keys, invalid coordinates and incorrect derived areas.

`validate_result_geometry(spec, result)` reports exact room identity, area,
boundary/cutout coverage and overlap evidence. Its overall acceptance remains
`UNKNOWN` or `FAIL`: minimum dimensions, opening topology and full connectivity
are still unevaluated. It cannot issue a full layout PASS.

Full rectangular consumers use `require_rectangular_layout` before work. A
polygon currently raises `UnsupportedGeometryError` with structured `UNKNOWN`
capability evidence at walls/openings, equipment, routes, rules, ranking, viewer
and DXF/PDF/IFC export boundaries. Solver selection and polygon consumer
integration remain separate steps; rectangular generation remains supported.

## Exact DRAFT wall and DXF projection

`build_orthogonal_wall_plan(spec, result)` and
`export_orthogonal_draft_dxf(path, spec, result)` provide a separate bounded
polygon consumer. Walls follow actual orthogonal edges; Boolean differences
cut openings on real shared boundaries. Automatic required-adjacency doors
must fit one continuous segment, never a sum of disconnected contacts. An
explicit shared/exterior opening is supported only when its side/offset selects
one unambiguous actual segment. Window/daylight generation and ambiguous manual
selectors return structured UNKNOWN capability evidence before writing output.

The semantic DXF preserves closed room rings, true areas, stable room/door IDs,
millimetre units and hashes of canonical spec/result and delivered artifact.
Concave-room labels use an interior slab anchor. Invalid source contracts,
area/overlap/boundary failures and forbidden contacts are rejected before output.
The artifact is always **DRAFT**, with `ok: false`: usable room dimensions,
full circulation and equipment clearance still need their own checks. Complete
exporters keep their existing capability guards. Rectangular plan import rejects
these polygon outlines rather than replacing them with bounding rectangles.

## Independent room and equipment predicates

`validate_orthogonal_layout(spec, result)` adds actual minimum dimensions,
wall-inset connectivity and a graph built from the generated usable doors.
The project policy `axis_aligned_coverage_v1` requires every footprint point
to be covered by a contained axis-aligned rectangle meeting the source room's
minimum width and depth. A rotatable room chooses one orientation for its
whole footprint. This explicit geometry policy is not a regulatory width rule.
It accepts qualifying L-shaped arms and rejects thin spurs or narrow necks
even when their bounding box is large. Scan/candidate exhaustion yields UNKNOWN.

The usable footprint must remain one nonempty component after the wall-half
mitre inset. Actual doors must reach that component across their full width;
shared contact alone does not establish circulation. Unsupported window or
ambiguous opening selectors retain UNKNOWN evidence.

`validate_orthogonal_equipment(building, layout, equipment)` independently
checks supplied equipment identities, hosts, orientation and directional
service envelopes against actual inset components and wall bands, including
pair collisions. Anchors use actual unique inset edges; an ambiguous edge
selector is UNKNOWN. This checker does not place equipment.

These reports certify their stated predicates only. They do not establish
process-route acceptance, engineering sign-off or a complete facility bundle.
The full rectangular consumers retain their capability guards until separate
solver/export/route integration is independently verified.

## Opt-in finite room catalog solver

`solve_orthogonal_candidates(spec, ...)` in `orthogonal_solver` generates
grid-aligned rectangles and single-notch L footprints in the floor window,
preserving representable source cutouts. An explicit complete room catalog and
separately hashed fixed footprints can be supplied instead. This direct API does
not change facility/CLI dispatch.

CP-SAT selects one actual footprint per room. Pair constraints use true polygon
overlap, forbidden contact and one continuous required door segment. Independent
room, dimension, inset and door QA checks each selected combination; rejected
combinations are excluded before another bounded attempt. Area accuracy ranks
ahead of preferred contacts and position.

```python
from layout_configurator.orthogonal_solver import solve_orthogonal_candidates

draft = solve_orthogonal_candidates(spec, seed=1, deterministic_units=.05)
for candidate in draft.candidates:
    assert candidate.room_report.ok
    print(candidate.result.to_dict())
```

The 6 m × 6 m counterexample tiles a genuine 20 m² L and 16 m² rectangle even
though the corresponding exact-area rectangular model has no solution. Actual
footprints do not overlap despite overlapping bounding boxes.

`OrthogonalSolverBudget` defaults to 128 retained candidates and 8,192 enumeration
steps per room, 100,000 pair checks and 32 model attempts. Reports retain source,
fixed-constraint and catalog hashes, truncation/counts and bounded negative
samples, including the latest independent QA failure. Exhaustion is UNKNOWN about
this finite pattern family. A complete requested pool is DRAFT with `ok: false`;
partial pools stay UNKNOWN. Equipment packing, routes, rule packs and complete
exports remain separate integration gates.

The backend stores its integral CP objective through `solver.Value` on the
actual integer expression. Native floating objective residues cannot invalidate
otherwise canonical multi-candidate results. A real third candidate previously
returned `2650.0000000000005`; the four-candidate regression now passes strict
result round-trip without rounding geometry or weakening the schema. Fresh root
room/objective regression: 17 tests and 16 subtests passed in 8.98 s.

## Opt-in finite equipment packing

`place_orthogonal_equipment(building, layout, ...)` in `orthogonal_equipment`
places declared equipment in actual wall-inset room components. It accepts a
complete explicit catalog or generates source-grid positions and permitted
quarter-turns. Directional service envelopes must fit one real usable component;
a bounding rectangle never substitutes for the source footprint. Unique actual
side anchors retain their exact source offsets; ambiguous selectors yield UNKNOWN.

Every actual door receives a full-width local approach reservation, even when
the source has no flow declarations. The conservative project policy uses the
larger of actual opening width and the widest declared required flow, plus two
10 mm margins, and preserves the full interior approach depth. Equipment and
service envelopes cannot occupy these reservations.

Accepted combinations pass the independent B.4 room/equipment checks and a
separately recomputed door-approach report. Default bounds are 128 candidates and
16,384 catalog steps per item, 100,000 pair checks and 16 model attempts. Source,
layout and catalog hashes, truncation, solver statuses and rejected combinations
remain in the evidence. Exhaustion is UNKNOWN about the bounded search. A checked
placement is still DRAFT with `ok: false`; process routing, full dispatch and
complete facility export remain separate gates.

Packing also accepts optional `excluded_layouts`: at most 128 independently
valid, complete equipment combinations for the current exact room layout.
Canonical no-good constraints exclude each complete selected combination while
allowing alternatives that reuse some individual placements. Valid combinations
absent from the retained domain are recorded as unmatched; no hash alone removes
geometry. Nonempty exclusions have canonical count/hash/match evidence. Omitted,
empty and null exclusions preserve ordinary serialized output exactly. Fresh
root packing/exclusion regression: 24 tests and 31 subtests passed in 11.29 s.
This supplies bounded alternate packing for later route/profile retries;
exhausting the finite catalog remains UNKNOWN about the complete program.

## Actual-width orthogonal process routes

`route_orthogonal_flows(building, layout, equipment)` searches bounded
Manhattan visibility graphs in actual wall-inset room components joined only
through generated internal door throats. Source wall bands and every directional
equipment service envelope remain obstacles. The search uses square corridor
erosion, critical coordinates and deterministic shortest paths; it can use a
longer real-door room chain when the direct chain is blocked.

Equipment terminals use a declared-front service standoff of half the source
flow width plus 10 mm. A separate full-width access strip must reach the physical
front without crossing walls, another service envelope or the actual usable
room boundary. Room and zone terminals use real free components, never the
centre of a footprint's bounding rectangle. This is a project routing policy,
not an inferred regulatory or manufacturer requirement.

`validate_orthogonal_flow_routes` independently rebuilds rooms, equipment,
walls and doors. It checks canonical endpoint pairs and required completeness,
source widths, ordered actual room crossings, complete opening spans and the
entire square swept corridor, including turns and terminal caps. Forged widths,
room paths, notch shortcuts, concave inset corners and unrelated equipment
exemptions fail these predicates.

Default limits are 256 endpoint pairs, 16 representatives per endpoint, 4,096
nodes, 65,536 grid steps and 100,000 edge/search steps per pair. Evidence records
source/layout/equipment/search hashes, actual counts, truncation and bounded
negative samples. Exhausted search remains UNKNOWN; it is not a proof of whole
program infeasibility. Erosion may omit zero-area exact-width connections even
when a supplied route passes the independent checker.

A successfully checked route is still DRAFT with `ok: false`. Flow segregation,
profile compliance and the complete polygon export/acceptance pipeline remain
separate gates. The B.5 room and B.6 equipment outputs feed this API directly,
but packing acceptance alone does not establish full-width front access.

## Exact IFC4 coordination export

`export_orthogonal_draft_ifc` consumes the canonical building and mixed result
footprints directly. Concave spaces, actual polygon wall material with enclosed
voids, and matching door/opening solids retain their geometry. Native
`IfcRelVoidsElement` and `IfcRelFillsElement` connect each actual opening to its
wall and door. Optional equipment, rotated directional service envelopes and
full route corridors/Axis polylines require fresh independent local predicates.

Geometry and source hashes record the current inputs. Stable semantic GUIDs
depend on project name, source version, projection schema and semantic ID;
geometry and variant changes do not change occurrence identity. Coordination
proxy heights are explicitly recorded: rooms/walls 2,800 mm, doors/openings
2,100 mm, equipment 1,000 mm and service/corridors 20 mm.

`validate_orthogonal_ifc` independently reads the exact hashed bytes and rebuilds
expected material from canonical inputs. It checks actual outer/inner profile
rings through world, product and solid transforms, source identities, optional
states, real opening links, units and DRAFT metadata. Rehashed bounding-box
substitutions, missing holes, moved geometry, changed GUIDs and forged acceptance
fail. Supported input is bounded to 16 MiB, 100,000 entities, 32-level acyclic
placements, 64 source rooms/equipment and 256 routes. Arbitrary imported IFC
representations remain outside this polyline-profile checker.

Fresh root verification: 31 tests and 24 subtests passed in 20.58 s across the
new projection, existing IFC groups and exports. An actual fixture passed
strict IFC4 EXPRESS with zero statements and native reconstruction of all eight
represented products. This establishes the stated local exchange predicates.
Export remains DRAFT, full facility validation UNKNOWN and engineering
acceptance PENDING; the existing rectangular dispatch guards remain unchanged.

## Exact vector PDF coordination export

`export_orthogonal_draft_pdf` projects actual room contours, wall material with
holes and real openings onto an A3 vector plan. Supplied equipment/service and
full-width route corridors require independent local checks. Directional service
envelopes retain actual rotations; the opening marker is drawn above corridors.
A paginated source inventory retains all canonical IDs and source dimensions.
Every page prints its variant, revision, source hashes, DRAFT status, full
facility UNKNOWN and engineering acceptance PENDING.

The strict artifact index records the PDF byte hash, exact canonical source
hashes, supplied component states, projection, page inventory and resource limits.
`validate_orthogonal_pdf` independently parses actual painted paths and effective
transforms, compares contours/material and ordered polylines to the source,
and checks visible printed identity. Rehashed bounding rectangles, displaced or
missing vectors, hidden background order, zero-size text, clipping, raster/form
resources and forged statuses cannot satisfy this bounded contract.

Default limits are 32 rooms/equipment, 64 routes, 4,096 room vertices, eight
pages, 8 MiB PDF bytes, 4 MiB streams, 50,000 operators and 256 resource objects.
Writing checks the generated in-memory streams before creating a destination.
The supported vector material readback has a 0.001 mm coordinate tolerance.

Fresh root PDF/sheet regression: 32 tests and 46 subtests passed in 6.20 s.
All five final concave and dense-inventory pages were rendered with Poppler and
inspected; root separately inspected the plan and final inventory continuation.
This is an exact source projection with local exchange predicates. Shared
polygon bundle dispatch, profile integration and engineering review remain
separate gates.

## Declared facility and profile predicates

`validate_orthogonal_building` independently recomputes actual room/opening,
directional equipment/service, full-width door approaches and complete route
predicates before evaluating the unchanged sourced facility policies. Required
functional-zone contacts require one contiguous actual shared edge at least the
source door width; disjoint contact lengths cannot be summed. Forbidden contacts
also use actual footprints, including mixed rectangle/polygon results.

Absent declared equipment or routes remain UNKNOWN. Supplied empty, undeclared
or forged geometry is checked and can fail. Separation and process-sequence
checks consume routes only after independent actual geometry passes. Every
declared separation rule is evaluated with its own source provenance. Canonical
building/layout/equipment/routing/profile hashes bind both positive and negative
reports; invalid undeclared equipment retains its real frame without an invented
service envelope.

The shared rule helpers require actual finite numeric pressure thresholds and
actual nonnegative integer airlock counts, excluding booleans. Strings such as
`"NaN"` and fractional counts cannot establish PASS through coercion. Existing
rectangular checks receive the same repair, while valid sourced pilot thresholds
keep their original behavior. Missing supported policy evidence stays UNKNOWN.

Fresh root geometry/profile/result/pharma regression: 83 tests and 77 subtests
passed in 4.19 s. Meaningful RED exposed both numeric coercion defects and the
undeclared-equipment source-hash failure before repair. Actual three-room routes
prove an intermediate-room separation failure and a second independently sourced
rule. The report's PASS covers these declared software predicates; complete
facility artifact validation remains UNKNOWN and engineering acceptance PENDING.
Windows/daylight and shared polygon bundle dispatch still require separate gates.

Nested LayoutIR is also strictly schema-checked before BuildingIR normalization.
Direct malformed dataclasses, such as a boolean grid or string wall thickness,
cannot become plausible numeric source values through normalization. Both cases
reproduced RED before the guard; fresh root profile/schema regression passed
44 tests and 18 subtests in 2.83 s.

## Canonical facility candidate orchestration

`generate_orthogonal_facility_variants` composes the finite room backend,
directional equipment packing, actual full-width routes and fresh declared
facility/profile predicates. It accepts the same canonical BuildingIR and
profile, optional bounded catalogs and fixed footprints. No competing model or
implicit rectangular fallback is introduced.

Every candidate is checked against the current source. A downstream rejection
excludes the complete equipment combination before the next bounded attempt;
individual placements can be reused. Verified partial room pools can supply an
accepted candidate, while an incomplete requested accepted set stays UNKNOWN.
Rejection samples and aggregate counts preserve source IDs, hashes, rule
provenance and actual stage evidence. Exhaustion does not prove whole-program
infeasibility.

Wall-clock mode uses the high-resolution monotonic clock, stops launching stages
at the deadline and rejects a late candidate. Existing bounded route/profile
stages cannot be preempted mid-call. Deterministic work limits apply per CP
invocation, without a claim of aggregate consumed work or cross-platform
repeatability.

Fresh root generation/objective/exclusion verification: 16 tests and 30 subtests
passed in 22.82 s. The source-generated exact 20+16 m2 counterexample produces an
actual concave room, equipment and a route without supplied catalogs or fixed
footprints. Its preserved individual IFC/PDF artifacts match their byte hashes;
strict IFC EXPRESS has zero violations and both PDF pages were inspected.
Local candidate status remains DRAFT with full facility validation UNKNOWN and
engineering acceptance PENDING. Selectable product dispatch and complete
persisted bundle acceptance remain separate.

## Actual exterior window lines

`project_orthogonal_windows` resolves explicit openings on a single actual
outward facade segment, including real cutout facades. Source IDs, side, center
offset and width are retained. A manual side with multiple possible segments
remains UNKNOWN until the canonical source has a segment selector.

Rooms declaring daylight can receive deterministic openings under the existing
project policy: a 600 mm minimum width and 200 mm end margins. Candidate spans
use actual exterior edges and exclude the declared exterior entry. Explicit
windows satisfy the declared opening request without an extra automatic window.
These dimensions are project opening policy, not a physical daylight or
regulatory calculation.

Independent supplied-line validation checks source identity, host, full exterior
facade coverage and exact position/width/orientation. Duplicate automatic/source
IDs and overlaps fail. Before expensive geometry, canonical spec/result sources
are limited together to 1 MiB UTF-8, 4096 boundary/cutout/result vertices, 64 room
records, 128 combined explicit/required automatic openings and finite geometry
scalars/endpoints within 1,000,000 mm. Rectangle scalars are checked before
endpoint arithmetic so huge integers cannot escape as OverflowError.

Fresh root window/projection/daylight regression passed 41 tests and 31 subtests
in 1.49 s, including meaningful malformed-input and source-bound negatives.
The existing IFC/PDF/window capability guards remain active. This standalone
component covers two-dimensional facade lines only; coordinated wall/three-format
window delivery, window elevations, engineering daylight and complete facility
acceptance remain separate.

### Opted coordination window capability

`build_orthogonal_wall_plan(..., include_windows=True)` freshly verifies the
`source_facade_v1` capability and attaches canonical `WindowOpening` records.
Wall material and actual door openings remain exactly unchanged: a window is
a closed facade barrier, never a circulation portal.

The source sill plus height must fit the existing 2800 mm coordination wall
proxy. A larger head retains scoped UNKNOWN and does not prove physical
infeasibility. Ambiguous source facade selection retains UNKNOWN; duplicate
identities retain FAIL. Omitted/false options preserve the existing unsupported
window guards. Fresh root wall-plan regression passed 23 tests and 24 subtests
in 1.11 s; the agent's broader regression passed 102 tests and 145 subtests.
Three-format export and complete generation/bundle propagation remain pending.

## Atomic canonical draft bundle

`write_orthogonal_bundle` persists five canonical source snapshots, three actual
DXF/PDF/IFC artifacts and their indices, current facility predicates, a manifest
and independently recomputed QA. The fixed 14-file inventory retains source,
profile, variant and revision identities. Equipment service envelopes and full
route widths retain their actual geometry in every format.

`validate_orthogonal_bundle` reads bounded snapshots of the actual saved bytes.
It recomputes the declared current-profile predicates, compares saved provenance
with that fresh report, independently reads physical geometry in all three
formats and runs strict IFC4 EXPRESS. Rehashing a substituted contour, wall hole,
door, service envelope, corridor or current profile cannot preserve acceptance.
The saved QA also binds the manifest and artifact-index bytes.

Publication uses a sibling staging directory and a native no-replacement rename;
existing or raced-in destinations remain protected. JSON structure, artifact,
total-byte and DXF entity limits are enforced before publication and on readback.
Declared structural grids and windows are rejected before creating output until
their exporter capability is integrated. Unsupported native zones and systems
remain explicitly UNKNOWN; absent optional evidence is null and UNKNOWN.

Fresh root verification passed five targeted tests in 62.44 s, covering the
actual source-generated concave candidate, complete saved-source QA, rehashed
PDF/IFC substitution, changed profile and a raced-in output directory. The agent
passed all 23 bundle tests and 34 subtests in 263.77 s. Both retained actual and
source-generated bundles pass every scoped check with zero EXPRESS statements;
their two-page PDFs were rendered and inspected. Windows publication is tested
locally; the native Linux publication branch awaits pinned CI.

The manifest stays DRAFT with `ok: false`; full facility validation remains
UNKNOWN and engineering acceptance PENDING. Scoped exchange QA PASS verifies
the documented software predicates and does not promote either acceptance.

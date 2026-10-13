# Source-bound typed transfer permissions

I.2.1 introduces `schemas/transfer_program.schema.json`,
`validate_transfer_program` and `validate_transfer_routes`. These bounded
sidecar checks reference existing canonical source objects; they do not change
BuildingIR, add openings or replace facility generation and QA.

The version 0.1 contract binds the complete canonical BuildingIR SHA256 and the
exact supplied production-program SHA256. An embedded production program is
included in the whole-building identity. Provenance resolves through the I.1
source namespace, verified UTF-8 content, usage rights and source attribution.
Each flow ID has an explicit personnel/material/product/waste classification;
free-text `FlowSpec.type` never supplies this authority.

Each transfer binds one original explicit `DoorSpec` ID and its ordered pair of
existing rooms, a minimum clear width and forward/reverse movement permissions.
Supported declarations are `door`, `personnel_airlock`, `material_airlock` and
`pass_box`. Facade windows cannot be portals; generated automatic doors have no
implicit typed permission. This first slice does not model conveyor windows or
complete airlock chambers.

```python
from layout_configurator.transfer_program import validate_transfer_program
from layout_configurator.transfer_validation import validate_transfer_routes

contract = validate_transfer_program(raw, building=building,
                                     production_program=production_program)
report = validate_transfer_routes(building, layout, routing, raw,
                                 production_program=production_program,
                                 equipment=equipment)
```

Actual route verification first performs fresh orthogonal room, equipment,
endpoint, corridor and opening checks. Crossings come from literal route
coordinates and room interiors, never the saved `room_path`. All actual rebuilt
doors are considered: multiple or unresolved source matches remain UNKNOWN,
without selecting a lexical ID to authorize passage. Current route width, source
flow width and the declared transfer minimum all apply at the actual span.

Personnel through a pass box or material airlock is rejected with flow,
transfer, opening and source IDs. Missing classification/permissions, unclear
rights or assumed declarations remain UNKNOWN. Known denials remain FAIL even
when other evidence is incomplete. A permitted door can pass the scoped 2D
permission check; permitted material through a pass box still has an UNKNOWN
mechanism because chamber, interlock, loading and HVAC evidence is absent.
Authenticated approval is UNKNOWN, full facility acceptance UNKNOWN and
engineering acceptance PENDING. No result proves original-building infeasibility.

Admission defaults bound 64 rooms, zones, equipment and flows; 128 transfers,
256 source openings/routes, 1,024 points per route and 16,384 total points.
The conservative geometry work score is limited to one million interactions;
contract and combined input bounds are 2 MiB and 8 MiB. Typed and raw preflight
runs before expanding serialization or geometry. Detached contract JSON is
bounded before parsing and rejects duplicate keys. Overflow cannot yield a
partial PASS. Original sources and geometry are never mutated.

`examples/transfer_program_passbox.json` is a self-contained synthetic software
fixture with canonical source, I.1 evidence, typed policy and actual layout.
Its ordinary route geometry passes, but its explicitly classified personnel
route crosses the material-only `passbox_portal` and correctly fails permission.
This is not an approved manufacturing design or the medical-plastics benchmark.

Meaningful RED covered the missing actual personnel rejection and contract,
then malformed source/opening/coordinate and width cases. The agent's complete
related gate passed 136 tests and 149 subtests in 23.47 s. Fresh root verification
passed 68 contract/source tests in 2.169 s. Independent durable probes verify the
literal boundary at x=3,000 mm, span y=1,050..1,950 mm and crossing [3,000,1,500];
personnel denial, permitted material with UNKNOWN mechanism, scoped door PASS
and forged room-path FAIL all retain exact source identities. A regular wheel
from pinned `a175b19` plus the six frozen files passes the same actual denial
and offline schema/hash checks from a foreign directory. Its SHA256 is
`16dd5ff6372dc2a6b223ccdfbb2f96546266b9bb6a04d791bce99b44df817e2d`.
Producer enforcement, ordinary generation/HTTP admission, pressure/scenarios
and installed full CI for later source remain separate checkpoints.

## Actual JSON review command

I.2.1.1 adds an explicit bounded review boundary:

```console
layout-configurator check-transfers supplied-review.json --output transfer-report.json
```

The JSON contains complete canonical `building`, standalone `production_program`
and `transfer_program`, actual `layout` and `flow_routes`, plus optional canonical
`equipment` placements. Routes use their original serialized `routes` array with
literal point objects; the command never generates replacement routes or solves
a facility. Equipment retains strict original numeric/rotation types and any
saved clearance must match freshly derived source geometry. Unknown fields,
duplicate keys, scalar coercions and stale bindings cannot gain acceptance.
`--include-windows` explicitly enables source facade-window geometry, with no
permission to cross a window. Omit `--output` to print JSON.

Exit codes are scoped PASS0, UNKNOWN4 and FAIL2. Reports retain exact review-file
SHA256, canonical source identities, actual crossings and the original proof
boundaries. The source file is preserved; output cannot resolve to the input
path. File bytes, raw JSON nodes/depth and total route/geometry workload are
bounded before parsing or constructing geometry. Overflow prevents partial
acceptance. Embedded production programs are explicitly preserved during review;
unsupported fresh geometry remains UNKNOWN and ordinary generation still stops
before CP search. A local permission result never closes full facility QA.

Meaningful missing-command RED produced ten failures in six methods without
errors; two additional RED checks require room/point limits before geometry.
Fresh root verification passes75 related tests in6.083 s and final eight review
tests in0.538 s. Durable actual command outputs preserve source bytes and
distinct0/2/4 exits. A regular wheel from pinned `9632549` plus the three frozen
files reproduces all three actual command cases from a foreign directory,
with exact implementation hashes; wheel SHA256
`1c54559ec8b7f0a0b8cf3030bf5f2ba62fc88c1fd85219ce6f544afcc7dca492`.

Checkpoint I.2.1.2 checks actual filesystem identity as well as resolved paths
before review and immediately before writing an optional report. A different
filename hard-linked to the input rejects with exit2 and preserves both files;
the same holds when the alias is created during review. Identity lookup errors
reject before writing. This is a source-preservation check, not a claim about
arbitrary concurrent filesystem modifications after the final check. Two actual
hard-link regressions first failed without errors;44 related transfer tests
then passed in1.564 s on2026-10-13.

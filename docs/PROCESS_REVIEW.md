# Synchronized Supplied Process Review

Checkpoint I.5.1 adds a read-only, offline process review of one supplied model
revision. It produces detached JSON schedules and freshly computed local
evidence. PDF appendices, drawing overlays and attachment to a complete
validated facility export bundle remain subsequent work.

```python
from layout_configurator.process_review import load_process_review

report = load_process_review("examples/process_review_input.json")
```

The closed input requires `version`, `id`, `drawing_revision`, `variant`,
`building`, `layout`, `equipment`, actual `flow_routes`, `production_program`,
`transfer_program`, `environment_program`, `library` and `scenario_requests`.
Environment and library may be explicitly null. Mandatory parents cannot be
null; supplied verdicts, unknown fields and output destinations are rejected.
The variant must exactly match the original layout. Embedded production
programs remain in the whole building source and its hash; a different top-level
program is a fresh binding failure, including different numeric scalar types.

Every schedule and component report carries the same drawing revision, variant
and nine component SHA-256 identities. `review_source` additionally records the
exact original UTF-8 file bytes and SHA-256. `source_frames` preserves supplied
component values. Schedules cover rooms and actual footprints, operations,
equipment and placements, typed transfers, separate clean and ventilation
zones, pressure declarations and explicit scenario requests.

Fresh evidence reuses the original production-program, transfer, environment,
engineering-library and local equipment-scenario checks. A missing library
does not suppress independent canonical body/clearance checks. Movement types
come from explicit source-bound declarations. Saved room chains do not grant
permissions; an inconsistent chain remains a geometry failure. ISO and GMP,
required pressure values, supplied design values and supplied measurements
retain their separate meanings.

Resource admission bounds the complete raw input before canonical constructors
or GEOS: four MiB input, 131,072 nodes, depth 16, 64 rooms, 32 equipment items,
one MiB combined source contents, 256 KiB per source, 4,096 library geometry
points and 131,072 scenario predicate pairs. Embedded and external source
occurrences share the byte budget. Structured library contents share a combined
lexical node/byte budget before any nested decoding. The aggregate geometry
work score includes the repeated independent checks. Limits only tighten.

Eight MiB bounds detailed report delivery; smaller delivery budgets must hold
at least 4,096 bytes. Incomplete delivery is explicitly marked and retains
identity-stamped originating failure evidence. Exact source dimensions, frame,
host, rotation and scenario request identities remain mandatory even when the
library is absent. Positive extents lost at world-coordinate precision yield
UNKNOWN before geometry construction.

The document always remains DRAFT. Overall status is FAIL when fresh known
conflicts exist and UNKNOWN otherwise; `.ok` is false. Missing evidence cannot
authorize PASS or hide a known contradiction. Full scenarios, exterior
installation paths, three-dimensional feasibility, HVAC, source authentication
and full facility acceptance remain UNKNOWN; engineering acceptance is PENDING.
Source URIs are retained as data and are never fetched. No solver runs, source
file writes or generation capability changes are introduced.

Fresh local single-file verification passed 21 tests and 26 subtests in 4.09 s.
Regressions cover source identity, actual permission and geometry failures,
missing library evidence, exact placement/request admission, source budgets,
world precision and bounded originating-failure retention.

Local verification details and the paused continuation state are recorded under
`out/checkpoints/process-review-i51/`. A fresh regular installed-wheel check
remains a separate verification before shipping this new review API.

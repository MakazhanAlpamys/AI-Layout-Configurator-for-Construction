# Opted production geometry in process review

Checkpoint I.4.3.2 adds an explicit reader for the existing closed process
source version `0.2`:

```python
from layout_configurator.process_production_layout_review import (
    load_process_review_with_production_layout,
)

review = load_process_review_with_production_layout("process-with-paths.json")
production = review["additional_reports"]["production_layout"]
```

Keyword controls are `include_windows=False`, `limits=ProcessReviewLimits()`,
`path_limits=ProcessInstallationPathLimits()` and
`production_limits=ProductionLayoutLimits()`. Limit classes, defaults and
historical readers remain unchanged. No source fields, saved verdicts or schema
aliases are added. Version `0.1` is not accepted by this explicit reader.

The original UTF-8 file is read once. Existing five reports, eight schedules
and supplied-path evidence retain their original identities. In complete
delivery, the additional wrapper carries the same drawing revision, variant
and nine raw component
hashes, plus fresh [standalone production geometry](PRODUCTION_LAYOUT_REVIEW.md).
Its three original building/layout/program hashes match the corresponding
common hashes. The separate base-building hash excludes only the embedded
program. Canonical path signatures retain their separate meaning.

Optional raw layout fields and integer/float distinctions remain original
source data. The reader writes no intermediate files, rewrites no request and
rebinds no program. Detached evidence can be edited without changing its source.
Native room adjacency comes from actual contiguous shared spans and overlaps;
thresholds remain project declarations. Door/transfer suitability, throughput,
all scenarios and complete facility acceptance remain separate checks.

## Admission and shared work

Original file/schema bounds apply first. Exact raw base binding and whole
embedded/top program identity are checked before path or room precision probes.
An independently unrepresentable positive contour cannot hide a known stale
binding. Unsupported valid canonical roundtrip fields yield
`UNKNOWN/NOT_EVALUATED` without normalizing the source.

Before any canonical model, geometry or nested library decode, the opted
pipeline checks the complete raw core + common + path + production geometry
score. It sums path and production source vertices under the existing shared
scenario ceiling. Existing repeated library/node/source work also includes the
introduced complete raw3 snapshot and, when present, one full embedded contract
decode. Full compact JSON UTF bytes are charged; bibliographic content-only
metrics remain separate. Work counters state this accounting scope.

In the clear supplied fixture, original common/core/path work totals 5,955;
production adds 112, yielding 6,067. A 6,000-unit ceiling admits either existing
consumer separately but stops their combination before constructors. Vertex
reservations 58 + 12 exceed a shared ceiling of 66. The complete raw3 snapshot
uses 3,998 JSON UTF bytes versus 326 content bytes: shared byte work rises from
70,858 to 74,856, and node work from 9,409 to 9,764. These are bounded admission
scores and byte/node reservations, not time or operating-system RAM measurements.

Resource stops retain the original file hash and explicit missing inventories.
Frame snapshots are allocated only after their joint admission. Pure binding
stops preserve original identities without allocating a new frame snapshot.
Positive orthogonal area underflow remains UNKNOWN before constructors/GEOS;
this reader does not invent feasible geometry.

## Delivery and boundaries

Known additional FAIL promotes the overall report to FAIL beside missing
staging or unverified provenance. Existing pressure and path-body conflicts
remain FAIL beside an UNKNOWN additional report. A local production PASS leaves
the full process report UNKNOWN: `ok=False`, DRAFT, full facility UNKNOWN,
engineering PENDING and authenticated approval UNKNOWN remain mandatory.

Additional evidence is attached before one final delivery cap. Compact output
prioritizes the originating production requirement ID/SHA, operation/source IDs
and source SHA, room IDs and measured span/threshold ahead of earlier generic
pressure/path witnesses. Failure inventories state omissions and incomplete
leaf delivery. An incomplete report never acquires an approval claim.

Historical document exporters and package APIs use their existing readers and
inventories. Publishing this additional evidence in documents, enforcing the
program in generation, complete scenarios/3D, the medical-plastics benchmark
and professional acceptance remain later checkpoints.

## Verification

Initial missing-module RED covers ten methods, then two added binding/precision
methods. Independent complete-JSON serialization exposes two UTF-budget
subfailures that the content-only check missed. Two further subfailures show
known stale/embedded binding being hidden by positive-area precision; the
minimal ordering fix retains FAIL before either precision probe.

Final root single-file verification passes fourteen tests/fifteen subtests in
15.13 seconds; the unchanged historical path behavior passes seventeen tests/
twenty-one subtests in 19.45 seconds. Ordinary installed wheels outside the
checkout reproduce twenty-one cases, including combined geometry/vertex/node/
byte stops, full snapshot/embedded UTF stops, optional fields, whole scalar
identity, source binding and precision. Eight original version0.2 outputs,
one version0.1 output, all existing limit defaults and the path API signature
match their saved baseline hashes. Solver calls remain zero.

A 4,096-byte/one-finding cap retains the originating `entry_work_contact`
failure beside pressure FAIL in 3,763 bytes: original requirement/source SHA,
handover operation, entry/work rooms and measured 0 versus required 1,600 mm.
[Compact evidence](acceptance/517ae7c-process-production-layout.json) retains
source/archive/wheel hashes and exact cases. Raw proof and source/result JSON
remain local under `out/checkpoints/process-production-layout-review-i432/`.
Root helper assumptions about a nonexistent input envelope and optional normal
inventory metadata were corrected without changing production code. No full
suite, benchmark, background task or full CI dispatch is run by this checkpoint.

The pushed `357ddce` source checkpoint passes
[quick CI](https://github.com/MakazhanAlpamys/AI-Layout-Configurator-for-Construction/actions/runs/38590587537)
in thirty-eight seconds including setup. Full acceptance jobs are skipped;
the pending changed full gate remains separate.

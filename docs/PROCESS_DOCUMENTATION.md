# Synchronized DRAFT Process Documentation

Checkpoint I.5.5.1 separately exports complete original production requirement
appendices in an explicit version `0.3` document format from source `0.2`.
[Production requirement contract](PROCESS_PRODUCTION_LAYOUT_DOCUMENTATION.md).
The historical APIs described below retain their original scope and versions.

Checkpoint I.5.4.2 separately exports version `0.2` supplied installation paths
with fresh body/sweep/staging diagrams and independent actual PDF readback.
[Explicit path-document contract](PROCESS_INSTALLATION_DOCUMENTATION.md).
The version `0.1` APIs described below remain unchanged.

Checkpoint I.5.3 separately attaches these unchanged process documents to an
exact matching, freshly checked facility child in a bounded outer package.
[Attachment contract](PROCESS_FACILITY_PACKAGES.md).

Checkpoint I.5.2 publishes a standalone technical appendix from the original
[process-review input](PROCESS_REVIEW.md). It preserves the exact input bytes,
freshly computes the eight schedules and five local reports, and prints them
with literal source overlays. It does not run a solver or regenerate a layout.

```python
from layout_configurator.process_documentation import (
    export_process_documentation,
    validate_process_documentation,
)

index = export_process_documentation("review-input.json", "new-process-documents")
qa = validate_process_documentation("review-input.json", "new-process-documents")
```

The destination must be a new directory. Publication stages and independently
checks a closed five-file inventory: `source.json`, `review.json`,
`documentation.pdf`, `index.json` and `qa.json`. Existing destinations, source
aliases and destinations appearing during publication remain protected. Source
bytes are checked during fresh review, after PDF saving, before publication
and during final readback. Unsupported or incomplete review inventories cannot
be published as a complete appendix.

The PDF is a vector A3 technical appendix with portable bundled fonts. Every
page identifies DRAFT, original source SHA-256, revision, variant, page ID,
review status and the limits of acceptance. Detailed source records and fresh
evidence paginate without dropping continuations. ISO class, GMP grade,
state, applicability and required/design/measured pressures retain separate
source fields. Missing values remain UNKNOWN; known conflicts and their
source IDs and witnesses remain FAIL.

The source overlay page retains actual rectangle or concave room corners,
separate literal clean and ventilation boundaries, and pressure portal
segments only when the fresh environment review resolves the actual complete
opening. No parent-zone connection, automatic route, measurement or airflow
is inferred. These overlays are source projections, not complete architectural
drawings or equipment-installation diagrams.

Independent saved readback recomputes the source review and verifies original
bytes, complete report contents, closed index, pages, printed scope and exact
painted path coordinates through PDF transforms. Hashes alone cannot authorize
altered contours, shifted portals, omitted pages or changed visible verdicts.
Page boxes, annotation coverage, glyph area/orientation, vector styles and
clipping remain constrained to the supported static document contract.

Limits only tighten the process-review bounds. Additional defaults are eight
MiB PDF, 32 MiB complete delivery, one MiB metadata, one million source text
characters, 256 pages, four MiB per encoded/decoded stream, 50,000 operations
per page, 262,144 total PDF operations, 256 resource objects, 262,144 delivery
JSON nodes/depth 64, 1,024 overlay paths and 32,768 overlay vertices. Stream
decoding and actual readback are bounded; no source URI is fetched.

Artifact QA PASS means this appendix agrees with its exact source and fresh
scoped evidence. The document remains DRAFT and the process review remains
UNKNOWN or FAIL. Full facility validation is UNKNOWN, authenticated approval
is UNKNOWN and engineering acceptance is PENDING. The existing facility
bundle's fourteen-file inventory is unchanged. I.5.3 separately implements
explicit version `0.1` outer attachment and I.5.4.2 supplies the version `0.2`
path diagrams. Additional discipline views, version `0.2` outer attachment and
Cycle I benchmark and engineering acceptance remain separate work.

Fresh root single-file verification passes 14 tests and 25 subtests in 48.15 s.
Actual regressions first exposed omitted resolved portals, clipped/shifted
page frames, covering annotations and invisible zero-area text, then passed
after repair. A regular installed wheel from `54af4e4` plus the two frozen
files reproduces the 17-page source-bound appendix and fresh actual readback
outside the checkout. A second 16-page fixture retains the exact 20,000,000
mm2 concave room and 1,040,000 mm2 concave clean boundary. All 33 final pages
were rendered; page inventories and representative full pages were inspected.
Local verification is retained under `out/checkpoints/process-documentation-i52/`.

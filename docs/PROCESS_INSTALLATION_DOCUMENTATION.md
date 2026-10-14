# Source-Bound Installation Diagrams in Process Documents

Checkpoint I.5.4.3 separately attaches these documents to unchanged original
facility artifacts. [Attachment contract](PROCESS_INSTALLATION_FACILITY_PACKAGES.md).

Checkpoint I.5.4.2 explicitly exports the original version `0.2`
[process/path input](PROCESS_INSTALLATION_REVIEW.md). Eight schedules, five
process reports, supplied requests, coverage and fresh installation evidence
share the original revision, variant, component identities and whole-file SHA.

```python
from layout_configurator.process_installation_documentation import (
    export_process_documentation_with_paths,
    validate_process_documentation_with_paths,
)

index = export_process_documentation_with_paths(
    "process-with-paths.json", "new-process-documents",
)
qa = validate_process_documentation_with_paths(
    "process-with-paths.json", "new-process-documents",
)
```

The destination must be new. The closed five-file inventory remains
`source.json`, `review.json`, `documentation.pdf`, `index.json` and `qa.json`.
Version `0.2` index and QA additionally declare closed, tighten-only path
limits. Existing version `0.1` document and outer-package entry points retain
their original input contract, versions, inventories and limit fields.

The vector A3 plan draws only fresh admitted geometry: complete equipment
body poses, supported fixed-orientation axis sweeps, separately bound transport
clearance, and source-bound exterior staging floor and obstacles. Body outlines
are blue; clearance is orange and dashed. Staging and its obstacles have separate
outline roles. Unsupported rotation or diagonal motion supplies no manufactured
sweep. Literal allowed poses remain visible, and a known collision remains FAIL.

Exterior coordinates may be negative. Projection includes every admitted
painted corner and the source wall band within a declared plan viewport,
leaving the request legend in its own sidebar. Each request's full SHA, fresh
status and local 2D status appear on that plan page. The complete request and
evidence appendix paginates separately without dropping continuations.

Drawing inventory removes one redundant final endpoint of an explicitly closed
source contour. Exact source bytes, component hashes and original contours
remain unchanged. Nonfinite or collapsed projection rejects publication. The
overview also requires each projected closed contour's bounding box to be at
least one point wide and high: valid narrow or very distant source geometry can
exceed this readability subset. Such rejection does not establish geometric
infeasibility or authorize alteration of the input.

Saved readback recomputes the source review and admitted path inventory, then
checks actual PDF text, glyph visibility, roles, styles, transforms, physical
page positions and complete vector coordinates. Rehashing PDF/index/QA cannot
authorize an omitted body, a smaller footprint, an invented sweep, a shifted
exterior pose or a forged positive verdict. Inherited document bounds and
shared process/path admission limits apply before rendering and readback.
Native staged publication protects existing, aliased and raced destinations;
the original source is checked again after PDF saving and before publication.

Artifact-consistency PASS means exact agreement with the supplied source and
fresh scoped evidence. Documents remain DRAFT with `.ok == False`; process
review can be UNKNOWN or FAIL. Full installation, 3D and facility feasibility
remain UNKNOWN, source authentication UNKNOWN and engineering acceptance
PENDING. Path search, producer enforcement, additional discipline views and
Cycle I benchmark remain separate. Explicit attachment is implemented at I.5.4.3.

Fresh root sequential single-file verification passes ten document tests and
twelve subtests in 42.91 seconds, fourteen historical document tests/twenty-five
subtests in 39.34 seconds, seventeen process/path tests/twenty-one subtests in
24.47 seconds, and twenty-four standalone path tests/twenty-three subtests in
3.57 seconds. Actual RED cases preceded the source-bound admitted-geometry
field and redundant closed-endpoint repairs.

A regular wheel from `e35263a` plus the five frozen files runs outside the
checkout. Clear, blocked and unsupported cases each publish nineteen pages
with fresh artifact QA PASS, while retaining local 2D PASS, FAIL and UNKNOWN
respectively. Historical version `0.1` result identity, all forty-eight document
limit fields, saved PDF and saved outer-package QA remain unchanged/passing.
No solver is called. All fifty-seven pages were rendered and contact-inspected;
three complete plan pages and a dense evidence page were separately inspected.
[Compact evidence](acceptance/e35263a-process-path-documents.json) records exact
source, wheel, input and PDF identities. Detailed local proof remains under
`out/checkpoints/process-installation-documentation-i542/`.

The pushed `e5f0009` checkpoint passes
[quick CI](https://github.com/MakazhanAlpamys/AI-Layout-Configurator-for-Construction/actions/runs/38562618459)
in thirty-eight seconds including setup. Full acceptance jobs are skipped on
this push; changed full-suite acceptance remains separate.
